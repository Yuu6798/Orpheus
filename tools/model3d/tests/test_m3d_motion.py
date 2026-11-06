"""motion.prepare_keypoints: 合成の 70 点（SAM 3D Body と同じ並び）で、整える手順の各段を確かめる。実際の推定の精度は見ない。"""
import json
import math

import numpy as np
import pytest

from model3d import motion
from model3d.io import ToolError, atomic_json


def skeleton(frames=60, radius=0.4, period=240, sway=True):
    """合成の 70 点（x 右・y 奥・z 上、m、床 z = 0）。腰の中心がゆっくり円を描き、足の最下点は床に着く。"""
    rng = np.random.default_rng(7)
    body = np.zeros((70, 3))
    body[list(motion.UPPER)] = rng.uniform([-0.3, -0.1, 0.1], [0.3, 0.1, 0.8], (len(motion.UPPER), 3))
    body[0] = [0.0, 0.0, 0.75]                                   # 頭（腰の真上）
    for (hip, knee, ankle, foot), side in zip(motion.LEGS, (-1.0, 1.0)):
        body[hip] = [0.1 * side, 0.0, 0.0]
        body[knee] = [0.1 * side, -0.03, -0.45]
        body[ankle] = [0.1 * side, 0.0, -0.85]
        body[foot[1]] = [0.1 * side, -0.15, -0.9]                # つま先
        body[foot[2]] = [0.13 * side, -0.12, -0.9]
        body[foot[3]] = [0.1 * side, 0.05, -0.9]                 # かかと
    t = np.arange(frames)
    angle = 2 * math.pi * t / period
    centre = np.stack([radius * np.cos(angle), 3.0 + radius * np.sin(angle), np.full(frames, 0.9)], axis=1)
    if not sway:
        centre[:, :2] = [0.0, 3.0]
    return body[None] + centre[:, None]


def tilt(points, degrees, axis=(0.3, 1.0, 0.0)):
    """床に沿う軸（z 成分 0）のまわりに回す。最小の回転で戻せるので、水平化の結果を元と比べられる。"""
    axis = np.array(axis, float) / np.linalg.norm(axis)
    angle = math.radians(degrees)
    cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rotation = np.eye(3) + math.sin(angle) * cross + (1 - math.cos(angle)) * cross @ cross
    return points @ rotation.T


def to_camera(points, seed=3):
    """x 右・y 奥・z 上 → SAM 3D Body の形（カメラ座標の点と、コマごとの並進）。"""
    camera = np.stack([points[..., 0], -points[..., 2], points[..., 1]], axis=-1)
    translation = np.random.default_rng(seed).normal(0.0, 0.2, (len(points), 3)) + [0.0, 0.0, 3.0]
    return camera - translation[:, None], translation


def test_camera_frame_round_trip():
    points = skeleton(8)
    assert np.allclose(motion.from_camera(*to_camera(points)), points)


@pytest.mark.parametrize("window", [5, 11])
def test_savgol_matches_scipy_including_the_edges(window):
    signal = pytest.importorskip("scipy.signal")
    values = np.random.default_rng(1).normal(size=(40, 3))
    assert np.abs(motion.savgol(values, window) - signal.savgol_filter(values, window, 2, axis=0)).max() < 1e-10
    with pytest.raises(ValueError):
        motion.savgol(values, 4)


def test_a_one_frame_spike_is_replaced_and_steady_points_are_kept():
    points = skeleton(30)
    spiked = points.copy()
    spiked[20, 41] += [0.0, 0.12, 0.0]                           # 右手首が 1 コマだけ 12 cm 跳ぶ
    cleaned, replaced = motion.clean(spiked)
    assert replaced == 1
    assert np.abs(cleaned[20, 41] - points[20, 41]).max() < 0.005
    assert np.abs(motion.clean(points)[0] - points).max() < 1e-3


def test_a_tilted_floor_is_levelled_back():
    points = skeleton()
    keypoints, translation = to_camera(tilt(points, 9.0))
    prepared, stance, report = motion.prepare(keypoints, translation)
    upper = list(motion.UPPER)
    assert np.abs(prepared[:, upper] - points[:, upper]).max() < 2e-3
    assert abs(report["floor"]["measured_roll_deg"]) > 1 and abs(report["floor"]["measured_pitch_deg"]) > 1
    assert report["floor"]["pitch_overridden"] is False and abs(report["floor"]["lowered_m"]) < 2e-3
    levelled = motion.level(motion.clean(motion.from_camera(keypoints, translation))[0])[0]   # 足を止める前の床
    lowest = np.minimum(*(levelled[:, list(foot), 2].min(axis=1) for foot in motion.FEET))
    assert np.abs(lowest).max() < 2e-3 and stance.shape == (len(points), 2)


