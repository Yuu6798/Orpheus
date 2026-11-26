"""袖の区間と受け渡しを確かめ、本物の Blender があれば合成の鎖を焼く。"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d import executor
from model3d.blender_adapter.vrm_drape_sleeves import DEFAULTS, proximity, smooth_weights
from model3d.io import ToolError, atomic_json
from model3d.ops.vrm_ops import drape_settings


PARAMETERS = {"chest": "Chest", "head": "Head", "left": [["Sleeve root", "Sleeve end"]], "back_axis": "+Y"}


def recipe(tmp_path, blender, *, expected="succeeded", **parameters):
    path = tmp_path / "recipe.json"
    atomic_json(path, {"schema_version": "model3d-recipe/0.1", "name": "sleeve",
                       "inputs": {"input": {"path": "input.blend"}},
                       "steps": [{"id": "drape", "operation": "vrm.drape_sleeves",
                                  "inputs": {"blend": "$inputs.input"},
                                  "parameters": {**PARAMETERS, "blender": str(blender), **parameters},
                                  "outputs": {"blend": "draped.blend", "report": "drape.json"}}]})
    run = executor.run_recipe(path, tmp_path / "runs")
    assert run["status"] == expected, run
    return run["steps"]["drape"].get("artifacts", {})


def test_drape_weights_raise_near_head_or_above_chest():
    assert proximity(-0.4, 0.8, 1, DEFAULTS) == 0
    assert proximity(0.2, 0.8, 0, DEFAULTS) == pytest.approx(0.55)
    assert proximity(-0.4, 0.1, 1, DEFAULTS) == 1
    assert smooth_weights([0, 0, 1, 0, 0], 0, 1) == [0, 1, 1, 1, 0]
    weighted = smooth_weights([0, 0, 1, 0, 0], 1, 0)
    assert weighted == pytest.approx(weighted[::-1])
    assert 0 < weighted[0] < weighted[1] < weighted[2] < 1
    assert smooth_weights([0.7], 5, 3) == pytest.approx([0.7])


@pytest.mark.parametrize("parameters", [{"left": [[]]}, {"right": [["Sleeve root"]]}, {"left": []},
                                        {"head": "Chest"}, {"left_axis": "-Y"},
                                        {"frames": [True, 5]}, {"strength": 2}, {"height_m": 0}])
def test_invalid_drape_settings(parameters):
    with pytest.raises(ToolError):
        drape_settings({**PARAMETERS, **parameters}, "")


def test_drape_recipe_calls_blender_and_emits_a_new_blend(tmp_path):
    (tmp_path / "input.blend").write_bytes(b"base")
    blender = stand_in_executable(tmp_path, "blender", """
import json, sys
from pathlib import Path

if "--version" in sys.argv:
    print("Blender 5.2.4")
    raise SystemExit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def path(flag):
    return Path(args[args.index(flag) + 1])
settings = json.loads(path("--settings").read_text(encoding="utf-8"))
path("--out-blend").write_bytes(path("--blend").read_bytes() + b"-draped")
report = {"armature": "Avatar", "frames": [1, 3], "frames_modified": 2, "keys": 4, "bake_check_deg": 0,
          "settings": settings, "script": Path(sys.argv[sys.argv.index("--python") + 1]).name}
path("--report").write_text(json.dumps(report), encoding="utf-8")
""")
    artifacts = recipe(tmp_path, blender, left_axis="-X", frames=[1, 3])
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["script"] == "vrm_drape_sleeves.py"
    assert report["settings"]["left"] == PARAMETERS["left"]
    assert report["settings"]["left_axis"] == "-X"
    assert report["settings"]["frames"] == [1, 3]
    assert Path(artifacts["blend"]).read_bytes() == b"base-draped"
    assert (tmp_path / "input.blend").read_bytes() == b"base"


def run_blender(blender, path, script):
    path.write_text(script, encoding="utf-8")
    result = subprocess.run([str(blender), "--background", "--factory-startup", "--python-exit-code", "1",
                             "--python", str(path)], capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr


def test_blender_drapes_a_raised_chain_preserves_low_keys_and_rejects_sparse_keys(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("実 Blender がありません（MODEL3D_TEST_BLENDER）")
    source = tmp_path / "input.blend"
    sparse = tmp_path / "sparse.blend"
    run_blender(blender, tmp_path / "make.py", f"""
import bpy
from mathutils import Quaternion

data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", data)
bpy.context.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name, head, tail, parent in [
    ("Chest", (0, 0, 1.0), (0, 0, 1.3), None),
    ("Head", (0, 0, 1.65), (0, 0, 1.8), "Chest"),
    ("Carrier", (0.3, 0, 0.0), (0.3, 0, 0.1), None),
    ("Sleeve root", (0.3, 0, 0.1), (0.3, 0, 0.7), "Carrier"),
    ("Sleeve end", (0.3, 0, 0.7), (0.3, 0, 1.0), "Sleeve root")
]:
    bone = data.edit_bones.new(name)
    bone.head = head
    bone.tail = tail
    if parent:
        bone.parent = data.edit_bones[parent]
bpy.ops.object.mode_set(mode="OBJECT")
scene = bpy.context.scene
scene.frame_start, scene.frame_end = 1, 4
for frame, height in enumerate((0, 1, 1, 0), 1):
    rig.pose.bones["Carrier"].location.y = height
    rig.pose.bones["Carrier"].keyframe_insert("location", frame=frame)
    for name in ("Sleeve root", "Sleeve end"):
        bone = rig.pose.bones[name]
        bone.rotation_mode = "QUATERNION"
        bone.rotation_quaternion = Quaternion()
        bone.keyframe_insert("rotation_quaternion", frame=frame)
scene.frame_set(1)
bpy.ops.wm.save_as_mainfile(filepath={str(source)!r})
rig.pose.bones["Sleeve root"].keyframe_delete("rotation_quaternion", frame=2, index=0)
bpy.ops.wm.save_as_mainfile(filepath={str(sparse)!r})
""")
    artifacts = recipe(tmp_path, blender, back_axis="-Z", smooth_s=0, lead_s=0)
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["frames_modified"] == 2 and report["keys"] == 4
    assert report["bake_check_deg"] < 0.001
    run_blender(blender, tmp_path / "check.py", f"""
import bpy
bpy.ops.wm.open_mainfile(filepath={artifacts["blend"]!r})
scene = bpy.context.scene
rig = bpy.data.objects["Rig"]
for frame, raised in [(1, False), (2, True), (3, True), (4, False)]:
    scene.frame_set(frame)
    carrier = rig.pose.bones["Carrier"]
    assert abs(carrier.location.y - int(raised)) < 1e-6
    for name in ("Sleeve root", "Sleeve end"):
        bone = rig.pose.bones[name]
        if raised:
            assert (bone.tail - bone.head).normalized().z < 0.1, (frame, name, bone.head, bone.tail)
        else:
            assert abs(bone.rotation_quaternion.w - 1) < 1e-6
""")
    source.write_bytes(sparse.read_bytes())
    assert recipe(tmp_path, blender, expected="failed", back_axis="-Z", frames=[2, 3]) == {}
    logs = [log.read_text(encoding="utf-8") for log in (tmp_path / "runs").rglob("blender.log")]
    assert any("袖の四元数の 4 成分をシーンの全コマに焼いてください" in log for log in logs)
