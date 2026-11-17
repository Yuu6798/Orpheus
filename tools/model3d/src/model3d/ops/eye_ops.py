"""目の作りの工程（docs/model3d.md の「頭の候補の目の作りを調べる」）。

- check.eyes: 頭の候補の目の作り（目のまわりの部品・白目の扇かもしれない所・絵の中の場所）を、数と絵で調べる（Blender の外。numpy と
  Pillow。合否は決めない）。頭の候補を選ぶ段で、目の作り直しの前に使う。
"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json
from . import StepResult, register, require

EYE_PARAMETERS = {"front_y", "window", "eyes", "resolution", "sections", "top", "bones", "section_pixel", "top_pixel", "margin"}
IMAGES = ("front", "parts", "sections", "top")


def _pair(value, where):
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value) or value[0] >= value[1]):
        raise ToolError(f"{where} は [小さい方, 大きい方]（数）にしてください: {value!r}")
    return [float(v) for v in value]


def _number(value, where, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or (positive and value <= 0):
        raise ToolError(f"{where} は{'正の' if positive else ''}数にしてください: {value!r}")
    return float(value)


def eye_settings(parameters):
    """check.eyes の設定を確かめる（形の誤りは ToolError）。"""
    from ..eye_check import DEFAULTS
    unknown = set(parameters) - EYE_PARAMETERS
    if unknown:
        raise ToolError(f"check.eyes の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(EYE_PARAMETERS)}）")
    settings = {key: _number(parameters.get(key, value), key, positive=True) for key, value in DEFAULTS.items()}
    if "front_y" not in parameters:
        raise ToolError("check.eyes には front_y（正面の絵に描く三角形: いちばん手前の頂点の y がこれより手前）が要ります")
    settings["front_y"] = _number(parameters["front_y"], "front_y")
    window = parameters.get("window")
    if not isinstance(window, dict) or set(window) != {"x", "z", "y"}:
        raise ToolError("check.eyes には window（{\"x\": [x0, x1], \"z\": [z0, z1], \"y\": 中心の y の上限}。目のまわりの部品を並べる窓）が要ります")
    settings["window"] = {"x": _pair(window["x"], "window.x"), "z": _pair(window["z"], "window.z"), "y": _number(window["y"], "window.y")}
    find = (parameters.get("eyes") or {}).get("find") if isinstance(parameters.get("eyes"), dict) else None
    if not isinstance(find, dict) or set(find) != {"x", "z", "front_y"}:
        raise ToolError("check.eyes には eyes.find（{\"x\": [内, 外], \"z\": [z0, z1], \"front_y\": y}。目ごとに扇の中心を探す箱）が要ります")
    settings["eyes"] = {"find": {"x": _pair(find["x"], "eyes.find.x"), "z": _pair(find["z"], "eyes.find.z"),
                                 "front_y": _number(find["front_y"], "eyes.find.front_y")}}
    sections = parameters.get("sections")
    if sections is not None:
        if not isinstance(sections, dict) or set(sections) - {"x", "z", "y"}:
            raise ToolError(f"sections は {{\"x\": [縦に切る x …], \"z\": [横に切る z …], \"y\": [y0, y1]}} にしてください: {sections!r}")
        out = {}
        for key in ("x", "z"):
            if key in sections:
                values = sections[key]
                if not isinstance(values, list) or not values:
                    raise ToolError(f"sections.{key} は切る位置（数）の並びにしてください: {values!r}")
                out[key] = [_number(v, f"sections.{key}") for v in values]
        if "y" in sections:
            out["y"] = _pair(sections["y"], "sections.y")
        settings["sections"] = out
    top = parameters.get("top")
    if top is not None:
        if not isinstance(top, dict) or set(top) - {"z", "x", "y"}:
            raise ToolError(f"top は {{\"z\": 切る高さ, \"x\": [x0, x1], \"y\": [y0, y1]}} にしてください: {top!r}")
        settings["top"] = {key: (_number(top[key], "top.z") if key == "z" else _pair(top[key], f"top.{key}")) for key in top}
    bones = parameters.get("bones")
    if bones is not None:
        if (not isinstance(bones, list)
                or not all(isinstance(b, dict) and set(b) <= {"name", "head", "tail"} and {"head", "tail"} <= set(b) for b in bones)):
            raise ToolError(f"bones は [{{\"name\", \"head\": [x, y, z], \"tail\": [x, y, z]}}, …] にしてください: {bones!r}")
        for b in bones:
            for key in ("head", "tail"):
                if (not isinstance(b[key], list) or len(b[key]) != 3
                        or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in b[key])):
                    raise ToolError(f"bones の {key} は [x, y, z]（m）にしてください: {b[key]!r}")
        settings["bones"] = bones
    return settings


@register("check.eyes", inputs=("mesh", "texture"), outputs=("report", *IMAGES),
          summary="頭の候補の目の作りを数と絵で調べる（Blender の外。numpy と Pillow。合否は決めない）: 窓（window）に中心がある部品（辺で"
                  "つながった島）の頂点と三角形の数・中心・範囲・色・絵の中の場所、目ごとの白目の扇かもしれない所（探す箱 eyes.find の肌の頂点の"
                  "うち辺がいちばん多い頂点を中心にした三角形。輪の範囲・円すいの深さ・その範囲の部品）。front は正面（色の絵か部品の色）、parts"
                  "は部品の色分け（番号つき）、sections は縦と横の断面、top は上から見た断面（目の高さ。bones で目の骨の軸も）。目の作り直しの"
                  "前に、頭の候補を選ぶため")
def check_eyes(inputs, parameters, outputs, context):
    from .. import eye_check
    from .face_ops import _head_arrays, _read_texture
    require(inputs, ("mesh",), "check.eyes")
    settings = eye_settings(parameters)
    if "report" not in outputs:
        raise ToolError("check.eyes には outputs の report が要ります")
    for key in IMAGES:
        if key in outputs and Path(outputs[key]).suffix.lower() != ".png":
            raise ToolError(f"check.eyes の outputs の {key} は .png にしてください: {Path(outputs[key]).name}")
    keys = ("co", "edges", "tris") + (("tri_loops", "uv") if "texture" in inputs else ())
    arrays = _head_arrays(inputs["mesh"], keys)
    image = _read_texture(inputs["texture"], keep_alpha=False) if "texture" in inputs else None
    report, material = eye_check.survey(arrays["co"], arrays["edges"], arrays["tris"], settings, arrays.get("uv"),
                                        arrays.get("tri_loops"), image)
    wanted = [key for key in IMAGES if key in outputs]
    if wanted:
        drawn = eye_check.images(material, settings, report, image)
        for key in wanted:
            if key not in drawn:
                raise ToolError(f"check.eyes: {key} の絵を描けません（扇が見つからない目しかない）")
            drawn[key].save(outputs[key], format="PNG")
    report["settings"] = settings
    atomic_json(outputs["report"], report)
    eyes = {side: ({"fan_tris": e["fan_tris"], "valence": e["valence"], "parts_in_ring": len(e["parts_in_ring"])} if e.get("found") else None)
            for side, e in report["eyes"].items()}
    return StepResult(observations=[{"kind": "eye_check", "islands": report["islands"], "parts": len(report["parts"]), "eyes": eyes}])


RIG_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "eye_rig.py"
RIG_PARAMETERS = {"bones", "armature", "blender", "addons", "blender_extensions", "timeout"}


def _geometry(path):
    """目の形の値（model3d-eye-geometry/0.1）を読んで確かめる。"""
    from ..blender_adapter.eye_math import GeometryError, validate
    from ..io import read_json
    try:
        return validate(read_json(path))
    except GeometryError as error:
        raise ToolError(f"目の形の値（geometry）: {error}") from error


def _blender_common(parameters, context, operation, allowed):
    from . import number
    from .blender_ops import blender_addons
    from .motion_ops import _blender
    from .vrm_ops import VRM_ADDON
    unknown = set(parameters) - allowed
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    armature = parameters.get("armature")
    if armature is not None and (not isinstance(armature, str) or not armature):
        raise ToolError(f"armature はアーマチュアの名前にしてください: {armature!r}")
    if parameters.get("timeout") is not None:
        number(parameters, "timeout", minimum=0, exclusive=True)
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,))
    return _blender(parameters, context, operation), addons, extensions, armature


@register("model.eye_rig", inputs=("blend", "geometry"), outputs=("blend", "report"), cacheable=False,
          summary="目の骨の根もとを目の球の中心へ移し（先は中心 + 目の板の向き × 半径。ロールは上へ）、瞳の頂点の重みを目の骨だけに付ける"
                  "（Blender の中で）。geometry は目の形の値（model3d-eye-geometry/0.1。球・瞳・縁・瞳の頂点）。骨を少し回すと、瞳が板から"
                  "浮きも沈みもせずに滑る作りにするため。目の骨は bones か VRM の人型の骨の割り当て。入力の .blend は変えない。"
                  "Blender（VRM Add-on）が要り、再利用しない（毎回実行）")
def eye_rig(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _timeout
    require(inputs, ("blend", "geometry"), "model.eye_rig")
    blender, addons, extensions, armature = _blender_common(parameters, context, "model.eye_rig", RIG_PARAMETERS)
    for name in ("blend", "report"):
        if name not in outputs:
            raise ToolError(f"model.eye_rig には outputs の {name} が要ります")
    bones = parameters.get("bones")
    if bones is not None and (not isinstance(bones, dict) or set(bones) != {"R", "L"}
                              or not all(isinstance(v, str) and v for v in bones.values()) or bones["R"] == bones["L"]):
        raise ToolError(f"bones は {{\"R\": 右目の骨, \"L\": 左目の骨}}（別の名前）にしてください: {bones!r}")
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "eye_rig_settings.json"
    atomic_json(settings_path, {"geometry": _geometry(inputs["geometry"]), "bones": bones, "armature": armature})
    record = launcher.run_blender(blender, RIG_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                               "--out", str(outputs["blend"]), "--report", str(outputs["report"])],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    report["blender_version"] = record["blender_version"]
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "eye_rig", "bones": report["bones"], "groups_added": report["groups_added"],
                                     "eyes": {side: {"length_mm": e["length_mm"], "iris_vertices": e["iris_vertices"]}
                                              for side, e in report["eyes"].items()}}])
