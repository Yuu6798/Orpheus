"""Blender の中で動かす: 部位を色分けして描く診断の絵（render.part_colors）。何が何を通り抜けているかを目で見分けるためのもの。

    blender --background --factory-startup --python part_colors.py -- --blend dance.blend --settings settings.json \
        --frames-dir frames --report report.json

- bone_meshes のメッシュ（体など）は、頂点ごとに、いちばん重みの大きい頂点グループの名前で色を分ける（bone_colors の
  [名前の頭, 色] を上から見て、最初に名前の頭が合ったもの。どれにも合わなければ bone_default）。
- ほかのメッシュは物体ごとに 1 色（object_colors。無ければ default_color）。色はシーンの線形の値。
- show に挙げた物体は、描画から隠してあっても描く（布の身代わりや当たり判定の写しなど）。hide に挙げた物体は描かない。
  ほかの、描画から隠してある物体は描かない。
- 描き方は簡易描画（Workbench）の頂点の色・スタジオの光・くぼみの強調（cavity）。裏の面も描く。色の変換は Standard。
  画像に日時などの控えを書かない。
- カメラは、狙う点 target（[x, y] m）の真上の高さ center から水平に向き、方位 yaw（度。0 で -Y 側の正面、90 で +X 側）・
  レンズ lens（mm、受光は縦に 36 mm）で、縦 height（m）が画面の高さに入る距離に置く（横長の画面でも縦で合わせる）。
- コマ（frames。小数は前後のコマの間の時刻）ごとに <frames-dir>/f<T>.<jpg|png>（T は並びの 1 から）。

.blend は保存しない（色の属性・カメラ・背景は、描くための読み込みの中だけ。ほかの物体と分け合うメッシュは、色を付ける前に写す）。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ絵になる（違いは簡易描画の描くたびの揺れの
大きさの中）ことを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys
import time

try:   # パッケージとして読むとき（テスト）
    from .pose_sample import apply_pose_sample, prepare_pose_samples
    from .stage_render import motion_frame, set_frame
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from pose_sample import apply_pose_sample, prepare_pose_samples
    from stage_render import motion_frame, set_frame
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"frames": [], "bone_meshes": [], "bone_colors": [], "bone_default": [0.6, 0.6, 0.6], "object_colors": {},
            "default_color": [0.95, 0.95, 0.95], "show": [], "hide": [], "resolution": [720, 960], "format": "jpeg", "quality": 90,
            "antialiasing": "8", "cavity": True, "world": [0.2, 0.2, 0.22],
            "camera": {"yaw": 0.0, "height": 1.5, "center": 0.75, "lens": 85.0, "target": [0.0, 0.0]}, "armature": None}
ATTRIBUTE = "model3d_part_colors"
SENSOR = 36.0     # 受光の縦の大きさ（mm）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="part_colors.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    out = {**DEFAULTS, **{k: v for k, v in settings.items() if k != "camera"}}
    out["camera"] = {**DEFAULTS["camera"], **settings.get("camera", {})}
    return out


def group_color(name, bone_colors, default):
    """頂点グループの名前 name の色: bone_colors の [名前の頭, 色] を上から見て、最初に頭が合ったもの（無ければ default）。"""
    for prefix, color in bone_colors:
        if name.startswith(prefix):
            return color
    return default


def camera_place(camera, unit=1.0):
    """カメラの位置（Blender 単位）・向き（オイラー角、ラジアン）・狙う点までの水平の距離（m）。リポジトリの外の台本と同じ式。"""
    fov = 2 * math.atan(SENSOR / 2 / camera["lens"])            # 縦の画角（受光は縦に合わせる）
    distance = (camera["height"] / 2) / math.tan(fov / 2)
    yaw = math.radians(camera["yaw"])
    x, y = camera["target"]
    location = ((x + math.sin(yaw) * distance) / unit, (y - math.cos(yaw) * distance) / unit, camera["center"] / unit)
    return location, (math.radians(90), 0.0, yaw), distance


def frame_file(T, fmt):
    return f"f{int(T):04d}.{'png' if fmt == 'png' else 'jpg'}"


def color_meshes(bpy, scene, settings):
    """描く物体を決めて、頂点の色の属性を付ける。戻り: {名前: 色の決め方}・描く物体・隠した物体。"""
    import numpy as np
    shown, hidden = set(settings["show"]), set(settings["hide"])
    bone_meshes = set(settings["bone_meshes"])
    applied, drawn, skipped = {}, [], []
    for obj in scene.objects:
        if obj.type != "MESH":
            continue
        if obj.name in shown:
            obj.hide_render = False
            obj.display_type = "TEXTURED"
        elif obj.hide_render:
            skipped.append(obj.name)            # 当たり判定の写しなど、描画から隠してある物体はそのまま（描かない）
            continue
        if obj.name in hidden:
            obj.hide_render = True
            continue
        if obj.data.users > 1:                  # ほかの物体と分け合うメッシュは写す（物体ごとの色が上書きし合わないように）
            obj.data = obj.data.copy()
        mesh = obj.data
        attribute = mesh.color_attributes.get(ATTRIBUTE) or mesh.color_attributes.new(ATTRIBUTE, "FLOAT_COLOR", "POINT")
        colors = np.empty((len(mesh.vertices), 4), dtype=np.float32)
        if obj.name in bone_meshes:
            names = {group.index: group.name for group in obj.vertex_groups}
            counts = {}
            for vertex in mesh.vertices:
                color = settings["bone_default"]
                if vertex.groups:
                    color = group_color(names[max(vertex.groups, key=lambda item: item.weight).group], settings["bone_colors"],
                                        settings["bone_default"])
                colors[vertex.index] = (*color, 1.0)
                counts[tuple(color)] = counts.get(tuple(color), 0) + 1
            applied[obj.name] = {"by": "bone", "vertices_by_color": [[list(k), v] for k, v in counts.items()]}
        else:
            color = settings["object_colors"].get(obj.name, settings["default_color"])
            colors[:] = (*color, 1.0)
            applied[obj.name] = {"by": "object", "color": list(color)}
        attribute.data.foreach_set("color", colors.reshape(-1))
        mesh.color_attributes.active_color = attribute
        mesh.color_attributes.render_color_index = list(mesh.color_attributes).index(attribute)
        drawn.append(obj.name)
    return applied, drawn, skipped


def apply_render(bpy, scene, settings):
    """簡易描画の描き方（リポジトリの外の台本と同じ値。台本がファイルの値のまま使った所も、Blender の既定の値で入れ直す）。"""
    width, height = settings["resolution"]
    render = scene.render
    render.resolution_x, render.resolution_y, render.resolution_percentage = width, height, 100
    render.engine = "BLENDER_WORKBENCH"
    render.film_transparent = False
    render.dither_intensity = 1.0
    image = render.image_settings
    if settings["format"] == "png":
        image.file_format, image.color_mode, image.color_depth, image.compression = "PNG", "RGB", "8", 15
    else:
        image.file_format, image.color_mode, image.quality = "JPEG", "RGB", settings["quality"]
    for name in dir(render):                             # 画像に日時・ファイル名などの控えを書かない
        if name.startswith("use_stamp") and isinstance(getattr(render, name), bool):
            setattr(render, name, False)
    scene.display.render_aa = settings["antialiasing"]
    shading = scene.display.shading
    shading.light, shading.studio_light, shading.studiolight_rotate_z = "STUDIO", "Default", 0.0
    shading.color_type = "VERTEX"
    shading.show_backface_culling = False
    shading.show_cavity = settings["cavity"]
    shading.cavity_type = "SCREEN"
    shading.cavity_ridge_factor = shading.cavity_valley_factor = 1.0
    shading.show_shadows = shading.show_object_outline = shading.show_xray = False
    shading.show_specular_highlight, shading.use_world_space_lighting = True, False
    scene.view_settings.view_transform, scene.view_settings.look = "Standard", "None"
    scene.view_settings.exposure, scene.view_settings.gamma, scene.view_settings.use_curve_mapping = 0.0, 1.0, False
    world = bpy.data.worlds.new("model3d_part_colors_world")
    world.color = tuple(settings["world"])
    scene.world = world


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects
                 if obj.type == "ARMATURE" and (not settings["armature"] or obj.name == settings["armature"])]
    if settings["armature"] and not armatures:
        raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    scene = use_scene_of(bpy, armatures[0]) if armatures else bpy.context.scene
    poses = settings.get("poses")
    if poses:
        if not armatures:
            raise SystemExit("poses を描くためのアーマチュアがありません")
        prepare_pose_samples(armatures[0], (step for pose in poses for step in pose["rotations"]))
        set_frame(scene, scene.frame_start)
    names = {obj.name for obj in scene.objects}
    absent = sorted({*settings["bone_meshes"], *settings["show"], *settings["hide"]} - names)
    if absent:
        raise SystemExit(f"シーンに無い物体の名前があります（bone_meshes・show・hide）: {absent}")
    unused = sorted(set(settings["object_colors"]) - names)      # 色の表は使い回せるように、シーンに無い名前は報告に残すだけ
    applied, drawn, skipped = color_meshes(bpy, scene, settings)
    not_mesh = sorted(set(settings["bone_meshes"]) - set(applied))
    if not_mesh:
        raise SystemExit(f"bone_meshes に、色を付けられないメッシュがあります（隠してあるか、メッシュでない）: {not_mesh}")
    apply_render(bpy, scene, settings)
    unit = float(scene.unit_settings.scale_length)
    location, rotation, distance = camera_place(settings["camera"], unit)
    camera_data = bpy.data.cameras.new("model3d_part_colors_camera")
    camera_data.lens, camera_data.clip_end = settings["camera"]["lens"], 100 / unit
    # 受光は縦に 36 mm（縦の高さ height が、横長の画面でも画面の高さに入る。縦長の画面では台本の既定 AUTO と同じ写り方）
    camera_data.sensor_fit, camera_data.sensor_height, camera_data.sensor_width = "VERTICAL", SENSOR, SENSOR
    camera = bpy.data.objects.new("model3d_part_colors_camera", camera_data)
    scene.collection.objects.link(camera)
    camera.location, camera.rotation_euler = location, rotation
    scene.camera = camera
    os.makedirs(a.frames_dir, exist_ok=True)
    rendered, began = [], time.time()
    for T, f in enumerate(settings["frames"], start=1):
        f = motion_frame(f)
        if poses:
            apply_pose_sample(armatures[0], poses[T - 1]["rotations"], bpy.context.view_layer)
        else:
            set_frame(scene, f)
        scene.render.filepath = os.path.join(os.path.abspath(a.frames_dir), frame_file(T, settings["format"]))
        bpy.ops.render.render(write_still=True)
        rendered.append({"T": T, "f": f, "file": frame_file(T, settings["format"])})
        if poses:
            rendered[-1]["pose"] = poses[T - 1]["name"]
    report = {"schema_version": "model3d-part-colors/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "fps": scene.render.fps / scene.render.fps_base, "resolution": settings["resolution"], "frames": rendered,
              "objects": applied, "drawn": drawn, "skipped_hidden": skipped, "hidden": sorted(settings["hide"]),
              "unused_object_colors": unused,
              "camera": {"location_m": [v * unit for v in location], "rotation_deg": [math.degrees(v) for v in rotation],
                         "lens_mm": settings["camera"]["lens"], "sensor_mm": SENSOR, "sensor_fit": "VERTICAL",
                         "distance_m": distance},
              "seconds": round(time.time() - began, 1), "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"frames": len(rendered), "drawn": len(drawn)}))


if __name__ == "__main__":
    main()
