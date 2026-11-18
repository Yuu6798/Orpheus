"""能力カタログの公開入口。

実装本体は ``_capability_catalog_impl`` に置き、この入口で optional component の
未導入と、導入済みパッケージの破損を厳密に区別する。テストや既存利用者が
差し替えてきた内部フックも、この入口を通して実装本体へ渡す。
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from . import _capability_catalog_impl as _impl
from .capability_workflows import WORKFLOWS

CatalogError = _impl.CatalogError
CATALOG_SCHEMA = _impl.CATALOG_SCHEMA
SEARCH_SCHEMA = _impl.SEARCH_SCHEMA
VALID_MATURITY = _impl.VALID_MATURITY
VALID_PARAMETER_STATUS = _impl.VALID_PARAMETER_STATUS
VALID_COMPONENT_STATES = _impl.VALID_COMPONENT_STATES
VALID_EFFECT_MODES = _impl.VALID_EFFECT_MODES
EXTRA_IMPORTS = _impl.EXTRA_IMPORTS
MACHINE_READABLE_PARAMETER_STATUS = _impl.MACHINE_READABLE_PARAMETER_STATUS
TOOL_RESOLUTION = _impl.TOOL_RESOLUTION
MV_OVERRIDES = _impl.MV_OVERRIDES
MV_RUNTIME = _impl.MV_RUNTIME
MV_RUNTIME.update({
    ("demo",): {"external_process": "always", "tools": ["ffmpeg"],
                "external_process_note": "合成素材の音源を正規化してprojectを初期化する"},
    ("render",): {"network": "environment-dependent", "external_process": "always",
                  "tools": ["node", "remotion", "browser", "ffmpeg", "ffprobe"],
                  "network_note": "Chrome Headless Shellが未取得でORPHEUS_BROWSERも未指定の初回はRemotionがブラウザーを取得し得る",
                  "external_process_note": "Node/Remotionで描画し、FFmpeg/FFprobeで動画と音声を検証する"},
    ("preview",): {"network": "environment-dependent", "external_process": "always",
                   "tools": ["node", "remotion", "browser", "ffmpeg", "ffprobe"],
                   "network_note": "管理ブラウザーが未取得でORPHEUS_BROWSERも未指定の初回はRemotionがブラウザーを取得し得る",
                   "external_process_note": "--studioではRemotion Studioとbrowser、通常previewでは描画とFFmpeg/FFprobe検証を使う"},
    ("resume",): {"network": "environment-dependent", "external_process": "opt-in",
                  "tools": ["node", "remotion", "browser", "ffmpeg", "ffprobe"],
                  "network_note": "再描画が必要で管理ブラウザーが未取得の場合はRemotionがブラウザーを取得し得る",
                  "external_process_note": "必要入力が揃い、同一renderのcacheが無いか無効な場合に描画と検証を再開する"},
    ("run-adapter",): {"network": "recipe-defined", "external_process": "always",
                       "tools": ["configured-adapter", "ffprobe"],
                       "network_note": "argvで指定した同期器・分離器の実装と設定次第。未キャッシュ重みを取得する実装もあり得る",
                       "external_process_note": "外部adapterを起動し、separationでは出力音声をFFprobeで確認する"},
    ("workflow", "check"): {"external_process": "opt-in", "tools": ["ffmpeg"],
                            "external_process_note": "gate設定など動画検査の材料を渡した場合にFFmpegを使う"},
    ("audio", "end"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("audio", "lows"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("dance", "demo"): {"external_process": "always", "tools": ["ffmpeg"],
                         "external_process_note": "合成の人物動画を書き出す"},
    ("inspect", "demo"): {"external_process": "always", "tools": ["ffmpeg"],
                           "external_process_note": "既知の異常を入れた合成動画を書き出す"},
    ("gate", "check"): {"external_process": "opt-in", "tools": ["ffmpeg"],
                         "external_process_note": "gate設定にvideo・master・flash・tailなど動画検査の材料がある場合に起動する"},
    ("gate", "tail"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("sheet",): {"external_process": "opt-in", "tools": ["ffmpeg"],
                 "external_process_note": "動画入力または並べた動画の出力でFFmpegを使う"},
    ("deliver", "join"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("deliver", "review-copy"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("deliver", "split"): {"external_process": "always", "tools": ["ffmpeg"]},
    ("layer", "render"): {"network": "environment-dependent", "external_process": "always",
                          "tools": ["node", "remotion", "browser"],
                          "network_note": "管理ブラウザーが未取得でORPHEUS_BROWSERも未指定の初回はRemotionがブラウザーを取得し得る"},
})
MODEL3D_OVERRIDES = _impl.MODEL3D_OVERRIDES
MODEL3D_OVERRIDES["check.eyes"].update(network_mode="never", external_process_mode="never")
for _name in ("model.eye_rig", "model.eye_keys", "vrm.look_at", "render.eye_sheet"):
    MODEL3D_OVERRIDES[_name].update(network_mode="never", external_process_mode="always")
MODEL3D_OVERRIDES.setdefault("blender.run_script", {
    "tools": ["blender"], "network_mode": "recipe-defined", "external_process_mode": "always",
    "network_note": "指定したBlender Python scriptと有効化するadd-onの実装次第",
    "external_process_note": "指定したBlenderをbackgroundまたは対話モードで毎回起動する",
})
MODEL3D_OVERRIDES.setdefault("render.assemble", {
    "tools": ["ffmpeg"],
    "tool_requirements": [{
        "name": "ffmpeg", "resolution": "parameter-or-context-or-bundled-or-path",
        "parameter": "ffmpeg", "context_tool": "ffmpeg", "required_on_path": False,
        "bundled_by": "imageio-ffmpeg",
    }],
    "network_mode": "never", "external_process_mode": "always",
    "external_process_note": "PNG連番と任意の音声をH.264/AACへまとめ、出力をFFmpegで読み戻す",
})
importlib = _impl.importlib
importlib_util = _impl.importlib_util

# テスト・拡張で使ってきた補助関数も互換のため公開する。
_normal = _impl._normal
_json_value = _impl._json_value
_effect = _impl._effect
_tool_requirement = _impl._tool_requirement
_requirements = _impl._requirements
_mode = _impl._mode
_argument = _impl._argument
_schema = _impl._schema
_walk_parser = _impl._walk_parser
_mv_extras = _impl._mv_extras
_availability_for_extras = _impl._availability_for_extras
_placeholder = _impl._placeholder
_workflow_availability = _impl._workflow_availability
_workflow_parameter_readiness = _impl._workflow_parameter_readiness
_parameter_statuses = _impl._parameter_statuses
_ngrams = _impl._ngrams
_score = _impl._score

_ORIGINAL_MV_CAPABILITIES = _impl._mv_capabilities


def _component_failure(error: Exception, missing_modules: set[str]) -> tuple[str, str]:
    """トップレベルのoptional package自体が無いときだけ ``missing`` にする。

    ``model3d`` はあるが ``model3d.ops`` が無い、または依存moduleが無い場合は、
    導入済みcomponentが不完全なので ``broken`` とする。
    """
    roots = {name.split(".", 1)[0] for name in missing_modules}
    missing = isinstance(error, ModuleNotFoundError) and error.name in roots
    return ("missing" if missing else "broken"), f"{type(error).__name__}: {error}"


# private実装はこの公開入口専用なので、一時差し替えではなく安定した判定関数を設定する。
# これにより複数threadから同時にカタログを作っても、復元順で判定が変わらない。
_impl._component_failure = _component_failure


def _unknown_effects(item, *, domain):
    """明示metadataの無い実行可能な項目を、副作用なしと推測せずunknownにする。"""
    requires = item["requires"]
    if not requires["tools"] and requires["network"]["mode"] == "never":
        requires["network"] = {"mode": "unknown", "note": f"この{domain}は通信可否のmetadataをまだ公開していません"}
    if not requires["tools"] and requires["external_process"]["mode"] == "never":
        requires["external_process"] = {"mode": "unknown", "note": f"この{domain}は外部process起動のmetadataをまだ公開していません"}


def _mv_capabilities():
    component, capabilities = _ORIGINAL_MV_CAPABILITIES()
    for item in capabilities:
        if item["kind"] == "command-group":
            continue
        path = tuple(item["entrypoint"]["argv"][1:])
        if path not in MV_RUNTIME:
            _unknown_effects(item, domain="command")
    return component, capabilities


def _declared(mapping, *keys):
    return any(key in mapping for key in keys)


def _model3d_capabilities() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """model3d登録表を1回読み、効果ごとの明示宣言だけを確定値として扱う。"""
    component = {"available": False, "state": "missing", "reason": None, "operations": 0}
    try:
        ops = importlib.import_module("model3d.ops")
        descriptions = ops.describe()
    except Exception as error:
        state, reason = _component_failure(error, {"model3d", "model3d.ops"})
        component.update(state=state, reason=reason)
        return component, []
    capabilities = [{
        "id": "model3d.run", "kind": "command", "domain": "model3d", "maturity": "experimental",
        "summary": "RecipeGraphを依存順に実行し、生成物・観測・未解決・再利用元をRunRecordへ残す。",
        "aliases": ["3Dレシピ", "3Dモデルを加工", "VRM", "Blender"], "tags": ["recipe", "run"],
        "entrypoint": {"argv": ["model3d", "run", "RECIPE"], "docs": "docs/model3d.md"},
        "inputs": ["recipe"], "outputs": ["RunRecord", "工程ごとの生成物"],
        "parameters": {"status": "schema", "schema": {"$ref": "tools/model3d/src/model3d/schemas/recipe.schema.json"}},
        "requires": _requirements(["model3d"], ["recipe-defined"], network="recipe-defined",
                                  external_process="recipe-defined",
                                  network_note="recipe内のexternal.runなど、選んだ操作と設定次第",
                                  external_process_note="recipe内の操作がBlenderや任意CLIを起動する場合がある"),
        "availability": {"available": True, "reason": None},
    }]
    for raw in descriptions:
        name = raw["operation"]
        metadata = raw.get("capability") if isinstance(raw.get("capability"), dict) else {}
        override = MODEL3D_OVERRIDES.get(name, {})
        parameters = (metadata.get("parameters") if isinstance(metadata.get("parameters"), dict)
                      else {"status": "undocumented", "schema": None})
        tools = list(metadata.get("tools") or override.get("tools") or [])
        network_declared = _declared(metadata, "network", "network_mode") or _declared(override, "network", "network_mode")
        external_declared = (_declared(metadata, "external_process", "external_process_mode", "tools")
                             or _declared(override, "external_process", "external_process_mode", "tools"))
        network_value = metadata.get("network_mode", metadata.get("network", override.get("network_mode", override.get("network"))))
        external_value = metadata.get("external_process_mode", metadata.get("external_process",
                                      override.get("external_process_mode", override.get("external_process"))))
        network = _mode(network_value) if network_declared else "unknown"
        if _declared(metadata, "external_process", "external_process_mode") or _declared(override, "external_process", "external_process_mode"):
            external = _mode(external_value)
        elif external_declared and tools:
            external = "always"
        else:
            external = "unknown"
        requirements = _requirements(["model3d"], tools, network=network, external_process=external,
                                     network_note=metadata.get("network_note") or override.get("network_note"),
                                     external_process_note=metadata.get("external_process_note") or override.get("external_process_note"))
        declared_tools = metadata.get("tool_requirements") or override.get("tool_requirements")
        if declared_tools is not None:
            requirements["tool_requirements"] = copy.deepcopy(declared_tools)
        capabilities.append({
            "id": f"model3d.{name}", "kind": "operation", "domain": "model3d",
            "maturity": metadata.get("maturity", "experimental"),
            "summary": override.get("summary") or metadata.get("summary") or raw.get("summary") or name,
            "aliases": list(dict.fromkeys([*(metadata.get("aliases") or []), *(override.get("aliases") or [])])),
            "tags": list(dict.fromkeys([name.split(".", 1)[0], *(metadata.get("tags") or [])])),
            "entrypoint": {"argv": ["model3d", "run", "RECIPE"], "recipe_operation": name,
                           "docs": metadata.get("docs", "docs/model3d.md")},
            "inputs": list(raw.get("inputs") or []), "outputs": list(raw.get("outputs") or []), "parameters": parameters,
            "requires": requirements,
            "cacheable": bool(raw.get("cacheable", True)), "selectable": list(raw.get("selectable") or []),
            "availability": {"available": True, "reason": None},
        })
    component.update(available=True, state="available", reason=None, operations=len(descriptions))
    return component, capabilities


def build_catalog() -> dict[str, Any]:
    """現在の登録表からカタログを組み立てる。private実装のglobal関数は差し替えない。"""
    mv_component, capabilities = _mv_capabilities()
    model3d_component, model3d = _model3d_capabilities()
    capabilities.extend(model3d)
    components = {"mv": mv_component, "model3d": model3d_component}
    by_id = {item["id"]: item for item in capabilities}
    for workflow in WORKFLOWS:
        for step in workflow["steps"]:
            if step["capability"] not in by_id:
                component_name = "model3d" if step["capability"].startswith("model3d.") else "mv"
                item = _placeholder(step["capability"], component_name, components[component_name])
                capabilities.append(item)
                by_id[item["id"]] = item
    capabilities.sort(key=lambda item: item["id"])
    workflows = []
    for raw in WORKFLOWS:
        item = copy.deepcopy(raw)
        item["availability"] = _workflow_availability(item, by_id)
        item["parameter_readiness"] = _workflow_parameter_readiness(item, by_id)
        workflows.append(item)
    workflows.sort(key=lambda item: item["id"])
    counts = Counter(item["parameters"]["status"] for item in capabilities)
    return {"schema_version": CATALOG_SCHEMA, "components": components,
            "coverage": {"capabilities": len(capabilities), "workflows": len(workflows),
                         "parameters": {key: counts.get(key, 0) for key in sorted(VALID_PARAMETER_STATUS)},
                         "machine_readable_parameters": sum(counts.get(key, 0) for key in MACHINE_READABLE_PARAMETER_STATUS)},
            "capabilities": capabilities, "workflows": workflows}


def validate_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    return _impl.validate_catalog(catalog if catalog is not None else build_catalog())


filter_items = _impl.filter_items
get_item = _impl.get_item
search_catalog = _impl.search_catalog

__all__ = [
    "CatalogError", "CATALOG_SCHEMA", "SEARCH_SCHEMA", "VALID_MATURITY",
    "VALID_PARAMETER_STATUS", "VALID_COMPONENT_STATES", "VALID_EFFECT_MODES",
    "MACHINE_READABLE_PARAMETER_STATUS", "TOOL_RESOLUTION",
    "MV_OVERRIDES", "MV_RUNTIME", "MODEL3D_OVERRIDES", "build_catalog",
    "validate_catalog", "filter_items", "get_item", "search_catalog",
]
