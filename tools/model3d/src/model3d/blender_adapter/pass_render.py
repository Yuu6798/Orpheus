"""Blender の中で動かす: キャラを要素（部屋・人物・人物の陰影・足もとの影の円・顔の範囲）に分けて、簡易描画（Workbench）で
コマごとに描き、カメラと踊り手の点を控える。重ね合わせは Blender の外で行う（docs/model3d.md の「要素別に描く」）。

    blender --background --factory-startup --python pass_render.py -- --blend in.blend --settings settings.json \
        --track track.json --passes-dir passes --report report.json --cameras-out cameras.json [--cameras cameras.json]

要素（settings の passes に挙げたものだけ。どれも Workbench・色の変換は Standard・アンチエイリアスは antialiasing）:
- room: 部屋の物体（room.collection のコレクションの物体）だけを、色の絵のまま平らな光で。背景は room.world（シーンの線形の色）
- char: 人物（部屋と円のほかの、描画に出る物体）だけを、色の絵のまま平らな光で。背景は透け（RGBA の PNG）
- shade: 人物を白一色で、スタジオの光（shade.studio_light。世界に固定した向き・照り返しなし）。背景は黒。重ねる側が光と陰の色を
  混ぜる割合に使う
- disc: 足もとの影のもとになる円（半径 disc.radius・頂点 disc.vertices の多角形）を、踊り手の点の真下の高さ disc.height に白で。
  背景は黒
- head: 顔の範囲。head に挙げた物体を白、ほかの人物の物体を黒で（髪などに隠れた所は黒）。背景は黒
- r<要素>（mirror.passes に挙げた char・shade）: 同じ要素を、カメラを床の面（高さ mirror.height）で反転して描いたもの（床への映り込み）。
  反転したカメラの行列は、入れた行列（記録のカメラなら記録の行列、トラックのカメラなら Blender の行列）に左から鏡の行列を掛けたもの
- ph<i>（phantoms.items の i 番目の分身）: 人物の骨組みを offset（m）だけずらし、踊りの時刻を delay 秒遅らせて、shade と同じ白い人物と
  スタジオの光で描いたもの（背景は透け。RGBA の PNG）。phantoms.during の時刻（コマの時刻 (T − 1) / fps）のコマだけ描く。
  phantoms.mirror なら、反転したカメラでも描く（rph<i>）。分身と踊り手の点（骨の通り道の phantoms.point を σ phantoms.sigma_s 秒で
  ならしたもの）のカメラからの距離を報告に書き、分身のほうが近ければ near（重ねる側が人物の手前に重ねる）
ファイルは <passes-dir>/<要素>/f<T>.png（char・rchar 以外は format が jpeg なら .jpg）。描いてあるファイルはとばす（続きから描ける）。
画像に日時などの控え（メタデータ）を書かない（同じ入力なら同じバイト列）。

settings.json（render.passes が書く。DEFAULTS に重ねる）:
- resolution、passes、format（char 以外の要素の形式。png か jpeg）・quality（jpeg の品質）、antialiasing（Workbench の標本の数）
- camera: sensor_height（縦の受光の大きさ mm。受光は縦に合わせる）、lens（トラックのカメラにレンズが無いときの mm）、clip（写る範囲 m）
- dancer: bones（踊り手の点にする骨の付け根の平均）、sigma（動きのコマの並びでならすガウスの σ。コマ）
- disc（m）、head（物体の名前の並び）、room（collection・world）、shade（studio_light・rotate_z 度）、armature、only（描く T）
- mirror: passes（反転したカメラでも描く要素の並び。char・shade）、height（床の面の高さ m）
- phantoms: items（[{offset, delay}]）、during（[[頭, 終わり]] 秒）、mirror、point・sigma_s・reach（距離に使う点とならし）、
  frame0・fps（動きのコマ → 踊りの時刻。fps を省くと .blend の fps）

カメラ（コマ T ごと）:
- 入力 cameras（model3d-cameras/0.1）にあれば、その行列（matrix_world に入れる。回転の持ち方は物体の既定の XYZ のオイラー角のまま）と、
  焦点距離・主点（その記録の resolution の画素）から、レンズとずらしを決める。dancer があれば、それを踊り手の点にする。
- 無ければトラックのカメラ（位置・狙う点・レンズ。render.stage と同じく、四元数で狙う点へ向ける。ずらしは 0）。
長さはトラック・記録・設定とも m で、シーンの scale_length（1 Blender 単位が何 m か）で直す。
シーンは、アーマチュア（armature か最初のアーマチュア）の入ったシーンにする（作業用のシーンを開いたまま保存されたファイルがある）。
人物と部屋の物体は、そのシーンの物体から選ぶ。
出力の cameras-out は、並びの全部の T のカメラ（Blender が持ち直した行列・レンズ・ずらし・焦点距離・主点）と踊り手の点・距離。
**検証**: 本物の Blender 5.2.4 で、リポジトリ外の試作の要素の描き方と同じ値から、同じ画素になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys
import time

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import stage_render
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stage_render
    from vrm_apply_motion import use_scene_of

PASSES = ("room", "char", "shade", "disc", "head")
MIRRORED = ("char", "shade")                      # 床で反転したカメラでも描ける要素（r<要素>）
ALPHA = ("char", "ph")                            # 透けの RGBA の PNG で書く要素（反転したものも）
EPS = 1e-6                                        # 時刻の境目（plan.stage と同じく、この秒だけ前のコマから届いたとみなす）
DEFAULTS = {
    "resolution": [1080, 1920], "passes": ["char", "shade", "disc"], "format": "png", "quality": 90, "antialiasing": "5",
    "camera": {"sensor_height": 24.0, "lens": 35.0, "clip": [0.1, 1000.0]},
    "dancer": {"bones": ["mixamorig:Hips"], "sigma": 6.0},
    "disc": {"radius": 0.42, "vertices": 48, "height": 0.004},
    "head": [], "room": {"collection": None, "world": [0.0, 0.0, 0.0]},
    "shade": {"studio_light": "Default", "rotate_z": 0.0},
    "armature": None, "fps": None, "only": None, "mirror": {"passes": [], "height": 0.0},
    "phantoms": {"items": [], "during": None, "mirror": False, "point": "hips", "sigma_s": 0.292, "reach": 3.0, "frame0": 1.0,
                 "fps": None},
}
SECTIONS = ("camera", "dancer", "disc", "room", "shade", "mirror", "phantoms")
DRAWN = {"MESH", "CURVE", "CURVES", "SURFACE", "META", "FONT", "POINTCLOUD", "VOLUME", "GREASEPENCIL"}   # 描画に出る物体の種類
DISC_NAME = "model3d_passes_disc"


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="pass_render.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--track", required=True)
    ap.add_argument("--passes-dir", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--cameras-out", required=True)
    ap.add_argument("--cameras")
    return ap.parse_args(argv)


def merged(settings):
    """既定の値に settings を重ねる（camera・dancer・disc・room・shade は中の鍵ごと）。"""
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k not in SECTIONS}}
    for key in SECTIONS:
        out[key] = {**DEFAULTS[key], **settings.get(key, {})}
    return out


def phantom_index(kind):
    """ph<i>・rph<i> の i（分身でなければ None）。"""
    name = kind[1:] if kind.startswith("rph") else kind
    return int(name[2:]) if name.startswith("ph") and name[2:].isdigit() else None


def base_kind(kind):
    """r<要素>（反転したカメラで描いたもの）と分身 ph<i>・rph<i> の描き方の名前（元の要素・ph）。そのほかはそのまま。"""
    if phantom_index(kind) is not None:
        return "ph"
    return kind[1:] if kind.startswith("r") and kind[1:] in MIRRORED else kind


def is_mirrored(kind):
    """反転したカメラで描く元の要素の写し（rchar・rshade）か（分身の rph<i> は分身の描き方で描くので含めない）。"""
    return kind.startswith("r") and kind[1:] in MIRRORED


def phantom_kinds(settings):
    """分身の要素の名前（ph<i>、mirror なら続けて rph<i>）。"""
    count = len(settings["phantoms"]["items"])
    names = [f"ph{i}" for i in range(count)]
    return names + ([f"rph{i}" for i in range(count)] if settings["phantoms"]["mirror"] else [])


def within(t, during):
    """時刻 t が分身を描く時間（during の [頭, 終わり) のどれか。None ならいつも）に入るか。境目は EPS 秒前のコマから。"""
    return during is None or any(start - EPS <= t < end - EPS for start, end in during)


def phantom_distances(points, name, dance_t, items, camera_at):
    """踊り手と分身の、カメラからの距離（m）。points は point_track.Samples。分身の点 = 遅らせた時刻の点 + offset。
    戻り: (踊り手の距離, [{i, distance_m, near}])（near は分身のほうがカメラに近いこと）。"""
    import numpy as np
    camera_at = np.asarray(camera_at, float)
    main = float(np.linalg.norm(points.at(name, dance_t) - camera_at))
    out = []
    for i, item in enumerate(items):
        offset = np.array(list(item["offset"]) + [0.0] * (3 - len(item["offset"])), float)
        distance = float(np.linalg.norm(points.at(name, dance_t - item["delay"]) + offset - camera_at))
        out.append({"i": i, "distance_m": distance, "near": distance < main})
    return main, out


def extension(kind, fmt):
    return "png" if base_kind(kind) in ALPHA or fmt == "png" else "jpg"


def all_kinds(settings):
    """描く要素の名前の並び（passes の後に、反転したカメラで描く r<要素>、分身の ph<i>・rph<i>）。"""
    return list(settings["passes"]) + ["r" + k for k in settings["mirror"]["passes"]] + phantom_kinds(settings)


def mirror_values(height):
    """床の面（高さ height。Blender 単位）で反転する 4×4 の行列の値（z → 2 height − z）。"""
    return [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, -1.0, 2.0 * height], [0.0, 0.0, 0.0, 1.0]]


def pass_file(kind, T, fmt):
    """要素 kind のコマ T のファイルの、要素のフォルダからの相対パス。"""
    return f"{kind}/f{int(T):04d}.{extension(kind, fmt)}"


def lens_from_focal(focal_px, height_px, sensor_height):
    """焦点距離（画面の高さ height_px の画素）→ レンズ（mm）。受光は縦に合わせる。"""
    return focal_px / height_px * sensor_height


def focal_from_lens(lens, sensor_height, height_px):
    return lens / sensor_height * height_px


def shift_from_principal(principal_px, width_px, height_px):
    """主点（画素。左上が 0）→ Blender のレンズのずらし（画面の高さに対する割合。受光は縦に合わせる）。"""
    return ((width_px / 2 - principal_px[0]) / height_px, (principal_px[1] - height_px / 2) / height_px)


def principal_from_shift(shift, width_px, height_px):
    return [width_px / 2 - shift[0] * height_px, height_px / 2 + shift[1] * height_px]


def record_cameras(record):
    """入力 cameras（model3d-cameras/0.1）→ {T: (行列, 焦点距離, 主点, 踊り手の点 or None)}。焦点距離と主点は記録の resolution のまま。"""
    if record is None:
        return {}
    return {int(fr["T"]): (fr["matrix"], float(fr["focal_px"]), [float(v) for v in fr["principal_px"]], fr.get("dancer"))
            for fr in record["frames"]}


def cameras_problem(plan, by_record):
    """カメラの無いコマ（入力 cameras にも、トラックのコマにも無い）があれば、その説明。"""
    missing = [T for T, _, spec in plan if T not in by_record and "camera" not in spec]
    if missing:
        head = ", ".join(str(T) for T in missing[:10]) + (" …" if len(missing) > 10 else "")
        return f"カメラの無いコマがあります（T {head}。トラックの camera か、入力 cameras で渡してください）"
    return None


def dancer_path(points_by_f, frames, sigma):
    """動きのコマ（重ねず小さい順）ごとの踊り手の点を、σ のガウスでならした {f: 点}（render.stage の腰のならしと同じ）。"""
    frames = sorted(set(frames))
    points = [points_by_f[f] for f in frames]
    return {f: p for f, p in zip(frames, stage_render.gauss_smooth(points, sigma))}


def camera_entry(T, f, matrix, lens, shift, sensor_height, resolution, unit, dancer):
    """出力 cameras の 1 コマ（位置は m）。matrix は Blender の matrix_world（Blender 単位）。dancer は m。"""
    M = [list(map(float, row)) for row in matrix]
    for row in M[:3]:
        row[3] *= unit
    width, height = resolution
    entry = {"T": int(T), "f": stage_render.motion_frame(f), "matrix": M, "focal_px": focal_from_lens(lens, sensor_height, height),
             "principal_px": principal_from_shift(shift, width, height), "lens_mm": float(lens), "shift": [float(v) for v in shift]}
    if dancer is not None:
        entry["dancer"] = [float(v) for v in dancer]
        entry["distance_m"] = float(math.dist(entry["dancer"], [M[0][3], M[1][3], M[2][3]]))
    return entry


def set_pass(bpy, kind, handles, settings):
    """要素ごとに、何を描くかと描き方を切り替える（どの要素も毎回すべてを入れ直す。前の要素の値を引き継がない）。
    r<要素> は元の要素と同じ描き方（カメラだけが反転している）。"""
    kind = base_kind(kind)
    sc = bpy.context.scene
    shading = sc.display.shading
    show_character = kind in ("char", "shade", "head", "ph")
    for o in handles["character"]:
        o.hide_render = not show_character
    for o in handles["room"]:
        o.hide_render = kind != "room"
    if handles["disc"] is not None:
        handles["disc"].hide_render = kind != "disc"
    lit = kind in ("shade", "ph")                       # 分身は、陰影と同じ白い人物とスタジオの光（背景は透け）
    shading.light = "STUDIO" if lit else "FLAT"
    shading.color_type = "OBJECT" if kind == "head" else ("SINGLE" if kind in ("shade", "disc", "ph") else "TEXTURE")
    shading.single_color = (1.0, 1.0, 1.0)
    shading.show_specular_highlight = not lit
    shading.use_world_space_lighting = lit
    if lit:
        shading.studio_light = settings["shade"]["studio_light"]
        shading.studiolight_rotate_z = math.radians(settings["shade"]["rotate_z"])
    if kind == "head":                                   # 顔の物体だけ白、ほかの人物の物体は黒
        heads = set(settings["head"])
        for o in handles["character"]:
            o.color = (1.0, 1.0, 1.0, 1.0) if o.name in heads else (0.0, 0.0, 0.0, 1.0)
    sc.render.film_transparent = kind in ALPHA
    handles["world"].color = tuple(settings["room"]["world"]) if kind == "room" else (0.0, 0.0, 0.0)
    image = sc.render.image_settings
    if kind in ALPHA or settings["format"] == "png":
        image.file_format, image.color_mode, image.color_depth, image.compression = "PNG", "RGBA" if kind in ALPHA else "RGB", "8", 15
    else:
        image.file_format, image.color_mode, image.quality = "JPEG", "RGB", settings["quality"]


def apply_render(bpy, settings):
    """Workbench の描き方（どの要素にも共通）。"""
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_WORKBENCH"
    sc.render.resolution_x, sc.render.resolution_y = settings["resolution"]
    sc.render.resolution_percentage = 100
    sc.display.render_aa = settings["antialiasing"]
    sc.view_settings.view_transform, sc.view_settings.look = "Standard", "None"
    sc.view_settings.exposure, sc.view_settings.gamma, sc.view_settings.use_curve_mapping = 0.0, 1.0, False
    shading = sc.display.shading
    shading.show_backface_culling = False
    shading.show_shadows = False
    shading.show_cavity = False
    shading.show_object_outline = False
    shading.show_xray = False
    for name in dir(sc.render):                         # 画像に日時・ファイル名などの控えを書かない（焼き込みもしない）
        if name.startswith("use_stamp") and isinstance(getattr(sc.render, name), bool):
            setattr(sc.render, name, False)
    world = bpy.data.worlds.new("model3d_passes_world")    # .blend の空は変えない（Workbench の背景はこの色）
    sc.world = world
    return world


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    with open(a.track, encoding="utf-8") as stream:
        track = json.load(stream)
    record = None
    if a.cameras:
        with open(a.cameras, encoding="utf-8") as stream:
            record = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    if settings["armature"]:
        arm = bpy.data.objects.get(settings["armature"])
        if arm is None or arm.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    else:
        armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not armatures:
            raise SystemExit("アーマチュアがありません")
        arm = armatures[0]
    sc = use_scene_of(bpy, arm)
    missing = [b for b in settings["dancer"]["bones"] if b not in arm.pose.bones]
    if missing:
        raise SystemExit(f"{arm.name} に無い骨（踊り手の点）: {missing}（bone_prefix と dancer.bones を確かめてください）")
    plan = stage_render.frame_plan(track, sc.frame_start, sc.frame_end)
    by_record = record_cameras(record)
    problem = cameras_problem(plan, by_record)
    if problem:
        raise SystemExit(problem)
    if bpy.context.object is not None and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    unit = float(sc.unit_settings.scale_length)          # 1 Blender 単位が何 m か（トラック・記録・設定の長さは m）
    kinds = all_kinds(settings)
    mirrored = {k for k in kinds if is_mirrored(k)}
    flip = Matrix(mirror_values(settings["mirror"]["height"] / unit))

    room = set()
    if settings["room"]["collection"]:
        coll = bpy.data.collections.get(settings["room"]["collection"])
        if coll is None:
            raise SystemExit(f"部屋のコレクションがありません: {settings['room']['collection']}")
        room = {o for o in coll.all_objects if o.type in DRAWN and o.name in sc.objects and not o.hide_render}
    character = [o for o in sc.objects if o.type in DRAWN and not o.hide_render and o not in room]
    heads = set(settings["head"])
    absent = sorted(heads - {o.name for o in character})
    if "head" in kinds and absent:
        raise SystemExit(f"顔の物体が、描画に出る人物の物体にありません: {absent}")
    disc = None
    if "disc" in kinds:
        bpy.ops.mesh.primitive_circle_add(vertices=int(settings["disc"]["vertices"]), radius=settings["disc"]["radius"] / unit,
                                          fill_type="NGON", location=(0.0, 0.0, settings["disc"]["height"] / unit))
        disc = bpy.context.object
        disc.name = DISC_NAME
        disc.hide_render = True
        for coll in list(disc.users_collection):         # 今のコレクションが描画から外されていても描けるよう、シーンの直下に置く
            coll.objects.unlink(disc)
        sc.collection.objects.link(disc)
    world = apply_render(bpy, settings)
    handles = {"character": character, "room": sorted(room, key=lambda o: o.name), "disc": disc, "world": world}

    cam_s = settings["camera"]
    cam_d = bpy.data.cameras.new("model3d_passes_camera")
    cam_d.sensor_fit, cam_d.sensor_height = "VERTICAL", cam_s["sensor_height"]
    cam_d.clip_start, cam_d.clip_end = cam_s["clip"][0] / unit, cam_s["clip"][1] / unit
    cam = bpy.data.objects.new("model3d_passes_camera", cam_d)
    sc.collection.objects.link(cam)
    sc.camera = cam

    # 踊り手の点: 骨の付け根の平均を、動きのコマの並びでならす（骨だけを動かして測る。部品の変形は止めて速くする。後で戻す）
    frames = sorted({f for T, f, _ in plan if by_record.get(T, (None,) * 4)[3] is None})   # 記録に点の無いコマだけ測る
    mods = [(m, m.show_viewport) for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers]
    for m, _ in mods:
        m.show_viewport = False
    raw = {}
    try:
        for f in frames:
            stage_render.set_frame(sc, f)
            heads_at = [arm.matrix_world @ arm.pose.bones[b].head for b in settings["dancer"]["bones"]]
            raw[f] = np.array(sum(heads_at, Vector()) / len(heads_at)) * unit
    finally:
        for m, shown in mods:
            m.show_viewport = shown
    path = dancer_path(raw, frames, settings["dancer"]["sigma"]) if frames else {}
    record_res = record["resolution"] if record is not None else None

    def place(T, f, spec):
        """コマ T のカメラを置く。戻り: (レンズ, ずらし, 踊り手の点 m, 入れた行列（記録のカメラのとき。トラックのカメラは None）)。"""
        assigned = None
        if T in by_record:
            matrix, focal, principal, dancer = by_record[T]
            cam.rotation_mode = "XYZ"
            M = Matrix(matrix)
            M.translation = M.translation / unit
            cam.matrix_world = M
            assigned = M
            width, height = record_res
            cam_d.lens = lens_from_focal(focal, height, cam_d.sensor_height)
            cam_d.shift_x, cam_d.shift_y = shift_from_principal(principal, width, height)
            point = path[f] if dancer is None else np.array(dancer, float)
        else:
            camera = spec["camera"]
            cam.rotation_mode = "QUATERNION"
            loc, target = Vector(camera["location"]) / unit, Vector(camera["target"]) / unit
            cam.scale = (1.0, 1.0, 1.0)                  # 前のコマの反転したカメラの大きさ（−1）を残さない
            cam.location = loc
            cam.rotation_quaternion = (target - loc).to_track_quat("-Z", "Y")
            cam_d.lens = camera.get("lens", cam_s["lens"])
            cam_d.shift_x, cam_d.shift_y = 0.0, 0.0
            point = path[f]
        if disc is not None:
            disc.location = (point[0] / unit, point[1] / unit, settings["disc"]["height"] / unit)
        return cam_d.lens, (cam_d.shift_x, cam_d.shift_y), point, assigned

    only = set(settings["only"]) if settings["only"] else None
    for kind in kinds:
        os.makedirs(os.path.join(a.passes_dir, kind), exist_ok=True)
    rendered, existing = {k: [] for k in kinds}, {k: [] for k in kinds}
    fps = settings["fps"] or track.get("fps") or sc.render.fps / sc.render.fps_base
    ghosts = settings["phantoms"]
    ghost_kinds = set(phantom_kinds(settings))
    dance_fps = ghosts["fps"] or sc.render.fps / sc.render.fps_base     # 分身の遅れ（秒）を動きのコマに直す fps
    began = time.time()
    for T, f, spec in plan:
        if only is not None and T not in only:
            continue
        files = {k: os.path.join(a.passes_dir, pass_file(k, T, settings["format"])) for k in kinds}
        active = [k for k in kinds if k not in ghost_kinds or within((T - 1) / fps, ghosts["during"])]
        todo = [k for k in active if not os.path.exists(files[k])]
        for k in active:
            if k not in todo:
                existing[k].append(T)
        if not todo:
            continue
        t0 = time.time()
        stage_render.set_frame(sc, f)
        assigned = place(T, f, spec)[3]
        bpy.context.view_layer.update()
        main = assigned if assigned is not None else cam.matrix_world.copy()   # 反転と分身は、このコマの入れた行列から
        for k in todo:
            if k in mirrored or k in ghost_kinds:
                continue
            set_pass(bpy, k, handles, settings)
            sc.render.filepath = files[k]
            bpy.ops.render.render(write_still=True)
            rendered[k].append(T)
        flipped = [k for k in todo if k in mirrored]
        if flipped:                                      # 床で反転したカメラ（入れた行列に左から鏡の行列。試作と同じ）
            cam.matrix_world = flip @ main
            bpy.context.view_layer.update()
            for k in flipped:
                set_pass(bpy, k, handles, settings)
                sc.render.filepath = files[k]
                bpy.ops.render.render(write_still=True)
                rendered[k].append(T)
        for i, item in enumerate(ghosts["items"]):       # 分身: 踊りの時刻を遅らせ、骨組みをずらして描く（反転したカメラでも）
            names = [k for k in (f"ph{i}", f"rph{i}") if k in todo]
            if not names:
                continue
            stage_render.set_frame(sc, stage_render.motion_frame(f - item["delay"] * dance_fps))
            base = arm.location.copy()
            offset = list(item["offset"]) + [0.0] * (3 - len(item["offset"]))
            arm.location = base + Vector(offset) / unit
            for k in names:
                cam.matrix_world = main if k.startswith("ph") else flip @ main
                bpy.context.view_layer.update()
                set_pass(bpy, k, handles, settings)
                sc.render.filepath = files[k]
                bpy.ops.render.render(write_still=True)
                rendered[k].append(T)
            arm.location = base
        print(f"T {T} f {f} {len(todo)} passes {time.time() - t0:.2f}s", flush=True)
    seconds = time.time() - began

    # カメラの控え: 並びの全部の T（描いたコマに関係なく）。Blender が持ち直した行列を読む
    entries = []
    for T, f, spec in plan:
        lens, shift, point, _ = place(T, f, spec)
        bpy.context.view_layer.update()
        entries.append(camera_entry(T, f, cam.matrix_world, lens, shift, cam_d.sensor_height, settings["resolution"], unit, point))
    cameras = {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": settings["resolution"], "fps": fps,
               "sensor_height_mm": cam_d.sensor_height, "clip_m": [cam_d.clip_start * unit, cam_d.clip_end * unit], "frames": entries}
    with open(a.cameras_out, "w", encoding="utf-8") as stream:
        json.dump(cameras, stream)
    count = sum(len(v) for v in rendered.values())
    report = {"schema_version": "model3d-pass-render/0.1", "blender": bpy.app.version_string,
              "planned": [{"T": T, "f": f} for T, f, _ in plan], "rendered": rendered, "skipped_existing": existing,
              "fps": fps, "resolution": settings["resolution"], "scale_length": unit, "engine": sc.render.engine,
              "color": {"view_transform": sc.view_settings.view_transform, "look": sc.view_settings.look,
                        "display": sc.display_settings.display_device},
              "objects": {"character": sorted(o.name for o in character), "room": [o.name for o in handles["room"]],
                          "head": sorted(heads)},
              "armature": arm.name, "dancer_bones": settings["dancer"]["bones"],
              "camera": {"from_record": sum(1 for T, _, _ in plan if T in by_record),
                         "from_track": sum(1 for T, _, _ in plan if T not in by_record),
                         "unused_record": sorted(set(by_record) - {T for T, _, _ in plan})},
              "seconds": round(seconds, 1), "seconds_per_pass": round(seconds / count, 3) if count else None, "settings": settings,
              "phantoms": {"count": len(ghosts["items"]), "dance_fps": dance_fps}}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"planned": len(plan), "rendered": count, "skipped_existing": sum(len(v) for v in existing.values())}))


if __name__ == "__main__":
    main()
