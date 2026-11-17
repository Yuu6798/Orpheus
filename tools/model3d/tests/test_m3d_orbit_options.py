"""plan.orbit の、書いたときだけ効く指定（リポジトリ外の 3 作目の VRM のダンスの演出版のカメラを書くため）: 2 乗のつなぎ方（in2・out2）、
狙う点のまわりの仰角（el）、レンズ（lens）、世界の座標のずらし（offset）、区間の平均と混ぜる狙い（follow）、区間の中だけのならし
（smooth.per_shot）、絞りとピント（fstop・focus）、揺れの形 orbit。値は作り物（どの曲とも関係ない）。省いたときに今までと同じ値に
なることは、出力の金型（test_m3d_output_golden）と test_m3d_orbit が確かめる。"""
import math

import numpy as np
import pytest

from model3d import contracts, orbit_plan
from model3d.io import ToolError, atomic_json


def _path(count=61, rate=30.0, points=None):
    """1/rate 秒おきの通り道（動きの fps 30、frame0 1）。points は名前 → (count, 3) の列。既定は動かない腰・顔。"""
    frames = [1.0 + k * 30.0 / rate for k in range(count)]
    points = points or {"hips": np.tile([0.0, 0.0, 1.0], (count, 1)), "face": np.tile([0.0, 0.0, 1.5], (count, 1))}
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": frames, "bones": {},
            "points": {name: [[float(v) for v in row] for row in np.asarray(values, float)] for name, values in points.items()}}


def _plan(shots, **extra):
    plan = {"schema_version": "model3d-orbit-plan/0.1", "fps": 30, "frames": 30, "dance": {"fps": 30}, "smooth": {"sigma_s": 0.0},
            "shots": shots}
    for key, value in extra.items():
        plan[key] = {**plan.get(key, {}), **value} if isinstance(value, dict) and key in plan else value
    return plan


def _key(**values):
    key = {"az": 0.0, "dist": 2.0, "el": 0.0, "aim": "hips", "lens": 50.0}
    key.update(values)
    return key


def _build(shots, path=None, **extra):
    plan = _plan(shots, **extra)
    contracts.validate("orbit-plan", plan)
    cameras, track, report = orbit_plan.build(plan, path or _path())
    contracts.validate("cameras", cameras)
    return cameras["frames"], report


def test_the_square_eases_are_the_quadratic_in_and_out():
    assert orbit_plan.EASE["in2"](0.5) == 0.25 and orbit_plan.EASE["out2"](0.5) == 0.75
    assert orbit_plan.EASE["in2"](-1.0) == 0.0 and orbit_plan.EASE["out2"](2.0) == 1.0
    assert orbit_plan.EASE["in"](0.5) == 0.125 and orbit_plan.EASE["out"](0.5) == 0.875      # 3 乗のつなぎ方は今のまま
    frames, _ = _build([{"until": 1.0, "keys": [_key(az=0.0, ease="out2"), _key(az=40.0)]}])
    assert frames[15]["matrix"][0][3] == pytest.approx(2.0 * math.sin(math.radians(40.0 * 0.75)))   # u = 0.5 で 3/4 だけ回る


def test_el_places_the_camera_on_a_sphere_around_the_aim():
    frames, _ = _build([{"keys": [_key(el=30.0, dist=2.0)]}])
    M = np.array(frames[0]["matrix"])
    assert np.allclose(M[:3, 3], [0.0, -2.0 * math.cos(math.radians(30)), 1.0 + 2.0 * math.sin(math.radians(30))])
    forward = -M[:3, 2]
    assert np.allclose(forward, (np.array([0.0, 0.0, 1.0]) - M[:3, 3]) / 2.0)                # 狙う点を向く
    side, _ = _build([{"keys": [_key(az=90.0, el=0.0, dist=3.0)]}])
    assert np.allclose(np.array(side[0]["matrix"])[:3, 3], [3.0, 0.0, 1.0])
    low, _ = _build([{"keys": [_key(el=-80.0, dist=3.0)]}])
    assert np.array(low[0]["matrix"])[2][3] == 0.12                                            # 高さの下限は同じ
    pushed, _ = _build([{"keys": [_key(el=30.0, dist=2.0)]}],
                       effects=[{"kind": "push", "at": 0.0, "amount": 0.5, "tau": 100.0, "length": 1.0}])
    assert np.linalg.norm(np.array(pushed[0]["matrix"])[:3, 3] - [0, 0, 1]) == pytest.approx(1.0)   # 寄りはまっすぐの距離に掛かる


