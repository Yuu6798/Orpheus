"""粒が集まって形になる演出の部品。fx-timeline の hooks から使う（実験的。docs/fx.md）。

fire.py・sky.py・accent.py と同じく、**画面の座標**（出力の画素。画素の中心が整数）と**表示の値**（float32 の 0..255、ガンマ符号化）の
上で計算し、画 f を書き換えて返す（f は float32 (H, W, 3)。hooks の shot・final に渡る画）。重ね方はスクリーン合成。
字や形そのものは描かない（書体の依存を持たない）。透過の題字カードなどのアルファ（2 次元の配列）から点を取り、その点へ粒を集める。

- 状態を持たない。粒 i の位置と光は (t, i, seed) と引数だけの式で、区間を分けて描いても、静止画でも同じ画になる。
- 乱数は seed から作る（timeline.seeded_rng）。粒ごとの乱数は種類ごとに別の系列なので、点を後ろに足しても前の粒は変わらない。
- 位置（points・origin の点）は画面の画素。長さ（size・glow・swirl・lift・rise）は画素に k を掛けたもの（k は view.px や geo.k。既定 1）。

    targets(alpha, n, seed)                 アルファの不透明な所から、面積に比例して n 個の点（その配列の画素座標）
    gather(f, t, P, points, k)              時刻 t の粒を f に重ねる（points は画面の画素の行き先）
    flight(t, P, points, frame_size, k)     時刻 t の粒の位置と光（描く前の値）。gather はこれを splat で描く
    splat(f, xy, rgb, size, glow, gain)     粒を f に重ねる（芯と光彩）

粒 i の式（d・w・b などは粒ごとの乱数、s は出どころ、g は行き先、T = t1 − t0、p = (t − t0)/T、τ = t − t0）:

    u    = clamp((p − d·stagger) / (1 − stagger), 0, 1)       粒ごとに d·stagger·T だけ遅れて出て、(1 − stagger)·T で着く。t1 で全部が着く
                                                              （p は t0 より前で 0、t1 からは 1）
    e    = u²·(3 − 2u)                                        出だしと着く所でゆっくり
    位置 = s·(1 − e) + g·e                                    e = 0 で出どころ、e = 1 で行き先ちょうど
         + swirl·k·w·(1 − e)·(cos θ, sin θ)                   渦。θ = 位相 + 2π·回転数·τ（どの粒も同じ向きに回る）。着くと 0
         − (0, lift·k·sin(π·e))                               弧。途中（e = 0.5）で lift だけ上がる
    色   = colors[j]·(1 − e) + settle·e                       j は粒ごとに決まる番号
    光   = 色·b·(1 − twinkle·(1 + cos(φ + 2π·hz·τ))/2)·出方    b は粒ごとの明るさ（BRIGHT の間）

出方は、t < t0 − lead は 0（描かない）、t0 − lead から t0 へ直線で 0 → 1（その間、粒は出どころで待つ。渦があれば回りながら）、t0 からは 1。
after があれば、t1 を過ぎると粒ごとに少しずつ遅れて上へ流れ（rise·k 画素/秒）、fade_s 秒で 0 まで薄れる。無ければ着いたまま。

描き方（splat）: 粒の光を 4 つの画素に分けて足し（位置が画素の間でもなめらかに動く）、size のガウスでぼかして芯にする（1 粒の山が
その粒の光になるようにそろえる）。芯を 255 で止めて glow のガウスでぼかしたものを HALO 倍して光彩にする。芯、光彩の順にスクリーン合成する。
粒が重なる所は光が足し合わさり、白に近づく。
"""
import math

import numpy as np

from ..io import PipelineError
from . import timeline
from .fire import blur, params_at, screen

