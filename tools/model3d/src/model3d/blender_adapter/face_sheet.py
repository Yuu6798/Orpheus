"""Blender の中で動かす: 表情（形状キーの組み合わせ）を、いくつかの向きから寄りで描く（render.face_sheet の描く側。保存しない）。

    blender --background --factory-startup --python face_sheet.py -- --blend in.blend --settings settings.json --out-dir views \
        --report report.json

- settings の combos（[[札, {形状キー: 値}], …]）ごとに、object のほかの形状キー（基準のキーのほか）を 0 にしてから値を入れ、views の向きごとに、
  正射影のカメラで <札>_<向き>.png を描く（image_names。名前が重なる組み合わせは止める）。並べるのは Blender の外（render.face_sheet）。
- 向き（views の 1 つ）: name、azimuth（度。0 が正面、正でカメラがキャラの左へ回る）、elevation（度。正で上から）、scale（正射影の幅 m）、
  centre（狙う点 m。省くと object の箱の中心）。カメラは狙う点から 1 m、見える奥行きはカメラの既定の範囲を m とみなす。長さはシーンの
  scale_length（1 Blender 単位が何 m か）で Blender 単位に直す（1 なら値はそのまま）。
- EEVEE（taa の標本 samples）、色の変換は Standard、背景は background の色（世界を足す）。灯りは足さない。hide の物体は描かない。
- 描く大きさは size × int(size × 0.9) 画素。画像に日時・ファイル名などの控え（メタデータ）を書かない（同じ入力なら同じバイト列）。
- 報告の長さ（箱の中心）は m。
リポジトリ外の 3 作目の VRM のダンスの台本と同じ描き方。**検証**: 本物の Blender 5.2.4 で、その台本の絵と比べた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="face_sheet.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def camera_offset(azimuth_deg, elevation_deg):
    """狙う点からカメラへの向き（長さ 1）。キャラは −Y を向き、azimuth 0 で正面（−Y の側）、正でキャラの左（+x）へ、elevation 正で上。"""
    a, e = math.radians(azimuth_deg), math.radians(elevation_deg)
    return (math.sin(a) * math.cos(e), -math.cos(a) * math.cos(e), math.sin(e))


DISTANCE_M = 1.0                                         # 狙う点からカメラまで（m）


def view_in_units(view, unit=1.0):
    """向き（views の 1 つ）の長さを Blender 単位に直す: (狙う点 [x, y, z] か None, 狙う点からカメラまで, 正射影の幅)。設定の長さは m なので、
    シーンの scale_length（1 Blender 単位が何 m か）で割る（1 なら値はそのまま）。狙う点を省いたときの箱の中心は、もとから Blender 単位。"""
    centre = None if view.get("centre") is None else [v / unit for v in view["centre"]]
    return centre, DISTANCE_M / unit, view["scale"] / unit


def image_name(label, view):
    return f"{label}_{view}.png"


def image_names(labels, views):
    """札（labels）× 向き（views）ごとの絵のファイルの名前 {(札, 向き): 名前}。描く側と並べる側が同じ名前で書いて読む。札か向きに _ があると、
    別の組み合わせが同じ名前になることがある（smile_left と front、smile と left_front）。大文字と小文字の違いだけの名前も、Windows では
    同じファイルになる。重なれば ValueError（前の絵が上書きされ、2 つのこまに同じ絵が入るのを、描く前に止める）。"""
    names, seen = {}, {}
    for label in labels:
        for view in views:
            name = image_name(label, view)
            other = seen.setdefault(name.lower(), (label, view))
            if other != (label, view):
                raise ValueError(f"札と向きから作る絵の名前が重なります: {other[0]} と {other[1]}、{label} と {view}（{name}）")
            names[(label, view)] = name
    return names


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    try:
        names = image_names([label for label, _ in settings["combos"]], [view["name"] for view in settings["views"]])
    except ValueError as error:
        raise SystemExit(str(error))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    os.makedirs(a.out_dir, exist_ok=True)
    head = bpy.data.objects.get(settings["object"])
    if head is None or head.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    missing = [name for name in settings["hide"] if name not in bpy.data.objects]
    if missing:
        raise SystemExit(f"hide の物体がありません: {missing}")
    for name in settings["hide"]:
        bpy.data.objects[name].hide_render = True
    keys = head.data.shape_keys.key_blocks if head.data.shape_keys else {}
    absent = sorted({name for _, values in settings["combos"] for name in values if name not in keys})
    if absent:
        raise SystemExit(f"{head.name} に無い形状キー: {absent}")
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = settings["samples"]
    scene.view_settings.view_transform = "Standard"
    size = settings["size"]
    scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = size, int(size * 0.9), 100
    scene.render.image_settings.file_format = "PNG"
    for name in dir(scene.render):                       # 画像に日時・ファイル名などの控えを書かない（render.passes と同じ。画素は変わらない）
        if name.startswith("use_stamp") and isinstance(getattr(scene.render, name), bool):
            setattr(scene.render, name, False)
    world = bpy.data.worlds.new("model3d_face_sheet")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (*settings["background"], 1)
    scene.world = world
    cam_data = bpy.data.cameras.new("model3d_face_sheet")
    cam_data.type = "ORTHO"
    cam = bpy.data.objects.new("model3d_face_sheet", cam_data)
    scene.collection.objects.link(cam)
    scene.camera = cam
    unit = float(scene.unit_settings.scale_length)       # 1 Blender 単位が何 m か（向きの長さは m）
    cam_data.clip_start, cam_data.clip_end = cam_data.clip_start / unit, cam_data.clip_end / unit   # 既定の範囲を m とみなす
    corners = [head.matrix_world @ Vector(corner) for corner in head.bound_box]
    box_centre = sum(corners, Vector()) / 8
    reference = head.data.shape_keys.reference_key.name if head.data.shape_keys else None
    images = []
    for label, values in settings["combos"]:
        for key in keys:
            if key.name != reference:
                key.value = 0.0
        for name, value in values.items():
            keys[name].value = value
        for view in settings["views"]:
            given, distance, ortho_scale = view_in_units(view, unit)
            centre = Vector(given) if given is not None else box_centre
            cam.location = centre + Vector(camera_offset(view["azimuth"], view["elevation"])) * distance
            cam.rotation_euler = (centre - cam.location).to_track_quat("-Z", "Y").to_euler()
            cam_data.ortho_scale = ortho_scale
            name = names[(label, view["name"])]
            scene.render.filepath = os.path.join(os.path.abspath(a.out_dir), name)
            bpy.ops.render.render(write_still=True)
            images.append({"label": label, "view": view["name"], "file": name})
    report = {"schema_version": "model3d-face-sheet/0.1", "blender": bpy.app.version_string, "object": head.name, "images": images,
              "box_centre": [float(v) * unit for v in box_centre], "scale_length": unit, "resolution": [size, int(size * 0.9)],
              "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"rendered": len(images)}))


if __name__ == "__main__":
    main()
