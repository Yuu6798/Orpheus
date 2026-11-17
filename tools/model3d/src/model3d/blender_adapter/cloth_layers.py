"""布の層と、骨に沿った閉じた筒の当たりの部品（cloth.pushout・cloth.lining_gap・cloth.follow が使う）。リポジトリ外の 3 作目の VRM のダンスの
台本の計算をそのまま移した（計算の順と型まで同じ）。Blender の中で使う関数は、bpy・mathutils を関数の中で読む。

- 層: 同じメッシュの中で布が重なっている所（裾の表・ひだ・裏地）を、初期姿勢で、縦の軸から頂点へ水平に引いた線が横切る面の数で分ける。
- 筒: 体のメッシュを輪切りにし、輪ごとに中心と K 方向の太さを測って、両端を丸めた閉じた筒にする（穴も鋭い角も無い当たり）。
"""
import math

import numpy as np

SECTORS = 16                                            # 筒の輪を分ける方向の数（台本の既定）
PERCENTILE = 95.0                                       # 筒の太さに使う、頂点の距離の百分位（台本の既定）
# 内外の確かめに使う 7 本の線の向き（台本と同じ）
RAY_DIRECTIONS = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (0.6, 0.5, 0.62))


def unit(v):
    return v / max(float(np.linalg.norm(v)), 1e-9)


def sector_radii(u, v, sectors=SECTORS, percentile=PERCENTILE):
    """輪切りにした頂点（輪の中心から見た 2 つの向きの座標）から、sectors 方向の太さを測る。頂点の無い方向は両隣から補い、輪のまわりに
    [0.25, 0.5, 0.25] でならす。頂点が 6 に満たないか、測れた方向が 3 に満たなければ None（台本と同じ）。"""
    if len(u) < 6:
        return None
    sector = ((np.arctan2(v, u) + math.pi) / (2 * math.pi) * sectors).astype(int) % sectors
    dist = np.hypot(u, v)
    raw = np.full(sectors, np.nan)
    for k in range(sectors):
        d = dist[sector == k]
        if len(d) >= 3:
            raw[k] = np.percentile(d, percentile)
    have = np.where(~np.isnan(raw))[0]
    if len(have) < 3:
        return None
    filled = raw.copy()
    for k in np.where(np.isnan(raw))[0]:
        left = have[np.argmin((k - have) % sectors)]
        right = have[np.argmin((have - k) % sectors)]
        dl, dr = (k - left) % sectors, (right - k) % sectors
        filled[k] = (raw[left] * dr + raw[right] * dl) / max(dl + dr, 1)
    return 0.25 * np.roll(filled, 1) + 0.5 * filled + 0.25 * np.roll(filled, -1)


def smooth_along(rings):
    """輪から輪への太さの段を、ならす（両端の輪はそのまま）。"""
    radii = np.array([r["radii"] for r in rings])
    out = radii.copy()
    out[1:-1] = 0.25 * radii[:-2] + 0.5 * radii[1:-1] + 0.25 * radii[2:]
    for r, row in zip(rings, out):
        r["radii"] = row


def patch_vertices(cloth, group_name):
    """当て布の頂点（頂点グループ group_name の重みが 0.5 を超える頂点）の番号の集まり。グループが無ければ空。"""
    group = cloth.vertex_groups.get(group_name)
    if group is None:
        return set()
    return {v.index for v in cloth.data.vertices if any(g.group == group.index and g.weight > 0.5 for g in v.groups)}


