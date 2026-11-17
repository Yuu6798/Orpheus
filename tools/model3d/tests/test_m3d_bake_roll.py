"""手の回転の一部を写す骨の回転を焼く（motion.bake_roll）: 角度の戻し方と台本の式・拘束の形の見分け方・差の要約（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import bake_roll as br
from model3d.io import ToolError, atomic_json


def test_angles_wrap_like_the_script():
    angles = np.array([0.0, 3.0, 3.5, -3.5, 2 * math.pi, -7.0, math.pi])
    assert br.wrapped(angles).tobytes() == ((angles + np.pi) % (2 * np.pi) - np.pi).tobytes()
    assert br.wrapped([3.5])[0] == pytest.approx(3.5 - 2 * math.pi)
    assert np.all(np.abs(br.wrapped(angles)) <= math.pi)


def test_only_the_roll_form_of_copy_rotation_is_baked():
    good = NS(use_x=False, use_y=True, use_z=False, mix_mode="ADD", target_space="LOCAL", owner_space="LOCAL", subtarget="hand.L")
    assert br.roll_problem(good) == ""
    assert "Y だけ" in br.roll_problem(NS(**{**vars(good), "use_x": True}))
    assert "Add" in br.roll_problem(NS(**{**vars(good), "mix_mode": "REPLACE"}))
    assert "ローカル" in br.roll_problem(NS(**{**vars(good), "owner_space": "WORLD"}))
    assert "subtarget" in br.roll_problem(NS(**{**vars(good), "subtarget": ""}))


def test_difference_summary_wraps_around_the_half_turn():
    before = np.radians([10.0, 179.0, -179.0, 0.0])
    after = np.radians([10.0, -179.0, 179.0, 30.0])
    summary = br.off_by_deg(before, after)
    assert summary["max"] == pytest.approx(30.0) and summary["median"] == pytest.approx(2.0)   # 179° と −179° の差は 2°


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out-blend"))
names = settings["bones"] or ["lower_arm_twist.L", "lower_arm_twist.R"]
report = {"schema_version": "model3d-bake-roll/0.1", "armature": "Rig", "frames": [1, 3],
          "bones": {n: {"from": "hand." + n[-1], "frames": 3, "largest_twist_deg": 44.0,
                        "constraint_was_off_by_deg": {"median": 3.0, "p95": 15.0, "max": 55.0}} for n in names},
          "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def run_recipe(tmp_path, parameters):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "bake",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "bake", "operation": "motion.bake_roll", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": blender, **parameters},
                                                      "outputs": {"blend": "baked.blend", "report": "bake.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_bake_roll_step_passes_the_bones(tmp_path):
    result = run_recipe(tmp_path, {"bones": ["lower_arm_twist.L"]})
    assert result["status"] == "succeeded", result
    step = result["steps"]["bake"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"] == {"bones": ["lower_arm_twist.L"], "armature": None}
    assert step["observations"][0]["bones"]["lower_arm_twist.L"]["constraint_was_off_by_deg"]["max"] == 55.0


def test_bake_roll_without_bones_lets_the_script_find_them(tmp_path):
    result = run_recipe(tmp_path, {})
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["bake"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["bones"] == [] and set(report["bones"]) == {"lower_arm_twist.L", "lower_arm_twist.R"}


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"bones": []}, ("blend", "report"), "bones"),
    ({"bones": ["a", "a"]}, ("blend", "report"), "bones"),
    ({"bones": ["a"], "euler_order": "ZYX"}, ("blend", "report"), "知らない設定"),
    ({"armature": ""}, ("blend", "report"), "armature"),
    ({}, ("blend",), "report"),
])
def test_bake_roll_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("motion.bake_roll").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                         {name: tmp_path / f"o.{name}" for name in outputs}, Context(tmp_path))
