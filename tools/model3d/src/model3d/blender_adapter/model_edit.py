"""モデルの手直しの台本（body_shape.py・body_size.py など。docs/model3d.md の「モデルを手直しする」）が共通に使う部品。

前半は numpy だけで動く計算（テストで確かめる）、後半は Blender の物体から値を読み書きする部品（bpy の物体を受け取るだけで、
bpy は import しない）。リポジトリ外の 2 作目の VRM のダンスの台本と同じ計算の順にしてある（照合で値まで同じになるように）。
"""
import numpy as np


# ---- numpy だけの計算 -------------------------------------------------------------------------

def islands(count, edges):
    """辺 (E, 2) でつながった頂点の島の番号 (count,)。番号は島の中の 1 つの頂点の番号（台本と同じ union-find で、番号の付き方まで同じ）。"""
    parent = np.arange(count)

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in np.asarray(edges, dtype=np.int64).reshape(-1, 2):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(count)], dtype=np.int64)


def profile(table, value):
    """[[位置, 値], …] の表を直線で補間する（表の外は端の値）。"""
    xs, ys = [row[0] for row in table], [row[1] for row in table]
    return np.interp(value, xs, ys)


def ramp(value, low, high):
    """low で 0、high で 1 のなめらかな段（smoothstep）。"""
    t = np.clip((value - low) / max(high - low, 1e-9), 0.0, 1.0)
    return t * t * (3 - 2 * t)


def bone_axis(head, tail):
    """骨の付け根 → 先の単位の向きと長さ。付け根と先は読んだ型のまま引く（Blender 5.2.4 の bone_world は単精度の値を返す。
    倍精度に直してから引くと、台本と 1e-8 ほど違う向きになった）。"""
    axis = np.asarray(tail) - np.asarray(head)
    length = float(np.linalg.norm(axis))
    return axis / length, length


def check_table(table, where, low=None, high=None):
    """[[位置, 値], …] の表を確かめる（位置は増える順・値は正の有限の数）。ValueError で止める。"""
    if not isinstance(table, list) or not table:
        raise ValueError(f"{where} は [[位置, 値], …] の表にしてください: {table!r}")
    for row in table:
        if (not isinstance(row, list) or len(row) != 2
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in row)):
            raise ValueError(f"{where} の行は [位置, 値]（有限の数）にしてください: {row!r}")
        if row[1] <= 0:
            raise ValueError(f"{where} の値（倍率）は 0 より大きくしてください: {row!r}")
        if (low is not None and row[0] < low) or (high is not None and row[0] > high):
            raise ValueError(f"{where} の位置は {low}〜{high} にしてください: {row!r}")
    xs = [row[0] for row in table]
    if any(b <= a for a, b in zip(xs, xs[1:])):
        raise ValueError(f"{where} の位置は増える順（重ならない）にしてください: {xs!r}")
    return table


# ---- Blender の物体から読み書きする部品（bpy は import しない） ------------------------------------

def world_points(obj):
    """メッシュの頂点（基本の形）を世界の座標 (N, 3) で（float64）。"""
    world = np.array(obj.matrix_world)
    local = np.empty(len(obj.data.vertices) * 3)
    obj.data.vertices.foreach_get("co", local)
    return local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]


def write_points(obj, points):
    """世界の座標 (N, 3) の頂点を書き戻す。形状キーのあるメッシュは、どのキーにも同じ動き（基本の形で求めた移動量）を足す
    （台本のモデルは形状キーが無く、頂点を書くだけだった）。"""
    inverse = np.array(obj.matrix_world.inverted())
    local = points @ inverse[:3, :3].T + inverse[:3, 3]
    mesh = obj.data
    keys = mesh.shape_keys
    if keys is not None:
        before = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", before)
        delta = local.reshape(-1) - before
        for block in keys.key_blocks:
            data = np.empty(len(mesh.vertices) * 3)
            block.data.foreach_get("co", data)
            block.data.foreach_set("co", data + delta)
    mesh.vertices.foreach_set("co", local.reshape(-1))
    mesh.update()


def mesh_edges(mesh):
    """メッシュの辺 (E, 2)（int64）。"""
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
    mesh.edges.foreach_get("vertices", edges)
    return edges.reshape(-1, 2)


def deform_names(armature):
    """アーマチュアの変形に使う骨の名前。頂点グループのうち、骨の重みとして読むのはこの名前のものだけ（選択や押し出しのための
    グループ、変形に使わない骨のグループは読まない。混ぜると、頼んだ広げ方が薄まる）。"""
    return {bone.name for bone in armature.data.bones if bone.use_deform}


def top_groups(obj, allowed=None):
    """頂点ごとの、いちばん重みの大きい頂点グループの名前 (N,)（allowed の名前のグループだけ。無い頂点は ""。同じ重みは先のもの。
    台本と同じ）。"""
    names = {group.index: group.name for group in obj.vertex_groups}
    out = []
    for vertex in obj.data.vertices:
        items = [g for g in vertex.groups if allowed is None or names[g.group] in allowed]
        out.append(names[max(items, key=lambda g: g.weight).group] if items else "")
    return np.array(out)


def group_weights(obj, allowed=None):
    """頂点グループの重みを、[(名前, 頂点の番号 (K,), 重み (K,)), …] で（allowed の名前のグループだけ。頂点の順に、初めて出た順。
    台本の足し合わせの順と同じ）。"""
    names = {group.index: group.name for group in obj.vertex_groups}
    collected = {}
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            if allowed is None or names[item.group] in allowed:
                collected.setdefault(names[item.group], []).append((vertex.index, item.weight))
    return [(name, np.array([pair[0] for pair in pairs]), np.array([pair[1] for pair in pairs])) for name, pairs in collected.items()]


def ignored_groups(obj, allowed):
    """allowed に無い頂点グループの名前と、その重みを持つ頂点の数（報告用）。"""
    names = {group.index: group.name for group in obj.vertex_groups}
    counts = {name: 0 for name in names.values() if name not in allowed}
    for vertex in obj.data.vertices:
        for item in vertex.groups:
            if names[item.group] not in allowed and item.weight > 0:
                counts[names[item.group]] += 1
    return counts


def bone_world(armature, name):
    """骨の付け根と先の、初期姿勢の世界の座標。型は Blender の値のまま（5.2.4 の mathutils は単精度の配列になる。台本と同じ）。"""
    world = np.array(armature.matrix_world)
    bone = armature.data.bones[name]
    return (world[:3, :3] @ np.array(bone.head_local) + world[:3, 3],
            world[:3, :3] @ np.array(bone.tail_local) + world[:3, 3])
