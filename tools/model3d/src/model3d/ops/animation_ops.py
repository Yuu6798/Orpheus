"""動きの検品の工程（docs/model3d.md の「動きを検品する」）。

- check.animation: 動きを付けた .blend（VRM の人型の骨）を全コマ調べて、貫通と破綻の候補を「いつ・どこが・どれだけ」の出来事で挙げる。
  直さない・採点しない・関門にしない（出来事はどれも要確認で、工程は出来事があれば needs_review。strict は無い）。測るのは Blender の中
  （blender_adapter/animation_sample.py）、数えるのは Blender の外（animation_check.py。numpy だけ）。証拠の絵はもう一度 Blender で
  描く（blender_adapter/animation_evidence.py）。骨へ載せる前の点を測る check.motion とは別の段（こちらは骨へ載せ、揺れものを焼き、
  手直しをした後の .blend を見る）。
"""
import math
import os
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

SAMPLE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "animation_sample.py"
EVIDENCE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "animation_evidence.py"
INJECT_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "fault_inject.py"
IMAGE_SIZE, IMAGE_QUALITY = 480, 88          # 証拠の絵（試作と同じ）


def _parameters():
    from ..animation_check import DEFAULTS
    return {"armature", "frames", "skip_bones", "foot_lowest", "cloth", "max_images", "report_only", "blender", "addons",
            "blender_extensions", "timeout", *DEFAULTS}


@register("check.animation", inputs=("blend", "expected"), outputs=("report", "table", "sheet", "expected_table"), cacheable=False,
          summary="動きを付けた .blend（VRM の人型の骨）を全コマ調べて、破綻の候補を出来事（信号・場所・最初と最後のコマと秒・いちばん大きい"
                  "コマと値と位置）にまとめる。静的: 腕が胴・頭・脚に入る、腕どうし・脚どうしの重なり、体が布（揺れものの骨で動くメッシュ）を"
                  "突き抜ける、足が床より下、手首の曲げとねじれ・首・ひざの逆曲がり。動的: 向きの飛び、位置の飛び、震え、着いた足の滑り、"
                  "全身の切り替え。体の形はそのモデルの初期姿勢のメッシュから作る。table は読む用の表（Markdown）、sheet は証拠の絵"
                  "（場所を狙うカメラと赤い印。max_images 件まで）の一覧。入力 expected（motion.inject_faults の、わざと入れた破綻）が"
                  "あれば、見つけたかを照らし合わせる（報告の expected と expected_table）。直さない・採点しない（出来事はどれも status: review。"
                  "出来事があれば needs_review、report_only なら測るだけ）。しきい値の既定は仮の値。Blender（VRM Add-on）が要り、"
                  "再利用しない（毎回実行）")
