"""model.twist_bones の roll（手の回転の一部を写す骨）・min_weight・max_influences: 割合の折れ線と台本の式・1 頂点の骨の数の切り方
（Blender なし）、設定の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import twist_bones as tb
from model3d.io import ToolError, atomic_json


def test_half_way_roll_shares_match_the_script_bit_for_bit():
    """start 0.5 の折れ線は、台本の max(0, 1 − 2t)・1 − |2t − 1|・max(0, 2t − 1) とビットまで同じ。"""
    rng = np.random.default_rng(3)
    t = np.concatenate([[0.0, 0.25, 0.5, 0.5 + 1e-12, 0.75, 1.0], rng.random(20000), rng.random(2000) * 1e-3, 1 - rng.random(2000) * 1e-3])
    swing, source, roll = tb.part_shares(t, 0.5)
    assert swing.tobytes() == np.maximum(0.0, 1.0 - 2.0 * t).tobytes()
    assert source.tobytes() == (1.0 - np.abs(2.0 * t - 1.0)).tobytes()
    assert roll.tobytes() == np.maximum(0.0, 2.0 * t - 1.0).tobytes()


def test_shares_without_roll_and_with_another_start():
    t = np.array([0.0, 0.2, 0.3, 0.65, 1.0])
    swing, source, roll = tb.part_shares(t)
    assert roll is None and swing == pytest.approx(1 - t) and source.tobytes() == t.tobytes()
    swing, source, roll = tb.part_shares(t, 0.3)
    assert swing == pytest.approx([1.0, 1 / 3, 0.0, 0.0, 0.0])           # 付け根 → start で足した骨から元の骨へ
    assert roll == pytest.approx([0.0, 0.0, 0.0, 0.5, 1.0])              # start → 先で元の骨から roll の骨へ
    assert swing + source + roll == pytest.approx(np.ones(5))


def test_influences_are_cut_to_the_largest_and_keep_the_total():
    assert tb.capped([(0, 0.5), (1, 0.3), (2, 0.2)], 4) is None
    kept, dropped, share = tb.capped([(0, 0.1), (1, 0.4), (2, 0.2), (3, 0.2), (4, 0.1)], 4)
    assert [g for g, _ in kept] == [1, 2, 3, 0] and dropped == [4]       # 同じ重み（0 と 4）は先のものを残す
    assert sum(w for _, w in kept) == pytest.approx(1.0) and share == pytest.approx(0.1)
    assert kept[0][1] == pytest.approx(0.4 / 0.9)                          # 残した骨に同じ割合で割り振る
    kept, dropped, _ = tb.capped([(5, 0.3), (6, 0.3), (7, 0.2), (8, 0.1), (9, 0.05), (10, 0.05)], 2)
    assert [g for g, _ in kept] == [5, 6] and dropped == [7, 8, 9, 10]


def test_roll_settings_fill_defaults_and_refuse_bad_values():
    assert tb.check_roll({"name": "lower_arm_twist.L", "from": "hand.L"}, "roll") == {
        "name": "lower_arm_twist.L", "from": "hand.L", "start": 0.5, "euler_order": "ZYX"}
    for bad, message in [("x", "name"), ({"name": "a"}, "from"), ({"name": "a", "from": "a"}, "別の骨"),
                         ({"name": "a", "from": "b", "start": 1.0}, "start"), ({"name": "a", "from": "b", "start": True}, "start"),
                         ({"name": "a", "from": "b", "euler_order": "AUTO"}, "euler_order"), ({"name": "a", "from": "b", "axis": "X"}, "知らない設定")]:
        with pytest.raises(ValueError, match=message):
            tb.check_roll(bad, "roll")


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
report = {"schema_version": "model3d-twist-bones/0.1", "armature": "Rig",
          "bones": [{"name": e["name"]} for e in settings["bones"]] + [{"name": e["roll"]["name"]} for e in settings["bones"] if "roll" in e],
          "weights": {"Body": {e["bone"]: {"verts": 9, "rigid_islands": 0} for e in settings["bones"]}},
          "influences": {"Body": {"verts_checked": 9, "most_before": 5, "verts_cut": 2, "largest_share_cut": 0.05}}, "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def test_roll_and_limits_reach_the_script(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "model.blend").write_bytes(b"model")
    bones = [{"bone": "upper_arm.L", "aim": "lower_arm.L", "name": "upper_arm_swing.L"},
             {"bone": "lower_arm.L", "aim": "hand.L", "name": "lower_arm_swing.L", "roll": {"name": "lower_arm_twist.L", "from": "hand.L"}}]
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "roll", "inputs": {"model": {"path": "model.blend"}},
                                           "steps": [{"id": "bones", "operation": "model.twist_bones", "inputs": {"blend": "$inputs.model"},
                                                      "parameters": {"blender": blender, "bones": bones, "min_weight": 1e-5, "max_influences": 4},
                                                      "outputs": {"blend": "twist.blend", "report": "bones.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["bones"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    sent = report["x_settings"]
    assert sent["bones"][1]["roll"] == {"name": "lower_arm_twist.L", "from": "hand.L", "start": 0.5, "euler_order": "ZYX"}
    assert "roll" not in sent["bones"][0] and sent["min_weight"] == 1e-5 and sent["max_influences"] == 4
    observation = step["observations"][0]
    assert observation["bones"] == ["upper_arm_swing.L", "lower_arm_swing.L", "lower_arm_twist.L"]
    assert observation["influences"]["Body"]["verts_cut"] == 2


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "c"}}]}, "from"),
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "a", "from": "b"}}]}, "roll の name"),
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "r", "from": "a"}}]}, "roll の name"),
    ({"bones": [{"bone": "a", "aim": "b", "name": "s", "roll": {"name": "s", "from": "b"}}]}, "roll の name"),
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "r", "from": "b"}},
                {"bone": "c", "aim": "d", "roll": {"name": "r", "from": "d"}}]}, "重なって"),
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "r", "from": "b"}}, {"bone": "c", "aim": "r"}]}, "別の名前"),
    ({"bones": [{"bone": "a", "aim": "b", "roll": {"name": "r", "from": "b", "start": 0}}]}, "start"),
    ({"bones": [{"bone": "a", "aim": "b"}], "min_weight": -1}, "min_weight"),
    ({"bones": [{"bone": "a", "aim": "b"}], "max_influences": 0}, "max_influences"),
    ({"bones": [{"bone": "a", "aim": "b"}], "max_influences": 2.5}, "max_influences"),
])
def test_twist_bones_rejects_bad_roll_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("model.twist_bones").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                          {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
