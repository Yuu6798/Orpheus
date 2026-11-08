"""参照画像（設定画など）をメッシュの UV テクスチャへ写す処理と、テクスチャを貼ったメッシュの正射影の描画。

- 視点は座標軸に沿った正射影。`from` はカメラのある側（-Y は正面、+Y は背面、+X・-X は横）で、上は +Z。
  視点の平面での中心 `center`（[右, 上]）、画像の高さに当たる幅 `span`、画素数 `size`（[幅, 高さ]）で決める。
  長さはメッシュの単位で、軸の約束は 上 +Z・正面 -Y のメッシュだけを受け付ける（違えば mesh.transform の to_axes で直す）。
- 見え隠れは z バッファで決める。点の写る画素でいちばん手前の面の平面を、その点の位置まで延ばした奥行きから `depth_tolerance`
  以内にある点だけを見えると数える（画素の中心の奥行きと比べると、傾いた面が自分を隠して縞が抜けるため）。
- 参照画像と視点の画素の対応は、目印の組（視点の画素 ↔ 参照画像の画素）から相似変換か affine で決める。目印は推定しない。
- 色はテクスチャの画素ごとに、その画素が載る 3D の点を視点へ写して参照画像から引き、混ぜる（テクスチャ側から引くので穴が空かない）。
  重みは 面の向き × 見えているか × マスク × 参照画像のアルファ。重みの無い画素は元の色のまま残す。
- uv が島をまたいで引き伸ばされた三角形（継ぎ目の頂点を結合したときなど）は塗らない。繰り返しの uv で 1 をまたぐ三角形は、
  重なる 1 枚ごとに基本の 1 枚へずらして塗る（描画の sample(wrap=True) と同じ画素に当たる）。
- 三角形の塗りつぶしは numpy でまとめて行う（大きさごとに束ねる）。同じ入力なら同じ出力。
"""
import numpy as np
from PIL import Image, ImageFilter

from .io import ToolError
from .mesh import DEFAULT_AXES

VIEW_FORWARD = {"-Y": (0.0, 1.0, 0.0), "+Y": (0.0, -1.0, 0.0), "-X": (1.0, 0.0, 0.0), "+X": (-1.0, 0.0, 0.0)}
UP = np.array([0.0, 0.0, 1.0])
BUCKETS = (1, 2, 4, 8, 16, 32, 64)   # 三角形の外接箱の一辺（画素）ごとに束ねて塗る。これより大きいものは 1 つずつ
CHUNK = 2_000_000                    # 1 回に調べる 三角形 × 候補の画素 の数（メモリの上限）


# ---- 視点 ----------------------------------------------------------------------------

def check_view(view):
    """視点の設定を確かめ、(右, 上, 奥) の向き・1 m あたりの画素数・中心・画素数を返す。"""
    if not isinstance(view, dict) or view.get("from") not in VIEW_FORWARD:
        raise ToolError(f"view.from は {', '.join(VIEW_FORWARD)} のどれかにしてください: {view!r}")
    center, span, size = view.get("center"), view.get("span"), view.get("size")
    if (not isinstance(center, (list, tuple)) or len(center) != 2
            or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v) for v in center)):
        raise ToolError(f"view.center は [右, 上]（m）の 2 つの有限の数にしてください: {center!r}")
    if isinstance(span, bool) or not isinstance(span, (int, float)) or not np.isfinite(span) or span <= 0:
        raise ToolError(f"view.span は 0 より大きい有限の数（m）にしてください: {span!r}")
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16384 for v in size)):
        raise ToolError(f"view.size は [幅, 高さ]（1〜16384 の整数）にしてください: {size!r}")
    forward = np.array(VIEW_FORWARD[view["from"]])
    right = np.cross(forward, UP)
    return right, UP, forward, size[1] / span, np.asarray(center, dtype=np.float64), (int(size[0]), int(size[1]))


def require_view_axes(mesh):
    """視点は 上 +Z・正面 -Y の軸の約束で決める。違う軸のメッシュは止める（座標をそのまま写さない）。"""
    if mesh.axes != DEFAULT_AXES:
        raise ToolError(f"視点は軸の約束 {DEFAULT_AXES} で決めます（mesh: {mesh.axes}）。"
                        "mesh.transform の to_axes でそろえてから渡してください")


def project_points(points, view):
    """点 (N,3) を視点の画素の座標 (N,2)（画素 (x, y) の中心が (x+0.5, y+0.5)）と奥行き (N,)（小さいほど手前）へ写す。"""
    right, up, forward, scale, center, (width, height) = check_view(view)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    x = (points @ right - center[0]) * scale + width / 2
    y = height / 2 - (points @ up - center[1]) * scale
    return np.stack([x, y], axis=1), points @ forward


# ---- 三角形の塗りつぶし -------------------------------------------------------------------

