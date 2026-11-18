"""AIがOrpheusの道具を目的から逆引きするための機械可読カタログ。"""
from __future__ import annotations

import argparse
import copy
import importlib
from importlib import util as importlib_util
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Any

from .capability_workflows import WORKFLOWS

CATALOG_SCHEMA = "orpheus-capability-catalog/0.1"
SEARCH_SCHEMA = "orpheus-capability-search/0.1"
VALID_MATURITY = {"stable", "experimental", "external"}
VALID_PARAMETER_STATUS = {"schema", "cli", "docs", "undocumented"}
EXTRA_IMPORTS = {"analysis": "librosa", "fx": "cv2"}


class CatalogError(ValueError):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code, self.message, self.details = code, message, details


# mvのCLIを正典にし、用途語・成熟度・資料だけ補う。子コマンドは最上位の値を継承する。
MV_OVERRIDES = {
    "workflow": {"domain": "workflow", "maturity": "stable", "docs": "docs/workflow.md",
                 "aliases": ["工程計画", "制作計画", "完了確認", "何をすればよい"]},
    "run-adapter": {"domain": "integration", "maturity": "stable", "docs": "docs/adapters.md",
                    "aliases": ["歌詞同期", "音源分離", "外部モデル接続", "Suno語タイミング"]},
    "audio": {"domain": "audio", "maturity": "experimental", "docs": "docs/audio.md",
              "aliases": ["拍解析", "BPM", "強打", "低音", "音ハメ"]},
    "fx": {"domain": "2d", "maturity": "experimental", "docs": "docs/fx.md",
           "aliases": ["一枚絵を動かす", "2.5D", "視差", "局所光", "複数ショット"]},
    "dance": {"domain": "dance", "maturity": "experimental", "docs": "docs/dance.md",
              "aliases": ["ダンス編集", "グリーンバック", "人物を背景に合成", "縦動画"]},
    "gate": {"domain": "quality", "maturity": "experimental", "docs": "docs/gate.md",
             "aliases": ["書き出し前検査", "歌詞時刻検査", "明滅", "同期確認"]},
    "inspect": {"domain": "quality", "maturity": "experimental", "docs": "docs/inspect.md",
                "aliases": ["動画の破綻", "異常検出", "検品", "衣装の崩れ", "動きの不連続"]},
    "handoff": {"domain": "quality", "maturity": "experimental", "docs": "docs/handoff.md",
                "aliases": ["受け渡し画像の検査", "指定範囲外を変えない", "画像修正の確認"]},
    "sheet": {"domain": "quality", "maturity": "experimental", "docs": "docs/sheet.md",
              "aliases": ["コンタクトシート", "コマを並べる", "比較画像"]},
    "deliver": {"domain": "delivery", "maturity": "experimental", "docs": "docs/deliver.md",
                "aliases": ["納品", "区間を結合", "音を付ける", "確認用動画", "チェックサム"]},
    "overlay": {"domain": "2d", "maturity": "experimental", "docs": "docs/fx.md",
                "aliases": ["透過連番", "アルファ動画", "歌詞を重ねる", "題字を重ねる"]},
    "export": {"domain": "text", "maturity": "experimental", "docs": "docs/subtitles.md",
               "aliases": ["字幕", "SRT", "WebVTT", "LRC", "書き出し"]},
    "layer": {"domain": "2d", "maturity": "experimental", "docs": "docs/remotion-props.md", "aliases": ["透過レイヤー"]},
}

MODEL3D_OVERRIDES = {
    "check.eyes": {"summary": "頭候補の目の構造を数値と確認画像で調べる。",
                   "aliases": ["目の構造検査", "白目", "瞳", "まぶた", "目の候補を調べる"], "tools": ["blender"]},
    "model.eye_rig": {"summary": "目の骨を眼球中心へ置き、瞳のウェイトを左右の目の骨へ割り当てる。",
                      "aliases": ["目の骨", "瞳のウェイト", "眼球リグ", "視線リグ"], "tools": ["blender"]},
    "model.eye_keys": {"summary": "まばたきを直し、縮瞳・横伸び・つぶれ・見開きの形状キーを追加する。",
                       "aliases": ["まばたき", "縮瞳", "見開き", "目の形状キー"], "tools": ["blender"]},
    "vrm.look_at": {"summary": "VRMの視線を目の骨で動かす設定にし、瞳の滑る量から倍率を作る。",
                    "aliases": ["VRM視線", "look at", "視線設定", "目で追う"], "tools": ["blender"]},
    "render.eye_sheet": {"summary": "目を姿勢×見る向きで描いて並べ、はみ出し・浮き・すき間を目視する。",
                         "aliases": ["目の破綻", "視線の確認", "目を多方向から確認", "白目がはみ出す", "瞳が浮く"], "tools": ["blender"]},
}


