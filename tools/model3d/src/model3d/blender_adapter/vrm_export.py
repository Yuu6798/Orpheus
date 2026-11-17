"""Blender の中で動かす: VRM のキャラの .blend を、VRM Add-on で VRM 1.0 として書き出す。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_export.py -- --blend in.blend --out-blend out.blend --out-vrm out.vrm --report report.json \
        [--meta meta.json] [--jpeg-quality 95 --textures textures/] [--armature NAME]

- 骨を基本の姿勢に戻し（動きを外し、姿勢の値を消す）、別名の .blend に保存してから書き出す。元のファイルは変えない。
- --meta（{"name", "version", "authors"} の JSON）があれば、VRM のメタの名前・版・作者に入れる。.blend には VRM の名前と作者が
  残らないことがあるので、元の VRM から写すために使う（利用条件などほかのメタは .blend のまま）。
- --jpeg-quality が 0 より大きければ、材質が使う画像を JPEG（その品質）にして --textures に書き、.blend に埋め込み直す
  （色の絵を PNG のまま埋めると大きくなる。2 作目の VRM のダンスでは 4 枚で 68 MB → 14 MB）。透過の要る画像には使わない。
- 入れないもの: 動き（VRM には入らない）。布の計算は VRM の仕組みに無い。表情は形状キーを VRM の表情に割り当てたものだけが入る。
- 骨の拘束: VRM Add-on は、VRM 1.0 の拘束（VRMC_node_constraint の aim・roll・rotation）の形の拘束だけを書き出す（骨 1 本に 1 つ。
  ミュートした拘束は書き出さない）。報告の bone_constraints に、.blend の骨の拘束（骨・拘束の名前・種類・ミュート・影響・相手と、
  Copy Rotation なら軸・混ぜ方・空間・反転、Damped Track なら向き・head_tail）を並べる。書き出した拘束は check.vrm の作りの一覧
  （structure の node_constraints）にあるので、工程（vrm.export）が left_out で、骨・VRM の拘束の形（vrm_form）・軸・相手・重みが
  同じものどうしを突き合わせ、書き出されなかった拘束と、.blend に見つからなかった VRM の拘束を観測に載せる。

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスの衣装のアセットと同じ VRM になることを確かめた。
3 作目の VRM のダンスの腕のねじれの骨の拘束 6 個も突き合わせた（docs/model3d.md）。CI には Blender が無い（突き合わせ方はテストする）。
"""
import argparse
import json
import os
import re
import sys

# Damped Track の向き → VRM の aim の軸（VRMC_node_constraint の aimAxis）
AIM_AXES = {"TRACK_X": "PositiveX", "TRACK_Y": "PositiveY", "TRACK_Z": "PositiveZ",
            "TRACK_NEGATIVE_X": "NegativeX", "TRACK_NEGATIVE_Y": "NegativeY", "TRACK_NEGATIVE_Z": "NegativeZ"}
WEIGHT_TOLERANCE = 1e-6                                 # 影響（float32）と VRM の重み（JSON の数）の比べ


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


def vrm_form(row):
    """.blend の骨の拘束 1 つ（報告の bone_constraints の 1 行）が VRM 1.0 の拘束の形なら (種類, 軸)、形でなければ None。
    roll: Copy Rotation の軸 1 つ・Add・ローカルどうし・反転なし。rotation: 同じで軸 3 つ。aim: Damped Track の head_tail 0。
    ミュートした拘束は形でない（書き出されない）。"""
    if row.get("mute"):
        return None
    if (row.get("type") == "COPY_ROTATION" and row.get("mix") == "ADD" and row.get("spaces") == ["LOCAL", "LOCAL"]
            and not row.get("inverted")):
        axes = row.get("axes") or ""
        if axes == "XYZ":
            return ("rotation", None)
        if len(axes) == 1:
            return ("roll", axes)
    if row.get("type") == "DAMPED_TRACK" and row.get("head_tail") == 0 and row.get("track") in AIM_AXES:
        return ("aim", AIM_AXES[row["track"]])
    return None


