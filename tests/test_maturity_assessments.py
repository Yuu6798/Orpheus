"""実績のある操作だけが昇格し、根拠や利用可否を失わないことを確かめる。"""
import copy
import json
from pathlib import Path

import pytest

from orpheus_catalog import capabilities_cli, capability_catalog, maturity
from orpheus_catalog.capability_catalog import build_catalog, get_item, search_catalog
from orpheus_catalog.capability_validation import validate_catalog
from orpheus_catalog.schemas import validation_errors


def _record(*identifiers, status="verified"):
    return {
        "ids": list(identifiers), "maturity": status,
        "reason": "実制作との照合記録を確認した", "scope": "記録にある入力と設定の範囲",
        "evidence": [{"path": "docs/deliver.md", "section": "実制作との比較",
                      "summary": "対象動画で既存出力との一致を確認した"}],
        "limitations": ["全環境を保証しない"], "promotion_criteria": ["互換性の扱いを確定する"],
    }


def _document(*records):
    return {
        "schema_version": maturity.ASSESSMENT_SCHEMA,
        "reviewed_on": "2026-11-20", "review_basis": "既存記録の監査。実素材再実行ではない",
        "next_review": "次の実制作完了時、または対象機能の挙動変更時",
        "assessments": list(records),
    }


def _use_document(monkeypatch, document):
    monkeypatch.setattr(maturity, "load_assessments", lambda: copy.deepcopy(document))


def test_assessments_only_change_exact_ids_and_do_not_change_acceptance():
    capabilities = [
        {"id": "mv.deliver", "maturity": "experimental"},
        {"id": "mv.deliver.join", "maturity": "experimental", "accepted": False,
         "availability": {"available": False}, "technical_pass": False},
        {"id": "mv.deliver.new-command", "maturity": "experimental"},
    ]
    workflows = [{"id": "workflow.delivery", "maturity": "experimental"}]
    document = _document(_record("mv.deliver.join"))
    original = copy.deepcopy(capabilities[1])
    maturity.apply_assessments(capabilities, workflows, document)

    assert [item["maturity"] for item in capabilities] == ["experimental", "verified", "experimental"]
    assert workflows[0]["maturity"] == "experimental"
    assert capabilities[1]["maturity_assessment"]["reviewed_on"] == "2026-11-20"
    for key in ("accepted", "availability", "technical_pass"):
        assert capabilities[1][key] == original[key]
    capabilities[1]["maturity_assessment"]["limitations"].append("caller annotation")
    assert document["assessments"][0]["limitations"] == ["全環境を保証しない"]


