import pytest

from model3d import contracts, demo, manifest, semantic
from model3d.io import ToolError, atomic_json


def test_preset_vocabulary_is_checked():
    preset = demo.preset_json()
    semantic.check_preset(preset)
    preset["parts"]["weird"] = "garment.cape"
    with pytest.raises(ToolError, match="知らない部位の役割: garment.cape"):
        semantic.check_preset(preset)
    preset["parts"]["weird"] = "x.cape"      # 拡張は x. で始める
    semantic.check_preset(preset)


def test_build_maps_parts_and_bones_with_namespace_tolerance_and_reports_gaps():
    preset = demo.preset_json()
    result = semantic.build("asset-1", preset, parts=["garment_sash_front", "unknown_part"],
                            bones=["mixamorig:Hips", "Spine", {"name": "LeftUpLeg", "parent": "Hips"}, "mystery"])
    contracts.validate("semantic-map", result)
    assert result["parts"]["garment_sash_front"] == {"role": "garment.sash.front", "side": "C", "basis": "preset", "state": "unconfirmed"}
    assert result["bones"]["Spine"]["role"] == "spine" and result["bones"]["LeftUpLeg"]["role"] == "leg.upper.L"
    assert result["bones"]["LeftUpLeg"]["side"] == "L" and result["bones"]["LeftUpLeg"]["parent"] == "Hips"
    assert result["unmapped"] == {"parts": ["unknown_part"], "bones": ["mystery"]}
    assert result["required_missing"]["parts"] == ["collider.leg.upper.L"]
    assert "head" in result["required_missing"]["bones"] and "arm.upper.L" in result["required_missing"]["bones"]


def test_confirm_and_roles_present():
    preset = demo.preset_json()
    result = semantic.build("asset-1", preset, parts=["garment_sash_front", "leg_upper_l_collider"], bones=["mixamorig:Hips"])
    semantic.confirm(result, "parts", "leg_upper_l_collider", "rejected", "tester", "袖と胴体が融合している")
    present = semantic.roles_present(result)
    assert present["parts"] == {"garment.sash.front"} and present["bones"] == {"hips"}
    assert semantic.roles_present(result, confirmed_only=True)["parts"] == set()
    assert result["history"][-1]["key"] == "leg_upper_l_collider" and result["parts"]["leg_upper_l_collider"]["basis"] == "user"
    with pytest.raises(ToolError):
        semantic.confirm(result, "parts", "nope", "confirmed", "tester")
    with pytest.raises(ToolError):
        semantic.confirm(result, "parts", "garment_sash_front", "maybe", "tester")


def test_unconfirmed_roles_include_regions_and_do_not_hide_duplicate_mappings():
    result = semantic.build("asset", demo.preset_json(), parts=["garment_sash_front", "leg_upper_l_collider"],
                            bones=["mixamorig:Hips"])
    semantic.confirm(result, "parts", "garment_sash_front", "confirmed", "tester")
    semantic.confirm(result, "parts", "leg_upper_l_collider", "rejected", "tester")
    result["regions"]["extra_sash"] = {"role": "garment.sash.front", "part": "garment_sash_front", "faces": [0],
                                       "topology_sha256": "0" * 64, "basis": "inferred", "state": "unconfirmed"}
    contracts.validate("semantic-map", result)
    assert semantic.unconfirmed_roles(result) == {"garment.sash.front", "hips"}
    semantic.confirm(result, "regions", "extra_sash", "confirmed", "tester")
    assert semantic.unconfirmed_roles(result) == {"hips"}


def test_asset_manifest_records_hashes_and_mesh_identity(tmp_path):
    sash = demo.sash_mesh()
    sash.save(tmp_path / "sash.json")
    (tmp_path / "ref.png").write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    atomic_json(tmp_path / "notes.json", {"hello": 1})
    data = manifest.build("asset-1", [("sash", tmp_path / "sash.json"), ("sheet", tmp_path / "ref.png"), ("notes", tmp_path / "notes.json")],
                          parts=["garment_sash_front"], version="2", notes="synthetic")
    contracts.validate("asset-manifest", data)
    by_name = {i["name"]: i for i in data["inputs"]}
    assert by_name["sash"]["kind"] == "mesh" and by_name["sash"]["topology_sha256"] == sash.topology_hash()
    assert by_name["sash"]["vertex_count"] == sash.vertex_count and by_name["sheet"]["kind"] == "image"
    assert by_name["notes"]["kind"] == "json" and len(by_name["notes"]["sha256"]) == 64
    assert data["parts"] == [{"id": "garment_sash_front"}] and data["version"] == "2"
    with pytest.raises(ToolError, match="重なって"):
        manifest.build("asset-1", [("a", tmp_path / "sash.json"), ("a", tmp_path / "ref.png")])
    with pytest.raises(ToolError, match="見つかりません"):
        manifest.build("asset-1", [("a", tmp_path / "missing.json")])
