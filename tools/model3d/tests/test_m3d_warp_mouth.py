"""絵の口を、もとの口の絵を変形して作る（texture.warp_mouth）: 色の印の探し方（OpenCV があれば同じ値）、3 次の補間（OpenCV の remap と同じ値）、
合成の顔での口の変形（口角の印が動かない・何もしなければ同じ絵・すき間は口角で 0・あごの線の内側）、工程の受け渡しと悪い設定。
素材はすべてプログラムで作った合成物。リポジトリ外の台本との比べは docs/model3d.md。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import color_marks, mouth_warp
from model3d.io import ToolError

SKIN, LINE, MARK = [240.0, 200.0, 180.0], [200.0, 90.0, 90.0], [150.0, 50.0, 60.0]
RULE = {"red_minus_green_above": 45, "red_above": 120, "green_below": 190}
LIPS = {"above": 30, "below": 70, "upper_keep": 10, "lower_keep": 40}           # 合成の顔の唇の高さ（画素）
JAW = {"green_below": 185, "start_below_line": 60, "keep_above": 34, "mask_above": 22}


def face(width=300, height=260, neck=None):
    """肌色の地に、口の線 1 本（y 100、x 100〜200）と、両端の口角の濃い印 2 つ（6 画素）。neck の行から下は首の色（緑が低い）。"""
    image = np.full((height, width, 3), SKIN)
    image[100:102, 100:201] = LINE
    image[99:103, 100:106] = MARK
    image[99:103, 195:201] = MARK
    if neck is not None:
        image[neck:] = [200.0, 150.0, 140.0]
    return image


def settings(**extra):
    from model3d.ops.texture_ops import warp_mouth_settings
    return warp_mouth_settings({"window": [60, 70, 240, 140], "line": RULE, "lips": LIPS, **extra})


def test_components_and_the_largest_one_match_opencv():
    rng = np.random.default_rng(1)
    mask = rng.random((40, 60)) > 0.6
    labels, count = color_marks.components(mask)
    cv2 = pytest.importorskip("cv2")
    n, cv_labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    assert count == n - 1
    pairs = {(a, b) for a, b in zip(labels.ravel(), cv_labels.ravel())}
    assert len(pairs) == n and len({a for a, _ in pairs}) == n                  # 同じ分け方（番号の付け方だけ違ってよい）
    areas = np.bincount(cv_labels.ravel())[1:]
    assert color_marks.largest_component(mask).sum() == areas.max()
    assert color_marks.largest_component(np.zeros((3, 3), bool)) is None


def test_remap_cubic_matches_opencv():
    rng = np.random.default_rng(2)
    image = (rng.random((30, 40, 3)) * 255).astype(np.float32)
    ys, xs = np.mgrid[0:30, 0:40].astype(np.float32)
    map_x = xs + rng.normal(0, 2, xs.shape).astype(np.float32)
    map_y = ys + rng.normal(0, 2, ys.shape).astype(np.float32)
    ours = mouth_warp.remap_cubic(image, map_x, map_y)
    assert np.array_equal(mouth_warp.remap_cubic(image, xs, ys), image)         # 動かさない画素は、そのまま
    cv2 = pytest.importorskip("cv2")
    theirs = cv2.remap(image, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
    assert np.abs(ours - theirs).max() < 1e-3


def test_the_line_is_measured_by_colour():
    measured = mouth_warp.measure(face(), settings())
    assert (measured["left"], measured["right"], measured["centre_x"]) == (100, 200, 150.0)
    assert measured["line_y_mean"] == pytest.approx(100.6, abs=0.3) and abs(measured["tilt"]) < 1e-6
    with pytest.raises(ValueError, match="口の線の色"):
        mouth_warp.measure(np.full((260, 300, 3), SKIN), settings())


def test_doing_nothing_keeps_the_picture():
    warped, mask, report = mouth_warp.warp(face(), settings())
    assert np.array_equal(warped, face()) and mask.max() == 0 and report["mask_box"] is None


def test_an_opened_mouth_keeps_the_corner_marks_and_closes_the_gap_at_the_corners():
    image = face()
    opened = settings(open=20, corner_mark=8, inside_color=[100, 30, 50], tongue_color=[200, 110, 120])
    warped, mask, report = mouth_warp.warp(image, opened)
    assert np.abs(warped[99:103, 100:104] - image[99:103, 100:104]).max() < 1.0  # 口角の印は動かない
    assert np.abs(warped[99:103, 197:201] - image[99:103, 197:201]).max() < 1.0
    column = warped[:, 150]
    inside = np.nonzero(np.abs(column - [100, 30, 50]).max(axis=1) < 40)[0]   # 真ん中の列の、口の中の色
    assert 100 <= inside.min() <= 104 and inside.max() >= 112                    # 線の下に、開ける量ぶんのすき間
    assert report["opened_pixels"] > 300 and mask[110, 150] > 0.99 and mask[5, 5] == 0
    assert np.abs(warped[105, 103] - image[105, 103]).max() < 1.0               # 口角の外（印の内側の端より外）にすき間は無い


def test_a_smile_raises_the_corners_and_a_wider_scale_moves_them_out():
    image = face()
    smile, _, _ = mouth_warp.warp(image, settings(corner_lift=6, scale_x=1.1))
    dark = lambda picture, x: np.nonzero(picture[:, x, 1] < 120)[0]
    assert dark(smile, 120).mean() < dark(image, 120).mean() - 1.5              # 口角の側は上がる
    assert abs(dark(smile, 150).mean() - dark(image, 150).mean()) < 0.6         # 真ん中は動かない
    assert len(dark(smile, 95)) > 0 and len(dark(image, 95)) == 0                # 横に広がる


def test_the_jaw_line_keeps_the_change_above_the_neck():
    image = face(neck=200)                                                     # 線（y 100）の 60 画素下から探す
    opened = settings(open=12, corner_mark=8, inside_color=[100, 30, 50], tongue_color=[200, 110, 120], jaw=JAW)
    warped, mask, report = mouth_warp.warp(image, opened)
    assert report["jaw_y_at_centre"] == 199.0                                  # σ 1.5 でならした緑が 185 を切る所（首の 1 画素上）
    assert mask[199 - 22:, :].max() == 0                                        # あごの線の 22 画素上から下は写さない
    assert report["columns_not_above_jaw"] == 0 and report["above_jaw_px"] >= 22
    assert np.abs(warped[200 - 34:] - image[200 - 34:]).max() < 1.0             # 動きは、あごの線の 34 画素上までに 0 へ戻る
    free, free_mask, _ = mouth_warp.warp(image, settings(open=12, corner_mark=8, inside_color=[100, 30, 50], tongue_color=[200, 110, 120]))
    assert free_mask[200 - 22:, :].max() > 0                                    # あごの線を測らなければ、収めない


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


def test_warp_mouth_step_writes_the_picture_the_mask_and_the_report(tmp_path):
    from model3d import executor
    from model3d.io import atomic_json
    Image.fromarray(face().astype(np.uint8)).save(tmp_path / "front.png")
    parameters = {"window": [60, 70, 240, 140], "line": RULE, "lips": LIPS, "scale_x": 0.8, "corner_lift": -4, "open": 18, "corner_mark": 8,
                  "inside_color": [100, 30, 50], "tongue_color": [200, 110, 120]}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "mouth", "inputs": {"front": {"path": "front.png"}},
                                           "steps": [{"id": "open", "operation": "texture.warp_mouth", "inputs": {"image": "$inputs.front"},
                                                      "parameters": parameters,
                                                      "outputs": {"image": "open.png", "mask": "open_mask.png", "report": "open.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["open"]
    report = json.loads(open(step["artifacts"]["report"], encoding="utf-8").read())
    assert report["left"] == 100 and report["mask_box"] is not None and report["settings"]["lengths"]["smooth"] == 25
    with Image.open(step["artifacts"]["mask"]) as mask:
        assert mask.mode == "L" and np.asarray(mask).max() == 255
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["open"]["reused_from"]                                   # 同じ絵と設定なら再利用する


GOOD = {"window": [60, 70, 240, 140], "line": RULE, "lips": LIPS}


@pytest.mark.parametrize("parameters, message", [
    ({"line": RULE, "lips": LIPS}, "window"),
    ({"window": [60, 70, 240, 140], "lips": LIPS}, "line"),
    ({**GOOD, "line": {"red_above": 3}}, "line"),
    ({"window": [60, 70, 240, 140], "line": RULE}, "lips"),
    ({**GOOD, "lips": {"above": 30}}, "lips"),
    ({**GOOD, "open": 10}, "corner_mark"),
    ({**GOOD, "open": 10, "corner_mark": 8}, "inside_color"),
    ({**GOOD, "scale_x": 0}, "scale_x"),
    ({**GOOD, "jaw": {"green_below": 185}}, "jaw"),
    ({**GOOD, "jaw": {**JAW, "tilt": 3}}, "知らないパラメーター"),
    ({**GOOD, "smile": 1}, "知らないパラメーター"),
    ({**GOOD, "tilt_after_stretch": "yes"}, "tilt_after_stretch"),
    ({**GOOD, "window": [60, 70, 400, 140]}, "はみ出し"),
    ({**GOOD, "open": 10, "corner_mark": 50, "inside_color": [100, 30, 50], "tongue_color": [200, 110, 120]}, "半分"),
])
def test_warp_mouth_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    Image.fromarray(face().astype(np.uint8)).save(tmp_path / "front.png")
    with pytest.raises(ToolError, match=message):
        get("texture.warp_mouth").function({"image": tmp_path / "front.png"}, parameters,
                                           {"image": tmp_path / "o.png", "mask": tmp_path / "m.png"}, Context(tmp_path))


@pytest.mark.parametrize("scale", [0.5, 1.2, 2.0])
def test_other_scales_stretch_the_lengths_without_breaking(scale):
    image = face()
    opened = settings(scale=scale, open=12, corner_mark=8, inside_color=[100, 30, 50], tongue_color=[200, 110, 120])
    assert opened["lengths"]["smooth"] == 25 * scale and opened["lengths"]["lower_keep"] == 40   # 顔の形に寄る長さは書いた値のまま
    warped, mask, report = mouth_warp.warp(image, opened)
    assert report["left"] == 100 and report["right"] == 200 and abs(report["tilt"]) < 1e-6 and report["opened_pixels"] > 100
    assert np.isfinite(warped).all() and np.isfinite(mask).all()


def test_a_corner_mark_as_wide_as_half_the_mouth_is_refused():
    opened = settings(open=10, corner_mark=50, inside_color=[100, 30, 50], tongue_color=[200, 110, 120])
    with pytest.raises(ValueError, match="半分"):
        mouth_warp.warp(face(), opened)


def tilted_face():
    """右へ下がる口の線（傾き 0.1。x 100〜200、太さ 3 画素）。"""
    image = np.full((260, 300, 3), SKIN)
    for x in range(100, 201):
        y = int(round(95 + 0.1 * (x - 100)))
        image[y:y + 3, x] = LINE
    return image


def line_height(picture, x):
    return np.nonzero(picture[:, x, 1] < 120)[0].mean()


def test_a_stretched_tilted_smile_is_level_with_the_stretched_slope():
    image = tilted_face()
    smile = dict(corner_lift=4, scale_x=1.4)
    exact, _, report = mouth_warp.warp(image, settings(**smile, tilt_after_stretch=True))
    script, _, _ = mouth_warp.warp(image, settings(**smile))                      # 台本と同じ（傾き × k）
    assert report["tilt"] > 0.08
    left, right = 150 - 56, 150 + 56                                             # 横に広げたあとの、真ん中から同じ距離の 2 列
    assert abs(line_height(exact, left) - line_height(exact, right)) < 1.5       # 傾き ÷ k で引くと、水平になる
    assert abs(line_height(script, left) - line_height(script, right)) > 4       # × k は広げた口で直しすぎる
    same, _, _ = mouth_warp.warp(image, settings(corner_lift=4, tilt_after_stretch=True))
    assert np.array_equal(same, mouth_warp.warp(image, settings(corner_lift=4))[0])   # k = 1 なら、どちらも同じ
