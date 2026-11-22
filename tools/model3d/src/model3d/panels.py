"""コマ割りの絵: 同じ瞬間を、2〜3 のカメラのコマで上下に割って見せる（境は折れ線。境の線は、色のにじみと芯）。

リポジトリ外の 4 作目の 3D のダンスの仕上げの台本（OpenCV で書いた）を移した。model3d は OpenCV に頼らないので、numpy で書く。
- 折れ線の点の決め方（乱数の引き方と順番）は、台本と同じ（同じ種なら同じ点）。点は台本と同じく整数へ切り捨てる。
- 上下のずらしは、OpenCV 5 の warpAffine（直線の補間・端の画素を延ばす）と同じ形の計算（float32 の位置と重み、偶数への丸め）。
  整数の画素のずらしは、そのまま写すのと同じ（台本のずらしは、どれもこれ）。小数のずらしは、まれに 1 画素の値が 1 違う。
- 境から下の範囲は、折れ線を画素の列ごとにつないだ高さより下（台本の fillPoly とは境の 1 画素がずれうる。芯の線の下に隠れる）。
- にじみは、台本と同じく、決めた太さの縁のくっきりした線（OpenCV は float の絵に線のなめらかさを付けない）をぼかす。
  ぼかしは題字と同じ（OpenCV の GaussianBlur と同じ長さと形の核）。
- 芯は、なめらかな縁の線（線からの距離で濃さを決める）。OpenCV の LINE_AA とは縁の画素の濃さが少し違う。
大きさ（境の高さ・ずらす量・線の太さ・ぼかし）は、基準の高さ（reference_height）での数で書き、コマの高さに合わせる。"""
import random

import numpy as np

OVERHANG = 1 / 240          # 折れ線の左右の端を、画面の外へ出す長さ（基準の高さの比。台本と同じ）


def jagged(width, height, left, right, seed, steps, jag, jag_x, reference):
    """境の折れ線の点（画素。整数へ切り捨て）。left・right は左右の端の高さ、jag は折れの上下の量の上限（ずれ = −jag〜jag の乱数
    × 0.5〜1 の乱数 × 折れごとに入れ替わる ±1。最初の乱数の符号も乱数なので、上下の向きは交互にならず、大きさは 0〜jag）、jag_x は
    横の揺れ（−jag_x〜jag_x の乱数）。どれも基準の高さ reference での数。乱数は random.Random(seed) で、折れごとに 上下の量 →
    上下の割合 → 横の揺れ の順に引く（台本と同じ式。jag_x が 0 でも引く）。"""
    s = height / reference
    o = reference * OVERHANG
    rng = random.Random(seed)
    points = []
    for i in range(steps + 1):
        k = i / steps
        inner = i not in (0, steps)
        dy = rng.uniform(-jag, jag) * (1 if i % 2 else -1) * rng.uniform(0.5, 1.0) if inner else 0.0
        dx = rng.uniform(-jag_x, jag_x) if inner else 0.0
        points.append(((-o + (width / s + 2 * o) * k + dx) * s, (left + (right - left) * k + dy) * s))
    return [(int(x), int(y)) for x, y in points]      # 0 へ向けて切り捨て（台本の np.int32 と同じ）


def line_height(points, width):
    """折れ線の、画素の列 x = 0〜width − 1 での高さ（点の間を直線でつなぐ）。点の x は左から右へ増えていること。"""
    xs = np.array([p[0] for p in points], np.float64)
    ys = np.array([p[1] for p in points], np.float64)
    return np.interp(np.arange(width, dtype=np.float64), xs, ys)


def below(points, height, width):
    """折れ線から下（線の高さを四捨五入した画素から下）の画素の覆い（bool の高さ × 幅）。"""
    top = np.floor(line_height(points, width) + 0.5)
    return np.arange(height, dtype=np.float64)[:, None] >= top[None, :]