def _normal(value: Any) -> str:
    return unicodedata.normalize("NFKC", str(value)).casefold().strip()


def _json_value(value: Any) -> Any:
    if value is argparse.SUPPRESS:
        return None
    if isinstance(value, Path):
        return str(value)
    try:
        json.dumps(value)
        return value
    except TypeError:
        return str(value)


def _argument(action: argparse.Action) -> dict[str, Any]:
    choices = ([_json_value(choice) for choice in action.choices]
               if action.choices is not None and not isinstance(action, argparse._SubParsersAction) else None)
    required = bool(action.required)
    if not action.option_strings and action.nargs not in ("?", "*", argparse.OPTIONAL, argparse.ZERO_OR_MORE):
        required = True
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction, argparse.BooleanOptionalAction)):
        type_name = "bool"
    elif isinstance(action, argparse._CountAction):
        type_name = "int"
    else:
        type_name = getattr(action.type, "__name__", None) if action.type else None
    return {"name": action.dest, "options": list(action.option_strings), "required": required, "nargs": action.nargs,
            "type": type_name, "choices": choices, "default": _json_value(action.default), "metavar": action.metavar,
            "action": type(action).__name__}


def _schema(arguments: list[dict[str, Any]]) -> dict[str, Any]:
    properties, required = {}, []
    for arg in arguments:
        prop: dict[str, Any] = {"x-options": arg["options"], "x-action": arg["action"]}
        if arg["choices"] is not None:
            prop["enum"] = arg["choices"]
        elif arg["type"] in ("int", "float"):
            prop["type"] = "integer" if arg["type"] == "int" else "number"
        elif arg["type"] == "bool" or arg["action"] in ("_StoreTrueAction", "_StoreFalseAction", "BooleanOptionalAction"):
            prop["type"] = "boolean"
        else:
            prop["type"] = "string"
        if arg["nargs"] in ("+", "*") or (isinstance(arg["nargs"], int) and arg["nargs"] > 0):
            prop = {"type": "array", "items": prop, "x-nargs": arg["nargs"]}
        if arg["action"] in ("_AppendAction", "_AppendConstAction"):
            prop = {"type": "array", "items": prop, "x-repeatable": True}
        if arg["default"] is not None:
            prop["default"] = arg["default"]
        properties[arg["name"]] = prop
        if arg["required"]:
            required.append(arg["name"])
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def _walk_parser(parser: argparse.ArgumentParser, path: tuple[str, ...] = (), help_text: str | None = None):
    if path:
        yield path, parser, help_text
    for action in parser._actions:
        if not isinstance(action, argparse._SubParsersAction):
            continue
        help_by_name = {choice.dest: choice.help for choice in action._choices_actions}
        for name, child in action.choices.items():
            yield from _walk_parser(child, (*path, name), help_by_name.get(name))


def _mv_extras(path: tuple[str, ...]) -> list[str]:
    """実際にそのサブコマンドを呼ぶときだけ必要な optional extra。グループ自体には付けない。"""
    top = path[0]
    if path == ("analyze",):
        return ["analysis"]
    if top == "audio":
        return ["analysis"] if len(path) > 1 and path[1] in {"grid", "hits"} else []
    if top == "fx":
        if len(path) == 1 or path[1] in {"legacy", "accept"}:
            return []
        if path[1] == "timeline" and (len(path) == 2 or path[2] == "plan"):
            return []
        return ["fx"]
    if top == "dance":
        return [] if len(path) == 1 or path[1] in {"plan", "accept"} else ["fx"]
    if top == "inspect":
        return [] if len(path) == 1 else ["fx"]
    if top == "gate" and len(path) > 1 and path[1] in {"voice", "notes", "repeat"}:
        return ["analysis"]
    if top == "overlay":
        return ["fx"]
    return []


