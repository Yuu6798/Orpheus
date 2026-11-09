"""dance/finish.py: 仕上げの部品（表示の値）。非公開の制作の式の写しと同じ値になること、hooks の見本が動くこと。"""
import math
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.dance import api, demo, finish  # noqa: E402

H, W = 192, 108
EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "dance" / "finish_hooks.py"
rng = np.random.default_rng(11)


def person(cx=54, foot=170, arms=True):
    a = np.zeros((H, W), np.float32)
    cv2.ellipse(a, (cx, foot - 70), (12, 33), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
    cv2.circle(a, (cx, foot - 112), 9, 1.0, -1, cv2.LINE_AA)
    for side in (-1, 1):
        cv2.line(a, (cx + side * 4, foot - 42), (cx + side * 11, foot), 1.0, 4, cv2.LINE_AA)
        if arms:
            cv2.line(a, (cx + side * 9, foot - 95), (cx + side * 45, foot - 82), 1.0, 3, cv2.LINE_AA)
    return np.clip(cv2.GaussianBlur(a, (0, 0), 0.8), 0, 1)


STAGE = np.clip(np.linspace(0.1, 0.7, H, dtype=np.float32)[:, None, None] + rng.random((H, W, 3)).astype(np.float32) * 0.2, 0, 1)
FG = rng.random((H, W, 3)).astype(np.float32)


# ---- 制作の式の写し（v2 の Renderer.compose の順。小さな画で、長さはそのまま） ----------------------------

def _production(stage, fg, al, fy, k, rim_rgb, edge_rgb, gains):
    rgb = lambda c: np.array(c, np.float32) / 255.0  # noqa: E731
    img = stage.copy()
    halo = cv2.GaussianBlur(cv2.resize(al, (W // 4, H // 4), interpolation=cv2.INTER_AREA), (0, 0), 6)
    halo = cv2.resize(halo, (W, H), interpolation=cv2.INTER_LINEAR)
    img *= (1 - 0.42 * np.clip(halo * 1.4, 0, 1))[..., None]
    rows = np.nonzero((al[::2] > 0.5).any(1))[0] * 2
    if len(rows):
        bot, top = rows.max(), rows.min()
        feet = al[max(bot - int(0.06 * (bot - top)), 0):bot + 1] > 0.5
        xs = np.nonzero(feet.any(0))[0]
        if len(xs):
            cxf, fw = (xs.min() + xs.max()) / 2, xs.max() - xs.min()
            sh = np.zeros((H // 2, W // 2), np.float32)
            cv2.ellipse(sh, (int(cxf / 2), fy // 2 + 3), (int(fw * 0.30 + 30), 16), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
            sh = cv2.resize(cv2.GaussianBlur(sh, (0, 0), 6), (W, H))
            lift = max(fy - bot, 0)
            img *= (1 - 0.5 * float(np.clip(1 - lift / 140.0, 0.25, 1)) * sh)[..., None]
    refl_h = min(H - fy, 300)
    if refl_h > 10:
        rp = (fg * al[..., None])[fy - refl_h:fy][::-1]
        ra = al[fy - refl_h:fy][::-1]
        fade = np.linspace(0.22, 0.0, refl_h, dtype=np.float32)[:, None]
        img[fy:fy + refl_h] = img[fy:fy + refl_h] * (1 - ra[..., None] * fade[..., None]) + rp * fade[..., None]
    band = np.clip(al - cv2.erode(al, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))), 0, 1)
    band = cv2.GaussianBlur(band, (0, 0), 1.6)
    yb = np.linspace(1.25, 0.75, al.shape[0], dtype=np.float32)[:, None]
    fg = np.clip(fg + (band * yb)[..., None] * rgb(rim_rgb) * k, 0, 1)
    rim = np.clip(al - cv2.erode(al, np.ones((5, 5), np.uint8)), 0, 1)
    fg = fg * np.array(gains, np.float32) + rim[..., None] * rgb(edge_rgb) * 0.16
    return fg * al[..., None] + img * (1 - al[..., None])


@pytest.mark.parametrize("cx, foot, floor", [(54, 170, 172), (60, 150, 172), (40, 160, 180)])
def test_the_parts_in_order_give_the_production_compose(cx, foot, floor):
    alpha = person(cx, foot)
    k, rim, edge, gains = 0.52, (210, 230, 255), (240, 240, 250), (1.0, 0.98, 0.96)
    theirs = _production(STAGE, FG, alpha, floor, k, rim, edge, gains)
    img = finish.halo(STAGE.copy(), alpha)
    img = finish.contact_shadow(img, alpha, floor)
    img = finish.floor_reflection(img, FG, alpha, floor)
    lit = finish.rim_light(FG, alpha, rim, k)
    lit = finish.rim_light(finish.tint(lit, gains), alpha, edge, 0.16, kernel=5, shape="rect", blur=0, gradient=None, clip=False)
    assert np.array_equal(finish.over(lit, alpha, img), theirs)


def test_stage_bloom_and_sharpen_follow_the_production_lines():
    st = STAGE * 1.4
    small = cv2.resize(st, (W // 4, H // 4), interpolation=cv2.INTER_AREA)
    bloom = st + cv2.resize(cv2.GaussianBlur(np.clip(small - 0.45, 0, 0.8), (0, 0), 7), (W, H), interpolation=cv2.INTER_LINEAR) * 0.6
    assert np.array_equal(finish.stage_bloom(STAGE * 1.4), bloom)
    open_top = st + cv2.resize(cv2.GaussianBlur(np.clip(small - 0.30, 0, None), (0, 0), 6), (W, H), interpolation=cv2.INTER_LINEAR) * 0.9
    assert np.array_equal(finish.stage_bloom(STAGE * 1.4, threshold=0.30, ceiling=None, sigma=6, strength=0.9), open_top)
    assert np.array_equal(finish.sharpen(FG), np.clip(FG + (FG - cv2.GaussianBlur(FG, (0, 0), 1.4)) * 0.4, 0, 1))


# ---- 部品ごと --------------------------------------------------------------------------------

def test_feet_ignore_the_arms_and_the_shadow_fades_when_the_feet_leave_the_floor():
    alpha = person(54, 170)
    bottom, center, width = finish.feet_span(alpha)
    assert abs(bottom - 170) <= 2 and abs(center - 54) <= 1 and width < 30            # 腕（左右に 45 画素）は数えない
    assert finish.feet_span(np.zeros((H, W), np.float32)) is None
    on_floor = STAGE - finish.contact_shadow(STAGE, alpha, 172)
    in_air = STAGE - finish.contact_shadow(STAGE, person(54, 100), 172)
    assert on_floor.max() > in_air.max() > 0                                          # 上がるほど薄い（下限 0.25 倍）
    assert on_floor[150:, :, :].max() > on_floor[:100, :, :].max()
    assert np.array_equal(finish.contact_shadow(STAGE, np.zeros((H, W), np.float32), 172), STAGE)


def test_the_halo_darkens_only_near_the_performer():
    alpha = person(30, 170, arms=False)
    darker = STAGE - finish.halo(STAGE, alpha)
    assert darker[100:170, 20:40].mean() > 10 * max(darker[:, 90:].mean(), 1e-6)


def test_the_floor_reflection_mirrors_the_performer_and_fades_down():
    alpha = person(54, 150, arms=False)
    out = finish.floor_reflection(np.zeros((H, W, 3), np.float32), np.ones((H, W, 3), np.float32), alpha, 152)
    span = min(H - 152, 300)
    assert out[:152].max() == 0 and out[152:152 + span].max() > 0
    assert out[152:156].mean() > out[152 + span - 6:152 + span].mean()
    assert np.array_equal(finish.floor_reflection(STAGE, FG, alpha, H - 5), STAGE)     # 10 画素以下なら何もしない


def test_rim_light_shapes():
    alpha = person(54, 170, arms=False)
    plain = finish.rim_light(np.zeros((H, W, 3), np.float32), alpha, (255, 255, 255), 1.0, gradient=None)
    graded = finish.rim_light(np.zeros((H, W, 3), np.float32), alpha, (255, 255, 255), 1.0)
    assert plain.max() > 0 and plain[100, 54].max() < 0.2 * plain.max()                # 縁に光り、胴の真ん中はほとんど光らない
    top = graded[:H // 2].sum() / max(plain[:H // 2].sum(), 1e-6)
    bottom = graded[H // 2:].sum() / max(plain[H // 2:].sum(), 1e-6)
    assert top > 1.0 > bottom                                                          # 上が強い
    loose = finish.rim_light(np.full((H, W, 3), 0.9, np.float32), alpha, (255, 255, 255), 2.0, clip=False)
    assert loose.max() > 1.0 and finish.rim_light(np.full((H, W, 3), 0.9, np.float32), alpha, (255, 255, 255), 2.0).max() == 1.0


def test_beat_pulse_counts_recent_beats_with_bar_heads_and_gain():
    beats = [0.0, 0.5, 1.0, 1.5, 2.0]
    heads = [0.0, 2.0]
    assert finish.beat_pulse(beats, -0.1) == 0.0
    assert finish.beat_pulse(beats, 0.1, downbeats=heads) == pytest.approx(math.exp(-0.1 / 0.12))
    assert finish.beat_pulse(beats, 0.6, downbeats=heads) == pytest.approx(0.55 * math.exp(-0.1 / 0.12))
    two = finish.beat_pulse(beats, 0.6, tau=0.13, downbeats=heads, beat=0.6, count=2)
    assert two == pytest.approx(0.6 * math.exp(-0.1 / 0.13) + math.exp(-0.6 / 0.13))
    muted = finish.beat_pulse(beats, 1.1, downbeats=heads, gain=lambda at, t: 0.0 if at == 1.0 else 1.0)
    assert muted == 0.0


def test_display_and_linear_round_trip():
    img = rng.random((8, 8, 3)).astype(np.float32)
    assert np.allclose(finish.linear(finish.display(img)), img, atol=1e-6)
    alpha = rng.random((8, 8)).astype(np.float32) * 0.9 + 0.1
    fg = rng.random((8, 8, 3)).astype(np.float32)
    assert np.allclose(finish.straight(finish.premultiplied(fg, alpha), alpha), fg, atol=1e-5)
    assert finish.straight(np.zeros((2, 2, 3), np.float32), np.zeros((2, 2), np.float32)).max() == 0


# ---- hooks の見本 -----------------------------------------------------------------------------

def test_the_example_hooks_render_and_stay_deterministic(tmp_path):
    demo.create(tmp_path, 180, 320, 3.0, 12)
    frames = []
    for _ in range(2):
        ctx = api.prepare(tmp_path / "spec.json", hooks=EXAMPLE, out=tmp_path / "runs")
        try:
            frames.append(api.frame(ctx, 0.5))
            L = api.layers(ctx, 0.5)
            assert L.performer_info["body"] is not None
        finally:
            api.close(ctx)
    plain_ctx = api.prepare(tmp_path / "spec.json", out=tmp_path / "runs")
    try:
        plain = api.frame(plain_ctx, 0.5)
    finally:
        api.close(plain_ctx)
    assert np.array_equal(frames[0], frames[1]) and not np.array_equal(frames[0], plain)