def test_pitch_override_replaces_the_fitted_pitch():
    keypoints, translation = to_camera(tilt(skeleton(), 9.0))
    fitted = motion.prepare(keypoints, translation)
    override = fitted[2]["floor"]["measured_pitch_deg"] + 6.0
    forced = motion.prepare(keypoints, translation, pitch_deg=override)
    assert forced[2]["floor"]["pitch_overridden"] is True
    assert forced[2]["floor"]["applied_pitch_deg"] == pytest.approx(override, abs=1e-3)
    assert forced[2]["floor"]["measured_pitch_deg"] == fitted[2]["floor"]["measured_pitch_deg"]
    assert np.abs(forced[0] - fitted[0]).max() > 0.01


def test_torso_back_turns_the_upper_body_away_from_the_camera():
    keypoints, translation = to_camera(skeleton())
    straight = motion.prepare(keypoints, translation)[0]
    leaned = motion.prepare(keypoints, translation, torso_back_deg=12.0)[0]

    def head_angle(points):
        head = points[:, 0] - points[:, list(motion.HIPS)].mean(axis=1)
        return np.degrees(np.arctan2(head[:, 1], head[:, 2]))
    assert np.allclose(head_angle(leaned) - head_angle(straight), 12.0, atol=0.05)   # 上の端が奥（y が増える向き）へ
    legs = list(range(9, 21))
    assert np.abs(leaned[:, legs] - straight[:, legs]).max() < 1e-9


