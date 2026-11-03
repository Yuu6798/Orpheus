"""設定画から初期モデルを作る工程: 前処理の記録との照合、代役の生成器での一気通貫、検査、記録、CLI。"""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import box, run_cli
from model3d import contracts, demo, executor, generate
from model3d.io import ToolError, atomic_json, read_json, sha256
from model3d.mesh import Mesh, canonical_signature, compare_geometry, read_obj, write_obj

FIXTURE = Path(__file__).parent / "data" / "prepare_reference_cases.json"


def _synthetic_cases():
    """検証の記録と同じ手順で合成の RGBA を作る（numpy の default_rng(20261103)、同じ形と順）。"""
    rng = np.random.default_rng(20261103)
    cases = []
    for index, (height, width) in enumerate([(13, 20), (20, 13), (16, 16), (17, 17), (5, 8), (8, 5)]):
        data = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
        data[:, :, 3] = 0
        data[1:-1, 1:-1, 3] = rng.integers(1, 256, (height - 2, width - 2), dtype=np.uint8)
        cases.append((f"synthetic_{index}", data))
    return cases


def test_prepare_reference_matches_the_recorded_verification_cases():
    """2026-11-03 の検証で原本の関数と画素単位で一致した合成 6 件と、sha256・切り出し・辺の長さが一致する。"""
    recorded = {c["name"]: c for c in json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"]}
    for name, pixels in _synthetic_cases():
        rgb, metadata = generate.prepare_reference(pixels)
        expect = recorded[name]
        assert list(rgb.shape) == expect["shape"], name
        assert __import__("hashlib").sha256(rgb.tobytes()).hexdigest() == expect["sha256"], name
        for key in ("crop_xyxy_exclusive_compat", "square_side", "output_side", "foreground_ratio", "background_rgb_float"):
            assert metadata[key] == expect["contract"][key], (name, key)


def test_prepare_reference_rejections_match_the_record():
    rejections = {r["name"] for r in json.loads(FIXTURE.read_text(encoding="utf-8"))["rejections"]}
    assert rejections == {"empty_alpha", "single_pixel"}
    with pytest.raises(ToolError, match="前景"):
        generate.prepare_reference(np.zeros((8, 8, 4), dtype=np.uint8))
    single = np.pad(np.full((1, 1, 4), 255, dtype=np.uint8), ((2, 2), (2, 2), (0, 0)))
    with pytest.raises(ToolError, match="2 行 2 列"):
        generate.prepare_reference(single)
    with pytest.raises(ToolError, match="foreground_ratio"):
        generate.prepare_reference(generate.synthetic_sheet(), foreground_ratio=0)
    with pytest.raises(ToolError, match="RGBA"):
        generate.prepare_reference(np.zeros((8, 8, 3), dtype=np.uint8))


def test_prepare_file_writes_png_and_hashes(tmp_path):
    sheet = tmp_path / "sheet.png"
    generate.save_rgba(generate.synthetic_sheet(), sheet)
    metadata = generate.prepare_file(sheet, tmp_path / "out" / "prepared.png", 0.85)
    assert metadata["input"]["sha256"] == sha256(sheet) and metadata["input"]["width"] == 240 and metadata["input"]["height"] == 360
    prepared = Path(metadata["prepared"]["path"])
    assert prepared.is_file() and metadata["prepared"]["sha256"] == sha256(prepared)
    rgb = generate.load_rgba(prepared)
    assert rgb.shape[0] == rgb.shape[1] == metadata["output_side"]
    assert (rgb[0, 0, :3] == 127).all()      # 余白は灰色 0.5 に合成（uint8 では 127）


def test_obj_vertex_colors_round_trip_and_inspect(tmp_path):
    mesh = box(2.0)
    mesh.extra["x_vertex_colors"] = [[i / 8, 0.5, 1 - i / 8] for i in range(mesh.vertex_count)]
    write_obj(mesh, tmp_path / "colored.obj")
    text = (tmp_path / "colored.obj").read_text(encoding="utf-8")
    assert text.count("\nv ") == 8 and " 0.5 " in text
    back = read_obj(tmp_path / "colored.obj")
    assert np.allclose(back.extra["x_vertex_colors"], mesh.extra["x_vertex_colors"])
    checks = generate.inspect_mesh(back)
    assert checks["vertices"] == 8 and checks["triangles"] == 12 and checks["components"] == 1
    assert checks["closed"] and checks["winding_consistent"] and checks["vertex_colors"] and not checks["scale_calibrated"]
    assert checks["extent"] == [2.0, 2.0, 2.0] and checks["finite"]
    two = Mesh(np.vstack([mesh.vertices, mesh.vertices + 5]), mesh.faces + [[i + 8 for i in f] for f in mesh.faces])
    assert two.components() == 2
    flipped = Mesh(mesh.vertices, [list(reversed(mesh.faces[0]))] + mesh.faces[1:])
    assert not flipped.edge_report()["consistent_winding"] and flipped.components() == 1


def test_canonical_signature_ignores_enumeration_but_not_winding():
    vertices = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    colors = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]
    first = Mesh(vertices, [[0, 1, 2]], extra={"x_vertex_colors": colors})
    reordered = Mesh(vertices[[2, 0, 1]], [[2, 0, 1]], extra={"x_vertex_colors": [colors[2], colors[0], colors[1]]})
    reverse = Mesh(vertices, [[0, 2, 1]], extra={"x_vertex_colors": colors})
    recolored = Mesh(vertices, [[0, 1, 2]], extra={"x_vertex_colors": [[0, 0, 0]] * 3})
    assert compare_geometry(first, reordered)["same"]
    assert not compare_geometry(first, reverse)["same"]
    assert not compare_geometry(first, recolored)["same"] and compare_geometry(first, recolored, colors=False)["same"]
    signature = canonical_signature(first)
    assert signature["vertices"] == 3 and signature["triangles"] == 1 and signature["colors"] and not signature["duplicate_positions"]
    doubled = Mesh(np.vstack([vertices, vertices[:1]]), [[0, 1, 2], [3, 1, 2]])
    assert canonical_signature(doubled)["duplicate_positions"]


