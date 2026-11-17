"""閉じた目の線を描き直す（texture.closed_eye_line）: 窓の形・閉じる処理・ぼかし（OpenCV があれば同じ値）、合成の顔での消し方と描き方、
工程の受け渡しと悪い設定。素材はすべてプログラムで作った合成物。リポジトリ外の台本との比べは docs/model3d.md。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import eye_line
from model3d.io import ToolError, atomic_json

SETTINGS = {**eye_line.DEFAULTS, "eyes": [[90, 40], [110, 160]], "end_y": 60.0, "erase_rows": [52, 75], "erase_columns": [30, 170],
            "sag": 8.0, "thickness": 6.0, "wing": {"length": 6.0, "angle_deg": 28.0, "width": 3.0},
            "lashes": {"at": [0.2, 0.6], "length": [4, 5], "width": 2.0, "lean_deg": 22.0}, "close_px": 9}


def skin_with_thin_lines(size=200):
    image = np.full((size, size, 3), [240.0, 200.0, 180.0])
    image[60, 45:88] = [40, 20, 20]                                       # 細い閉じ目の線（左）
    image[60, 112:155] = [40, 20, 20]                                     # 右
    return image


def test_ellipse_kernel_is_the_opencv_shape():
    kernel = eye_line.ellipse_kernel(5)
    assert kernel.astype(int).tolist() == [[0, 0, 1, 0, 0], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [1, 1, 1, 1, 1], [0, 0, 1, 0, 0]]
    assert eye_line.ellipse_kernel(21).sum() == 333
    cv2 = pytest.importorskip("cv2")
    for size in (3, 9, 21, 27):
        assert (eye_line.ellipse_kernel(size) == cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (size, size)).astype(bool)).all()


def test_close_and_blur_match_opencv():
    rng = np.random.default_rng(5)
    image = rng.random((40, 50, 3)).astype(np.float32) * 255
    kernel = eye_line.ellipse_kernel(7)
    closed = eye_line.morph(eye_line.morph(image, kernel, np.maximum), kernel, np.minimum)
    blurred = eye_line.gaussian(closed, 1.2)
    cv2 = pytest.importorskip("cv2")
    expected = cv2.morphologyEx(image, cv2.MORPH_CLOSE, kernel.astype(np.uint8))
    assert np.abs(closed - expected).max() == 0
    assert np.abs(blurred - cv2.GaussianBlur(expected, (0, 0), 1.2)).max() < 1e-3


def test_the_thin_line_is_erased_and_the_arc_drawn():
    image = skin_with_thin_lines()
    result, report = eye_line.draw(image, SETTINGS)
    assert np.abs(result[45, 100] - image[45, 100]).max() == 0                  # 帯の外は元のまま
    assert result[60, 140].min() < 120 and result[66, 65].max() < 120            # 弧（下へたわむ）は暗い線の色
    assert np.abs(result[54, 100] - [240, 200, 180]).max() < 2                   # 目と目の間の帯は肌の色（元の線は無い所）
    assert report["line_pixels"] > 300 and report["changed_box"][0] >= 30 and report["changed_box"][2] <= 170
    narrow = eye_line.draw(image, {**SETTINGS, "thickness": 2.0})[1]
    assert narrow["line_pixels"] < report["line_pixels"]                         # 太さで塗る量が変わる
    scaled, scaled_report = eye_line.draw(np.repeat(np.repeat(image, 2, axis=0), 2, axis=1), {**SETTINGS, "base_width": 200})
    assert scaled_report["scale"] == 2.0 and scaled_report["changed_box"][0] >= 30   # 幅 200 で書いた位置を 400 の絵に伸ばす


def test_bands_past_the_edge_are_clipped_to_the_image():
    image = np.full((80, 120, 3), [240.0, 200.0, 180.0])
    image[3, 30:90] = [40, 20, 20]                                          # 上の端の近くの細い線
    settings = {**SETTINGS, "erase_rows": [-5, 10], "erase_columns": [20, 100], "close_px": 9}
    erased = eye_line.erase_band(image, settings, 1.0)
    assert np.abs(erased[3, 60] - [240, 200, 180]).max() < 5               # 負の座標でも帯は空にならず、線を消す（元は 40）
    assert (erased[40:] == image[40:]).all()                                # 帯から遠い所はそのまま
    with pytest.raises(ValueError, match="外"):
        eye_line.erase_band(image, {**settings, "erase_rows": [-20, -5]}, 1.0)


def test_closed_eye_line_step(tmp_path):
    from model3d import executor
    Image.fromarray(skin_with_thin_lines().astype(np.uint8)).save(tmp_path / "closed.png")
    parameters = {k: SETTINGS[k] for k in ("eyes", "end_y", "erase_rows", "erase_columns", "sag", "thickness", "wing", "lashes", "close_px")}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "eyes",
                                           "inputs": {"face": {"path": "closed.png"}},
                                           "steps": [{"id": "line", "operation": "texture.closed_eye_line", "inputs": {"image": "$inputs.face"},
                                                      "parameters": parameters, "outputs": {"image": "closed_line.png", "report": "line.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["line"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["size"] == [200, 200] and report["settings"]["close_px"] == 9 and report["settings"]["color"] == [44, 24, 22]
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["line"]["reused_from"]                                 # 絵と設定が同じなら再利用する


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path



def test_closed_eye_line_keeps_alpha_and_writes_png_only(tmp_path):
    from model3d.ops import get
    step = get("texture.closed_eye_line").function
    parameters = {k: SETTINGS[k] for k in ("eyes", "end_y", "erase_rows", "erase_columns", "sag", "thickness", "wing", "lashes", "close_px")}
    rgb = skin_with_thin_lines().astype(np.uint8)
    face = np.dstack([rgb, np.full(rgb.shape[:2], 255, np.uint8)])
    face[:20, :, 3] = 0                                                   # 上の帯は透け
    Image.fromarray(face, "RGBA").save(tmp_path / "face.png")
    Image.fromarray(rgb).save(tmp_path / "plain.png")
    step({"image": tmp_path / "face.png"}, parameters, {"image": tmp_path / "out.png"}, Context(tmp_path))
    step({"image": tmp_path / "plain.png"}, parameters, {"image": tmp_path / "plain_out.png"}, Context(tmp_path))
    with Image.open(tmp_path / "out.png") as out, Image.open(tmp_path / "plain_out.png") as plain:
        kept, drawn = np.asarray(out), np.asarray(plain)
    assert kept.shape[2] == 4 and (kept[..., 3] == face[..., 3]).all()   # 透けはそのまま
    assert drawn.shape[2] == 3 and (kept[..., :3] == drawn).all()         # 色は透けの無い絵と同じに描く
    with pytest.raises(ToolError, match="png"):
        step({"image": tmp_path / "face.png"}, parameters, {"image": tmp_path / "out.jpg"}, Context(tmp_path))

@pytest.mark.parametrize("parameters, message", [
    ({"end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2]}, "eyes"),
    ({"eyes": [[5, 5]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2]}, "eyes"),
    ({"eyes": [[5, 9]], "erase_rows": [1, 2], "erase_columns": [1, 2]}, "end_y"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [3, 2], "erase_columns": [1, 2]}, "erase_rows"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2], "thickness": 0}, "thickness"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2], "lashes": {"at": [0.1], "length": []}}, "lashes"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2], "color": [300, 0, 0]}, "color"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2], "supersample": 9}, "supersample"),
    ({"eyes": [[5, 9]], "end_y": 60, "erase_rows": [1, 2], "erase_columns": [1, 2], "glow": 1}, "知らないパラメーター"),
])
def test_closed_eye_line_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    Image.fromarray(skin_with_thin_lines().astype(np.uint8)).save(tmp_path / "closed.png")
    with pytest.raises(ToolError, match=message):
        get("texture.closed_eye_line").function({"image": tmp_path / "closed.png"}, parameters,
                                                {"image": tmp_path / "o.png", "report": tmp_path / "r.json"}, Context(tmp_path))
