"""点の最近傍を numpy だけで探す（model3d は scipy に頼らない）。

格子に点を分け、問いの点の升目とその周りの升目だけを比べる。見つけた点より近い点が、探した升目の外にあり得る問いは、升目を
1 周ずつ広げて探し直し、それでも残れば全部の点と比べる。距離は float64 で測り、同じ距離なら番号の小さい点を選ぶ。"""
import itertools

import numpy as np

from .io import ToolError


def _shell(radius):
    """チェビシェフ距離がちょうど radius の升目のずれ（radius 0 は中心だけ）。"""
    span = range(-radius, radius + 1)
    return np.array([d for d in itertools.product(span, span, span) if max(abs(v) for v in d) == radius], dtype=np.int64)


def default_cell(points, occupancy=3.0, rounds=4):
    """升目の大きさの既定: 点の入った升目に、平均で occupancy 個ほど入る大きさ。広がりの体積からの見積もりから始め、
    点の入った升目の平均の数を数えて縮める（面の上の点は、1 升目の数が升目の大きさの 2 乗に比例するとして直す）。
    見積もりには、升目より厚い軸だけを使う（平らな面は面積、線は長さから。厚み 0 の軸まで体積に入れると、升目が点の間隔より
    ずっと小さくなり、ほとんどの問いが全部の点と比べることになる）。3 軸とも升目より厚い点は、体積からの見積もりのまま。"""
    points = np.asarray(points, dtype=np.float64)
    n = len(points)
    extent = points.max(axis=0) - points.min(axis=0)
    keep = extent > 0
    while keep.any():
        cell = float(2.0 * (np.prod(extent[keep]) / max(n, 1)) ** (1 / int(keep.sum()))) or 1.0
        thin = keep & (extent < cell)
        if not thin.any():
            break
        keep &= ~thin                                      # 升目より薄い軸を外して、残りの軸で見積もり直す
    else:
        cell = 1.0                                         # 全部の点が同じ位置（どの大きさでも同じ）
    origin = points.min(axis=0)
    for _ in range(rounds):
        cells = np.floor((points - origin) / cell).astype(np.int64)
        dims = cells.max(axis=0) + 1
        filled = len(np.unique((cells[:, 0] * dims[1] + cells[:, 1]) * dims[2] + cells[:, 2]))
        mean = n / max(filled, 1)
        if mean <= occupancy * 1.5:
            break
        cell *= (occupancy / mean) ** 0.5
    return cell


def nearest(points, queries, cell=None, max_radius=4, chunk=200_000):
    """queries の点ごとに、points の中でいちばん近い点の番号と距離（float64）。戻り: (番号 (Q,), 距離 (Q,), 報告)。"""
    P = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    Q = np.asarray(queries, dtype=np.float64).reshape(-1, 3)
    if not len(P):
        raise ToolError("最近傍を探す点がありません")
    if not np.isfinite(P).all() or not np.isfinite(Q).all():
        raise ToolError("最近傍を探す点に非有限の値があります")
    cell = float(cell) if cell else default_cell(P)
    if not cell > 0:
        raise ToolError(f"升目の大きさは正の数にしてください: {cell!r}")
    origin = P.min(axis=0)
    p_cells = np.floor((P - origin) / cell).astype(np.int64)
    dims = p_cells.max(axis=0) + 1
    key = (p_cells[:, 0] * dims[1] + p_cells[:, 1]) * dims[2] + p_cells[:, 2]
    order = np.argsort(key, kind="stable")
    keys, start, count = np.unique(key[order], return_index=True, return_counts=True)
    q_cells = np.floor((Q - origin) / cell).astype(np.int64)
    best = np.full(len(Q), np.inf)
    index = np.full(len(Q), -1, dtype=np.int64)
    pending = np.arange(len(Q))
    passes, brute = 0, 0
    for radius in range(0, max_radius + 1):
        if not len(pending):
            break
        passes += 1
        for offset in _shell(radius):
            for lo in range(0, len(pending), chunk):
                q = pending[lo:lo + chunk]
                c = q_cells[q] + offset
                inside = ((c >= 0) & (c < dims)).all(axis=1)
                q, c = q[inside], c[inside]
                if not len(q):
                    continue
                k = (c[:, 0] * dims[1] + c[:, 1]) * dims[2] + c[:, 2]
                pos = np.minimum(np.searchsorted(keys, k), len(keys) - 1)
                found = keys[pos] == k
                q, first, many = q[found], start[pos[found]], count[pos[found]]
                for j in range(int(many.max()) if len(many) else 0):
                    take = many > j
                    qq, cand = q[take], order[first[take] + j]
                    d2 = ((Q[qq] - P[cand]) ** 2).sum(axis=1)
                    better = (d2 < best[qq]) | ((d2 == best[qq]) & (cand < index[qq]))
                    best[qq[better]], index[qq[better]] = d2[better], cand[better]
        reach = (radius * cell) ** 2 if radius else 0.0
        pending = pending[~(best[pending] <= reach)]       # 探した升目の外の点は、radius × cell より遠い
    if len(pending):                                       # 升目を広げても決まらない点（離れた点）は、全部の点と比べる
        brute = len(pending)
        rows = max(1, 16_000_000 // len(P))               # 一度に作る距離の表を 1600 万要素までにする
        for lo in range(0, len(pending), rows):
            q = pending[lo:lo + rows]
            d2 = ((Q[q, None, :] - P[None, :, :]) ** 2).sum(axis=2)
            pick = d2.argmin(axis=1)                       # 同じ距離なら番号の小さい点（argmin は最初の最小）
            chosen = d2[np.arange(len(q)), pick]
            better = (chosen < best[q]) | ((chosen == best[q]) & (pick < index[q]))
            best[q[better]], index[q[better]] = chosen[better], pick[better]
    return index, np.sqrt(best), {"cell": cell, "passes": passes, "brute_force": int(brute)}
