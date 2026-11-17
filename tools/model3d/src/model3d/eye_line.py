"""閉じた目の線を、2D の描き方（下にたわんだ太い弧・目尻のはね・下向きのまつ毛）で描き直す（texture.closed_eye_line。Blender なし）。

リポジトリ外の 2 作目の VRM のダンスで、目を閉じた顔の絵（別の担当が描いた差分）の閉じた目の線が細く薄かった（目の幅の 2.5%）。線を太らせる
方法では形が足りなかったので、元の線を消して描き直した台本を移した。numpy と Pillow だけ（model3d は OpenCV に頼らない）。台本は OpenCV で
描いたので、線の縁の塗り（アンチエイリアス）が少し違う（docs/model3d.md の数）。

1. 元の線を消す: 消す帯（erase_rows × erase_columns）の中を、まわりの肌の色で埋める。肌の色は、絵を楕円の窓（close_px）で「閉じて」（最大の窓の
   あと最小の窓。暗い細い線が消える）σ close_blur でぼかしたもの。帯の縁は σ band_blur でなめらかにつなぐ。
2. 線を描く（supersample 倍の細かさで塗ってから、面積の平均で縮めて縁をなめらかに）: 目ごと（[目頭の x, 目尻の x]）に、
   - 弧: 両端の高さ end_y、下の縁が中央で sag だけ下にたわみ、太さ thickness（目尻寄りに outer_bias。両端へ細く）。
   - 目尻のはね: 長さ wing.length・上向きの角 wing.angle_deg・根もとの幅 wing.width の先細りの線。
   - まつ毛: 弧の下の縁の at（0 が中央、1 が目尻）の位置から、長さ length、根もとの幅 width、目尻ほど外へ寝かせた lean_deg + 30 × at の向き。
3. 線の色 color（[R, G, B]）で、塗った割合だけ混ぜる。
長さと位置は、幅 base_width（既定は絵の幅）の絵の画素で書く（絵の幅が違えば伸ばす）。
"""
import math

import numpy as np
from PIL import Image, ImageDraw

DEFAULTS = {"sag": 21.0, "thickness": 13.0, "outer_bias": 0.25,
            "wing": {"length": 17.0, "angle_deg": 28.0, "width": 6.0},
            "lashes": {"at": [0.05, 0.30, 0.52, 0.70, 0.86], "length": [8, 10, 12, 12, 10], "width": 4.0, "lean_deg": 22.0},
            "color": [44, 24, 22], "supersample": 4, "close_px": 21, "close_blur": 1.2, "band_blur": 3.0, "base_width": None}


def ellipse_kernel(size):
    """size × size の楕円の窓（OpenCV の getStructuringElement(MORPH_ELLIPSE) と同じ形）。"""
    r = c = size // 2
    inverse = 1.0 / (r * r) if r else 0.0
    kernel = np.zeros((size, size), dtype=bool)
    for i in range(size):
        dy = i - r
        if abs(dy) <= r:
            dx = int(np.rint(c * math.sqrt((r * r - dy * dy) * inverse)))
            kernel[i, max(c - dx, 0):min(c + dx + 1, size)] = True
    return kernel


def morph(image, kernel, take):
    """窓の中の最大（take=np.maximum。膨らませる）か最小（np.minimum。縮める）。画像の外は数えない。"""
    size = kernel.shape[0]
    half = size // 2
    fill = -np.inf if take is np.maximum else np.inf
    pad = [(half, half), (half, half)] + [(0, 0)] * (image.ndim - 2)
    padded = np.pad(image.astype(np.float64), pad, mode="constant", constant_values=fill)
    height, width = image.shape[:2]
    out = np.full(image.shape, fill, dtype=np.float64)
    for dy, dx in zip(*np.nonzero(kernel)):
        out = take(out, padded[dy:dy + height, dx:dx + width])
    return out


