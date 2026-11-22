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


# ---- 足した描き方（上へたわむ弧・太さの変わる線・肌で埋める消し方・半目・写す範囲） --------------------------------------------

MAIN_DIGEST = "290ea3f8748c3da6eb42ac6374cfeb1d150de02533ca52b274bd16880bb5f239"   # 足す前の eye_line.draw の結果（同じ絵と設定）
SKIN_RULE = {"rgb_min": [200, 160, 150], "red_minus_green_below": 70, "red_at_least_blue": True}


def test_the_old_settings_draw_the_same_bytes_as_before():
    import hashlib
    result, _ = eye_line.draw(skin_with_thin_lines(), SETTINGS)
    assert hashlib.sha256(np.clip(result + 0.5, 0, 255).astype(np.uint8).tobytes()).hexdigest() == MAIN_DIGEST


def dark_rows(result, column):
    return np.nonzero(result[:, column].max(axis=1) < 120)[0]


def test_a_negative_sag_bows_the_arc_upward():
    image = skin_with_thin_lines()
    no_lashes = {**SETTINGS["lashes"], "length": [0, 0]}
    down, _ = eye_line.draw(image, {**SETTINGS, "sag": 16.0, "lashes": no_lashes})
    up, _ = eye_line.draw(image, {**SETTINGS, "sag": -16.0, "lashes": no_lashes})
    centre, end = 135, 114                                                  # 右の目の真ん中と、目頭の近く
    assert dark_rows(down, centre).mean() > dark_rows(down, end).mean() + 3   # 下へたわむ（真ん中が低い）
    assert dark_rows(up, centre).mean() < dark_rows(up, end).mean() - 3       # 上へたわむ（真ん中が高い）


def plain_skin(size=200):
    return np.full((size, size, 3), [240.0, 200.0, 180.0])


def stroke_settings(*strokes):
    return {**eye_line.DEFAULTS, "eyes": None,                              # 消す帯なしで、線だけを描く
            "strokes": [{**eye_line.STROKE_DEFAULTS, **stroke} for stroke in strokes]}


def test_a_stroke_follows_a_curve_and_its_width_changes_along_it():
    settings = stroke_settings({"points": [[40, 100], [100, 80], [160, 100]], "curve": True, "widths": [[0, 12], [1, 3]]})
    result, report = eye_line.draw(plain_skin(), settings)
    assert abs(dark_rows(result, 100).mean() - 80) < 1.5                    # 真ん中で 2 点目を通る
    assert len(dark_rows(result, 45)) > len(dark_rows(result, 155)) + 4      # 始め（太さ 12）が終わり（3）より太い
    assert report["strokes"] == 1 and report["line_pixels"] > 300
    assert (result[150:] == plain_skin()[150:]).all()                       # 線の無い所は元の画素のまま
    blue = stroke_settings({**settings["strokes"][0], "widths": {"middle": 10, "ends": 2}, "color": [10, 10, 200]})
    shaded, _ = eye_line.draw(plain_skin(), blue)
    is_blue = lambda column: (np.abs(shaded[:, column] - [10, 10, 200]).max(axis=1) < 30).sum()
    assert len(dark_rows(shaded, 100)) == 0 and is_blue(100) > is_blue(45)  # 線ごとの色。弓の形は真ん中が太い


def test_later_strokes_are_drawn_over_earlier_ones_and_span_cuts_the_line():
    settings = stroke_settings({"points": [[20, 100], [180, 100]], "widths": [[0, 10], [1, 10]], "color": [0, 0, 255]},
                               {"points": [[20, 100], [180, 100]], "widths": [[0, 4], [1, 4]], "color": [255, 0, 0], "span": [0.5, 1.0]})
    result, _ = eye_line.draw(plain_skin(), settings)
    assert result[100, 60, 2] > 200 and result[100, 60, 0] < 60             # 前半は始めの線（青）だけ
    assert result[100, 150, 0] > 200 and result[100, 150, 2] < 60          # 後半は後の線（赤）が上
    assert result[96, 150, 2] > 200                                         # 後の線の外は、始めの線のまま


def face_with_eye_and_hair(size=200):
    image = plain_skin(size)
    image[:, :20] = [128, 128, 128]                                         # 切れた背景（灰色。肌らしくない）
    image[70:110, 60:140] = [255, 255, 255]                                 # 白目（肌らしい明るさだが、目の枠の中なので使わない）
    image[80:100, 85:115] = [60, 30, 90]                                    # 瞳
    image[60:120, 140:150] = [40, 30, 30]                                   # 枠の右の外の、顔に寄った髪
    return image


def fill_settings(**extra):
    fill = {**eye_line.SKIN_FILL_DEFAULTS, "boxes": [[60, 70, 140, 110]], "skin": SKIN_RULE, "keep_dark_below": 100,
            "sigmas": [6.0, 12.0], "exclude": 6, "grow": 4, "feather": 2.0, "soften": 1.0, **extra}
    return {**eye_line.DEFAULTS, "eyes": None, "skin_fill": fill}