def _inverse(tri):
    """三角形 (T,3,2) の辺の行列の逆（重心座標を求めるため）と、退化していないか。"""
    origin = tri[:, 0]
    m00, m01 = tri[:, 1, 0] - origin[:, 0], tri[:, 2, 0] - origin[:, 0]
    m10, m11 = tri[:, 1, 1] - origin[:, 1], tri[:, 2, 1] - origin[:, 1]
    det = m00 * m11 - m01 * m10
    ok = np.abs(det) > 1e-12
    safe = np.where(ok, det, 1.0)
    inverse = np.stack([np.stack([m11, -m01], axis=1), np.stack([-m10, m00], axis=1)], axis=1) / safe[:, None, None]
    return origin, inverse, ok


def _weights(points, origin, inverse):
    """点 (T,K,2) の、各三角形に対する重心座標 (T,K,3)。"""
    local = np.einsum("tij,tkj->tki", inverse, points - origin[:, None, :])
    return np.concatenate([1.0 - local.sum(axis=2, keepdims=True), local], axis=2)


def _edge_distance_squared(points, tri):
    """点 (T,K,2) から三角形 (T,3,2) の 3 辺（線分）までの距離の 2 乗のうち、いちばん小さい値 (T,K)。"""
    best = None
    for i in range(3):
        a, b = tri[:, i][:, None, :], tri[:, (i + 1) % 3][:, None, :]
        ab = b - a
        t = np.clip(((points - a) * ab).sum(axis=2) / np.maximum((ab ** 2).sum(axis=2), 1e-30), 0.0, 1.0)
        d = ((points - a - t[..., None] * ab) ** 2).sum(axis=2)
        best = d if best is None else np.minimum(best, d)
    return best


def rasterize(triangles, width, height, expand=0.0):
    """三角形 (T,3,2)（画素の座標）が覆う画素。画素の中心が三角形の中（辺の上を含む）にあれば覆うと数える。

    返り値は 三角形の番号 (K,)、画素の x・y (K,)、重心座標 (K,3)。退化した三角形と画像の外は飛ばす。
    expand は三角形から画素の中心までの距離の上限（画素。UV の継ぎ目で色が欠けないよう、島の縁の外も塗る）。細い三角形も
    同じ幅だけ広がり、鋭い角は expand より先へ伸びない。外の画素の重心座標は三角形の平面を延ばした値（負の成分を持つ）。
    """
    tri = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 2)
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3)))
    if not len(tri):
        return empty
    lo = np.maximum(np.floor(tri.min(axis=1) - 0.5 - expand).astype(np.int64), 0)
    hi = np.minimum(np.ceil(tri.max(axis=1) - 0.5 + expand).astype(np.int64), [width - 1, height - 1])
    origin, inverse, ok = _inverse(tri)
    usable = ok & (hi >= lo).all(axis=1) & np.isfinite(tri).all(axis=(1, 2))
    extent = (hi - lo + 1).max(axis=1)
    parts = []

    def emit(ids, pixels):
        within = (pixels <= hi[ids][:, None, :]).all(axis=2)
        centres = pixels + 0.5
        weights = _weights(centres, origin[ids], inverse[ids])
        inside = weights.min(axis=2) >= -1e-9
        if expand:
            inside |= _edge_distance_squared(centres, tri[ids]) <= expand * expand
        inside &= within
        if not inside.any():
            return
        which, slot = np.nonzero(inside)
        parts.append((ids[which], pixels[which, slot, 0], pixels[which, slot, 1], weights[which, slot]))

    done = ~usable
    for size in BUCKETS:
        group = np.flatnonzero(~done & (extent <= size))
        done[group] = True
        if not len(group):
            continue
        offsets = np.stack(np.meshgrid(np.arange(size), np.arange(size), indexing="xy"), axis=-1).reshape(-1, 2)
        step = max(1, CHUNK // (size * size))
        for start in range(0, len(group), step):
            ids = group[start:start + step]
            emit(ids, lo[ids][:, None, :] + offsets[None, :, :])
    for index in np.flatnonzero(~done):   # 大きな三角形は 1 つずつ（外接箱の画素を全部調べる）
        (x0, y0), (x1, y1) = lo[index], hi[index]
        ys, xs = np.mgrid[y0:y1 + 1, x0:x1 + 1]
        emit(np.array([index]), np.stack([xs.ravel(), ys.ravel()], axis=1)[None, :, :])
    if not parts:
        return empty
    return tuple(np.concatenate([p[i] for p in parts]) for i in range(4))


def depth_planes(points, depth, triangles):
    """三角形ごとの奥行きの平面。視点の画素の座標 p での奥行きは z0 + g·(p - o)（正射影なので画面の座標に対して線形。
    三角形の外は延長）。返り値は o (T,2)、g (T,2)、z0 (T,)。"""
    origin, inverse, _ = _inverse(points[triangles])
    corner = depth[triangles]
    gradient = np.einsum("tij,ti->tj", inverse, corner[:, 1:] - corner[:, :1])
    return origin, gradient, corner[:, 0]


def zbuffer(points, depth, triangles, width, height):
    """画素ごとのいちばん手前の奥行き (H,W)（面の無い画素は inf）と、勝った断片（三角形の番号・x・y・重心座標）。"""
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    index, x, y, bary = rasterize(points[triangles], width, height)
    buffer = np.full(width * height, np.inf)
    if not len(index):
        return buffer.reshape(height, width), (index, x, y, bary)
    z = np.einsum("kj,kj->k", bary, depth[triangles[index]])
    pixel = y * width + x
    order = np.lexsort((index, z, pixel))   # 画素ごとに 手前 → 三角形の番号 の順。同じ入力なら同じ勝者
    ranked = pixel[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = ranked[1:] != ranked[:-1]
    win = order[first]
    buffer[pixel[win]] = z[win]
    return buffer.reshape(height, width), (index[win], x[win], y[win], bary[win])


# ---- 画像 ----------------------------------------------------------------------------

def load_image(path, alpha=False):
    """画像を float の (H,W,C) で読む（RGB、alpha=True なら RGBA）。0〜255。"""
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("RGBA" if alpha else "RGB"), dtype=np.float64)
    except OSError as error:
        raise ToolError(f"画像を読めません: {path}（{error}）") from error


def load_texture(path):
    """テクスチャを float で読む。アルファのある画像は (H,W,4) のまま（写しても透明な所を変えないため）、ほかは (H,W,3)。"""
    try:
        with Image.open(path) as im:
            keep = im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info)
            return np.asarray(im.convert("RGBA" if keep else "RGB"), dtype=np.float64)
    except OSError as error:
        raise ToolError(f"画像を読めません: {path}（{error}）") from error


