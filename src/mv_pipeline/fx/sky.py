"""空と天気の演出の部品（灯・稲光・雨・空の奥の色）。fx-timeline の hooks から使う（実験的。docs/fx.md）。

fire.py と同じく、**画面の座標**（出力の画素）と**表示の値**（float32 の 0..255、ガンマ符号化）の上で計算し、画 f を返す
（lamps・lightning は f を書き換える。rain・grade は新しい画を返す）。実制作の演出の部品と同じ式・同じ順・同じ型で書いてあり、
同じ入力なら同じ画になる。

- 位置と長さは基準座標（素材ごとの座標系。例えば 1672×941 の画素）で書き、geo で画面へ写す。geo は hooks の
  view.base(幅, 高さ) が返すもの（pt(x, y)・pts(P)・k。実制作の Geo と同じ使い方）。
- 深度（lightning の sky: depth と grade）は**画面の**深度（出力の大きさ、1 = 手前・0 = 奥）で渡す。hooks の view.depth()
  が spec の深度を camera で写したものを返す。
- どれも時刻 t と引数だけで決まる。雨粒の並びは、大きさと seed ごとに 1 回だけ作って持つ（RAIN_LIMIT 通りを超えたら作り直す）。

    lamps(f, geo, t, P)                       灯（灯籠の火袋・窓・遠い点）。点く・消える時刻と揺らぎ
    lightning(f, geo, t, P, screen_depth)     稲光（空の明滅と、枝つきの稲妻の筋）
    rain(f, t, P, sc)                         画面全体の雨（3 層）。光のある所では雨粒が明るく見える
    grade(f, P, screen_depth)                 暗い所・遠い所に薄い色（既定は紫）を足す
"""
import math

import cv2
import numpy as np

from .fire import blur, roi_of, screen

_rain = {}
RAIN_LIMIT = 32            # 作り置く雨粒の並びの数の上限（超えたら作り直す）


# ---- 灯（灯籠の火袋・窓・遠い点） ---------------------------------------------------------------

def flick(t, seed, amt=1.0):
    """明かりの揺らぎの倍率（1 の周りを 3 つの正弦で揺れる。seed で位相をずらす）。"""
    ph = (seed * 1.618) % 6.28
    return 1 + amt * (0.05 * math.sin(t * 9.3 + ph) + 0.035 * math.sin(t * 17.9 + 2 * ph) + 0.025 * math.sin(t * 3.7 + 3 * ph))


def lamp_level(L, t):
    """灯 L の時刻 t の明るさ。on で点き（ramp 秒で上がり、少し揺れて落ち着く）、off があれば off_ramp 秒で消える。amt を掛ける。"""
    on = L.get("on", -1e9)
    if t < on:
        return 0.0
    dur = L.get("ramp", 0.35)
    x = (t - on) / max(dur, 1e-3)
    lv = 1.0 if x >= 1 else (x ** 0.7) * (1 + 0.35 * math.sin(x * 9))
    if "off" in L and t > L["off"]:
        lv *= max(0.0, 1 - (t - L["off"]) / L.get("off_ramp", 0.4))
    return lv * L.get("amt", 1.0)


def lamps(f, geo, t, P):
    """灯をまとめて描く。P["lamps"] の各要素:

    x・y（光の中心）、r（光の半径）、kind（dot・box・window）、box（x0, y0, x1, y1。box・window の火袋や窓の範囲）、
    on・ramp・off・off_ramp・amt（lamp_level）、flick（揺らぎの強さ）、col、gain・fill・feather（box・window の紙の光り方）、
    core・halo・core_amt（にじみの芯の大きさと、外と芯の強さ）。位置と長さは基準座標。P の amt と col は全部の灯に掛かる。"""
    H, W = f.shape[:2]
    gcol = np.array(P.get("col", [255, 150, 60]), np.float32)
    for j, L in enumerate(P["lamps"]):
        lv = lamp_level(L, t) * P.get("amt", 1.0)
        if lv <= 0.01:
            continue
        lv *= flick(t, j + 7, L.get("flick", 1.0))
        col = np.array(L.get("col", gcol), np.float32)
        X, Y = geo.pt(L["x"], L["y"])
        r = L.get("r", 20) * geo.k
        kind = L.get("kind", "dot")
        if kind in ("box", "window") and "box" in L:
            bx0, by0 = geo.pt(L["box"][0], L["box"][1])
            bx1, by1 = geo.pt(L["box"][2], L["box"][3])
            x0, y0, x1, y1 = int(max(0, bx0 - 2)), int(max(0, by0 - 2)), int(min(W, bx1 + 3)), int(min(H, by1 + 3))
            if x1 > x0 and y1 > y0:
                yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
                fe = max(1.0, L.get("feather", 1.5) * geo.k)
                m = np.clip((xx - bx0) / fe, 0, 1) * np.clip((bx1 - xx) / fe, 0, 1) * np.clip((yy - by0) / fe, 0, 1) * np.clip((by1 - yy) / fe, 0, 1)
                # 紙（明るい所）ほど強く光る: 絵の明るさに光を掛ける。格子（暗い所）は暗いまま
                sub = f[y0:y1, x0:x1]
                lum = sub.mean(-1, keepdims=True) / 255.0
                gain = np.array(L.get("gain", [5.0, 3.1, 1.4]), np.float32) * lv
                lit = sub * (1 + gain[None, None] * m[..., None]) + col[None, None] * (m * lv * L.get("fill", 0.35) * (0.4 + lum[..., 0]))[..., None]
                sub[:] = np.clip(lit, 0, 255)
        # 光のにじみ（芯と外）
        cr = max(1.0, L.get("core", 0.25) * r)
        x0, y0, x1, y1 = int(max(0, X - 3 * r)), int(max(0, Y - 3 * r)), int(min(W, X + 3 * r + 1)), int(min(H, Y + 3 * r + 1))
        if x1 <= x0 or y1 <= y0:
            continue
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float32)
        d2 = (xx - X) ** 2 + (yy - Y) ** 2
        halo = np.exp(-d2 / (2 * r * r)) * L.get("halo", 0.45) * lv
        core = np.exp(-d2 / (2 * cr * cr)) * L.get("core_amt", 0.9 if kind == "dot" else 0.25) * lv
        sub = f[y0:y1, x0:x1]
        sub[:] = screen(sub, col[None, None] * np.clip(halo, 0, 1)[..., None])
        sub[:] = screen(sub, np.array([255, 236, 190], np.float32)[None, None] * np.clip(core, 0, 1)[..., None])
    return f


