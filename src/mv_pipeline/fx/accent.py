"""明かりと質感の演出の部品（照り返し・映り込みの筋・影・紙に広がる灯・明かりの揺れ・煙・鋼の質感）。fx-timeline の hooks から
使う（実験的。docs/fx.md）。素材の局所照明（spec の lights）は light.py で、こちらは描いた後の画に掛ける演出。

fire.py・sky.py と同じく、**画面の座標**と**表示の値**（float32 の 0..255）の上で計算し、実制作の演出の部品と同じ式・同じ順・
同じ型で書いてある（同じ入力なら同じ画）。位置と長さは基準座標で書き、geo（hooks の view.base(幅, 高さ)）で画面へ写す。
amt・len を [[t, 値], ...] で書くと時刻で補間する。どれも時刻 t と引数だけで決まる。

    warm_light(f, geo, t, P)      柔らかい暖色の光（顔の照り返し・石への映り込み。mode: mul・tint・それ以外はスクリーン合成）
    streaks(f, geo, t, P)         濡れた石に映る縦の光の筋
    shadow(f, geo, t, P)          足元から伸びる影（くさび形、柔らかい縁）
    spread_light(f, geo, t, P)    紙の内側に灯が広がる（暗く平らにした紙が、中心から円く元の絵に戻る）。新しい画を返す
    flicker(f, geo, t, P, sc)     描かれた灯・炎の明るい暖色の所を少し揺らす。新しい画を返す
    smoke(f, geo, t, P)           立ち昇る煙
    steel(f, blade, t, P)         刃の中の粒と、刃に沿ってゆっくり動く反射（blade は fire.Blade。view.blade(...) が作る）
"""
import math

import cv2
import numpy as np

from .fire import blur, ease, lerp_keys, roi_of, screen, tex, tex_win
from .sky import flick


def warm_light(f, geo, t, P):
    """柔らかい暖色の光。P: x・y・rx・ry（楕円の中心と半径。基準座標）、amt（keys 可）、seed・flick（揺らぎ）、col、mode
    （mul: 色を掛けて gain・add で明るく、tint: 暖色に寄せる（tint は色ごとの倍率）、それ以外: スクリーン合成）。"""
    amt = lerp_keys(P["amt"], t) if isinstance(P.get("amt"), list) else P.get("amt", 0.5)
    if amt <= 0.005:
        return f
    H, W = f.shape[:2]
    X, Y = geo.pt(P["x"], P["y"])
    rx = P.get("rx", 200) * geo.k
    ry = P.get("ry", 200) * geo.k
    x0, x1 = int(max(0, X - 3 * rx)), int(min(W, X + 3 * rx))
    y0, y1 = int(max(0, Y - 3 * ry)), int(min(H, Y + 3 * ry))
    if x1 <= x0 or y1 <= y0:
        return f
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    g = np.exp(-(((xx - X) / rx) ** 2 + ((yy - Y) / ry) ** 2)) * amt * flick(t, P.get("seed", 3), P.get("flick", 2.0))
    col = np.array(P.get("col", [255, 140, 50]), np.float32)
    sub = f[y0:y1, x0:x1]
    if P.get("mode", "mul") == "tint":
        warm = sub * (np.array(P.get("tint", [1.08, 0.80, 0.56]), np.float32)[None, None]) + np.array([18, 4, 0], np.float32)
        sub[:] = sub * (1 - g[..., None]) + warm * g[..., None]
    elif P.get("mode", "mul") == "mul":
        sub[:] = sub * (1 + g[..., None] * (col / 255.0) * P.get("gain", 1.6)) + col * (g * P.get("add", 0.08))[..., None]
    else:
        sub[:] = screen(sub, col * g[..., None])
    return f