def test_demo_generate_recipe_runs_with_the_stand_in_and_stays_needs_review(tmp_path):
    created = demo.create(tmp_path / "demo")
    assert created["expected"]["generate_status"] == "needs_review"
    contracts.load("recipe", tmp_path / "demo" / "recipe_generate.json")
    result = executor.run_recipe(tmp_path / "demo" / "recipe_generate.json", tmp_path / "runs")
    assert result["status"] == "needs_review" and result["candidates"] == ["generate"] and result["error"] is None
    steps = result["steps"]
    assert steps["prepare"]["status"] == "succeeded" and steps["generate"]["status"] == "needs_review"
    assert steps["inspect"]["status"] == "succeeded" and steps["export"]["status"] == "succeeded"
    assert generate.REVIEW_NOTE in steps["generate"]["unresolved"]
    observation = steps["generate"]["observations"][0]
    assert observation["backend"]["kind"] == "stand_in" and observation["vertex_colors"] and observation["components"] == 1
    assert observation["closed"] and observation["winding_consistent"] and not observation["scale_calibrated"]
    record = contracts.load("initial-model", steps["generate"]["artifacts"]["record"])
    assert record["review"] == "needs_review" and record["single_view_only"] and record["backend"]["tool"] == "stand-in"
    assert record["backend"]["exit_code"] == 0 and len(record["backend"]["executable_sha256"]) == 64
    assert record["preparation"]["prepared"]["sha256"] == sha256(steps["generate"]["artifacts"]["prepared"])
    assert record["outputs"]["obj"]["sha256"] == sha256(record["outputs"]["obj"]["path"])
    assert record["backend"]["result"]["foreground_bbox_xyxy"] and record["parameters"]["resolution"] == 64
    mesh = Mesh.load(steps["generate"]["artifacts"]["mesh"])
    assert len(mesh.extra["x_vertex_colors"]) == mesh.vertex_count and mesh.extra["x_scale_calibrated"] is False
    assert steps["inspect"]["observations"][0]["canonical_sha256"] == canonical_signature(mesh)["sha256"]
    assert Path(steps["export"]["artifacts"]["obj"]).read_text(encoding="utf-8").count("\nv ") == 8
    again = executor.run_recipe(tmp_path / "demo" / "recipe_generate.json", tmp_path / "runs")
    assert again["steps"]["prepare"]["reused_from"] == result["run_id"]       # 前処理は再利用できる
    assert again["steps"]["generate"]["reused_from"] is None                     # 生成は毎回実行する


