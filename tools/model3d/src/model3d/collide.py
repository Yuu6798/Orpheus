"""閉じたメッシュに対する点の内外判定と、中の点を外へ出す補正。

- 内外は**複数方向の光線と面の交わりの回数の奇偶**で決める（多数決）。最も近い面の向きで決める方法
  （Blender の Shrinkwrap の Outside など）は、口の開いた筒やふさいだ口のふちで読み違えた（2026-11-02 の記録）。
  辺や頂点を共有する三角形を同じ距離で通る交点は 1 回と数える（四角形の対角線をちょうど通る光線で奇偶が反転しないため）。
- 補正は、中の点を最も近い面の上へ出し、面の外向き法線の方向に余裕（clearance）だけ離す。外にあっても余裕より
  近い点は、面から点へ向かう方向に余裕まで離す（pad_outside）。
- 速さに入れない: ここは位置だけを直す。押し出し量を速度に混ぜると弾んで裏返る（同じ記録）。
- 速い探し方（search="grid"、既定）: 光線の向きに垂直な面に三角形を写し、その面の格子の目ごとに、写した三角形の範囲が掛かる
  三角形を並べておく。点は、写した位置の目の三角形とだけ交わりを調べる。光線が通る三角形は、写すと必ず点を含むので、候補から
  漏れない（範囲は少し広げてある）。交わりの式と同じ距離の畳み方は総当たり（search="all"）と同じで、答えも同じになる。
"""
import numpy as np

from .io import ToolError

DIRECTIONS = np.array([[0.3137, 0.7121, 0.6273], [-0.6071, 0.3463, -0.7152], [0.5205, -0.8101, 0.2697]])
DIRECTIONS = DIRECTIONS / np.linalg.norm(DIRECTIONS, axis=1)[:, None]
BUDGET = 1_500_000  # 1 回の計算で扱う 点 × 三角形 の数（メモリの上限）
SEARCHES = ("grid", "all")   # 内外の判定の候補の探し方（格子で絞る・総当たり）
GRID_CELLS = 4096            # 格子の目の数の上限（三角形の数より多くしない）
GRID_PAIRS = 2_000_000       # 1 回の計算で扱う（点, 候補の三角形）の組の数（メモリの上限）


def _triangles(mesh):
    points = mesh.triangle_points()
    if not len(points):
        raise ToolError("衝突相手に面がありません")
    return points


def directions_of(directions):
    """内外判定に使う方向。None なら既定の 3 本。[[x, y, z], …] で 1 本以上・有限・長さ 0 でないことを確かめて正規化する。
    偶数本だと同数（引き分け）を外と数えるので、奇数本を勧める。"""
    if directions is None:
        return DIRECTIONS
    dirs = np.asarray(directions, dtype=np.float64)
    if dirs.ndim != 2 or dirs.shape[1] != 3 or len(dirs) == 0:
        raise ToolError("directions は [[x, y, z], …]（1 本以上）にしてください")
    if not np.isfinite(dirs).all():
        raise ToolError("directions に非有限の値があります")
    norms = np.linalg.norm(dirs, axis=1)
    if (norms <= 0).any():
        raise ToolError("directions に長さ 0 のベクトルがあります")
    return dirs / norms[:, None]