def test_lens_keys_blend_millimetres():
    frames, _ = _build([{"until": 1.0, "keys": [_key(lens=30.0), _key(lens=60.0)]}])
    assert frames[0]["lens_mm"] == 30.0 and frames[0]["focal_px"] == pytest.approx(1920 * 30.0 / 36.0)
    w = orbit_plan.EASE["smooth"](15 / 30)
    assert frames[15]["lens_mm"] == pytest.approx(30.0 + 30.0 * w)                              # mm でつなぐ（画角ではなく）


@pytest.mark.parametrize("keys, message", [
    ([{**_key(), "height": 1.0}], "height か el"),
    ([_key(), {k: v for k, v in {**_key(), "height": 1.0}.items() if k != "el"}], "どれも height か"),
    ([_key(), {k: v for k, v in {**_key(), "fov": 40.0}.items() if k != "lens"}], "どれも fov か"),
])
def test_the_keys_of_a_shot_use_one_way_of_writing_each_value(keys, message):
    plan = _plan([{"until": 1.0, "keys": keys}])
    with pytest.raises(ToolError, match=message):
        orbit_plan.Orbit(plan)


def test_the_schema_takes_one_of_each_pair():
    for key in ({**_key(), "height": 1.0}, {**_key(), "fov": 40.0}, {**_key(), "dz": 0.1, "offset": [0, 0, 0.1]},
                {k: v for k, v in _key().items() if k != "el"}):
        with pytest.raises(ToolError, match="orbit-plan"):
            contracts.validate("orbit-plan", _plan([{"keys": [key]}]))
    assert contracts.validate("orbit-plan", _plan([{"keys": [_key(ease="out2", offset=[0.1, 0.0, 0.0], follow=0.5, focus="face")],
                                                    "fstop": 2.0, "seed": 4}], handheld={"form": "orbit"},
                                                  smooth={"per_shot": True, "focus_sigma_s": 0.1}))


def test_offset_moves_the_aim_in_the_world():
    frames, _ = _build([{"keys": [_key(offset=[0.3, 0.0, 0.2])]}])
    M = np.array(frames[0]["matrix"])
    assert np.allclose(M[:3, 3], [0.3, -2.0, 1.2])                                             # 狙う点 (0.3, 0, 1.2) の正面


def _walk(count=61):
    """腰が x の向きへ 1 秒に 1 m 進む通り道。"""
    x = np.arange(count) / 30.0
    return _path(count, points={"hips": np.stack([x, np.zeros(count), np.ones(count)], axis=1)})


def test_follow_mixes_the_mean_of_the_shot_and_the_point_of_the_moment():
    frames, _ = _build([{"until": 1.0, "keys": [_key(follow=0.25)]}], path=_walk())
    mean = np.mean(np.arange(30) / 30.0)                                                         # 区間のコマ（T 1〜30）の生の点の平均
    for T in (1, 16, 30):
        live = (T - 1) / 30.0
        aim_x = mean * 0.75 + live * 0.25
        assert frames[T - 1]["matrix"][0][3] == pytest.approx(aim_x)                             # 正面のカメラの x は狙う点の x
    still, _ = _build([{"until": 1.0, "keys": [_key()]}], path=_walk())
    assert [fr["matrix"][0][3] for fr in still] == pytest.approx([(T - 1) / 30.0 for T in range(1, 31)])   # 省けば、その時刻の点


