"""部位のラベルの移し替え（mesh.transfer_parts）と、numpy だけの最近傍（spatial.nearest）: 全部と比べた答え・cKDTree との一致、
同じ距離の選び方、升目の外の点、重心の型、npz と model3d のメッシュの入出力、遠い面の扱い、悪い設定。"""
import json
from pathlib import Path

import numpy as np
import pytest

from model3d import mesh as m3
from model3d import spatial, transfer
from model3d.io import ToolError, atomic_json


def _brute(points, queries):
    d2 = ((queries[:, None, :] - points[None, :, :]) ** 2).sum(axis=2)
    return d2.argmin(axis=1), np.sqrt(d2.min(axis=1))


def test_the_grid_search_finds_the_same_nearest_points_as_comparing_all():
    rng = np.random.default_rng(7)
    points = rng.random((3000, 3))
    queries = np.vstack([rng.random((800, 3)), [[5.0, 5.0, 5.0], [-3.0, 0.5, 0.5]]])   # 2 つは点の広がりの外
    index, distance, info = spatial.nearest(points, queries)
    expected_index, expected_distance = _brute(points, queries)
    assert np.array_equal(index, expected_index) and np.allclose(distance, expected_distance, rtol=0, atol=1e-12)
    assert info["passes"] >= 1 and info["brute_force"] >= 1                     # 外の点は全部と比べた
    small, _, small_info = spatial.nearest(points, queries[:50], cell=0.01)       # 小さい升目でも同じ答え
    assert np.array_equal(small, expected_index[:50]) and small_info["passes"] > 1


def test_the_grid_search_matches_cKDTree():
    cKDTree = pytest.importorskip("scipy.spatial").cKDTree
    rng = np.random.default_rng(11)
    points = rng.normal(size=(5000, 3)).astype(np.float32)
    queries = rng.normal(size=(2000, 3)).astype(np.float32)
    index, distance, _ = spatial.nearest(points, queries)
    kd_distance, kd_index = cKDTree(points).query(queries, k=1)
    assert np.array_equal(index, kd_index) and np.array_equal(distance, kd_distance)


def test_equal_distances_pick_the_smaller_index_and_bad_inputs_stop():
    index, distance, _ = spatial.nearest([[2, 0, 0], [0, 0, 0], [2, 0, 0]], [[1, 0, 0], [3, 0, 0]])
    assert index.tolist() == [0, 0] and distance.tolist() == [1.0, 1.0]
    with pytest.raises(ToolError, match="点がありません"):
        spatial.nearest(np.zeros((0, 3)), [[0, 0, 0]])
    with pytest.raises(ToolError, match="非有限"):
        spatial.nearest([[0, 0, np.nan]], [[0, 0, 0]])
    with pytest.raises(ToolError, match="升目"):
        spatial.nearest([[0, 0, 0]], [[0, 0, 0]], cell=-1)


def test_the_default_cell_keeps_a_few_points_per_filled_cell():
    xs, ys = np.meshgrid(np.linspace(0, 1, 200), np.linspace(0, 1, 200))
    sheet = np.c_[xs.ravel(), ys.ravel(), 0.3 * xs.ravel()]                       # 面の上の点
    cell = spatial.default_cell(sheet)
    cells = np.floor((sheet - sheet.min(axis=0)) / cell).astype(np.int64)
    occupancy = len(sheet) / len(np.unique(cells, axis=0))
    assert 1 <= occupancy <= 6


def test_centroids_keep_the_vertex_type_and_handle_mixed_faces():
    vertices = np.array([[0, 0, 0], [3, 0, 0], [0, 3, 0], [3, 3, 0]], dtype=np.float32)
    centroid = transfer.face_centroids(vertices, np.array([[0, 1, 2]]))
    assert centroid.dtype == np.float32 and np.allclose(centroid, [[1, 1, 0]])
    mixed = transfer.face_centroids(vertices.astype(np.float64), [[0, 1, 2], [0, 1, 3, 2]])
    assert np.allclose(mixed, [[1, 1, 0], [1.5, 1.5, 0]])


def _grid(x0, n=4):
    """x0 から始まる n×n の格子の三角形（頂点と面）。"""
    vertices = np.array([[x0 + i, j, 0] for j in range(n + 1) for i in range(n + 1)], dtype=np.float32)
    faces = []
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            faces += [[a, a + 1, a + n + 2], [a, a + n + 2, a + n + 1]]
    return vertices, np.array(faces, dtype=np.int32)


def _inputs(tmp_path, offset=1e-4, shuffle=True):
    left, right = _grid(0.0), _grid(10.0)
    np.savez(tmp_path / "parts.npz", names=np.array(["left", "right"]), co0=left[0], f0=left[1], co1=right[0], f1=right[1])
    vertices = np.vstack([left[0], right[0]]) + np.float32(offset)
    faces = np.vstack([left[1], right[1] + len(left[0])])
    order = np.random.default_rng(3).permutation(len(faces)) if shuffle else np.arange(len(faces))
    np.savez(tmp_path / "mesh.npz", co=vertices, faces=faces[order])
    expected = (np.r_[np.zeros(len(left[1])), np.ones(len(right[1]))].astype(np.int32))[order]
    return expected


