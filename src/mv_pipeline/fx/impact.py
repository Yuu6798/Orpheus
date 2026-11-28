"""山場の演出の部品（一斉に飛ぶ火花・衝撃の輪・閃光・揺れ・点の閃き・暗転）。fx-timeline の hooks から使う（実験的。docs/fx.md）。

fire・sky・accent と同じく、**画面の座標**（出力の画素。画素の中心が整数）と**表示の値**（float32 の 0..255、ガンマ符号化）の
上で計算する。時刻 t と引数だけで決まる（状態を持たない。乱数は seed から。区間を分けて描いても同じ画）。t が窓の外なら
画を変えずにそのまま返す（ビット単位で同じ）。

長さ（速さ・半径・太さ・ぼかし）は fire.embers・particles.gather と同じく**幅 1920 の画素**で書き、k を掛けて画面の画素にする。
絵に付いて動くもの（火花・輪・閃き）は hooks の shot の中で k = view.px を渡す（寄りでも引きでも、絵に対して同じ大きさ）。
画面に付くもの（揺れ）は画面の短い辺に対する割合で書く。

    burst(f, t, t0, origin, k)   t0 に origin から一斉に飛び散る火花（重力で落ちる短い筋。fire.embers と違い、生まれ直さない）
    ring(f, t, t0, center, k)    地面に沿って広がる横長の光の輪（衝撃の輪）
    flash(f, t, t0)              画面全体の閃光（timeline の flash のつなぎと同じ式。ショットを切らずに掛けられる）
    shake(f, t, t0)              減っていく画面の揺れ（端が見えないように少し拡大して写す。**新しい画を返す**）
    glint(f, X, Y, t, t0, k)     点の閃き（細い横の光の筋と芯。目の光など）
    fade(f, t, end)              終わりの暗転（end の duration 秒前から黒へ。smoothstep）

shake 以外は f を書き換えて返す（形と型は変えない）。光は fire と同じスクリーン合成で重ねるので、明るい所が白に飛びにくい。
描く範囲は光の届く所だけに絞り、その範囲の角を 4 画素の升目にそろえる（fire.blur が縮めてぼかす倍率の最大。升目がそろうと、
光が動いても光彩が揺れない。particles と同じ考え）。
"""
import math

import cv2
import numpy as np

from .fire import blur, screen
from .timeline import seeded_rng

GRID = 4                                  # 描く範囲の角をそろえる升目（画素）
SPARK = (255, 222, 168)                   # 火花の芯の色
SPARK_GLOW = (255, 128, 36)               # 火花の光彩の色（fire.embers の光彩に近い橙）
RING = (255, 232, 200)                    # 衝撃の輪の色
FLASH = (255, 246, 232)                   # 閃光の色（温かい白。timeline の flash の既定 (235, 210, 255) とは別）
GLINT = (190, 228, 255)                   # 閃きの色（青白い光）


def smoothstep(x):
    x = min(max(float(x), 0.0), 1.0)
    return x * x * (3.0 - 2.0 * x)


def _roi(x0, y0, x1, y1, pad, width, height):
    """(x0, y0)–(x1, y1) を pad 広げ、角を GRID の升目にそろえて画面に収めた範囲 (x0, y0, x1, y1)。狭すぎれば None。"""
    gx0 = max(0, int(math.floor((x0 - pad) / GRID)) * GRID)
    gy0 = max(0, int(math.floor((y0 - pad) / GRID)) * GRID)
    gx1 = min(width, int(math.ceil((x1 + pad) / GRID)) * GRID)
    gy1 = min(height, int(math.ceil((y1 + pad) / GRID)) * GRID)
    if gx1 - gx0 < 2 or gy1 - gy0 < 2:
        return None
    return gx0, gy0, gx1, gy1


def _screen_into(f, roi, layer, color, gain):
    """f の roi に color × clip(layer × gain) をスクリーン合成する。"""
    x0, y0, x1, y1 = roi
    sub = f[y0:y1, x0:x1]
    sub[:] = screen(sub, np.asarray(color, np.float32)[None, None] * np.clip(layer * gain, 0, 1)[..., None])


