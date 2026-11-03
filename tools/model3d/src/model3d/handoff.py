"""Orpheus へ渡す成果物の控え（model3d-handoff/0.1）。run・レシピ・成果物の sha256 を 1 つの JSON にする。

dance の spec からは `x_model3d: {"handoff": "handoff.json"}` のように参照できる（x_ の鍵は dance が無視するので、
dance の契約を変えずに結び付けられる）。mv dance の manifest に残る素材の sha256 と、この控えの artifact.sha256 を照合する。
"""
from pathlib import Path

from . import contracts, executor
from .io import ToolError, atomic_json, now, sha256


def find_artifact(record, run_dir, artifact):
    """"<工程 id>/<出力名>" か、出力ファイル名、run の中の相対パスで成果物を探す。"""
    matches = []
    for step in record["steps"]:
        for name, item in step["artifacts"].items():
            labels = {f"{step['id']}/{name}", item["path"], Path(item["path"]).name}
            if artifact in labels:
                matches.append((step, name, item))
    if not matches:
        raise ToolError(f"成果物 {artifact!r} が run にありません（<工程 id>/<出力名> で指定してください）")
    if len(matches) > 1:
        raise ToolError(f"成果物 {artifact!r} が複数あります: " + ", ".join(f"{s['id']}/{n}" for s, n, _ in matches))
    return matches[0]


def write(run_dir, artifact, out, notes=None):
    run_dir, record = executor.load_run(run_dir)
    step, name, item = find_artifact(record, run_dir, artifact)
    file = run_dir / item["path"]
    if not file.is_file():
        raise ToolError(f"成果物のファイルがありません: {file}")
    digest = sha256(file)
    if digest != item["sha256"]:
        raise ToolError(f"成果物のハッシュが記録と違います（{file}）。run の後に書き換えられています")
    data = {"schema_version": "model3d-handoff/0.1", "created_at": now(), "run_id": record["run_id"], "run_dir": str(run_dir),
            "run_status": record["status"], "recipe": {"name": record["recipe"]["name"], "sha256": record["recipe"]["sha256"]},
            "artifact": {"step": step["id"], "name": name, "path": str(file), "sha256": digest, "bytes": file.stat().st_size},
            "environment": record["environment"], "adopted": record["adopted"]}
    if notes:
        data["notes"] = notes
    contracts.validate("handoff", data)
    atomic_json(out, data)
    return {"out": str(Path(out).resolve()), "artifact": data["artifact"], "run_status": record["status"]}