def test_each_target_face_takes_the_part_of_the_nearest_source_face(tmp_path):
    expected = _inputs(tmp_path)
    names, labels, mesh, report = transfer.transfer_parts(tmp_path / "parts.npz", tmp_path / "mesh.npz")
    assert names == ["left", "right"] and mesh is None and np.array_equal(labels, expected)
    assert report["one_to_one"] == {"matched_once": 64, "unmatched": 0, "matched_more": 0}
    assert report["distance"]["max"] == pytest.approx(np.sqrt(3) * 1e-4, rel=2e-2) and report["units"].startswith("m")
    assert [p["target_faces"] for p in report["parts"]] == [32, 32] and report["unresolved"] == 0
    _, far, _, far_report = transfer.transfer_parts(tmp_path / "parts.npz", tmp_path / "mesh.npz", max_distance=1e-5)
    assert (far == -1).all() and far_report["unresolved"] == 64


def test_model3d_meshes_can_be_the_source_and_the_target(tmp_path):
    left, right = _grid(0.0), _grid(10.0)
    vertices = np.vstack([left[0], right[0]]).astype(np.float64)
    faces = np.vstack([left[1], right[1] + len(left[0])]).tolist()
    source = m3.Mesh(vertices, faces, name="split", parts={"left": list(range(32)), "right": list(range(32, 64))})
    source.save(tmp_path / "source.json")
    m3.Mesh(vertices + 1e-4, faces, name="whole").save(tmp_path / "target.json")
    names, labels, target, _ = transfer.transfer_parts(tmp_path / "source.json", tmp_path / "target.json")
    assert names == ["left", "right"] and target.name == "whole" and labels[:32].tolist() == [0] * 32
    m3.Mesh(vertices, faces, units="mm").save(tmp_path / "mm.json")
    with pytest.raises(ToolError, match="単位が違います"):
        transfer.transfer_parts(tmp_path / "source.json", tmp_path / "mm.json")
    m3.Mesh(vertices, faces, parts={"a": [0, 1], "b": [1, 2]}).save(tmp_path / "overlap.json")
    with pytest.raises(ToolError, match="2 つの parts"):
        transfer.transfer_parts(tmp_path / "overlap.json", tmp_path / "target.json")
    m3.Mesh(vertices, faces).save(tmp_path / "plain.json")
    with pytest.raises(ToolError, match="parts"):
        transfer.transfer_parts(tmp_path / "plain.json", tmp_path / "target.json")


def _recipe(tmp_path, parameters, target="mesh.npz", outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "transfer",
                                           "inputs": {"source": {"path": "parts.npz"}, "target": {"path": target}},
                                           "steps": [{"id": "transfer", "operation": "mesh.transfer_parts",
                                                      "inputs": {"source": "$inputs.source", "target": "$inputs.target"},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"labels": "labels.npz", "report": "transfer.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_labels_and_reuses_them(tmp_path):
    from model3d import executor
    expected = _inputs(tmp_path)
    result = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["transfer"]
    with np.load(step["artifacts"]["labels"]) as data:
        assert data["names"].tolist() == ["left", "right"] and np.array_equal(data["labels"], expected)
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-transfer-parts/0.1" and len(report["labels_sha256"]) == 64
    assert step["observations"][0]["kind"] == "transfer_parts"
    again = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert again["steps"]["transfer"]["reused_from"] is not None                # 入力のファイルだけで決まる
    review = executor.run_recipe(_recipe(tmp_path, {"max_distance": 1e-5}), tmp_path / "runs")
    assert review["steps"]["transfer"]["status"] == "needs_review" and "64 面" in review["steps"]["transfer"]["unresolved"][0]


def test_the_operation_writes_parts_into_a_model3d_target(tmp_path):
    from model3d import executor
    _inputs(tmp_path)
    left, right = _grid(0.0), _grid(10.0)
    m3.Mesh(np.vstack([left[0], right[0]]).astype(np.float64), np.vstack([left[1], right[1] + 25]).tolist(),
            name="whole", parts={"keep": [0]}).save(tmp_path / "target.json")
    result = executor.run_recipe(_recipe(tmp_path, {}, "target.json", {"mesh": "target.json"}), tmp_path / "runs")
    out = m3.Mesh.load(result["steps"]["transfer"]["artifacts"]["mesh"])
    assert out.parts["left"] == list(range(32)) and out.parts["right"] == list(range(32, 64)) and out.parts["keep"] == [0]
    refused = executor.run_recipe(_recipe(tmp_path, {}, "mesh.npz", {"mesh": "x.json"}), tmp_path / "runs2")
    assert refused["status"] == "failed" and "npz" in refused["steps"]["transfer"]["error"]


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, {"labels": "l.npz"}, "知らないパラメーター"),
    ({}, {"report": "r.json"}, "labels"),
    ({"max_distance": -1}, {"labels": "l.npz"}, "max_distance"),
    ({"cell": 0}, {"labels": "l.npz"}, "cell"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    _inputs(tmp_path)
    with pytest.raises(ToolError, match=message):
        get("mesh.transfer_parts").function({"source": tmp_path / "parts.npz", "target": tmp_path / "mesh.npz"}, parameters,
                                            {k: tmp_path / v for k, v in outputs.items()}, Context(tmp_path))
