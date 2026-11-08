"""マスク作り（`mv fx masks`）。実制作の 2 つのマスク作りの台本（外の切り抜き + 多角形 + 飛び地消し、GrabCut + 多角形）を
書き写した参照実装と、合成の画像で画素まで照らし合わせる。素材はすべてプログラムで作る。"""
import json

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.fx import masks  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

W, H = 240, 160


def scene(seed=4):
    """暗い背景に、色の違う人物（楕円と胴）と地面。GrabCut が手がかりにできる色の差がある。"""
    rng = np.random.default_rng(seed)
    image = np.zeros((H, W, 3), np.uint8)
    image[:] = (40, 50, 70)
    image[110:] = (60, 60, 55)
    cv2.ellipse(image, (120, 60), (22, 28), 0, 0, 360, (200, 120, 90), -1)
    cv2.rectangle(image, (100, 80), (140, 130), (180, 100, 80), -1)
    noise = rng.integers(-12, 13, image.shape)
    return np.clip(image.astype(int) + noise, 0, 255).astype(np.uint8)


def write_scene(folder):
    rgb = scene()
    Image.fromarray(rgb).save(folder / "base.png")
    alpha = np.zeros((H, W), np.uint8)
    cv2.ellipse(alpha, (120, 60), (21, 27), 0, 0, 360, 255, -1)
    cv2.rectangle(alpha, (101, 81), (139, 118), 200, -1)                  # 足元が欠けた切り抜き（外のツールの出力の代わり）
    alpha[10:14, 10:14] = 180                                             # 遠くの飛び地
    Image.fromarray(alpha).save(folder / "alpha.png")
    return rgb, alpha


BLADE = [(0.55, 0.30), (0.80, 0.10), (0.82, 0.12), (0.58, 0.34)]
BOOT = [(0.42, 0.72), (0.50, 0.72), (0.50, 0.82), (0.42, 0.82)]
GROUND = [(0.0, 0.70), (1.0, 0.70), (1.0, 1.0), (0.0, 1.0)]
PERSON = [(96, 30), (144, 30), (146, 134), (96, 134)]
HORN = [(118, 26), (124, 26), (121, 18)]
GROUND_PX = [(0, 112), (240, 112), (240, 160), (0, 160)]


# ---- 実制作の台本の書き写し --------------------------------------------------------

def production_poly(shape, points, norm=True):
    h, w = shape
    mask = np.zeros(shape, np.uint8)
    pts = [(x * w, y * h) for x, y in points] if norm else points
    cv2.fillPoly(mask, [np.array(pts, np.int32)], 255)
    return mask


def production_alpha_masks(rembg, base_bgr):
    shape = rembg.shape
    blade = cv2.GaussianBlur(production_poly(shape, BLADE), (0, 0), 1.0)
    subject = cv2.max(cv2.max(rembg, blade), cv2.GaussianBlur(production_poly(shape, BOOT), (0, 0), 1.5))
    hard = (subject > 127).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(hard)
    if n > 1:
        keep = np.isin(labels, 1 + np.flatnonzero(stats[1:, cv2.CC_STAT_AREA] >= 0.02 * stats[1:, cv2.CC_STAT_AREA].max()))
        region = cv2.dilate(keep.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9)))
        subject = np.where(region > 0, subject, 0).astype(np.uint8)
    ground = production_poly(shape, GROUND)
    ground[cv2.dilate((subject > 40).astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))) > 0] = 0
    ground_soft = np.minimum(cv2.GaussianBlur(ground, (0, 0), 5.0), 255 - subject)
    preview = base_bgr.astype(np.float32)
    tint = np.zeros_like(preview)
    tint[..., 2], tint[..., 1] = subject, ground_soft
    return subject, ground_soft, np.clip(preview * 0.6 + tint * 0.45, 0, 255).astype(np.uint8)


