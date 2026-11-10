"""粒が集まって形になる部品（fx/particles.py）と、その見本の hooks（examples/fx/particles_hooks.py）。

素材はすべて合成（輪や四角のアルファと乱数の画）。粒の式は、fx/particles.py の冒頭と docs/fx.md に書いたものを、粒 1 つずつ
Python の数で計算した写し（ref_particle）と比べる。
"""
import inspect
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import camera, fire, particles, sequence, sky  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx import timeline as fxtimeline  # noqa: E402
from mv_pipeline.fx.hooks import Hooks  # noqa: E402
from mv_pipeline.fx.view import View  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
SIZE = (240, 160)                    # 合成の画の (幅, 高さ)
WINDOW = {"t0": 1.0, "t1": 3.0}      # 集まる時刻（ほかは既定値）


def ring_alpha(size=SIZE, r0=28, r1=40):
    """輪の形のアルファ（0..1）。中心は画面の中央。"""
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    distance = np.hypot(xx - (width - 1) / 2, yy - (height - 1) / 2)
    return ((distance >= r0) & (distance <= r1)).astype(np.float32)


def night(size=SIZE, seed=0):
    rng = np.random.default_rng(seed)
    return rng.uniform(5, 90, (size[1], size[0], 3)).astype(np.float32)


def grown(mask, px):
    """形を px 画素だけ太らせた範囲（真偽）。"""
    return cv2.dilate(mask.astype(np.uint8), np.ones((2 * px + 1, 2 * px + 1), np.uint8)) > 0


def added(t, P, points, k=1.0, seed=0):
    """粒が足した光（画素ごと。3 色の和）。"""
    base = night(seed=seed)
    return (particles.gather(base.copy(), t, P, points, k) - base).sum(-1)


def share_inside(light, region):
    return float(light[region].sum() / light.sum())


# ---- 行き先（アルファから点を取る） -------------------------------------------------------------

def two_blocks():
    """左に 20×30（600 画素）、右に 60×30（1800 画素）の不透明な四角。面積の比は 1 : 3。"""
    alpha = np.zeros((50, 120), np.float32)
    alpha[10:40, 10:30] = 1.0
    alpha[10:40, 50:110] = 1.0
    return alpha


def pixels_of(points):
    """点を含む画素（列, 行）。画素の中心が整数。"""
    return np.floor(points + 0.5).astype(int)


def test_targets_sit_on_opaque_pixels_in_proportion_to_the_area():
    alpha = two_blocks()
    points = particles.targets(alpha, 800, 3)
    assert points.shape == (800, 2) and points.dtype == np.float32
    column, row = pixels_of(points).T
    assert np.all(alpha[row, column] == 1.0)                                # 全部の点が不透明な画素の上
    left, right = int((column < 40).sum()), int((column >= 40).sum())
    assert left + right == 800 and left / right == pytest.approx(600 / 1800, rel=0.1), (left, right)
    assert len({(c, r) for c, r in zip(column, row)}) == 800                # 画素の数より少なければ、同じ画素を 2 度使わない
    # 画素の数より多ければ、どの画素も同じ回数（差は 1 回まで）使う。ちょうど 2 巡なら、点の数の比が面積の比と同じになる
    column, row = pixels_of(particles.targets(alpha, 4800, 3)).T
    assert np.all(np.bincount(row * 120 + column, minlength=50 * 120)[alpha.ravel() == 1.0] == 2)
    assert int((column < 40).sum()) * 3 == int((column >= 40).sum())
    used = np.bincount(np.ravel_multi_index(pixels_of(particles.targets(alpha, 2400 + 7, 3)).T[::-1], alpha.shape), minlength=alpha.size)
    assert sorted(set(used[alpha.ravel() == 1.0])) == [1, 2] and int((used == 2).sum()) == 7


def test_targets_depend_only_on_the_arguments():
    alpha = two_blocks()
    first = particles.targets(alpha, 300, 5)
    assert np.array_equal(first, particles.targets(alpha.copy(), 300, 5))
    assert not np.array_equal(first, particles.targets(alpha, 300, 6))
    assert np.array_equal(particles.targets(alpha, 900, 5)[:300], first)    # n を増やしても、前の点は変わらない
    assert np.array_equal(first, particles.targets((alpha * 255).astype(np.uint8), 300, 5))      # 0..255 でも同じ点
    assert np.array_equal(first, particles.targets(alpha > 0, 300, 5))      # 真偽でも同じ点
    assert particles.targets(alpha, 0, 5).shape == (0, 2)


def test_targets_threshold_decides_what_is_opaque():
    alpha = np.zeros((20, 30), np.float32)
    alpha[5:15, 5:15] = 1.0
    alpha[5:15, 15:25] = 0.4                                                # 半透明の縁
    column = pixels_of(particles.targets(alpha, 400, 1))[:, 0]
    assert column.max() < 15                                                # 既定（0.5 以上）では半透明の所に点を置かない
    column = pixels_of(particles.targets(alpha, 400, 1, threshold=0.3))[:, 0]
    assert column.max() >= 15
    scaled = pixels_of(particles.targets(alpha * 255, 400, 1, threshold=0.3))[:, 0]
    assert np.array_equal(scaled, column)                                   # threshold は 0..1 で書く（0..255 のアルファでも同じ）


