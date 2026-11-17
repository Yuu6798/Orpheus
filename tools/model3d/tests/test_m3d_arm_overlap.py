"""左右の腕の入り込み（check.arm_overlap）: いちばん重い骨の選び方・面の分け方・線の向き・要約と合否（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import arm_overlap as ao
from model3d.io import ToolError, atomic_json
from model3d.ops.arm_ops import overlap_summary

ALIASES = {"lower_arm_swing.L": "lower_arm.L", "lower_arm_twist.L": "lower_arm.L", "upper_arm_swing.L": "upper_arm.L"}


def test_twist_bones_count_with_their_bone_when_choosing_the_heaviest():
    items = [("upper_arm.L", 0.3), ("upper_arm_swing.L", 0.3), ("lower_arm.L", 0.4)]
    assert ao.dominant(items, {}) == "lower_arm.L"                       # 1 つずつなら前腕
    assert ao.dominant(items, ALIASES) == "upper_arm.L"                   # 上腕の仲間を足すと上腕（腕の頂点に数えない）
    assert ao.dominant([("hand.L", 0.5), ("lower_arm_twist.L", 0.25), ("lower_arm.L", 0.25)], ALIASES) == "hand.L"   # 同じ重みは先
    assert ao.dominant([], ALIASES) == ""


def test_faces_belong_to_an_arm_only_when_all_corners_do():
    tris = np.array([[0, 1, 2], [2, 3, 4], [3, 4, 5], [0, 1, 6]])
    side_of = np.array([1, 1, 1, 2, 2, 2, 0])
    sleeve = np.array([True, True, False, False, True, False, False])
    side, is_sleeve = ao.triangle_sides(tris, side_of, sleeve)
    assert list(side) == [1, 0, 2, 0] and list(is_sleeve) == [True, False, False, True]


def test_ray_directions_are_fixed_unit_vectors():
    first, again = ao.rays(), ao.rays()
    assert len(first) == 14 and all(np.array_equal(a, b) for a, b in zip(first, again))
    assert all(np.linalg.norm(d) == pytest.approx(1.0) for d in first)
    rng = np.random.default_rng(11)                                       # 台本と同じ乱数の並び
    assert np.array_equal(first[0], (lambda v: v / np.linalg.norm(v))(rng.normal(size=(14, 3))[0]))


def row(frame, skin=(0, 0.0), sleeve_skin=(0, 0.0), skin_sleeve=(0, 0.0), soft=(0, 0.0)):
    return {"frame": frame, "skin_in_skin": list(skin), "sleeve_in_skin": list(sleeve_skin), "skin_in_sleeve": list(skin_sleeve),
            "sleeve_in_sleeve": list(soft)}


def test_summary_counts_frames_with_enough_deep_vertices():
    raw = {"arms": {"L": {"verts": 900}}, "rows": [row(1), row(2, skin=(6, 3.5)), row(3, skin=(2, 9.0)), row(4, sleeve_skin=(3, 3.0)),
                                                   row(5, soft=(8, 12.0)), row(7, skin_sleeve=(4, 2.9))]}
    report = overlap_summary(raw)
    assert report["frames_overlapping"] == 2 and report["runs"] == [[2, 2], [4, 4]]   # 2 頂点・2.9 mm は数えない
    assert report["frames_skin_in_skin"] == 1 and report["frames_sleeve_only"] == 1
    assert report["worst"][0][0] == 2 and report["pass"] is None
    assert overlap_summary(raw, max_frames=2)["pass"] is True
    judged = overlap_summary(raw, max_frames=1)
    assert judged["pass"] is False and "2" in judged["failures"][0]
    assert overlap_summary(raw, min_verts=2)["frames_overlapping"] == 3


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
rows = [{"frame": f, "skin_in_skin": [6 if f == 2 else 0, 3.5], "sleeve_in_skin": [0, 0.0], "skin_in_sleeve": [0, 0.0],
         "sleeve_in_sleeve": [0, 0.0]} for f in (1, 2, 3)]
raw = {"schema_version": "model3d-arm-overlap/0.1", "armature": "Rig", "body": settings["body"],
       "arms": {a["name"]: {"verts": 10, "sleeve_verts": 4, "rest_ignored": 0} for a in settings["arms"]}, "rows": rows, "x_settings": settings}
json.dump(raw, open(value("--report"), "w", encoding="utf-8"))
'''

ARMS = [{"name": "L", "bones": ["lower_arm.L", "hand.L"], "forearm": "lower_arm.L", "wrist": "hand.L"},
        {"name": "R", "bones": ["lower_arm.R", "hand.R"], "forearm": "lower_arm.R", "wrist": "hand.R"}]


def test_overlap_step_reports_and_judges(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "overlap", "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "overlap", "operation": "check.arm_overlap", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": blender, "body": "Body", "arms": ARMS, "aliases": ALIASES,
                                                                     "sleeve_until": 0.5, "max_frames": 0},
                                                      "outputs": {"report": "overlap.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "needs_review", result
    report = json.loads(Path(result["steps"]["overlap"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["pass"] is False and report["runs"] == [[2, 2]] and report["arms"]["L"]["sleeve_verts"] == 4
    sent = json.loads(Path(report["raw"]).read_text(encoding="utf-8"))["x_settings"]
    assert sent["aliases"] == ALIASES and sent["sleeve_until"] == 0.5 and sent["vote"] == 0.7 and sent["near"] == 0.04


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


BASE = {"body": "Body", "arms": ARMS, "sleeve_until": 0.5}


@pytest.mark.parametrize("parameters, message", [
    ({"arms": ARMS, "sleeve_until": 0.5}, "body"),
    ({**BASE, "arms": ARMS[:1]}, "2 本"),
    ({**BASE, "arms": [ARMS[0], {**ARMS[1], "name": "L"}]}, "別の name"),
    ({**BASE, "arms": [ARMS[0], {**ARMS[1], "bones": ["hand.L", "lower_arm.R"]}]}, "重ならない"),
    ({**BASE, "arms": [{**ARMS[0], "forearm": "elbow.L"}, ARMS[1]]}, "forearm"),
    ({**BASE, "aliases": {"a": "a"}}, "aliases"),
    ({**BASE, "vote": 0}, "vote"),
    ({"body": "Body", "arms": ARMS}, "sleeve_until"),               # 袖の終わる位置は衣装ごとに違うので既定が無い
    ({**BASE, "sleeve_until": 1.5}, "sleeve_until"),
    ({**BASE, "min_verts": 0}, "min_verts"),
    ({**BASE, "max_frames": -1}, "max_frames"),
    ({**BASE, "rays": 20}, "知らない設定"),
])
def test_arm_overlap_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("check.arm_overlap").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                          {"report": tmp_path / "r.json"}, Context(tmp_path))
