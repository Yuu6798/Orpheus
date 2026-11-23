import json
from pathlib import Path
from types import SimpleNamespace as Namespace

import numpy as np
import pytest
from m3d_helpers import stand_in_executable

from model3d import executor
from model3d.blender_adapter.view_point import ray_intersections, texture_sample
from model3d.io import ToolError, atomic_json
from model3d.ops.check_ops import view_point_settings


def _settings():
    return {"objects": ["Sample"], "rays": [{"origin": [0, -1, 0], "direction": [0, 5, 0]}]}


def test_ray_returns_both_windings_sorted_by_distance_and_ignores_other_triangles():
    base = np.array([[-2.0, 0, -1], [2.0, 0, -1], [0.0, 0, 2]])
    triangles = np.array([base + [0, 2, 0], base[::-1], base + [0, -2, 0], base + [10, 0, 0],
                          np.zeros((3, 3))])
    indices, distances, weights = ray_intersections(triangles, np.array([0, -1, 0]), np.array([0, 1, 0]))
    assert indices.tolist() == [1, 0]
    assert distances.tolist() == [1.0, 3.0]
    assert weights == pytest.approx(np.full((2, 3), 1 / 3))
    points = np.einsum("ij,ijk->ik", weights, triangles[indices])
    assert points == pytest.approx(np.array([[0, 0, 0], [0, 2, 0]]))


def test_ray_at_triangle_edge_preserves_valid_barycentric_weights():
    triangle = np.array([[[-1, 0, 0], [1, 0, 0], [0, 0, 1]]])
    indices, distances, weights = ray_intersections(triangle, np.array([0, -2, 0]), np.array([0, 1, 0]))
    assert indices.tolist() == [0]
    assert distances.tolist() == [2.0]
    assert weights.tolist() == [[0.5, 0.5, 0.0]]


@pytest.mark.parametrize(("extension", "uv", "pixel"), [
    ("REPEAT", [-0.25, 1.25], [1, 0]),
    ("MIRROR", [-0.25, 1.25], [0, 1]),
    ("EXTEND", [-0.25, 1.25], [0, 1]),
    ("CLIP", [-0.25, 1.25], None),
])
def test_texture_sample_uses_image_node_extension(extension, uv, pixel):
    data = [0.125, 0.25, 0.375, 1.0] * 4
    node = Namespace(extension=extension, image=Namespace(name="Test image", size=(2, 2), channels=4, pixels=data,
                                                           colorspace_settings=Namespace(name="sRGB")))
    sample = texture_sample(node, uv)
    if pixel is None:
        assert sample is None
    else:
        assert sample == {"pixel": pixel, "rgb": [0.125, 0.25, 0.375], "image": "Test image", "color_space": "sRGB"}


@pytest.mark.parametrize(("overrides", "message"), [
    ({"objects": []}, "objects"),
    ({"objects": ["Sample", "Sample"]}, "重なった"),
    ({"rays": []}, "rays"),
    ({"rays": [{"origin": [0, 1, 0], "direction": [0, 0, 0]}]}, "ゼロ"),
    ({"rays": [{"origin": [0, 1, 0], "direction": [0, 1, float("inf")]}]}, "有限"),
    ({"rays": [{"origin": [0, 1], "direction": [0, 1, 0]}]}, "origin"),
    ({"modifiers": "false"}, "true / false"),
    ({"keys": {"Unselected": {"Lift": 0.5}}}, "keys.Unselected"),
])
def test_invalid_probe_settings(overrides, message):
    with pytest.raises(ToolError, match=message):
        view_point_settings({**_settings(), **overrides})


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
report = {"schema_version": "model3d-view-point/0.1", "settings": settings, "argv": sys.argv[1:],
          "rays": [{**ray, "hits": []} for ray in settings["rays"]]}
arg("--report").write_text(json.dumps(report), encoding="utf-8")
'''


def test_recipe_passes_normalized_rays_and_writes_a_report(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    scene = tmp_path / "fixture.blend"
    scene.write_bytes(b"synthetic scene")
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "point", "inputs": {"scene": {"path": scene.name}},
              "steps": [{"operation": "check.view_point", "id": "point", "inputs": {"blend": "$inputs.scene"},
                         "parameters": {**_settings(), "keys": {"Sample": {"Lift": 0.5}}, "modifiers": False,
                                        "blender": str(blender)}, "outputs": {"report": "point.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["point"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == {"objects": ["Sample"], "keys": {"Sample": {"Lift": 0.5}}, "modifiers": False,
                                  "rays": [{"origin": [0, -1, 0], "direction": [0, 1, 0]}]}
    assert step["observations"][0]["intersections"] == [0]
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "view_point.py"
    assert scene.read_bytes() == b"synthetic scene"
