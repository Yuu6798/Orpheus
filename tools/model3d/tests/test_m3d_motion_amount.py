"""check.motion_amount（区間ごとの動きの量）: 速さ（真ん中の時刻で区間に入れる・組の平均）、頭の上下の幅、体の向き（正面が 0・
反時計回りが正・±180° をまたいでもつなぐ）、止めの拍（拍の窓・しきい値・続いた拍の数）、区間の秒と拍、悪いパラメーター、工程の受け渡し
（数を出すだけ）。値は作り物（どの踊りとも関係ない）。リポジトリ外の 3 作目の VRM のダンスの数との比べは docs/model3d.md。"""
import json
import math

import numpy as np
import pytest

from model3d import motion_amount
from model3d.io import ToolError, atomic_json
from model3d.ops import amount_ops


def _path(count=61, fps=30.0, **points):
    """1/fps 秒おき（動きのコマ 1〜count）の通り道。既定は、止まった腰・両手・両足・頭の先・両肩（正面を向く）。"""
    still = {"hips": [0.0, 0.0, 1.0], "hand_l": [0.3, 0.0, 1.0], "hand_r": [-0.3, 0.0, 1.0], "foot_l": [0.1, 0.0, 0.0],
             "foot_r": [-0.1, 0.0, 0.0], "head_top": [0.0, 0.0, 1.7], "shoulder_l": [0.2, 0.0, 1.4], "shoulder_r": [-0.2, 0.0, 1.4]}
    columns = {name: np.tile(value, (count, 1)) for name, value in still.items()}
    columns.update({name: np.asarray(value, float) for name, value in points.items()})
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": [1.0 + k * 30.0 / fps for k in range(count)], "bones": {},
            "points": {name: values.tolist() for name, values in columns.items()}}


def _report(path, **parameters):
    parameters.setdefault("sections", [{"name": "全部", "from": 0.0, "until": 10.0}])
    return motion_amount.build(amount_ops.settings_of(parameters), path)


def test_speeds_average_the_steps_whose_middle_is_in_the_section():
    count = 61
    x = np.arange(count) / 30.0 * 0.5                                             # 腰が 0.5 m/s で進む
    hand = np.zeros((count, 3))
    hand[:, 2] = 1.0 + np.arange(count) / 30.0                                   # 左手だけ 1 m/s で上がる
    report = _report(_path(hips=np.stack([x, np.zeros(count), np.ones(count)], axis=1), hand_l=hand),
                     sections=[{"name": "前", "from": 0.0, "until": 1.0}, {"from": 1.0, "until": 2.0}])
    first, second = report["sections"]
    assert first["name"] == "前" and second["name"] == "区間 2"
    assert first["speeds_m_s"]["hips"] == pytest.approx(0.5) and first["speeds_m_s"]["hands"] == pytest.approx(0.5)   # 左右の平均
    assert first["speeds_m_s"]["feet"] == 0.0 and first["samples"] == 30 and second["samples"] == 30
    assert report["pass"] is None and "測るだけ" in report["reason"]


def test_the_rise_is_the_height_range_of_the_head_point():
    count = 61
    head = np.tile([0.0, 0.0, 1.7], (count, 1))
    head[10:20, 2] = 1.3                                                          # しゃがむ
    report = _report(_path(head_top=head), sections=[{"from": 0.0, "until": 1.0}, {"from": 1.0, "until": 2.1}])
    assert report["sections"][0]["rise_m"] == pytest.approx(0.4) and report["sections"][1]["rise_m"] == 0.0


def test_facing_is_zero_to_the_front_counterclockwise_positive_and_continuous():
    count = 91
    turn = np.radians(np.linspace(0.0, 540.0, count))                              # 3 秒で 1 回転半（上から見て反時計回り）
    left = np.stack([0.2 * np.cos(turn), 0.2 * np.sin(turn), np.full(count, 1.4)], axis=1)
    right = -left * [1, 1, 0] + [0, 0, 1.4]
    angles = motion_amount.facing_angles(left, right)
    assert angles[0] == pytest.approx(0.0, abs=1e-9)                               # 左が +X なら正面（−Y）
    assert angles[30] == pytest.approx(180.0) and angles[-1] == pytest.approx(540.0)
    report = _report(_path(count, shoulder_l=left, shoulder_r=right), sections=[{"from": 0.0, "until": 3.1}])
    assert report["sections"][0]["facing_range_deg"] == pytest.approx(540.0)
    assert report["sections"][0]["facing_turn_deg"] == pytest.approx(540.0)
    side = np.tile([[0.0, 0.2, 1.4]], (count, 1))                                 # 左の点が +Y: 右を向いて +X が前
    assert motion_amount.facing_angles(side, -side * [1, 1, 0] + [0, 0, 1.4])[0] == pytest.approx(90.0)


