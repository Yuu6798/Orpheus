"""仕上げの部品（hooks から使う）: 後光・接地の影・床の反射・輪郭の光・シャープ・ステージのブルーム・拍の脈。

どれも**表示の値**（sRGB に符号化した 0〜1 の float32）で計算する。非公開の制作のレンダラは表示の値で合成していたので、
同じ式・同じ順で書いてある（合成の配列で、制作の関数と float32 の値まで同じになることを確かめた。docs/dance.md）。
hooks の画は線形なので、`display` で表示の値にしてから掛け、`linear` で戻す。人物は前乗算の線形の色なので、
`straight` で表示の値の色（アルファで割った色）にし、`premultiplied` で戻す。

    from mv_pipeline.dance import finish

    def behind(img, t, ctx, L):                     # 人物の後ろ: 後光・接地の影・床の反射
        if L.performer is None:
            return img
        color, alpha = L.performer
        d = finish.halo(finish.display(img), alpha, unit=ctx.size[0] / 1080)
        ...
        return finish.linear(d)

長さ（画素）の既定は 1080×1920 の出力の値で、`unit` を掛けて使う（別の大きさでは `ctx.size[0] / 1080`）。縮小の倍率
（down）は大きさに比例するので掛けない。色（輪郭の光など）は作品ごとの値なので既定値を持たず、呼ぶ側が渡す。
表示の値への変換は [0, 1] で切るので、明るい所に光を足して 1 を超えた分は、制作（後で切る）と違ってその場で切れる。
"""
import bisect
import math

import cv2
import numpy as np

from ..fx.color import from_srgb_float, to_srgb_float


# ---- 線形 ↔ 表示 ------------------------------------------------------------------------

def display(img):
    """線形の float32 → 表示の値（sRGB に符号化した 0〜1）。[0, 1] の外は切る。"""
    return to_srgb_float(img)


def linear(img):
    """表示の値 → 線形の float32。[0, 1] の外は切る。"""
    return from_srgb_float(img)


def straight(color, alpha):
    """前乗算の線形の色 (H, W, 3) とアルファ (H, W) → 表示の値の色（アルファで割ってから符号化。アルファ 0 の所は 0）。"""
    a = alpha[..., None]
    return to_srgb_float(np.where(a > 0, color / np.maximum(a, np.float32(1e-6)), np.float32(0.0)).astype(np.float32))


def premultiplied(fg, alpha):
    """straight の逆: 表示の値の色とアルファ → 前乗算の線形の色。"""
    return from_srgb_float(fg) * alpha[..., None]


def _px(value, unit):
    return value * unit if unit != 1.0 else value


# ---- 人物の後ろ ------------------------------------------------------------------------

