"""目の骨を目の球の中心へ（model.eye_rig）: 目の形の値の確かめ、骨の根もとと先の計算、目の骨の名前の決め方（Blender なし）、
工程の受け渡し（代役の Blender）。本物の Blender 5.2.4 での照合は docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import eye_math, eye_rig
from model3d.io import atomic_json


def geometry(**change):
    """目の形の値の例（右目と左目）。"""
    def eye(sign):
        normal = np.array([-0.4 * sign, -0.9, -0.1])
        normal /= np.linalg.norm(normal)
        return {"sphere": {"centre": [0.04 * sign, 0.1, 1.45], "radius": 0.12, "normal": normal.tolist()},
                "pupil": [-0.025 * sign, 1.44], "iris": {"a": 0.006, "b": 0.005},
                "rim": {"x": sorted([-0.04 * sign, -0.025 * sign, -0.01 * sign]), "upper": [1.446, 1.448, 1.446], "lower": [1.436, 1.434, 1.436]},
                "window": sorted([-0.055 * sign, -0.003 * sign]) + [1.42, 1.47], "iris_vertices": [10, 11, 12] if sign > 0 else [20, 21, 22],
                "lid_vertices": [1, 2, 3]}
    data = {"schema_version": "model3d-eye-geometry/0.1", "object": "Head", "eyes": {"R": eye(1.0), "L": eye(-1.0)}}
    for key, value in change.items():
        side, field = key.split("__")
        if value is None:
            data["eyes"][side].pop(field)
        else:
            data["eyes"][side][field] = value
    return data


def test_geometry_is_checked():
    assert eye_math.validate(geometry())["object"] == "Head"
    assert eye_math.validate(geometry(), vertex_count=30)
    for change, message in (({"R__sphere": {"centre": [0, 0, 1], "radius": 0.12, "normal": [0, -2, 0]}}, "長さ 1"),
                            ({"R__sphere": {"centre": [0, 0], "radius": 0.12, "normal": [0, -1, 0]}}, "centre"),
                            ({"L__sphere": {"centre": [0, 0, 1], "radius": 0, "normal": [0, -1, 0]}}, "radius"),
                            ({"R__iris": {"a": 0.007}}, "iris.b"), ({"R__rim": {"x": [0.1, 0.0], "upper": [1, 1], "lower": [1, 1]}}, "小さい順"),
                            ({"R__rim": {"x": [0.0, 0.1], "upper": [1, 1, 1], "lower": [1, 1]}}, "rim"),
                            ({"L__window": [0.1, 0.0, 1.3, 1.4]}, "window"), ({"R__iris_vertices": [1, 1]}, "2 回"),
                            ({"R__iris_vertices": [1, -2]}, "iris_vertices"), ({"L__pupil": None}, "pupil")):
        with pytest.raises(eye_math.GeometryError, match=message):
            eye_math.validate(geometry(**change))
    with pytest.raises(eye_math.GeometryError, match="頂点の数"):
        eye_math.validate(geometry(), vertex_count=15)
    with pytest.raises(eye_math.GeometryError, match="schema_version"):
        eye_math.validate({"object": "Head"})
    with pytest.raises(eye_math.GeometryError, match=r"\['R', 'L'\]"):
        eye_math.validate({"schema_version": eye_math.SCHEMA, "object": "Head", "eyes": {"R": geometry()["eyes"]["R"]}})


def test_bone_reaches_the_plate_from_the_sphere_centre():
    normal = np.array([0.4, -0.9, -0.1]) / np.linalg.norm([0.4, -0.9, -0.1])
    root, tip = eye_math.bone_ends([0.04, 0.1, 1.45], normal.tolist(), 0.12)
    assert np.allclose(root, [0.04, 0.1, 1.45]) and np.linalg.norm(tip - root) == pytest.approx(0.12)
    assert np.allclose((tip - root) / 0.12, normal)
    # 滑る量 = 半径 × 回す角: 半径 10 cm で 5 mm 滑らせるのに 2.86°
    assert math.degrees(0.005 / 0.1) == pytest.approx(2.86, abs=0.01)
    placed = np.eye(4)
    placed[:3, 3] = (0.0, 0.0, 0.5)
    placed[:3, :3] = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]                      # アーマチュアが 90° 回って 0.5 m 上にある
    local = eye_math.to_armature([1.0, 2.0, 3.5], placed)
    assert np.allclose(placed[:3, :3] @ local + placed[:3, 3], [1.0, 2.0, 3.5])


def test_roll_up_and_length_follow_the_armature_placement():
    still = np.eye(4)
    still[:3, 3] = (0.3, 0.0, 0.2)                                              # 動かしただけなら、世界の上のまま（試しと同じ）
    assert eye_math.up_in_armature(still).tolist() == [0.0, 0.0, 1.0]
    tilted = np.eye(4)
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    tilted[:3, :3] = [[1, 0, 0], [0, c, -s], [0, s, c]]                       # X のまわりに 30° 傾けたアーマチュア
    up = eye_math.up_in_armature(tilted)
    assert np.allclose(tilted[:3, :3] @ up, [0.0, 0.0, 1.0])                  # アーマチュアの中の向きを世界へ戻すと、世界の上
    scaled = np.diag([2.0, 2.0, 2.0, 1.0])
    assert np.allclose(eye_math.up_in_armature(scaled), [0.0, 0.0, 1.0]) and np.linalg.norm(eye_math.up_in_armature(scaled)) == pytest.approx(1.0)
    assert eye_math.world_length(scaled, [0.0, 0.0, 0.0], [0.0, 0.06, 0.0]) == pytest.approx(0.12)   # 大きさ 2 なら、中の 6 cm は世界で 12 cm
    assert eye_math.world_length(tilted, [0.0, 0.0, 0.0], [0.0, 0.12, 0.0]) == pytest.approx(0.12)


def test_eye_bones_come_from_the_humanoid_when_not_given():
    assert eye_rig.eye_bones(None, {"right_eye": "eye.R", "left_eye": "eye.L"}) == {"R": "eye.R", "L": "eye.L"}
    assert eye_rig.eye_bones({"R": "EyeR", "L": "EyeL"}, {}) == {"R": "EyeR", "L": "EyeL"}
    with pytest.raises(SystemExit, match="right_eye"):
        eye_rig.eye_bones(None, {"left_eye": "eye.L"})
    assert eye_rig.GROUP_ORDER == ("L", "R")                                   # 頂点グループは左目・右目の順に足す


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out"))
eyes = {side: {"iris_vertices": len(settings["geometry"]["eyes"][side]["iris_vertices"]), "length_mm": 120.0} for side in ("R", "L")}
json.dump({"blender": "5.2.4", "bones": settings["bones"] or {"R": "eye.R", "L": "eye.L"}, "groups_added": ["eye.L", "eye.R"],
           "eyes": eyes, "x_settings": settings, "x_argv": sys.argv[1:]}, open(value("--report"), "w", encoding="utf-8"))
'''


def _recipe(tmp_path, parameters, data=None):
    (tmp_path / "head.blend").write_bytes(b"blend")
    atomic_json(tmp_path / "eyes.json", data or geometry())
    step = {"id": "rig", "operation": "model.eye_rig", "inputs": {"blend": "$inputs.head", "geometry": "$inputs.eyes"},
            "parameters": parameters, "outputs": {"blend": "rig.blend", "report": "rig.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "rig",
                                           "inputs": {"head": {"path": "head.blend"}, "eyes": {"path": "eyes.json"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_eye_rig_step_passes_the_geometry(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "bones": {"R": "EyeR", "L": "EyeL"}}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["rig"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["bones"] == {"R": "EyeR", "L": "EyeL"} and report["x_settings"]["geometry"]["object"] == "Head"
    assert report["blender_version"] == "5.2.4" and step["observations"][0]["eyes"]["R"]["iris_vertices"] == 3
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "eye_rig.py" and "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]
    assert executor.run_recipe(_recipe(tmp_path, {"blender": blender}), tmp_path / "runs")["steps"]["rig"]["reused_from"] is None


@pytest.mark.parametrize("parameters, data, message", [
    ({"bones": {"R": "eye"}}, None, "bones"), ({"bones": {"R": "eye", "L": "eye"}}, None, "bones"), ({"spin": 1}, None, "知らないパラメーター"),
    ({}, {"schema_version": "other"}, "目の形の値"), ({"armature": ""}, None, "armature")])
def test_eye_rig_refuses_bad_settings(tmp_path, parameters, data, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}, data), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["rig"]["error"]
