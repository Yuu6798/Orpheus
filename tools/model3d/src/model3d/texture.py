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


def rasterize(triangles, width, height, expand=0.0):
    """三角形 (T,3,2)（画素の座標）が覆う画素。画素の中心が三角形の中（辺の上を含む）にあれば覆うと数える。

    返り値は 三角形の番号 (K,)、画素の x・y (K,)、重心座標 (K,3)。退化した三角形と画像の外は飛ばす。
    expand は三角形を外側へ広げる画素数（UV の継ぎ目で色が欠けないため）で、重心座標は元の三角形で求める（端では少し外挿）。
    """
    tri = np.asarray(triangles, dtype=np.float64).reshape(-1, 3, 2)
    empty = (np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros(0, np.int64), np.zeros((0, 3)))
    if not len(tri):
        return empty
    shape = tri
    if expand:
        center = tri.mean(axis=1, keepdims=True)
        reach = np.maximum(np.linalg.norm(tri - center, axis=2).min(axis=1), 0.5)
        shape = center + (tri - center) * (1.0 + expand / reach)[:, None, None]
    lo = np.maximum(np.floor(shape.min(axis=1) - 0.5).astype(np.int64), 0)
    hi = np.minimum(np.ceil(shape.max(axis=1) - 0.5).astype(np.int64), [width - 1, height - 1])
    origin_s, inverse_s, ok_s = _inverse(shape)
    origin_o, inverse_o, ok_o = _inverse(tri)
    usable = ok_s & ok_o & (hi >= lo).all(axis=1) & np.isfinite(tri).all(axis=(1, 2))
    extent = (hi - lo + 1).max(axis=1)
    parts = []

    def emit(ids, pixels):
        within = (pixels <= hi[ids][:, None, :]).all(axis=2)
        centres = pixels + 0.5
        shaped = _weights(centres, origin_s[ids], inverse_s[ids])
        inside = within & (shaped.min(axis=2) >= -1e-9)
        if not inside.any():
            return
        which, slot = np.nonzero(inside)
        bary = (_weights(centres, origin_o[ids], inverse_o[ids]) if expand else shaped)[which, slot]
        parts.append((ids[which], pixels[which, slot, 0], pixels[which, slot, 1], bary))

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


def load_mask(path):
    """重みの画像を float の (H,W) で読む（灰色に直す。0〜255）。"""
    try:
        with Image.open(path) as im:
            return np.asarray(im.convert("L"), dtype=np.float64)
    except OSError as error:
        raise ToolError(f"画像を読めません: {path}（{error}）") from error


def save_image(path, array):
    """(H,W,3) は RGB、(H,W) は灰色の PNG として書く（0〜255 に丸める）。"""
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
                      facing_ramp=0.3, depth_tolerance=0.002, expand=1.0, max_stretch=8.0):
    """参照画像をテクスチャへ写した新しいテクスチャ (H,W,3)、画素ごとの重み (H,W)、報告。

    faces（面の番号）があれば、その面の上の画素だけを塗る（見え隠れはメッシュ全体で判定する）。matrix・offset は
    `alignment` の 参照画像 → 視点 の変換。reference は RGB か RGBA（アルファも重みに掛ける）、mask は参照画像と同じ大きさの
    0〜255 の重み（任意）。max_stretch は `stretch` がこれを超える三角形を塗らない上限。
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
    report = {"kind": "project_reference", "view": dict(view), "candidate_triangles": int(len(candidates)),
              "skipped_stretched_triangles": int(stretched.sum()), "tile_copies": int(len(copies)), "fragments": int(len(index))}
    new = texture[..., :3].copy()
    weights = np.zeros((tex_h, tex_w))
    if not len(index):
        report.update(painted_texels=0, full_weight_texels=0, visible_fragments=0)
        return new, weights, report
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
    return new, weights, report
