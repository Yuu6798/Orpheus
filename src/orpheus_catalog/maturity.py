"""記録済みの制作検証に基づく成熟度。実行や作品の採否は変更しない。"""
from __future__ import annotations

import copy
import json
import re
from datetime import date
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

from .schemas import load_schema

VALID_MATURITY = {"stable", "verified", "experimental", "external"}
ASSESSMENT_SCHEMA = "orpheus-maturity-assessments/0.1"
_ID = re.compile(r"^(mv|model3d|workflow)\.[A-Za-z0-9_.-]+$")
_REVIEW_FIELDS = ("reviewed_on", "review_basis", "next_review")


class MaturityAssessmentError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]):
        super().__init__("成熟度の評価記録が不正です")
        self.errors = errors


def load_assessments() -> dict[str, Any]:
    """配布物に同梱した評価記録を読む。素材・外部サービスにはアクセスしない。"""
    resource = files(__package__).joinpath("maturity_assessments.json")
    try:
        return json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise MaturityAssessmentError([{"code": "UNREADABLE_MATURITY_ASSESSMENTS",
                                        "reason": str(error)}]) from error


def assessment_index(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """完全一致のID索引。重複・根拠なしの昇格を上書きや推測で通さない。"""
    errors: list[dict[str, Any]] = []
    if not isinstance(document, dict) or document.get("schema_version") != ASSESSMENT_SCHEMA:
        raise MaturityAssessmentError([{"code": "BAD_MATURITY_ASSESSMENT_SCHEMA"}])
    records = document.get("assessments")
    if not isinstance(records, list):
        raise MaturityAssessmentError([{"code": "BAD_MATURITY_ASSESSMENTS"}])
    try:
        reviewed_on = document.get("reviewed_on")
        if not isinstance(reviewed_on, str) or date.fromisoformat(reviewed_on).isoformat() != reviewed_on:
            raise ValueError("expected YYYY-MM-DD")
    except ValueError:
        errors.append({"code": "BAD_MATURITY_REVIEW_DATE"})
    for key in ("review_basis", "next_review"):
        if not isinstance(document.get(key), str) or not document[key].strip():
            errors.append({"code": "BAD_MATURITY_REVIEW", "field": key})
    schema = load_schema("catalog")["$defs"]["maturityAssessment"]
    validator = Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER)
    index: dict[str, dict[str, Any]] = {}
    for position, record in enumerate(records):
        if not isinstance(record, dict):
            errors.append({"code": "BAD_MATURITY_ASSESSMENT", "record": position})
            continue
        identifiers = record.get("ids")
        if not isinstance(identifiers, list) or not identifiers:
            errors.append({"code": "BAD_MATURITY_IDS", "record": position})
            continue
        maturity = record.get("maturity")
        if not isinstance(maturity, str) or maturity not in VALID_MATURITY:
            errors.append({"code": "BAD_MATURITY", "record": position, "maturity": maturity})
        detail = {**{key: document.get(key) for key in _REVIEW_FIELDS},
                  **{key: value for key, value in record.items() if key not in {"ids", "maturity"}}}
        for failure in validator.iter_errors(detail):
            errors.append({"code": "BAD_MATURITY_ASSESSMENT", "record": position,
                           "path": list(failure.absolute_path), "message": failure.message})
        if maturity == "verified" and not detail.get("evidence"):
            errors.append({"code": "MISSING_MATURITY_EVIDENCE", "record": position})
        for identifier in identifiers:
            if not isinstance(identifier, str) or not _ID.fullmatch(identifier):
                errors.append({"code": "BAD_MATURITY_ID", "record": position, "id": identifier})
                continue
            if identifier in index:
                errors.append({"code": "DUPLICATE_MATURITY_ID", "id": identifier})
                continue
            index[identifier] = {"maturity": maturity, "maturity_assessment": detail}
    if errors:
        raise MaturityAssessmentError(errors)
    return index


def apply_assessments(capabilities: list[dict[str, Any]], workflows: list[dict[str, Any]],
                      document: dict[str, Any] | None = None) -> None:
    """確認したIDだけを更新する。親・兄弟・workflowへの伝播は行わない。"""
    index = assessment_index(load_assessments() if document is None else document)
    unrecorded = [{"code": "UNRECORDED_VERIFIED_CAPABILITY", "id": item["id"]}
                  for item in [*capabilities, *workflows]
                  if item.get("maturity") == "verified" and item["id"] not in index]
    if unrecorded:
        raise MaturityAssessmentError(unrecorded)
    for item in [*capabilities, *workflows]:
        assessment = index.get(item["id"])
        if assessment is not None:
            item.update(copy.deepcopy(assessment))


def assessment_reference_errors(catalog: dict[str, Any], document: dict[str, Any] | None = None
                                ) -> list[dict[str, Any]]:
    """利用可能な登録表に存在しない評価先と、評価を伴わないverifiedを検出する。"""
    try:
        index = assessment_index(load_assessments() if document is None else document)
    except MaturityAssessmentError as error:
        return error.errors
    errors = []
    items = {item["id"]: item for item in [*catalog.get("capabilities", []),
                                         *catalog.get("workflows", [])] if isinstance(item.get("id"), str)}
    for identifier, assessment in index.items():
        component = identifier.split(".", 1)[0]
        item = items.get(identifier)
        if item is None:
            # optional componentの未導入/破損から、登録済みIDの不存在は判断しない。
            if component == "workflow" or catalog.get("components", {}).get(component, {}).get("state") == "available":
                errors.append({"code": "UNKNOWN_MATURITY_CAPABILITY", "id": identifier})
        elif any(item.get(key) != value for key, value in assessment.items()):
            errors.append({"code": "MATURITY_ASSESSMENT_MISMATCH", "id": identifier})
    for identifier, item in items.items():
        if item.get("maturity") == "verified" and identifier not in index:
            errors.append({"code": "UNRECORDED_VERIFIED_CAPABILITY", "id": identifier})
    return errors