@pytest.mark.parametrize("alpha, n, seed, message", [
    (np.zeros((20, 30), np.float32), 10, 0, "不透明な画素がありません"),
    (np.full((20, 30), 0.4, np.float32), 10, 0, "0..1 の値として読み"),
    (np.full((20, 30), 100, np.uint8), 10, 0, "0..255 の値として読み"),
    (np.ones((20, 30, 4), np.float32), 10, 0, "2 次元の配列"),
    (np.ones((0, 30), np.float32), 10, 0, "2 次元の配列"),
    (np.full((20, 30), np.nan, np.float32), 10, 0, "NaN"),
    (np.full((20, 30), 65535, np.uint16), 10, 0, "0..1 か 0..255"),
    (np.ones((20, 30), np.float32), -1, 0, "0 以上"),
    (np.ones((20, 30), np.float32), 10, -2, "seed"),
    (np.ones((20, 30), np.float32), 10, 1.5, "seed"),
])
def test_targets_reject_what_they_cannot_read(alpha, n, seed, message):
    with pytest.raises(PipelineError, match=message):
        particles.targets(alpha, n, seed)


# ---- 粒の動き（式の写し） ----------------------------------------------------------------------

def ref_particle(i, n, t, P, target, frame_size, k=1.0):
    """fx/particles.py の冒頭と docs/fx.md に書いた粒 i の式を、Python の数でそのまま計算したもの。戻り値は ((x, y), [r, g, b])。"""
    seed = P.get("seed", 0)

    def rand(label):
        return float(fxtimeline.seeded_rng(seed, "particles:" + label).random(n)[i])

    def pick(label, count):
        return int(fxtimeline.seeded_rng(seed, "particles:" + label).integers(0, count, n)[i])

    t0, t1 = P["t0"], P["t1"]
    tau, stagger = t - t0, P.get("stagger", 0.5)
    u = min(max(((t - t0) / (t1 - t0) - rand("delay") * stagger) / (1 - stagger), 0.0), 1.0)
    e = u * u * (3 - 2 * u)
    width, height = frame_size
    origin = P.get("origin", "ring")
    if origin == "ring":
        angle, rho = 2 * math.pi * rand("origin:angle"), 1 + (math.sqrt(2) - 1) * rand("origin:radius")
        sx, sy = (width - 1) / 2 + math.cos(angle) * rho * width / 2, (height - 1) / 2 + math.sin(angle) * rho * height / 2
    elif origin == "below":
        sx, sy = -0.5 + width * rand("origin:x"), height - 0.5 + 0.25 * height * rand("origin:y")
    else:
        sx, sy = origin[pick("origin:pick", len(origin))]
    x, y = sx * (1 - e) + target[0] * e, sy * (1 - e) + target[1] * e
    turns, phase = 0.25 + 0.5 * rand("turns"), 2 * math.pi * rand("phase")
    radius = P.get("swirl", 0.0) * k * (0.5 + 0.5 * rand("reach")) * (1 - e)
    x += radius * math.cos(phase + 2 * math.pi * turns * tau)
    y += radius * math.sin(phase + 2 * math.pi * turns * tau)
    y -= P.get("lift", 0.0) * k * math.sin(math.pi * e)
    level = 1.0 if t >= t0 else (t - (t0 - P["lead"])) / P["lead"]
    if "after" in P and t > t1:
        past, fade_s = t - t1, P["after"]["fade_s"]
        risen = P["after"]["rise"] * k * (0.5 + rand("rise")) * max(0.0, past - rand("release") * 0.5 * fade_s)
        y -= risen
        x += 0.2 * risen * math.sin(phase + 2 * math.pi * turns * past)
        level *= max(0.0, 1 - past / fade_s)
    colors = P.get("colors", [(255, 200, 120), (255, 236, 190), (255, 125, 35)])
    start, settle = colors[pick("color", len(colors))], P.get("settle", (255, 200, 120))
    light = (0.6 + 0.4 * rand("bright")) * level
    light *= 1 - P.get("twinkle", 0.0) * (1 + math.cos(2 * math.pi * (rand("twinkle") + (1 + 2 * rand("hz")) * tau))) / 2
    return (x, y), [(a * (1 - e) + b * e) * light for a, b in zip(start, settle)]


FULL = {"t0": 1.0, "t1": 3.0, "stagger": 0.4, "swirl": 30, "lift": 12, "twinkle": 0.5, "lead": 0.5, "seed": 4,
        "colors": [[80, 120, 255], [255, 255, 255]], "settle": [255, 220, 160], "after": {"rise": 40, "fade_s": 1.5}}


