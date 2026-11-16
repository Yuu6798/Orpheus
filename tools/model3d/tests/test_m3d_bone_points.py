"""motion.bone_path の点（points）: 項の計算（骨の付け根から先への割合・軸に沿ったずらし・重みつきの平均・点の参照）、設定の確かめ、
工程の受け渡し（代役の Blender）、契約。値は作り物。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import bone_path
from model3d.io import ToolError, atomic_json
from model3d.ops.motion_ops import _bone_path_settings


def _rotation(degrees):
    """z のまわりに回した骨の向き（列が骨の x・y・z 軸）。長さを 2 倍にして、軸を長さ 1 に直すことも確かめる。"""
    r = math.radians(degrees)
    return 2.0 * np.array([[math.cos(r), -math.sin(r), 0.0], [math.sin(r), math.cos(r), 0.0], [0.0, 0.0, 1.0]])


BONES = {
    "head": (np.array([0.0, 0.1, 1.5]), np.array([0.0, 0.1, 1.7]), _rotation(0)),
    "arm.L": (np.array([0.2, 0.0, 1.4]), np.array([0.5, 0.0, 1.4]), _rotation(90)),
    "arm.R": (np.array([-0.2, 0.0, 1.4]), np.array([-0.5, 0.0, 1.4]), _rotation(-90)),
    "leg.L": (np.array([0.1, 0.0, 0.9]), np.array([0.1, 0.0, 0.5]), _rotation(180)),
    "leg.R": (np.array([-0.1, 0.0, 0.9]), np.array([-0.1, 0.0, 0.5]), _rotation(180)),
}


def _bone_of(name):
    head, tail, rotation = BONES[name]
    return head, tail, lambda k: rotation[:, k]


def test_a_bone_term_goes_along_the_bone_and_its_axes():
    head, tail, rotation = BONES["head"]
    axes = lambda k: rotation[:, k]                                   # noqa: E731
    assert np.array_equal(bone_path.bone_term(head, tail, axes, 0.0, [0, 0, 0], 1.0), head)
    assert np.array_equal(bone_path.bone_term(head, tail, axes, 1.0, [0, 0, 0], 1.0), tail)
    beyond = bone_path.bone_term(head, tail, axes, 1.2, [0.0, 0.0, -0.07], 1.0)
    assert np.allclose(beyond, head + 1.2 * (tail - head) - 0.07 * np.array([0.0, 0.0, 1.0]))     # 骨の z 軸（長さ 1 に直す）
    assert np.allclose(bone_path.bone_term(head, tail, axes, 0.5, [0.03, 0.0, 0.0], 1.0), [0.03, 0.1, 1.6])
    # 長さの単位: 1 Blender 単位 = 0.01 m なら、0.07 m のずらしは 7 単位
    assert np.allclose(bone_path.bone_term(head, tail, axes, 0.0, [0.0, 0.0, 0.07], 0.01), head + [0.0, 0.0, 7.0])


def test_points_average_their_terms_and_can_use_other_points():
    points = {
        "chest": [{"point": "shoulders", "weight": 0.7}, {"point": "hips", "weight": 0.3}],    # 参照する点より前に書いてもよい
        "shoulders": [{"bone": "arm.L", "at": 0.0, "offset": [0, 0, 0], "weight": 1.0},
                      {"bone": "arm.R", "at": 0.0, "offset": [0, 0, 0], "weight": 1.0}],
        "hips": [{"bone": "leg.L", "at": 0.0, "offset": [0, 0, 0], "weight": 1.0},
                 {"bone": "leg.R", "at": 0.0, "offset": [0, 0, 0], "weight": 1.0}],
        "face": [{"bone": "head", "at": 1.2, "offset": [0.0, 0.0, -0.07], "weight": 1.0}],
        "hand": [{"bone": "arm.L", "at": 1.0, "offset": [0, 0, 0], "weight": 1.0}],
    }
    assert bone_path.point_order(points) == ["shoulders", "hips", "chest", "face", "hand"]
    out = bone_path.measure_points(points, _bone_of, 1.0)
    assert np.allclose(out["shoulders"], [0.0, 0.0, 1.4]) and np.allclose(out["hips"], [0.0, 0.0, 0.9])
    assert np.allclose(out["chest"], 0.7 * out["shoulders"] + 0.3 * out["hips"])
    assert np.allclose(out["face"], [0.0, 0.1, 1.5 + 1.2 * 0.2 - 0.07])
    assert np.array_equal(out["hand"], BONES["arm.L"][1])               # ちょうど先は、先の値そのもの


def test_combining_keeps_the_rounding_of_the_prototype_formulas():
    """台本の (左 + 右) / 2 と 肩 × 0.7 + 腰 × 0.3 を、単精度で同じ丸めにする（重みの和 0.7 + 0.3 はちょうど 1）。"""
    a, b = np.float32([0.1234567, 1.0, 2.0]), np.float32([0.7654321, 3.0, -1.5])
    terms = [{"weight": 1.0}, {"weight": 1.0}]
    values = iter([a, b])
    assert np.array_equal(bone_path.combine(terms, lambda term: next(values)), (a + b) / 2)
    shoulders, hips = np.float32([0.31, 0.02, 1.41]), np.float32([0.05, -0.01, 0.93])
    mixed = bone_path.combine([{"weight": 0.7}, {"weight": 0.3}], lambda term: shoulders if term["weight"] == 0.7 else hips)
    assert 0.7 + 0.3 == 1.0
    assert np.array_equal(mixed, shoulders * np.float32(0.7) + hips * np.float32(0.3))


def test_the_settings_fill_in_the_terms_and_prefix_the_bones():
    settings = _bone_path_settings({"bone_prefix": "rig:", "points": {
        "face": [{"bone": "Head", "at": 1.2, "offset": [0, 0, -0.07]}],
        "chest": [{"point": "face", "weight": 2}, {"bone": "Hips"}]}}, None)
    assert settings["bones"] == []                                     # points があれば、bones の既定は空
    assert settings["points"] == {
        "face": [{"bone": "rig:Head", "at": 1.2, "offset": [0.0, 0.0, -0.07], "weight": 1.0}],
        "chest": [{"point": "face", "weight": 2.0}, {"bone": "rig:Hips", "at": 0.0, "offset": [0.0, 0.0, 0.0], "weight": 1.0}]}
    plain = _bone_path_settings({}, None)
    assert plain == {"bones": ["mixamorig:Hips", "mixamorig:Head"], "frames": None, "armature": None}    # 前と同じ
    both = _bone_path_settings({"bones": ["Hips"], "points": {"hand": [{"bone": "Hand", "at": 1}]}}, None)
    assert both["bones"] == ["mixamorig:Hips"] and both["points"]["hand"][0]["bone"] == "mixamorig:Hand"


@pytest.mark.parametrize("points, message", [
    ({}, "points は"),
    ([], "points は"),
    ({"a": []}, "項の並び"),
    ({"a": [{"weight": 1}]}, "どちらか 1 つ"),
    ({"a": [{"bone": "B", "point": "a"}]}, "どちらか 1 つ"),
    ({"a": [{"bone": "B", "scale": 2}]}, "知らない鍵"),
    ({"a": [{"point": "a", "at": 1}]}, "知らない鍵"),
    ({"a": [{"point": "b"}]}, "points にありません"),
    ({"a": [{"point": "a"}]}, "輪"),
    ({"a": [{"point": "b"}], "b": [{"point": "c"}], "c": [{"point": "a"}]}, "輪"),
    ({"a": [{"bone": ""}]}, "bone は"),
    ({"a": [{"bone": "B", "offset": [0, 0]}]}, "offset"),
    ({"a": [{"bone": "B", "offset": [0, True, 0]}]}, "offset"),
    ({"a": [{"bone": "B", "offset": [0, float("nan"), 0]}]}, "offset"),
    ({"a": [{"bone": "B", "weight": 0}]}, "weight"),
    ({"a": [{"bone": "B", "weight": float("inf")}]}, "weight"),
    ({"a": [{"bone": "B", "at": "tail"}]}, "at"),
    ({"a": [{"bone": "B", "at": float("inf")}]}, "at"),
])
def test_bad_points_are_refused(points, message):
    with pytest.raises(ToolError, match=message):
        _bone_path_settings({"points": points}, None)


def test_bones_must_not_be_empty_without_points():
    with pytest.raises(ToolError, match="bones"):
        _bone_path_settings({"bones": []}, None)


def test_the_contract_takes_points_with_or_without_bones():
    base = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": [1, 2]}
    contracts.validate("bone-path", {**base, "bones": {}, "points": {"face": [[0, 0, 1], [0, 0, 1]]}})
    contracts.validate("bone-path", {**base, "bones": {"Hips": [[0, 0, 1], [0, 0, 1]]}})
    with pytest.raises(ToolError):
        contracts.validate("bone-path", {**base, "bones": {}})
    with pytest.raises(ToolError):
        contracts.validate("bone-path", {**base, "bones": {}, "points": {}})


# 代役の Blender: 設定の bones と points を、位置 (f, 0, 1) の通り道で書き、受け取った設定を notes に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
frames = settings["frames"] or [1, 2, 3]
out = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "scale_length": 1.0, "fps": 24, "armature": "rig", "frames": frames,
       "bones": {b: [[f, 0, 1] for f in frames] for b in settings["bones"]}, "notes": json.dumps(settings)}
if settings.get("points"):
    names = ["other"] if "drop" in settings["points"] else settings["points"]
    out["points"] = {name: [[f, 0, 2] for f in frames] for name in names}
json.dump(out, open(value("--out"), "w"))
'''


def _recipe(tmp_path, points):
    (tmp_path / "dance.blend").write_bytes(b"moved")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "points",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "bones", "operation": "motion.bone_path", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)),
                                                                     "bone_prefix": "", "frame_range": [1, 2], "points": points},
                                                      "outputs": {"path": "bones.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_passes_the_points_and_reports_them(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_recipe(tmp_path, {"face": [{"bone": "head", "at": 1.2, "offset": [0, 0, -0.07]}],
                                                    "hand": [{"bone": "hand.L", "at": 1}]}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["bones"]
    path = contracts.load("bone-path", step["artifacts"]["path"])
    assert sorted(path["points"]) == ["face", "hand"] and path["bones"] == {}
    settings = json.loads(path["notes"])
    assert settings["points"]["face"] == [{"bone": "head", "at": 1.2, "offset": [0.0, 0.0, -0.07], "weight": 1.0}]
    assert settings["frames"] == [1, 2]
    assert step["observations"][0]["points"] == ["face", "hand"]
    assert Path(step["artifacts"]["path"]).is_file()


def test_the_operation_stops_when_a_point_is_missing_from_the_path(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_recipe(tmp_path, {"drop": [{"bone": "head"}]}), tmp_path / "runs")
    assert result["status"] == "failed" and "点がありません" in result["error"]
