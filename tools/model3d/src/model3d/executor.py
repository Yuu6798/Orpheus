"""レシピの実行器。工程を依存順に実行し、RunRecord（model3d-run/0.1）と再利用の索引（index.json）を書く。

出力先 <out>（既定は recipe のあるディレクトリの model3d_runs/）:

    <out>/<UTC stamp>-<recipe sha 先頭 8>/   recipe.json（写し）run_record.json run.log <工程 id>/<出力ファイル>
    <out>/index.json                          工程の key -> 成功した工程の生成物（succeeded だけ。needs_review は載せない）

- 工程の状態は pending / running / succeeded / needs_review / failed。検査に通らない工程は needs_review（候補）として残し、
  失敗した工程でレシピを止める（後ろの工程は pending のまま）。
- 再利用は、工程の key（操作・パラメーター・入力のハッシュ・コードの指紋・ライブラリ版・preset と対応表の sha256）が一致し、
  前の生成物がハッシュまで揃っているときだけ。ファイルがあるだけでは再利用しない。別プロセスを呼ぶ操作
  （external.run・blender.run_script）は設定が参照する実行ファイルや素材を知らないので、毎回実行する（索引にも載せない）。
- 入力の sha256 が recipe の宣言と違えば実行しない。未確認の役割・観測不足は unresolved に残し、成功扱いにしない。
- 工程の select（{"<入力名>": "<役割>"}）は、対応表でその入力メッシュの名前と頂点構成に結び付いた面領域（か同じ名前の面集合）を
  選んで操作に渡す。別の部品の面領域・古い対応（頂点構成の違う面領域）・当たる面の無い役割では工程を止め、メッシュ全体へ黙って当てない。
"""
import platform
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import VERSION, contracts, fingerprint, ops, semantic
from . import recipe as recipes
from .io import ToolError, atomic_json, directory_lock, now, read_json, resolve_path, safe_path, sha256, stamp
from .mesh import Mesh

INDEX_SCHEMA = "model3d-index/0.1"
STATES = ("pending", "running", "succeeded", "needs_review", "failed")


@dataclass
class Context:
    run_dir: Path
    step_dir: Path
    recipe_dir: Path
    preset: object = None
    semantic_map: object = None
    tools: dict = field(default_factory=dict)
    log: object = None
    options: dict = field(default_factory=dict)
    space: object = None          # 工程が宣言した座標系（local / world / rest / pose）。生成物の記録に使う
    applies_to: tuple = ()        # 工程が宣言した役割
    selections: dict = field(default_factory=dict)   # 工程の select で選んだ面領域（入力名 -> semantic.select の結果）


def library_versions():
    return {"numpy": np.__version__}


def environment(tools=None, code_fingerprint=None):
    recorded = {}
    for name, value in (tools or {}).items():
        path = Path(value)
        recorded[name] = {"path": str(path), "sha256": sha256(path) if path.is_file() else None}
    return {"python": platform.python_version(), "platform": platform.platform(), "numpy": np.__version__,
            "model3d": {"version": VERSION, "fingerprint": code_fingerprint or fingerprint()}, "tools": recorded}


def execute(operation, inputs, parameters, context, outputs):
    """設計の `execute(operation, inputs, parameters, context)`。outputs は宣言した出力名 -> 書き先のパス。"""
    op = ops.get(operation)
    result = op.function(dict(inputs), dict(parameters), dict(outputs), context)
    if not isinstance(result, ops.StepResult):
        raise ToolError(f"操作 {operation} が StepResult を返しませんでした")
    return result


class Run:
    def __init__(self, out, recipe_sha):
        self.out = Path(out)
        mark, serial = f"{stamp()}-{recipe_sha[:8]}", 1
        run_id = mark
        while True:
            try:
                (self.out / run_id).mkdir(parents=True)
                break
            except FileExistsError:
                serial += 1
                run_id = f"{mark}-{serial}"
        self.dir = self.out / run_id
        self.log_path = self.dir / "run.log"
        self.record = {"schema_version": "model3d-run/0.1", "run_id": run_id, "created_at": now(), "finished_at": None,
                       "status": "running", "error": None, "recipe": None, "inputs": {}, "environment": None, "steps": [],
                       "candidates": [], "adopted": None, "human_edits": [], "warnings": []}

    @property
    def run_id(self):
        return self.record["run_id"]

    def save(self):
        atomic_json(self.dir / "run_record.json", self.record)

    def log(self, message):
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{now()} {message}\n")


