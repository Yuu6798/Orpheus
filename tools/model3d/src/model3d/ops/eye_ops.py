"""目の作りの工程（docs/model3d.md の「頭の候補の目の作りを調べる」）。

- check.eyes: 頭の候補の目の作り（目のまわりの部品・白目の扇かもしれない所・絵の中の場所）を、数と絵で調べる（Blender の外。numpy と
  Pillow。合否は決めない）。頭の候補を選ぶ段で、目の作り直しの前に使う。
- model.eye_rig・model.eye_keys・vrm.look_at: 目の板と瞳を入れた頭を、目の形の値（model3d-eye-geometry/0.1）から、動かせる作りにする
  （目の骨を球の中心へ・まばたきのキーの直しと新しいキー・VRM の視線。docs/model3d.md の「目を動かせる作りにする」）。
- render.eye_sheet: 動かせる作りにした頭を、姿勢 × 見る向きで描いて並べる（確かめの絵。はみ出しの確かめも）。
- motion.eye_plan: 目の動きの台本（時刻と量）を、.blend の動きのキー（形状キーの値と目の骨の回転）にする。
- render.eye_captions: 描いたコマに、台本の区間の説明の字を入れる（見せ直しの区間の説明）。
"""
import math
import re
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


def _blender_common(parameters, context, operation, allowed, vrm=True):
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
    addons, extensions = blender_addons(parameters, context, default=(VRM_ADDON,) if vrm else ())
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


KEYS_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "eye_keys.py"
KEY_AMOUNTS = ("lid_front", "lid_margin", "close_below", "close_reach", "close_max_scale", "wide_up", "wide_down", "iris_small", "iris_lift")
KEY_PAIRS = ("iris_stretch", "iris_squash")
KEY_SHAPES = ("side_fade", "bump_power", "upper_fall", "lower_fall")
KEY_NAMES = {"wide": "eye_wide_", "small": "iris_small_", "stretch": "iris_wide_", "squash": "iris_squash_"}
KEYS_PARAMETERS = {"blink", "smile", "names", *KEY_AMOUNTS, *KEY_PAIRS, *KEY_SHAPES, "blender", "addons", "blender_extensions", "timeout"}


def _sides(value, where, default):
    if value is None:
        return default
    if not isinstance(value, dict) or set(value) != {"R", "L"} or not all(isinstance(v, str) and v for v in value.values()):
        raise ToolError(f"{where} は {{\"R\": 右目の名前, \"L\": 左目の名前}} にしてください: {value!r}")
    return dict(value)


def key_settings(parameters):
    """model.eye_keys の量と形を確かめる（量にも形にも既定は無い。頭ごとに決める）。"""
    cfg = {}
    for key in KEY_AMOUNTS:
        if key not in parameters:
            raise ToolError(f"model.eye_keys には {key} が要ります（量に既定は無い）")
        cfg[key] = _number(parameters[key], key, positive=key not in ("close_below",))
    if not 0 < cfg["close_reach"] <= 1:
        raise ToolError(f"close_reach は 0 より大きく 1 以下にしてください（元のまばたきのキーが開きの何割まで閉じるか）: {cfg['close_reach']}")
    if cfg["close_max_scale"] < 1:
        raise ToolError(f"close_max_scale は 1 以上にしてください: {cfg['close_max_scale']}")
    for key in KEY_PAIRS:
        value = parameters.get(key)
        if (not isinstance(value, list) or len(value) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 for v in value)):
            raise ToolError(f"model.eye_keys には {key}（[横の倍率, たての倍率]。正の数）が要ります: {value!r}")
        cfg[key] = [float(v) for v in value]
    for key in KEY_SHAPES:
        if key not in parameters:
            raise ToolError(f"model.eye_keys には {key} が要ります（形に既定は無い）")
        value = parameters[key]
        if key == "upper_fall":
            if (not isinstance(value, list) or len(value) != 2
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value) or value[1] <= 0):
                raise ToolError(f"upper_fall は [縁から減らし始める m, 減らす幅 m] にしてください: {value!r}")
            cfg[key] = [float(v) for v in value]
        else:
            cfg[key] = _number(value, key, positive=True)
    return cfg


