"""閉じたメッシュに対する点の内外判定と、中の点を外へ出す補正。

- 内外は**複数方向の光線と面の交わりの回数の奇偶**で決める（多数決）。最も近い面の向きで決める方法
  （Blender の Shrinkwrap の Outside など）は、口の開いた筒やふさいだ口のふちで読み違えた（2026-11-02 の記録）。
- 補正は、中の点を最も近い面の上へ出し、面の外向き法線の方向に余裕（clearance）だけ離す。外にあっても余裕より
  近い点は、面から点へ向かう方向に余裕まで離す（pad_outside）。
- 速さに入れない: ここは位置だけを直す。押し出し量を速度に混ぜると弾んで裏返る（同じ記録）。
"""
import numpy as np

from .io import ToolError

DIRECTIONS = np.array([[0.3137, 0.7121, 0.6273], [-0.6071, 0.3463, -0.7152], [0.5205, -0.8101, 0.2697]])
DIRECTIONS = DIRECTIONS / np.linalg.norm(DIRECTIONS, axis=1)[:, None]
BUDGET = 1_500_000  # 1 回の計算で扱う 点 × 三角形 の数（メモリの上限）


def _triangles(mesh):
    points = mesh.triangle_points()
    if not len(points):
        raise ToolError("衝突相手に面がありません")
    return points


def _chunks(count, per_item):
    size = max(1, BUDGET // max(per_item, 1))
    for start in range(0, count, size):
        yield start, min(count, start + size)


def ray_hits(origins, direction, tri, eps=1e-12):
    """各点から direction へ飛ばした光線が三角形と交わる回数（Möller–Trumbore）。"""
    origins = np.asarray(origins, dtype=np.float64).reshape(-1, 3)
    d = np.asarray(direction, dtype=np.float64)
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
        counts[start:end] = hit.sum(axis=1)
    return counts


def inside(points, mesh, directions=None):
    """閉じたメッシュ mesh の中にある点の真偽 (P,)。bounding box の外は見ずに外とする。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    tri = _triangles(mesh)
    dirs = DIRECTIONS if directions is None else np.asarray(directions, dtype=np.float64).reshape(-1, 3)
    result = np.zeros(len(points), dtype=bool)
    if not len(points):
        return result
    lo, hi = tri.reshape(-1, 3).min(axis=0), tri.reshape(-1, 3).max(axis=0)
    candidate = np.all((points >= lo) & (points <= hi), axis=1)
    if not candidate.any():
        return result
    votes = np.zeros(int(candidate.sum()), dtype=np.int64)
    for d in dirs:
        votes += ray_hits(points[candidate], d / np.linalg.norm(d), tri) % 2
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
              "residual_inside": residual, "directions": (DIRECTIONS if directions is None else np.asarray(directions)).tolist()}
    return moved, report