def close_orphans(out):
    for path in sorted(Path(out).glob("*/run_record.json")):
        try:
            record = read_json(path)
        except (OSError, ToolError):
            continue
        if isinstance(record, dict) and record.get("status") == "running":
            record.update(status="failed", finished_at=now(), error="interrupted: プロセスが終了する前に止まりました（強制終了など）")
            for step in record.get("steps", []):
                if step.get("status") == "running":
                    step["status"] = "failed"
                    step["error"] = record["error"]
            atomic_json(path, record)


def load_index(out):
    path = Path(out) / "index.json"
    if not path.exists():
        return {"schema_version": INDEX_SCHEMA, "steps": {}}
    try:
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("steps"), dict):
            raise ToolError("形式が違います")
    except (OSError, ToolError) as error:
        raise ToolError(f"{path} を読めません（{error}）。壊れている場合は削除してください（既存の run は残り、再実行になります）") from error
    return data


def reusable(out, index, key):
    entry = index["steps"].get(key)
    if not isinstance(entry, dict):
        return None
    try:
        for item in entry["artifacts"].values():
            file = safe_path(out, item["path"])
            if not file.is_file() or sha256(file) != item["sha256"]:
                return None
    except (ToolError, OSError, KeyError, TypeError):
        return None
    return entry


def _precondition(text, step, inputs, semantic_map):
    """`role:<役割>` / `closed:<入力名>` / `units:<単位>` を確かめる。他の文は前提の説明として記録だけする。"""
    if ":" not in text:
        return None
    kind, value = text.split(":", 1)
    if kind == "role":
        if semantic_map is None:
            raise ToolError(f"前提 {text}: 役割を確かめる対応表（SemanticMap）がありません")
        present = semantic.roles_present(semantic_map)
        if value not in present["parts"] and value not in present["bones"]:
            raise ToolError(f"前提 {text}: 役割 {value} が対応表にありません（未対応か rejected）")
    elif kind == "closed":
        if value not in inputs:
            raise ToolError(f"前提 {text}: 入力 {value} がありません")
        report = Mesh.load(inputs[value]).edge_report()
        if not report["closed"]:
            raise ToolError(f"前提 {text}: 入力 {value} が閉じていません（境界辺 {report['boundary_edges']}・非多様体辺 {report['nonmanifold_edges']}）")
    elif kind == "units":
        for name, path in inputs.items():
            if Path(path).suffix.lower() in (".json", ".obj"):
                try:
                    mesh = Mesh.load(path)
                except ToolError:
                    continue
                if mesh.units != value:
                    raise ToolError(f"前提 {text}: 入力 {name} の単位が {mesh.units} です")
    else:
        raise ToolError(f"知らない前提の種類です: {text!r}（role: / closed: / units: か、コロンを含まない説明文）")
    return text


