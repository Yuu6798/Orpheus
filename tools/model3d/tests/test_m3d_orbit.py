"""plan.orbit（踊り手を中心にした極座標のカメラ）: 点の通り道の読み方とならし、キーの選び方とつなぎ方、カメラの行列（位置・向き・
真上からの上の向き・ロール・手持ちの揺れと打点の揺れ）、踊りの時刻の止め、plan.stage の寄りと揺れ、悪い段取り表、工程の受け渡し。
値は作り物（どの曲の時刻とも関係ない）。本物の Blender 5.2.4 での確かめは docs/model3d.md。"""
import math

import numpy as np
import pytest

from model3d import contracts
from model3d import orbit_plan
from model3d.blender_adapter.point_track import PathError, Samples
from model3d.io import ToolError, atomic_json


def _path(frames, points):
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": list(frames), "bones": {},
            "points": {name: [list(map(float, p)) for p in values] for name, values in points.items()}}


def _still_path(count=121, frame0=1.0, fps=24.0, rate=60.0):
    """動かない踊り手（腰 (0, 0, 1)・顔 (0, 0, 1.5)・胸 (0, 0, 1.3)）を 1/rate 秒おきに測った通り道。"""
    times = np.arange(count) / rate
    frames = frame0 + times * fps
    return _path(frames, {"hips": [[0, 0, 1]] * count, "face": [[0, 0, 1.5]] * count, "chest": [[0, 0, 1.3]] * count})


def _plan(shots, **extra):
    return {"schema_version": "model3d-orbit-plan/0.1", "fps": 30, "frames": 30, "dance": {"fps": 24}, "smooth": {"sigma_s": 0.0},
            "shots": shots, **extra}


def _key(**values):
    key = {"az": 0.0, "dist": 2.0, "height": 1.0, "aim": "hips", "fov": 40.0}
    key.update(values)
    return key


def test_samples_smooth_in_seconds_and_read_between_the_samples():
    rate, count = 60.0, 200
    frames = 1.0 + np.arange(count) / rate * 24.0
    rise = np.stack([np.arange(count) / rate, np.zeros(count), np.ones(count)], axis=1)        # x が 1 秒に 1 m 進む
    samples = Samples(_path(frames, {"p": rise}), {"p": 0.0}, 1.0, 24.0)
    assert samples.rate == pytest.approx(60.0) and samples.start == 0.0
    assert np.allclose(samples.at("p", 0.5 + 1 / 120), [0.5 + 1 / 120, 0, 1])                    # 標本の間は直線で補う
    assert np.allclose(samples.at("p", -3.0), rise[0]) and np.allclose(samples.at("p", 99.0), rise[-1], atol=1e-3)
    spike = np.zeros((count, 3))
    spike[100] = [1.0, 0.0, 0.0]
    smoothed = Samples(_path(frames, {"p": spike}), {"p": 0.1}, 1.0, 24.0, 3.0)
    sigma = 0.1 * smoothed.rate
    radius = math.ceil(3 * sigma)
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    assert np.allclose(smoothed.columns["p"][100 - radius:101 + radius, 0], kernel[::-1])         # ±ceil(3σ) の標本のガウス
    assert smoothed.columns["p"][100 - radius - 1, 0] == 0.0


def test_samples_must_be_even_in_time_and_have_the_points():
    frames = [1.0, 1.4, 1.8, 2.6]
    with pytest.raises(PathError, match="等間隔"):
        Samples(_path(frames, {"p": [[0, 0, 0]] * 4}), {"p": 0.0}, 1.0, 24.0)
    with pytest.raises(PathError, match="点がありません"):
        Samples(_path([1.0, 1.4], {"p": [[0, 0, 0]] * 2}), {"q": 0.0}, 1.0, 24.0)
    with pytest.raises(ToolError, match="点がありません"):
        orbit_plan.read_points(_path([1.0, 1.4], {"p": [[0, 0, 0]] * 2}), ["q"], 1.0, 24.0, 0.0, 3.0)


