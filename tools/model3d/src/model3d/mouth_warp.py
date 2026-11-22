"""絵の口を、もとの口の絵を変形して作る（texture.warp_mouth。Blender なし。numpy と Pillow だけ）。

4 作目の 3D のダンスで、顔が 1 枚の色の絵で描かれたモデル（目も口も絵。形のキーが無い）の口の表情を、正面の正射影の描画の上で作った台本を
移した。口を肌で消して形を描く作り方は、もとの絵にある物（口角の濃い印・やわらかい線・下唇の赤みと光）が無くなり、色の形を貼ったように
見えたので、もとの絵の画素を動かす（消さない）。

1. もとの口の線を色で測る（line の色の決まりに入る画素の、探す範囲 window の中のいちばん大きいかたまり）: 左右の端、列ごとの線の高さ
   （かたまりの画素の高さの平均。25 画素の幅でならす）、左右の傾き。
2. 口を、真ん中のまわりで横に scale_x 倍する（口のまわりだけ。離れるほど元へ戻す）。口角の印もいっしょに動く。
3. 線を境に、上（上唇の側）と下（下唇の側）を別々に上下へ動かす。上 = 口角を上げる（corner_lift。負なら下げる）・真ん中を下げる
   （upper_sag）・真ん中を上げる（upper_arch）。下 = さらに、開ける量（open）だけ下げる。動かす量は、線から離れるほど 0 へ戻す。
   開けた口は、あごが下がるぶん口角も下げる（開ける量の 27 % ほど。こうしないと、上の縁が平らな「コップの形」になる）。これは corner_lift
   を負にして書く。口角を上げるときは、もとの線の左右の傾きもならす。
4. 上と下のあいだのすき間を、口の中の色（inside_color。上ほど暗い）で埋め、下に舌（tongue_color）。すき間は口角の印（corner_mark 画素）の
   内側で 0 になるので、口角はもとの絵の口角のまま細くとがる。
あごの線（jaw。任意）: 列ごとに、口の線の下から、緑の値が green_below より小さくなる所（肌が首の色に変わる所）をあごの線とし、動かす範囲と
写す範囲をその内側に収める（収めないと、あごの線がずれて、首に顔の肌色がはみ出す）。
出すのは、描いた絵と、写す範囲（動かした画素とすき間を 13 画素ふくらませ、σ 4 でぼかす）。
台本は OpenCV（remap の 3 次の補間・ぼかし・ふくらませ）で書いた。ここでは同じ計算を numpy で書いた。
計算の作りの長さ（ならしの幅・なだらかに戻す距離・写す範囲のふくらませとぼかし。LENGTHS・JAW）は、台本を 0.1 mm/画素の正面の描画で
回したときの値を既定にし、scale で伸ばす。顔の形に寄る長さ（唇の高さ lips、あごの線 jaw の探し始め・収める高さ）は既定を置かない。
"""
import numpy as np

from .color_marks import color_rule, largest_component
from .eye_line import gaussian

A = -0.75                                                              # 3 次の補間の係数（OpenCV と同じ）
# 計算の作りの長さ（画素。ならしの幅・なだらかに戻す距離・写す範囲のふくらませとぼかし）。台本を 0.1 mm/画素の描画で回したときの値で、
# scale で伸ばす。顔の形に寄る長さ（唇の高さ lips・あごの線 jaw）は既定を置かず、工程で必ず書く。
LENGTHS = {"smooth": 25, "core": 12, "fall": 100.0, "above_fade": 50, "below_fade": 60, "upper_fade": 60, "lower_fade": 150.0,
           "mask_grow": 13, "mask_blur": 4.0}
JAW = {"blur": 1.5, "erode": 13, "mask_fade": 6}
LIPS_KEYS = ("above", "below", "upper_keep", "lower_keep")              # 顔の形に寄る長さ（必須）
JAW_KEYS = ("green_below", "start_below_line", "keep_above", "mask_above")   # あごの線を測るときに必須


def cubic_weights(fraction):
    """端数 (…,)（0〜1）の、4 つの画素の重み (…, 4)（OpenCV の interpolateCubic と同じ式）。"""
    x = np.asarray(fraction, dtype=np.float64)
    c0 = ((A * (x + 1) - 5 * A) * (x + 1) + 8 * A) * (x + 1) - 4 * A
    c1 = ((A + 2) * x - (A + 3)) * x * x + 1
    c2 = ((A + 2) * (1 - x) - (A + 3)) * (1 - x) * (1 - x) + 1
    return np.stack([c0, c1, c2, 1 - c0 - c1 - c2], axis=-1)