EMBER = (255, 200, 120)       # fire.embers の火の粉の色
CORE = (255, 236, 190)        # sky.lamps の灯の芯の色
FLAME = (255, 125, 35)        # fire.candle の炎の光の色（burn の縁の既定と同じ）
COLORS = (EMBER, CORE, FLAME)  # colors の既定
SETTLE = EMBER                # settle の既定
SIZE = 1.6                    # size の既定（画素）。fire.embers の粒の半径 1.0〜2.2 の中央
GLOW = 3.0                    # glow の既定（画素）。fire.embers の光彩のぼかしと同じ
RISE = 110.0                  # after.rise の既定（画素/秒）。fire.embers の昇る速さ 60〜160 の中央
FADE_S = 1.0                  # after.fade_s の既定（秒）。fire.embers の粒の寿命 0.6〜1.4 の中央
STAGGER = 0.5                 # stagger の既定（仮の値。合成素材で決めた）
MAX_STAGGER = 0.95            # stagger の上限（1 だと飛ぶ時間が 0 になる）
BRIGHT = (0.6, 1.0)           # 粒ごとの明るさ（sky.rain の雨粒の明るさと同じ幅）
THRESHOLD = 0.5               # targets が不透明とみなすアルファ（0..1）
JITTER = 0.98                 # targets が画素の中で点をずらす幅（画素。1 だと丸めで隣の画素に入る）
BELOW = 0.25                  # origin "below" の帯の深さ（画面の高さの割合。仮の値）
SWIRL_TURNS = (0.25, 0.75)    # 渦の速さ（1 秒あたりの回転数。仮の値）
SWIRL_REACH = (0.5, 1.0)      # 渦の半径の粒ごとの倍率（仮の値）
TWINKLE_HZ = (1.0, 3.0)       # またたきの速さ（1 秒あたりの回数。仮の値）
RELEASE = 0.5                 # after: 粒が上へ流れ始める時刻のばらつき（fade_s に対する割合。仮の値）
RISE_SPREAD = (0.5, 1.5)      # after: 昇る速さの粒ごとの倍率（仮の値）
SWAY = 0.2                    # after: 昇った高さに対する横の揺れの割合（仮の値）
HALO = 0.5                    # 光彩の強さ（芯をぼかしたものに掛ける。仮の値）
REACH = 4.0                   # ぼかしの届く幅（ガウスの σ の倍数。その外の粒は描かない）
GRID = 4                      # 描く範囲をそろえる升目（fire.blur が縮めてぼかす倍率の最大。升目がそろうと、粒が動いても光彩が揺れない）


def _rng(seed, label):
    """seed と名前で決まる乱数の系列。前から順に作るので、数を増やしても前の値は変わらない。"""
    try:
        valid = int(seed) == seed and seed >= 0
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise PipelineError(f"particles の seed は 0 以上の整数にしてください: {seed!r}")
    return timeline.seeded_rng(int(seed), "particles:" + label)


def _rand(seed, label, shape):
    """0..1 の乱数（float64）。"""
    return _rng(seed, label).random(shape)


def _span(pair, x):
    return pair[0] + (pair[1] - pair[0]) * x


# ---- 行き先（アルファから点を取る） -------------------------------------------------------------

def targets(alpha, n, seed, threshold=THRESHOLD):
    """アルファ（2 次元の配列。大きさは任意）の不透明な所から n 個の点を取る。戻り値は float32 (n, 2) の (x, y)。

    座標はその配列の画素（画素の中心が整数）。画面と同じ大きさのカードならそのまま gather の points に渡せる。違う大きさなら
    (点 + 0.5) / [幅, 高さ] で正規化座標にして、view.points などで画面へ写す。

    - アルファは 0..1 か 0..255。最大値が 1 を超えていれば 0..255 として読む（真偽の配列は 0・1）。threshold（0..1）以上が不透明。
    - 面積に比例して取る: 不透明な画素を seed で決まる順に並べ、前から n 個を使う（n が画素の数より多ければ、同じ順でもう一巡する）。
      どの画素も同じ回数（差は 1 回まで）使うので、字の太い所にも細い所にも面積のとおりに点が乗る。点は画素の中で少しずらす。
    - 同じ引数なら同じ点。n を増やしても、前の点は変わらない。不透明な画素が無ければ PipelineError。"""
    a = np.asarray(alpha)
    if a.ndim != 2 or a.size == 0:
        raise PipelineError(f"particles.targets のアルファは 2 次元の配列 (H, W) で渡してください（渡した形: {a.shape}）")
    count = int(n)
    if count < 0:
        raise PipelineError(f"particles.targets の n は 0 以上にしてください: {n}")
    a = a.astype(np.float32)
    if not np.isfinite(a).all():
        raise PipelineError("particles.targets のアルファに NaN か無限大があります")
    peak = float(a.max())
    if peak > 255.0:
        raise PipelineError(f"particles.targets のアルファは 0..1 か 0..255 で渡してください（最大値 {peak:g}）")
    top = 255.0 if peak > 1.0 else 1.0
    ys, xs = np.nonzero(a >= float(threshold) * top)
    if len(xs) == 0:
        raise PipelineError(f"particles.targets のアルファに不透明な画素がありません（最大値 {peak:g} を 0..{top:g} の値として読み、"
                            f"{float(threshold) * top:g} 以上を探しました）。カードのアルファか threshold を確かめてください")
    order = _rng(seed, "targets").permutation(len(xs))
    pick = order[np.arange(count) % len(xs)]
    jitter = (_rand(seed, "jitter", (count, 2)) - 0.5) * JITTER
    return (np.stack([xs[pick], ys[pick]], 1) + jitter).astype(np.float32)