def shift_rows(image, dy):
    """絵を dy 画素だけ下へずらす（負なら上へ）。外から入る所は端の画素を延ばす。OpenCV 5 の warpAffine（INTER_LINEAR・
    BORDER_REPLICATE）と同じ形で、元の高さ y − dy を float32 で求め、上下の 2 画素を a + (b − a) × 端数 で混ぜて、偶数へ丸める。
    整数の dy なら、そのまま写すのと同じ。"""
    h = image.shape[0]
    src = np.arange(h, dtype=np.float32) - np.float32(dy)
    y0 = np.floor(src).astype(np.int64)
    frac = src - y0.astype(np.float32)
    if not frac.any():
        return image[np.clip(y0, 0, h - 1)].copy()
    a = image[np.clip(y0, 0, h - 1)].astype(np.float64)
    b = image[np.clip(y0 + 1, 0, h - 1)].astype(np.float64)
    f = frac.astype(np.float64).reshape((-1,) + (1,) * (image.ndim - 1))
    out = np.rint((a + (b - a) * f).astype(np.float32))
    return np.clip(out, np.iinfo(image.dtype).min, np.iinfo(image.dtype).max).astype(image.dtype)


def distance(points, rows, width):
    """画素の中心（整数の座標）から折れ線までの距離（rows の行 × width）。"""
    yy = np.asarray(rows, np.float64)[:, None]
    xx = np.arange(width, dtype=np.float64)[None, :]
    best = np.full((len(rows), width), np.inf)
    P = np.asarray(points, np.float64)
    for (x0, y0), (x1, y1) in zip(P[:-1], P[1:]):
        dx, dy = x1 - x0, y1 - y0
        length = dx * dx + dy * dy
        t = np.clip(((xx - x0) * dx + (yy - y0) * dy) / length, 0, 1) if length > 0 else np.zeros_like(yy + xx)
        best = np.minimum(best, np.hypot(xx - (x0 + t * dx), yy - (y0 + t * dy)))
    return best


def band(lines, height, pad):
    """折れ線の高さの範囲を、上下に pad 画素広げた行（画面の中に切る）。"""
    ys = [p[1] for line in lines for p in line]
    return np.arange(max(0, int(min(ys) - pad)), min(height, int(max(ys) + pad) + 1))


def gutter(image, lines, glow_color, glow_width, glow_sigma, glow_gain, core_color, core_width):
    """境の線を描く（image は uint8 の高さ × 幅 × 3。新しい絵を返す）。
    線の半径は (太さ + 1) // 2（OpenCV の太い線と同じく、奇数の太さは 1 画素太い）。
    1. にじみ: 太さ glow_width の縁のくっきりした線（距離 ≤ 半径 + 0.5）を glow_color で描いてぼかし（sigma = glow_sigma）、
       glow_gain 倍して足す（0〜255 に切り、切り捨て）。
    2. 芯: 太さ core_width のなめらかな縁の線（濃さ = 半径 + 1.2 − 距離 を 0〜1 に切る。1.2 は、OpenCV の LINE_AA の縁と、太さ
       10〜43 の折れ線で差がいちばん小さくなる幅）を core_color で重ねる（四捨五入）。"""
    from .title import blur, blur_reach
    height, width = image.shape[:2]
    out = image.copy()
    reach = blur_reach(glow_sigma)
    rows = band(lines, height, glow_width / 2 + 2 * reach + 2)
    if len(rows):
        d = np.min([distance(line, rows, width) for line in lines], axis=0)
        cover = (d <= (glow_width + 1) // 2 + 0.5).astype(np.float32)
        spread = blur(cover, glow_sigma)
        glow = spread[..., None] * np.asarray(glow_color, np.float32)[None, None] * np.float32(glow_gain)
        part = out[rows[0]:rows[-1] + 1].astype(np.float32) + glow
        out[rows[0]:rows[-1] + 1] = np.clip(part, 0, 255).astype(np.uint8)
    rows = band(lines, height, core_width / 2 + 2)
    if len(rows):
        d = np.min([distance(line, rows, width) for line in lines], axis=0)
        a = np.clip((core_width + 1) // 2 + 1.2 - d, 0, 1)[..., None]
        part = out[rows[0]:rows[-1] + 1].astype(np.float64)
        part = part + (np.asarray(core_color, np.float64)[None, None] - part) * a
        out[rows[0]:rows[-1] + 1] = np.floor(part + 0.5).astype(np.uint8)
    return out


def compose(images, shifts, borders):
    """段の絵（上から順。uint8 の高さ × 幅 × 3）を、段ごとに shifts 画素ずらして、境（上から順の折れ線。段の数 − 1）で割る。
    いちばん上の段を地にし、境ごとに、その境より下を次の段の絵にする（台本と同じ順）。"""
    height, width = images[0].shape[:2]
    out = shift_rows(images[0], shifts[0])
    for image, dy, line in zip(images[1:], shifts[1:], borders):
        mask = below(line, height, width)
        out[mask] = shift_rows(image, dy)[mask]
    return out