def remap_cubic(image, map_x, map_y):
    """image (H, W, C) を、出力の画素ごとの元の座標 (map_x, map_y)（画素の中心が整数）で 3 次の補間で引く（縁は端の画素を伸ばす）。
    座標は丸めない（台本を回した OpenCV 5 の remap と同じ。OpenCV 4 は 1/32 画素に丸めていた）。元の位置のままの画素は計算しない。"""
    height, width = image.shape[:2]
    out = image.astype(np.float64).copy()
    ident_y, ident_x = np.mgrid[0:height, 0:width]
    map_x, map_y = np.asarray(map_x, dtype=np.float64), np.asarray(map_y, dtype=np.float64)
    ys, xs = np.nonzero((map_x != ident_x) | (map_y != ident_y))
    if not len(ys):
        return out
    sx, sy = map_x[ys, xs], map_y[ys, xs]
    ix, iy = np.floor(sx).astype(np.int64), np.floor(sy).astype(np.int64)
    wx, wy = cubic_weights(sx - ix), cubic_weights(sy - iy)
    total = np.zeros((len(ys), image.shape[2]))
    for j in range(4):
        row = np.clip(iy - 1 + j, 0, height - 1)
        for i in range(4):
            column = np.clip(ix - 1 + i, 0, width - 1)
            total += (wy[:, j] * wx[:, i])[:, None] * image[row, column]
    out[ys, xs] = total
    return out