@register("model.eye_keys", inputs=("blend", "geometry"), outputs=("blend", "report"), cacheable=False,
          summary="目の板と瞳を入れた頭の、まばたきのキーを直し（上まぶたを下まぶたのふちより close_below 下まで届かせる・下りてきたまぶたを"
                  "板より lid_margin 手前の球の面へ押し出す・瞳は動かさない）、新しい形状キー（瞳が縮む・横へ伸びる・つぶれる・見開き。左右別）を"
                  "足す（Blender の中で。計算は numpy）。geometry は目の形の値（model3d-eye-geometry/0.1）。量と形に既定は無い。入力の .blend は"
                  "変えない。Blender が要り、再利用しない（毎回実行）")
def eye_keys(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _timeout
    require(inputs, ("blend", "geometry"), "model.eye_keys")
    blender, addons, extensions, _armature = _blender_common(parameters, context, "model.eye_keys", KEYS_PARAMETERS, vrm=False)
    for name in ("blend", "report"):
        if name not in outputs:
            raise ToolError(f"model.eye_keys には outputs の {name} が要ります")
    cfg = key_settings(parameters)
    blink = _sides(parameters.get("blink"), "blink", {"R": "blink_R", "L": "blink_L"})
    smile = parameters.get("smile", {"R": "blink_smile_R", "L": "blink_smile_L"})
    smile = None if smile is False else _sides(smile, "smile", None)
    names = parameters.get("names", KEY_NAMES)
    if not isinstance(names, dict) or set(names) != set(KEY_NAMES) or not all(isinstance(v, str) and v for v in names.values()):
        raise ToolError(f"names は {sorted(KEY_NAMES)} を鍵にした、新しいキーの名前の頭にしてください: {names!r}")
    if len(set(names.values())) != len(names):
        raise ToolError(f"names の名前の頭が重なっています: {names!r}")
    fixing = list(blink.values()) + (list(smile.values()) if smile else [])
    if len(set(fixing)) != len(fixing):
        raise ToolError(f"blink と smile の名前が重なっています（同じキーを 2 回直してしまう）: {fixing}")
    geometry = _geometry(inputs["geometry"])
    no_lids = [side for side in ("R", "L") if geometry["eyes"][side].get("lid_vertices") is None]
    if no_lids:
        raise ToolError(f"model.eye_keys には、目の形の値の lid_vertices（見開きで動かすまぶたの頂点）が要ります: {no_lids}")
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "eye_keys_settings.json"
    atomic_json(settings_path, {"geometry": geometry, "blink": blink, "smile": smile, "names": names, "cfg": cfg})
    record = launcher.run_blender(blender, KEYS_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                "--out", str(outputs["blend"]), "--report", str(outputs["report"])],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    report["blender_version"] = record["blender_version"]
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "eye_keys", "blink": report["blink"], "new_keys": sorted(report["new_keys"])}])


LOOK_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "eye_look.py"
LOOK_PARAMETERS = {"slide", "input_max", "armature", "blender", "addons", "blender_extensions", "timeout"}


@register("vrm.look_at", inputs=("blend", "geometry"), outputs=("blend", "report"), cacheable=False,
          summary="VRM の視線を目の骨を回す作り（bone）にし、4 つの範囲（横の内・外、たての下・上）の入力の最大を input_max、出力の倍率を"
                  " degrees(瞳が滑る量 ÷ 目の球の半径) にする（Blender の中で VRM Add-on の設定を書く）。geometry は目の形の値"
                  "（model3d-eye-geometry/0.1。半径は左右の球の平均）。slide（{horizontal, vertical}。m）と input_max（°）に既定は無い。"
                  "入力の .blend は変えない。Blender（VRM Add-on）が要り、再利用しない（毎回実行）")