# ---- 粒の動き ------------------------------------------------------------------------------

def _origin(origin, n, frame_size, seed):
    """出どころ（float64 (n, 2)）。"ring"・"below"・画面の画素の点の集まり。"""
    width, height = frame_size
    if isinstance(origin, str):
        if origin == "ring":        # 画面に内接する楕円と、四隅を通る楕円の間（半径の倍率 1〜√2）
            angle = 2 * math.pi * _rand(seed, "origin:angle", n)
            rho = 1.0 + (math.sqrt(2.0) - 1.0) * _rand(seed, "origin:radius", n)
            return np.stack([(width - 1) / 2 + np.cos(angle) * rho * width / 2, (height - 1) / 2 + np.sin(angle) * rho * height / 2], 1)
        if origin == "below":       # 画面の下の縁の外（幅いっぱい、深さは高さの BELOW 倍）
            return np.stack([-0.5 + width * _rand(seed, "origin:x", n), height - 0.5 + BELOW * height * _rand(seed, "origin:y", n)], 1)
        raise PipelineError(f'particles.gather の origin は "ring"・"below"・[[x, y], ...] のどれかにしてください: {origin!r}')
    spots = np.asarray(origin, np.float64)
    if spots.ndim != 2 or spots.shape[1] != 2 or len(spots) == 0 or not np.isfinite(spots).all():
        raise PipelineError(f"particles.gather の origin の点は [[x, y], ...]（1 個以上。画面の画素）で渡してください（渡した形: {spots.shape}）")
    return spots[_rng(seed, "origin:pick").integers(0, len(spots), n)]


def _points(points):
    target = np.asarray(points, np.float64)
    if target.size == 0:
        return target.reshape(0, 2)
    if target.ndim != 2 or target.shape[1] != 2 or not np.isfinite(target).all():
        raise PipelineError(f"particles.gather の points は (n, 2) の (x, y)（画面の画素）で渡してください（渡した形: {target.shape}）")
    return target


