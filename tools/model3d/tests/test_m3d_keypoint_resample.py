import json

import numpy as np
import pytest

from model3d import executor, keypoint_resample
from model3d.io import atomic_json
from model3d.ops import get


def clip(values, fps=2.0):
    values = np.asarray(values, dtype=np.float32)
    return {
        "pred_keypoints_3d": np.broadcast_to(values[:, None, None], (len(values), 70, 3)),
        "pred_cam_t": np.broadcast_to(values[:, None], (len(values), 3)),
        "fps": np.float64(fps),
    }


def test_pchip_matches_known_interpolation_and_holds_last_frame():
    arrays = clip([0, 1, 1.5, 2, 0])
    converted, report = keypoint_resample.resample(arrays, 4)
    # Interior slopes at the first sample pair are 1.25 and 2/3 values per input frame.
    expected = [0, 55 / 96, 1, 61 / 48, 1.5, 29 / 16, 2, 45 / 32, 0, 0]
    np.testing.assert_allclose(converted["pred_keypoints_3d"][:, 0, 0], expected)
    np.testing.assert_allclose(converted["pred_cam_t"][:, 0], expected)
    np.testing.assert_array_equal(converted["t"], np.arange(10) / 4)
    assert converted["pred_cam_t"].dtype == np.float32
    assert report == {"source_fps": 2, "target_fps": 4, "source_frames": 5, "target_frames": 10}


def test_metadata_flags_and_single_frame():
    arrays = clip([0, 1, 2], fps=4)
    matrix = np.eye(3)
    arrays.update(valid=np.array([True, False, True]), used_full_image_box=np.array([False, True, False]),
                  cam_int_fixed=matrix, camera_name=np.array(["a", "b", "c"]), t=np.array([2., 2.25, 2.5]))
    converted, report = keypoint_resample.resample(arrays, 8)
    np.testing.assert_array_equal(converted["valid"], [True, False, False, False, True, True])
    np.testing.assert_array_equal(converted["used_full_image_box"], [False, True, True, True, False, False])
    np.testing.assert_array_equal(converted["cam_int_fixed"], matrix)
    assert converted["camera_name"].tolist() == list("abbccc")
    np.testing.assert_array_equal(converted["t"], 2 + np.arange(6) / 8)
    assert report["invalid_frames"] == {"source": 1, "target": 3}
    converted, _ = keypoint_resample.resample(clip([1.5], fps=24), 120)
    np.testing.assert_array_equal(converted["pred_cam_t"][:, 0], [1.5] * 5)


def test_resample_operation_runs_then_prepares_in_a_recipe(tmp_path):
    # MHR70 の合成の踊りを、既存の下ごしらえで読めることまで確認する。
    from test_m3d_motion import write_npz

    write_npz(tmp_path / "clip.npz")
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1",
        "name": "resample",
        "inputs": {"clip": {"path": "clip.npz"}},
        "steps": [
            {
                "id": "resample", "operation": "motion.resample_keypoints",
                "inputs": {"keypoints": "$inputs.clip"}, "parameters": {"source_fps": 24, "target_fps": 30},
                "outputs": {"keypoints": "keypoints.npz", "report": "report.json"},
            },
            {
                "id": "prepare", "operation": "motion.prepare_keypoints",
                "inputs": {"keypoints": "$steps.resample.keypoints"}, "parameters": {"pitch_deg": 0},
                "outputs": {"points": "points.npz"},
            },
        ],
    })
    run = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert run["status"] == "succeeded"
    with open(run["steps"]["resample"]["artifacts"]["report"], encoding="utf-8") as stream:
        report = json.load(stream)
    assert report["target_frames"] == 60
    with np.load(run["steps"]["prepare"]["artifacts"]["points"], allow_pickle=False) as data:
        assert data["points"].shape == (60, 70, 3)


@pytest.mark.parametrize("arrays, source, message", [
    ({}, None, "fps"),
    ({}, 4, "pred_keypoints_3d"),
    (clip([1, 2]), 4, "違います"),
    (dict(clip([1, 2]), t=np.array([0, 1])), None, "等間隔"),
    (dict(clip([1, 2]), pred_cam_t=np.array([np.nan, 1])), None, "pred_cam_t"),
])
def test_bad_input_is_reported(arrays, source, message, tmp_path):
    from model3d.io import ToolError

    path = tmp_path / "clip.npz"
    np.savez(path, **arrays)
    arguments = {"target_fps": 4}
    if source is not None:
        arguments["source_fps"] = source
    operation = get("motion.resample_keypoints").function
    with pytest.raises(ToolError, match=message):
        operation({"keypoints": path}, arguments, {"keypoints": tmp_path / "result.npz"}, None)


def test_invalid_neighbours_do_not_distort_a_valid_interpolation():
    arrays = dict(clip([50, 0, 10, 100]), valid=np.array([False, True, True, True]))
    output, _ = keypoint_resample.resample(arrays, 4)
    assert not output["valid"][1]
    assert output["valid"][3]
    assert output["pred_keypoints_3d"][3, 0, 0] == 5.0
    assert output["pred_cam_t"][3, 0] == 5.0


def test_long_float32_timestamps_allow_rounding_and_detect_jitter():
    count = 3600
    arrays = clip(np.ones(count), fps=60)
    arrays["t"] = np.arange(count, dtype=np.float32) / np.float32(60)
    converted, report = keypoint_resample.resample(arrays, 30)
    assert report["target_frames"] == 1800
    np.testing.assert_array_equal(converted["t"], np.arange(1800) / 30)
    arrays["t"][-1] += np.float32(0.0003)
    with pytest.raises(ValueError, match="等間隔"):
        keypoint_resample.resample(arrays, 30)
