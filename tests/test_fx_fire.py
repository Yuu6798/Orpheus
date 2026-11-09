"""火の演出の部品（fx/fire.py）・ショットの見え方（fx/view.py）・fx-timeline の hooks（fx/hooks.py）。

部品は実制作の演出の部品の関数を写したもの（ref_*）と、同じ入力で画がバイト単位で同じかを比べる。
"""
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import fx_synth  # noqa: E402
from mv_pipeline.fx import camera, fire, sequence  # noqa: E402
from mv_pipeline.fx.hooks import Hooks  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

# ---- 実制作の演出の部品（写し） --------------------------------------------------------------------
_ref_cache = {}


def ref_screen(base, add):
    return 255.0 - (255.0 - base) * (255.0 - np.clip(add, 0, 255)) / 255.0


def ref_blur(a, sigma):
    if sigma <= 6:
        return cv2.GaussianBlur(a, (0, 0), max(0.3, sigma))
    q = 4 if sigma > 16 else 2
    h, w = a.shape[:2]
    sm = cv2.resize(a, (max(1, w // q), max(1, h // q)), interpolation=cv2.INTER_AREA)
    sm = cv2.GaussianBlur(sm, (0, 0), sigma / q)
    return cv2.resize(sm, (w, h), interpolation=cv2.INTER_LINEAR)


def ref_q(v):
    return float(2.0 ** (round(math.log2(max(float(v), 0.5)) * 6) / 6.0))


def ref_tex(seed, n=512, sx=24.0, sy=24.0):
    sx, sy = ref_q(sx), ref_q(sy)
    key = ('tex', seed, n, sx, sy)
    if key in _ref_cache:
        return _ref_cache[key]
    rng = np.random.default_rng(seed)
    w = rng.standard_normal((n, n)).astype(np.float32)
    fy = np.fft.fftfreq(n)[:, None]
    fx = np.fft.fftfreq(n)[None, :]
    filt = np.exp(-0.5 * ((fx * sx * 2 * np.pi) ** 2 + (fy * sy * 2 * np.pi) ** 2))
    v = np.real(np.fft.ifft2(np.fft.fft2(w) * filt)).astype(np.float32)
    v = (v - v.mean()) / (v.std() + 1e-6)
    v = np.clip(0.5 + 0.18 * v, 0, 1)
    _ref_cache[key] = v
    return v


def ref_tex_win(T, y0, x0, h, w):
    n0, n1 = T.shape
    rows = (np.arange(h) + int(y0)) % n0
    cols = (np.arange(w) + int(x0)) % n1
    return T[np.ix_(rows, cols)]


def ref_ramp_fire(x, white=0.0):
    stops = np.array([0.0, 0.18, 0.42, 0.66, 0.86, 1.0], np.float32)
    c0 = np.array([[50, 6, 0], [165, 32, 4], [240, 98, 16], [255, 168, 58], [255, 222, 138], [255, 244, 212]], np.float32)
    c1 = np.array([[70, 20, 10], [210, 80, 40], [255, 150, 90], [255, 214, 170], [255, 240, 222], [255, 252, 246]], np.float32)
    cols = c0 + (c1 - c0) * float(white)
    out = np.empty(x.shape + (3,), np.float32)
    for c in range(3):
        out[..., c] = np.interp(x, stops, cols[:, c])
    return out


def ref_smear_up(src, lam):
    r = math.exp(-1.0 / max(lam, 1e-3))
    out = np.empty_like(src)
    acc = np.zeros(src.shape[1], np.float32)
    for y in range(src.shape[0] - 1, -1, -1):
        acc = src[y] + r * acc
        out[y] = acc
    return out * (1 - r)


def ref_flame_field(src, t, hpx, seed, rise=1.0, wob=1.0, gain=1.8, thr=0.30, gam=0.85, detail=1.0, src_px=6.0, contrast=1.7, base_mod=0.8):
    h, w = src.shape
    sp = max(4.0, hpx * 0.9) * rise
    if base_mod > 0:
        Tb = ref_tex(seed + 21, 512, sx=max(3.0, hpx * 0.22), sy=max(3.0, hpx * 0.22))
        mb = ref_tex_win(Tb, t * sp * 0.25, seed * 17 + t * sp * 0.15, h, w)
        src = src * np.clip(1 + base_mod * ((mb - 0.5) * 3.2), 0.08, 1.6)
    lam = max(2.0, 0.40 * hpx)
    D = ref_smear_up(src, lam)
    D = D / max(1e-3, 1 - math.exp(-max(2.0, src_px) / lam))
    Tw = ref_tex(seed + 11, 512, sx=max(6.0, hpx * 0.45), sy=max(6.0, hpx * 0.6))
    dx = (ref_tex_win(Tw, t * sp * 0.6, seed * 37, h, w) - 0.5) * 2.0 * (0.20 * hpx) * wob
    yy = np.arange(h, dtype=np.float32)[:, None]
    mx = (np.arange(w, dtype=np.float32)[None, :] + dx).astype(np.float32)
    my = np.repeat(yy, w, 1).astype(np.float32)
    D = cv2.remap(D.astype(np.float32), mx, my, cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
    T1 = ref_tex(seed + 1, 512, sx=max(2.5, hpx * 0.11), sy=max(5.0, hpx * 0.38))
    T2 = ref_tex(seed + 2, 512, sx=max(1.5, hpx * 0.05), sy=max(3.0, hpx * 0.14))
    T = 0.62 * ref_tex_win(T1, t * sp, seed * 13, h, w) + 0.38 * (ref_tex_win(T2, t * sp * 1.4, seed * 29, h, w) * detail + 0.5 * (1 - detail))
    T = np.clip((T - 0.5) * contrast * 2.2 + 0.5, 0, 1)
    return np.clip(D * gain * (0.05 + 1.25 * T) - thr, 0, 1) ** gam


def ref_add_fire(f, x0, y0, F, white=0.0, amt=1.0, glow=None, glow_amt=0.0, glow_col=(255, 120, 30)):
    h, w = F.shape
    sub = f[y0:y0 + h, x0:x0 + w]
    if glow is not None and glow_amt > 0:
        sub[:] = ref_screen(sub, np.array(glow_col, np.float32)[None, None] * np.clip(glow * glow_amt, 0, 1)[..., None])
    Fa = np.clip(F * amt, 0, 1)
    sub[:] = ref_screen(sub, ref_ramp_fire(Fa, white) * np.clip(Fa * 1.35, 0, 1)[..., None])
    return f


def ref_candle(f, X, Y, t, size, seed, amt=1.0, white=0.0, sway=0.0, lean=0.0, glow_amt=0.5, flick=1.0):
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
    v = (Y - yy) / max(hgt, 1e-3)
    sw_ = sway * (math.sin(t * 7.7 + ph[3]) * 0.6 + math.sin(t * 3.1 + ph[1]) * 0.4)
    cx = X + hgt * (lean + sw_) * np.clip(v, 0, 1.4) ** 1.6
    prof = np.where(v < 0, np.sqrt(np.clip(1 - (v / 0.28) ** 2, 0, 1)), np.clip(np.sin(np.pi * np.clip(v, 0, 1) ** 0.55), 0, 1))
    hw = wid * 0.5 * np.clip(prof, 0.02, 1)
    dxn = (xx - cx) / np.maximum(hw, 0.5)
    body = np.clip(1 - dxn ** 2, 0, 1) * (v > -0.28) * (v < 1.05)
    body = cv2.GaussianBlur(body, (0, 0), max(0.6, hgt * 0.035))
    core = np.clip(1 - (dxn / 0.55) ** 2, 0, 1) * np.clip(1 - np.abs(v - 0.25) / 0.45, 0, 1)
    core = cv2.GaussianBlur(core, (0, 0), max(0.5, hgt * 0.03))
    Fv = np.clip(body * (0.55 + 0.45 * np.clip(1 - v, 0, 1)) * amt + core * 0.55 * amt, 0, 1)
    sub = f[y0c:y1c, x0c:x1c]
    g = np.exp(-(((xx - X) / (hgt * 0.9)) ** 2 + ((yy - (Y - hgt * 0.45)) / (hgt * 1.1)) ** 2)) * glow_amt * amt * fl
    sub[:] = ref_screen(sub, np.array([255, 125, 35], np.float32)[None, None] * np.clip(g, 0, 1)[..., None])
    sub[:] = ref_screen(sub, ref_ramp_fire(Fv, white) * np.clip(Fv * 1.4, 0, 1)[..., None])
    return f


def ref_embers(f, pts_fn, t, seed, n, rate_win, life=(0.6, 1.4), vy=(60, 160), vx=(-25, 25), size=(1.0, 2.2), k=1.0, amt=1.0,
               col=(255, 200, 120), glow_col=(255, 100, 20), wind=0.0, acc=30.0):
    if amt <= 0.01 or n <= 0:
        return f
    H, W = f.shape[:2]
    rng = np.random.default_rng(seed)
    cyc = rate_win
    us = rng.random(n)
    ph = rng.random(n) * cyc
    lf = rng.uniform(*life, n)
    vys = rng.uniform(*vy, n) * k
    vxs = rng.uniform(*vx, n) * k
    rs = rng.uniform(*size, n) * max(0.6, k)
    wp = rng.uniform(0, 6.28, n)
    wa = rng.uniform(4, 14, n) * k
    P = []
    for i in range(n):
        age = (t + ph[i]) % cyc
        if age > lf[i]:
            continue
        cyc_i = math.floor((t + ph[i]) / cyc)
        u = (us[i] + 0.6180339 * cyc_i) % 1.0
        X0, Y0 = pts_fn(u)
        X = X0 + vxs[i] * age + wind * k * age * age * 20 + wa[i] * math.sin(wp[i] + age * 6.0)
        Y = Y0 - vys[i] * age - acc * k * age * age
        if not (-5 <= X < W + 5 and -5 <= Y < H + 5):
            continue
        br = (1 - age / lf[i]) ** 1.2 * (0.6 + 0.4 * math.sin(age * 25 + wp[i]) ** 2)
        P.append((X, Y, rs[i], br))
    if not P:
        return f
    A = np.array(P, np.float32)
    g = max(1.0, 3 * k)
    pad = int(g * 3 + A[:, 2].max() + 3)
    x0 = int(max(0, A[:, 0].min() - pad))
    x1 = int(min(W, A[:, 0].max() + pad))
    y0 = int(max(0, A[:, 1].min() - pad))
    y1 = int(min(H, A[:, 1].max() + pad))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return f
    sp = np.zeros((y1 - y0, x1 - x0), np.float32)
    for X, Y, r, br in P:
        cv2.circle(sp, (int((X - x0) * 4), int((Y - y0) * 4)), max(1, int(r * 4)), float(br), -1, lineType=cv2.LINE_AA, shift=2)
    sub = f[y0:y1, x0:x1]
    sub[:] = ref_screen(sub, np.array(col, np.float32) * np.clip(sp * 1.2 * amt, 0, 1)[..., None])
    sub[:] = ref_screen(sub, np.array(glow_col, np.float32) * np.clip(ref_blur(sp, g) * 2.5 * amt, 0, 1)[..., None])
    return f


def ref_roi_of(pts, pad, W, H, up=0):
    x0 = int(max(0, np.floor(pts[:, 0].min() - pad)))
    x1 = int(min(W, np.ceil(pts[:, 0].max() + pad)))
    y0 = int(max(0, np.floor(pts[:, 1].min() - pad - up)))
    y1 = int(min(H, np.ceil(pts[:, 1].max() + pad)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1, y1


def ref_cov(s, cov, feather=0.05):
    a, b = cov
    return np.clip((s - a) / feather + 0.5, 0, 1) * np.clip((b - s) / feather + 0.5, 0, 1)


def ref_blade_fire(f, S, s, w, k, t, P):
    """blade_fire の _blade_screen の後（S・s・w・geo.k を受け取る）。"""
    amt = float(P.get('amt', 1.0))
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    cov = P.get('cov', [0.0, 1.0])
    c = ref_cov(s, cov, P.get('feather', 0.06))
    if c.max() <= 0.01:
        return f
    hpx = float(P.get('h', 60)) * k
    seed = int(P.get('seed', 3))
    roi = ref_roi_of(S, pad=w.max() + hpx * 0.5 + 8, W=W, H=H, up=hpx * 1.7)
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
    src = ref_blur(src, max(0.8, w.mean() * 0.35))
    patch = float(P.get('patch', 0.0))
    if patch > 0:
        Tp = ref_tex(seed + 5, 512, sx=max(4.0, hpx * 0.5), sy=max(4.0, hpx * 0.5))
        m = ref_tex_win(Tp, t * hpx * 0.8, seed * 7 + t * hpx * 0.3, h, ww)
        src = src * np.clip((m - patch * 0.55) / 0.2 + 0.25, 0, 1.2)
    src *= amt
    F = ref_flame_field(src, t, hpx, seed, rise=P.get('rise', 1.0), wob=P.get('wob', 1.0), gain=P.get('gain', 1.8),
                        thr=P.get('thr', 0.30), gam=P.get('gam', 0.8), src_px=float(w.mean()))
    glow = ref_blur(src, max(2.0, hpx * 0.55)) * 3.0
    fl = 1 + 0.06 * math.sin(t * 17.0 + seed) + 0.04 * math.sin(t * 29.0 + 2 * seed)
    f = ref_add_fire(f, x0, y0, F * fl, white=P.get('white', 0.0), amt=1.0, glow=glow, glow_amt=P.get('glow', 0.5) * amt,
                     glow_col=P.get('glow_col', (255, 120, 30)))
    heat = float(P.get('heat', 0.0)) * amt
    if heat > 0.01:
        cm = cv2.GaussianBlur(core, (0, 0), 0.8) * heat * fl
        hot = np.array(P.get('hot', [255, 236, 214]), np.float32)
        sub = f[y0:y1, x0:x1]
        sub[:] = sub * (1 - cm[..., None] * 0.75) + hot[None, None] * (cm[..., None] * 0.75)
    ne = int(P.get('embers', 0))
    if ne > 0:
        ca, cb = cov

        def at(u):
            q = ca + (cb - ca) * u
            i = q * (len(S) - 1)
            i0 = int(min(i, len(S) - 2))
            a = i - i0
            return S[i0, 0] * (1 - a) + S[i0 + 1, 0] * a, S[i0, 1] * (1 - a) + S[i0 + 1, 1] * a - w[i0] * 0.5
        f = ref_embers(f, at, t, seed + 99, ne, 1.6, k=k * 1.4, amt=amt, vy=(50, 140), size=(0.8, 1.8))
    return f


def ref_blade_candle(f, S, w, k, t, P):
    amt = float(P.get('amt', 1.0))
    if amt <= 0.01:
        return f
    q = float(P.get('at', 0.8))
    i = q * (len(S) - 1)
    i0 = int(min(i, len(S) - 2))
    a = i - i0
    X = S[i0, 0] * (1 - a) + S[i0 + 1, 0] * a
    Y = S[i0, 1] * (1 - a) + S[i0 + 1, 1] * a - (w[i0] * 0.35)
    size = float(P.get('h', 40)) * k
    sway = float(P.get('sway', 0.12))
    lean = float(P.get('lean', 0.0))
    if P.get('kick'):
        tk, amp, dur = P['kick']
        if tk <= t < tk + dur:
            x = (t - tk) / dur
            lean += amp * math.exp(-4.0 * x) * math.sin(2.2 * math.pi * x)
    f = ref_candle(f, X, Y, t, size, int(P.get('seed', 5)), amt=amt, white=P.get('white', 0.15), sway=sway, lean=lean,
                   glow_amt=P.get('glow', 0.55))
    heat = float(P.get('heat', 0.4)) * amt
    if heat > 0.01:
        r = size * 0.6
        x0, y0, x1, y1 = int(max(0, X - r)), int(max(0, Y - r * 0.6)), int(min(f.shape[1], X + r)), int(min(f.shape[0], Y + r * 0.6))
        if x1 > x0 and y1 > y0:
            yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
            g = np.exp(-(((xx - X) / (r * 0.6)) ** 2 + ((yy - Y) / (r * 0.35)) ** 2)) * heat
            f[y0:y1, x0:x1] = ref_screen(f[y0:y1, x0:x1], np.array([255, 170, 90], np.float32) * g[..., None])
    return f


def night(width=240, height=160, seed=0):
    rng = np.random.default_rng(seed)
    return rng.uniform(5, 90, (height, width, 3)).astype(np.float32)


# ---- 部品が実制作と同じか ---------------------------------------------------------------------

@pytest.mark.parametrize("t", [0.0, 0.37, 1.9, 7.25])
def test_candle_and_flame_field_equal_the_private_parts(t):
    mine, theirs = night(), night()
    for X, Y, size, kwargs in ((60.5, 120.0, 30.0, {"white": 0.2, "sway": 0.15}), (180.2, 80.7, 18.0, {"lean": 0.3, "amt": 0.6}),
                               (5.0, 158.0, 40.0, {})):
        fire.candle(mine, X, Y, t, size, 5, **kwargs)
        ref_candle(theirs, X, Y, t, size, 5, **kwargs)
    assert np.array_equal(mine, theirs)
    src = np.zeros((90, 70), np.float32)
    src[70:76, 10:60] = 1.0
    assert np.array_equal(fire.flame_field(src, t, 30.0, 3), ref_flame_field(src, t, 30.0, 3))
    assert np.array_equal(fire.flame_field(src, t, 14.0, 8, detail=0.5, base_mod=0.0), ref_flame_field(src, t, 14.0, 8, detail=0.5, base_mod=0.0))


@pytest.mark.parametrize("t", [0.0, 0.8, 2.2, 13.7])
def test_embers_equal_the_private_parts_and_depend_only_on_t(t):
    pts = [(100.0, 140.0), (130.0, 120.0), (160.0, 100.0)]

    def at(u):
        return pts[min(int(u * len(pts)), len(pts) - 1)]
    for kwargs in ({}, {"vy": (-50, -10), "acc": -110, "life": (0.9, 1.5), "k": 1.8}, {"vx": (-60, -10), "k": 1.6, "wind": 0.2}):
        mine, theirs = night(), night()
        fire.embers(mine, at, t, 35, 30, 2.2, **kwargs)
        ref_embers(theirs, at, t, 35, 30, 2.2, **kwargs)
        assert np.array_equal(mine, theirs)
    again = night()
    fire.embers(again, at, t, 35, 30, 2.2)               # 前の呼び出しに引きずられない（状態を持たない）
    first = night()
    fire.embers(first, at, t, 35, 30, 2.2)
    assert np.array_equal(again, first)


def blade_points(n=25):
    s = np.linspace(0, 1, n)
    return np.stack([60 + 120 * s, 130 - 50 * s + 8 * np.sin(s * 3)], 1).astype(np.float32), s


@pytest.mark.parametrize("t", [0.0, 0.5, 3.3])
def test_blade_fire_and_candle_equal_the_private_parts(t):
    S, s = blade_points()
    k = 0.8
    w = (14 + (6 - 14) * s) * k
    blade = fire.Blade(points=S, s=s, widths=w, k=k)
    for P in ({"h": 60, "cov": [0.2, 0.9], "patch": 0.4, "heat": 0.5, "embers": 12, "seed": 3, "white": 0.2},
              {"h": 40, "amt": 0.5, "glow": 0.8, "glow_col": (255, 90, 20)}, {"amt": 0.0}):
        mine, theirs = night(), night()
        fire.blade_fire(mine, blade, t, P)
        ref_blade_fire(theirs, S, s, w, k, t, P)
        assert np.array_equal(mine, theirs)
    for P in ({"at": 0.74, "h": 56, "white": 0.2, "sway": 0.15, "seed": 5}, {"at": 0.3, "kick": [t - 0.1, 0.4, 0.5], "heat": 0.8}):
        mine, theirs = night(), night()
        fire.blade_candle(mine, blade, t, P)
        ref_blade_candle(theirs, S, w, k, t, P)
        assert np.array_equal(mine, theirs)


def burning_columns(blade, cov, P=None):
    image, base = night(), night()
    fire.blade_fire(image, blade, 0.7, {"h": 30, "cov": cov, "seed": 3, **(P or {})})
    xs = np.nonzero((np.abs(image - base).max(axis=2) > 8).any(axis=0))[0]
    return (int(xs.min()), int(xs.max())) if len(xs) else None


def straight_blade(n):
    s = np.linspace(0, 1, n)
    return fire.Blade(points=np.stack([40 + 160 * s, 120 - 40 * s], 1).astype(np.float32), s=s, widths=10 + (5 - 10) * s, k=1.0)


def test_resample_makes_the_burning_range_independent_of_the_blade_points():
    # 既定は実制作と同じく刃の点の上で cov を数えるので、点の数で燃える範囲が変わる（点が 2 つだと [0.2, 0.8] は燃えない）。
    # resample を書くと、細かく分けてから数えるので、同じ刃なら点の数によらず同じ範囲が燃える
    assert burning_columns(straight_blade(2), [0.2, 0.8]) is None and burning_columns(straight_blade(3), [0.2, 0.8]) is not None
    for cov in ([0.2, 0.8], [0.6, 1.0], [0.0, 0.3]):
        dense = burning_columns(straight_blade(65), cov, {"resample": 1.0})
        for n in (2, 3, 5, 9):
            sparse = burning_columns(straight_blade(n), cov, {"resample": 1.0})
            assert sparse is not None and abs(sparse[0] - dense[0]) <= 3 and abs(sparse[1] - dense[1]) <= 3, (cov, n, sparse, dense)
    points, s, widths = fire.resample(straight_blade(3).points, np.linspace(0, 1, 3), np.array([10.0, 7.5, 5.0]), 2.0)
    assert len(points) == 2 * 42 + 1 and s[42] == 0.5 and widths[42] == 7.5 and np.allclose(points[42], straight_blade(3).points[1])
    with pytest.raises(ValueError, match="resample"):
        fire.resample(points, s, widths, 0)


def test_keys_follow_the_private_interpolation():
    entry = {"amt": 1.0, "h": 56, "keys": {"amt": [[143.83, 1.0], [145.1, 0.95], [145.3, 0.3], [145.7, 0.0]], "c": [[0, [0, 10]], [2, [10, 30]]]}}
    assert fire.params_at(entry, 140)["amt"] == 1.0 and fire.params_at(entry, 146)["amt"] == 0.0
    assert fire.params_at(entry, 145.2)["amt"] == pytest.approx(0.95 + (0.3 - 0.95) * 0.5)
    assert fire.params_at(entry, 1.0)["c"] == [5.0, 20.0] and fire.params_at(entry, 1.0)["h"] == 56
    assert fire.lerp_keys([], 1.0) is None


# ---- 見え方（素材の座標 → 画面） ------------------------------------------------------------------

def test_view_points_follow_the_camera_and_base_coordinates(tmp_path):
    from mv_pipeline.fx.view import View
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": 12}, "duration_s": 1.0, "assets": {"base": "b.png"},
           "camera": {"keys": [{"t": 0, "zoom": 1.3, "center": [0.3, 0.6]}], "frame": "source", "clamp": True}}
    from mv_pipeline.fx import spec as fxspec
    data = fxspec.parse_spec(raw, tmp_path).data
    path = camera.CameraPath(data["camera"], 1.0, (90, 160), (0.0, 0.0, 1.0, 1.0), plate=(320, 180))
    view = View(path, (0.0, 0.0, 1.0, 1.0), (320, 180), (90, 160), 0.5)
    box = camera.screen_box(path, 0.5, [0.25, 0.5, 0.35, 0.7])
    (x0, y0), (x1, y1) = view.point(0.25, 0.5), view.point(0.35, 0.7)
    assert [(x0 + 0.5) / 90, (y0 + 0.5) / 160, (x1 + 0.5) / 90, (y1 + 0.5) / 160] == pytest.approx(box, abs=1e-6)
    assert (x1 - x0) == pytest.approx(0.1 * view.scale, rel=1e-6)
    geo = view.base(1672, 941)
    assert geo.pt(1672 * 0.25, 941 * 0.5) == pytest.approx(view.point(0.25, 0.5)) and geo.k == pytest.approx(view.scale / 1672)
    blade = view.blade([(400, 500), (600, 450)], 20, 10, base=(1672, 941))
    assert blade.k == pytest.approx(geo.k) and blade.widths[0] == pytest.approx(20 * geo.k) and blade.points.dtype == np.float32
    source = np.tile(np.linspace(0, 1, 320, dtype=np.float32), (180, 1))
    warped = view.warp(source)
    assert warped.shape == (160, 90) and np.all(np.diff(warped[80]) >= -1e-6)          # 横の勾配が写って、左から右へ増える


def test_a_normalized_blade_burns_like_the_same_blade_in_1920_wide_coordinates():
    # 正規化座標で書いた刃も、素材の幅を 1920 とした基準座標で書いた同じ刃と同じ火になる（火の粉の速さ・大きさも）。
    # 正規化座標の刃の k は素材の幅 1 の画素数なので、それをそのまま火の粉に掛けると、数百倍の火の粉が一瞬で画面の外へ飛んでいた
    from mv_pipeline.fx import spec as fxspec
    from mv_pipeline.fx.view import View
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 240, "height": 160, "fps": 12}, "duration_s": 2.0, "assets": {"base": "b.png"},
           "camera": {"keys": [{"t": 0, "zoom": 1.2, "center": [0.5, 0.5]}]}}
    path = camera.CameraPath(fxspec.parse_spec(raw, ".").data["camera"], 2.0, (240, 160), (0.0, 0.0, 1.0, 1.0))
    view = View(path, (0.0, 0.0, 1.0, 1.0), None, (240, 160), 0.5)
    points = [(0.25, 0.75), (0.375, 0.6875), (0.5, 0.625), (0.625, 0.59375)]
    normalized = view.blade(points, 1 / 128, 1 / 256)                       # 幅と高さは素材の幅の割合
    reference = view.blade([(u * 1920, v * 1024) for u, v in points], 15, 7.5, base=(1920, 1024))
    for t in (0.4, 1.3):
        a, b = night(), night()
        fire.blade_fire(a, normalized, t, {"h": 0.05, "cov": [0.0, 1.0], "embers": 16, "seed": 3})
        fire.blade_fire(b, reference, t, {"h": 0.05 * 1920, "cov": [0.0, 1.0], "embers": 16, "seed": 3})
        assert np.abs(a - b).max() < 0.5, (t, float(np.abs(a - b).max()))
    assert normalized.px == pytest.approx(view.scale / 1920) == pytest.approx(reference.k) and reference.px == reference.k


# ---- hooks ----------------------------------------------------------------------------------

@pytest.fixture
def demo_timeline(tmp_path):
    sequence.demo(tmp_path, 160, 90, 3.0, 8)
    return tmp_path / "timeline.json"


def write_hooks(path, body):
    path.write_text(body, encoding="utf-8")
    return path


@pytest.mark.parametrize("body, message", [
    ("x = 1\n", "どれもありません"),
    ("def shot(img, t, view):\n    return img[:10]\n", "同じ形"),
    ("def shot(img, t, view):\n    raise ValueError('boom')\n", "shot が失敗"),
    ("import nothing_here\n", "読み込み が失敗"),
])
def test_hooks_errors_name_the_cause(demo_timeline, tmp_path, body, message):
    hooks = write_hooks(tmp_path / "bad.py", body)
    with pytest.raises(PipelineError, match=message):
        ctx = sequence.Context(sequence.load_timeline(demo_timeline), hooks=Hooks(hooks))
        try:
            ctx.frame(0.5)
        finally:
            ctx.close()


def test_missing_hooks_file_stops(tmp_path):
    with pytest.raises(PipelineError, match="見つかりません"):
        Hooks(tmp_path / "none.py")


def test_hooks_run_per_shot_and_after_the_transitions(demo_timeline, tmp_path):
    raw = json.loads(demo_timeline.read_text(encoding="utf-8"))
    raw.pop("look")                                 # 全体の look（周辺減光・粒状感）を外して、hooks の画をそのまま見る
    demo_timeline = demo_timeline.with_name("timeline_plain.json")
    demo_timeline.write_text(json.dumps(raw), encoding="utf-8")
    plain = sequence.Context(sequence.load_timeline(demo_timeline))
    hooks = write_hooks(tmp_path / "paint.py", """
calls = []
def prepare(ctx):
    calls.append(("prepare", ctx.size))
def shot(img, t, view):
    calls.append(("shot", view.index, view.kind, round(view.t, 6)))
    img[: img.shape[0] // 2] = 255.0          # 上半分を白に
    return img
def final(img, t, ctx):
    calls.append(("final", round(t, 6)))
    img[-1] = 0.0                             # 最後の行を黒に（つないだ後）
    return img
""")
    loaded = Hooks(hooks)
    ctx = sequence.Context(sequence.load_timeline(demo_timeline), hooks=loaded)
    try:
        frame, base = ctx.frame(0.75), plain.frame(0.75)
    finally:
        ctx.close()
        plain.close()
    assert np.all(frame[:45] >= 250) and np.all(frame[-1] == 0) and np.array_equal(frame[45:-1], base[45:-1])
    calls = loaded.module.calls
    assert calls[0] == ("prepare", (160, 90)) and ("shot", 0, "spec", 0.75) in calls and calls[-1] == ("final", 0.75)


def test_runs_record_hooks_and_render_deterministically(demo_timeline, tmp_path):
    from pathlib import Path as _Path
    example = _Path(__file__).resolve().parents[1] / "examples" / "fx" / "fire_hooks.py"
    first = sequence.render_run(demo_timeline, quiet=True, hooks=example)
    assert first["technical_pass"] and first["hooks"]["sha256"] and not first["reused"]
    assert sequence.render_run(demo_timeline, quiet=True, hooks=example)["reused"]
    plain = sequence.render_run(demo_timeline, quiet=True)
    assert not plain["reused"] and plain["hooks"] is None                     # hooks の有無で key が分かれる
    ctx_a = sequence.Context(sequence.load_timeline(demo_timeline), hooks=Hooks(example))
    ctx_b = sequence.Context(sequence.load_timeline(demo_timeline), hooks=Hooks(example))
    try:
        frames = [(ctx_a.frame(t), ctx_b.frame(t)) for t in (0.5, 2.6, 0.5)]
    finally:
        ctx_a.close()
        ctx_b.close()
    assert all(np.array_equal(a, b) for a, b in frames) and np.array_equal(frames[0][0], frames[2][0])
    changed = tmp_path / "fire_hooks.py"
    changed.write_text(example.read_text(encoding="utf-8").replace("seed=5", "seed=6"), encoding="utf-8")
    assert not sequence.render_run(demo_timeline, quiet=True, hooks=changed)["reused"]     # hooks を変えたら描き直す
    manifest = json.loads((_Path(first["run_dir"]) / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["hooks"]["sha256"] == first["hooks"]["sha256"]