def production_grabcut_masks(base_bgr, band=14):
    shape = base_bgr.shape[:2]
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * band + 1, 2 * band + 1))
    person = production_poly(shape, PERSON, norm=False)
    init = np.full(shape, cv2.GC_BGD, np.uint8)
    init[cv2.dilate(person, kernel) > 0] = cv2.GC_PR_BGD
    init[person > 0] = cv2.GC_PR_FGD
    init[cv2.erode(person, kernel) > 0] = cv2.GC_FGD
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(7)
    cv2.grabCut(base_bgr, init, None, bgd, fgd, 5, cv2.GC_INIT_WITH_MASK)
    cut = np.where((init == cv2.GC_FGD) | (init == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    cut = cv2.morphologyEx(cut, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(cut)
    if n > 1:
        cut = np.where(labels == 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])), 255, 0).astype(np.uint8)
    subject = cv2.max(cut, production_poly(shape, HORN, norm=False))
    subject_soft = cv2.GaussianBlur(subject, (0, 0), 2.0)
    ground = production_poly(shape, GROUND_PX, norm=False)
    ground[cv2.dilate(subject, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))) > 0] = 0
    ground_soft = np.minimum(cv2.GaussianBlur(ground, (0, 0), 6.0), 255 - subject_soft)
    preview = base_bgr.astype(np.float32)
    tint = np.zeros_like(preview)
    tint[..., 2], tint[..., 1] = subject_soft, ground_soft
    preview = np.clip(preview * 0.6 + tint * 0.45, 0, 255).astype(np.uint8)
    cv2.polylines(preview, [np.array(PERSON, np.int32)], True, (255, 255, 0), 1)
    return subject_soft, ground_soft, preview


ALPHA_PLAN = {"schema_version": "fx-masks/0.1", "base": "base.png",
              "masks": [{"id": "subject", "steps": [{"op": "image", "path": "alpha.png"},
                                                    {"op": "polygon", "points": BLADE, "blur": 1.0},
                                                    {"op": "polygon", "points": BOOT, "blur": 1.5},
                                                    {"op": "islands", "keep": "share", "share": 0.02, "threshold": 127, "grow_px": 9}]},
                        {"id": "ground", "steps": [{"op": "polygon", "points": GROUND},
                                                   {"op": "cut", "mask": "subject", "threshold": 40, "grow_px": 11}],
                         "blur": 5.0, "exclusive_of": ["subject"]}]}
GRABCUT_PLAN = {"schema_version": "fx-masks/0.1", "base": "base.png", "units": "px",
                "masks": [{"id": "subject", "steps": [{"op": "grabcut", "points": PERSON, "band_px": 14, "iterations": 5, "seed": 7},
                                                      {"op": "close", "px": 7}, {"op": "islands", "keep": "largest"},
                                                      {"op": "polygon", "points": HORN}], "blur": 2.0},
                          {"id": "ground", "steps": [{"op": "polygon", "points": GROUND_PX},
                                                     {"op": "cut", "mask": "subject", "grow_px": 17, "before_blur": True}],
                           "blur": 6.0, "exclusive_of": ["subject"]}]}


def write_plan(folder, plan, name="plan.json"):
    (folder / name).write_text(json.dumps(plan), encoding="utf-8")
    return folder / name


def png(path):
    with Image.open(path) as image:
        return np.asarray(image)


# ---- 実制作の台本との一致 ---------------------------------------------------------

def test_the_alpha_and_polygon_plan_equals_the_production_script(tmp_path):
    rgb, alpha = write_scene(tmp_path)
    result = masks.make_masks(write_plan(tmp_path, ALPHA_PLAN), tmp_path / "out")
    subject, ground, preview = production_alpha_masks(alpha, np.ascontiguousarray(rgb[..., ::-1]))
    assert np.array_equal(png(result["masks"]["subject"]), subject) and np.array_equal(png(result["masks"]["ground"]), ground)
    assert np.array_equal(png(result["preview"]), preview[..., ::-1])
    assert png(result["masks"]["subject"])[12, 12] == 0                                       # 遠くの飛び地は消える
    assert result["counts"]["masks"]["subject"]["area_px"] == int(np.count_nonzero(subject > 127))
    assert result["counts"]["overlap_px"]["subject|ground"] == int(np.count_nonzero((subject > 20) & (ground > 20)))