def test_keys_are_chosen_by_u_and_eased_by_the_departing_key():
    orbit = orbit_plan.Orbit(_plan([{"until": 1.0, "keys": [_key(ease="out"), _key(u=0.5, ease="linear"), _key(u=1.0)]}]))
    a, b, w = orbit.keys_at(0, 0.25)
    assert a["ease"] == "out" and w == pytest.approx(1 - (1 - 0.5) ** 3)
    a, b, w = orbit.keys_at(0, 0.75)
    assert a["ease"] == "linear" and w == pytest.approx(0.5)
    assert orbit_plan.EASE["in"](0.5) == 0.125 and orbit_plan.EASE["whip"](1.0) == 1.0 and orbit_plan.EASE["smooth"](0.5) == 0.5


def _camera(shots, T=1, path=None, **extra):
    orbit = orbit_plan.Orbit(_plan(shots, **extra))
    samples = orbit_plan.read_points(path or _still_path(), orbit.names, orbit.frame0, orbit.dance_fps, 0.0, 3.0)
    return orbit.camera(T, samples)


def test_the_camera_circles_the_centre_and_looks_at_the_aim():
    matrix, focal, dancer, f, n, held = _camera([{"keys": [_key(aim="face", height={"above": 0.0})]}])
    assert np.allclose(matrix[:3, 3], [0.0, -2.0, 1.5])                         # 正面（−Y 側）2 m、顔と同じ高さ
    assert np.allclose(matrix[:3, :3], [[1, 0, 0], [0, 0, -1], [0, 1, 0]])     # 右 +X、上 +Z、−Z（前）が +Y
    assert focal == pytest.approx(1920 / 2 / math.tan(math.radians(20)))
    assert np.allclose(dancer, [0, 0, 1]) and f == 1 and n == 0 and not held
    side, *_ = _camera([{"keys": [_key(az=90.0, dist=3.0, height=1.0)]}])
    assert np.allclose(side[:3, 3], [3.0, 0.0, 1.0]) and np.allclose(-side[:3, 2], [-1, 0, 0])
    low, *_ = _camera([{"keys": [_key(height=-5.0)]}])
    assert low[2, 3] == pytest.approx(0.12)                                      # 高さの下限


def test_roll_turns_the_camera_around_its_view():
    flat, *_ = _camera([{"keys": [_key(height=1.0)]}])
    rolled, *_ = _camera([{"keys": [_key(height=1.0, roll=90.0)]}])
    assert np.allclose(rolled[:3, 0], flat[:3, 1]) and np.allclose(rolled[:3, 1], -flat[:3, 0])
    assert np.allclose(rolled[:3, 2], flat[:3, 2])


def test_looking_straight_down_keeps_the_horizontal_front_as_up():
    top, *_ = _camera([{"keys": [_key(dist=0.001, height=5.0, az=30.0)]}])
    forward = -top[:3, 2]
    assert forward[2] < -0.99
    flat = np.array([-math.sin(math.radians(30)), math.cos(math.radians(30)), 0.0])
    assert np.allclose(top[:3, 1], flat, atol=1e-3)                              # 上は、水平の前の向き
    assert np.allclose(top[:3, :3].T @ top[:3, :3], np.eye(3))


def test_handheld_shake_and_kicks():
    still, *_ = _camera([{"keys": [_key()]}], kicks=[{"at": 0.0, "deg": 5.0}])
    plain, *_ = _camera([{"keys": [_key()]}])
    assert np.allclose(still, plain)                                              # 振れ幅 0 の区間は、打点の揺れも入れない
    orbit = orbit_plan.Orbit(_plan([{"keys": [_key()], "handheld": 0.5}], kicks=[{"at": 0.0, "deg": 2.0}]))
    phases = np.random.default_rng(3).uniform(0, 2 * np.pi, (3, 3))
    t = 0.1
    expected = [0.5 * (math.sin(2 * math.pi * 0.9 * t + phases[j, 0]) + 0.6 * math.sin(2 * math.pi * 2.3 * t + phases[j, 1])
                       + 0.3 * math.sin(2 * math.pi * 5.1 * t + phases[j, 2])) for j in range(3)]
    k = 2.0 * math.exp(-t / 0.13)
    expected[0] += k * math.sin(2 * math.pi * 7.5 * t) * 0.6
    expected[1] += k * math.sin(2 * math.pi * 8.3 * t + 1.0)
    expected[2] += k * math.sin(2 * math.pi * 6.1 * t + 2.0) * 0.8
    assert orbit.jitter(t, 0.5) == expected                                      # 試作の式と同じ順・同じ値
    late = orbit.jitter(0.7, 0.5)
    assert late == [0.5 * (math.sin(2 * math.pi * 0.9 * 0.7 + phases[j, 0]) + 0.6 * math.sin(2 * math.pi * 2.3 * 0.7 + phases[j, 1])
                           + 0.3 * math.sin(2 * math.pi * 5.1 * 0.7 + phases[j, 2])) for j in range(3)]


