"""plan.scout（向きの下見）: 行（拍か時刻）× 列（方位）のコマの並び、動きのコマ、狙う点（高さの下限と上乗せ）、球の座標のカメラと向き、
通り道の読み方、悪いパラメーター、工程の受け渡しと render.passes へそのまま渡せること。値は作り物（どの曲とも関係ない）。
リポジトリ外の 3 作目の VRM のダンスの下見の台本との照合は docs/model3d.md。"""
import math

import numpy as np
import pytest

from model3d import contracts, scout_plan
from model3d.io import ToolError, atomic_json
from model3d.ops import plan_ops


def _path(count=120, z=1.0):
    """1/30 秒おき（動きのコマ 1〜count）に、腰が x の向きへ 1 秒に 1 m 進む通り道。"""
    t = np.arange(count) / 30.0
    hips = np.stack([t, np.zeros(count), np.full(count, z)], axis=1)
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": [1.0 + k for k in range(count)], "bones": {},
            "points": {"hips": hips.tolist()}}


def _build(parameters, path=None):
    settings = plan_ops._scout_settings(parameters)
    cameras, track, report = scout_plan.build(settings, path or _path())
    contracts.validate("cameras", cameras)
    contracts.validate("stage-track", track)
    return cameras["frames"], track["frames"], report


def test_rows_are_beats_and_columns_are_angles():
    frames, track, report = _build({"beats": [0, 2], "grid": {"period": 0.5, "k0_time": 0.1}, "angles": [-90, 0, 90]})
    assert [fr["T"] for fr in frames] == list(range(1, 10)) and report["columns"] == 3
    assert [(row["beat"], row["time"], row["f"], row["T"]) for row in report["rows"]] == [
        (0, 0.1, 4, [1, 3]), (1, 0.6, 19, [4, 6]), (2, 1.1, 34, [7, 9])]       # f = 1 + round(30 × 時刻)
    assert [fr["f"] for fr in track] == [4, 4, 4, 19, 19, 19, 34, 34, 34]


def test_the_camera_circles_the_aim_on_a_sphere_and_looks_at_it_level():
    frames, _, _ = _build({"times": [1.0], "angles": [0, 90], "el": -4.0, "dist": 3.1, "dz": 0.22, "aim_floor": 0.75})
    aim = np.array([1.0, 0.0, 1.0 + 0.22])                                        # 1 秒の腰（x 1 m）、高さ 1 + 0.22
    e = math.radians(-4.0)
    front, side = (np.array(fr["matrix"]) for fr in frames)
    assert np.allclose(front[:3, 3], aim + np.array([0.0, -math.cos(e), math.sin(e)]) * 3.1)
    assert np.allclose(side[:3, 3], aim + np.array([math.cos(e), 0.0, math.sin(e)]) * 3.1)    # 正の方位は +X 側
    for M in (front, side):
        forward = -M[:3, 2]
        assert np.allclose(forward, (aim - M[:3, 3]) / np.linalg.norm(aim - M[:3, 3]))       # 狙う点を向く
        assert abs(M[2, 0]) < 1e-12                                                            # 傾けない（右が水平）
    assert frames[0]["lens_mm"] == 35.0 and frames[0]["focal_px"] == pytest.approx(1920 * 35.0 / 36.0)
    dancer = np.array([1.0, 0.0, 1.0])                                            # 踊り手の点は、高さを足す前の点
    assert frames[0]["dancer"] == dancer.tolist() and frames[0]["distance_m"] == pytest.approx(np.linalg.norm(dancer - front[:3, 3]))


def test_the_aim_does_not_sink_below_the_floor_value():
    low = _path(z=0.5)
    floored, _, _ = _build({"times": [0.0], "angles": [0]}, low)
    assert np.array(floored[0]["matrix"])[2, 3] == pytest.approx(0.75 + 0.22 + 3.1 * math.sin(math.radians(-4.0)))
    free, _, _ = _build({"times": [0.0], "angles": [0], "aim_floor": None}, low)
    assert np.array(free[0]["matrix"])[2, 3] == pytest.approx(0.5 + 0.22 + 3.1 * math.sin(math.radians(-4.0)))


def test_the_point_of_a_frame_is_the_sample_itself_or_between_samples():
    path = _path()
    reader = scout_plan.Reader(path, "hips", 1.0, 30.0)
    exact, outside = reader.at(31)
    assert exact.tolist() == path["points"]["hips"][30] and not outside        # そのコマの標本そのもの
    half = {**path, "frames": [1.0 + 2 * k for k in range(60)], "points": {"hips": path["points"]["hips"][:60]}}
    between, _ = scout_plan.Reader(half, "hips", 1.0, 30.0).at(4)
    assert between[0] == pytest.approx((path["points"]["hips"][1][0] + path["points"]["hips"][2][0]) / 2)
    assert scout_plan.Reader(path, "hips", 1.0, 30.0).at(500)[1]                  # 通り道の外


@pytest.mark.parametrize("parameters, message", [
    ({}, "beats"),
    ({"beats": [0, 1]}, "grid"),
    ({"beats": [0, 1], "grid": {"period": 0.5, "k0_time": 0}, "times": [1.0]}, "どちらか 1 つ"),
    ({"beats": [2, 1], "grid": {"period": 0.5, "k0_time": 0}}, "最初 ≤ 最後"),
    ({"times": [1.0], "grid": {"period": 0.5, "k0_time": 0}}, "beats といっしょ"),
    ({"times": []}, "1 つ以上"),
    ({"times": [1.0], "angles": []}, "angles"),
    ({"times": [1.0], "el": 95}, "el"),
    ({"times": [1.0], "dist": 0}, "dist"),
    ({"times": [1.0], "lens": -1}, "lens"),
    ({"times": [1.0], "aim": ""}, "aim"),
    ({"times": [1.0], "dance": {"fps": 0}}, "dance.fps"),
    ({"times": [1.0], "camera": {"resolution": [1080]}}, "resolution"),
    ({"times": [1.0], "zoom": 2}, "知らないパラメーター"),
    ({"times": [float("nan")]}, "数"),
])
def test_bad_parameters_stop(parameters, message):
    with pytest.raises(ToolError, match=message):
        plan_ops._scout_settings(parameters)


def test_the_operation_writes_cameras_that_render_passes_takes(tmp_path):
    from model3d import executor
    from model3d.blender_adapter import pass_render, stage_render
    atomic_json(tmp_path / "bones.json", _path())
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "scout",
                                           "inputs": {"bones": {"path": "bones.json"}},
                                           "steps": [{"id": "scout", "operation": "plan.scout", "inputs": {"bones": "$inputs.bones"},
                                                      "parameters": {"beats": [1, 3], "grid": {"period": 0.5, "k0_time": 0.0}},
                                                      "outputs": {"cameras": "cameras.json", "track": "track.json",
                                                                  "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    step = result["steps"]["scout"]
    assert step["observations"][0] == {"kind": "scout_plan", "frames": 21, "rows": 3, "columns": 7, "f": [16, 46],
                                       "outside_samples": 0}
    cameras = contracts.load("cameras", step["artifacts"]["cameras"])
    track = contracts.load("stage-track", step["artifacts"]["track"])
    plan = stage_render.frame_plan(track, 0, 0)
    assert pass_render.cameras_problem(plan, pass_render.record_cameras(cameras)) is None     # render.passes にそのまま渡せる
