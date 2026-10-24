import math

import numpy as np
import pytest

from mv_pipeline.fx import timeline


@pytest.mark.parametrize("name", timeline.EASES)
def test_ease_endpoints_and_monotonic(name):
    assert timeline.ease(name, 0.0) == pytest.approx(0.0, abs=1e-12)
    assert timeline.ease(name, 1.0) == pytest.approx(1.0, abs=1e-12)
    values = [timeline.ease(name, u) for u in np.linspace(0, 1, 501)]
    assert all(b >= a - 1e-12 for a, b in zip(values, values[1:]))
    assert all(-1e-12 <= v <= 1 + 1e-12 for v in values)


def test_ease_shapes_and_clamping():
    assert timeline.ease("in_out", 0.5) == pytest.approx(0.5)
    assert timeline.ease("in", 0.5) < 0.5 < timeline.ease("out", 0.5)
    assert timeline.ease("in", 0.5) + timeline.ease("out", 0.5) == pytest.approx(1.0)  # out は in の鏡像
    assert timeline.ease("linear", -3) == 0.0 and timeline.ease("in_out", 9) == 1.0
    assert timeline.ease("in", 0.0001) < 1e-6                                          # 加速は 0 から滑らかに
    array = timeline.ease("in_out", np.array([-1.0, 0.5, 2.0]))
    assert array.tolist() == [0.0, 0.5, 1.0]
    with pytest.raises(ValueError):
        timeline.ease("bounce", 0.5)


KEYS = [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5], "ease": "linear"},
        {"t": 2.0, "zoom": 1.2, "center": [0.6, 0.4], "ease": "in_out"},
        {"t": 4.0, "zoom": 1.0, "center": [0.5, 0.5], "ease": "linear"}]


def test_interp_holds_ends_and_never_extrapolates():
    assert timeline.interp(KEYS, -5.0, ["zoom"]) == {"zoom": 1.0}
    assert timeline.interp(KEYS, 4.0, ["zoom"]) == {"zoom": 1.0}
    assert timeline.interp(KEYS, 99.0, ["zoom"]) == {"zoom": 1.0}
    assert timeline.interp(KEYS[:1], 7.0, ["zoom", "center"]) == {"zoom": 1.0, "center": [0.5, 0.5]}


def test_interp_uses_the_arriving_keys_ease_and_blends_vectors():
    mid = timeline.interp(KEYS, 1.0, ["zoom", "center"])          # 0→2 は到達側 (in_out) の曲線
    assert mid["zoom"] == pytest.approx(1.1) and mid["center"] == pytest.approx([0.55, 0.45])
    quarter = timeline.interp(KEYS, 0.5, ["zoom"])["zoom"]
    assert quarter == pytest.approx(1.0 + 0.2 * timeline.ease("in_out", 0.25)) and quarter < 1.05
    back = timeline.interp(KEYS, 3.0, ["zoom"])["zoom"]           # 2→4 は linear
    assert back == pytest.approx(1.1)


def test_interp_is_monotone_between_monotone_keys():
    keys = [{"t": 0.0, "v": 0.0}, {"t": 1.0, "v": 1.2, "ease": "out"}, {"t": 10.0, "v": 0.9, "ease": "in"}]
    up = [timeline.interp_scalar(keys, t) for t in np.linspace(0, 1, 101)]
    assert all(b >= a for a, b in zip(up, up[1:])) and up[0] == 0.0 and up[-1] == pytest.approx(1.2)
    down = [timeline.interp_scalar(keys, t) for t in np.linspace(1, 10, 101)]
    assert all(b <= a for a, b in zip(down, down[1:])) and down[-1] == pytest.approx(0.9)


def test_interp_requires_keys():
    with pytest.raises(ValueError):
        timeline.interp([], 0.0, ["v"])


def test_frame_count_and_time():
    assert timeline.frame_count(10.0, 24) == 240
    assert timeline.frame_count(1.5, 12) == 18
    assert timeline.frame_count(0.001, 24) == 1
    assert timeline.frame_count(10.0, 23.976) == round(10.0 * 23.976)
    assert timeline.frame_time(0, 24) == 0.0
    assert timeline.frame_time(48, 24, start=5.0) == pytest.approx(7.0)


def test_seeded_rng_is_stable_and_label_separated():
    a = timeline.seeded_rng(7, "fog:x").random(5)
    assert np.array_equal(a, timeline.seeded_rng(7, "fog:x").random(5))
    assert not np.array_equal(a, timeline.seeded_rng(8, "fog:x").random(5))
    assert not np.array_equal(a, timeline.seeded_rng(7, "fog:y").random(5))
    # 乱数列の固定: numpy (2.3.5 に pin) が PCG64 の列を変えたら決定論の前提が崩れるので、更新時に検知する
    assert timeline.seeded_rng(0, "x").random() == pytest.approx(0.8018812680339334, abs=1e-15)


def test_flicker_properties():
    phases = timeline.flicker_phases(7, "flame")
    assert phases == timeline.flicker_phases(7, "flame") and phases != timeline.flicker_phases(8, "flame")
    assert timeline.flicker(1.234, 0.0, 5.0, phases) == 1.0
    samples = [timeline.flicker(t, 1.0, 5.0, phases) for t in np.linspace(0, 4, 2000)]
    assert min(samples) >= 0.0 and max(samples) > 1.2 and min(samples) < 0.8      # 振れるが負にはならない
    assert abs(np.mean(samples) - 1.0) < 0.05
    assert all(math.isfinite(s) for s in samples)