@pytest.mark.parametrize("origin", ["ring", "below", [[10.0, 20.0], [200.0, 30.0], [120.0, 150.0]]])
@pytest.mark.parametrize("t", [0.6, 1.0, 1.4, 2.2, 2.9, 3.0, 3.4, 4.2])
def test_flight_equals_the_written_formula(origin, t):
    P = {**FULL, "origin": origin}
    points = np.random.default_rng(1).uniform([20, 20], [220, 140], (40, 2))
    xy, rgb = particles.flight(t, P, points, SIZE, k=1.5)
    assert xy.shape == (40, 2) and rgb.shape == (40, 3) and xy.dtype == np.float32 and rgb.dtype == np.float32
    for i in (0, 7, 39):
        (x, y), color = ref_particle(i, 40, t, P, points[i], SIZE, k=1.5)
        assert xy[i] == pytest.approx([x, y], abs=1e-3) and rgb[i] == pytest.approx(color, abs=1e-3), (i, t)


def test_defaults_leave_the_ring_fly_straight_and_stay():
    points = np.random.default_rng(2).uniform([20, 20], [220, 140], (30, 2))
    for t in (1.0, 2.0, 3.0, 9.0):
        xy, rgb = particles.flight(t, WINDOW, points, SIZE)
        for i in (0, 29):
            (x, y), color = ref_particle(i, 30, t, WINDOW, points[i], SIZE)
            assert xy[i] == pytest.approx([x, y], abs=1e-3) and rgb[i] == pytest.approx(color, abs=1e-3)


def test_particles_start_at_the_origin_and_arrive_exactly_at_the_points():
    width, height = SIZE
    points = np.random.default_rng(3).uniform([20, 20], [220, 140], (500, 2)).astype(np.float32)
    P = {**WINDOW, "swirl": 40, "lift": 25, "twinkle": 0.6}
    for t in (3.0, 3.5, 60.0):                                            # t1 からは、行き先ちょうど（渦も弧も 0）で、色は settle
        xy, rgb = particles.flight(t, P, points, SIZE)
        assert np.array_equal(xy, points)
        assert np.allclose(rgb / rgb[:, :1], np.float32(particles.SETTLE) / 255.0, atol=1e-5)
    # t0 では出どころ。ring は、画面に内接する楕円と四隅を通る楕円の間
    xy, _ = particles.flight(1.0, WINDOW, points, SIZE)
    rho = np.hypot((xy[:, 0] - (width - 1) / 2) / (width / 2), (xy[:, 1] - (height - 1) / 2) / (height / 2))
    assert 1.0 - 1e-5 <= rho.min() < 1.05 and 1.36 < rho.max() <= math.sqrt(2) + 1e-5
    on_screen = (xy[:, 0] > -0.5) & (xy[:, 0] < width - 0.5) & (xy[:, 1] > -0.5) & (xy[:, 1] < height - 0.5)
    assert 0.05 < on_screen.mean() < 0.5                                   # 四隅の近くは画面の中、ほかは外
    xy, _ = particles.flight(1.0, {**WINDOW, "origin": "below"}, points, SIZE)        # below は画面の下の縁の外、幅いっぱい
    assert xy[:, 1].min() >= height - 0.5 and xy[:, 1].max() <= height - 0.5 + 0.25 * height
    assert xy[:, 0].min() < 0.1 * width and xy[:, 0].max() > 0.9 * width and -0.5 <= xy[:, 0].min() and xy[:, 0].max() <= width - 0.5
    spots = [[10.0, 20.0], [200.0, 30.0], [120.0, 150.0]]
    xy, _ = particles.flight(1.0, {**WINDOW, "origin": spots}, points, SIZE)          # 点の集まりは、粒ごとにどれか 1 つ
    assert {tuple(p) for p in xy.tolist()} == {tuple(p) for p in spots}


def test_stagger_delays_the_departures_and_everything_arrives_by_t1():
    points = np.full((400, 2), 100.0)
    spots = [[10.0, 10.0]]

    def progress(t, stagger):
        xy, _ = particles.flight(t, {**WINDOW, "origin": spots, "stagger": stagger}, points, SIZE)
        return (xy[:, 0] - 10.0) / 90.0                                    # 0 = 出どころ、1 = 行き先

    assert np.ptp(progress(2.0, 0.0)) < 1e-6 and progress(2.0, 0.0)[0] == pytest.approx(0.5)     # stagger 0 は全部が同時（窓の真ん中で半分）
    early = progress(1.5, 0.5)                                             # 窓の 1/4: 遅れ d·0.5 が 0.25 より小さい粒（半分）だけが出ている
    assert 0.4 < (early > 0).mean() < 0.6 and early.max() < 1.0
    late = progress(2.5, 0.5)                                              # 窓の 3/4: 全部が出ていて、遅れの小さい半分は着いている
    assert late.min() > 0 and 0.4 < (late == 1.0).mean() < 0.6
    assert np.all(progress(3.0, 0.5) == 1.0) and np.all(progress(3.0, 0.95) == 1.0) and np.all(progress(3.0, 5.0) == 1.0)
    assert np.array_equal(progress(2.5, 5.0), progress(2.5, 0.95)) and np.array_equal(progress(2.5, -1.0), progress(2.5, 0.0))     # stagger は 0〜0.95 に止める
    instant = {"t0": 2.0, "t1": 2.0, "origin": spots, "lead": 0.5}        # t0 と t1 が同じなら、その時刻に着いている
    assert np.all(particles.flight(2.0, instant, points, SIZE)[0] == 100.0) and np.all(particles.flight(1.9, instant, points, SIZE)[0] == 10.0)
    steps = np.stack([progress(t, 0.5) for t in np.linspace(1.0, 3.0, 41)])
    assert np.all(np.diff(steps, axis=0) >= -1e-6)                         # 戻らない


