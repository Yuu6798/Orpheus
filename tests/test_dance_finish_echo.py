"""mv_pipeline.dance.finish.afterimage_echo（分身炎残像エコー）の単体テスト。"""
import numpy as np
import pytest

from mv_pipeline.dance import finish


def _circle_alpha(h, w, cx, cy, r):
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    return (((xx - cx) ** 2 + (yy - cy) ** 2) <= r * r).astype(np.float32)


def test_afterimage_echo_adds_tinted_contour_behind_moving_performer():
    h, w = 128, 128
    base = np.zeros((h, w, 3), dtype=np.float32)
    a_prev = _circle_alpha(h, w, 44, 64, 20)
    a_curr = _circle_alpha(h, w, 72, 64, 20)

    out = finish.afterimage_echo(
        base,
        trail_alphas=[a_prev],
        colors=[(1.0, 0.2, 0.05)],
        strength=0.8,
        sigma=2.0,
        edge_only=True,
        edge_ksize=7,
        current_alpha=a_curr,
        unit=1.0,
    )
    assert out.shape == base.shape and out.dtype == np.float32
    assert float(out.min()) >= 0.0 and float(out.max()) <= 1.0

    # 過去シルエットの輪郭付近（x=24, y=64）が赤橙色に強く発光している
    rim_px = out[64, 24]
    assert rim_px[0] > 0.25 and rim_px[0] > rim_px[1] > rim_px[2]
    # 過去シルエットの内部中心（x=44, y=64）は edge_only により輪郭より暗い
    assert out[64, 44, 0] < rim_px[0] * 0.3
    # 離れた背景は 0 のまま
    assert float(out[5, 5].sum()) == 0.0


def test_afterimage_echo_noop_and_unit_scaling_and_validation():
    base = np.full((64, 64, 3), 0.1, dtype=np.float32)
    # 空リストや strength=0 は元の画像をそのまま返す
    assert np.array_equal(finish.afterimage_echo(base, [], [(1, 0, 0)]), base)
    assert np.array_equal(finish.afterimage_echo(base, [np.ones((64, 64), np.float32)], [(1, 0, 0)], strength=0.0), base)

    # 半サイズ解像度 + unit=0.5 でも正常に動作し非有限値ゼロ
    a_half = _circle_alpha(64, 64, 24, 32, 10)
    out_half = finish.afterimage_echo(base, [a_half, a_half], [(1.0, 0.6, 0.1)], strength=0.5, unit=0.5)
    assert np.isfinite(out_half).all() and out_half.mean() > base.mean()

    with pytest.raises(ValueError):
        finish.afterimage_echo(base, [a_half], [])
    with pytest.raises(ValueError):
        finish.afterimage_echo(base, [np.ones((32, 32), np.float32)], [(1, 0, 0)])
    with pytest.raises(ValueError):
        finish.afterimage_echo(base, [a_half], [(1, 0, 0, 1)])
