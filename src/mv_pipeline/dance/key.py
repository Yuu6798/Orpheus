"""緑抜き: 動画のフレーム（uint8 RGB）から、前乗算の線形色とアルファを作る。

透明度は色差（BT.709 の Cb・Cr）の距離で決める。明るさを見ないので、キー色の影の濃淡に強い。
色差の距離は 0..約 1.2（純緑と純マゼンタの間が最大）。tolerance 以下は完全に透明、tolerance + softness 以上は完全に不透明。
"""
import cv2
import numpy as np

from ..fx.color import to_linear

_KR, _KB = 0.2126, 0.0722


def _chroma(rgb):
    luma = _KR * rgb[..., 0] + (1.0 - _KR - _KB) * rgb[..., 1] + _KB * rgb[..., 2]
    return (rgb[..., 2] - luma) / 1.8556, (rgb[..., 0] - luma) / 1.5748


def alpha_of(image, key):
    """uint8 RGB (H, W, 3) → float32 アルファ (H, W)。縁の調整（choke・feather）の前の値。"""
    rgb = image.astype(np.float32) / 255.0
    cb, cr = _chroma(rgb)
    target = np.asarray(key["color"], dtype=np.float32) / 255.0
    key_cb, key_cr = _chroma(target)
    distance = np.sqrt((cb - key_cb) ** 2 + (cr - key_cr) ** 2)
    u = np.clip((distance - key["tolerance"]) / key["softness"], 0.0, 1.0)
    return u * u * (3.0 - 2.0 * u)


def despill(image, key):
    """キー色の成分が他の成分を超える分を削る（緑かぶりの除去）。spill=1 で完全に削り、0 で何もしない。"""
    if key["spill"] <= 0:
        return image
    channel = int(np.argmax(key["color"]))
    others = [c for c in range(3) if c != channel]
    limit = np.maximum(image[..., others[0]], image[..., others[1]]).astype(np.float32)
    value = image[..., channel].astype(np.float32)
    over = np.maximum(value - limit, 0.0)
    out = image.copy()
    out[..., channel] = np.rint(value - over * float(key["spill"])).astype(np.uint8)
    return out


def refine(alpha, key, scale=1.0):
    """アルファの縁を内側へ削り（choke_px）、ぼかす（feather_px）。長さは元動画の画素で、scale を掛けて使う。"""
    choke = int(round(key["choke_px"] * scale))
    if choke > 0:
        alpha = cv2.erode(alpha, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * choke + 1, 2 * choke + 1)))
    feather = key["feather_px"] * scale
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), float(feather))
    return alpha


def cutout(image, key):
    """uint8 RGB → (前乗算の線形色 float32 (H, W, 3), アルファ float32 (H, W))。"""
    alpha = refine(alpha_of(image, key), key)
    return to_linear(despill(image, key)) * alpha[..., None], alpha
