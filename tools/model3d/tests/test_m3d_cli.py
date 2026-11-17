"""CLI: デモ → 実行 → 受け渡し → 判定、契約の検証、検査、メッシュの変換。"""
import json

import pytest

from m3d_helpers import run_cli
from model3d import contracts
from model3d.io import atomic_json, read_json, sha256


def test_demo_run_handoff_accept_end_to_end(tmp_path):
    code, created, err = run_cli("demo", tmp_path / "demo")
    assert code == 0, err
    assert {k: created["expected"][k] for k in ("inside_before", "inside_after", "welded_vertices")} == {"inside_before": 15, "inside_after": 0, "welded_vertices": 35}
    assert created["expected"]["generate_status"] == "needs_review" and (tmp_path / "demo" / "recipe_generate.json").is_file()
    for kind, name in (("preset", "preset.json"), ("semantic-map", "semantic_map.json"), ("recipe", "recipe.json")):
        contracts.load(kind, tmp_path / "demo" / name)
    contracts.load("rig", tmp_path / "demo" / "inputs" / "rig.json")
    contracts.load("mesh", tmp_path / "demo" / "inputs" / "sash.json")

    code, result, err = run_cli("run", tmp_path / "demo" / "recipe.json", "--out", tmp_path / "runs", "--quiet")
    assert code == 0, err
    assert result["status"] == "succeeded" and result["candidates"] == []
    steps = result["steps"]
    assert steps["weld"]["observations"][0]["vertices_after"] == 35 and steps["weld"]["observations"][0]["merged_groups"] == 7
    assert steps["before"]["observations"][0]["inside"] == 15 and steps["before"]["status"] == "succeeded"
    assert steps["push"]["observations"][0]["moved"] == 15 and steps["push"]["observations"][0]["residual_inside"] == 0
    assert steps["after"]["observations"][0]["inside"] == 0 and steps["structure"]["observations"][0]["pass"]
    assert steps["structure"]["observations"][0]["stats"]["weights"]["vertex_count"] == 35
    assert steps["structure"]["observations"][0]["stats"]["mesh_binding"]["vertex_count"] == 35
    rig = contracts.load("rig", steps["weld"]["artifacts"]["rig"])
    mapping = read_json(steps["weld"]["artifacts"]["mapping"])
    assert len(mapping["old_to_new"]) == 42 and set(mapping["old_to_new"]) == set(range(35))
    assert rig["weights"]["topology_sha256"] == mapping["target_topology_sha256"]
    assert rig["weights"]["topology_sha256"] == steps["structure"]["observations"][0]["stats"]["mesh_binding"]["topology_sha256"]
    code, binding, err = run_cli("check", "structure", steps["weld"]["artifacts"]["rig"], "--mesh", steps["push"]["artifacts"]["mesh"])
    assert code == 0 and binding["pass"], err
    code, mismatch, err = run_cli("check", "structure", tmp_path / "demo" / "inputs" / "rig.json",
                                 "--mesh", steps["push"]["artifacts"]["mesh"])
    assert code == 1 and not mismatch["pass"] and "頂点数" in " ".join(mismatch["failures"]), err
    assert steps["sleeve"]["observations"][0]["closed"] and steps["leg"]["observations"][0]["closed"]
    run_dir = tmp_path / "runs" / result["run_id"]
    record = contracts.load("run", run_dir / "run_record.json")
    assert record["preset"]["sha256"] == sha256(tmp_path / "demo" / "preset.json")
    assert (run_dir / "export" / "sash_fixed.obj").read_text(encoding="utf-8").count("\nv ") == 35
    deltas = read_json(run_dir / "push" / "sash_deltas.json")
    assert len(deltas["deltas"]) == 15 and deltas["topology_sha256"] == record["steps"][2]["observations"][0]["topology_sha256"]

    code, hand, err = run_cli("handoff", run_dir, "push/mesh", tmp_path / "handoff.json")
    assert code == 0, err
    data = contracts.load("handoff", tmp_path / "handoff.json")
    assert data["artifact"]["sha256"] == record["steps"][4]["artifacts"]["mesh"]["sha256"] and data["run_status"] == "succeeded"
    code, _, err = run_cli("handoff", run_dir, "sash_fixed.obj", tmp_path / "handoff2.json")
    assert code == 0, err
    code, _, err = run_cli("handoff", run_dir, "nope/none", tmp_path / "handoff3.json")
    assert code == 1 and "成果物" in err

    code, accepted, err = run_cli("accept", run_dir, "--decision", "accepted", "--by", "tester", "--note", "demo")
    assert code == 0 and accepted["adopted"]["decision"] == "accepted"
    code, edited, err = run_cli("edit", run_dir, "--by", "tester", "--what", "手で 1 点", "--minutes", "3")
    assert code == 0 and edited["human_edits"] == 1

    code, again, err = run_cli("run", tmp_path / "demo" / "recipe.json", "--out", tmp_path / "runs", "--quiet")
    assert code == 0 and all(s["reused_from"] == result["run_id"] for s in again["steps"].values())


