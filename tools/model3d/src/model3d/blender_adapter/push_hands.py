"""Blender の中で動かす: 手が体（胴・腰まわり・脚）に沈むコマで、腕を肩から少し回して、手を体の面から浮かせる。

    blender --background --factory-startup --python push_hands.py -- --blend in.blend --out-blend out.blend --report report.json \
        [--bone-prefix mixamorig:] [--body-object NAME ...] [--passes 2] [--gap-mm 6] [--reach-mm 40] [--cap-mm 40]

- 手の点: 手の骨（Hand）とその先の骨（指）の重みがいちばん大きい頂点（左右別）。
- 体の点: 腰の骨から下がる骨のうち、首・肩・足から先を除いた骨（腰・背骨・太もも・すねと、その下に足した骨）の重みが
  いちばん大きい頂点。--body-object で選んだメッシュの全頂点にもできる。重みは骨の名前の頂点グループだけを見る。
  骨の重みで選ぶときは、レンダーに出すメッシュだけを見る（物体とコレクションの hide_render、ビューレイヤーでの除外を見る）。
- コマごとに、手の点にいちばん近い体の点を探し、体の面の法線の向きに測った距離が gap より小さい点の足りない量を測る
  （reach より遠い点は数えない）。2 mm より足りない点が 15 未満なら、手のひらの丸みの誤差とみなして動かさない。
- 足りない量で重みをつけた法線の平均の向きに、足りない量（向きの傾きで割り増し）の 98% 点だけ手首を動かす（1 回に cap まで）。
- コマの前後でならす: 前後 3 コマでいちばん大きい量を広げてから、σ 1.5 コマのガウス（±3σ）でならす。
- 二の腕の骨を肩のまわりに回して、手首をその量の腕に垂直な分だけ動かす（肘から先の曲げと手の形は変えない）。
  回しても手首は腕に沿っては動かないので、腕に沿う分は除いて角度を決める（試作は量の長さ全部で角度を決めていたので、
  腕に沿う押しのわずかな横の分の向きに、手首を大きく振ることがあった）。
- これを --passes 回くり返し、最後に測り直す。体の面の内側へ 2 mm より深く入った手の点を、直す前・後で数えて報告に残す。
コマは .blend のシーンの開始〜終了。骨の名前は --bone-prefix + Mixamo の名前。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じ点から、同じ動かす量になることを確かめた。骨の回し方は、
腕に沿う分を除くところだけ試作と違う（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

SIDES = ("Left", "Right")
BODY_STOP = ("Neck", "LeftShoulder", "RightShoulder", "LeftFoot", "RightFoot")   # 体から除く骨（とその下）
NOISE, MIN_POINTS = 0.002, 15     # この深さ（m）より足りない点がこの数より少なければ動かさない
WINDOW, SIGMA = 3, 1.5            # ならし: 前後のコマ数でいちばん大きい量を広げ、このガウスでならす
MOVE_MIN = 0.0005                 # これより小さい動き（m）は回さない
INSIDE, CROWD = 0.002, 50         # 報告: 面の内側へこの深さ（m）より入った手の点が、この数より多いコマを数える


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="push_hands.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--bone-prefix", default="mixamorig:")
    ap.add_argument("--body-object", action="append", default=[], help="体のメッシュの名前（何度でも）。既定は骨の重みで選ぶ")
    ap.add_argument("--passes", type=int, default=2, help="測って回すのを何回くり返すか")
    ap.add_argument("--gap-mm", type=float, default=6.0, help="体の面から離す距離（輪郭線が見えるように）")
    ap.add_argument("--reach-mm", type=float, default=40.0, help="体の面からこの距離までの手の点を数える")
    ap.add_argument("--cap-mm", type=float, default=40.0, help="1 回に手首を動かす上限")
    return ap.parse_args(argv)


def subtree(parents, root, stop=()):
    """root から下がる骨の名前（stop の骨とその下は除く）。parents は {骨の名前: 親の名前か None}。"""
    children = {}
    for name, parent in parents.items():
        children.setdefault(parent, []).append(name)
    names, todo = set(), [root]
    while todo:
        name = todo.pop()
        if name in stop or name not in parents:
            continue
        names.add(name)
        todo.extend(children.get(name, []))
    return names


def gaussian(sigma):
    radius = max(1, math.ceil(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return kernel / kernel.sum()


def spread_and_smooth(need, window=WINDOW, sigma=SIGMA):
    """コマごとの手首を動かす量（F×3）を、前後 window コマでいちばん大きいものに広げてから、ガウスでならす。
    （ならした後に 1 コマだけ足りない分を戻すと、そのコマで腕が跳ねた）"""
    need = np.asarray(need, float)
    raw = np.linalg.norm(need, axis=1)
    wide = np.zeros_like(need)
    for f in range(len(need)):
        lo, hi = max(0, f - window), min(len(need), f + window + 1)
        wide[f] = need[lo + int(raw[lo:hi].argmax())]
    kernel = gaussian(sigma)
    radius = len(kernel) // 2
    return np.stack([np.convolve(np.pad(wide[:, j], radius, mode="edge"), kernel, mode="valid") for j in range(3)], 1)


def signed_gap(points, body, normals, index):
    """手の点が、いちばん近い体の点から法線の向きにどれだけ外にあるか（m。負は内側）。"""
    return np.einsum("ij,ij->i", points - body[index], normals[index])


def push_vector(gap_now, normals, index, distance, gap, reach, cap):
    """手首を動かす量（m のベクトル）と、いちばん深い足りなさ（m）。動かさないときは (None, 0.0)。"""
    lack = np.where((gap_now < gap) & (distance < reach), gap - gap_now, 0.0)
    if (lack > NOISE).sum() < MIN_POINTS:            # ほんの数点は手のひらの丸みの誤差
        return None, 0.0
    m = lack > 0
    toward = (normals[index[m]] * lack[m, None]).sum(0)
    toward /= max(np.linalg.norm(toward), 1e-9)
    cosine = np.maximum(normals[index[m]] @ toward, 0.3)
    required = float(np.percentile(lack[m] / cosine, 98))
    return toward * min(required, cap), float((gap - gap_now[m]).max())


def around(body, points, reach):
    """手の点の囲み（各軸の最小〜最大）を広げた中にある体の点の印。広げる幅は reach 以上（既定の 40 mm では試作と同じ
    5 cm）なので、どれかの手の点から reach より近い体の点は落とさない（いちばん近い点の探し方も変わらない）。"""
    pad = max(0.05, reach)
    return np.all((body > points.min(0) - pad) & (body < points.max(0) + pad), axis=1)


def inside(gap_now, distance, reach):
    """体の面の内側へ INSIDE より深く入った手の点の数と、いちばん深い深さ（m）。"""
    hit = (gap_now < -INSIDE) & (distance < reach)
    return int(hit.sum()), float(-gap_now[hit].min()) if hit.any() else 0.0


def swing(arm, move, limit=0.5):
    """肩 → 手首の向き arm の手首を move だけ動かしたいときの、肩のまわりに回す角度（ラジアン）。
    回すと手首は腕に垂直な向きにしか動かないので、move の腕に沿う分を除いた長さで角度を決める（腕に沿う押しで、わずかな
    横の分の向きに大きく振らない。試作は move の長さ全部で決めていた）。sin(角度) は limit まで（試作と同じ 30°）。"""
    length2 = sum(a * a for a in arm)
    along = sum(m * a for m, a in zip(move, arm)) / length2
    across = math.sqrt(sum((m - along * a) ** 2 for m, a in zip(move, arm)))
    return math.asin(min(across / math.sqrt(length2), limit))


def summary(frames, count, deepest):
    return {s: {"frames_crowded": int((count[s] > CROWD).sum()), "points_inside_max": int(count[s].max()),
                "deepest_mm": round(float(deepest[s].max()) * 1000, 1), "deepest_frame": int(frames[int(deepest[s].argmax())])}
            for s in count}


def rendered(ob, view_layers):
    """ob がレンダーに出るか。物体の hide_render と、ob の入ったコレクションまでたどる道（どれか 1 つ）の、コレクションの
    hide_render とビューレイヤーでの除外を見る。使うビューレイヤーのどれかで出れば出る"""
    if ob.hide_render:
        return False

    def walk(layer):
        if layer.exclude or layer.collection.hide_render:
            return False
        return ob.name in layer.collection.objects or any(walk(child) for child in layer.children)

    return any(walk(vl.layer_collection) for vl in view_layers if vl.use)


# ---------- ここから下は Blender の中でだけ動く ----------

def select_by_weight(arm, prefix, body_objects=()):
    """骨の重みで、体の点（[(メッシュの名前, 頂点の番号か None)]）と左右の手の点（{side: [(名前, 番号)]}）を選ぶ。"""
    import bpy
    parents = {b.name: (b.parent.name if b.parent else None) for b in arm.data.bones}
    hand_bones = {side: subtree(parents, prefix + side + "Hand") for side in SIDES}
    body_bones = subtree(parents, prefix + "Hips", stop={prefix + n for n in BODY_STOP})
    hands, body = {side: [] for side in SIDES}, []
    layers = bpy.context.scene.view_layers
    for o in bpy.data.objects:
        if o.type != "MESH" or not rendered(o, layers):   # レンダーに出さないメッシュ（当たりの写し、コレクションで隠した衣装など）は選ばない
            continue
        groups = {g.index: g.name for g in o.vertex_groups if g.name in parents}   # 骨の名前の重みだけを見る
        if not groups:
            continue
        dominant = np.full(len(o.data.vertices), "", dtype=object)
        for v in o.data.vertices:
            best, which = 0.0, None
            for g in v.groups:
                if g.group in groups and g.weight > best:
                    best, which = g.weight, groups[g.group]
            if which is not None:
                dominant[v.index] = which
        for side in SIDES:
            index = np.where(np.isin(dominant, list(hand_bones[side])))[0]
            if len(index):
                hands[side].append((o.name, index))
        if not body_objects:
            index = np.where(np.isin(dominant, list(body_bones)))[0]
            if len(index):
                body.append((o.name, index))
    for name in body_objects:
        o = bpy.data.objects.get(name)
        if o is None or o.type != "MESH":
            raise SystemExit(f"体のメッシュがありません: {name}")
        body.append((name, None))
    for side, chosen in hands.items():
        if not chosen:
            raise SystemExit(f"{side} の手の骨の重みがいちばん大きい頂点がありません")
    if not body:
        raise SystemExit("体の骨の重みがいちばん大きい頂点がありません（--body-object で体のメッシュを選んでください）")
    return body, hands


def quiet_other_meshes(frame, body, hands):
    """測らないメッシュの変形を止める（速くする）。番号で選んだメッシュは、頂点の数を変えるモディファイアを止める。
    戻り: 元の表示（保存の前に戻す）と、止めたモディファイアの一覧。"""
    import bpy
    measured = {name for name, _ in body} | {name for chosen in hands.values() for name, _ in chosen}
    original = {(o.name, m.name): m.show_viewport for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers}
    for o in bpy.data.objects:
        if o.type == "MESH" and o.name not in measured:
            for m in o.modifiers:
                m.show_viewport = False
    bpy.context.scene.frame_set(frame)
    dg = bpy.context.evaluated_depsgraph_get()
    off = []
    indexed = {name for name, index in body + [c for chosen in hands.values() for c in chosen] if index is not None}
    for name in sorted(indexed):
        o = bpy.data.objects[name]
        ev = o.evaluated_get(dg)
        count = len(ev.to_mesh().vertices)
        ev.to_mesh_clear()
        if count != len(o.data.vertices):
            for m in o.modifiers:
                if m.type != "ARMATURE" and m.show_viewport:
                    m.show_viewport = False
                    off.append([name, m.name])
    return original, off


def restore_view(original):
    import bpy
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = original[(o.name, m.name)]


def evaluated(name, dg, cache):
    """メッシュの、変形した後の頂点と法線（世界の座標、単精度）。1 コマの中では cache で使い回す。"""
    import bpy
    if name not in cache:
        ev = bpy.data.objects[name].evaluated_get(dg)
        me = ev.to_mesh()
        n = len(me.vertices)
        co = np.zeros(n * 3, np.float32)
        me.vertices.foreach_get("co", co)
        nr = np.zeros(n * 3, np.float32)
        me.vertex_normals.foreach_get("vector", nr)
        ev.to_mesh_clear()
        co, nr = co.reshape(-1, 3), nr.reshape(-1, 3)
        M = np.array(ev.matrix_world)
        if not np.array_equal(M, np.eye(4)):         # 単位行列ならそのまま（試作と同じ値になる）
            L = M[:3, :3]
            co = (co @ L.T + M[:3, 3]).astype(np.float32)
            nr = nr @ np.linalg.inv(L)
            nr = (nr / np.maximum(np.linalg.norm(nr, axis=1, keepdims=True), 1e-12)).astype(np.float32)
        cache[name] = (co, nr)
    return cache[name]


def measure(frames, body, hands, gap, reach, cap):
    """コマごと・左右ごとの、手首を動かす量（ならす前）・いちばん深い足りなさ・内側の手の点の数・いちばん深い内側。"""
    import bpy
    from mathutils.kdtree import KDTree
    sc = bpy.context.scene
    need = {s: np.zeros((len(frames), 3)) for s in hands}
    depth = {s: np.zeros(len(frames)) for s in hands}
    count = {s: np.zeros(len(frames), int) for s in hands}
    deepest = {s: np.zeros(len(frames)) for s in hands}
    for k, f in enumerate(frames):
        sc.frame_set(f)
        dg = bpy.context.evaluated_depsgraph_get()
        cache = {}
        parts = [(evaluated(name, dg, cache), index) for name, index in body]
        bc_all = np.vstack([co if index is None else co[index] for (co, _), index in parts])
        bn_all = np.vstack([nr if index is None else nr[index] for (_, nr), index in parts])
        for side, chosen in hands.items():
            p = np.vstack([evaluated(name, dg, cache)[0][index] for name, index in chosen])
            near = around(bc_all, p, reach)              # 手のまわりだけで木を作る
            bc, bn = bc_all[near], bn_all[near]
            if len(bc) == 0:
                continue
            tree = KDTree(len(bc))
            for j, q in enumerate(bc):
                tree.insert(q.tolist(), j)
            tree.balance()
            hit = [tree.find(q.tolist()) for q in p]
            index = np.array([h[1] for h in hit])
            distance = np.array([h[2] for h in hit])
            s = signed_gap(p, bc, bn, index)
            vector, depth[side][k] = push_vector(s, bn, index, distance, gap, reach, cap)
            if vector is not None:
                need[side][k] = vector
            count[side][k], deepest[side][k] = inside(s, distance, reach)
    return need, depth, count, deepest


def rotate_arms(arm, prefix, frames, moves):
    """二の腕の骨を肩のまわりに回して、手首を moves（{side: F×3、世界の m}）だけ動かし、回転のキーを打つ。"""
    import bpy
    from mathutils import Matrix, Vector
    sc, vl = bpy.context.scene, bpy.context.view_layer
    to_armature = arm.matrix_world.to_3x3().inverted()
    for side in SIDES:
        upper, hand = arm.pose.bones[prefix + side + "Arm"], arm.pose.bones[prefix + side + "Hand"]
        prev = None
        for k, f in enumerate(frames):
            sc.frame_set(f)
            vl.update()
            move = Vector(moves[side][k].tolist())
            if move.length > MOVE_MIN:
                dv = to_armature @ move
                S, W = upper.head.copy(), hand.head.copy()
                r = W - S
                ax = r.cross(dv)
                angle = swing(tuple(r), tuple(dv))
                if ax.length > 1e-9 and angle > 0:
                    R = Matrix.Rotation(angle, 4, ax.normalized())
                    upper.matrix = Matrix.Translation(S) @ R @ Matrix.Translation(-S) @ upper.matrix
                    vl.update()
            q = upper.rotation_quaternion.copy()
            if prev is not None and q.dot(prev) < 0:
                q = -q
                upper.rotation_quaternion = q
            prev = q.copy()
            upper.keyframe_insert("rotation_quaternion", frame=f)


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    if a.passes < 1 or not (a.gap_mm >= 0 and a.reach_mm > 0 and a.cap_mm > 0):
        raise SystemExit("--passes は 1 以上、--gap-mm は 0 以上、--reach-mm と --cap-mm は 0 より大きくしてください")
    gap, reach, cap = a.gap_mm / 1000, a.reach_mm / 1000, a.cap_mm / 1000
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    for side in SIDES:
        for name in ("Arm", "Hand"):
            if a.bone_prefix + side + name not in arm.pose.bones:
                raise SystemExit(f"骨がありません（--bone-prefix を確かめてください）: {a.bone_prefix + side + name}")
        upper = arm.pose.bones[a.bone_prefix + side + "Arm"]
        if upper.rotation_mode != "QUATERNION":
            raise SystemExit(f"{upper.name} の回転が四元数ではありません（{upper.rotation_mode}）")
    frames = list(range(sc.frame_start, sc.frame_end + 1))
    body, hands = select_by_weight(arm, a.bone_prefix, a.body_object)
    original, modifiers_off = quiet_other_meshes(frames[0], body, hands)
    passes, before = [], None
    for _ in range(a.passes):
        need, depth, count, deepest = measure(frames, body, hands, gap, reach, cap)
        if before is None:
            before = summary(frames, count, deepest)
        moves = {s: spread_and_smooth(need[s]) for s in need}
        passes.append({s: {"frames_moved": int((np.linalg.norm(moves[s], axis=1) > MOVE_MIN).sum()),
                           "largest_move_mm": round(float(np.linalg.norm(moves[s], axis=1).max()) * 1000, 1),
                           "deepest_lack_mm": round(float(depth[s].max()) * 1000, 1)} for s in moves})
        rotate_arms(arm, a.bone_prefix, frames, moves)
    _, _, count, deepest = measure(frames, body, hands, gap, reach, cap)
    after = summary(frames, count, deepest)
    restore_view(original)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-push-hands/0.1", "blender": bpy.app.version_string, "frames": [frames[0], frames[-1]],
              "bone_prefix": a.bone_prefix,
              "selection": {"body_from": "body_objects" if a.body_object else "bone_weights",
                            "body": {name: (None if index is None else int(len(index))) for name, index in body},
                            "hands": {s: {name: int(len(index)) for name, index in chosen} for s, chosen in hands.items()},
                            "modifiers_off": modifiers_off},
              "settings": {"passes": a.passes, "gap_mm": a.gap_mm, "reach_mm": a.reach_mm, "cap_mm": a.cap_mm},
              "passes": passes, "inside": {"before": before, "after": after}}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"frames": len(frames), "passes": passes, "inside": report["inside"]}))


if __name__ == "__main__":
    main()