def test_initial_model_rejects_bad_backend_configs(tmp_path):
    demo.create(tmp_path / "demo")
    recipe = read_json(tmp_path / "demo" / "recipe_generate.json")
    config = read_json(tmp_path / "demo" / "generator.json")
    del config["outputs"]
    atomic_json(tmp_path / "demo" / "generator.json", config)
    result = executor.run_recipe(tmp_path / "demo" / "recipe_generate.json", tmp_path / "runs")
    assert result["status"] == "failed" and "outputs.obj" in result["error"]
    silent = {"tool": "silent", "version": "1", "kind": "stand_in", "argv": [sys.executable, "-c", "pass"],
              "outputs": {"obj": "never.obj"}}
    atomic_json(tmp_path / "demo" / "generator.json", silent)
    result = executor.run_recipe(tmp_path / "demo" / "recipe_generate.json", tmp_path / "runs")
    assert result["status"] == "failed" and "OBJ を書いていません" in result["error"]
    recipe["steps"][1]["parameters"]["backend"] = {"tool": "x", "version": "1", "kind": "nope", "argv": [sys.executable, "-c", "pass"],
                                                   "outputs": {"obj": "a.obj"}}
    atomic_json(tmp_path / "demo" / "recipe_generate.json", recipe)
    result = executor.run_recipe(tmp_path / "demo" / "recipe_generate.json", tmp_path / "runs")
    assert result["status"] == "failed" and "kind" in result["error"]


def test_generate_and_mesh_cli_commands(tmp_path):
    run_cli("demo", tmp_path / "demo")
    code, metadata, err = run_cli("generate", "prepare", tmp_path / "demo" / "inputs" / "sheet_front.png", tmp_path / "prepared.png")
    assert code == 0, err
    assert metadata["output_side"] > metadata["square_side"] and (tmp_path / "prepared.png").is_file()
    code, result, err = run_cli("run", tmp_path / "demo" / "recipe_generate.json", "--out", tmp_path / "runs", "--quiet")
    assert code == 0 and result["status"] == "needs_review"
    mesh_path = result["steps"]["generate"]["artifacts"]["mesh"]
    code, report, _ = run_cli("generate", "inspect", mesh_path, "--out", tmp_path / "inspect.json")
    assert code == 0 and report["check"] == "initial_model" and report["vertices"] == 8 and (tmp_path / "inspect.json").is_file()
    code, valid, _ = run_cli("validate", "initial-model", result["steps"]["generate"]["artifacts"]["record"])
    assert code == 0 and valid["valid"]
    obj = result["steps"]["export"]["artifacts"]["obj"]
    code, same, _ = run_cli("mesh", "compare", mesh_path, obj)
    assert code == 0 and same["same"] and same["left"]["colors"]
    code, differ, _ = run_cli("mesh", "compare", mesh_path, tmp_path / "demo" / "inputs" / "sash.json")
    assert code == 0 and not differ["same"]


def test_rea_pyc_evidence(tmp_path):
    from model3d.rea_bridge import evidence
    summary = {"analyzer": {"project": "morluto/rea", "version": "6.8.0", "commit": "9" * 40, "full_cli_or_mcp_invoked": False},
               "target": {"project": "VAST-AI-Research/TripoSR", "commit": "1" * 40, "selected_source_files": 2, "parsed_code_objects": 3},
               "verification": {"all_input_and_output_schemas": "pass", "all_source_correspondence_symbols_found": "pass"},
               "source_correspondence": [
                   {"id": "surface_extraction", "source": "tsr/system.py", "upstream_url": "https://example.invalid/system.py#L171",
                    "rea_code_object_index": 9, "rea_code_qualname": "TSR.extract_mesh", "rea_firstlineno": 171,
                    "rea_observed_names": ["query_triplane", "isosurface_helper"], "rea_bytecode_sha256": "a" * 64,
                    "source_reviewed_contract": "density on a grid, level surface at threshold 25, vertex colors"},
                   {"id": "no_contract", "source": "run.py", "rea_code_object_index": 0, "rea_code_qualname": "<module>",
                    "rea_firstlineno": 1, "rea_observed_names": [], "rea_bytecode_sha256": "b" * 64}],
               "limitations": ["parser API only"]}
    atomic_json(tmp_path / "summary.json", summary)
    result = evidence.from_pyc_evidence(tmp_path / "summary.json")
    assert [e["source"] for e in result["evidence"]] == ["rea", "oss", "rea"]
    assert result["evidence"][0]["state"] == "observed" and "TSR.extract_mesh" in result["evidence"][0]["ref"]
    assert result["evidence"][1]["ref"].startswith("https://example.invalid") and "threshold 25" in result["evidence"][1]["note"]
    assert any("6.8.0" in n for n in result["notes"]) and any("parser API" in n for n in result["notes"])
    assert result["limitations"] == ["parser API only"]
    summary["verification"]["all_input_and_output_schemas"] = "fail"
    atomic_json(tmp_path / "summary.json", summary)
    assert evidence.from_pyc_evidence(tmp_path / "summary.json")["evidence"][0]["state"] == "inferred"
    with pytest.raises(ToolError, match="source_correspondence"):
        evidence.from_pyc_evidence(FIXTURE)
    code, out, err = run_cli("rea", "pyc", tmp_path / "summary.json", tmp_path / "evidence.json")
    assert code == 0 and out["evidence"] == 3