def test_run_exit_code_reflects_failure(tmp_path):
    run_cli("demo", tmp_path / "demo")
    recipe = read_json(tmp_path / "demo" / "recipe.json")
    recipe["steps"][5]["parameters"]["strict"] = True
    recipe["steps"][4]["parameters"]["clearance"] = 0.0
    recipe["steps"][5]["inputs"]["mesh"] = "$steps.weld.mesh"      # 補正前の帯を strict に検査 → 失敗
    atomic_json(tmp_path / "demo" / "recipe.json", recipe)
    code, result, err = run_cli("run", tmp_path / "demo" / "recipe.json", "--out", tmp_path / "runs", "--quiet")
    assert code == 1 and result["status"] == "failed" and "不合格" in result["error"]


def test_demo_rejects_original_weights_for_the_processed_mesh(tmp_path):
    run_cli("demo", tmp_path / "demo")
    recipe = read_json(tmp_path / "demo" / "recipe.json")
    structure = next(s for s in recipe["steps"] if s["id"] == "structure")
    structure["inputs"]["rig"] = "$inputs.rig"
    structure["parameters"]["strict"] = True
    atomic_json(tmp_path / "demo" / "recipe.json", recipe)
    code, result, _ = run_cli("run", tmp_path / "demo" / "recipe.json", "--out", tmp_path / "runs", "--quiet")
    assert code == 1 and result["status"] == "failed" and "頂点数" in result["error"]


def test_validate_check_mesh_and_ops_commands(tmp_path):
    run_cli("demo", tmp_path / "demo")
    code, result, _ = run_cli("validate", "recipe", tmp_path / "demo" / "recipe.json")
    assert code == 0 and result["valid"]
    code, _, err = run_cli("validate", "mesh", tmp_path / "demo" / "recipe.json")
    assert code == 1 and "形式が違います" in err
    code, report, _ = run_cli("check", "structure", tmp_path / "demo" / "inputs" / "rig.json", "--semantic-map",
                              tmp_path / "demo" / "semantic_map.json", "--required", "humanoid")
    assert code == 0 and report["pass"]
    code, report, _ = run_cli("check", "structure", tmp_path / "demo" / "inputs" / "rig.json", "--required", "hips")
    assert code == 1 and not report["pass"]
    code, info, _ = run_cli("mesh", "info", tmp_path / "demo" / "inputs" / "sash.json")
    assert code == 0 and info["vertices"] == 42 and info["parts"] == {"garment_sash_front": 24}
    code, conv, _ = run_cli("mesh", "convert", tmp_path / "demo" / "inputs" / "sash.json", tmp_path / "sash.obj")
    assert code == 0 and (tmp_path / "sash.obj").exists()
    code, back, _ = run_cli("mesh", "convert", tmp_path / "sash.obj", tmp_path / "sash2.json")
    assert code == 0 and back["topology_sha256"] == info["topology_sha256"]
    code, ops, _ = run_cli("ops")
    names = {o["operation"] for o in ops}
    assert {"mesh.weld", "mesh.extrude_profile", "collision.push_out", "check.structure", "check.penetration", "check.continuity",
            "external.run", "blender.run_script", "mesh.export_obj", "mesh.import", "mesh.transform"} <= names
    atomic_json(tmp_path / "track.json", {"frames": [[0, [[0, 0, 0]]], [1, [[0, 0, 2]]]]})
    code, report, _ = run_cli("check", "continuity", tmp_path / "track.json", "--max-jump", "0.5")
    assert code == 1 and report["jumps"]
    atomic_json(tmp_path / "bad.json", {"frames": [{"t": 0}, [1, [[0, 0, 0]]]]})
    code, _, err = run_cli("check", "continuity", tmp_path / "bad.json", "--max-jump", "0.5")
    assert code == 1 and err.startswith("Error:")


