import hashlib
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace as Namespace

import pytest
from m3d_helpers import stand_in_executable

from model3d import executor
from model3d.blender_adapter.mtoon_preview import _shape_keys, shading_bounds
from model3d.io import ToolError, atomic_json
from model3d.ops.face_ops import mtoon_preview_settings


def _settings():
    return {
        "lights": [{"name": "key", "direction": [0, -3, 4]}],
        "views": [{"name": "detail", "direction": [0, -6, 0], "centre": [0, 0, 1], "scale": 2.25, "size": [64, 96]}],
    }


def test_shading_interval_has_expected_shift_width_and_exact_threshold():
    assert shading_bounds(0.125, 0.375) == (-0.75, 0.5)
    assert shading_bounds(-0.25, 1.0) == (0.25, 0.25)


def test_settings_uses_explicit_directions_and_supports_ordered_pose_and_multiple_shapes():
    settings = mtoon_preview_settings({
        **_settings(),
        "armature": "Rig", "pose": [{"bone": "joint", "axis": "Y", "degrees": -20}],
        "sets": [{"name": "rest"}, {"name": "change", "keys": {"Sample": {"Lift": 0.5}}}],
    })
    assert settings["lights"][0]["direction"] == pytest.approx([0, -0.6, 0.8])
    assert settings["views"][0] == {"name": "detail", "direction": [0, -1, 0], "centre": [0, 0, 1], "scale": 2.25, "size": [64, 96]}
    assert settings["pose"][0]["degrees"] == -20 and settings["reset_keys"]
    assert settings["sets"][1]["keys"] == {"Sample": {"Lift": 0.5}}


@pytest.mark.parametrize(("overrides", "message"), [
    ({"lights": []}, "lights"),
    ({"lights": [{"name": "key", "direction": [0, 0, 0]}]}, "ゼロ"),
    ({"views": [{"name": "a", "direction": [0, 0, 1], "centre": [0, 0, 0], "scale": 0}]}, "scale"),
    ({"views": [{"name": "a", "size": [16, 5000]}]}, "size"),
    ({"sets": [{"name": "rest"}, {"name": "rest"}]}, "重なっています"),
    ({"sets": [{"name": "rest"}], "keys": {}}, "どちらか一方"),
    ({"keys": {"Sample": {"Lift": float("nan")}}}, "有限"),
    ({"pose": [{"bone": "joint", "axis": "Y", "degrees": 20}]}, "armature"),
    ({"pose": [{"bone": "joint", "axis": "W", "degrees": 20}]}, "axis"),
    ({"background": [0.125, 0.25, 1.125]}, "background"),
])
def test_preview_invalid_settings(overrides, message):
    with pytest.raises(ToolError, match=message):
        mtoon_preview_settings({**_settings(), **overrides})


