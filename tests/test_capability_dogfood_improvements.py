import json

from jsonschema import Draft202012Validator

from orpheus_catalog import capabilities_cli
from orpheus_catalog.capability_catalog import build_catalog, filter_items, get_item, search_catalog
from orpheus_catalog.capability_validation import validate_catalog
from orpheus_catalog.schemas import load_schema, names, validation_errors


def test_public_schemas_are_valid_and_cover_real_outputs(capsys):
    for name in names():
        Draft202012Validator.check_schema(load_schema(name))

    catalog = build_catalog()
    assert validation_errors("catalog", catalog) == []
    search = search_catalog(catalog, "既存の資産で短い歌詞MVを作る", limit=5)
    assert validation_errors("search", search) == []

    assert capabilities_cli.main(["schema", "catalog"]) == 0
    envelope = json.loads(capsys.readouterr().out)
    assert envelope["ok"] is True
    assert envelope["result"]["schema"]["$id"] == "orpheus-capability-catalog/0.3"
    assert validation_errors("response", envelope) == []


def test_lyric_workflow_points_to_a_runnable_existing_starter():
    workflow = get_item(build_catalog(), "workflow.lyric_mv")
    assert workflow["asset_expectations"]
    starter = workflow["starters"][0]
    assert starter["id"] == "starter.synthetic_lyric_mv"
    assert starter["commands"] == [["uv", "run", "python", "examples/workflow/run.py", "PROJECT_DIR"]]
    assert "verification.json" in " ".join(starter["expected_outputs"])
    result = search_catalog(build_catalog(), "既存の資産で短い歌詞MVを作る", limit=3)
    assert result["results"][0]["item"]["id"] == "workflow.lyric_mv"


def test_tool_resolution_distinguishes_path_and_bundled_fallbacks():
    render = get_item(build_catalog(), "mv.render")
    tools = {row["name"]: row for row in render["requires"]["tool_requirements"]}
    assert tools["ffmpeg"] == {"name": "ffmpeg", "resolution": "env-or-path-or-bundled",
                                "environment": "ORPHEUS_FFMPEG", "required_on_path": False,
                                "bundled_by": "imageio-ffmpeg"}
    assert tools["ffprobe"]["required_on_path"] is False
    assert tools["ffprobe"]["bundled_by"] == "remotion"
    assert tools["node"]["required_on_path"] is True
    assert tools["browser"]["network_on_first_use"] is True
    assert render["requires"]["network"]["mode"] == "environment-dependent"


def test_parameter_readiness_makes_undocumented_model3d_visible_and_filterable():
    catalog = build_catalog()
    eye = get_item(catalog, "workflow.eye_rebuild")
    lyric = get_item(catalog, "workflow.lyric_mv")
    assert eye["parameter_readiness"]["required"]["machine_readable_complete"] is False
    assert eye["parameter_readiness"]["required"]["undocumented"]
    assert lyric["parameter_readiness"]["required"]["machine_readable_complete"] is True

    ready = {item["id"] for item in filter_items(catalog, kind="workflow", machine_readable_only=True)}
    assert "workflow.lyric_mv" in ready
    assert "workflow.eye_rebuild" not in ready
    undocumented = {item["id"] for item in filter_items(catalog, kind="workflow", parameter_status="undocumented")}
    assert "workflow.eye_rebuild" in undocumented


def test_catalog_validation_includes_public_json_schema():
    report = validate_catalog(build_catalog())
    assert report["valid"] is True, report
    broken = build_catalog()
    del broken["workflows"][0]["starters"]
    report = validate_catalog(broken)
    assert report["valid"] is False
    assert any(error["code"] == "SCHEMA_VALIDATION" for error in report["errors"])
