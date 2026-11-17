"""Blender の中で動かす: 表情（形状キーの組み合わせ）を、いくつかの向きから寄りで描く（render.face_sheet の描く側。保存しない）。

    blender --background --factory-startup --python face_sheet.py -- --blend in.blend --settings settings.json --out-dir views \
        --report report.json

- object の形状キーは相対だけ（絶対の形状キーは値で形を選べないので止める。absolute_keys_problem）。combos に基準のキーは入れられない。
- 入れる値が効くように、この場で（保存しない）入れる形状キーのミュートを外し、値（0 にするキーの 0 も）が範囲（slider_min〜slider_max）の外なら
  範囲を広げ（key_changes）、形状キーを 1 つだけ見せる設定（show_only_shape_key）を切り、形状キーの値を動かすアニメーションとドライバーを外す。
  変えたことは報告の key_changes。
- settings の combos（[[札, {形状キー: 値}], …]）ごとに、object のほかの形状キー（基準のキーのほか）を 0 にしてから値を入れ、views の向きごとに、
  正射影のカメラで <札>_<向き>.png を描く（image_names。名前が重なる組み合わせは止める）。並べるのは Blender の外（render.face_sheet）。
- 向き（views の 1 つ）: name、azimuth（度。0 が正面、正でカメラがキャラの左へ回る）、elevation（度。正で上から）、scale（正射影の幅 m）、
  centre（狙う点 m。省くと object の箱の中心）。カメラは狙う点から 1 m、見える奥行きはカメラの既定の範囲を m とみなす。長さはシーンの
  scale_length（1 Blender 単位が何 m か）で Blender 単位に直す（1 なら値はそのまま）。
- EEVEE（taa の標本 samples）、色の変換は Standard、背景は background の色（世界を足す）。灯りは足さない。hide の物体は描かない。
- .blend から引き継ぐと出てくる絵をすり替えうる描く設定は、この場で決まった値にする（RENDER_FIXED・VIEW_FIXED: シーケンサー・コンポジター・
  描く範囲の切り取り・画素の縦横比・多視点・透ける背景・ぶれ・色の補正）。マーカーに結んだカメラも外す。.blend と違っていたものは報告の render_changes。
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
RENDER_FIXED = {                                         # scene.render: .blend のままだと出てくる絵をすり替えうる設定と、描くときの値
    "use_sequencer": False,                              # シーケンサーに素材があると、その出力になる
    "use_compositing": False,                            # コンポジターの結果になる
    "use_border": False,                                 # 描く範囲の切り取り
    "pixel_aspect_x": 1.0, "pixel_aspect_y": 1.0,        # 画素の縦横比（ゆがむ）
    "use_multiview": False,                              # 視点ごとに別の絵
    "film_transparent": False,                           # 背景が透けて background の色が出ない
    "use_motion_blur": False,                            # ぶれ
}
VIEW_FIXED = {"look": "None", "exposure": 0.0, "gamma": 1.0, "use_curve_mapping": False}   # scene.view_settings: 色の補正


def setting_changes(current, fixed):
    """current（設定の名前 → 今の値）のうち、fixed（描くときの値）と違うもの {名前: [今の値, 描くときの値]}。"""
    return {name: [current[name], value] for name, value in fixed.items() if current[name] != value}


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


def absolute_keys_problem(name, use_relative):
    """表情の組み合わせは、相対の形状キーの値で入り切りする。絶対の形状キー（Relative のチェックなし）は値でなく時間（eval_time）で形を
    選ぶので、値を入れても組み合わせの形にならず、どの組み合わせも同じ絵になりうる（model.shape_keys・render.passes と同じ考え）。
    そのときの説明（相対なら None）。"""
    if use_relative:
        return None
    return (f"{name} の形状キーは絶対（Relative のチェックなし）で、時間で形を選びます。表情の組み合わせは形状キーの値で入り切りするので、"
            "相対の形状キーのメッシュにしてください")


def key_changes(combos, states):
    """入れる値が効くように、この場で変える形状キー（保存しない）。states は基準のキーのほかの形状キーの {名前: (mute, slider_min, slider_max)}。
    組み合わせごとに、どのキーも 0 にしてから値を入れるので、どのキーも 0 と入れる値が範囲に入っていなければならない（範囲の外の値は
    Blender が範囲の端へ切る）。ミュートしたキーは値を入れても形が変わらない。
    戻り: (ミュートを外すキー（入れるキーだけ）, 範囲を広げるキー {名前: (新しい slider_min, 新しい slider_max)})。"""
    ranges = {name: (0.0, 0.0) for name in states}
    for _, values in combos:
        for name, value in values.items():
            low, high = ranges[name]
            ranges[name] = (min(low, value), max(high, value))
    requested = {name for _, values in combos for name in values}
    unmute = [name for name in states if name in requested and states[name][0]]
    widen = {}
    for name, (low, high) in ranges.items():
        _, slider_min, slider_max = states[name]
        if low < slider_min or high > slider_max:
            widen[name] = (min(slider_min, low), max(slider_max, high))
    return unmute, widen


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
    problem = absolute_keys_problem(head.name, head.data.shape_keys.use_relative) if head.data.shape_keys else None
    if problem:
        raise SystemExit(problem)
    keys = head.data.shape_keys.key_blocks if head.data.shape_keys else {}
    absent = sorted({name for _, values in settings["combos"] for name in values if name not in keys})
    if absent:
        raise SystemExit(f"{head.name} に無い形状キー: {absent}")
    reference = head.data.shape_keys.reference_key.name if head.data.shape_keys else None
    if reference is not None and any(reference in values for _, values in settings["combos"]):
        raise SystemExit(f"combos に基準のキー {reference} は入れられません（相対の形状キーの基準は、値を入れても形が変わらない）")
    changes = {"unmuted": [], "widened": {}, "pin_off": False, "animation_cleared": None}
    if head.data.shape_keys is not None:                 # 入れる値が効くように、この場で変える（保存しない）
        states = {key.name: (key.mute, key.slider_min, key.slider_max) for key in keys if key.name != reference}
        unmute, widen = key_changes(settings["combos"], states)
        for name in unmute:
            keys[name].mute = False
        for name, (low, high) in widen.items():          # 広げるだけなので、下の端から変えても上の端を越えない
            keys[name].slider_min, keys[name].slider_max = low, high
        changes.update(unmuted=unmute, widened={name: [low, high] for name, (low, high) in widen.items()})
        animation = head.data.shape_keys.animation_data
        if animation is not None:                        # アクション・ドライバー・NLA は、描くときに入れた値を上書きする
            changes["animation_cleared"] = {"action": animation.action.name if animation.action else None,
                                            "drivers": len(animation.drivers), "nla_tracks": len(animation.nla_tracks)}
            head.data.shape_keys.animation_data_clear()
    if head.show_only_shape_key:                         # 形状キーを 1 つだけ見せる設定（ピン）は、値を見ない
        head.show_only_shape_key = False
        changes["pin_off"] = True
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = settings["samples"]
    scene.view_settings.view_transform = "Standard"
    render_changes = {"render": setting_changes({name: getattr(scene.render, name) for name in RENDER_FIXED}, RENDER_FIXED),
                      "view": setting_changes({name: getattr(scene.view_settings, name) for name in VIEW_FIXED}, VIEW_FIXED),
                      "marker_cameras": [marker.name for marker in scene.timeline_markers if marker.camera is not None]}
    for name, (_, value) in render_changes["render"].items():
        setattr(scene.render, name, value)
    for name, (_, value) in render_changes["view"].items():
        setattr(scene.view_settings, name, value)
    for marker in scene.timeline_markers:                # マーカーに結んだカメラは、描くときにカメラを差し替える
        marker.camera = None
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
              "key_changes": changes, "render_changes": render_changes, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"rendered": len(images)}))


if __name__ == "__main__":
    main()