def layer_classes(cloth, axis_xy, gap, patch_group):
    """同じメッシュの中で布が重なっている所を、層に分ける: 0 ＝いちばん外、1 ＝間、2 ＝いちばん内（裏地）、3 ＝当て布。

    初期姿勢（基本の形）で、縦の軸から頂点へ水平に線を引く。頂点より外側の gap 以内に同じメッシュの面があれば「覆われている」。
    覆われていて、内側にもう面が無ければ、いちばん内。当て布（patch_group の頂点）の面は、層を数えるときには無い物として扱う。
    戻り: (層の番号, すぐ外側の面までの距離（無ければ None）, いちばん内の層か)
    """
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree
    mw = cloth.matrix_world
    co = [mw @ v.co for v in cloth.data.vertices]
    cloth.data.calc_loop_triangles()
    patch = patch_vertices(cloth, patch_group)
    tree = BVHTree.FromPolygons(co, [tuple(t.vertices) for t in cloth.data.loop_triangles if not any(i in patch for i in t.vertices)])
    classes, gaps, inner = [], [], []
    for index, p in enumerate(co):
        if index in patch:
            classes.append(3)
            gaps.append(None)
            inner.append(False)
            continue
        origin = Vector((axis_xy[0], axis_xy[1], p.z))
        d = p - origin
        reach = d.length
        if reach < 1e-4:
            classes.append(0)
            gaps.append(None)
            inner.append(False)
            continue
        d = d / reach
        hits, start, travelled = [], origin, 0.0
        for _ in range(40):
            loc, _, _, dist = tree.ray_cast(start, d)
            if loc is None:
                break
            travelled += dist
            hits.append(travelled)
            start = loc + d * 2e-5
            travelled += 2e-5
        covered = any(reach + 0.0003 < t < reach + gap for t in hits)
        inside, last = 0, -1.0
        for t in hits:
            if t < reach - 0.0003:
                if t - last > 0.0002:
                    inside += 1
                last = t
        classes.append(0 if not covered else (2 if inside == 0 else 1))
        outside = [t - reach for t in hits if t > reach + 0.0003]
        gaps.append(min(outside) if outside else None)
        inner.append(inside == 0)
    return classes, gaps, inner


def mesh_problem(obj, shape_keys=False):
    """メッシュを書きかえる前の確かめ。ほかの物体と共有しているメッシュ（リンク複製）は、頂点も重み（頂点グループの重みはメッシュにある）も
    全部の物体に効くので止める。shape_keys なら、形状キーのあるメッシュも止める（頂点を動かしても足しても、形状キーの形と合わなくなる）。
    戻り: 止める理由（無ければ ""）。"""
    mesh = obj.data
    users = mesh.users - (1 if mesh.use_fake_user else 0)
    if users > 1:
        return f"{obj.name} のメッシュ {mesh.name} を、ほかの物体と共有しています（リンク複製）。書きかえると全部に効くので、先に分けてください"
    if shape_keys and mesh.shape_keys is not None:
        return f"{obj.name} に形状キーがあります。頂点を動かしたり足したりすると形状キーの形と合わなくなるので止めます"
    return ""


def widen_lining(cloth, gaps, inner, axis_xy, lining_gap):
    """いちばん内の層（裏地）の頂点のうち、すぐ外の面との間が lining_gap より狭い物を、内側（縦の軸の向き）へ寄せる。形を書き換える。"""
    from mathutils import Vector
    inverse = cloth.matrix_world.inverted()
    shifts = []
    for i, vertex in enumerate(cloth.data.vertices):
        if not inner[i] or gaps[i] is None or gaps[i] >= lining_gap:
            continue
        point = cloth.matrix_world @ vertex.co
        outward = Vector((point.x - axis_xy[0], point.y - axis_xy[1], 0.0))
        if outward.length < 1e-4:
            continue
        shift = lining_gap - gaps[i]
        vertex.co = inverse @ (point - outward.normalized() * shift)
        shifts.append(shift)
    return {"verts_moved": len(shifts), "max_mm": round(max(shifts, default=0.0) * 1000, 1),
            "mean_mm": round(float(np.mean(shifts)) * 1000, 1) if shifts else 0.0}


