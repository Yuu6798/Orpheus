"""Blender の中で動かす: 目を動かせる作りにした頭を、姿勢（視線・形状キーの値）× 見る向きの組み合わせで描く（render.eye_sheet）。

    blender --background --factory-startup --python eye_sheet.py -- --blend in.blend --settings settings.json --out-dir 絵のフォルダ \
        --report report.json

settings.json（render.eye_sheet が書く）:
- radius（目の球の半径 m）、bones（{"R", "L"}。目の骨。省くと VRM の人型の骨の割り当て）、armature
- poses: [{"name", "gaze": [横 m, たて m], "keys": {形状キー: 値}}]。視線は、瞳が目の板の上を滑る量（横は + がキャラの左＝画面の右、
  たては + が上）。目の骨を (たて ÷ 半径, 0, 横 ÷ 半径) ラジアン（XYZ）回す。キーの名前は、名前 + "_L"・"_R" があれば両方に、無ければ
  その名前に入れる。姿勢ごとに、ほかのキーは 0 に戻す
- views: [{"name", "yaw", "pitch", "target", "span", "distance"}]。target を、yaw（+ でキャラの左側へ回りこむ）と pitch（+ で上から）の
  向きから distance 離れて見る。span は画面の横に入る幅（m）
- paint（{"plate": 色, "iris": 色}。あれば、目の板と瞳の面を塗った材質に替える。はみ出しの確かめ。.blend は保存しない）、size、background、
  aa、hide（描かない物体）
描き方（試しと同じ）: Workbench、光は FLAT、色は絵の色そのまま（TEXTURE）、裏も描く、影・くぼみ・輪郭線なし、色の変換は Standard。
アーマチュアは描かない。絵の名前は face_sheet.image_names（札 = 姿勢、向き = view）。.blend は保存しない。
"""
import argparse
import json
import math
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .animation_sample import humanoid_map, pick_armature
    from .eye_keys import absolute_keys_problem
    from .eye_rig import eye_bones
    from .face_sheet import image_names
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import humanoid_map, pick_armature
    from eye_keys import absolute_keys_problem
    from eye_rig import eye_bones
    from face_sheet import image_names
    from vrm_apply_motion import use_scene_of


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="eye_sheet.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def gaze_rotation(gaze, radius):
    """視線（瞳が滑る量 [横, たて] m）から、目の骨の回転（XYZ のオイラー角。ラジアン）。試しと同じく、たては X、横は Z。"""
    return (gaze[1] / radius, 0.0, gaze[0] / radius)


def key_targets(values, names):
    """{名前: 値} を、形状キーごとの値 {形状キー: 値} にする（名前 + _L・_R があれば両方へ、無ければその名前へ。無い名前は外す）。"""
    out = {}
    for name, value in values.items():
        for side in ("L", "R"):
            if f"{name}_{side}" in names:
                out[f"{name}_{side}"] = value
            elif name in names:
                out[name] = value
    return out