def _availability_for_extras(extras: list[str]) -> dict[str, Any]:
    missing = []
    for extra in extras:
        module = EXTRA_IMPORTS.get(extra)
        if module is None:
            continue
        try:
            found = importlib_util.find_spec(module) is not None
        except (ImportError, AttributeError, ValueError):
            found = False
        if not found:
            missing.append({"extra": extra, "import": module})
    return {"available": not missing,
            "reason": None if not missing else "optional extra が現在の Python 環境にありません",
            "missing_extras": missing}


def _mv_capabilities() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    component = {"available": False, "reason": None, "commands": 0}
    try:
        cli = importlib.import_module("mv_pipeline.cli")
        root = cli.parser()
    except Exception as error:
        component["reason"] = f"{type(error).__name__}: {error}"
        return component, []
    capabilities = []
    for path, parser, help_text in _walk_parser(root):
        top = path[0]
        meta = MV_OVERRIDES.get(top, {})
        actions = [a for a in parser._actions if not isinstance(a, argparse._HelpAction | argparse._SubParsersAction)]
        arguments = [_argument(action) for action in actions]
        has_children = any(isinstance(a, argparse._SubParsersAction) for a in parser._actions)
        summary = parser.description or help_text or f"mv {' '.join(path)}"
        extras = _mv_extras(path)
        capabilities.append({
            "id": "mv." + ".".join(path), "kind": "command-group" if has_children else "command",
            "domain": meta.get("domain", "video"), "maturity": meta.get("maturity", "stable"), "summary": summary,
            "aliases": list(meta.get("aliases", [])), "tags": list(dict.fromkeys(path)),
            "entrypoint": {"argv": ["mv", *path], "docs": meta.get("docs", "docs/runbook.md")},
            "inputs": [a["name"] for a in arguments if a["required"]], "outputs": [],
            "parameters": {"status": "cli", "schema": _schema(arguments), "arguments": arguments},
            "requires": {"extras": extras, "tools": [], "network": False},
            "availability": _availability_for_extras(extras),
        })
    component.update(available=True, reason=None, commands=len(capabilities))
    return component, capabilities


def _model3d_capabilities() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    component = {"available": False, "reason": None, "operations": 0}
    try:
        ops = importlib.import_module("model3d.ops")
        descriptions = ops.describe()
    except Exception as error:
        component["reason"] = f"{type(error).__name__}: {error}"
        return component, []
    capabilities = [{
        "id": "model3d.run", "kind": "command", "domain": "model3d", "maturity": "experimental",
        "summary": "RecipeGraphを依存順に実行し、生成物・観測・未解決・再利用元をRunRecordへ残す。",
        "aliases": ["3Dレシピ", "3Dモデルを加工", "VRM", "Blender"], "tags": ["recipe", "run"],
        "entrypoint": {"argv": ["model3d", "run", "RECIPE"], "docs": "docs/model3d.md"},
        "inputs": ["recipe"], "outputs": ["RunRecord", "工程ごとの生成物"],
        "parameters": {"status": "schema", "schema": {"$ref": "tools/model3d/src/model3d/schemas/recipe.schema.json"}},
        "requires": {"extras": ["model3d"], "tools": ["recipe-defined"], "network": False},
        "availability": {"available": True, "reason": None},
    }]
    for raw in descriptions:
        name = raw["operation"]
        metadata = raw.get("capability") if isinstance(raw.get("capability"), dict) else {}
        override = MODEL3D_OVERRIDES.get(name, {})
        parameters = metadata.get("parameters") if isinstance(metadata.get("parameters"), dict) else {"status": "undocumented", "schema": None}
        capabilities.append({
            "id": f"model3d.{name}", "kind": "operation", "domain": "model3d",
            "maturity": metadata.get("maturity", "experimental"),
            "summary": override.get("summary") or metadata.get("summary") or raw.get("summary") or name,
            "aliases": list(dict.fromkeys([*(metadata.get("aliases") or []), *(override.get("aliases") or [])])),
            "tags": list(dict.fromkeys([name.split(".", 1)[0], *(metadata.get("tags") or [])])),
            "entrypoint": {"argv": ["model3d", "run", "RECIPE"], "recipe_operation": name,
                           "docs": metadata.get("docs", "docs/model3d.md")},
            "inputs": list(raw.get("inputs") or []), "outputs": list(raw.get("outputs") or []), "parameters": parameters,
            "requires": {"extras": ["model3d"], "tools": list(metadata.get("tools") or override.get("tools") or []),
                         "network": bool(metadata.get("network", False))},
            "cacheable": bool(raw.get("cacheable", True)), "selectable": list(raw.get("selectable") or []),
            "availability": {"available": True, "reason": None},
        })
    component.update(available=True, reason=None, operations=len(descriptions))
    return component, capabilities