def test_swirl_and_lift_bend_the_path_and_scale_with_k():
    spot = [[120.0, 80.0]]
    points = np.full((300, 2), spot[0])                                    # 出どころと行き先を同じにして、渦と弧だけを見る
    P = {**WINDOW, "origin": spot, "stagger": 0.0}
    for k in (1.0, 2.5):
        xy, _ = particles.flight(1.0, {**P, "swirl": 20}, points, SIZE, k)
        radius = np.hypot(*(xy - spot[0]).T)
        assert 20 * k * 0.5 - 1e-3 <= radius.min() and radius.max() <= 20 * k + 1e-3 and np.ptp(radius) > 5 * k     # 半径は振れ幅の 0.5〜1 倍
        xy, _ = particles.flight(2.0, {**P, "swirl": 20}, points, SIZE, k)            # 窓の真ん中（e = 0.5）で半分
        assert np.hypot(*(xy - spot[0]).T).max() <= 10 * k + 1e-3
        xy, _ = particles.flight(2.0, {**P, "lift": 15}, points, SIZE, k)             # 弧の頂上は lift·k だけ上（画面の y は小さくなる）
        assert np.allclose(xy[:, 0], 120.0) and np.allclose(xy[:, 1], 80.0 - 15 * k, atol=1e-4)
    a, _ = particles.flight(1.2, {**P, "swirl": 20}, points, SIZE)
    b, _ = particles.flight(1.7, {**P, "swirl": 20}, points, SIZE)
    turn = np.unwrap(np.arctan2(*(b - spot[0]).T[::-1]) - np.arctan2(*(a - spot[0]).T[::-1]), discont=math.pi) % (2 * math.pi)
    assert 2 * math.pi * 0.25 * 0.5 - 1e-3 <= turn.min() and turn.max() <= 2 * math.pi * 0.75 * 0.5 + 1e-3     # 0.5 秒で 0.125〜0.375 回転、同じ向き


def test_lead_shows_the_particles_waiting_before_t0():
    points = np.random.default_rng(4).uniform([20, 20], [220, 140], (50, 2))
    assert particles.flight(0.99, WINDOW, points, SIZE) is None            # lead 0（既定）は t0 まで描かない
    assert particles.flight(1.0, WINDOW, points, SIZE) is not None
    P = {**WINDOW, "lead": 0.4}
    assert particles.flight(0.59, P, points, SIZE) is None and particles.flight(0.6, P, points, SIZE) is None    # 明るさ 0 のコマも描かない
    xy0, full = particles.flight(1.0, P, points, SIZE)
    xy, half = particles.flight(0.8, P, points, SIZE)
    assert np.allclose(xy, xy0) and np.allclose(half, full * 0.5, atol=1e-3)           # 出どころで待ち、直線で明るくなる
    base = night()
    assert np.array_equal(particles.gather(base.copy(), 0.5, P, points), base)


def test_after_lets_the_particles_rise_and_fade_out():
    points = np.random.default_rng(5).uniform([20, 60], [220, 140], (200, 2)).astype(np.float32)
    P = {**WINDOW, "after": {"rise": 30, "fade_s": 2.0}}
    xy, settled = particles.flight(3.0, P, points, SIZE)
    assert np.array_equal(xy, points)                                      # t1 ちょうどは着いたまま
    xy, rgb = particles.flight(3.5, P, points, SIZE)                       # 粒ごとに、fade_s の半分までの間で遅れて流れ始める
    risen = points[:, 1] - xy[:, 1]
    assert risen.min() == 0 and 0.3 < (risen > 0).mean() < 0.7 and risen.max() <= 30 * 1.5 * 0.5 + 1e-3
    assert np.all(np.abs(xy[:, 0] - points[:, 0]) <= 0.2 * risen + 1e-3)   # 横の揺れは昇った高さの 0.2 倍まで
    assert np.allclose(rgb, settled * 0.75, atol=1e-3)                     # 明るさは fade_s で直線に 0 へ
    xy, rgb = particles.flight(4.0, P, points, SIZE)
    risen = points[:, 1] - xy[:, 1]
    assert risen.min() > 0 and (risen > 20).any() and risen.max() <= 30 * 1.5 * 1.0 + 1e-3      # fade_s の半分で、全部の粒が流れている
    assert np.allclose(rgb, settled * 0.5, atol=1e-3)
    assert particles.flight(5.0, P, points, SIZE) is None and particles.flight(8.0, P, points, SIZE) is None
    base = night()
    assert np.array_equal(particles.gather(base.copy(), 5.0, P, points), base)
    assert particles.flight(8.0, WINDOW, points, SIZE) is not None         # after が無ければ、着いたまま


