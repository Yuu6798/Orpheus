"""法線のならし（mesh.smooth_normals / mesh.smooth_part_normals）: 格子でのまとめ、継ぎ目の重複点、面積の重み、隣の法線でのならし、
試作の計算（scipy の疎行列）との一致、0 の法線の数え方、工程の入出力と悪い設定。"""
import json
from pathlib import Path

import numpy as np
import pytest

from model3d import mesh as m3
from model3d.io import ToolError, atomic_json


def test_grid_weld_merges_points_in_the_same_cell_and_numbers_them_in_sorted_order():
    points = [[0.2, 0, 0], [0, 0, 0], [0.2 + 4e-6, 0, 0], [0, 0, 1e-4]]
    assert m3.grid_weld(points, 1e-5).tolist() == [2, 0, 2, 1]        # 丸めた座標の辞書順: (0,0,0) (0,0,10) (20000,0,0)
    assert m3.grid_weld(np.zeros((0, 3)), 1e-5).tolist() == []


def test_face_edges_follow_each_face_boundary():
    assert m3.face_edges(np.array([[0, 1, 2]])).tolist() == [[0, 1], [1, 2], [2, 0]]
    assert m3.face_edges([[0, 1, 2, 3]]).tolist() == [[0, 1], [1, 2], [2, 3], [3, 0]]
    assert m3.face_edges(np.zeros((0, 3), dtype=np.int64)).shape == (0, 2)


CUBE_CORNERS = np.array([[x, y, z] for x in (0, 1) for y in (0, 1) for z in (0, 1)], dtype=np.float64)
CUBE_QUADS = [[0, 1, 3, 2], [4, 6, 7, 5], [0, 4, 5, 1], [2, 3, 7, 6], [0, 2, 6, 4], [1, 5, 7, 3]]   # 外向き


def _cube(seams=False):
    """立方体の三角形。seams なら面ごとに頂点を分ける（UV の継ぎ目のように、同じ位置の頂点が 3 つずつ）。"""
    if not seams:
        return CUBE_CORNERS.copy(), np.array([t for q in CUBE_QUADS for t in ((q[0], q[1], q[2]), (q[0], q[2], q[3]))])
    vertices, triangles = [], []
    for q in CUBE_QUADS:
        base = len(vertices)
        vertices.extend(CUBE_CORNERS[q])
        triangles.extend([(base, base + 1, base + 2), (base, base + 2, base + 3)])
    return np.array(vertices), np.array(triangles)


def test_vertices_split_at_seams_get_the_normals_of_the_welded_mesh():
    welded_v, welded_t = _cube()
    split_v, split_t = _cube(seams=True)
    for iterations in (0, 3):
        whole, _ = m3.smooth_normals(welded_v, welded_t, m3.face_edges(welded_t), iterations)
        split, report = m3.smooth_normals(split_v, split_t, m3.face_edges(split_t), iterations)
        assert report["vertices"] == 24 and report["welded"] == 8
        corner = {tuple(p): k for k, p in enumerate(CUBE_CORNERS)}
        for k, p in enumerate(split_v):                              # 同じ位置の 3 つの頂点は、まとめたメッシュの角と同じ法線
            assert np.allclose(split[k], whole[corner[tuple(p)]], atol=1e-12)
        outward = np.einsum("ij,ij->i", whole, CUBE_CORNERS - 0.5)
        assert (outward > 0).all() and np.allclose(np.linalg.norm(whole, axis=1), 1.0)


def _bumpy_grid(size=12, seed=1):
    rng = np.random.default_rng(seed)
    xs, ys = np.meshgrid(np.arange(size, dtype=np.float64), np.arange(size, dtype=np.float64))
    vertices = np.c_[xs.ravel(), ys.ravel(), 0.3 * rng.standard_normal(size * size)]
    triangles = []
    for r in range(size - 1):
        for c in range(size - 1):
            a, b, d, e = r * size + c, r * size + c + 1, (r + 1) * size + c, (r + 1) * size + c + 1
            triangles += [(a, b, e), (a, e, d)]
    return vertices, np.array(triangles)


def test_smoothing_with_the_neighbours_flattens_bumps():
    vertices, triangles = _bumpy_grid()
    angles = []
    for iterations in (0, 2, 10):
        normals, report = m3.smooth_normals(vertices, triangles, m3.face_edges(triangles), iterations)
        angles.append(float(np.degrees(np.arccos(np.clip(normals[:, 2], -1, 1))).mean()))
        assert (report["mean_turn_deg"] == 0.0) == (iterations == 0) and report["zero_normals"] == 0
    assert angles[0] > angles[1] > angles[2]                          # 上（+z）からのずれが回数とともに減る