def test_skin_fill_paints_the_box_from_skin_like_pixels_only():
    image = face_with_eye_and_hair()
    result, report = eye_line.draw(image, fill_settings())
    assert np.abs(result[90, 100] - [240, 200, 180]).max() < 3              # 瞳と白目は肌の色に（白目は使わない）
    assert np.abs(result[90, 62] - [240, 200, 180]).max() < 3
    assert np.abs(result[60:120, 145:150] - image[60:120, 145:150]).max() < 0.5   # 枠の外の帯の髪（暗い画素）は埋めない
    assert (result[150:] == image[150:]).all() and (result[:, :20] == image[:, :20]).all()   # 遠い所と灰色の背景はそのまま
    assert report["erase"] == "skin_fill" and report["filled_pixels"] >= 80 * 40
    loose, _ = eye_line.draw(image, fill_settings(keep_dark_below=None))
    assert np.abs(loose[90, 142] - image[90, 142]).max() > 30               # 決まりを外せば、帯の髪も塗る


def test_half_closed_eye_fills_only_the_upper_part_and_draws_the_lid():
    image = face_with_eye_and_hair()
    settings = fill_settings(boxes=[[60, 70, 140, 92]], exclude_boxes=[[60, 70, 140, 110]])
    settings["strokes"] = [{**eye_line.STROKE_DEFAULTS, "points": [[58, 92], [100, 96], [142, 92]], "curve": True,
                            "widths": [[0, 3], [0.5, 6], [1, 3]]}]
    result, report, mask = eye_line.draw(image, settings, with_mask=True)
    assert np.abs(result[78, 100] - [240, 200, 180]).max() < 3              # 上まぶたを下ろした所は肌
    assert (result[108:110, 70:130] == image[108:110, 70:130]).all()     # 白目の下は元のまま（ぼかしの届く先）
    assert result[96, 100].max() < 80                                        # 縁の線
    assert mask[96, 100] == 1.0 and mask[78, 100] > 0.99 and mask[107, 100] < 1e-3 and mask[150, 100] == 0
    leaked, _ = eye_line.draw(image, fill_settings(boxes=[[60, 70, 140, 92]]))   # 目全体を除かないと、下の白目も肌の色に使う
    assert np.abs(leaked[78, 100] - result[78, 100]).max() > 1


def test_wide_gaussian_is_the_same_blur():
    rng = np.random.default_rng(3)
    image = rng.random((30, 45, 3)) * 255
    for sigma in (1.2, 4.0, 30.0):                                          # 窓が絵より広くても同じ
        assert np.abs(eye_line.wide_gaussian(image, sigma) - eye_line.gaussian(image, sigma)).max() < 1e-9


def test_closed_eye_line_step_with_strokes_skin_fill_and_mask(tmp_path):
    from model3d.ops import get
    Image.fromarray(face_with_eye_and_hair().astype(np.uint8)).save(tmp_path / "face.png")
    parameters = {"skin_fill": {"boxes": [[60, 70, 140, 110]], "skin": SKIN_RULE, "keep_dark_below": 100, "sigmas": [6, 12],
                                "exclude": 6, "grow": 4},
                  "strokes": [{"points": [[58, 104], [100, 112], [142, 104]], "curve": True, "widths": {"middle": 6, "ends": 2},
                               "name": "閉じ目"}]}
    get("texture.closed_eye_line").function({"image": tmp_path / "face.png"}, parameters,
                                            {"image": tmp_path / "o.png", "mask": tmp_path / "m.png", "report": tmp_path / "r.json"},
                                            Context(tmp_path))
    with Image.open(tmp_path / "m.png") as mask:
        weights = np.asarray(mask)
    assert weights.ndim == 2 and weights[90, 100] == 255 and weights[180, 100] == 0
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["strokes"] == 1 and report["settings"]["strokes"][0]["samples"] == 120 and "name" not in report["settings"]["strokes"][0]


GOOD_STROKE = {"points": [[1, 1], [2, 2]], "widths": [[0, 1], [1, 1]]}
FILL = {"boxes": [[1, 1, 5, 5]], "skin": SKIN_RULE, "sigmas": [4], "exclude": 1, "grow": 1}


