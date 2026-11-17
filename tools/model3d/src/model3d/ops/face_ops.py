"""表情を形で作る工程（docs/model3d.md の「表情を形で作る」）。頭のメッシュを書き出し、Blender の外で形状キーの動きを計算し、
形状キーとして入れて VRM の表情に割り当てる。リポジトリ外の 3 作目の VRM のダンスで通した手順を工程にしたもの。

- model.export_mesh: 物体のメッシュ（頭など）と色の絵を、Blender の外で計算するための npz と PNG に書き出す（Blender の中で）。
- model.face_shapes: 頭のメッシュから、表情の形状キーの頂点の動き（まばたき・笑顔の閉じ目・眉・口角）を計算する（Blender の外。numpy だけ）。
- model.shape_keys: 計算した形を、メッシュの形状キーとして入れる（Blender の中で）。VRM の表情への割り当ては vrm.expressions（vrm_ops）。
- render.face_sheet: 表情の組み合わせを、いくつかの向きから描いて一覧の絵に並べる（描くのは Blender の中、並べるのは Pillow）。
- texture.mouth_inside: 色の絵の、口の中の面と部品が使う所だけを塗る（Blender の外。numpy と Pillow）。
"""
import math
import re
from pathlib import Path

from ..io import ToolError, atomic_json, read_json
from . import StepResult, register, require

EXPORT_MESH_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "mesh_export.py"
COMMON = {"blender", "addons", "blender_extensions", "timeout"}


def _refuse_unknown(values, allowed, where):
    unknown = set(values) - set(allowed)
    if unknown:
        raise ToolError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは {sorted(allowed)}）")


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


@register("model.export_mesh", inputs=("blend",), outputs=("mesh", "texture", "report"), cacheable=False,
          summary="物体（object）のメッシュを、Blender の外で計算するための npz に書き出す（Blender の中で）: 頂点の基本の形の世界の座標（m）・"
                  "辺・三角形とその面の角・uv・法線。texture を挙げると、その物体の材質が使う色の絵（image は名前の一部。省くと使う絵が 1 つの"
                  "ときだけ）を PNG で書く。.blend は変えない")
def export_mesh(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "model.export_mesh")
    _refuse_unknown(parameters, COMMON | {"object", "image"}, "model.export_mesh")
    settings = {"object": _name(parameters.get("object"), "object")}
    if parameters.get("image") is not None:
        settings["image"] = _name(parameters["image"], "image")
    if "mesh" not in outputs or "report" not in outputs:
        raise ToolError("model.export_mesh には outputs の mesh と report が要ります")
    if Path(outputs["mesh"]).suffix.lower() != ".npz":
        raise ToolError(f"model.export_mesh の outputs の mesh は .npz にしてください: {Path(outputs['mesh']).name}")
    if "texture" in outputs and Path(outputs["texture"]).suffix.lower() != ".png":
        raise ToolError(f"model.export_mesh の outputs の texture は .png にしてください: {Path(outputs['texture']).name}")
    if "image" in settings and "texture" not in outputs:
        raise ToolError("image は outputs の texture（色の絵の PNG）を挙げたときだけ使えます")
    blender = _blender(parameters, context, "model.export_mesh")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "export_mesh_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--out-mesh", outputs["mesh"], "--report", outputs["report"]]
    if "texture" in outputs:
        args += ["--out-texture", outputs["texture"]]
    record = launcher.run_blender(blender, EXPORT_MESH_SCRIPT, None, args, log=step_dir / "blender.log", timeout=_timeout(parameters),
                                  addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "export_mesh", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   **{key: report.get(key) for key in ("object", "vertices", "edges", "triangles", "uv_layer", "shape_keys", "scale_length",
                                                       "texture")}}
    return StepResult(observations=[observation])


