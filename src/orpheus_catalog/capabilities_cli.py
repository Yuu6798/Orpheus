"""Orpheus の能力カタログを AI と人が JSON で読むための CLI。"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from .capability_catalog import CatalogError, build_catalog, filter_items, get_item, search_catalog
from .capability_validation import validate_catalog
from .schemas import load_schema, names as schema_names


BRIEF_SCHEMA = "orpheus-capability-brief/0.1"
BRIEF_HELP = ("結果ごとに id・kind・maturity・score・summary・docs・使えるか（available。使えなければ reason・missing）・"
              "workflow の starters のコマンドだけを、字下げなしの JSON で出す（詳しくは show ID）")


class JsonArgumentParser(argparse.ArgumentParser):
    """help 以外の構文エラーを、stderr の自由文でなく構造化エラーへ渡す。"""

    def error(self, message: str) -> None:
        raise CatalogError("ARGUMENT_ERROR", message)


def parser() -> argparse.ArgumentParser:
    p = JsonArgumentParser(
        prog="orpheus",
        description="Orpheus の道具と目的別 workflow を、実行せずに検索・表示する",
    )
    p.add_argument("--compact", action="store_true", help="JSON を改行なしで出す")
    commands = p.add_subparsers(dest="command", required=True)

    capabilities = commands.add_parser("capabilities", help="現在見える能力の一覧")
    _filters(capabilities, include_workflow=False)

    search = commands.add_parser("search", help="自然言語の目的から workflow と道具を逆引きする")
    search.add_argument("query", nargs="+", help="例: 目の破綻を確認して直したい")
    _filters(search)
    search.add_argument("--limit", type=int, default=10)
    search.add_argument("--brief", action="store_true", help=BRIEF_HELP)

    show = commands.add_parser("show", help="1 つの能力または workflow の詳細")
    show.add_argument("id")

    workflows = commands.add_parser("workflows", help="目的別 workflow の一覧または検索")
    workflows.add_argument("--query", help="workflow だけを目的で検索する")
    workflows.add_argument("--domain")
    workflows.add_argument("--maturity", choices=["stable", "verified", "experimental", "external"])
    workflows.add_argument("--available-only", action="store_true")
    workflows.add_argument("--parameter-status", choices=["schema", "cli", "docs", "undocumented"])
    workflows.add_argument("--machine-readable-only", action="store_true",
                           help="必須工程のパラメーターがすべてschemaまたはCLIで読めるworkflowだけ")
    workflows.add_argument("--limit", type=int, default=20)
    workflows.add_argument("--brief", action="store_true", help=BRIEF_HELP)

    schema = commands.add_parser("schema", help="カタログ・検索結果・CLI応答の公開JSON Schema")
    schema.add_argument("name", choices=schema_names())

    commands.add_parser("validate", help="カタログのJSON Schemaと内部参照を検査する")
    return p


def _filters(command: argparse.ArgumentParser, *, include_workflow: bool = True) -> None:
    command.add_argument("--domain")
    kinds = (["command", "command-group", "operation", "workflow"] if include_workflow
             else ["command", "command-group", "operation"])
    command.add_argument("--kind", choices=kinds)
    command.add_argument("--maturity", choices=["stable", "verified", "experimental", "external"])
    command.add_argument("--available-only", action="store_true")
    command.add_argument("--parameter-status", choices=["schema", "cli", "docs", "undocumented"])
    command.add_argument("--machine-readable-only", action="store_true")


def _success(command: str, result: Any) -> dict[str, Any]:
    return {"ok": True, "command": command, "result": result}


def brief_item(item: dict[str, Any], score: float | None = None) -> dict[str, Any]:
    """検索・一覧の 1 件の要点（--brief）。資料は workflow なら docs、道具なら entrypoint.docs。"""
    out: dict[str, Any] = {"id": item["id"], "kind": item["kind"], "maturity": item["maturity"]}
    if score is not None:
        out["score"] = round(float(score), 1)
    out["summary"] = item["summary"]
    out["docs"] = item.get("docs") if item["kind"] == "workflow" else (item.get("entrypoint") or {}).get("docs")
    availability = item.get("availability") or {}
    out["available"] = bool(availability.get("available"))
    if not out["available"]:
        out["reason"] = availability.get("reason")
        missing = [row.get("extra") if isinstance(row, dict) else row
                   for row in availability.get("missing_extras") or availability.get("unavailable") or []]
        if missing:
            out["missing"] = [str(value) for value in missing]
    starters = [starter.get("commands", []) for starter in item.get("starters", [])]
    if starters:
        out["starters"] = starters
    return out


def brief_result(result: dict[str, Any]) -> dict[str, Any]:
    """search の結果（results の score・item）か workflows の一覧（workflows）を、--brief の形にする。"""
    if "results" in result:
        out: dict[str, Any] = {"schema_version": BRIEF_SCHEMA, "query": result["query"],
                               "results": [brief_item(row["item"], row["score"]) for row in result["results"]],
                               "searched": result["searched"]}
        return out
    return {"schema_version": BRIEF_SCHEMA, "count": result["count"], "results": [brief_item(item) for item in result["workflows"]]}


def _dispatch(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "schema":
        return _success(args.command, {"name": args.name, "schema": load_schema(args.name)})

    catalog = build_catalog()
    if args.command == "capabilities":
        items = filter_items(
            catalog,
            domain=args.domain,
            kind=args.kind,
            maturity=args.maturity,
            available_only=args.available_only,
            parameter_status=args.parameter_status,
            machine_readable_only=args.machine_readable_only,
        )
        items = [item for item in items if item["kind"] != "workflow"]
        return _success(
            args.command,
            {
                "schema_version": catalog["schema_version"],
                "components": catalog["components"],
                "coverage": catalog["coverage"],
                "count": len(items),
                "capabilities": items,
            },
        )
    if args.command == "search":
        query = " ".join(args.query)
        result = search_catalog(
            catalog,
            query,
            domain=args.domain,
            kind=args.kind,
            maturity=args.maturity,
            available_only=args.available_only,
            parameter_status=args.parameter_status,
            machine_readable_only=args.machine_readable_only,
            limit=args.limit,
        )
        return _success(args.command, brief_result(result) if args.brief else result)
    if args.command == "show":
        return _success(args.command, get_item(catalog, args.id))
    if args.command == "workflows":
        if args.query:
            result = search_catalog(
                catalog,
                args.query,
                domain=args.domain,
                kind="workflow",
                maturity=args.maturity,
                available_only=args.available_only,
                parameter_status=args.parameter_status,
                machine_readable_only=args.machine_readable_only,
                limit=args.limit,
            )
        else:
            workflows = filter_items(
                catalog,
                domain=args.domain,
                kind="workflow",
                maturity=args.maturity,
                available_only=args.available_only,
                parameter_status=args.parameter_status,
                machine_readable_only=args.machine_readable_only,
            )
            if args.limit < 1 or args.limit > 100:
                raise CatalogError("BAD_LIMIT", "limit は 1〜100 にしてください", limit=args.limit)
            workflows = workflows[: args.limit]
            result = {"schema_version": catalog["schema_version"], "count": len(workflows), "workflows": workflows}
        return _success(args.command, brief_result(result) if args.brief else result)
    if args.command == "validate":
        result = validate_catalog(catalog)
        if not result["valid"]:
            raise CatalogError("INVALID_CATALOG", "能力カタログの内部検査に失敗しました", errors=result["errors"])
        return _success(args.command, result)
    raise CatalogError("UNKNOWN_COMMAND", f"知らないコマンドです: {args.command}")


def _write(payload: dict[str, Any], *, compact: bool, stream: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=None if compact else 2,
                     separators=(",", ":") if compact else None), file=stream)


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    compact = "--compact" in raw or "--brief" in raw
    try:
        args = parser().parse_args(raw)
        payload = _dispatch(args)
    except CatalogError as error:
        _write(
            {
                "ok": False,
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                    "retryable": error.code in {"ARGUMENT_ERROR", "EMPTY_QUERY", "BAD_LIMIT", "NOT_FOUND"},
                },
            },
            compact=compact,
            stream=sys.stderr,
        )
        return 2
    except Exception as error:  # 予期しない失敗も、AI が読める外形を守る
        _write(
            {
                "ok": False,
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": str(error),
                    "details": {"type": type(error).__name__},
                    "retryable": False,
                },
            },
            compact=compact,
            stream=sys.stderr,
        )
        return 1
    _write(payload, compact=args.compact or getattr(args, "brief", False), stream=sys.stdout)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