def check_animation(inputs, parameters, outputs, context):
    from .. import animation_check
    from ..blender_adapter import launcher
    from . import number
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    from .vrm_ops import VRM_ADDON
    require(inputs, ("blend",), "check.animation")
    unknown = set(parameters) - _parameters()
    if unknown:
        raise ToolError(f"check.animation の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(_parameters())}）")
    if "report" not in outputs:
        raise ToolError("check.animation には outputs の report が要ります")
    if "table" in outputs and Path(outputs["table"]).suffix.lower() != ".md":
        raise ToolError(f"check.animation の outputs の table は .md にしてください: {Path(outputs['table']).name}")
    if "sheet" in outputs and Path(outputs["sheet"]).suffix.lower() not in (".jpg", ".jpeg"):
        raise ToolError(f"check.animation の outputs の sheet は .jpg にしてください: {Path(outputs['sheet']).name}")
    if "expected_table" in outputs and (inputs.get("expected") is None or Path(outputs["expected_table"]).suffix.lower() != ".md"):
        raise ToolError("check.animation の outputs の expected_table（.md）は、入力 expected と一緒に使ってください")
    faults = None
    if inputs.get("expected") is not None:
        try:
            faults = animation_check.expected_faults(read_json(inputs["expected"]))
        except animation_check.SettingsError as error:
            raise ToolError(f"check.animation: {error}") from error
    try:
        cfg = animation_check.settings(parameters)
    except animation_check.SettingsError as error:
        raise ToolError(f"check.animation: {error}") from error
    span = parameters.get("frames")
    if span is not None and (not isinstance(span, list) or len(span) != 2
                             or not all(isinstance(v, int) and not isinstance(v, bool) for v in span) or span[0] > span[1]):
        raise ToolError(f"frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {span!r}")
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature はアーマチュアの名前にしてください: {armature!r}")
    skip = parameters.get("skip_bones", [])
    if not isinstance(skip, list) or not all(isinstance(p, str) and p for p in skip):
        raise ToolError(f"skip_bones は骨の名前の型（fnmatch。例 \"*twist*\"）の並びにしてください: {skip!r}")
    foot_lowest = parameters.get("foot_lowest", "world")
    if foot_lowest not in animation_check.FOOT_LOWEST:
        raise ToolError(f"foot_lowest は {' か '.join(animation_check.FOOT_LOWEST)} にしてください: {foot_lowest!r}")
    for key in ("report_only", "cloth"):
        if parameters.get(key) is not None and not isinstance(parameters[key], bool):
            raise ToolError(f"{key} は true か false にしてください: {parameters[key]!r}")
    max_images = number(parameters, "max_images", 30, minimum=1, integer=True)
    if parameters.get("timeout") is not None:
        number(parameters, "timeout", minimum=0, exclusive=True)
    blender = _blender(parameters, context, "check.animation")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    settings_path, sample_path, meta_path = step_dir / "animation_settings.json", step_dir / "sample.npz", step_dir / "sample.json"
    atomic_json(settings_path, {"armature": armature, "frames": span, "cloth": parameters.get("cloth", True),
                                "cloth_near_m": cfg["cloth_near_m"], "cloth_depth_min_m": cfg["cloth_depth_min_m"]})
    record = launcher.run_blender(blender, SAMPLE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                  "--out", str(sample_path), "--meta", str(meta_path)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    meta = read_json(meta_path)
    try:
        sample = animation_check.load_sample(sample_path, meta)
        report = animation_check.check(sample, cfg, skip_bones=skip, foot_lowest=foot_lowest)
    except animation_check.SettingsError as error:
        raise ToolError(f"check.animation: {error}") from error
    report = {"schema_version": report.pop("schema_version"), "blend": str(inputs["blend"]),
              "blender_version": record["blender_version"], "armature": meta["armature"], "scene": meta["scene"],
              "hair_objects": meta.get("hair_objects", []), **report}
    report_dir = Path(outputs["report"]).parent
    if "sheet" in outputs:                                       # 証拠の絵（もう一度 Blender で）と、その一覧
        image_dir = Path(outputs["sheet"]).parent / animation_check.IMAGE_DIRECTORY
        chosen = animation_check.evidence_events(report["events"], max_images)
        keys = ("id", "kind", "category", "signal", "peak_frame", "location", "normal")
        evidence_path, images_path = step_dir / "evidence_settings.json", step_dir / "evidence_images.json"
        atomic_json(evidence_path, {"armature": meta["armature"], "frames": report["frames"], "hips": sample.humanoid["hips"],
                                    "directory": str(image_dir), "resolution": IMAGE_SIZE, "quality": IMAGE_QUALITY,
                                    "events": [{key: event[key] for key in keys} for event in chosen]})
        launcher.run_blender(blender, EVIDENCE_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(evidence_path),
                                                               "--report", str(images_path)],
                             log=step_dir / "blender_evidence.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
        drawn = read_json(images_path)
        for event in report["events"]:
            names = drawn["images"].get(str(event["id"]))
            if names:
                event["images"] = names
        report["images_directory"] = Path(os.path.relpath(image_dir, report_dir)).as_posix()
        report["render_changes"] = drawn.get("render_changes", {})
        sheets = animation_check.contact_sheets(report, image_dir)
        sheet_path = Path(outputs["sheet"])
        names = []
        for k, sheet in enumerate(sheets or [None]):
            path = sheet_path if k == 0 else sheet_path.with_name(f"{sheet_path.stem}_{k + 1}{sheet_path.suffix}")
            if sheet is None:                                    # 絵を描く出来事が無い: 一覧はそう書いた小さな絵
                from PIL import Image, ImageDraw, ImageFont
                sheet = Image.new("RGB", (300, 40), (24, 24, 26))
                ImageDraw.Draw(sheet).text((8, 12), "no evidence images", fill=(235, 235, 235), font=ImageFont.load_default(size=14))
            sheet.save(path, quality=88)
            names.append(Path(os.path.relpath(path, report_dir)).as_posix())
        report["sheets"] = names
    if faults is not None:
        report["expected"] = animation_check.match_expected(report["events"], faults)
    atomic_json(outputs["report"], report)
    if "expected_table" in outputs:
        Path(outputs["expected_table"]).write_text(animation_check.expected_markdown(report), encoding="utf-8", newline="\n")
    if "table" in outputs:
        Path(outputs["table"]).write_text(animation_check.table_markdown(report, report.get("images_directory", animation_check.IMAGE_DIRECTORY)),
                                          encoding="utf-8", newline="\n")
    summary = {key: report[key] for key in ("schema_version", "overall", "frames", "fps", "counts", "skipped")}
    summary["events"] = len(report["events"])
    if faults is not None:
        summary["expected"] = {key: report["expected"][key] for key in ("found", "total")}
    result = StepResult(observations=[summary])
    if report["events"] and not parameters.get("report_only"):
        result.unresolved.append(f"動きの検品: 出来事 {len(report['events'])} 件（数と場所だけ。絵で見て、直すかを決める）")
        result.review = True
    return result


INJECT_PARAMETERS = {"faults", "armature", "blender", "addons", "blender_extensions", "timeout"}


def _fault(fault, k):
    """motion.inject_faults の破綻 1 つを確かめる（形の誤りは ToolError）。"""
    from ..animation_check import LABEL
    from ..blender_adapter.fault_inject import KINDS, SHAPES
    where = f"faults[{k}]"
    if not isinstance(fault, dict):
        raise ToolError(f"{where} は破綻の表（name・bone か role・frames・kind ほか）にしてください: {fault!r}")
    known = {"name", "bone", "role", "frames", "kind", "axis", "degrees", "delta", "shape", "ramp", "expect"}
    if set(fault) - known:
        raise ToolError(f"{where} の知らない鍵: {sorted(set(fault) - known)}（使えるのは {sorted(known)}）")
    if not isinstance(fault.get("name"), str) or not fault["name"]:
        raise ToolError(f"{where} の name（入れた破綻の名前）が要ります")
    if (fault.get("bone") is None) == (fault.get("role") is None) or not isinstance(fault.get("bone") or fault.get("role"), str):
        raise ToolError(f"{where} は bone（骨の名前）か role（人型の骨の役割）のどちらか 1 つにしてください")
    frames = fault.get("frames")
    if (not isinstance(frames, list) or len(frames) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) for v in frames)
            or frames[0] > frames[1]):
        raise ToolError(f"{where} の frames は [最初のコマ, 最後のコマ]（整数、最初 ≤ 最後）にしてください: {frames!r}")
    kind = fault.get("kind")
    if kind not in KINDS:
        raise ToolError(f"{where} の kind は {' か '.join(KINDS)} にしてください: {kind!r}")

    def numbers(value, length):
        return (isinstance(value, list) and len(value) == length
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value))
    if kind == "shift":
        if not numbers(fault.get("delta"), 3) or fault.get("axis") is not None or fault.get("degrees") is not None:
            raise ToolError(f"{where}: shift は delta（[x, y, z] m）だけにしてください")
    else:
        degrees = fault.get("degrees")
        if isinstance(degrees, bool) or not isinstance(degrees, (int, float)) or not math.isfinite(degrees) or fault.get("delta") is not None:
            raise ToolError(f"{where}: {kind} は degrees（度）と axis にしてください")
        axis = fault.get("axis")
        if kind == "world" and axis not in ("X", "Y", "Z"):
            raise ToolError(f"{where}: world の axis は \"X\"・\"Y\"・\"Z\" にしてください: {axis!r}")
        if kind == "local" and (not numbers(axis, 3) or not any(axis)):
            raise ToolError(f"{where}: local の axis は骨の中の向き [x, y, z]（0 でない）にしてください: {axis!r}")
    if fault.get("shape", "ramp") not in SHAPES:
        raise ToolError(f"{where} の shape は {' か '.join(SHAPES)} にしてください: {fault.get('shape')!r}")
    ramp = fault.get("ramp", 3)
    if isinstance(ramp, bool) or not isinstance(ramp, int) or ramp < 1:
        raise ToolError(f"{where} の ramp は 1 以上の整数にしてください: {ramp!r}")
    expect = fault.get("expect", {})
    signals = expect.get("signals") if isinstance(expect, dict) else None
    if (not isinstance(signals, list) or not signals or not all(s in LABEL for s in signals)
            or not isinstance(expect.get("part", []), list) or not all(isinstance(p, str) for p in expect.get("part", []))):
        raise ToolError(f"{where} の expect は {{\"signals\": [見つかるはずの信号], \"part\": [場所の言葉]}} にしてください: {expect!r}")
    return fault