def test_manifest_and_semantic_commands(tmp_path):
    run_cli("demo", tmp_path / "demo")
    code, result, err = run_cli("manifest", tmp_path / "asset.json", "--asset-id", "demo", "--input",
                                f"sash={tmp_path / 'demo' / 'inputs' / 'sash.json'}", "--input", f"rig={tmp_path / 'demo' / 'inputs' / 'rig.json'}",
                                "--part", "garment_sash_front")
    assert code == 0, err
    data = contracts.load("asset-manifest", tmp_path / "asset.json")
    assert {i["name"]: i["kind"] for i in data["inputs"]} == {"sash": "mesh", "rig": "json"}
    code, result, err = run_cli("manifest", tmp_path / "asset2.json", "--asset-id", "demo", "--input",
                                f"rig={tmp_path / 'demo' / 'inputs' / 'rig.json'}:other")
    assert code == 0 and contracts.load("asset-manifest", tmp_path / "asset2.json")["inputs"][0]["kind"] == "other"
    code, result, err = run_cli("semantic", "build", tmp_path / "demo" / "preset.json", tmp_path / "map.json", "--asset-id", "demo",
                                "--parts", "garment_sash_front,ghost", "--rig", tmp_path / "demo" / "inputs" / "rig.json", "--bones", "Extra:mixamorig:Hips")
    assert code == 0 and result["unmapped"] == {"parts": ["ghost"], "bones": ["Extra"]}
    code, result, err = run_cli("semantic", "confirm", tmp_path / "map.json", "--kind", "parts", "--key", "garment_sash_front",
                                "--state", "confirmed", "--by", "tester")
    assert code == 0 and contracts.load("semantic-map", tmp_path / "map.json")["parts"]["garment_sash_front"]["state"] == "confirmed"


def test_check_penetration_command_matches_the_check(tmp_path):
    """check penetration: 2 つのメッシュを読んで checks.penetration を呼び、合否を終了コードにする。"""
    from m3d_helpers import box
    from model3d import checks
    from model3d.mesh import Mesh
    box(1.0).save(tmp_path / "collider.json")
    box(0.5).save(tmp_path / "inside.json")                       # 頂点 8 つとも衝突相手の中
    box(0.5, center=(3.0, 0.0, 0.0)).save(tmp_path / "outside.json")
    for name, code_expected, inside in (("inside", 1, 8), ("outside", 0, 0)):
        code, report, err = run_cli("check", "penetration", tmp_path / f"{name}.json", tmp_path / "collider.json")
        assert code == code_expected and report["inside"] == inside, err
        assert report == checks.penetration(Mesh.load(tmp_path / f"{name}.json"), Mesh.load(tmp_path / "collider.json"))
    closed = box(1.0)
    Mesh(closed.vertices, closed.faces[:-1], None, "open", closed.units, closed.axes, {}).save(tmp_path / "open.json")
    code, _, err = run_cli("check", "penetration", tmp_path / "inside.json", tmp_path / "open.json")
    assert code == 1 and err.startswith("Error:") and "閉じていません" in err


