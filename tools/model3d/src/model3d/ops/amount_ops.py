"""区間ごとの動きの量を測る（check.motion_amount）。骨の通り道から、区間ごとの速さ・頭の上下の幅・体の向きの幅・止めの拍を数で出す。
計算だけ（Blender は使わない）。数を出すだけで、合否（どの区間が弱いか）は決めない。"""
import math

from ..io import ToolError
from . import register, require

PARAMETERS = {"sections", "grid", "marks", "dance", "speeds", "rise", "facing", "holds", "strict", "report_only"}


def _name_list(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value):
        raise ToolError(f"check.motion_amount の {where} は点の名前（空でない文字列）の並びにしてください: {value!r}")
    return list(value)


def _real(value, where, minimum=None, exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"check.motion_amount の {where} は数にしてください: {value!r}")
    if minimum is not None and ((value <= minimum) if exclusive else (value < minimum)):
        raise ToolError(f"check.motion_amount の {where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return float(value)


def _time(value, where):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return _real(value, where)
    if isinstance(value, str) and value:
        return value
    if isinstance(value, dict) and set(value) == {"beat"}:
        return {"beat": _real(value["beat"], f"{where}.beat")}
    raise ToolError(f"check.motion_amount の {where} は秒の数・印の名前・拍 {{\"beat\": k}} のどれかにしてください: {value!r}")


def settings_of(parameters):
    """パラメーターを確かめ、既定を足す（motion_amount.DEFAULTS）。"""
    from ..motion_amount import DEFAULTS
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise ToolError(f"check.motion_amount の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PARAMETERS)}）")
    sections = parameters.get("sections")
    if not isinstance(sections, list) or not sections:
        raise ToolError("check.motion_amount には sections（[{\"name\", \"from\", \"until\"}, …]。1 つ以上）が要ります")
    s = {"sections": []}
    for n, item in enumerate(sections):
        if not isinstance(item, dict) or set(item) - {"name", "from", "until"} or not {"from", "until"} <= set(item):
            raise ToolError(f"check.motion_amount の sections[{n}] は {{\"name\"（任意）, \"from\", \"until\"}} にしてください: {item!r}")
        entry = {"from": _time(item["from"], f"sections[{n}].from"), "until": _time(item["until"], f"sections[{n}].until")}
        if "name" in item:
            if not isinstance(item["name"], str) or not item["name"]:
                raise ToolError(f"check.motion_amount の sections[{n}].name は空でない文字列にしてください: {item['name']!r}")
            entry["name"] = item["name"]
        s["sections"].append(entry)
    grid = parameters.get("grid")
    if grid is not None:
        if not isinstance(grid, dict) or set(grid) != {"period", "k0_time"}:
            raise ToolError(f"check.motion_amount の grid は {{\"period\": 1 拍の秒, \"k0_time\": 拍 0 の秒}} にしてください: {grid!r}")
        grid = {"period": _real(grid["period"], "grid.period", 0, True), "k0_time": _real(grid["k0_time"], "grid.k0_time")}
    s["grid"] = grid
    marks = parameters.get("marks")
    if marks is not None:
        if not isinstance(marks, dict) or not all(isinstance(k, str) and k for k in marks):
            raise ToolError(f"check.motion_amount の marks は {{名前: 秒か拍}} にしてください: {marks!r}")
        marks = {k: _time(v, f"marks.{k}") for k, v in marks.items()}
        if any(isinstance(v, str) for v in marks.values()):
            raise ToolError("check.motion_amount の marks の値は秒の数か拍にしてください（印の名前は使えません）")
    s["marks"] = marks
    uses_beats = any(isinstance(v, dict) for item in s["sections"] for v in (item["from"], item["until"])) or \
        any(isinstance(v, dict) for v in (marks or {}).values())
    if uses_beats and grid is None:
        raise ToolError("check.motion_amount の sections か marks に拍 {\"beat\": k} がありますが、grid（拍の格子）がありません")
    dance = parameters.get("dance", {})
    if not isinstance(dance, dict) or set(dance) - {"frame0", "fps"}:
        raise ToolError(f"check.motion_amount の dance は {{\"frame0\": 踊りの時刻 0 秒の動きのコマ, \"fps\": 動きの fps}} にしてください: {dance!r}")
    s["dance"] = {"frame0": _real(dance.get("frame0", DEFAULTS["dance"]["frame0"]), "dance.frame0"),
                  "fps": _real(dance.get("fps", DEFAULTS["dance"]["fps"]), "dance.fps", 0, True)}
    speeds = parameters.get("speeds", DEFAULTS["speeds"])
    if not isinstance(speeds, dict) or not speeds:
        raise ToolError(f"check.motion_amount の speeds は {{名前: [点の名前, …]}}（1 つ以上）にしてください: {speeds!r}")
    s["speeds"] = {label: _name_list(names, f"speeds.{label}") for label, names in speeds.items()}
    rise = parameters.get("rise", DEFAULTS["rise"])
    if not isinstance(rise, str) or not rise:
        raise ToolError(f"check.motion_amount の rise は点の名前にしてください: {rise!r}")
    s["rise"] = rise
    facing = _name_list(parameters.get("facing", DEFAULTS["facing"]), "facing")
    if len(facing) != 2 or facing[0] == facing[1]:
        raise ToolError(f"check.motion_amount の facing は [左の点, 右の点]（別の 2 つ）にしてください: {facing!r}")
    s["facing"] = facing
    holds = parameters.get("holds", {})
    if not isinstance(holds, dict) or set(holds) - {"points", "speed", "min_beats"}:
        raise ToolError(f"check.motion_amount の holds は {{\"points\", \"speed\", \"min_beats\"}} にしてください: {holds!r}")
    min_beats = holds.get("min_beats", DEFAULTS["holds"]["min_beats"])
    if isinstance(min_beats, bool) or not isinstance(min_beats, int) or min_beats < 1:
        raise ToolError(f"check.motion_amount の holds.min_beats は 1 以上の整数にしてください: {min_beats!r}")
    s["holds"] = {"points": _name_list(holds["points"], "holds.points") if holds.get("points") is not None else None,
                  "speed": _real(holds.get("speed", DEFAULTS["holds"]["speed"]), "holds.speed", 0, True), "min_beats": min_beats}
    return s


@register("check.motion_amount", inputs=("path",), outputs=("report",),
          summary="区間ごとの動きの量: 骨の通り道（path。model3d-bone-path/0.1）から、区間（sections。秒か拍）ごとに、点の組の速さ（speeds。"
                  "既定は腰・両手・両足）、頭の上下の幅（rise）、体の向きの幅と回った量（facing の左右の点から）を出す。grid（拍の格子）があれば、"
                  "拍ごとの止めの点の速さと、holds.speed より遅い拍が holds.min_beats 以上続く止めも数える。数を出すだけで、どの区間が"
                  "弱いかは決めない（report_only で要確認にしない）。計算だけ")
def motion_amount(inputs, parameters, outputs, context):
    from .. import contracts
    from ..motion_amount import build
    from .check_ops import _finish
    require(inputs, ("path",), "check.motion_amount")
    settings = settings_of(parameters)
    report = build(settings, contracts.load("bone-path", inputs["path"]))
    summary = {k: v for k, v in report.items() if k != "beats"}
    if "report" in outputs:
        from ..io import atomic_json
        atomic_json(outputs["report"], report)
    return _finish(summary, {}, parameters, "動きの量")
