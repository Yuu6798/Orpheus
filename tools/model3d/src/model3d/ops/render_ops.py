"""描く: 塗りと輪郭線を付けたキャラを、舞台と光の中で、コマごとに PNG に描く（Blender の中で）。描く直前の押し出し（push）
の準備として、当たりの写しと押し出す頂点の組も足す（render.prepare_push）。もう 1 つの描き方として、キャラを要素に分けて簡易描画で
描き、カメラを控える（render.passes。重ね合わせは Blender の外）。部位を色分けして描く診断の絵（render.part_colors）も描く。"""
import math
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path, sha256
from . import StepResult, number, register, require

RENDER_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "stage_render.py"
RENDER_PARAMETERS = {"blender", "resolution", "samples", "raytracing", "bloom", "bone_prefix", "fps", "camera", "outline", "stage",
                     "background", "push", "frames_dir", "only", "timeout"}
PUSH_KEYS = {"meshes", "colliders", "groups", "smooth", "outlines", "recheck"}
CAMERA_KEYS = {"sensor_height", "lens", "az", "dist", "height", "aim", "follow", "follow_sigma"}
OUTLINE_KEYS = {"line_px", "reference_height", "scale"}
STAGE_KEYS = {"enabled", "stripe_floor", "stripe_soft"}


def _section(parameters, key, allowed):
    section = parameters.get(key, {})
    if not isinstance(section, dict):
        raise ToolError(f"{key} は辞書にしてください: {section!r}")
    unknown = set(section) - allowed
    if unknown:
        raise ToolError(f"{key} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(allowed)}）")
    return section


def _flag(parameters, key, where=None):
    value = parameters[key]
    if not isinstance(value, bool):
        raise ToolError(f"{where or key} は true か false: {value!r}")
    return value


def _render_settings(parameters):
    s = {}
    if "resolution" in parameters:
        res = parameters["resolution"]
        if (not isinstance(res, list) or len(res) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 16
                                                                    for v in res)):
            raise ToolError(f"resolution は [幅, 高さ]（16 以上の整数）にしてください: {res!r}")
        s["resolution"] = res
    if "samples" in parameters:
        s["samples"] = int(number(parameters, "samples", minimum=1, integer=True))
    for key in ("raytracing", "bloom"):
        if key in parameters:
            s[key] = _flag(parameters, key)
    if "bone_prefix" in parameters:
        if not isinstance(parameters["bone_prefix"], str):
            raise ToolError(f"bone_prefix は文字列にしてください: {parameters['bone_prefix']!r}")
        s["bone_prefix"] = parameters["bone_prefix"]
    if parameters.get("fps") is not None:
        s["fps"] = float(number(parameters, "fps", minimum=0, exclusive=True))
    camera = _section(parameters, "camera", CAMERA_KEYS)
    c = {}
    for key in ("sensor_height", "lens", "dist", "follow_sigma"):
        if key in camera:
            c[key] = float(number(camera, key, minimum=0, exclusive=True))
    for key in ("az", "height", "aim"):
        if key in camera:
            c[key] = float(number(camera, key))
    if "follow" in camera:
        c["follow"] = float(number(camera, "follow", minimum=0))
        if c["follow"] > 1:
            raise ToolError(f"camera.follow は 0〜1 にしてください: {c['follow']!r}")
    outline = _section(parameters, "outline", OUTLINE_KEYS)
    o = {}
    for key in ("line_px", "reference_height"):
        if key in outline:
            o[key] = float(number(outline, key, minimum=0, exclusive=True))
    if "scale" in outline:
        scale = outline["scale"]
        if not isinstance(scale, dict) or not all(isinstance(k, str) and k and isinstance(v, (int, float)) and not isinstance(v, bool)
                                                  and v > 0 for k, v in scale.items()):
            raise ToolError(f"outline.scale は {{元のメッシュの名前: 太さの倍率（0 より大きい）}} にしてください: {scale!r}")
        o["scale"] = {k: float(v) for k, v in scale.items()}
    stage = _section(parameters, "stage", STAGE_KEYS)
    st = {}
    if "enabled" in stage:
        st["enabled"] = _flag(stage, "enabled", "stage.enabled")
    for key in ("stripe_floor", "stripe_soft"):
        if key in stage:
            st[key] = float(number(stage, key, minimum=0))
    if st.get("stripe_soft", 0.0) >= 0.05:
        raise ToolError("stage.stripe_soft は 0.05 より小さくしてください（帯の境 0.66〜0.76 が入れ替わる）")
    background = parameters.get("background")
    if background is not None:
        color = background.get("color") if isinstance(background, dict) and set(background) == {"color"} else None
        if background != "transparent" and not (isinstance(color, list) and len(color) == 3 and all(
                isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in color)):
            raise ToolError(f"background は \"transparent\" か {{\"color\": [R, G, B]}}（0〜255 の整数）にしてください: {background!r}")
        if st.get("enabled", True):
            raise ToolError("background を使うときは stage.enabled を false にしてください（舞台の床や壁が背景を隠す）")
        s["background"] = background
    if parameters.get("push") is not None:
        s["push"] = _push_settings(parameters["push"])
    only = parameters.get("only")
    if only is not None:
        if (not isinstance(only, list) or not only or len(set(only)) != len(only)
                or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in only)):
            raise ToolError(f"only は描くコマの T（1 以上の整数、重ならない）の並びにしてください: {only!r}")
        s["only"] = only
    return {**s, "camera": c, "outline": o, "stage": st}


def _names(value, key):
    if not isinstance(value, list) or not value or not all(isinstance(n, str) and n for n in value) or len(set(value)) != len(value):
        raise ToolError(f"{key} は名前（空でない・重ならない文字列）の並びにしてください: {value!r}")
    return value


def _push_settings(push):
    """描く直前の押し出しの並び（meshes・colliders・groups・smooth・outlines）を確かめる。"""
    if not isinstance(push, list) or not push:
        raise ToolError(f"push は押し出しの並び（1 つ以上）にしてください: {push!r}")
    out, used = [], set()
    for n, spec in enumerate(push):
        where = f"push[{n}]"
        if not isinstance(spec, dict):
            raise ToolError(f"{where} は辞書にしてください: {spec!r}")
        unknown = set(spec) - PUSH_KEYS
        if unknown:
            raise ToolError(f"{where} の知らない鍵: {sorted(unknown)}（使えるのは {sorted(PUSH_KEYS)}）")
        missing = [k for k in ("meshes", "colliders", "groups") if k not in spec]
        if missing:
            raise ToolError(f"{where} には {', '.join(missing)} が要ります")
        meshes, colliders = _names(spec["meshes"], f"{where}.meshes"), _names(spec["colliders"], f"{where}.colliders")
        if set(meshes) & set(colliders):
            raise ToolError(f"{where} の meshes と colliders に同じメッシュがあります: {sorted(set(meshes) & set(colliders))}")
        if set(meshes) & used:
            raise ToolError(f"{where} の meshes が前の押し出しと重なっています: {sorted(set(meshes) & used)}")
        used |= set(meshes)
        groups = spec["groups"]
        if (not isinstance(groups, list) or not groups or not all(isinstance(g, dict) and set(g) == {"name", "offset_mm"}
                                                                  and isinstance(g["name"], str) and g["name"] for g in groups)
                or len({g["name"] for g in groups}) != len(groups)):
            raise ToolError(f"{where}.groups は [{{\"name\": 頂点の組の名前, \"offset_mm\": 離す量}}, …]（名前は重ならない）にしてください: "
                            f"{groups!r}")
        entry = {"meshes": meshes, "colliders": colliders,
                 "groups": [{"name": g["name"], "offset_mm": float(number(g, "offset_mm", minimum=0, exclusive=True))}
                            for g in groups]}
        if "smooth" in spec:
            entry["smooth"] = int(number(spec, "smooth", minimum=0, integer=True))
        for key in ("outlines", "recheck"):
            if key in spec:
                entry[key] = _flag(spec, key, f"{where}.{key}")
        out.append(entry)
    return out


