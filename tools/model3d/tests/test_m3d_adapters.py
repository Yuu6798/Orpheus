"""別プロセスの接続、Blender の argv と観測の整形、REA の根拠。Blender 本体は無いので起動はしない。"""
import json
import sys
from pathlib import Path

import pytest

from model3d import contracts
from model3d.adapters import process
from model3d.blender_adapter import launcher, observe
from model3d.io import ToolError, atomic_json
from model3d.rea_bridge import REA_COMMIT, evidence, observation


def test_process_runs_literal_argv_without_a_shell_and_records_the_tool(tmp_path):
    script = tmp_path / "tool.py"
    script.write_text("import json, sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n", encoding="utf-8")
    out = tmp_path / "out.json"
    literal = "$(should-not-run); & echo secret"
    config = {"tool": "fixture", "version": "1", "license": "MIT", "weights": {"name": "w", "version": "3"},
              "argv": [sys.executable, str(script), "{result}", "{image}", literal, "{work}/x"]}
    record = process.run(config, {"result": out, "image": "a b.png", "work": tmp_path}, log=tmp_path / "log.txt")
    assert json.loads(out.read_text()) == ["a b.png", literal, f"{tmp_path}/x"]
    assert record["exit_code"] == 0 and record["tool"] == "fixture" and record["license"] == "MIT" and record["weights"]["version"] == "3"
    assert len(record["executable_sha256"]) == 64 and (tmp_path / "log.txt").exists()