def _chunks(count, per_item):
    size = max(1, BUDGET // max(per_item, 1))
    for start in range(0, count, size):
        yield start, min(count, start + size)


def ray_hits(origins, direction, tri, eps=1e-12):
    """各点から direction へ飛ばした光線が三角形と交わる回数（Möller–Trumbore）。同じ距離の交点は 1 回と数える。"""
    origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
    d = np.asarray(direction, dtype=np.float64)
    tolerance = 1e-9 * max(1.0, float(np.abs(tri).max()), float(np.abs(origins).max()) if len(origins) else 1.0)
    v0, e1, e2 = tri[:, 0], tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(d[None, :], e2)                      # (T,3)
    a = np.einsum("ij,ij->i", e1, h)                  # (T,)
    parallel = np.abs(a) < eps
    f = np.where(parallel, 0.0, 1.0 / np.where(parallel, 1.0, a))
    counts = np.zeros(len(origins), dtype=np.int64)
    for start, end in _chunks(len(origins), len(tri)):
        s = origins[start:end, None, :] - v0[None, :, :]          # (P,T,3)
        u = f[None, :] * np.einsum("ptj,tj->pt", s, h)
        q = np.cross(s, e1[None, :, :])                            # (P,T,3)
        v = f[None, :] * np.einsum("ptj,j->pt", q, d)
        t = f[None, :] * np.einsum("ptj,tj->pt", q, e2)
        hit = (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > eps) & ~parallel[None, :]
        distances = np.where(hit, t, np.inf)
        distances.sort(axis=1)
        finite = np.isfinite(distances)
        with np.errstate(invalid="ignore"):   # 当たらない列は inf で、inf - inf の警告は無視してよい
            distinct = finite[:, 1:] & (np.diff(distances, axis=1) > tolerance)   # 辺・頂点を共有する三角形の重複を畳む
        counts[start:end] = finite[:, 0].astype(np.int64) + distinct.sum(axis=1)
    return counts


def _plane(d):
    """向き d に垂直な面の、直交する 2 本の向き（3, 2）。"""
    helper = np.array([1.0, 0.0, 0.0]) if abs(d[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    first = np.cross(d, helper)
    first /= np.linalg.norm(first)
    return np.stack([first, np.cross(d, first)], axis=1)


def _pair_chunks(counts, budget):
    """点の並びを、（点, 候補の三角形）の組の数が budget ほどの区切りに分ける（点 1 つの候補が多ければ、その点だけで 1 区切り）。"""
    begin, total = 0, 0
    for index, count in enumerate(counts.tolist()):
        if total and total + count > budget:
            yield begin, index
            begin, total = index, 0
        total += count
    yield begin, len(counts)


def ray_hits_grid(origins, direction, tri, eps=1e-12):
    """ray_hits と同じ数（各点から direction へ飛ばした光線が三角形と交わる回数。同じ距離の交点は 1 回）を、光線の向きに垂直な面の
    格子で候補の三角形を絞って数える。"""
    origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
    d = np.asarray(direction, dtype=np.float64)
    counts = np.zeros(len(origins), dtype=np.int64)
    if not len(origins):
        return counts
    tolerance = 1e-9 * max(1.0, float(np.abs(tri).max()), float(np.abs(origins).max()))
    v0, e1, e2 = tri[:, 0], tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]
    h = np.cross(d[None, :], e2)
    a = np.einsum("ij,ij->i", e1, h)
    parallel = np.abs(a) < eps
    f = np.where(parallel, 0.0, 1.0 / np.where(parallel, 1.0, a))
    plane = _plane(d)
    projected = tri @ plane                                       # (T, 3, 2)
    margin = 1e-7 * max(1.0, float(np.abs(projected).max()))      # 写した範囲を少し広げる（丸めで候補から漏らさない）
    low, high = projected.min(axis=1) - margin, projected.max(axis=1) + margin
    area_low, area_high = low.min(axis=0), high.max(axis=0)
    cells = int(max(1, min(GRID_CELLS, len(tri))) ** 0.5)
    size = np.maximum((area_high - area_low) / cells, 1e-300)
    first = np.clip(np.floor((low - area_low) / size).astype(np.int64), 0, cells - 1)
    last = np.clip(np.floor((high - area_low) / size).astype(np.int64), 0, cells - 1)
    width = last[:, 0] - first[:, 0] + 1
    spans = width * (last[:, 1] - first[:, 1] + 1)
    owner = np.repeat(np.arange(len(tri)), spans)                 # 三角形ごとに、掛かる目の数だけ並べる
    local = np.arange(len(owner)) - np.repeat(np.cumsum(spans) - spans, spans)
    cell = (first[owner, 1] + local // width[owner]) * cells + first[owner, 0] + local % width[owner]
    members = owner[np.argsort(cell, kind="stable")]             # 目の順に並べた三角形
    per_cell = np.bincount(cell, minlength=cells * cells)
    starts = np.cumsum(per_cell) - per_cell
    flat = origins @ plane
    within = np.all((flat >= area_low) & (flat <= area_high), axis=1)
    at = np.clip(np.floor((flat - area_low) / size).astype(np.int64), 0, cells - 1)
    point_cell = at[:, 1] * cells + at[:, 0]
    candidates = np.where(within, per_cell[point_cell], 0)
    for begin, end in _pair_chunks(candidates, GRID_PAIRS):
        chunk = np.arange(begin, end)
        n = candidates[chunk]
        if not n.sum():
            continue
        pair_point = np.repeat(chunk, n)
        offset = np.arange(len(pair_point)) - np.repeat(np.cumsum(n) - n, n)
        pair_tri = members[np.repeat(starts[point_cell[chunk]], n) + offset]
        s = origins[pair_point] - v0[pair_tri]                    # 交わりの式は ray_hits と同じ（Möller–Trumbore）
        u = f[pair_tri] * np.einsum("kj,kj->k", s, h[pair_tri])
        q = np.cross(s, e1[pair_tri])
        v = f[pair_tri] * np.einsum("kj,j->k", q, d)
        t = f[pair_tri] * np.einsum("kj,kj->k", q, e2[pair_tri])
        hit = (u >= 0.0) & (v >= 0.0) & (u + v <= 1.0) & (t > eps) & ~parallel[pair_tri]
        hit_point, hit_t = pair_point[hit], t[hit]
        if not len(hit_point):
            continue
        order = np.lexsort((hit_t, hit_point))                    # 点ごとに、近い順
        hit_point, hit_t = hit_point[order], hit_t[order]
        first_of_point = np.r_[True, hit_point[1:] != hit_point[:-1]]
        distinct = first_of_point | np.r_[False, np.diff(hit_t) > tolerance]   # 辺・頂点を共有する三角形の重複を畳む
        counts += np.bincount(hit_point[distinct], minlength=len(origins))
    return counts


def inside(points, mesh, directions=None, search="grid"):
    """閉じたメッシュ mesh の中にある点の真偽 (P,)。bounding box の外は見ずに外とする。search は候補の三角形の探し方
    （grid: 光線の向きに垂直な面の格子で絞る。all: 総当たり）。どちらも同じ答え。"""
    if search not in SEARCHES:
        raise ToolError(f"search は {' か '.join(SEARCHES)} にしてください: {search!r}")
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tri = _triangles(mesh)
    dirs = directions_of(directions)
    result = np.zeros(len(points), dtype=bool)
    if not len(points):
        return result
    lo, hi = tri.reshape(-1, 3).min(axis=0), tri.reshape(-1, 3).max(axis=0)
    candidate = np.all((points >= lo) & (points <= hi), axis=1)
    if not candidate.any():
        return result
    hits = ray_hits_grid if search == "grid" else ray_hits
    votes = np.zeros(int(candidate.sum()), dtype=np.int64)
    for d in dirs:
        votes += hits(points[candidate], d, tri) % 2
    result[candidate] = votes * 2 > len(dirs)
    return result


def closest_points(points, mesh):
    """各点に最も近い面の上の点 (P,3)、距離 (P,)、三角形の番号 (P,)。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tri = _triangles(mesh)
    best_point = np.zeros_like(points)
    best_dist = np.full(len(points), np.inf)
    best_index = np.zeros(len(points), dtype=np.int64)
    a, b, c = tri[:, 0], tri[:, 1], tri[:, 2]
    ab, ac = b - a, c - a
    for start, end in _chunks(len(points), len(tri)):
        p = points[start:end, None, :]
        candidate = _closest_on_triangles(p, a[None], b[None], c[None], ab[None], ac[None])   # (P,T,3)
        dist = np.linalg.norm(candidate - p, axis=2)
        index = dist.argmin(axis=1)
        rows = np.arange(end - start)
        best_point[start:end] = candidate[rows, index]
        best_dist[start:end] = dist[rows, index]
        best_index[start:end] = index
    return best_point, best_dist, best_index


def _closest_on_triangles(p, a, b, c, ab, ac):
    """点 p に最も近い三角形 abc の上の点（Ericson, Real-Time Collision Detection 5.1.5 をベクトル化）。"""
    ap = p - a
    d1 = np.einsum("ptj,ptj->pt", ab, ap)
    d2 = np.einsum("ptj,ptj->pt", ac, ap)
    bp = p - b
    d3 = np.einsum("ptj,ptj->pt", ab, bp)
    d4 = np.einsum("ptj,ptj->pt", ac, bp)
    cp = p - c
    d5 = np.einsum("ptj,ptj->pt", ab, cp)
    d6 = np.einsum("ptj,ptj->pt", ac, cp)
    vc = d1 * d4 - d3 * d2
    vb = d5 * d2 - d1 * d6
    va = d3 * d6 - d5 * d4
    shape = np.broadcast(p, a).shape
    result = np.zeros(shape)
    done = np.zeros(shape[:2], dtype=bool)

    def put(mask, value):
        mask = mask & ~done
        result[mask] = np.broadcast_to(value, shape)[mask]
        done[mask] = True

    put((d1 <= 0) & (d2 <= 0), a)
    put((d3 >= 0) & (d4 <= d3), b)
    put((d6 >= 0) & (d5 <= d6), c)
    with np.errstate(divide="ignore", invalid="ignore"):
        v = d1 / np.where(d1 - d3 == 0, 1.0, d1 - d3)
        put((vc <= 0) & (d1 >= 0) & (d3 <= 0), a + v[..., None] * ab)
        w = d2 / np.where(d2 - d6 == 0, 1.0, d2 - d6)
        put((vb <= 0) & (d2 >= 0) & (d6 <= 0), a + w[..., None] * ac)
        w2 = (d4 - d3) / np.where((d4 - d3) + (d5 - d6) == 0, 1.0, (d4 - d3) + (d5 - d6))
        put((va <= 0) & (d4 - d3 >= 0) & (d5 - d6 >= 0), b + w2[..., None] * (c - b))
        denom = 1.0 / np.where(va + vb + vc == 0, 1.0, va + vb + vc)
        v3 = vb * denom
        w3 = vc * denom
        put(~done, a + ab * v3[..., None] + ac * w3[..., None])
    return result


def push_out(points, mesh, clearance, directions=None, pad_outside=True):
    """中の点（と、余裕より近い外の点）を面から clearance だけ離した位置へ動かす。動かした後の点と報告を返す。"""
    if isinstance(clearance, bool) or not isinstance(clearance, (int, float)) or not np.isfinite(clearance) or clearance < 0:
        raise ToolError(f"clearance は 0 以上の有限の数にしてください: {clearance!r}")
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    directions = directions_of(directions)
    tri = _triangles(mesh)
    normals = mesh.triangle_normals()
    if mesh.signed_volume() < 0:   # 内向きに張られたメッシュでも外向きの法線を使う
        normals = -normals
    is_inside = inside(points, mesh, directions)
    cp, dist, index = closest_points(points, mesh)
    moved = points.copy()
    moved[is_inside] = cp[is_inside] + normals[index[is_inside]] * clearance
    padded = np.zeros(len(points), dtype=bool)
    if pad_outside and clearance > 0:
        padded = ~is_inside & (dist < clearance)
        direction = points[padded] - cp[padded]
        length = np.linalg.norm(direction, axis=1)
        direction = np.where(length[:, None] > 0, direction / np.where(length[:, None] > 0, length[:, None], 1.0),
                             normals[index[padded]])
        moved[padded] = cp[padded] + direction * clearance
    depth = np.where(is_inside, dist, 0.0)
    residual = int(inside(moved, mesh, directions).sum()) if len(points) else 0
    report = {"points": int(len(points)), "inside": int(is_inside.sum()), "padded": int(padded.sum()),
              "moved": int((is_inside | padded).sum()), "max_depth": float(depth.max()) if len(points) else 0.0,
              "mean_depth": float(depth[is_inside].mean()) if is_inside.any() else 0.0, "clearance": float(clearance),
              "residual_inside": residual, "directions": directions.tolist()}
    return moved, report