def look_at(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _timeout
    require(inputs, ("blend", "geometry"), "vrm.look_at")
    blender, addons, extensions, armature = _blender_common(parameters, context, "vrm.look_at", LOOK_PARAMETERS)
    for name in ("blend", "report"):
        if name not in outputs:
            raise ToolError(f"vrm.look_at には outputs の {name} が要ります")
    slide = parameters.get("slide")
    if not isinstance(slide, dict) or set(slide) != {"horizontal", "vertical"}:
        raise ToolError(f"vrm.look_at には slide（{{\"horizontal\": m, \"vertical\": m}}。視線がいちばん端のときに瞳が滑る量）が要ります: {slide!r}")
    slide = {axis: _number(slide[axis], f"slide.{axis}", positive=True) for axis in ("horizontal", "vertical")}
    if "input_max" not in parameters:
        raise ToolError("vrm.look_at には input_max（視線の入力のいちばん大きい角度 °）が要ります（既定は無い）")
    input_max = _number(parameters["input_max"], "input_max", positive=True)
    if input_max > 180:
        raise ToolError(f"input_max は 180° 以下にしてください: {input_max}")
    geometry = _geometry(inputs["geometry"])
    radii = {side: float(geometry["eyes"][side]["sphere"]["radius"]) for side in ("R", "L")}
    settings = {"radius": (radii["R"] + radii["L"]) / 2, "radii": radii, "slide": slide, "input_max": input_max, "armature": armature}
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "look_at_settings.json"
    atomic_json(settings_path, settings)
    record = launcher.run_blender(blender, LOOK_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                "--out", str(outputs["blend"]), "--report", str(outputs["report"])],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    report["blender_version"] = record["blender_version"]
    report["radii"] = radii
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "look_at", "type": report["after"]["type"], "radius": settings["radius"],
                                     "degrees": report["degrees"]}])


SHEET_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "eye_sheet.py"
SHEET_PARAMETERS = {"poses", "views", "paint", "bones", "armature", "size", "background", "aa", "hide", "cell", "quality", "font", "font_size",
                    "blender", "addons", "blender_extensions", "timeout"}
SHEET_DEFAULTS = {"size": [960, 540], "background": [0.40, 0.39, 0.38], "aa": "8", "cell": 480, "quality": 90, "font_size": 16}   # 仮の値
VIEW_DISTANCE = 0.5                                                          # 狙う点からカメラまで（m。仮の値）
PAINT_COLOURS = {"plate": [1.0, 0.0, 1.0], "iris": [0.0, 1.0, 0.0]}          # はみ出しの確かめ: 目の板は赤紫、瞳は緑
AA_LEVELS = ("OFF", "FXAA", "5", "8", "11", "16", "32")
LABEL = re.compile(r"^[A-Za-z0-9_.+-]+$")


def _label(value, where):
    if not isinstance(value, str) or not LABEL.match(value):
        raise ToolError(f"{where} は英数字と _ . + - の名前にしてください（絵のファイルの名前になる）: {value!r}")
    return value


def _colour(value, where):
    if not isinstance(value, list) or len(value) != 3:
        raise ToolError(f"{where} は色 [R, G, B]（シーンの直線の値 0〜1）にしてください: {value!r}")
    colour = [_number(v, where) for v in value]
    if not all(0 <= v <= 1 for v in colour):
        raise ToolError(f"{where} の色は 0〜1 にしてください: {value!r}")
    return colour


def _whole(value, where, minimum, maximum=None):
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
        span = f"{minimum}〜{maximum}" if maximum is not None else f"{minimum} 以上"
        raise ToolError(f"{where} は {span} の整数にしてください: {value!r}")
    return value