def smooth_step(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def ramp(t):
    """0 → 1。まっすぐと S 字の中間（傾きの山を低くして、折り返しを防ぐ）。"""
    t = np.clip(t, 0.0, 1.0)
    return 0.5 * t + 0.5 * t * t * (3 - 2 * t)


def upper_rows(rows, line, shift, keep, fade):
    """線より上の行 rows（増える並び）を動かした先: 線から keep まではそのまま shift 動かし、その先でなだらかに 0 へ戻す。
    ramp の傾きは最大 1.25 なので、上へ fade / 1.25 より大きく動かすと並びが折れ返る（顔の画素がとんだり重なったりする）。下と同じく、
    戻す長さを動く量の 1.3 倍まで広げる（台本の値（動く量が fade の 0.8 倍まで）では、台本と同じ）。"""
    reach = max(fade, 1.3 * abs(shift))
    return rows + shift * (1 - ramp((line - rows - keep) / reach))


def measure(image, settings):
    """もとの口の線を測る。戻り: 測った値の辞書（列ごとの線の高さ line_x・line_y を含む）。見つからなければ ValueError。"""
    x0, y0, x1, y1 = settings["window"]
    found = largest_component(color_rule(image[y0:y1, x0:x1], settings["line"]))
    if found is None:
        raise ValueError(f"探す範囲 {settings['window']} に、口の線の色（line）の画素がありません")
    columns = np.flatnonzero(found.any(axis=0))
    lx, rx = x0 + int(columns.min()), x0 + int(columns.max())
    centre, half = (lx + rx) / 2.0, (rx - lx) / 2.0
    line_x = np.arange(lx, rx + 1, dtype=np.float64)
    rows = np.arange(y0, y1, dtype=np.float64)
    line_y = np.array([rows[found[:, x - x0]].mean() if found[:, x - x0].any() else np.nan for x in range(lx, rx + 1)])
    known = ~np.isnan(line_y)
    line_y = np.interp(line_x, line_x[known], line_y[known])
    width = max(int(round(settings["lengths"]["smooth"])), 1) | 1        # 奇数の幅（両側に同じだけ広げて、長さを変えない）
    pad = np.pad(line_y, width // 2, mode="edge")
    line_y = np.convolve(pad, np.ones(width) / width, mode="valid")
    tilt = float(np.polyfit(line_x - centre, line_y, 1)[0])
    return {"left": lx, "right": rx, "centre_x": centre, "half_width": half, "line_y_mean": float(line_y.mean()),
            "line_y_left": float(line_y[0]), "line_y_right": float(line_y[-1]), "tilt": tilt, "line_x": line_x, "line_y": line_y}


def jaw_line(image, line_mean, settings):
    """列ごとのあごの線の高さ (W,)（あごの線を測らないときは、絵の高さ）。"""
    height, width = image.shape[:2]
    jaw = settings.get("jaw")
    if jaw is None:
        return np.full(width, float(height))
    soft = gaussian(image[..., 1], jaw["blur"])
    start = int(line_mean) + int(round(jaw["start_below_line"]))
    found = np.full(width, float(height))
    below = soft[start:] < jaw["green_below"]
    has = below.any(axis=0)
    found[has] = start + np.argmax(below[:, has], axis=0)
    half = int(jaw["erode"]) // 2                                       # 左右 half 画素の中で、いちばん上（控えめに）
    padded = np.pad(found, half, mode="constant", constant_values=np.inf)
    return np.min([padded[k:k + width] for k in range(2 * half + 1)], axis=0)


def warp(image, settings):
    """口を変形した絵 (H, W, 3)（0〜255、丸める前）、写す範囲 (H, W)（0〜1）、報告。image は RGB (H, W, 3)。"""
    height, width = image.shape[:2]
    f = image.astype(np.float64)
    lengths = settings["lengths"]
    m = measure(f, settings)
    cx, hw0, line_mean = m["centre_x"], m["half_width"], m["line_y_mean"]
    jaw = jaw_line(f, line_mean, settings)
    XS, YS = np.arange(width, dtype=np.float64), np.arange(height, dtype=np.float64)
    ident_x = np.tile(XS, (height, 1))
    ident_y = np.tile(YS[:, None], (1, width))
    k = settings["scale_x"]
    map_x = ident_x.copy()
    scaled = f
    if abs(k - 1) >= 1e-3:                                             # 2. 横に伸び縮み（口のまわりだけ）
        core, fall = hw0 + lengths["core"], lengths["fall"]
        t = np.abs(XS - cx)
        shift = np.where(t <= core, (k - 1) * (XS - cx), np.sign(XS - cx) * (k - 1) * core * (1 - smooth_step((t - core) / fall)))
        dy = YS - line_mean
        weight_y = np.where(dy < 0, 1 - smooth_step((-dy - lengths["above"]) / lengths["above_fade"]),
                            1 - smooth_step((dy - lengths["below"]) / lengths["below_fade"]))
        for y in np.flatnonzero(weight_y > 1e-4):
            map_x[y] = np.interp(XS, XS + weight_y[y] * shift, XS)       # 行ごと: 動かした先 → もとの x
        map_x = map_x.astype(np.float32).astype(np.float64)
        scaled = remap_cubic(f, map_x, ident_y)
    lift, sag, arch, drop, full = (settings[key] for key in ("corner_lift", "upper_sag", "upper_arch", "open", "bulge"))
    mark = settings.get("corner_mark") or 0.0
    if mark >= hw0:
        raise ValueError(f"口角の印の長さ corner_mark（{mark:g} 画素）が、測った口の幅の半分（{hw0:g} 画素）以上です（すき間が作れません）")
    hw, open_hw = hw0 * k, (hw0 - mark) * k
    u, uo = (XS - cx) / hw, (XS - cx) / max(open_hw, 1e-9)
    yl = np.interp(cx + (XS - cx) / k, m["line_x"], m["line_y"])        # 横に伸び縮みさせたあとの、線の高さ
    au = np.abs(u)
    beyond = 0.5 * (1 + np.cos(np.pi * np.clip((au - 1) / 0.6, 0, 1)))
    lift_shape = np.where(au <= 1, au ** 2, beyond)                      # 口角で最大、その外でなだらかに 0
    if lift < 0:                                                         # 下げるとき: 印の内側の端で最大にして、印は水平のまま下げる
        lift_shape = np.where(au <= 1, np.clip(np.abs(uo), 0, 1) ** 2, lift_shape)
    a = sag * np.clip(1 - u * u, 0, 1) - lift * lift_shape - arch * np.clip(1 - uo * uo, 0, 1)   # 上唇の動き（+ = 下）
    if lift > 0 and settings["level_tilt"]:                              # 笑う口: もとの線の左右の傾きをならす
        # 横に k 倍したあとの線の傾きは tilt / k。台本は tilt × k で引いていた（k ≠ 1 で、広げた口は直しすぎ、狭めた口は足りない）。
        # 既定は台本と同じにし（照合を崩さない）、tilt_after_stretch で正しい傾きを選べる
        slope = m["tilt"] / k if settings.get("tilt_after_stretch") else m["tilt"] * k
        a = a - slope * (XS - cx) * np.where(au <= 1, 1.0, beyond)
    d = drop * np.clip(1 - np.abs(uo) ** full, 0, 1)                      # すき間の高さ（口角の印の内側で 0）
    top, bottom = yl + a, yl + a + d
    fall_low = max(lengths["lower_fade"], 1.7 * float(np.max(np.abs(a + d))))
    map_y = ident_y.copy()
    tight = 0
    keep = settings["jaw"]["keep_above"] if settings.get("jaw") is not None else 0.0
    for x in range(max(0, int(cx - 1.65 * hw - 4)), min(width, int(cx + 1.65 * hw + 5))):
        if abs(a[x]) < 0.01 and d[x] < 0.01:
            continue
        upper = np.concatenate([YS[YS < yl[x]], [yl[x]]])                # 線より上: upper_keep まではそのまま動かし、その先 upper_fade で 0 へ
        upper_to = upper_rows(upper, yl[x], a[x], lengths["upper_keep"], lengths["upper_fade"])
        lower = np.concatenate([[yl[x]], YS[YS > yl[x]]])                # 線より下: lower_keep（下唇の赤みと光）まではそのまま、その先で 0 へ
        fall_here = min(fall_low, jaw[x] - keep - (yl[x] + lengths["lower_keep"]))   # あごの線の keep_above 画素上までに 0 へ
        if fall_here < 1.3 * abs(a[x] + d[x]):                           # 収まらない（折り返す）ときは、折り返さない長さを優先して数える
            fall_here, tight = 1.3 * abs(a[x] + d[x]), tight + 1
        lower_to = lower + (a[x] + d[x]) * (1 - ramp((lower - yl[x] - lengths["lower_keep"]) / fall_here))
        middle = (top[x] + bottom[x]) / 2
        is_up = YS <= middle
        map_y[is_up, x] = np.interp(YS[is_up], upper_to, upper)
        map_y[~is_up, x] = np.interp(YS[~is_up], lower_to, lower)
    map_y = map_y.astype(np.float32).astype(np.float64)
    warped = remap_cubic(scaled, ident_x, map_y)
    yy = YS[:, None]
    cover = np.clip(np.minimum(yy - top[None, :], bottom[None, :] - yy) + 0.5, 0, 1) * np.clip(d[None, :] / 1.5, 0, 1)   # すき間
    if drop > 0:
        depth = np.clip((yy - top[None, :]) / np.maximum(d[None, :], 1.0), 0, 1)
        inside = np.asarray(settings["inside_color"], dtype=np.float64)[None, None, :] * (0.74 + 0.26 * depth)[..., None]   # 上ほど暗い
        bottom_c = float(np.interp(cx, XS, bottom))
        rx_t, ry_t = open_hw * 0.62, drop * 0.42
        tongue = np.clip((1 - ((XS[None, :] - cx) / rx_t) ** 2 - ((yy - bottom_c) / ry_t) ** 2) * ry_t / 3.0, 0, 1)
        tongue_color = np.asarray(settings["tongue_color"], dtype=np.float64)
        inside = inside * (1 - tongue[..., None]) + tongue_color[None, None, :] * tongue[..., None]
        warped = warped * (1 - cover[..., None]) + inside * cover[..., None]
    moved = (np.abs(map_y - ident_y) > 0.2) | (np.abs(map_x - ident_x) > 0.2) | (cover > 0)
    grow = int(lengths["mask_grow"]) // 2
    padded = np.pad(moved, grow)
    grown = np.zeros_like(moved)
    for dy in range(2 * grow + 1):
        for dx in range(2 * grow + 1):
            grown |= padded[dy:dy + height, dx:dx + width]
    mask = gaussian(grown.astype(np.float64), lengths["mask_blur"])
    if settings.get("jaw") is not None:                                  # あごの線の mask_above 画素上から下（回り込みと首）は写さない
        mask = mask * np.clip((jaw[None, :] - settings["jaw"]["mask_above"] - yy) / settings["jaw"]["mask_fade"], 0, 1)
    on = mask > 0.004
    used = on.any(axis=0)
    rows, cols = np.nonzero(on)
    lowest = height - 1 - np.argmax(on[::-1], axis=0)
    report = {k_: (round(v, 4) if isinstance(v, float) else v) for k_, v in m.items() if k_ not in ("line_x", "line_y")}
    report.update(jaw_y_at_centre=float(jaw[int(cx)]) if settings.get("jaw") is not None else None,
                  mask_box=[int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())] if len(rows) else None,
                  above_jaw_px=float(np.min(jaw[used] - lowest[used])) if used.any() and settings.get("jaw") is not None else None,
                  columns_not_above_jaw=tight, opened_pixels=int((cover > 0.5).sum()))
    return warped, mask, report