def _real(value, where, minimum=None, exclusive=False, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は有限の数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    if maximum is not None and value > maximum:
        raise ToolError(f"{where} は {maximum} 以下にしてください: {value!r}")
    return float(value)


def _span(value, where, minimum=None):
    if not isinstance(value, list) or len(value) != 2:
        raise ToolError(f"{where} は [下, 上]（m）にしてください: {value!r}")
    low, high = _real(value[0], where, minimum), _real(value[1], where, minimum)
    if low >= high:
        raise ToolError(f"{where} は [下, 上]（下 < 上）にしてください: {value!r}")
    return [low, high]


def _integer(value, where, minimum):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ToolError(f"{where} は {minimum} 以上の整数にしてください: {value!r}")
    return value


def _section(parameters, key, where, required, optional=()):
    """parameters[key] の辞書を確かめる（知らない鍵は止め、required は全部要る）。"""
    value = parameters.get(key)
    if not isinstance(value, dict):
        raise ToolError(f"{where} は {{{', '.join(required)}}} の辞書にしてください: {value!r}")
    _refuse_unknown(value, set(required) | set(optional), where)
    missing = [name for name in required if name not in value]
    if missing:
        raise ToolError(f"{where} に {', '.join(missing)} が要ります")
    return value


def _raster_size(width, z, resolution, where, limit=20000):
    """正面の z バッファの画素の数を確かめる（大きすぎる窓や細かすぎる画素でメモリを使い切らないように）。"""
    columns, rows = int(round(width / resolution)), int(round((z[1] - z[0]) / resolution))
    if columns < 2 or rows < 2 or columns > limit or rows > limit:
        raise ToolError(f"{where} を resolution（{resolution} m）の画素にすると {columns} × {rows} になります（2〜{limit} にしてください）")


EYE_DEFAULT_KEYS = {"min_rows", "gap_px", "smooth_px", "edge_rows", "corner_margin", "iris_margin", "iris_gap", "cover_gap", "cover_above",
                    "lower_depth_gain"}


def _eye_settings(raw, resolution):
    find = _section(raw, "find", "eyes.find", ("x", "z", "front_y"))
    window = _section(raw, "window", "eyes.window", ("half_width", "z"))
    iris = _section(raw, "iris", "eyes.iris", ("front_y", "max_size", "max_depth", "back_y"))
    region = _section(raw, "region", "eyes.region", ("front_y", "max_part_size"))
    upper = _section(raw, "upper", "eyes.upper", ("keep", "fade"))
    lower = _section(raw, "lower", "eyes.lower", ("fade",))
    eyes = {"find": {"x": _span(find["x"], "eyes.find.x", 0.0), "z": _span(find["z"], "eyes.find.z"),
                     "front_y": _real(find["front_y"], "eyes.find.front_y")},
            "window": {"half_width": _real(window["half_width"], "eyes.window.half_width", 0.0, exclusive=True),
                       "z": _span(window["z"], "eyes.window.z")},
            "iris": {"front_y": _real(iris["front_y"], "eyes.iris.front_y"),
                     "max_size": _real(iris["max_size"], "eyes.iris.max_size", 0.0, exclusive=True),
                     "max_depth": _real(iris["max_depth"], "eyes.iris.max_depth", 0.0, exclusive=True),
                     "back_y": _real(iris["back_y"], "eyes.iris.back_y")},
            "region": {"front_y": _real(region["front_y"], "eyes.region.front_y"),
                       "max_part_size": _real(region["max_part_size"], "eyes.region.max_part_size", 0.0, exclusive=True)},
            "upper": {"keep": _real(upper["keep"], "eyes.upper.keep", 0.0), "fade": _real(upper["fade"], "eyes.upper.fade", 0.0, exclusive=True)},
            "lower": {"fade": _real(lower["fade"], "eyes.lower.fade", 0.0, exclusive=True)},
            "close_at": _real(raw["close_at"], "eyes.close_at", 0.0, maximum=1.0),
            "iris_back": _real(raw["iris_back"], "eyes.iris_back", 0.0)}
    if raw.get("brow") is not None:
        brow = _section(raw, "brow", "eyes.brow", ("front_y", "z", "min_size", "min_z"))
        eyes["brow"] = {"front_y": _real(brow["front_y"], "eyes.brow.front_y"), "z": _span(brow["z"], "eyes.brow.z"),
                        "min_size": _real(brow["min_size"], "eyes.brow.min_size", 0.0), "min_z": _real(brow["min_z"], "eyes.brow.min_z")}
    if raw.get("smile") is not None:
        smile = _section(raw, "smile", "eyes.smile", ("arc",), ("power", "clip"))
        eyes["smile"] = {"arc": _real(smile["arc"], "eyes.smile.arc", 0.0)}
        if "power" in smile:
            eyes["smile"]["power"] = _real(smile["power"], "eyes.smile.power", 0.0, exclusive=True)
        if "clip" in smile:
            clip = smile["clip"]
            if not isinstance(clip, list) or len(clip) != 2:
                raise ToolError(f"eyes.smile.clip は [下の縁からの割合, 上の縁からの割合] にしてください: {clip!r}")
            low, high = _real(clip[0], "eyes.smile.clip", 0.0), _real(clip[1], "eyes.smile.clip", 0.0)
            if low + high >= 1.0:
                raise ToolError(f"eyes.smile.clip の 2 つの割合の和は 1 より小さくしてください: {clip!r}")
            eyes["smile"]["clip"] = [low, high]
    for key, minimum in (("min_rows", 1), ("gap_px", 1), ("smooth_px", 1), ("edge_rows", 1)):   # gap_px 0 は隣の列まで切るので 1 から
        if key in raw:
            eyes[key] = _integer(raw[key], f"eyes.{key}", minimum)
    if eyes.get("smooth_px", 15) % 2 == 0:
        raise ToolError(f"eyes.smooth_px は奇数にしてください（左右対称の窓）: {eyes['smooth_px']}")
    for key, minimum, exclusive in (("corner_margin", 0.0, True), ("iris_margin", 0.0, True), ("iris_gap", 0.0, False),
                                    ("cover_gap", 0.0, False), ("cover_above", 0.0, False), ("lower_depth_gain", 0.0, True)):
        if key in raw:
            eyes[key] = _real(raw[key], f"eyes.{key}", minimum, exclusive)
    _raster_size(2 * eyes["window"]["half_width"], eyes["window"]["z"], resolution, "eyes.window")
    return eyes


def _brow_settings(brows):
    shapes = brows["shapes"]
    if not isinstance(shapes, dict) or not shapes:
        raise ToolError(f"brows.shapes は {{形の名前: {{\"inner\": 内側の高さの動き, \"outer\": 外側}}}} にしてください: {shapes!r}")
    checked = {}
    for name, amount in shapes.items():
        if not isinstance(name, str) or not name or name in ("blink", "blink_smile"):
            raise ToolError(f"brows.shapes の名前は、空でなく blink・blink_smile でない文字列にしてください: {name!r}")
        if not isinstance(amount, dict):
            raise ToolError(f"brows.shapes.{name} は {{\"inner\", \"outer\"}} にしてください: {amount!r}")
        _refuse_unknown(amount, ("inner", "outer"), f"brows.shapes.{name}")
        checked[name] = {"inner": _real(amount.get("inner"), f"brows.shapes.{name}.inner"),
                         "outer": _real(amount.get("outer"), f"brows.shapes.{name}.outer")}
    return {"forward": _real(brows["forward"], "brows.forward"), "shapes": checked}


def _mouth_settings(raw, resolution):
    window = _section(raw, "window", "mouth.window", ("half_width", "z"))
    zone = _section(raw, "zone", "mouth.zone", ("half_width",))
    seam = _section(raw, "seam", "mouth.seam", ("depth", "centre", "band"))
    corner = _section(raw, "corner", "mouth.corner", ("radius", "depth"))
    smile = _section(raw, "smile", "mouth.smile", ("out", "up", "back"))
    mouth = {"window": {"half_width": _real(window["half_width"], "mouth.window.half_width", 0.0, exclusive=True),
                        "z": _span(window["z"], "mouth.window.z")},
             "zone": {"half_width": _real(zone["half_width"], "mouth.zone.half_width", 0.0, exclusive=True)},
             "seam": {key: _real(seam[key], f"mouth.seam.{key}", 0.0, exclusive=True) for key in ("depth", "centre", "band")},
             "corner": {"radius": _real(corner["radius"], "mouth.corner.radius", 0.0, exclusive=True),
                        "depth": _real(corner["depth"], "mouth.corner.depth")},
             "smile": {key: _real(smile[key], f"mouth.smile.{key}") for key in ("out", "up", "back")}}
    if "visible" in raw:
        mouth["visible"] = _real(raw["visible"], "mouth.visible", 0.0)
    if "bins" in raw:
        mouth["bins"] = _integer(raw["bins"], "mouth.bins", 2)
    if "corner_percentile" in raw:
        mouth["corner_percentile"] = _real(raw["corner_percentile"], "mouth.corner_percentile", 0.0, maximum=100.0)
    if "min_points" in raw:
        mouth["min_points"] = _integer(raw["min_points"], "mouth.min_points", 1)
    _raster_size(2 * mouth["window"]["half_width"], mouth["window"]["z"], resolution, "mouth.window")
    _zone_in_window(mouth, "mouth.")
    return mouth


def _zone_in_window(settings, prefix):
    """口のまわり（zone）は、正面から描く窓（window）の中だけにする。窓の外の頂点は、窓の端の列の奥行きと比べてしまい、
    見える面（ほおなど）を見えないと数えうる（model.face_shapes の口・texture.mouth_inside・check.mouth で同じ）。"""
    zone, window = settings["zone"]["half_width"], settings["window"]["half_width"]
    if zone > window:
        raise ToolError(f"{prefix}zone.half_width（{zone}）は {prefix}window.half_width（{window}）以下にしてください"
                        "（窓の外の頂点は、見えるかどうかを窓の端の奥行きと比べてしまう）")


def face_shapes_settings(parameters):
    """model.face_shapes のパラメーターを確かめ、face_shapes.build の設定にする（長さは m。頭の部品の箱と動かす量に既定は無い）。"""
    _refuse_unknown(parameters, {"front_y", "resolution", "eyes", "brows", "mouth"}, "model.face_shapes")
    settings = {"front_y": _real(parameters.get("front_y"), "front_y")}
    if "resolution" in parameters:
        settings["resolution"] = _real(parameters["resolution"], "resolution", 0.0, exclusive=True)
    resolution = settings.get("resolution", 0.0001)
    if parameters.get("eyes") is None and parameters.get("mouth") is None:
        raise ToolError("model.face_shapes には eyes（まばたき・笑顔の閉じ目）か mouth（口角）が要ります")
    if parameters.get("eyes") is not None:
        raw = _section(parameters, "eyes", "eyes", ("find", "window", "iris", "region", "upper", "lower", "close_at", "iris_back"),
                       {"brow", "smile"} | EYE_DEFAULT_KEYS)
        settings["eyes"] = _eye_settings(raw, resolution)
    if parameters.get("brows") is not None:
        if "eyes" not in settings or "brow" not in settings["eyes"]:
            raise ToolError("brows は eyes.brow（眉の部品の見つけ方）といっしょに使ってください（眉の部品は目の窓から探す）")
        settings["brows"] = _brow_settings(_section(parameters, "brows", "brows", ("forward", "shapes")))
    if parameters.get("mouth") is not None:
        raw = _section(parameters, "mouth", "mouth", ("window", "zone", "seam", "corner", "smile"),
                       ("visible", "bins", "corner_percentile", "min_points"))
        settings["mouth"] = _mouth_settings(raw, resolution)
    return settings


@register("model.face_shapes", inputs=("mesh",), outputs=("shapes", "report", "debug"),
          summary="頭のメッシュ（mesh。model.export_mesh の npz）から、表情の形状キーの頂点の動きを計算する（Blender の外。numpy だけ）: "
                  "まばたき（blink_L・blink_R）と笑顔の閉じ目（blink_smile_*。eyes.smile）は、正面から見える目の開きの上下の縁を測って閉じる線へ"
                  "寄せる。眉（brows）は部品をそのまま動かす。口角（mouth_smile。mouth）は口の端のまわりだけに掛かる重みで動かす。shapes は形の"
                  "名前ごとの動き（世界の座標、m）の npz。部品の箱と動かす量に既定は無い（頭ごとに決める）")
def face_shapes(inputs, parameters, outputs, context):
    import numpy as np
    from .. import face_shapes as computed
    from ..mesh import _npz_arrays
    require(inputs, ("mesh",), "model.face_shapes")
    settings = face_shapes_settings(parameters)
    if "shapes" not in outputs or "report" not in outputs:
        raise ToolError("model.face_shapes には outputs の shapes と report が要ります")
    for key in ("shapes", "debug"):
        if key in outputs and Path(outputs[key]).suffix.lower() != ".npz":
            raise ToolError(f"model.face_shapes の outputs の {key} は .npz にしてください: {Path(outputs[key]).name}")
    arrays = _npz_arrays(inputs["mesh"], "頭のメッシュの npz", ["co", "edges", "tris"])
    co, edges, tris = arrays["co"], arrays["edges"], arrays["tris"]
    if co.ndim != 2 or co.shape[1] != 3 or not np.issubdtype(co.dtype, np.floating) or not np.isfinite(co).all():
        raise ToolError(f"co は有限の頂点の位置 (N, 3) にしてください（形 {co.shape}、{co.dtype}）")
    for name, value, width in (("edges", edges, 2), ("tris", tris, 3)):
        if value.ndim != 2 or value.shape[1] != width or not np.issubdtype(value.dtype, np.integer):
            raise ToolError(f"{name} は頂点の番号 (K, {width}) の整数にしてください（形 {value.shape}、{value.dtype}）")
        if value.size and (value.min() < 0 or value.max() >= len(co)):
            raise ToolError(f"{name} が無い頂点を指しています")
    try:
        shapes, report, debug = computed.build(co, edges, tris, settings)
    except ValueError as error:
        raise ToolError(str(error)) from error
    from ..blender_adapter.mesh_export import write_npz
    write_npz(outputs["shapes"], shapes)                   # 並びは形状キーの並び（右の目・左の目・口角）。名前は挙げたまま（.NPZ でも）
    if "debug" in outputs:
        write_npz(outputs["debug"], debug)
    report["settings"] = settings
    atomic_json(outputs["report"], report)
    still = [name for name, value in report["shapes"].items() if not value["moved_vertices"]]
    observation = {"kind": "face_shapes", "vertices": report["vertices"], "shapes": report["shapes"],
                   "eyes": {side: {key: value.get(key) for key in ("x", "opening_height_mm", "moved_vertices", "iris_parts", "brow_parts")}
                            for side, value in (report.get("eyes") or {}).items()},
                   "mouth": {key: report["mouth"].get(key) for key in ("seam_z_centre", "corner_x")} if "mouth" in report else None}
    unresolved = [f"動く頂点の無い形があります（部品の箱と量を確かめてください）: {still}"] if still else []
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


SHAPE_KEYS_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "shape_keys.py"


@register("model.shape_keys", inputs=("blend", "shapes"), outputs=("blend", "report"), cacheable=False,
          summary="計算した形（shapes。model.face_shapes の npz の、形の名前ごとの世界の座標の動き）を、物体（object）の形状キーとして入れる"
                  "（Blender の中で）。並びは npz の並び、値は 0。同じ名前のキーがあれば止める（replace で入れ直す）。元のファイルは変えない")
def shape_keys(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend", "shapes"), "model.shape_keys")
    _refuse_unknown(parameters, COMMON | {"object", "replace"}, "model.shape_keys")
    settings = {"object": _name(parameters.get("object"), "object")}
    replace = parameters.get("replace", False)
    if not isinstance(replace, bool):
        raise ToolError(f"replace は true か false にしてください: {replace!r}")
    settings["replace"] = replace
    if "blend" not in outputs or "report" not in outputs:
        raise ToolError("model.shape_keys には outputs の blend と report が要ります")
    blender = _blender(parameters, context, "model.shape_keys")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "shape_keys_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, SHAPE_KEYS_SCRIPT, None, ["--blend", inputs["blend"], "--shapes", inputs["shapes"], "--settings",
                                                                     str(settings_path), "--out-blend", outputs["blend"], "--report",
                                                                     outputs["report"]],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    observation = {"kind": "shape_keys", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "object": report.get("object"),
                   "keys": [key.get("name") for key in report.get("keys") or []], "replaced": [key.get("name") for key in report.get("keys") or []
                                                                                              if key.get("replaced")]}
    return StepResult(observations=[observation])


FACE_SHEET_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "face_sheet.py"
SAFE_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


def _safe(value, where):
    if not isinstance(value, str) or not SAFE_NAME.match(value):
        raise ToolError(f"{where} は英数字と _ . - の名前にしてください（ファイルの名前になる）: {value!r}")
    return value


def face_sheet_settings(parameters, recipe_dir=None):
    """render.face_sheet のパラメーターを確かめる。戻り: (描く側の settings, 並べる側の設定)。"""
    from ..blender_adapter.face_sheet import image_names
    from ..io import resolve_path
    _refuse_unknown(parameters, COMMON | {"object", "combos", "views", "hide", "size", "samples", "background", "cell", "font", "font_size",
                                          "quality"}, "render.face_sheet")
    combos = parameters.get("combos")
    if not isinstance(combos, list) or not combos:
        raise ToolError(f"combos は [[札, {{形状キー: 値}}], …] にしてください: {combos!r}")
    checked = []
    for k, combo in enumerate(combos):
        if not isinstance(combo, list) or len(combo) != 2 or not isinstance(combo[1], dict):
            raise ToolError(f"combos[{k}] は [札, {{形状キー: 値}}] にしてください: {combo!r}")
        label = _safe(combo[0], f"combos[{k}] の札")
        values = {}
        for key, value in combo[1].items():
            if not isinstance(key, str) or not key:
                raise ToolError(f"combos[{k}] の形状キーの名前は空でない文字列にしてください: {key!r}")
            values[key] = _real(value, f"combos[{k}].{key}", -10.0, maximum=10.0)   # Blender の形状キーの値の幅
        checked.append([label, values])
    if len({label for label, _ in checked}) != len(checked):
        raise ToolError("combos の札が重なっています")
    views = parameters.get("views")
    if not isinstance(views, list) or not views:
        raise ToolError(f"views は [{{\"name\", \"azimuth\", \"elevation\", \"scale\"}}, …] にしてください: {views!r}")
    out_views = []
    for k, view in enumerate(views):
        if not isinstance(view, dict):
            raise ToolError(f"views[{k}] は {{\"name\", \"azimuth\", \"elevation\", \"scale\", \"centre\"}} にしてください: {view!r}")
        _refuse_unknown(view, ("name", "azimuth", "elevation", "scale", "centre"), f"views[{k}]")
        entry = {"name": _safe(view.get("name"), f"views[{k}].name"), "azimuth": _real(view.get("azimuth"), f"views[{k}].azimuth"),
                 "elevation": _real(view.get("elevation"), f"views[{k}].elevation", -89.0, maximum=89.0),
                 "scale": _real(view.get("scale"), f"views[{k}].scale", 0.0, exclusive=True)}
        if view.get("centre") is not None:
            centre = view["centre"]
            if not isinstance(centre, list) or len(centre) != 3:
                raise ToolError(f"views[{k}].centre は狙う点 [x, y, z]（m）にしてください: {centre!r}")
            entry["centre"] = [_real(v, f"views[{k}].centre") for v in centre]
        out_views.append(entry)
    if len({view["name"] for view in out_views}) != len(out_views):
        raise ToolError("views の name が重なっています")
    try:                                                 # 絵の名前 <札>_<向き>.png が別の組み合わせと重なると、前の絵が上書きされる
        image_names([label for label, _ in checked], [view["name"] for view in out_views])
    except ValueError as error:
        raise ToolError(f"{error}。札か向きの名前を変えてください") from error
    hide = parameters.get("hide", [])
    if not isinstance(hide, list) or not all(isinstance(name, str) and name for name in hide):
        raise ToolError(f"hide は描かない物体の名前の並びにしてください: {hide!r}")
    background = parameters.get("background", [0.18, 0.18, 0.2])
    if not isinstance(background, list) or len(background) != 3:
        raise ToolError(f"background は背景の色 [R, G, B]（シーンの直線の値 0〜1）にしてください: {background!r}")
    render = {"object": _name(parameters.get("object"), "object"), "combos": checked, "views": out_views, "hide": hide,
              "size": _integer(parameters.get("size", 500), "size", 16), "samples": _integer(parameters.get("samples", 16), "samples", 1),
              "background": [_real(v, "background", 0.0, maximum=1.0) for v in background]}
    if render["size"] > 4096:
        raise ToolError(f"size は 4096 画素までにしてください: {render['size']}")
    if render["object"] in hide:
        raise ToolError(f"hide に object（{render['object']}）は入れられません（描く頭が消える）")
    font = parameters.get("font")
    if font is not None:
        if not isinstance(font, str) or not font:
            raise ToolError(f"font は札の書体のファイルのパスにしてください: {font!r}")
        font = resolve_path(recipe_dir, font) if recipe_dir is not None else Path(font)
        if not Path(font).is_file():
            raise ToolError(f"font の書体のファイルがありません: {font}")
    quality = _integer(parameters.get("quality", 90), "quality", 1)
    if quality > 100:
        raise ToolError(f"quality は 1〜100 にしてください: {quality}")
    join = {"cell": _integer(parameters.get("cell", 320), "cell", 16), "font": str(font) if font is not None else None,
            "font_size": _integer(parameters.get("font_size", 16), "font_size", 4), "quality": quality}
    return render, join


@register("render.face_sheet", inputs=("blend",), outputs=("sheet", "report"), cacheable=False,
          summary="表情の一覧の絵: 頭（object）の形状キーの組み合わせ（combos）ごとに、いくつかの向き（views。方位・高さの角・正射影の幅・狙う点）から"
                  "寄りで描き（Blender の中で EEVEE。保存しない）、表情（行）× 向き（列）に並べる（Pillow）。正面だけでは気づけない、斜めから白目が"
                  "のぞくなどを見るため。hide の物体（髪など）は描かない。Blender が要り、再利用しない（毎回実行）")
def face_sheet(inputs, parameters, outputs, context):
    from .. import face_sheet as sheet
    from ..blender_adapter import launcher
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "render.face_sheet")
    render, join = face_sheet_settings(parameters, getattr(context, "recipe_dir", None))
    if "sheet" not in outputs or "report" not in outputs:
        raise ToolError("render.face_sheet には outputs の sheet と report が要ります")
    if Path(outputs["sheet"]).suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ToolError(f"render.face_sheet の outputs の sheet は .jpg か .png にしてください: {Path(outputs['sheet']).name}")
    blender = _blender(parameters, context, "render.face_sheet")
    addons, extensions = blender_addons(parameters, context)
    step_dir = Path(context.step_dir)
    settings_path, views_dir, render_report = step_dir / "face_sheet_settings.json", step_dir / "views", step_dir / "face_sheet_render.json"
    atomic_json(settings_path, render)
    record = launcher.run_blender(blender, FACE_SHEET_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path), "--out-dir",
                                                                     str(views_dir), "--report", str(render_report)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(render_report)
    try:
        size = sheet.join(views_dir, [label for label, _ in render["combos"]], [view["name"] for view in render["views"]], outputs["sheet"],
                          join["cell"], join["font"], join["font_size"], join["quality"])
    except (ValueError, OSError) as error:
        raise ToolError(f"表情の一覧の絵を作れません: {error}") from error
    report.update(schema_version="model3d-face-sheet/0.1", sheet={"size": list(size), **join}, views_dir=str(views_dir))
    atomic_json(outputs["report"], report)
    observation = {"kind": "face_sheet", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "object": report.get("object"), "images": len(report.get("images") or []),
                   "sheet_size": list(size)}
    return StepResult(observations=[observation])


