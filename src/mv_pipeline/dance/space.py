"""3D の要素別の描画（model3d の render.passes）を重ねる部品のうち、3D の位置とカメラを使うもの（`mv dance passes`）。

どれも**表示の値**（sRGB に符号化した 0〜1 の float32）で計算する。リポジトリ外の 2 作目の VRM のダンスの演出版の重ね合わせの台本と
同じ式・同じ順・同じ型で書いてある（同じ入力なら、台本と同じ値になる）。長さ（画素）は幅 1080 の画面での値で、unit（画面の幅 ÷ 1080）を
掛けて使う。

    mirror(size, parts, unit)                         床への映り込み: 床で反転したカメラで描いた要素をぼかして倍率を掛け、足し合わせる
    ripple(image, amount, clock, settings, unit)      水鏡: 映り込みを行ごとに横へゆらす（2 つのうねり）
"""
import cv2
import numpy as np

from . import finish

RIPPLE = {"px": 3.0, "wave_px": 15.0, "speed": [2.5, 1.7]}
SECOND_WAVE = {"share": 0.5, "length": 2.7, "slant": 0.004}     # 2 つ目のうねり: 振れ幅の割合・波の間隔の倍率・横へ傾ける量（1 画素あたり）

_grids = {}


def grid(size):
    """(ys, xs) の float32 の格子（画素の番号。大きさごとに作り置く）。"""
    width, height = size
    if (width, height) not in _grids:
        _grids.clear()
        _grids[(width, height)] = np.mgrid[0:height, 0:width].astype(np.float32)
    return _grids[(width, height)]


def mirror(size, parts, unit=1.0):
    """床への映り込み。parts = [(前乗算の色 (H, W, 3), σ（画素。幅 1080 の画面で）, 倍率の並び)] を、並べた順に
    ぼかし（finish.blur。σ × unit が 0.2 以下ならそのまま）、倍率（数か RGB）を順に掛けて足し合わせた画（0 から）。"""
    width, height = size
    total = np.zeros((height, width, 3), np.float32)
    for color, sigma, gains in parts:
        term = finish.blur(color, sigma, unit)
        for gain in gains:
            term = term * (np.asarray(gain, np.float32) if isinstance(gain, (list, tuple, np.ndarray)) else gain)
        total += term
    return total


def ripple(image, amount, clock, settings=None, unit=1.0):
    """水鏡: 行 y・列 x の画素を横へ shift だけずらして読む（線形補間、端は端の画素）。amount が 0 以下ならそのまま。

        shift = px × unit × amount × sin(y ÷ (wave_px × unit) + clock × speed[0])
              + 0.5 × px × unit × amount × sin(y ÷ (wave_px × unit × 2.7) − clock × speed[1] + x × 0.004)

    clock は効果の時刻（時間停止でゆっくりになる時計）。"""
    if not amount > 0:
        return image
    s = {**RIPPLE, **(settings or {})}
    height, width = image.shape[:2]
    ys, xs = grid((width, height))
    second = SECOND_WAVE
    shift = s["px"] * unit * amount * np.sin(ys / (s["wave_px"] * unit) + clock * s["speed"][0]) \
        + second["share"] * s["px"] * unit * amount * np.sin(ys / (s["wave_px"] * unit * second["length"]) - clock * s["speed"][1]
                                                            + xs * second["slant"])
    return cv2.remap(image, xs + shift.astype(np.float32), ys, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


PHANTOM = {"dark": [0.75, 0.16, 0.03], "hot": [1.0, 0.78, 0.36], "fill": 0.38, "edge": 1.1, "edge_blur": 9.0, "edge_gain": 2.0,
           "glow": 14.0, "glow_gain": 0.7}


def flame_colors(gray, dark, hot):
    """白黒の陰影 gray (H, W)（0〜1 に切る）→ 炎の色（暗い所は dark、明るい所は hot へ混ぜる。float32）。"""
    w = np.clip(gray, 0, 1)[..., None]
    return np.asarray(dark, np.float32) * (1 - w) + np.asarray(hot, np.float32) * w


def phantom(target, picture, strength, settings=None, unit=1.0):
    """分身（白い人物にスタジオの光を当てた透けの RGBA）を、炎の色の光として target に足す。中は fill だけ透けて、輪郭（アルファ − σ
    edge_blur でぼかしたアルファ、× edge_gain）が edge 倍で光る。σ glow でぼかした光を glow_gain 倍足す。"""
    s = {**PHANTOM, **(settings or {})}
    solid = picture[..., 3]
    outline = np.clip(solid - finish.blur(solid, s["edge_blur"], unit), 0, 1) * s["edge_gain"]
    cover = (solid * (s["fill"] + s["edge"] * outline) * strength)[..., None]
    body = flame_colors(picture[..., :3].mean(axis=2), s["dark"], s["hot"]) * cover
    return target + body + finish.blur(body, s["glow"], unit) * s["glow_gain"]


def phantom_glow(picture, dark, hot):
    """床への映り込みの分身の項のもと: 炎の色 × アルファ（mirror の parts に入れて、ぼかして強さを掛ける）。"""
    return flame_colors(picture[..., :3].mean(axis=2), dark, hot) * picture[..., 3:4]
