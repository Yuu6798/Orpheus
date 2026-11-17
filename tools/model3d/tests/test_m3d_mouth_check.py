"""頭の候補の口の作りを調べる（check.mouth）: 合成の唇（上下が別の面の唇と、真ん中だけ合わせ目がくっついた唇）で、くっついている所・辺の数・
口の中・色の差・絵（Blender なし）と、工程の受け渡しと悪い設定。3 作目の VRM のダンスの頭での数は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import mouth
from model3d.io import ToolError, atomic_json

SEAM = 1.45
# 唇の断面（y, z）の並び: 上唇の外の面（上から合わせ目まで）→ 合わせ目の裏へ折れる面（浅い・深い）→ 口の奥の壁 → 下の折れる面 → 下唇の外の面
PROFILE = ([(-0.06, SEAM + d) for d in (0.010, 0.008, 0.006, 0.004, 0.002, 0.0)]
           + [(-0.0565, SEAM + 0.0002), (-0.053, SEAM + 0.0002), (-0.04, SEAM), (-0.053, SEAM - 0.0002), (-0.0565, SEAM - 0.0002)]
           + [(-0.06, SEAM - d) for d in (0.0, 0.002, 0.004, 0.006, 0.008, 0.010)])
U1, L1 = 6, 10                                                         # 浅い所の、上と下の折れる面の頂点（並びの番号）
INSIDE_ROWS = range(7, 10)                                             # 深い所（奥の壁のまわり）
XS = np.round(np.arange(-0.024, 0.0241, 0.002), 6)


def synthetic_lips(glued_within=None, extra_triangle=False):
    """1 枚のつながった面の唇。上下の外の面は合わせ目で同じ位置に来るが、別の頂点（上下が別の面）。glued_within（|x| の上限）の列だけ、浅い所の
    上下の折れる面を辺でつなぐ（合わせ目がくっついている）。uv は、列が u、断面の並びが v。"""
    rows = len(PROFILE)
    co = np.array([[x, y, z] for x in XS for y, z in PROFILE])
    index = lambda c, r: c * rows + r
    tris, edges = [], set()
    for c in range(len(XS)):
        for r in range(rows - 1):
            edges.add((index(c, r), index(c, r + 1)))
        if c + 1 < len(XS):
            for r in range(rows):
                edges.add((index(c, r), index(c + 1, r)))
            for r in range(rows - 1):
                a, b, d, e = index(c, r), index(c + 1, r), index(c + 1, r + 1), index(c, r + 1)
                tris += [(a, b, d), (a, d, e)]
                edges.add((a, d))
        if glued_within is not None and abs(XS[c]) <= glued_within:
            edges.add((index(c, U1), index(c, L1)))
    if extra_triangle:                                                   # 外の面の辺に、もう 1 枚の面（3 枚の面が集まる辺）
        a, b = index(5, 2), index(6, 2)
        co = np.vstack([co, [[XS[5], -0.07, SEAM + 0.006]]])
        tris.append((a, b, len(co) - 1))
        edges |= {(a, len(co) - 1), (b, len(co) - 1)}
    tris = np.array(tris, dtype=np.int64)
    uv_points = np.array([[c / (len(XS) - 1), 1 - r / (rows - 1)] for c in range(len(XS)) for r in range(rows)] + ([[0.5, 0.5]] if extra_triangle else []))
    tri_loops = np.arange(tris.size, dtype=np.int64).reshape(-1, 3)
    uv = uv_points[tris.reshape(-1)]
    return co, np.array(sorted(tuple(sorted(e)) for e in edges), dtype=np.int64), tris, tri_loops, uv


SETTINGS = {"front_y": -0.02, "resolution": 0.0002, "window": {"half_width": 0.03, "z": [1.43, 1.47]}, "zone": {"half_width": 0.026},
            "seam": {"depth": 0.006, "centre": 0.015, "band": 0.003}, "min_points": 2}


def test_separate_lips_do_not_meet_near_the_front():
    co, edges, tris, _, _ = synthetic_lips()
    report, front, section = mouth.check_mouth(co, edges, tris, SETTINGS)
    lips = report["lips"]
    assert report["seam"]["seam_z_centre"] == pytest.approx(SEAM) and report["seam"]["corner_x"] == pytest.approx(0.023, abs=0.0015)   # 端の列は見える
    assert lips["bins_measured"] >= 10 and lips["glued_bins"] == 0 and lips["separate_bins"] == lips["bins_measured"]
    assert lips["glued_vertices"] == 0
    assert lips["deep_path_mm"] > 40                                       # 口の奥を回る道だけ（約 46 mm）
    assert report["inside"]["pocket_triangles"] > 0 and report["inside"]["depth_mm"] == pytest.approx(20.0, abs=0.2)
    assert report["edges"]["three_or_more_faces"] == 0 and report["edges"]["open_edges"] > 0
    assert front.shape == (200, 300, 3) and section.ndim == 3


def test_glued_lips_are_found_where_they_touch():
    co, edges, tris, _, _ = synthetic_lips(glued_within=0.006)
    lips = mouth.check_mouth(co, edges, tris, SETTINGS)[0]["lips"]
    assert lips["glued_bins"] >= 2 and lips["separate_bins"] >= 6
    glued_x = [b["x"] for b in lips["bins"] if b["connected"]]
    assert all(abs(x) < 0.010 for x in glued_x)                            # くっついているのは真ん中の区間だけ
    assert all(b["path_mm"] < 20 for b in lips["bins"] if b["connected"])
    assert lips["glued_vertices"] > 0 and lips["deep_path_mm"] < 20         # 合わせ目をまたぐ短い道がある


def test_three_faces_on_one_edge_are_counted():
    co, edges, tris, _, _ = synthetic_lips(extra_triangle=True)
    assert mouth.check_mouth(co, edges, tris, SETTINGS)[0]["edges"]["three_or_more_faces"] == 1


def test_mouth_colour_is_compared_with_the_skin():
    co, edges, tris, tri_loops, uv = synthetic_lips()
    height = 160
    image = np.full((height, 160, 3), (240, 200, 190), dtype=np.uint8)
    rows = len(PROFILE)
    top, bottom = int((min(INSIDE_ROWS) - 0.5) / (rows - 1) * height), int((max(INSIDE_ROWS) + 0.5) / (rows - 1) * height)
    image[top:bottom] = (90, 30, 35)                                        # 口の奥の面が使う所だけ暗い赤
    color = mouth.check_mouth(co, edges, tris, SETTINGS, uv, tri_loops, image)[0]["color"]
    assert color["skin_median"] == [240.0, 200.0, 190.0] and color["distance"] > 150
    same = mouth.check_mouth(co, edges, tris, SETTINGS, uv, tri_loops, np.full((height, 160, 3), 200, dtype=np.uint8))[0]["color"]
    assert same["distance"] == 0.0                                          # 口の中も肌の色（口を開けると肌色に見える）
    assert color["uv_outside_triangles"] == 0
    shifted = uv.copy()
    shifted[:, 0] += 1.0                                                    # uv を絵の外へ
    outside = mouth.check_mouth(co, edges, tris, SETTINGS, shifted, tri_loops, image)[0]["color"]
    assert outside["uv_outside_triangles"] > 0 and outside["inside_pixels"] < color["inside_pixels"]   # 絵の外は数えず、数を出す


def test_shortest_paths_and_face_counts():
    distance = mouth.shortest_paths(4, np.array([[0, 1], [1, 2]]), np.array([1.0, 2.5]), [0])
    assert distance.tolist() == [0.0, 1.0, 3.5, float("inf")]
    counts = mouth.edge_face_counts(np.array([[0, 1], [1, 2], [0, 2], [2, 3]]), np.array([[0, 1, 2], [1, 0, 3]]), 4)
    assert counts.tolist() == [2, 1, 1, 0]                                 # 面の中の対角線（1-3）はメッシュの辺に無いので数えない


# ---------- 工程 ----------

def _recipe(tmp_path, parameters, outputs=None, texture=False):
    co, edges, tris, tri_loops, uv = synthetic_lips(glued_within=0.006)
    np.savez_compressed(tmp_path / "head_mesh.npz", co=co, edges=edges, tris=tris, tri_loops=tri_loops, uv=uv)
    inputs = {"mesh": {"path": "head_mesh.npz"}}
    step_inputs = {"mesh": "$inputs.mesh"}
    if texture:
        Image.new("RGB", (32, 32), (230, 190, 180)).save(tmp_path / "head.png")
        inputs["texture"], step_inputs["texture"] = {"path": "head.png"}, "$inputs.texture"
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "mouth-check", "inputs": inputs,
        "steps": [{"id": "check", "operation": "check.mouth", "inputs": step_inputs, "parameters": parameters,
                   "outputs": outputs or {"report": "mouth.json", "front": "front.png", "section": "section.png"}}]})
    return tmp_path / "recipe.json"


def test_check_mouth_operation_reports_numbers_and_pictures(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_recipe(tmp_path, SETTINGS, texture=True), tmp_path / "runs")
    assert result["status"] == "succeeded", result                         # 合否は決めない（くっついていても要確認にしない）
    step = result["steps"]["check"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-mouth-check/0.1" and report["lips"]["glued_bins"] >= 2 and report["color"]["distance"] == 0.0
    assert Image.open(step["artifacts"]["front"]).size == (300, 200) and Image.open(step["artifacts"]["section"]).mode == "RGB"
    observed = step["observations"][0]
    assert observed["kind"] == "mouth_check" and observed["glued_bins"] == report["lips"]["glued_bins"]
    assert not step["unresolved"]


def _with(**changes):
    return {**SETTINGS, **changes}


@pytest.mark.parametrize("parameters, message", [
    ({k: v for k, v in SETTINGS.items() if k != "seam"}, "seam"),
    (_with(seeds=[0.008, 0.003]), "seeds"),
    (_with(bins=1), "bins"),
    (_with(parts={"half_width": 0.02, "z": [1.44, 1.46], "front_y": -0.02, "max_size": 0.04, "front_limit": -0.07}), "parts と tongue"),
    (_with(window={"half_width": 0.03, "z": [1.47, 1.43]}), "window.z"),
    (_with(colour=1), "知らない設定"),
])
def test_check_mouth_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import face_ops
    with pytest.raises(ToolError, match=message):
        face_ops.mouth_check_settings(parameters)


def test_check_mouth_refuses_16_bit_textures(tmp_path):
    from model3d import executor
    recipe = _recipe(tmp_path, SETTINGS, texture=True)
    Image.fromarray(np.full((32, 32), 50000, dtype=np.uint16)).save(tmp_path / "head.png")   # 色に変えると 255 で切られて白くなる
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "failed" and "8 ビット" in result["steps"]["check"]["error"]


def test_check_mouth_stops_when_there_is_no_seam(tmp_path):
    from model3d import executor
    flat = _with(seam={"depth": 0.001, "centre": 0.015, "band": 0.003})        # 合わせ目（3.5 mm 裏）が帯の外
    result = executor.run_recipe(_recipe(tmp_path, flat), tmp_path / "runs")
    assert result["status"] == "failed" and "合わせ目" in result["steps"]["check"]["error"]