def burst(f, t, t0, origin, k=1.0, n=200, seed=0, life=(0.4, 1.4), speed=(420.0, 1600.0), angle=(-170.0, -10.0),
          gravity=1400.0, delay=0.08, trail=0.04, width=2.0, glow=6.0, color=SPARK, glow_color=SPARK_GLOW, amt=1.0):
    """t0 に origin（画面の画素 (X, Y)）から n 個の火花が一斉に飛び散る。重力で落ちる短い筋で、寿命が来ると消える。

    angle は飛ぶ向きの範囲（度。0 が右、−90 が真上。既定は上半分）、speed は速さ（幅 1920 の画素/秒）、gravity は重力
    （幅 1920 の画素/秒²）、life は寿命（秒）、delay は飛び出しのばらつき（秒）、trail は筋の長さ（その秒数に進む分）、
    width・glow は芯の太さと光彩のぼかし（幅 1920 の画素）。どれも k 倍して画面の画素にする。
    火花ごとの値は seed から決まる（n を変えると並びが変わる）。t0 の前と、いちばん遅い火花が消えた後は画を変えない。"""
    elapsed = t - t0
    if amt <= 0 or n <= 0 or elapsed < 0 or elapsed > life[1] + delay:
        return f
    height, width_px = f.shape[:2]
    rng = seeded_rng(seed, "impact.burst")
    angles = np.deg2rad(rng.uniform(angle[0], angle[1], n))
    speeds = rng.uniform(speed[0], speed[1], n) * k
    lives = rng.uniform(life[0], life[1], n)
    delays = rng.uniform(0.0, delay, n)
    age = elapsed - delays
    alive = (age > 0) & (age < lives)
    if not alive.any():
        return f
    vx, vy, g = speeds * np.cos(angles), speeds * np.sin(angles), gravity * k
    head = age
    tail = np.maximum(0.0, age - trail)
    x1, y1 = origin[0] + vx * head, origin[1] + vy * head + 0.5 * g * head * head
    x0, y0 = origin[0] + vx * tail, origin[1] + vy * tail + 0.5 * g * tail * tail
    margin = 50.0 * max(k, 1.0)
    inside = alive & (x1 > -margin) & (x1 < width_px + margin) & (y1 > -margin) & (y1 < height + margin)
    if not inside.any():
        return f
    xs, ys = np.concatenate([x0[inside], x1[inside]]), np.concatenate([y0[inside], y1[inside]])
    thick = max(1, int(round(width * k)))
    roi = _roi(float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max()), 3.0 * glow * k + thick + 4, width_px, height)
    if roi is None:
        return f
    rx, ry = roi[0], roi[1]
    canvas = np.zeros((roi[3] - roi[1], roi[2] - roi[0]), np.float32)
    bright = np.clip(1.0 - age / lives, 0.0, 1.0) ** 1.2
    for i in np.flatnonzero(inside):
        cv2.line(canvas, (int(round((x0[i] - rx) * 4)), int(round((y0[i] - ry) * 4))),
                 (int(round((x1[i] - rx) * 4)), int(round((y1[i] - ry) * 4))), float(bright[i]), thick, cv2.LINE_AA, shift=2)
    if not canvas.any():
        return f
    _screen_into(f, roi, canvas, color, 1.2 * amt)
    _screen_into(f, roi, blur(canvas, glow * k), glow_color, 2.0 * amt)
    return f


def ring(f, t, t0, center, k=1.0, duration=0.5, radius=600.0, flat=0.28, width=7.0, glow=10.0, color=RING, amt=1.0):
    """t0 から duration 秒、center（画面の画素）から横長の光の輪が広がって消える（地面を走る衝撃の輪）。

    半径は radius·(1 − (1 − x)²)（x = (t − t0)/duration。速く広がって止まる）、縦は横の flat 倍。明るさは (1 − x)^1.5、
    線の太さは width·(1 − x) + 2。長さは幅 1920 の画素（k 倍）。窓の外は画を変えない。"""
    x = (t - t0) / duration if duration > 0 else 1.0
    if amt <= 0 or x < 0 or x >= 1:
        return f
    height, width_px = f.shape[:2]
    r = radius * k * (1.0 - (1.0 - x) ** 2)
    alpha = (1.0 - x) ** 1.5
    thick = max(1, int(round((width * (1.0 - x) + 2.0) * k)))
    cx, cy = center
    pad = 3.0 * glow * k + thick + 4
    roi = _roi(cx - r, cy - r * flat, cx + r, cy + r * flat, pad, width_px, height)
    if roi is None:
        return f
    canvas = np.zeros((roi[3] - roi[1], roi[2] - roi[0]), np.float32)
    cv2.ellipse(canvas, (int(round((cx - roi[0]) * 4)), int(round((cy - roi[1]) * 4))),
                (max(1, int(round(r * 4))), max(1, int(round(r * flat * 4)))), 0, 0, 360, 1.0, thick, cv2.LINE_AA, shift=2)
    if not canvas.any():
        return f
    canvas = blur(canvas, 1.2 * k)
    halo = blur(canvas, glow * k)
    _screen_into(f, roi, canvas * 0.8 * alpha + halo * 1.3 * alpha, color, amt)
    return f


def flash(f, t, t0, strength=0.75, color=FLASH, rate=13.0, window_s=0.30):
    """画面全体の閃光。t0 から window_s 秒の間、f + (color − f)·strength·exp(−(t − t0)·rate)。

    fx-timeline の flash のつなぎと同じ式（色の既定だけ違う）。ショットを切らずに、連続したカメラの途中に掛けられる。
    1 回の閃光は、明るさの上がりと下がりの 1 組（`mv gate flashes` の明滅 1 回）。窓の外は画を変えない。
    山の所で画の濃淡は (1 − strength) 倍になる。明るい絵は明るさがあまり増えずに濃淡だけが減るので、strength を下げる
    （mv fx quick は山場の画面の明るさから 0.65〜0.3 を選ぶ）。"""
    elapsed = t - t0
    if strength <= 0 or elapsed < 0 or elapsed >= window_s:
        return f
    weight = np.float32(strength * math.exp(-elapsed * rate))
    f += (np.asarray(color, np.float32)[None, None] - f) * weight
    return f