def _color(value, where):
    if not isinstance(value, list) or len(value) != 3 or not all(
            not isinstance(v, bool) and isinstance(v, int) and 0 <= v <= 255 for v in value):
        raise ToolError(f"{where} は色 [R, G, B]（0〜255 の整数）にしてください: {value!r}")
    return value


def mouth_window_settings(parameters, where):
    """口のまわりの窓（front_y・window・zone）を確かめる（texture.mouth_inside と check.mouth で同じ書き方）。"""
    window = _section(parameters, "window", f"{where}.window" if where else "window", ("half_width", "z"))
    zone = _section(parameters, "zone", f"{where}.zone" if where else "zone", ("half_width",))
    settings = {"front_y": _real(parameters.get("front_y"), "front_y"),
                "window": {"half_width": _real(window["half_width"], "window.half_width", 0.0, exclusive=True), "z": _span(window["z"], "window.z")},
                "zone": {"half_width": _real(zone["half_width"], "zone.half_width", 0.0, exclusive=True)}}
    if "resolution" in parameters:
        settings["resolution"] = _real(parameters["resolution"], "resolution", 0.0, exclusive=True)
    if "visible" in parameters:
        settings["visible"] = _real(parameters["visible"], "visible", 0.0)
    _raster_size(2 * settings["window"]["half_width"], settings["window"]["z"], settings.get("resolution", 0.0001), "window")
    _zone_in_window(settings, "")
    return settings


