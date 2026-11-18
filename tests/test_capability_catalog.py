import json
import sys
from types import ModuleType

import pytest

from mv_pipeline import capabilities_cli
from mv_pipeline import capability_catalog as catalog_module
from mv_pipeline.capability_catalog import build_catalog, get_item, search_catalog, validate_catalog
from mv_pipeline.capability_workflows import WORKFLOWS


def _without_model3d(monkeypatch):
    package = ModuleType("model3d")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "model3d", package)
    monkeypatch.delitem(sys.modules, "model3d.ops", raising=False)


def _with_fake_mv(monkeypatch):
    refs = sorted({step["capability"] for workflow in WORKFLOWS for step in workflow["steps"] if step["capability"].startswith("mv.")})
    capabilities = []
    for id in refs:
        domain = "delivery" if id.startswith("mv.deliver") else "quality" if id.startswith(("mv.gate", "mv.inspect", "mv.sheet", "mv.handoff")) else "video"
        aliases = ["区間を結合", "納品"] if id == "mv.deliver.join" else []
        capabilities.append({
            "id": id, "kind": "command", "domain": domain, "maturity": "experimental", "summary": id,
            "aliases": aliases, "tags": id.split(".")[1:], "entrypoint": {"argv": id.split("."), "docs": "docs/test.md"},
            "inputs": [], "outputs": [], "parameters": {"status": "cli", "schema": {"type": "object"}},
            "requires": {"extras": [], "tools": [], "network": False},
            "availability": {"available": True, "reason": None},
        })
    monkeypatch.setattr(catalog_module, "_mv_capabilities", lambda: ({"available": True, "reason": None, "commands": len(capabilities)}, capabilities))


def test_catalog_is_valid_and_keeps_optional_model3d_visible(monkeypatch):
    _with_fake_mv(monkeypatch)
    _without_model3d(monkeypatch)
    catalog = build_catalog()
    report = validate_catalog(catalog)
    assert report["valid"], report
    assert catalog["components"]["mv"]["available"] is True
    assert catalog["components"]["model3d"]["available"] is False
    assert get_item(catalog, "model3d.render.eye_sheet")["availability"]["available"] is False
    assert get_item(catalog, "workflow.eye_rebuild")["availability"]["available"] is False


def test_model3d_registered_operations_are_merged_with_goal_words(monkeypatch):
    _with_fake_mv(monkeypatch)
    package = ModuleType("model3d")
    package.__path__ = []
    ops = ModuleType("model3d.ops")
    ops.describe = lambda: [
        {"operation": "render.eye_sheet", "inputs": ["blend", "eye_geometry"], "outputs": ["sheet", "report"],
         "summary": "raw summary", "cacheable": False, "selectable": []},
        {"operation": "check.custom_motion", "inputs": ["track"], "outputs": ["report"],
         "summary": "動きの独自検査", "cacheable": True, "selectable": ["track"]},
    ]
    package.ops = ops
    monkeypatch.setitem(sys.modules, "model3d", package)
    monkeypatch.setitem(sys.modules, "model3d.ops", ops)

    catalog = build_catalog()
    eye = get_item(catalog, "model3d.render.eye_sheet")
    custom = get_item(catalog, "model3d.check.custom_motion")
    assert catalog["components"]["model3d"] == {"available": True, "reason": None, "operations": 2}
    assert eye["summary"].startswith("目を姿勢×見る向き")
    assert eye["inputs"] == ["blend", "eye_geometry"]
    assert eye["cacheable"] is False
    assert eye["requires"]["tools"] == ["blender"]
    assert custom["summary"] == "動きの独自検査"
    assert custom["parameters"]["status"] == "undocumented"
    assert custom["requires"]["tools"] == []


def test_goal_search_prefers_workflow_then_specific_tool(monkeypatch):
    _with_fake_mv(monkeypatch)
    _without_model3d(monkeypatch)
    result = search_catalog(build_catalog(), "目の破綻を確認して直したい", limit=5)
    ids = [row["item"]["id"] for row in result["results"]]
    assert ids[0] == "workflow.eye_rebuild"
    assert "model3d.render.eye_sheet" in ids


def test_search_filters_and_parameter_coverage(monkeypatch):
    _with_fake_mv(monkeypatch)
    _without_model3d(monkeypatch)
    catalog = build_catalog()
    result = search_catalog(catalog, "納品用に区間を結合", domain="delivery", available_only=True)
    ids = [row["item"]["id"] for row in result["results"]]
    assert "workflow.delivery" in ids
    assert "mv.deliver.join" in ids
    assert catalog["coverage"]["parameters"]["undocumented"] >= 5