def halo(img, alpha, strength=0.42, gain=1.4, sigma=6.0, down=4, unit=1.0):
    """人物のまわりを暗くして浮かせる。アルファを 1/down に縮めてぼかし（sigma は縮めた画素）、戻して gain 倍で 1 に切り、
    img に (1 − strength × それ) を掛ける。"""
    h, w = alpha.shape[:2]
    small = cv2.resize(alpha, (w // down, h // down), interpolation=cv2.INTER_AREA)
    blurred = cv2.resize(cv2.GaussianBlur(small, (0, 0), _px(sigma, unit)), (w, h), interpolation=cv2.INTER_LINEAR)
    return img * (1 - strength * np.clip(blurred * gain, 0, 1))[..., None]


def feet_span(alpha, band=0.06):
    """(足元の行, 足の左右の中心 x, 足の幅)。人物の高さの下 band の帯で、アルファ 0.5 超の列を見る（腕が足元の位置を動かさない）。
    行は 1 行おきに見る（制作と同じ）。人物がいなければ None。"""
    rows = np.nonzero((alpha[::2] > 0.5).any(1))[0] * 2
    if not len(rows):
        return None
    bottom, top = rows.max(), rows.min()
    feet = alpha[max(bottom - int(band * (bottom - top)), 0):bottom + 1] > 0.5
    xs = np.nonzero(feet.any(0))[0]
    if not len(xs):
        return None
    return int(bottom), (xs.min() + xs.max()) / 2, xs.max() - xs.min()


def contact_shadow(img, alpha, floor_y, strength=0.5, band=0.06, width=0.30, pad=30.0, height=16.0, sigma=6.0, lift=140.0,
                   least=0.25, drop=3.0, down=2, unit=1.0):
    """足元の影。床の線 floor_y（出力の画素）に、足の幅 × width + pad、高さ height の楕円（1/down の画素。drop だけ下）を描いて
    ぼかし、img を (1 − strength × 濃さ × 影) 倍にする。濃さは足が床から上がるほど薄く（lift 画素で least まで）。"""
    span = feet_span(alpha, band)
    if span is None:
        return img
    bottom, center, feet_width = span
    h, w = alpha.shape[:2]
    fy = int(floor_y)
    shadow = np.zeros((h // down, w // down), np.float32)
    axes = (int(feet_width * width + _px(pad, unit)), int(_px(height, unit)) if unit != 1.0 else height)
    cv2.ellipse(shadow, (int(center / down), int(fy // down + _px(drop, unit))), (int(axes[0]), int(axes[1])), 0, 0, 360, 1.0, -1,
                cv2.LINE_AA)
    shadow = cv2.resize(cv2.GaussianBlur(shadow, (0, 0), _px(sigma, unit)), (w, h))
    level = float(np.clip(1 - max(fy - bottom, 0) / _px(lift, unit), least, 1))
    return img * (1 - strength * level * shadow)[..., None]


def floor_reflection(img, fg, alpha, floor_y, height=300.0, start=0.22, unit=1.0):
    """床の反射。床の線 floor_y の上 height 画素の人物（表示の値の色 fg × アルファ）を上下に返して床に重ね、
    濃さを start から 0 へ直線で薄くする。"""
    out = img.copy()
    fy = int(floor_y)
    span = int(min(out.shape[0] - fy, _px(height, unit), fy))
    if span <= 10:
        return out
    mirrored = (fg * alpha[..., None])[fy - span:fy][::-1]
    mirrored_alpha = alpha[fy - span:fy][::-1]
    fade = np.linspace(start, 0.0, span, dtype=np.float32)[:, None]
    out[fy:fy + span] = out[fy:fy + span] * (1 - mirrored_alpha[..., None] * fade[..., None]) + mirrored * fade[..., None]
    return out


def over(fg, alpha, img):
    """表示の値の色 fg を img に重ねる（fg × α + img × (1 − α)。制作の合成と同じ）。"""
    return fg * alpha[..., None] + img * (1 - alpha[..., None])


# ---- 人物 ------------------------------------------------------------------------------

def rim_light(fg, alpha, rgb, strength, kernel=7, shape="ellipse", blur=1.6, gradient=(1.25, 0.75), clip=True, unit=1.0):
    """輪郭の光。シルエットの縁のすぐ内側（アルファ − kernel で削ったアルファ）を blur でぼかし、上から下へ gradient の
    比で強さを変えて、色 rgb（0〜255。画の色の並びに合わせる）× strength を fg に足す。clip なら 0〜1 に切る。"""
    size = max(1, int(round(_px(kernel, unit))))
    element = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)) if shape == "ellipse" else np.ones((size, size), np.uint8)
    band = np.clip(alpha - cv2.erode(alpha, element), 0, 1)
    if blur:
        band = cv2.GaussianBlur(band, (0, 0), _px(blur, unit))
    if gradient is not None:
        band = band * np.linspace(gradient[0], gradient[1], alpha.shape[0], dtype=np.float32)[:, None]
    out = fg + band[..., None] * (np.asarray(rgb, np.float32) / np.float32(255.0)) * strength
    return np.clip(out, 0, 1) if clip else out


def tint(fg, gains):
    """チャンネルごとの倍率（画の色の並び）。"""
    return fg * np.asarray(gains, np.float32)


def sharpen(fg, sigma=1.4, amount=0.4, unit=1.0):
    """アンシャープ: fg + (fg − ぼかし) × amount を 0〜1 に切る。"""
    return np.clip(fg + (fg - cv2.GaussianBlur(fg, (0, 0), _px(sigma, unit))) * amount, 0, 1)


# ---- ステージ ---------------------------------------------------------------------------

def stage_bloom(img, threshold=0.45, ceiling=0.8, sigma=7.0, strength=0.6, down=4, unit=1.0):
    """ステージだけのブルーム。1/down に縮めて threshold を引き（0〜ceiling に切る。ceiling None なら上は切らない）、
    ぼかして戻し、strength 倍を足す。"""
    h, w = img.shape[:2]
    small = cv2.resize(img, (w // down, h // down), interpolation=cv2.INTER_AREA)
    glow = cv2.GaussianBlur(np.clip(small - threshold, 0, ceiling), (0, 0), _px(sigma, unit))
    return img + cv2.resize(glow, (w, h), interpolation=cv2.INTER_LINEAR) * strength


# ---- 拍の脈 ---------------------------------------------------------------------------

def beat_pulse(beats, t, tau=0.12, downbeats=(), bar=1.0, beat=0.55, count=1, gain=None):
    """拍の脈。t 以前の直前 count 個の拍について amp × exp(−経過/tau) を足す。amp は小節頭（downbeats にある拍）なら bar、
    ほかは beat。gain(拍の時刻, t) を amp に掛ける（0 で消す。曲の区間ごとの強さや、休みの間を消すのに使う）。
    beats・downbeats は spec の拍の格子（ctx.spec.data["beats"]）をそのまま渡せる。"""
    index = bisect.bisect_right(beats, t) - 1
    heads = set(downbeats)
    total = 0.0
    for k in range(index, max(index - count, -1), -1):
        at = beats[k]
        amp = bar if at in heads else beat
        if gain is not None:
            amp *= gain(at, t)
        total += amp * math.exp(-(t - at) / tau)
    return total