def _track(path):
    """トラックを読み、契約と、T が重ならないことを確かめる。"""
    from .. import contracts
    track = contracts.load("stage-track", path)
    seen = set()
    for frame in track["frames"]:
        if frame["T"] in seen:
            raise ToolError(f"トラックの T が重なっています: {frame['T']}")
        seen.add(frame["T"])
    return track


def frames_manifest(report, frames_dir):
    """描くコマの並び（報告の planned）のうち、ファイルのあるコマの sha256 と大きさ。無いコマは missing。"""
    from .. import contracts
    from ..blender_adapter.stage_render import frame_name
    frames, missing = [], []
    for item in report["planned"]:
        file = Path(frames_dir) / frame_name(item["T"])
        if file.is_file():
            frames.append({"T": item["T"], "f": item["f"], "file": file.name, "sha256": sha256(file), "bytes": file.stat().st_size})
        else:
            missing.append(item["T"])
    manifest = {"schema_version": "model3d-frames/0.1", "directory": str(Path(frames_dir).resolve()), "fps": report["fps"],
                "resolution": report["resolution"], "frames": frames, "missing": missing}
    return contracts.validate("frames", manifest)


def _stage_key(inputs, settings, blender):
    """render.stage の入力のまとまり（コマのフォルダの入力の控え。render_inputs）。only は描く絵を変えないので除く。"""
    from .. import render_inputs
    from ..blender_adapter import launcher
    return {"operation": "render.stage", "blend": render_inputs.file_sha(inputs["blend"]),
            "track": render_inputs.file_sha(inputs.get("track")), "settings": {k: v for k, v in settings.items() if k != "only"},
            "scripts": render_inputs.scripts(RENDER_SCRIPT), "blender": launcher.blender_version(blender)}


def _stage_files(track, frames_dir, only):
    """描く工程がとばしうるコマのファイル（フォルダからの相対パス）。トラックがあればその並び（only で絞る）、無ければ並びは .blend の
    シーンで決まるので、フォルダにもうある f<T>.png の全部（only で絞る）。"""
    import re
    from ..blender_adapter.stage_render import frame_name
    if track is not None:
        return [frame_name(fr["T"]) for fr in track["frames"] if only is None or fr["T"] in only]
    names = []
    for path in Path(frames_dir).iterdir():
        m = re.fullmatch(r"f(\d+)\.png", path.name)
        if m and path.is_file() and (only is None or int(m.group(1)) in only):
            names.append(path.name)
    return sorted(names)


