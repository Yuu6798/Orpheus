"""Blender の中で動かす: 目の骨の根もとを目の球の中心へ移し、瞳の頂点の重みを目の骨へ付ける（model.eye_rig）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python eye_rig.py -- \
        --blend in.blend --settings settings.json --out out.blend --report report.json

目の板を球の面の一部にし、瞳を同じ中心の球（少し手前）に乗せ、目の骨の根もとを球の中心に置くと、骨を少し回すだけで、瞳は板から浮きも
沈みもせずに滑る（3 作目の VRM のダンスのあとの試し）。settings.json（model.eye_rig が書く）:
- geometry: 目の形の値（eye_math.py の model3d-eye-geometry/0.1）。object（頭のメッシュ）・片目ごとの sphere（centre・normal・radius）・
  iris_vertices（瞳の頂点の番号）
- bones: {"R": 右目の骨, "L": 左目の骨}（省くと VRM の人型の骨の割り当ての right_eye・left_eye）、armature（省くと人型の骨の割り当ての
  あるアーマチュアが 1 つのときそれ）
すること（試しと同じ手順と計算）:
- 目の骨の頂点グループが無ければ、左目・右目の順に足す。瞳の頂点は、ほかの頂点グループから外し、その目の骨に重み 1 で付ける。
- 目の骨（編集の骨）: つながり（use_connect）を外し、根もと = 球の中心、先 = 中心 + 板の向き × 半径（骨の先がちょうど目の板に届く）、
  ロールは世界の上（+Z）にそろえる。アーマチュアの置き場所の行列の 3×3 で解いて、アーマチュアの中の座標にする。
入力の .blend は変えない（別のファイルに書く）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .animation_sample import humanoid_map, pick_armature
    from .eye_math import bone_ends, to_armature, validate
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import humanoid_map, pick_armature
    from eye_math import bone_ends, to_armature, validate

GROUP_ORDER = ("L", "R")         # 頂点グループを足す順（試しと同じ）
BONE_ORDER = ("R", "L")          # 骨を直す順（試しと同じ。結果は順によらない）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="eye_rig.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def eye_bones(given, humanoid):
    """右目・左目の骨の名前。given（{"R", "L"}）が無ければ人型の骨の割り当ての right_eye・left_eye。"""
    if given:
        return {"R": given["R"], "L": given["L"]}
    names = {"R": humanoid.get("right_eye"), "L": humanoid.get("left_eye")}
    if not all(names.values()):
        raise SystemExit("目の骨が決まりません（bones を渡すか、VRM の人型の骨の割り当てに right_eye・left_eye を入れてください）")
    return names


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    geometry = settings["geometry"]
    head = bpy.data.objects.get(geometry["object"])
    if head is None or head.type != "MESH":
        raise SystemExit(f"頭のメッシュの物体がありません: {geometry['object']}")
    validate(geometry, vertex_count=len(head.data.vertices))
    armature = pick_armature(bpy, settings.get("armature"))
    names = eye_bones(settings.get("bones"), humanoid_map(armature))
    missing = [name for name in names.values() if name not in armature.data.bones]
    if missing:
        raise SystemExit(f"{armature.name} に目の骨がありません: {missing}")
    added = []
    for side in GROUP_ORDER:
        if names[side] not in head.vertex_groups:
            head.vertex_groups.new(name=names[side])
            added.append(names[side])
    report = {"blender": bpy.app.version_string, "object": head.name, "armature": armature.name, "bones": names,
              "groups_added": added, "eyes": {}}
    for side in BONE_ORDER:
        iris = geometry["eyes"][side]["iris_vertices"]
        for group in head.vertex_groups:                                  # 瞳の頂点は、目の骨だけに付ける
            group.remove(iris)
        head.vertex_groups[names[side]].add(iris, 1.0, "REPLACE")
        report["eyes"][side] = {"iris_vertices": len(iris)}
    bpy.context.view_layer.objects.active = armature
    bpy.ops.object.mode_set(mode="EDIT")
    world = np.array(armature.matrix_world)
    for side in BONE_ORDER:
        sphere = geometry["eyes"][side]["sphere"]
        bone = armature.data.edit_bones[names[side]]
        before = {"head": [round(v, 6) for v in bone.head], "tail": [round(v, 6) for v in bone.tail], "use_connect": bone.use_connect}
        root, tip = bone_ends(sphere["centre"], sphere["normal"], sphere["radius"])
        bone.use_connect = False
        bone.head = Vector(to_armature(root, world))
        bone.tail = Vector(to_armature(tip, world))
        bone.align_roll(Vector((0.0, 0.0, 1.0)))
        report["eyes"][side].update(before=before, head=[round(v, 6) for v in bone.head], tail=[round(v, 6) for v in bone.tail],
                                    length_mm=round(bone.length * 1000, 3))
    bpy.ops.object.mode_set(mode="OBJECT")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"bones": list(names.values()), "groups_added": added}))


if __name__ == "__main__":
    main()