def load_mask(path):
    """重みの画像を float の (H,W) で読む（灰色に直す。0〜255）。"""
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("L"), dtype=np.float64)
    except OSError as error:
        raise ToolError(f"画像を読めません: {path}（{error}）") from error


def save_image(path, array):
    """(H,W,3) は RGB、(H,W,4) は RGBA、(H,W) は灰色の PNG として書く（0〜255 に丸める）。"""
    Image.fromarray(np.clip(np.rint(array), 0, 255).astype(np.uint8)).save(path)


def draw_mask(size, shapes, feather=0.0):
    """多角形・楕円を順に塗った (H,W) の重み（0〜255）。画素の中心が図形の中にあれば塗る。

    shapes は {"polygon": [[x, y], …]} か {"ellipse": [cx, cy, rx, ry], "angle": 度（時計回り、任意）} の列で、
    "value"（0〜255、既定 255）で塗る。後の図形が上書きするので value 0 で切り抜ける。feather は縁をぼかす幅（画素、ガウスの σ）。
    """
    if (not isinstance(size, (list, tuple)) or len(size) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 16384 for v in size)):
        raise ToolError(f"size は [幅, 高さ]（1〜16384 の整数）にしてください: {size!r}")
    if not isinstance(shapes, list) or not shapes:
        raise ToolError("shapes に図形を 1 つ以上書いてください（polygon か ellipse）")
    width, height = size
    ys, xs = np.mgrid[0:height, 0:width]
    px, py = xs + 0.5, ys + 0.5
    mask = np.zeros((height, width))
    for k, shape in enumerate(shapes):
        if not isinstance(shape, dict):
            raise ToolError(f"shapes[{k}] は {{\"polygon\": …}} か {{\"ellipse\": …}} にしてください: {shape!r}")
        value = shape.get("value", 255)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 255:
            raise ToolError(f"shapes[{k}] の value は 0〜255 にしてください: {shape!r}")
        if "polygon" in shape:
            try:
                points = np.asarray(shape["polygon"], dtype=np.float64).reshape(-1, 2)
            except (TypeError, ValueError) as error:
                raise ToolError(f"shapes[{k}].polygon は [[x, y], …] にしてください（{error}）") from error
            if len(points) < 3 or not np.isfinite(points).all():
                raise ToolError(f"shapes[{k}].polygon には有限の点が 3 つ以上要ります")
            inside = np.zeros((height, width), dtype=bool)
            for (x0, y0), (x1, y1) in zip(points, np.roll(points, -1, axis=0)):   # 交差数（偶奇）で中か外か
                if y0 == y1:
                    continue
                crosses = (y0 > py) != (y1 > py)
                inside ^= crosses & (px < x0 + (py - y0) * (x1 - x0) / (y1 - y0))
        elif "ellipse" in shape:
            values = shape["ellipse"]
            angle = shape.get("angle", 0.0)
            if (not isinstance(values, list) or len(values) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) for v in values)
                    or not np.isfinite(values).all() or values[2] <= 0 or values[3] <= 0
                    or isinstance(angle, bool) or not isinstance(angle, (int, float))):
                raise ToolError(f"shapes[{k}].ellipse は [cx, cy, rx, ry]（半径は正）、angle は度の数にしてください: {shape!r}")
            cx, cy, rx, ry = values
            c, s = np.cos(np.radians(angle)), np.sin(np.radians(angle))
            along, across = (px - cx) * c + (py - cy) * s, -(px - cx) * s + (py - cy) * c
            inside = (along / rx) ** 2 + (across / ry) ** 2 <= 1.0
        else:
            raise ToolError(f"shapes[{k}] は polygon か ellipse にしてください: {shape!r}")
        mask[inside] = value
    if feather:
        blurred = Image.fromarray(np.rint(mask).astype(np.uint8)).filter(ImageFilter.GaussianBlur(feather))
        mask = np.asarray(blurred, dtype=np.float64)
    return mask