def test_the_grabcut_plan_equals_the_production_script(tmp_path):
    rgb, _ = write_scene(tmp_path)
    result = masks.make_masks(write_plan(tmp_path, GRABCUT_PLAN), tmp_path / "out")
    subject, ground, preview = production_grabcut_masks(np.ascontiguousarray(rgb[..., ::-1]))
    assert np.array_equal(png(result["masks"]["subject"]), subject) and np.array_equal(png(result["masks"]["ground"]), ground)
    assert np.array_equal(png(result["preview"]), preview[..., ::-1])
    record = read_json(result["record"])
    assert record["schema_version"] == "fx-masks-record/0.1" and record["counts"]["grabcut_changed_px"]["subject#0"] > 0
    assert record["inputs"]["base"]["size"] == [W, H] and record["engine"]["opencv"] == cv2.__version__


# ---- 手順 ----------------------------------------------------------------------

def test_combine_modes():
    a = np.array([[0, 100, 200]], np.uint8)
    b = np.array([[50, 50, 50]], np.uint8)
    assert masks.combine(a, b, "max").tolist() == [[50, 100, 200]] and masks.combine(a, b, "min").tolist() == [[0, 50, 50]]
    assert masks.combine(a, b, "subtract").tolist() == [[0, 100, 200]] and masks.combine(a, b, "replace").tolist() == [[50, 50, 50]]
    assert masks.combine(a, np.full((1, 3), 255, np.uint8), "subtract").tolist() == [[0, 0, 0]]


def test_islands_keep_the_largest_or_a_share_and_can_grow():
    current = np.zeros((40, 60), np.uint8)
    current[5:25, 5:25] = 255                                                                 # 400 画素
    current[30:34, 40:44] = 200                                                               # 16 画素
    current[30:33, 50:53] = 90                                                                # しきい値の下
    assert masks.islands(current, "largest")[31, 41] == 0 and masks.islands(current, "largest")[10, 10] == 255
    assert masks.islands(current, "share", share=0.02)[31, 41] == 200                         # 400 の 2% = 8 以上は残す
    assert masks.islands(current, "share", share=0.05)[31, 41] == 0
    grown = masks.islands(current, "largest", grow_px=41)                                     # 半径 20 だけ広げる
    assert grown[31, 41] == 200 and grown[31, 51] == 0                                       # 近い飛び地は残り、遠いものは 0
    assert np.array_equal(masks.islands(np.zeros((4, 4), np.uint8)), np.zeros((4, 4), np.uint8))


def test_cut_can_use_the_state_before_the_final_blur(tmp_path):
    write_scene(tmp_path)
    plan = {"schema_version": "fx-masks/0.1", "base": "base.png", "units": "px",
            "masks": [{"id": "a", "steps": [{"op": "polygon", "points": [(100, 60), (140, 60), (140, 100), (100, 100)]}], "blur": 6.0},
                      {"id": "b", "steps": [{"op": "polygon", "points": [(0, 0), (240, 0), (240, 160), (0, 160)]},
                                            {"op": "cut", "mask": "a", "threshold": 0, "before_blur": True}]},
                      {"id": "c", "steps": [{"op": "polygon", "points": [(0, 0), (240, 0), (240, 160), (0, 160)]},
                                            {"op": "cut", "mask": "a", "threshold": 0}]}]}
    built = masks.build(masks.parse_plan(json.loads(json.dumps(plan)), tmp_path), tmp_path)        # JSON と同じ形（タプルは配列に）
    before, after = built["masks"]["b"]["final"], built["masks"]["c"]["final"]
    assert before[80, 97] == 255 and after[80, 97] == 0                                       # ぼかしで広がった縁を避けるかどうか
    assert before[80, 120] == 0 and after[80, 120] == 0


def test_read_gray_takes_alpha_or_luminance_or_16_bit(tmp_path):
    rgba = np.zeros((4, 4, 4), np.uint8)
    rgba[..., 3] = [[0, 50, 100, 255]] * 4
    Image.fromarray(rgba).save(tmp_path / "a.png")
    Image.fromarray(np.full((4, 4), 65535, np.uint16)).save(tmp_path / "d.png")
    Image.fromarray(np.full((4, 4, 3), 200, np.uint8)).save(tmp_path / "rgb.png")
    assert masks.read_gray(tmp_path / "a.png")[0].tolist() == [0, 50, 100, 255]
    assert masks.read_gray(tmp_path / "d.png")[0, 0] == 255 and masks.read_gray(tmp_path / "rgb.png")[0, 0] == 200


