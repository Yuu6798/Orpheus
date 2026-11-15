"""Blender の中で動かす: VRM のキャラの .blend から、骨の名前を Mixamo 式に付け替えた作業コピーと、名前の対応表を作る。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_motion_rig.py -- --character avatar.blend --out-blend rig.blend --bone-map bone_map.json \
        --report report.json [--bone-prefix mixamorig:] [--armature NAME] [--skip-meshes Hair --skip skip.npz]

model3d の動きの工程（motion.retarget ほか）は Mixamo 式の骨の名前を前提にする。VRM のキャラは、VRM Add-on が持つ人型の骨の割り当て
（VRM 1.0 の humanoid。`vrm_addon_extension.vrm1.humanoid.human_bones`）で、骨の役割が決まっている。その割り当てから、
model3d が役割を知っている骨（semantic.MIXAMO_ROLES）だけを Mixamo 式の名前へ付け替える。目・あごの骨など、Mixamo の名前の無い骨は
付け替えずに報告に残す。骨の名前を変えると、その骨で動くメッシュの頂点グループの名前も Blender が一緒に付け替える。
元のファイルは変えない（別名で保存する）。キャラの骨のあるシーンだけを残し、ほかのシーンは作業コピーに持ち込まない。

出力:
- --out-blend: 作業コピー
- --bone-map: 元の名前 → 付け替えた名前（model3d-vrm-bone-map/0.1。動きを元の VRM の .blend へ戻すときに使う）
- --report: 付け替えた骨の数・頂点グループ・残したシーン・付け替えなかった人型の骨（model3d-vrm-motion-rig/0.1）
- --skip（--skip-meshes を挙げたとき）: motion.retarget の skip_vertices に渡す npz（挙げたメッシュの頂点をすべて真にする。
  髪の前に手があるだけで、胴への刺さりと判定しないため）

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスの作業コピーと同じ対応表・同じ骨と頂点グループに
なることを確かめた（docs/model3d.md）。CI には Blender が無い。
"""
import argparse
import json
import os
import sys

import numpy as np

# VRM 1.0 の人型の骨（VRM Add-on の名前）→ Mixamo の骨の名前（接頭辞の後ろ）。並びは 体の中心 → 左 → 右（左右ごとに 腕と脚 → 指）。
# 親指は VRM 1.0 では metacarpal・proximal・distal、Mixamo では 1・2・3。ほかの指は proximal・intermediate・distal が 1・2・3。
CENTER = (("hips", "Hips"), ("spine", "Spine"), ("chest", "Spine1"), ("upper_chest", "Spine2"), ("neck", "Neck"), ("head", "Head"))
LIMBS = (("shoulder", "Shoulder"), ("upper_arm", "Arm"), ("lower_arm", "ForeArm"), ("hand", "Hand"),
         ("upper_leg", "UpLeg"), ("lower_leg", "Leg"), ("foot", "Foot"), ("toes", "ToeBase"))
FINGERS = (("thumb", "Thumb", ("metacarpal", "proximal", "distal")), ("index", "Index", ("proximal", "intermediate", "distal")),
           ("middle", "Middle", ("proximal", "intermediate", "distal")), ("ring", "Ring", ("proximal", "intermediate", "distal")),
           ("little", "Pinky", ("proximal", "intermediate", "distal")))


def _vrm_to_mixamo():
    table = dict(CENTER)
    for side, mixamo_side in (("left", "Left"), ("right", "Right")):
        for vrm, mixamo in LIMBS:
            table[f"{side}_{vrm}"] = mixamo_side + mixamo
        for vrm, mixamo, segments in FINGERS:
            for number, segment in enumerate(segments, start=1):
                table[f"{side}_{vrm}_{segment}"] = f"{mixamo_side}Hand{mixamo}{number}"
    return table


VRM_TO_MIXAMO = _vrm_to_mixamo()
# Mixamo の名前の無い VRM 1.0 の人型の骨（付け替えない。報告にだけ残す）
UNMAPPED_ROLES = ("left_eye", "right_eye", "jaw")
# motion.retarget が要る骨（retarget.REQUIRED_BONES と同じ。台本は 1 本ずつ Blender で動くので写しを持つ）
RETARGET_REQUIRED = ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftArm", "LeftForeArm", "LeftHand", "LeftUpLeg", "LeftLeg",
                     "LeftFoot", "RightArm", "RightForeArm", "RightHand", "RightUpLeg", "RightLeg", "RightFoot")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vrm_motion_rig.py")
    ap.add_argument("--character", required=True, help="VRM のキャラの .blend（VRM Add-on の人型の骨の割り当て入り）")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--bone-map", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--bone-prefix", default="mixamorig:")
    ap.add_argument("--armature", default="", help="アーマチュアの名前（人型の骨の割り当てのあるものが 1 つだけなら省ける）")
    ap.add_argument("--skip-meshes", default="", help="skip の npz で、すべての頂点を真にするメッシュ（カンマ区切り）")
    ap.add_argument("--skip", default="", help="--skip-meshes の npz の書き先")
    return ap.parse_args(argv)


