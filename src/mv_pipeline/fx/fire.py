"""火の演出の部品（炎の舌・小さい炎・火の粉・刃の火）。fx-timeline の hooks から使う（実験的。docs/fx.md）。

どれも**画面の座標**（出力の画素）と**表示の値**（float32 の 0..255、ガンマ符号化）の上で計算し、画 f を書き換えて返す
（f は float32 (H, W, 3)。hooks の shot に渡る画）。重ね方はスクリーン合成で、明るい所が白に飛びにくい。
実制作の演出の部品と同じ式・同じ順・同じ型で書いてあり、同じ入力なら同じ画になる。

- どの関数も時刻 t と引数だけで決まる（状態を持たない。区間を分けて描いても、静止画でも同じ画）。乱数は seed から作る。
- 位置・大きさは画面の画素で渡す。素材の座標からの写し方は、hooks の view（fx/view.py。実制作の Geo に当たる）が持つ。
- むら（tex）は粒の大きさごとに作り置く（1 枚 1 MB。160 枚を超えたら作り直す）。

    flame_field(src, t, hpx, seed)   炎の出どころ src（0..1 の配列）から立ち上がる炎の場 F（0..1）
    add_fire(f, x0, y0, F)           F を炎の色で f[y0:, x0:] に重ねる（光 glow も）
    candle(f, X, Y, t, size, seed)   (X, Y) を根元にした小さな炎（芯の火）
    embers(f, at, t, seed, n, cycle) 火の粉（at(u) が生まれる場所）
    blade_fire(f, blade, t, P)       刃に沿って燃える火（Blade は画面の点列と幅）
    blade_candle(f, blade, t, P)     刃の上の小さな炎
"""
import math
from dataclasses import dataclass

import cv2
import numpy as np

_cache = {}
TEX_LIMIT = 160            # 作り置くむらの枚数の上限（超えたら作り直す）


# ---- 小道具 -------------------------------------------------------------------------------

def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def lerp_keys(keys, t):
    """keys = [[t, v], ...]（v は数か数の並び）を t で直線補間する。範囲の外は端の値。"""
    if not keys:
        return None
    if t <= keys[0][0]:
        return keys[0][1]
    if t >= keys[-1][0]:
        return keys[-1][1]
    for (t0, v0), (t1, v1) in zip(keys[:-1], keys[1:]):
        if t0 <= t <= t1:
            a = (t - t0) / max(1e-6, t1 - t0)
            if isinstance(v0, (list, tuple)):
                return [x0 + (x1 - x0) * a for x0, x1 in zip(v0, v1)]
            return v0 + (v1 - v0) * a
    return keys[-1][1]


def params_at(entry, t):
    """entry の値に、entry["keys"]（名前 → [[t, v], ...]）を時刻 t で補間した値を上書きした辞書。"""
    params = dict(entry)
    for name, keys in entry.get("keys", {}).items():
        params[name] = lerp_keys(keys, t)
    return params


def screen(base, add):
    """スクリーン合成（0..255）。"""
    return 255.0 - (255.0 - base) * (255.0 - np.clip(add, 0, 255)) / 255.0