def test_relative_contact_keeps_the_lower_foot_planted_when_the_whole_body_bobs():
    points = skeleton(72, radius=0.15)
    bob = np.where((np.arange(72) // 6) % 2 == 1, 0.07, 0.0)   # 6 コマごとに体ごと 7 cm 浮く（推定の揺れ）
    points[..., 2] += bob[:, None]
    keypoints, translation = to_camera(points)
    absolute = motion.prepare(keypoints, translation, pitch_deg=0.0)[2]["contact"]["locked_frames"]
    relative = motion.prepare(keypoints, translation, pitch_deg=0.0, contact="relative")[2]["contact"]["locked_frames"]
    for side in motion.SIDES:
        assert relative[side] == 72 and absolute[side] < 60


def test_step_split_relocks_a_foot_that_moves_while_on_the_floor():
    points = skeleton(60, sway=False)
    left = list(motion.LEGS[0][3])
    points[20:30, left, 0] -= np.linspace(0.0, 0.25, 10)[:, None]   # 左足が床に着いたまま 25 cm 動く（一歩）
    points[30:, left, 0] -= 0.25
    keypoints, translation = to_camera(points)
    one = motion.prepare(keypoints, translation, pitch_deg=0.0)[2]["contact"]["spans"]["left"]
    split = motion.prepare(keypoints, translation, pitch_deg=0.0, step_split=0.06)[2]["contact"]["spans"]["left"]
    assert len(one) == 1 and len(split) >= 2


def test_within_reach_moves_an_out_of_reach_ankle_the_least_and_keeps_others():
    hip = np.array([0.0, 0.0, 1.0])
    near = np.array([0.1, 0.0, 0.2])
    assert np.array_equal(motion.within_reach(hip, near, 1.0), near)
    far = np.array([0.6, 0.0, 0.1])                                         # 0.9 下・0.6 横は 1.08 m で届かない
    pulled = motion.within_reach(hip, far, 1.0)
    assert np.linalg.norm(pulled - hip) == pytest.approx(1.0)
    assert np.linalg.norm(pulled - far) == pytest.approx(math.hypot(0.6, 0.9) - 1.0)   # 届かない分だけ動く（最小）
    assert np.allclose(np.cross(pulled - hip, far - hip), 0.0)               # 股関節を通る直線の上
    pushed = motion.within_reach(hip, np.array([0.0, 0.0, 0.98]), 1.0, 0.1)  # 近すぎる: まっすぐ離す
    assert np.linalg.norm(pushed - hip) == pytest.approx(0.1)


def test_a_planted_foot_out_of_the_legs_reach_keeps_both_bone_lengths():
    # 例: 0.5 m の骨 2 本、足首が 1 m 下・0.4 m 横。足首を残したまま膝だけ縮めると脛が約 0.577 m に伸びる
    hip, knee, ankle = np.array([0.0, 0.0, 1.0]), np.array([0.0, -0.05, 0.5]), np.array([0.4, 0.0, 0.0])
    upper, lower = 0.5, 0.5
    kept = motion.within_reach(hip, ankle, upper + lower - 1e-4, 1e-4)
    solved = motion.leg_ik(hip, kept, upper, lower, knee - (hip + kept) / 2)
    assert np.linalg.norm(solved - hip) == pytest.approx(upper, abs=1e-6)
    assert np.linalg.norm(kept - solved) == pytest.approx(lower, abs=1e-6)
    # 通しで: 足は床に着いたまま、体ごと 0.4 m 横へ流れる（推定では足も流れる）。止めた足首は腰から離れる
    points = skeleton(40, sway=False)
    points[..., 0] += np.linspace(-0.2, 0.2, 40)[:, None]
    keypoints, translation = to_camera(points)
    prepared, _, report = motion.prepare(keypoints, translation, pitch_deg=0.0)
    pulled = report["contact"]["pulled_in"]["left"]
    assert pulled["frames"] > 0 and 0 < pulled["max_m"] < 0.05
    for hip_i, knee_i, ankle_i, _ in motion.LEGS:
        before = (np.linalg.norm(points[:, knee_i] - points[:, hip_i], axis=1),
                  np.linalg.norm(points[:, ankle_i] - points[:, knee_i], axis=1))
        after = (np.linalg.norm(prepared[:, knee_i] - prepared[:, hip_i], axis=1),
                 np.linalg.norm(prepared[:, ankle_i] - prepared[:, knee_i], axis=1))
        assert np.abs(after[0] - before[0]).max() < 2e-3 and np.abs(after[1] - before[1]).max() < 2e-3


@pytest.mark.parametrize("along", [(0.0, -0.8, 0.0), (0.0, 0.0, -0.8)], ids=["toward the camera", "straight down"])
def test_a_straight_leg_bends_somewhere_finite_whatever_way_it_points(along):
    # 脚がまっすぐで膝の手がかりが脚と平行（0 に射影される）。奥行きに沿う脚では、1 つ目の予備の向きも脚と平行になる
    hip = np.array([0.0, 0.0, 0.9])
    ankle = hip + np.array(along)
    knee = motion.leg_ik(hip, ankle, 0.45, 0.4, (ankle - hip) * 0.3)
    assert np.isfinite(knee).all()
    assert np.linalg.norm(knee - hip) == pytest.approx(0.45) and np.linalg.norm(ankle - knee) == pytest.approx(0.4)
    if along[1]:
        assert knee[2] > hip[2]          # カメラの方へ伸ばした脚は上へ曲げる
    else:
        assert knee[1] < hip[1]          # 下へ伸ばした脚は従来どおりカメラの方（膝が前）へ


def test_floor_fit_keeps_the_last_plane_when_trimming_leaves_too_few_points():
    # 1 点が 10 回重なり、ほか 2 点。3 点を通る平面 z = 0.2 y にぴったり乗るので残差がそろい、外すと 2 点以下しか残らない
    feet = np.array([[0.0, 0.0, 0.0]] * 10 + [[1.0, 0.0, 0.0], [0.0, 1.0, 0.2]])
    assert np.allclose(motion.fit_floor(feet), [0.0, 0.2, 0.0], atol=1e-9)
    assert motion.fit_floor(feet[:10]) is None                      # 1 点だけでは決まらない
    line = np.array([[x, 0.5, 0.1 * x] for x in np.linspace(-1, 1, 9)])
    assert motion.fit_floor(line) is None                           # 1 本の直線の上
    assert np.allclose(motion.fit_floor(line, 0.0), [0.1, 0.0, 0.0], atol=1e-9)   # 前後の傾きを決めれば左右は当たる


def test_a_standing_still_dancer_needs_the_pitch_and_gets_only_the_roll_measured():
    points = skeleton(30, sway=False)                               # 足の最下点が 2 か所だけ（同じ奥行き）
    keypoints, translation = to_camera(points)
    with pytest.raises(ValueError, match="pitch_deg"):
        motion.prepare(keypoints, translation)
    prepared, _, report = motion.prepare(keypoints, translation, pitch_deg=0.0)
    assert report["floor"]["measured_pitch_deg"] is None and report["floor"]["applied_pitch_deg"] == 0.0
    assert abs(report["floor"]["measured_roll_deg"]) < 1e-6
    upper = list(motion.UPPER)
    assert np.abs(prepared[:, upper] - points[:, upper]).max() < 2e-3


def test_prepare_refuses_to_return_nan_points(monkeypatch):
    monkeypatch.setattr(motion, "leg_ik", lambda *args: np.full(3, np.nan))   # 膝の解が壊れたとき
    keypoints, translation = to_camera(skeleton(20))
    with pytest.raises(ValueError, match="NaN"):
        motion.prepare(keypoints, translation)


def test_prepare_rejects_bad_shapes():
    keypoints, translation = to_camera(skeleton(10))
    with pytest.raises(ValueError, match="70"):
        motion.prepare(keypoints[:, :60], translation)
    with pytest.raises(ValueError, match="並進"):
        motion.prepare(keypoints, translation[:5])
    with pytest.raises(ValueError, match="5"):
        motion.prepare(keypoints[:3], translation[:3])
    keypoints[2, 4, 1] = np.nan
    with pytest.raises(ValueError, match="NaN"):
        motion.prepare(keypoints, translation)


def write_npz(path, frames=48):
    keypoints, translation = to_camera(skeleton(frames))
    valid = np.ones(frames, bool)
    valid[5] = False
    full = np.zeros(frames, bool)
    full[[7, 8]] = True
    np.savez(path, pred_keypoints_3d=keypoints.astype(np.float32), pred_cam_t=translation.astype(np.float32),
             focal_length=np.linspace(1100, 1300, frames).astype(np.float32), valid=valid, used_full_image_box=full)
    return path


def test_the_operation_runs_in_a_recipe_and_is_reused(tmp_path):
    from model3d import executor
    write_npz(tmp_path / "clip.npz")
    parameters = {"contact": "relative", "step_split": 0.06, "torso_back_deg": 12, "pitch_deg": -5}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "prepare",
                                           "inputs": {"clip": {"path": "clip.npz"}},
                                           "steps": [{"id": "prepare", "operation": "motion.prepare_keypoints",
                                                      "inputs": {"keypoints": "$inputs.clip"}, "parameters": parameters,
                                                      "outputs": {"points": "points.npz", "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["prepare"]
    with np.load(step["artifacts"]["points"], allow_pickle=False) as data:
        assert data["points"].shape == (48, 70, 3) and data["points"].dtype == np.float32
        assert data["stance"].shape == (48, 2) and float(data["focal"]) == pytest.approx(1200.0, abs=1.0)
    report = json.loads(open(step["artifacts"]["report"], encoding="utf-8").read())
    assert report["schema_version"] == "model3d-prepared-keypoints/0.1"
    assert report["input"] == {"format": "sam3d_body", "focal_length_px_median": pytest.approx(1200.0, abs=1.0),
                               "invalid_frames": 1, "full_image_box_frames": 2}
    assert report["floor"]["applied_pitch_deg"] == pytest.approx(-5.0) and report["torso_back_deg"] == 12.0
    assert report["contact"]["mode"] == "relative" and report["contact"]["step_split_m"] == 0.06
    observation = step["observations"][0]
    assert observation["kind"] == "prepare_keypoints" and observation["full_image_box_frames"] == 2
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["prepare"]["reused_from"] is not None


@pytest.mark.parametrize("parameters, message", [
    ({"extra": 1}, "知らないパラメーター"),
    ({"depth_window": 10}, "奇数"),
    ({"depth_window": 3}, "depth_window"),
    ({"contact": "sometimes"}, "contact"),
    ({"format": "openpose"}, "format"),
    ({"torso_back_deg": 60}, "torso_back_deg"),
    ({"pitch_deg": 80}, "pitch_deg"),
    ({"step_split": 0}, "step_split"),
])
def test_the_operation_rejects_bad_parameters(tmp_path, parameters, message):
    from model3d.ops import get
    write_npz(tmp_path / "clip.npz", frames=12)
    operation = get("motion.prepare_keypoints").function
    with pytest.raises(ToolError, match=message):
        operation({"keypoints": tmp_path / "clip.npz"}, parameters, {}, None)


def test_the_operation_rejects_an_npz_without_keypoints(tmp_path):
    from model3d.ops import get
    np.savez(tmp_path / "other.npz", points=np.zeros((10, 70, 3)))
    with pytest.raises(ToolError, match="pred_keypoints_3d"):
        get("motion.prepare_keypoints").function({"keypoints": tmp_path / "other.npz"}, {}, {}, None)
