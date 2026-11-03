import copy

import numpy as np
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


def test_structure_with_mesh_requires_matching_vertex_count_and_recorded_topology():
    from model3d.mesh import weld
    mesh = demo.sash_mesh()
    rig = demo.rig_json(mesh.vertex_count, mesh.topology_hash())
    assert checks.structure(rig, mesh=mesh)["pass"]
    welded, _ = weld(mesh, 1e-6)
    report = checks.structure(rig, mesh=welded)
    assert not report["pass"] and report["stats"]["weights"]["vertex_count"] == 42
    assert report["stats"]["mesh_binding"]["vertex_count"] == 35
    assert "頂点数" in " ".join(report["failures"]) and "topology_sha256" in " ".join(report["failures"])
    unbound = demo.rig_json(mesh.vertex_count)
    assert checks.structure(unbound)["pass"]   # 既存のリグ単体検査と、実メッシュへの結び付けを区別
    assert not checks.structure(unbound, mesh=mesh)["pass"]
    wrong_topology = copy.deepcopy(rig)
    wrong_topology["weights"]["topology_sha256"] = "0" * 64
    assert not checks.structure(wrong_topology, mesh=mesh)["pass"]
    no_weights = {k: v for k, v in rig.items() if k != "weights"}
    assert not checks.structure(no_weights, mesh=mesh)["pass"]


def test_structure_operation_keeps_used_unconfirmed_bone_roles_for_review(tmp_path):
    from types import SimpleNamespace
    from model3d.io import atomic_json
    from model3d.ops import check_ops
    rig = demo.rig_json(demo.sash_mesh().vertex_count)
    semantic_map = semantic.build("synthetic", demo.preset_json(), bones=rig["bones"])
    atomic_json(tmp_path / "rig.json", rig)
    context = SimpleNamespace(preset=None, semantic_map=semantic_map)
    args = ({"rig": tmp_path / "rig.json"}, {"required_bone_roles": ["hips"]}, {}, context)
    result = check_ops.check_structure(*args)
    assert result.observations[0]["pass"] and result.review and "hips" in result.unresolved[0]
    semantic_map["bones"]["mixamorig:Hips"]["state"] = "confirmed"
    result = check_ops.check_structure(*args)
    assert not result.review and not result.unresolved   # 未使用の spine などは成功を妨げない


def test_penetration_reports_inside_vertices_and_depth():
    strip = Mesh([[0, 0, 0], [0.5, 0, 0], [3, 0, 0], [3, 0, 1]], [[0, 1, 2, 3]])
    report = checks.penetration(strip, box(2.0))
    assert report["inside"] == 2 and report["inside_indices"] == [0, 1] and not report["pass"]
    assert abs(report["max_depth"] - 1.0) < 1e-12 and report["collider_topology_sha256"]
    clean = checks.penetration(Mesh([[5, 5, 5], [6, 5, 5], [5, 6, 5]], [[0, 1, 2]]), box(2.0))
    assert clean["pass"] and clean["inside"] == 0


def test_penetration_and_push_out_reject_different_units(tmp_path):
    from model3d.ops import collision_ops
    cm_strip = Mesh([[50, 0, 0], [60, 0, 0], [50, 10, 0]], [[0, 1, 2]], units="cm")
    with pytest.raises(ToolError, match="単位が違います"):
        checks.penetration(cm_strip, box(2.0))
    cm_strip.save(tmp_path / "strip.json")
    box(2.0).save(tmp_path / "box.json")
    with pytest.raises(ToolError, match="単位が違います"):
        collision_ops.push_out({"mesh": tmp_path / "strip.json", "collider": tmp_path / "box.json"}, {"clearance": 0.01}, {}, None)
    from model3d.mesh import transform
    in_m = transform(cm_strip, to_units="m")
    assert in_m.units == "m" and abs(in_m.vertices[0][0] - 0.5) < 1e-12
    assert checks.penetration(in_m, box(2.0))["inside"] == 3
    y_up = Mesh([[0, 5, 0], [3, 5, 0], [0, 5, 0.5]], [[0, 1, 2]], axes={"up": "+Y", "forward": "-Z"})   # 換算後: 中・外・中
    tall = transform(box(2.0), scale=[1, 1, 6])        # Z-up で z が -6..6 の箱: Y-up の [0, 5, 0] は換算すると中
    with pytest.raises(ToolError, match="軸の約束が違います"):
        checks.penetration(y_up, tall)
    y_up.save(tmp_path / "y_up.json")
    tall.save(tmp_path / "tall.json")
    with pytest.raises(ToolError, match="軸の約束が違います"):
        collision_ops.push_out({"mesh": tmp_path / "y_up.json", "collider": tmp_path / "tall.json"}, {"clearance": 0.01}, {}, None)
    z_up = transform(y_up, to_axes={"up": "+Z", "forward": "-Y"})
    assert z_up.axes == {"up": "+Z", "forward": "-Y"} and np.allclose(z_up.vertices[0], [0, 0, 5])
    assert np.allclose(z_up.vertices, [[0, 0, 5], [-3, 0, 5], [0, 0.5, 5]])
    assert checks.penetration(z_up, tall)["inside_indices"] == [0, 2] and not checks.penetration(z_up, tall)["pass"]


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
    empty = checks.continuity({"frames": [[0, []], [1, []]]}, 0.1)
    assert empty["pass"] is None and empty["status"] == "skipped" and "点の無いコマ" in empty["reason"]
    from model3d.ops import check_ops
    with pytest.raises(ToolError, match="未実施"):
        check_ops._finish(empty, {}, {"strict": True}, "連続性の検査")
    lenient = check_ops._finish(empty, {}, {}, "連続性の検査")
    assert lenient.review and "未実施" in lenient.unresolved[0]
    with pytest.raises(ToolError, match="コマ 1 の形"):
        checks.continuity({"frames": [[0, [[0, 0, 0]]], [1.0]]}, 0.1)
    with pytest.raises(ToolError, match="点の列を読めません"):
        checks.continuity({"frames": [[0, [[0, 0, 0]]], [1, [[0, 0]]]]}, 0.1)