def test_the_dance_time_can_hold_while_the_camera_moves():
    count = 121
    frames = 1.0 + np.arange(count) / 60.0 * 24.0
    walk = np.stack([np.arange(count) / 60.0, np.zeros(count), np.ones(count)], axis=1)        # 腰が 1 秒に 1 m 進む
    path = _path(frames, {"hips": walk})
    plan = _plan([{"until": 1.0, "keys": [_key(az=0.0), _key(az=90.0)]}], time_offset=0.5,
                 dance={"frame0": 1, "fps": 24, "holds": [{"from": 0.7, "at": 0.6}]})
    orbit = orbit_plan.Orbit(plan)
    samples = orbit_plan.read_points(path, orbit.names, orbit.frame0, orbit.dance_fps, 0.0, 3.0)
    early = orbit.camera(1, samples)                                              # t = 0.5
    assert early[3] == pytest.approx(1 + 24 * 0.5) and not early[5]
    later = orbit.camera(10, samples)                                              # t = 0.8（止めた区間）
    assert later[3] == pytest.approx(1 + 24 * 0.6) and later[5]
    assert np.allclose(later[2], [0.6, 0, 1])                                       # 狙いも止めた時刻の姿勢
    moved = orbit.camera(12, samples)
    assert moved[3] == later[3] and not np.allclose(moved[0], later[0])          # カメラは動き続ける


def test_plan_stage_effects_move_the_camera():
    pushed, *_ = _camera([{"keys": [_key()]}], effects=[{"kind": "push", "at": 0.0, "amount": 0.5, "tau": 10.0, "length": 1.0}])
    assert pushed[1, 3] == pytest.approx(-2.0 * 0.5)                              # 距離に 1 − 0.5 × exp(−0/10)
    quiet, *_ = _camera([{"keys": [_key()], "effects": False}], effects=[{"kind": "push", "at": 0.0, "amount": 0.5}])
    assert quiet[1, 3] == pytest.approx(-2.0)


@pytest.mark.parametrize("shots, extra, message", [
    ([{"keys": [_key(), _key()]}], {}, "until が要ります"),
    ([{"until": 1.0, "keys": [_key(), _key(u=0.6), _key(u=0.4), _key()]}], {}, "昇順"),
    ([{"until": 1.0, "keys": [_key(), _key(), _key()]}], {}, "u"),
    ([{"until": {"beat": 2}, "keys": [_key()]}, {"keys": [_key()]}], {}, "grid"),
    ([{"keys": [_key()]}], {"dance": {"fps": 24, "holds": [{"from": 1.0, "until": 0.5, "at": 0.0}]}}, "holds"),
])
def test_bad_plans_are_refused(shots, extra, message):
    with pytest.raises(ToolError, match=message):
        orbit_plan.Orbit(_plan(shots, **extra))


def test_the_first_shot_starts_at_the_time_of_the_first_frame():
    orbit = orbit_plan.Orbit(_plan([{"until": -0.5, "keys": [_key()]}, {"until": 0.5, "keys": [_key(), _key(az=90.0)]},
                                    {"keys": [_key()]}], time_offset=-1.0))
    assert orbit.spans[:2] == [(-1.0, -0.5), (-0.5, 0.5)]                       # 負の時刻から始まる前置きの区間も書ける
    assert orbit.keys_at(1, 0.0)[2] == pytest.approx(0.5)
    with pytest.raises(ToolError, match="区間の頭"):
        orbit_plan.Orbit(_plan([{"until": 0.5, "keys": [_key()]}, {"keys": [_key()]}], time_offset=1.0))


