"""plan.shots（2D の絵コンテのショット表 → 3D のカメラ）: 仮の部屋の置き方（render.box_room と同じ式）、追う点がちょうど狙いの画素に
来ること（主点を解く）、追う点の追い方、キーと手直しの混ぜ方、回り込み（行き過ぎない補間・注視点・画角）、踊りの時刻の止め、
つなぎの相手のカメラと揺れなしの基準のカメラ、悪い表、工程の受け渡し（Blender なし）。"""
import json
import math

import numpy as np
import pytest

from model3d import contracts
from model3d.io import ToolError, atomic_json
from model3d.shot_plan import Room, Shots, build, pchip, project

ROOM = {"focal_px": 2000.0, "center_px": [540.0, 800.0], "camera_height": 1.0, "origin_px": [600.0, 1800.0], "scale": 1.2}


def _bones(count=600, rate=60.0):
    """点: 腰は (0.1 t, 0, 0.9)、胸は (0.1 t, 0, 1.25)、顔は (0.1 t, −0.05, 1.55)（t は踊りの時刻。0 秒から 10 秒）。"""
    times = np.arange(count) / rate
    frames = 1.0 + times * 24.0
    def path(y, z):
        return [[0.1 * t, y, z] for t in times]
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": frames.tolist(), "bones": {},
            "points": {"hips": path(0.0, 0.9), "chest": path(0.0, 1.25), "face": path(-0.05, 1.55)}}


def _plan(**parts):
    plan = {"schema_version": "model3d-shot-plan/0.1", "fps": 30, "frames": 60, "time_offset": 1.0, "room": ROOM,
            "dance": {"fps": 24.0}, "smooth": {"sigma_s": 0.0},
            "shots": [{"name": "A", "until": 2.0, "keys": [
                          {"move": [0.0, 0.2, 0.3], "yaw": -4.0, "pitch": 3.0, "roll": 2.0, "zoom": 1.5, "anchor": "chest", "at": [540, 900],
                           "ease": "linear"},
                          {"move": [0.2, 0.2, 0.5], "yaw": 2.0, "pitch": 1.0, "roll": -1.0, "zoom": 1.8, "anchor": [560, 1000], "at": [520, 980]}]},
                      {"name": "B", "orbit": {"keys": [[2.0, 30.0, 2.5, 2.0], [2.5, 0.0, 2.4, 1.5], [3.0, -20.0, 2.6, 0.5]], "aim_z": 1.0, "fov": 48.0}}]}
    plan.update(parts)
    return plan


def test_the_room_is_placed_like_the_box_room():
    room = Room(ROOM)
    assert room.z_d == pytest.approx(2000.0 * 1.0 / (1800 - 800)) and room.x_d == pytest.approx((600 - 540) * 2.0 / 2000)
    assert np.allclose(room.world((room.x_d, 1.0, room.z_d)), [0.0, 0.0, 0.0])           # 踊り手の立つ所が原点
    assert np.allclose(room.world((0.0, 0.0, 0.0)), [-room.x_d * 1.2, -room.z_d * 1.2, 1.2])  # 基準のカメラ（床から 1 × 1.2 m）
    assert np.allclose(room.on_plane([600.0, 1800.0]), [0.0, 0.0, 0.0])
    with pytest.raises(ToolError, match="origin_px"):
        Room({**ROOM, "origin_px": [600.0, 700.0]})


def test_the_base_camera_sees_the_floor_point_where_the_picture_has_it():
    shots = Shots(_plan(shots=[{"keys": [{"move": [0, 0, 0], "anchor": [540, 800], "at": [540, 800]}]}]))
    from model3d.orbit_plan import read_points
    samples = read_points(_bones(), shots.names, 1.0, 24.0, 0.0, 3.0)
    matrix, focal, principal, _, _ = shots.camera(1.0, 0, samples)
    assert focal == 2000.0 and np.allclose(principal, [540.0, 800.0])
    assert np.allclose(matrix[:3, 2], [0.0, -1.0, 0.0])                                     # 奥（+Y）を向く
    seen = project(matrix, focal, principal, shots.room.on_plane([700.0, 1500.0]))
    assert np.allclose(seen, [700.0, 1500.0])                                               # 基準のカメラでは絵の画素どおり


