"""2 つの台本の値を、同じコマ T どうしで比べる（check.track_match）。変えたコマだけ描き直すときに、前の版のコマを写してよいコマ
（台本の値が同じコマ）と、描き直すコマ（render.stage・render.passes の only にそのまま渡せる）を出す。計算だけ。"""
import math

from ..io import ToolError, atomic_json, read_json
from . import register, require

PARAMETERS = {"ignore", "tolerance", "top", "strict", "report_only"}


def _load(path, side):
    from .. import contracts
    from ..track_match import KINDS
    data = read_json(path)
    kind = contracts.kind_of(data)
    if kind not in KINDS:
        raise ToolError(f"check.track_match の {side} は、コマの並びの記録（{', '.join('model3d-' + k + '/0.1' for k in KINDS)}）にしてください: "
                        f"{data.get('schema_version') if isinstance(data, dict) else data!r}")
    return kind, contracts.validate(kind, data, path)


@register("check.track_match", inputs=("left", "right"), outputs=("report",),
          summary="2 つの台本の値（コマの並びの記録。model3d-cameras/0.1 どうしか model3d-stage-track/0.1 どうし）を、同じ T どうしで比べる。"
                  "left は前の版、right は今の版。T のほかの欄を全部たどり、数は差が tolerance（既定 0）以下、数でない値は同じなら「同じ」。"
                  "同じコマの範囲（前の版のコマを写せる）と、描き直すコマの範囲と T の並び（今の版にある T だけ。空でなければ render.stage・"
                  "render.passes の only に渡せる。空なら描き直す段を飛ばす）、欄ごとの差の最大を出す。"
                  "ignore の欄は見ない。測るだけ（写すかは人が決める。report_only で要確認にしない）。計算だけ")
def track_match(inputs, parameters, outputs, context):
    from ..track_match import compare
    from .check_ops import _finish
    require(inputs, ("left", "right"), "check.track_match")
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise ToolError(f"check.track_match の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PARAMETERS)}）")
    ignore = parameters.get("ignore", [])
    if not isinstance(ignore, list) or not all(isinstance(v, str) and v for v in ignore):
        raise ToolError(f"check.track_match の ignore は欄の名前（\"f\"・\"camera.lens\" など）の並びにしてください: {ignore!r}")
    tolerance = parameters.get("tolerance", 0.0)
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(tolerance) or tolerance < 0:
        raise ToolError(f"check.track_match の tolerance は 0 以上の数にしてください: {tolerance!r}")
    top = parameters.get("top", 5)
    if isinstance(top, bool) or not isinstance(top, int) or top < 1:
        raise ToolError(f"check.track_match の top は 1 以上の整数にしてください: {top!r}")
    kind_left, left = _load(inputs["left"], "left")
    kind_right, right = _load(inputs["right"], "right")
    if kind_left != kind_right:
        raise ToolError(f"check.track_match の left と right は同じ種類の記録にしてください（{kind_left} と {kind_right}）")
    report = compare(left, right, ignore, float(tolerance), top)
    report.update(kind=kind_left, left=str(inputs["left"]), right=str(inputs["right"]))
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    summary = {k: v for k, v in report.items() if k not in ("rows", "redo_T")}
    return _finish(summary, {}, parameters, "台本の値の比べ")
