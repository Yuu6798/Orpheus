"""部品の一覧（mesh.list_parts と model3d mesh parts）と、uv の伸びの検査（check.uv_stretch）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import run_cli
from model3d import checks, mesh as m3
from model3d.io import ToolError, atomic_json

QUAD = np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=np.float32)


def _parts(path, uv=True, broken=False):
    tri = np.array([[0, 0, 0], [2, 0, 0], [0, 3, 1]], dtype=np.float32)
    arrays = {"names": np.array(["tri", "quad"]), "co0": tri, "f0": np.array([[0, 1, 2]], np.int32),
              "co1": QUAD, "f1": np.array([[0, 1, 2, 3]], np.int32)}
    if uv:
        arrays["uv0"] = np.array([[0, 0], [1, 0], [0, 1]], np.float32)
    if broken:
        arrays["co0"] = np.array([[0, 0, 0], [np.nan, 0, 0], [0, 3, 1]], dtype=np.float32)
        arrays["f1"] = np.array([[0, 1, 2, 9]], np.int32)
    np.savez(path, **arrays)
    return path


def test_the_list_counts_each_part_and_checks_the_normals(tmp_path):
    _parts(tmp_path / "parts.npz")
    np.savez(tmp_path / "normals.npz", n0=np.zeros((3, 3), np.float32), n1=np.zeros((5, 3), np.float32), n7=np.zeros((1, 3)))
    report = m3.list_parts(tmp_path / "parts.npz", tmp_path / "normals.npz")
    tri, quad = report["parts"]
    assert tri["name"] == "tri" and tri["vertices"] == 3 and tri["faces"] == 1 and tri["triangles"] == 1 and tri["sides"] == 3
    assert tri["bounds"] == {"min": [0.0, 0.0, 0.0], "max": [2.0, 3.0, 1.0]} and tri["uv"]["max"] == [1.0, 1.0]
    assert quad["sides"] == 4 and quad["triangles"] == 2 and quad["uv"] is None
    assert tri["normals"] == {"present": True, "shape_ok": True, "finite": True} and quad["normals"]["shape_ok"] is False
    assert report["totals"] == {"parts": 2, "vertices": 7, "faces": 2, "triangles": 3} and report["normals"]["stray"] == ["n7"]
    assert len(report["problems"]) == 2 and "n1" in report["problems"][0] and "n7" in report["problems"][1]
    clean = m3.list_parts(tmp_path / "parts.npz")
    assert clean["problems"] == [] and clean["normals"] is None and "normals" not in clean["parts"][0]


def test_broken_parts_are_listed_with_what_is_wrong(tmp_path):
    report = m3.list_parts(_parts(tmp_path / "parts.npz", broken=True))
    assert report["parts"][0]["finite"] is False and "bounds" not in report["parts"][0]
    assert report["parts"][1]["face_indices_ok"] is False
    assert any("非有限" in p for p in report["problems"]) and any("無い頂点" in p for p in report["problems"])


def test_non_finite_uv_and_normals_are_problems_and_the_report_stays_writable(tmp_path):
    from model3d.ops import get
    with np.load(_parts(tmp_path / "parts.npz")) as data:
        arrays = {key: data[key] for key in data.files}
    arrays["uv0"] = np.array([[0, 0], [np.nan, 0], [0, np.inf]], np.float32)               # 形は合うが、非有限の uv
    np.savez(tmp_path / "parts.npz", **arrays)
    np.savez(tmp_path / "normals.npz", n0=np.full((3, 3), np.nan, np.float32), n1=np.zeros((4, 3), np.float32))
    report = m3.list_parts(tmp_path / "parts.npz", tmp_path / "normals.npz")
    tri = report["parts"][0]
    assert tri["uv"] == {"shape_ok": True, "finite": False} and tri["normals"]["finite"] is False   # 非有限の範囲は書かない
    assert any("uv0 に非有限" in p for p in report["problems"]) and any("n0 に非有限" in p for p in report["problems"])
    result = get("mesh.list_parts").function({"parts": tmp_path / "parts.npz", "normals": tmp_path / "normals.npz"}, {},
                                             {"report": tmp_path / "r.json"}, Context(tmp_path))
    assert result.review and json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["problems"] == report["problems"]
    code, _, _ = run_cli("mesh", "parts", tmp_path / "parts.npz", "--normals", tmp_path / "normals.npz")
    assert code == 1                                                                         # 法線の非有限も、見つけたこととして数える


def _recipe(tmp_path, steps, inputs):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "parts", "inputs": inputs, "steps": steps})
    return tmp_path / "recipe.json"


def test_the_operation_writes_the_list_and_asks_for_review_when_something_is_wrong(tmp_path):
    from model3d import executor
    _parts(tmp_path / "parts.npz")
    np.savez(tmp_path / "normals.npz", n0=np.zeros((3, 3), np.float32))
    step = {"id": "list", "operation": "mesh.list_parts", "inputs": {"parts": "$inputs.parts"}, "outputs": {"report": "parts.json"}}
    result = executor.run_recipe(_recipe(tmp_path, [step], {"parts": {"path": "parts.npz"}}), tmp_path / "runs")
    assert result["status"] == "succeeded" and result["steps"]["list"]["observations"][0]["triangles"] == 3
    report = json.loads(Path(result["steps"]["list"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-parts/0.1"
    with_normals = dict(step, inputs={"parts": "$inputs.parts", "normals": "$inputs.normals"})
    review = executor.run_recipe(_recipe(tmp_path, [with_normals], {"parts": {"path": "parts.npz"}, "normals": {"path": "normals.npz"}}),
                                 tmp_path / "runs2")
    assert review["steps"]["list"]["status"] == "needs_review" and "n1" in review["steps"]["list"]["unresolved"][0]


def test_the_cli_prints_the_list_and_fails_on_problems(tmp_path):
    _parts(tmp_path / "parts.npz")
    code, result, _ = run_cli("mesh", "parts", tmp_path / "parts.npz")
    assert code == 0 and [p["name"] for p in result["parts"]] == ["tri", "quad"]
    np.savez(tmp_path / "normals.npz", n0=np.zeros((2, 3)))
    code, result, _ = run_cli("mesh", "parts", tmp_path / "parts.npz", "--normals", tmp_path / "normals.npz")
    assert code == 1 and len(result["problems"]) == 2                              # n0 の形違いと、n1 が無い


def _sheet(stretched=False, degenerate=False):
    """2×2 の格子の平らな面（uv は 3D と同じ割合）。stretched なら 1 つの三角形の uv を遠くへ伸ばす。"""
    vertices = np.array([[x, y, 0] for y in range(3) for x in range(3)], dtype=np.float64)
    faces = []
    for r in range(2):
        for c in range(2):
            a = r * 3 + c
            faces += [[a, a + 1, a + 4], [a, a + 4, a + 3]]
    uv = vertices[:, :2] / 2.0
    if stretched:
        vertices = np.vstack([vertices, [[0, 0, 0.01], [0.1, 0, 0.01], [0, 0.1, 0.01]]])
        uv = np.vstack([uv, [[0, 0], [1, 0], [0, 1]]])                             # 小さな三角形に、テクスチャ全体
        faces.append([9, 10, 11])
    if degenerate:
        vertices = np.vstack([vertices, [[0, 0, 0], [0, 0, 0], [0, 0, 0]]])
        uv = np.vstack([uv, [[0.1, 0.1], [0.2, 0.1], [0.1, 0.2]]])
        faces.append([len(vertices) - 3, len(vertices) - 2, len(vertices) - 1])
    return m3.Mesh(vertices, faces, uv, name="sheet")


def test_uv_stretch_measures_how_far_uv_triangles_are_stretched():
    even = checks.uv_stretch(_sheet(), 512, 512)
    assert even["triangles"] == 8 and even["degenerate"] == 0 and even["over"] == 0
    assert even["ratio_percentiles"] == {"p50": 1.0, "p99": 1.0, "p99_9": 1.0, "max": 1.0}
    assert even["uv_extent_px"]["max"] == 256.0 and even["pass"] is None             # 1 マスは uv で 0.5（512 の半分）。しきい値が無ければ測るだけ
    stretched = checks.uv_stretch(_sheet(stretched=True, degenerate=True), 512, 512, thresholds={"max_over": 0, "max_degenerate": 0})
    assert stretched["over"] == 1 and stretched["degenerate"] == 1 and stretched["pass"] is False and len(stretched["failures"]) == 2
    loose = checks.uv_stretch(_sheet(stretched=True), 512, 512, thresholds={"max_over": 1})
    assert loose["pass"] is True
    flat = m3.Mesh(_sheet().vertices, _sheet().faces)
    assert checks.uv_stretch(flat, 64, 64)["status"] == "skipped"


def test_a_mesh_whose_triangles_are_all_collapsed_counts_every_one():
    dot = m3.Mesh(np.zeros((3, 3)), [[0, 1, 2]], [[0, 0], [1, 0], [0, 1]], name="dot")       # 3 つの頂点が同じ位置の三角形だけ
    report = checks.uv_stretch(dot, 64, 64, thresholds={"max_degenerate": 0})
    assert report["degenerate"] == 1 and report["ratio_percentiles"] is None and report["over"] == 0
    assert report["pass"] is False and "潰れた" in report["failures"][0]                   # 比の正規化（全部 0）に惑わされない


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def test_the_check_takes_the_texture_size_from_an_image_or_a_parameter(tmp_path):
    from PIL import Image
    from model3d.ops import get
    _sheet(stretched=True).save(tmp_path / "sheet.json")
    Image.new("RGB", (256, 128)).save(tmp_path / "atlas.png")
    run = get("check.uv_stretch").function
    out = {"report": tmp_path / "r.json"}
    result = run({"mesh": tmp_path / "sheet.json", "texture": tmp_path / "atlas.png"}, {"max_over": 0}, out, Context(tmp_path))
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["texture_size"] == [256, 128] and result.review and "伸びが" in result.unresolved[0]
    run({"mesh": tmp_path / "sheet.json"}, {"texture_size": 1024, "report_only": True}, out, Context(tmp_path))
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["texture_size"] == [1024, 1024]
    for inputs, parameters, message in [({"mesh": tmp_path / "sheet.json"}, {}, "テクスチャの大きさ"),
                                        ({"mesh": tmp_path / "sheet.json", "texture": tmp_path / "atlas.png"}, {"texture_size": 64}, "一緒に"),
                                        ({"mesh": tmp_path / "sheet.json"}, {"texture_size": [64]}, "texture_size"),
                                        ({"mesh": tmp_path / "sheet.json"}, {"texture_size": 64, "wobble": 1}, "知らないパラメーター"),
                                        ({"mesh": tmp_path / "sheet.json"}, {"texture_size": 64, "threshold": 0}, "threshold")]:
        with pytest.raises(ToolError, match=message):
            run(inputs, parameters, out, Context(tmp_path))


def test_the_list_operation_refuses_parameters(tmp_path):
    from model3d.ops import get
    _parts(tmp_path / "parts.npz")
    with pytest.raises(ToolError, match="パラメーターはありません"):
        get("mesh.list_parts").function({"parts": tmp_path / "parts.npz"}, {"part": "tri"}, {"report": tmp_path / "r.json"}, Context(tmp_path))
