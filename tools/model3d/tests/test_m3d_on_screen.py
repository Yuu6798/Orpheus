"""画面のどこに映るか（check.on_screen。ops/event_ops.py）: 画面の真ん中・横の外・カメラの後ろ・骨の点・margin、工程の受け渡し、
悪い設定と無いコマ。値は作り物（Blender なし）。"""
import json

import numpy as np
import pytest

from model3d import executor
from model3d.io import ToolError, atomic_json
from model3d.ops import event_ops


FPS = 30


def _path():
    """60 標本。左足は 20 で着地（25 cm から下りて止まる）、右足は 40 で着地。腰は 30 でいちばん低い。手は 45 でいちばん前（−y）。"""
    n = 60
    frames = list(range(1, n + 1))
    i = np.arange(n)
    foot_l = np.where(i < 12, 0.05, np.where(i < 20, 0.25 - 0.025 * (i - 12), 0.05))
    foot_r = np.where(i < 32, 0.05, np.where(i < 40, 0.25 - 0.025 * (i - 32), 0.05))
    hips = 1.0 - 0.3 * np.exp(-((i - 30) / 5.0) ** 2)
    hand_y = -0.4 * np.exp(-((i - 45) / 4.0) ** 2)
    col = lambda z, y=None: [[0.0, float(y[k]) if y is not None else 0.0, float(z[k])] for k in range(n)]
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "fps": FPS, "frames": frames, "bones": {},
            "points": {"foot_l": col(foot_l), "foot_r": col(foot_r), "hips": col(hips), "hand": col(np.full(n, 1.2), hand_y)}}


def _cameras():
    """カメラは (0, −5, 1) から +y を水平に見る（右 +x・上 +z）。100 × 200 画素、焦点 100 画素。"""
    matrix = [[1, 0, 0, 0], [0, 0, -1, -5], [0, 1, 0, 1], [0, 0, 0, 1]]     # 列 = 右・上・後ろ
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [100, 200],
            "frames": [{"T": T, "f": T, "matrix": matrix, "focal_px": 100.0, "principal_px": [50.0, 100.0]} for T in (1, 2, 3)]}


def test_on_screen_places_points_on_the_frame():
    settings = event_ops.screen_settings({"points": {"centre": [0, 0, 1], "right": [3, 0, 1], "behind": [0, -6, 1],
                                                     "hand": {"point": "hand", "offset": [0, 0, 0.3]}}, "frames": [1, 2]})
    report = event_ops.on_screen(settings, _cameras(), _path())
    rows = {(r["T"], r["point"]): r for r in report["rows"]}
    assert rows[(1, "centre")]["x"] == pytest.approx(0.5) and rows[(1, "centre")]["y"] == pytest.approx(0.5) and rows[(1, "centre")]["depth"] == pytest.approx(5)
    assert rows[(1, "right")]["x"] == pytest.approx((50 + 100 * 3 / 5) / 100) and not rows[(1, "right")]["inside"]
    assert rows[(1, "behind")]["behind"] and rows[(1, "behind")]["x"] is None
    assert rows[(2, "hand")]["y"] == pytest.approx((100 - 100 * 0.5 / 5) / 200)                  # 手 (0, 0, 1.2) + 0.3 → 上へ 0.5 m
    assert report["outside"] == 4 and report["behind"] == 2 and report["pass"] is False and len(report["failures"]) == 4
    narrow = event_ops.on_screen(event_ops.screen_settings({"points": {"centre": [0, 0, 1]}, "margin": 0.3}), _cameras())
    assert narrow["pass"] is True and narrow["frames"] == 3


@pytest.mark.parametrize("parameters, message", [
    ({}, "points"),
    ({"points": {"a": [0, 0]}}, "3 つの数"),
    ({"points": {"a": {"camera": [0, 0, 1]}}}, "point"),
    ({"points": {"a": [0, 0, 0]}, "frames": [1.5]}, "整数"),
    ({"points": {"a": [0, 0, 0]}, "margin": 0.5}, "0.5 より小さく"),
])
def test_bad_screen_settings_stop(parameters, message):
    with pytest.raises(ToolError, match=message):
        event_ops.screen_settings(parameters)


def test_on_screen_needs_the_path_and_known_frames():
    with pytest.raises(ToolError, match="path"):
        event_ops.on_screen(event_ops.screen_settings({"points": {"h": {"point": "hand"}}}), _cameras())
    with pytest.raises(ToolError, match="無いコマ"):
        event_ops.on_screen(event_ops.screen_settings({"points": {"a": [0, 0, 0]}, "frames": [9]}), _cameras())


def test_the_on_screen_operation_reports(tmp_path):
    atomic_json(tmp_path / "cameras.json", _cameras())
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "screen", "inputs": {"cameras": {"path": "cameras.json"}},
              "steps": [{"id": "screen", "operation": "check.on_screen", "inputs": {"cameras": "$inputs.cameras"},
                         "parameters": {"points": {"a": [0, 0, 1], "b": [9, 0, 1]}}, "outputs": {"report": "screen.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "needs_review"                                  # b が画面の外
    report = json.loads(open(result["steps"]["screen"]["artifacts"]["report"], encoding="utf-8").read())
    assert len(report["rows"]) == 6 and report["outside"] == 3
    recipe["steps"][0]["parameters"]["points"] = {"a": [0, 0, 1]}
    atomic_json(tmp_path / "recipe.json", recipe)
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["status"] == "succeeded"
