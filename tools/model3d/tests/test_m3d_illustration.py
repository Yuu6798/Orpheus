"""check.illustration: 合成の立ち絵と T ポーズの体の絵（緑の背景・透け）で、行の幅・頭身・くびれ・確認用の絵・悪い設定を確かめる。
3 作目の VRM のダンスの絵での突き合わせ（台本と同じ数）は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import checkpoint
from model3d.io import ToolError

GREEN = (0, 255, 0)


def _figure(alpha=False):
    """200 × 400 の人物: 頭（20〜59 行）、胴（60〜199 行・幅 60）、腕（70〜149 行・幅 15 を左右に離して）、脚（200〜380 行・幅 20 が 2 本）。
    308 行に幅 3 の細い区間（髪の毛先のつもり。min_run で除く）。"""
    person = np.zeros((400, 200), dtype=bool)
    person[20:60, 85:115] = True
    person[60:200, 70:130] = True
    person[70:150, 40:55] = True
    person[70:150, 145:160] = True
    person[200:381, 75:95] = True
    person[200:381, 105:125] = True
    person[308, 10:13] = True
    if alpha:
        rgba = np.zeros((400, 200, 4), dtype=np.uint8)
        rgba[:, :, :3] = GREEN                            # 透けで決めるので、人物も緑のまま
        rgba[person, 3] = 255
        return rgba
    rgb = np.zeros((400, 200, 3), dtype=np.uint8)
    rgb[:] = GREEN
    rgb[person] = (230, 200, 190)
    return rgb


ROWS = [{"name": "waist", "at": 0.3, "kind": "torso"}, {"name": "upper_arm", "at": 0.2, "kind": "arms"},
        {"name": "calf", "at": 0.8, "kind": "legs"}, {"name": "thigh", "at": 0.6, "kind": "legs"}]


class Context:
    tools, step_dir, recipe_dir = {}, None, None


def _run(tmp_path, image, parameters, outputs=None):
    from model3d.ops import get
    path = tmp_path / "figure.png"
    Image.fromarray(image).save(path)
    outputs = outputs or {"report": tmp_path / "report.json"}
    result = get("check.illustration").function({"image": path}, parameters, outputs, Context())
    return result, json.loads(outputs["report"].read_text(encoding="utf-8"))


@pytest.mark.parametrize("alpha", [False, True])
def test_standing_rows_read_torso_arms_and_legs(tmp_path, alpha):
    result, report = _run(tmp_path, _figure(alpha), {"rows": ROWS, "chin_y": 60})
    assert report["mask"] == ("alpha" if alpha else "color")
    assert (report["top_y"], report["bottom_y"], report["height_px"]) == (20, 380, 360)
    assert report["head_px"] == 40 and report["heads_tall"] == pytest.approx(9.0)
    rows = report["rows"]
    assert rows["waist"]["y"] == 128 and rows["waist"]["width_px"] == 60 and rows["waist"]["arms_separate"] is True
    assert rows["waist"]["runs_px"] == [15, 60, 15] and rows["waist"]["width_per_height"] == pytest.approx(60 / 360)
    assert rows["upper_arm"]["y"] == 92 and rows["upper_arm"]["width_px"] == 15.0
    assert rows["calf"]["y"] == 308 and rows["calf"]["runs_px"] == [20, 20] and rows["calf"]["width_px"] == 20.0   # 幅 3 は除く
    assert result.review is True                           # 測っただけ（合否は出さない）は要確認
    assert "測っただけ" in result.unresolved[0]


def test_top_row_and_narrow_runs_can_be_set(tmp_path):
    _, report = _run(tmp_path, _figure(), {"rows": ROWS, "top_y": 60, "min_run": 2, "report_only": True})
    assert report["height_px"] == 320 and "heads_tall" not in report
    assert report["rows"]["calf"]["y"] == int(round(60 + 0.8 * 320))
    image = _figure()
    image[2:378, 75:125] = (230, 200, 190)                 # 脚が 1 つの区間につながると半分にする
    _, report = _run(tmp_path, image, {"rows": [{"name": "legs", "at": 0.8, "kind": "legs"}]})
    assert report["rows"]["legs"]["width_px"] == 25.0 and "半分" in report["rows"]["legs"]["note"]


def _tpose_figure():
    """T ポーズ: 100 行に腕（左右いっぱい）、胴は 100〜199 行・幅 60 で、140〜150 行だけ幅 40（くびれ）、脚は 200〜380 行。"""
    person = np.zeros((400, 240), dtype=bool)
    person[30:100, 105:135] = True
    person[100, 10:230] = True
    person[100:200, 90:150] = True
    person[140:151, 90:150] = False
    person[140:151, 100:140] = True
    person[200:381, 95:115] = True
    person[200:381, 125:145] = True
    rgb = np.zeros((400, 240, 3), dtype=np.uint8)
    rgb[:] = GREEN
    rgb[person] = (230, 200, 190)
    return rgb


def test_tpose_measures_the_waist_on_the_floor_to_arm_scale(tmp_path):
    _, report = _run(tmp_path, _tpose_figure(), {"tpose": True})
    assert (report["arm_y"], report["floor_y"], report["S_px"]) == (100, 380, 280)
    assert report["centre_x"] == 119.5
    waist = report["waist"]
    assert waist["width_px"] == 40 and waist["width"] == pytest.approx(40 / 280) and 140 <= waist["y"] <= 150
    assert report["tpose"] == {"waist_range": [0.80, 0.92], "step": 0.005}
    _, report = _run(tmp_path, _tpose_figure(), {"tpose": {"waist_range": [0.6, 0.7], "step": 0.01}})
    assert report["waist"]["width_px"] == 60                 # くびれの外の高さだけを探す


def test_the_view_marks_the_rows(tmp_path):
    view = tmp_path / "view.png"
    _run(tmp_path, _figure(alpha=True), {"rows": ROWS, "chin_y": 60}, {"report": tmp_path / "r.json", "view": view})
    picture = np.asarray(Image.open(view).convert("RGB"))
    assert picture.shape == (400, 200, 3)
    assert tuple(picture[128, 199]) == (255, 0, 0)          # 行の赤い線（人物の外まで）
    assert tuple(picture[128, 100]) == (0, 255, 255)         # 区間の水色の線
    assert tuple(picture[5, 5]) == GREEN                     # 透けの絵は、人物の外を緑に
    tpose = tmp_path / "tpose.jpg"
    _run(tmp_path, _tpose_figure(), {"tpose": True}, {"report": tmp_path / "t.json", "view": tpose})
    assert Image.open(tpose).format == "JPEG"


def test_color_rules_and_masks(tmp_path):
    rgb = np.array([[[0, 255, 0], [0, 151, 89], [0, 150, 0], [255, 255, 255]]], dtype=np.uint8)
    assert checkpoint.color_rule(rgb, checkpoint.GREEN_BACKGROUND).tolist() == [[True, True, False, False]]
    assert checkpoint.color_rule(rgb, {"r-g": [-255, -100]}).tolist() == [[True, True, True, False]]
    alpha = np.array([[0, 128, 129, 255]], dtype=np.uint8)
    mask, used = checkpoint.person_mask(rgb, alpha)
    assert used == "alpha" and mask.tolist() == [[False, False, True, True]]
    mask, used = checkpoint.person_mask(rgb, np.full((1, 4), 200, np.uint8))          # 透けの無い透けの絵は色で決める
    assert used == "color" and mask.tolist() == [[False, False, True, True]]
    assert checkpoint.runs(np.array([1, 1, 0, 1, 1, 1, 0, 1], bool), 2) == [(0, 2), (3, 6)]


@pytest.mark.parametrize("parameters, message", [
    ({}, "どちらか 1 つ"),
    ({"rows": ROWS, "tpose": True}, "どちらか 1 つ"),
    ({"rows": []}, "rows"),
    ({"rows": [{"name": "w", "at": 1.2, "kind": "torso"}]}, "at"),
    ({"rows": [{"name": "w", "at": 0.3, "kind": "neck"}]}, "kind"),
    ({"rows": [{"name": "w", "at": 0.3, "kind": "torso"}, {"name": "w", "at": 0.4, "kind": "torso"}]}, "name"),
    ({"rows": [{"name": "w", "at": 0.3}]}, "rows\\[0\\]"),
    ({"rows": ROWS, "chin_y": 10}, "chin_y"),
    ({"rows": ROWS, "top_y": 500}, "top_y"),
    ({"rows": ROWS, "top_y": -1}, "top_y"),
    ({"rows": ROWS, "min_run": 0}, "min_run"),
    ({"rows": ROWS, "mask": "luma"}, "mask"),
    ({"rows": ROWS, "mask": "alpha"}, "透けのある絵"),
    ({"rows": ROWS, "background": {"g": [151, 300]}}, "background"),
    ({"rows": ROWS, "background": {"h": [0, 10]}}, "background"),
    ({"rows": ROWS, "alpha_min": 255}, "alpha_min"),
    ({"tpose": {"waist_range": [0.9, 0.8]}}, "waist_range"),
    ({"tpose": {"step": 0}}, "step"),
    ({"tpose": True, "top_y": 3}, "tpose"),
    ({"rows": ROWS, "wobble": 1}, "知らないパラメーター"),
])
def test_illustration_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        _run(tmp_path, _figure(), parameters)


def test_pictures_that_cannot_be_measured_are_refused(tmp_path):
    with pytest.raises(ToolError, match="人物の画素"):
        _run(tmp_path, np.full((40, 40, 3), GREEN, np.uint8), {"rows": ROWS})
    deep = tmp_path / "deep.png"
    Image.fromarray(np.zeros((8, 8), dtype=np.uint16)).save(deep)
    with pytest.raises(ToolError, match="8 ビット"):
        checkpoint.read_image(deep)