def sample(image, xy, wrap=False):
    """画像 (H,W,C) を連続の画素の座標 (K,2)（画素の中心は +0.5）で双線形に引く。wrap=False なら外は端の色で、
    外かどうか (K,) も返す。"""
    height, width = image.shape[:2]
    x, y = xy[:, 0] - 0.5, xy[:, 1] - 0.5
    x0, y0 = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = (x - x0)[:, None], (y - y0)[:, None]
    outside = (xy[:, 0] < 0) | (xy[:, 1] < 0) | (xy[:, 0] > width) | (xy[:, 1] > height)

    def at(ix, iy):
        if wrap:
            return image[iy % height, ix % width]
        return image[np.clip(iy, 0, height - 1), np.clip(ix, 0, width - 1)]

    color = (at(x0, y0) * (1 - fx) * (1 - fy) + at(x0 + 1, y0) * fx * (1 - fy)
             + at(x0, y0 + 1) * (1 - fx) * fy + at(x0 + 1, y0 + 1) * fx * fy)
    return color, outside


def texel_coordinates(uv, width, height):
    """uv (…,2) をテクスチャの画素の座標へ（v は上が 1。繰り返しは折り返さない。引くときに sample の wrap で回す）。"""
    uv = np.asarray(uv, dtype=np.float64)
    return np.stack([uv[..., 0] * width, (1.0 - uv[..., 1]) * height], axis=-1)


def tile_copies(corners, width, height):
    """テクスチャの画素の座標の三角形 (T,3,2) を、重なる繰り返しの 1 枚ごとに基本の 1 枚へずらした写し (C,3,2) と、元の番号 (C,)。
    頂点ごとに 1 で割った余りを取ると u = 1 ちょうどの角が 0 へ飛んで三角形が潰れ、三角形ごとに 1 枚へずらすだけでは
    u が 0.75〜1.25 のような三角形の 1 を超えた部分（0〜0.25 の画素）が欠ける。ほとんどの三角形は写し 1 つ。"""
    corners = np.asarray(corners, dtype=np.float64).reshape(-1, 3, 2)
    size = np.array([width, height], dtype=np.float64)
    first = np.floor(corners.min(axis=1) / size).astype(np.int64)
    last = np.maximum(np.ceil(corners.max(axis=1) / size).astype(np.int64) - 1, first)
    single = (last == first).all(axis=1)
    parts = [corners[single] - (first[single] * size)[:, None, :]]
    owners = [np.flatnonzero(single)]
    for t in np.flatnonzero(~single):   # 1 をまたぐ三角形（繰り返しの uv）だけ、重なる 1 枚ごとに写す
        for kx in range(first[t, 0], last[t, 0] + 1):
            for ky in range(first[t, 1], last[t, 1] + 1):
                parts.append(corners[t][None] - np.array([kx, ky]) * size)
                owners.append(np.array([t]))
    return np.concatenate(parts), np.concatenate(owners)


def stretch(corners, points):
    """三角形ごとの（画素での周長 / 3D の周長）を、その中央値で割った値。uv が島をまたいで引き伸ばされた三角形ほど大きい。
    3D で潰れた三角形は inf。"""
    texel = np.linalg.norm(corners - np.roll(corners, 1, axis=1), axis=2).sum(axis=1)
    length = np.linalg.norm(points - np.roll(points, 1, axis=1), axis=2).sum(axis=1)
    density = np.divide(texel, length, out=np.full(len(texel), np.inf), where=length > 0)
    usable = density[np.isfinite(density) & (density > 0)]
    return density / np.median(usable) if len(usable) else np.zeros(len(density))


# ---- 位置合わせ ------------------------------------------------------------------------