def blur(a, sigma):
    """ガウスぼかし。大きいぼかしは縮めてから掛けて戻す（速さのため）。"""
    if sigma <= 6:
        return cv2.GaussianBlur(a, (0, 0), max(0.3, sigma))
    q = 4 if sigma > 16 else 2
    h, w = a.shape[:2]
    small = cv2.resize(a, (max(1, w // q), max(1, h // q)), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), sigma / q)
    return cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR)


def _q(v):
    """むらの粒の大きさを 1/6 オクターブ刻みに丸める（寄りで毎コマ大きさが変わっても、作り置きが増え続けないように）。"""
    return float(2.0 ** (round(math.log2(max(float(v), 0.5)) * 6) / 6.0))


def tex(seed, n=512, sx=24.0, sy=24.0):
    """端が反対側とつながるなめらかなむら（n×n、0..1）。sx・sy は粒の大きさ（画素）。"""
    sx, sy = _q(sx), _q(sy)
    key = ("tex", seed, n, sx, sy)
    if key in _cache:
        return _cache[key]
    if sum(1 for k in _cache if k[0] == "tex") > TEX_LIMIT:
        for k in [k for k in _cache if k[0] == "tex"]:
            del _cache[k]
    rng = np.random.default_rng(seed)
    white = rng.standard_normal((n, n)).astype(np.float32)
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.fftfreq(n)[None, :]
    filt = np.exp(-0.5 * ((fx * sx * 2 * np.pi) ** 2 + (fy * sy * 2 * np.pi) ** 2))
    v = np.real(np.fft.ifft2(np.fft.fft2(white) * filt)).astype(np.float32)
    v = (v - v.mean()) / (v.std() + 1e-6)
    v = np.clip(0.5 + 0.18 * v, 0, 1)
    _cache[key] = v
    return v


def tex_win(texture, y0, x0, h, w):
    """つながったむらから (y0, x0) 起点の h×w を切り出す（はみ出しは反対側から）。"""
    n0, n1 = texture.shape
    rows = (np.arange(h) + int(y0)) % n0
    cols = (np.arange(w) + int(x0)) % n1
    return texture[np.ix_(rows, cols)]


def ramp_fire(x, white=0.0):
    """炎の色（0..1 → 暗い赤 → 赤 → 橙 → 黄 → 白）。white で白炎（芯が広く白い、縁は淡い橙）へ寄せる。"""
    stops = np.array([0.0, 0.18, 0.42, 0.66, 0.86, 1.0], np.float32)
    c0 = np.array([[50, 6, 0], [165, 32, 4], [240, 98, 16], [255, 168, 58], [255, 222, 138], [255, 244, 212]], np.float32)
    c1 = np.array([[70, 20, 10], [210, 80, 40], [255, 150, 90], [255, 214, 170], [255, 240, 222], [255, 252, 246]], np.float32)
    cols = c0 + (c1 - c0) * float(white)
    out = np.empty(x.shape + (3,), np.float32)
    for c in range(3):
        out[..., c] = np.interp(x, stops, cols[:, c])
    return out


def smear_up(src, lam):
    """下にある光を上へ引き伸ばす（指数の尾）。dst(y) = src(y) + r·dst(y+1) を下から計算する。"""
    r = math.exp(-1.0 / max(lam, 1e-3))
    out = np.empty_like(src)
    acc = np.zeros(src.shape[1], np.float32)
    for y in range(src.shape[0] - 1, -1, -1):
        acc = src[y] + r * acc
        out[y] = acc
    return out * (1 - r)


def roi_of(pts, pad, width, height, up=0):
    """点列を pad（上は pad + up）広げた画面の中の矩形 (x0, y0, x1, y1)。4 画素より狭ければ None。"""
    x0 = int(max(0, np.floor(pts[:, 0].min() - pad)))
    x1 = int(min(width, np.ceil(pts[:, 0].max() + pad)))
    y0 = int(max(0, np.floor(pts[:, 1].min() - pad - up)))
    y1 = int(min(height, np.ceil(pts[:, 1].max() + pad)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1, y1


# ---- 炎の場（立ち上がる炎の舌） -----------------------------------------------------------------

def flame_field(src, t, hpx, seed, rise=1.0, wob=1.0, gain=1.8, thr=0.30, gam=0.85, detail=1.0, src_px=6.0, contrast=1.7, base_mod=0.8):
    """src: 炎の出どころ（0..1）。hpx: 炎の高さ（画素）。戻り値 F（0..1）。
    出どころにもむらを掛けて、炎の舌が所々から立つようにする（帯のまま立てると白い壁になる）。"""
    h, w = src.shape
    sp = max(4.0, hpx * 0.9) * rise                       # 上へ流れる速さ（画素/秒）
    if base_mod > 0:
        tb = tex(seed + 21, 512, sx=max(3.0, hpx * 0.22), sy=max(3.0, hpx * 0.22))
        mb = tex_win(tb, t * sp * 0.25, seed * 17 + t * sp * 0.15, h, w)
        src = src * np.clip(1 + base_mod * ((mb - 0.5) * 3.2), 0.08, 1.6)
    lam = max(2.0, 0.40 * hpx)
    d = smear_up(src, lam)
    d = d / max(1e-3, 1 - math.exp(-max(2.0, src_px) / lam))     # 出どころのすぐ上で 1 前後になるように
    tw = tex(seed + 11, 512, sx=max(6.0, hpx * 0.45), sy=max(6.0, hpx * 0.6))   # 横の揺れ（上へ流れるむらで横にずらす）
    dx = (tex_win(tw, t * sp * 0.6, seed * 37, h, w) - 0.5) * 2.0 * (0.20 * hpx) * wob
    yy = np.arange(h, dtype=np.float32)[:, None]
    mx = (np.arange(w, dtype=np.float32)[None, :] + dx).astype(np.float32)
    my = np.repeat(yy, w, 1).astype(np.float32)
    d = cv2.remap(d.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    t1 = tex(seed + 1, 512, sx=max(2.5, hpx * 0.11), sy=max(5.0, hpx * 0.38))    # 舌の形（縦長のむらが上へ流れる）
    t2 = tex(seed + 2, 512, sx=max(1.5, hpx * 0.05), sy=max(3.0, hpx * 0.14))
    tongue = 0.62 * tex_win(t1, t * sp, seed * 13, h, w) + 0.38 * (tex_win(t2, t * sp * 1.4, seed * 29, h, w) * detail + 0.5 * (1 - detail))
    tongue = np.clip((tongue - 0.5) * contrast * 2.2 + 0.5, 0, 1)
    return np.clip(d * gain * (0.05 + 1.25 * tongue) - thr, 0, 1) ** gam


def add_fire(f, x0, y0, field, white=0.0, amt=1.0, glow=None, glow_amt=0.0, glow_col=(255, 120, 30)):
    """field を炎の色で f[y0:, x0:] にスクリーンで重ねる。glow があればその光も先に重ねる。"""
    h, w = field.shape
    sub = f[y0:y0 + h, x0:x0 + w]
    if glow is not None and glow_amt > 0:
        sub[:] = screen(sub, np.array(glow_col, np.float32)[None, None] * np.clip(glow * glow_amt, 0, 1)[..., None])
    fa = np.clip(field * amt, 0, 1)
    sub[:] = screen(sub, ramp_fire(fa, white) * np.clip(fa * 1.35, 0, 1)[..., None])
    return f


# ---- 小さい炎（指一本の幅・芯の火） ------------------------------------------------------------

def candle(f, X, Y, t, size, seed, amt=1.0, white=0.0, sway=0.0, lean=0.0, glow_amt=0.5, flick=1.0):
    """(X, Y) を根元にした小さな炎。size: 炎の高さ（画素）。sway: 横揺れの量（高さ比）。lean: 横の傾き（高さ比、+ で右）。"""
    if amt <= 0.01 or size < 1.5:
        return f
    H, W = f.shape[:2]
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0, 6.28, 4)
    fl = 1 + 0.10 * flick * math.sin(t * 13.1 + ph[0]) + 0.07 * flick * math.sin(t * 23.7 + ph[1]) + 0.05 * flick * math.sin(t * 5.3 + ph[2])
    hgt = size * fl * (0.6 + 0.4 * amt)
    wid = hgt * 0.36
    pad = int(hgt * 1.6 + 6)
    x0, y0 = int(X - pad), int(Y - hgt * 1.5 - pad)
    x1, y1 = int(X + pad), int(Y + pad * 0.6)
    x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
    if x1c - x0c < 3 or y1c - y0c < 3:
        return f
    yy, xx = np.mgrid[y0c:y1c, x0c:x1c].astype(np.float32)
    v = (Y - yy) / max(hgt, 1e-3)                                   # 0 = 根元、1 = 先
    swing = sway * (math.sin(t * 7.7 + ph[3]) * 0.6 + math.sin(t * 3.1 + ph[1]) * 0.4)
    cx = X + hgt * (lean + swing) * np.clip(v, 0, 1.4) ** 1.6
    prof = np.where(v < 0, np.sqrt(np.clip(1 - (v / 0.28) ** 2, 0, 1)), np.clip(np.sin(np.pi * np.clip(v, 0, 1) ** 0.55), 0, 1))
    hw = wid * 0.5 * np.clip(prof, 0.02, 1)
    dxn = (xx - cx) / np.maximum(hw, 0.5)
    body = np.clip(1 - dxn ** 2, 0, 1) * (v > -0.28) * (v < 1.05)
    body = cv2.GaussianBlur(body, (0, 0), max(0.6, hgt * 0.035))
    core = np.clip(1 - (dxn / 0.55) ** 2, 0, 1) * np.clip(1 - np.abs(v - 0.25) / 0.45, 0, 1)
    core = cv2.GaussianBlur(core, (0, 0), max(0.5, hgt * 0.03))
    fv = np.clip(body * (0.55 + 0.45 * np.clip(1 - v, 0, 1)) * amt + core * 0.55 * amt, 0, 1)
    sub = f[y0c:y1c, x0c:x1c]
    g = np.exp(-(((xx - X) / (hgt * 0.9)) ** 2 + ((yy - (Y - hgt * 0.45)) / (hgt * 1.1)) ** 2)) * glow_amt * amt * fl
    sub[:] = screen(sub, np.array([255, 125, 35], np.float32)[None, None] * np.clip(g, 0, 1)[..., None])
    sub[:] = screen(sub, ramp_fire(fv, white) * np.clip(fv * 1.4, 0, 1)[..., None])
    return f


# ---- 火の粉 ------------------------------------------------------------------------------

def embers(f, at, t, seed, n, cycle, life=(0.6, 1.4), vy=(60, 160), vx=(-25, 25), size=(1.0, 2.2), k=1.0, amt=1.0,
           col=(255, 200, 120), glow_col=(255, 100, 20), wind=0.0, acc=30.0):
    """at(u) → 生まれる場所 (X, Y)（u は 0..1）。速さ・大きさは画素（k 倍）。粒は周期 cycle 秒で生まれ直す。t だけで決まる。"""
    if amt <= 0.01 or n <= 0:
        return f
    H, W = f.shape[:2]
    rng = np.random.default_rng(seed)
    us = rng.random(n)
    ph = rng.random(n) * cycle
    lf = rng.uniform(*life, n)
    vys = rng.uniform(*vy, n) * k
    vxs = rng.uniform(*vx, n) * k
    rs = rng.uniform(*size, n) * max(0.6, k)
    wp = rng.uniform(0, 6.28, n)
    wa = rng.uniform(4, 14, n) * k
    points = []
    for i in range(n):
        age = (t + ph[i]) % cycle
        if age > lf[i]:
            continue
        cycle_i = math.floor((t + ph[i]) / cycle)
        u = (us[i] + 0.6180339 * cycle_i) % 1.0
        X0, Y0 = at(u)
        X = X0 + vxs[i] * age + wind * k * age * age * 20 + wa[i] * math.sin(wp[i] + age * 6.0)
        Y = Y0 - vys[i] * age - acc * k * age * age
        if not (-5 <= X < W + 5 and -5 <= Y < H + 5):
            continue
        brightness = (1 - age / lf[i]) ** 1.2 * (0.6 + 0.4 * math.sin(age * 25 + wp[i]) ** 2)
        points.append((X, Y, rs[i], brightness))
    if not points:
        return f
    A = np.array(points, np.float32)
    g = max(1.0, 3 * k)
    pad = int(g * 3 + A[:, 2].max() + 3)
    x0, x1 = int(max(0, A[:, 0].min() - pad)), int(min(W, A[:, 0].max() + pad))
    y0, y1 = int(max(0, A[:, 1].min() - pad)), int(min(H, A[:, 1].max() + pad))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return f
    sparks = np.zeros((y1 - y0, x1 - x0), np.float32)
    for X, Y, r, brightness in points:
        cv2.circle(sparks, (int((X - x0) * 4), int((Y - y0) * 4)), max(1, int(r * 4)), float(brightness), -1, lineType=cv2.LINE_AA, shift=2)
    sub = f[y0:y1, x0:x1]
    sub[:] = screen(sub, np.array(col, np.float32) * np.clip(sparks * 1.2 * amt, 0, 1)[..., None])
    sub[:] = screen(sub, np.array(glow_col, np.float32) * np.clip(blur(sparks, g) * 2.5 * amt, 0, 1)[..., None])
    return f


# ---- 刃の火 ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Blade:
    """画面の上の刃。points は根元 → 切っ先の点列 (n, 2)（画素）、s は 0..1 の位置、widths は幅（画素）、
    k は素材の長さ 1 が画面の何画素か（炎の高さ h と火の粉の速さに掛ける）。view.blade(...) が作る。"""
    points: np.ndarray
    s: np.ndarray
    widths: np.ndarray
    k: float


def coverage(s, cov, feather=0.05):
    """刃の上の燃える範囲 cov = [a, b]（0..1）の重み。端は feather でなじませる。"""
    a, b = cov
    return np.clip((s - a) / feather + 0.5, 0, 1) * np.clip((b - s) / feather + 0.5, 0, 1)


def blade_fire(f, blade, t, P):
    """刃に沿って燃える火。P: amt・h（炎の高さ。素材の長さ）・cov・white・heat・patch・seed・embers・glow・glow_col・hot・
    rise・wob・gain・thr・gam・feather。"""
    amt = float(P.get("amt", 1.0))
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    S, s, w = blade.points, blade.s, blade.widths
    cov = P.get("cov", [0.0, 1.0])
    c = coverage(s, cov, P.get("feather", 0.06))
    if c.max() <= 0.01:
        return f
    hpx = float(P.get("h", 60)) * blade.k
    seed = int(P.get("seed", 3))
    roi = roi_of(S, pad=w.max() + hpx * 0.5 + 8, width=W, height=H, up=hpx * 1.7)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    h, ww = y1 - y0, x1 - x0
    src = np.zeros((h, ww), np.float32)
    core = np.zeros((h, ww), np.float32)
    for i in range(len(S) - 1):
        ci = 0.5 * (c[i] + c[i + 1])
        if ci <= 0.01:
            continue
        p0 = (int((S[i, 0] - x0) * 4), int((S[i, 1] - y0) * 4))
        p1 = (int((S[i + 1, 0] - x0) * 4), int((S[i + 1, 1] - y0) * 4))
        th = max(1, int(round(0.5 * (w[i] + w[i + 1]))))
        cv2.line(src, p0, p1, float(ci), th, cv2.LINE_AA, shift=2)
        cv2.line(core, p0, p1, float(ci), max(1, int(th * 0.7)), cv2.LINE_AA, shift=2)
    src = blur(src, max(0.8, w.mean() * 0.35))
    patch = float(P.get("patch", 0.0))                  # むら（燃えている所と弱い所）: 刃に沿って流れる
    if patch > 0:
        tp = tex(seed + 5, 512, sx=max(4.0, hpx * 0.5), sy=max(4.0, hpx * 0.5))
        m = tex_win(tp, t * hpx * 0.8, seed * 7 + t * hpx * 0.3, h, ww)
        src = src * np.clip((m - patch * 0.55) / 0.2 + 0.25, 0, 1.2)
    src *= amt
    field = flame_field(src, t, hpx, seed, rise=P.get("rise", 1.0), wob=P.get("wob", 1.0), gain=P.get("gain", 1.8),
                        thr=P.get("thr", 0.30), gam=P.get("gam", 0.8), src_px=float(w.mean()))
    glow = blur(src, max(2.0, hpx * 0.55)) * 3.0
    fl = 1 + 0.06 * math.sin(t * 17.0 + seed) + 0.04 * math.sin(t * 29.0 + 2 * seed)
    f = add_fire(f, x0, y0, field * fl, white=P.get("white", 0.0), amt=1.0, glow=glow, glow_amt=P.get("glow", 0.5) * amt,
                 glow_col=P.get("glow_col", (255, 120, 30)))
    heat = float(P.get("heat", 0.0)) * amt              # 刃が熱を帯びて白く光る
    if heat > 0.01:
        cm = cv2.GaussianBlur(core, (0, 0), 0.8) * heat * fl
        hot = np.array(P.get("hot", [255, 236, 214]), np.float32)
        sub = f[y0:y1, x0:x1]
        sub[:] = sub * (1 - cm[..., None] * 0.75) + hot[None, None] * (cm[..., None] * 0.75)
    count = int(P.get("embers", 0))                     # 火の粉
    if count > 0:
        ca, cb = cov

        def at(u):
            q = ca + (cb - ca) * u
            i = q * (len(S) - 1)
            i0 = int(min(i, len(S) - 2))
            a = i - i0
            return S[i0, 0] * (1 - a) + S[i0 + 1, 0] * a, S[i0, 1] * (1 - a) + S[i0 + 1, 1] * a - w[i0] * 0.5

        f = embers(f, at, t, seed + 99, count, 1.6, k=blade.k * 1.4, amt=amt, vy=(50, 140), size=(0.8, 1.8))
    return f


def blade_candle(f, blade, t, P):
    """刃の上の小さな炎（指一本の幅）。P: at（刃の上の位置。0 = 根元、1 = 切っ先）・h（高さ。素材の長さ）・amt・white・sway・
    lean・glow・seed・heat・kick（[時刻, 量, 長さ]。強拍で一度大きく横へ揺れる）。"""
    amt = float(P.get("amt", 1.0))
    if amt <= 0.01:
        return f
    S, w = blade.points, blade.widths
    q = float(P.get("at", 0.8))
    i = q * (len(S) - 1)
    i0 = int(min(i, len(S) - 2))
    a = i - i0
    X = S[i0, 0] * (1 - a) + S[i0 + 1, 0] * a
    Y = S[i0, 1] * (1 - a) + S[i0 + 1, 1] * a - (w[i0] * 0.35)
    size = float(P.get("h", 40)) * blade.k
    sway, lean = float(P.get("sway", 0.12)), float(P.get("lean", 0.0))
    if P.get("kick"):                                   # 拍の直後に大きく倒れ、小さく戻る
        tk, amp, dur = P["kick"]
        if tk <= t < tk + dur:
            x = (t - tk) / dur
            lean += amp * math.exp(-4.0 * x) * math.sin(2.2 * math.pi * x)
    f = candle(f, X, Y, t, size, int(P.get("seed", 5)), amt=amt, white=P.get("white", 0.15), sway=sway, lean=lean,
               glow_amt=P.get("glow", 0.55))
    heat = float(P.get("heat", 0.4)) * amt
    if heat > 0.01:                                     # 炎の根元の刃が少し熱を帯びる
        r = size * 0.6
        x0, y0 = int(max(0, X - r)), int(max(0, Y - r * 0.6))
        x1, y1 = int(min(f.shape[1], X + r)), int(min(f.shape[0], Y + r * 0.6))
        if x1 > x0 and y1 > y0:
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            g = np.exp(-(((xx - X) / (r * 0.6)) ** 2 + ((yy - Y) / (r * 0.35)) ** 2)) * heat
            f[y0:y1, x0:x1] = screen(f[y0:y1, x0:x1], np.array([255, 170, 90], np.float32) * g[..., None])
    return f