def left_out(bone_constraints, exported):
    """.blend の骨の拘束（報告の bone_constraints。骨ごとに拘束の並びの順）と、書き出した VRM の拘束（check.vrm の structure の
    node_constraints: [{"node", "kind", "source", "axis", "weight"}]）を突き合わせる。VRM の拘束ごとに、同じ骨（node）の拘束のうち、
    VRM の拘束の形（vrm_form）の種類と軸・相手（source）・重み（影響）が同じ最初のものを、書き出された拘束とする。
    戻り: (書き出されなかった .blend の拘束, .blend に見つからなかった VRM の拘束)。"""
    taken, unmatched = set(), []
    for row in exported:
        weight = 1.0 if row.get("weight") is None else row["weight"]      # VRM の重みの既定は 1
        for index, constraint in enumerate(bone_constraints):
            if (index not in taken and constraint["bone"] == row["node"] and vrm_form(constraint) == (row["kind"], row["axis"])
                    and constraint.get("source") == row["source"]
                    and abs(float(constraint.get("influence", 1.0)) - float(weight)) <= WEIGHT_TOLERANCE):
                taken.add(index)
                break
        else:
            unmatched.append(row)
    return [c for index, c in enumerate(bone_constraints) if index not in taken], unmatched


def constraint_row(pose_bone, constraint, armature):
    """突き合わせに使う、骨の拘束 1 つの値（Blender の中で読む）。相手は、このアーマチュアの骨ならその名前、ほかの物体ならその名前。"""
    row = {"bone": pose_bone.name, "constraint": constraint.name, "type": constraint.type, "mute": bool(constraint.mute),
           "influence": float(constraint.influence)}
    target = getattr(constraint, "target", None)
    row["source"] = (getattr(constraint, "subtarget", "") or None) if target == armature else getattr(target, "name", None)
    if constraint.type == "COPY_ROTATION":
        row.update({"axes": "".join(a for a, on in zip("XYZ", (constraint.use_x, constraint.use_y, constraint.use_z)) if on),
                    "inverted": bool(constraint.invert_x or constraint.invert_y or constraint.invert_z),
                    "mix": constraint.mix_mode, "spaces": [constraint.owner_space, constraint.target_space]})
    elif constraint.type == "DAMPED_TRACK":
        row.update({"track": constraint.track_axis, "head_tail": float(constraint.head_tail)})
    return row


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

    # 基本の姿勢に戻す（動きは VRM に入らない。書き出しの基本の姿勢をずらさない）。動きはアクションも NLA の層も外す
    # （層が残ると、POSE に戻したときに動きが当たり直す。vrm_apply_motion.py と同じく、ドライバーはリグの仕組みなので残す）
    armature.data.pose_position = "REST"
    cleared = None
    if armature.animation_data:
        animation = armature.animation_data
        cleared = {"action": animation.action.name if animation.action else None, "nla_tracks": len(animation.nla_tracks)}
        animation.action = None
        for track in list(animation.nla_tracks):
            animation.nla_tracks.remove(track)
    for bone in armature.pose.bones:
        bone.location = (0, 0, 0)
        bone.rotation_quaternion = (1, 0, 0, 0)
        bone.rotation_euler = (0, 0, 0)
        bone.rotation_axis_angle = (0, 0, 1, 0)
        bone.scale = (1, 1, 1)
    bpy.context.view_layer.update()
    report = {"schema_version": "model3d-vrm-export/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "cleared_animation": cleared}

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
    report["bone_constraints"] = [constraint_row(pose_bone, constraint, armature)
                                  for pose_bone in armature.pose.bones for constraint in pose_bone.constraints]
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"vrm": out_vrm, "bytes": report["vrm_bytes"]}))


if __name__ == "__main__":
    main()
