"""公開する能力カタログJSON Schemaの読込みと検証。"""
from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator

__all__ = ("SCHEMA_FILES", "names", "load_schema", "validation_errors")

# Keys are the stable names accepted by `orpheus schema NAME`.
SCHEMA_FILES = {
    "catalog": "catalog.schema.json",
    "search": "search.schema.json",
    "response": "response.schema.json",
    "brief": "brief.schema.json",
}


def names() -> tuple[str, ...]:
    return tuple(SCHEMA_FILES)


def load_schema(name: str) -> dict[str, Any]:
    if name not in SCHEMA_FILES:
        raise KeyError(name)
    resource = files(__package__).joinpath(SCHEMA_FILES[name])
    return json.loads(resource.read_text(encoding="utf-8"))


def validation_errors(name: str, instance: Any) -> list[dict[str, Any]]:
    validator = Draft202012Validator(load_schema(name))
    errors = sorted(validator.iter_errors(instance), key=lambda error: list(error.absolute_path))
    return [{"path": list(error.absolute_path), "message": error.message,
             "validator": error.validator} for error in errors]