def run_recipe(recipe_path, out=None, force=False, preset_path=None, tools=None, progress=None):
    """recipe を実行して RunRecord を書き、要約を返す。失敗した工程でレシピは止まる（記録は残る）。"""
    began = time.monotonic()
    say = progress or (lambda message: None)
    recipe_path = Path(recipe_path).resolve()
    recipe, inputs = recipes.load(recipe_path)
    base = recipe_path.parent
    ordered = recipes.order_steps(recipe)
    for step in ordered:
        op = ops.get(step["operation"])
        for name in step.get("select", {}):
            if name not in step.get("inputs", {}):
                raise ToolError(f"工程 {step['id']} の select の {name} は、その工程の入力にありません")
            if name not in op.selectable:
                usable = sorted(o.name for o in ops.OPERATIONS.values() if o.selectable)
                raise ToolError(f"工程 {step['id']}: 操作 {step['operation']} は入力 {name} の select（面領域の選択）に対応していません"
                                f"（対応する操作: {', '.join(usable)}）")
        if step.get("select") and not recipe.get("semantic_map"):
            raise ToolError(f"工程 {step['id']} の select には、recipe の semantic_map（面領域を持つ対応表）が要ります")
    preset = None
    preset_file = Path(preset_path).resolve() if preset_path else (resolve_path(base, recipe["preset"]) if recipe.get("preset") else None)
    if preset_file is not None:
        preset = semantic.load_preset(preset_file)
    semantic_map = None
    semantic_file = resolve_path(base, recipe["semantic_map"]) if recipe.get("semantic_map") else None
    if semantic_file is not None:
        semantic_map = contracts.load("semantic-map", semantic_file)
    tools = dict(tools or {})
    out = (Path(out) if out else base / "model3d_runs").resolve()
    recipe_sha = sha256(recipe_path)
    code_fingerprint = fingerprint()
    libraries = library_versions()
    context_hashes = {"preset": sha256(preset_file) if preset_file is not None else None,
                      "semantic_map": sha256(semantic_file) if semantic_file is not None else None}
    digests_by_step = {}
    with directory_lock(out):
        close_orphans(out)
        index = load_index(out)
        run = Run(out, recipe_sha)
        shutil.copyfile(recipe_path, run.dir / "recipe.json")
        run.record.update(recipe={"path": str(recipe_path), "sha256": recipe_sha, "name": recipe["name"]}, inputs=inputs,
                          environment=environment(tools, code_fingerprint))
        if preset_file is not None:
            run.record["preset"] = {"path": str(preset_file), "sha256": context_hashes["preset"]}
        if semantic_file is not None:
            run.record["semantic_map"] = {"path": str(semantic_file), "sha256": context_hashes["semantic_map"]}
            unconfirmed = sum(1 for kind in ("parts", "bones", "regions") for v in semantic_map[kind].values() if v["state"] != "confirmed")
            if unconfirmed:
                run.record["warnings"].append(f"対応表に未確認の対応が {unconfirmed} 件あります（確認は model3d semantic confirm）")
        for step in ordered:
            run.record["steps"].append({"id": step["id"], "operation": step["operation"], "status": "pending", "key": None,
                                        "started_at": None, "finished_at": None, "seconds": None,
                                        "parameters": step.get("parameters", {}), "inputs": {}, "artifacts": {},
                                        "observations": [], "unresolved": [], "error": None, "reused_from": None,
                                        **({"applies_to": step["applies_to"]} if step.get("applies_to") else {}),
                                        **({"select": step["select"]} if step.get("select") else {}),
                                        **({"space": step["space"]} if step.get("space") else {})})
        run.save()
        run.log(f"recipe {recipe['name']} sha256 {recipe_sha} steps {len(ordered)}")
        artifacts_by_step = {}
        failed = False
        try:
            for step, entry in zip(ordered, run.record["steps"]):
                if failed:
                    break
                entry["status"] = "running"
                entry["started_at"] = now()
                run.save()
                step_began = time.monotonic()
                try:
                    step_inputs, hashes = {}, {}
                    for name, ref in step.get("inputs", {}).items():
                        parsed = recipes.parse_ref(ref)
                        if parsed[0] == "input":
                            step_inputs[name] = Path(inputs[parsed[1]]["path"])
                            hashes[name] = inputs[parsed[1]]["sha256"]       # 読み込み時に計算済み
                        else:
                            produced = artifacts_by_step.get(parsed[1], {})
                            if parsed[2] not in produced:
                                raise ToolError(f"工程 {parsed[1]} の出力 {parsed[2]} がありません（その工程が失敗したか、作られていない）")
                            step_inputs[name] = produced[parsed[2]]
                            hashes[name] = digests_by_step[parsed[1]][parsed[2]]
                    entry["inputs"] = hashes
                    key = recipes.step_key(step, hashes, code_fingerprint, libraries, context_hashes)
                    entry["key"] = key
                    cacheable = ops.get(step["operation"]).cacheable
                    unresolved = []
                    for role in step.get("applies_to", []):
                        if semantic_map is None:
                            unresolved.append(f"役割 {role} を確かめる対応表（SemanticMap）がありません")
                        else:
                            present = semantic.roles_present(semantic_map)
                            if role not in present["parts"] and role not in present["bones"]:
                                raise ToolError(f"applies_to の役割 {role} が対応表にありません（未対応か rejected）")
                    for text in step.get("preconditions", []):
                        _precondition(text, step, step_inputs, semantic_map)
                    # select: 入力メッシュの名前と頂点構成に結び付いた面領域だけを渡す。別の部品・古い対応・当たる面の無い役割は止める。
                    selections = {name: semantic.select(semantic_map, role, Mesh.load(step_inputs[name]))
                                  for name, role in sorted(step.get("select", {}).items())}
                    chosen_notes = [{"kind": "selection", "input": name, "role": chosen["role"], "faces": len(chosen["faces"]),
                                     "vertices": len(chosen["vertices"]), "topology_sha256": chosen["topology_sha256"],
                                     "sources": chosen["sources"], "ignored_stale": chosen["ignored_stale"],
                                     "ignored_other_part": chosen["ignored_other_part"]}
                                    for name, chosen in selections.items()]
                    # 使用する役割だけを候補扱いにする。無関係な未確認の対応は run の警告に留める。
                    # 同じ役割が applies_to と role: の両方に出ても、未解決事項は一度だけ記録する。
                    used_roles = set(step.get("applies_to", [])) | set(step.get("select", {}).values()) | {
                        text.split(":", 1)[1] for text in step.get("preconditions", []) if text.startswith("role:")}
                    if semantic_map is not None:
                        for role in sorted(used_roles & semantic.unconfirmed_roles(semantic_map)):
                            unresolved.append(f"役割 {role} の対応が未確認です（model3d semantic confirm で確認してください）")
                    step_dir = run.dir / step["id"]
                    step_dir.mkdir(parents=True, exist_ok=True)
                    outputs = {name: step_dir / file for name, file in step.get("outputs", {}).items()}
                    previous = None if (force or not cacheable or unresolved) else reusable(out, index, key)
                    if previous is not None:
                        for name, item in previous["artifacts"].items():
                            shutil.copyfile(safe_path(out, item["path"]), outputs[name])
                        entry["observations"] = list(previous.get("observations", []))
                        entry["reused_from"] = previous["run_id"]
                        say(f"[model3d] {step['id']}: 同じ入力の成功した工程を再利用します（{previous['run_id']}）")
                        run.log(f"{step['id']}: reused from {previous['run_id']}")
                    else:
                        say(f"[model3d] {step['id']}: {step['operation']}")
                        run.log(f"{step['id']}: run {step['operation']}")
                        context = Context(run.dir, step_dir, base, preset, semantic_map, tools, run.log, {"force": force},
                                          step.get("space"), tuple(step.get("applies_to", [])), selections)
                        result = execute(step["operation"], step_inputs, step.get("parameters", {}), context, outputs)
                        entry["observations"] = chosen_notes + list(result.observations)
                        unresolved.extend(result.unresolved)
                        if result.review:
                            entry["status"] = "needs_review"
                    missing = [name for name, path in outputs.items() if not path.is_file()]
                    if missing:
                        raise ToolError(f"工程 {step['id']} が宣言した出力を作っていません: {', '.join(missing)}")
                    entry["artifacts"] = {name: {"path": str(path.relative_to(run.dir).as_posix()), "sha256": sha256(path),
                                                 "bytes": path.stat().st_size} for name, path in outputs.items()}
                    artifacts_by_step[step["id"]] = outputs
                    digests_by_step[step["id"]] = {name: item["sha256"] for name, item in entry["artifacts"].items()}
                    entry["unresolved"] = unresolved
                    if entry["status"] == "running":
                        entry["status"] = "needs_review" if unresolved else "succeeded"
                    if entry["status"] == "needs_review":
                        run.record["candidates"].append(step["id"])
                    elif entry["status"] == "succeeded" and cacheable:
                        index["steps"][key] = {"run_id": run.run_id, "step": step["id"],
                                               "artifacts": {name: {**item, "path": f"{run.run_id}/{item['path']}"}
                                                             for name, item in entry["artifacts"].items()},
                                               "observations": entry["observations"]}
                    run.log(f"{step['id']}: {entry['status']} {'; '.join(unresolved)}")
                except ToolError as error:
                    entry["status"], entry["error"], failed = "failed", str(error)[:3000], True
                    run.log(f"{step['id']}: failed: {error}")
                except Exception as error:  # noqa: BLE001 - 記録してから止める
                    entry["status"], entry["error"], failed = "failed", f"{type(error).__name__}: {error}"[:3000], True
                    run.log(traceback.format_exc())
                finally:
                    entry["finished_at"] = now()
                    entry["seconds"] = round(time.monotonic() - step_began, 3)
                    run.save()
        finally:
            for entry in run.record["steps"]:   # KeyboardInterrupt などで途中で止まった工程を running のまま残さない
                if entry["status"] == "running":
                    entry["status"] = "failed"
                    entry["error"] = entry["error"] or "interrupted: 工程の途中で止まりました"
                    entry["finished_at"] = entry["finished_at"] or now()
            statuses = [s["status"] for s in run.record["steps"]]
            if "failed" in statuses:
                run.record["status"] = "failed"
                run.record["error"] = next(s["error"] for s in run.record["steps"] if s["status"] == "failed")
            elif "needs_review" in statuses:
                run.record["status"] = "needs_review"
            elif all(s == "succeeded" for s in statuses):
                run.record["status"] = "succeeded"
            else:
                run.record["status"] = "failed"
                run.record["error"] = run.record["error"] or "interrupted"
            run.record["finished_at"] = now()
            contracts.validate("run", run.record, run.dir / "run_record.json")
            run.save()
            atomic_json(out / "index.json", index)
    return summarize(run.dir, run.record, round(time.monotonic() - began, 3))


