"""裾などの揺れものの変形骨を、メッシュの辺に沿ってならす（cloth.smooth_weights）。

指定の骨で既に動いている頂点同士をつなぐ辺を使う。各頂点で指定した骨の配分をならし、腰など指定外の骨には重みを流さない。
指定外の骨を優先して残し、空いた枠に指定骨の重みを大きい順に入れる。対象頂点の変形骨は 4 本まで、合計は 1。
"""
import argparse
from collections import defaultdict
import json
import math
import os
import sys

import numpy as np

try:
    from .cloth_layers import mesh_problem
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cloth_layers import mesh_problem


MAX_INFLUENCES = 4


def smooth_weights(rows, edges, groups, iterations, alpha):
    """変形骨ごとの正の重みから {対象頂点: 新しい重み} と、骨を切った頂点数を返す。"""
    group_set = set(groups)
    ratios = np.array([[row.get(name, 0.0) for name in groups] for row in rows], dtype=float).reshape(-1, len(groups))
    totals = ratios.sum(axis=1)
    active = totals > 0
    ratios[active] /= totals[active, None]

    # 重みのない頂点を経由せず、離れたひだやスリットの反対側へにじませない。
    pairs = np.asarray(edges, dtype=np.intp).reshape(-1, 2)
    pairs = pairs[active[pairs].all(axis=1)]
    source = np.concatenate((pairs[:, 0], pairs[:, 1]))
    destination = np.concatenate((pairs[:, 1], pairs[:, 0]))
    degree = np.bincount(source, minlength=len(rows))
    connected = degree > 0
    for _ in range(iterations):
        neighbours = np.zeros_like(ratios)
        np.add.at(neighbours, source, ratios[destination])
        ratios[connected] = (1 - alpha) * ratios[connected] + alpha * neighbours[connected] / degree[connected, None]

    result, trimmed = {}, 0
    for index in np.flatnonzero(active).tolist():
        row = rows[index]
        # 過大な入力だけ比例して縮める。腰を含む指定外の重みは増やさない。
        scale = max(1.0, math.fsum(row.values()))
        fixed = {name: weight / scale for name, weight in row.items() if name not in group_set}
        slots = MAX_INFLUENCES - len(fixed)
        if slots <= 0:
            raise ValueError(f"頂点 {index} は groups の指定外に {len(fixed)} 本の変形骨があり、揺れものの重みを 4 本以内に残せません")
        spring = sorted(((name, value) for name, value in zip(groups, ratios[index]) if value > 0),
                        key=lambda item: -item[1])
        trimmed += len(spring) > slots
        kept = spring[:slots]
        budget = 1.0 - math.fsum(fixed.values())
        if budget > 0:
            total = math.fsum(value for _, value in kept)
            fixed.update({name: float(value / total * budget) for name, value in kept})
        result[index] = fixed
    return result, trimmed


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--blend", required=True)
    parser.add_argument("--settings", required=True)
    parser.add_argument("--out-blend", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)
    with open(args.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(args.blend))
    mesh = bpy.data.objects.get(settings["mesh"])
    if mesh is None or mesh.type != "MESH":
        raise SystemExit(f"mesh のメッシュがありません: {settings['mesh']}")
    if problem := mesh_problem(mesh):
        raise SystemExit(problem)
    bound = mesh.find_armature()
    armature_name = settings.get("armature")
    armature = bpy.data.objects.get(armature_name) if armature_name is not None else bound
    if armature is None or armature.type != "ARMATURE" or (bound is not None and bound != armature):
        raise SystemExit(f"{mesh.name} の変形を受け持つアーマチュアが見つかりません（armature: {armature_name!r}）")
    deform_bones = {bone.name for bone in armature.data.bones if bone.use_deform}
    bone_groups = {group.index: group.name for group in mesh.vertex_groups if group.name in deform_bones}
    if unknown := set(settings["groups"]).difference(bone_groups.values()):
        raise SystemExit(f"{mesh.name} に変形骨の頂点グループがありません: {sorted(unknown)}")

    rows = [{bone_groups[group.group]: group.weight for group in vertex.groups if group.group in bone_groups and group.weight > 0}
            for vertex in mesh.data.vertices]
    edges = [tuple(edge.vertices) for edge in mesh.data.edges]
    try:
        plan, trimmed = smooth_weights(rows, edges, settings["groups"], settings["iterations"], settings["alpha"])
    except ValueError as exc:
        raise SystemExit(f"{mesh.name}: {exc}") from exc
    if not plan:
        raise SystemExit(f"{mesh.name} で、groups の頂点グループに正の重みがある頂点が見つかりません")
    remove = defaultdict(list)
    changed = 0
    for index, weights in plan.items():
        previous = rows[index]
        if previous == weights:
            continue
        changed += 1
        for name in previous.keys() - weights.keys():
            remove[name].append(index)
        for name, weight in weights.items():
            if previous.get(name) != weight:
                mesh.vertex_groups[name].add([index], weight, "REPLACE")
    for name, indices in remove.items():
        mesh.vertex_groups[name].remove(indices)

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(args.out_blend), copy=True, compress=True)
    report = {
        "schema_version": "model3d-cloth-smooth-weights/0.1",
        "blender": bpy.app.version_string,
        "mesh": mesh.name,
        "armature": armature.name,
        "verts": len(rows),
        "target_vertices": len(plan),
        "changed_vertices": changed,
        "trimmed_vertices": trimmed,
        "max_influences_before": max(len(rows[index]) for index in plan),
        "max_influences_after": max(map(len, plan.values())),
        "settings": settings,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.report)), exist_ok=True)
    with open(args.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": args.out_blend, "changed_vertices": changed}))


if __name__ == "__main__":
    main()
