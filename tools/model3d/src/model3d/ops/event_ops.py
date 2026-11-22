"""出来事を置く前の段取り: 踊りと打音から、出来事の時刻の候補を拾う（plan.motion_marks）。
出来事の形そのものは、render.show のフックから blender_adapter の fx_* を呼んで作る（docs/model3d.md の「演出の出来事」）。"""
import math

from ..io import ToolError, atomic_json
from . import StepResult, register, require

MARKS_PARAMETERS = {"dance", "landing", "extremes", "hits"}


def _real(value, where, minimum=None, exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は数にしてください: {value!r}")
    if minimum is not None and ((value <= minimum) if exclusive else (value < minimum)):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return float(value)


def _vector(value, where):
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ToolError(f"{where} は 3 つの数 [x, y, z] にしてください: {value!r}")
    return [_real(v, where) for v in value]


def _names(value, where):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value):
        raise ToolError(f"{where} は点の名前（空でない文字列）の並びにしてください: {value!r}")
    return list(value)


def _dance(parameters, operation):
    dance = parameters.get("dance", {})
    if not isinstance(dance, dict) or set(dance) - {"frame0", "fps"}:
        raise ToolError(f"{operation} の dance は {{\"frame0\": 踊りの時刻 0 秒の動きのコマ, \"fps\": 動きの fps}} にしてください: {dance!r}")
    return {"frame0": _real(dance.get("frame0", 1), f"{operation} の dance.frame0"),
            "fps": _real(dance.get("fps", 30), f"{operation} の dance.fps", 0, True)}


# ---- plan.motion_marks ----
def marks_settings(parameters):
    unknown = set(parameters) - MARKS_PARAMETERS
    if unknown:
        raise ToolError(f"plan.motion_marks の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(MARKS_PARAMETERS)}）")
    s = {"dance": _dance(parameters, "plan.motion_marks")}
    landing = parameters.get("landing")
    if landing is not None:
        keys = {"points", "drop", "fall", "still", "window", "gap"}
        if not isinstance(landing, dict) or set(landing) != keys:
            raise ToolError(f"plan.motion_marks の landing は {sorted(keys)} を全部書いてください: {landing!r}")
        window = landing["window"]
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ToolError(f"plan.motion_marks の landing.window は 1 以上の整数（標本の数）にしてください: {window!r}")
        s["landing"] = {"points": _names(landing["points"], "plan.motion_marks の landing.points"), "window": window,
                        **{k: _real(landing[k], f"plan.motion_marks の landing.{k}", 0) for k in ("drop", "fall", "still", "gap")}}
    extremes = parameters.get("extremes")
    if extremes is not None:
        if not isinstance(extremes, list) or not extremes:
            raise ToolError("plan.motion_marks の extremes は [{\"name\", \"point\", \"axis\", \"kind\", \"prominence\", \"gap\"}, …] にしてください")
        s["extremes"], seen = [], set()
        for n, item in enumerate(extremes):
            keys = {"name", "point", "axis", "kind", "prominence", "gap"}
            if not isinstance(item, dict) or set(item) != keys:
                raise ToolError(f"plan.motion_marks の extremes[{n}] は {sorted(keys)} を全部書いてください: {item!r}")
            if not isinstance(item["name"], str) or not item["name"] or item["name"] in seen:
                raise ToolError(f"plan.motion_marks の extremes[{n}].name は、ほかと違う空でない文字列にしてください: {item['name']!r}")
            seen.add(item["name"])
            if item["kind"] not in ("max", "min"):
                raise ToolError(f"plan.motion_marks の extremes[{n}].kind は max か min にしてください: {item['kind']!r}")
            axis = _vector(item["axis"], f"plan.motion_marks の extremes[{n}].axis")
            if math.hypot(*axis) == 0:
                raise ToolError(f"plan.motion_marks の extremes[{n}].axis は長さのある向きにしてください")
            s["extremes"].append({"name": item["name"], "point": _names([item["point"]], f"extremes[{n}].point")[0], "axis": axis,
                                  "kind": item["kind"], "prominence": _real(item["prominence"], f"extremes[{n}].prominence", 0),
                                  "gap": _real(item["gap"], f"extremes[{n}].gap", 0, True)})
    hits = parameters.get("hits")
    if hits is not None:
        if not isinstance(hits, list):
            raise ToolError(f"plan.motion_marks の hits は秒の並びにしてください: {hits!r}")
        s["hits"] = [_real(t, "plan.motion_marks の hits") for t in hits]
    if not any(k in s for k in ("landing", "extremes", "hits")):
        raise ToolError("plan.motion_marks には landing・extremes・hits のどれか 1 つ以上が要ります")
    return s


@register("plan.motion_marks", inputs=("path",), outputs=("marks",),
          summary="出来事の時刻の候補を拾う: 骨の通り道（path。model3d-bone-path/0.1）から、足の着地（landing: 下りて止まる標本）、"
                  "点を向き（axis）に写した値の山か谷（extremes: しゃがみの底・指先がいちばん前など）、曲の打音の秒（hits）が届く最初の動きの"
                  "コマを出す（marks: model3d-motion-marks/0.1）。コマの番号は出来事の表に frame でそのまま書ける。拾うだけで、どれを"
                  "出来事にするかは決めない。計算だけ")
def motion_marks(inputs, parameters, outputs, context):
    from .. import contracts
    from ..motion_marks import build
    require(inputs, ("path",), "plan.motion_marks")
    if "marks" not in outputs:
        raise ToolError("plan.motion_marks には outputs の marks が要ります")
    report = build(marks_settings(parameters), contracts.load("bone-path", inputs["path"]))
    atomic_json(outputs["marks"], report)
    return StepResult(observations=[{"kind": "motion_marks", **report["counts"]}])