def test_adding_points_keeps_the_earlier_particles():
    points = np.random.default_rng(6).uniform([20, 20], [220, 140], (60, 2))
    for origin in ("ring", "below", [[10.0, 20.0], [200.0, 30.0]]):
        for t in (0.8, 1.6, 3.0, 3.7):
            P = {**FULL, "origin": origin}
            many, few = particles.flight(t, P, points, SIZE), particles.flight(t, P, points[:25], SIZE)
            assert np.array_equal(many[0][:25], few[0]) and np.array_equal(many[1][:25], few[1])


@pytest.mark.parametrize("P, points, message", [
    ({"t1": 3.0}, [[1.0, 2.0]], "t0 と t1"),
    ({"t0": 3.0, "t1": 1.0}, [[1.0, 2.0]], "t0 以降"),
    ({**WINDOW, "origin": "above"}, [[1.0, 2.0]], "origin"),
    ({**WINDOW, "origin": []}, [[1.0, 2.0]], "origin"),
    ({**WINDOW, "origin": [[1.0, 2.0, 3.0]]}, [[1.0, 2.0]], "origin"),
    ({**WINDOW, "after": 1.0}, [[1.0, 2.0]], "after"),
    ({**WINDOW, "colors": []}, [[1.0, 2.0]], "colors"),
    ({**WINDOW, "seed": -1}, [[1.0, 2.0]], "seed"),
    (WINDOW, [[1.0, 2.0, 3.0]], "points"),
    (WINDOW, [1.0, 2.0, 3.0], "points"),
    (WINDOW, [[1.0, float("nan")]], "points"),
])
def test_gather_names_what_is_wrong_with_the_arguments(P, points, message):
    with pytest.raises(PipelineError, match=message):
        particles.gather(night(), 2.0, P, points)


# ---- 描く（splat） ---------------------------------------------------------------------------

@pytest.mark.parametrize("size", [0.3, 0.8, 1.6, 3.0])
def test_one_particle_peaks_at_its_own_light_and_sits_on_its_position(size):
    f = np.zeros((41, 61, 3), np.float32)
    assert particles.splat(f, [[30.0, 20.0]], [[200.0, 100.0, 50.0]], size=size, glow=0) is f
    assert f[20, 30] == pytest.approx([200, 100, 50], rel=0.03) and f.dtype == np.float32      # 山の高さは粒の光（芯の太さによらない）
    assert np.unravel_index(f[..., 0].argmax(), f.shape[:2]) == (20, 30)
    f = np.zeros((41, 61, 3), np.float32)
    particles.splat(f, [[30.3, 17.6]], [[200.0, 200.0, 200.0]], size=size, glow=3.0)
    yy, xx = np.mgrid[0:41, 0:61]
    weight = f[..., 0] / f[..., 0].sum()
    assert [(weight * xx).sum(), (weight * yy).sum()] == pytest.approx([30.3, 17.6], abs=0.02)    # 画素の間の位置でも、光の重心が位置に来る


def test_glow_adds_a_halo_around_the_core():
    core, both = np.zeros((61, 61, 3), np.float32), np.zeros((61, 61, 3), np.float32)
    particles.splat(core, [[30.0, 30.0]], [[255.0, 255.0, 255.0]], size=1.0, glow=0)
    particles.splat(both, [[30.0, 30.0]], [[255.0, 255.0, 255.0]], size=1.0, glow=5.0)
    assert core[30, 38].max() < 1e-3 < both[30, 38].min() and np.all(both >= core - 1e-4)       # 芯の外に光彩が広がる
    assert both[30, 38].max() < 0.1 * both[30, 30].min()                    # 光彩は芯より弱い
    assert np.array_equal(particles.splat(core.copy(), [[30.0, 30.0]], [[255.0, 255.0, 255.0]], size=1.0, glow=-1.0),
                          particles.splat(core.copy(), [[30.0, 30.0]], [[255.0, 255.0, 255.0]], size=1.0, glow=0))


