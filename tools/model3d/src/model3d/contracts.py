"""データ契約（schemas/）の読み込みと検証。未知の鍵は拒否し、`x_` で始まる鍵はどの階層でも許す。

これらは自作ツールの制作データの契約で、Orpheus の `contracts/`（analysis / storyboard / assets）とは別物。
"""
from functools import lru_cache

from jsonschema import Draft202012Validator

from . import PACKAGE_DIR, SCHEMAS
from .io import ToolError, read_json

FILES = {
    "mesh": "mesh.schema.json",
    "asset-manifest": "asset_manifest.schema.json",
    "semantic-map": "semantic_map.schema.json",
    "correspondence": "correspondence.schema.json",
    "recipe": "recipe.schema.json",
    "observation": "observation3d.schema.json",
    "run": "run_record.schema.json",
    "rig": "rig.schema.json",
    "preset": "preset.schema.json",
    "handoff": "handoff.schema.json",
}

KINDS = tuple(FILES)


@lru_cache(maxsize=None)
def schema(kind):
    if kind not in FILES:
        raise ToolError(f"知らない契約です: {kind}（{', '.join(KINDS)} から選んでください）")
    data = read_json(PACKAGE_DIR / "schemas" / FILES[kind])
    Draft202012Validator.check_schema(data)
    return data


@lru_cache(maxsize=None)
def _validator(kind):
    return Draft202012Validator(schema(kind))


def describe(error):
    path = "/".join(str(p) for p in error.absolute_path) or "(root)"
    message = error.message
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        allowed = set(error.schema.get("properties", {}))
        unknown = sorted(k for k in error.instance if k not in allowed and not k.startswith("x_"))
        message = f"知らない鍵があります: {', '.join(unknown)}"
    return f"{path}: {message[:300]}"


def errors(kind, data):
    return sorted(_validator(kind).iter_errors(data), key=lambda e: [str(p) for p in e.absolute_path])


def validate(kind, data, where=None):
    """契約 kind に合わなければ ToolError（最初の 5 件を並べる）。合えば data をそのまま返す。"""
    found = errors(kind, data)
    if found:
        label = f"{kind}（{where}）" if where else kind
        raise ToolError(f"{label} の形式が違います: " + "; ".join(describe(e) for e in found[:5]))
    return data


def load(kind, path):
    return validate(kind, read_json(path), path)


def kind_of(data):
    """schema_version から契約名を引く（分からなければ None）。"""
    version = data.get("schema_version") if isinstance(data, dict) else None
    for kind, name in SCHEMAS.items():
        if name == version:
            return kind
    return None
