"""render.prepare_push: 骨の子孫・重みの付け方・同じ点のまとめ方・つながった部分の選び方（Blender なしで動く部分）と、
設定の確かめと工程の受け渡し（代役の Blender）を確かめる。写しを作って頂点の組を足す所は本物の Blender が要るので、
CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import prepare_push as pp
from model3d.io import ToolError, atomic_json
from model3d.ops.render_ops import _prepare_settings


def test_the_seed_bones_include_their_children():
    parents = {"A0": None, "A1": "A0", "A2": "A1", "B0": None, "B1": "B0", "C": None}
    assert pp.subtree(parents, ["A0", "B1"]) == {"A0", "A1", "A2", "B1"}
    assert pp.subtree(parents, ["Z"]) == set()


def test_the_weights_fade_in_below_the_top_and_are_rounded_to_64ths():
    w = pp.fade_weights([1.0, 0.99, 0.98, 0.5, 1.2], 1.0, 0.02)
    assert w.tolist() == pytest.approx([0.0, 0.5, 1.0, 1.0, 0.0])
    levels, front = pp.group_levels(np.array([0.9999, 0.99, 0.9]), np.array([-0.2, 0.0, 0.7]), 1.0, 0.02)
    assert levels.tolist() == [0, 32, 64] and front.tolist() == [True, False, False]   # 上の端のすぐ下は 0 で組に入れない


def test_positions_on_the_same_grid_are_one_point():
    weld, count = pp.weld_index([[0, 0, 0], [0.000001, 0, 0], [0.00002, 0, 0], [0, 0, 0]], 1e-5)
    assert count == 2 and weld[0] == weld[1] == weld[3] != weld[2]
    labels = pp.components(5, [[0, 1], [3, 4]])
    assert labels[0] == labels[1] and labels[3] == labels[4] and len({labels[0], labels[2], labels[3]}) == 3


def strip(x, z_top, z_bottom, rows=5):
    """x の位置の 2 列の縦の帯（頂点と、横と縦の辺）。"""
    zs = np.linspace(z_top, z_bottom, rows)
    points = [[x + dx, 0.0, z] for z in zs for dx in (0.0, 0.01)]
    edges = [[2 * r, 2 * r + 1] for r in range(rows)] + [[2 * r + k, 2 * r + 2 + k] for r in range(rows - 1) for k in (0, 1)]
    return points, edges


def mesh(*parts):
    points, edges = [], []
    for p, e in parts:
        edges += [[a + len(points), b + len(points)] for a, b in e]
        points += p
    return np.array(points, float), np.array(edges)


def test_the_hanging_part_is_what_touches_a_seed_below_the_top():
    # 0〜9: 帯 A（1.1〜0.7 m）、10〜19: 帯 B（同じ）、20〜29: 帯 C（1.0〜0.6 m、種なし）、30〜31: A と B の上（1.05 m）の結び目。
    # C の上の端（1.0 m）は A の上の端（1.1 m）とつながる。どれも上の端（1.0 m）より上でだけつながる
    a, b, c = strip(0.0, 1.1, 0.7), strip(0.2, 1.1, 0.7), strip(0.4, 1.0, 0.6)
    points, edges = mesh(a, b, c)
    knot = np.array([[0.1, 0.0, 1.05], [0.11, 0.0, 1.05]])
    points = np.vstack([points, knot])
    edges = np.vstack([edges, [[0, 30], [30, 31], [31, 10], [20, 0]]])
    seeds = np.zeros(len(points), bool)
    seeds[[8, 9, 18]] = True                             # A と B の下の方だけに骨の重み
    chosen, pieces, low_seeds = pp.hanging_part(points, edges, seeds, 1.0, 1e-5)
    below = points[:, 2] < 1.0
    assert pieces == 2 and low_seeds == 3
    assert set(chosen.tolist()) == {i for i in range(20) if below[i]}   # 上の端より上（結び目と 1.1 m の行）は入らない
    assert not set(chosen.tolist()) & set(range(20, 32))              # 上の端より上でだけ A とつながる帯 C は入らない


def test_a_seam_joins_two_copies_of_one_point():
    # 帯 A の下半分を、同じ位置の別の頂点（継ぎ目）で写した帯。辺は写しの中だけ
    a = strip(0.0, 0.9, 0.5)
    points, edges = mesh(a, ([p[:] for p in a[0][4:]], [[x - 4, y - 4] for x, y in a[1] if x >= 4 and y >= 4]))
    seeds = np.zeros(len(points), bool)
    seeds[0] = True
    chosen, pieces, _ = pp.hanging_part(points, edges, seeds, 1.0, 1e-5)
    assert pieces == 1 and len(chosen) == len(points)    # 継ぎ目の向こうの写しも、同じ点として入る
    apart, _, _ = pp.hanging_part(points + np.r_[np.zeros((10, 3)), np.full((6, 3), 0.001)], edges, seeds, 1.0, 1e-5)
    assert len(apart) == 10                              # 位置がずれれば、別の部分


def test_the_settings_fill_the_defaults_and_add_the_bone_prefix():
    s = _prepare_settings({"colliders": [{"from": "leg_L"}, {"from": "leg_R", "name": "hit_R", "weld_mm": 0.05, "decimate": 1}],
                           "groups": [{"mesh": "skirt", "bones": ["TailA0"], "top": 1.0, "front": "f", "back": "b"}]})
    assert s["colliders"] == [{"from": "leg_L", "name": "proxy_leg_L", "weld": pytest.approx(2e-05), "decimate": 0.25},
                              {"from": "leg_R", "name": "hit_R", "weld": pytest.approx(5e-05), "decimate": 1.0}]
    assert s["groups"] == [{"mesh": "skirt", "bones": ["mixamorig:TailA0"], "top": 1.0, "fade": 0.02, "weld": pytest.approx(1e-05),
                            "front": "f", "back": "b"}]
    assert "armature" not in s
    only = _prepare_settings({"groups": [{"mesh": "m", "bones": ["X"], "top": 0, "front": "f", "back": "b"}], "bone_prefix": "",
                              "armature": "rig"})
    assert only["colliders"] == [] and only["groups"][0]["bones"] == ["X"] and only["armature"] == "rig"


GROUP = {"mesh": "skirt", "bones": ["TailA0"], "top": 1.0, "front": "f", "back": "b"}


@pytest.mark.parametrize("parameters, message", [
    ({}, "colliders"),
    ({"colliders": [], "groups": []}, "colliders"),
    ({"colliders": {"from": "leg"}}, "colliders"),
    ({"colliders": [{"name": "x"}]}, r"colliders\[0\]"),
    ({"colliders": [{"from": "leg", "size": 1}]}, r"colliders\[0\]"),
    ({"colliders": [{"from": ""}]}, r"colliders\[0\]\.from"),
    ({"colliders": [{"from": "leg", "name": "leg"}]}, "重なって"),
    ({"colliders": [{"from": "leg"}, {"from": "arm", "name": "proxy_leg"}]}, "重なって"),
    ({"colliders": [{"from": "leg", "decimate": 0}]}, "decimate"),
    ({"colliders": [{"from": "leg", "decimate": 1.5}]}, "decimate"),
    ({"colliders": [{"from": "leg", "weld_mm": 0}]}, "weld_mm"),
    ({"groups": [{**GROUP, "top": None}]}, "top"),
    ({"groups": [{**GROUP, "top": True}]}, "top"),
    ({"groups": [{k: v for k, v in GROUP.items() if k != "back"}]}, r"groups\[0\]"),
    ({"groups": [{**GROUP, "colour": 1}]}, r"groups\[0\]"),
    ({"groups": [{**GROUP, "bones": []}]}, "bones"),
    ({"groups": [{**GROUP, "bones": ["A", "A"]}]}, "bones"),
    ({"groups": [{**GROUP, "back": "f"}]}, "別の名前"),
    ({"groups": [{**GROUP, "fade": 0}]}, "fade"),
    ({"groups": [GROUP, GROUP]}, "mesh が重なって"),
    ({"colliders": [{"from": "leg"}], "groups": [{**GROUP, "mesh": "proxy_leg"}]}, "写しの名前"),
    ({"groups": [GROUP], "armature": ""}, "armature"),
    ({"groups": [GROUP], "bone_prefix": 1}, "bone_prefix"),
])
def test_bad_settings_are_refused(parameters, message):
    with pytest.raises(ToolError, match=message):
        _prepare_settings(parameters)


# 代役の Blender: --version に答え、ふだんは設定を報告に写し、写しと組の数を作った体で書く
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"prepared")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-prepare-push/0.1", "blender": "4.5.15", "scale_length": 1.0,
           "colliders": [{"name": c["name"], "from": c["from"], "armature": "rig", "filled_edges": 12, "open_edges": 0,
                          "volume_l": 6.4, "flipped": True, "vertices": 100, "faces": 196} for c in settings["colliders"]],
           "groups": [{"mesh": g["mesh"], "armature": "rig", "bones": g["bones"], "seeds": 40, "pieces": 2, "chosen": 50,
                       "front": {"name": g["front"], "vertices": 26}, "back": {"name": g["back"], "vertices": 21}, "left_out": 3,
                       "z": [0.7, 1.0]} for g in settings["groups"]],
           "settings": settings, "argv": args, "script": script}, open(value("--report"), "w"))
'''


def test_the_operation_writes_the_settings_and_passes_them_to_blender(tmp_path):
    from model3d import executor
    (tmp_path / "char.blend").write_bytes(b"char")
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    parameters = {"blender": str(blender), "colliders": [{"from": "leg_L"}, {"from": "leg_R"}], "groups": [GROUP]}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "prepare",
                                           "inputs": {"char": {"path": "char.blend"}},
                                           "steps": [{"id": "prepare", "operation": "render.prepare_push",
                                                      "inputs": {"blend": "$inputs.char"}, "parameters": parameters,
                                                      "outputs": {"blend": "prepared.blend", "report": "prepare.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["prepare"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "prepare_push.py" and "--settings" in report["argv"]
    assert report["settings"] == _prepare_settings({k: v for k, v in parameters.items() if k != "blender"})
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"prepared"
    observation = step["observations"][0]
    assert observation["kind"] == "prepare_push" and observation["blender_version"] == "4.5.15"
    assert observation["colliders"] == [{"name": "proxy_leg_L", "from": "leg_L", "open_edges": 0, "flipped": True, "faces": 196},
                                        {"name": "proxy_leg_R", "from": "leg_R", "open_edges": 0, "flipped": True, "faces": 196}]
    assert observation["groups"] == [{"mesh": "skirt", "pieces": 2, "front": 26, "back": 21}]
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["prepare"]["reused_from"] is None        # Blender は外部の実行ファイルなので毎回実行


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "char.blend").write_bytes(b"char")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("render.prepare_push").function({"blend": tmp_path / "char.blend"}, parameters, declared, Context(tmp_path))


def test_the_operation_needs_blender_both_outputs_and_known_parameters(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {"groups": [GROUP]})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": blender, "groups": [GROUP]}, {"blend": "o.blend"})
    with pytest.raises(ToolError, match="知らないパラメーター"):
        call(tmp_path, {"blender": blender, "groups": [GROUP], "push": []})
    with pytest.raises(ToolError, match="colliders"):              # 設定の誤りは Blender を起こす前に止まる
        call(tmp_path, {"blender": blender})