def test_overlapping_particles_add_up_through_the_screen_blend():
    one, two = np.full((21, 21, 3), 40, np.float32), np.full((21, 21, 3), 40, np.float32)
    particles.splat(one, [[10.0, 10.0]], [[100.0, 100.0, 100.0]], size=1.6, glow=0)
    particles.splat(two, [[10.0, 10.0], [10.0, 10.0]], [[100.0, 100.0, 100.0]] * 2, size=1.6, glow=0)
    assert one[10, 10] == pytest.approx(255 - (255 - 40) * (255 - 100) / 255, rel=1e-3)          # スクリーン合成
    assert two[10, 10] == pytest.approx(255 - (255 - 40) * (255 - 200) / 255, rel=1e-3)          # 重なった粒の光は足してから合成する
    hot = np.full((21, 21, 3), 40, np.float32)
    particles.splat(hot, [[10.0, 10.0]] * 9, [[255.0, 200.0, 120.0]] * 9, size=1.6, glow=0)
    assert hot[10, 10] == pytest.approx([255, 255, 255], abs=1e-3) and hot.max() <= 255.0       # たくさん重なると白で止まる


def ref_splat(f, cells, rgb, size, glow, gain):
    """fx/particles.py の冒頭と docs/fx.md に書いた描き方を、画面いっぱいの配列でそのまま計算したもの（粒は画素の中心 (列, 行) に置く）。"""
    energy = np.zeros(f.shape, np.float32)
    for (column, row), light in zip(cells, rgb):
        energy[row, column] += np.float32(light)
    dots = fire.blur(energy, size) * np.float32(2 * math.pi * size * size)       # 1 粒の山の高さを、その粒の光にそろえる
    f = fire.screen(f, dots * np.float32(gain))
    return fire.screen(f, fire.blur(np.minimum(dots, np.float32(255.0)), glow) * np.float32(0.5 * gain))


@pytest.mark.parametrize("size, glow, gain", [(1.6, 3.0, 1.0), (1.0, 5.0, 0.6), (2.5, 10.0, 1.5), (1.2, 20.0, 1.0)])
def test_splat_equals_the_written_drawing(size, glow, gain):
    rng = np.random.default_rng(2)
    cells = np.stack([rng.integers(160, 240, 300), rng.integers(120, 180, 300)], 1)     # 重なる粒も多い（芯が 255 で止まる所がある）
    rgb = rng.uniform(40, 255, (300, 3))
    base = night((400, 300))                                               # 粒から画面の縁まで、いちばん大きいぼかしの 4σ より遠い
    mine = particles.splat(base.copy(), cells.astype(np.float32), rgb, size=size, glow=glow, gain=gain)
    theirs = ref_splat(base.copy(), cells, rgb, size, glow, gain)
    assert np.abs(mine - theirs).max() < 2e-3 and (mine - base).max() > 100


def test_particles_off_the_screen_shine_in_only_from_nearby():
    light = [[255.0, 255.0, 255.0]]
    f = np.zeros((40, 60, 3), np.float32)
    particles.splat(f, [[-3.0, 20.0]], light, size=1.6, glow=3.0)
    assert f[20, 0].min() > 0.5 and f[20, 0].min() > f[20, 5].max() and f[:, 30:].max() == 0     # 縁のすぐ外の粒は、光だけが入る
    for xy in ([[-200.0, 20.0]], [[30.0, 500.0]], [[float("nan"), 20.0]], [[float("inf"), 20.0]]):
        f = night((60, 40))
        assert np.array_equal(particles.splat(f.copy(), xy, light, size=1.6, glow=3.0), f)       # 届かない粒・数でない位置は描かない
    f = night((60, 40))
    assert np.array_equal(particles.splat(f.copy(), np.zeros((0, 2)), np.zeros((0, 3))), f)
    assert np.array_equal(particles.splat(f.copy(), [[30.0, 20.0]], [[0.0, 0.0, 0.0]]), f)
    assert np.array_equal(particles.splat(f.copy(), [[30.0, 20.0]], light, gain=0.0), f)


@pytest.mark.parametrize("glow", [3.0, 10.0, 20.0])
def test_the_drawn_light_does_not_depend_on_the_drawn_range(glow):
    # 描く範囲は粒の周りだけ。遠くにほとんど光らない粒を足して範囲を広げても、元の粒の光は変わらない
    # （fire.blur は大きいぼかしを 1/2・1/4 に縮めて掛けるので、範囲の升目がそろっていないと光彩が変わる）
    rng = np.random.default_rng(1)
    xy, rgb = rng.uniform([100, 80], [140, 110], (60, 2)), rng.uniform(50, 255, (60, 3))
    near, wide = np.zeros((200, 300, 3), np.float32), np.zeros((200, 300, 3), np.float32)
    particles.splat(near, xy, rgb, size=1.6, glow=glow)
    particles.splat(wide, np.vstack([xy, [[291.3, 5.2]]]), np.vstack([rgb, [[1e-6, 1e-6, 1e-6]]]), size=1.6, glow=glow)
    assert near.max() > 100 and np.abs(near - wide).max() < 1e-3


# ---- 粒が集まって形になる（gather） ---------------------------------------------------------------