def _placeholder(id: str, reason: str) -> dict[str, Any]:
    domain = "model3d" if id.startswith("model3d.") else "unknown"
    name = id.removeprefix("model3d.")
    override = MODEL3D_OVERRIDES.get(name, {})
    return {"id": id, "kind": "operation" if domain == "model3d" else "command", "domain": domain,
            "maturity": "experimental", "summary": override.get("summary", f"現在の環境では取得できない能力: {id}"),
            "aliases": list(override.get("aliases", [])), "tags": [], "entrypoint": {"argv": [], "docs": None},
            "inputs": [], "outputs": [], "parameters": {"status": "undocumented", "schema": None},
            "requires": {"extras": [domain] if domain == "model3d" else [], "tools": [], "network": False},
            "availability": {"available": False, "reason": reason}}


def _workflow_availability(workflow: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unavailable = [step["capability"] for step in workflow["steps"] if step.get("required", True)
                   and not by_id[step["capability"]]["availability"]["available"]]
    return {"available": not unavailable, "reason": None if not unavailable else "必須の道具が現在の環境で使えません",
            "unavailable": unavailable}


def build_catalog() -> dict[str, Any]:
    mv_component, capabilities = _mv_capabilities()
    model3d_component, model3d = _model3d_capabilities()
    capabilities.extend(model3d)
    by_id = {item["id"]: item for item in capabilities}
    for workflow in WORKFLOWS:
        for step in workflow["steps"]:
            if step["capability"] not in by_id:
                reason = model3d_component["reason"] if step["capability"].startswith("model3d.") else mv_component["reason"]
                item = _placeholder(step["capability"], reason or "現在の版に登録されていません")
                capabilities.append(item)
                by_id[item["id"]] = item
    capabilities.sort(key=lambda item: item["id"])
    workflows = []
    for raw in WORKFLOWS:
        item = copy.deepcopy(raw)
        item["availability"] = _workflow_availability(item, by_id)
        workflows.append(item)
    workflows.sort(key=lambda item: item["id"])
    counts = Counter(item["parameters"]["status"] for item in capabilities)
    return {"schema_version": CATALOG_SCHEMA,
            "components": {"mv": mv_component, "model3d": model3d_component},
            "coverage": {"capabilities": len(capabilities), "workflows": len(workflows),
                         "parameters": {key: counts.get(key, 0) for key in sorted(VALID_PARAMETER_STATUS)}},
            "capabilities": capabilities, "workflows": workflows}


def validate_catalog(catalog: dict[str, Any] | None = None) -> dict[str, Any]:
    data, errors, ids = catalog or build_catalog(), [], set()
    capability_ids = {item.get("id") for item in data.get("capabilities", [])}
    for item in [*data.get("capabilities", []), *data.get("workflows", [])]:
        id = item.get("id")
        if not isinstance(id, str) or not id:
            errors.append({"code": "MISSING_ID"}); continue
        if id in ids: errors.append({"code": "DUPLICATE_ID", "id": id})
        ids.add(id)
        if item.get("maturity") not in VALID_MATURITY: errors.append({"code": "BAD_MATURITY", "id": id})
        if not str(item.get("summary", "")).strip(): errors.append({"code": "MISSING_SUMMARY", "id": id})
        if not isinstance(item.get("availability", {}).get("available"), bool): errors.append({"code": "BAD_AVAILABILITY", "id": id})
        if item.get("kind") != "workflow" and item.get("parameters", {}).get("status") not in VALID_PARAMETER_STATUS:
            errors.append({"code": "BAD_PARAMETER_STATUS", "id": id})
    for workflow in data.get("workflows", []):
        for step in workflow.get("steps", []):
            if step.get("capability") not in capability_ids:
                errors.append({"code": "UNKNOWN_CAPABILITY", "id": workflow.get("id"), "capability": step.get("capability")})
    return {"schema_version": "orpheus-capability-validation/0.1", "valid": not errors, "errors": errors,
            "coverage": data.get("coverage", {})}


def filter_items(catalog: dict[str, Any], *, domain=None, kind=None, maturity=None, available_only=False):
    result = []
    for item in [*catalog["capabilities"], *catalog["workflows"]]:
        if domain and item.get("domain") != domain: continue
        if kind and item.get("kind") != kind: continue
        if maturity and item.get("maturity") != maturity: continue
        if available_only and not item["availability"]["available"]: continue
        result.append(item)
    return result


def get_item(catalog: dict[str, Any], id: str) -> dict[str, Any]:
    for item in [*catalog["capabilities"], *catalog["workflows"]]:
        if item["id"] == id: return item
    raise CatalogError("NOT_FOUND", f"能力または workflow が見つかりません: {id}", id=id)


def _ngrams(text: str, n=2) -> set[str]:
    compact = re.sub(r"[^0-9a-zぁ-んァ-ン一-龥々ー]+", "", _normal(text))
    return {compact[i:i+n] for i in range(max(0, len(compact)-n+1))} if len(compact) >= n else ({compact} if compact else set())


def _score(query: str, item: dict[str, Any]) -> tuple[float, list[str]]:
    q, reasons, score = _normal(query), [], 0.0
    fields = {"id": _normal(item.get("id", "")), "summary": _normal(item.get("summary", "")),
              "aliases": " ".join(_normal(v) for v in item.get("aliases", [])),
              "tags": " ".join(_normal(v) for v in item.get("tags", [])),
              "inputs": " ".join(_normal(v) for v in item.get("inputs", item.get("required_inputs", []))),
              "steps": " ".join(_normal(v.get("purpose", "")) for v in item.get("steps", []))}
    if fields["id"] == q: score += 1000; reasons.append("id が完全一致")
    elif q in fields["id"]: score += 180; reasons.append("id に一致")
    for field, weight in (("aliases", 180), ("summary", 140), ("tags", 90), ("inputs", 45), ("steps", 75)):
        if q and q in fields[field]: score += weight; reasons.append(f"{field} に問い合わせ全体が一致")
    terms = [term for term in re.split(r"[\s、。,.!?！？/]+", q) if len(term) >= 2]
    for term in terms:
        for field, weight in (("aliases", 42), ("summary", 32), ("tags", 24), ("id", 20), ("steps", 18)):
            if term in fields[field]: score += weight
    aliases, qgrams = [_normal(v) for v in item.get("aliases", [])], _ngrams(q)
    exact = [alias for alias in aliases if len(alias) >= 2 and (alias in q or q in alias)]
    if exact: score += min(140, 55*len(exact)); reasons.append("別名: " + ", ".join(exact[:3]))
    close = []
    for alias in aliases:
        grams = _ngrams(alias)
        coverage = len(qgrams & grams)/len(grams) if grams and qgrams else 0
        if coverage >= .45: close.append((coverage, alias))
    if close:
        coverage, alias = max(close); score += coverage*125; reasons.append(f"目的の別名に近い: {alias} ({coverage:.2f})")
    semantic = score
    if semantic < 10: return 0, []
    if item.get("kind") == "workflow":
        score += 12
        if semantic >= 20 and any(word in q for word in ("したい", "作りたい", "作る", "直す", "確認", "調べる", "仕上げる")):
            score += 90; reasons.append("目的文なので workflow を優先")
    if item.get("availability", {}).get("available"): score += 2
    return score, list(dict.fromkeys(reasons))[:5]


def search_catalog(catalog: dict[str, Any], query: str, *, domain=None, kind=None, maturity=None,
                   available_only=False, limit=10) -> dict[str, Any]:
    if not query.strip(): raise CatalogError("EMPTY_QUERY", "検索する目的を空にできません")
    if not 1 <= limit <= 100: raise CatalogError("BAD_LIMIT", "limit は 1〜100 にしてください", limit=limit)
    candidates = filter_items(catalog, domain=domain, kind=kind, maturity=maturity, available_only=available_only)
    ranked = []
    for item in candidates:
        score, reasons = _score(query, item)
        if score >= 10: ranked.append({"score": round(score, 3), "reasons": reasons, "item": item})
    ranked.sort(key=lambda row: (-row["score"], row["item"]["id"]))
    return {"schema_version": SEARCH_SCHEMA, "query": query,
            "filters": {"domain": domain, "kind": kind, "maturity": maturity, "available_only": available_only},
            "results": ranked[:limit], "searched": len(candidates)}
