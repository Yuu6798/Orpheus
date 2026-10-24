"""sRGB と線形光の変換、ハイライトの緩やかな圧縮（ソフトニー）。

合成はすべて線形の float32 で行い、最後に 1 回だけ sRGB の uint8 へ戻す。
"""
import numpy as np

KNEE = 0.8
_ENCODE_STEPS = 16384


def _srgb_to_linear(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.04045, v / 12.92, ((v + 0.055) / 1.055) ** 2.4)


def _linear_to_srgb(v):
    v = np.asarray(v, dtype=np.float64)
    return np.where(v <= 0.0031308, v * 12.92, 1.055 * np.power(np.maximum(v, 0), 1 / 2.4) - 0.055)


_DECODE_LUT = _srgb_to_linear(np.arange(256) / 255.0).astype(np.float32)
_ENCODE_LUT = np.clip(np.rint(_linear_to_srgb(np.arange(_ENCODE_STEPS + 1) / _ENCODE_STEPS) * 255.0), 0, 255).astype(np.uint8)


def to_linear(u8):
    """uint8 sRGB (…, C) → float32 線形。"""
    return _DECODE_LUT[u8]


def color_to_linear(rgb255):
    """[r, g, b] (0-255 sRGB) → float32 線形 (3,)。"""
    return _srgb_to_linear(np.asarray(rgb255, dtype=np.float64) / 255.0).astype(np.float32)


def to_srgb8(lin):
    """float32 線形 (…, C) → uint8 sRGB。[0, 1] の外は切る。"""
    idx = (np.clip(lin, 0.0, 1.0) * np.float32(_ENCODE_STEPS) + np.float32(0.5)).astype(np.int32)
    return _ENCODE_LUT[idx]


def knee(x):
    """0.8 までは恒等、それ以上は 1.0 へ漸近する圧縮（連続・傾き連続・単調）: x − d²/(d+h)。

    d = max(x−0.8, 0)、h = 0.2。四則演算だけなので SIMD と端数処理で結果がずれず、同じ入力は常に
    同じ出力になる（「光が当たらない画素は light 無しと完全一致」の保証が、この性質に依る）。
    """
    head = np.float32(1.0 - KNEE)
    d = np.maximum(x - np.float32(KNEE), np.float32(0.0))
    return x - d * d / (d + head)


def soft_add(base, add, roi=None):
    """線形画像 base に光 add (≥0) を足す。0.8 を超える部分はソフトニーで圧縮する。

    add == 0 の画素は base と**ビット単位で同じ**値を返す。roi=(y0, y1, x0, x1) を渡すとその範囲だけ
    計算し、外は base のまま（add が roi の外で 0 であることが前提）。戻り値は新しい配列で、base は変えない。
    圧縮が要るのは合計が 0.8 を超えた成分だけなので、そこだけ計算する（暗い画面ほど速い）。
    """
    if roi is None:
        window = (slice(None), slice(None))
    else:
        y0, y1, x0, x1 = roi
        if y1 <= y0 or x1 <= x0:
            return base.copy()
        window = (slice(y0, y1), slice(x0, x1))
    b = base[window]
    total = b + add[window]
    hot = total > np.float32(KNEE)
    if hot.any():
        bh = b[hot]
        total[hot] = bh + (knee(total[hot]) - knee(bh))
    if roi is None:
        return total
    out = base.copy()
    out[window] = total
    return out
