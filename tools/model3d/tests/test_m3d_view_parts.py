"""手前の部品の地図（texture.view_parts）と、顔の投影の遮り（texture.project_reference の occlusion）。素材はプログラムで作った板。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import texture
from model3d.io import ToolError, atomic_json

FRONT = {"from": "-Y", "center": [0.0, 0.0], "span": 2.0, "size": [64, 64]}
IDENTITY = ([[0.0, 0.0], [64.0, 0.0]], [[0.0, 0.0], [64.0, 0.0]])


def _plane(y, x0=-1.0, x1=1.0, z0=-1.0, z1=1.0):
    """y の平面の四角形を 2 つの三角形にした頂点と面（正面 -Y を向く）。"""
    vertices = np.array([[x0, y, z0], [x1, y, z0], [x1, y, z1], [x0, y, z1]], dtype=np.float64)
    return vertices, np.array([[0, 1, 2], [0, 2, 3]])


def test_each_pixel_takes_the_nearest_part():
    back, front = _plane(0.0), _plane(-0.5, x0=-1.0, x1=0.0)                 # 左半分の前に別の板
    owners, depth, report = texture.view_parts([("face", *back), ("hair", *front)], FRONT)
    assert owners[32, 10] == 1 and owners[32, 50] == 0 and depth[32, 10] == -0.5 and depth[32, 50] == 0.0
    assert [r["front_pixels"] for r in report["parts"]] == [32 * 64, 32 * 64] and report["covered_pixels"] == 64 * 64
    far = texture.view_parts([("far", *_plane(0.0, x0=5.0, x1=6.0))], FRONT)
    assert (far[0] == -1).all() and np.isinf(far[1]).all() and far[2]["parts"][0]["triangles_in_view"] == 0


def test_triangles_that_span_the_window_are_drawn_even_with_every_corner_outside():
    big = np.array([[-50.0, 0.0, -50.0], [50.0, 0.0, -50.0], [0.0, 0.0, 50.0]])   # 角は全部窓の外、窓はまたぐ
    owners, _, report = texture.view_parts([("sheet", big, np.array([[0, 1, 2]]))], FRONT)
    assert report["parts"][0]["triangles_in_view"] == 1 and (owners >= 0).all()


def test_the_preview_colours_each_part_and_repeats_the_palette():
    owners = np.array([[-1, 0], [10, 11]], dtype=np.int32)
    image = texture.parts_preview(owners, background=245)
    assert image[0, 0].tolist() == [245, 245, 245] and image[0, 1].tolist() == texture.PART_PALETTE[0].tolist()
    assert image[1, 1].tolist() == texture.PART_PALETTE[0].tolist() and image[1, 0].tolist() == texture.PART_PALETTE[10].tolist()


def _parts_npz(path):
    (back, f0), (front, f1) = _plane(0.0), _plane(-0.5, x0=-1.0, x1=0.0)
    np.savez(path, names=np.array(["face", "hair"]), co0=back.astype(np.float32), f0=f0.astype(np.int32),
             co1=front.astype(np.float32), f1=f1.astype(np.int32))
    return path


def _recipe(tmp_path, parameters, outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "view", "inputs": {"parts": {"path": "parts.npz"}},
                                           "steps": [{"id": "view", "operation": "texture.view_parts", "inputs": {"parts": "$inputs.parts"},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"occlusion": "occlusion.npz", "preview": "parts.png",
                                                                             "report": "view.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_the_map_the_preview_and_the_counts(tmp_path):
    from model3d import executor
    _parts_npz(tmp_path / "parts.npz")
    result = executor.run_recipe(_recipe(tmp_path, {"view": FRONT}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["view"]
    with np.load(step["artifacts"]["occlusion"], allow_pickle=False) as data:
        assert data["names"].tolist() == ["face", "hair"] and data["owner"].shape == (64, 64) and data["owner"][32, 10] == 1
        assert json.loads(str(data["view"])) == FRONT and data["depth"][32, 50] == 0.0
    with Image.open(step["artifacts"]["preview"]) as preview:
        assert preview.size == (64, 64) and preview.getpixel((10, 32)) == tuple(int(v) for v in texture.PART_PALETTE[1])
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-view-parts/0.1" and report["names"] == ["face", "hair"]
    assert step["observations"][0]["front"] == {"face": 2048, "hair": 2048}
    only = executor.run_recipe(_recipe(tmp_path, {"view": FRONT, "parts": ["face"]}), tmp_path / "runs")
    assert only["steps"]["view"]["observations"][0]["front"] == {"face": 4096}


class Context:
    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir
        self.tools = {}


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"view": FRONT, "parts": ["hat"]}, {"occlusion": "o.npz"}, "npz に無い部品"),
    ({"view": FRONT, "parts": []}, {"occlusion": "o.npz"}, "parts"),
    ({"view": FRONT}, {"report": "r.json"}, "occlusion"),
    ({"view": FRONT, "wobble": 1}, {"occlusion": "o.npz"}, "知らないパラメーター"),
    ({"view": {"from": "-Y"}}, {"occlusion": "o.npz"}, "view"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    _parts_npz(tmp_path / "parts.npz")
    with pytest.raises(ToolError, match=message):
        get("texture.view_parts").function({"parts": tmp_path / "parts.npz"}, parameters, {k: tmp_path / v for k, v in outputs.items()},
                                           Context(tmp_path))


def _face_mesh():
    from model3d.mesh import Mesh
    vertices, faces = _plane(0.0)
    uv = [[0, 0], [1, 0], [1, 1], [0, 1]]
    return Mesh(vertices, faces.tolist(), uv, name="face")


def _halves(size=64):
    image = np.zeros((size, size, 4))
    image[:, : size // 2, :3] = (220, 30, 30)
    image[:, size // 2:, :3] = (30, 30, 220)
    image[..., 3] = 255
    return image


def test_parts_in_front_hide_the_projection_and_no_map_keeps_it_unchanged():
    mesh = _face_mesh()
    matrix, offset, _ = texture.alignment(*IDENTITY)
    base = np.full((64, 64, 3), 128.0)
    plain, plain_weights, plain_report = texture.project_reference(mesh, base, _halves(), FRONT, matrix, offset, expand=0.0)
    same, same_weights, _ = texture.project_reference(mesh, base, _halves(), FRONT, matrix, offset, expand=0.0, occlusion=None)
    assert np.array_equal(plain, same) and np.array_equal(plain_weights, same_weights) and "occluded_fragments" not in plain_report
    owners, depth, _ = texture.view_parts([("face", *_plane(0.0)), ("hair", *_plane(-0.5, x0=-1.0, x1=0.0))], FRONT)
    hidden, weights, report = texture.project_reference(mesh, base, _halves(), FRONT, matrix, offset, expand=0.0,
                                                        occlusion=(owners, depth, 0, ["face", "hair"]))
    assert report["occluded_by"] == {"hair": report["occluded_fragments"]} and report["occluded_fragments"] > 1000
    assert weights[32, 10] == 0 and np.allclose(hidden[32, 10], 128)                    # 髪の後ろは塗らない（元の色）
    assert weights[32, 50] == pytest.approx(1.0) and np.allclose(hidden[32, 50], [30, 30, 220])
    self_only, _, self_report = texture.project_reference(mesh, base, _halves(), FRONT, matrix, offset, expand=0.0,
                                                          occlusion=(np.zeros_like(owners), depth, 0, ["face"]))
    assert self_report["occluded_fragments"] == 0                                    # 自分の部品は隠さない（自分の見え隠れは z バッファ）


def _projection_recipe(tmp_path, parameters, occlusion=True):
    from model3d.mesh import Mesh
    _face_mesh().save(tmp_path / "face.json")
    Image.fromarray(np.full((64, 64, 3), 128, np.uint8)).save(tmp_path / "atlas.png")
    Image.fromarray(_halves().astype(np.uint8)).save(tmp_path / "reference.png")
    _parts_npz(tmp_path / "parts.npz")
    steps = [{"id": "view", "operation": "texture.view_parts", "inputs": {"parts": "$inputs.parts"}, "parameters": {"view": FRONT},
              "outputs": {"occlusion": "occlusion.npz"}},
             {"id": "paint", "operation": "texture.project_reference",
              "inputs": {"mesh": "$inputs.face", "texture": "$inputs.atlas", "reference": "$inputs.reference",
                         **({"occlusion": "$steps.view.occlusion"} if occlusion else {})},
              "parameters": {"view": FRONT, "alignment": {"model": IDENTITY[0], "reference": IDENTITY[1]}, "expand": 0.0, **parameters},
              "outputs": {"texture": "painted.png", "report": "paint.json"}}]
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "paint",
                                           "inputs": {"face": {"path": "face.json"}, "atlas": {"path": "atlas.png"},
                                                      "reference": {"path": "reference.png"}, "parts": {"path": "parts.npz"}},
                                           "steps": steps})
    return tmp_path / "recipe.json"


def test_the_projection_step_takes_the_map_and_checks_its_view_and_part(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_projection_recipe(tmp_path, {}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    report = json.loads(Path(result["steps"]["paint"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["occlusion"] == {"parts": ["face", "hair"], "self": "face"} and report["occluded_by"]["hair"] > 1000
    other = executor.run_recipe(_projection_recipe(tmp_path, {"occlusion_part": "hat"}), tmp_path / "runs2")
    assert other["status"] == "failed" and "occlusion_part" in other["steps"]["paint"]["error"]
    alone = executor.run_recipe(_projection_recipe(tmp_path, {"occlusion_part": "face"}, occlusion=False), tmp_path / "runs3")
    assert alone["status"] == "failed" and "一緒に" in alone["steps"]["paint"]["error"]
    np.savez(tmp_path / "wrong.npz", owner=np.zeros((64, 64), np.int32), depth=np.zeros((64, 64)), names=np.array(["face"]),
             view=np.array(json.dumps({**FRONT, "span": 3.0})))
    from model3d.ops.texture_ops import load_occlusion
    with pytest.raises(ToolError, match="視点"):
        load_occlusion(tmp_path / "wrong.npz", FRONT)