def star_tube(bpy, bmesh, scene, armature, name, rings, flatten_start, flatten_end, sectors=SECTORS):
    """輪の並び（中心・軸・横・上・sectors 方向の太さ・骨）から、両端を丸めた閉じた筒を作る（骨で動く・描かない）。戻り: (物体, 体積)。"""
    inv = armature.matrix_world.inverted()                # 筒はアーマチュアの子なので、頂点はアーマチュアの空間で作る（原点になくても合う）
    from mathutils import Vector
    mesh = bpy.data.meshes.new(name)
    proxy = bpy.data.objects.new(name, mesh)
    scene.collection.objects.link(proxy)
    index = {g: proxy.vertex_groups.new(name=g).index for g in sorted({r["group"] for r in rings})}
    first, last = rings[0], rings[-1]
    full = []
    for deg in (60, 30):                                   # 始まりの側の丸い端
        a = math.radians(deg)
        mean = float(np.mean(first["radii"]))
        full.append({**first, "centre": first["centre"] - first["axis"] * mean * flatten_start * math.sin(a), "radii": first["radii"] * math.cos(a)})
    full.extend(rings)
    for deg in (30, 60):                                   # 終わりの側の丸い端
        a = math.radians(deg)
        mean = float(np.mean(last["radii"]))
        full.append({**last, "centre": last["centre"] + last["axis"] * mean * flatten_end * math.sin(a), "radii": last["radii"] * math.cos(a)})
    poles = ((first["centre"] - first["axis"] * float(np.mean(first["radii"])) * flatten_start, first["group"]),
             (last["centre"] + last["axis"] * float(np.mean(last["radii"])) * flatten_end, last["group"]))
    bm = bmesh.new()
    layer = bm.verts.layers.deform.verify()
    made = []
    for r in full:
        ring = []
        for k in range(sectors):
            a = -math.pi + (k + 0.5) * 2 * math.pi / sectors
            v = bm.verts.new(inv @ Vector(r["centre"] + (r["side"] * math.cos(a) + r["up"] * math.sin(a)) * r["radii"][k]))
            v[layer][index[r["group"]]] = 1.0
            ring.append(v)
        made.append(ring)
    for a, b in zip(made[:-1], made[1:]):
        for k in range(sectors):
            k2 = (k + 1) % sectors
            bm.faces.new((a[k], a[k2], b[k2], b[k]))
    for ring, (point, group), flip in ((made[0], poles[0], True), (made[-1], poles[1], False)):
        c = bm.verts.new(inv @ Vector(point))
        c[layer][index[group]] = 1.0
        for k in range(sectors):
            k2 = (k + 1) % sectors
            bm.faces.new((ring[k2], ring[k], c) if flip else (ring[k], ring[k2], c))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    if bm.calc_volume(signed=True) < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
    volume = bm.calc_volume(signed=True)
    bm.to_mesh(mesh)
    bm.free()
    proxy.parent = armature
    modifier = proxy.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    proxy.hide_render = True
    proxy.display_type = "WIRE"
    return proxy, volume


def rest_tree(obj):
    """物体の基本の形（世界の座標）の面の BVH。"""
    from mathutils.bvhtree import BVHTree
    co = [obj.matrix_world @ v.co for v in obj.data.vertices]
    return BVHTree.FromPolygons(co, [tuple(p.vertices) for p in obj.data.polygons])


def signed_distance(tree, p):
    """点 p のいちばん近い面までの距離（面の外向きなら正、裏側なら負）。Shrinkwrap の Outside と同じ見方。"""
    from mathutils import Vector
    loc, normal, _, dist = tree.find_nearest(Vector(p))
    return dist if (Vector(p) - loc).dot(normal) >= 0 else -dist


def far_test(obj, tree):
    """形のまわり（表面から 2 cm 以上離れた点）で、Shrinkwrap の Outside の見方（いちばん近い面の裏側か）が外れないかを確かめる。
    正しい内外は、その点から 7 つの向きへ線を伸ばし、面を横切る回数が奇数の向きが多いか（4 本以上）で決める。
    戻り: (見方が外れた点の数, 調べた点の数, そのうち本当に中にあった点の数)"""
    from mathutils import Vector
    rays = [Vector(unit(np.array(d, dtype=float))) for d in RAY_DIRECTIONS]
    rng = np.random.default_rng(7)
    wrong, total, inside = 0, 0, 0
    for v in obj.data.vertices:
        base = obj.matrix_world @ v.co
        for _ in range(6):
            p = base + Vector(unit(rng.normal(size=3)) * rng.uniform(0.03, 0.30))
            loc, normal, _, dist = tree.find_nearest(p)
            if dist < 0.02:
                continue
            odd = 0
            for d in rays:
                count, start = 0, p
                for _ in range(40):
                    hit = tree.ray_cast(start, d)
                    if hit[0] is None:
                        break
                    count += 1
                    start = hit[0] + d * 1e-5
                odd += count % 2
            truly_inside = odd >= 4
            total += 1
            inside += truly_inside
            if ((p - loc).dot(normal) < 0) != truly_inside:
                wrong += 1
    return wrong, total, inside
