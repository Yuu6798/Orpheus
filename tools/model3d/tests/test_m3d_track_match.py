"""check.track_match（2 つの台本の値を同じ T どうしで比べる）: 欄のたどり方、同じコマと描き直すコマの範囲、差の許し、見ない欄、片方にしか
無い T、記録の種類、悪いパラメーター、工程の受け渡し（測るだけ）。値は作り物。リポジトリ外の 3 作目の VRM のダンスの台本での確かめは
docs/model3d.md。"""
import json

import numpy as np
import pytest

from model3d import track_match
from model3d.io import ToolError, atomic_json


def _cameras(Ts, shift=None):
    shift = shift or {}
    frames = []
    for T in Ts:
        M = np.eye(4)
        M[0, 3] = 0.1 * T + shift.get(T, 0.0)
        frames.append({"T": T, "f": T, "matrix": M.tolist(), "focal_px": 2000.0, "principal_px": [540.0, 960.0]})
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [1080, 1920], "frames": frames}


def test_nested_values_are_flattened_with_their_paths():
    flat = track_match.flatten({"camera": {"location": [1, 2, 3], "lens": 35}, "stage": "led", "rims": [{"energy": 9}]})
    assert flat == {"camera.lens": 35, "camera.location[0]": 1, "camera.location[1]": 2, "camera.location[2]": 3,
                    "rims[0].energy": 9, "stage": "led"}


def test_same_and_redo_frames_come_in_ranges():
    left, right = _cameras(range(1, 11)), _cameras(range(1, 11), shift={3: 0.5, 4: 1e-12, 8: 0.2})
    report = track_match.compare(left, right)
    assert report["same"] == [[1, 2], [5, 7], [9, 10]] and report["redo"] == [[3, 4], [8, 8]] and report["redo_T"] == [3, 4, 8]
    assert report["same_frames"] == 7 and report["redo_frames"] == 3 and report["pass"] is None
    assert report["max_diff_by_field"]["matrix"] == {"max": pytest.approx(0.5), "T": 3}
    assert [r["T"] for r in report["largest"]] == [3, 8, 4] and report["rows"][2]["differing"] == ["matrix"]
    loose = track_match.compare(left, right, tolerance=1e-9)
    assert loose["redo"] == [[3, 3], [8, 8]]                                        # 差の許しの中は同じ
    blind = track_match.compare(left, right, ignore=["matrix"])
    assert blind["redo"] == [] and blind["same"] == [[1, 10]]


def test_values_that_are_not_numbers_and_missing_fields_differ():
    a = {"schema_version": "model3d-stage-track/0.1", "frames": [{"T": 1, "f": 1, "stage": "led"}, {"T": 2, "f": 2},
                                                                    {"T": 3, "f": 3, "tone": {"gain": 1.0}}]}
    b = {"schema_version": "model3d-stage-track/0.1", "frames": [{"T": 1, "f": 1, "stage": "void"}, {"T": 2, "f": 2, "stage": "led"},
                                                                    {"T": 3, "f": 3, "tone": {"gain": 1.0}}, {"T": 4, "f": 4}]}
    report = track_match.compare(a, b)
    assert report["redo_T"] == [1, 2, 4] and report["same"] == [[3, 3]] and report["only_right"] == [4]
    assert report["rows"][0]["differing"] == ["stage"] and report["rows"][3] == {"T": 4, "same": False, "only": "right"}
    assert track_match.compare(a, b, ignore=["stage"])["redo_T"] == [4]
    assert track_match.compare(_cameras([1]), _cameras([1]), ignore=["f"])["redo"] == []


def test_frames_only_in_the_previous_version_are_not_redone():
    # left は前の版、right は今の版。今の版で消えたコマ（5）は描かないので、only に渡す並びに入れない（入れると描く工程が止まる）
    left, right = _cameras(range(1, 6)), _cameras([1, 2, 3, 4, 6], shift={2: 0.3})
    report = track_match.compare(left, right)
    assert report["redo_T"] == [2, 6] and report["redo"] == [[2, 2], [6, 6]] and report["redo_frames"] == 2
    assert report["only_left"] == [5] and report["only_right"] == [6] and report["same"] == [[1, 1], [3, 4]]
    assert {"T": 5, "same": False, "only": "left"} in report["rows"] and report["frames"] == 6
    assert set(report["redo_T"]) <= {fr["T"] for fr in right["frames"]}
    right["resolution"] = [540, 960]                                                   # 記録の頭が違っても、消えたコマは描かない
    assert track_match.compare(left, right)["redo_T"] == [1, 2, 3, 4, 6]


def test_a_different_record_header_makes_every_frame_redo():
    left, right = _cameras([1, 2]), _cameras([1, 2])
    right["resolution"] = [540, 960]                                                   # 同じ焦点距離の画素でも、画面が違えば画角が違う
    report = track_match.compare(left, right)
    assert report["redo_T"] == [1, 2] and report["header_differing"] == ["resolution[0]", "resolution[1]"]
    assert report["rows"][0]["differing"] == ["(記録の頭) resolution"]
    right = {**_cameras([1, 2]), "notes": "別の版", "x_memo": 1}                         # notes と x_ の欄は見ない
    assert track_match.compare(left, right)["same"] == [[1, 2]]


def test_repeated_T_stops():
    doubled = _cameras([1, 1])
    with pytest.raises(ToolError, match="重なっています"):
        track_match.compare(doubled, _cameras([1]))


def _run(tmp_path, left, right, parameters=None):
    from model3d import executor
    atomic_json(tmp_path / "left.json", left)
    atomic_json(tmp_path / "right.json", right)
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "match",
                                           "inputs": {"left": {"path": "left.json"}, "right": {"path": "right.json"}},
                                           "steps": [{"id": "match", "operation": "check.track_match",
                                                      "inputs": {"left": "$inputs.left", "right": "$inputs.right"},
                                                      "parameters": parameters or {}, "outputs": {"report": "match.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_the_operation_writes_the_ranges_and_measures_only(tmp_path):
    result = _run(tmp_path, _cameras(range(1, 6)), _cameras(range(1, 6), shift={2: 0.3}), {"report_only": True})
    assert result["status"] == "succeeded", result["error"]
    report = json.loads(open(result["steps"]["match"]["artifacts"]["report"], encoding="utf-8").read())
    assert report["kind"] == "cameras" and report["redo_T"] == [2] and report["same"] == [[1, 1], [3, 5]]
    assert "rows" not in result["steps"]["match"]["observations"][0]
    assert _run(tmp_path, _cameras([1]), _cameras([1]))["status"] == "needs_review"   # 測るだけ（合否は無い）


@pytest.mark.parametrize("left, right, parameters, message", [
    (_cameras([1]), {"schema_version": "model3d-stage-track/0.1", "frames": [{"T": 1, "f": 1}]}, {}, "同じ種類"),
    ({"schema_version": "model3d-frames/0.1"}, _cameras([1]), {}, "コマの並びの記録"),
    (_cameras([1]), _cameras([1]), {"ignore": "f"}, "ignore"),
    (_cameras([1]), _cameras([1]), {"tolerance": -1}, "tolerance"),
    (_cameras([1]), _cameras([1]), {"top": 0}, "top"),
    (_cameras([1]), _cameras([1]), {"wobble": 1}, "知らないパラメーター"),
])
def test_bad_inputs_stop(tmp_path, left, right, parameters, message):
    result = _run(tmp_path, left, right, parameters)
    assert result["status"] == "failed" and message in result["error"]
