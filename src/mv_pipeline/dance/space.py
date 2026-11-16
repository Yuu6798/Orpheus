"""3D の要素別の描画（model3d の render.passes）を重ねる部品のうち、3D の位置とカメラを使うもの（`mv dance passes`）。

どれも**表示の値**（sRGB に符号化した 0〜1 の float32）で計算する。リポジトリ外の 2 作目の VRM のダンスの演出版の重ね合わせの台本と
同じ式・同じ順・同じ型で書いてある（同じ入力なら、台本と同じ値になる）。長さ（画素）は幅 1080 の画面での値で、unit（画面の幅 ÷ 1080）を
掛けて使う。

    mirror(size, parts, unit)                         床への映り込み: 床で反転したカメラで描いた要素をぼかして倍率を掛け、足し合わせる
    ripple(image, amount, clock, settings, unit)      水鏡: 映り込みを行ごとに横へゆらす（2 つのうねり）
    phantom(target, picture, strength, settings)      分身を炎の色の光として足す（phantom_glow は分身の映り込みのもと）
    sprite_layers(size, camera, items, sprites, …)    小さな絵を 3D の位置とカメラの控えから画面に置く（遠い・近い・映り込みの 3 枚）
    over_glow(image, layer, glow, glow_blur)          小さな絵の層を重ね、にじませる
    world(room, alpha, mix, settings, …)              背景を、部屋から別の世界（空・床の光のたまり）へ、踊り手から焼け抜けて切り替える（fx.burn）
    impact(frame, amount, zoom, steps)                衝撃: 少しずつ拡大した画を重ねて、奥へ吸い込まれる筋にする
    card(frame, rgb, alpha, corners, camera, …)        手前の板: 3D の 4 隅をカメラで写して射影で重ねる（card_corners で基準のカメラから置く）
    pillar(…)・wipe(old, new, …)                      柱が横切るつなぎ: 柱の位置を境に 2 つの画を入れ替え、柱を重ねる
"""
import math

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


# ---- 小さな絵を 3D の位置に置く（提灯など） ----------------------------------------------------------

SPRITES = {"gain": 1.05, "glow": 0.45, "glow_blur": 18.0, "focus_blur": 0.10, "max_blur": 5.0, "room_blur": 0.15, "near_clip": 0.3,
           "margin": 200.0, "fade": [1.25, 40.0, 0.45, 1.1], "mirror_blur": 2.0, "floor": 0.0, "mirror_mode": "rotate"}
MIRROR_MODES = ("rotate", "flip")        # 映り込みの絵の向き: rotate は 180° 回す（試作と同じ）、flip は左右を保って上下だけ返す
_flipped = {}


def flipped(levels):
    """左右を返した絵の並び（作り置く）。映り込みを flip で置くとき、上下を返す置き方と合わせて、左右を保った上下反転になる。"""
    key = id(levels)
    if key not in _flipped or _flipped[key][0] is not levels:
        _flipped[key] = (levels, [np.ascontiguousarray(level[:, ::-1]) for level in levels])
    return _flipped[key][1]


