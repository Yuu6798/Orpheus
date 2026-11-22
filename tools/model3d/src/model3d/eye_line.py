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

足した描き方（4 作目の 3D のダンスで、顔が 1 枚の色の絵のモデルの表情を描いた台本から移した。省けば今までとファイルまで同じ）:
- sag を負にすると、弧が上へたわむ（笑顔の閉じ目）。
- strokes: 点の列に沿って太さの変わる線（目尻側を太くしたアイライン・まぶたのしわ・弧の下のうすい色・まつ毛・目尻のはね）。3 点と curve なら、
  両端を通り真ん中で 2 点目を通る 2 次の曲線。太さは [位置 0〜1, 太さ] の折れ線か、{"middle", "ends"}（真ん中が太い弓の形）。線の範囲だけを
  supersample 倍に広げて、線ごとに前の線の上に重ね、最後に面積の平均で縮める（線の無い所は元の画素のまま）。
- skin_fill: 消し方の選択肢。目の枠（boxes）の中を、枠の外の肌らしい画素だけを重みにしたぼかしで埋める（暗い髪や切れた背景を使わない）。
  枠の外のぼかしの帯では、keep_dark_below より暗い画素（髪）の所は埋めない。半目は、枠を瞳の上までにして埋め、縁に線を描く。