@pytest.mark.parametrize("parameters, message", [
    ({"strokes": [{**GOOD_STROKE, "curve": True}], "erase_rows": [1, 2], "erase_columns": [1, 2]}, "curve"),
    ({"strokes": [{**GOOD_STROKE, "widths": [[0.5, 1], [0.2, 1]]}], "erase_rows": [1, 2], "erase_columns": [1, 2]}, "位置"),
    ({"strokes": [{**GOOD_STROKE, "widths": {"middle": 2}}], "erase_rows": [1, 2], "erase_columns": [1, 2]}, "弓"),
    ({"strokes": [{**GOOD_STROKE, "glow": 1}], "erase_rows": [1, 2], "erase_columns": [1, 2]}, "知らないパラメーター"),
    ({"strokes": [GOOD_STROKE], "erase_rows": [1, 2], "erase_columns": [1, 2], "end_y": 3}, "end_y"),
    ({"strokes": [GOOD_STROKE], "erase_rows": [1, 2], "erase_columns": [1, 2], "skin_fill": FILL},
     "一緒に使えません"),
    ({"strokes": [GOOD_STROKE], "skin_fill": {**FILL, "skin": {"red_at_least_blue": True}}}, "rgb_min"),
    ({"strokes": [GOOD_STROKE], "skin_fill": {"boxes": [[1, 1, 5, 5]], "skin": SKIN_RULE}}, "sigmas, exclude, grow"),
    ({"strokes": [GOOD_STROKE], "skin_fill": {**FILL, "boxes": [[5, 1, 1, 5]]}}, "boxes"),
    ({"strokes": [GOOD_STROKE], "skin_fill": {**FILL, "exclude_boxes": []}}, "exclude_boxes"),
    ({"strokes": [GOOD_STROKE], "skin_fill": {**FILL, "exclude_all": 1}}, "exclude_all"),
])
def test_closed_eye_line_rejects_bad_strokes_and_fills(tmp_path, parameters, message):
    from model3d.ops import get
    Image.fromarray(skin_with_thin_lines().astype(np.uint8)).save(tmp_path / "closed.png")
    with pytest.raises(ToolError, match=message):
        get("texture.closed_eye_line").function({"image": tmp_path / "closed.png"}, parameters,
                                                {"image": tmp_path / "o.png", "report": tmp_path / "r.json"}, Context(tmp_path))


def test_the_step_with_the_old_settings_writes_the_same_pixels_as_before(tmp_path):
    import hashlib
    from model3d.ops import get
    Image.fromarray(skin_with_thin_lines().astype(np.uint8)).save(tmp_path / "closed.png")
    parameters = {k: SETTINGS[k] for k in ("eyes", "end_y", "erase_rows", "erase_columns", "sag", "thickness", "wing", "lashes", "close_px")}
    get("texture.closed_eye_line").function({"image": tmp_path / "closed.png"}, parameters, {"image": tmp_path / "o.png"}, Context(tmp_path))
    with Image.open(tmp_path / "o.png") as out:                                 # 設定の組み立てから通しても、足す前と画素まで同じ
        assert hashlib.sha256(np.asarray(out).tobytes()).hexdigest() == MAIN_DIGEST


def test_strokes_alone_need_no_erase_band(tmp_path):
    from model3d.ops import get
    image = skin_with_thin_lines()
    image[0:3, 0:3] = [10, 200, 10]                                            # 角の画素（消す帯があれば、ぼかしで変わっていた）
    Image.fromarray(image.astype(np.uint8)).save(tmp_path / "face.png")
    get("texture.closed_eye_line").function({"image": tmp_path / "face.png"}, {"strokes": [{"points": [[40, 120], [160, 120]],
                                                                                           "widths": [[0, 4], [1, 4]]}]},
                                            {"image": tmp_path / "o.png", "mask": tmp_path / "m.png", "report": tmp_path / "r.json"},
                                            Context(tmp_path))
    with Image.open(tmp_path / "o.png") as out, Image.open(tmp_path / "m.png") as mask:
        drawn, weights = np.asarray(out, dtype=np.float64), np.asarray(mask)
    assert (drawn[:100] == image[:100]).all() and drawn[120, 100].max() < 60   # 線の外は元のまま（元の細い線も残る）
    assert weights[120, 100] == 255 and weights[:100].max() == 0
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["erase"] == "none"


def test_skin_fill_without_skin_within_reach_is_refused():
    image = np.full((120, 120, 3), [128.0, 128.0, 128.0])                     # 肌らしい画素が 1 つも無い
    with pytest.raises(ValueError, match="肌らしい画素"):
        eye_line.draw(image, fill_settings(boxes=[[40, 40, 80, 80]]))


def test_exclude_all_keeps_the_other_eye_out_of_the_skin_sample():
    image = plain_skin(200)
    image[70:130, 30:90] = [255, 255, 255]                                      # 左の目（白目。肌らしい明るさ）
    image[70:130, 100:170] = [255, 255, 255]                                    # 右の目（ぼかしの窓が届く近さ）
    boxes = [[30, 70, 90, 130], [100, 70, 170, 130]]
    script, _ = eye_line.draw(image, fill_settings(boxes=boxes, sigmas=[30.0], keep_dark_below=None))
    exact, _ = eye_line.draw(image, fill_settings(boxes=boxes, sigmas=[30.0], keep_dark_below=None, exclude_all=True))
    assert np.abs(exact[100, 85] - [240, 200, 180]).max() < 0.1                 # 全部の枠を外すと、まわりの肌の色だけ
    assert script[100, 85, 2] > exact[100, 85, 2] + 20                           # 台本と同じ既定では、もう片方の白目が混ざる（白い斑）