# ---- 稲光 ------------------------------------------------------------------------------------

def bolt_path(p0, p1, seed, rough=0.22, depth=6, branch=0.5):
    """画面の点 p0 → p1 の稲妻の折れ線（中点をずらして depth 回分ける）と枝。戻り値は [(点列 (n, 2), 太さの比)]。"""
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
    for _ in range(nb):
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


def lightning(f, geo, t, P, screen_depth=None):
    """稲光。P: t0（光る時刻）、pulses（[[t0 からの秒, 強さ], ...] の明滅。既定 4 回）、decay（1 回の明滅の減り方の秒）、
    tail（最後の明滅の後に見る秒）、flash（空の明るさ）、sky（空を明るくする範囲: all・top・depth）、top（top の範囲。画面の上から
    の割合）、far（depth の範囲。この深さより奥）、center・radius（光源の近くほど明るい）、lift（全体を少し照らす）、col、
    p0・p1（稲妻の始点と終点）、bolt（稲妻の明るさ）、width、seed、rough、depth（稲妻の折れの細かさ）。位置と長さは基準座標。
    screen_depth は画面の深度（sky: depth に使う。None なら top と同じ）。"""
    t0 = P["t0"]
    pulses = P.get("pulses", [[0.0, 1.0], [0.09, 0.45], [0.2, 0.8], [0.42, 0.25]])
    if t < t0 - 0.01 or t > t0 + pulses[-1][0] + P.get("tail", 0.6):
        return f
    lv = 0.0
    for dt, a in pulses:
        x = t - (t0 + dt)
        if x >= 0:
            lv = max(lv, a * math.exp(-x / P.get("decay", 0.07)))
    if lv <= 0.005:
        return f
    H, W = f.shape[:2]
    col = np.array(P.get("col", [175, 120, 255]), np.float32)
    # 空の明滅
    fl = P.get("flash", 0.5) * lv
    if fl > 0.004:
        mode = P.get("sky", "top")
        if mode == "all":
            m = np.ones((H, W), np.float32)
        elif mode == "depth" and screen_depth is not None:
            m = np.clip((P.get("far", 0.32) - screen_depth) / 0.18, 0, 1)
        else:
            yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
            m = np.clip((P.get("top", 0.55) - yy) / 0.35, 0, 1) * np.ones((1, W), np.float32)
        if "center" in P:                                                      # 光源の近くほど明るい
            cx, cy = geo.pt(*P["center"])
            rr = P.get("radius", 600) * geo.k
            yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
            m = m * (0.25 + 0.75 * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * rr * rr)))
        lum = f.mean(-1, keepdims=True)
        f[:] = f + (col[None, None] * 0.55 + lum * 0.9) * (m * fl)[..., None]
        if P.get("lift", 0) > 0:                                               # 全体を少し照らす（地形と道を一瞬見せる）
            f[:] = f * (1 + P["lift"] * lv) + (col / 255.0)[None, None] * (P["lift"] * lv * 20)
    # 稲妻の筋
    bv = P.get("bolt", 1.0) * lv
    if bv > 0.01 and "p0" in P:
        paths = bolt_path(geo.pt(*P["p0"]), geo.pt(*P["p1"]), P.get("seed", 1), P.get("rough", 0.22), P.get("depth", 6))
        allp = np.concatenate([p for p, _ in paths])
        roi = roi_of(allp.astype(np.float32), pad=30 * geo.k + 10, width=W, height=H)
        if roi is not None:
            x0, y0, x1, y1 = roi
            c = np.zeros((y1 - y0, x1 - x0), np.float32)
            wd = P.get("width", 2.2) * geo.k
            for p, wr in paths:
                q = ((p - [x0, y0]) * 4).astype(np.int32)
                cv2.polylines(c, [q], False, float(wr), max(1, int(round(wd * wr))), cv2.LINE_AA, shift=2)
            sub = f[y0:y1, x0:x1]
            sub[:] = screen(sub, col[None, None] * np.clip(blur(c, 10 * geo.k) * 2.2 * bv, 0, 1)[..., None])
            sub[:] = screen(sub, np.array([245, 230, 255], np.float32)[None, None] * np.clip(blur(c, 0.7) * 1.6 * bv, 0, 1)[..., None])
    return f


