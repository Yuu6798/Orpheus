"""繰り返し物の位置検出（`mv fx points`）。石畳のようなむらのある背景に、同じ形の小さな輪 60 個と形の違う点 20 個を
既知の位置に置いた合成の画像で、数個の種から輪だけを拾えることを確かめる。素材はすべてプログラムで作る。"""
import json
import math
import sys

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.fx import camera, masks, points, sky  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx.view import View  # noqa: E402
from mv_pipeline.io import PipelineError, read_json, sha256  # noqa: E402

W, H = 480, 320
COLS, ROWS = 10, 8                     # 物を 1 つずつ置く升目（48×40 画素）。物どうしは 27 画素以上離れる
PATCH = 15
HAND = [(0, 0), (1, 0), (0, -1), (-1, 1)]      # 手で読んだ種のずれ（画素）


# ---- 合成の場面 --------------------------------------------------------------------

def cobbles(rng):
    """石畳のようなむらのある背景: 石ごとの明るさ・暗い目地・ゆるい陰影・画素の雑音。"""
    gray = np.full((H, W), 96.0)
    stone_w, stone_h = 22, 14
    for row in range(H // stone_h + 1):
        shift = (row % 2) * (stone_w // 2)
        for col in range(-1, W // stone_w + 2):
            x0, y0 = col * stone_w + shift, row * stone_h
            gray[max(0, y0):y0 + stone_h, max(0, x0):max(0, x0 + stone_w)] += rng.uniform(-16, 16)
            gray[max(0, y0):y0 + 1, max(0, x0):max(0, x0 + stone_w)] -= 22
            if 0 <= x0 < W:
                gray[max(0, y0):y0 + stone_h, x0:x0 + 1] -= 22
    yy, xx = np.mgrid[0:H, 0:W]
    gray += 14 * np.sin(xx / 77.0) * np.cos(yy / 53.0)
    return gray + rng.normal(0, 4, gray.shape)


def draw_ring(gray, x, y, gain):
    """同じ形の小さな輪（外径 10 画素ほど。中心は少し暗い）。明るさだけ個体ごとに違う。"""
    layer = np.zeros(gray.shape, np.uint8)
    cv2.circle(layer, (x, y), 4, 255, 2, cv2.LINE_AA)
    gray += layer / 255.0 * 78 * gain
    layer[:] = 0
    cv2.circle(layer, (x, y), 1, 255, -1, cv2.LINE_AA)
    gray -= layer / 255.0 * 20 * gain


def draw_other(gray, x, y, kind, gain):
    """形の違う点: 塗りつぶした丸・四角・十字・斜めの棒・小さな丸。"""
    layer = np.zeros(gray.shape, np.uint8)
    if kind == 0:
        cv2.circle(layer, (x, y), 4, 255, -1, cv2.LINE_AA)
    elif kind == 1:
        cv2.rectangle(layer, (x - 4, y - 4), (x + 4, y + 4), 255, -1)
    elif kind == 2:
        cv2.line(layer, (x - 5, y), (x + 5, y), 255, 2)
        cv2.line(layer, (x, y - 5), (x, y + 5), 255, 2)
    elif kind == 3:
        cv2.line(layer, (x - 5, y - 5), (x + 5, y + 5), 255, 3)
    else:
        cv2.circle(layer, (x, y), 2, 255, -1, cv2.LINE_AA)
    gray += layer / 255.0 * 78 * gain


def scene(seed=11):
    """(RGB の画像, 輪の中心 60 個, 形の違う点の中心 20 個)。位置は升目の中でずらし、輪と点の升目は乱数で決める。"""
    rng = np.random.default_rng(seed)
    gray = cobbles(rng)
    cell_w, cell_h = W // COLS, H // ROWS
    places = [(col * cell_w + int(rng.integers(13, cell_w - 13)), row * cell_h + int(rng.integers(13, cell_h - 13)))
              for row in range(ROWS) for col in range(COLS)]
    order = rng.permutation(len(places))
    rings, others = [places[i] for i in order[:60]], [places[i] for i in order[60:]]
    for x, y in rings:
        draw_ring(gray, x, y, rng.uniform(0.8, 1.2))
    for index, (x, y) in enumerate(others):
        draw_other(gray, x, y, index % 5, rng.uniform(0.8, 1.2))
    gray = np.clip(gray + rng.normal(0, 3, gray.shape), 0, 255)
    return np.clip(np.stack([gray * 1.03, gray, gray * 0.94], -1), 0, 255).astype(np.uint8), rings, others


def norm(x, y):
    return [(x + 0.5) / W, (y + 0.5) / H]


def write_scene(folder, seed=11, name="base.png"):
    rgb, rings, others = scene(seed)
    Image.fromarray(rgb).save(folder / name)
    return rgb, rings, others


def plan_for(rings, **changes):
    """種 4 個（手で読んだ位置。輪の中心から 1 画素ずれたものを含む）の計画。ほかは既定値。"""
    seeds = [norm(x + dx, y + dy) for (x, y), (dx, dy) in zip(rings, HAND)]
    return {"schema_version": "fx-points/0.1", "image": "base.png", "seeds": seeds, "patch_px": PATCH, **changes}


def write_plan(folder, plan, name="points_plan.json"):
    (folder / name).write_text(json.dumps(plan), encoding="utf-8")
    return folder / name


def pixels(result):
    """結果の点（正規化座標）→ 画素の中心 [(x, y)]。"""
    return [(p["u"] * W - 0.5, p["v"] * H - 0.5) for p in result["points"]]


def matched(found, truth, tolerance=2.0):
    """検出を、tolerance 画素以内のいちばん近い正解に 1 対 1 で当てる。当たった正解の番号の集合。"""
    used = set()
    for x, y in found:
        near = sorted((math.hypot(x - tx, y - ty), index) for index, (tx, ty) in enumerate(truth) if index not in used)
        if near and near[0][0] <= tolerance:
            used.add(near[0][1])
    return used


def find(folder, plan, out="out"):
    result = points.find_points(write_plan(folder, plan), folder / out)
    return result, read_json(result["points"])


# ---- 拾う ------------------------------------------------------------------------

def test_four_hand_read_seeds_find_the_rings_and_leave_the_other_shapes(tmp_path):
    _, rings, others = write_scene(tmp_path)
    summary, result = find(tmp_path, plan_for(rings))
    found = pixels(result)
    hits = matched(found, rings)
    assert len(hits) / len(rings) >= 0.95 and len(hits) / len(found) >= 0.95                 # 再現率・適合率（許容 2 画素）
    assert not matched(found, others, tolerance=7.0)                                          # 形の違う点は 1 つも拾わない
    assert result["schema_version"] == "fx-points-result/0.1" and result["count"] == len(result["points"]) == summary["count"]
    assert result["threshold"] == points.DEFAULT_THRESHOLD and result["min_distance_px"] == (PATCH + 1) // 2
    assert result["template"] == {"size": [PATCH, PATCH], "patches": result["rounds"][-1]["patches"]}
    assert result["image"] == {"path": "base.png", "sha256": sha256(tmp_path / "base.png"), "size": [W, H]}
    assert result["plan"]["sha256"] == sha256(tmp_path / "points_plan.json") and result["plan"]["normalized"]["refine"] == 1
    assert all(result["threshold"] <= p["score"] <= 1.0 for p in result["points"])
    assert [(p["v"], p["u"]) for p in result["points"]] == sorted((p["v"], p["u"]) for p in result["points"])   # 上から・左から
    assert len(result["seeds"]) == 4 and result["warnings"] == [] and result["engine"]["opencv"] == cv2.__version__
    assert "pass" not in json.dumps(result) and "accept" not in json.dumps(result)            # 合否は出さない


@pytest.mark.parametrize("seed", [11, 12, 13])
def test_the_default_threshold_sits_between_the_rings_and_the_other_shapes(seed):
    # 既定のしきい値（仮の値）は、この合成の場面の「輪の点数」と「形の違う点・背景の点数」の間に置いてある
    rgb, rings, others = scene(seed)
    gray8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    seeds = [(x + dx, y + dy) for (x, y), (dx, dy) in zip(rings, HAND)]
    found = points.detect(gray8, seeds, PATCH, points.DEFAULT_THRESHOLD, 8, 1, np.ones((H, W), bool))
    half = PATCH // 2
    score = points.correlate(gray8.astype(np.float32), found["template"], points.flat_windows(gray8, PATCH))
    ring_scores = [score[y - half - 1:y - half + 2, x - half - 1:x - half + 2].max() for x, y in rings]
    apart = np.ones(score.shape, bool)
    for x, y in rings:
        apart[max(0, y - half - 5):y - half + 6, max(0, x - half - 5):x - half + 6] = False       # 輪の周りを除いた残り（点と背景）
    assert score[apart].max() < 0.66 < points.DEFAULT_THRESHOLD < 0.86 < min(ring_scores)        # docs/fx.md の「しきい値」に書いた数
    assert len(found["points"]) == 60 and len(matched([(x, y) for x, y, _ in found["points"]], rings)) == 60


def test_the_same_input_gives_the_same_bytes(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    plan = write_plan(tmp_path, plan_for(rings, exclude=[{"box": [0.0, 0.0, 0.2, 0.25]}]))
    first, second = points.find_points(plan, tmp_path / "a"), points.find_points(plan, tmp_path / "b")
    for name in ("points.json", "points_preview.jpg", "template.png"):
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes(), name
    text = (tmp_path / "a" / "points.json").read_text(encoding="utf-8")
    assert str(tmp_path) not in text and "created_at" not in text                             # 時刻と絶対パスを入れない
    assert {k: v for k, v in first.items() if k not in ("points", "preview", "template")} == \
           {k: v for k, v in second.items() if k not in ("points", "preview", "template")}


def test_three_seeds_are_enough_and_exact_seeds_score_high(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    plan = plan_for(rings, seeds=[norm(x, y) for x, y in rings[:3]])
    _, result = find(tmp_path, plan)
    assert len(matched(pixels(result), rings)) == 60 and result["count"] == 60
    assert all(seed["score"] > 0.85 for seed in result["seeds"])                              # 種の位置そのものの点数
    assert [[s["u"], s["v"]] for s in result["seeds"]] == [[round(u, 6), round(v, 6)] for u, v in plan["seeds"]]


def test_a_16_bit_or_transparent_image_is_read_like_the_guides_read_it(tmp_path):
    rgb, rings, _ = write_scene(tmp_path)
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    Image.fromarray(gray).save(tmp_path / "gray8.png")
    Image.fromarray(gray.astype(np.uint16) * 257).save(tmp_path / "gray16.png")
    Image.fromarray(np.dstack([gray, gray, gray, np.full_like(gray, 90)])).save(tmp_path / "rgba.png")      # アルファは見ない
    results = [find(tmp_path, plan_for(rings, image=name), out=f"out-{name}")[1]["points"] for name in ("gray8.png", "gray16.png", "rgba.png")]
    assert results[0] == results[1] == results[2] and len(results[0]) == 60


# ---- 範囲（include・exclude） ---------------------------------------------------------

def ring_radius(x, y, center, ry_ratio):
    """輪の中心 (x, y) の、楕円の中心からの距離（docs/fx.md の式を、道具を使わずに書いたもの）。"""
    return math.hypot((x + 0.5) - center[0] * W, ((y + 0.5) - center[1] * H) / ry_ratio) / W


ELLIPSE = {"center": [0.5, 0.5], "rx": 0.38, "ry_ratio": 0.55, "r_min": 0.16}
SLANT = [[0.0, 1.0], [0.0, 0.42], [0.55, 1.0]]                       # 左下の三角


def inside_slant(x, y):
    u, v = (x + 0.5) / W, (y + 0.5) / H
    return v >= 0.42 + (1.0 - 0.42) * u / 0.55


@pytest.mark.parametrize("ranges, expected", [
    ({"include": [{"box": [0.2, 0.25, 0.7, 0.75]}]}, lambda x, y: 0.2 * W <= x + 0.5 <= 0.7 * W and 0.25 * H <= y + 0.5 <= 0.75 * H),
    ({"exclude": [{"polygon": SLANT}]}, lambda x, y: not inside_slant(x, y)),
    ({"include": [{"ellipse": ELLIPSE}]}, lambda x, y: 0.16 <= ring_radius(x, y, [0.5, 0.5], 0.55) <= 0.38),
    ({"include": [{"box": [0.0, 0.0, 0.5, 1.0]}, {"box": [0.5, 0.0, 1.0, 0.5]}], "exclude": [{"ellipse": dict(ELLIPSE, r_min=0.0)}]},
     lambda x, y: (x + 0.5 <= 0.5 * W or y + 0.5 <= 0.5 * H) and ring_radius(x, y, [0.5, 0.5], 0.55) > 0.38),
])
def test_include_and_exclude_keep_the_rings_whose_centers_are_in_range(tmp_path, ranges, expected):
    _, rings, _ = write_scene(tmp_path)
    wanted = {index for index, (x, y) in enumerate(rings) if expected(x, y)}
    for index, (x, y) in enumerate(rings):                                                    # 境目から 2 画素以内の輪は無い（判定が揺れない）
        assert all(expected(x + dx, y + dy) == (index in wanted) for dx in (-2, 0, 2) for dy in (-2, 0, 2)), (x, y)
    _, result = find(tmp_path, plan_for(rings, **ranges))
    assert 0 < len(wanted) < 60 and matched(pixels(result), rings) == wanted and result["count"] == len(wanted)


def test_regions_are_decided_at_pixel_centers():
    box = points.region({"box": [0.25, 0.5, 0.5, 1.0]}, (8, 4))                               # 幅 8・高さ 4。画素の中心は (x + 0.5) / 8
    inside = [False, False, True, True, False, False, False, False]
    assert box.tolist() == [[False] * 8, [False] * 8, inside, inside]
    ring = points.region({"ellipse": {"center": [0.5, 0.5], "rx": 0.4, "ry_ratio": 0.5, "r_min": 0.2}}, (100, 100))
    assert ring[50, 85] and not ring[50, 95] and not ring[50, 60]                             # 横: 0.2〜0.4 の幅の所だけ
    assert ring[65, 50] and not ring[75, 50] and not ring[55, 50]                             # 縦は画素で半分につぶれる
    disc = points.region({"ellipse": {"center": [0.5, 0.5], "rx": 0.4, "ry_ratio": 1.0, "r_min": 0.0}}, (100, 50))
    assert disc[25, 50] and disc[25, 88] and not disc[25, 92] and disc[49, 50]                # 半径は画像の幅で数える（幅 100 で 40 画素）
    polygon = [[0.1, 0.1], [0.9, 0.2], [0.5, 0.9]]
    assert np.array_equal(points.region({"polygon": polygon}, (60, 40)), masks.polygon((40, 60), polygon, "norm") > 0)
    radius = points.ellipse_radius([0.5, 0.9, 0.5], [0.5, 0.5, 1.0], [0.5, 0.5], 0.5, (100, 100))
    assert radius.tolist() == pytest.approx([0.0, 0.4, 1.0])


def test_include_and_exclude_combine_as_any_include_and_no_exclude():
    plan = {"include": [{"box": [0.0, 0.0, 0.5, 1.0]}, {"box": [0.5, 0.0, 1.0, 0.5]}], "exclude": [{"box": [0.25, 0.0, 0.75, 1.0]}]}
    area = points.allowed_area(plan, (4, 2))
    assert area.tolist() == [[True, False, False, True], [True, False, False, False]]
    assert points.allowed_area({"include": [], "exclude": []}, (3, 2)).all()


# ---- 手順の部品 --------------------------------------------------------------------

def test_peaks_keep_the_higher_of_two_close_maxima_and_only_allowed_ones():
    score = np.zeros((20, 30), np.float32)
    score[5, 5], score[5, 10], score[12, 20], score[15, 3], score[2, 25] = 0.9, 0.8, 0.7, 0.4, 0.8
    everywhere = np.ones(score.shape, bool)
    assert points.peaks(score, 0.5, 5, everywhere) == [(5, 5, pytest.approx(0.9)), (25, 2, pytest.approx(0.8)), (10, 5, pytest.approx(0.8)),
                                                       (20, 12, pytest.approx(0.7))]            # 点数の高い順。同点は上から。5 画素ちょうどは残す
    assert [p[:2] for p in points.peaks(score, 0.5, 6, everywhere)] == [(5, 5), (25, 2), (20, 12)]   # 6 画素より近い低い方は捨てる
    allowed = everywhere.copy()
    allowed[5, 5] = False
    assert [p[:2] for p in points.peaks(score, 0.5, 6, allowed)] == [(25, 2), (10, 5), (20, 12)]     # 除いた点は隣を消さない
    assert points.peaks(score, 0.95, 5, everywhere) == []
    plateau = np.zeros((9, 9), np.float32)
    plateau[4, 3:6] = 0.8
    assert [p[:2] for p in points.peaks(plateau, 0.5, 3, np.ones(plateau.shape, bool))] == [(3, 4)]   # 同じ高さの続きは 1 つ


def test_too_many_maxima_stop_with_advice(monkeypatch):
    monkeypatch.setattr(points, "MAX_CANDIDATES", 3)
    score = np.zeros((9, 9), np.float32)
    score[::4, ::4] = 0.9
    with pytest.raises(PipelineError, match="threshold を上げて"):
        points.peaks(score, 0.5, 2, np.ones(score.shape, bool))


def test_flat_windows_score_zero_and_are_never_picked(tmp_path):
    rgb, rings, _ = write_scene(tmp_path)
    rgb[:, :200] = 128                                                                        # 左側を同じ値で塗りつぶす
    Image.fromarray(rgb).save(tmp_path / "base.png")
    right = [ring for ring in rings if ring[0] >= 230]
    gray8 = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    flat = points.flat_windows(gray8, PATCH)
    half = PATCH // 2
    assert flat.shape == (H - PATCH + 1, W - PATCH + 1) and flat[:, :200 - PATCH + 1].all() and not flat[:, 200 - half:].any()
    template = points.template_of(points.patches(gray8.astype(np.float32), right[:4], half))
    score = points.correlate(gray8.astype(np.float32), template, flat)
    assert np.all(score[flat] == 0.0) and np.isfinite(score).all() and -1.0 <= score.min() and score.max() <= 1.0
    _, result = find(tmp_path, plan_for(right, threshold=0.3, refine=0))
    assert all(x > 200 - PATCH for x, _ in pixels(result)) and len(matched(pixels(result), right)) == len(right)
    flat_seed = plan_for(right)
    flat_seed["seeds"][0] = norm(100, 100)
    with pytest.raises(PipelineError, match="種 0 の周り 15 画素が平ら"):
        points.find_points(write_plan(tmp_path, flat_seed), tmp_path / "flat")
    assert not (tmp_path / "flat").exists()


def test_refine_rebuilds_the_template_from_the_best_half_of_the_detections(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    _, plain = find(tmp_path, plan_for(rings, refine=0), out="r0")
    _, twice = find(tmp_path, plan_for(rings, refine=2), out="r2")
    assert plain["rounds"] == [{"template": "seeds", "patches": 4, "count": 60}] and plain["template"]["patches"] == 4
    assert [r["template"] for r in twice["rounds"]] == ["seeds", "detections", "detections"]
    assert [r["patches"] for r in twice["rounds"][1:]] == [math.ceil(r["count"] * points.REFINE_SHARE) for r in twice["rounds"][:-1]]
    assert len(matched(pixels(twice), rings)) == 60
    # 手で読んだ種（1 画素ずれ）の型紙より、検出から作り直した型紙のほうが、輪の点数の下限が上がる
    assert min(p["score"] for p in twice["points"]) > min(p["score"] for p in plain["points"])
    assert not np.array_equal(np.asarray(Image.open(tmp_path / "r0" / "template.png")), np.asarray(Image.open(tmp_path / "r2" / "template.png")))


def test_refining_from_mostly_false_detections_spoils_the_template(tmp_path):
    # 限界（docs/fx.md）: しきい値が低すぎて誤検出が検出の半分を超えると、作り直した型紙が誤検出に寄る
    _, rings, _ = write_scene(tmp_path)

    def ring_floor(result):
        return min(p["score"] for p, at in zip(result["points"], pixels(result)) if matched([at], rings))

    _, plain = find(tmp_path, plan_for(rings, threshold=0.4, refine=0), out="r0")
    _, refined = find(tmp_path, plan_for(rings, threshold=0.4, refine=1), out="r1")
    assert plain["count"] > 4 * 60 and refined["rounds"][1]["patches"] == math.ceil(plain["count"] / 2)   # 型紙の材料の大半が輪ではない
    assert ring_floor(plain) > 0.86 and ring_floor(refined) < 0.7 and refined["count"] > plain["count"]   # 輪の点数が下がり、誤検出が増える


def test_defaults_are_filled_in(tmp_path):
    raw = {"schema_version": "fx-points/0.1", "image": "base.png", "seeds": [[0.2, 0.2], [0.4, 0.4], [0.6, 0.6]], "patch_px": 21,
           "include": [{"ellipse": {"center": [0.5, 0.5], "rx": 0.3}}]}
    plan = points.parse_plan(raw, tmp_path)
    assert plan["threshold"] == points.DEFAULT_THRESHOLD == 0.75 and plan["min_distance_px"] == 11 and plan["refine"] == 1
    assert plan["include"] == [{"ellipse": {"center": [0.5, 0.5], "rx": 0.3, "ry_ratio": 1.0, "r_min": 0.0}}] and plan["exclude"] == []
    assert points.schema()["properties"]["threshold"]["default"] == points.DEFAULT_THRESHOLD        # スキーマに書いた既定と同じ
    assert points.schema()["properties"]["refine"]["default"] == points.DEFAULT_REFINE


def test_no_detection_is_a_result_with_notes_not_a_failure(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    plan = write_plan(tmp_path, plan_for(rings, threshold=0.999))
    code, summary, err = run_cli("fx", "points", plan, "--out", tmp_path / "out")
    assert code == 0, err                                                                     # 合否を出さない: 0 個でも終了コードは 0
    assert summary["count"] == 0 and summary["scores"] is None and len(summary["rounds"]) == 1
    assert any("refine" in w for w in summary["warnings"]) and any("検出がありません" in w for w in summary["warnings"])
    result = read_json(summary["points"])
    assert result["points"] == [] and result["warnings"] == summary["warnings"] and (tmp_path / "out" / "points_preview.jpg").is_file()


def test_a_seed_without_a_detection_nearby_is_noted(tmp_path):
    _, rings, others = write_scene(tmp_path)
    plan = plan_for(rings)
    plan["seeds"].append(norm(*others[0]))                                                    # 5 個目の種は形の違う点
    _, result = find(tmp_path, plan)
    assert len(matched(pixels(result), rings)) == 60
    assert [w for w in result["warnings"] if w.startswith("種 4 の近く（8 画素以内）に検出がありません")] and len(result["warnings"]) == 1


# ---- 書き出しと検証 ---------------------------------------------------------------

def test_the_points_command_writes_the_table_the_preview_and_the_template(tmp_path):
    rgb, rings, _ = write_scene(tmp_path)
    plan = write_plan(tmp_path, plan_for(rings, include=[{"ellipse": ELLIPSE}], exclude=[{"box": [0.4, 0.0, 0.6, 0.3]}]))
    code, summary, err = run_cli("fx", "points", plan)                                        # --out を省くと計画のフォルダー
    assert code == 0, err
    assert set(summary) == {"points", "preview", "template", "count", "threshold", "scores", "size", "rounds", "warnings"}
    assert summary["size"] == [W, H] and summary["count"] > 0 and summary["scores"]["min"] >= summary["threshold"] == 0.75
    assert (tmp_path / "points.json").is_file() and summary["points"] == str(tmp_path / "points.json")
    assert (tmp_path / "points_preview.jpg").read_bytes()[:2] == b"\xff\xd8"
    assert (tmp_path / "template.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    with Image.open(tmp_path / "template.png") as image:
        template = np.asarray(image)
    assert template.shape == (PATCH, PATCH) and template.min() == 0 and template.max() == 255
    with Image.open(tmp_path / "points_preview.jpg") as image:
        shown = np.asarray(image.convert("RGB")).astype(int)
    assert shown.shape == (H, W, 3)
    drawn = np.abs(shown - rgb.astype(int)).mean(-1)                                          # 元の絵との差（JPEG の誤差と、描いた印）
    radius = PATCH // 2 + 1
    for x, y in ((int(round(a)), int(round(b))) for a, b in pixels(read_json(tmp_path / "points.json"))):
        assert max(drawn[y, x + radius], drawn[y, x - radius], drawn[y + radius, x], drawn[y - radius, x]) > 40, (x, y)   # 検出の丸
    assert np.median(drawn) < 6                                                               # 印の無い所は元の絵


def test_the_preview_draws_circles_seed_crosses_and_range_outlines():
    rgb = np.zeros((80, 120, 3), np.uint8)
    plan = {"patch_px": 9, "include": [{"box": [0.5, 0.5, 0.9, 0.9]}], "exclude": [{"ellipse": {"center": [0.25, 0.25], "rx": 0.1, "ry_ratio": 1.0, "r_min": 0.0}}]}
    shown = points.preview(rgb, plan, [(30, 60, 0.9)], [(100, 20)])
    assert rgb.max() == 0                                                                     # 渡した絵は変えない
    assert shown[60, 35, 0] == 0 and shown[60, 35, 1] == shown[60, 35, 2] > 150 and shown[60, 30].max() == 0   # 丸（半径 5。水色）。中は塗らない
    assert shown[50:58, 36:46].any()                                                          # 番号（丸の右上）
    assert tuple(shown[20, 100]) == points.SEED_COLOR and tuple(shown[20, 104]) == points.SEED_COLOR
    assert tuple(shown[40, 80]) == points.INCLUDE_COLOR                                       # box の左上の角 (60, 40) からの上の辺
    assert shown[20, 42, 0] > 150 and shown[20, 42, 1] < 120                                  # 楕円の線（赤）
    assert points.preview(rgb, dict(plan, include=[], exclude=[]), [], []).max() == 0


@pytest.mark.parametrize("mutate, message", [
    (lambda p: p.__setitem__("schema_version", "fx-points/9"), "schema_version"),
    (lambda p: p.__setitem__("seeds", p["seeds"][:2]), "正しくありません"),                           # 種が 2 個
    (lambda p: p.__setitem__("patch_px", 14), "奇数"),
    (lambda p: p["seeds"].__setitem__(0, [1.2, 0.5]), "正しくありません"),                          # 画像の外の種
    (lambda p: p["seeds"].__setitem__(0, [0.01, 0.5]), "種 0 .*はみ出します"),                     # 切り抜きが画像からはみ出す種
    (lambda p: p["seeds"].__setitem__(1, p["seeds"][0]), "同じ画素を指す種"),
    (lambda p: p.__setitem__("patch_px", 321), "画像（480x320）より大きい"),
    (lambda p: p.__setitem__("threshold", 0), "正しくありません"),
    (lambda p: p.__setitem__("threshold", 1.5), "正しくありません"),
    (lambda p: p.__setitem__("min_distance_px", 0), "正しくありません"),
    (lambda p: p.__setitem__("refine", -1), "正しくありません"),
    (lambda p: p.__setitem__("sparkle", 1), "正しくありません"),
    (lambda p: p.__setitem__("include", [{"box": [0.6, 0.1, 0.4, 0.9]}]), r"include\[0\] の box"),  # 逆の範囲
    (lambda p: p.__setitem__("exclude", [{"box": [0.1, 0.1, 1.4, 0.9]}]), "正しくありません"),        # 画像の外の範囲
    (lambda p: p.__setitem__("include", [{"polygon": [[0, 0], [1, 1]]}]), "正しくありません"),
    (lambda p: p.__setitem__("include", [{"circle": [0.5, 0.5, 0.1]}]), "正しくありません"),
    (lambda p: p.__setitem__("include", [{"polygon": [[0, 0], [1e12, 0], [0, 1]]}]), "正しくありません"),   # 桁外れの頂点
    (lambda p: p.__setitem__("include", [{"ellipse": {"center": [0.5, 0.5], "rx": 1e9}}]), "正しくありません"),
    (lambda p: p.__setitem__("include", [{"box": [0, 0, 1, 1], "polygon": [[0, 0], [1, 0], [1, 1]]}]), "正しくありません"),
    (lambda p: p.__setitem__("exclude", [{"ellipse": {"center": [0.5, 0.5], "rx": 0.2, "r_min": 0.2}}]), r"exclude\[0\] の ellipse の r_min"),
    (lambda p: p.__setitem__("include", [{"box": [0.0, 0.0, 0.01, 0.01]}]), "探す範囲が残りません"),
    (lambda p: p.__setitem__("image", "../base.png"), "base"),
    (lambda p: p.__setitem__("image", "nothing.png"), "素材が見つかりません"),
])
def test_bad_plans_are_refused_before_anything_is_written(tmp_path, mutate, message):
    _, rings, _ = write_scene(tmp_path)
    plan = plan_for(rings)
    mutate(plan)
    with pytest.raises(PipelineError, match=message):
        points.find_points(write_plan(tmp_path, plan), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_a_plan_that_is_missing_or_not_json_explains_itself(tmp_path):
    with pytest.raises(PipelineError, match="点の計画が見つかりません"):
        points.find_points(tmp_path / "nothing.json")
    (tmp_path / "broken.json").write_text("{", encoding="utf-8")
    with pytest.raises(PipelineError, match="JSON として読めません"):
        points.find_points(tmp_path / "broken.json")


def test_keys_starting_with_x_are_allowed_and_dropped(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    plan = plan_for(rings, x_note="広場の皿", include=[{"box": [0.0, 0.0, 1.0, 1.0], "x_why": "全体"}])
    _, result = find(tmp_path, plan)
    assert result["count"] == 60 and "x_note" not in result["plan"]["normalized"]
    assert result["plan"]["normalized"]["include"] == [{"box": [0.0, 0.0, 1.0, 1.0]}]


@pytest.mark.parametrize("plan_name, image_name, message", [
    ("points.json", "base.png", "points.json を書くと、入力（計画）"),
    ("points_plan.json", "template.png", "template.png を書くと、入力（画像）"),
    ("points_plan.json", "points_preview.jpg", "points_preview.jpg を書くと、入力（画像）"),
    ("template.png.tmp", "base.png", "template.png.tmp を書くと、入力（計画）"),                    # 書き出しの一時ファイルの名前
])
def test_no_output_is_written_over_an_input(tmp_path, plan_name, image_name, message):
    rgb, rings, _ = scene()
    Image.fromarray(rgb).save(tmp_path / image_name, **({"quality": 95} if image_name.endswith(".jpg") else {}))
    plan = write_plan(tmp_path, plan_for(rings, image=image_name), plan_name)
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    with pytest.raises(PipelineError, match=message):
        points.find_points(plan)                                                              # 計画のフォルダーに書く
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir()} == before                     # 入力を含め、何も書いていない
    assert points.find_points(plan, tmp_path / "elsewhere")["count"] > 0                      # 別のフォルダーになら書ける


def test_missing_opencv_stops_like_the_other_fx_commands(tmp_path, monkeypatch):
    _, rings, _ = write_scene(tmp_path)
    plan = write_plan(tmp_path, plan_for(rings))
    monkeypatch.setitem(sys.modules, "cv2", None)
    code, _, err = run_cli("fx", "points", plan, "--out", tmp_path / "out")
    assert code == 1 and "--extra fx" in err and "opencv" in err and not (tmp_path / "out").exists()


# ---- hooks から使う ----------------------------------------------------------------

def view_of(tmp_path, zoom=1.0, center=(0.5, 0.5)):
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": W, "height": H, "fps": 12}, "duration_s": 1.0, "assets": {"base": "base.png"},
           "camera": {"keys": [{"t": 0, "zoom": zoom, "center": list(center)}]}}
    path = camera.CameraPath(fxspec.parse_spec(raw, tmp_path).data["camera"], 1.0, (W, H), (0.0, 0.0, 1.0, 1.0))
    return View(path, (0.0, 0.0, 1.0, 1.0), None, (W, H), 0.0)


def test_the_points_go_to_the_screen_through_the_view(tmp_path):
    _, rings, _ = write_scene(tmp_path)
    _, result = find(tmp_path, plan_for(rings))
    uv = [(p["u"], p["v"]) for p in result["points"]]
    view = view_of(tmp_path)                                                                  # 画像と同じ大きさ・寄りなし: 画面の画素 = 画像の画素
    screen = view.points(uv)
    assert screen.shape == (60, 2) and screen.dtype == np.float32
    assert matched([tuple(p) for p in screen.tolist()], rings) == set(range(60))
    assert np.abs(screen - np.round(screen)).max() < 0.01                                     # 検出は画素の中心（整数）に戻る
    close = view_of(tmp_path, zoom=2.0, center=(0.4, 0.6))                                    # 寄ったカメラでも、絵に付いて動く
    for (u, v), (x, y) in zip(uv[:5], close.points(uv)[:5]):
        assert (x, y) == pytest.approx(close.base(W, H).pt(u * W, v * H), abs=1e-3)
        assert x - close.point(0.4, 0.6)[0] == pytest.approx((u - 0.4) * close.scale, abs=1e-3)


def test_lamps_built_from_the_table_light_up_from_the_inside_of_the_ring(tmp_path):
    # docs/fx.md の hooks の例と同じ作り方: 点の表から灯を作り、点く時刻を輪の中心からの距離で決める
    rgb, rings, _ = write_scene(tmp_path)
    _, table = find(tmp_path, plan_for(rings, include=[{"ellipse": ELLIPSE}]))
    size = table["image"]["size"]
    lamps = []
    for p in table["points"]:
        r = float(points.ellipse_radius(p["u"], p["v"], ELLIPSE["center"], ELLIPSE["ry_ratio"], size))
        lamps.append({"x": p["u"] * size[0], "y": p["v"] * size[1], "r": 5, "flick": 0.0,
                      "on": 1.0 + 3.0 * (r - ELLIPSE["r_min"]) / (ELLIPSE["rx"] - ELLIPSE["r_min"])})
    assert len(lamps) == table["count"] > 5 and all(1.0 <= lamp["on"] <= 4.0 for lamp in lamps)
    view = view_of(tmp_path)
    base = rgb.astype(np.float32)
    early, late = (sky.lamps(base.copy(), view.base(*size), t, {"lamps": lamps}) for t in (2.5, 6.0))
    gains = {"early": [], "late": [], "on": []}
    for lamp, (x, y) in zip(lamps, pixels(table)):
        x, y = int(round(x)), int(round(y))
        gains["early"].append(float((early[y, x] - base[y, x]).mean()))
        gains["late"].append(float((late[y, x] - base[y, x]).mean()))
        gains["on"].append(lamp["on"])
    assert min(gains["late"]) > 20                                                            # 最後は全部の位置が明るい
    inner = [g for g, on in zip(gains["early"], gains["on"]) if on < 2.0]
    outer = [g for g, on in zip(gains["early"], gains["on"]) if on > 3.2]
    assert inner and outer and min(inner) > 20 and max(outer) < 8                             # 途中では内側だけが点いている