def test_rea_report_and_summary_commands_write_the_evidence(tmp_path):
    """rea report / rea summary: REA の出力を読んで evidence を書き、数を返す（rea pyc は test_m3d_generate）。"""
    from model3d.rea_bridge import evidence
    summary = {"schema": "rea-real-3d-tools-experiment/0.1", "jscad": {"source_commit": "f" * 40, "final_all_pass": True},
               "rea": {"jscad": {"files": 4, "nodes": 5, "relations": 4, "unknowns": 1, "coverage": "unknown",
                                 "queries": [{"target": "extrudeLinear", "status": "found"}, {"target": "x", "status": "no-match"}]}},
               "limits": ["Only the named features were reconstructed."]}
    report = {"tool": "jscad", "repository": "jscad/OpenJSCAD.org", "commit": "f" * 40,
              "files": [{"path": "a.js", "sha256": "1" * 64, "bytes": 10, "coverage": {"status": "complete"}, "limitations": []}],
              "graph": {"id": "g", "nodes": 3, "relations": 2, "unknowns": 1, "coverage": {"status": "unknown"}},
              "queries": [{"target": "extrudeLinear", "file": "q.json", "status": "found", "coverage": {"status": "partial"}}],
              "limitations": ["static only", "no runtime"]}
    atomic_json(tmp_path / "summary.json", summary)
    atomic_json(tmp_path / "report.json", report)
    code, result, err = run_cli("rea", "summary", tmp_path / "summary.json", tmp_path / "from_summary.json")
    assert code == 0, err
    assert read_json(tmp_path / "from_summary.json") == evidence.from_summary(tmp_path / "summary.json")
    assert result == {"out": str((tmp_path / "from_summary.json").resolve()), "evidence": 4, "limitations": 1}
    code, result, err = run_cli("rea", "report", tmp_path / "report.json", tmp_path / "from_report.json")
    assert code == 0, err
    assert read_json(tmp_path / "from_report.json") == evidence.from_analysis_report(tmp_path / "report.json")
    assert result == {"out": str((tmp_path / "from_report.json").resolve()), "evidence": 3, "limitations": 2}
    code, _, err = run_cli("rea", "summary", tmp_path / "report.json", tmp_path / "x.json")
    assert code == 1 and err.startswith("Error:") and "schema" in err and not (tmp_path / "x.json").exists()


# 代役の Blender: observe.py の引数（-- の後ろ）を本物と同じ parse_args で読み、骨の無い物体を並べた観測を --out に書く。
# 物体の名前に broken があれば、形の違う JSON を書く
OBSERVE_STAND_IN = r'''
import hashlib, json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
from model3d.blender_adapter import observe
args = observe.parse_args(sys.argv[sys.argv.index("--") + 1:])
blend = next(a for a in sys.argv if a.endswith(".blend"))
names = args.objects or ["Cube"]
objects = [{"name": name, "type": "MESH", "dimensions": [0.4, 0.2, 1.6], "modifiers": [],
            "matrix_world": [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], "vertices": 8, "faces": 6,
            "vertex_groups": {}, "shape_keys": []} for name in names]
data = observe.build_observation("Scene", objects, blend, hashlib.sha256(Path(blend).read_bytes()).hexdigest(), units=args.units,
                                 blender_version="5.2.4")
Path(args.out).write_text(json.dumps({"broken": True} if "broken" in names else data), encoding="utf-8")
'''


def test_blender_observe_command_runs_the_observe_script_and_validates_the_observation(tmp_path):
    """blender observe: 観測の台本と .blend と引数（--out・--units・--objects）を Blender に渡し、書かれた観測を契約で確かめる。
    本物の Blender は使わない（CI に無い）。"""
    from m3d_helpers import stand_in_executable
    from model3d.blender_adapter import launcher
    blender = stand_in_executable(tmp_path, "blender", OBSERVE_STAND_IN)
    (tmp_path / "scene.blend").write_bytes(b"BLENDER-stand-in")
    out = tmp_path / "obs" / "observation.json"
    out.parent.mkdir()
    code, result, err = run_cli("blender", "observe", "--blender", blender, "--blend", tmp_path / "scene.blend", "--out", out,
                                "--objects", "Body", "Hair", "--units", "cm")
    assert code == 0, err
    data = contracts.load("observation", out)
    assert data["coverage"]["objects"] == ["Body", "Hair"]
    assert {f["id"]: f["value"] for f in data["facts"]}["object:Body:dimensions"] == [40.0, 20.0, 160.0]   # cm で書いた
    assert result["out"] == str(out.resolve()) and result["exit_code"] == 0 and result["blender_version"] == "5.2.4"
    argv = result["argv"]
    assert argv[argv.index("--python") + 1] == str(launcher.OBSERVE_SCRIPT) and str((tmp_path / "scene.blend").resolve()) in argv
    assert argv[argv.index("--") + 1:] == ["--out", str(out.resolve()), "--units", "cm", "--objects", "Body", "Hair"]
    assert out.with_suffix(".log").is_file()                                   # ログは観測の隣
    code, _, err = run_cli("blender", "observe", "--blender", blender, "--blend", tmp_path / "scene.blend", "--out", out,
                           "--objects", "broken")
    assert code == 1 and err.startswith("Error:")                              # 形の違う観測は契約で止める