def test_the_anchor_lands_on_its_pixel_and_follows_the_dancer():
    plan = _plan()
    shots = Shots(plan)
    from model3d.orbit_plan import read_points
    samples = read_points(_bones(), shots.names, 1.0, 24.0, 0.0, 3.0)
    for t in (1.0, 1.3, 1.7, 1.99):
        matrix, focal, principal, anchor, target = shots.camera(t, 0, samples)
        assert np.allclose(project(matrix, focal, principal, anchor), target, atol=1e-9)   # 主点を解いて、追う点が狙いの画素に
        assert np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3))
    a = shots.tracked(0, "chest", 1.5, samples)
    mean = np.mean([samples.at("chest", float(x)) for x in np.linspace(1.0, 2.0, 25)], axis=0)
    assert np.allclose(a, mean + 0.8 * (samples.at("chest", 1.5) - mean))                  # 平均から follow（既定 0.8）だけ寄せる
    still = Shots(_plan(follow={"default": 0.0}))
    assert np.allclose(still.tracked(0, "chest", 1.9, samples), still.tracked(0, "chest", 1.1, samples))
    _, f_lin, _, _, _ = shots.camera(1.5, 0, samples)
    assert f_lin == pytest.approx(2000.0 * (1.5 + (1.8 - 1.5) * 0.5))                       # linear で半分
    fixed = Shots(_plan(shots=[{**plan["shots"][0], "fix": {"zoom": [1.0, 2.0], "px": 30, "dyaw": 1.0}}, plan["shots"][1]]))
    m2, f2, p2, anchor2, target2 = fixed.camera(1.5, 0, samples)
    assert f2 == pytest.approx(f_lin * 1.5) and target2[0] == pytest.approx(530.0 + 30)     # 手直し: 寄りは u で 1 → 2、px は足す
    assert np.allclose(project(m2, f2, p2, anchor2), target2, atol=1e-9)


def test_the_orbit_goes_round_the_dancer_without_overshooting():
    xs, ys = [0.0, 1.0, 2.0, 3.0], [0.0, 1.0, 1.0, 3.0]
    values = [pchip(xs, ys, x) for x in np.linspace(0, 3, 61)]
    assert min(values) >= 0.0 and all(1.0 - 1e-12 <= pchip(xs, ys, x) <= 1.0 + 1e-12 for x in np.linspace(1, 2, 11))  # 行き過ぎない
    assert pchip(xs, ys, 1.0) == 1.0 and pchip(xs, ys, 3.0) == 3.0
    shots = Shots(_plan())
    from model3d.orbit_plan import read_points
    samples = read_points(_bones(), shots.names, 1.0, 24.0, 0.0, 3.0)
    matrix, focal, principal, anchor, _ = shots.camera(2.5, 1, samples)
    centre = samples.at("hips", 2.5)
    assert anchor is None and np.allclose(principal, [540.0, 960.0])
    assert focal == pytest.approx(960.0 / math.tan(math.radians(24.0)))
    position = matrix[:3, 3]
    assert np.allclose(position, [centre[0], centre[1] - 2.4, 1.5])                         # 方位 0: 正面（−Y）に 2.4 m、高さ 1.5 m
    aim = np.array([centre[0], centre[1], 1.0])
    forward = -matrix[:3, 2]
    assert np.allclose(forward, (aim - position) / np.linalg.norm(aim - position))
    early = shots.camera(1.5, 1, samples)[0][:3, 3]                                          # 区間の前の時刻は最初のキーのまま
    assert np.allclose(early[2], 2.0)


