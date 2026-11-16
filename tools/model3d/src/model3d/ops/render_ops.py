"""描く: 塗りと輪郭線を付けたキャラを、舞台と光の中で、コマごとに PNG に描く（Blender の中で）。描く直前の押し出し（push）
の準備として、当たりの写しと押し出す頂点の組も足す（render.prepare_push）。もう 1 つの描き方として、キャラを要素に分けて簡易描画で
描き、カメラを控える（render.passes。重ね合わせは Blender の外）。"""
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


@register("render.stage", inputs=("blend", "track"), outputs=("frames", "report"), cacheable=False,
          summary="塗りと輪郭線を付けたキャラ（blend）を、舞台（床・LED の壁・トラス・光の柱）と 4 つのスポットの中で、コマごとに "
                  "PNG に描く（Blender の EEVEE）。トラック（model3d-stage-track/0.1）があれば、そのコマの並びとカメラ・舞台・光・"
                  "塗りの値で、無ければシーンの全コマを腰を追う正面のカメラと動かない光で描く。輪郭線の太さはカメラの距離で"
                  "画面の上の太さにそろえる。舞台を切って background で緑背景か透けにも描ける。push で、描く直前に帯などの頂点を"
                  "当たり（閉じたメッシュ）の外へ出す。描いたコマはとばす（続きから描ける）。frames は描いたコマの一覧（model3d-frames/0.1）。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def stage_render(inputs, parameters, outputs, context):
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
    if inputs.get("track") is not None:
        _track(inputs["track"])
    if parameters.get("frames_dir"):
        frames_dir = resolve_path(context.recipe_dir, parameters["frames_dir"])
    else:
        frames_dir = Path(context.step_dir) / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    settings_path = Path(context.step_dir) / "render_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--frames-dir", str(frames_dir),
            "--report", outputs["report"]]
    if inputs.get("track") is not None:
        args += ["--track", inputs["track"]]
    record = launcher.run_blender(blender, RENDER_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                                  timeout=_timeout(parameters))
    report = read_json(outputs["report"])
    manifest = frames_manifest(report, frames_dir)
    atomic_json(outputs["frames"], manifest)
    observation = {"kind": "stage_render", "exit_code": record["exit_code"], "seconds": record["seconds"],
                   "blender_version": record["blender_version"], "planned": len(report["planned"]),
                   "rendered": len(report.get("rendered") or []), "skipped_existing": len(report.get("skipped_existing") or []),
                   "frames": len(manifest["frames"]), "missing": len(manifest["missing"])}
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
                   "dancer", "disc", "head", "room", "shade", "bone_prefix", "armature", "fps", "passes_dir", "only", "timeout"}
PASS_CAMERA_KEYS = {"sensor_height", "lens", "clip"}
DANCER_KEYS = {"bones", "sigma"}
DISC_KEYS = {"radius", "vertices", "height"}
ROOM_KEYS = {"collection", "world"}
SHADE_KEYS = {"studio_light", "rotate_z"}
ANTIALIASING = ("OFF", "FXAA", "5", "8", "11", "16", "32")


def _pass_settings(parameters):
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
    return s


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


def passes_manifest(report, passes_dir, settings):
    """描く並び（報告の planned）のうち、要素ごとにファイルのあるコマの sha256 と大きさ。無いコマは missing。"""
    from .. import contracts
    from ..blender_adapter import pass_render
    passes = {}
    for kind in settings["passes"]:
        frames, missing = [], []
        for item in report["planned"]:
            name = pass_render.pass_file(kind, item["T"], settings["format"])
            file = Path(passes_dir) / name
            if file.is_file():
                frames.append({"T": item["T"], "f": item["f"], "file": name, "sha256": sha256(file), "bytes": file.stat().st_size})
            else:
                missing.append(item["T"])
        passes[kind] = {"format": "png" if pass_render.extension(kind, settings["format"]) == "png" else "jpeg",
                        "alpha": kind == "char", "frames": frames, "missing": missing}
    manifest = {"schema_version": "model3d-passes/0.1", "directory": str(Path(passes_dir).resolve()), "fps": report["fps"],
                "resolution": report["resolution"], "passes": passes}
    return contracts.validate("passes", manifest)


@register("render.passes", inputs=("blend", "track", "cameras"), outputs=("passes", "cameras", "report"), cacheable=False,
          summary="キャラ（blend）を要素に分けて、Blender の簡易描画（Workbench）でコマごとに描く: room（部屋の物体）・char（人物の"
                  "色の絵のまま。透けの PNG）・shade（白い人物にスタジオの光）・disc（踊り手の点の真下の床の円）・head（顔の物体だけ白）。"
                  "コマの並びとカメラはトラック（model3d-stage-track/0.1）、cameras（model3d-cameras/0.1）があればそのコマのカメラは"
                  "行列と焦点距離と主点から。出力の cameras は、並びの全部のコマのカメラと踊り手の点（重ねる側が使う）。描いたコマは"
                  "とばす（続きから描ける）。render.stage と並ぶ 2 つ目の描き方で、重ね合わせは Blender の外。"
                  "Blender は外部の実行ファイルなので再利用しない（毎回実行）")
def render_passes(inputs, parameters, outputs, context):
    from .. import contracts
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
    settings = _pass_settings(parameters)
    merged = script.merged(settings)
    track = _track(inputs["track"])
    record = _camera_record(inputs["cameras"], merged["resolution"]) if inputs.get("cameras") is not None else None
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
    settings_path = Path(context.step_dir) / "pass_settings.json"
    atomic_json(settings_path, settings)
    args = ["--blend", inputs["blend"], "--settings", str(settings_path), "--track", inputs["track"],
            "--passes-dir", str(passes_dir), "--report", outputs["report"], "--cameras-out", outputs["cameras"]]
    if record is not None:
        args += ["--cameras", inputs["cameras"]]
    run = launcher.run_blender(blender, PASS_SCRIPT, None, args, log=Path(context.step_dir) / "blender.log",
                               timeout=_timeout(parameters), addons=addons, extensions=extensions)
    report = read_json(outputs["report"])
    contracts.load("cameras", outputs["cameras"])
    manifest = passes_manifest(report, passes_dir, merged)
    atomic_json(outputs["passes"], manifest)
    observation = {"kind": "pass_render", "exit_code": run["exit_code"], "seconds": run["seconds"],
                   "blender_version": run["blender_version"], "addons": run.get("addons"), "planned": len(report["planned"]),
                   "passes": {k: {"rendered": len(report["rendered"].get(k, [])),
                                  "skipped_existing": len(report["skipped_existing"].get(k, [])),
                                  "frames": len(v["frames"]), "missing": len(v["missing"])} for k, v in manifest["passes"].items()},
                   "camera": report.get("camera"), "objects": {k: len(v) for k, v in (report.get("objects") or {}).items()}}
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