@register("render.stage", inputs=("blend", "track"), outputs=("frames", "report"), cacheable=False,
          summary="塗りと輪郭線を付けたキャラ（blend）を、舞台（床・LED の壁・トラス・光の柱）と 4 つのスポットの中で、コマごとに "
                  "PNG に描く（Blender の EEVEE）。トラック（model3d-stage-track/0.1）があれば、そのコマの並びとカメラ・舞台・光・"
                  "塗りの値で、無ければシーンの全コマを腰を追う正面のカメラと動かない光で描く。輪郭線の太さはカメラの距離で"
                  "画面の上の太さにそろえる。舞台を切って background で緑背景か透けにも描ける。push で、描く直前に帯などの頂点を"
                  "当たり（閉じたメッシュ）の外へ出す。描いたコマはとばす（続きから描ける）。とばせるのは、フォルダの入力の控え"
                  "（model3d_inputs.json）がいまの入力と同じコマだけで、違うコマ・控えの無いコマ・別の入力で描くと控えたコマがあれば描く前に止める。"
                  "frames は描いたコマの一覧（model3d-frames/0.1）。Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def stage_render(inputs, parameters, outputs, context):
    from .. import render_inputs
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "render.stage")
    unknown = set(parameters) - RENDER_PARAMETERS
    if unknown:
        raise ToolError(f"render.stage の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(RENDER_PARAMETERS)}）")
    missing = [name for name in ("frames", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.stage には outputs の {', '.join(missing)} が要ります")
    blender = _blender(parameters, context, "render.stage")
    settings = _render_settings(parameters)
    track = _track(inputs["track"]) if inputs.get("track") is not None else None
    if parameters.get("frames_dir"):
        frames_dir = resolve_path(context.recipe_dir, parameters["frames_dir"])
    else:
        frames_dir = Path(context.step_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    only = set(settings["only"]) if settings.get("only") else None
    key = _stage_key(inputs, settings, blender)
    files = _stage_files(track, frames_dir, only)
    # トラックがあれば、確かめと同じ待ち合わせの中で、描く前に控える（途中で止まっても、描いたコマには控えがある）
    _, kept = render_inputs.check(frames_dir, key, files, "render.stage", claim=track is not None)
    settings_path = Path(context.step_dir) / "render_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--frames-dir", str(frames_dir),
            "--report", outputs["report"]]
    if track is not None:
        args += ["--track", inputs["track"]]
    try:
        record = launcher.run_blender(blender, RENDER_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                      timeout=_timeout(parameters))
    except BaseException:
        if track is None:                                 # 並びはシーンで決まるので、止まる前に書いたコマを控えてから止める
            try:
                render_inputs.claim(frames_dir, key, [n for n in _stage_files(None, frames_dir, only) if n not in set(files)])
            except ToolError:
                pass
        raise
    report = read_json(outputs["report"])
    if track is None:                                     # 並びはシーンで決まったので、描いたコマを描いた後に控える
        from ..blender_adapter.stage_render import frame_name
        render_inputs.claim(frames_dir, key, [frame_name(T) for T in report.get("rendered") or []])
    manifest = frames_manifest(report, frames_dir)
    atomic_json(outputs["frames"], manifest)
    observation = {"kind": "stage_render", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "planned": len(report["planned"]),
                   "rendered": len(report.get("rendered") or []), "skipped_existing": len(report.get("skipped_existing") or []),
                   "frames": len(manifest["frames"]), "missing": len(manifest["missing"]),
                   "inputs": {"key": render_inputs.key_id(key), "kept": kept}}
    if report.get("push"):
        observation["push_max_mm"] = report["push"].get("max_mm")
    return StepResult(observations=[observation])


PREPARE_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "prepare_push.py"
PREPARE_PARAMETERS = {"blender", "bone_prefix", "armature", "colliders", "groups", "timeout"}
COLLIDER_KEYS = {"from", "name", "weld_mm", "decimate"}
GROUP_KEYS = {"mesh", "bones", "top", "fade", "front", "back", "weld_mm"}


def _name(value, where):
    if not isinstance(value, str) or not value:
        raise ToolError(f"{where} は名前（空でない文字列）にしてください: {value!r}")
    return value


def _prepare_settings(parameters):
    """当たりの写し（colliders）と押し出す頂点の組（groups）の並びを確かめ、既定で埋める（長さは m）。"""
    from .motion_ops import _bone_prefix
    prefix = _bone_prefix(parameters)
    colliders, groups = parameters.get("colliders", []), parameters.get("groups", [])
    if not isinstance(colliders, list) or not isinstance(groups, list) or not (colliders or groups):
        raise ToolError("render.prepare_push には colliders（当たりの写し）か groups（押し出す頂点の組）の並びが要ります")
    s, names = {"colliders": [], "groups": []}, set()
    for n, spec in enumerate(colliders):
        where = f"colliders[{n}]"
        if not isinstance(spec, dict) or "from" not in spec or set(spec) - COLLIDER_KEYS:
            raise ToolError(f"{where} は {{\"from\": 写す元のメッシュ, …}}（使える鍵は {sorted(COLLIDER_KEYS)}）にしてください: {spec!r}")
        source = _name(spec["from"], f"{where}.from")
        name = _name(spec.get("name", "proxy_" + source), f"{where}.name")
        if name in names or name == source:
            raise ToolError(f"{where}.name（{name}）が、ほかの写しか写す元と重なっています")
        names.add(name)
        decimate = number(spec, "decimate", 0.25, minimum=0, exclusive=True)
        if decimate > 1:
            raise ToolError(f"{where}.decimate は面を残す割合（0 より大きく 1 以下）にしてください: {decimate!r}")
        s["colliders"].append({"from": source, "name": name,
                               "weld": float(number(spec, "weld_mm", 0.02, minimum=0, exclusive=True)) / 1000,
                               "decimate": float(decimate)})
    for n, spec in enumerate(groups):
        where = f"groups[{n}]"
        if not isinstance(spec, dict) or set(spec) - GROUP_KEYS or not {"mesh", "bones", "top", "front", "back"} <= set(spec):
            raise ToolError(f"{where} は {{\"mesh\", \"bones\", \"top\", \"front\", \"back\"}}（任意で fade・weld_mm）にしてください: "
                            f"{spec!r}")
        mesh = _name(spec["mesh"], f"{where}.mesh")
        if mesh in names:
            raise ToolError(f"{where}.mesh（{mesh}）は当たりの写しの名前です")
        bones = _names(spec["bones"], f"{where}.bones")
        front, back = _name(spec["front"], f"{where}.front"), _name(spec["back"], f"{where}.back")
        if front == back:
            raise ToolError(f"{where} の front と back は別の名前にしてください: {front!r}")
        top = spec["top"]
        if isinstance(top, bool) or not isinstance(top, (int, float)):
            raise ToolError(f"{where}.top は上の端の高さ（m）にしてください: {top!r}")
        s["groups"].append({"mesh": mesh, "bones": [prefix + b for b in bones], "top": float(top),
                            "fade": float(number(spec, "fade", 0.02, minimum=0, exclusive=True)),
                            "weld": float(number(spec, "weld_mm", 0.01, minimum=0, exclusive=True)) / 1000,
                            "front": front, "back": back})
    meshes = [g["mesh"] for g in s["groups"]]
    if len(set(meshes)) != len(meshes):
        raise ToolError(f"groups の mesh が重なっています: {meshes}（1 つのメッシュには 1 つにまとめてください）")
    armature = parameters.get("armature")
    if armature is not None:
        s["armature"] = _name(armature, "armature")
    return s


@register("render.prepare_push", inputs=("blend",), outputs=("blend", "report"), cacheable=False,
          summary="render.stage の push（描く直前の押し出し）の準備（Blender の中で）。colliders: 元のメッシュ（脚など）を写し、"
                  "穴をふさいで外向きの閉じた当たりにし、面を減らしてなめらかにし、元と同じ骨で動かす（描かない）。groups: 骨"
                  "（bones とその子孫）に重みのある頂点を種に、高さ top より下でつながった部分（帯の垂れなど）を選び、上の端から "
                  "fade で重みを 0 → 1 にして、法線の前と後ろで front と back の頂点の組にする。リポジトリ外の試作の手順と同じ。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def prepare_push(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "render.prepare_push")
    unknown = set(parameters) - PREPARE_PARAMETERS
    if unknown:
        raise ToolError(f"render.prepare_push の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PREPARE_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.prepare_push には outputs の {', '.join(missing)} が要ります")
    settings = _prepare_settings(parameters)
    blender = _blender(parameters, context, "render.prepare_push")
    settings_path = Path(context.step_dir) / "prepare_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--out-blend", outputs["blend"], "--report", outputs["report"],
            "--settings", str(settings_path)]
    record = launcher.run_blender(blender, PREPARE_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    colliders = report.get("colliders", [])
    unresolved = [f"当たり {c.get('name')}: 間引いた後に開いた辺 {c.get('open_edges')}・3 面以上で使う辺 {c.get('nonmanifold_edges')}"
                  "（閉じていない当たりでは、押し出しの内外の判定が狂う）"
                  for c in colliders if c.get("open_edges") or c.get("nonmanifold_edges")]
    observation = {"kind": "prepare_push", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"],
                   "colliders": [{k: c.get(k) for k in ("name", "from", "open_edges", "nonmanifold_edges", "flipped", "faces")}
                                 for c in colliders],
                   "groups": [{"mesh": g.get("mesh"), "pieces": g.get("pieces"), "front": (g.get("front") or {}).get("vertices"),
                               "back": (g.get("back") or {}).get("vertices")} for g in report.get("groups", [])]}
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


PASS_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "pass_render.py"
PASS_PARAMETERS = {"blender", "addons", "blender_extensions", "resolution", "passes", "format", "quality", "antialiasing", "camera",
                   "dancer", "disc", "head", "room", "shade", "mirror", "phantoms", "fx", "pool", "bone_prefix", "armature", "fps",
                   "passes_dir", "only", "timeout", "expressions"}
EXPRESSION_KEYS = {"objects", "images", "times", "shape_keys", "values"}
PASS_CAMERA_KEYS = {"sensor_height", "lens", "clip"}
DANCER_KEYS = {"bones", "sigma"}
DISC_KEYS = {"radius", "vertices", "height"}
ROOM_KEYS = {"collection", "world"}
SHADE_KEYS = {"studio_light", "rotate_z"}
MIRROR_KEYS = {"passes", "height"}
PHANTOM_KEYS = {"items", "during", "mirror", "point", "sigma_s", "reach", "frame0", "fps"}
FX_KEYS = {"shapes", "levels", "clock", "rate", "during", "frame0", "fps", "sigmas", "reach"}
FX_LEVELS = ("embers", "trails", "sparks", "ring", "flare")
POOL_KEYS = {"centre", "radii", "segments", "falloff", "floor", "floor_falloff", "height", "during"}
ANTIALIASING = ("OFF", "FXAA", "5", "8", "11", "16", "32")


def _expression_values(values):
    """expressions.values（{形状キー: [[秒, 値], …]}。秒は小さい順で重ならない）を確かめる。"""
    if not isinstance(values, dict):
        raise ToolError(f"expressions.values は {{形状キー: [[秒, 値], …]}} にしてください: {values!r}")
    checked = {}
    for key, points in values.items():
        if not isinstance(key, str) or not key:
            raise ToolError(f"expressions.values の形状キーの名前は空でない文字列にしてください: {key!r}")
        if (not isinstance(points, list) or not points
                or not all(isinstance(p, list) and len(p) == 2 and all(not isinstance(v, bool) and isinstance(v, (int, float)) and math.isfinite(v)
                                                                       for v in p) for p in points)):
            raise ToolError(f"expressions.values.{key} は [[秒, 値], …]（有限の数）にしてください")
        seconds = [float(p[0]) for p in points]
        if any(b <= a for a, b in zip(seconds, seconds[1:])):
            raise ToolError(f"expressions.values.{key} の秒は小さい順にし、重ねないでください")
        checked[key] = [[float(t), float(v)] for t, v in points]
    return checked


def _expression_settings(value, recipe_dir):
    """render.passes の expressions を確かめ、絵のパスを絶対パスにする（recipe からの相対パスも可）。"""
    if not isinstance(value, dict):
        raise ToolError(f"expressions は {{\"objects\": …, \"images\": …, \"times\": …}} にしてください: {value!r}")
    unknown = set(value) - EXPRESSION_KEYS
    if unknown:
        raise ToolError(f"expressions の知らない鍵: {sorted(unknown)}（使えるのは {sorted(EXPRESSION_KEYS)}）")
    objects = _names(value.get("objects"), "expressions.objects")
    values = _expression_values(value["values"]) if "values" in value else {}
    if values and "times" not in value:                 # 形状キーの値の時刻のキーだけ（名前の表は無い）
        if value.get("images") or value.get("shape_keys"):
            raise ToolError("expressions の images・shape_keys は、times（[[秒, 表情], …]）といっしょに使ってください")
        return {"objects": objects, "images": {}, "times": [], "shape_keys": {}, "values": values}
    images = value.get("images", {})
    if not isinstance(images, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in images.items()):
        raise ToolError(f"expressions.images は {{表情: 絵のパス}} にしてください: {images!r}")
    resolved = {}
    for name, path in images.items():
        full = resolve_path(recipe_dir, path) if recipe_dir is not None else Path(path)
        if not Path(full).is_file():
            raise ToolError(f"expressions.images の絵がありません（{name}）: {full}")
        resolved[name] = str(Path(full).resolve())
    times = value.get("times")
    if (not isinstance(times, list) or not times
            or not all(isinstance(e, list) and len(e) == 2 and isinstance(e[0], (int, float)) and not isinstance(e[0], bool)
                       and isinstance(e[1], str) and e[1] for e in times)):
        raise ToolError(f"expressions.times は [[秒, 表情], …] にしてください: {times!r}")
    starts = [float(e[0]) for e in times]
    if any(b < a for a, b in zip(starts, starts[1:])):
        raise ToolError(f"expressions.times は秒の小さい順にしてください: {starts}")
    shape_keys = value.get("shape_keys", {})
    if not isinstance(shape_keys, dict) or not all(isinstance(k, str) and k and isinstance(v, str) and v for k, v in shape_keys.items()):
        raise ToolError(f"expressions.shape_keys は {{表情: 形状キーの名前}} にしてください: {shape_keys!r}")
    named = {e[1] for e in times}
    unused = sorted((set(resolved) | set(shape_keys)) - named)
    if unused:
        raise ToolError(f"expressions の images・shape_keys に、times に出てこない表情があります: {unused}")
    if not resolved and not shape_keys and not values:
        raise ToolError("expressions には images（表情の絵）か shape_keys（表情の形状キー）か values（形状キーの値の時刻のキー）が要ります")
    both = sorted(set(shape_keys.values()) & set(values))
    if both:
        raise ToolError(f"expressions の shape_keys と values に同じ形状キーがあります（どちらで決めるか曖昧になる）: {both}")
    return {"objects": objects, "images": resolved, "times": [[float(t), name] for t, name in times], "shape_keys": dict(shape_keys),
            "values": values}


def _pass_settings(parameters, recipe_dir=None):
    """render.passes のパラメーターを確かめ、台本の settings にする（省いた値は台本の既定。長さは m）。"""
    from ..blender_adapter import pass_render
    from .motion_ops import _bone_prefix
    s = {}
    if "resolution" in parameters:
        s["resolution"] = _render_settings({"resolution": parameters["resolution"]})["resolution"]
    if "passes" in parameters:
        kinds = parameters["passes"]
        if (not isinstance(kinds, list) or not kinds or len(set(kinds)) != len(kinds)
                or not all(isinstance(k, str) and k in pass_render.PASSES for k in kinds)):
            raise ToolError(f"passes は要素の名前（{', '.join(pass_render.PASSES)}。重ならない）の並びにしてください: {kinds!r}")
        s["passes"] = kinds
    kinds = s.get("passes", pass_render.DEFAULTS["passes"])
    if "format" in parameters:
        if parameters["format"] not in ("png", "jpeg"):
            raise ToolError(f"format は png か jpeg にしてください（char はいつも透けの PNG）: {parameters['format']!r}")
        s["format"] = parameters["format"]
    if "quality" in parameters:
        quality = number(parameters, "quality", minimum=1, integer=True)
        if quality > 100:
            raise ToolError(f"quality は 1〜100 にしてください: {quality}")
        s["quality"] = quality
    if "antialiasing" in parameters:
        if parameters["antialiasing"] not in ANTIALIASING:
            raise ToolError(f"antialiasing は {', '.join(ANTIALIASING)} のどれかにしてください: {parameters['antialiasing']!r}")
        s["antialiasing"] = parameters["antialiasing"]
    camera = _section(parameters, "camera", PASS_CAMERA_KEYS)
    c = {}
    for key in ("sensor_height", "lens"):
        if key in camera:
            c[key] = float(number(camera, key, minimum=0, exclusive=True))
    if "clip" in camera:
        clip = camera["clip"]
        if (not isinstance(clip, list) or len(clip) != 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in clip) or not 0 < clip[0] < clip[1]):
            raise ToolError(f"camera.clip は [近い所, 遠い所]（m。0 < 近い所 < 遠い所）にしてください: {clip!r}")
        c["clip"] = [float(v) for v in clip]
    s["camera"] = c
    dancer = _section(parameters, "dancer", DANCER_KEYS)
    prefix = _bone_prefix(parameters)
    s["dancer"] = {"bones": [prefix + b for b in (_names(dancer["bones"], "dancer.bones") if "bones" in dancer else ["Hips"])]}
    if "sigma" in dancer:
        s["dancer"]["sigma"] = float(number(dancer, "sigma", minimum=0, exclusive=True))
    disc = _section(parameters, "disc", DISC_KEYS)
    d = {}
    if "radius" in disc:
        d["radius"] = float(number(disc, "radius", minimum=0, exclusive=True))
    if "vertices" in disc:
        d["vertices"] = int(number(disc, "vertices", minimum=3, integer=True))
    if "height" in disc:
        d["height"] = float(number(disc, "height"))
    s["disc"] = d
    if "head" in parameters:
        s["head"] = _names(parameters["head"], "head")
    elif "head" in kinds:
        raise ToolError("passes に head（顔の範囲）を入れるときは、head に顔の物体の名前の並びを書いてください")
    room = _section(parameters, "room", ROOM_KEYS)
    r = {}
    if "collection" in room:
        r["collection"] = _name(room["collection"], "room.collection")
    if "world" in room:
        world = room["world"]
        if (not isinstance(world, list) or len(world) != 3
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in world)):
            raise ToolError(f"room.world は部屋の背景の色 [R, G, B]（シーンの線形の値。0 以上）にしてください: {world!r}")
        r["world"] = [float(v) for v in world]
    if "room" in kinds and "collection" not in r:
        raise ToolError("passes に room（部屋）を入れるときは、room.collection に部屋の物体のコレクションの名前を書いてください")
    s["room"] = r
    shade = _section(parameters, "shade", SHADE_KEYS)
    sh = {}
    if "studio_light" in shade:
        sh["studio_light"] = _name(shade["studio_light"], "shade.studio_light")
    if "rotate_z" in shade:
        sh["rotate_z"] = float(number(shade, "rotate_z"))
    s["shade"] = sh
    if parameters.get("armature") is not None:
        s["armature"] = _name(parameters["armature"], "armature")
    if parameters.get("fps") is not None:
        s["fps"] = float(number(parameters, "fps", minimum=0, exclusive=True))
    if parameters.get("only") is not None:
        s["only"] = _render_settings({"only": parameters["only"]})["only"]
    mirror = _section(parameters, "mirror", MIRROR_KEYS)
    m = {}
    if "passes" in mirror:
        flipped = mirror["passes"]
        if (not isinstance(flipped, list) or len(set(flipped)) != len(flipped)
                or not all(isinstance(k, str) and k in pass_render.MIRRORED for k in flipped)):
            raise ToolError(f"mirror.passes は、床で反転したカメラでも描く要素（{', '.join(pass_render.MIRRORED)}。重ならない）の並びにしてください: "
                            f"{flipped!r}")
        m["passes"] = flipped
    if "height" in mirror:
        m["height"] = float(number(mirror, "height"))
    s["mirror"] = m
    s["phantoms"] = _phantom_settings(_section(parameters, "phantoms", PHANTOM_KEYS))
    s["fx"] = _fx_settings(_section(parameters, "fx", FX_KEYS))
    s["pool"] = _pool_settings(_section(parameters, "pool", POOL_KEYS))
    if parameters.get("expressions") is not None:
        s["expressions"] = _expression_settings(parameters["expressions"], recipe_dir)
    drawn = set(kinds) | set(m.get("passes", []))
    if "fx" in drawn and not s["fx"].get("shapes"):
        raise ToolError("passes か mirror.passes に fx（効果）を入れるときは、fx.shapes に描く形（embers・bursts・sparks・trails・ring）を書いてください")
    return s


def _during(value, where):
    """描く時間 [[頭, 終わり], …]（秒。頭 < 終わり）。"""
    if (not isinstance(value, list) or not value
            or not all(isinstance(w, list) and len(w) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in w)
                       and w[0] < w[1] for w in value)):
        raise ToolError(f"{where} は描く時間 [[頭, 終わり], …]（秒。頭 < 終わり）にしてください: {value!r}")
    return [[float(a), float(b)] for a, b in value]