"""
import math

import numpy as np
from PIL import Image, ImageDraw

DEFAULTS = {"sag": 21.0, "thickness": 13.0, "outer_bias": 0.25,
            "wing": {"length": 17.0, "angle_deg": 28.0, "width": 6.0},
            "lashes": {"at": [0.05, 0.30, 0.52, 0.70, 0.86], "length": [8, 10, 12, 12, 10], "width": 4.0, "lean_deg": 22.0},
            "color": [44, 24, 22], "supersample": 4, "close_px": 21, "close_blur": 1.2, "band_blur": 3.0, "base_width": None}
# 肌で埋める消し方の、計算の作りの既定（重みの強さ・範囲の縁のぼかし。台本を 0.1 mm/画素の描画で回したときの値で、base_width で伸ばす）。
# 顔と目の大きさに寄る値（枠 boxes・肌らしさの決まり skin・肌の色を探す広さ sigmas・肌の色を取らない幅 exclude・枠を広げる幅 grow）は
# 既定を置かず、工程で必ず書く。
SKIN_FILL_DEFAULTS = {"keep_dark_below": None, "gain": 8.0, "feather": 7.0, "soften": 1.5, "exclude_all": False}
SKIN_FILL_REQUIRED = ("boxes", "skin", "sigmas", "exclude", "grow")
STROKE_DEFAULTS = {"curve": False, "samples": 120, "span": None, "color": None}


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
    rows = (min(max(int(y0 * scale), 0), height), min(max(int(y1 * scale), 0), height))   # 絵の範囲に切り詰める（負の座標を
    cols = (min(max(int(x0 * scale), 0), width), min(max(int(x1 * scale), 0), width))      # 後ろからの添字にしない）
    if rows[1] <= rows[0] or cols[1] <= cols[0]:
        raise ValueError(f"消す帯（erase_rows {settings['erase_rows']}・erase_columns {settings['erase_columns']}）が、絵（{width}×{height}）の"
                         "外です")
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


def wide_gaussian(image, sigma):
    """gaussian と同じぼかし（窓 round(8σ + 1) の奇数、縁は鏡写しで端の画素を重ねない）を、行列の積で計算する（広い σ でも速い）。"""
    size = int(round(sigma * 8 + 1)) | 1
    half = size // 2
    x = np.arange(size, dtype=np.float64) - half
    kernel = np.exp(-(x * x) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    out = image.astype(np.float64)
    for axis in (1, 0):
        length = out.shape[axis]
        index = np.arange(-half, length + half)
        if length > 1:                                                 # 鏡写し（端の画素を重ねない。長さを超える窓は折り返す）
            period = 2 * (length - 1)
            index = index % period
            index = np.where(index >= length, period - index, index)
        else:
            index = np.zeros_like(index)
        band = np.zeros((length, length))
        rows = np.arange(length)
        for k in range(size):
            np.add.at(band, (rows, index[rows + k]), kernel[k])
        out = np.moveaxis(np.moveaxis(out, axis, -1) @ band.T, -1, axis)
    return out


def skin_like(image, rule):
    """肌らしい画素 (H, W)。rule は {"rgb_min": [R, G, B]（どれもこれより大きい）, "red_minus_green_below": 数（任意。R − G がこれより小さい）,
    "red_at_least_blue": true（任意。R ≥ B）}。"""
    r, g, b = image[..., 0], image[..., 1], image[..., 2]
    low = rule["rgb_min"]
    ok = (r > low[0]) & (g > low[1]) & (b > low[2])
    if rule.get("red_minus_green_below") is not None:
        ok &= (r - g) < rule["red_minus_green_below"]
    if rule.get("red_at_least_blue"):
        ok &= r >= b
    return ok


def skin_fill(image, settings, scale):
    """目の枠（skin_fill.boxes の [x0, y0, x1, y1]。右と下は含まない）の中を、枠の外の肌らしい画素から埋めた絵 (H, W, 3) と、埋めた重み (H, W)。
    枠ごとに: 肌らしい画素（枠 ± exclude を除く。exclude_boxes があれば、その枠 ± exclude を除く）を重みにしたぼかし（sigmas の広さを狭い順に重ね、先の広さで足りた所は後の広さを使わない）で
    埋めた色を作り、枠を grow 広げて σ feather でぼかした範囲（枠の外では keep_dark_below より暗い画素を除き、σ soften でならす）で混ぜる。
    計算は枠のまわりだけ（ぼかしの窓が届く所まで読むので、絵全体でぼかしたのと同じ値）。"""
    fill = settings["skin_fill"]
    height, width = image.shape[:2]
    base = image.astype(np.float64)
    result = base.copy()
    weight_total = np.zeros((height, width))
    skin = skin_like(base, fill["skin"])
    dark = (base.max(axis=2) < fill["keep_dark_below"]) if fill.get("keep_dark_below") is not None else np.zeros((height, width), bool)
    grow, exclude = int(round(fill["grow"] * scale)), int(round(fill["exclude"] * scale))
    feather, soften = fill["feather"] * scale, fill["soften"] * scale
    sigmas = [s * scale for s in fill["sigmas"]]
    for k, box in enumerate(fill["boxes"]):
        x0, y0, x1, y1 = (int(round(v * scale)) for v in box)
        excluded = fill.get("exclude_boxes") or fill["boxes"]
        # 肌の色を取らない枠: 台本と同じく今の目の枠だけ（既定）か、exclude_all で全部の枠（ぼかしの窓がもう片方の目に届くと、その白目が
        # 肌らしい画素として混ざり、白い斑になるため）
        skip = excluded if fill.get("exclude_all") else [excluded[k]]
        reach = (int(round(feather * 8 + 1)) | 1) // 2 + (int(round(soften * 8 + 1)) | 1) // 2 + grow + 2
        top, bottom = max(y0 - reach, 0), min(y1 + reach, height)        # 埋める範囲が届く所
        left, right = max(x0 - reach, 0), min(x1 + reach, width)
        if y1 <= 0 or x1 <= 0 or y0 >= height or x0 >= width:
            raise ValueError(f"skin_fill の枠 {list(box)} が、絵（{width}×{height}）の外です")
        far = (int(round(max(sigmas) * 8 + 1)) | 1) // 2 + 1              # ぼかしの窓が届く所
        sy0, sy1, sx0, sx1 = max(top - far, 0), min(bottom + far, height), max(left - far, 0), min(right + far, width)
        valid = skin[sy0:sy1, sx0:sx1].copy()
        for other in skip:
            ex0, ey0, ex1, ey1 = (int(round(v * scale)) for v in other)
            valid[max(ey0 - exclude - sy0, 0):max(ey1 + exclude - sy0, 0), max(ex0 - exclude - sx0, 0):max(ex1 + exclude - sx0, 0)] = False
        valid = valid.astype(np.float64)
        source = base[sy0:sy1, sx0:sx1] * valid[..., None]
        filled = np.zeros(source.shape)
        weight = np.zeros(valid.shape)
        for sigma in sigmas:
            v = wide_gaussian(valid, sigma)
            c = wide_gaussian(source, sigma)
            w = np.clip(v * fill["gain"], 0, 1) * (1 - np.clip(weight, 0, 1))
            filled += c / np.maximum(v, 1e-4)[..., None] * w[..., None]
            weight += w
        inside = weight[max(y0, 0) - sy0:min(y1, height) - sy0, max(x0, 0) - sx0:min(x1, width) - sx0]
        if inside.size and inside.min() < 1e-3:                         # 肌らしい画素が、ぼかしの届く所に無い（黒へ寄せてしまう）
            raise ValueError(f"skin_fill の枠 {list(box)} の中に、肌らしい画素（skin）がぼかしの届く所（sigmas）に無い画素があります"
                             "（skin の決まりか sigmas を確かめてください）")
        filled = (filled / np.maximum(weight, 1e-4)[..., None])[top - sy0:bottom - sy0, left - sx0:right - sx0]
        area = np.zeros((bottom - top, right - left))
        area[max(0, y0 - grow - top):max(y1 + grow - top, 0), max(0, x0 - grow - left):max(x1 + grow - left, 0)] = 1.0
        area = gaussian(area, feather)
        core = np.zeros(area.shape, bool)
        core[max(y0 - top, 0):max(y1 - top, 0), max(x0 - left, 0):max(x1 - left, 0)] = True
        area = gaussian(area * np.where(core | ~dark[top:bottom, left:right], 1.0, 0.0), soften)
        patch = result[top:bottom, left:right]
        result[top:bottom, left:right] = patch * (1 - area[..., None]) + filled * area[..., None]
        weight_total[top:bottom, left:right] = np.maximum(weight_total[top:bottom, left:right], area)
    return result, weight_total


def quadratic_through(p0, p1, p2, count):
    """p0 から p2 へ、真ん中（t = 0.5）で p1 を通る 2 次の曲線の count 点。"""
    p0, p1, p2 = (np.asarray(p, dtype=np.float64) for p in (p0, p1, p2))
    control = 2 * p1 - 0.5 * (p0 + p2)
    t = np.linspace(0, 1, count)[:, None]
    return (1 - t) ** 2 * p0 + 2 * t * (1 - t) * control + t ** 2 * p2


def stroke_path(stroke):
    """線の点の列 (n, 2)（base_width の画素）と、区間ごとの太さ (n − 1,)。3 点と curve なら 2 次の曲線の samples 点、2 点なら samples 点に
    等分、ほかは点の列のまま。span [始め, 終わり]（0〜1）で点の列の一部だけにする。太さは区間の真ん中の位置で読む。"""
    points = np.asarray(stroke["points"], dtype=np.float64)
    if stroke["curve"]:
        path = quadratic_through(points[0], points[1], points[2], stroke["samples"])
    elif len(points) == 2:
        path = points[0] + (points[1] - points[0]) * np.linspace(0, 1, stroke["samples"])[:, None]
    else:
        path = points
    if stroke["span"] is not None:
        path = path[int(stroke["span"][0] * len(path)):int(stroke["span"][1] * len(path))]
    if len(path) < 2:
        return path, np.zeros(0)
    at = (np.arange(len(path) - 1) + 0.5) / (len(path) - 1)
    widths = stroke["widths"]
    if isinstance(widths, dict):                                        # 弓の形: 真ん中が middle、両端が ends
        thick = widths["ends"] + (widths["middle"] - widths["ends"]) * np.sin(np.pi * at) ** 0.8
    else:
        thick = np.interp(at, [w[0] for w in widths], [w[1] for w in widths])
    return path, thick


def stroke_layers(image, strokes, color, scale, supersample):
    """線を前から順に重ねた絵 (H, W, 3) と、線の重み (H, W)（0〜1）。線の範囲だけを supersample 倍に広げ（元の画素を繰り返す）、線ごとに
    区間の四角と両端の丸を塗って重ね、面積の平均で縮める（線の無い所は元の画素のまま）。"""
    height, width = image.shape[:2]
    paths = [stroke_path(stroke) for stroke in strokes]
    result = image.astype(np.float64).copy()
    alpha_total = np.zeros((height, width))
    spans = [(path.min(axis=0) - thick.max() / 2 - 2, path.max(axis=0) + thick.max() / 2 + 2) for path, thick in paths if len(thick)]
    if not spans:
        return result, alpha_total
    low = np.min([s[0] for s in spans], axis=0) * scale
    high = np.max([s[1] for s in spans], axis=0) * scale
    x0, y0 = max(int(np.floor(low[0])), 0), max(int(np.floor(low[1])), 0)
    x1, y1 = min(int(np.ceil(high[0])) + 1, width), min(int(np.ceil(high[1])) + 1, height)
    if x1 <= x0 or y1 <= y0:
        return result, alpha_total
    s = supersample
    canvas = np.repeat(np.repeat(result[y0:y1, x0:x1], s, axis=0), s, axis=1)
    union = np.zeros(canvas.shape[:2])
    for stroke, (path, thick) in zip(strokes, paths):
        layer = Image.new("L", (canvas.shape[1], canvas.shape[0]), 0)
        pen = ImageDraw.Draw(layer)
        points = (path * scale - [x0, y0]) * s
        for k, w in enumerate(thick):
            a, c = points[k], points[k + 1]
            half = max(w * scale * s, 1.0) / 2
            length = math.hypot(*(c - a))
            if length > 1e-9:
                normal = np.array([a[1] - c[1], c[0] - a[0]]) / length * half
                pen.polygon([tuple(a + normal), tuple(c + normal), tuple(c - normal), tuple(a - normal)], fill=255)
            for p in (a, c):                                            # 両端の丸（線どうしの継ぎ目に角を出さない）
                pen.ellipse([p[0] - half, p[1] - half, p[0] + half, p[1] + half], fill=255)
        a = np.asarray(layer, dtype=np.float64) / 255.0
        tint = np.array(stroke["color"] if stroke["color"] is not None else color, dtype=np.float64)
        canvas = canvas * (1 - a[..., None]) + tint * a[..., None]
        union = 1 - (1 - union) * (1 - a)
    h, w = y1 - y0, x1 - x0
    result[y0:y1, x0:x1] = canvas.reshape(h, s, w, s, 3).mean(axis=(1, 3))
    alpha_total[y0:y1, x0:x1] = union.reshape(h, s, w, s).mean(axis=(1, 3))
    return result, alpha_total


def draw(image, settings, with_mask=False):
    """閉じた目の線を描き直した絵 (H, W, 3)（0〜255、丸める前）と報告。image は (H, W, 3) の RGB（0〜255）。
    with_mask なら、描き直した所の重み (H, W)（0〜1。消した範囲と線の重みの大きいほう）も返す。"""
    height, width = image.shape[:2]
    scale = width / (settings.get("base_width") or width)
    if settings.get("skin_fill") is not None:
        base, erased = skin_fill(image, settings, scale)
    elif settings.get("erase_rows") is not None:
        base, erased = erase_band(image, settings, scale), None
    else:                                                               # 消さずに、線だけを描く（strokes だけのとき）
        base, erased = image.astype(np.float64), np.zeros((height, width))
    result = base
    alpha = np.zeros((height, width, 1))
    if settings.get("eyes"):
        alpha = line_alpha(shapes(settings), width, height, scale, int(settings["supersample"]))[..., None]
        color = np.array(settings["color"], dtype=np.float64)
        result = base * (1 - alpha) + color * alpha
    lines = alpha[..., 0]
    if settings.get("strokes"):
        result, stroke_alpha = stroke_layers(result, settings["strokes"], settings["color"], scale, int(settings["supersample"]))
        lines = np.maximum(lines, stroke_alpha)
    changed = np.abs(result - image).max(axis=2) > 2
    ys, xs = np.nonzero(changed)
    box = [int(xs.min() / scale), int(ys.min() / scale), int(xs.max() / scale), int(ys.max() / scale)] if len(xs) else None
    report = {"changed_box": box, "changed_pixels": int(changed.sum()), "line_pixels": int((lines > 0.5).sum()),
              "thickness": settings["thickness"], "sag": settings["sag"], "scale": scale}
    if settings.get("strokes"):
        report["strokes"] = len(settings["strokes"])
    if settings.get("skin_fill") is not None:
        report.update(erase="skin_fill", filled_pixels=int((erased > 0.5).sum()))
    elif settings.get("erase_rows") is None:
        report["erase"] = "none"
    if not with_mask:
        return result, report
    if erased is None:                                                  # 消す帯の重み（erase_band と同じ帯とぼかし）
        (y0, y1), (x0, x1) = settings["erase_rows"], settings["erase_columns"]
        band = np.zeros((height, width))
        band[min(max(int(y0 * scale), 0), height):min(max(int(y1 * scale), 0), height),
             min(max(int(x0 * scale), 0), width):min(max(int(x1 * scale), 0), width)] = 1.0
        erased = gaussian(band, settings["band_blur"] * scale)
    # 線の所は、縁のなめらかな塗りまで絵に入っているので、重み 1 で写す（線の重みのままだと、縁が 2 回薄まる）。線のある画素を
    # 2 画素ふくらませ、σ 1 でならす
    cover = morph((lines > 0).astype(np.float64), ellipse_kernel(5), np.maximum)
    return result, report, np.clip(np.maximum(erased, np.maximum(gaussian(cover, 1.0) * 2, lines)), 0, 1)