def sprite_levels(path):
    """小さな絵（RGBA の PNG。8 か 16 ビット、アルファは前乗算でない）→ 前乗算の (色, 不透明度) の float32 と、半分ずつ縮めたもの
    （短い辺が 12 画素以下になるまで。INTER_AREA）の並び。"""
    from pathlib import Path
    data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"小さな絵は RGBA の PNG にしてください: {path}")
    top = 65535.0 if image.dtype == np.uint16 else 255.0
    rgba = image[..., [2, 1, 0, 3]].astype(np.float32) / np.float32(top)
    alpha = rgba[..., 3:4]
    levels = [np.concatenate([rgba[..., :3] * alpha, alpha], axis=2).astype(np.float32)]
    while min(levels[-1].shape[:2]) > 12:
        levels.append(cv2.resize(levels[-1], (levels[-1].shape[1] // 2, levels[-1].shape[0] // 2), interpolation=cv2.INTER_AREA))
    return levels


def paste(layer, levels, centre, top, tone, sigma):
    """小さな絵を 1 つ置く（layer は前乗算の (色, 不透明度) の (H, W, 4)。書き換える）。centre と top は、絵の中心と上端の画面の
    位置（画素）。絵の縦を centre → top の向きに合わせ、高さ 2 × |top − centre| に縮める（倍率に近い大きさの縮めた絵から）。
    σ が 0.4 より大きければぼかす。重ねは前乗算の over（色 × tone）。"""
    half = math.hypot(top[0] - centre[0], top[1] - centre[1])
    if half < 1.5:
        return
    tall = 2 * half
    picture = levels[-1]
    for candidate in levels:
        picture = candidate
        if candidate.shape[0] <= tall * 2:
            break
    sprite_h, sprite_w = picture.shape[:2]
    s = tall / sprite_h
    down = ((centre[0] - top[0]) / half, (centre[1] - top[1]) / half)
    right = (down[1], -down[0])
    origin_x = centre[0] - (sprite_w / 2 * right[0] + sprite_h / 2 * down[0]) * s
    origin_y = centre[1] - (sprite_w / 2 * right[1] + sprite_h / 2 * down[1]) * s
    corners = np.array([[0, 0], [sprite_w, 0], [sprite_w, sprite_h], [0, sprite_h]], np.float64)
    xs = origin_x + (corners[:, 0] * right[0] + corners[:, 1] * down[0]) * s
    ys = origin_y + (corners[:, 0] * right[1] + corners[:, 1] * down[1]) * s
    pad = int(3 * sigma + 2)
    height, width = layer.shape[:2]
    x0, x1 = max(int(math.floor(xs.min())) - pad, 0), min(int(math.ceil(xs.max())) + pad, width)
    y0, y1 = max(int(math.floor(ys.min())) - pad, 0), min(int(math.ceil(ys.max())) + pad, height)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    transform = np.array([[right[0] * s, down[0] * s, origin_x - x0], [right[1] * s, down[1] * s, origin_y - y0]], np.float64)
    warped = cv2.warpAffine(picture, transform, (x1 - x0, y1 - y0), flags=cv2.INTER_LINEAR, borderValue=0)
    if sigma > 0.4:
        warped = cv2.GaussianBlur(warped, (0, 0), sigma)
    cover = warped[..., 3:4]
    region = layer[y0:y1, x0:x1]
    region[..., :3] = region[..., :3] * (1 - cover) + warped[..., :3] * tone
    region[..., 3:4] = region[..., 3:4] * (1 - cover) + cover


def sprite_layers(size, camera, items, sprites, settings=None, room_sigma=0.0, unit=1.0):
    """小さな絵を置いた 3 枚（踊り手より遠いもの、近いもの、床への映り込み）。どれも前乗算の (色, 不透明度) の (H, W, 4)。

    camera: {"matrix": 4×4（m）, "focal": 焦点距離（出力の画素）, "principal": 主点（出力の画素）, "focus": 踊り手までの距離 m}。
    items: (N, 6) の [x, y, z, 高さ, 明るさ, 絵の番号]（m）。絵の中心がその点で、上端は高さの半分だけ上。
    遠い順に置き、踊り手までの距離 focus より遠ければ遠い方に、近ければ近い方に。明るさ = 明るさ × clip(fade[0] − 奥行き ÷ fade[1],
    fade[2], fade[3]) × gain。ぼかし = min(|奥行き − focus| × focus_blur, max_blur) × unit + room_blur × 部屋の σ × unit。
    床への映り込みは、床の面（floor）で上下を返した位置に、ぼかしを mirror_blur × unit 足して置く（絵の向きは mirror_mode。rotate は
    180° 回す（試作と同じ。左右も入れ替わる）、flip は左右を保って上下だけ返す（床の映り込みとして正しい向き））。奥行き near_clip 以下と、
    画面の外 margin 画素より外と、上端（映り込みは下端）がカメラの後ろ（奥行き 0.05 m 以下）に回る絵は置かない。"""
    s = {**SPRITES, **(settings or {})}
    width, height = size
    far = np.zeros((height, width, 4), np.float32)
    near = np.zeros((height, width, 4), np.float32)
    mirror = np.zeros((height, width, 4), np.float32)
    items = np.asarray(items, np.float64).reshape(-1, 6)
    if not len(items):
        return far, near, mirror
    centres, heights, tones, kinds = items[:, :3], items[:, 3], items[:, 4], items[:, 5].astype(int)
    matrix = np.asarray(camera["matrix"], np.float64)
    right, up, back, position = matrix[:3, 0], matrix[:3, 1], matrix[:3, 2], matrix[:3, 3]
    focal, (px, py), focus = camera["focal"], camera["principal"], camera["focus"]

    def project(points):
        relative = points - position
        depth = -(relative @ back)
        safe = np.where(depth > 0.05, depth, np.nan)
        return focal * (relative @ right) / safe + px, -focal * (relative @ up) / safe + py, depth

    lift = np.zeros_like(centres)
    lift[:, 2] = heights / 2
    cu, cv_, depth = project(centres)
    tu, tv, _ = project(centres + lift)
    mirrored = centres * np.array([1.0, 1.0, -1.0])
    if s["floor"]:
        mirrored[:, 2] += 2 * s["floor"]
    mu, mv, mirror_depth = project(mirrored)
    nu, nv, _ = project(mirrored - lift)
    margin, (near_fade, per_m, low, high) = s["margin"], s["fade"]
    for index in np.argsort(-depth):                                 # 遠いものから置く
        if not depth[index] > s["near_clip"]:
            continue
        levels = sprites[int(kinds[index]) % len(sprites)]
        brightness = float(tones[index] * np.clip(near_fade - depth[index] / per_m, low, high) * s["gain"])
        sigma = float(min(abs(depth[index] - focus) * s["focus_blur"], s["max_blur"]) * unit + s["room_blur"] * room_sigma * unit)
        if (np.isfinite(cu[index]) and np.isfinite(tu[index]) and -margin < cu[index] < width + margin
                and -margin < cv_[index] < height + margin):
            paste(far if depth[index] > focus else near, levels, (cu[index], cv_[index]), (tu[index], tv[index]), brightness, sigma)
        if (mirror_depth[index] > s["near_clip"] and np.isfinite(mu[index]) and np.isfinite(nu[index])
                and -margin < mu[index] < width + margin and -margin < mv[index] < height + margin):
            shown = flipped(levels) if s["mirror_mode"] == "flip" else levels
            paste(mirror, shown, (mu[index], mv[index]), (nu[index], nv[index]), brightness, sigma + s["mirror_blur"] * unit)
    return far, near, mirror


def over_glow(image, layer, glow, glow_blur, unit=1.0):
    """小さな絵の層（前乗算）を image に重ね、σ glow_blur でぼかした色を glow 倍足す（光ってにじむ）。"""
    return image * (1 - layer[..., 3:4]) + layer[..., :3] + finish.blur(layer[..., :3], glow_blur, unit) * glow


# ---- 世界の切り替え・衝撃 ----------------------------------------------------------------

WORLD = {"sky": [0.012, 0.009, 0.018], "pool": [0.40, 0.15, 0.055], "halo": {"sigma": 110.0, "color": [0.10, 0.04, 0.015]}}


EFFECTS = {"gain": 0.9, "glow": 0.32, "blur": 1.0, "glow_blur": 12.0}    # 試作の値


def effects(frame, picture, gain, glow, settings, unit=1.0):
    """効果の要素（model3d の render.passes の fx。人物を黒で一緒に描いた、光として足す色）を、σ blur でぼかして gain 倍、さらに
    σ glow_blur でぼかして glow 倍、frame に足す（試作と同じ式）。"""
    soft = finish.blur(picture, settings["blur"], unit)
    return frame + soft * gain + finish.blur(soft, settings["glow_blur"], unit) * glow


def centroid(alpha):
    """アルファの重みの中心（画素）。人物が 50 画素ぶんより小さければ画面の中心。"""
    height, width = alpha.shape
    total = float(alpha.sum())
    if total <= 50:
        return width / 2, height / 2
    ys, xs = grid((width, height))
    return float((xs * alpha).sum() / total), float((ys * alpha).sum() / total)


def other_world(alpha, pool, settings, unit=1.0):
    """別の世界の背景: 空の色 + 床の光のたまり（要素 pool の明るさ × pool の色）+ 人物のまわりのほのかな明るみ（アルファを σ halo でぼかし、
    halo の色を掛ける）。"""
    s = settings
    halo = finish.blur(alpha, s["halo"]["sigma"], unit)[..., None]
    return (np.asarray(s["sky"], np.float32) + (pool if pool is not None else 0) * np.asarray(s["pool"], np.float32)
            + halo * np.asarray(s["halo"]["color"], np.float32))


def world(room, alpha, mix, pool, settings, burn_settings, unit=1.0):
    """背景を、部屋（room）から別の世界（other_world）へ切り替える。mix が 0 以下なら部屋のまま（部屋が無ければ黒）、1 以上か部屋が
    無ければ別の世界、その間は fx.burn の焼け抜け（進み mix。場は踊り手の重心 from から広がる）。room は表示の値の (H, W, 3)（無ければ None）。"""
    height, width = alpha.shape
    if mix <= 0.0:
        return room if room is not None else np.zeros((height, width, 3), np.float32)
    dark = other_world(alpha, pool, settings, unit)
    if room is None or mix >= 1.0:
        return dark * np.ones((height, width, 3), np.float32)
    from ..fx import burn as fxburn
    cx, cy = centroid(alpha)
    burn = {**burn_settings, "from": [cx / width, cy / height]}
    field = fxburn.field(burn, (width, height))
    before = room * np.float32(255.0)
    now = np.broadcast_to(dark, room.shape) * np.float32(255.0)
    return fxburn.composite(before, now, field, mix, burn) / np.float32(255.0)


def impact(frame, amount, zoom=0.05, steps=3):
    """衝撃: 画の中心のまわりに 1 + zoom × amount × k 倍（k = 1〜steps）に拡大した画（線形補間、端は鏡）を、元の画と平均する。"""
    if not amount > 0.01:
        return frame
    height, width = frame.shape[:2]
    total = frame.copy()
    for step in range(1, int(steps) + 1):
        scale = 1 + zoom * amount * step
        move = np.array([[scale, 0, (1 - scale) * width / 2], [0, scale, (1 - scale) * height / 2]], np.float64)
        total += cv2.warpAffine(frame, move, (width, height), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
    return total / (int(steps) + 1)


# ---- 手前の板と、柱が横切るつなぎ ------------------------------------------------------------------

def project_points(camera, points):
    """点（m。(n, 3)）を、カメラ（行列 matrix（m）・焦点距離 focal_px・主点 principal_px。その画面の画素）で写した (u, v, 奥行き)。"""
    matrix = np.asarray(camera["matrix"], np.float64)
    relative = np.asarray(points, np.float64) - matrix[:3, 3]
    depth = -(relative @ matrix[:3, 2])
    u = camera["focal_px"] * (relative @ matrix[:3, 0]) / depth + camera["principal_px"][0]
    v = -camera["focal_px"] * (relative @ matrix[:3, 1]) / depth + camera["principal_px"][1]
    return u, v, depth


def card_corners(camera, at, depth, width_px, anchor, image_size):
    """基準のカメラから見て、画面の画素 at の向き・奥行き depth（m）の所に、幅 width_px 画素ぶんの板（絵の縦横比）を置いた 4 隅（m。
    左上・右上・右下・左下）。anchor は板のどこを at に合わせるか（[0〜1, 0〜1]。絵の左上が [0, 0]）。板は向きを基準のカメラの面に
    そろえて、世界に固定する（試作と同じ式）。"""
    matrix = np.asarray(camera["matrix"], np.float64)
    right, up, forward, position = matrix[:3, 0], matrix[:3, 1], -matrix[:3, 2], matrix[:3, 3]
    f, (px, py) = float(camera["focal_px"]), camera["principal_px"]
    image_w, image_h = image_size
    wide = width_px * depth / f
    high = wide * image_h / image_w
    centre = position + depth * (forward + (at[0] - px) / f * right - (at[1] - py) / f * up)
    return np.array([centre + (u / image_w - anchor[0]) * wide * right - (v / image_h - anchor[1]) * high * up
                     for u, v in ((0, 0), (image_w, 0), (image_w, image_h), (0, image_h))])


def read_rgba(path):
    """RGBA の PNG（8 か 16 ビット。アルファは前乗算でない）→ (色 (H, W, 3), 不透明度 (H, W)) の float32（0〜1）。"""
    from pathlib import Path
    image = cv2.imdecode(np.frombuffer(Path(path).read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.ndim != 3 or image.shape[2] != 4:
        raise ValueError(f"RGBA の PNG にしてください: {path}")
    top = 65535.0 if image.dtype == np.uint16 else 255.0
    rgba = image[..., [2, 1, 0, 3]].astype(np.float32) / np.float32(top)
    return np.ascontiguousarray(rgba[..., :3]), np.ascontiguousarray(rgba[..., 3])


def wave(rgb, alpha, amount, phase, wavelength):
    """板を列ごとに上下へゆらす（shift = amount × sin(phase + 列 ÷ wavelength)。線形補間、端は端の画素。試作の客の頭の影の揺れ）。"""
    height, width = alpha.shape
    xs = np.arange(width, dtype=np.float32)
    shift = amount * np.sin(phase + xs / wavelength)
    map_x = np.tile(xs, (height, 1))
    map_y = (np.arange(height, dtype=np.float32)[:, None] - shift[None, :]).astype(np.float32)
    return (cv2.remap(rgb, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE),
            cv2.remap(alpha, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE))


def card(frame, rgb, alpha, corners, camera, sigma, gain, size, unit=1.0):
    """板を 1 枚重ねる: 4 隅をカメラで写して、前乗算の色と不透明度を射影で写し、σ でぼかして over（色は gain 倍）。板がカメラの後ろに
    回った（奥行き 0.05 m 未満）か、画面から大きく外れたら重ねない。カメラの画素は画面の大きさ size に合わせる。"""
    width, height = size
    u, v, depth = project_points(camera, corners)
    if np.any(depth < 0.05):
        return frame
    scale = width / float(camera["resolution"][0])
    target = (np.stack([u, v], 1) * scale).astype(np.float32)
    if target[:, 0].max() < -width or target[:, 0].min() > 2 * width or target[:, 1].max() < -height or target[:, 1].min() > 2 * height:
        return frame
    image_h, image_w = alpha.shape
    source = np.array([[0, 0], [image_w, 0], [image_w, image_h], [0, image_h]], np.float32)
    transform = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(rgb * alpha[..., None], transform, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)
    cover = cv2.warpPerspective(alpha, transform, (width, height), flags=cv2.INTER_LINEAR, borderValue=0)
    warped, cover = finish.blur(warped, sigma, 1.0), finish.blur(cover, sigma, 1.0)
    return frame * (1 - cover[..., None]) + warped * gain


PILLAR = {"width_px": 760.0, "extra_px": 40.0, "blur": 5.0, "gain": 0.62, "motion": 0.5, "ease": 0.15, "edge_px": 40.0}


def pillar_image(rgb, alpha, size, settings, seconds, fps, unit=1.0):
    """柱の絵（前乗算の色, 不透明度, 幅）: 柱の絵（上から使う）を、幅 width_px × unit・高さ（画面の高さ + extra_px × unit）に縦横比のまま
    引き伸ばし（3 次の補間）、σ blur でぼかして色を gain 倍、画面を seconds 秒で横切る速さの motion コマぶんの動きぶれを横に付ける
    （試作と同じ作り）。"""
    width, height = size
    s = settings
    wide = int(round(s["width_px"] * unit))
    rows = int(math.ceil((height + s["extra_px"] * unit) / (wide / rgb.shape[1])))
    target = (wide, int(round(height + s["extra_px"] * unit)))
    image = cv2.resize(rgb[:rows] * alpha[:rows, :, None], target, interpolation=cv2.INTER_CUBIC)
    mask = cv2.resize(alpha[:rows], target, interpolation=cv2.INTER_CUBIC)
    image = cv2.GaussianBlur(image, (0, 0), s["blur"] * unit) * s["gain"]
    mask = cv2.GaussianBlur(mask, (0, 0), s["blur"] * unit)
    streak = max(1, int((width + wide) / seconds / fps * s["motion"]))
    return cv2.blur(image, (streak, 1)), cv2.blur(mask, (streak, 1)), wide


def pillar(image, mask, wide, progress, direction, size, settings, unit=1.0):
    """柱が横切る位置の (前乗算の色, 不透明度, 柱の中心の x)。direction −1 は右から左、+1 は左から右（絵を左右に返す）。
    進んだ割合 = ease × smoothstep(progress) + (1 − ease) × progress。柱は extra_px ÷ 2 × unit だけ上へずらして置く。"""
    width, height = size
    s = settings
    if direction > 0:
        image, mask = image[:, ::-1], mask[:, ::-1]
    p = min(max(progress, 0.0), 1.0)
    travelled = s["ease"] * (p * p * (3 - 2 * p)) + (1 - s["ease"]) * progress
    centre = (width + wide / 2) - (width + wide) * travelled if direction < 0 else (-wide / 2) + (width + wide) * travelled
    move = np.array([[1, 0, centre - wide / 2], [0, 1, -s["extra_px"] / 2 * unit]], np.float64)
    return (cv2.warpAffine(np.ascontiguousarray(image), move, (width, height), borderValue=0),
            np.clip(cv2.warpAffine(np.ascontiguousarray(mask), move, (width, height), borderValue=0), 0, 1), centre)


def wipe(old, new, post, mask, centre, direction, settings, unit=1.0):
    """柱が通り過ぎた側を新しい画にし（境目は edge_px でなだらか）、柱を重ねる。"""
    width = old.shape[1]
    xs = np.arange(width, dtype=np.float32)
    side = np.clip(-direction * (xs - centre) / (settings["edge_px"] * unit) + 0.5, 0, 1)[None, :, None]
    frame = old * (1 - side) + new * side
    return frame * (1 - mask[..., None]) + post