# ---- 書き出しと検証 ---------------------------------------------------------------

def test_existing_masks_are_kept_unless_forced(tmp_path):
    write_scene(tmp_path)
    plan = write_plan(tmp_path, ALPHA_PLAN)
    first = masks.make_masks(plan)
    assert sorted(first["written"]) == ["ground", "subject"] and (tmp_path / "mask_subject.png").is_file()
    again = masks.make_masks(plan)
    assert again["written"] == []                                                             # 同じ中身なら書き直さない
    Image.fromarray(np.zeros((H, W), np.uint8)).save(tmp_path / "mask_ground.png")            # 手で直したマスクの代わり
    with pytest.raises(PipelineError, match="--force"):
        masks.make_masks(plan)
    assert png(tmp_path / "mask_ground.png").max() == 0
    assert masks.make_masks(plan, force=True)["written"] == ["ground"]


def test_the_base_is_resized_to_the_mask_size_with_a_note(tmp_path):
    write_scene(tmp_path)
    plan = dict(ALPHA_PLAN, size=[W // 2, H // 2])
    Image.fromarray(np.asarray(Image.open(tmp_path / "alpha.png").resize((W // 2, H // 2)))).save(tmp_path / "alpha.png")
    result = masks.make_masks(write_plan(tmp_path, plan), tmp_path / "out")
    assert result["size"] == [W // 2, H // 2] and any("縮尺" in w for w in result["warnings"])
    with pytest.raises(PipelineError, match="縦横比"):
        masks.make_masks(write_plan(tmp_path, dict(ALPHA_PLAN, size=[W, W]), "square.json"), tmp_path / "out2")


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.__setitem__("schema_version", "fx-masks/9"), "schema_version"),
    (lambda p: p["masks"][1]["steps"][1].__setitem__("mask", "nobody"), "先に作るマスク"),
    (lambda p: p["masks"][1].__setitem__("exclusive_of", ["ground"]), "exclusive_of"),
    (lambda p: p["masks"][1].__setitem__("id", "subject"), "重複"),
    (lambda p: p["masks"][0]["steps"].append({"op": "close", "px": 4}), "奇数"),
    (lambda p: p["masks"][0]["steps"].append({"op": "sparkle"}), "正しくありません"),
    (lambda p: p["masks"][0]["steps"].append({"op": "polygon", "points": [(0, 0), (1, 1)]}), "正しくありません"),
    (lambda p: p["masks"][0]["steps"][0].__setitem__("path", "../alpha.png"), "alpha"),
    (lambda p: p["masks"][1].__setitem__("out", "mask_subject.png"), "重なって"),
])
def test_bad_plans_are_refused(tmp_path, mutate, message):
    write_scene(tmp_path)
    plan = json.loads(json.dumps(ALPHA_PLAN))
    mutate(plan)
    with pytest.raises(PipelineError, match=message):
        masks.make_masks(write_plan(tmp_path, plan))


def test_grabcut_on_an_impossible_polygon_explains_itself(tmp_path):
    write_scene(tmp_path)
    plan = dict(GRABCUT_PLAN, masks=[{"id": "all", "steps": [{"op": "grabcut", "points": [(0, 0), (240, 0), (240, 160), (0, 160)], "band_px": 1}]}])
    with pytest.raises(PipelineError, match="GrabCut"):
        masks.make_masks(write_plan(tmp_path, plan), tmp_path / "out")


def test_the_masks_command(tmp_path):
    write_scene(tmp_path)
    code, result, err = run_cli("fx", "masks", write_plan(tmp_path, ALPHA_PLAN), "--out", tmp_path / "out")
    assert code == 0, err
    assert set(result["masks"]) == {"subject", "ground"} and (tmp_path / "out" / "masks_record.json").is_file()
    assert result["preview"].endswith("masks_preview.png")
