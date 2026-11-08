"""題字（曲名などのカード）の絵: 文字や画像を、影と光り（glow）を付けた絵にし、コマに重ねる。

リポジトリ外の試作の題字（2D 完成版の題字と同じ字形・大きさ・光り方・出方）を移した。計算は float32 で、書き出しは 0〜255 への
切り捨て（試作と同じ。同じコマになる）。ぼかしは、OpenCV の GaussianBlur（sigma の指定・float の画像・端は反射 101）と同じ長さと
形の核を numpy で掛ける（model3d は OpenCV に頼らない）。重ね方の既定も試作と同じ（覆いを二度掛ける。blit の blend）。"""
import numpy as np

BLENDS = ("prototype", "over")      # 重ね方: 試作と同じ（既定）か、ふつうの重ね方


def smoothstep(u):
    """0〜1 に切ってから、なめらかに 0 → 1（3u² − 2u³）。"""
    u = min(1.0, max(0.0, u))
    return u * u * (3 - 2 * u)


def gaussian_kernel(sigma):
    """OpenCV の GaussianBlur（ksize を 0 にして sigma で決める、float の画像）と同じ長さの核（和が 1 の float32）。
    長さは round(sigma × 8 + 1) を奇数にしたもの（sigma 9 で 73、18 で 145）。"""
    if not sigma > 0:
        raise ValueError(f"sigma は正の数にしてください: {sigma!r}")
    n = int(round(sigma * 8 + 1)) | 1
    x = np.arange(n, dtype=np.float64) - (n - 1) / 2
    t = np.exp(-(x * x) / (2.0 * sigma * sigma))
    return (t / t.sum()).astype(np.float32)


def blur_reach(sigma):
    """ぼかしが覆いの外へ届く画素（核の長さの半分。sigma の約 4 倍。sigma 2 で 8、9 で 36、18 で 72）。"""
    return len(gaussian_kernel(sigma)) // 2


def reflect101(n, r):
    """長さ n の並びを、両側へ r ずつ「端を繰り返さない折り返し」で延ばすときの番号（OpenCV の BORDER_REFLECT_101。
    r が n より長くても、何度でも折り返す）。"""
    i = np.arange(-r, n + r)
    if n == 1:
        return np.zeros_like(i)
    period = 2 * (n - 1)
    i = np.mod(i, period)
    return np.where(i >= n, period - i, i)


def blur(a, sigma):
    """2 次元の配列を、ガウスでぼかす（横、縦の順。端は反射 101 = OpenCV の既定。和は float64 で取り、float32 で返す）。"""
    k = gaussian_kernel(sigma).astype(np.float64)
    r = len(k) // 2
    out = np.asarray(a, dtype=np.float64)
    for axis in (1, 0):
        n = out.shape[axis]
        padded = np.take(out, reflect101(n, r), axis=axis)
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            acc += w * (padded[:, i:i + n] if axis == 1 else padded[i:i + n, :])
        out = acc
    return out.astype(np.float32)


def load_font(path, size, variation=None):
    """書体（TrueType / OpenType）。path が無ければ Pillow の内蔵の書体（ラテン文字だけ）。variation は可変の書体の名前つきの形（"Black" など）。"""
    from PIL import ImageFont
    if path is None:
        if variation:
            raise ValueError("variation は font（可変の書体のファイル）と一緒に指定してください")
        return ImageFont.load_default(size)
    font = ImageFont.truetype(str(path), size)
    if variation:
        try:
            font.set_variation_by_name(variation)
        except (OSError, ValueError) as error:
            names = []
            try:
                names = [n.decode("utf-8", "replace") if isinstance(n, bytes) else str(n) for n in font.get_variation_names()]
            except OSError:
                pass
            raise ValueError(f"書体に形 {variation!r} がありません（ある形: {names or 'なし（可変の書体ではない）'}）") from error
    return font


def text_layer(text, font, size, color=(255, 255, 255), stroke=0, stroke_color=(8, 6, 30), tracking=0.0):
    """文字を 1 字ずつ（字送りに tracking × size を足して）透明の地に描き、色（H×W×3、0〜1）と覆い（H×W、0〜1）を返す。
    縁取り（stroke 画素、stroke_color）は文字の色の外側。周りに size × 0.45 + 2 × stroke の余白を取る（影と光りの広がる場所）。"""
    from PIL import Image, ImageDraw
    widths = [font.getlength(c) for c in text]
    trk = tracking * size
    ascent, descent = font.getmetrics()
    pad = int(size * 0.45) + stroke * 2
    w, h = int(sum(widths) + trk * (len(text) - 1) + 2 * pad), int(ascent + descent + 2 * pad)
    image = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    x = pad
    for c, advance in zip(text, widths):
        draw.text((x, pad), c, font=font, fill=tuple(color) + (255,), stroke_width=stroke, stroke_fill=tuple(stroke_color) + (255,))
        x += advance + trk
    arr = np.asarray(image).astype(np.float32) / 255.0
    return arr[..., :3], arr[..., 3]