def _poses(poses):
    if not isinstance(poses, list) or not poses:
        raise ToolError(f"poses は [{{\"name\", \"gaze\": [横 m, たて m], \"keys\": {{形状キー: 値}}}}, …] にしてください: {poses!r}")
    checked = []
    for k, pose in enumerate(poses):
        if not isinstance(pose, dict) or set(pose) - {"name", "gaze", "keys"}:
            raise ToolError(f"poses[{k}] は {{\"name\", \"gaze\", \"keys\"}} にしてください: {pose!r}")
        gaze = pose.get("gaze", [0.0, 0.0])
        if not isinstance(gaze, list) or len(gaze) != 2:
            raise ToolError(f"poses[{k}].gaze は瞳が滑る量 [横 m, たて m] にしてください: {gaze!r}")
        keys = pose.get("keys", {})
        if not isinstance(keys, dict) or not all(isinstance(name, str) and name for name in keys):
            raise ToolError(f"poses[{k}].keys は {{形状キー: 値}} にしてください: {keys!r}")
        values = {}
        for name, value in keys.items():
            values[name] = _number(value, f"poses[{k}].keys.{name}")
            if not -10 <= values[name] <= 10:
                raise ToolError(f"poses[{k}].keys.{name} は -10〜10 にしてください（Blender の形状キーの値の幅）: {value!r}")
        checked.append({"name": _label(pose.get("name"), f"poses[{k}].name"), "gaze": [_number(v, f"poses[{k}].gaze") for v in gaze],
                        "keys": values})
    return checked


def _views(views):
    if not isinstance(views, list) or not views:
        raise ToolError(f"views は [{{\"name\", \"yaw\", \"pitch\", \"target\", \"span\"}}, …] にしてください: {views!r}")
    checked = []
    for k, view in enumerate(views):
        if not isinstance(view, dict) or set(view) - {"name", "yaw", "pitch", "target", "span", "distance"}:
            raise ToolError(f"views[{k}] は {{\"name\", \"yaw\", \"pitch\", \"target\", \"span\", \"distance\"}} にしてください: {view!r}")
        target = view.get("target")
        if not isinstance(target, list) or len(target) != 3:
            raise ToolError(f"views[{k}].target は狙う点 [x, y, z]（世界の座標 m）にしてください: {target!r}")
        entry = {"name": _label(view.get("name"), f"views[{k}].name"), "yaw": _number(view.get("yaw"), f"views[{k}].yaw"),
                 "pitch": _number(view.get("pitch"), f"views[{k}].pitch"), "target": [_number(v, f"views[{k}].target") for v in target],
                 "span": _number(view.get("span"), f"views[{k}].span", positive=True),
                 "distance": _number(view.get("distance", VIEW_DISTANCE), f"views[{k}].distance", positive=True)}
        if not -89 <= entry["pitch"] <= 89:
            raise ToolError(f"views[{k}].pitch は -89〜89° にしてください: {entry['pitch']}")
        checked.append(entry)
    return checked