def test_per_shot_smoothing_does_not_carry_motion_across_the_cut():
    count = 61
    x = np.where(np.arange(count) < 15, 0.0, 1.0)                                                # T 16（0.5 秒）で 1 m 跳ぶ
    path = _path(count, points={"hips": np.stack([x, np.zeros(count), np.ones(count)], axis=1)})
    shots = [{"until": 0.5, "keys": [_key()]}, {"until": 1.0, "keys": [_key()]}]
    split, report = _build(shots, path=path, smooth={"sigma_s": 4 / 30, "reach": 2.0, "per_shot": True})
    whole, _ = _build(shots, path=path, smooth={"sigma_s": 4 / 30, "reach": 2.0})
    assert split[14]["matrix"][0][3] == 0.0                                                    # 区間の中だけでならすので、つなぎ目で混ざらない
    assert split[15]["matrix"][0][3] == pytest.approx(1.0, abs=1e-12)                         # （重みの和の丸めで 1 ± 2e-16 になる環境がある）
    assert 0.0 < whole[14]["matrix"][0][3] < 0.5 < whole[15]["matrix"][0][3] < 1.0           # 全体でならすと、つなぎ目の前後に混ざる
    assert report["samples"]["per_shot"] is True
    cylinder = [{"until": 0.5, "keys": [{**{k: v for k, v in _key().items() if k != "el"}, "height": {"above": 0.0}}]},
                {"until": 1.0, "keys": [{**{k: v for k, v in _key().items() if k != "el"}, "height": {"above": 0.0}}]}]
    centred, _ = _build(cylinder, path=path, smooth={"sigma_s": 4 / 30, "reach": 2.0, "per_shot": True})
    assert centred[14]["matrix"][0][3] == 0.0                                              # 円柱の置き方の回る中心も、区間の中だけ
    assert centred[15]["matrix"][0][3] == pytest.approx(1.0, abs=1e-12)
    assert centred[14]["dancer"][0] == 0.0 and centred[15]["dancer"][0] == pytest.approx(1.0, abs=1e-12)
    blended, _ = _build(cylinder, path=path, smooth={"sigma_s": 4 / 30, "reach": 2.0})
    assert 0.0 < blended[14]["matrix"][0][3] < 0.5
    bump = np.zeros(count)
    bump[5] = 1.0
    path = _path(count, points={"hips": np.stack([bump, np.zeros(count), np.ones(count)], axis=1)})
    one, _ = _build([{"until": 1.0, "keys": [_key()]}], path=path, smooth={"sigma_s": 4 / 30, "reach": 2.0, "per_shot": True})
    radius = 8
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / 4.0) ** 2)
    kernel /= kernel.sum()
    expected = np.convolve(np.pad(bump[:30], radius, mode="edge"), kernel, mode="valid")        # σ 4 コマ ±8 コマ、端は端の値
    assert [fr["matrix"][0][3] for fr in one] == pytest.approx(expected.tolist())


def test_fstop_and_focus_are_written_for_the_shots_with_an_aperture():
    shots = [{"until": 0.5, "keys": [_key()]},
             {"until": 1.0, "fstop": 2.0, "keys": [_key(focus="face"), _key(focus="hips")]}]
    frames, report = _build(shots)
    assert "fstop" not in frames[0] and "focus_m" not in frames[14]
    first, last = frames[15], frames[29]
    position = np.array(first["matrix"])[:3, 3]
    assert first["fstop"] == 2.0 and first["focus_m"] == pytest.approx(np.linalg.norm(np.array([0, 0, 1.5]) - position))
    w = orbit_plan.EASE["smooth"](14 / 15)
    focus = np.array([0, 0, 1.5]) + (np.array([0, 0, 1.0]) - np.array([0, 0, 1.5])) * w            # ピントが顔から腰へ移る
    assert last["focus_m"] == pytest.approx(np.linalg.norm(focus - np.array(last["matrix"])[:3, 3]))
    assert report["dof"] == {"frames": 15, "focus_points": ["face", "hips"], "focus_sigma_s": 0.0}
    aim, _ = _build([{"fstop": 1.4, "keys": [_key(dist=3.0)]}])                                  # focus が無ければ狙う点
    assert aim[0]["fstop"] == 1.4 and aim[0]["focus_m"] == pytest.approx(3.0)
    frame = {"T": 1, "matrix": np.eye(4).tolist(), "focal_px": 1.0, "principal_px": [0, 0], "fstop": 2.0}
    with pytest.raises(ToolError, match="cameras"):
        contracts.validate("cameras", {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [2, 2], "frames": [frame]})