def test_the_camera_must_not_sit_on_its_aim():
    with pytest.raises(ToolError, match="同じ"):
        _camera([{"keys": [_key(dist=0.0, height={"above": 0.0})]}])


def test_build_writes_cameras_track_and_report():
    plan = _plan([{"name": "a", "until": 0.5, "keys": [_key(), _key(az=45.0)]}, {"name": "b", "keys": [_key(az=45.0)]}],
                 time_offset=0.2, dance={"frame0": 1, "fps": 24, "holds": [{"from": 0.9, "at": 0.8}]})
    cameras, track, report = orbit_plan.build(plan, _still_path())
    contracts.validate("cameras", cameras)
    contracts.validate("stage-track", track)
    assert [fr["T"] for fr in cameras["frames"]] == list(range(1, 31)) == [fr["T"] for fr in track["frames"]]
    assert track["frames"][0]["f"] == pytest.approx(1 + 24 * 0.2) and cameras["frames"][0]["principal_px"] == [540.0, 960.0]
    assert cameras["frames"][-1]["f"] == pytest.approx(1 + 24 * 0.8)
    assert report["shots"][0]["name"] == "a" and report["shots"][0]["T"] == [1, 9] and report["shots"][1]["T"] == [10, 30]
    assert report["held_frames"] == 9 and report["samples"]["outside"] == 0       # t = 0.2 + 21/30 は丸めで 0.9 の手前だが止める側
    first = cameras["frames"][0]
    assert first["distance_m"] == pytest.approx(math.dist(first["dancer"], [row[3] for row in first["matrix"][:3]]))


# ---- 工程（Blender なし） ----

def test_the_plan_orbit_operation_writes_its_outputs(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "plan.json", _plan([{"until": 0.5, "keys": [_key(aim="face"), _key(az=30.0, aim="chest")]},
                                               {"keys": [_key(az=30.0, aim="chest")]}]))
    atomic_json(tmp_path / "bones.json", _still_path())
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "orbit",
                                           "inputs": {"plan": {"path": "plan.json"}, "bones": {"path": "bones.json"}},
                                           "steps": [{"id": "orbit", "operation": "plan.orbit",
                                                      "inputs": {"plan": "$inputs.plan", "bones": "$inputs.bones"},
                                                      "outputs": {"cameras": "cameras.json", "track": "track.json",
                                                                  "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    step = result["steps"]["orbit"]
    cameras = contracts.load("cameras", step["artifacts"]["cameras"])
    assert len(cameras["frames"]) == 30
    observation = step["observations"][0]
    assert observation["kind"] == "orbit_plan" and observation["frames"] == 30 and observation["points"] == ["chest", "face", "hips"]
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["orbit"]["reused_from"] is not None                     # 計算だけなので、同じ入力なら使い回す


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def test_the_plan_orbit_operation_checks_its_inputs(tmp_path):
    from model3d.ops import get
    op = get("plan.orbit").function
    atomic_json(tmp_path / "plan.json", _plan([{"keys": [_key()]}]))
    atomic_json(tmp_path / "bones.json", _still_path())
    inputs = {"plan": tmp_path / "plan.json", "bones": tmp_path / "bones.json"}
    outputs = {"cameras": tmp_path / "c.json", "track": tmp_path / "t.json"}
    with pytest.raises(ToolError, match="知らないパラメーター"):
        op(inputs, {"wobble": 1}, outputs, Context(tmp_path))
    with pytest.raises(ToolError, match="track"):
        op(inputs, {}, {"cameras": tmp_path / "c.json"}, Context(tmp_path))
    atomic_json(tmp_path / "bad.json", {**_plan([{"keys": [_key()]}]), "fps": 0})
    with pytest.raises(ToolError, match="orbit-plan"):
        op({**inputs, "plan": tmp_path / "bad.json"}, {}, outputs, Context(tmp_path))
