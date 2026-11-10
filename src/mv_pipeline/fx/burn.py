"""焼け抜け（burn）: fx-timeline/0.2 のつなぎ。前のショットがノイズの形に焼け抜けて今のショットが現れ、境目が光る（実験的。docs/fx.md）。

numpy だけで計算する（cv2 は使わない）。時刻（進み p）と設定だけで決まり、状態を持たない。区間を分けて描いても同じコマになる。

場 N（float32 (H, W)、0..1）は**画面の正規化座標**の上に作る。横も縦も画面の幅を 1 とする座標（縦は 0〜高さ/幅）なので、
むらの升目は正方形になり、画素の中心で読むので解像度を変えても同じ模様になる:

    N  = 粗いむら·0.7 + 細かいむら·0.3 を 0..1 に伸ばしたもの       むらは格子の乱数（seed で決まる）を 3 次の補間でつないだもの。
                                                               升目は粗い方が画面の幅 × scale/4、細かい方がその 1/4
    N' = N·(1 − bias) + D·bias                                 D は from からの距離（いちばん遠い画面の隅で 1）。bias 0 なら N のまま

つなぎは表示の値（0..255 の float）の上で計算する（xfade と同じ）。進み p は 0→1、width は縁の幅（縁が無ければ 0）、R = soft + width:

    thr = −R + p·(1 + 2·R)                                     しきい値。p = 0 で今のショットは 1 画素も出ず、p = 1 で前のショットは 1 画素も残らない
    m   = clamp((thr − N') / soft, 0, 1)                        今のショットの割合
    c   = clamp(1 − |N' − thr − R·CHAR_AHEAD| / (R·CHAR_HALF), 0, 1)   焦げの帯（前線の少し先。まだ前のショットの側）
    画  = (前·(1 − m) + 今·m)·(1 − char·c)
    e   = clamp(1 − |N' − thr| / width, 0, 1)                   縁
    画  = 画 + (255 − 画)·min(色·gain·e, 255)/255               スクリーン合成（255 − (255 − 画)·(255 − 光)/255 と同じ値）

m・e・焦げの帯は、リポジトリの外の試作のつなぎと同じ式（既定の soft 0.035・width 0.05 で、焦げは前線の 0.03 先を中心に前後 0.04）。
前線から R より遠い画素は、前のショットか今のショットの値そのまま（m は 0 か 1、c と e は 0）。
p ≤ 0 は前の画、p ≥ 1 は今の画をそのまま返す（最後に切り捨てるので、端は丸めに頼らずに一致させる）。
"""
import math

import numpy as np

from . import timeline

OCTAVES = ((1.0, 0.7), (0.25, 0.3))    # むらの (升目の大きさの倍率, 重み)。粗いむらと、升目がその 1/4 の細かいむら
CELLS_PER_SCALE = 4.0                  # scale（模様の大きさ）は、粗いむらの升目 4 つ分
CUBIC = -0.75                          # 3 次の補間の係数（OpenCV の INTER_CUBIC と同じ核）
REFERENCE_STEPS = 10                   # 0..1 に伸ばす最小と最大は、粗い升目あたり 10 点の格子で読む（出力の解像度に依らない値にするため）
REFERENCE_WIDTHS = (256, 1024)         # その格子の幅の下限と上限
CHAR_AHEAD = 0.03 / 0.085              # 焦げの帯の中心: 前線から先へ、R = soft + width のこの割合（soft 0.035・width 0.05 で 0.03）
CHAR_HALF = 0.04 / 0.085               # 焦げの帯の半分の幅（同じく 0.04）。帯は前線から R の内側に収まる


def _lattices(seed, scale, aspect):
    """むらごとの (升目の大きさ, 重み, 格子の値, 格子のずれ)。升目の数は scale と縦横比だけで決まる。"""
    out = []
    for octave, (size, weight) in enumerate(OCTAVES):
        rng = timeline.seeded_rng(seed, f"burn:{octave}")
        cell = scale / CELLS_PER_SCALE * size
        offset = rng.random(2) + 1.0                            # むらごとに升目の端をずらす（+1 は左上の隣の格子点の分）
        columns, rows = int(math.ceil(1.0 / cell)) + 5, int(math.ceil(aspect / cell)) + 5
        out.append((cell, weight, rng.random((rows, columns)).astype(np.float32), offset))
    return out


def _kernel(x):
    """3 次の補間の核（係数 CUBIC）。|x| ≤ 1 と 1 < |x| < 2 の 2 つの式。"""
    x = np.abs(x)
    near = ((CUBIC + 2.0) * x - (CUBIC + 3.0)) * x * x + 1.0
    far = ((CUBIC * x - 5.0 * CUBIC) * x + 8.0 * CUBIC) * x - 4.0 * CUBIC
    return np.where(x <= 1.0, near, np.where(x < 2.0, far, 0.0)).astype(np.float32)