def summarize(run_dir, record, seconds=None):
    run_dir = Path(run_dir)
    steps = {}
    for step in record["steps"]:
        steps[step["id"]] = {"operation": step["operation"], "status": step["status"], "reused_from": step["reused_from"],
                             "artifacts": {name: str(run_dir / item["path"]) for name, item in step["artifacts"].items()},
                             "observations": step["observations"], "unresolved": step["unresolved"], "error": step["error"]}
    return {"run_id": record["run_id"], "run_dir": str(run_dir), "status": record["status"], "error": record["error"],
            "steps": steps, "candidates": record["candidates"], "warnings": record["warnings"], "seconds": seconds}


def load_run(run_dir):
    run_dir = Path(run_dir).resolve()
    path = run_dir / "run_record.json"
    if not path.is_file():
        raise ToolError(f"run_record.json がありません: {run_dir}")
    return run_dir, contracts.load("run", path)


def accept(run_dir, decision, by, note=None):
    """人の判定（accepted / rejected / hold）を記録する。technical な合否とは別物。"""
    if decision not in ("accepted", "rejected", "hold"):
        raise ToolError(f"decision は accepted / rejected / hold です: {decision!r}")
    run_dir, record = load_run(run_dir)
    if record["status"] == "running":
        raise ToolError("実行中の run は判定できません")
    record["adopted"] = {"at": now(), "by": by, "decision": decision, **({"note": note} if note else {})}
    contracts.validate("run", record)
    atomic_json(run_dir / "run_record.json", record)
    return {"run_id": record["run_id"], "adopted": record["adopted"]}


def note_edit(run_dir, by, what, minutes=None):
    """手修正の内容・回数・時間を残す（作業量の比較のため）。"""
    run_dir, record = load_run(run_dir)
    entry = {"at": now(), "by": by, "what": what}
    if minutes is not None:
        entry["minutes"] = float(minutes)
    record["human_edits"].append(entry)
    contracts.validate("run", record)
    atomic_json(run_dir / "run_record.json", record)
    return {"run_id": record["run_id"], "human_edits": len(record["human_edits"])}