def streaks(f, geo, t, P):
    """濡れた石に映る縦の光の筋（刀の火・灯の映り込み）。P: pts（筋の上端の点列。基準座標）、len・w（長さと幅）、amt（keys 可）、
    seed、col。"""
    amt = lerp_keys(P["amt"], t) if isinstance(P.get("amt"), list) else P.get("amt", 0.5)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    S = geo.pts(P["pts"])
    roi = roi_of(S, pad=P.get("len", 120) * geo.k + 10, width=W, height=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    c = np.zeros((y1 - y0, x1 - x0), np.float32)
    rng = np.random.default_rng(P.get("seed", 9))
    for i, (X, Y) in enumerate(S):
        L = P.get("len", 120) * geo.k * rng.uniform(0.6, 1.0)
        wd = max(1, int(P.get("w", 6) * geo.k * rng.uniform(0.6, 1.2)))
        fl = flick(t, i + 3, 2.5)
        for k in range(6):
            yy = Y + L * k / 6.0
            a = (1 - k / 6.0) * fl * rng.uniform(0.5, 1.0)
            jx = math.sin(t * 3 + i + k) * 1.5 * geo.k
            cv2.line(c, (int((X + jx - x0) * 4), int((yy - y0) * 4)), (int((X + jx - x0) * 4), int((yy + L / 6.0 - y0) * 4)), float(a), wd,
                     cv2.LINE_AA, shift=2)
    c = blur(c, 1.2 * geo.k + 0.5)
    sub = f[y0:y1, x0:x1]
    sub[:] = screen(sub, np.array(P.get("col", [255, 150, 70]), np.float32)[None, None] * np.clip(c * amt, 0, 1)[..., None])
    return f


def shadow(f, geo, t, P):
    """足元から伸びる影。P: foot（足元）、to（影の先。基準座標）、len（foot → to の何割まで伸ばすか。keys 可）、w0・w1（根元と
    先の幅）、amt（濃さ）。"""
    ln = lerp_keys(P["len"], t) if isinstance(P.get("len"), list) else P.get("len", 1.0)
    if ln <= 0.01:
        return f
    H, W = f.shape[:2]
    X0, Y0 = geo.pt(*P["foot"])
    X1, Y1 = geo.pt(*P["to"])
    X1 = X0 + (X1 - X0) * ln
    Y1 = Y0 + (Y1 - Y0) * ln
    wd0 = P.get("w0", 40) * geo.k
    wd1 = P.get("w1", 90) * geo.k
    d = np.array([X1 - X0, Y1 - Y0])
    L = np.linalg.norm(d) + 1e-6
    n = np.array([-d[1], d[0]]) / L
    poly = np.array([[X0, Y0] + n * wd0 / 2, [X1, Y1] + n * wd1 / 2, [X1, Y1] - n * wd1 / 2, [X0, Y0] - n * wd0 / 2], np.float32)
    roi = roi_of(poly, pad=30 * geo.k, width=W, height=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    m = np.zeros((y1 - y0, x1 - x0), np.float32)
    cv2.fillPoly(m, [((poly - [x0, y0]) * 4).astype(np.int32)], 1.0, cv2.LINE_AA, shift=2)
    yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    along = np.clip(((xx - X0) * d[0] + (yy - Y0) * d[1]) / (L * L), 0, 1)
    m = blur(m, 6 * geo.k + 1) * (1 - 0.6 * along)
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 - m[..., None] * P.get("amt", 0.4))
    return f


def spread_light(f, geo, t, P):
    """紙の内側に灯が広がる。はじめは紙（明るい所）を暗く平らにし（描かれた灯の明るさの山を消す）、中心から灯が円く広がって元の
    絵に戻る。新しい画を返す。P: x・y（中心）、t0・t1（広がる時刻）、r0・r1（はじめと最後の半径）、edge（縁の柔らかさ）、
    dim（暗い紙の明るさ 0..255）、lum0（紙とみなす明るさ）。"""
    H, W = f.shape[:2]
    X, Y = geo.pt(P["x"], P["y"])
    p = ease((t - P["t0"]) / max(1e-3, P["t1"] - P["t0"]))
    r = P.get("r0", 30) * geo.k + (P.get("r1", 900) * geo.k) * p
    edge = P.get("edge", 140) * geo.k
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    d = np.sqrt((xx - X) ** 2 + ((yy - Y) * 1.15) ** 2)
    nz = tex_win(tex(91, 512, 18, 18), t * 20, 5, H, W)
    band = np.exp(-((d - r) / max(edge, 1.0)) ** 2)
    lit = np.clip((r - d) / max(edge, 1.0) + 0.5 + (nz - 0.5) * 0.9 * band, 0, 1)
    lum = f.mean(-1)
    m = np.clip((lum - P.get("lum0", 60)) / 25.0, 0, 1)                    # 紙（明るい所）。格子の木は暗いので外れる
    sg = max(4.0, 40 * geo.k)
    Lb = blur(lum * m, sg) / np.maximum(blur(m, sg), 1e-3)                  # 紙の上だけで均した、描かれた灯の明るさの山
    pm = blur(m, 1.5)
    unlit = pm * np.clip(P.get("dim", 42) / np.maximum(Lb, 1.0), 0, 1) + (1 - pm)
    fac = unlit * (1 - lit) + lit
    out = f * fac[..., None]
    g = np.exp(-(d / max(1.0, 0.45 * r + 12 * geo.k)) ** 2) * (0.25 + 0.35 * p) * flick(t, 5, 2.0)   # 中心の灯の芯
    return screen(out, np.array([255, 150, 60], np.float32)[None, None] * np.clip(g * pm, 0, 1)[..., None])


def flicker(f, geo, t, P, sc=None):
    """描かれた灯・炎の明るい暖色の所を少し揺らす。新しい画を返す。P: amt、region（x0, y0, x1, y1。基準座標。無ければ画面全体）。
    sc はむらの流れる速さの倍率（既定は画の高さ / 1080。実制作は出力の高さ / 1080）。"""
    amt = P.get("amt", 0.06)
    H, W = f.shape[:2]
    scale = H / 1080.0 if sc is None else sc
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    m = np.clip((r - b - 40) / 80.0, 0, 1) * np.clip((f.mean(-1) - 60) / 80.0, 0, 1)
    if P.get("region"):
        x0, y0 = geo.pt(*P["region"][:2])
        x1, y1 = geo.pt(*P["region"][2:])
        reg = np.zeros((H, W), np.float32)
        reg[int(max(0, y0)):int(max(0, y1)), int(max(0, x0)):int(max(0, x1))] = 1
        m = m * blur(reg, 10)
    nz = tex_win(tex(55, 512, 40, 60), t * 120 * scale, 9, H, W)
    fl = 1 + amt * ((nz - 0.5) * 2.5 + 0.6 * math.sin(t * 11.3) + 0.4 * math.sin(t * 6.1))
    return f * (1 + (fl - 1) * m)[..., None]


def smoke(f, geo, t, P):
    """立ち昇る煙。P: src（根元。基準座標）、w・h（幅と高さ）、amt（keys 可）、speed（昇る速さ）、drift（上ほど横へ流れる割合）、col。"""
    amt = lerp_keys(P["amt"], t) if isinstance(P.get("amt"), list) else P.get("amt", 0.5)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    X, Y = geo.pt(*P["src"])
    wpx = P.get("w", 220) * geo.k
    hpx = P.get("h", 600) * geo.k
    x0, x1 = int(max(0, X - wpx * 1.6)), int(min(W, X + wpx * 1.6))
    y0, y1 = int(max(0, Y - hpx)), int(min(H, Y + 10))
    if x1 <= x0 or y1 <= y0:
        return f
    h, w = y1 - y0, x1 - x0
    T1 = tex(77, 512, sx=max(4.0, wpx * 0.25), sy=max(6.0, wpx * 0.45))
    T2 = tex(78, 512, sx=max(3.0, wpx * 0.12), sy=max(3.0, wpx * 0.2))
    sp = P.get("speed", 60) * geo.k
    n = 0.6 * tex_win(T1, t * sp, 11, h, w) + 0.4 * tex_win(T2, t * sp * 1.3, 37, h, w)
    yy = np.arange(h, dtype=np.float32)[:, None] + y0
    xx = np.arange(w, dtype=np.float32)[None, :] + x0
    v = np.clip((Y - yy) / hpx, 0, 1)                                             # 0 = 根元、1 = 上
    drift = P.get("drift", 0.25) * hpx * v ** 1.5
    spread = wpx * (0.35 + 1.2 * v)
    col_m = np.exp(-((xx - X - drift) / spread) ** 2)
    dens = np.clip((n - 0.42) * 2.6, 0, 1) * col_m * np.clip(v / 0.08, 0, 1) * (1 - v) ** 1.2 * amt
    c = np.array(P.get("col", [120, 125, 140]), np.float32)
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 - dens[..., None] * 0.55) + c[None, None] * (dens[..., None] * 0.55)
    return f


