"""骨の軸の位置（motion.bone_offset。blender_adapter/bone_path.py の bone_offset と ops/event_ops.py）: 骨の項の逆になっていること、
悪い設定、工程の受け渡し（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import executor
from model3d.blender_adapter import bone_path
from model3d.io import ToolError, atomic_json
from model3d.ops import event_ops


def test_bone_offset_is_the_inverse_of_a_bone_term():
    head, tail = np.array([0.1, 0.2, 1.4]), np.array([0.1, 0.25, 1.6])
    angle = 0.3
    axes = [np.array([math.cos(angle), math.sin(angle), 0.0]), np.array([-math.sin(angle), math.cos(angle), 0.0]) * 2.0, np.array([0.0, 0.0, 1.0])]
    for at in (0, 0.4, 1):
        for unit in (1.0, 0.01):
            offset = bone_path.bone_offset(head / unit, tail / unit, axes, np.array([0.13, 0.5, 1.7]) / unit, at, unit)
            back = bone_path.bone_term(head / unit, tail / unit, lambda k: axes[k], at, offset, unit) * unit
            assert np.allclose(back, [0.13, 0.5, 1.7])


@pytest.mark.parametrize("targets, message", [
    (None, "targets"),
    ({"tip": {"bone": "Head", "object": "body"}}, "pick"),
    ({"tip": {"bone": "Head", "object": "body", "pick": "top"}}, "highest"),
    ({"tip": {"bone": "Head", "object": "body", "pick": "vertex"}}, "index"),
    ({"tip": {"bone": "Head", "object": "body", "pick": "highest", "box": [[0, 0, 1], [0, 0, 0]]}}, "下が上"),
    ({"tip": {"bone": "Head", "object": "body", "pick": "nearest"}}, "3 つの数"),
])
def test_bad_offset_settings_stop(targets, message):
    with pytest.raises(ToolError, match=message):
        event_ops.offset_settings({} if targets is None else {"targets": targets})


# 代役の Blender: 設定の targets を、決まった offset で書き、受け取った設定を notes に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
value = lambda flag: args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
points = {name: [{"bone": t["name"], "at": t["at"], "offset": [0.0, 0.2, 0.1]}] for name, t in settings["targets"].items()}
if "drop" in settings["targets"]:
    points = {}
json.dump({"schema_version": "model3d-bone-offset/0.1", "unit": "m", "frame": settings["frame"], "points": points,
           "notes": json.dumps(settings)}, open(value("--out"), "w"))
'''


def test_the_bone_offset_operation_passes_the_targets(tmp_path):
    (tmp_path / "dance.blend").write_bytes(b"moved")

    def run(targets):
        recipe = {"schema_version": "model3d-recipe/0.1", "name": "offset", "inputs": {"dance": {"path": "dance.blend"}},
                  "steps": [{"id": "offset", "operation": "motion.bone_offset", "inputs": {"blend": "$inputs.dance"},
                             "parameters": {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "frame": 3,
                                            "targets": targets}, "outputs": {"offsets": "offsets.json"}}]}
        atomic_json(tmp_path / "recipe.json", recipe)
        return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")

    result = run({"tip": {"bone": "Head", "object": "body", "pick": "highest", "box": [[-1, -1, 1.5], [1, 1, 2]]}})
    assert result["status"] == "succeeded"
    out = json.loads(open(result["steps"]["offset"]["artifacts"]["offsets"], encoding="utf-8").read())
    settings = json.loads(out["notes"])
    assert settings["targets"]["tip"]["bone"] == "mixamorig:Head" and settings["targets"]["tip"]["name"] == "Head" and settings["frame"] == 3
    assert out["points"]["tip"] == [{"bone": "Head", "at": 0.0, "offset": [0.0, 0.2, 0.1]}]
    assert result["steps"]["offset"]["observations"][0]["points"] == {"tip": [0.0, 0.2, 0.1]}
    failed = run({"drop": {"bone": "Head", "object": "body", "pick": "lowest"}})
    assert failed["status"] == "failed" and "点が足りません" in failed["error"]