def _keys_or_number(value, where, minimum=None):
    """数か時刻のキー [[時刻, 数], [時刻, 数, つなぎ方], …]（時刻の昇順。つなぎ方は linear・smooth・step）。"""
    def ok_number(v):
        return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and (minimum is None or v >= minimum)
    if ok_number(value):
        return float(value)
    if (isinstance(value, list) and value
            and all(isinstance(k, list) and len(k) in (2, 3) and isinstance(k[0], (int, float)) and not isinstance(k[0], bool)
                    and ok_number(k[1]) and (len(k) == 2 or k[2] in ("linear", "smooth", "step")) for k in value)
            and all(b[0] >= a[0] for a, b in zip(value, value[1:]))):
        return [[float(k[0]), float(k[1])] + list(k[2:]) for k in value]
    floor = "" if minimum is None else f"（{minimum:g} 以上）"
    raise ToolError(f"{where} は数{floor}か時刻のキー [[時刻, 値], [時刻, 値, つなぎ方], …]（時刻の昇順）にしてください: {value!r}")


def _fx_settings(fx):
    """効果（fx）の設定を確かめる。shapes は fx_shapes の設定（知らない形・欄は止める）、levels は形ごとの強さ。"""
    from ..blender_adapter import fx_shapes
    out = {}
    if "shapes" in fx:
        shapes = fx["shapes"]
        if not isinstance(shapes, dict):
            raise ToolError(f"fx.shapes は形の設定 {{形の名前: {{…}}}} にしてください: {shapes!r}")
        unknown = sorted(set(shapes) - set(fx_shapes.DEFAULTS))
        if unknown:
            raise ToolError(f"fx.shapes に知らない欄があります: {unknown}（形は {', '.join(fx_shapes.SHAPES)}）")
        for name in fx_shapes.SHAPES:
            if shapes.get(name) is None:
                continue
            if not isinstance(shapes[name], dict):
                raise ToolError(f"fx.shapes.{name} は設定の {{…}} にしてください（既定のままなら {{}}）: {shapes[name]!r}")
            extra = sorted(set(shapes[name]) - set(fx_shapes.DEFAULTS[name]))
            if extra:
                raise ToolError(f"fx.shapes.{name} に知らない欄があります: {extra}")
        out["shapes"] = shapes
    if "levels" in fx:
        levels = fx["levels"]
        if not isinstance(levels, dict) or set(levels) - set(FX_LEVELS):
            raise ToolError(f"fx.levels は形ごとの強さ（{', '.join(FX_LEVELS)}。数か時刻のキー）にしてください: {levels!r}")
        out["levels"] = {k: _keys_or_number(v, f"fx.levels.{k}", minimum=0) for k, v in levels.items()}
    if fx.get("clock") is not None:
        clock = _keys_or_number(fx["clock"], "fx.clock")
        if not isinstance(clock, list):
            raise ToolError("fx.clock は効果の時刻のキー [[コマの時刻, 効果の時刻], …] にしてください（コマの時刻のままなら省く）")
        out["clock"] = clock
    if "rate" in fx:
        out["rate"] = _keys_or_number(fx["rate"], "fx.rate", minimum=0)
    if fx.get("during") is not None:
        out["during"] = _during(fx["during"], "fx.during")
    if "frame0" in fx:
        out["frame0"] = float(number(fx, "frame0"))
    for key in ("fps", "reach"):
        if fx.get(key) is not None:
            out[key] = float(number(fx, key, minimum=0, exclusive=True))
    if "sigmas" in fx:
        sigmas = fx["sigmas"]
        if (not isinstance(sigmas, dict) or not all(isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0
                                                     for k, v in sigmas.items())):
            raise ToolError(f"fx.sigmas は点ごとのならし {{点の名前: σ 秒（0 以上）}} にしてください: {sigmas!r}")
        out["sigmas"] = {k: float(v) for k, v in sigmas.items()}
    return out