def _weights(f):
    """格子点 −1・0・+1・+2 の重み（和は 1）。"""
    return [_kernel(f + 1.0), _kernel(f), _kernel(1.0 - f), _kernel(2.0 - f)]


def _sample(lattices, width, height):
    """幅 width・高さ height の画素の中心で読んだむらの和（float32 (H, W)。まだ伸ばしていない）。"""
    u = (np.arange(width, dtype=np.float64) + 0.5) / width
    v = (np.arange(height, dtype=np.float64) + 0.5) / width      # 縦も画面の幅が単位
    total = np.zeros((height, width), dtype=np.float32)
    for cell, weight, lattice, offset in lattices:
        x, y = u / cell + offset[0], v / cell + offset[1]
        column, row = np.floor(x).astype(np.intp), np.floor(y).astype(np.intp)
        rows = sum(w[:, None] * lattice[row - 1 + k] for k, w in enumerate(_weights(y - row)))       # 縦につないだ (H, 格子の列)
        total += np.float32(weight) * sum(w[None, :] * rows[:, column - 1 + k] for k, w in enumerate(_weights(x - column)))
    return total


def noise(seed, scale, size):
    """seed と scale で決まるなめらかなノイズ（float32 (H, W)、0..1）。size は出力の (W, H)。

    0..1 に伸ばす最小と最大は、同じ場を決まった細かさの格子（粗い升目あたり REFERENCE_STEPS 点）で読んで決める。出力の解像度を変えても、
    同じ正規化座標の値は変わらない（その格子より細かい所の山と谷は 0 と 1 で切る）。"""
    width, height = size
    aspect = height / width
    lattices = _lattices(seed, scale, aspect)
    steps = int(math.ceil(REFERENCE_STEPS * CELLS_PER_SCALE / scale))
    across = min(max(steps, REFERENCE_WIDTHS[0]), REFERENCE_WIDTHS[1])
    reference = _sample(lattices, across, max(1, int(round(across * aspect))))
    low, high = float(reference.min()), float(reference.max())
    return np.clip((_sample(lattices, width, height) - np.float32(low)) / np.float32(max(high - low, 1e-6)), 0.0, 1.0)


def distance(origin, size):
    """from（画面の正規化座標 [x, y]）からの距離（float32 (H, W)）。いちばん遠い画面の隅で 1。"""
    width, height = size
    aspect = height / width
    u = (np.arange(width, dtype=np.float64) + 0.5) / width
    v = (np.arange(height, dtype=np.float64) + 0.5) / width
    ox, oy = origin[0], origin[1] * aspect
    farthest = max(math.hypot(cx - ox, cy - oy) for cx in (0.0, 1.0) for cy in (0.0, aspect))
    return (np.hypot((u - ox)[None, :], (v - oy)[:, None]) / max(farthest, 1e-6)).astype(np.float32)


def field(burn, size):
    """正規化済みの burn の設定と出力の (W, H) から、場 N'（float32 (H, W)、0..1）。"""
    base = noise(burn["seed"], burn["scale"], size)
    if burn["bias"] <= 0:
        return base
    bias = np.float32(burn["bias"])
    return base * (np.float32(1.0) - bias) + distance(burn["from"], size) * bias


def reach(burn):
    """R = soft + 縁の width。前線から、縁と焦げが届く場の値の幅で、しきい値の両端の余白でもある。"""
    return burn["soft"] + (burn["edge"]["width"] if burn["edge"] else 0.0)


def threshold(burn, p):
    """進み p（0..1）のしきい値。"""
    margin = reach(burn)
    return -margin + p * (1.0 + 2.0 * margin)


def composite(before, now, field, p, burn):
    """前の画 before と今の画 now（表示の値。uint8 か float32 の (H, W, 3)）を、進み p の焼け抜けで混ぜた float32（まだ切らない）。"""
    if p <= 0.0:
        return before.astype(np.float32)
    if p >= 1.0:
        return now.astype(np.float32)
    edge, margin = burn["edge"], np.float32(reach(burn))
    ahead = field - np.float32(threshold(burn, p))               # 前線から先（まだ前のショットの側）が正
    m = np.clip(-ahead / np.float32(burn["soft"]), 0.0, 1.0)[..., None]
    image = before.astype(np.float32) * (np.float32(1.0) - m) + now.astype(np.float32) * m
    if burn["char"] > 0:
        band = np.clip(1.0 - np.abs(ahead - margin * np.float32(CHAR_AHEAD)) / (margin * np.float32(CHAR_HALF)), 0.0, 1.0)
        image = image * (np.float32(1.0) - np.float32(burn["char"]) * band)[..., None]
    if edge:
        e = np.clip(1.0 - np.abs(ahead) / np.float32(edge["width"]), 0.0, 1.0)
        glow = np.minimum(np.asarray(edge["color"], np.float32) * (np.float32(edge["gain"]) * e)[..., None], np.float32(255.0))
        image = image + (np.float32(255.0) - image) * (glow / np.float32(255.0))
    return image