def sheet_settings(parameters, geometry, recipe_dir=None):
    """render.eye_sheet のパラメーターを確かめる。戻り: (描く側の settings, 並べる側の設定)。姿勢と向きに既定は無い（頭ごとに決める）。"""
    from ..blender_adapter.face_sheet import image_names
    from ..io import resolve_path
    poses, views = _poses(parameters.get("poses")), _views(parameters.get("views"))
    for kind, rows in (("poses", poses), ("views", views)):
        if len({row["name"] for row in rows}) != len(rows):
            raise ToolError(f"{kind} の name が重なっています")
    try:                                                 # 絵の名前 <姿勢>_<向き>.png が別の組み合わせと重なると、前の絵が上書きされる
        image_names([pose["name"] for pose in poses], [view["name"] for view in views])
    except ValueError as error:
        raise ToolError(f"{error}。姿勢か向きの名前を変えてください") from error
    paint = parameters.get("paint")
    if paint is True:
        paint = PAINT_COLOURS
    elif paint is not None and paint is not False and (not isinstance(paint, dict) or set(paint) != {"plate", "iris"}):
        raise ToolError(f"paint は true か {{\"plate\": 色, \"iris\": 色}} にしてください: {paint!r}")
    if paint:
        paint = {part: _colour(paint[part], f"paint.{part}") for part in ("plate", "iris")}
        if any(geometry["eyes"][side].get("plate_vertices") is None for side in ("R", "L")):
            raise ToolError("paint には、目の形の値の plate_vertices（目の板の頂点の番号）が要ります")
    bones = parameters.get("bones")
    if bones is not None and (not isinstance(bones, dict) or set(bones) != {"R", "L"}
                              or not all(isinstance(v, str) and v for v in bones.values()) or bones["R"] == bones["L"]):
        raise ToolError(f"bones は {{\"R\": 右目の骨, \"L\": 左目の骨}}（別の名前）にしてください: {bones!r}")
    size = parameters.get("size", SHEET_DEFAULTS["size"])
    if not isinstance(size, list) or len(size) != 2:
        raise ToolError(f"size は描く絵の大きさ [横, 縦]（画素）にしてください: {size!r}")
    aa = parameters.get("aa", SHEET_DEFAULTS["aa"])
    if aa not in AA_LEVELS:
        raise ToolError(f"aa は {list(AA_LEVELS)} のどれかにしてください: {aa!r}")
    hide = parameters.get("hide", [])
    if not isinstance(hide, list) or not all(isinstance(name, str) and name for name in hide):
        raise ToolError(f"hide は描かない物体の名前の並びにしてください: {hide!r}")
    if geometry["object"] in hide:
        raise ToolError(f"hide に頭の物体（{geometry['object']}）は入れられません")
    radii = [float(geometry["eyes"][side]["sphere"]["radius"]) for side in ("R", "L")]
    render = {"object": geometry["object"], "radius": sum(radii) / 2, "bones": bones, "armature": parameters.get("armature"),
              "poses": poses, "views": views, "paint": paint or None,
              "plate_vertices": {side: geometry["eyes"][side].get("plate_vertices") or [] for side in ("R", "L")},
              "iris_vertices": {side: geometry["eyes"][side]["iris_vertices"] for side in ("R", "L")},
              "size": [_whole(v, "size", 16, 4096) for v in size],
              "background": _colour(parameters.get("background", SHEET_DEFAULTS["background"]), "background"), "aa": aa, "hide": hide}
    font = parameters.get("font")
    if font is not None:
        if not isinstance(font, str) or not font:
            raise ToolError(f"font は札の書体のファイルのパスにしてください: {font!r}")
        font = resolve_path(recipe_dir, font) if recipe_dir is not None else Path(font)
        if not Path(font).is_file():
            raise ToolError(f"font の書体のファイルがありません: {font}")
    join = {"cell": _whole(parameters.get("cell", SHEET_DEFAULTS["cell"]), "cell", 16),
            "quality": _whole(parameters.get("quality", SHEET_DEFAULTS["quality"]), "quality", 1, 100),
            "font": str(font) if font is not None else None,
            "font_size": _whole(parameters.get("font_size", SHEET_DEFAULTS["font_size"]), "font_size", 4)}
    width, height = render["size"]
    cell_height = int(join["cell"] * height / width)                    # 並べるこまの縦（絵と同じ比。join と同じ計算）
    if cell_height < 3:                                                 # こまの絵は枠の 2 画素を引いた大きさ
        raise ToolError(f"size {render['size']} と cell {join['cell']} では、並べるこまの縦が {cell_height} 画素になり、枠（2 画素）を"
                        "引くと絵が入りません。cell を大きくするか、絵の縦を大きくしてください")
    return render, join