def _pool_settings(pool):
    """床の光のたまり（pool）の設定を確かめる。radii は 0 以上で増えていく半径（m。2 つ以上）。"""
    out = {}
    if "centre" in pool:
        out["centre"] = _pair(pool["centre"], "pool.centre")
    if "radii" in pool:
        radii = pool["radii"]
        if (not isinstance(radii, list) or len(radii) < 2
                or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 for v in radii)
                or any(b <= a for a, b in zip(radii, radii[1:]))):
            raise ToolError(f"pool.radii は 0 以上で増えていく半径の並び（m。2 つ以上）にしてください: {radii!r}")
        out["radii"] = [float(v) for v in radii]
    if "segments" in pool:
        out["segments"] = int(number(pool, "segments", minimum=3, integer=True))
    for key in ("falloff", "floor_falloff"):
        if key in pool:
            out[key] = float(number(pool, key, minimum=0, exclusive=True))
    if "floor" in pool:
        out["floor"] = float(number(pool, "floor", minimum=0))
    if "height" in pool:
        out["height"] = float(number(pool, "height"))
    if pool.get("during") is not None:
        out["during"] = _during(pool["during"], "pool.during")
    return out


def _phantom_settings(ghosts):
    """分身（phantoms）の設定を確かめる。items は [{offset: [x, y(, z)] m, delay: 秒}]、during は [[頭, 終わり], …] 秒。"""
    out = {}
    if "items" in ghosts:
        items = ghosts["items"]
        if not isinstance(items, list):
            raise ToolError(f"phantoms.items は分身の並び [{{offset, delay}}] にしてください: {items!r}")
        out["items"] = []
        for n, item in enumerate(items):
            where = f"phantoms.items[{n}]"
            if not isinstance(item, dict) or set(item) - {"offset", "delay"} or "offset" not in item:
                raise ToolError(f"{where} は {{offset: [x, y]（m）, delay: 秒}} にしてください: {item!r}")
            offset = item["offset"]
            if (not isinstance(offset, list) or len(offset) not in (2, 3)
                    or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in offset)):
                raise ToolError(f"{where}.offset は踊り手からのずらし [x, y] か [x, y, z]（m）にしてください: {offset!r}")
            delay = float(number(item, "delay", 0.0, minimum=0))
            out["items"].append({"offset": [float(v) for v in offset], "delay": delay})
    if ghosts.get("during") is not None:
        out["during"] = _during(ghosts["during"], "phantoms.during")
    if "mirror" in ghosts:
        out["mirror"] = _flag(ghosts, "mirror", "phantoms.mirror")
    if "point" in ghosts:
        out["point"] = _name(ghosts["point"], "phantoms.point")
    for key, minimum, exclusive in (("sigma_s", 0, False), ("reach", 0, True), ("fps", 0, True)):
        if ghosts.get(key) is not None:
            out[key] = float(number(ghosts, key, minimum=minimum, exclusive=exclusive))
    if "frame0" in ghosts:
        out["frame0"] = float(number(ghosts, "frame0"))
    return out


def _camera_record(path, resolution):
    """入力 cameras（model3d-cameras/0.1）を読み、T が重ならないことと、画面の縦横比が描く大きさと同じことを確かめる。"""
    from .. import contracts
    record = contracts.load("cameras", path)
    seen = set()
    for frame in record["frames"]:
        if frame["T"] in seen:
            raise ToolError(f"cameras の T が重なっています: {frame['T']}")
        seen.add(frame["T"])
    width, height = record["resolution"]
    if width * resolution[1] != height * resolution[0]:
        raise ToolError(f"cameras の画面（{width}×{height}）と描く大きさ（{resolution[0]}×{resolution[1]}）の縦横比が違います")
    return record


def phantom_order(report, settings, bones, record, cameras_out):
    """分身を描くコマの、踊り手と分身のカメラからの距離と near（分身のほうが近い）。{T: [{i, distance_m, near}]}。
    点は骨の通り道（bones）の phantoms.point を σ phantoms.sigma_s 秒でならしたもの（試作と同じ）。カメラの位置は、入力の
    記録があればその行列（入れた行列）、無ければ Blender が書いた控え。"""
    from ..blender_adapter import pass_render
    from ..blender_adapter.point_track import PathError, Samples
    ghosts = pass_render.merged(settings)["phantoms"]
    if not ghosts["items"]:
        return {}
    dance_fps = ghosts["fps"] or report["phantoms"]["dance_fps"]
    try:
        points = Samples(bones, {ghosts["point"]: ghosts["sigma_s"]}, ghosts["frame0"], dance_fps, ghosts["reach"])
    except PathError as error:
        raise ToolError(f"render.passes の分身: {error}") from error
    positions = {fr["T"]: [row[3] for row in fr["matrix"][:3]] for fr in cameras_out["frames"]}
    for fr in (record or {}).get("frames", []):
        positions[fr["T"]] = [row[3] for row in fr["matrix"][:3]]
    out = {}
    for item in report["planned"]:
        T = item["T"]
        if not pass_render.within((T - 1) / report["fps"], ghosts["during"]):
            continue
        dance_t = (float(item["f"]) - ghosts["frame0"]) / dance_fps
        out[T] = pass_render.phantom_distances(points, ghosts["point"], dance_t, ghosts["items"], positions[T])[1]
    return out


