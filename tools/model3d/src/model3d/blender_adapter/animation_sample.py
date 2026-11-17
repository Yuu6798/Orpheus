"""Blender の中で動かす: 動きを付けた .blend の全コマを読み、動きの検品（check.animation）が数えるための値を書き出す
（測るだけ。.blend は変えない）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python animation_sample.py -- \
        --blend dance.blend --settings settings.json --out sample.npz --meta sample.json

数える所（しきい値・出来事のまとめ方）は Blender の外（model3d/animation_check.py）。ここは読むだけ:
- 人型の骨の割り当て（VRM Add-on の VRM 1.0 の humanoid）、骨の名前・親・初期姿勢の行列（アーマチュアの中）
- メッシュ（このアーマチュアで動き、描くもの。hide_render の物は数えない。押し出し用の身代わりなど）を、頂点ごとにいちばん重みの
  大きい頂点グループで、体と布（揺れものの骨が 6 割以上で、頭の下でないもの）に分ける。体のメッシュは、初期姿勢の頂点の位置
  （アーマチュアの中）、頂点ごとのいちばん重い頂点グループ、胴の骨の重みの和と全部の重みの和、面を書く
- 頂点グループごとの重みの最大（どの骨が見た目を動かすか）
- コマごと: アーマチュアの世界の行列、全部の骨の姿勢の行列（アーマチュアの中）と向き（クォータニオン）、骨の付け根の世界の位置、足と、つま先の骨にいちばん
  重い頂点の低い点（2 通り: 物体の中の z がいちばん小さい頂点＝リポジトリ外の試作と同じ、世界の z がいちばん小さい頂点）
- 布（cloth が true で、布のメッシュがあるとき）: 初期姿勢で布の近くにある体の頂点を選び（もう布の外にある頂点は見ない）、コマごとに、
  その頂点から骨の軸へ向けた光線が布に当たった所を、(骨, 布) ごとに数・いちばん深い所・位置の平均・外向きの向きにまとめる
  （光線の当たり判定は Blender の BVHTree。試作と同じ手順と計算（mathutils））

settings.json（check.animation が書く）: armature（省くと VRM 1.0 の人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）、
frames（[最初, 最後]。省くとシーンの全コマ）、cloth（布を見るか）・cloth_near_m・cloth_depth_min_m。
シーンの scale_length が 1 でないとき、アーマチュアの物体に大きさ（scale）があるときは止める（長さを m で測るため）。
**検証**: 本物の Blender 5.2.4 で、リポジトリ外の試作と同じ入力から同じ出来事になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of

SCHEMA = "model3d-animation-sample/0.1"
TORSO_ROLES = ("hips", "spine", "chest", "upper_chest", "neck")
SPRING_SHARE, HEAD_SHARE = 0.6, 0.5        # 布: 人型でない骨にいちばん重い頂点が 6 割以上で、頭の骨の下の骨のものが 5 割未満
UNIT_TOLERANCE = 1e-6
# 布の光線（試作と同じ決まった値）: 骨の軸から 1 cm 未満の頂点は見ない、光線は頂点から 0.5 mm 骨の側から始め、長さは 6 cm と
# 骨の軸までの 9 割の短い方
CLOTH_GAP_M, CLOTH_START_M, CLOTH_RAY_M, CLOTH_RAY_SHARE = 0.01, 0.0005, 0.06, 0.9


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="animation_sample.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--meta", required=True)
    return ap.parse_args(argv)


def humanoid_map(armature):
    """VRM Add-on の人型の骨の割り当て（VRM 1.0）。{役割: 骨の名前}。割り当ての無い役割は入れない。"""
    extension = getattr(armature.data, "vrm_addon_extension", None)
    found = {}
    if extension is None:
        return found
    human_bones = extension.vrm1.humanoid.human_bones
    for role in dir(human_bones):
        node = getattr(getattr(human_bones, role, None), "node", None)
        if node is not None and getattr(node, "bone_name", ""):
            found[role] = node.bone_name
    return found


def dominant(vertices, names):
    """頂点ごとの、いちばん重みの大きい頂点グループの名前（重みが 0 より大きいものが無ければ None）。同じ重みなら先のもの。
    vertices は頂点ごとの (グループの番号, 重み) の並び、names は番号 → 名前。"""
    out = []
    for groups in vertices:
        best, weight = None, 0.0
        for index, value in groups:
            if value > weight:
                best, weight = names.get(index), value
        out.append(best)
    return out


def descendants(children, root):
    """骨 root とその子孫の名前（children は 名前 → 子の名前の並び）。"""
    found, stack = set(), [root]
    while stack:
        bone = stack.pop()
        found.add(bone)
        stack.extend(children.get(bone, ()))
    return found


def classify(names, human, head_tree):
    """メッシュの頂点ごとのいちばん重い頂点グループの名前から、"cloth"（揺れもので動く布。裾・垂れなど）・"hair"（頭の下の揺れもの）・
    "body" を決める。"""
    n = max(1, len(names))
    spring = sum(1 for name in names if name and name not in human) / n
    under_head = sum(1 for name in names if name in head_tree) / n
    if spring >= SPRING_SHARE:
        return "cloth" if under_head < HEAD_SHARE else "hair"
    return "body"


def lowest_indices(co, matrix):
    """頂点（物体の中の座標 (N, 3)）のうち、(物体の中の z がいちばん小さいもの, 世界の z がいちばん小さいもの) の番号。
    前者はリポジトリ外の試作と同じ選び方で、物体が回っている（Y を上にして作ったメッシュを X のまわりに 90° 回して立てたなど）と、
    いちばん低い点にならない（かかとの後ろの点になる）。matrix は物体の置き場所の 4×4。"""
    matrix = np.asarray(matrix, dtype=np.float64)
    world_z = co @ matrix[2, :3] + matrix[2, 3]
    return int(np.argmin(co[:, 2])), int(np.argmin(world_z))


def seg_closest(p, a, b):
    """点 p から線分 a–b のいちばん近い点までの距離と、その点（試作と同じ式。mathutils の Vector でも、テストの代役でも動く）。"""
    ab = b - a
    u = max(0.0, min(1.0, (p - a).dot(ab) / max(ab.length_squared, 1e-12)))
    c = a + ab * u
    return (p - c).length, c


def toward_bone(p, a, b):
    """体の頂点 p から、その骨の線分 a–b の軸へ向かう光線（始まり, 向き, 長さ）。骨の軸から 1 cm 未満の頂点は None。"""
    dist, c = seg_closest(p, a, b)
    if dist < CLOTH_GAP_M:
        return None
    direction = (c - p) / dist
    return p + direction * CLOTH_START_M, direction, min(CLOTH_RAY_M, dist * CLOTH_RAY_SHARE)


def watched(points, names, human, head_tree, segment, tree, near, depth_min):
    """初期姿勢で、布の近く（near 未満）にある体の頂点（人型の骨にいちばん重く、頭の骨の下でないもの）の番号。
    骨の軸へ向かって見て、深さ depth_min の半分以上奥で布に当たる頂点（もう布の外にある。帯の下に差し込んだ裾の上など、作りとして
    そうなっている所）は見ない。segment(骨の名前) は骨の線分の両端。返す: (見る頂点の番号の並び, 見ない頂点の数)。"""
    picked, tucked = [], 0
    for i, (p, name) in enumerate(zip(points, names)):
        if name not in human or name in head_tree:
            continue
        nearest = tree.find_nearest(p)
        if nearest[3] is None or nearest[3] >= near:
            continue
        ray = toward_bone(p, *segment(name))
        if ray is not None:
            hit = tree.ray_cast(*ray)
            if hit[0] is not None and hit[3] >= depth_min * 0.5:
                tucked += 1
                continue
        picked.append(i)
    return picked, tucked


def through_cloth(points, names, segment, tree, face_object, depth_min, zero, found=None):
    """体の頂点から骨の軸へ向かう光線が、depth_min 以上奥で布に当たる（体が布の外へ出ている）所を、(骨, 布の物体) ごとに数える。
    返す（found に足す）: {(骨, 布の物体): [数, いちばん深い所, 位置の和, 外向きの和]}（並びは見つけた順）。zero() は 0 の 3 次元の値。"""
    found = {} if found is None else found
    for p, name in zip(points, names):
        ray = toward_bone(p, *segment(name))
        if ray is None:
            continue
        hit = tree.ray_cast(*ray)
        if hit[0] is not None and hit[3] >= depth_min:
            entry = found.setdefault((name, face_object[hit[2]]), [0, 0.0, zero(), zero()])
            entry[0] += 1
            entry[1] = max(entry[1], hit[3])
            entry[2] += p
            entry[3] -= ray[1]
    return found


def pick_armature(bpy, name):
    if name:
        armature = bpy.data.objects.get(name)
        if armature is None or armature.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {name}")
        return armature
    found = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and humanoid_map(obj)]
    if len(found) != 1:
        raise SystemExit(f"人型の骨の割り当て（VRM 1.0）のあるアーマチュアが {len(found)} 個です（armature で選んでください。"
                         "VRM Add-on を有効にして起動しているかも確かめてください）")
    return found[0]


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armature = pick_armature(bpy, settings.get("armature"))
    scene = use_scene_of(bpy, armature)
    unit = float(scene.unit_settings.scale_length)
    if abs(unit - 1.0) > UNIT_TOLERANCE:
        raise SystemExit(f"シーンの scale_length が {unit} です（1 Blender 単位 = 1 m のシーンだけ測れます）")
    world = armature.matrix_world
    axes = np.array(world)[:3, :3]
    if not np.allclose(axes.T @ axes, np.eye(3), atol=1e-5):
        raise SystemExit("アーマチュアの物体に大きさ（scale）かずれ（shear）があります（長さを m で測れません）")
    # 体のメッシュの点はアーマチュアの中で比べる（骨の姿勢の行列と同じ座標）。アーマチュアが世界の原点のまま（ちょうど単位の行列）
    # なら、試作と同じく世界の座標のまま使う
    to_armature = None if np.array_equal(np.array(world), np.eye(4)) else world.inverted()
    humanoid = humanoid_map(armature)
    if not humanoid:
        raise SystemExit(f"{armature.name} に人型の骨の割り当て（VRM 1.0）がありません（VRM Add-on を有効にして起動してください）")
    bones = [pose_bone.name for pose_bone in armature.pose.bones]
    parents = [armature.data.bones[name].parent.name if armature.data.bones[name].parent else None for name in bones]
    children = {}
    for name, parent in zip(bones, parents):
        if parent is not None:
            children.setdefault(parent, []).append(name)
    human = set(humanoid.values())
    head_tree = descendants(children, humanoid["head"]) if "head" in humanoid else set()
    torso_bones = {humanoid[role] for role in TORSO_ROLES if role in humanoid}
    foot_bones = {side: {humanoid[f"{side}_{part}"] for part in ("foot", "toes") if f"{side}_{part}" in humanoid}
                  for side in ("left", "right")}
    first, last = settings.get("frames") or (scene.frame_start, scene.frame_end)
    frames = list(range(int(first), int(last) + 1))

    # 描くメッシュ（このアーマチュアで動き、hide_render でないもの）
    meshes, groups, deform = [], [], {}
    group_index = {}
    for obj in bpy.data.objects:
        if obj.type != "MESH" or obj.find_armature() is not armature or obj.hide_render:
            continue
        names = {group.index: group.name for group in obj.vertex_groups}
        vertices = [[(g.group, g.weight) for g in vertex.groups] for vertex in obj.data.vertices]
        for groups_of_vertex in vertices:
            for index, weight in groups_of_vertex:
                name = names.get(index)
                if name is not None and weight > deform.get(name, -1.0):
                    deform[name] = weight
        top = dominant(vertices, names)
        meshes.append({"object": obj, "names": names, "vertices": vertices, "dominant": top,
                       "kind": classify(top, human, head_tree)})
    arrays = {"rest": np.array([np.array(bone.matrix_local) for bone in (armature.data.bones[name] for name in bones)])}
    cloth_objects = [m["object"] for m in meshes if m["kind"] == "cloth"]
    look_at_cloth = bool(settings.get("cloth", True)) and bool(cloth_objects)
    if look_at_cloth:                                                    # 初期姿勢の布（世界の座標）
        cv, cf = [], []
        for obj in cloth_objects:
            base = len(cv)
            cv += [obj.matrix_world @ vertex.co for vertex in obj.data.vertices]
            cf += [[base + i for i in polygon.vertices] for polygon in obj.data.polygons]
        rest_cloth = BVHTree.FromPolygons(cv, cf)

        def rest_segment(name):
            bone = armature.data.bones[name]
            return world @ bone.head_local, world @ bone.tail_local
    body_meta, feet, watch, tucked = [], {"left": [], "right": []}, [], 0
    for mesh in meshes:
        if mesh["kind"] != "body":
            continue
        obj, k = mesh["object"], len(body_meta)
        matrix = obj.matrix_world
        co = [matrix @ vertex.co for vertex in obj.data.vertices]          # リポジトリ外の試作と同じ計算（mathutils）
        if look_at_cloth:
            picked, skipped = watched(co, mesh["dominant"], human, head_tree, rest_segment, rest_cloth,
                                      settings["cloth_near_m"], settings["cloth_depth_min_m"])
            tucked += skipped
            if picked:
                watch.append((obj, np.array(picked, dtype=np.int64), [mesh["dominant"][i] for i in picked]))
        if to_armature is not None:
            co = [to_armature @ point for point in co]
        torso = []
        for groups_of_vertex in mesh["vertices"]:
            total = sum(weight for _, weight in groups_of_vertex)
            inside = sum(weight for index, weight in groups_of_vertex if mesh["names"].get(index) in torso_bones)
            torso.append((inside, total))
        for name in mesh["dominant"]:
            if name is not None and name not in group_index:
                group_index[name] = len(groups)
                groups.append(name)
        arrays[f"body{k}_co"] = np.array([tuple(point) for point in co], dtype=np.float64).reshape(-1, 3)
        arrays[f"body{k}_dominant"] = np.array([group_index[name] if name is not None else -1 for name in mesh["dominant"]],
                                               dtype=np.int32)
        arrays[f"body{k}_torso"] = np.array(torso, dtype=np.float64).reshape(-1, 2)
        start, verts = [], []
        for polygon in obj.data.polygons:
            start.append(len(verts))
            verts.extend(polygon.vertices)
        arrays[f"body{k}_face_start"] = np.array(start, dtype=np.int32)
        arrays[f"body{k}_face_verts"] = np.array(verts, dtype=np.int32)
        for side in ("left", "right"):
            picked = np.array([i for i, name in enumerate(mesh["dominant"]) if name in foot_bones[side]], dtype=np.int64)
            if len(picked):
                feet[side].append((obj, picked))
        body_meta.append({"name": obj.name, "vertices": len(obj.data.vertices), "faces": len(obj.data.polygons)})

    count = len(bones)
    pose = np.zeros((len(frames), count, 4, 4), dtype=np.float32)
    quat = np.zeros((len(frames), count, 4))
    heads = np.zeros((len(frames), count, 3))
    worlds = np.zeros((len(frames), 4, 4))
    foot_object = np.full((len(frames), 2, 3), np.nan)
    foot_world = np.full((len(frames), 2, 3), np.nan)
    pose_bones = [armature.pose.bones[name] for name in bones]
    bone_index = {name: i for i, name in enumerate(bones)}
    cloth_index = {obj.name: i for i, obj in enumerate(cloth_objects)}
    cloth_rows = []                                                      # (コマの番号, 骨, 布, 数, 深さ, 位置 3, 外向き 3)
    cloth_unwatched = {}                                                 # 体のメッシュ → 頂点の数が変わって見なかったコマ

    def pose_segment(name):
        bone = armature.pose.bones[name]
        return world @ bone.head, world @ bone.tail
    for fi, frame in enumerate(frames):
        scene.frame_set(frame)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        worlds[fi] = np.array(world)                                     # アーマチュアの物体が動いていても、そのコマの置き場所
        if not np.allclose(worlds[fi][:3, :3].T @ worlds[fi][:3, :3], np.eye(3), atol=1e-5):   # 大きさのキーがあっても止める
            raise SystemExit(f"コマ {frame} で、アーマチュアの物体に大きさ（scale）かずれ（shear）があります（長さを m で測れません）")
        for bi, pose_bone in enumerate(pose_bones):
            matrix = pose_bone.matrix
            pose[fi, bi] = np.array(matrix)
            q = matrix.to_quaternion()                                   # 部位そのものの向き（アーマチュアから見て）
            quat[fi, bi] = (q.w, q.x, q.y, q.z)
            heads[fi, bi] = tuple(world @ matrix.translation)
        for si, side in enumerate(("left", "right")):
            by_object = by_world = None
            for obj, picked in feet[side]:
                evaluated = obj.evaluated_get(depsgraph)
                if len(evaluated.data.vertices) != len(obj.data.vertices):
                    continue                                             # 頂点を足し引きする変形の物は見ない（試作と同じ）
                co = np.empty(len(evaluated.data.vertices) * 3)
                evaluated.data.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)[picked]
                matrix = evaluated.matrix_world
                k_object, k_world = lowest_indices(co, np.array(matrix))
                point = matrix @ Vector(co[k_object])                    # 試作と同じ計算（mathutils）
                if by_object is None or point.z < by_object.z:
                    by_object = point
                point = matrix @ Vector(co[k_world])
                if by_world is None or point.z < by_world.z:
                    by_world = point
            if by_object is not None:
                foot_object[fi, si] = tuple(by_object)
                foot_world[fi, si] = tuple(by_world)
        if watch:                                                        # 体 → 布（試作と同じ手順と計算）
            cv, cf, face_object = [], [], []
            for obj in cloth_objects:
                evaluated = obj.evaluated_get(depsgraph)
                base = len(cv)
                matrix = evaluated.matrix_world
                cloth_mesh = evaluated.data                               # 面も変形のあとのメッシュから（頂点を足し引きする変形でも合う）
                cv += [matrix @ vertex.co for vertex in cloth_mesh.vertices]
                cf += [[base + i for i in polygon.vertices] for polygon in cloth_mesh.polygons]
                face_object += [obj.name] * len(cloth_mesh.polygons)
            tree = BVHTree.FromPolygons(cv, cf)
            found = {}
            for obj, picked, names in watch:
                evaluated = obj.evaluated_get(depsgraph)
                if len(evaluated.data.vertices) != len(obj.data.vertices):   # 頂点を足し引きする変形のコマは、番号が合わないので見ない
                    cloth_unwatched.setdefault(obj.name, []).append(frame)
                    continue
                co = np.empty(len(evaluated.data.vertices) * 3)
                evaluated.data.vertices.foreach_get("co", co)
                co = co.reshape(-1, 3)[picked]
                matrix = evaluated.matrix_world
                through_cloth([matrix @ Vector((x, y, z)) for x, y, z in co], names, pose_segment, tree, face_object,
                              settings["cloth_depth_min_m"], lambda: Vector((0.0, 0.0, 0.0)), found)
            for (name, cloth), (number, depth, position_sum, outward_sum) in found.items():
                normal = tuple(outward_sum.normalized()) if outward_sum.length > 1e-6 else (np.nan,) * 3
                cloth_rows.append((fi, bone_index[name], cloth_index[cloth], number, depth, *tuple(position_sum / number), *normal))
    arrays.update(pose=pose, quat=quat, heads=heads, foot_object=foot_object, foot_world=foot_world, world=worlds)
    rows = np.array(cloth_rows, dtype=np.float64).reshape(-1, 11)
    arrays.update(cloth_frame=rows[:, 0].astype(np.int32), cloth_bone=rows[:, 1].astype(np.int32),
                  cloth_object=rows[:, 2].astype(np.int32), cloth_count=rows[:, 3].astype(np.int32), cloth_depth=rows[:, 4],
                  cloth_location=rows[:, 5:8], cloth_normal=rows[:, 8:11])
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "wb") as stream:
        np.savez_compressed(stream, **arrays)
    meta = {"schema_version": SCHEMA, "blender": bpy.app.version_string, "blend": os.path.abspath(a.blend), "scene": scene.name,
            "armature": armature.name, "armature_world": [list(row) for row in world],
            "fps": scene.render.fps / scene.render.fps_base, "frames": frames, "humanoid": humanoid, "bones": bones,
            "parents": parents, "groups": groups, "deform": deform, "body": body_meta,
            "cloth_objects": [obj.name for obj in cloth_objects], "cloth_measured": look_at_cloth,
            "cloth_watched": int(sum(len(picked) for _, picked, _ in watch)), "cloth_tucked": tucked, "cloth_unwatched": cloth_unwatched,
            "hair_objects": [m["object"].name for m in meshes if m["kind"] == "hair"],
            "foot_objects": {side: [obj.name for obj, _ in feet[side]] for side in feet}}
    with open(a.meta, "w", encoding="utf-8") as stream:
        json.dump(meta, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"frames": len(frames), "bones": count, "body": len(body_meta)}))


if __name__ == "__main__":
    main()
