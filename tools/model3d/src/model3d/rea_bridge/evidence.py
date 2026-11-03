"""REA の解析報告（analysis_report.json）と実験の集計 JSON を、RecipeGraph の evidence の列に写す。

照会の status → evidence の state: found → observed、partial / ambiguous → inferred、no-match / unsupported → unavailable、
それ以外 → unknown。coverage が unknown のグラフは unknown のまま残す（成功扱いにしない）。
"""
from .. import contracts
from ..io import ToolError, read_json
from . import REA_COMMIT, REA_VERSION

STATE_BY_STATUS = {"found": "observed", "partial": "inferred", "ambiguous": "inferred", "no-match": "unavailable",
                   "unsupported": "unavailable"}


def state_of(status):
    return STATE_BY_STATUS.get(str(status), "unknown")


def _check_rea(info, where):
    version, commit = info.get("version"), info.get("commit")
    notes = []
    if version and version != REA_VERSION:
        notes.append(f"REA の版が {version}（固定は {REA_VERSION}）")
    if commit and commit != REA_COMMIT:
        notes.append(f"REA の commit が {commit[:12]}（固定は {REA_COMMIT[:12]}）")
    return notes


def from_analysis_report(path):
    """REA の analysis_report.json（tool / commit / files / graph / queries）から evidence を作る。"""
    report = read_json(path)
    for key in ("tool", "commit", "files", "queries"):
        if key not in report:
            raise ToolError(f"REA の解析報告に {key} がありません: {path}")
    tool, commit = report["tool"], report["commit"]
    notes = _check_rea(report.get("rea", {}), path)
    evidence = []
    for file in report["files"]:
        coverage = file.get("coverage", {}).get("status", "unknown")
        evidence.append({"source": "oss", "ref": f"{report.get('repository', tool)}@{commit}:{file['path']} sha256:{file['sha256']}",
                         "state": "observed" if coverage == "complete" else "inferred" if coverage == "partial" else "unknown",
                         "note": f"REA coverage {coverage}; limitations {len(file.get('limitations', []))}"})
    graph = report.get("graph", {})
    if graph:
        evidence.append({"source": "rea", "ref": f"{tool}@{commit} graph {graph.get('id', '')}",
                         "state": "unknown" if graph.get("coverage", {}).get("status", "unknown") == "unknown" else "inferred",
                         "note": f"nodes {graph.get('nodes')}, relations {graph.get('relations')}, unknowns {graph.get('unknowns')}, "
                                 f"coverage {graph.get('coverage', {}).get('status')}"})
    for query in report["queries"]:
        evidence.append({"source": "rea", "ref": f"{tool}@{commit}:{query['target']} ({query.get('file', 'query')})",
                         "state": state_of(query.get("status")),
                         "note": f"status {query.get('status')}; coverage {query.get('coverage', {}).get('status')}"})
    return {"tool": tool, "commit": commit, "rea": {"version": REA_VERSION, "commit": REA_COMMIT}, "notes": notes,
            "evidence": _validated(evidence), "limitations": list(report.get("limitations", []))}


def from_summary(path):
    """実験の集計 JSON（rea-real-3d-tools-experiment/0.1）から、ツールごとの evidence を作る。"""
    summary = read_json(path)
    if summary.get("schema") != "rea-real-3d-tools-experiment/0.1":
        raise ToolError(f"集計 JSON の schema が違います: {summary.get('schema')!r}")
    result = {"rea": {"version": REA_VERSION, "commit": REA_COMMIT}, "tools": {}, "limitations": list(summary.get("limits", []))}
    for tool, info in summary.get("rea", {}).items():
        commit = summary.get(tool, {}).get("source_commit", "")
        evidence = [{"source": "rea", "ref": f"{tool}@{commit} graph", "state": "unknown" if info.get("coverage") == "unknown" else "inferred",
                     "note": f"files {info.get('files')}, nodes {info.get('nodes')}, relations {info.get('relations')}, unknowns {info.get('unknowns')}"}]
        for query in info.get("queries", []):
            evidence.append({"source": "rea", "ref": f"{tool}@{commit}:{query['target']}", "state": state_of(query.get("status")),
                             "note": f"status {query.get('status')}"})
        verdict = comparison_verdict(summary.get(tool, {}))
        if verdict is None:   # 比較の結果が無い（metadata だけ）ときは観測にしない
            evidence.append({"source": "artifact", "ref": f"{tool}@{commit} reconstruction comparison", "state": "unknown",
                             "note": "comparison results not supplied (no final_all_pass or passed/total counters)"})
        else:
            evidence.append({"source": "artifact", "ref": f"{tool}@{commit} reconstruction comparison",
                             "state": "observed" if verdict else "unavailable",
                             "note": "original artifacts and recipes compared with an independent reconstruction"
                                     + ("" if verdict else "; comparison failed")})
        result["tools"][tool] = {"commit": commit, "evidence": _validated(evidence)}
    return result


def comparison_verdict(comparison):
    """集計の比較結果。final_all_pass（真偽）か passed/total（整数）が明示されているときだけ True/False、無ければ None。"""
    verdict = comparison.get("final_all_pass")
    if isinstance(verdict, bool):
        return verdict
    passed, total = comparison.get("passed"), comparison.get("total")
    counters = all(isinstance(v, int) and not isinstance(v, bool) for v in (passed, total))
    if counters and total > 0:
        return passed == total
    return None


def _validated(evidence):
    schema = contracts.schema("recipe")["$defs"]["evidence"]
    from jsonschema import Draft202012Validator
    validator = Draft202012Validator(schema)
    for item in evidence:
        errors = list(validator.iter_errors(item))
        if errors:
            raise ToolError(f"evidence の形式が違います: {errors[0].message}")
    return evidence