# ---- 雨 -------------------------------------------------------------------------------------

def rain(f, t, P, sc=None):
    """画面全体の雨（3 層: 遠い細かい・中・近い長い）。光のある所では雨粒が光を拾って明るく見える。新しい画を返す。

    P: amt、density（本数の倍率）、angle（縦 1 に対する横の流れ）、layers（[[本数, 速さ, 長さ, 明るさ, 太さ], ...]。
    本数は 960×540 あたり、速さと長さは高さ 1080 の画素）、seed、col（雨の色）、ambient（雨そのものの明るさ）、catch（光の拾い方）。
    sc は長さと速さの倍率（既定は画の高さ / 1080。実制作は出力の高さ / 1080）。"""
    amt = P.get("amt", 1.0)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    scale = H / 1080.0 if sc is None else sc
    small = cv2.resize(f, (max(1, W // 8), max(1, H // 8)), interpolation=cv2.INTER_AREA)
    light = cv2.resize(cv2.GaussianBlur(small, (0, 0), 2.0), (W, H), interpolation=cv2.INTER_LINEAR)
    c = np.zeros((H, W), np.float32)
    ang = P.get("angle", -0.13)                       # 横の流れ（縦 1 に対して）
    layers = P.get("layers", [[260, 1500, 14, 0.30, 1], [140, 2100, 26, 0.45, 1], [45, 2800, 54, 0.60, 2]])
    for li, (n, vy, ln, br, th) in enumerate(layers):
        n = int(n * P.get("density", 1.0) * (W * H) / (960 * 540))
        key = (li, n, W, H, P.get("seed", 3))
        if key not in _rain:
            if len(_rain) >= RAIN_LIMIT:
                _rain.clear()
            rng = np.random.default_rng(P.get("seed", 3) * 31 + li)
            _rain[key] = (rng.random(n) * (W + H), rng.random(n) * H * 1.3, rng.uniform(0.6, 1.0, n), rng.uniform(0.85, 1.15, n))
        xs, ys, bs, vs = _rain[key]
        v = vy * scale * vs
        L = ln * scale * vs
        Y = (ys + v * t) % (H * 1.3) - H * 0.15
        X = xs - H * 0.5 + ang * Y
        for x, y, b, l in zip(X, Y, bs, L):
            if -l < y < H + l and -20 < x < W + 20:
                cv2.line(c, (int(x * 4), int(y * 4)), (int((x - ang * l) * 4), int((y - l) * 4)), float(b * br),
                         max(1, int(th * max(1.0, scale))), cv2.LINE_AA, shift=2)
    c = cv2.GaussianBlur(c, (0, 0), 0.6 * max(1.0, scale))
    base = np.array(P.get("col", [150, 165, 200]), np.float32)
    add = (base[None, None] * P.get("ambient", 0.22) + light * P.get("catch", 1.6)) * (c * amt)[..., None]
    return f + add


# ---- 空の奥の色 ---------------------------------------------------------------------------------

def grade(f, P, screen_depth=None):
    """暗い所・遠い所に薄い色を足す。新しい画を返す。P: amt、tint（足す色。既定は紫 [10, -2, 22]）、far（screen_depth が
    あるとき、この深さより奥）、top（深度が無いとき、画面の上から何割までを空とみなすか）。明るい所（輝度 140 以上）には足さない。"""
    amt = P.get("amt", 1.0)
    if amt <= 0.01:
        return f
    H, W = f.shape[:2]
    if screen_depth is not None:
        m = np.clip((P.get("far", 0.30) - screen_depth) / 0.22, 0, 1)
    else:
        yy = np.linspace(0, 1, H, dtype=np.float32)[:, None]
        m = np.clip((P.get("top", 0.45) - yy) / 0.3, 0, 1) * np.ones((1, W), np.float32)
    lum = f.mean(-1)
    m = m * np.clip(1 - lum / 140.0, 0, 1)
    tint = np.array(P.get("tint", [10, -2, 22]), np.float32)
    return f + tint[None, None] * (m * amt)[..., None]