def test_the_light_gathers_from_outside_into_the_shape():
    alpha = ring_alpha()
    points = particles.targets(alpha, 600, 3)
    near = grown(alpha, 3)                                                 # 形を 3 画素（既定の glow）だけ太らせた範囲
    start = added(1.0, WINDOW, points)
    assert start.sum() > 0 and share_inside(start, near) < 0.05             # t0 では、光は形の外（画面の外周）
    shares = [share_inside(added(t, WINDOW, points), near) for t in (1.0, 1.5, 2.0, 2.5, 3.0)]
    assert shares == sorted(shares) and shares[2] < 0.5 < shares[3]
    for t in (3.0, 3.01, 4.0, 30.0):                                       # t1 からは、足した光の 95% 以上が形の近く
        assert share_inside(added(t, WINDOW, points), near) >= 0.95, t
    assert added(0.99, WINDOW, points).sum() == 0
    inside = added(3.0, WINDOW, points)[alpha > 0]
    assert (inside > 30).mean() > 0.95                                     # 形の全体に光が乗る（輪の一部に固まらない）


def test_gather_depends_only_on_the_arguments():
    points = particles.targets(ring_alpha(), 300, 3)
    P = {**FULL, "origin": "ring"}
    times = (0.7, 1.3, 2.4, 3.0, 3.8)
    first = [particles.gather(night(), t, P, points) for t in times]
    again = [particles.gather(night(), t, P, points) for t in reversed(times)][::-1]      # 描く順を変えても同じ（状態を持たない）
    assert all(np.array_equal(a, b) for a, b in zip(first, again))
    other = [particles.gather(night(), t, {**P, "seed": 5}, points) for t in times]
    assert all(not np.array_equal(a, b) for a, b in zip(first, other))     # seed を変えると違う画
    assert all(not np.array_equal(a, night()) for a in first)


def test_gather_keeps_the_shape_and_type_and_gain_zero_changes_nothing():
    points = particles.targets(ring_alpha(), 300, 3)
    base = night()
    for t in (0.2, 1.0, 2.0, 3.0, 5.0):
        f = base.copy()
        out = particles.gather(f, t, WINDOW, points)
        assert out is f and out.shape == (160, 240, 3) and out.dtype == np.float32
        assert np.all(out >= base - 1e-3) and out.max() <= 255.0           # スクリーン合成は暗くしない・255 を超えない
        for gain in (0.0, -1.0):
            assert np.array_equal(particles.gather(base.copy(), t, {**WINDOW, "gain": gain}, points), base)     # ビット単位で同じ
    assert np.array_equal(particles.gather(base.copy(), 2.0, WINDOW, np.zeros((0, 2), np.float32)), base)      # 点が無ければ何も描かない
    assert np.array_equal(particles.gather(base.copy(), 2.0, WINDOW, []), base)
    one = particles.gather(base.copy(), 3.0, WINDOW, [[120.0, 80.0]])                     # 点が 1 つ
    assert (one - base)[80, 120].min() > 20 and (one - base)[:40].max() == 0
    dim, bright = added(3.0, {**WINDOW, "gain": 0.3}, points), added(3.0, {**WINDOW, "gain": 1.0}, points)
    assert 0 < dim.sum() < 0.6 * bright.sum()


def test_keys_interpolate_the_numbers_in_time():
    points = particles.targets(ring_alpha(), 300, 3)
    keyed = {**WINDOW, "swirl": 10, "keys": {"gain": [[3.0, 1.0], [4.0, 0.0]], "swirl": [[1.0, 40.0], [2.0, 0.0]]}}
    base = night()
    assert np.array_equal(particles.gather(base.copy(), 3.5, keyed, points), particles.gather(base.copy(), 3.5, {**WINDOW, "gain": 0.5}, points))
    assert np.array_equal(particles.gather(base.copy(), 4.0, keyed, points), base)        # キーで gain が 0 になれば何も描かない
    assert np.array_equal(particles.gather(base.copy(), 1.5, keyed, points), particles.gather(base.copy(), 1.5, {**WINDOW, "swirl": 20.0}, points))
    assert np.array_equal(particles.flight(1.5, keyed, points, SIZE)[0], particles.flight(1.5, fire.params_at(keyed, 1.5), points, SIZE)[0])


def test_k_scales_the_lengths_but_not_the_positions():
    points = particles.targets(ring_alpha(), 300, 3)
    thin, thick = added(3.0, WINDOW, points, k=0.5), added(3.0, WINDOW, points, k=2.0)
    alpha = ring_alpha()
    assert share_inside(thin, grown(alpha, 2)) > 0.97 > share_inside(thick, grown(alpha, 2))     # k が大きいほど粒が太く、光が形の外へ広がる
    assert np.array_equal(particles.flight(3.0, WINDOW, points, SIZE, k=0.5)[0], particles.flight(3.0, WINDOW, points, SIZE, k=2.0)[0])


