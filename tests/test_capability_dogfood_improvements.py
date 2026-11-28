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
    assert envelope["result"]["schema"]["$id"] == "orpheus-capability-catalog/0.4"
    assert validation_errors("response", envelope) == []


def test_lyric_workflow_points_to_a_runnable_existing_starter():
    workflow = get_item(build_catalog(), "workflow.lyric_mv")
    assert workflow["asset_expectations"]
    starter = workflow["starters"][0]
    assert starter["id"] == "starter.synthetic_lyric_mv"
    assert starter["commands"] == [
        ["uv", "sync", "--locked", "--extra", "analysis"],
        ["pnpm", "install", "--frozen-lockfile"],
        ["uv", "run", "--no-sync", "python", "examples/workflow/run.py", "PROJECT_DIR"],
    ]
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
    assert tools["node"]["required_on_path"] is False
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


def test_model3d_ffmpeg_resolution_matches_assemble_operation():
    catalog = build_catalog()
    if not catalog["components"]["model3d"]["available"]:
        return
    assemble = get_item(catalog, "model3d.render.assemble")
    ffmpeg = {row["name"]: row for row in assemble["requires"]["tool_requirements"]}["ffmpeg"]
    assert ffmpeg["resolution"] == "parameter-or-context-or-bundled-or-path"
    assert ffmpeg["parameter"] == "ffmpeg"
    assert ffmpeg["context_tool"] == "ffmpeg"
    assert ffmpeg["required_on_path"] is False
    assert ffmpeg["bundled_by"] == "imageio-ffmpeg"
    assert "environment" not in ffmpeg


def test_all_synthetic_starters_install_their_optional_extras():
    catalog = build_catalog()
    expected = {
        "workflow.dance_edit": "fx",
        "workflow.inspect_and_repair": "fx",
        "workflow.character_3d": "model3d",
    }
    for workflow_id, extra in expected.items():
        starter = get_item(catalog, workflow_id)["starters"][0]
        assert starter["commands"][0] == ["uv", "sync", "--locked", "--extra", extra]
        assert all("--no-sync" in command for command in starter["commands"][1:])


def test_brief_search_and_workflows_are_small_and_follow_their_schema(capsys):
    assert capabilities_cli.main(["search", "一枚の画像から動画を作る", "--limit", "3", "--brief"]) == 0
    text = capsys.readouterr().out
    assert len(text.encode("utf-8")) <= 3000 and "\n " not in text                  # 字下げなし・3 KB まで
    envelope = json.loads(text)
    assert validation_errors("response", envelope) == [] and validation_errors("brief", envelope["result"]) == []
    results = envelope["result"]["results"]
    assert [row["id"] for row in results[:2]] == ["workflow.one_image", "mv.fx.quick"]
    assert results[0]["docs"] == results[1]["docs"] == "docs/recipes/one-image.md" and results[0]["available"] is True
    assert results[0]["starters"][0][0] == ["uv", "sync", "--locked", "--extra", "fx"] and "starters" not in results[1]
    assert capabilities_cli.main(["search", "一枚の画像から動画を作る", "--limit", "3"]) == 0     # 既定の出力は今までどおり
    full = json.loads(capsys.readouterr().out)
    assert validation_errors("search", full["result"]) == [] and full["result"]["results"][0]["item"]["id"] == "workflow.one_image"
    assert capabilities_cli.main(["workflows", "--brief"]) == 0
    listing = json.loads(capsys.readouterr().out)["result"]
    assert validation_errors("brief", listing) == [] and listing["count"] == len(listing["results"]) >= 10
    assert all(row["kind"] == "workflow" and "score" not in row for row in listing["results"])


def test_brief_items_say_why_a_tool_cannot_run():
    tool = {"id": "mv.x", "kind": "command", "maturity": "experimental", "summary": "s", "entrypoint": {"argv": ["mv", "x"], "docs": "docs/x.md"},
            "availability": {"available": False, "reason": "optional extra が現在のPython環境にありません",
                             "missing_extras": [{"extra": "fx", "import": "cv2"}]}}
    assert capabilities_cli.brief_item(tool, 12.345) == {
        "id": "mv.x", "kind": "command", "maturity": "experimental", "score": 12.3, "summary": "s", "docs": "docs/x.md",
        "available": False, "reason": "optional extra が現在のPython環境にありません", "missing": ["fx"]}
    flow = {"id": "workflow.x", "kind": "workflow", "maturity": "stable", "summary": "s", "docs": "docs/w.md", "starters": [],
            "availability": {"available": False, "reason": "必須の道具が現在の環境で使えません", "unavailable": ["mv.x"]}}
    assert capabilities_cli.brief_item(flow)["missing"] == ["mv.x"] and "starters" not in capabilities_cli.brief_item(flow)
