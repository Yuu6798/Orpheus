"""空と天気・明かりと質感の演出の部品（fx/sky.py・fx/accent.py）、静止画の手直し（fx/retouch.py）、拍の脈（fx-timeline の pulse）、
view の深度と view_at。

部品は実制作の演出の部品の関数を写したもの（ref_*）と、同じ入力で画がバイト単位で同じかを比べる。geo は実制作の Geo の写し
（RefGeo。点と倍率は Python の float）を両方に渡す。
"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import accent, camera, fire, retouch, sequence, sky  # noqa: E402
from mv_pipeline.fx.hooks import Hooks  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]

# ---- 実制作の演出の部品（写し） --------------------------------------------------------------------
_ref_cache = {}
_REF_RAIN = {}


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


def ref_roi_of(pts, pad, W, H, up=0):
    x0 = int(max(0, np.floor(pts[:, 0].min() - pad)))
    x1 = int(min(W, np.ceil(pts[:, 0].max() + pad)))
    y0 = int(max(0, np.floor(pts[:, 1].min() - pad - up)))
    y1 = int(min(H, np.ceil(pts[:, 1].max() + pad)))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    return x0, y0, x1, y1


def ref_ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def ref_lerp_keys(keys, t):
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


def ref_flick(t, seed, amt=1.0):
    ph = (seed * 1.618) % 6.28
    return 1 + amt * (0.05 * math.sin(t * 9.3 + ph) + 0.035 * math.sin(t * 17.9 + 2 * ph) + 0.025 * math.sin(t * 3.7 + 3 * ph))


def ref_lamp_level(L, t):
    on = L.get('on', -1e9)
    if t < on:
        return 0.0
    dur = L.get('ramp', 0.35)
    x = (t - on) / max(dur, 1e-3)
    lv = 1.0 if x >= 1 else (x ** 0.7) * (1 + 0.35 * math.sin(x * 9))
    if 'off' in L and t > L['off']:
        lv *= max(0.0, 1 - (t - L['off']) / L.get('off_ramp', 0.4))
    return lv * L.get('amt', 1.0)


def ref_lamps(f, geo, t, P):
    H, W = f.shape[:2]
    gcol = np.array(P.get('col', [255, 150, 60]), np.float32)
    for j, L in enumerate(P['lamps']):
        lv = ref_lamp_level(L, t) * P.get('amt', 1.0)
        if lv <= 0.01:
            continue
        lv *= ref_flick(t, j + 7, L.get('flick', 1.0))
        col = np.array(L.get('col', gcol), np.float32)
        X, Y = geo.pt(L['x'], L['y'])
        r = L.get('r', 20) * geo.k
        kind = L.get('kind', 'dot')
        if kind in ('box', 'window') and 'box' in L:
            bx0, by0 = geo.pt(L['box'][0], L['box'][1])
            bx1, by1 = geo.pt(L['box'][2], L['box'][3])
            x0, y0, x1, y1 = int(max(0, bx0 - 2)), int(max(0, by0 - 2)), int(min(W, bx1 + 3)), int(min(H, by1 + 3))
            if x1 > x0 and y1 > y0:
                yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
                fe = max(1.0, L.get('feather', 1.5) * geo.k)
                m = np.clip((xx - bx0) / fe, 0, 1) * np.clip((bx1 - xx) / fe, 0, 1) * np.clip((yy - by0) / fe, 0, 1) * np.clip((by1 - yy) / fe, 0, 1)
                sub = f[y0:y1, x0:x1]
                lum = sub.mean(-1, keepdims=True) / 255.0
                gain = np.array(L.get('gain', [5.0, 3.1, 1.4]), np.float32) * lv
                lit = sub * (1 + gain[None, None] * m[..., None]) + col[None, None] * (m * lv * L.get('fill', 0.35) * (0.4 + lum[..., 0]))[..., None]
                sub[:] = np.clip(lit, 0, 255)
        cr = max(1.0, L.get('core', 0.25) * r)
        x0, y0, x1, y1 = int(max(0, X - 3 * r)), int(max(0, Y - 3 * r)), int(min(W, X + 3 * r + 1)), int(min(H, Y + 3 * r + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d2 = (xx - X) ** 2 + (yy - Y) ** 2
        halo = np.exp(-d2 / (2 * r * r)) * L.get('halo', 0.45) * lv
        core = np.exp(-d2 / (2 * cr * cr)) * L.get('core_amt', 0.9 if kind == 'dot' else 0.25) * lv
        sub = f[y0:y1, x0:x1]
        sub[:] = ref_screen(sub, col[None, None] * np.clip(halo, 0, 1)[..., None])
        sub[:] = ref_screen(sub, np.array([255, 236, 190], np.float32)[None, None] * np.clip(core, 0, 1)[..., None])
    return f


def ref_bolt_path(p0, p1, seed, rough=0.22, depth=6, branch=0.5):
    rng = np.random.default_rng(seed)

    def split(a, b, d, out):
        if d == 0:
            out.append(b)
            return
        m = (a + b) / 2
        L = np.linalg.norm(b - a)
        n = np.array([-(b - a)[1], (b - a)[0]]) / max(L, 1e-6)
        m = m + n * rng.normal(0, rough * L * 0.5)
        split(a, m, d - 1, out)
        split(m, b, d - 1, out)
    a = np.array(p0, np.float64)
    b = np.array(p1, np.float64)
    main = [a]
    split(a, b, depth, main)
    paths = [(np.array(main), 1.0)]
    nb = int(rng.integers(2, 5))
    for k in range(nb):
        if rng.random() > branch:
            continue
        i = int(rng.integers(len(main) // 5, len(main) * 4 // 5))
        s = main[i]
        dirv = (b - a)
        ang = rng.uniform(-0.9, 0.9)
        R = np.array([[math.cos(ang), -math.sin(ang)], [math.sin(ang), math.cos(ang)]])
        e = s + R @ dirv * rng.uniform(0.2, 0.45)
        br = [s]
        split(s, e, depth - 2, br)
        paths.append((np.array(br), 0.45))
    return paths


def ref_lightning(f, geo, t, P):
    t0 = P['t0']
    pulses = P.get('pulses', [[0.0, 1.0], [0.09, 0.45], [0.2, 0.8], [0.42, 0.25]])
    if t < t0 - 0.01 or t > t0 + pulses[-1][0] + P.get('tail', 0.6):
        return f
    lv = 0.0
    for dt, a in pulses:
        x = t - (t0 + dt)
        if x >= 0:
            lv = max(lv, a * math.exp(-x / P.get('decay', 0.07)))
    if lv <= 0.005:
        return f
    H, W = f.shape[:2]
    col = np.array(P.get('col', [175, 120, 255]), np.float32)
    fl = P.get('flash', 0.5) * lv
    if fl > 0.004:
        mode = P.get('sky', 'top')
        if mode == 'all':
            m = np.ones((H, W), np.float32)
        elif mode == 'depth' and geo.dep is not None:
            d = geo.warp(geo.dep)
            m = np.clip((P.get('far', 0.32) - d) / 0.18, 0, 1)
        else:
            yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
            m = np.clip((P.get('top', 0.55) - yy) / 0.35, 0, 1) * np.ones((1, W), np.float32)
        if 'center' in P:
            cx, cy = geo.pt(*P['center'])
            rr = P.get('radius', 600) * geo.k
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            m = m * (0.25 + 0.75 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rr * rr)))
        lum = f.mean(-1, keepdims=True)
        f[:] = f + (col[None, None] * 0.55 + lum * 0.9) * (m * fl)[..., None]
        if P.get('lift', 0) > 0:
            f[:] = f * (1 + P['lift'] * lv) + (col / 255.0)[None, None] * (P['lift'] * lv * 20)
    bv = P.get('bolt', 1.0) * lv
    if bv > 0.01 and 'p0' in P:
        paths = ref_bolt_path(geo.pt(*P['p0']), geo.pt(*P['p1']), P.get('seed', 1), P.get('rough', 0.22), P.get('depth', 6))
        allp = np.concatenate([p for p, _ in paths])
        roi = ref_roi_of(allp.astype(np.float32), pad=30 * geo.k + 10, W=W, H=H)
        if roi is not None:
            x0, y0, x1, y1 = roi
            c = np.zeros((y1 - y0, x1 - x0), np.float32)
            wd = P.get('width', 2.2) * geo.k
            for p, wr in paths:
                q = ((p - [x0, y0]) * 4).astype(np.int32)
                cv2.polylines(c, [q], False, float(wr), max(1, int(round(wd * wr))), cv2.LINE_AA, shift=2)
            sub = f[y0:y1, x0:x1]
            sub[:] = ref_screen(sub, col[None, None] * np.clip(ref_blur(c, 10 * geo.k) * 2.2 * bv, 0, 1)[..., None])
            sub[:] = ref_screen(sub, np.array([245, 230, 255], np.float32)[None, None] * np.clip(ref_blur(c, 0.7) * 1.6 * bv, 0, 1)[..., None])
    return f


def ref_rain(f, t, P, SC):
    amt = P.get('amt', 1.0)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    small = cv2.resize(f, (max(1, W // 8), max(1, H // 8)), interpolation=cv2.INTER_AREA)
    light = cv2.resize(cv2.GaussianBlur(small, (0, 0), 2.0), (W, H), interpolation=cv2.INTER_LINEAR)
    c = np.zeros((H, W), np.float32)
    ang = P.get('angle', -0.13)
    layers = P.get('layers', [[260, 1500, 14, 0.30, 1], [140, 2100, 26, 0.45, 1], [45, 2800, 54, 0.60, 2]])
    for li, (n, vy, ln, br, th) in enumerate(layers):
        n = int(n * P.get('density', 1.0) * (W * H) / (960 * 540))
        key = (li, n, W, H, P.get('seed', 3))
        if key not in _REF_RAIN:
            rng = np.random.default_rng(P.get('seed', 3) * 31 + li)
            _REF_RAIN[key] = (rng.random(n) * (W + H), rng.random(n) * H * 1.3, rng.uniform(0.6, 1.0, n), rng.uniform(0.85, 1.15, n))
        xs, ys, bs, vs = _REF_RAIN[key]
        v = vy * SC * vs
        L = ln * SC * vs
        Y = (ys + v * t) % (H * 1.3) - H * 0.15
        X = xs - H * 0.5 + ang * Y
        for x, y, b, l in zip(X, Y, bs, L):
            if -l < y < H + l and -20 < x < W + 20:
                cv2.line(c, (int(x * 4), int(y * 4)), (int((x - ang * l) * 4), int((y - l) * 4)), float(b * br), max(1, int(th * max(1.0, SC))), cv2.LINE_AA, shift=2)
    c = cv2.GaussianBlur(c, (0, 0), 0.6 * max(1.0, SC))
    base = np.array(P.get('col', [150, 165, 200]), np.float32)
    add = (base[None, None] * P.get('ambient', 0.22) + light * P.get('catch', 1.6)) * (c * amt)[..., None]
    return f + add


def ref_grade(f, P, dep_screen=None):
    amt = P.get('amt', 1.0)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    if dep_screen is not None:
        m = np.clip((P.get('far', 0.30) - dep_screen) / 0.22, 0, 1)
    else:
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
        m = np.clip((P.get('top', 0.45) - yy) / 0.3, 0, 1) * np.ones((1, W), np.float32)
    lum = f.mean(-1)
    m = m * np.clip(1 - lum / 140.0, 0, 1)
    tint = np.array(P.get('tint', [10, -2, 22]), np.float32)
    return f + tint[None, None] * (m * amt)[..., None]


def ref_poly_mask(shape, poly, base, k_img):
    m = np.zeros(shape[:2], np.uint8)
    q = (np.array(poly, np.float32) * k_img).astype(np.int32)
    cv2.fillPoly(m, [q], 255)
    return m


def ref_static_edit(im, spec, base=(1672, 941)):
    k = im.shape[1] / float(base[0])
    out = im.copy()
    for e in spec:
        op = e['op']
        if op in ('erase', 'dim', 'specks'):
            reg = ref_poly_mask(im.shape, e['poly'], base, k) > 0
            f = out.astype(np.float32)
            r, g, b = f[..., 0], f[..., 1], f[..., 2]
            lum = f.mean(-1)
            warm = (r > b + e.get('warm', 25)) & (lum > e.get('lum', 70)) & reg
            m = warm.astype(np.uint8) * 255
            if op == 'specks':
                n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
                keep = np.zeros_like(m)
                for i in range(1, n):
                    if st[i, cv2.CC_STAT_AREA] <= e.get('max_area', 120) * k * k:
                        keep[lab == i] = 255
                m = keep
            m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=int(e.get('grow', 2)))
            if op == 'dim':
                a = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), 1.5)[..., None]
                fac = e.get('factor', 0.15)
                tgt = cv2.inpaint(out, m, 3, cv2.INPAINT_TELEA).astype(np.float32)
                mix = tgt * (1 - fac) + out.astype(np.float32) * fac
                out = np.clip(out * (1 - a) + mix * a, 0, 255).astype(np.uint8)
            else:
                out = cv2.inpaint(out, m, 4, cv2.INPAINT_TELEA)
        elif op == 'desat':
            reg = cv2.GaussianBlur(ref_poly_mask(im.shape, e['poly'], base, k).astype(np.float32) / 255.0, (0, 0), 6 * k)
            hsv = cv2.cvtColor(out, cv2.COLOR_RGB2HSV).astype(np.float32)
            red = ((hsv[..., 0] < 12) | (hsv[..., 0] > 165)) & (hsv[..., 1] > 60)
            a = reg * red * e.get('amt', 0.6)
            hsv[..., 1] *= (1 - a)
            hsv[..., 2] *= (1 - a * e.get('darken', 0.25))
            out = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2RGB)
    return out


def ref_smoke(f, geo, t, P):
    amt = ref_lerp_keys(P['amt'], t) if isinstance(P.get('amt'), list) else P.get('amt', 0.5)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    X, Y = geo.pt(*P['src'])
    wpx = P.get('w', 220) * geo.k
    hpx = P.get('h', 600) * geo.k
    x0, x1 = int(max(0, X - wpx * 1.6)), int(min(W, X + wpx * 1.6))
    y0, y1 = int(max(0, Y - hpx)), int(min(H, Y + 10))
    if x1 <= x0 or y1 <= y0:
        return f
    h, w = y1 - y0, x1 - x0
    T1 = ref_tex(77, 512, sx=max(4.0, wpx * 0.25), sy=max(6.0, wpx * 0.45))
    T2 = ref_tex(78, 512, sx=max(3.0, wpx * 0.12), sy=max(3.0, wpx * 0.2))
    sp = P.get('speed', 60) * geo.k
    n = 0.6 * ref_tex_win(T1, t * sp, 11, h, w) + 0.4 * ref_tex_win(T2, t * sp * 1.3, 37, h, w)
    yy = np.arange(h, dtype=np.float32)[:, None] + y0
    xx = np.arange(w, dtype=np.float32)[None, :] + x0
    v = np.clip((Y - yy) / hpx, 0, 1)
    drift = P.get('drift', 0.25) * hpx * v ** 1.5
    spread = wpx * (0.35 + 1.2 * v)
    col_m = np.exp(-((xx - X - drift) / spread) ** 2)
    dens = np.clip((n - 0.42) * 2.6, 0, 1) * col_m * np.clip(v / 0.08, 0, 1) * (1 - v) ** 1.2 * amt
    c = np.array(P.get('col', [120, 125, 140]), np.float32)
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 - dens[..., None] * 0.55) + c[None, None] * (dens[..., None] * 0.55)
    return f


def ref_warm_light(f, geo, t, P):
    amt = ref_lerp_keys(P['amt'], t) if isinstance(P.get('amt'), list) else P.get('amt', 0.5)
    if amt <= 0.005:
        return f
    H, W = f.shape[:2]
    X, Y = geo.pt(P['x'], P['y'])
    rx = P.get('rx', 200) * geo.k
    ry = P.get('ry', 200) * geo.k
    x0, x1 = int(max(0, X - 3 * rx)), int(min(W, X + 3 * rx))
    y0, y1 = int(max(0, Y - 3 * ry)), int(min(H, Y + 3 * ry))
    if x1 <= x0 or y1 <= y0:
        return f
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    g = np.exp(-(((xx - X) / rx) ** 2 + ((yy - Y) / ry) ** 2)) * amt * ref_flick(t, P.get('seed', 3), P.get('flick', 2.0))
    col = np.array(P.get('col', [255, 140, 50]), np.float32)
    sub = f[y0:y1, x0:x1]
    if P.get('mode', 'mul') == 'tint':
        warm = sub * (np.array(P.get('tint', [1.08, 0.80, 0.56]), np.float32)[None, None]) + np.array([18, 4, 0], np.float32)
        sub[:] = sub * (1 - g[..., None]) + warm * g[..., None]
    elif P.get('mode', 'mul') == 'mul':
        sub[:] = sub * (1 + g[..., None] * (col / 255.0) * P.get('gain', 1.6)) + col * (g * P.get('add', 0.08))[..., None]
    else:
        sub[:] = ref_screen(sub, col * g[..., None])
    return f


def ref_streaks(f, geo, t, P):
    amt = ref_lerp_keys(P['amt'], t) if isinstance(P.get('amt'), list) else P.get('amt', 0.5)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    S = geo.pts(P['pts'])
    roi = ref_roi_of(S, pad=P.get('len', 120) * geo.k + 10, W=W, H=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    c = np.zeros((y1 - y0, x1 - x0), np.float32)
    rng = np.random.default_rng(P.get('seed', 9))
    for i, (X, Y) in enumerate(S):
        L = P.get('len', 120) * geo.k * rng.uniform(0.6, 1.0)
        wd = max(1, int(P.get('w', 6) * geo.k * rng.uniform(0.6, 1.2)))
        fl = ref_flick(t, i + 3, 2.5)
        for k in range(6):
            yy = Y + L * k / 6.0
            a = (1 - k / 6.0) * fl * rng.uniform(0.5, 1.0)
            jx = math.sin(t * 3 + i + k) * 1.5 * geo.k
            cv2.line(c, (int((X + jx - x0) * 4), int((yy - y0) * 4)), (int((X + jx - x0) * 4), int((yy + L / 6.0 - y0) * 4)), float(a), wd, cv2.LINE_AA, shift=2)
    c = ref_blur(c, 1.2 * geo.k + 0.5)
    sub = f[y0:y1, x0:x1]
    sub[:] = ref_screen(sub, np.array(P.get('col', [255, 150, 70]), np.float32)[None, None] * np.clip(c * amt, 0, 1)[..., None])
    return f


def ref_shadow(f, geo, t, P):
    ln = ref_lerp_keys(P['len'], t) if isinstance(P.get('len'), list) else P.get('len', 1.0)
    if ln <= 0.01:
        return f
    H, W = f.shape[:2]
    X0, Y0 = geo.pt(*P['foot'])
    X1, Y1 = geo.pt(*P['to'])
    X1 = X0 + (X1 - X0) * ln
    Y1 = Y0 + (Y1 - Y0) * ln
    wd0 = P.get('w0', 40) * geo.k
    wd1 = P.get('w1', 90) * geo.k
    d = np.array([X1 - X0, Y1 - Y0])
    L = np.linalg.norm(d) + 1e-6
    n = np.array([-d[1], d[0]]) / L
    poly = np.array([[X0, Y0] + n * wd0 / 2, [X1, Y1] + n * wd1 / 2, [X1, Y1] - n * wd1 / 2, [X0, Y0] - n * wd0 / 2], np.float32)
    roi = ref_roi_of(poly, pad=30 * geo.k, W=W, H=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    cv2.fillPoly(m, [((poly - [x0, y0]) * 4).astype(np.int32)], 1.0, cv2.LINE_AA, shift=2)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    along = np.clip(((xx - X0) * d[0] + (yy - Y0) * d[1]) / (L * L), 0, 1)
    m = ref_blur(m, 6 * geo.k + 1) * (1 - 0.6 * along)
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 - m[..., None] * P.get('amt', 0.4))
    return f


def ref_spread_light(f, geo, t, P):
    H, W = f.shape[:2]
    X, Y = geo.pt(P['x'], P['y'])
    p = ref_ease((t - P['t0']) / max(1e-3, P['t1'] - P['t0']))
    r = P.get('r0', 30) * geo.k + (P.get('r1', 900) * geo.k) * p
    edge = P.get('edge', 140) * geo.k
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt((xx - X) ** 2 + ((yy - Y) * 1.15) ** 2)
    nz = ref_tex_win(ref_tex(91, 512, 18, 18), t * 20, 5, H, W)
    band = np.exp(-((d - r) / max(edge, 1.0)) ** 2)
    lit = np.clip((r - d) / max(edge, 1.0) + 0.5 + (nz - 0.5) * 0.9 * band, 0, 1)
    lum = f.mean(-1)
    m = np.clip((lum - P.get('lum0', 60)) / 25.0, 0, 1)
    sg = max(4.0, 40 * geo.k)
    Lb = ref_blur(lum * m, sg) / np.maximum(ref_blur(m, sg), 1e-3)
    pm = ref_blur(m, 1.5)
    unlit = pm * np.clip(P.get('dim', 42) / np.maximum(Lb, 1.0), 0, 1) + (1 - pm)
    fac = unlit * (1 - lit) + lit
    out = f * fac[..., None]
    g = np.exp(-(d / max(1.0, 0.45 * r + 12 * geo.k)) ** 2) * (0.25 + 0.35 * p) * ref_flick(t, 5, 2.0)
    return ref_screen(out, np.array([255, 150, 60], np.float32)[None, None] * np.clip(g * pm, 0, 1)[..., None])


def ref_flicker(f, geo, t, P, SC):
    amt = P.get('amt', 0.06)
    H, W = f.shape[:2]
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    m = np.clip((r - b - 40) / 80.0, 0, 1) * np.clip((f.mean(-1) - 60) / 80.0, 0, 1)
    if P.get('region'):
        x0, y0 = geo.pt(*P['region'][:2])
        x1, y1 = geo.pt(*P['region'][2:])
        reg = np.zeros((H, W), np.float32)
        reg[int(max(0, y0)):int(max(0, y1)), int(max(0, x0)):int(max(0, x1))] = 1
        m = m * ref_blur(reg, 10)
    nz = ref_tex_win(ref_tex(55, 512, 40, 60), t * 120 * SC, 9, H, W)
    fl = 1 + amt * ((nz - 0.5) * 2.5 + 0.6 * math.sin(t * 11.3) + 0.4 * math.sin(t * 6.1))
    return f * (1 + (fl - 1) * m)[..., None]


def ref_steel(f, S, s, w, t, P):
    H, W = f.shape[:2]
    roi = ref_roi_of(S, pad=w.max() + 4, W=W, H=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    h, ww = y1 - y0, x1 - x0
    m = np.zeros((h, ww), np.float32)
    band = np.zeros((h, ww), np.float32)
    ph = (t * P.get('speed', 0.12) + P.get('phase', 0.0)) % 1.6 - 0.3
    for i in range(len(S) - 1):
        p0 = (int((S[i, 0] - x0) * 4), int((S[i, 1] - y0) * 4))
        p1 = (int((S[i + 1, 0] - x0) * 4), int((S[i + 1, 1] - y0) * 4))
        th = max(1, int(round(0.5 * (w[i] + w[i + 1]) * 0.85)))
        cv2.line(m, p0, p1, 1.0, th, cv2.LINE_AA, shift=2)
        sm = 0.5 * (s[i] + s[i + 1])
        b = math.exp(-((sm - ph) / 0.07) ** 2)
        if b > 0.01:
            cv2.line(band, p0, p1, float(b), max(1, int(th * 0.5)), cv2.LINE_AA, shift=2)
    m = cv2.GaussianBlur(m, (0, 0), 0.6)
    T = ref_tex(301, 512, sx=1.2, sy=1.2)
    n = (ref_tex_win(T, int(t * 2) * 17, 31, h, ww) - 0.5) * 2.0
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 + n[..., None] * P.get('grain', 0.10) * m[..., None])
    band = ref_blur(band, max(1.0, w.mean() * 0.6)) * m
    sub[:] = ref_screen(sub, np.array([235, 240, 255], np.float32)[None, None] * np.clip(band * P.get('spec', 0.35), 0, 1)[..., None])
    return f


class RefGeo:
    """実制作の Geo の写し（視差なし）。M は出力の画素 → 素材の画素（WARP_INVERSE_MAP で使う形）。"""

    def __init__(self, sw, sh, M, base, W, H, dep=None):
        self.sw, self.sh, self.M, self.W, self.H = sw, sh, M, W, H
        self.bw, self.bh = base
        self.s = float(M[0, 0])
        self.x0 = float(M[0, 2])
        self.y0 = float(M[1, 2])
        self.k = (sw / self.bw) / self.s
        self.dep = dep

    def pt(self, x, y):
        return (x / self.bw * self.sw - self.x0) / self.s, (y / self.bh * self.sh - self.y0) / self.s

    def pts(self, P):
        return np.array([self.pt(x, y) for x, y in P], np.float32)

    def warp(self, a):
        if a.shape[0] != self.sh or a.shape[1] != self.sw:
            a = cv2.resize(a, (self.sw, self.sh), interpolation=cv2.INTER_LINEAR)
        return cv2.warpAffine(a, self.M, (self.W, self.H), flags=cv2.INTER_LINEAR | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


# ---- 素材 ------------------------------------------------------------------------------------------
W_OUT, H_OUT = 240, 160


def scene(seed=0):
    """夜の画（暗い空、明るい紙の窓、暖色の灯と炎）。float32 の表示の値。"""
    rng = np.random.default_rng(seed)
    f = rng.uniform(5, 60, (H_OUT, W_OUT, 3)).astype(np.float32)
    f[100:, :] += np.float32(30)                                       # 地面
    f[40:70, 150:190] = np.array([200, 180, 140], np.float32)          # 紙の窓
    f[44:66, 168:171] = np.float32(20)                                 # 格子
    f[110:140, 20:70] = np.array([235, 130, 40], np.float32)           # 描かれた炎
    f[60:64, 60:64] = np.array([250, 170, 70], np.float32)             # 描かれた灯
    return f


def geo_and_depth(dep=True):
    """素材 480×270（基準 1672×941）を 1.4 倍に寄せて 240×160 に写す Geo と、素材の深度（上が奥）。"""
    sw, sh = 480, 270
    s = 1 / 1.4 * (sh / H_OUT) * 0.95
    M = np.float32([[s, 0, 30.0], [0, s, 12.0]])
    depth = np.tile(np.linspace(0.05, 0.95, sh, dtype=np.float32)[:, None], (1, sw)) if dep else None
    return RefGeo(sw, sh, M, (1672, 941), W_OUT, H_OUT, depth)


def moved(image):
    """scene() から変わった画素があるか（比べた部品が実際に描いたか）。"""
    return bool(np.any(np.abs(np.asarray(image, np.float32) - scene()) > 0.5))


# ---- 部品が実制作と同じか ---------------------------------------------------------------------------

LAMPS = {"amt": 1.0, "col": [255, 150, 60], "lamps": [
    {"x": 520, "y": 300, "r": 9, "kind": "dot", "core": 0.32, "halo": 0.35, "core_amt": 0.8, "on": -1, "off": 3.0, "off_ramp": 2.0, "flick": 3},
    {"x": 1150, "y": 330, "r": 30, "kind": "window", "box": [1120, 300, 1240, 420], "on": 0.5, "ramp": 0.25},
    {"x": 300, "y": 700, "r": 14, "kind": "box", "box": [260, 660, 340, 740], "gain": [4.0, 2.5, 1.0], "fill": 0.5, "feather": 3.0, "col": [255, 200, 120]},
    {"x": 2000, "y": 2000, "r": 10},                                   # 画面の外
    {"x": 800, "y": 250, "r": 6, "on": 9.0}]}                         # まだ点かない


@pytest.mark.parametrize("t", [0.0, 0.62, 1.4, 3.9, 6.0])
def test_lamps_equal_the_private_parts(t):
    geo = geo_and_depth()
    mine, theirs = scene(), scene()
    sky.lamps(mine, geo, t, LAMPS)
    ref_lamps(theirs, geo, t, LAMPS)
    assert np.array_equal(mine, theirs) and moved(mine)
    assert sky.lamp_level({"on": 1.0}, 0.5) == 0.0 and sky.lamp_level({"on": 0.0, "off": 1.0, "off_ramp": 0.5}, 2.0) == 0.0
    assert sky.flick(t, 7, 2.0) == ref_flick(t, 7, 2.0)


@pytest.mark.parametrize("t", [0.99, 1.0, 1.05, 1.21, 1.6, 2.5])
@pytest.mark.parametrize("P", [
    {"t0": 1.0, "p0": [700, 140], "p1": [1150, 430], "seed": 11, "flash": 0.6, "bolt": 1.0, "width": 2.6, "sky": "all", "center": [930, 290],
     "radius": 420, "depth": 6, "rough": 0.24},
    {"t0": 1.0, "p0": [1100, 0], "p1": [1034, 318], "seed": 21, "flash": 0.45, "bolt": 0.95, "width": 1.6, "sky": "depth", "far": 0.42,
     "center": [1040, 250], "radius": 520, "depth": 5, "pulses": [[0.0, 1.0], [0.1, 0.4], [0.24, 0.7]]},
    {"t0": 1.0, "seed": 3, "flash": 0.5, "bolt": 0.0, "sky": "top", "top": 0.8, "center": [836, 300], "radius": 800, "lift": 0.3,
     "pulses": [[0.0, 1.0], [0.11, 0.4], [0.25, 0.65], [0.5, 0.2]]},
], ids=["all", "depth", "top"])
def test_lightning_equals_the_private_parts(t, P):
    geo = geo_and_depth()
    mine, theirs = scene(), scene()
    sky.lightning(mine, geo, t, P, screen_depth=geo.warp(geo.dep))
    ref_lightning(theirs, geo, t, P)
    assert np.array_equal(mine, theirs)
    if t in (0.99, 1.0, 1.21, 2.5):                      # 最初の明滅の前と尾の後は描かない（1.6 は明滅の形しだい）
        assert moved(mine) == (t in (1.0, 1.21))
    no_depth = scene()                                   # 深度が無ければ depth は top と同じ扱い（実制作と同じ）
    sky.lightning(no_depth, geo, t, P)
    geo.dep = None
    expected = ref_lightning(scene(), geo, t, P)
    assert np.array_equal(no_depth, expected)


def test_bolt_path_equals_the_private_part():
    for seed in (1, 11, 21):
        mine, theirs = sky.bolt_path((10.0, 5.0), (90.5, 120.0), seed, 0.24, 6), ref_bolt_path((10.0, 5.0), (90.5, 120.0), seed, 0.24, 6)
        assert len(mine) == len(theirs)
        assert all(np.array_equal(a, b) and wa == wb for (a, wa), (b, wb) in zip(mine, theirs))


@pytest.mark.parametrize("depth", [0, 1, 2])
def test_low_detail_bolts_stop_splitting_at_nonpositive_depths(depth):
    # 枝は depth − 2 で分ける。終わりを d == 0 だけで見ていたので、depth 0・1 では枝の分け方が負の深さで止まらず、再帰の上限で落ちていた
    paths = sky.bolt_path((10.0, 10.0), (100.0, 80.0), 3, depth=depth, branch=1.0)
    main = paths[0][0]
    assert len(main) == 2 ** depth + 1 and np.allclose(main[0], (10.0, 10.0)) and np.allclose(main[-1], (100.0, 80.0))
    assert len(paths) >= 3 and all(len(branch) == 2 ** max(depth - 2, 0) + 1 for branch, _ in paths[1:])


@pytest.mark.parametrize("t", [0.0, 0.37, 2.0, 11.3])
def test_rain_and_grade_equal_the_private_parts(t):
    P = {"amt": 1.0, "density": 1.0 * 0.45, "angle": -0.12, "ambient": 0.2, "catch": 1.5}
    for sc in (None, 2.5):
        f = scene()
        mine = sky.rain(f.copy(), t, P, sc)
        theirs = ref_rain(f.copy(), t, P, H_OUT / 1080.0 if sc is None else sc)
        assert np.array_equal(mine, theirs) and moved(mine)
        assert np.array_equal(sky.rain(f.copy(), t, P, sc), mine)              # 作り置きの雨粒でも同じ
    assert np.array_equal(sky.rain(scene(), t, {"amt": 0.0}), scene())
    geo = geo_and_depth()
    G = {"amt": 1.0, "tint": [9, -2, 20], "far": 0.3, "top": 0.42}
    depth = geo.warp(geo.dep)
    assert np.array_equal(sky.grade(scene(), G, depth), ref_grade(scene(), G, depth)) and moved(sky.grade(scene(), G, depth))
    assert np.array_equal(sky.grade(scene(), G), ref_grade(scene(), G))
    assert np.array_equal(sky.grade(scene(), {"amt": 0.0}), scene())


@pytest.mark.parametrize("t", [0.0, 0.8, 2.6, 5.1])
def test_light_and_texture_parts_equal_the_private_parts(t):
    geo = geo_and_depth()
    cases = [          # (部品, 写し, P, 描かない時刻（amt が 0 になる所）)
        (accent.warm_light, ref_warm_light, {"mode": "tint", "x": 822, "y": 668, "rx": 190, "ry": 120, "seed": 8, "flick": 3.0,
                                             "amt": [[0.5, 0.85], [2.0, 0.8], [4.5, 0.0]]}, {5.1}),
        (accent.warm_light, ref_warm_light, {"x": 500, "y": 400, "rx": 260, "ry": 200, "amt": 0.6, "gain": 2.0}, set()),
        (accent.warm_light, ref_warm_light, {"x": 1300, "y": 300, "amt": 0.4, "mode": "screen", "col": [255, 180, 90]}, set()),
        (accent.streaks, ref_streaks, {"pts": [[400, 600], [450, 610], [520, 640], [600, 650]], "len": 160, "w": 8, "amt": [[0, 0.2], [3, 0.9]]},
         set()),
        (accent.shadow, ref_shadow, {"foot": [700, 520], "to": [1000, 600], "len": [[0, 0.3], [4, 1.0]], "w0": 50, "w1": 120, "amt": 0.5}, set()),
        (accent.spread_light, ref_spread_light, {"x": 1180, "y": 350, "t0": 0.5, "t1": 3.0, "r0": 20, "r1": 400, "edge": 90, "dim": 42}, set()),
        (accent.smoke, ref_smoke, {"src": [600, 700], "w": 180, "h": 500, "amt": [[0, 0.0], [1, 0.7]], "speed": 70}, {0.0}),
    ]
    for mine_fn, ref_fn, P, quiet in cases:
        mine, theirs = scene(), scene()
        mine = mine_fn(mine, geo, t, P)
        theirs = ref_fn(theirs, geo, t, P)
        assert np.array_equal(mine, theirs), mine_fn.__name__
        assert moved(mine) == (t not in quiet), mine_fn.__name__
    for P, sc in (({"amt": 0.07, "region": [150, 450, 420, 640]}, None), ({"amt": 0.12}, 1.7777)):    # region は描かれた炎の周り
        mine = accent.flicker(scene(), geo, t, P, sc)
        assert np.array_equal(mine, ref_flicker(scene(), geo, t, P, H_OUT / 1080.0 if sc is None else sc)) and moved(mine)


@pytest.mark.parametrize("t", [0.0, 1.3, 4.7, 9.9])
def test_steel_equals_the_private_part(t):
    s = np.linspace(0, 1, 25)
    S = np.stack([40 + 150 * s, 120 - 60 * s + 6 * np.sin(s * 3)], 1).astype(np.float32)
    k = 0.7
    w = (16 + (8 - 16) * s) * k
    blade = fire.Blade(points=S, s=s, widths=w, k=k)
    for P in ({"phase": 0.5, "speed": 0.13, "grain": 0.1, "spec": 0.32}, {}):
        mine, theirs = scene(), scene()
        accent.steel(mine, blade, t, P)
        ref_steel(theirs, S, s, w, t, P)
        assert np.array_equal(mine, theirs) and moved(mine)


def test_static_edit_equals_the_private_retouch():
    rng = np.random.default_rng(3)
    im = rng.integers(10, 80, (270, 480, 3)).astype(np.uint8)
    im[40:60, 100:140] = (240, 170, 90)                       # 窓灯（暖色の明るい所）
    im[150:152, 200:202] = (250, 140, 40)                     # 描かれた火の粉
    im[150:170, 300:330] = (250, 140, 40)                     # 大きい暖色（specks では消さない）
    im[200:240, 50:120] = (200, 30, 30)                       # 赤い格子
    ops = [{"op": "erase", "poly": [[300, 100], [550, 100], [550, 260], [300, 260]]},
           {"op": "dim", "poly": [[0, 0], [1672, 0], [1672, 400], [0, 400]], "factor": 0.2},
           {"op": "specks", "poly": [[600, 450], [1200, 450], [1200, 700], [600, 700]], "max_area": 30},
           {"op": "desat", "poly": [[100, 650], [500, 650], [500, 900], [100, 900]], "amt": 0.6, "darken": 0.25}]
    for chosen in ([ops[0]], [ops[1]], [ops[2]], [ops[3]], ops):
        assert np.array_equal(retouch.static_edit(im, chosen), ref_static_edit(im, chosen))
    assert not np.array_equal(retouch.static_edit(im, ops), im)
    with pytest.raises(PipelineError, match="op は"):
        retouch.static_edit(im, [{"op": "blur", "poly": [[0, 0], [1, 1], [1, 0]]}])


# ---- 拍の脈 ----------------------------------------------------------------------------------------

def ref_beat_pulse(t, beats, high, amount):
    if not any(a <= t < b for a, b in high):
        return 0.0
    i = np.searchsorted(beats, t) - 1
    return 0.0 if i < 0 else amount * math.exp(-(t - beats[i]) * 9.0)


def test_pulse_follows_the_private_beat_pulse():
    beats = [0.02 + k * 1.379310 / 4 for k in range(80)]
    high = [[1.0, 6.5], [9.0, 12.0]]
    pulse = {"amount": 0.006, "decay": 9.0, "sections": high}
    times = [n / 30 for n in range(0, 420)] + beats[:40] + [1.0, 6.5, 9.0]
    for t in times:
        assert sequence.pulse_at(pulse, beats, t) == ref_beat_pulse(t, np.array(beats), high, 0.006)
    assert sequence.pulse_at(dict(pulse, sections=None), beats, 7.0) > 0          # 区間が無ければ全体
    assert sequence.pulse_at(dict(pulse, sections=None), beats, 0.01) == 0.0       # 最初の拍の前


def test_camera_pulse_and_punch_follow_the_private_cam():
    """実制作の縦のレンダラの cam: z = max(1, (z0 + (z1 − z0)·u)·p)、p = (1 + 拍の脈)·(1 − amt·(1 − ease_out(v)))。"""
    from mv_pipeline.fx import spec as fxspec
    beats = [0.02 + k * 0.3448275 for k in range(40)]
    pulse = {"amount": 0.006, "decay": 9.0, "sections": [[0.079, 5.0]]}
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": 30}, "duration_s": 3.0, "assets": {"base": "b.png"},
           "camera": {"keys": [{"t": 0, "zoom": 1.02, "center": [0.5, 0.5]}, {"t": 3.0, "zoom": 1.2, "center": [0.5, 0.5], "ease": "in_out"}],
                      "frame": "source", "punch": {"amount": 0.1, "duration_s": 0.4}}}
    data = fxspec.parse_spec(raw, ".").data
    start = 0.0
    path = camera.CameraPath(data["camera"], 3.0, (90, 160), plate=(320, 180)).with_pulse(lambda local: sequence.pulse_at(pulse, beats, start + local))
    for n in range(0, 90):
        t = n / 30
        u = ref_ease(t / 3.0)
        p = 1 + ref_beat_pulse(t, np.array(beats), pulse["sections"], 0.006)
        v = t / 0.4
        if v < 1:
            p *= 1 - 0.1 * (1 - camera.ease_out(v))
        expected = max(1.0, (1.02 + (1.2 - 1.02) * u) * p)
        assert path.at(t)[0] == pytest.approx(expected, rel=1e-12, abs=1e-12)
    plain = camera.CameraPath(data["camera"], 3.0, (90, 160), plate=(320, 180))
    assert plain.pulse is None and path.at(0.5)[0] != plain.at(0.5)[0]
    assert camera.CameraPath(data["camera"], 3.0, (90, 160), plate=(320, 180)).with_pulse(lambda local: 0.0).at(1.7) == plain.at(1.7)


@pytest.fixture
def demo_timeline(tmp_path):
    sequence.demo(tmp_path, 160, 90, 3.0, 8)
    return tmp_path / "timeline.json"


def with_pulse(path, pulse):
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["pulse"] = pulse
    out = path.with_name("timeline_pulse.json")
    out.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return out


def test_timeline_pulse_is_read_and_checked(demo_timeline):
    timeline = sequence.load_timeline(with_pulse(demo_timeline, {"amount": 0.01, "sections": [[0.5, {"beat": 4}]]}))
    assert timeline.data["pulse"] == {"amount": 0.01, "decay": 9.0, "sections": [[0.5, 2.0]]}
    assert "拍の脈: zoom +1%" in sequence.plan_text(with_pulse(demo_timeline, {"amount": 0.01, "sections": [[0.5, {"beat": 4}]]}))
    with pytest.raises(PipelineError, match="始め < 終わり"):
        sequence.load_timeline(with_pulse(demo_timeline, {"amount": 0.01, "sections": [[2.0, 1.0]]}))
    with pytest.raises(PipelineError, match="invalid"):
        sequence.load_timeline(with_pulse(demo_timeline, {"amount": 0.5}))
    raw = json.loads(demo_timeline.read_text(encoding="utf-8"))
    del raw["beats"]
    for shot in raw["shots"]:
        shot.pop("snap", None)
        if isinstance(shot["start"], dict):
            shot["start"] = 1.0
    raw["pulse"] = {"amount": 0.01}
    no_beats = demo_timeline.with_name("timeline_no_beats.json")
    no_beats.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(PipelineError, match="beats が必要"):
        sequence.load_timeline(no_beats)


def test_timeline_pulse_moves_every_shot_on_the_beats_only(demo_timeline):
    plain = sequence.load_timeline(demo_timeline)
    pulsed = sequence.load_timeline(with_pulse(demo_timeline, {"amount": 0.05, "sections": [[0.0, 2.2]]}))
    beats = pulsed.data["beats"]["beats"]
    a, b = sequence.Context(plain), sequence.Context(pulsed)
    try:
        for t in (0.625, 1.125, 1.75, 2.6):                     # 拍の少し後（spec のショット 2 本）と、区間の外
            inside = t < 2.2 and sequence.pulse_at(pulsed.data["pulse"], beats, t) > 1e-3
            assert (not np.array_equal(a.frame(t), b.frame(t))) == inside, t
        index = sequence.shot_index(pulsed.data, 1.125)
        local = pulsed.data["shots"][index]["offset_s"] + (1.125 - pulsed.data["shots"][index]["start"])
        assert b.view(index, local).scale > a.view(index, local).scale
        assert b.faces_at(0.625) != a.faces_at(0.625)            # 顔の枠も脈で動く
        assert b.shot(index) is b.shot(index)                   # ショットごとの写しは作り置く
    finally:
        a.close()
        b.close()
    video = sequence.load_timeline(with_pulse(demo_timeline, {"amount": 0.05}))
    last = len(video.data["shots"]) - 1
    ctx = sequence.Context(video)
    try:
        assert ctx.shot(last).path.pulse is not None             # 動画のショットにも掛かる
    finally:
        ctx.close()


# ---- 見え方の深度と view_at、見本の hooks ----------------------------------------------------------------

def test_view_points_are_plain_floats_and_depth_follows_the_camera(demo_timeline):
    timeline = sequence.load_timeline(demo_timeline)
    ctx = sequence.Context(timeline)
    try:
        view = ctx.view(0, 0.5)
        X, Y = view.point(0.5, 0.5)
        assert type(X) is float and type(Y) is float and type(view.scale) is float and type(view.base(1672, 941).k) is float
        depth = view.depth()
        assert depth.shape == (90, 160) and depth.dtype == np.float32
        assert depth[5].mean() < depth[-5].mean()                 # デモの深度は空が奥で、地面が手前
        assert depth is view.depth()                              # 1 回だけ写す
        last = len(timeline.data["shots"]) - 1
        assert ctx.view(last, 0.2).depth() is None                # 動画のショットに深度は無い
        xfade = timeline.data["shots"][1]
        during = xfade["start"] + xfade["transition_in"]["duration_s"] / 2
        assert ctx.view_at(during).index == 1                     # xfade の間は入ってくるショット
        assert ctx.view_at(during).t == pytest.approx(xfade["offset_s"] + (during - xfade["start"]))
    finally:
        ctx.close()


def test_sky_hooks_example_runs_on_the_demo(demo_timeline):
    timeline = sequence.load_timeline(demo_timeline)
    hooks = Hooks(ROOT / "examples" / "fx" / "sky_hooks.py")
    frames = {}
    for name, h in (("plain", None), ("hooks", hooks), ("again", hooks)):
        ctx = sequence.Context(timeline, hooks=h)
        try:
            frames[name] = [ctx.frame(t) for t in (0.6, 1.2, 1.3, 2.5)]
        finally:
            ctx.close()
    assert all(not np.array_equal(p, q) for p, q in zip(frames["plain"], frames["hooks"]))
    assert all(np.array_equal(p, q) for p, q in zip(frames["hooks"], frames["again"]))
