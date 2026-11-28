"""camera.curve: smooth（キーを通って速さが続く曲線）と timeline.SmoothCurve: キーを通る・傾きがつながる・zoom はキーの値を越えない・
中心は角を丸めて通り、ほぼ折り返す所だけで止まる・既定（ease）の spec の正規形は変わらない。"""
import math

import jsonschema
import numpy as np
import pytest

from mv_pipeline.fx import camera, timeline
from mv_pipeline.fx import spec as fxspec
from mv_pipeline.io import PipelineError


def test_curve_passes_the_points_and_its_slope_is_continuous():
    times = [0.0, 1.0, 2.5, 4.0]
    values = [[0.0, 0.2, 0.5], [0.3, 0.4, 0.5], [0.5, 0.6, 0.6], [0.1, 0.8, 0.4]]
    curve = timeline.SmoothCurve(times, values, path=(1, 2))
    for t, value in zip(times, values):
        assert np.allclose(curve(t), value)
    for t in times[1:-1]:                                                     # 点の前後で傾きがつながる
        assert np.allclose(curve.derivative(t - 1e-7), curve.derivative(t + 1e-7), atol=1e-4)
    assert np.allclose(curve(-1.0), values[0]) and np.allclose(curve(9.0), values[-1])     # 範囲の外は端の値


def test_monotone_columns_stay_between_the_points():
    times = [0.0, 1.0, 1.4, 4.0]
    values = [[0.0], [0.30], [0.32], [0.0]]                                  # 山（zoom の寄り）を越えない
    curve = timeline.SmoothCurve(times, values)
    samples = [float(curve(t)[0]) for t in np.linspace(0, 4, 401)]
    assert max(samples) <= 0.32 + 1e-12 and min(samples) >= -1e-12
    assert abs(curve.derivative(1.4)[0]) < 1e-12                             # 増減が変わる点では傾き 0
    assert curve.derivative(1.0)[0] > 0                                       # 増え続ける点では止まらない


def test_the_center_rounds_corners_and_stops_only_where_it_nearly_turns_back():
    corner = timeline.SmoothCurve([0, 1, 2], [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], path=(0, 1))      # 90 度の角
    assert np.hypot(*corner.derivative(1.0)) > 0.4
    back = timeline.SmoothCurve([0, 1, 2], [[0.0, 0.0], [1.0, 0.0], [0.0, 0.05]], path=(0, 1))       # ほぼ折り返す
    assert np.allclose(back.derivative(1.0), 0.0)
    fast = timeline.SmoothCurve([0, 1, 1.2], [[0.0, 0.0], [0.1, 0.0], [1.1, 0.0]], path=(0, 1))     # 遅い区間と速い区間
    assert np.hypot(*fast.derivative(1.0)) <= 2 * 0.1 + 1e-9                  # 遅い方の速さの 2 倍まで（遅い区間で行き過ぎない）


def spec(curve=None):
    camera_raw = {"keys": [{"t": 0, "zoom": 1.2, "center": [0.45, 0.5]}, {"t": 2, "zoom": 1.5, "center": [0.5, 0.45]},
                           {"t": 4, "zoom": 1.0, "center": [0.5, 0.5]}], "frame": "source", "clamp": True, "plate": "full"}
    if curve:
        camera_raw["curve"] = curve
    return {"schema_version": "fx-shot/0.3", "output": {"width": 320, "height": 180}, "duration_s": 4, "assets": {"base": "b.png"},
            "camera": camera_raw}


def test_spec_keeps_curve_only_when_smooth_and_the_camera_follows_it():
    assert "curve" not in fxspec.parse_spec(spec(), ".").data["camera"]                  # 既定（ease）の正規形は前と同じ
    assert "curve" not in fxspec.parse_spec(spec("ease"), ".").data["camera"]
    data = fxspec.parse_spec(spec("smooth"), ".").data["camera"]
    assert data["curve"] == "smooth"
    with pytest.raises((PipelineError, jsonschema.ValidationError)):
        fxspec.parse_spec(spec("spline"), ".")
    path = camera.CameraPath(data, 4.0, (320, 180), plate=(640, 360))
    curve = timeline.SmoothCurve([0, 2, 4], [[math.log(1.2), 0.45, 0.5], [math.log(1.5), 0.5, 0.45], [0.0, 0.5, 0.5]], path=(1, 2),
                                 scale=(640, 360))
    for t in (0.5, 1.3, 2.0, 3.1):
        zoom, cx, cy = path.at(t)
        u, x, y = curve(t)
        assert zoom == pytest.approx(math.exp(u)) and (cx, cy) == pytest.approx((x, y), abs=1e-9)
    eased = camera.CameraPath(fxspec.parse_spec(spec(), ".").data["camera"], 4.0, (320, 180), plate=(640, 360))
    assert eased.at(1.0)[0] == pytest.approx(1.35)                            # 既定は今までどおり直線
