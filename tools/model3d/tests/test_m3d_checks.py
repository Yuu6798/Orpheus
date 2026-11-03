import copy

import pytest

from m3d_helpers import box
from model3d import checks, demo, semantic
from model3d.io import ToolError
from model3d.mesh import Mesh


def test_structure_passes_the_demo_rig_and_reports_required_roles():
    rig = demo.rig_json(demo.sash_mesh().vertex_count)
    preset = demo.preset_json()
    semantic_map = semantic.build("a", preset, bones=[{"name": b["name"], "parent": b["parent"]} for b in rig["bones"]])
    report = checks.structure(rig, semantic_map, preset["required_roles"]["bones"])
    assert report["pass"] and report["stats"]["weights"]["unnormalized_vertices"] == 0 and report["stats"]["roots"] == 1


def test_structure_finds_cycles_missing_parents_bad_matrices_and_weights():
    rig = demo.rig_json(4)
    broken = copy.deepcopy(rig)
    broken["bones"][0]["parent"] = "mixamorig:Head"                      # Hips → Head → Spine → Hips
    broken["bones"][1]["rest_matrix"] = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]
    broken["bones"].append({"name": "mixamorig:Loose", "parent": "nobody"})
    broken["weights"] = {"vertex_count": 4, "groups": {"mixamorig:Hips": [[0, 0.5], [1, -0.1], [9, 1.0]], "ghost": [[2, 1.0]]}}
    report = checks.structure(broken)
    text = " ".join(report["failures"])
    assert not report["pass"]
    for expected in ("循環", "親 nobody", "レスト行列", "合計が 1 でない", "負の値", "無い頂点", "リグに無い骨", "ウェイトの無い頂点"):
        assert expected in text
    assert "根の骨がありません" in text


def test_structure_rejects_fractional_or_negative_weight_indices():
    """小数の頂点番号を丸めて通さない（-0.5 → 0 で合格にしない）。契約でも整数 0 以上に限る。"""
    from model3d import contracts
    rig = {"schema_version": "model3d-rig/0.1", "bones": [{"name": "root", "parent": None}],
           "weights": {"vertex_count": 1, "groups": {"root": [[-0.5, 1.0]]}}}
    with pytest.raises(ToolError, match="rig"):
        contracts.validate("rig", rig)
    report = checks.structure(rig)
    assert not report["pass"] and "整数でない頂点番号" in " ".join(report["failures"])
    rig["weights"]["groups"]["root"] = [[0.0, 1.0]]      # 1.0 のような整数値は通る
    contracts.validate("rig", rig)
    assert checks.structure(rig)["pass"]


def test_structure_required_roles_need_a_semantic_map():
    rig = demo.rig_json(1)
    report = checks.structure(rig, None, ["hips"])
    assert not report["pass"] and "SemanticMap" in report["failures"][0]
    preset = demo.preset_json()
    semantic_map = semantic.build("a", preset, bones=["mixamorig:Hips", "mixamorig:Ghost"])
    semantic_map["bones"]["mixamorig:Ghost"] = {"role": "head", "side": "C", "basis": "user", "state": "confirmed"}
    report = checks.structure(rig, semantic_map, ["hips", "head", "spine"])
    assert "spine" in report["failures"][0] and "対応表にある骨がリグにありません" in report["failures"][1]


def test_penetration_reports_inside_vertices_and_depth():
    strip = Mesh([[0, 0, 0], [0.5, 0, 0], [3, 0, 0], [3, 0, 1]], [[0, 1, 2, 3]])
    report = checks.penetration(strip, box(2.0))
    assert report["inside"] == 2 and report["inside_indices"] == [0, 1] and not report["pass"]
    assert abs(report["max_depth"] - 1.0) < 1e-12 and report["collider_topology_sha256"]
    clean = checks.penetration(Mesh([[5, 5, 5], [6, 5, 5], [5, 6, 5]], [[0, 1, 2]]), box(2.0))
    assert clean["pass"] and clean["inside"] == 0


def test_continuity_flags_jumps_and_skips_without_frames():
    track = {"frames": [[0.0, [[0, 0, 0], [1, 0, 0]]], [0.1, [[0, 0, 0.01], [1, 0, 0]]], [0.2, [[0, 0, 0.5], [1, 0, 0]]]]}
    report = checks.continuity(track, 0.1)
    assert not report["pass"] and report["jumps"][0]["frame"] == 2 and abs(report["worst_move"] - 0.49) < 1e-12
    assert checks.continuity(track, 1.0)["pass"]
    skipped = checks.continuity({"frames": []}, 0.1)
    assert skipped["pass"] is None and skipped["status"] == "skipped"
    with pytest.raises(ToolError, match="点の数"):
        checks.continuity({"frames": [[0, [[0, 0, 0]]], [1, [[0, 0, 0], [1, 1, 1]]]]}, 0.1)
    with pytest.raises(ToolError, match="max_jump"):
        checks.continuity(track, 0)
    with pytest.raises(ToolError, match="コマ 1 の形"):
        checks.continuity({"frames": [[0, [[0, 0, 0]]], [1.0]]}, 0.1)
    with pytest.raises(ToolError, match="点の列を読めません"):
        checks.continuity({"frames": [[0, [[0, 0, 0]]], [1, [[0, 0]]]]}, 0.1)
