"""能力カタログのJSON Schemaと実環境に対する厳密な参照検査。"""
from __future__ import annotations

from typing import Any

from .capability_catalog import build_catalog, validate_catalog as validate_shape
from .schemas import validation_errors
from .maturity import assessment_reference_errors


def validate_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    """公開schema、未導入、読込み破損、登録済み領域の参照切れを別の状態として検査する。"""
    data = catalog or build_catalog()
    report = validate_shape(data)
    errors = [*report["errors"], *assessment_reference_errors(data)]
    seen = {(error.get("code"), error.get("id"), error.get("component")) for error in errors}

    for failure in validation_errors("catalog", data):
        errors.append({"code": "SCHEMA_VALIDATION", **failure})

    for name, component in data.get("components", {}).items():
        if component.get("state") == "broken":
            error = {"code": "BROKEN_COMPONENT", "component": name, "reason": component.get("reason")}
            key = (error["code"], None, name)
            if key not in seen:
                errors.append(error)
                seen.add(key)

    for item in data.get("capabilities", []):
        availability = item.get("availability", {})
        if availability.get("missing_from_registered_component") is True:
            error = {"code": "UNKNOWN_CAPABILITY", "id": item.get("id"),
                     "component": availability.get("component")}
            key = (error["code"], error["id"], error["component"])
            if key not in seen:
                errors.append(error)
                seen.add(key)

    return {**report, "schema_version": "orpheus-capability-validation/0.4",
            "valid": not errors, "errors": errors}