def shake(f, t, t0, amp=0.012, duration=0.5, hz=14.0, seed=0, punch=0.0):
    """t0 から duration 秒、画面を揺らす。揺れ幅は画面の短い辺 × amp × (1 − x)²（x = (t − t0)/duration）。

    揺れの向きは周波数の違う 2 つの正弦の和（位相は seed から。なめらかで、同じ t なら同じ揺れ）。端が見えないように、
    揺れ幅の分だけ画面の中心で拡大して写す（最大 1 + 2·amp 倍）。punch を書くと、さらに (1 + punch·(1 − x)²) 倍に押し込む。
    **新しい画を返す**（窓の外は f をそのまま返す）。shot で絵に付けた火花や輪も一緒に揺らすなら、hooks の final で掛ける。"""
    x = (t - t0) / duration if duration > 0 else 1.0
    if x < 0 or x >= 1 or (amp <= 0 and punch <= 0):
        return f
    height, width = f.shape[:2]
    env = (1.0 - x) ** 2
    phase = seeded_rng(seed, "impact.shake").uniform(0.0, 2.0 * math.pi, 4)
    elapsed = t - t0
    nx = 0.6 * math.sin(2 * math.pi * hz * elapsed + phase[0]) + 0.4 * math.sin(2 * math.pi * hz * 1.73 * elapsed + phase[1])
    ny = 0.6 * math.sin(2 * math.pi * hz * 1.13 * elapsed + phase[2]) + 0.4 * math.sin(2 * math.pi * hz * 2.11 * elapsed + phase[3])
    side = min(width, height)
    dx, dy = amp * side * env * nx, amp * side * env * ny
    zoom = (1.0 + 2.0 * max(amp, 0.0) * env) * (1.0 + max(punch, 0.0) * env)
    cx, cy = (width - 1) / 2.0, (height - 1) / 2.0
    matrix = np.array([[zoom, 0.0, (1.0 - zoom) * cx + dx], [0.0, zoom, (1.0 - zoom) * cy + dy]], np.float32)
    return cv2.warpAffine(f, matrix, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def glint(f, X, Y, t, t0, k=1.0, duration=0.7, length=24.0, thick=2.2, core=6.0, color=GLINT, amt=0.9):
    """(X, Y)（画面の画素）の閃き。細い横の光の筋（左右へ exp(−|dx|/length) で消える）と丸い芯。

    t0 から duration 秒で、強さ amt·sin²(π·x) で上がって消える（x = (t − t0)/duration）。長さは幅 1920 の画素（k 倍）。
    既定の大きさは、素材の幅を 1920 とした画素で半径 150 ほどの顔の目に合う（筋が見えるのは点の左右に length の 2〜3 倍まで）。
    顔の大きさが違う絵では k を顔の半径 / 150 倍する（mv fx quick の hooks の GLINT_K）。k = view.px なら寄っても引いても
    絵に対して同じ大きさ。窓の外は画を変えない。目の光にするなら、点を目に合わせて目ごとに呼ぶ。"""
    x = (t - t0) / duration if duration > 0 else 1.0
    if amt <= 0 or x < 0 or x >= 1:
        return f
    level = amt * math.sin(math.pi * x) ** 2
    if level <= 0.005:
        return f
    height, width = f.shape[:2]
    reach, band, dot = max(1.0, length * k), max(0.5, thick * k), max(0.5, core * k)
    roi = _roi(X - 6.0 * reach, Y - 4.0 * max(3.0 * band, dot), X + 6.0 * reach, Y + 4.0 * max(3.0 * band, dot), 0, width, height)
    if roi is None:
        return f
    ys, xs = np.mgrid[roi[1]:roi[3], roi[0]:roi[2]].astype(np.float32)
    along = np.exp(-np.abs(xs - np.float32(X)) / np.float32(reach))
    dy = ys - np.float32(Y)
    streak = np.exp(-(dy / np.float32(band)) ** 2) * along + 0.3 * np.exp(-(dy / np.float32(3.0 * band)) ** 2) * along
    spot = np.exp(-((xs - np.float32(X)) ** 2 + dy ** 2) / np.float32(2.0 * dot * dot))
    _screen_into(f, roi, streak * 0.85 + spot * 0.75, color, level)
    return f


def fade(f, t, end, duration=0.8):
    """終わりの暗転。end − duration から end までに、smoothstep で黒へ（end 以降は黒）。その前は画を変えない。"""
    if duration <= 0:
        return f
    level = smoothstep((end - t) / duration)
    if level >= 1.0:
        return f
    f *= np.float32(level)
    return f
