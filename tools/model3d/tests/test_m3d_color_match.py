"""顔の投影の色合わせ（texture.color_match と texture.project_reference の color_match・keep）。CIELAB の変換、枠の中央値、
keep の外の 1 色、工程の設定。素材はプログラムで作った画像と板。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import texture
from model3d.io import ToolError, atomic_json

FRONT = {"from": "-Y", "center": [0.0, 0.0], "span": 2.0, "size": [64, 64]}
IDENTITY = ([[0.0, 0.0], [64.0, 0.0]], [[0.0, 0.0], [64.0, 0.0]])


def test_cielab_matches_the_standard_values_and_round_trips():
    assert np.allclose(texture.rgb_to_lab([255, 255, 255]), [100, 0, 0], atol=1e-3)
    assert np.allclose(texture.rgb_to_lab([0, 0, 0]), [0, 0, 0], atol=1e-9)
    assert np.allclose(texture.rgb_to_lab([255, 0, 0]), [53.2408, 80.0925, 67.2032], atol=1e-3)   # sRGB の赤（D65）
    rgb = np.random.default_rng(2).integers(0, 256, (500, 3)).astype(float)
    assert np.abs(texture.lab_to_rgb(texture.rgb_to_lab(rgb)) - rgb).max() < 1e-9
    assert np.allclose(texture.lab_8bit([50, -10, 20]), [127.5, 118, 148])


def test_cielab_stays_near_opencv_8bit_lab():
    cv2 = pytest.importorskip("cv2")
    rgb = np.random.default_rng(3).integers(0, 256, (2000, 3)).astype(np.uint8)
    theirs = cv2.cvtColor(rgb[None, :, ::-1], cv2.COLOR_BGR2LAB)[0].astype(float)
    assert np.abs(texture.lab_8bit(texture.rgb_to_lab(rgb)) - theirs).max() < 2.5     # OpenCV は 8 ビットで丸める


def _image(color, size=(40, 30), alpha=None):
    image = np.zeros((size[1], size[0], 4 if alpha is not None else 3))
    image[..., :3] = color
    if alpha is not None:
        image[..., 3] = alpha
    return image


def test_the_reference_colour_moves_to_the_model_colour_and_keeps_its_alpha():
    reference = _image((200, 150, 140), alpha=200)
    reference[0:5, 0:5, :3] = (10, 20, 30)                                         # 枠の外の色も同じだけずれる
    model = _image((230, 180, 170))
    out, report = texture.color_match(reference, model, [[5, 5, 15, 15]], [[10, 10, 20, 20], [20, 10, 30, 20]])
    assert np.allclose(out[15, 15, :3], [230, 180, 170], atol=0.05) and np.all(out[..., 3] == 200)
    assert report["model_pixels"] == 100 and report["reference_pixels"] == 200 and report["keep"] is False
    shifted = texture.lab_to_rgb(texture.rgb_to_lab([10, 20, 30]) + np.array(report["offset"]))
    assert np.allclose(out[2, 2, :3], np.clip(shifted, 0, 255), atol=1e-6)
    assert report["offset"] == pytest.approx(list(texture.rgb_to_lab([230, 180, 170]) - texture.rgb_to_lab([200, 150, 140])), abs=1e-3)


def test_outside_keep_paints_the_model_colour():
    reference = _image((200, 150, 140))
    keep = np.zeros((30, 40))
    keep[:, :20] = 255
    out, report = texture.color_match(reference, _image((230, 180, 170)), [[0, 0, 10, 10]], [[0, 0, 10, 10]], keep)
    assert np.allclose(out[5, 30], report["fill_rgb"]) and np.allclose(out[5, 5], [230, 180, 170], atol=0.05)
    with pytest.raises(ToolError, match="keep の大きさ"):
        texture.color_match(reference, reference, [[0, 0, 1, 1]], [[0, 0, 1, 1]], np.zeros((3, 3)))
    with pytest.raises(ToolError, match="外か、空"):
        texture.color_match(reference, reference, [[0, 0, 50, 10]], [[0, 0, 1, 1]])


def _halves(size=64):
    image = np.zeros((size, size, 4))
    image[:, : size // 2, :3] = (220, 30, 30)
    image[:, size // 2:, :3] = (30, 30, 220)
    image[..., 3] = 255
    return image


def _recipe(tmp_path, parameters, keep=False):
    from model3d.mesh import Mesh
    vertices = [[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1]]
    Mesh(vertices, [[0, 1, 2], [0, 2, 3]], [[0, 0], [1, 0], [1, 1], [0, 1]], name="face").save(tmp_path / "face.json")
    Image.fromarray(np.full((64, 64, 3), 128, np.uint8)).save(tmp_path / "atlas.png")
    Image.fromarray(_halves().astype(np.uint8)).save(tmp_path / "reference.png")
    Image.fromarray(np.full((64, 64), 255, np.uint8)).save(tmp_path / "keep.png")
    step_inputs = {"mesh": "$inputs.face", "texture": "$inputs.atlas", "reference": "$inputs.reference"}
    if keep:
        step_inputs["keep"] = "$inputs.keep"
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "paint",
                                           "inputs": {"face": {"path": "face.json"}, "atlas": {"path": "atlas.png"},
                                                      "reference": {"path": "reference.png"}, "keep": {"path": "keep.png"}},
                                           "steps": [{"id": "paint", "operation": "texture.project_reference", "inputs": step_inputs,
                                                      "parameters": {"view": FRONT, "alignment": {"model": IDENTITY[0], "reference": IDENTITY[1]},
                                                                     "expand": 0.0, **parameters},
                                                      "outputs": {"texture": "painted.png", "report": "paint.json"}}]})
    return tmp_path / "recipe.json"


def test_the_projection_step_matches_colours_before_painting(tmp_path):
    from model3d import executor
    matching = {"color_match": {"model": [[10, 10, 20, 20]], "reference": [[4, 4, 12, 12]]}}   # 灰色のモデルと、参照画像の赤
    result = executor.run_recipe(_recipe(tmp_path, matching), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["paint"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    gray_l = float(texture.lab_8bit(texture.rgb_to_lab([128, 128, 128]))[0])            # 描いたモデルは灰色 128
    assert report["color_match"]["model_pixels"] == 100 and report["color_match"]["model_lab_8bit"][0] == pytest.approx(gray_l, abs=0.01)
    with Image.open(step["artifacts"]["texture"]) as painted:
        assert np.allclose(painted.getpixel((10, 20)), (128, 128, 128), atol=1)       # 赤の半分は、モデルの灰色に合う
    plain = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs2")
    with Image.open(plain["steps"]["paint"]["artifacts"]["texture"]) as painted:
        assert np.allclose(painted.getpixel((10, 20)), (220, 30, 30), atol=1)         # 合わせなければ参照画像の赤のまま
    kept = executor.run_recipe(_recipe(tmp_path, matching, keep=True), tmp_path / "runs3")
    assert json.loads(Path(kept["steps"]["paint"]["artifacts"]["report"]).read_text(encoding="utf-8"))["color_match"]["keep"] is True
    lonely = executor.run_recipe(_recipe(tmp_path, {}, keep=True), tmp_path / "runs4")
    assert lonely["status"] == "failed" and "color_match" in lonely["steps"]["paint"]["error"]


@pytest.mark.parametrize("value, message", [
    ({"model": [[0, 0, 1, 1]]}, "color_match"),
    ({"model": [], "reference": [[0, 0, 1, 1]]}, "color_match.model"),
    ({"model": [[0, 0, 1, 1]], "reference": [[5, 5, 5, 9]]}, "color_match.reference"),
    ({"model": [[0, 0, 1.5, 1]], "reference": [[0, 0, 1, 1]]}, "color_match.model"),
    ("boxes", "color_match"),
])
def test_bad_colour_match_settings_are_refused(value, message):
    from model3d.ops.texture_ops import projection_settings
    with pytest.raises(ToolError, match=message):
        projection_settings({"view": FRONT, "alignment": {"model": IDENTITY[0], "reference": IDENTITY[1]}, "color_match": value})
