"""Blender の中で動かす: 左右の腕（前腕・袖口・手・指）のメッシュが、互いの中に入り込んでいるコマを、変形した後の形で直接調べる
（check.arm_overlap。測るだけで、.blend は変えない）。

    blender --background --factory-startup --python arm_overlap.py -- --blend dance.blend --settings settings.json --report raw.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 腕を「太さのある棒の集まり」とみなして測ると、袖口のフリルや手のひらの厚みなど、
棒で表せていない部分の重なりを見落とした（棒の測り方では重なりが出ないコマで、絵では腕が入り込んでいた）。描かれる形そのもの（骨で変形した後の
メッシュ。体のモディファイアも効かせる）で確かめる。

測り方:
  - 体のメッシュの頂点を、いちばん重みの大きい骨（aliases の頂点グループは、その骨の重みに足してから比べる。足したねじれの骨を元の骨の
    仲間にするため）で、2 本の腕（arms の bones）に分ける。比べるのは、変形に使う骨の頂点グループだけ（押し出しの選択などのグループは
    数えない）。all_groups が true なら、台本と同じく全部の頂点グループで比べる（台本は、袖口の押し出しの選択のグループが勝った袖の頂点を
    腕から外していた）。
  - 前腕（forearm）がいちばん重い頂点は、初期姿勢で前腕の付け根から手首（wrist の骨の付け根）へ向かう位置 t が sleeve_until より手前なら
    「袖」、ほかは「肌」。手と指は肌。面は、3 つの頂点が同じ腕のものだけを、その腕の面にする（2 つ以上が袖なら袖の面）。
  - コマごとに、片方の腕の頂点が、もう片方の腕の面の内側にあるかを調べる。2 つの見方の両方で内側と出た頂点だけを数える:
      1. いちばん近い面（near 以内）の裏側に depth より深くある。
      2. その点から 14 の決まった向きへ伸ばした線が面を奇数回横切る向きが vote の割合以上ある（閉じた形の中にいる）。
    1 だけだと、爪の先や手のふちのような鋭い角のそばで、外にある点まで裏側と出る（台本の最初の版は、両手が 10 cm 離れたコマで
    「147 頂点が 39 mm 入っている」と出た）。2 だけだと、フリルの内側のすき間にある点まで数える。深さは、いちばん近い面までの距離。
  - 初期姿勢でも同じ判定をして、もともと内側と出る頂点（面の向きが乱れている所）は数から除く。
  - 入り込みは「肌が肌に」「袖が肌に」「肌が袖に」「袖が袖に」の 4 つに分けて、頂点の数といちばん深い量を数える。

報告（raw）は model3d-arm-overlap/0.1: 腕ごとの頂点の数、初期姿勢で除いた頂点の数、コマごとの 4 つの数と深さ（mm、0.1 に丸める。台本と同じ）。
要約（重なりのあるコマの数）は check.arm_overlap がする。
**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
CI には Blender が無い（いちばん重い骨の選び方と、袖の分け方・面の分け方はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .stage_render import sync_to_render
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stage_render import sync_to_render
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"body": None, "arms": [], "aliases": {}, "frames": None, "armature": None, "depth": 0.002,
            "near": 0.04, "vote": 0.7, "all_groups": False}
RAY_SEED, RAY_COUNT, RAY_STEPS = 11, 14, 64           # 線を伸ばす向き（決まった乱数）と、1 本の線で数える面の上限（台本と同じ）
KINDS = ("skin_in_skin", "sleeve_in_skin", "skin_in_sleeve", "sleeve_in_sleeve")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="arm_overlap.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def dominant(items, aliases):
    """1 頂点の [(頂点グループの名前, 重み)] の、いちばん重い名前（aliases の名前は、その先の名前の重みに足してから比べる。同じ重みは先に
    出てきた名前。無ければ ""）。台本と同じ決め方。"""
    totals = {}
    for name, weight in items:
        name = aliases.get(name, name)
        totals[name] = totals.get(name, 0.0) + weight
    return max(totals, key=totals.get) if totals else ""


def triangle_sides(tris, side_of, sleeve):
    """三角形 (T, 3) ごとの腕（3 つの頂点が同じ腕なら 1 か 2、ほかは 0）と、袖の面か（2 つ以上の頂点が袖）。台本と同じ。"""
    side = np.where((side_of[tris] == 1).all(1), 1, np.where((side_of[tris] == 2).all(1), 2, 0))
    return side, sleeve[tris].sum(1) >= 2


def rays():
    """内外を決める線の向き（単位の向き 14 本。決まった乱数。台本と同じ）。"""
    rng = np.random.default_rng(RAY_SEED)
    return [v / np.linalg.norm(v) for v in rng.normal(size=(RAY_COUNT, 3))]


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        s = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not s["armature"] or obj.name == s["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + s['armature'] + '）' if s['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:          # 距離の設定（m）と Blender 単位を合わせる
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    body = bpy.data.objects.get(s["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"体のメッシュがありません: {s['body']}")
    for arm in s["arms"]:
        for key in ("forearm", "wrist"):
            if arm[key] not in armature.data.bones:
                raise SystemExit(f"{armature.name} に骨 {arm[key]} がありません（arms の {arm['name']} の {key}）")
    for obj in bpy.data.objects:                         # 体のほかのメッシュは要らない（コマ送りを速くする。台本と同じ）
        if obj.type == "MESH" and obj is not body:
            obj.hide_viewport = True
    synced = sync_to_render(body)                        # 描く形で測る: 表示と描画でモディファイアの効き方が違えば描画にそろえる（保存しない）

    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    names = {group.index: group.name for group in body.vertex_groups if s["all_groups"] or group.name in deform}
    count = len(body.data.vertices)
    side_of = np.zeros(count, dtype=int)                 # 0 ＝腕でない、1 ＝ arms[0]、2 ＝ arms[1]
    part_of = [""] * count
    members = [set(arm["bones"]) for arm in s["arms"]]
    for vertex in body.data.vertices:
        if not vertex.groups:
            continue
        name = dominant([(names[item.group], item.weight) for item in vertex.groups if item.group in names], s["aliases"])
        for k, bones in enumerate(members):
            if name in bones:
                side_of[vertex.index] = k + 1
                part_of[vertex.index] = name
    sleeve = np.zeros(count, dtype=bool)                 # 袖か肌か（初期姿勢で、前腕の骨に沿った位置から。台本と同じ単精度の式）
    world_body = np.array(body.matrix_world)
    world_armature = np.array(armature.matrix_world)
    for arm in s["arms"]:
        head = world_armature[:3, :3] @ np.array(armature.data.bones[arm["forearm"]].head_local) + world_armature[:3, 3]
        wrist = world_armature[:3, :3] @ np.array(armature.data.bones[arm["wrist"]].head_local) + world_armature[:3, 3]
        axis = wrist - head
        for vertex in body.data.vertices:
            if part_of[vertex.index] == arm["forearm"]:
                t = float((world_body[:3, :3] @ np.array(vertex.co) + world_body[:3, 3] - head) @ axis / (axis @ axis))
                sleeve[vertex.index] = t < s["sleeve_until"]
    body.data.calc_loop_triangles()
    tris = np.array([t.vertices[:] for t in body.data.loop_triangles])
    base_loops = np.empty(len(body.data.loops), dtype=np.int64)       # 面のつながり（面ごとの頂点の並び）。変形した後と比べる
    body.data.loops.foreach_get("vertex_index", base_loops)
    base_faces = len(body.data.polygons)
    tri_side, tri_sleeve = triangle_sides(tris, side_of, sleeve)
    directions = [Vector(d) for d in rays()]

    def positions():
        evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        loops = np.empty(len(mesh.loops), dtype=np.int64)
        mesh.loops.foreach_get("vertex_index", loops)
        verts, faces = len(mesh.vertices), len(mesh.polygons)
        if verts != count or faces != base_faces or not np.array_equal(loops, base_loops):
            evaluated.to_mesh_clear()                 # 頂点の番号と元のメッシュの三角形で測るので、つながりを変えるモディファイアは止める
            raise SystemExit(f"{body.name} のモディファイアが頂点や面のつながりを変えています（頂点 {count} → {verts}、"
                             f"面 {base_faces} → {faces}）。細分化・三角形化などを止めた .blend で測ってください")
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        evaluated.to_mesh_clear()
        matrix = np.array(evaluated.matrix_world)
        return co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]

    def crossings(tree, point, direction):
        hits, origin = 0, point
        for _ in range(RAY_STEPS):
            location = tree.ray_cast(origin, direction)[0]
            if location is None:
                break
            hits += 1
            origin = location + direction * 1e-5
        return hits

    def inside(points, src, dst):
        """src の腕の頂点のうち、dst の腕の面の内側にあるもの: {頂点の番号: (深さ, 相手の面が袖か)}。"""
        faces = tris[tri_side == dst]
        face_sleeve = tri_sleeve[tri_side == dst]
        used = np.unique(faces)
        remap = {int(v): i for i, v in enumerate(used)}
        tree = BVHTree.FromPolygons([Vector(points[v]) for v in used], [[remap[int(v)] for v in face] for face in faces])
        out = {}
        for v in np.where(side_of == src)[0]:
            point = Vector(points[v])
            location, normal, index, distance = tree.find_nearest(point, s["near"])
            if location is None:
                continue
            if -(point - location).dot(normal) <= s["depth"]:
                continue
            odd = sum(1 for direction in directions if crossings(tree, point, direction) % 2 == 1)
            if odd >= s["vote"] * len(directions):
                out[int(v)] = (float(distance), bool(face_sleeve[index]))
        return out

    saved = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    rest = positions()
    ignore = {1: set(inside(rest, 1, 2)), 2: set(inside(rest, 2, 1))}
    armature.data.pose_position = saved
    first, last = s["frames"] or (scene.frame_start, scene.frame_end)
    rows = []
    for frame in range(int(first), int(last) + 1):
        scene.frame_set(frame)
        points = positions()
        kinds = {key: [0, 0.0] for key in KINDS}
        for src, dst in ((1, 2), (2, 1)):
            for v, (depth, face_is_sleeve) in inside(points, src, dst).items():
                if v in ignore[src]:
                    continue
                key = ("sleeve" if sleeve[v] else "skin") + "_in_" + ("sleeve" if face_is_sleeve else "skin")
                kinds[key][0] += 1
                kinds[key][1] = max(kinds[key][1], depth)
        rows.append({"frame": frame, **{key: [n, round(depth * 1000, 1)] for key, (n, depth) in kinds.items()}})
    report = {"schema_version": "model3d-arm-overlap/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "body": body.name, "modifiers_synced": synced, "arms": {arm["name"]: {"verts": int((side_of == k + 1).sum()), "sleeve_verts": int((sleeve & (side_of == k + 1)).sum()),
                                                        "rest_ignored": len(ignore[k + 1])} for k, arm in enumerate(s["arms"])},
              "rows": rows}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(rows), "arms": report["arms"]}))


if __name__ == "__main__":
    main()