def flight(t, P, points, frame_size, k=1.0):
    """時刻 t の粒の位置と光。戻り値は (xy, rgb)（float32 の (n, 2) と (n, 3)。rgb は色 × 明るさで、gain はまだ掛けない）。
    描く粒が無い時刻（集まり始める前・薄れ切った後）と、points が空のときは None。frame_size は画面の (幅, 高さ)。P は gather と同じ。"""
    P = params_at(P, t)
    if "t0" not in P or "t1" not in P:
        raise PipelineError("particles.gather の P には t0 と t1（集まり始めと集まり終わりの時刻）が要ります")
    target = _points(points)
    n = len(target)
    if n == 0:
        return None
    t0, t1 = float(P["t0"]), float(P["t1"])
    if t1 < t0:
        raise PipelineError(f"particles.gather の t1 は t0 以降にしてください（t0 {t0:g}・t1 {t1:g}）")
    after, past = P.get("after"), t - t1
    if after is not None and not isinstance(after, dict):
        raise PipelineError(f'particles.gather の after は {{"rise": 画素/秒, "fade_s": 秒}} の形にしてください: {after!r}')
    lead = max(0.0, float(P.get("lead", 0.0)))
    if t < t0 - lead:
        return None
    level = 1.0 if t >= t0 else (t - (t0 - lead)) / lead
    leaving = after is not None and past > 0
    if leaving:
        fade_s = float(after.get("fade_s", FADE_S))
        level *= max(0.0, 1.0 - past / fade_s) if fade_s > 0 else 0.0
    if level <= 0:
        return None
    seed = P.get("seed", 0)
    tau = t - t0
    stagger = min(max(float(P.get("stagger", STAGGER)), 0.0), MAX_STAGGER)
    p = 1.0 if t >= t1 else 0.0 if t <= t0 else (t - t0) / (t1 - t0)       # t1 からは着いている（t0 と t1 が同じでも）
    u = np.clip((p - _rand(seed, "delay", n) * stagger) / (1.0 - stagger), 0.0, 1.0)
    e = u * u * (3.0 - 2.0 * u)
    xy = _origin(P.get("origin", "ring"), n, frame_size, seed) * (1.0 - e)[:, None] + target * e[:, None]
    turns = _span(SWIRL_TURNS, _rand(seed, "turns", n))
    phase = 2 * math.pi * _rand(seed, "phase", n)
    swirl = float(P.get("swirl", 0.0)) * k
    if swirl != 0:
        radius = swirl * _span(SWIRL_REACH, _rand(seed, "reach", n)) * (1.0 - e)
        angle = phase + 2 * math.pi * turns * tau
        xy[:, 0] += radius * np.cos(angle)
        xy[:, 1] += radius * np.sin(angle)
    lift = float(P.get("lift", 0.0)) * k
    if lift != 0:
        xy[:, 1] -= lift * np.sin(math.pi * e)
    if leaving:
        speed = float(after.get("rise", RISE)) * k * _span(RISE_SPREAD, _rand(seed, "rise", n))
        risen = speed * np.clip(past - _rand(seed, "release", n) * RELEASE * fade_s, 0.0, None)
        xy[:, 1] -= risen
        xy[:, 0] += SWAY * risen * np.sin(phase + 2 * math.pi * turns * past)
    colors = np.asarray(P.get("colors", COLORS), np.float64).reshape(-1, 3)
    if len(colors) == 0:
        raise PipelineError("particles.gather の colors は 1 色以上にしてください")
    settle = np.asarray(P.get("settle", SETTLE), np.float64).reshape(3)
    color = colors[_rng(seed, "color").integers(0, len(colors), n)] * (1.0 - e)[:, None] + settle * e[:, None]
    light = _span(BRIGHT, _rand(seed, "bright", n)) * level
    twinkle = float(P.get("twinkle", 0.0))
    if twinkle != 0:
        blink = 2 * math.pi * (_rand(seed, "twinkle", n) + _span(TWINKLE_HZ, _rand(seed, "hz", n)) * tau)
        light = light * (1.0 - twinkle * 0.5 * (1.0 + np.cos(blink)))
    return xy.astype(np.float32), (color * np.clip(light, 0.0, None)[:, None]).astype(np.float32)


# ---- 描く --------------------------------------------------------------------------------

