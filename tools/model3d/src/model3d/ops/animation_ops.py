"""動きの検品の工程（docs/model3d.md の「動きを検品する」）。

- check.animation: 動きを付けた .blend（VRM の人型の骨）を全コマ調べて、貫通と破綻の候補を「いつ・どこが・どれだけ」の出来事で挙げる。
  直さない・採点しない・関門にしない（出来事はどれも要確認で、工程は出来事があれば needs_review。strict は無い）。測るのは Blender の中
  （blender_adapter/animation_sample.py）、数えるのは Blender の外（animation_check.py。numpy だけ）。証拠の絵はもう一度 Blender で
  描く（blender_adapter/animation_evidence.py）。骨へ載せる前の点を測る check.motion とは別の段（こちらは骨へ載せ、揺れものを焼き、
  手直しをした後の .blend を見る）。
"""
import os
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

SAMPLE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "animation_sample.py"
EVIDENCE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "animation_evidence.py"
IMAGE_SIZE, IMAGE_QUALITY = 480, 88          # 証拠の絵（試作と同じ）


def _parameters():
    from ..animation_check import DEFAULTS
    return {"armature", "frames", "skip_bones", "foot_lowest", "cloth", "max_images", "report_only", "blender", "addons",
            "blender_extensions", "timeout", *DEFAULTS}


@register("check.animation", inputs=("blend",), outputs=("report", "table", "sheet"), cacheable=False,
          summary="動きを付けた .blend（VRM の人型の骨）を全コマ調べて、破綻の候補を出来事（信号・場所・最初と最後のコマと秒・いちばん大きい"
                  "コマと値と位置）にまとめる。静的: 腕が胴・頭・脚に入る、腕どうし・脚どうしの重なり、体が布（揺れものの骨で動くメッシュ）を"
                  "突き抜ける、足が床より下、手首の曲げとねじれ・首・ひざの逆曲がり。動的: 向きの飛び、位置の飛び、震え、着いた足の滑り、"
                  "全身の切り替え。体の形はそのモデルの初期姿勢のメッシュから作る。table は読む用の表（Markdown）、sheet は証拠の絵"
                  "（場所を狙うカメラと赤い印。max_images 件まで）の一覧。直さない・採点しない（出来事はどれも status: review。"
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
    atomic_json(outputs["report"], report)
    if "table" in outputs:
        Path(outputs["table"]).write_text(animation_check.table_markdown(report, report.get("images_directory", animation_check.IMAGE_DIRECTORY)),
                                          encoding="utf-8", newline="\n")
    summary = {key: report[key] for key in ("schema_version", "overall", "frames", "fps", "counts", "skipped")}
    summary["events"] = len(report["events"])
    result = StepResult(observations=[summary])
    if report["events"] and not parameters.get("report_only"):
        result.unresolved.append(f"動きの検品: 出来事 {len(report['events'])} 件（数と場所だけ。絵で見て、直すかを決める）")
        result.review = True
    return result
