"""check.three_view・texture.face_length: 合成の三面図で高さ・印の行・頭のかたまり・絵、合成の頭の絵で行の付け替え（帯の上は変えない・
帯の下はずらす）と悪い設定を確かめる。付け替えは、OpenCV があれば乱数の絵で OpenCV の remap（Lanczos4）と同じかも確かめる。
3 作目の VRM のダンスの絵での突き合わせ（台本と同じ数・同じ画素）は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json

import numpy as np
import pytest
from PIL import Image

from model3d import checkpoint
from model3d.io import ToolError

SKIN, PURPLE, BLUE = (230, 200, 190), (150, 30, 160), (40, 90, 220)
MARK_RULE = {"r": [120, 200], "g": [0, 60], "b": [130, 200]}        # 合成の紫の印
IRIS = {"r": [0, 120], "b": [150, 255], "b-r": [60, 255]}           # 合成の青い瞳
MARKS = [{"name": "cords", "rule": MARK_RULE, "range": [0.25, 0.4], "pick": "top"},
         {"name": "hem", "rule": MARK_RULE, "range": [0.5, 0.7], "pick": "bottom"}]


def _view(top=20, sole=420, cords=150, hem=260, arms=110, eyes=60):
    """300 × 440、透けの背景の人物（いちばん上 top・足の裏 sole）: 体は横の 130〜170、腕は arms 行に左右いっぱい、紫の印は
    cords〜cords+4 行と hem-4〜hem 行（体の中央に）、瞳は eyes 行に 2 つ（青の 5 × 5）。"""
    rgba = np.zeros((440, 300, 4), dtype=np.uint8)
    rgba[top:sole + 1, 130:170] = SKIN + (255,)
    rgba[arms, 20:280] = SKIN + (255,)
    rgba[cords:cords + 5, 135:165] = PURPLE + (255,)
    rgba[hem - 4:hem + 1, 135:165] = PURPLE + (255,)
    for x in (140, 156):
        rgba[eyes:eyes + 5, x:x + 5] = BLUE + (255,)
    return rgba


def test_view_rows_reads_heights_widest_row_and_marks():
    rgba = _view()
    mask = rgba[:, :, 3] > 128
    marks = [{**m, "least": 4, "strip": 0.07} for m in MARKS]
    rows = checkpoint.view_rows(rgba[:, :, :3], mask, marks)
    assert (rows["top_y"], rows["sole_y"], rows["height_px"], rows["centre_x"], rows["widest_y"]) == (20, 420, 400, 149, 110)
    assert rows["widest_frac"] == pytest.approx(90 / 400)
    assert rows["marks"]["cords"] == {"y": 150, "frac": pytest.approx(130 / 400)}     # 範囲の中のいちばん上の行
    assert rows["marks"]["hem"] == {"y": 260, "frac": pytest.approx(240 / 400)}       # いちばん下の行
    narrow = [{**marks[0], "range": [0.0, 0.2]}]
    assert checkpoint.view_rows(rgba[:, :, :3], mask, narrow)["marks"]["cords"] == {"y": None, "frac": None}
    blobs = checkpoint.head_blobs(rgba[:, :, :3], mask, IRIS, rows)
    # 5 × 5 の瞳は形が残り、2 × 2 の開きは OpenCV と同じく右下へ 1 画素ずれる（重心の x 142 → 143、158 → 159）
    assert [b[0] for b in blobs] == [25, 25] and sorted(b[1][0] for b in blobs) == [143.0, 159.0]


def _save(tmp_path, name, array):
    path = tmp_path / name
    Image.fromarray(array).save(path)
    return path


class Context:
    tools, step_dir, recipe_dir = {}, None, None


def test_three_view_operation_reports_each_view_and_draws(tmp_path):
    from model3d.ops import get
    inputs = {"front": _save(tmp_path, "front.png", _view()), "side": _save(tmp_path, "side.png", _view(cords=170, hem=280)),
              "back": _save(tmp_path, "back.png", _view(top=30))}
    outputs = {"report": tmp_path / "r.json", "sheet": tmp_path / "sheet.jpg", "heads": tmp_path / "heads.png"}
    result = get("check.three_view").function(inputs, {"marks": MARKS, "iris": IRIS}, outputs, Context())
    report = json.loads(outputs["report"].read_text(encoding="utf-8"))
    views = report["views"]
    assert views["side"]["marks"]["cords"]["y"] == 170 and views["back"]["height_px"] == 390
    assert views["front"]["mask"] == "alpha" and len(views["front"]["head_blobs"]) == 2
    assert report["marks"][0]["least"] == 4 and report["marks"][0]["strip"] == 0.07       # 既定を入れて残す
    assert result.review is True and "測っただけ" in result.unresolved[0]
    assert Image.open(outputs["sheet"]).height == 440 and Image.open(outputs["heads"]).width > 0
    report_only = get("check.three_view").function(inputs, {"report_only": True}, {"report": tmp_path / "o.json"}, Context())
    assert report_only.review is False and "head_blobs" not in json.loads((tmp_path / "o.json").read_text(encoding="utf-8"))["views"]["front"]


@pytest.mark.parametrize("marks, message", [
    ([{"name": "x", "rule": MARK_RULE, "range": [0.5, 0.4], "pick": "top"}], "range"),
    ([{"name": "x", "rule": MARK_RULE, "range": [0.1, 0.4], "pick": "middle"}], "pick"),
    ([{"name": "x", "rule": {"q": [0, 1]}, "range": [0.1, 0.4], "pick": "top"}], "rule"),
    ([{"name": "x", "rule": MARK_RULE, "range": [0.1, 0.4], "pick": "top", "least": 0}], "least"),
    ([{"name": "x", "rule": MARK_RULE, "range": [0.1, 0.4], "pick": "top", "strip": 0.9}], "strip"),
    ([MARKS[0], MARKS[0]], "name"),
    ("cords", "marks"),
])
def test_three_view_rejects_bad_marks(tmp_path, marks, message):
    from model3d.ops import get
    inputs = {v: _save(tmp_path, f"{v}.png", _view()) for v in ("front", "side", "back")}
    with pytest.raises(ToolError, match=message):
        get("check.three_view").function(inputs, {"marks": marks}, {"report": tmp_path / "r.json"}, Context())


def test_lanczos_table_matches_the_opencv_formula_and_keeps_whole_rows():
    table = checkpoint.lanczos_table()
    assert table.shape == (32, 8) and table.dtype == np.float32
    assert table[0].tolist() == [0, 0, 0, 1, 0, 0, 0, 0]                          # 整数の行はそのまま
    assert np.allclose(table.sum(axis=1), 1, atol=1e-6)
    image = np.random.default_rng(3).random((20, 6, 2)).astype(np.float32)
    assert (checkpoint.remap_rows(image, np.arange(20, dtype=np.float64)) == image).all()
    shifted = checkpoint.remap_rows(image, np.arange(20, dtype=np.float64) + 30)      # 絵の外は 0
    assert (shifted == 0).all()


def test_remap_rows_equals_opencv_remap():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(11)
    image = rng.random((40, 9, 4)).astype(np.float32)
    rows = np.sort(rng.uniform(-6, 46, 40))
    map_y = np.repeat(rows.astype(np.float32)[:, None], 9, axis=1)
    map_x = np.repeat(np.arange(9, dtype=np.float32)[None, :], 40, axis=0)
    want = cv2.remap(image, map_x, map_y, interpolation=cv2.INTER_LANCZOS4, borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    assert (checkpoint.remap_rows(image, rows) == want).all()


def _head():
    """160 × 120 の透けつきの頭の絵: 行ごとに色が変わり、80 行目に黒い横線（あごのつもり）、30〜40 行の外は透け。"""
    rgba = np.zeros((160, 120, 4), dtype=np.uint8)
    rgba[:, :, 0] = np.arange(160)[:, None]
    rgba[:, :, 1] = 200
    rgba[:, :, 2] = 255 - np.arange(160)[:, None]
    rgba[:, :, 3] = 255
    rgba[80, :, :3] = 0
    rgba[:, 30:40] = 0                                   # 透けの所は色も 0（付け替えた後も 0）
    return rgba


def test_shorten_face_keeps_the_top_and_moves_the_chin_up():
    rgba = _head()
    out, info = checkpoint.shorten_face(rgba, eye_y=40, chin_y=80, d=20, k=0.9, start=0.1)
    assert info["band_y"] == [42.0, 80] and info["shift_px"] == pytest.approx(3.8) and info["chin_y_after"] == pytest.approx(76.2)
    assert (out[:43] == rgba[:43]).all()                                          # 帯より上は 1 画素も変わらない
    chin = 60 + int(np.argmin(out[60:120, 60, :3].astype(int).sum(axis=1)))     # 下の端は絵の外（0）を引くので、あごのあたりだけ見る
    assert chin in (76, 77)                                                       # あごの線が帯の長さの差だけ上へ
    assert (out[:, 30:40, 3] == 0).all()                                          # 透けは透けのまま


def test_face_length_operation_writes_a_png(tmp_path):
    from model3d.ops import get
    source = _save(tmp_path, "head.png", _head())
    outputs = {"image": tmp_path / "out.png", "report": tmp_path / "r.json"}
    get("texture.face_length").function({"image": source}, {"eye_y": 40, "chin_y": 80, "d": 20, "k": 0.9}, outputs, Context())
    written = np.asarray(Image.open(outputs["image"]))
    assert written.shape == (160, 120, 4)
    assert json.loads(outputs["report"].read_text(encoding="utf-8"))["start"] == 0.1


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"chin_y": 80, "d": 20, "k": 0.9}, None, "eye_y"),
    ({"eye_y": 40, "chin_y": 80, "d": 20}, None, " k "),
    ({"eye_y": 40, "chin_y": 80, "d": 20, "k": 2.0}, None, "k は"),
    ({"eye_y": 40, "chin_y": 80, "d": 0, "k": 0.9}, None, "d は"),
    ({"eye_y": 40, "chin_y": 41, "d": 20, "k": 0.9}, None, "chin_y"),
    ({"eye_y": 40, "chin_y": 500, "d": 20, "k": 0.9}, None, "絵の高さ"),
    ({"eye_y": 40, "chin_y": 80, "d": 20, "k": 0.9, "wobble": 1}, None, "知らないパラメーター"),
    ({"eye_y": 40, "chin_y": 80, "d": 20, "k": 0.9}, {"image": "out.jpg"}, ".png"),
])
def test_face_length_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    source = _save(tmp_path, "head.png", _head())
    declared = {k: tmp_path / v for k, v in (outputs or {"image": "out.png"}).items()}
    with pytest.raises(ToolError, match=message):
        get("texture.face_length").function({"image": source}, parameters, declared, Context())


def test_face_length_needs_transparency(tmp_path):
    from model3d.ops import get
    source = _save(tmp_path, "head.png", _head()[:, :, :3].copy())
    with pytest.raises(ToolError, match="透け"):
        get("texture.face_length").function({"image": source}, {"eye_y": 40, "chin_y": 80, "d": 20, "k": 0.9},
                                            {"image": tmp_path / "out.png"}, Context())
