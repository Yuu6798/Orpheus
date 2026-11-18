"""能力カタログの公開入口。

実装本体は ``_capability_catalog_impl`` に置き、この入口で optional component の
未導入と、導入済みパッケージの破損を厳密に区別する。テストや既存利用者が
差し替えてきた内部フックも、この入口を通して実装本体へ渡す。
"""
from __future__ import annotations

from typing import Any

from . import _capability_catalog_impl as _impl

CatalogError = _impl.CatalogError
CATALOG_SCHEMA = _impl.CATALOG_SCHEMA
SEARCH_SCHEMA = _impl.SEARCH_SCHEMA
VALID_MATURITY = _impl.VALID_MATURITY
VALID_PARAMETER_STATUS = _impl.VALID_PARAMETER_STATUS
VALID_COMPONENT_STATES = _impl.VALID_COMPONENT_STATES
VALID_EFFECT_MODES = _impl.VALID_EFFECT_MODES
EXTRA_IMPORTS = _impl.EXTRA_IMPORTS
MV_OVERRIDES = _impl.MV_OVERRIDES
MV_RUNTIME = _impl.MV_RUNTIME
MODEL3D_OVERRIDES = _impl.MODEL3D_OVERRIDES
importlib = _impl.importlib
importlib_util = _impl.importlib_util

# テスト・拡張で使ってきた補助関数も互換のため公開する。
_normal = _impl._normal
_json_value = _impl._json_value
_effect = _impl._effect
_requirements = _impl._requirements
_mode = _impl._mode
_argument = _impl._argument
_schema = _impl._schema
_walk_parser = _impl._walk_parser
_mv_extras = _impl._mv_extras
_availability_for_extras = _impl._availability_for_extras
_placeholder = _impl._placeholder
_workflow_availability = _impl._workflow_availability
_ngrams = _impl._ngrams
_score = _impl._score

_ORIGINAL_MV_CAPABILITIES = _impl._mv_capabilities
_ORIGINAL_MODEL3D_CAPABILITIES = _impl._model3d_capabilities


def _component_failure(error: Exception, missing_modules: set[str]) -> tuple[str, str]:
    """トップレベルのoptional package自体が無いときだけ ``missing`` にする。

    ``model3d`` はあるが ``model3d.ops`` が無い、または依存moduleが無い場合は、
    導入済みcomponentが不完全なので ``broken`` とする。
    """
    roots = {name.split(".", 1)[0] for name in missing_modules}
    missing = isinstance(error, ModuleNotFoundError) and error.name in roots
    return ("missing" if missing else "broken"), f"{type(error).__name__}: {error}"


def _call_collector(function):
    previous = _impl._component_failure
    _impl._component_failure = _component_failure
    try:
        return function()
    finally:
        _impl._component_failure = previous


def _mv_capabilities():
    return _call_collector(_ORIGINAL_MV_CAPABILITIES)


def _model3d_capabilities():
    return _call_collector(_ORIGINAL_MODEL3D_CAPABILITIES)


def build_catalog() -> dict[str, Any]:
    previous_mv, previous_model3d = _impl._mv_capabilities, _impl._model3d_capabilities
    _impl._mv_capabilities, _impl._model3d_capabilities = _mv_capabilities, _model3d_capabilities
    try:
        return _impl.build_catalog()
    finally:
        _impl._mv_capabilities, _impl._model3d_capabilities = previous_mv, previous_model3d


def validate_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    return _impl.validate_catalog(catalog if catalog is not None else build_catalog())


filter_items = _impl.filter_items
get_item = _impl.get_item
search_catalog = _impl.search_catalog

__all__ = [
    "CatalogError", "CATALOG_SCHEMA", "SEARCH_SCHEMA", "VALID_MATURITY",
    "VALID_PARAMETER_STATUS", "VALID_COMPONENT_STATES", "VALID_EFFECT_MODES",
    "MV_OVERRIDES", "MV_RUNTIME", "MODEL3D_OVERRIDES", "build_catalog",
    "validate_catalog", "filter_items", "get_item", "search_catalog",
]