@register("render.eye_sheet", inputs=("blend", "geometry"), outputs=("sheet", "report"), cacheable=False,
          summary="目を動かせる作りにした頭を、姿勢（poses。視線＝瞳が板の上を滑る量と、形状キーの値）× 見る向き（views。方位・高さの角・"
                  "狙う点・画面の幅）で描き（Blender の中で Workbench、絵の色そのまま。保存しない）、姿勢（行）× 向き（列）に並べる。paint で目の板と"
                  "瞳を塗れば、顔の面からのはみ出しを見られる（目の形の値の plate_vertices が要る）。姿勢と向きに既定は無い。"
                  "Blender（VRM Add-on）が要り、再利用しない（毎回実行）")
def eye_sheet(inputs, parameters, outputs, context):
    from .. import eye_sheet as sheet
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _timeout
    require(inputs, ("blend", "geometry"), "render.eye_sheet")
    blender, addons, extensions, _armature = _blender_common(parameters, context, "render.eye_sheet", SHEET_PARAMETERS)
    if "sheet" not in outputs or "report" not in outputs:
        raise ToolError("render.eye_sheet には outputs の sheet と report が要ります")
    if Path(outputs["sheet"]).suffix.lower() not in (".jpg", ".jpeg", ".png"):
        raise ToolError(f"render.eye_sheet の outputs の sheet は .jpg か .png にしてください: {Path(outputs['sheet']).name}")
    render, join = sheet_settings(parameters, _geometry(inputs["geometry"]), getattr(context, "recipe_dir", None))
    step_dir = Path(context.step_dir)
    settings_path, views_dir, render_report = step_dir / "eye_sheet_settings.json", step_dir / "views", step_dir / "eye_sheet_render.json"
    atomic_json(settings_path, render)
    record = launcher.run_blender(blender, SHEET_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path), "--out-dir",
                                                                 str(views_dir), "--report", str(render_report)],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(render_report)
    try:
        size = sheet.join(views_dir, [pose["name"] for pose in render["poses"]], [view["name"] for view in render["views"]], outputs["sheet"],
                          join["cell"], join["quality"], join["font"], join["font_size"])
    except (ValueError, OSError) as error:
        raise ToolError(f"確かめの絵の一覧を作れません: {error}") from error
    report.update(schema_version="model3d-eye-sheet/0.1", blender_version=record["blender_version"], radius=render["radius"],
                  sheet={"size": list(size), **join}, views_dir=str(views_dir))
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "eye_sheet", "images": len(report.get("images") or []), "sheet_size": list(size),
                                     "unknown_keys": report.get("unknown_keys") or {}, "painted_faces": report.get("painted_faces") or {}}])


MOTION_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "eye_motion.py"
MOTION_PARAMETERS = {"start", "bones", "armature", "fps", "blender", "addons", "blender_extensions", "timeout"}
FPS_MODES = ("set", "keep")      # シーンと台本の fps が違うとき。省くと止める（ユウの答え 2026-11-18）


@register("motion.eye_plan", inputs=("blend", "geometry", "plan"), outputs=("blend", "timeline", "report"), cacheable=False,
          summary="目の動きの台本（plan。model3d-eye-plan/0.1。視線の飛び・追いかける目・まばたき・驚き・笑顔・視線につられるまぶた・横で瞳が"
                  "伸びる、の時刻と量）を、コマごとの視線とキーの値にし、.blend の動きのキー（頭の形状キーの値と目の骨の回転）にする"
                  "（Blender の中で）。区間（sections）の順に演技の時間をたどり、ゆっくり見せ直す区間もある。timeline にコマごとの値と、区間の"
                  "コマの範囲と説明（説明の字を重ねる範囲）を書く。台本の値に既定は無い。入力の .blend は変えない。Blender（VRM Add-on）が要り、"
                  "再利用しない（毎回実行）")