def alignment(model_points, reference_points, kind="similarity"):
    """参照画像の画素 → 視点の画素 の変換 (A, t) と報告。2 点の相似変換は厳密に、3 点以上は最小二乗（相似か affine）。"""
    try:
        target = np.asarray(model_points, dtype=np.float64).reshape(-1, 2)
        source = np.asarray(reference_points, dtype=np.float64).reshape(-1, 2)
    except (TypeError, ValueError) as error:
        raise ToolError(f"目印は [[x, y], …] の数の列にしてください（{error}）") from error
    if len(target) != len(source) or len(target) < 2 or not (np.isfinite(target).all() and np.isfinite(source).all()):
        raise ToolError("目印は視点と参照画像で同じ数（2 点以上）の有限の座標にしてください")
    if kind not in ("similarity", "affine"):
        raise ToolError(f"alignment.kind は similarity か affine です: {kind!r}")
    if kind == "affine":
        if len(target) < 3:
            raise ToolError("affine の位置合わせには 3 点以上の目印が要ります")
        design = np.hstack([source, np.ones((len(source), 1))])
        solution, _, rank, _ = np.linalg.lstsq(design, target, rcond=None)
        if rank < 3:
            raise ToolError("affine の目印が一直線に並んでいます")
        matrix, offset = solution[:2].T, solution[2]
    else:
        mean_s, mean_t = source.mean(axis=0), target.mean(axis=0)
        cs, ct = source - mean_s, target - mean_t
        variance = (cs ** 2).sum() / len(source)
        if variance <= 0:
            raise ToolError("参照画像の目印が 1 点に重なっています")
        u, s, vt = np.linalg.svd(ct.T @ cs / len(source))
        d = np.diag([1.0, np.sign(np.linalg.det(u @ vt)) or 1.0])
        rotation = u @ d @ vt
        scale = np.trace(np.diag(s) @ d) / variance
        matrix, offset = scale * rotation, mean_t - scale * rotation @ mean_s
    if abs(np.linalg.det(matrix)) < 1e-12:
        raise ToolError("位置合わせが退化しています（目印を確かめてください）")
    residual = np.linalg.norm(source @ matrix.T + offset - target, axis=1)
    report = {"kind": kind, "points": len(target), "matrix": matrix.tolist(), "offset": offset.tolist(),
              "scale": float(np.sqrt(abs(np.linalg.det(matrix)))), "max_residual_px": float(residual.max())}
    return matrix, offset, report


# ---- 法線・描画・投影 ---------------------------------------------------------------------

def vertex_normals(mesh, triangles):
    """頂点の法線。mesh の x_normals（頂点ごと）があればそれを、無ければ三角形の面積で重みを付けた平均。"""
    given = mesh.extra.get("x_normals")
    if isinstance(given, list) and len(given) == mesh.vertex_count:
        normals = np.asarray(given, dtype=np.float64).reshape(-1, 3)
    else:
        points = mesh.vertices[triangles]
        face = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
        normals = np.zeros_like(mesh.vertices)
        for corner in range(3):
            np.add.at(normals, triangles[:, corner], face)
    length = np.linalg.norm(normals, axis=1, keepdims=True)
    return normals / np.where(length > 0, length, 1.0)


def render_view(mesh, texture, view, background=(245.0, 245.0, 245.0)):
    """テクスチャを貼ったメッシュを正射影で描いた画像 (H,W,3) と報告（照明なし。色の確認と目印を読むため）。"""
    if mesh.uv is None:
        raise ToolError("メッシュに頂点ごとの uv がありません（テクスチャを貼れません）")
    require_view_axes(mesh)
    triangles, _ = mesh.triangles()
    width, height = check_view(view)[5]
    points, depth = project_points(mesh.vertices, view)
    _, (index, x, y, bary) = zbuffer(points, depth, triangles, width, height)
    image = np.empty((height, width, 3))
    image[:] = background
    if len(index):
        uv = np.einsum("kj,kjc->kc", bary, mesh.uv[triangles[index]])
        color, _ = sample(texture[..., :3], texel_coordinates(uv, texture.shape[1], texture.shape[0]), wrap=True)
        image[y, x] = color
    return image, {"kind": "render_view", "view": dict(view), "covered_pixels": int(len(index)),
                   "triangles": int(len(triangles))}


def aligned_overlay(mesh, texture, reference, view, matrix, offset, mask=None, opacity=0.5):
    """位置合わせの確認用: メッシュの正射影の描画に、視点へ合わせた参照画像を重ねた画像 (H,W,3)。
    参照画像の重み（マスク × アルファ × opacity）の所だけ混ぜる。"""
    image, _ = render_view(mesh, texture, view)
    height, width = image.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width]
    at = np.stack([xs.ravel() + 0.5, ys.ravel() + 0.5], axis=1)
    source = (at - offset) @ np.linalg.inv(matrix).T
    color, outside = sample(reference, source)
    weight = (~outside) * opacity
    if reference.shape[2] == 4:
        weight = weight * color[:, 3] / 255.0
    if mask is not None:
        weight = weight * sample(mask[..., None], source)[0][:, 0] / 255.0
    mixed = image.reshape(-1, 3) * (1 - weight[:, None]) + color[:, :3] * weight[:, None]
    return mixed.reshape(height, width, 3)


