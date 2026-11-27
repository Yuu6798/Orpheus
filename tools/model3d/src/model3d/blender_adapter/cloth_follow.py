"""Blender の中で動かす: 上に重なる布（腰の垂れなど）を、下の布（裾など）の表面と同じ動きにする（骨の重みを、すぐ下の布の重みで置きかえる）
（cloth.follow）。

    blender --background --factory-startup --python cloth_follow.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 帯のすぐ下の模様のある垂れは、裾とは別の揺れものの骨で動き、揺れものの計算には
垂れと裾の当たりが無いので、動くと垂れが真下へ垂れて、外へ広がった裾の中へ沈んだ（全コマで、垂れの頂点の 1 コマ平均 163 個が裾の内側）。

やり方: 初期姿勢で、上の布（follower）の頂点ごとに、下の布（leader）の「いちばん外の面」の頂点を k 個さがし、その骨の重みを距離で混ぜた値
（重み × 1 / (距離² + 1e-6)）で、上の布の骨の重みを置きかえる。上の布は、すぐ下の布の表面に付いた布のように動く（間は初期姿勢の形のまま）。
上の布自身の揺れものの骨は使わなくなる。1 頂点に付ける骨は max_influences 本まで（重みの大きい順に残して合計を 1 に直す。VRM は 4 本まで）。
下の布の「いちばん外の面」は、縦の軸（axis_bone の付け根を通る）から頂点へ水平に線を引き、外側の layer_gap 以内に下の布の面が無い頂点。
骨の重みは、アーマチュアの骨の名前の頂点グループだけを読み・書く（選択などのグループは変えない）。踊りの .blend にもモデルの .blend にも使える。
include_groups・exclude_groups で follower の頂点を選べる。元の重みが正の頂点で選び、除外を優先する。

報告は model3d-cloth-follow/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ重みになることを
確かめた（docs/model3d.md）。CI には Blender が無い（重みの混ぜ方と切り方はテストする）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .cloth_layers import mesh_problem
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cloth_layers import mesh_problem
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"follower": None, "leader": None, "armature": None, "axis_bone": None, "k": 6, "max_influences": 4, "layer_gap": 0.015}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_follow.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def blend_weights(neighbours, limit):
    """近い下の布の頂点 [(距離, [(骨, 重み)])] から、上の布の 1 頂点の骨の重み。距離の 2 乗の逆数で混ぜ、大きい順に limit 本までにして
    合計を 1 に直す。戻り: ({骨: 重み}（大きい順）か None（重みが無い）, 切ったか, 切った割合)。台本と同じ計算の順。"""
    mixed, total = {}, 0.0
    for dist, items in neighbours:
        w = 1.0 / (dist * dist + 1e-6)
        for name, weight in items:
            if weight > 0:
                mixed[name] = mixed.get(name, 0.0) + weight * w
                total += weight * w
    if total <= 0:
        return None, False, 0.0
    kept = sorted(mixed.items(), key=lambda kv: -kv[1])
    dropped = sum(value for _, value in kept[limit:]) / total
    cut = len(kept) > limit
    kept = kept[:limit]
    kept_total = sum(value for _, value in kept)
    return {name: value / kept_total for name, value in kept}, cut, dropped


def selected_vertices(obj, include_groups, exclude_groups):
    """元のグループで範囲を決める。除外は、含めるグループと重なる頂点にも効く。"""
    if not include_groups and not exclude_groups:
        return list(obj.data.vertices)
    group_ids = {group.name: group.index for group in obj.vertex_groups}
    unknown = (set(include_groups) | set(exclude_groups)).difference(group_ids)
    if unknown:
        raise SystemExit(f"{obj.name} の follower に頂点グループがありません: {sorted(unknown)}")
    include = {group_ids[name] for name in include_groups}
    exclude = {group_ids[name] for name in exclude_groups}
    selected = []
    for vertex in obj.data.vertices:
        memberships = {group.group for group in vertex.groups if group.weight > 0}
        if (not include or include & memberships) and not exclude & memberships:
            selected.append(vertex)
    if not selected:
        raise SystemExit(f"{obj.name} の include_groups / exclude_groups で選ばれた頂点がありません")
    return selected


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector, kdtree
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
    follower, leader = bpy.data.objects.get(s["follower"]), bpy.data.objects.get(s["leader"])
    for label, obj, name in (("follower", follower, s["follower"]), ("leader", leader, s["leader"])):
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"{label} のメッシュがありません: {name}")
    if problem := mesh_problem(follower):                # 重みを書きかえる（共有しているメッシュは全部に効く）
        raise SystemExit(problem)
    if s["axis_bone"] not in armature.data.bones:
        raise SystemExit(f"{armature.name} に縦の軸の骨 {s['axis_bone']} がありません（axis_bone）")
    include_groups, exclude_groups = s.get("include_groups", []), s.get("exclude_groups", [])
    masked = bool(include_groups or exclude_groups)
    selected = selected_vertices(follower, include_groups, exclude_groups)
    saved = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    bones = {b.name for b in armature.data.bones}
    axis = armature.matrix_world @ armature.data.bones[s["axis_bone"]].head_local

    # 下の布の、いちばん外の面の頂点
    lco = [leader.matrix_world @ v.co for v in leader.data.vertices]
    leader.data.calc_loop_triangles()
    tree = BVHTree.FromPolygons(lco, [tuple(t.vertices) for t in leader.data.loop_triangles])
    outer = []
    for i, p in enumerate(lco):
        origin = Vector((axis.x, axis.y, p.z))
        d = p - origin
        if d.length < 1e-4:
            continue
        d.normalize()
        hit = tree.ray_cast(p + d * 0.0003, d, s["layer_gap"])
        if hit[0] is None:
            outer.append(i)
    if not outer:
        raise SystemExit(f"{leader.name} に、いちばん外の面の頂点がありません（axis_bone と layer_gap を確かめてください）")
    kd = kdtree.KDTree(len(outer))
    for i in outer:
        kd.insert(lco[i], i)
    kd.balance()

    leader_groups = {g.index: g.name for g in leader.vertex_groups if g.name in bones}
    plan, far, over_limit, most_dropped = [], 0.0, 0, 0.0
    for v in selected:
        p = follower.matrix_world @ v.co
        neighbours = []
        for _, index, dist in kd.find_n(p, s["k"]):
            far = max(far, dist)
            neighbours.append((dist, [(leader_groups[g.group], g.weight) for g in leader.data.vertices[index].groups if g.group in leader_groups]))
        weights, cut, dropped = blend_weights(neighbours, s["max_influences"])
        if weights is None:
            continue
        if cut:
            over_limit += 1
            most_dropped = max(most_dropped, dropped)
        plan.append((v.index, weights))
    # 全体を追従させるときは従来のグループ順にそろえ、範囲指定時は使うグループだけ作る。
    transfer_groups = set(leader_groups.values())
    if masked:
        transfer_groups = {name for _, weights in plan for name, value in weights.items() if value >= 0.001}
    for g in leader.vertex_groups:
        if g.name in transfer_groups and follower.vertex_groups.get(g.name) is None:
            follower.vertex_groups.new(name=g.name)
    own = [g for g in follower.vertex_groups if g.name in bones]
    reweighted = [i for i, _ in plan]
    reweighted_ids = set(reweighted)
    before, after = {}, {}
    for v in follower.data.vertices:
        for g in v.groups:
            name = follower.vertex_groups[g.group].name
            if name in bones and g.weight > 0.01:
                before[name] = before.get(name, 0) + 1
                if masked and v.index not in reweighted_ids:
                    after[name] = after.get(name, 0) + 1
    for g in own:
        g.remove(reweighted)
    for i, weights in plan:
        for name, value in weights.items():
            if value < 0.001:
                continue
            follower.vertex_groups[name].add([i], value, "REPLACE")
            if value > 0.01:
                after[name] = after.get(name, 0) + 1
    armature.data.pose_position = saved
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report = {"schema_version": "model3d-cloth-follow/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "follower": follower.name, "leader": leader.name, "follower_verts": len(follower.data.vertices), "reweighted": len(plan),
              "leader_outer_verts": len(outer), "farthest_neighbour_mm": round(far * 1000, 1), "verts_cut_to_max_influences": over_limit,
              "largest_weight_share_cut": round(most_dropped, 3), "bones_before": dict(sorted(before.items(), key=lambda kv: -kv[1])),
              "bones_after": dict(sorted(after.items(), key=lambda kv: -kv[1])), "settings": s}
    if masked:
        report.update({"follower_selected_verts": len(selected), "follower_excluded_verts": len(follower.data.vertices) - len(selected)})
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "reweighted": len(plan)}))


if __name__ == "__main__":
    main()
