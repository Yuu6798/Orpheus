"""動きの検品の工程（docs/model3d.md の「動きを検品する」）。

- check.animation: 動きを付けた .blend（VRM の人型の骨）を全コマ調べて、貫通と破綻の候補を「いつ・どこが・どれだけ」の出来事で挙げる。
  直さない・採点しない・関門にしない（出来事はどれも要確認で、工程は出来事があれば needs_review。strict は無い）。測るのは Blender の中
  （blender_adapter/animation_sample.py）、数えるのは Blender の外（animation_check.py。numpy だけ）。骨へ載せる前の点を測る
  check.motion とは別の段（こちらは骨へ載せ、揺れものを焼き、手直しをした後の .blend を見る）。
"""
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

SAMPLE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "animation_sample.py"


def _parameters():
    from ..animation_check import DEFAULTS
    return {"armature", "frames", "skip_bones", "foot_lowest", "report_only", "blender", "addons", "blender_extensions", "timeout",
            *DEFAULTS}


@register("check.animation", inputs=("blend",), outputs=("report",), cacheable=False,
          summary="動きを付けた .blend（VRM の人型の骨）を全コマ調べて、破綻の候補を出来事（信号・場所・最初と最後のコマと秒・いちばん大きい"
                  "コマと値と位置）にまとめる。静的: 腕が胴・頭・脚に入る、腕どうし・脚どうしの重なり、足が床より下、手首の曲げとねじれ・首・"
                  "ひざの逆曲がり。動的: 向きの飛び、位置の飛び、震え、着いた足の滑り、全身の切り替え。体の形はそのモデルの初期姿勢の"
                  "メッシュから作る。直さない・採点しない（出来事はどれも status: review。出来事があれば needs_review、report_only なら"
                  "測るだけ）。しきい値の既定は仮の値。Blender（VRM Add-on）が要り、再利用しない（毎回実行）")
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
    if parameters.get("report_only") is not None and not isinstance(parameters["report_only"], bool):
        raise ToolError(f"report_only は true か false にしてください: {parameters['report_only']!r}")
    if parameters.get("timeout") is not None:
        number(parameters, "timeout", minimum=0, exclusive=True)
    blender = _blender(parameters, context, "check.animation")
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    step_dir = Path(context.step_dir)
    settings_path, sample_path, meta_path = step_dir / "animation_settings.json", step_dir / "sample.npz", step_dir / "sample.json"
    atomic_json(settings_path, {"armature": armature, "frames": span})
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
    atomic_json(outputs["report"], report)
    summary = {key: report[key] for key in ("schema_version", "overall", "frames", "fps", "counts", "skipped")}
    summary["events"] = len(report["events"])
    result = StepResult(observations=[summary])
    if report["events"] and not parameters.get("report_only"):
        result.unresolved.append(f"動きの検品: 出来事 {len(report['events'])} 件（数と場所だけ。絵で見て、直すかを決める）")
        result.review = True
    return result