def project_reference(mesh, texture, reference, view, matrix, offset, faces=None, mask=None, min_facing=0.2,
                      facing_ramp=0.3, depth_tolerance=0.002, expand=1.0, max_stretch=8.0, occlusion=None):
    """参照画像をテクスチャへ写した新しいテクスチャ（texture と同じ (H,W,3) か (H,W,4)。アルファは変えない）、画素ごとの重み (H,W)、報告。

    faces（面の番号）があれば、その面の上の画素だけを塗る（見え隠れはメッシュ全体で判定する）。matrix・offset は
    `alignment` の 参照画像 → 視点 の変換。reference は RGB か RGBA（アルファも重みに掛ける）、mask は参照画像と同じ大きさの
    0〜255 の重み（任意）。max_stretch は `stretch` がこれを超える三角形を塗らない上限。
    occlusion（任意）は view_parts の手前の部品の地図 (owner (H,W), depth (H,W), 自分の番号, 名前の並び)。画素の手前に別の部品があり、
    その奥行きが点より depth_tolerance を超えて手前なら、その点は隠れる（ほかの部品に隠れた所へ参照画像を写さない）。
    """
    if mesh.uv is None:
        raise ToolError("メッシュに頂点ごとの uv がありません（テクスチャへ写せません）")
    require_view_axes(mesh)
    if facing_ramp <= 0 or depth_tolerance < 0 or expand < 0 or max_stretch < 1:
        raise ToolError("facing_ramp は正、depth_tolerance と expand は 0 以上、max_stretch は 1 以上にしてください")
    if mask is not None and mask.shape[:2] != reference.shape[:2]:
        raise ToolError(f"mask の大きさ {mask.shape[1]}x{mask.shape[0]} が参照画像と違います")
    triangles, owner = mesh.triangles()
    _, _, forward, _, _, (width, height) = check_view(view)
    points, depth = project_points(mesh.vertices, view)
    _, (win_index, win_x, win_y, _) = zbuffer(points, depth, triangles, width, height)
    front = np.full(width * height, -1, dtype=np.int64)   # 画素ごとのいちばん手前の三角形（無ければ -1）
    front[win_y * width + win_x] = win_index
    plane_origin, plane_gradient, plane_depth = depth_planes(points, depth, triangles)
    chosen = np.isin(owner, np.asarray(sorted(set(faces)), dtype=np.int64)) if faces is not None else np.ones(len(triangles), bool)
    candidates = triangles[chosen]
    tex_h, tex_w = texture.shape[:2]
    corners = texel_coordinates(mesh.uv[candidates], tex_w, tex_h)
    stretched = stretch(corners, mesh.vertices[candidates]) > max_stretch
    candidates, corners = candidates[~stretched], corners[~stretched]
    copies, copied_from = tile_copies(corners, tex_w, tex_h)
    index, tx, ty, bary = rasterize(copies, tex_w, tex_h, expand)
    index = copied_from[index]
    given = mesh.extra.get("x_normals")
    report = {"kind": "project_reference", "view": dict(view),
              "normals": "x_normals" if isinstance(given, list) and len(given) == mesh.vertex_count else "computed",
              "candidate_triangles": int(len(candidates)),
              "skipped_stretched_triangles": int(stretched.sum()), "tile_copies": int(len(copies)), "fragments": int(len(index))}
    new = texture[..., :3].copy()
    weights = np.zeros((tex_h, tex_w))

    def with_alpha(rgb):   # 色だけを塗り、アルファ（透明な所）はそのまま戻す
        return np.concatenate([rgb, texture[..., 3:4]], axis=2) if texture.shape[2] == 4 else rgb

    if not len(index):
        report.update(painted_texels=0, full_weight_texels=0, visible_fragments=0)
        return with_alpha(new), weights, report
    corner_ids = candidates[index]
    surface = np.einsum("kj,kjc->kc", bary, mesh.vertices[corner_ids])
    normal = np.einsum("kj,kjc->kc", bary, vertex_normals(mesh, triangles)[corner_ids])
    normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-12)
    at, z = project_points(surface, view)
    px, py = np.floor(at[:, 0]).astype(np.int64), np.floor(at[:, 1]).astype(np.int64)
    on_view = (px >= 0) & (py >= 0) & (px < width) & (py < height)
    nearest = np.full(len(index), -1, dtype=np.int64)
    nearest[on_view] = front[py[on_view] * width + px[on_view]]
    visible = on_view & (nearest < 0)   # 画素の中心を覆う面が無い（縁の）点は、隠すものが無い
    covered = np.flatnonzero(nearest >= 0)
    k = nearest[covered]
    surface_z = plane_depth[k] + ((at[covered] - plane_origin[k]) * plane_gradient[k]).sum(axis=1)
    visible[covered] = z[covered] <= surface_z + depth_tolerance
    if occlusion is not None:                            # ほかの部品に隠れた点（部品の地図の、画素の中心の奥行きで比べる）
        occ_owner, occ_depth, self_index, occ_names = occlusion
        part = np.full(len(index), -1, dtype=np.int64)
        part_depth = np.full(len(index), np.inf)
        part[on_view] = occ_owner[py[on_view], px[on_view]]
        part_depth[on_view] = occ_depth[py[on_view], px[on_view]]
        hidden = on_view & (part >= 0) & (part != self_index) & (part_depth < z - depth_tolerance)
        visible &= ~hidden
        counts = np.bincount(part[hidden], minlength=len(occ_names)) if hidden.any() else np.zeros(len(occ_names), dtype=np.int64)
        report["occluded_fragments"] = int(hidden.sum())
        report["occluded_by"] = {occ_names[i]: int(c) for i, c in enumerate(counts) if c}
    facing = np.clip((-(normal @ forward) - min_facing) / facing_ramp, 0.0, 1.0)
    source = (at - offset) @ np.linalg.inv(matrix).T
    color, outside = sample(reference, source)
    weight = visible * facing * ~outside
    if reference.shape[2] == 4:
        weight = weight * color[:, 3] / 255.0
    if mask is not None:
        weight = weight * sample(mask[..., None], source)[0][:, 0] / 255.0
    texel = ty * tex_w + tx
    unique, slot = np.unique(texel, return_inverse=True)
    count = np.bincount(slot, minlength=len(unique)).astype(np.float64)
    total = np.bincount(slot, weights=weight, minlength=len(unique))
    painted = total > 1e-9
    alpha = np.clip(total / count, 0.0, 1.0)
    mixed = np.stack([np.bincount(slot, weights=weight * color[:, c], minlength=len(unique)) for c in range(3)], axis=1)
    mixed[painted] /= total[painted, None]
    rows, cols = unique // tex_w, unique % tex_w
    keep = painted
    a = alpha[keep, None]
    new[rows[keep], cols[keep]] = new[rows[keep], cols[keep]] * (1 - a) + mixed[keep] * a
    weights[rows[keep], cols[keep]] = alpha[keep]
    report.update(painted_texels=int(keep.sum()), full_weight_texels=int((alpha[keep] > 0.99).sum()),
                  visible_fragments=int(visible.sum()), facing_fragments=int((facing > 0).sum()),
                  inside_reference_fragments=int((~outside).sum()))
    return with_alpha(new), weights, report