def test_shared_key_conflicts_and_basis_fail_before_changes():
    key = Namespace(name="Basis")
    data = Namespace(as_pointer=lambda: 42, use_relative=True, key_blocks={"Basis": key, "Lift": object()}, reference_key=key)
    obj = Namespace(type="MESH", data=Namespace(shape_keys=data))
    scene = Namespace(objects={"a": obj, "b": obj})
    with pytest.raises(ValueError, match="異なる値"):
        _shape_keys(scene, [{"name": "test", "keys": {"a": {"Lift": 0.125}, "b": {"Lift": 0.25}}}], False)
    with pytest.raises(ValueError, match="Basis"):
        _shape_keys(scene, [{"name": "test", "keys": {"a": {"Basis": 0.125}}}], False)


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_blender_pose_survives_action_nla_and_driver_reevaluation(tmp_path):
    from model3d.blender_adapter import mtoon_preview

    test_script = tmp_path / "test_pose.py"
    test_script.write_text("""
import math
import runpy
import sys
import bpy
from mathutils import Matrix

pose = runpy.run_path(sys.argv[-1])["_pose"]

def close(actual, expected):
    return all(abs(a - b) < 1e-5 for row_a, row_b in zip(actual, expected) for a, b in zip(row_a, row_b))

for use_nla in (False, True):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    scene = bpy.context.scene
    bpy.ops.object.armature_add()
    rig = bpy.context.object
    bone = rig.pose.bones[0]
    bone.rotation_mode = "XYZ"
    bone.rotation_euler.x = 0.25
    bone.keyframe_insert("rotation_euler", index=0, frame=1)
    bone.rotation_euler.x = 0.75
    bone.keyframe_insert("rotation_euler", index=0, frame=9)
    if use_nla:
        action = rig.animation_data.action
        rig.animation_data.action = None
        rig.animation_data.nla_tracks.new().strips.new("Motion", 1, action)
    bone.driver_add("rotation_euler", 1).driver.expression = "0.03 * frame"
    rig.driver_add("location", 2).driver.expression = "0.05 * frame"
    constraint = bone.constraints.new("LIMIT_LOCATION")
    constraint.use_min_x = True
    constraint.min_x = 0.75
    scene.frame_set(4)
    evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
    reference = evaluated.matrix_basis.copy()
    evaluated_bone = evaluated.pose.bones[bone.name]
    assert 0.25 < evaluated_bone.rotation_euler.x < 0.75
    assert abs(evaluated_bone.rotation_euler.y - 0.12) < 1e-5
    assert abs(reference.translation.z - 0.2) < 1e-5
    base = bone.bone.matrix_local @ evaluated_bone.matrix_basis
    assert not close(evaluated_bone.matrix, base)
    pivot = base.translation
    expected = Matrix.Translation(pivot) @ Matrix.Rotation(math.radians(20), 4, "Z") @ Matrix.Translation(-pivot) @ base
    pose(bpy, {"armature": rig.name, "pose": [{"bone": bone.name, "axis": "Z", "degrees": 20}]})
    assert rig.animation_data is None and constraint.mute
    for frame in (4, 7):
        scene.frame_set(frame)
        evaluated = rig.evaluated_get(bpy.context.evaluated_depsgraph_get())
        assert close(evaluated.matrix_basis, reference)
        assert close(evaluated.pose.bones[bone.name].matrix, expected)
""", encoding="utf-8")
    result = subprocess.run([os.environ["MODEL3D_BLENDER"], "-b", "--factory-startup", "--disable-autoexec",
                             "--python-exit-code", "1", "--python", str(test_script), "--", mtoon_preview.__file__],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr


STAND_IN = r'''
import json, sys
from itertools import product
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def arg(name):
    return Path(args[args.index(name) + 1])
settings = json.loads(arg("--settings").read_text(encoding="utf-8"))
images = []
out = arg("--out-dir")
out.mkdir(parents=True)
for i, (pose, light, view) in enumerate(product(settings["sets"], settings["lights"], settings["views"]), 1):
    file = f"{i:04d}.png"
    (out / file).write_bytes(b"preview fixture")
    images.append({"file": file, "set": pose["name"], "light": light["name"], "view": view["name"], "size": view["size"]})
arg("--report").write_text(json.dumps({"schema_version": "model3d-mtoon-preview/0.1", "images": images, "materials": {"Sample": {}},
                                     "argv": sys.argv[1:], "settings": settings}), encoding="utf-8")
'''


def test_recipe_records_files_and_hashes_without_editing_input(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    blend = tmp_path / "fixture.blend"
    blend.write_bytes(b"synthetic scene")
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "preview", "inputs": {"scene": {"path": blend.name}},
              "steps": [{"operation": "render.mtoon_preview", "id": "preview", "inputs": {"blend": "$inputs.scene"},
                         "parameters": {**_settings(), "blender": str(blender), "sets": [{"name": "neutral"}, {"name": "lift"}]},
                         "outputs": {"report": "preview.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["preview"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert [image["file"] for image in report["images"]] == ["0001.png", "0002.png"]
    for image in report["images"]:
        assert (Path(report["directory"]) / image["file"]).is_file()
        assert image["sha256"] == hashlib.sha256(b"preview fixture").hexdigest()
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "mtoon_preview.py"
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]
    assert step["observations"][0]["images"] == 2
    assert blend.read_bytes() == b"synthetic scene"