def test_build_writes_cameras_alt_and_references(tmp_path):
    plan = _plan(dance={"fps": 24.0, "holds": [{"from": 2.5, "at": 3.0}]}, handheld={"seed": 3},
                 kicks=[{"at": 1.4, "deg": 0.5}], wipes=[{"at": 2.0, "seconds": 0.2}], references=[{"name": "start", "shot": "A"}])
    plan["shots"][0]["handheld"] = 0.1
    cameras, track, alt, report = build(contracts.validate("shot-plan", plan), _bones())
    contracts.validate("cameras", cameras)
    contracts.validate("cameras", alt)
    contracts.validate("stage-track", track)
    assert [fr["T"] for fr in cameras["frames"]] == list(range(1, 61)) and track["frames"][0] == {"T": 1, "f": 1 + 24}
    held = [fr["f"] for fr in track["frames"] if fr["T"] >= 46]
    assert set(held) == {1 + 24 * 3.0}                                                       # 2.5 秒から踊りの時刻 3.0 で止める
    assert report["held_frames"] == 15 and report["anchor_error_px"] < 1e-9
    assert [e["T"] for e in alt["frames"]] == [29, 30, 31, 32, 33]                           # |t − 2.0| < 0.1 のコマ（1.9 秒は 0.1 ちょうどで外）
    assert [a["shot"] for a in report["alt"]] == [1, 1, 0, 0, 0]                             # 前は後のショット、ちょうどからは前のショット
    shots = Shots(plan)
    from model3d.orbit_plan import read_points
    samples = read_points(_bones(), shots.names, 1.0, 24.0, 0.0, 3.0)
    m, f, p, _, _ = shots.camera(shots.time(29), 1, samples)
    assert np.allclose(alt["frames"][0]["matrix"], m) and alt["frames"][0]["focal_px"] == pytest.approx(f)
    reference = report["references"]["start"]
    m0, f0, p0, _, _ = shots.camera(1.0, 0, samples, shake=False)
    assert np.allclose(reference["matrix"], m0) and reference["time"] == 1.0                 # 揺れなしの、ショットの頭のカメラ
    shaken = shots.camera(1.0, 0, samples)[0]
    assert not np.allclose(shaken, m0)
    entry = cameras["frames"][0]
    assert entry["lens_mm"] == pytest.approx(entry["focal_px"] / 1920 * 36)
    assert entry["shift"][0] == pytest.approx((540 - entry["principal_px"][0]) / 1920)


def test_bad_plans_are_stopped():
    with pytest.raises(ToolError, match="keys"):
        Shots(_plan(shots=[{"name": "x"}]))
    with pytest.raises(ToolError, match="u"):
        Shots(_plan(shots=[{"until": 2.0, "keys": [{"u": 0.5, "move": [0, 0, 0], "anchor": "hips", "at": [0, 0]},
                                                    {"u": 0.2, "move": [0, 0, 0], "anchor": "hips", "at": [0, 0]}]}]))
    with pytest.raises(ToolError, match="orbit.keys"):
        Shots(_plan(shots=[{"orbit": {"keys": [[2.0, 0, 2, 1], [1.0, 0, 2, 1]]}}]))
    behind = _plan(shots=[{"keys": [{"move": [0, 0, 10.0], "anchor": [540, 900], "at": [540, 900]}]}])
    with pytest.raises(ToolError, match="後ろ"):
        build(behind, _bones())
    with pytest.raises(ToolError, match="points"):
        build(_plan(dancer="pelvis"), _bones())
    with pytest.raises(ToolError, match="references"):
        build(_plan(references=[{"name": "r", "shot": "Z"}]), _bones())
    with pytest.raises(Exception):
        contracts.validate("shot-plan", _plan(shots=[{"keys": [{"move": [0, 0, 0], "anchor": "hips", "at": [0, 0]}],
                                                      "orbit": {"keys": [[0, 0, 1, 1], [1, 0, 1, 1]]}}]))


def test_the_operation_writes_its_contracts(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "plan.json", _plan(wipes=[{"at": 2.0, "seconds": 0.2}]))
    atomic_json(tmp_path / "bones.json", _bones())
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "shots",
              "inputs": {"plan": {"path": "plan.json"}, "bones": {"path": "bones.json"}},
              "steps": [{"id": "shots", "operation": "plan.shots", "inputs": {"plan": "$inputs.plan", "bones": "$inputs.bones"},
                         "parameters": {}, "outputs": {"cameras": "cameras.json", "track": "track.json", "alt": "alt.json",
                                                       "report": "report.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    step = result["steps"]["shots"]
    assert result["status"] == "succeeded", step.get("error")
    assert len(contracts.load("cameras", step["artifacts"]["alt"])["frames"]) == 5
    obs = step["observations"][0]
    assert obs["kind"] == "shot_plan" and obs["frames"] == 60 and obs["alt_frames"] == 5 and obs["anchor_error_px"] < 1e-9
    recipe["steps"][0]["parameters"] = {"fps": 24}
    atomic_json(tmp_path / "recipe.json", recipe)
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["status"] == "failed"
