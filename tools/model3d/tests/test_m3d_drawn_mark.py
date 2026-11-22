"""描いた印のずれを色で測る（check.drawn_mark）: 印の測り方（OpenCV があれば同じ値）、合成の顔での中心と上の縁のずれ・許す枠の外の変化・
しきい値の合否、工程の受け渡しと悪い設定。素材はすべてプログラムで作った合成物。リポジトリ外の台本との比べは docs/model3d.md。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import color_marks
from model3d.io import ToolError

SKIN, LINE = [240.0, 200.0, 180.0], [200.0, 90.0, 90.0]
RULE = {"red_minus_green_above": 45, "red_above": 120, "green_below": 190}


def face(shift=0, gap=False, speck=None):
    image = np.full((200, 300, 3), SKIN)
    image[100:103, 100 + shift:201 + shift] = LINE
    if gap:
        image[100:103, 150 + shift:152 + shift] = SKIN                           # 線の途中の 2 画素のすき間（閉じてつなぐ）
    if speck is not None:
        image[speck[1], speck[0]] = [0, 0, 0]
    return image


def settings(**extra):
    from model3d.ops.check_ops import drawn_mark_settings
    return drawn_mark_settings({"window": [50, 60, 260, 140], "reference_rule": RULE, **extra})


def test_the_mark_is_measured_by_colour_and_a_small_gap_is_closed():
    mark = color_marks.measure_mark(face(gap=True), RULE, [50, 60, 260, 140])
    assert (mark["left"], mark["right"], mark["centre_x"], mark["top"], mark["bottom"], mark["width"]) == (100, 200, 150.0, 100, 102, 101)
    assert mark["mid_top"] == 100 and mark["mid_bottom"] == 102 and mark["area"] == 303
    split = color_marks.measure_mark(face(gap=True), RULE, [50, 60, 260, 140], close_size=1)
    assert split["width"] == 50                                                 # 閉じなければ、大きいほうの半分だけ
    assert color_marks.measure_mark(np.full((50, 50, 3), SKIN), RULE, [0, 0, 50, 50]) is None


@pytest.mark.parametrize("width", [1, 2, 3, 4, 5, 6])
def test_thin_marks_use_the_middle_column(width):
    image = np.full((40, 40, 3), SKIN)
    image[10:20, 15:15 + width] = LINE
    image[8:10, 15 + (width - 1) // 2] = LINE                                   # 真ん中の列（偶数の幅なら左の列）だけ上へ 2 画素
    mark = color_marks.measure_mark(image, RULE, [0, 0, 40, 40], close_size=1)
    assert mark["width"] == width and mark["centre_x"] == 15 + (width - 1) / 2
    assert mark["mid_top"] == 8 and mark["mid_bottom"] == 19


def test_measure_mark_matches_opencv():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(3)
    image = np.full((60, 80, 3), SKIN)
    image[rng.random((60, 80)) > 0.85] = LINE
    mark = color_marks.measure_mark(image, RULE, [5, 5, 75, 55])
    red = color_marks.color_rule(image, RULE)[5:55, 5:75].astype(np.uint8)
    red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    count, label, stats, centroid = cv2.connectedComponentsWithStats(red)
    i = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    assert mark["left"] == 5 + stats[i, cv2.CC_STAT_LEFT] and mark["width"] == stats[i, cv2.CC_STAT_WIDTH]
    assert mark["area"] == stats[i, cv2.CC_STAT_AREA] and mark["centroid_y"] == round(5 + float(centroid[i][1]), 1)


def test_a_shifted_mark_and_a_change_far_away_are_reported():
    report = color_marks.mark_shift(face(), face(shift=5, speck=(10, 10)),
                                    settings(search_margin=[20, 10, 20, 10], allowed_margin=[20, 10, 20, 10], metres_per_pixel=0.0001))
    assert report["centre_dx_px"] == 5.0 and report["centre_dx_mm"] == 0.5 and report["top_dy_px"] == 0
    assert report["width_ratio"] == 1.0 and report["height_px"] == 3
    assert report["changed_outside"] == 1 and report["changed_box"][:2] == [10, 10]   # 許す枠の外の 1 画素
    same = color_marks.mark_shift(face(), face(), settings())
    assert same["centre_dx_px"] == 0 and same["changed_pixels"] == 0 and "changed_outside" not in same


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


def write(tmp_path, name, image):
    Image.fromarray(image.astype(np.uint8)).save(tmp_path / name)
    return tmp_path / name


def test_drawn_mark_step_measures_only_or_judges_with_thresholds(tmp_path):
    from model3d import executor
    from model3d.io import atomic_json
    write(tmp_path, "before.png", face())
    write(tmp_path, "after.png", face(shift=5))
    base = {"window": [50, 60, 260, 140], "reference_rule": RULE, "search_margin": [20, 10, 20, 10], "allowed_margin": [20, 10, 20, 10]}

    def run(extra, folder):
        atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "mark",
                                               "inputs": {"before": {"path": "before.png"}, "after": {"path": "after.png"}},
                                               "steps": [{"id": "mouth", "operation": "check.drawn_mark",
                                                          "inputs": {"reference": "$inputs.before", "image": "$inputs.after"},
                                                          "parameters": {**base, **extra}, "outputs": {"report": "mark.json"}}]})
        return executor.run_recipe(tmp_path / "recipe.json", tmp_path / folder)

    measured = run({}, "runs")
    assert measured["status"] == "needs_review" and "未実施" in measured["steps"]["mouth"]["unresolved"][0]   # しきい値が無いと測っただけ
    report = json.loads(open(measured["steps"]["mouth"]["artifacts"]["report"], encoding="utf-8").read())
    assert report["centre_dx_px"] == 5.0 and report["pass"] is None
    assert run({"report_only": True}, "runs2")["status"] == "succeeded"
    assert run({"max_centre_shift_px": 6, "max_outside_pixels": 0}, "runs3")["status"] == "succeeded"
    failed = run({"max_centre_shift_px": 2}, "runs4")
    assert failed["status"] == "needs_review" and "中心の横のずれ" in failed["steps"]["mouth"]["unresolved"][0]


@pytest.mark.parametrize("parameters, message", [
    ({"reference_rule": RULE}, "window"),
    ({"window": [50, 60, 260, 140]}, "reference_rule"),
    ({"window": [50, 60, 260, 140], "reference_rule": {"red_above": 1}}, "reference_rule"),
    ({"window": [50, 60, 260, 140], "reference_rule": RULE, "search_margin": [1, 2]}, "search_margin"),
    ({"window": [50, 60, 260, 140], "reference_rule": RULE, "max_outside_pixels": 0}, "allowed_margin"),
    ({"window": [50, 60, 260, 140], "reference_rule": RULE, "glow": 1}, "知らないパラメーター"),
    ({"window": [50, 60, 400, 140], "reference_rule": RULE}, "はみ出し"),
])
def test_drawn_mark_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    before, after = write(tmp_path, "before.png", face()), write(tmp_path, "after.png", face(shift=5))
    with pytest.raises(ToolError, match=message):
        get("check.drawn_mark").function({"reference": before, "image": after}, parameters, {"report": tmp_path / "r.json"}, Context(tmp_path))


def test_drawn_mark_stops_when_the_original_mark_is_missing(tmp_path):
    from model3d.ops import get
    before, after = write(tmp_path, "before.png", np.full((200, 300, 3), SKIN)), write(tmp_path, "after.png", face())
    with pytest.raises(ToolError, match="もとの絵"):
        get("check.drawn_mark").function({"reference": before, "image": after}, {"window": [50, 60, 260, 140], "reference_rule": RULE},
                                         {"report": tmp_path / "r.json"}, Context(tmp_path))