def test_the_orbit_handheld_shakes_the_angles_with_the_shot_seed():
    shots = [{"until": 0.5, "handheld": 0.3, "seed": 7, "keys": [_key(el=10.0, roll=2.0)]},
             {"until": 1.0, "handheld": 0.3, "keys": [_key(el=10.0)]}]
    frames, _ = _build(shots, handheld={"form": "orbit"})
    for T, seed in ((4, 7), (20, 1)):                                                            # 種を省いた区間は区間の番号
        t = (T - 1) / 30
        d_az, d_el, d_roll = orbit_plan.orbit_shake(t, 0.3, seed)
        theta, phi = math.radians(d_az), math.radians(10.0 + d_el)
        M = np.array(frames[T - 1]["matrix"])
        assert np.allclose(M[:3, 3], [2 * math.sin(theta) * math.cos(phi), -2 * math.cos(theta) * math.cos(phi), 1 + 2 * math.sin(phi)])
        forward = -M[:3, 2]
        right = np.cross(forward, [0.0, 0.0, 1.0])
        right /= np.linalg.norm(right)
        roll = (2.0 if T < 16 else 0.0) + d_roll
        assert float(right @ M[:3, 0]) == pytest.approx(math.cos(math.radians(roll)))           # 向きは回さず、傾きだけ足す
    assert orbit_plan.orbit_shake(0.4, 0.3, 7)[0] == 0.3 * (math.sin(0.4 * 5.1 + 7) + 0.6 * math.sin(0.4 * 11.3 + 14))
    with pytest.raises(ToolError, match="仰角 el のキーの区間"):
        orbit_plan.Orbit(_plan([{"handheld": 0.3, "keys": [{**{k: v for k, v in _key().items() if k != "el"}, "height": 1.0}]}],
                               handheld={"form": "orbit"}))


def test_calling_the_camera_alone_needs_the_point_readers_for_follow():
    plan = _plan([{"keys": [_key(follow=0.5)]}])
    orbit = orbit_plan.Orbit(plan)
    samples = orbit_plan.read_points(_path(), orbit.names, orbit.frame0, orbit.dance_fps, 0.0, 3.0)
    with pytest.raises(ToolError, match="build が作る"):
        orbit.camera(1, samples)


def test_the_operation_writes_the_aperture_into_the_cameras(tmp_path):
    from model3d import executor
    plan = _plan([{"fstop": 2.8, "keys": [_key(follow=0.5, focus="face")]}], smooth={"per_shot": True})
    atomic_json(tmp_path / "plan.json", plan)
    atomic_json(tmp_path / "bones.json", _path())
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "orbit",
                                           "inputs": {"plan": {"path": "plan.json"}, "bones": {"path": "bones.json"}},
                                           "steps": [{"id": "orbit", "operation": "plan.orbit",
                                                      "inputs": {"plan": "$inputs.plan", "bones": "$inputs.bones"},
                                                      "outputs": {"cameras": "cameras.json", "track": "track.json",
                                                                  "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    cameras = contracts.load("cameras", result["steps"]["orbit"]["artifacts"]["cameras"])
    for fr in cameras["frames"]:
        position = np.array(fr["matrix"])[:3, 3]
        assert fr["fstop"] == 2.8 and fr["focus_m"] == pytest.approx(np.linalg.norm(np.array([0, 0, 1.5]) - position))
