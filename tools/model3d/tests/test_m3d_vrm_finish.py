from pathlib import Path

import numpy as np
import pytest
from m3d_helpers import stand_in_executable

from model3d import executor
from model3d.blender_adapter.eye_emissive_mask import eye_mask
from model3d.blender_adapter.face_normals import crown_normals
from model3d.io import ToolError, atomic_json
from model3d.ops.face_ops import crown_normals_settings
from model3d.ops.texture_ops import eye_emissive_settings


CROWN = {"objects": ["Hair"], "centre": [0, 0, 0], "radii": [1, 2, 2], "height": [0, 2], "weight": 1}
EYE = {"material": "Eyes", "iris_box": [0, 0, 0.75, 0.5]}


def test_dome_transfer_keeps_opposite_ribbon_normals_and_fades_into_an_ellipsoid():
    lower = [1, 0, 0]
    middle = [1, 0, 1]
    upper = [1, 0, 2]
    points = np.array([lower, lower, middle, upper], dtype=float)
    old = np.array([[0, 0, 1], [0, 0, -1], [0, 0, 1], [0, 0, 1]], dtype=float)
    normals, selected = crown_normals(points, old, np.eye(3), CROWN)
    assert selected.tolist() == [False, False, True, True]
    np.testing.assert_array_equal(normals[:2], old[:2])
    middle_outward = np.array([1, 0, 0.25])
    blended = old[2] + middle_outward / np.linalg.norm(middle_outward)
    np.testing.assert_allclose(normals[2], blended / np.linalg.norm(blended))
    np.testing.assert_allclose(normals[3], np.array([2, 0, 1]) / np.sqrt(5))


def test_dome_transfer_maps_the_target_into_local_space_under_nonuniform_scale():
    linear = np.array([[0, -3, 0], [2, 0, 0], [0, 0, 4]])
    normal, _ = crown_normals(np.array([[1.0, 0, 2]]), np.array([[0.0, 0, 1]]), linear, CROWN)
    np.testing.assert_allclose(normal[0], np.array([0, -3, 2]) / np.sqrt(13))


def test_eye_mask_protects_the_pupil_and_turns_bright_iris_and_sclera_into_different_strengths():
    atlas = np.ones((4, 4, 4), dtype=np.float32)
    atlas[0, 0, :3] = 0.14
    atlas[0, 1, :3] = 0.39  # halfway through the default luminance ramp
    atlas[1, 0, 3] = 0     # alpha-zero iris stays dark even when the RGB is white
    atlas[3, 0, :3] = 0.14
    image, report = eye_mask(atlas, eye_emissive_settings(EYE))
    assert image == pytest.approx(np.array([[0, 0.5, 1, 0.08], [0, 1, 1, 0.08],
                                             [0.08, 0.08, 0.08, 0.08], [0, 0.08, 0.08, 0.08]]))
    assert report["iris_pixels"] == 6
    assert report["protected_iris_pixels"] == 1


@pytest.mark.parametrize(("overrides", "field"), [
    ({"objects": ["Hair", "Hair"]}, "objects"),
    ({"radii": [1, 0, 1]}, "radii"),
    ({"height": [2, 2]}, "height"),
])
def test_crown_settings_reject_invalid_geometry(overrides, field):
    with pytest.raises(ToolError, match=field):
        crown_normals_settings({**CROWN, **overrides})


@pytest.mark.parametrize(("overrides", "field"), [
    ({"iris_box": [0, 0.5, 1, 0.5]}, "iris_box"),
    ({"luminance": [0.7, 0.1]}, "luminance"),
    ({"emissive_factor": [1, float("nan"), 0]}, "emissive_factor"),
])
def test_eye_settings_reject_invalid_values(overrides, field):
    with pytest.raises(ToolError, match=field):
        eye_emissive_settings({**EYE, **overrides})


BLENDER_STAND_IN = r'''
import json
from pathlib import Path
import sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def arg(flag):
    return Path(args[args.index(flag) + 1])
settings = json.loads(arg("--settings").read_text(encoding="utf-8"))
source = arg("--blend").read_bytes()
if "--crown" in args:
    assert settings["height"] == [0, 2]
    assert source == b"source"
    report = {"objects": [{"changed_corners": 3}]}
    arg("--out-blend").write_bytes(b"crown")
else:
    assert settings["material"] == "Eyes"
    assert source == b"crown"
    report = {"protected_iris_pixels": 4, "iris_mask_max": 1, "packed_sha256": "test_hash"}
    arg("--out-blend").write_bytes(b"eyes")
    arg("--mask").write_bytes(b"png")
arg("--report").write_text(json.dumps(report), encoding="utf-8")
'''


def test_crown_and_eye_mask_recipe_chain_the_blend_outputs(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", BLENDER_STAND_IN)
    source = tmp_path / "source.blend"
    source.write_bytes(b"source")
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {
        "schema_version": "model3d-recipe/0.1", "name": "finish", "inputs": {"source": {"path": source.name}},
        "steps": [
            {"id": "crown", "operation": "model.crown_normals", "inputs": {"blend": "$inputs.source"},
             "parameters": {**CROWN, "blender": str(blender)}, "outputs": {"blend": "crown.blend", "report": "crown.json"}},
            {"id": "eyes", "operation": "texture.eye_emissive_mask", "inputs": {"blend": "$steps.crown.blend"},
             "parameters": {**EYE, "blender": str(blender)}, "outputs": {"blend": "eyes.blend", "report": "eyes.json", "mask": "eyes.png"}},
        ],
    })
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    crown, eyes = (result["steps"][name] for name in ("crown", "eyes"))
    assert crown["observations"][0]["objects"] == [{"changed_corners": 3}]
    assert eyes["observations"][0]["protected_iris_pixels"] == 4
    assert Path(eyes["artifacts"]["mask"]).read_bytes() == b"png"
    assert source.read_bytes() == b"source"
