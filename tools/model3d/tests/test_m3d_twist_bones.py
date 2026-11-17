"""ねじれを分ける補助の骨（model.twist_bones）とねじれの角を測る（check.twist）: 重みの分け方とねじれの式・要約（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import twist_bones as tb
from model3d.blender_adapter import twist_measure as tm
from model3d.io import ToolError, atomic_json
from model3d.ops.model_ops import twist_summary


def test_along_the_bone_is_clipped_to_the_bone():
    head, tail = np.array([0.2, 0.0, 1.4]), np.array([0.5, 0.0, 1.4])
    positions = np.array([[0.1, 0, 1.4], [0.2, 0.03, 1.4], [0.35, 0, 1.43], [0.5, 0, 1.4], [0.7, 0, 1.4]])
    assert tb.along_bone(positions, head, tail) == pytest.approx([0.0, 0.0, 0.5, 1.0, 1.0])


def test_small_islands_on_the_bone_take_one_share():
    along = np.array([0.1, 0.3, 0.5, 0.6, 0.7, 0.8])
    roots = np.array([0, 0, 0, 3, 3, 3])                       # 島 0（腕の肌）と島 3（腕輪）
    touched = np.ones(6, bool)
    dominant = np.array([False, True, True, True, True, True])
    shares, rigid = tb.split_shares(along, roots, touched, dominant, max_verts=3, share=0.95)
    assert rigid == 1 and shares[3:] == pytest.approx([0.7, 0.7, 0.7])      # 腕輪は島の平均の割合
    assert shares[:3] == pytest.approx([0.1, 0.3, 0.5])                    # 肌は位置のまま（付け根ほど足した骨へ）
    _, rigid = tb.split_shares(along, roots, touched, dominant, max_verts=2, share=0.95)
    assert rigid == 0                                                      # 大きすぎる島は分けない


def test_dominant_bone_is_chosen_among_deform_groups():
    from types import SimpleNamespace as NS
    vertices = [NS(index=0, groups=[NS(group=1, weight=1.0), NS(group=0, weight=0.8)]), NS(index=1, groups=[NS(group=0, weight=0.5)])]
    mesh = NS(vertices=vertices)
    weights, dominant = tb.group_state(mesh, 0)
    assert list(weights) == [0.8, 0.5] and list(dominant) == [False, True]          # 全部で比べると、選択のグループ 1 が勝つ
    weights, dominant = tb.group_state(mesh, 0, bone_groups={0})
    assert list(dominant) == [True, True]                                            # 変形に使う骨のグループの中で比べる


def test_only_meshes_moved_by_the_armature_count():
    from types import SimpleNamespace as NS
    from model3d.blender_adapter.model_edit import deformed_by
    rig, other = NS(name="Rig"), NS(name="Other")
    assert deformed_by(NS(modifiers=[NS(type="ARMATURE", object=rig)], parent=None, parent_type="OBJECT"), rig)
    assert deformed_by(NS(modifiers=[], parent=rig, parent_type="ARMATURE"), rig)
    assert not deformed_by(NS(modifiers=[NS(type="ARMATURE", object=other)], parent=rig, parent_type="BONE"), rig)   # ほかのキャラ・骨の子


def test_twist_angle_from_the_quaternion():
    for angle in (0.0, 30.0, -100.0, 179.0):
        half = math.radians(angle) / 2
        assert tm.twist_deg(math.cos(half), math.sin(half)) == pytest.approx(angle)
    half = math.radians(200.0) / 2
    assert tm.twist_deg(math.cos(half), math.sin(half)) == pytest.approx(-160.0)   # −180〜180 に戻す


def test_twist_summary_counts_and_threshold():
    raw = {"frames": [1, 2, 3, 4], "twist_deg": {"upper_arm.L": [10.0, -95.0, 70.0, 5.0], "upper_arm.R": [0.0, 1.0, -2.0, 3.0]}}
    report = twist_summary(raw, over_deg=[60, 90], top=2)
    left = report["bones"]["upper_arm.L"]
    assert left["min_deg"] == -95.0 and left["max_deg"] == 70.0 and left["max_abs_deg"] == 95.0
    assert left["over"] == {"60": 2, "90": 1} and left["worst"] == [[2, -95.0], [3, 70.0]]
    assert report["pass"] is None and report["frame_count"] == 4
    judged = twist_summary(raw, max_twist_deg=90.0)
    assert judged["pass"] is False and len(judged["failures"]) == 1 and "upper_arm.L" in judged["failures"][0]
    assert twist_summary(raw, max_twist_deg=100.0)["pass"] is True


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
script = sys.argv[sys.argv.index("--python") + 1]
if script.endswith("twist_bones.py"):
    shutil.copyfile(value("--blend"), value("--out-blend"))
    report = {"schema_version": "model3d-twist-bones/0.1", "armature": "Rig",
              "bones": [{"name": e["name"], "copy_of": e["bone"], "aims_at": e["aim"]} for e in settings["bones"]],
              "weights": {"Body": {settings["bones"][0]["bone"]: {"verts": 9, "rigid_islands": 1}}}, "x_settings": settings}
else:
    report = {"schema_version": "model3d-twist/0.1", "armature": "Rig", "frames": [1, 2, 3],
              "twist_deg": {name: [10.0, -100.0, 20.0] for name in settings["bones"]}, "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def run_recipe(tmp_path, steps):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "twist",
                                           "inputs": {"dance": {"path": "dance.blend"}}, "steps": steps})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_twist_steps(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    steps = [{"id": "bones", "operation": "model.twist_bones", "inputs": {"blend": "$inputs.dance"},
              "parameters": {"blender": blender, "bones": [{"bone": "upper_arm.L", "aim": "lower_arm.L", "name": "upper_arm_swing.L"},
                                                           {"bone": "upper_arm.R", "aim": "lower_arm.R"}]},
              "outputs": {"blend": "twist.blend", "report": "bones.json"}},
             {"id": "measure", "operation": "check.twist", "inputs": {"blend": "$steps.bones.blend"},
              "parameters": {"blender": blender, "bones": ["upper_arm.L"], "max_twist_deg": 90},
              "outputs": {"report": "twist.json"}}]
    result = run_recipe(tmp_path, steps)
    assert result["status"] == "needs_review", result            # R の重みのメッシュが無い・ねじれが 90° を超える
    bones = result["steps"]["bones"]
    assert bones["status"] == "needs_review" and any("upper_arm.R" in u for u in bones["unresolved"])
    report = json.loads(Path(bones["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["bones"][1]["name"] == "upper_arm.R_swing"      # 名前を省くと bone + "_swing"
    measured = json.loads(Path(result["steps"]["measure"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert measured["pass"] is False and measured["bones"]["upper_arm.L"]["max_abs_deg"] == 100.0


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({}, "bones"),
    ({"bones": [{"bone": "a"}]}, "aim"),
    ({"bones": [{"bone": "a", "aim": "a"}]}, "別の骨"),
    ({"bones": [{"bone": "a", "aim": "b", "name": "c"}, {"bone": "a", "aim": "d", "name": "e"}]}, "重なって"),
    ({"bones": [{"bone": "a", "aim": "b", "name": "c"}, {"bone": "c", "aim": "d", "name": "e"}]}, "別の名前"),
    ({"bones": [{"bone": "a", "aim": "b", "wobble": 1}]}, "知らない設定"),
    ({"bones": [{"bone": "a", "aim": "b"}], "rigid_share": 2}, "rigid_share"),
    ({"bones": [{"bone": "a", "aim": "b"}], "meshes": []}, "meshes"),
])
def test_twist_bones_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("model.twist_bones").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                          {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({}, "bones"),
    ({"bones": ["a"], "frames": [5, 1]}, "frames"),
    ({"bones": ["a"], "over_deg": []}, "over_deg"),
    ({"bones": ["a"], "max_twist_deg": -1}, "max_twist_deg"),
    ({"bones": ["a"], "top": 0}, "top"),
])
def test_check_twist_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("check.twist").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                    {"report": tmp_path / "r.json"}, Context(tmp_path))