def test_process_config_validation_and_failure(tmp_path):
    with pytest.raises(ToolError, match="argv"):
        process.check_config({"tool": "t", "version": "1", "argv": "python"})
    with pytest.raises(ToolError, match="絶対パス"):
        process.check_config({"tool": "t", "version": "1", "argv": ["python"]})
    with pytest.raises(ToolError, match="tool と version"):
        process.check_config({"argv": [sys.executable]})
    failing = {"tool": "t", "version": "1", "argv": [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(3)"]}
    with pytest.raises(ToolError, match="終了コード 3"):
        process.run(failing, {})


def test_external_run_operation_through_the_executor(tmp_path):
    from model3d import executor
    script = tmp_path / "gen.py"
    script.write_text("import sys\nfrom pathlib import Path\nPath(sys.argv[2]).write_text(Path(sys.argv[1]).read_text() + ' generated')\n", encoding="utf-8")
    (tmp_path / "image.txt").write_text("sheet", encoding="utf-8")
    atomic_json(tmp_path / "generator.json", {"tool": "gen", "version": "0.1", "argv": [sys.executable, str(script), "{image}", "{mesh}"]})
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "gen",
                                           "inputs": {"image": {"path": "image.txt"}},
                                           "steps": [{"id": "gen", "operation": "external.run", "inputs": {"image": "$inputs.image"},
                                                      "parameters": {"config": "generator.json"}, "outputs": {"mesh": "model.txt"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    assert Path(result["steps"]["gen"]["artifacts"]["mesh"]).read_text() == "sheet generated"
    assert result["steps"]["gen"]["observations"][0]["tool"] == "gen"


def test_blender_argv_order_and_validation(tmp_path):
    argv = launcher.argv_for("/opt/blender", "/x/observe.py", "/x/scene.blend", ["--out", "o.json"])
    assert argv == ["/opt/blender", "--background", "/x/scene.blend", "--python", "/x/observe.py", "--", "--out", "o.json"]
    assert launcher.argv_for("/opt/blender", "/x/s.py", background=False) == ["/opt/blender", "--python", "/x/s.py"]
    with pytest.raises(ToolError, match="絶対パス"):
        launcher.run_blender("blender", tmp_path / "s.py")
    fake = tmp_path / "blender"
    fake.write_text("", encoding="utf-8")
    with pytest.raises(ToolError, match="スクリプト"):
        launcher.run_blender(fake, tmp_path / "missing.py")
    assert launcher.blender_version(tmp_path / "nope") is None


def test_blender_launcher_runs_a_stand_in_and_records_version(tmp_path):
    """Blender の代役（引数を書き出す Python）で、起動・ログ・版の記録を確かめる。本物の Blender は未検証。"""
    stand_in = tmp_path / "blender.py"
    stand_in.write_text("import sys\nif sys.argv[1] == '--version':\n    print('Blender 4.5.15')\nelse:\n    print(' '.join(sys.argv[1:]))\n", encoding="utf-8")
    wrapper = tmp_path / "blender"
    wrapper.write_text(f"#!{sys.executable}\nimport runpy, sys\nsys.argv = sys.argv\nrunpy.run_path({str(stand_in)!r}, run_name='__main__')\n", encoding="utf-8")
    wrapper.chmod(0o755)
    script = tmp_path / "s.py"
    script.write_text("", encoding="utf-8")
    record = launcher.run_blender(wrapper, script, args=["--out", "x"], log=tmp_path / "b.log")
    assert record["exit_code"] == 0 and record["blender_version"] == "4.5.15"
    assert "--python" in (tmp_path / "b.log").read_text(encoding="utf-8")


def test_observe_builds_a_valid_observation_without_bpy():
    objects = [{"name": "Body", "type": "MESH", "dimensions": [0.4, 0.2, 1.6], "modifiers": [{"name": "Armature", "type": "ARMATURE"}],
                "matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "vertices": 1200, "faces": 2400,
                "vertex_groups": {"Hips": {"count": 100, "min": 0.1, "max": 1.0}}, "shape_keys": ["Basis", "sash_fix"]},
               {"name": "Rig", "type": "ARMATURE", "dimensions": [0.5, 0.3, 1.7], "modifiers": [],
                "matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
                "bones": [{"name": "Hips", "parent": None, "head": [0, 0, 1], "tail": [0, 0, 1.1], "rest_matrix": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]}]}]
    data = observe.build_observation("Scene", objects, "/x/scene.blend", "a" * 64, blender_version="4.5.15")
    contracts.validate("observation", data)
    names = {f["name"] for f in data["facts"]}
    assert {"type", "dimensions", "vertex_count", "face_count", "vertex_groups", "shape_keys", "bones", "modifiers"} <= names
    assert data["coverage"]["objects"] == ["Body", "Rig"] and "pose frames" in data["coverage"]["unmeasured"]
    assert data["evidence"][0]["state"] == "observed" and "4.5.15" in data["evidence"][0]["method"]["version"]
    args = observe.parse_args(["--out", "o.json", "--objects", "Body", "--units", "cm"])
    assert args.objects == ["Body"] and args.units == "cm"


def test_observation_from_mesh_and_dangling_evidence():
    from m3d_helpers import box
    data = observation.from_mesh(box(2.0), "/x/box.json", "b" * 64)
    contracts.validate("observation", data)
    values = {f["name"]: f["value"] for f in data["facts"]}
    assert values["closed"] is True and abs(values["signed_volume"] - 8.0) < 1e-9 and values["vertex_count"] == 8
    with pytest.raises(ToolError, match="evidence_ref"):
        observation.new({"kind": "mesh", "id": "m"}, [{"id": "f", "name": "n", "value": 1, "type": "integer", "evidence_ref": "missing"}],
                        [], [{"sha256": "c" * 64}])


def test_rea_evidence_from_a_summary_and_an_analysis_report(tmp_path):
    summary = {"schema": "rea-real-3d-tools-experiment/0.1",
               "blockbench": {"source_commit": "e" * 40, "passed": 11, "total": 11},
               "jscad": {"source_commit": "f" * 40, "final_all_pass": True},
               "rea": {"blockbench": {"files": 2, "nodes": 10, "relations": 9, "unknowns": 3, "coverage": "unknown",
                                      "queries": [{"target": "mergeVertices", "status": "found"}, {"target": "property:uv", "status": "ambiguous"}]},
                       "jscad": {"files": 4, "nodes": 5, "relations": 4, "unknowns": 1, "coverage": "unknown",
                                 "queries": [{"target": "extrudeLinear", "status": "found"}, {"target": "x", "status": "no-match"}]}},
               "limits": ["Only the named features were reconstructed."]}
    atomic_json(tmp_path / "summary.json", summary)
    result = evidence.from_summary(tmp_path / "summary.json")
    states = {e["ref"].split(":", 1)[1]: e["state"] for e in result["tools"]["blockbench"]["evidence"] if ":" in e["ref"]}
    assert states["mergeVertices"] == "observed" and states["property:uv"] == "inferred"
    assert result["tools"]["blockbench"]["evidence"][0]["state"] == "unknown"       # coverage unknown はそのまま
    assert result["tools"]["jscad"]["evidence"][-1]["state"] == "observed"          # 比較は合格
    assert result["rea"]["commit"] == REA_COMMIT and result["limitations"]
    report = {"tool": "jscad", "repository": "jscad/OpenJSCAD.org", "commit": "f" * 40, "rea": {"version": "6.4.0", "commit": REA_COMMIT},
              "files": [{"path": "a.js", "sha256": "1" * 64, "bytes": 10, "coverage": {"status": "complete"}, "limitations": ["x"]},
                        {"path": "b.js", "sha256": "2" * 64, "bytes": 10, "coverage": {"status": "partial"}, "limitations": []}],
              "graph": {"id": "g", "nodes": 3, "relations": 2, "unknowns": 1, "coverage": {"status": "unknown"}},
              "queries": [{"target": "extrudeLinear", "file": "q.json", "status": "found", "coverage": {"status": "partial"}},
                          {"target": "property:height", "file": "p.json", "status": "partial", "coverage": {"status": "partial"}}],
              "limitations": ["static only"]}
    atomic_json(tmp_path / "report.json", report)
    result = evidence.from_analysis_report(tmp_path / "report.json")
    assert [e["state"] for e in result["evidence"]] == ["observed", "inferred", "unknown", "observed", "inferred"]
    assert result["notes"] == []
    report["rea"]["version"] = "7.0.0"
    atomic_json(tmp_path / "report.json", report)
    assert "7.0.0" in evidence.from_analysis_report(tmp_path / "report.json")["notes"][0]
    with pytest.raises(ToolError, match="schema"):
        evidence.from_summary(tmp_path / "report.json")