def test_holds_count_runs_of_still_beats():
    count = 121                                                                    # 4 秒、拍は 0.5 秒おき（拍 0〜7）
    t = np.arange(count) / 30.0
    moving = (t < 1.0) | ((t >= 2.5) & (t < 3.0))                                # 拍 0・1 と拍 5 で手が動く
    x = np.cumsum(np.where(moving, 1.0 / 30.0, 0.0))                              # 動く間は 1 m/s
    hand = np.stack([x, np.zeros(count), np.ones(count)], axis=1)
    path = _path(count, hand_l=hand, hand_r=hand * [1, 1, 1] + [-0.6, 0, 0])
    report = _report(path, grid={"period": 0.5, "k0_time": 0.0}, sections=[{"from": {"beat": 0}, "until": {"beat": 8}}])
    still = [row["beat"] for row in report["beats"] if row["still"]]
    assert still == [2, 3, 4, 6, 7] and [row["beat"] for row in report["beats"]][-1] == 7
    holds = report["sections"][0]["holds"]
    assert holds["runs"] == [{"beat": 2, "beats": 3, "time": 1.0}, {"beat": 6, "beats": 2, "time": 3.0}]
    assert holds["still_beats"] == 5 and holds["longest"] == 3 and holds["held_beats"] == 5
    longer = _report(path, grid={"period": 0.5, "k0_time": 0.0}, holds={"min_beats": 3},
                     sections=[{"from": 0.0, "until": 4.0}])["sections"][0]["holds"]
    assert longer["runs"] == [{"beat": 2, "beats": 3, "time": 1.0}]
    nogrid = _report(path, sections=[{"from": 0.0, "until": 4.0}])
    assert nogrid["beats"] == [] and "holds" not in nogrid["sections"][0]


@pytest.mark.parametrize("parameters, message", [
    ({}, "sections"),
    ({"sections": [{"from": 0, "until": 1, "speed": 1}]}, "sections\\[0\\]"),
    ({"sections": [{"from": {"beat": 0}, "until": 1}]}, "grid"),
    ({"sections": [{"from": 0, "until": 1}], "grid": {"period": 0}}, "grid"),
    ({"sections": [{"from": 0, "until": 1}], "speeds": {"hands": []}}, "speeds.hands"),
    ({"sections": [{"from": 0, "until": 1}], "facing": ["a", "a"]}, "facing"),
    ({"sections": [{"from": 0, "until": 1}], "holds": {"min_beats": 0}}, "min_beats"),
    ({"sections": [{"from": 0, "until": 1}], "holds": {"speed": -1}}, "holds.speed"),
    ({"sections": [{"from": 0, "until": 1}], "dance": {"fps": 0}}, "dance.fps"),
    ({"sections": [{"from": 0, "until": 1}], "wobble": 1}, "知らないパラメーター"),
])
def test_bad_parameters_stop(parameters, message):
    with pytest.raises(ToolError, match=message):
        amount_ops.settings_of(parameters)


def test_missing_points_and_empty_sections_stop():
    with pytest.raises(ToolError, match="点がありません"):
        _report(_path(), speeds={"hands": ["wrist_l"]})
    with pytest.raises(ToolError, match="標本がありません"):
        _report(_path(), sections=[{"from": 50.0, "until": 60.0}])
    with pytest.raises(ToolError, match="より後ではありません"):
        _report(_path(), sections=[{"from": 1.0, "until": 1.0}])


def test_the_operation_writes_the_report_and_measures_only(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "path.json", _path())
    params = {"sections": [{"name": "前半", "from": 0.0, "until": 1.0}, {"name": "後半", "from": 1.0, "until": 2.0}],
              "grid": {"period": 0.5, "k0_time": 0.0}}
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "amount", "inputs": {"path": {"path": "path.json"}},
              "steps": [{"id": "amount", "operation": "check.motion_amount", "inputs": {"path": "$inputs.path"},
                         "parameters": params, "outputs": {"report": "amount.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "needs_review"                                    # 数を出すだけ（合否は無い）
    report = json.loads(open(result["steps"]["amount"]["artifacts"]["report"], encoding="utf-8").read())
    assert [s["name"] for s in report["sections"]] == ["前半", "後半"] and len(report["beats"]) == 4
    recipe["steps"][0]["parameters"] = {**params, "report_only": True}
    atomic_json(tmp_path / "recipe.json", recipe)
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["status"] == "succeeded"
    assert math.isfinite(report["whole"]["speeds_m_s"]["hips"])