def test_points_from_a_view_can_be_passed_as_they_are(tmp_path):
    # アルファ（元画像と同じ大きさのカード）から取った点を正規化座標にして、view.points で画面へ写したものをそのまま渡す。
    # カメラが寄っていても、同じ view で写したアルファの形の上に光が集まる
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": SIZE[0], "height": SIZE[1], "fps": 12}, "duration_s": 4.0,
           "assets": {"base": "b.png"}, "camera": {"keys": [{"t": 0, "zoom": 1.1, "center": [0.5, 0.5]}, {"t": 4.0, "zoom": 1.5, "center": [0.45, 0.55]}]}}
    path = camera.CameraPath(fxspec.parse_spec(raw, tmp_path).data["camera"], 4.0, SIZE, (0.0, 0.0, 1.0, 1.0))
    alpha = ring_alpha(r0=20, r1=30)
    uv = (particles.targets(alpha, 500, 3) + 0.5) / np.float32(SIZE)
    for t in (3.0, 3.6):
        view = View(path, (0.0, 0.0, 1.0, 1.0), None, SIZE, t)
        points = view.points(uv)
        assert points.dtype == np.float32 and points.shape == (500, 2)
        base = night()
        k = view.scale / SIZE[0]                                           # 元画像の 1 画素が画面の何画素か（粒の太さもカメラに合わせる）
        light = (particles.gather(base.copy(), t, WINDOW, points, k=k) - base).sum(-1)
        on_screen = view.warp(alpha) > 0.5                                 # 同じ camera で画面へ写した形
        assert k > 1.3 and on_screen.sum() > 1.7 * alpha.sum()             # カメラが寄って、形は元より大きい
        assert share_inside(light, grown(on_screen, math.ceil(3 * k))) >= 0.95, t
        assert share_inside(light, grown(alpha > 0, math.ceil(3 * k))) < 0.6            # 元の位置のままではない


# ---- 既定値の出どころと、見本の hooks ----------------------------------------------------------------

def test_default_colors_and_lengths_are_the_values_of_the_existing_parts():
    embers = inspect.signature(fire.embers).parameters
    assert particles.EMBER == embers["col"].default == particles.SETTLE and particles.COLORS[0] == particles.EMBER
    assert particles.SIZE == pytest.approx(sum(embers["size"].default) / 2)
    assert particles.RISE == pytest.approx(sum(embers["vy"].default) / 2) and particles.FADE_S == pytest.approx(sum(embers["life"].default) / 2)
    assert f"g = max(1.0, {particles.GLOW:g} * k)" in inspect.getsource(fire.embers)
    assert str(list(particles.CORE)) in inspect.getsource(sky.lamps) and str(list(particles.FLAME)) in inspect.getsource(fire.candle)
    assert f"rng.uniform({particles.BRIGHT[0]}, {particles.BRIGHT[1]}, n)" in inspect.getsource(sky.rain)


def test_particles_hooks_example_runs_on_the_demo(tmp_path):
    sequence.demo(tmp_path, 160, 90, 3.0, 8)
    timeline = sequence.load_timeline(tmp_path / "timeline.json")
    hooks = Hooks(ROOT / "examples" / "fx" / "particles_hooks.py")
    during, before = (0.5, 1.0, 1.5, 2.0, 2.5), (0.0, 0.25)
    frames = {}
    for name, h in (("plain", None), ("hooks", hooks), ("again", hooks)):
        ctx = sequence.Context(timeline, hooks=h)
        try:
            frames[name] = [ctx.frame(t) for t in during + before]
        finally:
            ctx.close()
    count = len(during)
    assert all(not np.array_equal(p, q) for p, q in zip(frames["plain"][:count], frames["hooks"][:count]))
    assert all(np.array_equal(p, q) for p, q in zip(frames["plain"][count:], frames["hooks"][count:]))      # 集まり始める前は何も描かない
    assert all(np.array_equal(p, q) for p, q in zip(frames["hooks"], frames["again"]))
    # 見本の形（輪と横棒）に集まる: 集まり終わり（2.0 秒）のコマで、足した光の大半が形の近くにある
    module = hooks.module
    shape = cv2.resize(module.card(), (160, 90), interpolation=cv2.INTER_AREA) > 0.25
    light = (frames["hooks"][3].astype(np.float32) - frames["plain"][3].astype(np.float32)).clip(0).sum(-1)
    assert share_inside(light, grown(shape, 2)) > 0.9
    # 出力の大きさ（--scale）を変えても、同じ形の上に集まる（見本は点を正規化座標で持ち、長さに 画面の幅 / 1920 を掛ける）
    for width, height in ((160, 90), (320, 180), (640, 360)):
        base = np.full((height, width, 3), 20, np.float32)
        light = (module.final(base.copy(), 2.0, SimpleNamespace(size=(width, height))) - base).sum(-1)
        shape = cv2.resize(module.card(), (width, height), interpolation=cv2.INTER_NEAREST) > 0.5
        assert light.sum() > 0 and share_inside(light, grown(shape, max(1, width // 320))) > 0.95, (width, height)