@register("motion.inject_faults", inputs=("blend",), outputs=("blend", "expected", "report"), cacheable=False,
          summary="きれいな動きの .blend に、決まったコマでわざと破綻を入れて別の .blend に書き、入れた物の一覧（expected）を書く。"
                  "check.animation が入れた破綻を入れたコマで見つけるかを確かめる、正解の分かっている試験の素材を作る。破綻ごとに、骨"
                  "（bone か人型の役割 role）・コマ・入れ方（local: 骨の中の軸の回り、world: 付け根のまわりの世界の軸の回り、shift: 付け根を"
                  "動かす）・量と形（ramp・constant・alternate）・見つかるはずの信号と場所の言葉（expect）。触った骨にキーを打つ。"
                  "入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行）")
def inject_faults(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from . import number
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    from .vrm_ops import VRM_ADDON
    require(inputs, ("blend",), "motion.inject_faults")
    unknown = set(parameters) - INJECT_PARAMETERS
    if unknown:
        raise ToolError(f"motion.inject_faults の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(INJECT_PARAMETERS)}）")
    for name in ("blend", "expected"):
        if name not in outputs:
            raise ToolError(f"motion.inject_faults には outputs の {name} が要ります")
    faults = parameters.get("faults")
    if not isinstance(faults, list) or not faults:
        raise ToolError("motion.inject_faults には faults（入れる破綻の並び）が要ります")
    faults = [_fault(fault, k) for k, fault in enumerate(faults)]
    names = [fault["name"] for fault in faults]
    if len(set(names)) != len(names):
        raise ToolError(f"faults の name が重なっています: {names}")
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature はアーマチュアの名前にしてください: {armature!r}")
    if parameters.get("timeout") is not None:
        number(parameters, "timeout", minimum=0, exclusive=True)
    blender = _blender(parameters, context, "motion.inject_faults")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    settings_path, report_path = step_dir / "inject_settings.json", Path(outputs.get("report") or step_dir / "inject_report.json")
    atomic_json(settings_path, {"armature": armature, "faults": [{k: v for k, v in fault.items() if k != "expect"} for fault in faults]})
    record = launcher.run_blender(blender, INJECT_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                  "--out", str(outputs["blend"]), "--report", str(report_path)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    atomic_json(outputs["expected"], {"source": str(inputs["blend"]),
                                      "faults": [{"name": f["name"], "frames": f["frames"], "signals": f["expect"]["signals"],
                                                  "part": f["expect"].get("part", [])} for f in faults]})
    report = read_json(report_path)
    report["blender_version"] = record["blender_version"]
    atomic_json(report_path, report)
    missing = [row["name"] for row in report["faults"] if not row["keyed_frames"]]
    result = StepResult(observations=[{"faults": len(faults), "keyed": sum(len(row["keyed_frames"]) for row in report["faults"])}])
    if missing:                                                          # シーンのコマの範囲の外に置いた破綻
        result.unresolved.append(f"motion.inject_faults: シーンのコマの範囲の外で、入らなかった破綻があります: {missing}")
        result.review = True
    return result