def steel(f, blade, t, P):
    """刃の中に弱い粒と、刃に沿ってゆっくり動く反射を足す（描いた刀を周りの絵の質感に寄せる）。P: grain（粒の強さ）、spec（反射の
    強さ）、speed（反射の速さ。刃の長さ / 秒）、phase（反射の位置のずれ）。"""
    H, W = f.shape[:2]
    S, s, w = blade.points, blade.s, blade.widths
    roi = roi_of(S, pad=w.max() + 4, width=W, height=H)
    if roi is None:
        return f
    x0, y0, x1, y1 = roi
    h, ww = y1 - y0, x1 - x0
    m = np.zeros((h, ww), np.float32)
    band = np.zeros((h, ww), np.float32)
    ph = (t * P.get("speed", 0.12) + P.get("phase", 0.0)) % 1.6 - 0.3          # 反射の位置（0 = 根元、1 = 切っ先）
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
    T = tex(301, 512, sx=1.2, sy=1.2)
    n = (tex_win(T, int(t * 2) * 17, 31, h, ww) - 0.5) * 2.0                # 粒（2 秒ごとに少し変わる）
    sub = f[y0:y1, x0:x1]
    sub[:] = sub * (1 + n[..., None] * P.get("grain", 0.10) * m[..., None])
    band = blur(band, max(1.0, w.mean() * 0.6)) * m
    sub[:] = screen(sub, np.array([235, 240, 255], np.float32)[None, None] * np.clip(band * P.get("spec", 0.35), 0, 1)[..., None])
    return f
