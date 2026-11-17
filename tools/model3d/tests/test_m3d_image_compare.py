"""check.body_image・check.face_image: 合成の全身の絵と顔の絵で、脚の物差し・表・重ねた絵・瞳の組・顔の切り出し・悪い設定を確かめる。
開き（モルフォロジー）とかたまりの番号の順は、OpenCV があれば乱数の絵で OpenCV と同じかも確かめる。3 作目の VRM のダンスの絵での
突き合わせ（台本と同じ数）は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import checkpoint
from model3d.io import ToolError

SKIN, BLUE = (230, 200, 190), (40, 90, 220)
IRIS = {"r": [0, 120], "b": [150, 255], "b-r": [60, 255]}           # 合成の青い瞳の色の決まり


def _body(waist=40):
    """200 × 400、緑の背景の全身: 胴 60〜199 行（幅 60、150〜160 行だけ幅 waist）、腕は離して、脚 200〜380 行。脚の幅は足の裏から
    h 画素の高さで、0〜9 は 20、10〜60 は 12（足首）、61〜110 は 22（ふくらはぎ）、111〜140 は 16（ひざ）、141〜180 は 24（太もも）。"""
    person = np.zeros((400, 200), dtype=bool)
    person[20:60, 85:115] = True
    person[60:200, 70:130] = True
    person[150:161, 70:130] = False
    person[150:161, 100 - waist // 2:100 + waist // 2] = True
    person[70:150, 40:55] = True
    person[70:150, 145:160] = True
    for h in range(0, 181):
        width = 20 if h < 10 else 12 if h <= 60 else 22 if h <= 110 else 16 if h <= 140 else 24
        for centre in (85, 115):
            person[380 - h, centre - width // 2:centre + width // 2] = True
    rgb = np.zeros((400, 200, 3), dtype=np.uint8)
    rgb[:] = (0, 255, 0)
    rgb[person] = SKIN
    return rgb, person


def test_legs_are_measured_on_the_sole_to_waist_scale():
    _, person = _body()
    m = checkpoint.measure_legs(person)
    assert (m["sole_y"], m["hem_y"], m["waist_y"], m["U_px"], m["centre_x"]) == (380, 200, 150, 230, 100.0)
    assert m["waist_w"] == pytest.approx(40 / 230) and m["hem_h"] == pytest.approx(180 / 230)
    assert m["ankle_w"] == pytest.approx(12 / 230) and m["ankle_h"] == pytest.approx(23 / 230)    # 0.10 U から上で、最初のいちばん細い行
    assert m["calf_w"] == pytest.approx(22 / 230) and m["calf_h"] == pytest.approx(65 / 230)
    assert m["knee_w"] == pytest.approx(16 / 230) and m["knee_h"] == pytest.approx(125.5 / 230)   # 細い所（3% 以内）の真ん中
    assert m["thigh_w"] == pytest.approx(24 / 230)


def test_the_table_compares_the_body_with_the_reference():
    reference = checkpoint.measure_legs(_body()[1])
    body = checkpoint.measure_legs(_body(waist=48)[1])
    table = checkpoint.compare_legs(reference, body)
    assert table["waist_w"]["body_vs_reference"] == pytest.approx(1.2) and table["knee_w"]["body_vs_reference"] == pytest.approx(1.0)
    assert table["waist_w"]["label"] == "ウエストの幅"


def test_legs_that_cannot_be_found_are_refused():
    person = np.zeros((100, 100), dtype=bool)
    person[10:90, 30:70] = True                          # 脚が分かれていない
    with pytest.raises(ToolError, match="脚"):
        checkpoint.measure_legs(person)


def test_open_mask_and_components_follow_opencv():
    mask = np.zeros((9, 9), dtype=bool)
    mask[2:5, 2:5] = True
    mask[7, 7] = True
    opened = checkpoint.open_mask(mask, 3)
    assert opened[2:5, 2:5].all() and opened.sum() == 9                          # 3 × 3 は残り、1 画素は消える
    shifted = checkpoint.open_mask(mask, 2)
    assert shifted[3:6, 3:6].all() and shifted.sum() == 9                         # 偶数の四角は右下へ 1 画素ずれる（OpenCV と同じ）
    blocks = np.zeros((4, 20), dtype=bool)
    blocks[0, 10] = True
    blocks[1, 2] = True
    # 画素の順なら (10, 0) が先だが、2 × 2 の区画の順では (2, 1) の区画が先
    assert checkpoint.components(blocks) == [(1, 2.0, 1.0), (1, 10.0, 0.0)]
    ring = np.zeros((5, 5), dtype=bool)
    ring[0, :] = ring[4, :] = ring[:, 0] = ring[:, 4] = True
    ring[2, 2] = True
    assert [b[0] for b in checkpoint.components(ring)] == [16, 1]                # 斜めでもつながる・離れた 1 画素は別


def test_open_mask_and_components_equal_opencv_on_random_masks():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(7)
    for trial in range(12):
        mask = rng.random((37, 53)) < (0.25 + 0.05 * (trial % 4))
        for size in (2, 3):
            want = cv2.morphologyEx(mask.astype(np.uint8), cv2.MORPH_OPEN, np.ones((size, size), np.uint8)) > 0
            assert (checkpoint.open_mask(mask, size) == want).all(), (trial, size)
        n, _, stats, centres = cv2.connectedComponentsWithStats(mask.astype(np.uint8))
        want = [(int(stats[i, cv2.CC_STAT_AREA]), float(centres[i][0]), float(centres[i][1])) for i in range(1, n)]
        got = checkpoint.components(mask)
        assert [g[0] for g in got] == [w[0] for w in want], trial
        assert np.allclose([g[1:] for g in got], [w[1:] for w in want], atol=1e-12), trial


def _face(eyes=((120, 140), (180, 140)), ornament=(230, 60)):
    """300 × 300、透けの背景の顔: 肌の円に、瞳（青い円、半径 6）2 つと、同じ色の大きな飾り（半径 12）。"""
    ys, xs = np.mgrid[0:300, 0:300]
    rgba = np.zeros((300, 300, 4), dtype=np.uint8)
    face = (xs - 150) ** 2 + (ys - 160) ** 2 <= 110 ** 2
    rgba[face] = SKIN + (255,)
    for (x, y), r in [(eye, 6) for eye in eyes] + [(ornament, 12)]:
        disc = (xs - x) ** 2 + (ys - y) ** 2 <= r * r
        rgba[disc] = BLUE + (255,)
    return rgba


def test_eyes_are_the_pair_side_by_side_of_the_same_size():
    rgb, alpha = _face()[:, :, :3], _face()[:, :, 3]
    eyes = checkpoint.find_eyes(checkpoint.whitened(rgb, alpha > 128), IRIS)
    assert eyes == [pytest.approx([120, 140]), pytest.approx([180, 140])]       # 飾りは高さが違うので組にしない
    assert checkpoint.find_eyes(rgb, {"r": [0, 10], "g": [250, 255], "b": [0, 10]}) is None
    crop, d, tilt = checkpoint.face_crop(rgb, eyes, 150)
    assert crop.shape == (810, 660, 3) and d == pytest.approx(60) and tilt == pytest.approx(0)
    assert tuple(crop[330, 255]) == BLUE and tuple(crop[330, 405]) == BLUE         # 目は上から 2.2 size、間隔 size


class Context:
    tools, step_dir, recipe_dir = {}, None, None


def _save(tmp_path, name, array):
    path = tmp_path / name
    Image.fromarray(array).save(path)
    return path


def test_body_image_operation_reports_the_table_and_draws_the_overlay(tmp_path):
    from model3d.ops import get
    reference = _save(tmp_path, "reference.png", _body()[0])
    body_rgb, person = _body(waist=48)
    rgba = np.zeros((400, 200, 4), dtype=np.uint8)
    rgba[person] = SKIN + (255,)
    body = _save(tmp_path, "body.png", rgba)
    outputs = {"report": tmp_path / "r.json", "overlay": tmp_path / "overlay.png"}
    result = get("check.body_image").function({"reference": reference, "body": body}, {"report_only": True}, outputs, Context())
    report = json.loads(outputs["report"].read_text(encoding="utf-8"))
    assert result.review is False and report["mask"] == {"reference": "color", "body": "alpha"}
    assert report["scale_body_to_reference"] == 1.0 and report["table"]["waist_w"]["body_vs_reference"] == pytest.approx(1.2)
    overlay = np.asarray(Image.open(outputs["overlay"]))
    assert overlay.shape == (308, 400, 3)                                       # 足の裏の 1.25 U 上（92 行）から下の端まで


def test_face_image_operation_aligns_both_faces(tmp_path):
    from model3d.ops import get
    reference = _save(tmp_path, "reference.png", _face())
    head = _save(tmp_path, "head.png", _face(eyes=((100, 150), (170, 150))))
    outputs = {"report": tmp_path / "r.json", "sheet": tmp_path / "sheet.jpg", "zoom": tmp_path / "zoom.png"}
    get("check.face_image").function({"reference": reference, "head": head}, {"iris": IRIS, "report_only": True}, outputs, Context())
    report = json.loads(outputs["report"].read_text(encoding="utf-8"))
    assert report["reference"]["eyes"] == [pytest.approx([120, 140]), pytest.approx([180, 140])]
    assert report["head"]["d_px"] == pytest.approx(70) and report["head"]["eyes_from"] == "iris"
    assert Image.open(outputs["sheet"]).size == (3 * 660 + 2 * 8, 810)
    assert Image.open(outputs["zoom"]).size == (2 * 720 + 8, 570)
    manual = {"reference_eyes": [120, 140, 180, 140], "head_eyes": [100, 150, 170, 150]}       # 両方を手で渡せば瞳の色は要らない
    get("check.face_image").function({"reference": reference, "head": head}, manual, {"report": tmp_path / "m.json"}, Context())
    assert json.loads((tmp_path / "m.json").read_text(encoding="utf-8"))["head"]["eyes_from"] == "manual"


@pytest.mark.parametrize("parameters, message", [
    ({}, "iris"),
    ({"head_eyes": [100, 150, 170, 150]}, "iris"),
    ({"iris": IRIS, "reference_eyes": [1, 2, 3]}, "reference_eyes"),
    ({"iris": IRIS, "kernel": 0}, "kernel"),
    ({"iris": IRIS, "size": 10}, "size"),
    ({"iris": {"r": [0, 10], "g": [250, 255], "b": [0, 10]}}, "瞳が 2 つ見つかりません"),
    ({"iris": IRIS, "reference_eyes": [5, 5, 5, 5]}, "同じ位置"),
    ({"iris": IRIS, "head_eyes": [170, 150, 100, 150]}, "左右が逆"),
    ({"iris": IRIS, "wobble": 1}, "知らないパラメーター"),
])
def test_face_image_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    reference, head = _save(tmp_path, "reference.png", _face()), _save(tmp_path, "head.png", _face())
    with pytest.raises(ToolError, match=message):
        get("check.face_image").function({"reference": reference, "head": head}, parameters, {"report": tmp_path / "r.json"}, Context())


def test_body_image_rejects_bad_settings(tmp_path):
    from model3d.ops import get
    reference = _save(tmp_path, "reference.png", _body()[0])
    for parameters, message in (({"min_run": 0}, "min_run"), ({"wobble": 1}, "知らないパラメーター"), ({"mask": "luma"}, "mask")):
        with pytest.raises(ToolError, match=message):
            get("check.body_image").function({"reference": reference, "body": reference}, parameters, {"report": tmp_path / "r.json"},
                                             Context())
