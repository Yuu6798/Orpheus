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
入れる値が効くように、また .blend から引き継ぐと出てくる絵をすり替えうる設定は、この場で（保存しない）face_sheet と同じに直す: 入れる
形状キーのミュートを外し、範囲（slider_min・slider_max）を 0 と入れる値が入るまで広げ、形状キーの動き（アクション・ドライバー）を外し、
1 つだけ見せる設定（ピン）を外す。シーケンサー・コンポジター・切り取り・色の補正などは face_sheet の RENDER_FIXED・VIEW_FIXED・
DISPLAY_FIXED・IMAGE_FIXED の値にし、マーカーに結んだカメラを外す。頭の描かない設定を外す（描かないコレクションの中にしか無ければ止める）。
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
    from .face_sheet import (DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED, hidden_paths, image_names, key_changes,
                             setting_changes)
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import humanoid_map, pick_armature
    from eye_keys import absolute_keys_problem
    from eye_rig import eye_bones
    from face_sheet import (DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED, hidden_paths, image_names, key_changes,
                            setting_changes)
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
    reference = head.data.shape_keys.reference_key.name
    key_names = {key.name for key in keys if key.name != reference}
    combos = [(pose["name"], key_targets(pose.get("keys", {}), key_names)) for pose in settings["poses"]]
    states = {key.name: (key.mute, key.slider_min, key.slider_max) for key in keys if key.name != reference}
    unmute, widen = key_changes(combos, states)                        # 入れる値が効くように（face_sheet と同じ。保存しない）
    for name in unmute:
        keys[name].mute = False
    for name, (low, high) in widen.items():                            # 広げるだけなので、下の端から変えても上の端を越えない
        keys[name].slider_min, keys[name].slider_max = low, high
    prepared = {"unmuted": unmute, "widened": {name: [low, high] for name, (low, high) in widen.items()}, "pin_off": False,
                "animation_cleared": None}
    animation = head.data.shape_keys.animation_data
    if animation is not None:                                          # アクション・ドライバー・NLA は、描くときに入れた値を上書きする
        prepared["animation_cleared"] = {"action": animation.action.name if animation.action else None,
                                         "drivers": len(animation.drivers), "nla_tracks": len(animation.nla_tracks)}
        head.data.shape_keys.animation_data_clear()
    if head.show_only_shape_key:                                       # 形状キーを 1 つだけ見せる設定（ピン）は、値を見ない
        head.show_only_shape_key = False
        prepared["pin_off"] = True
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
    # .blend から引き継ぐと出てくる絵をすり替えうる設定（シーケンサー・コンポジター・切り取り・色の補正・出力の形式）を face_sheet と同じに
    groups = (("display", scene.display_settings, DISPLAY_FIXED), ("render", scene.render, RENDER_FIXED),
              ("image", scene.render.image_settings, IMAGE_FIXED))
    render_changes = {key: setting_changes({name: getattr(target, name) for name in fixed}, fixed) for key, target, fixed in groups}
    for key, target, _ in groups:
        for name, (_, value) in render_changes[key].items():
            setattr(target, name, value)
    scene.view_settings.view_transform = "Standard"                    # 表示の装置を決めてから（選べる変換は装置ごと）
    render_changes["view"] = setting_changes({name: getattr(scene.view_settings, name) for name in VIEW_FIXED}, VIEW_FIXED)
    for name, (_, value) in render_changes["view"].items():
        setattr(scene.view_settings, name, value)
    render_changes["marker_cameras"] = [marker.name for marker in scene.timeline_markers if marker.camera is not None]
    for marker in scene.timeline_markers:                              # マーカーに結んだカメラは、描くときにカメラを差し替える
        marker.camera = None
    prepared["render"] = render_changes
    world = scene.world or bpy.data.worlds.new("preview")
    scene.world = world
    world.color = settings["background"]
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
    prepared["object_shown"] = bool(head.hide_render)                  # 頭の描かない設定を外す（face_sheet と同じ）
    head.hide_render = False
    paths = hidden_paths(bpy.context.view_layer.layer_collection, head.name)
    if not paths:
        raise SystemExit(f"{head.name} が描くシーン（{scene.name}）のコレクションにありません")
    if all(paths):                                                     # どの道にも描かない設定がある
        raise SystemExit(f"{head.name} は描かないコレクションの中にしかありません（{sorted({n for path in paths for n in path})}）。"
                         "コレクションの描かない設定（hide_render）かビューレイヤーの除外を外してください")
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
    for pose, (_, targets) in zip(settings["poses"], combos):
        for block in keys:
            if block.name != reference:
                block.value = 0.0
        absent =[name for name in pose.get("keys", {}) if not any(t == name or t.startswith(name + "_") for t in targets)]
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
              "unknown_keys": unknown, "painted_faces": painted, "prepared": prepared}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"images": len(images), "unknown_keys": unknown}, ensure_ascii=False))


if __name__ == "__main__":
    main()
