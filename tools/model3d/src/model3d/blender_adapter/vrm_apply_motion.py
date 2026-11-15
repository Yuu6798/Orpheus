"""Blender の中で動かす: 作業コピー（骨の名前を Mixamo 式に付け替えたもの）で付けた動きを、元の VRM のキャラの .blend へ戻す。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_apply_motion.py -- --character avatar.blend --motion motion.blend --bone-map bone_map.json \
        --out-blend out.blend --report report.json [--fps 24]

- motion の .blend（motion.retarget などの出力）のアクションのうち、F カーブのいちばん多いものを取り込み、骨の名前を
  対応表（vrm.motion_rig の bone_map。元の名前 → 付け替えた名前）の逆で元の名前へ戻して、キャラのアーマチュアに付ける。
  キャラに無い骨の動きがあれば止まる。
- シーンのコマの範囲を動きのキーの範囲に、fps を motion の .blend のシーンの fps（--fps で上書きできる）にする。
  motion の .blend のシーンにある model3d_points_frames（各コマが points の何コマ目か）も写す。
- 元のファイルは変えない（別名で保存する）。VRM の設定と材質はキャラの .blend のまま。

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスでリポジトリの外の台本と同じ骨の動きになることを
確かめた（docs/model3d.md）。CI には Blender が無い。
"""
import argparse
import json
import os
import sys


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vrm_apply_motion.py")
    ap.add_argument("--character", required=True, help="元の VRM のキャラの .blend（骨は元の名前）")
    ap.add_argument("--motion", required=True, help="作業コピーで動きを付けた .blend（骨は付け替えた名前）")
    ap.add_argument("--bone-map", required=True, help="vrm.motion_rig の bone_map（map: 元の名前 → 付け替えた名前）")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--fps", type=float, default=None, help="動きの fps（既定は motion の .blend のシーンの fps）")
    ap.add_argument("--armature", default="", help="キャラのアーマチュアの名前（1 つだけなら省ける）")
    return ap.parse_args(argv)


def inverse_map(data):
    """対応表（{"map": {元の名前: 付け替えた名前}}）の逆（付け替えた名前 → 元の名前）。付け替えた名前が重なれば ValueError。"""
    mapping = data.get("map") if isinstance(data, dict) else None
    if not isinstance(mapping, dict) or not mapping or not all(isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()):
        raise ValueError("bone_map の map は、元の名前 → 付け替えた名前（文字列）の表にしてください")
    inverse = {}
    for old, new in mapping.items():
        if new in inverse:
            raise ValueError(f"bone_map で、付け替えた名前 {new} が 2 つの骨にあります（{inverse[new]} と {old}）")
        inverse[new] = old
    return inverse


def bone_span(data_path):
    """`pose.bones["名前"].…` の骨の名前（\\ の逃がしを戻したもの）と、名前の後ろの `]` の位置。骨の F カーブでなければ None。
    Blender は名前の中の `"` と `\\` を `\\"`・`\\\\` にして書く。vrm_bake_springs.py に同じ写しがある。"""
    prefix = 'pose.bones["'
    if not data_path.startswith(prefix):
        return None
    name, index = [], len(prefix)
    while index < len(data_path):
        char = data_path[index]
        if char == "\\" and index + 1 < len(data_path):
            name.append(data_path[index + 1])
            index += 2
        elif char == '"':
            return ("".join(name), index + 1) if data_path[index + 1:index + 2] == "]" else None
        else:
            name.append(char)
            index += 1
    return None


def bone_of(data_path):
    """骨の F カーブの骨の名前。骨の F カーブでなければ None。"""
    span = bone_span(data_path)
    return span[0] if span else None


def renamed_path(data_path, inverse):
    """骨の F カーブのデータパスの骨の名前を、元の名前へ戻したもの（対応表に無い骨ならそのまま）。"""
    span = bone_span(data_path)
    if span is None or span[0] not in inverse:
        return data_path
    escaped = inverse[span[0]].replace("\\", "\\\\").replace('"', '\\"')
    return 'pose.bones["' + escaped + '"' + data_path[span[1]:]


def fps_settings(fps):
    """fps（小数も可）を Blender の fps（整数）と fps_base にする。実効の fps は fps / fps_base。"""
    whole = int(round(fps))
    if whole < 1:
        raise ValueError(f"fps は 0.5 以上にしてください: {fps}")
    return whole, whole / fps


