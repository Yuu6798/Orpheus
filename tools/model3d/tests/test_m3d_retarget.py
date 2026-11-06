"""motion.retarget: Blender の中で動く台本の引数と、工程の受け渡し（代役の Blender）を確かめる。
骨の向きの計算そのものは本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import retarget
from model3d.io import ToolError, atomic_json

# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数を報告に残す
STAND_IN = r'''
import json, sys
import numpy as np
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"retargeted")
with open(value("--pose"), "wb") as stream:
    np.savez(stream, names=np.array(["mixamorig:Hips"]), pose=np.zeros((3, 1, 4, 4)))
json.dump({"schema_version": "model3d-retarget/0.1", "info": {"frames": 3, "scale": 1.1, "leg_scale": 1.2, "pitch0_deg": 35.0,
           "resolve": {"frames_fixed": 1}}, "check": {"summary": {"frames": 3}, "rows": []}, "argv": args, "script": script},
          open(value("--report"), "w"))
'''


def test_frame_list_reads_ranges_steps_and_lists():
    assert retarget.frame_list("", 4) == [0, 1, 2, 3]
    assert retarget.frame_list("1:9:3", 20) == [1, 4, 7]
    assert retarget.frame_list("5:99", 8) == [5, 6, 7]
    assert retarget.frame_list("0,5,9", 20) == [0, 5, 9]


def test_parse_args_defaults():
    args = retarget.parse_args(["--points", "p.npz", "--character", "c.blend", "--out-blend", "o.blend", "--pose", "pose.npz",
                                "--report", "r.json"])
    assert args.bone_prefix == "mixamorig:" and args.foot_yaw == "toes" and args.pitch0 is None and args.fps == 30
    assert not (args.resolve or args.smooth_arms or args.check)


def make_recipe(tmp_path, parameters, outputs=None, extra_inputs=None):
    np.savez(tmp_path / "points.npz", points=np.zeros((3, 70, 3)), stance=np.zeros((3, 2)))
    (tmp_path / "rig.blend").write_bytes(b"character")
    np.savez(tmp_path / "skip.npz", hair=np.zeros(4, bool))
    inputs = {"points": {"path": "points.npz"}, "rig": {"path": "rig.blend"}, "skip": {"path": "skip.npz"}}
    step_inputs = {"points": "$inputs.points", "character": "$inputs.rig", **(extra_inputs or {})}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "retarget", "inputs": inputs,
                                           "steps": [{"id": "retarget", "operation": "motion.retarget", "inputs": step_inputs,
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"blend": "dance.blend", "pose": "pose.npz",
                                                                             "report": "retarget.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_passes_points_character_and_options_to_blender(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    recipe = make_recipe(tmp_path, {"blender": str(blender), "pitch0_deg": 35, "foot_yaw": "heel", "resolve": True,
                                    "smooth_arms": True, "check": True, "frames": "0:3"},
                         extra_inputs={"skip_vertices": "$inputs.skip"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["retarget"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    argv = report["argv"]
    assert Path(report["script"]).name == "retarget.py"
    for flag in ("--resolve", "--smooth-arms", "--check"):
        assert flag in argv
    assert argv[argv.index("--pitch0") + 1] == "35.0" and argv[argv.index("--foot-yaw") + 1] == "heel"
    assert argv[argv.index("--frames") + 1] == "0:3" and argv[argv.index("--bone-prefix") + 1] == "mixamorig:"
    assert Path(argv[argv.index("--skip-vertices") + 1]).name == "skip.npz"
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"retargeted"
    observation = step["observations"][0]
    assert observation["kind"] == "retarget" and observation["blender_version"] == "4.5.15"
    assert observation["resolved_frames"] == 1 and observation["check_summary"] == {"frames": 3}
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["retarget"]["reused_from"] is None   # 毎回実行する


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "Blender"),
    ({"foot_yaw": "ankle"}, None, "foot_yaw"),
    ({"frames": "a:b"}, None, "frames"),
    ({"resolve": "yes"}, None, "resolve"),
    ({}, {"blend": "dance.blend"}, "pose"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    np.savez(tmp_path / "points.npz", points=np.zeros((3, 70, 3)))
    (tmp_path / "rig.blend").write_bytes(b"character")
    if message != "Blender":
        parameters = {"blender": str(blender), **parameters}
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "pose": "p.npz", "report": "r.json"}).items()}

    class Context:
        tools, step_dir = {}, tmp_path

    with pytest.raises(ToolError, match=message):
        get("motion.retarget").function({"points": tmp_path / "points.npz", "character": tmp_path / "rig.blend"}, parameters,
                                        declared, Context())