def test_cli_returns_json_and_structured_not_found(monkeypatch, capsys):
    _with_fake_mv(monkeypatch)
    _without_model3d(monkeypatch)
    assert capabilities_cli.main(["--compact", "search", "動画の破綻を探す", "--limit", "2"]) == 0
    success = json.loads(capsys.readouterr().out)
    assert success["ok"] is True
    assert success["result"]["results"][0]["item"]["id"] == "workflow.inspect_and_repair"

    assert capabilities_cli.main(["show", "missing.item"]) == 2
    failure = json.loads(capsys.readouterr().err)
    assert failure == {"ok": False, "error": {"code": "NOT_FOUND",
        "message": "能力または workflow が見つかりません: missing.item",
        "details": {"id": "missing.item"}, "retryable": True}}


def test_cli_argument_errors_are_json(monkeypatch, capsys):
    _with_fake_mv(monkeypatch)
    _without_model3d(monkeypatch)
    assert capabilities_cli.main(["search"]) == 2
    failure = json.loads(capsys.readouterr().err)
    assert failure["ok"] is False
    assert failure["error"]["code"] == "ARGUMENT_ERROR"
    assert failure["error"]["retryable"] is True


def test_boolean_optional_argument_becomes_a_boolean_schema():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--feature", action=argparse.BooleanOptionalAction, default=None)
    action = next(item for item in parser._actions if item.dest == "feature")
    argument = catalog_module._argument(action)
    assert argument["type"] == "bool"
    assert catalog_module._schema([argument])["properties"]["feature"]["type"] == "boolean"


def test_append_argument_becomes_a_repeatable_array_schema():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--region", action="append", default=[])
    action = next(item for item in parser._actions if item.dest == "region")
    schema = catalog_module._schema([catalog_module._argument(action)])
    prop = schema["properties"]["region"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"
    assert prop["x-repeatable"] is True


def test_missing_optional_extra_is_reported(monkeypatch):
    monkeypatch.setattr(catalog_module.importlib_util, "find_spec", lambda name: None)
    availability = catalog_module._availability_for_extras(["fx"])
    assert availability == {"available": False,
                            "reason": "optional extra が現在の Python 環境にありません",
                            "missing_extras": [{"extra": "fx", "import": "cv2"}]}


def test_optional_extras_follow_the_actual_subcommand():
    assert catalog_module._mv_extras(("analyze",)) == ["analysis"]
    assert catalog_module._mv_extras(("audio", "grid")) == ["analysis"]
    assert catalog_module._mv_extras(("audio", "keys")) == []
    assert catalog_module._mv_extras(("audio", "end")) == []
    assert catalog_module._mv_extras(("fx", "legacy")) == []
    assert catalog_module._mv_extras(("fx", "timeline", "plan")) == []
    assert catalog_module._mv_extras(("fx", "timeline", "render")) == ["fx"]
    assert catalog_module._mv_extras(("dance", "plan")) == []
    assert catalog_module._mv_extras(("dance", "render")) == ["fx"]
    assert catalog_module._mv_extras(("gate", "voice")) == ["analysis"]
    assert catalog_module._mv_extras(("gate", "check")) == []
    assert catalog_module._mv_extras(("overlay",)) == ["fx"]


def test_real_mv_parser_exposes_workflow_commands():
    try:
        import mv_pipeline.cli  # noqa: F401
    except ImportError:
        pytest.skip("最小単体試験には既存の mv CLI を置いていない")
    component, capabilities = catalog_module._mv_capabilities()
    assert component["available"] is True
    by_id = {item["id"]: item for item in capabilities}
    expected = {step["capability"] for workflow in WORKFLOWS for step in workflow["steps"]
                if step["capability"].startswith("mv.")}
    assert expected <= set(by_id)
    assert by_id["mv.audio.grid"]["parameters"]["schema"]["type"] == "object"
    assert by_id["mv.workflow.plan"]["parameters"]["schema"]["properties"]["lyrics"]["type"] == "boolean"
    assert validate_catalog(build_catalog())["valid"] is True


def test_real_model3d_registry_exposes_workflow_operations():
    try:
        import model3d.ops  # noqa: F401
    except ImportError:
        pytest.skip("最小単体試験には model3d extra を置いていない")
    component, capabilities = catalog_module._model3d_capabilities()
    if not component["available"]:
        pytest.skip(component["reason"] or "model3d registry を読み込めない")
    by_id = {item["id"]: item for item in capabilities}
    expected = {step["capability"] for workflow in WORKFLOWS for step in workflow["steps"]
                if step["capability"].startswith("model3d.")}
    assert expected <= set(by_id)