def fcurves_of(action):
    """層つきのアクション（Blender 4.4 以降。5.x はこれだけ）と、古い形式の両方から F カーブを集める。"""
    curves = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                curves.extend(bag.fcurves)
    if not curves and hasattr(action, "fcurves"):
        curves = list(action.fcurves)
    return curves


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.bone_map, encoding="utf-8") as stream:
        try:
            inverse = inverse_map(json.load(stream))
        except ValueError as error:
            raise SystemExit(str(error)) from error

    # motion の .blend のシーンの fps と、points の何コマ目かの印と、骨の回転の持ち方を先に読む
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.motion))
    source = bpy.context.scene
    motion_fps = source.render.fps / source.render.fps_base
    points_frames = list(source["model3d_points_frames"]) if "model3d_points_frames" in source else None
    source_armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    # motion.retarget はファイルの最初のアーマチュアを動かし、骨をクォータニオンの回転にする
    modes = {bone.name: bone.rotation_mode for bone in source_armatures[0].pose.bones} if source_armatures else {}

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.character))
    scene = bpy.context.scene
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not a.armature or obj.name == a.armature)]
    if len(armatures) != 1:
        raise SystemExit(f"キャラのアーマチュアが {len(armatures)} 個あります（--armature で選んでください）")
    armature = armatures[0]

    before = set(bpy.data.actions.keys())
    with bpy.data.libraries.load(os.path.abspath(a.motion), link=False) as (data_from, data_to):
        data_to.actions = list(data_from.actions)
    loaded = [action for action in bpy.data.actions if action.name not in before]
    if not loaded:
        raise SystemExit(f"motion の .blend にアクションがありません: {a.motion}")
    action = max(loaded, key=lambda candidate: len(fcurves_of(candidate)))
    renamed, unknown = 0, set()
    for curve in fcurves_of(action):
        name = bone_of(curve.data_path)
        if name is None:
            continue
        if name in inverse:
            curve.data_path = renamed_path(curve.data_path, inverse)
            renamed += 1
        elif name not in armature.pose.bones:
            unknown.add(name)
    if unknown:
        raise SystemExit(f"キャラの骨に無い名前の動きがあります: {sorted(unknown)[:8]}")
    for extra in loaded:                                   # 使わないアクションは持ち込まない
        if extra is not action:
            bpy.data.actions.remove(extra)

    # キャラにもとからある動き（アクション・NLA）は外す（motion.retarget と同じ。残すと新しい動きに混ざる）。
    # ドライバーはリグの仕組みなので残す
    cleared = None
    if armature.animation_data is not None:
        old = armature.animation_data
        cleared = {"action": old.action.name if old.action else None, "nla_tracks": len(old.nla_tracks)}
        old.action = None
        for track in list(old.nla_tracks):
            old.nla_tracks.remove(track)
    # 骨の回転の持ち方を動きの側にそろえる（クォータニオンのキーは、オイラーや軸角の骨では使われない）
    changed_modes = 0
    for name, mode in modes.items():
        bone = armature.pose.bones.get(inverse.get(name, name))
        if bone is not None and bone.rotation_mode != mode:
            bone.rotation_mode = mode
            changed_modes += 1
    animation = armature.animation_data_create()
    animation.action = action
    if getattr(action, "slots", None) and len(action.slots):
        animation.action_slot = action.slots[0]

    frames = [point.co[0] for curve in fcurves_of(action) for point in curve.keyframe_points]
    if not frames:
        raise SystemExit("取り込んだアクションにキーがありません")
    first, last = int(round(min(frames))), int(round(max(frames)))
    fps = a.fps if a.fps is not None else motion_fps
    try:
        scene.render.fps, scene.render.fps_base = fps_settings(fps)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    scene.frame_start, scene.frame_end = first, last
    if points_frames is not None:
        scene["model3d_points_frames"] = points_frames
    scene.frame_set(first)

    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    report = {"schema_version": "model3d-vrm-apply-motion/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "action": action.name, "curves": len(fcurves_of(action)), "curves_renamed": renamed, "frames": [first, last],
              "fps": scene.render.fps / scene.render.fps_base, "motion_fps": motion_fps,
              "points_frames": len(points_frames) if points_frames is not None else None,
              "cleared_animation": cleared, "rotation_modes_changed": changed_modes}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"curves_renamed": renamed, "out_blend": out}))


if __name__ == "__main__":
    main()
