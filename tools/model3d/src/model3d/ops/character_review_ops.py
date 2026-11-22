"""Experimental local review operations; configs can reference changing art and models."""
from pathlib import Path

from ..io import ToolError, atomic_json
from . import StepResult, register, require
from .motion_ops import _blender

LOCAL_REVIEW = {"tools": ["blender"], "network_mode": "never", "external_process_mode": "always",
                "docs": "docs/character-review.md", "maturity": "experimental"}


def _prepare(inputs, parameters, outputs, context, name):
    require(inputs, ("spec",), name)
    if "report" not in outputs or parameters.keys() - {"blender"}:
        raise ToolError(f"{name}: expected output report and an optional blender parameter")
    return _blender(parameters, context, name)


@register("review.character_views", inputs=("spec",), outputs=("report",), cacheable=False,
          summary="立ち絵・修正前後を身体基準の左右6方向と同一時刻で比較。表情や輪郭を切った対照画像も表示する",
          capability={**LOCAL_REVIEW, "aliases": ["横顔", "多視点比較", "設定画照合", "表情の切り分け"]})
def character_views(inputs, parameters, outputs, context):
    from ..character_review import run
    blender = _prepare(inputs, parameters, outputs, context, "review.character_views")
    report = Path(outputs["report"])
    views = Path(context.step_dir) / "views"
    if report.name.rstrip(".").casefold() == views.name:
        views = report.with_name(report.name + ".views")
    result = run(inputs["spec"], views, blender, roundtrip_report=inputs.get("roundtrip"))
    atomic_json(report, result)
    return StepResult(observations=[{"kind": "character_review", **result}], review=True)


@register("check.character_roundtrip", inputs=("spec",), outputs=("report",), cacheable=False,
          summary="アニメ付きGLBを読み戻し、原本と同時刻の前腕などの断面と表情を測って確認コマを記録する",
          capability={**LOCAL_REVIEW, "aliases": ["配布モデル", "書き出し検証", "前腕の細り", "まばたき"]})
def character_roundtrip(inputs, parameters, outputs, context):
    from ..character_roundtrip import run
    blender = _prepare(inputs, parameters, outputs, context, "check.character_roundtrip")
    run(inputs["spec"], outputs["report"], blender)
    return StepResult(observations=[{"kind": "character_roundtrip", "report": str(outputs["report"])}])
