"""メッシュを水平な面で切った切り口を、面のつながりでたどって輪にし、胴のくびれを探す部品（numpy だけ。Blender の中でも外でも
読める）。check.candidate（model3d/mesh_slices.py）が使う。座標は世界の座標（m、Z が上）。
V は (N, 3) の頂点、T は (M, 3) の三角形の頂点の番号。"""
import numpy as np

WAIST = (0.80, 0.92, 0.005)          # くびれを探す高さ（床から腕までの高さ S に対する割合）と、きざみ
ARM_REACH = 0.85                     # 腕の先: 体の中心からの横の距離が、いちばん遠い頂点の 0.85 倍を超える頂点


def section_loops(V, T, z):
    """高さ z の水平な面で切った切り口を、つながった輪ごとの点（(n, 2) の x, y）の並びにする。切り口の点を位置で分けず、
    面のつながりでたどる（面の粗い所で胴が細切れにならないように）。"""
    above = V[T][:, :, 2] > z
    crossing = T[(above.sum(axis=1) % 3) != 0]
    parent, point = {}, {}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    for tri in crossing:
        keys = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = int(tri[i]), int(tri[j])
            if (V[a, 2] > z) != (V[b, 2] > z):
                key = (min(a, b), max(a, b))
                if key not in point:
                    t = (z - V[a, 2]) / (V[b, 2] - V[a, 2])
                    point[key] = V[a, :2] + (V[b, :2] - V[a, :2]) * t
                    parent[key] = key
                keys.append(key)
        if len(keys) == 2:
            parent[find(keys[0])] = find(keys[1])
    loops = {}
    for key, p in point.items():
        loops.setdefault(find(key), []).append(p)
    return [np.array(points) for points in loops.values()]


def clusters(V, T, z, gap):
    """高さ z の切り口の輪の、横（x）の範囲が gap より近く重なる輪をまとめたかたまりの [(x0, x1, y0, y1)]（x0 の小さい順）。"""
    boxes = sorted([float(p[:, 0].min()), float(p[:, 0].max()), float(p[:, 1].min()), float(p[:, 1].max())]
                   for p in section_loops(V, T, z))
    merged = []
    for box in boxes:
        if merged and box[0] <= merged[-1][1] + gap:
            merged[-1][1] = max(merged[-1][1], box[1])
            merged[-1][2] = min(merged[-1][2], box[2])
            merged[-1][3] = max(merged[-1][3], box[3])
        else:
            merged.append(list(box))
    return [tuple(box) for box in merged]


def torso_box(V, T, z, cx, gap):
    """高さ z の切り口で、体の中心 cx を含むかたまりの (x0, x1, y0, y1)（無ければ None）。"""
    for box in clusters(V, T, z, gap):
        if box[0] <= cx <= box[1]:
            return box
    return None


def frame(V):
    """床の高さ z0・いちばん上 z1・体の中心 cx（下半分の頂点の x の中央値）・腕の先の高さ zs・物差し S = zs − z0。
    高さが 0 なら ValueError。"""
    z0, z1 = float(V[:, 2].min()), float(V[:, 2].max())
    if not z1 > z0:
        raise ValueError("体の高さが 0 です（Z が上の、立った体か確かめてください）")
    cx = float(np.median(V[V[:, 2] < z0 + 0.5 * (z1 - z0), 0]))
    reach = np.abs(V[:, 0] - cx)
    zs = float(np.median(V[reach > ARM_REACH * reach.max(), 2]))
    return z0, z1, cx, zs, zs - z0


def waist(V, T, z0, S, cx, gap):
    """くびれ: 床から S の 0.80〜0.92 倍の高さで、中心を含むかたまりの幅がいちばん狭い所の（S に対する割合, 幅, 厚み）。
    どの高さでも中心を含むかたまりが無ければ None。"""
    best = None
    for f in np.arange(WAIST[0], WAIST[1] + 0.2 * WAIST[2], WAIST[2]):
        box = torso_box(V, T, z0 + f * S, cx, gap)
        if box and (best is None or box[1] - box[0] < best[1]):
            best = (float(f), box[1] - box[0], box[3] - box[2])
    return best
