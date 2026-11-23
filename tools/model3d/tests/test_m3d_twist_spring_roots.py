"""Moves synthetic spring roots while keeping rest poses and descendants."""

import json
import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from model3d import executor
from model3d.blender_adapter.twist_bones import check_spring_centers, nearest_chain_index
from model3d.io import ToolError, atomic_json
from model3d.ops.model_ops import twist_bones_settings
from test_m3d_parented_roll import BLENDER_CHECK


@pytest.mark.parametrize("rule", [
    None, {}, {"names": []}, {"names": ["root", "root"]}, {"prefixes": [""]},
    {"names": ["root"], "prefixes": ["stem"]}, {"names": ["root"], "index": False},
    {"names": ["root"], "index": -1}, {"names": ["root"], "index": 4},
])
def test_spring_roots_rejects_invalid_selection_or_index(rule):
    entry = {"bone": "forearm", "aim": "tip", "segments": 3, "spring_roots": rule}
    with pytest.raises(ToolError, match=r"bones\[0\].spring_roots"):
        twist_bones_settings({"bones": [entry]})


def test_equal_segment_nearest_index_rounds_like_the_prototype_at_midpoint():
    points = [(str(index), index / 4) for index in range(5)]
    assert [nearest_chain_index({"segments": 4}, points, t) for t in [-.1, .125, .375, .625, .875, 1.1]] == [
        0, 0, 2, 2, 4, 4,
    ]


def test_aim_keeps_a_spring_center_above_its_joint():
    root = SimpleNamespace(name="spring_root")
    child = SimpleNamespace(name="spring_end", parent_recursive=[root])
    spring = SimpleNamespace(vrm_name="Fabric", center=SimpleNamespace(bone_name="forearm"),
                             joints=[SimpleNamespace(node=SimpleNamespace(bone_name=child.name))])
    extension = SimpleNamespace(spec_version="1.0", spring_bone1=SimpleNamespace(springs=[spring]))
    armature = SimpleNamespace(data=SimpleNamespace(vrm_addon_extension=extension, bones={child.name: child}))
    recipes = [{"bone": "forearm", "roll": {"name": "hand_helper"}}]
    move = {"bone": root.name, "from": "forearm", "to": "forearm_swing", "changed": True}
    with pytest.raises(SystemExit, match="中心 forearm"):
        check_spring_centers(armature, [move], recipes)
    check_spring_centers(armature, [{**move, "to": "hand_helper"}], recipes)


BLENDER_SPRING_CHECK = BLENDER_CHECK.split("args = sys.argv", maxsplit=1)[0] + r'''
def add_springs(path):
    make_arm(path)
    rig = bpy.data.objects["Fixture"]
    bpy.context.view_layer.objects.active = rig
    bpy.ops.object.mode_set(mode="EDIT")
    edit = rig.data.edit_bones
    bones = [
        ("fabric_root", "upper", (1, 0, 0), True),
        ("chain_near", "forearm", (1.08, .07, 0), False),
        ("chain_far", "forearm", (1.68, .07, 0), False),
        ("chain_end", "chain_near", (1.08, .09, -.15), False),
        ("other_root", "forearm", (1.26, -.08, 0), False),
    ]
    for name, parent, head, connected in bones:
        bone = edit.new(name)
        bone.head = head
        bone.tail = (head[0], head[1] + .02, head[2] - .15)
        bone.roll = -.37
        bone.parent = edit[parent]
        bone.use_connect = connected
    bpy.ops.object.mode_set(mode="OBJECT")
    roots = ["fabric_root", "chain_near", "chain_far"]
    rig.data["fixture_springs"] = roots[0]
    for number, name in enumerate(roots):
        rig.data.bones[name]["fixture_joint_id"] = number
    bpy.ops.wm.save_as_mainfile(filepath=path)

def snapshot(path):
    bpy.ops.wm.open_mainfile(filepath=path, use_scripts=False)
    rig = bpy.data.objects["Fixture"]
    return {bone.name: bone.matrix_local.copy() for bone in rig.data.bones}

def check(source, result):
    original = snapshot(source)
    updated = snapshot(result)
    rig = bpy.data.objects["Fixture"]
    bones = rig.data.bones
    assert rig.data["fixture_springs"] == "fabric_root"
    for number, name in enumerate(["fabric_root", "chain_near", "chain_far"]):
        assert bones[name]["fixture_joint_id"] == number
    assert bones["fabric_root"].parent.name == "upper_swing" and not bones["fabric_root"].use_connect
    assert bones["chain_near"].parent.name == "forearm_swing"
    assert bones["chain_far"].parent.name == "forearm_swing_segment_3"
    assert bones["chain_end"].parent.name == "chain_near"
    assert bones["other_root"].parent.name == "forearm"
    assert max(abs(before[r][c] - updated[name][r][c])
               for name, before in original.items() for r in range(4) for c in range(4)) < 1e-6
    forearm = rig.pose.bones["forearm"]
    forearm.rotation_mode = "QUATERNION"
    forearm.rotation_quaternion = Quaternion((0, 1, 0), .9)
    bpy.context.view_layer.update()
    near = rig.pose.bones["chain_near"]
    far = rig.pose.bones["chain_far"]
    assert (near.head - bones["chain_near"].head_local).length < 1e-6
    assert (far.head - bones["chain_far"].head_local).length > .03

action, *args = sys.argv[sys.argv.index("--") + 1:]
if action == "make":
    add_springs(*args)
else:
    check(*args)
'''


def test_spring_root_reparent_preserves_rest_pose_and_subtrees(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("Blender is needed for the synthetic spring hierarchy")
    script = tmp_path / "spring_fixture.py"
    script.write_text(BLENDER_SPRING_CHECK, encoding="utf-8")

    def run_blender(*args):
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
             "--python", str(script), "--", *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    source = tmp_path / "arm.blend"
    run_blender("make", source)
    source_data = source.read_bytes()
    bones = [
        {"bone": "upper", "spring_roots": {"names": ["fabric_root"], "index": 0}},
        {"bone": "forearm", "segments": [.17, .32, .83],
         "roll": {"from": "tip", "name": "tip_helper"}, "spring_roots": {"prefixes": ["chain_"]}},
    ]
    step = {"id": "twist", "operation": "model.twist_bones", "inputs": {"blend": "$inputs.arm"},
            "parameters": {"blender": blender, "bones": bones, "constraint_mode": "parented_roll", "rigid_max_verts": 0},
            "outputs": {"blend": "out.blend", "report": "twist.json"}}
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "spring-root",
                         "inputs": {"arm": {"path": source.name}}, "steps": [step]})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    artifacts = result["steps"]["twist"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    changes = {row["bone"]: (row["from"], row["index"], row["to"]) for row in report["reparented_spring_roots"]}
    assert changes == {"fabric_root": ("upper", 0, "upper_swing"),
                       "chain_near": ("forearm", 0, "forearm_swing"),
                       "chain_far": ("forearm", 3, "forearm_swing_segment_3")}
    assert source_data == source.read_bytes()
    run_blender("check", source, artifacts["blend"])