def gaussian(image, sigma):
    """σ sigma のガウスのぼかし（窓は OpenCV の浮動小数の画像と同じ round(8σ + 1) の奇数、縁は鏡写しで端の画素を重ねない）。"""
    size = int(round(sigma * 8 + 1)) | 1
    half = size // 2
    x = np.arange(size, dtype=np.float64) - half
    kernel = np.exp(-(x * x) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    out = image.astype(np.float64)
    for axis in (1, 0):
        pad = [(0, 0)] * out.ndim
        pad[axis] = (half, half)
        padded = np.pad(out, pad, mode="reflect")
        length = out.shape[axis]
        out = sum(kernel[i] * np.take(padded, range(i, i + length), axis=axis) for i in range(size))
    return out


def erase_band(image, settings, scale):
    """消す帯の中を肌の色で埋めた絵 (H, W, 3)（帯から遠い所は元の絵のまま。計算は帯のまわりだけ）。"""
    height, width = image.shape[:2]
    (y0, y1), (x0, x1) = settings["erase_rows"], settings["erase_columns"]
    rows = (int(y0 * scale), int(y1 * scale))
    cols = (int(x0 * scale), int(x1 * scale))
    close = int(settings["close_px"] * scale) | 1
    band_sigma = settings["band_blur"] * scale
    skin_sigma = settings["close_blur"] * scale
    reach = (int(round(band_sigma * 8 + 1)) | 1) // 2 + 1                 # 帯のぼかしが届く所
    margin = reach + close + (int(round(skin_sigma * 8 + 1)) | 1)        # 肌の色の計算が要る所
    top, bottom = max(rows[0] - margin, 0), min(rows[1] + margin, height)
    left, right = max(cols[0] - margin, 0), min(cols[1] + margin, width)
    patch = image[top:bottom, left:right]
    kernel = ellipse_kernel(close)
    skin = gaussian(morph(morph(patch, kernel, np.maximum), kernel, np.minimum), skin_sigma)
    band = np.zeros((height, width), dtype=np.float64)
    band[rows[0]:rows[1], cols[0]:cols[1]] = 1.0
    band = gaussian(band, band_sigma)[top:bottom, left:right, None]
    out = image.astype(np.float64).copy()
    out[top:bottom, left:right] = patch * (1 - band) + skin * band
    return out


def shapes(settings):
    """描く多角形の並び（幅 base_width の絵の画素）。"""
    polygons = []

    def stroke(start, angle_deg, length, base_width):
        direction = np.array([math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))])
        normal = np.array([-direction[1], direction[0]])
        start = np.array(start, float)
        polygons.append([start - normal * base_width / 2, start + normal * base_width / 2, start + direction * length])

    for inner_x, outer_x in settings["eyes"]:
        centre = (inner_x + outer_x) / 2
        half = abs(outer_x - inner_x) / 2
        side = 1.0 if outer_x > inner_x else -1.0
        upper, lower = [], []
        for u in np.linspace(-1, 1, 81):
            x = centre + side * u * half
            bottom = settings["end_y"] + settings["sag"] * (1 - u * u)
            thick = settings["thickness"] * (1 - abs(u) ** 2.4) * (1 + settings["outer_bias"] * u) + 1.2
            lower.append((x, bottom))
            upper.append((x, bottom - thick))
        polygons.append(upper + lower[::-1])
        wing = settings["wing"]
        angle = -wing["angle_deg"] if side > 0 else 180 + wing["angle_deg"]
        stroke((outer_x - side * 3, settings["end_y"] - 0.5), angle, wing["length"], wing["width"])
        lashes = settings["lashes"]
        for u, length in zip(lashes["at"], lashes["length"]):
            x = centre + side * u * half
            bottom = settings["end_y"] + settings["sag"] * (1 - u * u) - 1.5
            lean = lashes["lean_deg"] + 30 * u
            stroke((x, bottom), 90 - lean if side > 0 else 90 + lean, length, lashes["width"])
    return polygons


def line_alpha(polygons, width, height, scale, supersample):
    """多角形を supersample 倍の細かさで塗り（輪郭も 1 画素でなぞる）、面積の平均で縮めた割合 (H, W)（0〜1）。"""
    canvas = Image.new("L", (width * supersample, height * supersample), 0)
    draw = ImageDraw.Draw(canvas)
    for points in polygons:
        corners = np.round(np.array(points, dtype=np.float64) * scale * supersample).astype(np.int64)
        points = [tuple(int(v) for v in p) for p in corners]
        draw.polygon(points, fill=255)
        draw.line(points + [points[0]], fill=255, width=1)          # 輪郭もなぞる（台本の OpenCV の縁のなめらかな塗りに近い）
    mask = np.asarray(canvas, dtype=np.float64).reshape(height, supersample, width, supersample)
    return np.rint(mask.mean(axis=(1, 3))) / 255.0                       # 8 ビットに丸める（台本の縮め方と同じ）


def draw(image, settings):
    """閉じた目の線を描き直した絵 (H, W, 3)（0〜255、丸める前）と報告。image は (H, W, 3) の RGB（0〜255）。"""
    height, width = image.shape[:2]
    scale = width / (settings.get("base_width") or width)
    base = erase_band(image, settings, scale)
    alpha = line_alpha(shapes(settings), width, height, scale, int(settings["supersample"]))[..., None]
    color = np.array(settings["color"], dtype=np.float64)
    result = base * (1 - alpha) + color * alpha
    changed = np.abs(result - image).max(axis=2) > 2
    ys, xs = np.nonzero(changed)
    box = [int(xs.min() / scale), int(ys.min() / scale), int(xs.max() / scale), int(ys.max() / scale)] if len(xs) else None
    return result, {"changed_box": box, "changed_pixels": int(changed.sum()), "line_pixels": int((alpha[..., 0] > 0.5).sum()),
                    "thickness": settings["thickness"], "sag": settings["sag"], "scale": scale}
