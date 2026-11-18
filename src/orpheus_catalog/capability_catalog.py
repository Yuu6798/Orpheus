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
VALID_EFFECT_MODES.add("unknown")
EXTRA_IMPORTS = _impl.EXTRA_IMPORTS
MV_OVERRIDES = _impl.MV_OVERRIDES
MV_RUNTIME = _impl.MV_RUNTIME
MV_RUNTIME.update({
    ("demo",): {"external_process": "always", "tools": ["ffmpeg"],
                "external_process_note": "合成素材の音源を正規化してprojectを初期化する"},
    ("render",): {"external_process": "always", "tools": ["node", "remotion", "ffmpeg", "ffprobe"]},
    ("preview",): {"external_process": "always", "tools": ["node", "remotion", "browser", "ffmpeg", "ffprobe"],
                   "external_process_note": "--studioではRemotion Studioとbrowser、通常previewでは描画とFFmpeg/FFprobe検証を使う"},
    ("resume",): {"external_process": "opt-in", "tools": ["node", "remotion", "ffmpeg", "ffprobe"],
                  "external_process_note": "必要入力が揃い、同一renderのcacheが無いか無効な場合に描画と検証を再開する"},
    ("audio", "end"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("audio", "lows"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("dance", "demo"): {"external_process": "always", "tools": ["ffmpeg"],
                         "external_process_note": "合成の人物動画を書き出す"},
    ("inspect", "demo"): {"external_process": "always", "tools": ["ffmpeg"],
                           "external_process_note": "既知の異常を入れた合成動画を書き出す"},
    ("gate", "check"): {"external_process": "opt-in", "tools": ["ffmpeg"],
                         "external_process_note": "gate設定にvideo・master・flash・tailなど動画検査の材料がある場合に起動する"},
    ("gate", "tail"): {"external_process": "always", "tools": ["ffmpeg"]},
})
MODEL3D_OVERRIDES = _impl.MODEL3D_OVERRIDES
MODEL3D_OVERRIDES.setdefault("blender.run_script", {
    "tools": ["blender"], "network_mode": "recipe-defined", "external_process_mode": "always",
    "network_note": "指定したBlender Python scriptと有効化するadd-onの実装次第",
    "external_process_note": "指定したBlenderをbackgroundまたは対話モードで毎回起動する",
})
MODEL3D_OVERRIDES.setdefault("render.assemble", {
    "tools": ["ffmpeg"], "network_mode": "never", "external_process_mode": "always",
    "external_process_note": "PNG連番と任意の音声をH.264/AACへまとめ、出力をFFmpegで読み戻す",
})
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


def _unknown_effects(item, *, domain):
    """明示metadataの無い実行可能な項目を、副作用なしと推測せずunknownにする。"""
    requires = item["requires"]
    if (not requires["tools"] and requires["network"]["mode"] == "never"
            and requires["external_process"]["mode"] == "never"):
        requires["network"] = {"mode": "unknown", "note": f"この{domain}は通信可否のmetadataをまだ公開していません"}
        requires["external_process"] = {"mode": "unknown", "note": f"この{domain}は外部process起動のmetadataをまだ公開していません"}


def _mv_capabilities():
    component, capabilities = _call_collector(_ORIGINAL_MV_CAPABILITIES)
    for item in capabilities:
        if item["kind"] == "command-group":
            continue
        path = tuple(item["entrypoint"]["argv"][1:])
        if path not in MV_RUNTIME:
            _unknown_effects(item, domain="command")
    return component, capabilities


def _model3d_capabilities():
    component, capabilities = _call_collector(_ORIGINAL_MODEL3D_CAPABILITIES)
    # 実行効果を公開していない操作を「何もしない」とは扱わない。既知の純粋操作や
    # 明示metadata/overrideだけを確定し、残りはunknownとして利用者に確認を促す。
    for item in capabilities:
        if item["id"] == "model3d.run":
            continue
        name = item["id"].removeprefix("model3d.")
        if name not in MODEL3D_OVERRIDES:
            _unknown_effects(item, domain="operation")
    return component, capabilities


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