# ---- 手前の部品の地図 ------------------------------------------------------------------------

PART_PALETTE = np.array([[230, 80, 80], [80, 160, 230], [120, 200, 120], [240, 200, 90], [180, 120, 220], [90, 200, 200],
                         [200, 140, 90], [140, 140, 140], [220, 120, 180], [100, 100, 200], [160, 200, 80]], dtype=float)


def view_parts(parts, view, margin=2.0):
    """部品 [(名前, 頂点 (N,3), 三角形 (T,3)), …] を視点の正射影で描き、画素ごとにいちばん手前の部品の番号 (H,W)（無い画素は -1）と
    奥行き (H,W)（無い画素は inf）、部品ごとの数を返す。視点の窓（とその周り margin 画素）にかかる三角形だけを描く
    （三角形の範囲で選ぶので、角が全部窓の外でも窓をまたぐ三角形は描く）。z バッファは texture.zbuffer（同じ入力なら同じ勝者）。"""
    width, height = check_view(view)[5]
    points, depths, triangles, owner, rows = [], [], [], [], []
    offset = 0
    for i, (name, vertices, faces) in enumerate(parts):
        p, d = project_points(np.asarray(vertices, dtype=np.float64), view)
        faces = np.asarray(faces, dtype=np.int64).reshape(-1, 3)
        corners = p[faces] if len(faces) else np.zeros((0, 3, 2))
        low, high = corners.min(axis=1), corners.max(axis=1)
        keep = ((high[:, 0] >= -margin) & (low[:, 0] <= width + margin) & (high[:, 1] >= -margin) & (low[:, 1] <= height + margin)
                if len(faces) else np.zeros(0, dtype=bool))
        points.append(p)
        depths.append(d)
        triangles.append(faces[keep] + offset)
        owner.append(np.full(int(keep.sum()), i, dtype=np.int64))
        rows.append({"index": i, "name": name, "triangles": int(len(faces)), "triangles_in_view": int(keep.sum())})
        offset += len(p)
    points = np.concatenate(points) if points else np.zeros((0, 2))
    depths = np.concatenate(depths) if depths else np.zeros(0)
    triangles = np.concatenate(triangles) if triangles else np.zeros((0, 3), dtype=np.int64)
    owner = np.concatenate(owner) if owner else np.zeros(0, dtype=np.int64)
    buffer, (index, x, y, _) = zbuffer(points, depths, triangles, width, height)
    owners = np.full((height, width), -1, dtype=np.int32)
    owners[y, x] = owner[index]
    front = np.bincount(owner[index], minlength=len(parts)) if len(index) else np.zeros(len(parts), dtype=np.int64)
    for row in rows:
        row["front_pixels"] = int(front[row["index"]])
    return owners, buffer, {"view": dict(view), "covered_pixels": int(len(index)), "parts": rows, "margin": float(margin)}


def parts_preview(owners, background=245.0):
    """手前の部品の地図を色で描いた画像 (H,W,3)（部品の番号ごとの色。11 色を繰り返す。無い画素は background）。"""
    image = np.full(owners.shape + (3,), float(background))
    covered = owners >= 0
    image[covered] = PART_PALETTE[owners[covered] % len(PART_PALETTE)]
    return image


# ---- 色合わせ（CIELAB） -----------------------------------------------------------------------

SRGB_TO_XYZ = np.array([[0.4124564, 0.3575761, 0.1804375], [0.2126729, 0.7151522, 0.0721750], [0.0193339, 0.1191920, 0.9503041]])
D65_WHITE = np.array([0.95047, 1.0, 1.08883])


