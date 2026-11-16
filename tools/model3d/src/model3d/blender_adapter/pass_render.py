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
ファイルは <passes-dir>/<要素>/f<T>.png（char 以外は format が jpeg なら .jpg）。描いてあるファイルはとばす（続きから描ける）。
画像に日時などの控え（メタデータ）を書かない（同じ入力なら同じバイト列）。

settings.json（render.passes が書く。DEFAULTS に重ねる）:
- resolution、passes、format（char 以外の要素の形式。png か jpeg）・quality（jpeg の品質）、antialiasing（Workbench の標本の数）
- camera: sensor_height（縦の受光の大きさ mm。受光は縦に合わせる）、lens（トラックのカメラにレンズが無いときの mm）、clip（写る範囲 m）
- dancer: bones（踊り手の点にする骨の付け根の平均）、sigma（動きのコマの並びでならすガウスの σ。コマ）
- disc（m）、head（物体の名前の並び）、room（collection・world）、shade（studio_light・rotate_z 度）、armature、only（描く T）

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
DEFAULTS = {
    "resolution": [1080, 1920], "passes": ["char", "shade", "disc"], "format": "png", "quality": 90, "antialiasing": "5",
    "camera": {"sensor_height": 24.0, "lens": 35.0, "clip": [0.1, 1000.0]},
    "dancer": {"bones": ["mixamorig:Hips"], "sigma": 6.0},
    "disc": {"radius": 0.42, "vertices": 48, "height": 0.004},
    "head": [], "room": {"collection": None, "world": [0.0, 0.0, 0.0]},
    "shade": {"studio_light": "Default", "rotate_z": 0.0},
    "armature": None, "fps": None, "only": None,
}
SECTIONS = ("camera", "dancer", "disc", "room", "shade")
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


def extension(kind, fmt):
    return "png" if kind == "char" or fmt == "png" else "jpg"


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
    """要素ごとに、何を描くかと描き方を切り替える（どの要素も毎回すべてを入れ直す。前の要素の値を引き継がない）。"""
    sc = bpy.context.scene
    shading = sc.display.shading
    show_character = kind in ("char", "shade", "head")
    for o in handles["character"]:
        o.hide_render = not show_character
    for o in handles["room"]:
        o.hide_render = kind != "room"
    if handles["disc"] is not None:
        handles["disc"].hide_render = kind != "disc"
    shading.light = "STUDIO" if kind == "shade" else "FLAT"
    shading.color_type = "OBJECT" if kind == "head" else ("SINGLE" if kind in ("shade", "disc") else "TEXTURE")
    shading.single_color = (1.0, 1.0, 1.0)
    shading.show_specular_highlight = kind != "shade"
    shading.use_world_space_lighting = kind == "shade"
    if kind == "shade":
        shading.studio_light = settings["shade"]["studio_light"]
        shading.studiolight_rotate_z = math.radians(settings["shade"]["rotate_z"])
    if kind == "head":                                   # 顔の物体だけ白、ほかの人物の物体は黒
        heads = set(settings["head"])
        for o in handles["character"]:
            o.color = (1.0, 1.0, 1.0, 1.0) if o.name in heads else (0.0, 0.0, 0.0, 1.0)
    sc.render.film_transparent = kind == "char"
    handles["world"].color = tuple(settings["room"]["world"]) if kind == "room" else (0.0, 0.0, 0.0)
    image = sc.render.image_settings
    if kind == "char" or settings["format"] == "png":
        image.file_format, image.color_mode, image.color_depth, image.compression = "PNG", "RGBA" if kind == "char" else "RGB", "8", 15
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
    kinds = list(settings["passes"])

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
        """コマ T のカメラを置く。戻り: (レンズ, ずらし, 踊り手の点 m)。"""
        if T in by_record:
            matrix, focal, principal, dancer = by_record[T]
            cam.rotation_mode = "XYZ"
            M = Matrix(matrix)
            M.translation = M.translation / unit
            cam.matrix_world = M
            width, height = record_res
            cam_d.lens = lens_from_focal(focal, height, cam_d.sensor_height)
            cam_d.shift_x, cam_d.shift_y = shift_from_principal(principal, width, height)
            point = path[f] if dancer is None else np.array(dancer, float)
        else:
            camera = spec["camera"]
            cam.rotation_mode = "QUATERNION"
            loc, target = Vector(camera["location"]) / unit, Vector(camera["target"]) / unit
            cam.location = loc
            cam.rotation_quaternion = (target - loc).to_track_quat("-Z", "Y")
            cam_d.lens = camera.get("lens", cam_s["lens"])
            cam_d.shift_x, cam_d.shift_y = 0.0, 0.0
            point = path[f]
        if disc is not None:
            disc.location = (point[0] / unit, point[1] / unit, settings["disc"]["height"] / unit)
        return cam_d.lens, (cam_d.shift_x, cam_d.shift_y), point

    only = set(settings["only"]) if settings["only"] else None
    for kind in kinds:
        os.makedirs(os.path.join(a.passes_dir, kind), exist_ok=True)
    rendered, existing = {k: [] for k in kinds}, {k: [] for k in kinds}
    began = time.time()
    for T, f, spec in plan:
        if only is not None and T not in only:
            continue
        files = {k: os.path.join(a.passes_dir, pass_file(k, T, settings["format"])) for k in kinds}
        todo = [k for k in kinds if not os.path.exists(files[k])]
        for k in kinds:
            if k not in todo:
                existing[k].append(T)
        if not todo:
            continue
        t0 = time.time()
        stage_render.set_frame(sc, f)
        place(T, f, spec)
        bpy.context.view_layer.update()
        for k in todo:
            set_pass(bpy, k, handles, settings)
            sc.render.filepath = files[k]
            bpy.ops.render.render(write_still=True)
            rendered[k].append(T)
        print(f"T {T} f {f} {len(todo)} passes {time.time() - t0:.2f}s", flush=True)
    seconds = time.time() - began

    # カメラの控え: 並びの全部の T（描いたコマに関係なく）。Blender が持ち直した行列を読む
    entries = []
    for T, f, spec in plan:
        lens, shift, point = place(T, f, spec)
        bpy.context.view_layer.update()
        entries.append(camera_entry(T, f, cam.matrix_world, lens, shift, cam_d.sensor_height, settings["resolution"], unit, point))
    fps = settings["fps"] or track.get("fps") or sc.render.fps / sc.render.fps_base
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
              "seconds": round(seconds, 1), "seconds_per_pass": round(seconds / count, 3) if count else None, "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"planned": len(plan), "rendered": count, "skipped_existing": sum(len(v) for v in existing.values())}))


if __name__ == "__main__":
    main()
