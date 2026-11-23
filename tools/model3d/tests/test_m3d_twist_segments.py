"""Segment names, weights, and cumulative roll on a synthetic arm."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from model3d import executor
from model3d.blender_adapter.twist_bones import segmented_shares
from model3d.io import ToolError, atomic_json
from model3d.ops.model_ops import twist_bones_settings
from test_m3d_parented_roll import BLENDER_CHECK


def test_equal_segments_match_the_piecewise_weight_formula():
    t = np.linspace(0, 1, 1001)
    fractions = (1 / 3, 2 / 3)
    weights = np.stack(tuple(segmented_shares(t, fractions)))
    assert weights == pytest.approx(np.maximum(0, 1 - np.abs(t[None, :] * 3 - np.arange(4)[:, None])))
    assert (np.count_nonzero(weights, axis=0) <= 2).all()
    assert weights.sum(axis=0) == pytest.approx(np.ones(t.size))


def test_weight_fractions_follow_each_interval():
    weights = np.array(tuple(segmented_shares(np.array([0, .1, .2, .45, .7, .85, 1]), (.2, .7))))
    assert weights.T == pytest.approx(np.array([
        [1, 0, 0, 0], [.5, .5, 0, 0], [0, 1, 0, 0], [0, .5, .5, 0],
        [0, 0, 1, 0], [0, 0, .5, .5], [0, 0, 0, 1],
    ]))


@pytest.mark.parametrize("segments", [None, False, 1, 2.5, [], [0.0], [1], [.3, .3], [.8, .2], [float("nan")]])
def test_segments_rejects_invalid_knots(segments):
    with pytest.raises(ToolError, match=r"bones\[0\].segments"):
        twist_bones_settings({"bones": [{"bone": "upper", "aim": "forearm", "segments": segments}]})


@pytest.mark.parametrize("influence", [None, False, -.01, 1.01, float("inf")])
def test_roll_rejects_invalid_influence(influence):
    with pytest.raises(ToolError, match="influence"):
        twist_bones_settings({"bones": [{"bone": "forearm", "aim": "tip", "segments": 3,
                                        "roll": {"name": "tip_roll", "from": "tip", "influence": influence}}]})


def test_segment_names_are_checked_across_recipes_before_blender():
    bones = [
        {"bone": "forearm", "aim": "tip", "name": "helper", "segments": 2, "roll": {"name": "end", "from": "tip"}},
        {"bone": "upper", "aim": "forearm", "name": "helper_segment_1_carrier"},
    ]
    with pytest.raises(ToolError, match="重なっ"):
        twist_bones_settings({"bones": bones})


BLENDER_SEGMENT_CHECK = r'''
import json
import sys

import bpy
from mathutils import Quaternion

def values(path, mute):
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    rig = bpy.data.objects["Fixture"]
    for pb in rig.pose.bones:
        for c in pb.constraints:
            c.mute = mute
    for name, angle in [("upper", .63), ("forearm", .42), ("tip", -.7)]:
        bone = rig.pose.bones[name]
        bone.rotation_mode = "QUATERNION"
        bone.rotation_quaternion = Quaternion((0, 1, 0), angle)
    bpy.context.view_layer.update()
    mesh = bpy.data.objects["Tube"].evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    return [v.co.copy() for v in mesh.vertices]

source, aim, parented, out = sys.argv[sys.argv.index("--") + 1:]
plain = values(source, True)
fallback = values(parented, True)
aim_on = values(aim, False)
safe_on = values(parented, False)
rig = bpy.data.objects["Fixture"]
arms = rig.pose.bones
for index, fraction in enumerate((1/3, 2/3), 1):
    helper = arms[f"upper_helper_segment_{index}"]
    q = (helper.matrix @ helper.bone.matrix_local.inverted()).to_quaternion()
    expected = Quaternion((1, 0, 0), .63 * fraction)
    assert abs(q.dot(expected)) > .999999
    assert abs(helper.constraints[0].influence - (1 - fraction)) < 1e-6
for index, fraction in enumerate((.19, .52, .77), 1):
    helper = arms[f"forearm_helper_segment_{index}"]
    q = (helper.matrix @ helper.bone.matrix_local.inverted()).to_quaternion()
    expected = Quaternion((1, 0, 0), .63 + (.42 - .7 * .63) * fraction)
    assert abs(q.dot(expected)) > .999999
    basis = helper.parent
    carrier = basis.parent
    assert basis.name == helper.name + "_basis" and basis.bone.use_deform and not basis.constraints
    assert carrier.name == helper.name + "_carrier" and carrier.bone.use_deform
    assert abs(carrier.constraints[0].influence - (1 - fraction)) < 1e-6
    assert abs(helper.constraints[0].influence - fraction * .63) < 1e-6
obj = bpy.data.objects["Tube"]
for vertex in obj.data.vertices:
    positive = [item.weight for item in vertex.groups if item.weight > 0]
    assert 1 <= len(positive) <= 2 and abs(sum(positive) - 1) < 1e-6
    assert all(obj.vertex_groups[item.group].name != "forearm" for item in vertex.groups)
with open(out, "w", encoding="utf-8") as stream:
    json.dump({"muted_gap": max((a-b).length for a, b in zip(plain, fallback)),
               "enabled_gap": max((a-b).length for a, b in zip(aim_on, safe_on))}, stream)
'''


def test_segment_constraints_match_on_a_synthetic_tube(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("Blender is needed for the synthetic mesh evaluation")

    def run_blender(name, script, *args):
        path = tmp_path / name
        path.write_text(script, encoding="utf-8")
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
             "--python", str(path), "--", *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    source = tmp_path / "arm.blend"
    run_blender("make.py", BLENDER_CHECK, "make", source)
    bones = [
        {"bone": "upper", "aim": "forearm", "name": "upper_helper", "segments": 3},
        {"bone": "forearm", "aim": "tip", "name": "forearm_helper", "segments": [.19, .52, .77],
         "roll": {"name": "tip_helper", "from": "tip", "influence": .63}},
    ]
    results = {}
    for mode in ("aim", "parented_roll"):
        recipe = tmp_path / f"{mode}.json"
        atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": mode,
                             "inputs": {"arm": {"path": source.name}},
                             "steps": [{"id": "twist", "operation": "model.twist_bones", "inputs": {"blend": "$inputs.arm"},
                                        "parameters": {"blender": blender, "bones": bones, "rigid_max_verts": 0,
                                                       "constraint_mode": mode},
                                        "outputs": {"blend": "out.blend", "report": "twist.json"}}]})
        result = executor.run_recipe(recipe, tmp_path / mode)
        assert result["status"] == "succeeded", result
        artifacts = result["steps"]["twist"]["artifacts"]
        results[mode] = artifacts["blend"]
        report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
        assert [node["position"] for node in report["chains"]["forearm"]] == [0, .19, .52, .77, 1]
        assert report["chains"]["forearm"][-1]["name"] == "tip_helper"
    comparison = tmp_path / "compare.json"
    run_blender("measure.py", BLENDER_SEGMENT_CHECK, source, results["aim"], results["parented_roll"], comparison)
    report = json.loads(comparison.read_text(encoding="utf-8"))
    assert report["muted_gap"] < 1e-5, report
    assert report["enabled_gap"] < 1e-5, report