def bone_map(assigned, prefix="mixamorig:"):
    """人型の骨の割り当て（VRM の名前 → 骨の名前。割り当ての無い骨は入れない）から、付け替えの対応表を作る。

    返すもの: map（元の名前 → Mixamo 式の名前。VRM_TO_MIXAMO の並び）、unmapped（Mixamo の名前の無い役割 → 骨の名前）、
    missing（motion.retarget が要るのに割り当ての無い Mixamo の名前）。同じ骨が 2 つの役割に割り当てられていれば ValueError。
    """
    mapping = {}
    for role, mixamo in VRM_TO_MIXAMO.items():
        bone = assigned.get(role)
        if not bone:
            continue
        if bone in mapping:
            raise ValueError(f"骨 {bone} が人型の 2 つの役割に割り当てられています（{role} と、{mapping[bone]} にした役割）")
        mapping[bone] = prefix + mixamo
    unmapped = {role: assigned[role] for role in UNMAPPED_ROLES if assigned.get(role)}
    doubled = sorted(role for role, bone in unmapped.items() if bone in mapping)
    if doubled:
        raise ValueError(f"付け替える骨が、目・あごの役割にも割り当てられています: {doubled}")
    unknown = sorted(set(assigned) - set(VRM_TO_MIXAMO) - set(UNMAPPED_ROLES))
    if unknown:
        raise ValueError(f"知らない人型の骨の役割: {unknown}")
    targets = set(mapping.values())
    missing = [name for name in RETARGET_REQUIRED if prefix + name not in targets]
    return mapping, unmapped, missing


def rename_conflicts(bone_names, mapping):
    """付け替えた名前が、付け替えない骨（または別の骨の元の名前）とぶつかるもの。Blender は同じ名前を .001 にずらすので、先に止める。"""
    taken = set(bone_names) - set(mapping)
    return sorted(new for old, new in mapping.items() if old != new and (new in taken or new in mapping))


def _assigned_bones(armature):
    """VRM Add-on が持つ人型の骨の割り当て（VRM の名前 → 骨の名前）。割り当ての無い役割は入れない。"""
    extension = getattr(armature.data, "vrm_addon_extension", None)
    if extension is None:
        raise SystemExit("VRM Add-on の設定がありません（アドオンを有効にして起動してください。model3d は addons で有効にする）")
    if extension.spec_version != "1.0":
        raise SystemExit(f"VRM {extension.spec_version} の人型の骨は未対応です（VRM 1.0 だけ）: {armature.name}")
    return _humanoid(extension)