def mouth_inside_settings(parameters):
    """texture.mouth_inside のパラメーターを確かめる（口のまわりの箱と部品の見つけ方に既定は無い。色・ふくらませ方は既定あり）。"""
    _refuse_unknown(parameters, {"front_y", "resolution", "visible", "window", "zone", "parts", "tongue", "uv_margin", "grow", "inside_color",
                                 "tongue_color"}, "texture.mouth_inside")
    settings = mouth_window_settings(parameters, "")
    parts = _section(parameters, "parts", "parts", ("half_width", "z", "front_y", "max_size", "front_limit"))
    tongue = _section(parameters, "tongue", "tongue", ("back_y", "centre"))
    settings["parts"] = {"half_width": _real(parts["half_width"], "parts.half_width", 0.0, exclusive=True), "z": _span(parts["z"], "parts.z"),
                         "front_y": _real(parts["front_y"], "parts.front_y"),
                         "max_size": _real(parts["max_size"], "parts.max_size", 0.0, exclusive=True),
                         "front_limit": _real(parts["front_limit"], "parts.front_limit")}
    settings["tongue"] = {"back_y": _real(tongue["back_y"], "tongue.back_y"), "centre": _real(tongue["centre"], "tongue.centre", 0.0)}
    if "uv_margin" in parameters:
        settings["uv_margin"] = _real(parameters["uv_margin"], "uv_margin", 0.0)
    if "grow" in parameters:
        settings["grow"] = _integer(parameters["grow"], "grow", 0)
    for key in ("inside_color", "tongue_color"):
        if key in parameters:
            settings[key] = _color(parameters[key], key)
    return settings