def passes_manifest(report, passes_dir, settings, order=None):
    """描く並び（報告の planned）のうち、要素ごとにファイルのあるコマの sha256 と大きさ。無いコマは missing。分身（ph<i>・rph<i>）・
    効果（fx・rfx）・床の光のたまり（pool）は、描く時間（during）の外のコマを outside に分ける。分身は、描いたコマに near（分身のほうが
    カメラに近い）と距離を書く（order。phantom_order）。"""
    from .. import contracts
    from ..blender_adapter import pass_render
    merged = pass_render.merged(settings)
    ghosts = set(pass_render.phantom_kinds(merged))
    passes = {}
    for kind in pass_render.all_kinds(merged):
        frames, missing, outside = [], [], []
        index = pass_render.phantom_index(kind)
        for item in report["planned"]:
            if not pass_render.drawn_at(kind, (item["T"] - 1) / report["fps"], merged):
                outside.append(item["T"])
                continue
            name = pass_render.pass_file(kind, item["T"], merged["format"])
            file = Path(passes_dir) / name
            if file.is_file():
                entry = {"T": item["T"], "f": item["f"], "file": name, "sha256": sha256(file), "bytes": file.stat().st_size}
                if kind in ghosts and order and item["T"] in order:
                    entry["near"] = order[item["T"]][index]["near"]
                    entry["distance_m"] = order[item["T"]][index]["distance_m"]
                frames.append(entry)
            else:
                missing.append(item["T"])
        passes[kind] = {"format": "png" if pass_render.extension(kind, merged["format"]) == "png" else "jpeg",
                        "alpha": pass_render.base_kind(kind) in pass_render.ALPHA, "frames": frames, "missing": missing}
        if outside:
            passes[kind]["outside"] = outside
    manifest = {"schema_version": "model3d-passes/0.1", "directory": str(Path(passes_dir).resolve()), "fps": report["fps"],
                "resolution": report["resolution"], "passes": passes}
    return contracts.validate("passes", manifest)


def _passes_key(inputs, settings, blender, addons, extensions, bones_used):
    """render.passes の入力のまとまり（コマのフォルダの入力の控え。render_inputs）。描く絵を変えない only と、どの要素を描くかの
    passes・mirror.passes は除く（要素を足しても、もう描いた要素のコマはそのまま使える）。表情の絵はパスでなく中身（sha256）で控える。"""
    from .. import render_inputs
    from ..blender_adapter import launcher
    from ..io import sha256 as file_sha256
    kept = {k: v for k, v in settings.items() if k not in ("only", "passes")}
    if "mirror" in kept:
        kept["mirror"] = {k: v for k, v in kept["mirror"].items() if k != "passes"}
    if kept.get("expressions"):
        kept["expressions"] = {**kept["expressions"],
                               "images": {name: file_sha256(path) for name, path in kept["expressions"]["images"].items()}}
    return {"operation": "render.passes", "blend": render_inputs.file_sha(inputs["blend"]),
            "track": render_inputs.file_sha(inputs["track"]), "cameras": render_inputs.file_sha(inputs.get("cameras")),
            "bones": render_inputs.file_sha(inputs["bones"]) if bones_used else None, "settings": kept,
            "scripts": render_inputs.scripts(PASS_SCRIPT), "blender": launcher.blender_version(blender),
            "addons": [launcher.addon_record(name, extensions) for name in addons]}


