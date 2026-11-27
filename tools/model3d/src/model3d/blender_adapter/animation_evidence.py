"""Blender の中で動かす: 動きの検品（check.animation）の出来事の証拠の絵を描く（簡易描画。.blend は保存しない）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python animation_evidence.py -- \
        --blend dance.blend --settings settings.json --report images.json

settings.json（check.animation が書く）: armature、frames（[最初, 最後]。この範囲の外のコマは描かない）、hips（腰の骨の名前）、
directory（絵を書くフォルダ）、resolution・quality、events（描く出来事。並びの順に描く: id・kind・category・signal・peak_frame・
location・normal）。
- 出来事ごとに、いちばん大きいコマ（動的な出来事は前後のコマも）を、場所を狙うカメラから描く。カメラは、場所の外向き（normal。無ければ
  腰から場所へ）と体の前と少し上から、静的 0.95 m・動的 1.3 m 離れた所（床すれすれより下からは撮らない）。貫通（床を除く）は、場所に
  赤い玉の印を置く。床（z = 0）の板を置く（足が床より下なのが見える）。リポジトリ外の試作と同じ置き方と描き方（Workbench・平らな光・
  絵の色・輪郭・480 画素四方・JPEG）。
- .blend の描く設定のうち、絵をすり替えうるもの（シーケンサー・コンポジター・切り取り・縦横比・多視点・透ける背景・ぶれ・色の補正・
  表示の装置・出力の形式）は、その場で決まった値にする（render.face_sheet と同じ一覧）。画像に日時などの控えを書かない。
報告（images.json）: 出来事の id ごとの絵のファイルの名前の並び。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .face_sheet import DISPLAY_FIXED, RENDER_FIXED, VIEW_FIXED, setting_changes
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from face_sheet import DISPLAY_FIXED, RENDER_FIXED, VIEW_FIXED, setting_changes
    from vrm_apply_motion import use_scene_of

IMAGE_FIXED = {"file_format": "JPEG", "color_mode": "RGB", "color_management": "FOLLOW_SCENE"}   # 品質は settings の quality
CAMERA_DISTANCE = {"静的": 0.95, "動的": 1.3}
CAMERA_LOWEST_M = 0.35                     # 床すれすれより下からは撮らない
MARK_RADIUS_M = 0.028


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="animation_evidence.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def shots(event, first, last):
    """描くコマ: いちばん大きいコマ。動的な出来事は前後のコマも。範囲の外のコマは描かない。"""
    peak = int(event["peak_frame"])
    frames = [peak - 1, peak, peak + 1] if event["kind"] == "動的" else [peak]
    return [f for f in frames if first <= f <= last]


def image_name(event_id, frame):
    return "e%03d_f%04d.jpg" % (int(event_id), int(frame))


def camera_place(location, normal, hips_position, front, kind, vector):
    """カメラの位置（試作と同じ置き方。vector は 3 次元の値を作る関数: Blender では mathutils.Vector）。"""
    loc = vector(location)
    outward = vector((loc.x - hips_position.x, loc.y - hips_position.y, 0))
    if normal is not None:
        outward = vector(normal)
    toward = (outward.normalized() * 0.9 if outward.length > 1e-3 else vector((0, 0, 0))) + front * 0.5 + vector((0, 0, 0.25))
    place = loc + toward.normalized() * CAMERA_DISTANCE[kind]
    if place.z < CAMERA_LOWEST_M:
        place.z = CAMERA_LOWEST_M
    return place


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armature = bpy.data.objects.get(settings["armature"])
    if armature is None or armature.type != "ARMATURE":
        raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    scene = use_scene_of(bpy, armature)
    first, last = settings["frames"]
    directory = os.path.abspath(settings["directory"])
    os.makedirs(directory, exist_ok=True)
    render = scene.render
    render.engine = "BLENDER_WORKBENCH"
    render.resolution_x = render.resolution_y = int(settings["resolution"])
    render.resolution_percentage = 100
    groups = (("display", scene.display_settings, DISPLAY_FIXED), ("render", render, RENDER_FIXED),
              ("image", render.image_settings, IMAGE_FIXED))
    changes = {key: setting_changes({name: getattr(target, name) for name in fixed}, fixed) for key, target, fixed in groups}
    for key, target, _ in groups:
        for name, (_, value) in changes[key].items():
            setattr(target, name, value)
    render.image_settings.quality = int(settings["quality"])
    for name in dir(render):                             # 画像に .blend のパス・日時などの控えを書かない（画素は変わらない）
        if name.startswith("use_stamp") and isinstance(getattr(render, name), bool):
            setattr(render, name, False)
    scene.view_settings.view_transform = "Standard"
    changes["view"] = setting_changes({name: getattr(scene.view_settings, name) for name in VIEW_FIXED}, VIEW_FIXED)
    for name, (_, value) in changes["view"].items():
        setattr(scene.view_settings, name, value)
    for marker in scene.timeline_markers:                # マーカーに結んだカメラは、描くときにカメラを差し替える
        marker.camera = None
    shading = scene.display.shading
    shading.light, shading.color_type, shading.show_shadows, shading.show_cavity, shading.show_object_outline = (
        "FLAT", "TEXTURE", False, False, True)
    world = scene.world or bpy.data.worlds.new("animation_check")
    scene.world = world
    world.color = (0.42, 0.42, 0.44)
    camera_data = bpy.data.cameras.new("animation_check_camera")
    camera_data.lens, camera_data.sensor_width, camera_data.clip_start = 50, 36, 0.05
    camera = bpy.data.objects.new("animation_check_camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    camera.rotation_mode = "QUATERNION"
    bpy.ops.mesh.primitive_plane_add(size=8, location=(0, 0, 0))            # 床（z = 0）。足が床より下にあるのが見えるように
    floor = bpy.context.object
    floor_material = bpy.data.materials.new("animation_check_floor")
    floor_material.diffuse_color = (0.55, 0.50, 0.42, 1.0)
    floor.data.materials.append(floor_material)
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=1.0)
    mark = bpy.context.object
    mark_material = bpy.data.materials.new("animation_check_mark")
    mark_material.diffuse_color = (1.0, 0.05, 0.05, 1.0)
    mark.data.materials.append(mark_material)
    hips = settings["hips"]
    hips_rest = armature.data.bones[hips].matrix_local.to_3x3()
    world_matrix = armature.matrix_world
    images = {}
    for event in settings["events"]:
        peak = int(event["peak_frame"])
        scene.frame_set(peak)
        hips_matrix = world_matrix @ armature.pose.bones[hips].matrix
        front = (hips_matrix.to_3x3() @ hips_rest.inverted()) @ Vector((0, -1, 0))
        location = Vector(event["location"])
        camera.location = camera_place(event["location"], event.get("normal"), hips_matrix.translation, front, event["kind"], Vector)
        camera.rotation_quaternion = (location - camera.location).to_track_quat("-Z", "Y")
        mark.hide_render = event["signal"] != "cloth.hem_tear" and not (
            event["category"] == "貫通" and event["signal"] != "penetration.floor")
        mark.location = location
        mark.scale = (MARK_RADIUS_M, MARK_RADIUS_M, MARK_RADIUS_M)
        names = []
        for frame in shots(event, first, last):
            scene.frame_set(frame)
            name = image_name(event["id"], frame)
            render.filepath = os.path.join(directory, name)
            bpy.ops.render.render(write_still=True)
            names.append(name)
        images[str(event["id"])] = names
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump({"images": images, "render_changes": changes}, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"events": len(images), "images": sum(len(v) for v in images.values())}))


if __name__ == "__main__":
    main()