def splat(f, xy, rgb, size=SIZE, glow=GLOW, gain=1.0):
    """粒（xy: 画面の画素の (n, 2)、rgb: 粒ごとの光の (n, 3)）を f にスクリーン合成で重ねる。size は芯、glow は光彩のぼかしの幅（画素。
    ガウスの σ。glow が 0 以下なら光彩なし）。gain が 0 以下か、画面に届く粒が無ければ f をそのまま返す。"""
    gain = float(gain)
    xy, rgb = np.asarray(xy, np.float64).reshape(-1, 2), np.asarray(rgb, np.float64).reshape(-1, 3)
    if gain <= 0 or len(xy) == 0:
        return f
    height, width = f.shape[:2]
    core, halo = max(0.3, float(size)), max(0.0, float(glow))
    pad = GRID * int(math.ceil((REACH * max(core, halo) + GRID + 2) / GRID))
    x, y = xy[:, 0], xy[:, 1]
    keep = (np.isfinite(xy).all(1) & (rgb.max(1) > 0) & (x > 1 - pad) & (x < width + pad - 2) & (y > 1 - pad) & (y < height + pad - 2))
    if not keep.any():
        return f
    x, y, rgb = x[keep], y[keep], rgb[keep]
    # 描く範囲: 粒の周り pad。画面の外 pad までで切り、升目（GRID）にそろえる
    left = max(-pad, GRID * int(math.floor((math.floor(x.min()) - pad) / GRID)))
    top = max(-pad, GRID * int(math.floor((math.floor(y.min()) - pad) / GRID)))
    right = GRID * int(math.ceil(min(width + pad, math.ceil(x.max()) + 1 + pad) / GRID))
    bottom = GRID * int(math.ceil(min(height + pad, math.ceil(y.max()) + 1 + pad) / GRID))
    w, h = right - left, bottom - top
    column, row = np.floor(x).astype(np.int64), np.floor(y).astype(np.int64)
    fx, fy = x - column, y - row
    index, weight = [], []
    for dy, wy in ((0, 1.0 - fy), (1, fy)):
        for dx, wx in ((0, 1.0 - fx), (1, fx)):
            cell = ((row + dy - top) * w + (column + dx - left)) * 3
            for channel in range(3):
                index.append(cell + channel)
                weight.append(wy * wx * rgb[:, channel])
    energy = np.bincount(np.concatenate(index), weights=np.concatenate(weight), minlength=h * w * 3)
    dots = blur(energy.reshape(h, w, 3).astype(np.float32), core) * np.float32(max(1.0, 2 * math.pi * core * core))
    x0, y0, x1, y1 = max(left, 0), max(top, 0), min(right, width), min(bottom, height)
    if x1 <= x0 or y1 <= y0:
        return f
    inside = (slice(y0 - top, y1 - top), slice(x0 - left, x1 - left))
    sub = f[y0:y1, x0:x1]
    sub[:] = screen(sub, dots[inside] * np.float32(gain))
    if halo > 0:
        sub[:] = screen(sub, blur(np.minimum(dots, np.float32(255.0)), halo)[inside] * np.float32(HALO * gain))
    return f


def gather(f, t, P, points, k=1.0):
    """時刻 t の粒を f に重ねて返す（f を書き換える。形と型は変わらない）。points は行き先（画面の画素の (n, 2)。targets の点や
    view.points(...) をそのまま渡せる）。k は長さに掛ける倍率（view.px や geo.k。位置には掛けない）。

    P（t0・t1 だけが必須。数の値は keys（名前 → [[t, 値], ...]）で時刻ごとに補間できる。fire.params_at と同じ書き方）:
      t0・t1   集まり始めと集まり終わりの時刻。t1 で全部の粒が行き先に着く
      stagger  粒ごとの出発の遅れのばらつき（0〜0.95。t1 − t0 に対する割合。0 なら全部が同時に動く）
      origin   出どころ。"ring"（画面の外周の輪）・"below"（画面の下の外）・[[x, y], ...]（画面の画素の点。粒ごとにどれか 1 つ）
      swirl    渦の振れ幅（画素。着くと 0）      lift  途中で弧を描く高さ（画素。+ で上）
      colors   粒ごとに割り当てる色の一覧       settle  着いたときの色（飛ぶ間に colors から寄っていく）
      size・glow   芯と光彩の太さ（画素。ガウスの σ）     gain  明るさ（0 以下なら f を変えない）
      twinkle  またたき（0〜1。粒ごとに明るさが 1 − twinkle まで落ちる）
      lead     t0 の前に出どころで待つ秒（その間に 0 から明るくなる。0 なら t0 まで描かない）
      after    着いた後の動き {"rise": 画素/秒, "fade_s": 秒}。t1 から上へ流れて薄れる（無ければ着いたまま）
      seed     乱数の種"""
    P = params_at(P, t)
    gain = float(P.get("gain", 1.0))
    if gain <= 0:
        return f
    state = flight(t, P, points, (f.shape[1], f.shape[0]), k)
    if state is None:
        return f
    return splat(f, state[0], state[1], float(P.get("size", SIZE)) * k, float(P.get("glow", GLOW)) * k, gain)