def test_verified_is_visible_in_show_search_and_filters_with_public_schema(monkeypatch, capsys):
    _use_document(monkeypatch, _document(_record("mv.deliver.join", "workflow.delivery")))
    catalog = build_catalog()
    assert catalog["schema_version"] == "orpheus-capability-catalog/0.4"
    assert validation_errors("catalog", catalog) == []
    search = search_catalog(catalog, "区間を結合", maturity="verified")
    assert search["schema_version"] == "orpheus-capability-search/0.4"
    assert validation_errors("search", search) == []
    assert {row["item"]["id"] for row in search["results"]} == {"mv.deliver.join", "workflow.delivery"}

    assert capabilities_cli.main(["show", "mv.deliver.join"]) == 0
    item = json.loads(capsys.readouterr().out)["result"]
    assert item["maturity"] == "verified"
    assert item["maturity_assessment"]["evidence"][0]["path"] == "docs/deliver.md"
    assert capabilities_cli.main(["capabilities", "--maturity", "verified"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert [item["id"] for item in result["capabilities"]] == ["mv.deliver.join"]
    assert capabilities_cli.main(["workflows", "--maturity", "verified"]) == 0
    result = json.loads(capsys.readouterr().out)["result"]
    assert [item["id"] for item in result["workflows"]] == ["workflow.delivery"]


def test_missing_component_keeps_documented_maturity_and_unavailable_state(monkeypatch):
    component = {"available": False, "state": "missing", "reason": "not installed", "operations": 0}
    monkeypatch.setattr(capability_catalog, "_model3d_capabilities", lambda: (component, []))
    _use_document(monkeypatch, _document(_record("model3d.render.eye_sheet")))
    catalog = build_catalog()
    item = get_item(catalog, "model3d.render.eye_sheet")
    assert item["maturity"] == "verified"
    assert item["availability"]["available"] is False
    assert get_item(catalog, "workflow.eye_rebuild")["maturity"] == "experimental"
    assert validate_catalog(catalog)["valid"] is True


def test_search_schema_requires_verified_evidence_and_known_maturity(monkeypatch):
    _use_document(monkeypatch, _document(_record("mv.deliver.join")))
    result = search_catalog(build_catalog(), "区間を結合", maturity="verified")
    assert validation_errors("search", result) == []
    without_assessment = copy.deepcopy(result)
    del without_assessment["results"][0]["item"]["maturity_assessment"]
    assert validation_errors("search", without_assessment)
    without_evidence = copy.deepcopy(result)
    without_evidence["results"][0]["item"]["maturity_assessment"]["evidence"] = []
    assert validation_errors("search", without_evidence)
    unknown_status = copy.deepcopy(result)
    unknown_status["results"][0]["item"]["maturity"] = "self-approved"
    assert validation_errors("search", unknown_status)


def test_search_and_catalog_share_the_same_assessment_contract():
    from orpheus_catalog.schemas import load_schema

    catalog, search = load_schema("catalog"), load_schema("search")
    for name in ("maturityAssessment", "verifiedAssessmentRequirement"):
        assert search["$defs"][name] == catalog["$defs"][name]


@pytest.mark.parametrize("document,code", [
    (_document(_record("mv.deliver.join"), _record("mv.deliver.join")), "DUPLICATE_MATURITY_ID"),
    (_document(_record("mv.deliver.join", "mv.deliver.join")), "DUPLICATE_MATURITY_ID"),
    (_document({**_record("mv.deliver.join"), "evidence": []}), "MISSING_MATURITY_EVIDENCE"),
    (_document({**_record("mv.deliver.join"), "maturity": []}), "BAD_MATURITY"),
    ({**_document(), "reviewed_on": "2026-02-30"}, "BAD_MATURITY_REVIEW_DATE"),
    ({**_document(), "next_review": ""}, "BAD_MATURITY_REVIEW"),
])
def test_invalid_records_fail_instead_of_silently_promoting(document, code):
    with pytest.raises(maturity.MaturityAssessmentError) as caught:
        maturity.assessment_index(document)
    assert code in {error["code"] for error in caught.value.errors}


def test_cli_reports_duplicate_assessments_as_a_structured_error(monkeypatch, capsys):
    _use_document(monkeypatch, _document(_record("mv.deliver.join"), _record("mv.deliver.join")))
    assert capabilities_cli.main(["validate"]) == 2
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "INVALID_MATURITY_ASSESSMENTS"
    assert error["retryable"] is False


@pytest.mark.parametrize("arguments", [
    ["show", "model3d.new.unreviewed"],
    ["search", "new"],
    ["capabilities", "--maturity", "verified"],
])
def test_normal_cli_rejects_unrecorded_verified_metadata(monkeypatch, capsys, arguments):
    _use_document(monkeypatch, _document())
    component = {"available": True, "state": "available", "reason": None, "operations": 1}
    operation = {"id": "model3d.new.unreviewed", "kind": "operation", "domain": "model3d",
                 "maturity": "verified", "parameters": {"status": "undocumented", "schema": None}}
    monkeypatch.setattr(capability_catalog, "_model3d_capabilities", lambda: (component, [operation]))
    assert capabilities_cli.main(arguments) == 2
    error = json.loads(capsys.readouterr().err)["error"]
    assert error["code"] == "INVALID_MATURITY_ASSESSMENTS"
    assert any(failure["code"] == "UNRECORDED_VERIFIED_CAPABILITY" for failure in error["details"]["errors"])


def test_validation_rejects_unknown_ids_but_defers_absent_optional_components(monkeypatch):
    _use_document(monkeypatch, _document(_record("mv.deliver.does-not-exist")))
    report = validate_catalog(build_catalog())
    assert report["valid"] is False
    assert {"code": "UNKNOWN_MATURITY_CAPABILITY", "id": "mv.deliver.does-not-exist"} in report["errors"]

    _use_document(monkeypatch, _document(_record("model3d.only-in-optional-registry")))
    component = {"available": False, "state": "missing", "reason": "not installed", "operations": 0}
    monkeypatch.setattr(capability_catalog, "_model3d_capabilities", lambda: (component, []))
    assert validate_catalog(build_catalog())["valid"] is True


def test_verified_requires_recorded_evidence_and_must_match_the_registry(monkeypatch):
    _use_document(monkeypatch, _document(_record("mv.deliver.join")))
    catalog = build_catalog()
    item = get_item(catalog, "mv.deliver.join")
    item["maturity_assessment"]["evidence"] = []
    assert validation_errors("catalog", catalog)
    report = validate_catalog(catalog)
    assert {"code": "MATURITY_ASSESSMENT_MISMATCH", "id": "mv.deliver.join"} in report["errors"]

    catalog = build_catalog()
    unreviewed = get_item(catalog, "mv.deliver.split")
    unreviewed["maturity"] = "verified"
    assert validation_errors("catalog", catalog)
    report = validate_catalog(catalog)
    assert {"code": "UNRECORDED_VERIFIED_CAPABILITY", "id": "mv.deliver.split"} in report["errors"]


def test_packaged_assessments_reference_existing_evidence_and_registered_ids():
    document = maturity.load_assessments()
    index = maturity.assessment_index(document)
    assert index, "制作検証の評価記録が空です"
    root = Path(__file__).resolve().parents[1]
    for identifier, record in index.items():
        for evidence in record["maturity_assessment"]["evidence"]:
            source = (root / evidence["path"]).resolve()
            assert source.is_relative_to(root), (identifier, evidence)
            assert source.is_file(), (identifier, evidence)
            assert evidence["section"] in source.read_text(encoding="utf-8"), (identifier, evidence)
    catalog = build_catalog()
    assert maturity.assessment_reference_errors(catalog) == []
    assert validate_catalog(catalog)["valid"] is True
