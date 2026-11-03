"""参照画像をテクスチャへ写す処理と正射影の描画。素材はすべてプログラムで作った合成物（実キャラの設定画は使わない）。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import executor, semantic, texture
from model3d.io import ToolError, atomic_json, read_json, sha256
from model3d.mesh import Mesh

FRONT = {"from": "-Y", "center": [0.0, 0.0], "span": 2.0, "size": [64, 64]}
IDENTITY = ([[0.0, 0.0], [64.0, 0.0]], [[0.0, 0.0], [64.0, 0.0]])


def quad(y=0.0, x0=-1.0, x1=1.0, z0=-1.0, z1=1.0, flip=False, uv_box=(0.0, 0.0, 1.0, 1.0)):
    """y の平面の四角形（既定は正面 -Y を向く）。uv は四角形を uv_box（u0, v0, u1, v1）へ写す。"""
    corners = [[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]]
    u0, v0, u1, v1 = uv_box
    uv = [[u0 + (c[0] - x0) / (x1 - x0) * (u1 - u0), v0 + (c[2] - z0) / (z1 - z0) * (v1 - v0)] for c in corners]
    face = [3, 2, 1, 0] if flip else [0, 1, 2, 3]
    return corners, face, uv


def mesh_of(*quads, name="panel"):
    vertices, faces, uv = [], [], []
    for corners, face, coords in quads:
        faces.append([len(vertices) + i for i in face])
        vertices.extend(corners)
        uv.extend(coords)
    return Mesh(vertices, faces, uv, name=name)


def halves(size=64, left=(220, 30, 30), right=(30, 30, 220)):
    image = np.zeros((size, size, 4))
    image[:, : size // 2, :3] = left
    image[:, size // 2:, :3] = right
    image[..., 3] = 255
    return image


def gray(size=64, value=128.0):
    return np.full((size, size, 3), value)


def project(mesh, reference=None, faces=None, mask=None, view=FRONT, **options):
    matrix, offset, _ = texture.alignment(*IDENTITY)
    return texture.project_reference(mesh, gray(), halves() if reference is None else reference, view, matrix, offset,
                                     faces=faces, mask=mask, **{"expand": 0.0, **options})


def test_views_map_axes_like_a_camera_on_that_side():
    points = np.array([[1.0, 0.0, 0.0], [0.0, 0.0, 1.0], [0.0, 1.0, 0.0]])
    front, depth = texture.project_points(points, FRONT)
    assert front[0].tolist() == [64.0, 32.0] and front[1].tolist() == [32.0, 0.0] and depth.tolist() == [0.0, 0.0, 1.0]
    back, _ = texture.project_points(points, dict(FRONT, **{"from": "+Y"}))
    assert back[0][0] == 0.0   # 背面から見ると +X は左
    side, depth = texture.project_points(points, dict(FRONT, **{"from": "-X"}))
    assert side[2][0] == 0.0 and depth[0] == 1.0   # -X から見ると +Y は左、+X は奥
    for bad in ({"from": "top"}, dict(FRONT, span=0), dict(FRONT, size=[64, 0]), dict(FRONT, center=[0.0])):
        with pytest.raises(ToolError):
            texture.check_view(bad)


def test_rasterize_covers_pixel_centres_and_large_triangles():
    index, x, y, bary = texture.rasterize([[[0, 0], [4, 0], [0, 4]]], 8, 8)
    assert len(index) == 10 and np.allclose(bary.sum(axis=1), 1.0) and (bary >= -1e-9).all()   # 中心が対角線以下の画素
    big = [[[0, 0], [200, 0], [0, 200]]]   # 外接箱が 64 画素を超える三角形は 1 つずつ調べる
    index, x, y, bary = texture.rasterize(big, 256, 256)
    assert abs(len(index) - 200 * 200 / 2) < 210
    assert len(texture.rasterize([[[0, 0], [1, 1], [2, 2]]], 8, 8)[0]) == 0   # 退化した三角形は飛ばす


def test_triangles_keep_their_shape_at_u_one_and_in_other_tiles():
    # 頂点ごとに 1 で割った余りを取ると u = 1 の角が 0 へ飛んで潰れる。三角形ごとに同じ 1 枚へずらす
    corners = texture.triangle_texels([[[0, 0], [1, 0], [1, 1]], [[1, 0], [2, 0], [2, 1]], [[-1, 2], [0, 2], [0, 3]]], 64, 64)
    assert np.allclose(corners, [[[0, 64], [64, 64], [64, 0]]] * 3)


def test_stretch_flags_triangles_whose_uv_spans_far_more_than_their_size():
    points = np.array([[[0, 0, 0], [1, 0, 0], [0, 0, 1]]] * 5 + [[[0, 0, 0], [0.01, 0, 0], [0, 0, 0.01]]], dtype=float)
    corners = np.array([[[0, 0], [10, 0], [0, 10]]] * 6, dtype=float)
    ratio = texture.stretch(corners, points)
    assert np.allclose(ratio[:5], 1.0) and ratio[5] == pytest.approx(100.0)
    flat = points.copy()
    flat[0, 1] = flat[0, 0]
    flat[0, 2] = flat[0, 0]
    assert np.isinf(texture.stretch(corners, flat)[0])   # 3D で潰れた三角形は塗らない側へ


def test_zbuffer_keeps_the_nearest_surface():
    mesh = mesh_of(quad(y=0.0), quad(y=-0.5, x0=-1.0, x1=0.0))   # 左半分の前に別の板
    triangles, _ = mesh.triangles()
    points, depth = texture.project_points(mesh.vertices, FRONT)
    buffer, _ = texture.zbuffer(points, depth, triangles, 64, 64)
    assert buffer[32, 10] == -0.5 and buffer[32, 50] == 0.0


def test_projection_paints_visible_front_faces_with_the_reference():
    new, weights, report = project(mesh_of(quad()))
    assert np.allclose(new[20, 10], [220, 30, 30]) and np.allclose(new[20, 50], [30, 30, 220])
    assert weights[20, 10] == pytest.approx(1.0) and report["painted_texels"] > 3000
    back, _, report = project(mesh_of(quad(flip=True)))   # 裏を向いた面は塗らない
    assert report["painted_texels"] == 0 and np.allclose(back, 128)


def test_hidden_and_unselected_faces_keep_their_colour():
    # 奥の板（面 0）は左半分が手前の板（面 1）に隠れる。塗るのは選んだ面 0 だけ
    mesh = mesh_of(quad(), quad(y=-0.5, x0=-1.0, x1=0.0, uv_box=(0.0, 0.0, 0.01, 0.01)))
    new, weights, report = project(mesh, faces=[0])
    assert np.allclose(new[20, 10], 128) and weights[20, 10] == 0    # 隠れた所は元の色
    assert np.allclose(new[20, 50], [30, 30, 220])                   # 見える所は写る
    assert report["candidate_triangles"] == 2


def test_mask_and_reference_alpha_weigh_the_projection():
    mask = np.full((64, 64), 255.0)
    mask[:, 32:] = 0.0
    new, weights, _ = project(mesh_of(quad()), mask=mask)
    assert np.allclose(new[20, 10], [220, 30, 30]) and np.allclose(new[20, 50], 128) and weights[20, 50] == 0
    faint = halves()
    faint[..., 3] = 127.5
    new, weights, _ = project(mesh_of(quad()), reference=faint)
    assert weights[20, 10] == pytest.approx(0.5) and np.allclose(new[20, 10], [174, 79, 79])


def test_expand_paints_one_more_ring_around_the_uv_island():
    island = mesh_of(quad(x0=-0.5, x1=0.5, uv_box=(0.25, 0.25, 0.75, 0.75)))   # テクスチャの x・y 16〜48 の画素
    tight, _, small = project(island)
    wide, _, large = project(island, expand=1.0)
    assert small["painted_texels"] == 32 * 32 and large["painted_texels"] > small["painted_texels"]
    assert np.allclose(tight[30, 15], 128) and np.allclose(wide[30, 15], [220, 30, 30])   # 島のすぐ外も写る


def test_stretched_uv_triangles_are_skipped():
    # 手前の小さな板の uv がテクスチャ全体に広がっている（継ぎ目の頂点を結合したときのような三角形）
    smeared = quad(y=-0.2, x0=0.4, x1=0.45, z0=0.4, z1=0.45)
    mesh = mesh_of(quad(), quad(y=0.3, uv_box=(0.0, 0.0, 0.01, 0.01)), smeared)
    new, _, report = project(mesh)
    assert report["skipped_stretched_triangles"] == 2 and np.allclose(new[20, 10], [220, 30, 30])
    blurred, _, report = project(mesh, max_stretch=1000.0)
    assert report["skipped_stretched_triangles"] == 0 and not np.allclose(blurred[20, 10], [220, 30, 30])


def test_views_refuse_meshes_with_other_axes():
    corners, face, uv = quad()
    turned = Mesh(corners, [face], uv, name="panel", axes={"up": "+Y", "forward": "+Z"})
    with pytest.raises(ToolError, match="to_axes"):
        project(turned)
    with pytest.raises(ToolError, match="to_axes"):
        texture.render_view(turned, gray(), FRONT)


def test_alignment_fits_similarity_and_affine_and_refuses_bad_marks():
    matrix, offset, report = texture.alignment([[5, 5], [5, 25]], [[0, 0], [10, 0]])
    assert np.allclose(matrix, [[0, -2], [2, 0]]) and np.allclose(offset, [5, 5]) and report["max_residual_px"] < 1e-9
    matrix, offset, report = texture.alignment([[1, 2], [3, 2], [1, 5]], [[0, 0], [1, 0], [0, 1]], "affine")
    assert np.allclose(matrix, [[2, 0], [0, 3]]) and np.allclose(offset, [1, 2])
    for model, reference, kind in [([[0, 0]], [[0, 0]], "similarity"), ([[0, 0], [1, 1]], [[0, 0]], "similarity"),
                                   ([[0, 0], [1, 1]], [[2, 2], [2, 2]], "similarity"), ([[0, 0], [1, 1]], [[0, 0], [1, 1]], "affine"),
                                   ([[0, 0], [1, 1], [2, 2]], [[0, 0], [1, 1], [2, 2]], "affine"), ([[0, 0], [1, 1]], [[0, 0], [1, 1]], "warp")]:
        with pytest.raises(ToolError):
            texture.alignment(model, reference, kind)


def test_draw_mask_fills_shapes_at_pixel_centres_cuts_and_feathers():
    square = texture.draw_mask([8, 8], [{"polygon": [[2, 2], [6, 2], [6, 6], [2, 6]]}])
    assert (square == 255).sum() == 16 and square[2:6, 2:6].min() == 255
    ellipse = texture.draw_mask([8, 8], [{"ellipse": [4, 4, 2, 1]}])
    assert (ellipse > 0).sum() == 8   # 中心が (x-4)²/4 + (y-4)² ≤ 1 の画素: 2 行 × 4
    turned = texture.draw_mask([8, 8], [{"ellipse": [4, 4, 1, 2], "angle": 90}])
    assert np.array_equal(turned, ellipse)   # 半径 (1, 2) を 90° 回すと (2, 1) と同じ
    cut = texture.draw_mask([8, 8], [{"polygon": [[2, 2], [6, 2], [6, 6], [2, 6]]}, {"ellipse": [4, 4, 2, 1], "value": 0}])
    assert (cut == 255).sum() == 8
    soft = texture.draw_mask([32, 32], [{"polygon": [[8, 8], [24, 8], [24, 24], [8, 24]]}], feather=2.0)
    assert 0 < soft[16, 7] < 255 and soft[16, 16] == 255 and soft[0, 0] == 0
    for size, shapes in [([8, 0], [{"ellipse": [4, 4, 1, 1]}]), ([8, 8], []), ([8, 8], [{"polygon": [[0, 0], [1, 1]]}]),
                         ([8, 8], [{"circle": [4, 4, 1]}]), ([8, 8], [{"ellipse": [4, 4, 0, 1]}]),
                         ([8, 8], [{"ellipse": [4, 4, 1, 1], "value": 300}]), ([8, 8], [[4, 4, 1, 1]])]:
        with pytest.raises(ToolError):
            texture.draw_mask(size, shapes)


def test_render_view_samples_the_texture_through_the_uv():
    image, report = texture.render_view(mesh_of(quad(x0=-0.5, x1=0.5)), halves()[..., :3], FRONT)
    assert np.allclose(image[20, 20], [220, 30, 30]) and np.allclose(image[20, 44], [30, 30, 220])
    assert np.allclose(image[20, 2], 245) and report["covered_pixels"] == 32 * 64
    with pytest.raises(ToolError, match="uv"):
        texture.render_view(Mesh([[0, 0, 0], [1, 0, 0], [0, 0, 1]], [[0, 1, 2]]), gray(), FRONT)


@pytest.fixture
def workspace(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    mesh_of(quad(), quad(y=-0.5, x0=-1.0, x1=0.0, uv_box=(0.0, 0.0, 0.01, 0.01))).save(inputs / "panel.json")
    Image.fromarray(gray().astype(np.uint8)).save(inputs / "base.png")
    Image.fromarray(halves().astype(np.uint8)).save(inputs / "sheet.png")
    mapping = semantic.build("panel", {"schema_version": "model3d-preset/0.1", "name": "t", "parts": {}, "bones": {},
                                       "parameters": {}, "required_roles": {"parts": [], "bones": []}})
    semantic.add_region(mapping, "back", "body", Mesh.load(inputs / "panel.json"), [0], "tester")
    semantic.confirm(mapping, "regions", "back", "confirmed", "tester")
    atomic_json(tmp_path / "semantic_map.json", mapping)
    steps = [
        {"id": "look", "operation": "texture.render_view", "inputs": {"mesh": "$inputs.panel", "texture": "$inputs.base"},
         "parameters": {"view": FRONT}, "outputs": {"image": "look.png", "report": "look.json"}},
        {"id": "mask", "operation": "texture.draw_mask", "inputs": {"like": "$inputs.sheet"},   # 参照画像の右半分だけ
         "parameters": {"shapes": [{"polygon": [[32, 0], [64, 0], [64, 64], [32, 64]]}]}, "outputs": {"mask": "mask.png"}},
        {"id": "paint", "operation": "texture.project_reference",
         "inputs": {"mesh": "$inputs.panel", "texture": "$inputs.base", "reference": "$inputs.sheet", "mask": "$steps.mask.mask"},
         "select": {"mesh": "body"}, "parameters": {"view": FRONT, "alignment": {"model": IDENTITY[0], "reference": IDENTITY[1]},
                                                     "expand": 0.0},
         "outputs": {"texture": "painted.png", "weights": "weights.png", "report": "paint.json", "overlay": "overlay.png"}},
    ]
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "paint", "semantic_map": "semantic_map.json",
              "inputs": {"panel": {"path": "inputs/panel.json"}, "base": {"path": "inputs/base.png"},
                         "sheet": {"path": "inputs/sheet.png"}}, "steps": steps}
    atomic_json(tmp_path / "recipe.json", recipe)
    return tmp_path


def test_recipe_paints_only_the_selected_region_and_reuses_identical_runs(workspace):
    first = executor.run_recipe(workspace / "recipe.json", workspace / "runs")
    assert first["status"] == "succeeded", first
    paint = first["steps"]["paint"]
    assert paint["observations"][0]["kind"] == "selection" and paint["observations"][1]["painted_texels"] > 0
    painted = np.asarray(Image.open(paint["artifacts"]["texture"]).convert("RGB"), dtype=float)
    assert np.allclose(painted[20, 10], 128) and np.allclose(painted[20, 50], [30, 30, 220])
    overlay = np.asarray(Image.open(paint["artifacts"]["overlay"]).convert("RGB"), dtype=float)
    assert np.allclose(overlay[20, 50], [79, 79, 174]) and np.allclose(overlay[20, 10], 128)   # マスクの外は描画のまま
    assert first["steps"]["mask"]["observations"][0]["full_pixels"] == 32 * 64
    second = executor.run_recipe(workspace / "recipe.json", workspace / "runs")
    assert second["steps"]["paint"]["reused_from"] == first["run_id"]
    forced = executor.run_recipe(workspace / "recipe.json", workspace / "runs", force=True)
    assert sha256(forced["steps"]["paint"]["artifacts"]["texture"]) == sha256(paint["artifacts"]["texture"])
    report = read_json(paint["artifacts"]["report"])
    assert report["alignment"]["max_residual_px"] < 1e-9 and json.dumps(report)


def test_recipe_marks_an_empty_projection_for_review(workspace):
    data = read_json(workspace / "recipe.json")
    data["steps"][2]["parameters"]["view"] = dict(FRONT, **{"from": "+Y"})   # 背面から見ると板は裏向き
    atomic_json(workspace / "recipe.json", data)
    result = executor.run_recipe(workspace / "recipe.json", workspace / "runs")
    assert result["status"] == "needs_review" and "1 画素も" in result["steps"]["paint"]["unresolved"][0]
