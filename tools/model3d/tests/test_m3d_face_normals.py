import json
from pathlib import Path

import numpy as np
import pytest
from m3d_helpers import stand_in_executable

from model3d import executor
from model3d.blender_adapter.face_normals import custom_normals
from model3d.io import ToolError, atomic_json
from model3d.ops.face_ops import face_normals_settings


def test_face_blend_maps_world_direction_back_under_nonuniform_scale():
    world = np.array([[0.0, -3, 0], [2, 0, 0], [0, 0, 4]])
    points = np.array([[1.0, 0, 2]])
    corners = np.array([[0.0, 0, 1]])
    result = custom_normals(points, corners, world, face={"centre": [0, 0, 0], "stretch_z": 2, "weight": 1})
    assert result[0] == pytest.approx(np.array([0, -3, 4]) / 5)


def test_neck_blend_is_cylindrical_at_low_edge_and_smooth_through_midpoint():
    points = np.array([[2.0, 2, 0], [2, 2, 1], [2, 2, 2], [1, 2, 0]])
    corners = np.tile(np.array([0.0, 0, 1]), (4, 1))
    result = custom_normals(points, corners, np.eye(3), neck={"centre_xy": [1, 2], "height": [0, 2]})
    middle = 1 / np.sqrt(2)
    assert result == pytest.approx(np.array([[1, 0, 0], [middle, 0, middle], [0, 0, 1], [0, 0, 1]]))


def test_face_keeps_original_at_centre_and_when_directions_cancel():
    points = np.array([[0.0, 0, 0], [0, 0, -1]])
    corners = np.array([[0.0, 0, 1], [0, 0, 1]])
    assert np.array_equal(custom_normals(points, corners, np.eye(3), face={"centre": [0, 0, 0], "stretch_z": 2, "weight": 0.5}),
                          corners)
    with pytest.raises(ValueError, match="逆行列"):
        custom_normals(points, corners, np.zeros((3, 3)), face={"centre": [0, 0, 0], "stretch_z": 2, "weight": 1})


@pytest.mark.parametrize(("params", "message"), [
    ({}, "face または neck"),
    ({"face": {"centre": [0, 0, 0], "stretch_z": 0, "weight": 0.375}}, "stretch_z"),
    ({"face": {"centre": [0, 0, 0], "stretch_z": 2, "weight": 1.125}}, "weight"),
    ({"face": {"centre": [0, 0], "stretch_z": 2, "weight": 0.375}}, "centre"),
    ({"face": {"centre": [0, 0, float("nan")], "stretch_z": 2, "weight": 0.375}}, "有限"),
    ({"neck": {"centre_xy": [0, 0], "height": [2, 1]}}, "height"),
    ({"neck": {"centre_xy": [0, 0]}}, "height"),
    ({"shift": 0.375}, "知らない"),
])
def test_face_normal_settings_validate_explicit_geometry_parameters(params, message):
    with pytest.raises(ToolError, match=message):
        face_normals_settings({"object": "Sample", "material": "Target", **params})


STAND_IN = r'''
import json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def arg(name):
    return Path(args[args.index(name) + 1])
settings = json.loads(arg("--settings").read_text(encoding="utf-8"))
arg("--out-blend").write_bytes(b"result")
arg("--report").write_text(json.dumps({"target_corners": 8, "changed_corners": 6, "settings": settings,
                                     "argv": sys.argv[1:]}), encoding="utf-8")
'''


def test_face_normal_recipe_passes_both_regions_to_blender(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    source = tmp_path / "sample.blend"
    source.write_bytes(b"source")
    params = {"object": "Sample", "material": "Target", "face": {"centre": [0, 0, 2], "stretch_z": 2, "weight": 0.375},
              "neck": {"centre_xy": [0, 0], "height": [0, 1]}}
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "normals", "inputs": {"sample": {"path": source.name}},
              "steps": [{"operation": "model.face_normals", "id": "normals", "inputs": {"blend": "$inputs.sample"},
                         "parameters": {"blender": str(blender), **params}, "outputs": {"blend": "out.blend", "report": "normals.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["normals"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == params
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "face_normals.py"
    assert step["observations"][0]["changed_corners"] == 6
    assert source.read_bytes() == b"source"
