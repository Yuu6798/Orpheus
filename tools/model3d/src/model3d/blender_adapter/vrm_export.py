"""Blender の中で動かす: VRM のキャラの .blend を、VRM Add-on で VRM 1.0 として書き出す。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_export.py -- --blend in.blend --out-blend out.blend --out-vrm out.vrm --report report.json \
        [--meta meta.json] [--jpeg-quality 95 --textures textures/] [--armature NAME]

- 骨を基本の姿勢に戻し（動きを外し、姿勢の値を消す）、別名の .blend に保存してから書き出す。元のファイルは変えない。
- --meta（{"name", "version", "authors"} の JSON）があれば、VRM のメタの名前・版・作者に入れる。.blend には VRM の名前と作者が
  残らないことがあるので、元の VRM から写すために使う（利用条件などほかのメタは .blend のまま）。
- --jpeg-quality が 0 より大きければ、材質が使う画像を JPEG（その品質）にして --textures に書き、.blend に埋め込み直す
  （色の絵を PNG のまま埋めると大きくなる。2 作目の VRM のダンスでは 4 枚で 68 MB → 14 MB）。透過の要る画像には使わない。
- 入れないもの: 動き（VRM には入らない）。表情・布の計算・骨の拘束は、そもそも VRM の仕組みに無いか、アプリによって効かない。

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスの衣装のアセットと同じ VRM になることを確かめた
（docs/model3d.md）。CI には Blender が無い。
"""
import argparse
import json
import os
import re
import sys


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vrm_export.py")
    ap.add_argument("--blend", required=True, help="VRM のキャラの .blend（VRM Add-on の設定入り）")
    ap.add_argument("--out-blend", required=True, help="書き出す前の状態を保存する .blend")
    ap.add_argument("--out-vrm", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--meta", default="", help="メタの名前・版・作者の JSON（{name, version, authors}）")
    ap.add_argument("--jpeg-quality", type=int, default=0, help="色の絵を JPEG にする品質（1〜100。0 で変えない）")
    ap.add_argument("--textures", default="", help="JPEG にした絵の書き先のフォルダ（--jpeg-quality のとき）")
    ap.add_argument("--armature", default="", help="アーマチュアの名前（1 つだけなら省ける）")
    return ap.parse_args(argv)


def texture_name(image_name, taken):
    """画像の名前から、書き出す JPEG のファイル名（英数字と _ - だけ。重なれば番号を足す）。"""
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", image_name).strip("_") or "image"
    name, number = f"{stem}.jpg", 2
    while name.lower() in taken:
        name, number = f"{stem}_{number}.jpg", number + 1
    taken.add(name.lower())
    return name


def alpha_mode(material):
    """材質の透過の扱い（VRM の MToon の alpha_mode。OPAQUE・MASK・BLEND）。MToon の設定が無い材質は透過ありとみなす。"""
    mtoon = getattr(getattr(material, "vrm_addon_extension", None), "mtoon1", None)
    return getattr(mtoon, "alpha_mode", None) or "UNKNOWN"


def use_scene_of(bpy, armature):
    """アーマチュアのあるシーンを今のシーンにして返す（作業用のシーンを開いたまま保存されたファイルがある。vrm_apply_motion.py に
    同じ写しがある）。裏の起動でも、読み込んだファイルの窓のシーンを替えれば bpy.context.scene が替わる（Blender 5.2.4 で確かめた）。"""
    scenes = [scene for scene in bpy.data.scenes if armature.name in scene.objects]
    if not scenes:
        raise SystemExit(f"アーマチュア {armature.name} の入ったシーンがありません")
    if bpy.context.scene not in scenes:
        window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
        if window is None:
            raise SystemExit(f"アーマチュア {armature.name} のシーン {scenes[0].name} に切り替えられません（窓がありません）")
        window.scene = scenes[0]
    return bpy.context.scene


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    if not 0 <= a.jpeg_quality <= 100 or (a.jpeg_quality and not a.textures):
        raise SystemExit("--jpeg-quality は 0〜100 で、0 より大きいときは --textures が要ります")
    meta = None
    if a.meta:
        with open(a.meta, encoding="utf-8") as stream:
            meta = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not a.armature or obj.name == a.armature)]
    if len(armatures) != 1:
        raise SystemExit(f"アーマチュアが {len(armatures)} 個あります（--armature で選んでください）")
    armature = armatures[0]
    use_scene_of(bpy, armature)                           # 選んで書き出すアーマチュアは、今のシーンのビューレイヤーに要る
    extension = getattr(armature.data, "vrm_addon_extension", None)
    if extension is None:
        raise SystemExit("VRM Add-on の設定がありません（アドオンを有効にして起動してください）")

    # 基本の姿勢に戻す（動きは VRM に入らない。書き出しの基本の姿勢をずらさない）
    armature.data.pose_position = "REST"
    if armature.animation_data:
        armature.animation_data.action = None
    for bone in armature.pose.bones:
        bone.location = (0, 0, 0)
        bone.rotation_quaternion = (1, 0, 0, 0)
        bone.rotation_euler = (0, 0, 0)
        bone.rotation_axis_angle = (0, 0, 1, 0)
        bone.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    report = {"schema_version": "model3d-vrm-export/0.1", "blender": bpy.app.version_string, "armature": armature.name}

    if meta is not None:
        target = extension.vrm1.meta
        target.vrm_name = str(meta.get("name", ""))
        target.version = str(meta.get("version", ""))
        target.authors.clear()
        for author in meta.get("authors", []):
            target.authors.add().value = str(author)
        report["meta_copied"] = {"name": target.vrm_name, "version": target.version, "authors": [item.value for item in target.authors]}

    if a.jpeg_quality > 0:
        folder = os.path.abspath(a.textures)
        os.makedirs(folder, exist_ok=True)
        users = {}
        for material in bpy.data.materials:
            if material.node_tree:
                for node in material.node_tree.nodes:
                    if node.type == "TEX_IMAGE" and node.image and node.image.size[0] > 0:
                        users.setdefault(node.image, []).append(material)
        converted, taken = [], set()
        for image in sorted(users, key=lambda item: item.name):
            if image.channels == 4 and any(alpha_mode(material) != "OPAQUE" for material in users[image]):
                # 透過を使う材質の絵（JPEG は透過を持たない）。変えて書き出しはするが、報告に残す（工程が要確認にする）
                report.setdefault("alpha_dropped", []).append(image.name)
            path = os.path.join(folder, texture_name(image.name, taken))
            image.file_format = "JPEG"
            image.save(filepath=path, quality=a.jpeg_quality)
            if image.packed_file:
                image.unpack(method="REMOVE")
            image.filepath = path
            image.source = "FILE"
            image.reload()
            image.pack()
            converted.append({"image": image.name, "file": os.path.basename(path), "bytes": os.path.getsize(path)})
        report["textures"] = converted

    armature.data.pose_position = "POSE"
    bpy.context.view_layer.update()
    bpy.ops.object.select_all(action="DESELECT")
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature
    out_blend = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out_blend), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out_blend, check_existing=False, compress=True)

    out_vrm = os.path.abspath(a.out_vrm)
    os.makedirs(os.path.dirname(out_vrm), exist_ok=True)
    if os.path.exists(out_vrm):
        os.remove(out_vrm)
    result = bpy.ops.export_scene.vrm(filepath=out_vrm, armature_object_name=armature.name, ignore_warning=True)
    if result != {"FINISHED"} or not os.path.exists(out_vrm):
        raise SystemExit(f"VRM の書き出しに失敗しました: {result}")
    report["vrm_bytes"] = os.path.getsize(out_vrm)
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"vrm": out_vrm, "bytes": report["vrm_bytes"]}))


if __name__ == "__main__":
    main()