def _reference(vertices, triangles, iterations, step=1e-5):
    """リポジトリ外の試作と同じ書き方（scipy の疎行列の積）。"""
    sparse = pytest.importorskip("scipy.sparse")
    c = np.asarray(vertices, dtype=np.float64)
    _, inv = np.unique(np.round(c / step).astype(np.int64), axis=0, return_inverse=True)
    inv = inv.ravel()
    n = int(inv.max()) + 1
    g = inv[triangles]
    wp = np.zeros((n, 3))
    wp[inv] = c
    fn = np.cross(wp[g[:, 1]] - wp[g[:, 0]], wp[g[:, 2]] - wp[g[:, 0]])
    vn = np.zeros((n, 3))
    for k in range(3):
        np.add.at(vn, g[:, k], fn)
    vn /= np.maximum(np.linalg.norm(vn, axis=1, keepdims=True), 1e-12)
    e = np.r_[g[:, [0, 1]], g[:, [1, 2]], g[:, [2, 0]]]
    A = sparse.coo_matrix((np.ones(len(e)), (e[:, 0], e[:, 1])), shape=(n, n)).tocsr()
    A = ((A + A.T) > 0).astype(np.float64).tocsr()
    v = vn.copy()
    for _ in range(iterations):
        v = A @ v + v
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    return v[inv]


def _sphere(rings=9, segments=14, seed=4):
    """経線の継ぎ目と極で頂点が重なる球（でこぼこを付ける）に、つぶれた三角形を 1 つ足したもの。"""
    rng = np.random.default_rng(seed)
    radius = 1.0 + 0.05 * rng.standard_normal((rings + 1, segments))
    vertices = []
    for i in range(rings + 1):
        theta = np.pi * i / rings
        for j in range(segments + 1):                               # j = segments は j = 0 と同じ位置（継ぎ目）
            phi = 2 * np.pi * j / segments
            r = radius[i, j % segments] if 0 < i < rings else 1.0
            vertices.append([r * np.sin(theta) * np.cos(phi), r * np.sin(theta) * np.sin(phi), r * np.cos(theta)])
    vertices = np.array(vertices)
    vertices[np.abs(vertices) < 1e-12] = 0.0
    triangles = []
    for i in range(rings):
        for j in range(segments):
            a, b = i * (segments + 1) + j, i * (segments + 1) + j + 1
            c, d = a + segments + 1, b + segments + 1
            triangles += [(a, c, b), (b, c, d)]
    triangles.append((5, 5, 40))                                     # つぶれた三角形（自分への辺ができる）
    return vertices, np.array(triangles)


@pytest.mark.parametrize("iterations", [0, 3, 17])
def test_the_result_equals_the_prototype_sparse_matrix_formulation_bit_for_bit(iterations):
    vertices, triangles = _sphere()
    mine, report = m3.smooth_normals(vertices, triangles, m3.face_edges(triangles), iterations)
    assert report["welded"] < len(vertices)                          # 継ぎ目と極がまとまった
    assert np.array_equal(mine, _reference(vertices, triangles, iterations))


def test_opposite_coincident_faces_and_loose_vertices_keep_a_zero_normal_and_are_counted():
    vertices = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5]], dtype=np.float64)
    triangles = np.array([[0, 1, 2], [0, 2, 1]])                     # 向かい合う 2 枚が重なる
    normals, report = m3.smooth_normals(vertices, triangles, m3.face_edges(triangles), 4)
    assert np.allclose(normals, 0) and report["zero_normals"] == 4 and report["isolated"] == 1
    empty, report = m3.smooth_normals(np.zeros((0, 3)), np.zeros((0, 3), dtype=np.int64), np.zeros((0, 2), dtype=np.int64), 2)
    assert empty.shape == (0, 3) and report["welded"] == 0
    with pytest.raises(ToolError, match="iterations"):
        m3.smooth_normals(vertices, triangles, m3.face_edges(triangles), -1)
    with pytest.raises(ToolError, match="weld"):
        m3.smooth_normals(vertices, triangles, m3.face_edges(triangles), 1, step=0)


def _recipe(tmp_path, operation, inputs, step_inputs, parameters, outputs):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "normals", "inputs": inputs,
                                           "steps": [{"id": "normals", "operation": operation, "inputs": step_inputs,
                                                      "parameters": parameters, "outputs": outputs}]})
    return tmp_path / "recipe.json"