def rgb_to_lab(rgb):
    """sRGB（0〜255、(...,3)）を CIELAB（D65。L は 0〜100、a と b は 0 が無彩色）へ。"""
    c = np.asarray(rgb, dtype=np.float64) / 255.0
    linear = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    t = (linear @ SRGB_TO_XYZ.T) / D65_WHITE
    f = np.where(t > (6 / 29) ** 3, np.cbrt(t), t / (3 * (6 / 29) ** 2) + 4 / 29)
    return np.stack([116 * f[..., 1] - 16, 500 * (f[..., 0] - f[..., 1]), 200 * (f[..., 1] - f[..., 2])], axis=-1)


def lab_to_rgb(lab):
    """CIELAB（D65）を sRGB（0〜255 の float。範囲の外は切らずに返す）へ。"""
    lab = np.asarray(lab, dtype=np.float64)
    fy = (lab[..., 0] + 16) / 116
    f = np.stack([fy + lab[..., 1] / 500, fy, fy - lab[..., 2] / 200], axis=-1)
    t = np.where(f > 6 / 29, f ** 3, 3 * (6 / 29) ** 2 * (f - 4 / 29))
    linear = (t * D65_WHITE) @ np.linalg.inv(SRGB_TO_XYZ).T
    c = np.where(linear <= 0.0031308, 12.92 * linear, 1.055 * np.sign(linear) * np.abs(linear) ** (1 / 2.4) - 0.055)
    return c * 255.0


def lab_8bit(lab):
    """CIELAB を OpenCV の 8 ビットの Lab の目盛り（L × 255/100、a と b に +128）へ（記録との見比べ用）。"""
    lab = np.asarray(lab, dtype=np.float64)
    return np.stack([lab[..., 0] * 255 / 100, lab[..., 1] + 128, lab[..., 2] + 128], axis=-1)


def box_pixels(image, boxes, where):
    """画像 (H,W,C) の枠 [x0, y0, x1, y1]（右と下は含まない）の画素の色 (N,3) を、全部の枠からつなげて返す。"""
    height, width = image.shape[:2]
    pixels = []
    for box in boxes:
        x0, y0, x1, y1 = box
        if x0 < 0 or y0 < 0 or x1 > width or y1 > height or x1 <= x0 or y1 <= y0:
            raise ToolError(f"{where} の枠 {list(box)} が画像（{width}x{height}）の外か、空です")
        pixels.append(np.asarray(image[y0:y1, x0:x1, :3], dtype=np.float64).reshape(-1, 3))
    return np.vstack(pixels)


def color_match(reference, model_image, model_boxes, reference_boxes, keep=None):
    """参照画像の色を、モデルの色に合わせる。モデルの描画（視点の画素）の枠と、参照画像の枠の色の CIELAB の中央値の差を、
    参照画像の全部の画素の CIELAB に足して sRGB に戻す（0〜255 に切る。アルファは変えない）。keep（参照画像と同じ大きさの 0〜255）を
    渡すと、keep の外はモデルの中央値の 1 色で塗る（keep の重みで混ぜる）。戻り: (合わせた参照画像, 報告)。"""
    model_lab = np.median(rgb_to_lab(box_pixels(model_image, model_boxes, "color_match.model")), axis=0)
    reference_lab = np.median(rgb_to_lab(box_pixels(reference, reference_boxes, "color_match.reference")), axis=0)
    offset = model_lab - reference_lab
    raw = lab_to_rgb(rgb_to_lab(reference[..., :3]) + offset)
    clipped = float(((raw < 0) | (raw > 255)).any(axis=-1).mean())
    corrected = np.clip(raw, 0, 255)
    fill = np.clip(lab_to_rgb(model_lab), 0, 255)
    if keep is not None:
        if keep.shape[:2] != reference.shape[:2]:
            raise ToolError(f"keep の大きさ {keep.shape[1]}x{keep.shape[0]} が参照画像と違います")
        k = np.asarray(keep, dtype=np.float64)
        k = (k[..., 0] if k.ndim == 3 else k)[..., None] / 255.0
        corrected = corrected * k + fill * (1 - k)
    out = np.array(reference, dtype=np.float64)
    out[..., :3] = corrected
    report = {"model_lab": [round(float(v), 4) for v in model_lab], "reference_lab": [round(float(v), 4) for v in reference_lab],
              "offset": [round(float(v), 4) for v in offset], "model_lab_8bit": [round(float(v), 2) for v in lab_8bit(model_lab)],
              "reference_lab_8bit": [round(float(v), 2) for v in lab_8bit(reference_lab)],
              "model_pixels": int(sum((b[2] - b[0]) * (b[3] - b[1]) for b in model_boxes)),
              "reference_pixels": int(sum((b[2] - b[0]) * (b[3] - b[1]) for b in reference_boxes)),
              "clipped_fraction": round(clipped, 6), "fill_rgb": [round(float(v), 3) for v in fill], "keep": keep is not None}
    return out, report