@register("render.passes", inputs=("blend", "track", "cameras", "bones"), outputs=("passes", "cameras", "report"), cacheable=False,
          summary="キャラ（blend）を要素に分けて、Blender の簡易描画（Workbench）でコマごとに描く: room（部屋の物体）・char（人物の"
                  "色の絵のまま。透けの PNG）・shade（白い人物にスタジオの光）・disc（踊り手の点の真下の床の円）・head（顔の物体だけ白）。"
                  "mirror を書くと、char・shade をカメラを床の面で反転して描いた r<要素>（床への映り込み）も描く。"
                  "phantoms を書くと、骨組みをずらし踊りの時刻を遅らせた分身 ph<i>（と反転した rph<i>）も描き、骨の通り道（bones）の点で"
                  "分身と踊り手のカメラからの距離を比べて、一覧に near（分身が手前）を書く。"
                  "コマの並びとカメラはトラック（model3d-stage-track/0.1）、cameras（model3d-cameras/0.1）があればそのコマのカメラは"
                  "行列と焦点距離と主点から。出力の cameras は、並びの全部のコマのカメラと踊り手の点（重ねる側が使う）。描いたコマは"
                  "とばす（続きから描ける）。とばせるのは、フォルダの入力の控え（model3d_inputs.json）がいまの入力と同じコマだけで、"
                  "違うコマ・控えの無いコマ・別の入力で描くと控えたコマがあれば描く前に止める。render.stage と並ぶ 2 つ目の描き方で、重ね合わせは Blender の外。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def render_passes(inputs, parameters, outputs, context):
    from .. import contracts, render_inputs
    from ..blender_adapter import launcher
    from ..blender_adapter import pass_render as script
    from ..blender_adapter import stage_render
    from ..io import read_json
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend", "track"), "render.passes")
    unknown = set(parameters) - PASS_PARAMETERS
    if unknown:
        raise ToolError(f"render.passes の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PASS_PARAMETERS)}）")
    missing = [name for name in ("passes", "cameras", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.passes には outputs の {', '.join(missing)} が要ります")
    settings = _pass_settings(parameters, getattr(context, "recipe_dir", None))
    merged = script.merged(settings)
    track = _track(inputs["track"])
    record = _camera_record(inputs["cameras"], merged["resolution"]) if inputs.get("cameras") is not None else None
    bones = contracts.load("bone-path", inputs["bones"]) if inputs.get("bones") is not None else None
    if merged["phantoms"]["items"] and bones is None:
        raise ToolError("render.passes の phantoms（分身）には、inputs の bones（骨の通り道。分身と踊り手の距離を測る点）が要ります")
    effects = "fx" in {script.base_kind(k) for k in script.all_kinds(merged)}
    needed = script.fx_points(merged) if effects else []
    if needed and bones is None:
        raise ToolError(f"render.passes の fx（効果）の点（{', '.join(needed)}）には、inputs の bones（骨の通り道）が要ります")
    if needed:
        known = set((bones.get("points") or {}).keys()) | set((bones.get("bones") or {}).keys())
        absent = [name for name in needed if name not in known]
        if absent:
            raise ToolError(f"render.passes の fx: 骨の通り道に無い点 {absent}（motion.bone_path の points か bones に書いてください）")
    plan = stage_render.frame_plan(track, 0, 0)
    problem = script.cameras_problem(plan, script.record_cameras(record))
    if problem:
        raise ToolError(f"render.passes: {problem}")
    if merged["only"]:
        absent = sorted(set(merged["only"]) - {T for T, _, _ in plan})
        if absent:
            raise ToolError(f"only にトラックの並びに無い T があります: {absent}")
    blender = _blender(parameters, context, "render.passes")
    addons, extensions = blender_addons(parameters, context)
    if parameters.get("passes_dir"):
        passes_dir = resolve_path(context.recipe_dir, parameters["passes_dir"])
    else:
        passes_dir = Path(context.step_dir) / "passes"
    passes_dir.mkdir(parents=True, exist_ok=True)
    key = _passes_key(inputs, settings, blender, addons, extensions, bool(needed))
    chosen = set(merged["only"]) if merged["only"] else None
    files = [script.pass_file(kind, T, merged["format"]) for T, _, _ in plan if chosen is None or T in chosen
             for kind in script.all_kinds(merged)]
    # 確かめと同じ待ち合わせの中で、描く前に控える（途中で止まっても、描いたコマには控えがある）
    _, kept = render_inputs.check(passes_dir, key, files, "render.passes", claim=True)
    settings_path = Path(context.step_dir) / "pass_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--track", inputs["track"],
            "--passes-dir", str(passes_dir), "--report", outputs["report"], "--cameras-out", outputs["cameras"]]
    if record is not None:
        args += ["--cameras", inputs["cameras"]]
    if needed:
        args += ["--bones", inputs["bones"]]
    run = launcher.run_blender(blender, PASS_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                               timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    cameras_out = contracts.load("cameras", outputs["cameras"])
    order = phantom_order(report, merged, bones, record, cameras_out) if bones is not None else None
    manifest = passes_manifest(report, passes_dir, merged, order)
    atomic_json(outputs["passes"], manifest)
    observation = {"kind": "pass_render", "exit_code": run["exit_code"], "seconds": run["seconds"],
                   "blender_version": run["blender_version"], "addons": run.get("addons"), "planned": len(report["planned"]),
                   "passes": {k: {"rendered": len(report["rendered"].get(k, [])),
                                  "skipped_existing": len(report["skipped_existing"].get(k, [])),
                                  "frames": len(v["frames"]), "missing": len(v["missing"])} for k, v in manifest["passes"].items()},
                   "camera": report.get("camera"), "objects": {k: len(v) for k, v in (report.get("objects") or {}).items()},
                   "inputs": {"key": render_inputs.key_id(key), "kept": kept}}
    if merged.get("expressions"):                       # 表情の絵の素性（隠れた入力なので、sha256 を残す）
        observation["expressions"] = {"images": {name: {"path": path, "sha256": sha256(path)}
                                                 for name, path in merged["expressions"]["images"].items()},
                                      "counts": (report.get("expressions") or {}).get("counts"),
                                      "values": sorted(merged["expressions"].get("values") or {})}
    return StepResult(observations=[observation])


BOX_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "box_room.py"
BOX_PARAMETERS = {"blender", "focal_px", "center_px", "camera_height", "scale", "origin_px", "wall_px", "floor", "wall", "shell",
                  "collection", "armature", "timeout"}


def _pair(value, where):
    if not isinstance(value, list) or len(value) != 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
        raise ToolError(f"{where} は [x, y]（数 2 つ）にしてください: {value!r}")
    return [float(v) for v in value]


def _box_settings(parameters, image_size):
    """render.box_room のパラメーターを確かめる（絵の中の仮の部屋の置き方。docs/model3d.md の「箱の部屋」）。image_size は絵の（幅, 高さ）。"""
    from ..blender_adapter import box_room
    missing = [k for k in ("focal_px", "center_px", "camera_height", "origin_px", "wall_px") if k not in parameters]
    if missing:
        raise ToolError(f"render.box_room にはパラメーター {', '.join(missing)} が要ります")
    s = {"focal_px": float(number(parameters, "focal_px", minimum=0, exclusive=True)),
         "center_px": _pair(parameters["center_px"], "center_px"),
         "camera_height": float(number(parameters, "camera_height", minimum=0, exclusive=True)),
         "origin_px": _pair(parameters["origin_px"], "origin_px"), "wall_px": float(number(parameters, "wall_px"))}
    if "scale" in parameters:
        s["scale"] = float(number(parameters, "scale", minimum=0, exclusive=True))
    floor = _section(parameters, "floor", {"rows", "x"})
    f = {}
    if "rows" in floor:
        rows = floor["rows"]
        if (not isinstance(rows, list) or len(rows) != 3 or not all(isinstance(v, int) and not isinstance(v, bool) for v in rows)
                or rows[2] < 1 or rows[1] - rows[0] < 2 * rows[2]):
            raise ToolError(f"floor.rows は [最初の行, 終わりの行（含まない）, きざみ]（整数。段が 2 つ以上）にしてください: {rows!r}")
        f["rows"] = rows
    if "x" in floor:
        xs = floor["x"]
        if (not isinstance(xs, list) or len(xs) < 2 or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in xs)
                or any(b <= a for a, b in zip(xs, xs[1:]))):
            raise ToolError(f"floor.x は床の列の x（2 つ以上、小さい順）にしてください: {xs!r}")
        f["x"] = [float(v) for v in xs]
    s["floor"] = f
    wall = _section(parameters, "wall", {"x", "top"})
    w = {}
    if "x" in wall:
        w["x"] = _pair(wall["x"], "wall.x")
        if w["x"][0] >= w["x"][1]:
            raise ToolError(f"wall.x は [左の端, 右の端]（左 < 右）にしてください: {wall['x']!r}")
    if "top" in wall:
        w["top"] = float(number(wall, "top"))
        if w["top"] >= s["camera_height"]:
            raise ToolError("wall.top（壁の上の端の y。下が正）は camera_height（床）より小さくしてください")
    s["wall"] = w
    if "shell" in parameters:
        shell = parameters["shell"]
        if shell is None:
            s["shell"] = None
        else:
            shell = _section(parameters, "shell", {"location", "size", "color"})
            sh = {}
            for key in ("location", "size", "color"):
                if key in shell:
                    value = shell[key]
                    if not isinstance(value, list) or len(value) != 3 or not all(
                            isinstance(v, (int, float)) and not isinstance(v, bool) for v in value):
                        raise ToolError(f"shell.{key} は数 3 つにしてください: {value!r}")
                    if key != "location" and any(v < 0 or (key == "size" and v == 0) for v in value):
                        raise ToolError(f"shell.{key} は {'0 より大きい' if key == 'size' else '0 以上の'}数にしてください: {value!r}")
                    sh[key] = [float(v) for v in value]
            s["shell"] = sh
    if "collection" in parameters:
        s["collection"] = _name(parameters["collection"], "collection")
    if parameters.get("armature") is not None:
        s["armature"] = _name(parameters["armature"], "armature")
    problem = box_room.room_problem(box_room.merged(s), image_size)
    if problem:
        raise ToolError(f"render.box_room: {problem}")
    return s


@register("render.box_room", inputs=("blend", "image"), outputs=("blend", "report"), cacheable=False,
          summary="1 枚の背景の絵（image）を、基準のカメラ（焦点距離・主点・床からの高さ）から床と奥の壁に投影して張った「箱の部屋」を"
                  "キャラの .blend（blend）に足す（Blender の中で）。基準のカメラから見ると絵と同じ画になり、カメラが動くと床と壁が"
                  "ずれて奥行きが出る。踊り手の立つ所（絵の画素）が世界の原点、scale 倍が m。絵の届かない向きは暗い箱（shell）で囲う。"
                  "物体は collection（既定 model3d_room）に入れ、render.passes の room.collection で描く。絵は .blend に詰め込む。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def box_room(inputs, parameters, outputs, context):
    from ..blender_adapter import launcher
    from ..io import read_json
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend", "image"), "render.box_room")
    unknown = set(parameters) - BOX_PARAMETERS
    if unknown:
        raise ToolError(f"render.box_room の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(BOX_PARAMETERS)}）")
    missing = [name for name in ("blend", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.box_room には outputs の {', '.join(missing)} が要ります")
    from PIL import Image
    try:
        with Image.open(inputs["image"]) as picture:
            image_size = picture.size
    except OSError as error:
        raise ToolError(f"render.box_room: 背景の絵を読めません: {inputs['image']}（{error}）") from error
    settings = _box_settings(parameters, image_size)
    blender = _blender(parameters, context, "render.box_room")
    settings_path = Path(context.step_dir) / "box_room_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--image", inputs["image"], "--settings", str(settings_path),
            "--out-blend", outputs["blend"], "--report", outputs["report"]]
    run = launcher.run_blender(blender, BOX_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                               timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    observation = {"kind": "box_room", "exit_code": run["exit_code"], "seconds": run["seconds"],
                   "blender_version": run["blender_version"], "collection": report.get("collection"), "objects": report.get("objects"),
                   "scene": report.get("scene"), "vertices": report.get("vertices"), "faces": report.get("faces"),
                   "wall_depth": report.get("wall_depth"),
                   "camera_m": report.get("camera_m")}
    return StepResult(observations=[observation])


PART_PARAMETERS = {"blender", "addons", "blender_extensions", "frames", "bone_meshes", "bone_colors", "bone_default", "object_colors",
                   "default_color", "show", "hide", "resolution", "format", "quality", "antialiasing", "cavity", "world", "camera",
                   "armature", "timeout"}
PART_CAMERA_KEYS = {"yaw", "height", "center", "lens", "target"}
PART_SCRIPT = Path(__file__).resolve().parents[1] / "blender_adapter" / "part_colors.py"


def _color(value, where):
    """シーンの線形の色 [r, g, b]（0 以上の数）。"""
    if not (isinstance(value, list) and len(value) == 3
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 for v in value)):
        raise ToolError(f"{where} は色 [r, g, b]（シーンの線形の値。0 以上の数）にしてください: {value!r}")
    return [float(v) for v in value]


def _part_settings(parameters):
    """render.part_colors のパラメーターを確かめ、台本の settings にする（省いた値は台本の既定。長さは m）。"""
    frames = parameters.get("frames")
    if (not isinstance(frames, list) or not frames
            or not all(isinstance(f, (int, float)) and not isinstance(f, bool) and math.isfinite(f) for f in frames)):
        raise ToolError(f"frames は描く動きのコマ（数。小数は前後のコマの間の時刻）の並びにしてください: {frames!r}")
    s = {"frames": frames}
    for key in ("bone_meshes", "show", "hide"):
        if key in parameters:
            s[key] = _names(parameters[key], key)
    if set(s.get("show", [])) & set(s.get("hide", [])):
        raise ToolError(f"show と hide に同じ物体があります: {sorted(set(s['show']) & set(s['hide']))}")
    if "bone_colors" in parameters:
        pairs = parameters["bone_colors"]
        if not isinstance(pairs, list) or not all(isinstance(p, list) and len(p) == 2 and isinstance(p[0], str) and p[0] for p in pairs):
            raise ToolError(f"bone_colors は [頂点グループの名前の頭, [r, g, b]] の並びにしてください: {pairs!r}")
        s["bone_colors"] = [[p[0], _color(p[1], f"bone_colors[{n}]")] for n, p in enumerate(pairs)]
    if s.get("bone_meshes") and not s.get("bone_colors"):
        raise ToolError("bone_meshes を挙げるときは、bone_colors（頂点グループの名前の頭と色）も書いてください")
    if "object_colors" in parameters:
        colors = parameters["object_colors"]
        if not isinstance(colors, dict) or not all(isinstance(k, str) and k for k in colors):
            raise ToolError(f"object_colors は {{物体の名前: [r, g, b]}} にしてください: {colors!r}")
        s["object_colors"] = {k: _color(v, f"object_colors.{k}") for k, v in colors.items()}
    for key in ("bone_default", "default_color", "world"):
        if key in parameters:
            s[key] = _color(parameters[key], key)
    if "resolution" in parameters:
        s["resolution"] = _render_settings({"resolution": parameters["resolution"]})["resolution"]
    if "format" in parameters:
        if parameters["format"] not in ("png", "jpeg"):
            raise ToolError(f"format は png か jpeg にしてください: {parameters['format']!r}")
        s["format"] = parameters["format"]
    if "quality" in parameters:
        quality = number(parameters, "quality", minimum=1, integer=True)
        if quality > 100:
            raise ToolError(f"quality は 1〜100 にしてください: {quality}")
        s["quality"] = quality
    if "antialiasing" in parameters:
        if parameters["antialiasing"] not in ANTIALIASING:
            raise ToolError(f"antialiasing は {', '.join(ANTIALIASING)} のどれかにしてください: {parameters['antialiasing']!r}")
        s["antialiasing"] = parameters["antialiasing"]
    if "cavity" in parameters:
        s["cavity"] = _flag(parameters, "cavity")
    if parameters.get("armature") is not None:
        s["armature"] = _name(parameters["armature"], "armature")
    camera = _section(parameters, "camera", PART_CAMERA_KEYS)
    c = {}
    for key in ("height", "lens"):
        if key in camera:
            c[key] = float(number(camera, key, minimum=0, exclusive=True))
    for key in ("yaw", "center"):
        if key in camera:
            c[key] = float(number(camera, key))
    if "target" in camera:
        target = camera["target"]
        if not (isinstance(target, list) and len(target) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in target)):
            raise ToolError(f"camera.target は狙う点 [x, y]（m）にしてください: {target!r}")
        c["target"] = [float(v) for v in target]
    s["camera"] = c
    return s


@register("render.part_colors", inputs=("blend",), outputs=("frames", "report"), cacheable=False,
          summary="部位を色分けして描く診断の絵（Blender の簡易描画）: bone_meshes のメッシュは頂点ごとにいちばん重みの大きい頂点グループの"
                  "名前の頭（bone_colors）で、ほかのメッシュは物体ごとに 1 色（object_colors）。show で描画から隠した物（布の身代わり・"
                  "当たり判定の写し）も描き、hide の物は描かない。カメラは狙う点の方位 yaw から水平に。コマ（frames。小数も）ごとに "
                  "f<T>.jpg（png も）。何が何を通り抜けているかを目で見分ける（内側に入った頂点の数では、脚がまるごと布を通り抜ける"
                  "失敗を拾えない）。.blend は変えない。frames は描いた絵の一覧（model3d-frames/0.1）。Blender が要り、再利用しない")
def render_part_colors(inputs, parameters, outputs, context):
    from .. import contracts
    from ..blender_adapter import launcher
    from ..blender_adapter import part_colors as script
    from ..io import read_json
    from .blender_ops import blender_addons
    from .motion_ops import _blender, _timeout
    require(inputs, ("blend",), "render.part_colors")
    unknown = set(parameters) - PART_PARAMETERS
    if unknown:
        raise ToolError(f"render.part_colors の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PART_PARAMETERS)}）")
    missing = [name for name in ("frames", "report") if name not in outputs]
    if missing:
        raise ToolError(f"render.part_colors には outputs の {', '.join(missing)} が要ります")
    settings = _part_settings(parameters)
    merged = script.merged(settings)
    blender = _blender(parameters, context, "render.part_colors")
    addons, extensions = blender_addons(parameters, context)
    frames_dir = Path(context.step_dir) / "frames"          # 工程のフォルダの中（実行ごとに新しい）
    frames_dir.mkdir(parents=True, exist_ok=True)
    settings_path = Path(context.step_dir) / "part_colors_settings.json"
    atomic_json(settings_path, settings)
    run = launcher.run_blender(blender, PART_SCRIPT, None, ["--blend", inputs["blend"], "--settings", str(settings_path),
                                                            "--frames-dir", str(frames_dir), "--report", outputs["report"]],
                               log=Path(context.step_dir) / "blender.log", timeout=_timeout(parameters), addons=addons,
                               extensions=extensions)
    report = read_json(outputs["report"])
    frames, absent = [], []
    for item in report["frames"]:
        file = frames_dir / item["file"]
        if not file.is_file():
            absent.append(item["T"])
            continue
        frames.append({"T": item["T"], "f": item["f"], "file": item["file"], "sha256": sha256(file), "bytes": file.stat().st_size})
    if absent:
        raise ToolError(f"render.part_colors: 描いたはずの絵がありません（T {absent}）")
    manifest = contracts.validate("frames", {"schema_version": "model3d-frames/0.1", "directory": str(frames_dir.resolve()),
                                             "fps": report["fps"], "resolution": merged["resolution"], "frames": frames,
                                             "missing": []})
    atomic_json(outputs["frames"], manifest)
    observation = {"kind": "part_colors", "exit_code": run["exit_code"], "seconds": run["seconds"],
                   "blender_version": run["blender_version"], "addons": run.get("addons"), "frames": len(frames),
                   "drawn": len(report.get("drawn") or []), "camera": report.get("camera")}
    return StepResult(observations=[observation])
