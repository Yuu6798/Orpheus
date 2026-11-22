"""plan.stills（見本のコマ）: T の並び（f の小さい順）、足もとからの位置・狙う点・向き・レンズ・絞りとピント、plan.orbit で同じ踊りの
コマと位置を書いた 1 コマとの行列の一致、光の値の重ね方、悪いパラメーター、工程の受け渡し。値は作り物（どの曲とも関係ない）。
リポジトリ外の 4 作目の 3D のダンスの見本の台本との照合は docs/model3d.md。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from model3d import contracts, orbit_plan, stills_plan
from model3d.io import ToolError, atomic_json
from model3d.ops import plan_ops


def _path(count=60):
    """動きのコマ 1〜count。腰は x の向きへ 1 コマに 0.01 m 進み、胸は腰の 0.3 m 上、顔は 0.5 m 上で 0.1 m 前（−Y）。"""
    hips = np.stack([np.arange(count) * 0.01, np.zeros(count), np.full(count, 1.0)], axis=1)
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": [1.0 + k for k in range(count)], "bones": {},
            "points": {"hips": hips.tolist(), "chest": (hips + [0, 0, 0.3]).tolist(), "face": (hips + [0, -0.1, 0.5]).tolist()}}


def _shot(name, f, **extra):
    return {"name": name, "f": f, "offset": [0.5, -2.0, 1.4], "aim": "chest", "lens": 50, **extra}


def _build(parameters, path=None):
    settings = plan_ops._stills_settings(parameters)
    cameras, track, lights, report = stills_plan.build(settings, path or _path())
    contracts.validate("cameras", cameras)
    contracts.validate("stage-track", track)
    contracts.validate("light-track", lights)
    return cameras, track, lights, report


def test_the_shots_are_ordered_by_the_dance_frame():
    cameras, track, lights, report = _build({"shots": [_shot("late", 40), _shot("early", 5), _shot("middle", 21)]})
    assert [(fr["T"], fr["f"]) for fr in cameras["frames"]] == [(1, 5), (2, 21), (3, 40)]
    assert [(fr["T"], fr["f"]) for fr in track["frames"]] == [(1, 5), (2, 21), (3, 40)]
    assert [(row["T"], row["name"]) for row in report["shots"]] == [(1, "early"), (2, "middle"), (3, "late")]
    assert [fr["T"] for fr in lights["frames"]] == [1, 2, 3]


def test_the_camera_stands_off_the_feet_and_looks_level_at_the_aim():
    cameras, _, _, report = _build({"shots": [_shot("one", 11, dz=0.05)], "camera": {"resolution": [540, 960], "sensor_height": 36}})
    frame = cameras["frames"][0]
    M = np.array(frame["matrix"])
    hips = np.array([0.1, 0.0, 1.0])                                              # コマ 11 の腰
    position = np.array([0.1 + 0.5, -2.0, 1.4])                                   # 腰の真下の床 (x, y, 0) ＋ offset
    aim = hips + [0, 0, 0.3 + 0.05]
    assert np.allclose(M[:3, 3], position)
    forward = (aim - position) / np.linalg.norm(aim - position)
    assert np.allclose(-M[:3, 2], forward) and abs(M[2, 0]) < 1e-12               # 狙う点を向き、傾けない
    assert np.allclose(M[:3, 1], np.cross(M[:3, 0], forward)) and M[2, 1] > 0     # 上は右 × 前（上向き）
    assert frame["focal_px"] == 960 * 50 / 36 and frame["lens_mm"] == 50.0 and frame["principal_px"] == [270.0, 480.0]
    assert frame["dancer"] == hips.tolist() and frame["distance_m"] == pytest.approx(np.linalg.norm(hips - position))
    assert "fstop" not in frame and "focus_m" not in frame                        # 絞りが無ければ被写界深度なし
    assert report["shots"][0]["aim_distance_m"] == pytest.approx(np.linalg.norm(aim - position)) and report["outside"] == 0


def test_the_focus_is_the_named_point_or_the_aim():
    cameras, _, _, _ = _build({"shots": [_shot("aim", 3, fstop=2.8), _shot("face", 9, fstop=4, focus="face")]})
    near, far = cameras["frames"]
    position = np.array(near["matrix"])[:3, 3]
    assert near["fstop"] == 2.8 and near["focus_m"] == pytest.approx(np.linalg.norm(np.array([0.02, 0, 1.3]) - position))
    position = np.array(far["matrix"])[:3, 3]
    assert far["fstop"] == 4.0 and far["focus_m"] == pytest.approx(np.linalg.norm(np.array([0.08, -0.1, 1.5]) - position))


def test_the_matrix_matches_plan_orbit_for_the_same_frame_and_place():
    """plan.orbit の 1 コマ（ならしなし。踊りの時刻 0 秒が動きのコマ f）と、同じ位置（腰からの方位・距離・高さ）を offset で書いた見本。"""
    path = _path()
    for f, az, dist, height, dz, lens in ((11, 25.0, 2.4, 1.5, 0.1, 50.0), (37, -70.0, 3.1, 0.9, 0.0, 35.0)):
        plan = {"schema_version": "model3d-orbit-plan/0.1", "fps": 30, "frames": 1, "dance": {"fps": 30, "frame0": f},
                "smooth": {"sigma_s": 0.0}, "camera": {"resolution": [1080, 1920], "sensor_height": 36.0},
                "shots": [{"keys": [{"az": az, "dist": dist, "height": height, "aim": "chest", "dz": dz, "lens": lens}]}]}
        orbit_cameras, _, _ = orbit_plan.build(plan, path)
        theta = math.radians(az)
        offset = [dist * math.sin(theta), -dist * math.cos(theta), height]
        cameras, _, _, _ = _build({"shots": [{"name": "s", "f": f, "offset": offset, "aim": "chest", "dz": dz, "lens": lens}]}, path)
        a, b = np.array(orbit_cameras["frames"][0]["matrix"]), np.array(cameras["frames"][0]["matrix"])
        assert np.abs(a - b).max() < 1e-12
        assert orbit_cameras["frames"][0]["f"] == f and orbit_cameras["frames"][0]["focal_px"] == pytest.approx(cameras["frames"][0]["focal_px"])


def test_the_light_values_are_layered():
    _, _, lights, report = _build({"lights": {"key": 1.0, "ambient": [0.4, 0.3, 0.5]},
                                   "shots": [_shot("a", 3), _shot("b", 8, lights={"key": 0.2, "flash": 0.5, "rim": 2})]})
    first, second = (fr["values"] for fr in lights["frames"])
    assert first == {"flash": 0.0, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0, "key": 1.0, "ambient": [0.4, 0.3, 0.5]}
    assert second == {"flash": 0.5, "flash_color": [1.0, 1.0, 1.0], "fade": 1.0, "key": 0.2, "ambient": [0.4, 0.3, 0.5], "rim": 2.0}
    assert report["lights"] == ["ambient", "fade", "flash", "flash_color", "key", "rim"]


def test_points_outside_the_path_are_counted():
    _, _, _, report = _build({"shots": [_shot("far", 200)]})
    assert report["outside"] == 2                                                 # 足もとの点と狙う点


@pytest.mark.parametrize("parameters, message", [
    ({}, "shots"),
    ({"shots": []}, "shots"),
    ({"shots": [_shot("a", 3)], "zoom": 1}, "知らないパラメーター"),
    ({"shots": [_shot("a", 3), _shot("b", 3)]}, "同じ踊りのコマ 3"),
    ({"shots": [_shot("a", 3), _shot("a", 4)]}, "name"),
    ({"shots": [_shot("a", 3.5)]}, "整数"),
    ({"shots": [{**_shot("a", 3), "lens": 0}]}, "lens"),
    ({"shots": [{k: v for k, v in _shot("a", 3).items() if k != "aim"}]}, "aim"),
    ({"shots": [{**_shot("a", 3), "offset": [0, 1]}]}, "offset"),
    ({"shots": [{**_shot("a", 3), "focus": "face"}]}, "fstop"),
    ({"shots": [{**_shot("a", 3), "fstop": -1}]}, "fstop"),
    ({"shots": [{**_shot("a", 3), "tilt": 1}]}, "知らない鍵"),
    ({"shots": [{**_shot("a", 3), "lights": {"key": "on"}}]}, "lights.key"),
    ({"shots": [_shot("a", 3)], "lights": {"fade": -1}}, "fade"),
    ({"shots": [_shot("a", 3)], "lights": {"flash_color": 1}}, "flash_color"),
    ({"shots": [_shot("a", 3)], "lights": {"ambient": [1, 2]}}, "ambient"),
    ({"shots": [_shot("a", 3)], "camera": {"resolution": [540]}}, "resolution"),
    ({"shots": [_shot("a", 3)], "clip_m": [5, 1]}, "clip_m"),
    ({"shots": [_shot("a", 3)], "fps": 0}, "fps"),
])
def test_bad_parameters_are_refused(parameters, message):
    with pytest.raises(ToolError, match=message):
        plan_ops._stills_settings(parameters)


def test_a_missing_point_and_a_camera_on_the_aim_are_refused():
    with pytest.raises(ToolError, match="点がありません"):
        _build({"shots": [_shot("a", 3, aim="knee")]})
    with pytest.raises(ToolError, match="真上か真下にあり"):
        _build({"shots": [{**_shot("a", 3), "offset": [0.0, 0.0, 3.0]}]})


def test_the_operation_writes_cameras_lights_and_the_report(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "bones.json", _path())
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "stills", "inputs": {"bones": {"path": "bones.json"}},
              "steps": [{"id": "stills", "operation": "plan.stills", "inputs": {"bones": "$inputs.bones"},
                         "parameters": {"shots": [_shot("b", 30, fstop=2.8), _shot("a", 10)], "lights": {"key": 1.0}, "clip_m": [0.05, 400]},
                         "outputs": {"cameras": "cameras.json", "track": "track.json", "lights": "lights.json", "report": "stills.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["stills"]
    cameras = contracts.load("cameras", step["artifacts"]["cameras"])
    assert [fr["f"] for fr in cameras["frames"]] == [10, 30] and cameras["clip_m"] == [0.05, 400.0]
    assert contracts.load("light-track", step["artifacts"]["lights"])["frames"][1]["values"]["key"] == 1.0
    assert json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))["shots"][0]["name"] == "a"
    assert step["observations"][0] == {"kind": "stills_plan", "frames": 2, "f": [10, 30], "dof_frames": 1, "outside_samples": 0}
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["stills"]["reused_from"] is not None


def test_the_operation_needs_the_bones_and_both_outputs(tmp_path):
    from model3d.ops import get
    with pytest.raises(ToolError, match="bones"):
        get("plan.stills").function({}, {"shots": [_shot("a", 3)]}, {"cameras": tmp_path / "c.json", "lights": tmp_path / "l.json"}, None)
    atomic_json(tmp_path / "bones.json", _path())
    with pytest.raises(ToolError, match="lights"):
        get("plan.stills").function({"bones": tmp_path / "bones.json"}, {"shots": [_shot("a", 3)]}, {"cameras": tmp_path / "c.json"}, None)
