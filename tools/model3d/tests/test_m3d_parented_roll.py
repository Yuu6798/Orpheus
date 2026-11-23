"""Verifies the opt-in twist hierarchy with a synthetic arm and a tube."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from model3d import executor
from model3d.io import ToolError, atomic_json
from model3d.ops.model_ops import twist_bones_settings


def test_parented_roll_settings_preserve_the_legacy_recipe():
    legacy = {"bones": [{"bone": "upper", "aim": "forearm"}]}
    assert twist_bones_settings({**legacy, "constraint_mode": "aim"}) == twist_bones_settings(legacy)
    result = twist_bones_settings({"bones": [{"bone": "upper"}], "constraint_mode": "parented_roll"})
    assert result["constraint_mode"] == "parented_roll"
    assert result["bones"] == [{"bone": "upper", "name": "upper_swing"}]
    with pytest.raises(ToolError, match="aim"):
        twist_bones_settings({"bones": [{"bone": "upper"}]})


@pytest.mark.parametrize("extra, message", [
    ({"constraint_mode": "unknown"}, "constraint_mode"),
    ({"bones": [{"bone": "upper", "name": "helper"}, {"bone": "forearm", "name": "helper_basis"}]}, "重なっ"),
    ({"bones": [{"bone": "upper", "name": "補" * 20}]}, "63 バイト"),
])
def test_parented_roll_stops_ambiguous_generated_names(extra, message):
    params = {"constraint_mode": "parented_roll", "bones": [{"bone": "upper"}]}
    with pytest.raises(ToolError, match=message):
        twist_bones_settings({**params, **extra})


BLENDER_CHECK = r'''
import json
import math
import sys

import bpy
from mathutils import Quaternion

def make_arm(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    data = bpy.data.armatures.new("Fixture")
    rig = bpy.data.objects.new("Fixture", data)
    bpy.context.scene.collection.objects.link(rig)
    bpy.context.view_layer.objects.active = rig
    rig.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    parent = None
    joints = [
        ("root", (0, 0, -1), (0, 0, 0), 0.31),
        ("upper", (0, 0, 0), (1, 0, 0), 0.2),
        ("forearm", (1, 0, 0), (2, 0, 0), -0.29),
        ("tip", (2, 0, 0), (2.4, 0, 0), -0.29),
    ]
    for name, head, tail, roll in joints:
        bone = data.edit_bones.new(name)
        bone.head, bone.tail, bone.roll = head, tail, roll
        bone.parent = parent
        parent = bone
    bpy.ops.object.mode_set(mode="OBJECT")
    mesh = bpy.data.meshes.new("Tube")
    rings, segments = 11, 8
    vertices = [(2 * r / (rings - 1), .16 * math.cos(2 * math.pi * j / segments),
                 .16 * math.sin(2 * math.pi * j / segments)) for r in range(rings) for j in range(segments)]
    faces = [(r * segments + j, r * segments + (j + 1) % segments,
              (r + 1) * segments + (j + 1) % segments, (r + 1) * segments + j)
             for r in range(rings - 1) for j in range(segments)]
    mesh.from_pydata(vertices, [], faces)
    obj = bpy.data.objects.new("Tube", mesh)
    bpy.context.scene.collection.objects.link(obj)
    for name, first, last in [("upper", 0, 5 * segments), ("forearm", 5 * segments, len(vertices))]:
        obj.vertex_groups.new(name=name).add(list(range(first, last)), 1.0, "REPLACE")
    mod = obj.modifiers.new("Skin", "ARMATURE")
    mod.object = rig
    bpy.ops.wm.save_as_mainfile(filepath=path)

def points(path, mute):
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    rig = bpy.data.objects["Fixture"]
    if mute:
        for pb in rig.pose.bones:
            for constraint in pb.constraints:
                constraint.mute = True
    rotations = [
        [("upper", (1, 0, 0), .9), ("forearm", (0, 0, 1), -.42)],
        [("upper", (0, 1, 0), .68), ("forearm", (0, 1, 0), -.83), ("tip", (0, 1, 0), .55)],
        [("upper", (0, 1, 0), -.71), ("forearm", (0, 1, 0), .66), ("tip", (0, 1, 0), -.78)],
    ]
    result = []
    for rotation in rotations:
        for pb in rig.pose.bones:
            pb.rotation_mode = "QUATERNION"
            pb.rotation_quaternion = (1, 0, 0, 0)
        for name, axis, angle in rotation:
            rig.pose.bones[name].rotation_quaternion = Quaternion(axis, angle)
        bpy.context.view_layer.update()
        mesh = bpy.data.objects["Tube"].evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        result.append([v.co.copy() for v in mesh.vertices])
    return result

def max_gap(before, after):
    return max((a - b).length for frame_a, frame_b in zip(before, after) for a, b in zip(frame_a, frame_b))

args = sys.argv[sys.argv.index("--") + 1:]
if args[0] == "make":
    make_arm(args[1])
else:
    original, legacy, updated, report = args[1:]
    ref = points(original, True)
    data = {"vertices": len(ref[0]), "poses": len(ref),
            "legacy_muted": max_gap(points(legacy, True), ref),
            "parented_muted": max_gap(points(updated, True), ref),
            "enabled_twist": max_gap(points(updated, False)[1:], points(legacy, False)[1:])}
    rig = bpy.data.objects["Fixture"]
    assert rig.pose.bones["upper_helper"].constraints[0].type == "DAMPED_TRACK"
    bpy.ops.wm.open_mainfile(filepath=updated, use_scripts=False)
    rig = bpy.data.objects["Fixture"]
    for source, helper in [("upper", "upper_helper"), ("forearm", "forearm_helper")]:
        bp, sp = rig.data.bones[helper], rig.data.bones[source]
        assert bp.parent.name == helper + "_basis" and bp.parent.parent == sp
        assert bp.parent.use_deform and bp.parent.name not in bpy.data.objects["Tube"].vertex_groups
        assert bp.matrix_local.to_3x3().col[1].dot(sp.matrix_local.to_3x3().col[1]) < -.99999
        sc = rig.pose.bones[helper].constraints[0]
        assert sc.type == "COPY_ROTATION" and sc.subtarget == source
        assert (sc.use_x, sc.use_y, sc.use_z, sc.influence) == (False, True, False, 1.0)
        assert (sc.mix_mode, sc.target_space, sc.owner_space) == ("ADD", "LOCAL", "LOCAL")
        src_local = sp.parent.matrix_local.inverted() @ sp.matrix_local
        dst_local = bp.parent.matrix_local.inverted() @ bp.matrix_local
        assert max(abs(src_local[i][j] - dst_local[i][j]) for i in range(3) for j in range(3)) < 1e-5
    with open(report, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
'''


def test_parented_roll_arm_follows_with_constraints_disabled(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("Blender is needed for the synthetic mesh evaluation")
    script = tmp_path / "arm_test.py"
    script.write_text(BLENDER_CHECK, encoding="utf-8")

    def evaluate(*args):
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
             "--python", str(script), "--", *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    source = tmp_path / "in.blend"
    evaluate("make", source)
    start = source.read_bytes()
    bones = [
        {"bone": "upper", "aim": "forearm", "name": "upper_helper"},
        {"bone": "forearm", "aim": "tip", "name": "forearm_helper", "roll": {"name": "tip_helper", "from": "tip"}},
    ]
    results = {}
    for mode in ("aim", "parented_roll"):
        path = tmp_path / f"{mode}.json"
        params = {"blender": blender, "bones": bones, "rigid_max_verts": 0}
        if mode != "aim":
            params["constraint_mode"] = mode
        atomic_json(path, {"schema_version": "model3d-recipe/0.1", "name": mode,
                           "inputs": {"arm": {"path": source.name}},
                           "steps": [{"id": "twist", "operation": "model.twist_bones", "inputs": {"blend": "$inputs.arm"},
                                      "parameters": params, "outputs": {"blend": "out.blend", "report": "twist.json"}}]})
        result = executor.run_recipe(path, tmp_path / mode)
        assert result["status"] == "succeeded", result
        results[mode] = result["steps"]["twist"]["artifacts"]["blend"]
    assert source.read_bytes() == start
    report = tmp_path / "compare.json"
    evaluate("compare", source, results["aim"], results["parented_roll"], report)
    measured = json.loads(report.read_text(encoding="utf-8"))
    assert measured["vertices"] == 88 and measured["poses"] == 3
    assert measured["legacy_muted"] > .1, measured
    assert measured["parented_muted"] < 1e-5, measured
    assert measured["enabled_twist"] < 1e-5, measured