def view_direction(yaw_deg, pitch_deg):
    """狙う点からカメラへの向き（長さ 1）。yaw が + でキャラの左側（+x）へ、pitch が + で上から。"""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    return (math.sin(yaw) * math.cos(pitch), -math.cos(yaw) * math.cos(pitch), math.sin(pitch))


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    head = bpy.data.objects.get(settings["object"])
    if head is None or head.type != "MESH" or head.data.shape_keys is None:
        raise SystemExit(f"形状キーのある頭のメッシュがありません: {settings['object']}")
    problem = absolute_keys_problem(head.name, head.data.shape_keys.use_relative)   # 絶対の形状キーは値で姿勢を作れない
    if problem:
        raise SystemExit(problem)
    armature = pick_armature(bpy, settings.get("armature"))
    bones = eye_bones(settings.get("bones"), humanoid_map(armature))
    missing = [name for name in bones.values() if name not in armature.pose.bones]
    if missing:
        raise SystemExit(f"{armature.name} に目の骨がありません: {missing}")
    keys = head.data.shape_keys.key_blocks
    scene = use_scene_of(bpy, armature)                                 # 作業用のシーンを開いたまま保存されたファイルでも、頭のあるシーンで描く
    width, height = settings["size"]
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = width, height, 100
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = False
    shading = scene.display.shading
    shading.light = "FLAT"
    shading.color_type = "TEXTURE"
    shading.show_backface_culling = False
    shading.show_shadows = False
    shading.show_cavity = False
    shading.show_object_outline = False
    scene.display.render_aa = settings["aa"]
    scene.view_settings.view_transform = "Standard"
    world = scene.world or bpy.data.worlds.new("preview")
    scene.world = world
    world.color = settings["background"]
    scene.render.image_settings.file_format = "PNG"
    for flag in [name for name in dir(scene.render) if name.startswith("use_stamp")]:   # 絵にファイルの名前や日時を入れない
        setattr(scene.render, flag, False)
    cam_data = bpy.data.cameras.new("eye_camera")
    cam_data.clip_start, cam_data.clip_end = 0.02, 50
    cam = bpy.data.objects.new("eye_camera", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    for obj in bpy.data.objects:                                       # 骨は描かない
        if obj.type == "ARMATURE" or obj.name in settings.get("hide", []):
            obj.hide_render = True
    painted = {}
    if settings.get("paint"):
        # 目の板と瞳の面を、塗った材質に替える（顔の面からはみ出していれば、開きの外に色が出る）。保存はしない
        mesh = head.data
        plate = {int(i) for side in ("R", "L") for i in settings["plate_vertices"][side]}
        iris = {int(i) for side in ("R", "L") for i in settings["iris_vertices"][side]}
        for name, colour in (("diag_plate", settings["paint"]["plate"]), ("diag_iris", settings["paint"]["iris"])):
            material = bpy.data.materials.new(name)
            material.diffuse_color = (*colour, 1.0)
            mesh.materials.append(material)
        for poly in mesh.polygons:
            first = poly.vertices[0]
            if first in plate:
                poly.material_index = len(mesh.materials) - 2
                painted["plate"] = painted.get("plate", 0) + 1
            elif first in iris:
                poly.material_index = len(mesh.materials) - 1
                painted["iris"] = painted.get("iris", 0) + 1

    def aim(view):
        """試しと同じ置き方（mathutils の 32 ビットで計算する）。"""
        d = Vector(view_direction(view["yaw"], view["pitch"]))
        cam.location = Vector(view["target"]) + d * view["distance"]
        cam.rotation_euler = (Vector(view["target"]) - cam.location).to_track_quat("-Z", "Y").to_euler()
        cam_data.lens = cam_data.sensor_width * view["distance"] / view["span"]

    names = image_names([pose["name"] for pose in settings["poses"]], [view["name"] for view in settings["views"]])
    os.makedirs(a.out_dir, exist_ok=True)
    images, unknown = [], {}
    for pose in settings["poses"]:
        for block in keys[1:]:
            block.value = 0.0
        targets = key_targets(pose.get("keys", {}), set(keys.keys()))
        absent = [name for name in pose.get("keys", {}) if not any(t == name or t.startswith(name + "_") for t in targets)]
        if absent:
            unknown[pose["name"]] = absent
        for name, value in targets.items():
            keys[name].value = value
        rotation = gaze_rotation(pose.get("gaze", [0.0, 0.0]), settings["radius"])
        for side in ("L", "R"):
            bone = armature.pose.bones[bones[side]]
            bone.rotation_mode = "XYZ"
            bone.rotation_euler = rotation
        bpy.context.view_layer.update()
        for view in settings["views"]:
            aim(view)
            name = names[(pose["name"], view["name"])]
            scene.render.filepath = os.path.join(a.out_dir, name)
            bpy.ops.render.render(write_still=True)
            images.append({"pose": pose["name"], "view": view["name"], "file": name})
    report = {"blender": bpy.app.version_string, "object": head.name, "armature": armature.name, "bones": bones, "images": images,
              "unknown_keys": unknown, "painted_faces": painted}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"images": len(images), "unknown_keys": unknown}, ensure_ascii=False))


if __name__ == "__main__":
    main()