def motion_eye_plan(inputs, parameters, outputs, context):
    from .. import eye_plan
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _timeout
    require(inputs, ("blend", "geometry", "plan"), "motion.eye_plan")
    blender, addons, extensions, armature = _blender_common(parameters, context, "motion.eye_plan", MOTION_PARAMETERS)
    for name in ("blend", "timeline", "report"):
        if name not in outputs:
            raise ToolError(f"motion.eye_plan には outputs の {name} が要ります")
    start = parameters.get("start", 1)
    if isinstance(start, bool) or not isinstance(start, int):
        raise ToolError(f"start は台本の最初のコマを置くシーンのコマ（整数）にしてください: {start!r}")
    bones = parameters.get("bones")
    if bones is not None and (not isinstance(bones, dict) or set(bones) != {"R", "L"}
                              or not all(isinstance(v, str) and v for v in bones.values()) or bones["R"] == bones["L"]):
        raise ToolError(f"bones は {{\"R\": 右目の骨, \"L\": 左目の骨}}（別の名前）にしてください: {bones!r}")
    fps_mode = parameters.get("fps")
    if fps_mode is not None and fps_mode not in FPS_MODES:
        raise ToolError(f"fps は \"set\"（シーンを台本の fps にしてから打つ）か \"keep\"（シーンの fps のまま打つ）にしてください。省くと、"
                        f"シーンと台本の fps が違えば止めます: {fps_mode!r}")
    geometry = _geometry(inputs["geometry"])
    try:
        plan = eye_plan.validate(read_json(inputs["plan"]))
    except eye_plan.PlanError as error:
        raise ToolError(f"目の動きの台本（plan）: {error}") from error
    timeline = eye_plan.timeline(plan, start)
    if not timeline["frames"]:
        raise ToolError("台本の区間からコマが 1 つもできません（区間の長さ ÷ 速さ × fps が 0.5 より小さい）")
    atomic_json(outputs["timeline"], {"schema_version": "model3d-eye-timeline/0.1", **timeline})
    radii = [float(geometry["eyes"][side]["sphere"]["radius"]) for side in ("R", "L")]
    step_dir = Path(context.step_dir)
    settings_path = step_dir / "eye_motion_settings.json"
    atomic_json(settings_path, {"object": geometry["object"], "radius": sum(radii) / 2, "bones": bones, "armature": armature,
                                "plan_fps": plan["fps"], "fps_mode": fps_mode or "check",
                                "frames": [{"frame": row["frame"], "gaze": row["gaze"], "keys": row["keys"]} for row in timeline["frames"]]})
    record = launcher.run_blender(blender, MOTION_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                                  "--out", str(outputs["blend"]), "--report", str(outputs["report"])],
                                  log=step_dir / "blender.log", timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    report.update(blender_version=record["blender_version"], radius=sum(radii) / 2, fps=plan["fps"],
                  sections=[{key: section[key] for key in ("frames", "speed", "view", "caption")} for section in timeline["sections"]])
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "eye_plan", "frames": report["frames"], "count": report["count"], "keyed": len(report["keyed"]),
                                     "unknown_keys": report["unknown_keys"], "widened": sorted(report["widened"]),
                                     "sections": len(timeline["sections"]), "scene_fps": report.get("scene_fps")}])


CAPTION_PARAMETERS = {"frames_dir", "name", "out_dir", "title", "font", "title_font", *("reference_width", "bar_height", "bar_alpha", "caption_size",
                                                                                         "caption_at", "title_size", "title_at", "title_stroke")}


@register("render.eye_captions", inputs=("timeline",), outputs=("report",), cacheable=False,
          summary="目の動きの台本の区間の説明の字を、描いたコマに入れて別のフォルダへ書く（Pillow。下に半ば透けた黒い帯と区間の説明、左上に題）。"
                  "timeline は motion.eye_plan の出力（コマごとの区間と、区間ごとの説明）。コマは frames_dir の name（既定 f{frame:04d}.png）。"
                  "大きさは基準の幅での画素。書体は font（無ければ Pillow の内蔵の書体。ラテン文字だけ）。元のコマは書き換えない。"
                  "書体とコマは隠れた入力なので再利用しない（毎回実行）")