def _humanoid(extension):
    human_bones = extension.vrm1.humanoid.human_bones
    assigned = {}
    for role in (*VRM_TO_MIXAMO, *UNMAPPED_ROLES):
        bone = getattr(getattr(human_bones, role, None), "node", None)
        name = getattr(bone, "bone_name", "")
        if name:
            assigned[role] = name
    return assigned


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    skip_meshes = [name.strip() for name in a.skip_meshes.split(",") if name.strip()]
    if bool(skip_meshes) != bool(a.skip):
        raise SystemExit("--skip-meshes と --skip は一緒に渡してください")
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.character))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
    if a.armature:
        armatures = [obj for obj in armatures if obj.name == a.armature]
        if not armatures:
            raise SystemExit(f"アーマチュアがありません: {a.armature}")
    else:
        if armatures and getattr(armatures[0].data, "vrm_addon_extension", None) is None:
            raise SystemExit("VRM Add-on の設定がありません（アドオンを有効にして起動してください。model3d は addons で有効にする）")
        found = [obj for obj in armatures if obj.data.vrm_addon_extension.spec_version == "1.0"
                 and _humanoid(obj.data.vrm_addon_extension)]
        if len(found) != 1:
            other = sorted({obj.data.vrm_addon_extension.spec_version for obj in armatures} - {"1.0"})
            note = f"。VRM 1.0 でないもの: {other}（未対応）" if other else ""
            raise SystemExit(f"VRM 1.0 の人型の骨の割り当てのあるアーマチュアが {len(found)} 個です（1 つのとき使える。"
                             f"--armature で選べます）{note}")
        armatures = found
    armature = armatures[0]
    assigned = _assigned_bones(armature)
    try:
        mapping, unmapped, missing = bone_map(assigned, a.bone_prefix)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    bones = armature.data.bones
    absent = sorted(old for old in mapping if old not in bones)
    if absent:
        raise SystemExit(f"人型の骨に割り当てられた骨が、アーマチュアにありません: {absent}")
    if missing:
        raise SystemExit(f"motion.retarget が要る骨に、人型の骨の割り当てがありません（Mixamo の名前）: {missing}")
    conflicts = rename_conflicts([bone.name for bone in bones], mapping)
    if conflicts:
        raise SystemExit(f"付け替えた名前が、ほかの骨の名前とぶつかります: {conflicts}")

    # その骨で動くメッシュ（アーマチュアのモディファイアか、アーマチュアを親にした変形）
    deformed = [obj for obj in bpy.data.objects if obj.type == "MESH"
                and (any(m.type == "ARMATURE" and m.object == armature for m in obj.modifiers)
                     or (obj.parent == armature and obj.parent_type == "ARMATURE"))]
    before = {obj.name: {group.name for group in obj.vertex_groups} for obj in deformed}
    renamed = 0
    for old, new in mapping.items():
        if old != new:
            bones[old].name = new
            renamed += 1
    groups, stale = {}, {}
    for obj in deformed:
        names = {group.name for group in obj.vertex_groups}
        expected = [old for old, new in mapping.items() if old != new and old in before[obj.name]]
        groups[obj.name] = len([old for old in expected if mapping[old] in names])
        left = sorted(old for old in expected if old in names)
        if left:
            stale[obj.name] = left
    if stale:                                             # メッシュごとに、付け替えるはずの頂点グループが 1 つも残っていないこと
        raise SystemExit(f"骨の名前に合わせて頂点グループの名前が付け替わっていません: {stale}")

    # キャラの骨のあるシーンだけを残す（作業用のシーンが残っているファイルがある）
    keep = [scene for scene in bpy.data.scenes if armature.name in scene.objects]
    if not keep:
        raise SystemExit(f"アーマチュア {armature.name} の入ったシーンがありません")
    removed = []
    for scene in list(bpy.data.scenes):
        if scene is not keep[0]:
            removed.append(scene.name)
            bpy.data.scenes.remove(scene)
    # ほかのアーマチュアは作業コピーに持ち込まない（motion.retarget ほかの工程は、ファイルの最初のアーマチュアを使う）
    other_armatures = sorted(obj.name for obj in bpy.data.objects if obj.type == "ARMATURE" and obj != armature)
    for name in other_armatures:
        bpy.data.objects.remove(bpy.data.objects[name], do_unlink=True)

    skip = {}
    if skip_meshes:
        arrays = {}
        for name in skip_meshes:
            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != "MESH":
                raise SystemExit(f"--skip-meshes のメッシュがありません: {name}")
            arrays[name] = np.ones(len(obj.data.vertices), dtype=bool)
            skip[name] = len(obj.data.vertices)
        os.makedirs(os.path.dirname(os.path.abspath(a.skip)), exist_ok=True)
        with open(a.skip, "wb") as stream:
            np.savez(stream, **arrays)

    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    hips = bones[a.bone_prefix + "Hips"]
    data = {"schema_version": "model3d-vrm-bone-map/0.1", "prefix": a.bone_prefix, "armature": armature.name, "map": mapping,
            "humanoid": assigned, "unmapped": unmapped}
    report = {"schema_version": "model3d-vrm-motion-rig/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "bones": len(bones), "mapped": len(mapping), "renamed": renamed, "unmapped": unmapped,
              "vertex_groups_renamed": groups, "kept_scene": keep[0].name, "removed_scenes": removed,
              "removed_armatures": other_armatures,
              "meshes": [[obj.name, len(obj.data.vertices)] for obj in bpy.data.objects if obj.type == "MESH"],
              "hips_parent": hips.parent.name if hips.parent else None, "skip": skip}
    for path, value in ((a.bone_map, data), (a.report, report)):
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=1)
            stream.write("\n")
    print(json.dumps({"renamed": renamed, "out_blend": out}))


if __name__ == "__main__":
    main()
