"""fx.impact（山場の演出の部品）: 一斉に飛ぶ火花・衝撃の輪・閃光・揺れ・点の閃き・暗転。

どれも時刻 t と引数だけで決まり、窓の外では画を変えない（同じ配列のまま）。光は届く所だけに重ねる。
"""
import math

import numpy as np
import pytest

pytest.importorskip("cv2")

from mv_pipeline.fx import impact  # noqa: E402


def canvas(width=320, height=180, value=40.0):
    return np.full((height, width, 3), value, np.float32)


@pytest.mark.parametrize("draw", [
    lambda f, t: impact.burst(f, t, 1.0, (160, 120)),
    lambda f, t: impact.ring(f, t, 1.0, (160, 120)),
    lambda f, t: impact.flash(f, t, 1.0),
    lambda f, t: impact.shake(f, t, 1.0),
    lambda f, t: impact.glint(f, 160, 90, t, 1.0),
])
def test_parts_leave_the_frame_alone_outside_their_window(draw):
    for t in (0.0, 0.99, 3.5):
        frame = canvas()
        before = frame.copy()
        out = draw(frame, t)
        assert out is frame and np.array_equal(frame, before)


def test_burst_is_deterministic_local_and_fades_out():
    a = impact.burst(canvas(), 1.2, 1.0, (160, 120), seed=3)
    b = impact.burst(canvas(), 1.2, 1.0, (160, 120), seed=3)
    assert np.array_equal(a, b)
    assert not np.array_equal(a, impact.burst(canvas(), 1.2, 1.0, (160, 120), seed=4))
    assert a.max() > 200 and a.min() >= 40.0                 # スクリーン合成なので暗くはしない
    far = impact.burst(canvas(), 1.05, 1.0, (40, 170), k=0.5, n=30, seed=1)
    assert np.array_equal(far[:60, 200:], canvas()[:60, 200:])   # 届かない所は同じ値
    late = impact.burst(canvas(), 1.0 + 0.8 + 0.1 + 1e-6, 1.0, (160, 120), life=(0.3, 0.8), delay=0.1)
    assert np.array_equal(late, canvas())                      # いちばん遅い火花も消えた後


def test_burst_scales_with_k():
    small = impact.burst(canvas(640, 360), 1.3, 1.0, (320, 300), k=0.5, seed=2)
    large = impact.burst(canvas(640, 360), 1.3, 1.0, (320, 300), k=1.0, seed=2)
    rows_small = np.flatnonzero((small - 40).max(axis=(1, 2)) > 5)
    rows_large = np.flatnonzero((large - 40).max(axis=(1, 2)) > 5)
    assert 300 - rows_large.min() > 300 - rows_small.min()   # 速さも k 倍なので、大きい k ほど高く飛ぶ


def test_ring_grows_and_dims():
    early = impact.ring(canvas(640, 360), 1.05, 1.0, (320, 200))
    late = impact.ring(canvas(640, 360), 1.3, 1.0, (320, 200))
    lit_early, lit_late = (early - 40).max(axis=2) > 3, (late - 40).max(axis=2) > 3
    assert np.ptp(np.flatnonzero(lit_late.any(axis=0))) > np.ptp(np.flatnonzero(lit_early.any(axis=0)))   # 広がる
    assert (early - 40).max() > (late - 40).max()            # 消えていく
    middle = impact.ring(canvas(640, 360), 1.15, 1.0, (320, 200))
    assert np.allclose(middle[200, 320], 40.0, atol=1.0)      # 広がった輪の真ん中は光らない


def test_flash_is_the_timeline_flash_formula():
    frame = np.random.default_rng(1).uniform(0, 255, (20, 30, 3)).astype(np.float32)
    expected = frame + (np.array([255, 246, 232], np.float32) - frame) * np.float32(0.7 * math.exp(-0.1 * 13.0))
    out = impact.flash(frame.copy(), 1.1, 1.0, strength=0.7)
    assert np.allclose(out, expected, atol=1e-3)
    assert np.array_equal(impact.flash(frame.copy(), 1.3, 1.0), frame)      # 窓 0.3 秒の外


def test_shake_moves_the_frame_without_showing_edges():
    rng = np.random.default_rng(2)
    frame = rng.uniform(60, 200, (180, 320, 3)).astype(np.float32)
    out = impact.shake(frame, 1.05, 1.0, amp=0.03)
    assert out is not frame and out.shape == frame.shape
    assert not np.array_equal(out, frame)
    assert out.min() >= 59.9 and out.max() <= 200.1          # 端の外を引かない（黒い縁が出ない）
    assert np.array_equal(impact.shake(frame, 1.05, 1.0, amp=0.03), out)    # 同じ t なら同じ揺れ
    assert impact.shake(frame, 1.6, 1.0) is frame


def test_glint_rises_and_falls_at_the_point():
    peak = impact.glint(canvas(), 160, 90, 1.35, 1.0)
    edge = impact.glint(canvas(), 160, 90, 1.03, 1.0)
    assert peak[90, 160].mean() > edge[90, 160].mean() > 40.0
    assert peak[90, 160].mean() > peak[90, 200].mean() > 40.5     # 横へ伸びる筋（離れるほど暗い）
    assert np.allclose(peak[90, 310], 40.0, atol=0.5)               # 筋は短い（既定は顔の目ほど。顔を横切らない）
    assert np.allclose(peak[10, 160], 40.0, atol=0.5)               # 縦には伸びない


def test_fade_goes_to_black_at_the_end():
    assert np.array_equal(impact.fade(canvas(), 10.0, 12.0, 0.8), canvas())
    levels = [float(impact.fade(canvas(), t, 12.0, 0.8).mean()) for t in (11.2, 11.4, 11.6, 11.8, 12.0)]
    assert levels == sorted(levels, reverse=True) and levels[-1] == 0.0
