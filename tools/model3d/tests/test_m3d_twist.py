"""motion.twist_split: Blender の中で動く台本の引数と、工程の受け渡し（代役の Blender）を確かめる。
ねじれの分け方そのものは本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import twist_split
from model3d.io import ToolError, atomic_json

# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"twist split")
side = {"elbow_twist_median_deg": {"before": 12.2, "after": 8.8}, "wrist_twist_median_deg": {"before": 12.1, "after": 8.5},
        "half_sum_max_deg": 27.9}
json.dump({"schema_version": "model3d-twist-split/0.1", "frames": [1, 3], "bone_prefix": value("--bone-prefix"),
           "sides": {"Left": side, "Right": side},
           "unchanged": {"hand_turn_deg": 0.0, "forearm_direction_deg": 0.0, "fingertip_mm": FINGERTIP_MM},
           "argv": args, "script": script}, open(value("--report"), "w"))
'''


def blender_stand_in(directory, fingertip_mm=0.00041234):
    return stand_in_executable(directory, "blender", STAND_IN.replace("FINGERTIP_MM", repr(fingertip_mm)))


def test_parse_args_defaults():
    args = twist_split.parse_args(["--blend", "in.blend", "--out-blend", "out.blend", "--report", "r.json"])
    assert args.bone_prefix == "mixamorig:" and args.frames == ""


def test_frame_range_includes_the_last_frame_and_defaults_to_the_scene():
    assert twist_split.frame_range("", 1, 4) == [1, 2, 3, 4]
    assert twist_split.frame_range("3:5", 1, 99) == [3, 4, 5]
    assert twist_split.frame_range("7:7", 1, 99) == [7]
    for spec in ("5:3", "5", "a:b"):
        with pytest.raises(SystemExit):
            twist_split.frame_range(spec, 1, 99)


def test_wrapped_brings_angles_back_between_minus_and_plus_half_a_turn():
    np.testing.assert_allclose(twist_split.wrapped([1.5 * math.pi, -1.5 * math.pi, 0.5, 2 * math.pi]),
                               [-0.5 * math.pi, 0.5 * math.pi, 0.5, 0.0], atol=1e-12)


def make_recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "dance.blend").write_bytes(b"retargeted")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "twist",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "twist", "operation": "motion.twist_split",
                                                      "inputs": {"blend": "$inputs.dance"}, "parameters": parameters,
                                                      "outputs": outputs or {"blend": "twisted.blend", "report": "twist.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_passes_the_blend_and_options_to_blender(tmp_path):
    from model3d import executor
    recipe = make_recipe(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "frames": " 1 : 3 ", "bone_prefix": "rig:"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["twist"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    argv = report["argv"]
    assert Path(report["script"]).name == "twist_split.py"
    assert Path(argv[argv.index("--blend") + 1]).name == "dance.blend"
    assert argv[argv.index("--frames") + 1] == "1:3" and argv[argv.index("--bone-prefix") + 1] == "rig:"
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"twist split"
    observation = step["observations"][0]
    assert observation["kind"] == "twist_split" and observation["blender_version"] == "4.5.15"
    assert observation["frames"] == [1, 3] and observation["sides"]["Left"]["half_sum_max_deg"] == 27.9
    assert observation["unchanged"]["fingertip_mm"] == 0.0004   # 報告は丸めない、観察は丸める
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["twist"]["reused_from"] is None   # 毎回実行する


def test_without_frames_the_script_uses_the_scene_range(tmp_path):
    from model3d import executor
    result = executor.run_recipe(make_recipe(tmp_path, {"blender": str(blender_stand_in(tmp_path))}), tmp_path / "runs")
    argv = json.loads(Path(result["steps"]["twist"]["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert "--frames" not in argv and argv[argv.index("--bone-prefix") + 1] == "mixamorig:"


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"retargeted")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("motion.twist_split").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))


def test_the_operation_stops_when_the_hand_moved(tmp_path):
    with pytest.raises(ToolError, match="fingertip_mm"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, fingertip_mm=0.25))})


def test_the_tolerance_is_compared_before_rounding(tmp_path):
    with pytest.raises(ToolError, match="変わりました"):   # 0.10004 は 4 桁に丸めると 0.1 で通ってしまう
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, fingertip_mm=0.10004))})


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "Blender"),
    ({"frames": "a:b"}, None, "frames"),
    ({"frames": "5"}, None, "frames"),
    ({"frames": "5:3"}, None, "frames"),
    ({"frames": "1:3:2"}, None, "frames"),
    ({"frames": 3}, None, "frames"),
    ({"bone_prefix": 3}, None, "bone_prefix"),
    ({}, {"blend": "o.blend"}, "report"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    if message != "Blender":
        parameters = {"blender": str(blender_stand_in(tmp_path)), **parameters}
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters, outputs)