def eye_captions(inputs, parameters, outputs, context):
    from .. import eye_captions as captions
    from ..io import read_json, resolve_path
    require(inputs, ("timeline",), "render.eye_captions")
    unknown = set(parameters) - CAPTION_PARAMETERS
    if unknown:
        raise ToolError(f"render.eye_captions の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(CAPTION_PARAMETERS)}）")
    if "report" not in outputs:
        raise ToolError("render.eye_captions には outputs の report が要ります")
    recipe_dir = getattr(context, "recipe_dir", None)

    def path(key, required=False):
        value = parameters.get(key)
        if value is None and not required:
            return None
        if not isinstance(value, str) or not value:
            raise ToolError(f"{key} はパスにしてください: {value!r}")
        return Path(resolve_path(recipe_dir, value) if recipe_dir is not None else value)

    frames_dir = path("frames_dir", required=True)
    if not frames_dir.is_dir():
        raise ToolError(f"frames_dir のフォルダがありません: {frames_dir}")
    out_dir = path("out_dir") or Path(context.step_dir) / "frames"
    if out_dir.resolve() == frames_dir.resolve():
        raise ToolError("out_dir は frames_dir と別のフォルダにしてください（元のコマを書き換えない）")
    name = parameters.get("name", "f{frame:04d}.png")
    if not isinstance(name, str) or "{frame" not in name or Path(name).suffix.lower() != ".png":
        raise ToolError(f"name はコマのファイルの名前の形（{{frame}} を含む .png。既定 f{{frame:04d}}.png）にしてください: {name!r}")
    if "/" in name or "\\" in name or Path(name).name != name:         # フォルダを含むと、out_dir の同じ名前が元のコマに重なりうる
        raise ToolError(f"name はファイルの名前だけにしてください（フォルダは frames_dir で指す。元のコマを書き換えない）: {name!r}")
    fonts = {key: path(key) for key in ("font", "title_font")}
    for key, value in fonts.items():
        if value is not None and not value.is_file():
            raise ToolError(f"{key} の書体のファイルがありません: {value}")
    title = parameters.get("title")
    if title is not None and not isinstance(title, str):
        raise ToolError(f"title は題の文字にしてください: {title!r}")
    style = dict(captions.STYLE)
    for key in style:
        if key not in parameters:
            continue
        value = parameters[key]
        if key in ("caption_at", "title_at"):
            if not isinstance(value, list) or len(value) != 2:
                raise ToolError(f"{key} は [x, y]（基準の幅での画素）にしてください: {value!r}")
            style[key] = [_number(v, key) for v in value]
        else:
            style[key] = _whole(value, key, 0 if key in ("title_stroke", "bar_height") else 1, 255 if key == "bar_alpha" else None)
    timeline = read_json(inputs["timeline"])
    if not isinstance(timeline, dict) or timeline.get("schema_version") != "model3d-eye-timeline/0.1":
        raise ToolError("timeline は motion.eye_plan の出力（model3d-eye-timeline/0.1）にしてください")
    try:
        written = captions.caption_frames(timeline, frames_dir, out_dir, name, style, title, fonts["font"], fonts["title_font"])
    except (ValueError, OSError, KeyError) as error:
        raise ToolError(f"説明の字を入れられません: {error}") from error
    report = {"schema_version": "model3d-eye-captions/0.1", "frames_dir": str(frames_dir), "out_dir": str(out_dir), "written": len(written),
              "sections": [{key: s[key] for key in ("frames", "caption")} for s in timeline["sections"]], "title": title,
              "style": style, "fonts": {key: str(value) if value else None for key, value in fonts.items()}}
    atomic_json(outputs["report"], report)
    return StepResult(observations=[{"kind": "eye_captions", "written": len(written), "out_dir": str(out_dir)}])