def test_the_mesh_operation_writes_x_normals_and_keeps_the_mesh(tmp_path):
    from model3d import executor
    vertices, triangles = _cube(seams=True)
    source = m3.Mesh(vertices * 1000, triangles.tolist(), name="box", units="mm", parts={"all": list(range(12))})
    source.save(tmp_path / "box.json")
    result = executor.run_recipe(_recipe(tmp_path, "mesh.smooth_normals", {"box": {"path": "box.json"}}, {"mesh": "$inputs.box"},
                                         {"iterations": 2}, {"mesh": "box.json", "report": "normals.json"}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["normals"]
    out = m3.Mesh.load(step["artifacts"]["mesh"])
    assert np.array_equal(out.vertices, source.vertices) and out.faces == source.faces and out.parts == source.parts
    normals = np.array(out.extra["x_normals"])
    assert normals.shape == (24, 3) and np.allclose(np.linalg.norm(normals, axis=1), 1.0)
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["units"] == "mm" and report["parts"][0]["welded"] == 8
    assert report["parts"][0]["weld"] == pytest.approx(0.01)       # 既定の 1e-5 m を mm に換算
    assert step["observations"][0]["kind"] == "smooth_normals"


def _parts_npz(path):
    vertices, triangles = _cube(seams=True)
    quads = np.array(CUBE_QUADS)
    np.savez(path, names=np.array(["box", "quads"]), co0=vertices.astype(np.float32), f0=triangles.astype(np.int32),
             co1=CUBE_CORNERS.astype(np.float32), f1=quads.astype(np.int32))
    return path


def test_the_parts_operation_writes_one_normal_array_per_part_that_import_parts_reads(tmp_path):
    from model3d import executor
    _parts_npz(tmp_path / "parts.npz")
    result = executor.run_recipe(_recipe(tmp_path, "mesh.smooth_part_normals", {"parts": {"path": "parts.npz"}},
                                         {"parts": "$inputs.parts"}, {"iterations": {"box": 3, "quads": 0}},
                                         {"normals": "parts_n.npz", "report": "normals.json"}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["normals"]
    with np.load(step["artifacts"]["normals"]) as data:
        assert sorted(data.files) == ["n0", "n1"] and data["n0"].dtype == np.float32 and data["n0"].shape == (24, 3)
        expected, _ = m3.smooth_normals(*_cube(seams=True), m3.face_edges(_cube(seams=True)[1]), 3)
        assert np.array_equal(data["n0"], expected.astype(np.float32))
        assert data["n1"].shape == (8, 3) and np.allclose(np.linalg.norm(data["n1"], axis=1), 1.0, atol=1e-6)   # 四角形の部品
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert [row["iterations"] for row in report["parts"]] == [3, 0] and report["parts"][1]["name"] == "quads"
    box = m3.read_parts(tmp_path / "parts.npz", "box", step["artifacts"]["normals"])   # mesh.import_parts の入力 normals になる
    assert np.allclose(box.extra["x_normals"], expected, atol=1e-6)


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


@pytest.mark.parametrize("parameters, message", [
    ({}, "iterations"),
    ({"iterations": -1}, "0 以上の整数"),
    ({"iterations": 1.5}, "0 以上の整数"),
    ({"iterations": True}, "0 以上の整数"),
    ({"iterations": {"box": 1}}, "書いていない部品"),
    ({"iterations": {"box": 1, "quads": 1, "hat": 1}}, "npz に無い部品"),
    ({"iterations": {"box": 1, "quads": -2}}, "iterations.quads"),
    ({"iterations": 1, "weld": 0}, "weld"),
    ({"iterations": 1, "wobble": 1}, "知らないパラメーター"),
])
def test_the_parts_operation_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    _parts_npz(tmp_path / "parts.npz")
    with pytest.raises(ToolError, match=message):
        get("mesh.smooth_part_normals").function({"parts": tmp_path / "parts.npz"}, parameters,
                                                 {"normals": tmp_path / "n.npz"}, Context(tmp_path))


def test_broken_parts_are_refused_with_the_part_named(tmp_path):
    np.savez(tmp_path / "bad.npz", names=np.array(["a"]), co0=np.zeros((3, 3), np.float32), f0=np.array([[0, 1, 3]]))
    with pytest.raises(ToolError, match="無い頂点"):
        m3.read_all_parts(tmp_path / "bad.npz")
    np.savez(tmp_path / "nan.npz", names=np.array(["a"]), co0=np.full((3, 3), np.nan, np.float32), f0=np.array([[0, 1, 2]]))
    with pytest.raises(ToolError, match="非有限"):
        m3.read_all_parts(tmp_path / "nan.npz")
    np.savez(tmp_path / "short.npz", names=np.array(["a", "b"]), co0=np.zeros((3, 3)), f0=np.array([[0, 1, 2]]))
    with pytest.raises(ToolError, match="co1"):
        m3.read_all_parts(tmp_path / "short.npz")