def _head_arrays(path, keys):
    """頭のメッシュの npz（model.export_mesh）の配列を読み、形と番号の範囲を確かめる。"""
    import numpy as np
    from ..mesh import _npz_arrays
    arrays = _npz_arrays(path, "頭のメッシュの npz", list(keys))
    co = arrays["co"]
    if co.ndim != 2 or co.shape[1] != 3 or not np.issubdtype(co.dtype, np.floating) or not np.isfinite(co).all():
        raise ToolError(f"co は有限の頂点の位置 (N, 3) にしてください（形 {co.shape}、{co.dtype}）")
    for name, width, bound in (("edges", 2, len(co)), ("tris", 3, len(co)), ("tri_loops", 3, None)):
        if name not in arrays:
            continue
        value = arrays[name]
        if value.ndim != 2 or value.shape[1] != width or not np.issubdtype(value.dtype, np.integer):
            raise ToolError(f"{name} は番号 (K, {width}) の整数にしてください（形 {value.shape}、{value.dtype}）")
        if value.size and (value.min() < 0 or (bound is not None and value.max() >= bound)):
            raise ToolError(f"{name} が無い頂点を指しています")
    if "uv" in arrays:
        uv = arrays["uv"]
        if uv.ndim != 2 or uv.shape[1] != 2 or not np.isfinite(uv).all():
            raise ToolError(f"uv は面の角ごとの座標 (L, 2) にしてください（形 {uv.shape}）")
        if "tri_loops" in arrays and arrays["tri_loops"].size and arrays["tri_loops"].max() >= len(uv):
            raise ToolError("tri_loops が uv の無い面の角を指しています")
    return arrays


