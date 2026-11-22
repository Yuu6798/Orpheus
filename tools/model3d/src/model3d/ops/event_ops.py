"""出来事を置く前の段取り: 踊りと打音から出来事の時刻の候補を拾う（plan.motion_marks）、世界の点があるコマの画面のどこに映るかを
出す（check.on_screen）。
出来事の形そのものは、render.show のフックから blender_adapter の fx_* を呼んで作る（docs/model3d.md の「演出の出来事」）。"""
import math

from ..io import ToolError, atomic_json
from . import StepResult, register, require

MARKS_PARAMETERS = {"dance", "landing", "extremes", "hits"}
SCREEN_PARAMETERS = {"points", "frames", "margin", "dance", "strict", "report_only"}


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


# ---- check.on_screen ----
def screen_settings(parameters):
    unknown = set(parameters) - SCREEN_PARAMETERS
    if unknown:
        raise ToolError(f"check.on_screen の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(SCREEN_PARAMETERS)}）")
    points = parameters.get("points")
    if not isinstance(points, dict) or not points:
        raise ToolError("check.on_screen には points（{名前: [x, y, z] か {\"point\": 骨の点の名前, \"offset\": [x, y, z]}}）が要ります")
    s = {"points": {}, "dance": _dance(parameters, "check.on_screen")}
    for name, spec in points.items():
        if isinstance(spec, (list, tuple)):
            s["points"][name] = {"world": _vector(spec, f"check.on_screen の points.{name}")}
        elif isinstance(spec, dict) and "point" in spec and set(spec) <= {"point", "offset"}:
            s["points"][name] = {"point": _names([spec["point"]], f"points.{name}.point")[0],
                                 "offset": _vector(spec.get("offset", [0, 0, 0]), f"check.on_screen の points.{name}.offset")}
        else:
            raise ToolError(f"check.on_screen の points.{name} は [x, y, z] か {{\"point\", \"offset\"}} にしてください: {spec!r}")
    frames = parameters.get("frames")
    if frames is not None and (not isinstance(frames, list) or not frames or
                               not all(isinstance(T, int) and not isinstance(T, bool) for T in frames)):
        raise ToolError(f"check.on_screen の frames はコマ T（整数）の並びにしてください: {frames!r}")
    s["frames"] = frames
    s["margin"] = _real(parameters.get("margin", 0.0), "check.on_screen の margin", 0)
    if s["margin"] >= 0.5:
        raise ToolError(f"check.on_screen の margin は 0.5 より小さくしてください（画面の幅・高さに対する割合）: {s['margin']}")
    return s


def on_screen(settings, cameras, path=None):
    """コマごと・点ごとの画面の位置（x・y は 0〜1。0 が左・上）と奥行き（m。負はカメラの後ろ）と、画面の中か。"""
    import numpy as np

    from ..blender_adapter.fx_events import EventError, Path as BonePath
    from ..shot_plan import project
    width, height = (float(v) for v in cameras["resolution"])
    by_T = {frame["T"]: frame for frame in cameras["frames"]}
    wanted = settings["frames"] if settings["frames"] is not None else sorted(by_T)
    missing = sorted(set(wanted) - set(by_T))
    if missing:
        raise ToolError(f"check.on_screen: カメラの控えに無いコマ: {missing[:10]}")
    needs_path = any("point" in spec for spec in settings["points"].values())
    if needs_path and path is None:
        raise ToolError("check.on_screen: 骨の点を読むには、入力の path（骨の通り道）が要ります")
    bones = None
    if needs_path:
        try:
            bones = BonePath(path)
        except EventError as error:
            raise ToolError(f"check.on_screen: {error}") from error
    margin = settings["margin"]
    rows, outside = [], []
    for T in wanted:
        frame = by_T[T]
        matrix = np.asarray(frame["matrix"], float)
        for name, spec in settings["points"].items():
            if "world" in spec:
                point = np.asarray(spec["world"], float)
            else:
                if "f" not in frame:
                    raise ToolError(f"check.on_screen: 骨の点を読むには、カメラの控えのコマに f（動きのコマ）が要ります（T {T}）")
                try:
                    point = bones.at(spec["point"], float(frame["f"])) + np.asarray(spec["offset"], float)
                except EventError as error:
                    raise ToolError(f"check.on_screen: {error}") from error
            depth = -float((point - matrix[:3, 3]) @ matrix[:3, 2])
            row = {"T": T, "point": name, "depth": depth}
            if depth <= 0:
                row.update(x=None, y=None, inside=False, behind=True)
            else:
                px, py = project(matrix, float(frame["focal_px"]), frame["principal_px"], point)
                x, y = px / width, py / height
                row.update(x=x, y=y, behind=False, inside=margin <= x <= 1 - margin and margin <= y <= 1 - margin)
            rows.append(row)
            if not row["inside"]:
                outside.append(row)
    return {"schema_version": "model3d-on-screen/0.1", "resolution": [width, height], "margin": margin, "frames": len(wanted),
            "points": sorted(settings["points"]), "rows": rows, "outside": len(outside), "behind": sum(1 for r in outside if r["behind"]),
            "pass": not outside,
            "failures": [f"T {r['T']} の {r['point']} が" + ("カメラの後ろ" if r["behind"] else f"画面の外（x {r['x']:.3f}・y {r['y']:.3f}）")
                         for r in outside[:20]]}


@register("check.on_screen", inputs=("cameras", "path"), outputs=("report",),
          summary="世界の点（points。[x, y, z] か骨の通り道の点 + offset）が、カメラの控え（cameras。model3d-cameras/0.1）のコマ（frames。"
                  "省くと全部）の画面のどこに映るかを出す（x・y は 0〜1、0 が左・上。奥行き m）。margin（割合）の内側に無い点・カメラの"
                  "後ろの点を数える（全部が内側なら合格。report_only で数を出すだけ）。縦長の画面は横が狭いので、出来事を置く前に使う。"
                  "計算だけ（plan.shots と同じ写し方）")
def check_on_screen(inputs, parameters, outputs, context):
    from .. import contracts
    from .check_ops import _finish
    require(inputs, ("cameras",), "check.on_screen")
    settings = screen_settings(parameters)
    path = contracts.load("bone-path", inputs["path"]) if inputs.get("path") is not None else None
    report = on_screen(settings, contracts.load("cameras", inputs["cameras"]), path)
    summary = {k: v for k, v in report.items() if k != "rows"}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
        outputs = {}
    return _finish(summary, outputs, parameters, "画面の位置")