def image_layer(path, scale=1.0, margin=0):
    """画像（PNG など）の色と覆い（透けの無い画像は全面が覆い）。scale ≠ 1 なら Lanczos で大きさを変える。
    周りに margin 画素の透けた余白を足す（影と光りの広がる場所。余白が無いと、透けの無い画像や端まで描いた画像の影と光りは、
    画像の外へ出られず、上に載る画像の色に隠れる）。"""
    from PIL import Image
    with Image.open(path) as source:
        image = source.convert("RGBA")
    if scale != 1.0:
        image = image.resize((max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))), Image.LANCZOS)
    arr = np.asarray(image).astype(np.float32) / 255.0
    if margin > 0:
        arr = np.pad(arr, ((margin, margin), (margin, margin), (0, 0)))
    return arr[..., :3], arr[..., 3]


def sprite(color, cover, shadow=None, glow=None):
    """色と覆いから、影と光りを付けた絵（rgb, alpha）と、覆いが半分を超える範囲（x0, x1, y0, y1。中心合わせに使う）。
    shadow = (sigma, 濃さ): 覆いをぼかした黒い影。glow = ((r, g, b) 0〜255, sigma, 強さ): 覆いをぼかした色の光り（影の上）。
    文字の色は、その上に覆いの分だけ載せる。"""
    h, w = cover.shape
    rgb = np.zeros((h, w, 3), np.float32)
    alpha = blur(cover, shadow[0]) * shadow[1] if shadow is not None else np.zeros((h, w), np.float32)
    if glow is not None:
        glow_color, sigma, amount = glow
        g = np.clip(blur(cover, sigma) * amount, 0, 1)
        rgb = rgb * (1 - g[..., None]) + np.array(glow_color, np.float32)[None, None] / 255.0 * g[..., None]
        alpha = alpha * (1 - g) + g
    rgb = rgb * (1 - cover[..., None]) + color * cover[..., None]
    alpha = alpha * (1 - cover) + cover
    ys, xs = np.nonzero(cover > 0.5)
    if not len(xs):
        raise ValueError("覆いが半分を超える画素がありません（空白だけの文字か、透けた画像）")
    return rgb, alpha, (int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1)


def blit(dst, spr, cx, cy, amount, blend="prototype"):
    """絵を、覆いの範囲の中心が (cx, cy) に来るように、amount（0〜1）の濃さで dst（H×W×3、float32、0〜1）に重ねる。
    amount が 0.003 以下か、画面の外なら何もしない。重ねたら True。
    絵の色（sprite の rgb）は、影・光り・文字を覆いで重ねた色で、覆いを掛け済み。blend:
    - "prototype"（既定。試作と同じ）: その色に、もう一度 alpha × amount を掛けて重ねる。半ば透けた画素（文字の縁・光り・影・
      半透明の画像）は "over" より暗い（白の覆い 0.6 は、黒の上で 0.36）。採用版の題字はこの重ね方。
    - "over": ふつうの重ね方（色 × amount + 下 × (1 − alpha × amount)。白の覆い 0.6 は、黒の上で 0.6）。
    alpha が 1 か 0 の画素は、どちらでも同じ。"""
    if blend not in BLENDS:
        raise ValueError(f"blend は {' か '.join(BLENDS)} にしてください: {blend!r}")
    rgb, alpha, (bx0, bx1, by0, by1) = spr
    H, W = dst.shape[:2]
    x0, y0 = int(round(cx - (bx0 + bx1) / 2)), int(round(cy - (by0 + by1) / 2))
    h, w = alpha.shape
    xa, ya, xb, yb = max(0, x0), max(0, y0), min(W, x0 + w), min(H, y0 + h)
    if xb <= xa or yb <= ya or amount <= 0.003:
        return False
    a = alpha[ya - y0:yb - y0, xa - x0:xb - x0, None] * amount
    color = rgb[ya - y0:yb - y0, xa - x0:xb - x0]
    dst[ya:yb, xa:xb] = (color * a if blend == "prototype" else color * np.float32(amount)) + dst[ya:yb, xa:xb] * (1 - a)
    return True


def to_uint8(img):
    """0〜1 の float の画像を 0〜255 に（切り捨て。試作と同じ）。"""
    return (np.clip(img, 0, 1) * 255).astype(np.uint8)