@register("texture.mouth_inside", inputs=("mesh", "texture"), outputs=("texture", "report"),
          summary="色の絵（texture）の、口の中の面と部品が使う所だけを塗る（Blender の外。numpy と Pillow）: 頭のメッシュ（mesh。model.export_mesh の"
                  " npz）を正面から描き、口のまわりの肌の三角形のうち 3 頂点とも見えないもの（唇の裏の袋）と、口の中の部品（parts の島）が uv で使う"
                  "画素を暗い赤に、舌（tongue の島）を明るい赤に塗る。外から見える面が使う画素には触らない。口のまわりの箱と部品の見つけ方に既定は無い")
def mouth_inside(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image
    from .. import mouth
    require(inputs, ("mesh", "texture"), "texture.mouth_inside")
    settings = mouth_inside_settings(parameters)
    if "texture" not in outputs or "report" not in outputs:
        raise ToolError("texture.mouth_inside には outputs の texture と report が要ります")
    if Path(outputs["texture"]).suffix.lower() != ".png":
        raise ToolError(f"texture.mouth_inside の outputs の texture は .png にしてください: {Path(outputs['texture']).name}")
    arrays = _head_arrays(inputs["mesh"], ("co", "edges", "tris", "tri_loops", "uv"))
    try:
        with Image.open(inputs["texture"]) as im:
            if im.mode.startswith("I") or im.mode == "F":    # 16 ビットの灰色・整数・浮動小数の絵は、8 ビットの色に変えると値が切られる
                raise ToolError(f"色の絵は 8 ビットの絵にしてください（{im.mode}。16 ビットや浮動小数の絵は、色に変えると 255 で切られて白くなる）")
            keep = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            image = np.asarray(im.convert("RGBA" if keep else "RGB")).copy()
    except OSError as error:
        raise ToolError(f"色の絵を読めません: {inputs['texture']}（{error}）") from error
    try:
        painted, report = mouth.paint_inside(arrays["co"], arrays["edges"], arrays["tris"], arrays["tri_loops"], arrays["uv"], image, settings)
    except ValueError as error:
        raise ToolError(str(error)) from error
    Image.fromarray(painted).save(outputs["texture"], format="PNG")
    report = {"schema_version": "model3d-mouth-inside/0.1", "size": [int(image.shape[1]), int(image.shape[0])], **report,
              "alpha_kept": image.shape[2] == 4, "settings": settings}
    atomic_json(outputs["report"], report)
    unresolved = [] if report["painted_pixels"] else ["塗った画素がありません（口のまわりの箱と部品の見つけ方を確かめてください）"]
    if report["uv_outside_triangles"]:
        unresolved.append(f"uv が 0〜1 の外にかかる口の中の三角形が {report['uv_outside_triangles']} 個あります（外の所は塗っていません。"
                          "絵を繰り返して使う uv なら、塗れていない所を確かめてください）")
    return StepResult(observations=[{key: report[key] for key in ("pocket_triangles", "part_triangles", "tongue_triangles", "painted_pixels",
                                                                  "pixels_shared_with_visible_faces_left_untouched")}],
                      unresolved=unresolved, review=bool(unresolved))
