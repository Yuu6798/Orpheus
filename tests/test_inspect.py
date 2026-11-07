"""mv inspect（生成動画の検品）のテスト。素材はすべてプログラムで描いた合成物（inspection/demo.py）。

合成素材で決めたしきい値が合成素材で働くことを確かめるだけで、実作品の動画での見落とし・誤検出の少なさは保証しない。
"""
import hashlib
import importlib.util
import json
import os
import sys
import types
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.inspection import config as inspect_config  # noqa: E402
from mv_pipeline.inspection import demo, detect, reference, run, video, vlm  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
STANDIN = REPO / "scripts" / "vlm_standin.py"
CLAUDE = REPO / "scripts" / "vlm_claude.py"
SEGMENT_SLACK = 2       # 区間で見る信号（小刻みな揺れ）の端は窓の分だけずれてよい


@pytest.fixture(scope="module")
def checked(tmp_path_factory):
    """デモ素材（全部の異常入り）を作り、1 回検品した結果。"""
    root = tmp_path_factory.mktemp("inspect")
    demo.create(root)
    result, out = run.run(root / "inspect.json")
    return root, result, out, read_json(root / "expected.json")


def _overlaps(a, b):
    return min(a[1], b[1]) > max(a[0], b[0])


def test_demo_finds_each_injected_anomaly_at_its_frames_with_its_category(checked):
    _, result, _, expected = checked
    events = result["events"]
    for anomaly in expected["anomalies"]:
        hits = [e for e in events if _overlaps(e["frames"], anomaly["frames"])]
        assert len(hits) == 1, (anomaly, [(e["frames"], e["category"]) for e in hits])
        event = hits[0]
        assert event["category"] == anomaly["category"], (anomaly, event["category"], event["signals"])
        slack = SEGMENT_SLACK if anomaly["name"] == "jitter" else 0
        assert abs(event["frames"][0] - anomaly["frames"][0]) <= slack and abs(event["frames"][1] - anomaly["frames"][1]) <= slack, \
            (anomaly, event["frames"])
    assert len(events) == len(expected["anomalies"]), [(e["frames"], e["category"], e["label"]) for e in events]
    assert result["overall"] == "review" and not result["skipped"]


def test_each_event_says_what_was_measured_and_where_in_the_frame(checked):
    _, result, _, _ = checked
    by_label = {e["label"]: e for e in result["events"]}
    glitch = by_label["数コマだけの乱れ"]
    assert {"glitch", "silhouette_pop", "costume_pop"} <= {s["check"] for s in glitch["signals"]}
    assert glitch["categories"][0] == "continuity" and "shape" in glitch["categories"]   # 背景ごと変わったので連続性が主
    fragment = next(e for e in result["events"] if e["category"] == "shape")
    assert {"fragment", "silhouette_pop"} <= {s["check"] for s in fragment["signals"]}
    x0, y0, x1, y1 = fragment["region"]
    assert x0 >= 0.6 and x1 <= 1.0 and y1 <= 0.5, fragment["region"]          # 右上の離れた塊の所
    for event in result["events"]:
        assert event["evidence"] == "measured" and event["status"] == "review"
        assert all(s["value"] is not None for s in event["signals"])


def test_costume_event_names_the_missing_sheet_color_and_the_new_color(checked):
    _, result, _, _ = checked
    costume = next(e for e in result["events"] if e["category"] == "costume")
    missing = next(s for s in costume["signals"] if s["check"] == "reference_missing")
    extra = next(s for s in costume["signals"] if s["check"] == "reference_extra")
    red, blue = (int(missing["color"][i:i + 2], 16) for i in (1, 3, 5)), (int(extra["color"][i:i + 2], 16) for i in (1, 3, 5))
    red, blue = list(red), list(blue)
    assert red[0] > 120 and red[1] < 60 and red[2] < 80, missing["color"]       # 設定画の深紅の上着
    assert blue[2] > 150 and blue[0] < 90, extra["color"]                     # 入れ替えた青
    assert missing["reference"] == "sheet.png（front）" and missing["view"] == "front" and missing["value"] < 0.05
    summary = result["clips"][0]["reference"]
    assert summary["overlap_median"] > 0.8 and summary["overlap_min_frame"] == 30   # 乱れのコマがいちばん遠い
    assert summary["best_reference"] == [{"path": "sheet.png", "view": "front", "label": "sheet.png（front）", "frames": 96}]


def _fake_features(stats):
    count = len(stats)
    return types.SimpleNamespace(ref=stats, count=count, frame=lambda i: i, present=np.ones(count, bool),
                                 bbox=np.tile([0.3, 0.1, 0.7, 0.9], (count, 1)))


def _fake_palette(view, weights, hexes):
    rgb = np.array([[[int(h[i:i + 2], 16) for i in (1, 3, 5)] for h in hexes]], np.uint8)
    return {"rel": "turn.png", "view": view, "label": f"turn.png（{view}）" if view else "turn.png", "weights": np.array(weights),
            "hex": hexes, "colors": reference.to_lab(rgb).reshape(-1, 3), "bands": [2, 1, 1]}


def _fake_stats(palette, coverage, extra):
    coverage = np.array(coverage)
    return {"coverage": coverage, "extra": extra, "extra_lab": None, "extra_box": None,
            "overlap": float(np.minimum(coverage, palette["weights"]).sum())}


def test_with_several_views_a_frame_that_one_view_explains_is_not_flagged():
    """同じ衣装の正面と背面。重なりは正面が大きいのに正面の薄い色だけが欠けるコマ（背中を向いた・ぶれた）は、背面で欠けなく説明できるので出さない。
    どの向きでも欠けるコマ・どの向きにも無い色だけを出し、比べた向きを名前で書く（実素材の確かめで、重なりで選ぶと誤検出した）。"""
    front = _fake_palette("front", [0.5, 0.4, 0.1], ["#f0d0c8", "#703e80", "#d39bcd"])
    back = _fake_palette("back", [0.6, 0.2, 0.2], ["#f0d0c8", "#4b3151", "#c58ec2"])
    stats = [[_fake_stats(front, [0.5, 0.4, 0.01], 0.30), _fake_stats(back, [0.45, 0.2, 0.06], 0.05)] for _ in range(12)]
    stats += [[_fake_stats(front, [0.5, 0.45, 0.0], 0.25), _fake_stats(back, [0.5, 0.45, 0.0], 0.30)] for _ in range(12)]
    assert reference.best(stats[0]) == 0                       # 重なりだけなら正面を選ぶ（正面の薄紫が欠ける）
    found = reference.signals(_fake_features(stats), [front, back], inspect_config.THRESHOLDS, 24.0)
    missing = [s for s in found if s["check"] == "reference_missing"]
    extra = [s for s in found if s["check"] == "reference_extra"]
    assert [s["frames"] for s in missing] == [[12, 24]] and [s["frames"] for s in extra] == [[12, 24]]
    assert missing[0]["reference"] == "turn.png（front）" and missing[0]["view"] == "front" and missing[0]["color"] == "#d39bcd"
    assert "ほかの設定画でも欠けなく説明できないコマ" in missing[0]["detail"]
    assert extra[0]["reference"] == "turn.png（front）" and "ほかの設定画にも無い色" in extra[0]["detail"]


def test_each_missing_color_keeps_its_own_frames_and_value():
    """別の色が続けて欠けても、同時に欠けても、色ごとの区間と、その区間だけで測った値を残す（1 本にまとめて値を薄めない）。"""
    sheet = _fake_palette(None, [0.4, 0.35, 0.25], ["#f0d0c8", "#703e80", "#2050c0"])
    stats = []
    for i in range(48):
        coverage = [0.4, 0.35, 0.25]
        for c in ([i // 12] if i < 36 else [0, 1]):          # コマ 0〜35 は 12 コマずつ順に 1 色、36〜47 は 2 色が同時に欠ける
            coverage[c] = 0.0
        stats.append([_fake_stats(sheet, coverage, 0.0)])
    missing = reference.signals(_fake_features(stats), [sheet], inspect_config.THRESHOLDS, 24.0)
    found = sorted((s["frames"][0], s["frames"][1], s["color"]) for s in missing)
    assert found == [(0, 12, "#f0d0c8"), (12, 24, "#703e80"), (24, 36, "#2050c0"), (36, 48, "#703e80"), (36, 48, "#f0d0c8")], found
    assert all(s["value"] == 0.0 for s in missing)
    assert all("ほかの設定画" not in s["detail"] and s["reference"] == "turn.png" for s in missing)     # 設定画 1 枚なら書かない


def test_the_same_color_missing_while_the_compared_view_switches_is_one_interval():
    """向き違いの同じ色（正面と背面の薄紫）は 1 つの色として数える。比べる向きが 3 コマごとに入れ替わっても区間は切れない。"""
    front = _fake_palette("front", [0.5, 0.3, 0.2], ["#f0d0c8", "#703e80", "#d39bcd"])
    back = _fake_palette("back", [0.5, 0.3, 0.2], ["#f0d0c8", "#4b3151", "#c58ec2"])
    stats = []
    for i in range(12):
        near = 0.3 if (i // 3) % 2 == 0 else 0.2           # 重なりの大きい向きが 3 コマごとに入れ替わる
        stats.append([_fake_stats(front, [0.5, near, 0.0], 0.0), _fake_stats(back, [0.5, 0.5 - near, 0.0], 0.0)])
    assert [reference.explaining(item, [front, back], inspect_config.THRESHOLDS)[0] for item in stats[:6]] == [0, 0, 0, 1, 1, 1]
    missing = reference.signals(_fake_features(stats), [front, back], inspect_config.THRESHOLDS, 24.0)
    assert [(s["frames"], s["color"], s["view"]) for s in missing] == [([0, 12], "#d39bcd", "front")]


def test_two_colors_of_one_sheet_never_share_a_group_through_another_sheet():
    """別の設定画の中間の色（L 50）が同じ設定画の 2 色（L 20 と 80）の両方に近くても、その 2 色は別の組のまま。
    2 色が順に欠けたら、色ごとの区間と値 0.0 を残す（Codex の指摘の例）。"""
    front = {"rel": "a.png", "view": "front", "label": "a.png（front）", "weights": np.array([0.5, 0.5]), "hex": ["#333333", "#cccccc"],
             "colors": np.array([[20, 0, 0], [80, 0, 0]], np.float32), "bands": [1, 1]}
    back = {"rel": "a.png", "view": "back", "label": "a.png（back）", "weights": np.array([1.0]), "hex": ["#777777"],
            "colors": np.array([[50, 0, 0]], np.float32), "bands": [1]}
    groups = reference.color_groups([front, back], inspect_config.THRESHOLDS)
    assert sorted(groups) == [[(0, 0), (1, 0)], [(0, 1)]]                    # 近い順（同じ差なら番号順）に、1 枚から 1 色ずつ
    stats = [[_fake_stats(front, [0.0, 0.5] if i < 12 else [0.5, 0.0], 0.0), _fake_stats(back, [0.0], 0.0)] for i in range(24)]
    missing = reference.signals(_fake_features(stats), [front, back], inspect_config.THRESHOLDS, 24.0)
    assert sorted((s["frames"][0], s["frames"][1], s["color"], s["value"]) for s in missing) == [(0, 12, "#333333", 0.0), (12, 24, "#cccccc", 0.0)]


def test_a_missing_color_is_measured_only_on_the_frames_where_it_is_missing():
    """区間が欠けの無いコマをはさんでつながり、比べる向きも入れ替わるとき、値は書く色が欠けたコマだけで測る（Codex の指摘の例）。"""
    front = _fake_palette("front", [0.4, 0.4, 0.2], ["#f0d0c8", "#703e80", "#d39bcd"])
    back = _fake_palette("back", [0.4, 0.4, 0.2], ["#f0d0c8", "#4b3151", "#c58ec2"])
    kinds = {"front_missing": ([0.4, 0.4, 0.0], [0.4, 0.3, 0.0]), "back_missing": ([0.4, 0.3, 0.0], [0.4, 0.4, 0.0]),
             "healthy": ([0.4, 0.4, 0.2], [0.4, 0.4, 0.15])}
    order = ["front_missing", "healthy", "back_missing", "healthy"] * 3
    stats = [[_fake_stats(front, kinds[k][0], 0.0), _fake_stats(back, kinds[k][1], 0.0)] for k in order]
    missing = reference.signals(_fake_features(stats), [front, back], inspect_config.THRESHOLDS, 24.0)
    assert [(s["frames"], s["color"], s["value"]) for s in missing] == [([0, 11], "#d39bcd", 0.0)]
    assert missing[0]["peak"] in (0, 4, 8)


def test_an_extra_color_is_measured_only_on_the_frames_where_it_shows():
    """設定画に無い色の区間が、しきい値より下のコマをはさんでつながり、比べる向きも入れ替わるとき、値・山・色は
    しきい値を超えたコマだけで測る（Codex の指摘の例。前は値 0.0・しきい値 0.12 と書いた）。"""
    front = _fake_palette("front", [0.4, 0.4, 0.2], ["#f0d0c8", "#703e80", "#d39bcd"])
    back = _fake_palette("back", [0.4, 0.4, 0.2], ["#f0d0c8", "#4b3151", "#c58ec2"])
    blue = np.array([50.0, 0.0, -60.0], np.float32)

    def frame(palette, extra):
        shown = extra >= inspect_config.THRESHOLDS["ref_extra_min"]
        return {**_fake_stats(palette, palette["weights"], extra), "extra_lab": blue if shown else None,
                "extra_box": [0.1, 0.1, 0.2, 0.2] if shown else None}

    stats = [[frame(front, f), frame(back, b)] for f, b in [(0.4, 0.5), (0.0, 0.1), (0.5, 0.4), (0.0, 0.1)] * 3]
    found = reference.signals(_fake_features(stats), [front, back], inspect_config.THRESHOLDS, 24.0)
    assert [(s["check"], s["frames"], s["reference"], s["value"], s["peak"]) for s in found] == \
        [("reference_extra", [0, 11], "turn.png（front）", 0.4, 0)]


def test_the_markdown_names_the_other_colors_of_the_same_check(checked):
    import copy

    from mv_pipeline.inspection import report

    _, result, out, _ = checked
    result = copy.deepcopy(result)
    costume = next(e for e in result["events"] if e["category"] == "costume")
    primary = next(s for s in costume["signals"] if s["detail"] == costume["summary"])
    costume["signals"].append({**primary, "color": "#123456", "detail": "同じ検査で、別の色 #123456 の信号"})
    text = report.markdown(result, read_json(out / "fix_plan.json"))
    assert "同じ検査のほかの色: #123456" in text
    missing = [s for s in costume["signals"] if s["check"] == "reference_missing"]
    assert missing and all(s["detail"] != costume["summary"] for s in missing)
    line = next(line for line in text.splitlines() if line.startswith(f"| {costume['id']} |"))
    assert all(s["color"] in line for s in missing) and "sheet.png（front） と照合" in line      # 主でない設定画の信号も、色と設定画を表に出す
    assert "ほかの信号: " not in line or "設定画の色の欠け" not in line.split("ほかの信号: ")[1]


def test_the_same_sheet_used_twice_without_a_view_gets_numbered_labels_and_a_cutout(tmp_path):
    from PIL import Image

    image = np.zeros((40, 40, 4), np.uint8)
    image[10:30, 10:30] = (200, 30, 30, 255)
    Image.fromarray(image, "RGBA").save(tmp_path / "s.png")
    entries = [{"path": tmp_path / "s.png", "rel": "s.png"}, {"path": tmp_path / "s.png", "rel": "s.png", "view": "back"},
               {"path": tmp_path / "s.png", "rel": "s.png"}]
    palettes = reference.prepare_all(entries)
    assert [p["label"] for p in palettes] == ["s.png#1", "s.png（back）", "s.png#3"]
    cutout = palettes[0]["cutout"]
    assert cutout.shape == palettes[0]["thumb"].shape
    assert tuple(cutout[20, 20]) == (200, 30, 30) and cutout[0, 0, 0] == cutout[0, 0, 1] >= 200     # 人物の画素はそのまま、背景は市松模様


def test_two_views_cut_from_one_sheet_are_reported_by_view_with_their_cutouts(tmp_path):
    from PIL import Image

    demo.create(tmp_path, anomalies=["costume"])
    sheet = np.array(Image.open(tmp_path / "sheet.png"))
    Image.fromarray(np.concatenate([sheet, sheet[:, ::-1]], axis=1), "RGBA").save(tmp_path / "turn.png")
    config = read_json(tmp_path / "inspect.json")
    config["reference"] = [{"path": "turn.png", "view": "front", "box": [0.0, 0.0, 0.5, 1.0]},
                           {"path": "turn.png", "view": "back", "box": [0.5, 0.0, 1.0, 1.0]}]
    (tmp_path / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    result, out = run.run(tmp_path / "inspect.json", tmp_path / "out")
    labels = ["turn.png（front）", "turn.png（back）"]
    assert [r["label"] for r in result["references"]] == labels and [r["view"] for r in result["references"]] == ["front", "back"]
    for ref in result["references"]:
        assert ref["cutout"].startswith("references/") and video.load_rgb(out / ref["cutout"]).shape[1] > 2 * 120
    costume = next(e for e in result["events"] if e["category"] == "costume")
    assert {s["reference"] for s in costume["signals"] if s["check"].startswith("reference_")} <= set(labels)
    assert [b["label"] for b in result["clips"][0]["reference"]["best_reference"]] == labels
    text = (out / "inspect_report.md").read_text(encoding="utf-8")
    assert all(label in text for label in labels) and "references/01.png" in text


def test_events_point_at_the_clip_frames_and_carry_the_origin(checked):
    root, result, _, _ = checked
    origin = read_json(root / "inspect.json")["clips"][0]["origin"]
    digest = hashlib.sha256((root / "clip.mp4").read_bytes()).hexdigest()
    for event in result["events"]:
        target = event["target"]
        assert target["kind"] == "clip" and target["path"] == "clip.mp4" and target["sha256"] == digest
        assert target["source_frames"] == event["frames"] and target["origin"] == origin
        assert target["source_t"] == [round(event["frames"][0] / 24, 4), round(event["frames"][1] / 24, 4)]
        assert any(a["kind"] == "regenerate_segment" for a in event["actions"])
    freeze = next(e for e in result["events"] if e["label"] == "止まったコマ")
    assert [a["kind"] for a in freeze["actions"]][:1] == ["interpolate"]
    glitch = next(e for e in result["events"] if e["label"] == "数コマだけの乱れ")
    assert next(a for a in glitch["actions"] if a["kind"] == "replace_frames")["with"] == [29, 31]


def test_fix_plan_lists_segments_with_full_size_anchor_frames(checked):
    root, result, out, _ = checked
    plan = read_json(out / "fix_plan.json")
    assert plan["status"] == "proposed" and plan["executor"] is None
    covered = {event_id for segment in plan["segments"] for event_id in segment["events"]}
    assert covered == {e["id"] for e in result["events"]}
    for segment in plan["segments"]:
        first, last = segment["source_frames"]
        assert 0 <= first < last <= 96 and segment["origin"]["tool"] == "synthetic-demo"
        assert segment["actions"][0]["kind"] == "regenerate_segment" and segment["actions"][0]["frames"] == [first, last]
        for side, frame in (("before", first - 1), ("after", last)):
            anchor = segment["anchors"][side]
            if anchor is None:
                continue
            assert anchor["frame"] == frame
            image = video.load_rgb(out / anchor["image"])
            assert image.shape == (384, 216, 3)                                       # 元の大きさ（始点・終点の画に使う）
    pairs = [(s["events"], s["source_frames"]) for s in plan["segments"]]
    assert (["demo-F01", "demo-F02"], [6, 27]) in pairs        # すき間 3 コマ（0.15 秒以下）はまとめる


def test_stills_metrics_and_markdown_are_written(checked):
    _, result, out, _ = checked
    for event in result["events"]:
        image = video.load_rgb(out / event["still"])
        assert image.shape[0] == 384 and image.shape[1] >= 3 * 216          # 前・山・後
    rows = (out / "metrics" / "demo.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 97 and rows[0].startswith("frame,t,luma") and rows[0].endswith("reference_overlap,reference_missing,reference_extra")
    assert rows[1].split(",")[-2] == "0" and rows[65].split(",")[-2] != "0"               # 上着が青のコマ 64 で設定画の色が欠ける
    text = (out / "inspect_report.md").read_text(encoding="utf-8")
    assert "検出が無いことは合格ではありません" in text and "demo-F06" in text and "修正計画（提案。何も実行していません）" in text


def test_stills_are_grabbed_at_still_width_and_anchors_at_full_size(tmp_path):
    """静止画のコマは静止画の幅で取り出し、修正計画の前後のコマ（生成し直すときの画）だけを元の大きさで取り出す。
    元の大きさのコマを出来事の数だけ抱えないため（実素材の 60 秒 1080×1920 で、ピークのメモリが 2.1 GB から 0.6 GB になった）。"""
    from mv_pipeline.inspection import report

    demo.create(tmp_path, width=480, height=854, anomalies=["glitch"])
    result, out = run.run(tmp_path / "inspect.json", tmp_path / "out")
    event = next(e for e in result["events"] if e["frames"] == [30, 31])
    still = video.load_rgb(out / event["still"])
    assert still.shape[0] == video.even_size(480, 854, report.STILL_WIDTH)[1]          # 縦 568（幅 320 に縮めた高さ）
    segment = next(s for s in read_json(out / "fix_plan.json")["segments"] if event["id"] in s["events"])
    assert video.load_rgb(out / segment["anchors"]["before"]["image"]).shape == (854, 480, 3)


def test_a_dull_key_color_that_would_remove_grey_skin_and_hair_is_reported():
    """くすんだ緑（3D 描画の背景）では、既定の tolerance で灰色・白・黒まで抜ける。注意を出し、下げる値を示す。"""
    from mv_pipeline.inspection import features

    dull = {"color": [90, 174, 62], "tolerance": 0.22, "softness": 0.12}
    warning = features.key_warning(dull, "three")
    assert warning and "three" in warning and "tolerance を 0.11 以下" in warning
    assert features.key_warning({**dull, "tolerance": 0.06}, "three") is None
    assert features.key_warning({"color": [0, 255, 0], "tolerance": 0.22, "softness": 0.12}, "clip") is None
    grey = np.full((8, 8, 3), 200, np.uint8)
    assert not features.mask_of(grey, {"kind": "key", "key": dull}).any()                  # 実際に灰色が抜ける
    assert features.mask_of(grey, {"kind": "key", "key": {**dull, "tolerance": 0.06}}).all()


def test_a_clean_clip_has_no_events(tmp_path):
    demo.create(tmp_path, anomalies=[])
    result, _ = run.run(tmp_path / "inspect.json")
    assert result["events"] == [], [(e["frames"], e["label"]) for e in result["events"]]
    assert result["overall"] == "clean" and result["clips"][0]["reference"]["overlap_min"] > 0.8


def test_without_a_foreground_the_body_checks_are_not_run_and_say_so(tmp_path):
    demo.create(tmp_path, anomalies=["glitch", "fragment"])
    config = read_json(tmp_path / "inspect.json")
    config["foreground"] = "none"
    (tmp_path / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    result, _ = run.run(tmp_path / "inspect.json")
    skipped = {s["check"] for s in result["skipped"]}
    assert {"shape", "costume", "motion.position", "reference"} <= skipped
    assert any(e["category"] == "continuity" and e["frames"] == [30, 31] for e in result["events"])
    assert result["overall"] == "review"
    clean = tmp_path / "clean"
    demo.create(clean, anomalies=[])
    config = read_json(clean / "inspect.json")
    config["foreground"] = "none"
    (clean / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    assert run.run(clean / "inspect.json")[0]["overall"] == "clean_with_skips"     # 見ていない検査があれば clean にしない


def test_spikes_pair_into_and_back_or_stay_as_a_step():
    series = np.full(40, 0.01)
    series[0] = np.nan
    series[10], series[12], series[30] = 0.5, 0.45, 0.4
    found = detect.spikes(series, 6, 0.06, 6.0, 3.0, 0.004)
    assert [f[0] for f in found] == [10, 12, 30]
    back = {(9, 12)}       # コマ 9 と 12 はほぼ同じ絵（10〜11 だけ変わった）
    segments = detect.pair(found, lambda a, b: 0.01 if (a, b) in back else 0.5, 48, 0.5)
    assert [(a, b, kind) for a, b, kind, _ in segments] == [(10, 12, "pair"), (30, 31, "step")]


def test_runs_join_small_gaps_and_drop_short_runs():
    flags = [False, True, True, False, True, False, False, False, True, False]
    assert detect.runs(flags) == [(1, 3), (4, 5), (8, 9)]
    assert detect.runs(flags, minimum=2, gap=1) == [(1, 5)]


def _signal(check, category, frames, kind="pair", value=1.0, threshold=0.5):
    return {"check": check, "category": category, "label": detect.LABELS[check], "kind": kind, "frames": list(frames),
            "peak": frames[0], "value": value, "baseline": 0.0, "threshold": threshold, "unit": "", "z": 9.0, "region": None, "detail": check}


def test_signals_of_one_moment_merge_and_the_priority_picks_the_category():
    events = detect.events([_signal("glitch", "continuity", (30, 31)), _signal("silhouette_pop", "shape", (30, 31), value=3.0),
                            _signal("motion_spike", "motion", (31, 32), kind="step"),
                            _signal("silhouette_pop", "shape", (50, 53)), _signal("motion_spike", "motion", (50, 51), kind="step")],
                           "c", 24)
    assert [(e["frames"], e["category"]) for e in events] == [([30, 31], "continuity"), ([50, 53], "shape")]
    assert events[0]["categories"] == ["continuity", "shape", "motion"]


def test_an_independent_step_next_to_a_short_event_stays_inside_what_to_fix():
    """塊の分離 [30, 33) と、すぐ後の背景の切り替わり（変わり目だけ。コマ 34）がまとまっても、34 を区間から落とさない。"""
    events = detect.events([_signal("fragment", "shape", (30, 33), kind="segment"), _signal("jump", "continuity", (34, 35), kind="step")], "c", 24)
    assert len(events) == 1 and events[0]["frames"] == [30, 35] and events[0]["peak"] == 34 and events[0]["category"] == "continuity"
    back = detect.events([_signal("teleport", "motion", (84, 86)), _signal("motion_spike", "motion", (86, 87), kind="step")], "c", 24)
    assert [e["frames"] for e in back] == [[84, 86]]          # 組の戻りのコマの変わり目は区間に足さない


def test_a_freeze_keeps_the_catch_up_jump_frame_it_absorbs():
    """同じ絵がコマ 4〜6、コマ 7 は小さな変化、コマ 8 で追い付きの画の飛び（変わり目だけ）。止まった区間はコマ 8 まで含む。"""
    class Moving:
        first, count = 0, 16
        fdiff = np.array([np.nan, 0.01, 0.01, 0.01, 0.01, 0.0, 0.0, 0.01, 0.2, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01, 0.01])

        def frame(self, i):
            return int(i)

    jumps = [_signal("jump", "continuity", (8, 9), kind="step")]
    found = detect._frozen(Moving(), inspect_config.THRESHOLDS, 24, jumps)
    assert [(s["check"], s["frames"]) for s in found] == [("freeze", [4, 9])] and jumps == []     # 追い付きは止まりの信号に入れる


def test_the_jitter_peak_stays_inside_its_interval_when_the_shake_grows():
    """8 fps・20 コマ・体の高さ一定で、左右の揺れが少しずつ大きくなる。いちばん強いのは最後の方でも、山は区間の中。"""
    class Shaking:
        first, count, size = 0, 20, (100, 100)
        centroid = np.stack([[0.5 + 0.002 * (i + 1) * (1 if i % 2 else -1) for i in range(20)], [0.5] * 20], axis=1)
        present, body = np.ones(20, bool), np.full(20, 0.5)
        bbox = np.tile([0.4, 0.2, 0.6, 0.8], (20, 1))

        def frame(self, i):
            return int(i)

    found = detect._jitter(Shaking(), inspect_config.THRESHOLDS, 8)
    assert len(found) == 1
    a, b = found[0]["frames"]
    assert a <= found[0]["peak"] < b, found[0]


def test_a_clip_wide_duplicate_cadence_does_not_swallow_a_glitch_inside_it():
    events = detect.events([_signal("duplicate_cadence", "continuity", (3, 95), kind="segment", value=18, threshold=3),
                            _signal("glitch", "continuity", (30, 31))], "c", 24)
    assert sorted(e["label"] for e in events) == ["数コマだけの乱れ", "重複コマの周期"]


def test_a_hand_sized_piece_beside_a_large_body_is_counted():
    """全身が画面の 2 割ほどの描画で、体から離れた拳ほどの塊（体の 1.5%・画面の 0.3%）を塊として数える（Blender の描画で確かめた大きさ）。"""
    from mv_pipeline.inspection import features
    mask = np.zeros((568, 320), bool)
    mask[60:520, 110:200] = True                              # 体: 画面の 22.8%
    mask[120:134, 30:70] = True                               # 離れた塊: 画面の 0.31%・体の 1.35%
    assert features.silhouette(mask).components == 2
    mask[120:134, 30:70] = False
    mask[120:124, 30:40] = True                               # 雑音ほどの粒は数えない
    assert features.silhouette(mask).components == 1


def test_the_frames_where_the_person_vanishes_and_comes_back_are_not_a_motion_change(tmp_path):
    """人物の消えたコマと戻ったコマの動きは、出入りを測るだけなので測らない（消失は連続性の出来事 1 つ）。"""
    from mv_pipeline.fx import render as fxrender

    frames = [demo.draw_character(216, 384, 0.5 + 0.05 * np.sin(i / 5), 0.94, np.sin(i / 4)) for i in range(72)]
    for i in range(36, 40):
        frames[i] = np.full_like(frames[i], (0, 255, 0))
    fxrender.write_video(tmp_path / "vanish.mp4", iter(frames), (216, 384), 24, 12, 72)
    (tmp_path / "inspect.json").write_text(json.dumps({"schema_version": "inspect/0.1", "clips": [{"path": "vanish.mp4"}],
                                                       "foreground": {"key": {"color": [0, 255, 0]}}}), encoding="utf-8")
    result, _ = run.run(tmp_path / "inspect.json")
    assert [(e["category"], e["frames"]) for e in result["events"]] == [("continuity", [36, 40])],         [(e["frames"], e["label"]) for e in result["events"]]


def test_duplicate_frames_every_fifth_frame_are_one_cadence_finding(tmp_path):
    """24 コマの絵を 30fps に水増しした素材（5 コマに 1 コマが前と同じ）。"""
    from mv_pipeline.fx import render as fxrender

    pictures = [demo.draw_character(144, 256, 0.5 + 0.06 * np.sin(i / 4), 0.94, np.sin(i / 3)) for i in range(48)]
    frames, source = [], 0
    for i in range(60):
        frames.append(pictures[min(source, 47)])
        if i % 5 != 3:
            source += 1
    fxrender.write_video(tmp_path / "padded.mp4", iter(frames), (144, 256), 30, 12, 60)
    (tmp_path / "inspect.json").write_text(json.dumps({"schema_version": "inspect/0.1", "clips": [{"path": "padded.mp4"}],
                                                       "foreground": {"key": {"color": [0, 255, 0]}}}), encoding="utf-8")
    result, _ = run.run(tmp_path / "inspect.json")
    cadence = [e for e in result["events"] if any(s["check"] == "duplicate_cadence" for s in e["signals"])]
    assert len(cadence) == 1 and cadence[0]["category"] == "continuity"
    signal = next(s for s in cadence[0]["signals"] if s["check"] == "duplicate_cadence")
    assert signal["period"] == 5 and len(signal["duplicates"]) >= 10
    assert next(a for a in cadence[0]["actions"] if a["kind"] == "decimate")["cycle"] == 5
    assert not [e for e in result["events"] if any(s["check"] == "freeze" for s in e["signals"])]


@pytest.fixture(scope="module")
def dance_root(tmp_path_factory):
    """デモの素材を dance の spec で 2 か所に使う（2 本目は元の 1.5 秒から）。"""
    from PIL import Image

    root = tmp_path_factory.mktemp("inspect_dance")
    demo.create(root)
    Image.fromarray(np.full((384, 216, 3), (30, 20, 50), np.uint8)).save(root / "stage.png")
    spec = {"schema_version": "dance-shot/0.2", "output": {"width": 216, "height": 384, "fps": 24}, "duration_s": 3.5,
            "stage": {"path": "stage.png"},
            "performer": {"clips": [{"path": "clip.mp4", "start": 0.0, "end": 2.0},
                                    {"path": "clip.mp4", "start": 2.0, "end": 3.5, "source_start": 1.5}],
                          "key": {"color": [0, 255, 0]}}}
    (root / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
    config = {"schema_version": "inspect/0.1", "dance_spec": "spec.json", "reference": [{"path": "sheet.png"}],
              "origins": {"clip.mp4": {"tool": "synthetic-demo", "job": "jobs/clip.json"}}}
    (root / "inspect_dance.json").write_text(json.dumps(config), encoding="utf-8")
    return root


def test_dance_spec_links_each_finding_to_the_clip_entries_and_shot_times(dance_root):
    result, out = run.run(dance_root / "inspect_dance.json", dance_root / "dance_report")
    clip = result["clips"][0]
    assert clip["inspected"] == [0, 72] and clip["spec"] == "spec.json" and clip["foreground"] == "key"
    by_label = {e["label"]: e for e in result["events"]}
    freeze = by_label["止まったコマ"]["target"]
    assert freeze["kind"] == "dance_clip" and freeze["source_frames"] == [40, 44] and freeze["origin"]["job"] == "jobs/clip.json"
    assert [(u["pointer"], u["shot_frames"]) for u in freeze["uses"]] == [("/performer/clips/0", [[40, 44]]), ("/performer/clips/1", [[52, 56]])]
    fragment = next(e for e in result["events"] if e["category"] == "shape")["target"]
    assert [(u["pointer"], u["shot_frames"], u["shot_t"]) for u in fragment["uses"]] == [("/performer/clips/1", [[62, 65]], [[2.5833, 2.7083]])]
    assert not any(e["frames"][0] >= 84 for e in result["events"])      # spec に出ない後ろのコマは見ていない
    plan = read_json(out / "fix_plan.json")
    assert any(u["pointer"] == "/performer/clips/1" for s in plan["segments"] for u in s["uses"])


def test_dance_findings_on_frames_the_spec_never_shows_are_kept_apart(tmp_path, dance_root):
    spec = read_json(dance_root / "spec.json")
    spec["performer"]["clips"] = [{"path": "clip.mp4", "start": 0.0, "end": 2.0, "source_start": 1 / 24, "speed": 2.0}]
    spec["duration_s"] = 2.0
    (dance_root / "spec_fast.json").write_text(json.dumps(spec), encoding="utf-8")
    config = {"schema_version": "inspect/0.1", "dance_spec": "spec_fast.json", "checks": ["continuity"]}
    (dance_root / "inspect_fast.json").write_text(json.dumps(config), encoding="utf-8")
    result, _ = run.run(dance_root / "inspect_fast.json", tmp_path / "out")
    assert any(e["frames"] == [30, 31] for e in result["unused"])            # 奇数のコマだけが出るので、コマ 30 の乱れは出ない
    assert not any(e["frames"] == [30, 31] for e in result["events"])
    assert result["summary"]["unused"] == len(result["unused"]) >= 1


def _adapter(tmp_path, *extra, sends=False):
    path = tmp_path / ("vlm_send.json" if sends else "vlm.json")
    path.write_text(json.dumps({"tool": "vlm-standin", "version": "0.1", "sends_data": sends,
                                "argv": [sys.executable, str(STANDIN), "--request", "{request}", "--output", "{output}", *extra]}),
                    encoding="utf-8")
    return path


@pytest.fixture(scope="module")
def small_demo(tmp_path_factory):
    root = tmp_path_factory.mktemp("inspect_vlm")
    demo.create(root, anomalies=["glitch", "fragment"])
    return root


def test_vlm_packet_only_writes_the_request_and_sends_nothing(small_demo, tmp_path):
    result, out = run.run(small_demo / "inspect.json", tmp_path / "out", vlm_packet=True)
    request = read_json(out / "vlm" / "request.json")
    assert result["vlm"]["status"] == "packet" and not (out / "vlm" / "response.json").exists()
    assert {item["id"] for item in request["frames"]} >= {"demo@30", "demo@50"}         # 出来事の山のコマは必ず入る
    assert all((out / "vlm" / item["file"]).is_file() for item in request["frames"])
    assert request["reference"][0]["path"] == "sheet.png" and set(request["categories"]) == {"shape", "motion", "costume", "continuity", "other"}


def test_an_adapter_that_sends_material_needs_allow_send(small_demo, tmp_path):
    adapter = _adapter(tmp_path, sends=True)
    with pytest.raises(PipelineError, match="--allow-send"):
        run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=adapter)
    assert not (tmp_path / "out").exists()                                            # 重い処理の前に止まる
    result, _ = run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=adapter, allow_send=True)
    assert result["vlm"]["status"] == "run" and result["vlm"]["sends_data"] is True


def test_vlm_findings_are_estimates_attached_to_measured_events_or_new_events(small_demo, tmp_path):
    adapter = _adapter(tmp_path, "--echo-events", "other", "--flag", "demo@0:costume:冒頭のコマの指摘", "--unreviewed", "demo@24")
    result, out = run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=adapter)
    attached = [e for e in result["events"] if e["evidence"] == "measured+estimated"]
    assert {tuple(e["frames"]) for e in attached} == {(30, 31), (50, 53)}
    only = [e for e in result["events"] if e["evidence"] == "estimated"]
    assert len(only) == 1 and only[0]["frames"] == [0, 1] and only[0]["category"] == "costume"
    assert only[0]["target"]["source_frames"] == [0, 1] and only[0]["id"].startswith("demo-F")
    assert any(only[0]["id"] in s["events"] and "estimated" in s["evidence"] for s in read_json(out / "fix_plan.json")["segments"])
    assert any("見られなかったコマ" in w for w in result["vlm"]["warnings"])
    text = (out / "inspect_report.md").read_text(encoding="utf-8")
    assert "（VLM の指摘: 代役の指摘（event:" in text and "ほかの信号: VLM の指摘" not in text      # 数値の出来事に付いた指摘は文を出す


def test_a_vlm_response_must_answer_this_request(small_demo, tmp_path):
    run.run(small_demo / "inspect.json", tmp_path / "first", vlm_packet=True)
    request = (tmp_path / "first" / "vlm" / "request.json").read_bytes()
    good = {"schema_version": vlm.RESPONSE_SCHEMA, "tool": {"name": "hand"}, "request_sha256": hashlib.sha256(request).hexdigest(),
            "findings": [{"frame_id": "demo@30", "category": "continuity", "description": "手で見た指摘"}]}
    (tmp_path / "good.json").write_text(json.dumps(good), encoding="utf-8")
    result, _ = run.run(small_demo / "inspect.json", tmp_path / "second", vlm_response=tmp_path / "good.json")
    assert result["vlm"]["status"] == "imported" and result["vlm"]["findings"] == 1
    for broken, message in (({**good, "request_sha256": "0" * 64}, "request_sha256"),
                            ({**good, "findings": [{**good["findings"][0], "frame_id": "demo@999"}]}, "依頼に無い"),
                            ({**good, "findings": [{**good["findings"][0], "category": "taste"}]}, "形が違います")):
        (tmp_path / "bad.json").write_text(json.dumps(broken), encoding="utf-8")
        with pytest.raises(PipelineError, match=message):
            run.run(small_demo / "inspect.json", tmp_path / "third", vlm_response=tmp_path / "bad.json")


def test_the_report_shows_the_model_and_token_counts_an_adapter_wrote(small_demo, tmp_path):
    """アダプタが返答に書いたモデルとトークン数（x_usage）を、報告の VLM の欄に出す（実際の API で費用が分からなかった）。"""
    run.run(small_demo / "inspect.json", tmp_path / "first", vlm_packet=True)
    request = (tmp_path / "first" / "vlm" / "request.json").read_bytes()
    usage = {"input_tokens": 1234, "cache_creation_input_tokens": 5000, "cache_read_input_tokens": 10000, "output_tokens": 789}
    response = {"schema_version": vlm.RESPONSE_SCHEMA, "tool": {"name": "claude", "model": "some-model"},
                "request_sha256": hashlib.sha256(request).hexdigest(), "findings": [], "x_usage": usage}
    (tmp_path / "response.json").write_text(json.dumps(response), encoding="utf-8")
    result, out = run.run(small_demo / "inspect.json", tmp_path / "second", vlm_response=tmp_path / "response.json")
    assert result["vlm"]["usage"] == usage and result["vlm"]["model"] == "some-model"
    text = (out / "inspect_report.md").read_text(encoding="utf-8")
    assert "モデル some-model" in text and "入力 1,234・キャッシュの書き込み 5,000・キャッシュの読み出し 10,000・出力 789" in text
    (tmp_path / "response.json").write_text(json.dumps({**response, "x_usage": {"input_tokens": "many"}}), encoding="utf-8")
    result, _ = run.run(small_demo / "inspect.json", tmp_path / "third", vlm_response=tmp_path / "response.json")
    assert "usage" not in result["vlm"]                                            # 形の違う x_usage は写さない


def test_a_response_inside_the_last_report_can_be_imported_again(small_demo, tmp_path):
    adapter = _adapter(tmp_path, "--echo-events", "shape")
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out, vlm_config=adapter)
    result, _ = run.run(small_demo / "inspect.json", out, vlm_response=out / "vlm" / "response.json")   # 片付ける前に読む
    assert result["vlm"]["status"] == "imported" and result["vlm"]["findings"] >= 2


def test_the_output_folder_must_not_hold_other_work(small_demo, tmp_path):
    (tmp_path / "metrics").mkdir()
    (tmp_path / "metrics" / "keep.csv").write_text("mine", encoding="utf-8")
    with pytest.raises(PipelineError, match="別の --out"):
        run.run(small_demo / "inspect.json", tmp_path)
    assert (tmp_path / "metrics" / "keep.csv").read_text(encoding="utf-8") == "mine"


def test_plan_segments_never_overlap_when_a_long_event_holds_short_ones():
    from mv_pipeline.inspection import targets

    clip = {"id": "c", "rel": "c.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 120, "inspected": [0, 120], "origin": None}
    events = [{"id": f"c-F0{k}", "clip": "c", "frames": frames, "category": category, "evidence": "measured", "target": {},
               "actions": [{"kind": "regenerate_segment"}]}
              for k, (frames, category) in enumerate([([60, 75], "costume"), ([62, 63], "continuity"), ([70, 71], "motion")], 1)]
    segments = targets.plan([clip], events, inspect_config.THRESHOLDS)["segments"]
    assert [s["source_frames"] for s in segments] == [[58, 77]] and segments[0]["events"] == ["c-F01", "c-F02", "c-F03"]


def _plan_event(number, frames, category, checks, actions):
    return {"id": f"c-F{number:02d}", "clip": "c", "frames": list(frames), "category": category, "evidence": "measured", "target": {},
            "signals": [_signal(check, category, frames, kind="segment") for check in checks], "actions": actions}


def test_a_fast_change_that_does_not_settle_gets_interpolation_instead_of_frame_replacement():
    """絵が戻らない変化（step だけ）の 1 コマは、前後のコマで置き換えると止まりか二重写しになる（実素材で確かめた）。
    形状・動作は補間を、連続性の変わり目（画の切り替わり）は置き換えも補間も出さない。戻る変化（pair）は置き換えのまま。"""
    from mv_pipeline.inspection import targets

    clip = {"id": "c", "rel": "c.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 178, "inspected": [0, 170], "origin": None}

    def kinds(category, check, kind):
        event = {"frames": [81, 82], "category": category, "signals": [_signal(check, category, (81, 82), kind=kind)]}
        return [a["kind"] for a in targets.actions(event, clip, inspect_config.THRESHOLDS)]

    assert kinds("shape", "silhouette_pop", "step") == ["interpolate", "regenerate_segment"]
    assert kinds("motion", "motion_spike", "step") == ["interpolate", "regenerate_segment"]
    assert kinds("continuity", "jump", "step") == ["regenerate_segment"]
    assert kinds("shape", "silhouette_pop", "pair") == ["replace_frames", "regenerate_segment"]
    assert kinds("continuity", "glitch", "pair") == ["replace_frames", "regenerate_segment"]
    vlm_only = {"frames": [24, 25], "category": "shape", "signals": [{**_signal("silhouette_pop", "shape", (24, 25), kind="point"), "check": "vlm"}]}
    assert [a["kind"] for a in targets.actions(vlm_only, clip, inspect_config.THRESHOLDS)] == ["replace_frames", "regenerate_segment"]


def test_plan_keeps_a_clip_wide_cadence_out_of_local_regeneration():
    from mv_pipeline.inspection import targets

    clip = {"id": "c", "rel": "c.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 100, "inspected": [0, 100], "origin": None}
    cadence = _plan_event(1, (3, 95), "continuity", ["duplicate_cadence"], [{"kind": "decimate", "cycle": 5}])
    shape = _plan_event(2, (30, 31), "shape", ["silhouette_pop"], [{"kind": "regenerate_segment", "frames": [30, 31]}])
    segments = targets.plan([clip], [cadence, shape], inspect_config.THRESHOLDS)["segments"]
    assert [(s["events"], s["source_frames"], [a["kind"] for a in s["actions"]]) for s in segments] == [
        (["c-F01"], [1, 97], ["decimate"]), (["c-F02"], [28, 33], ["regenerate_segment"])]


def test_plan_keeps_each_frame_specific_remedy_of_merged_events():
    from mv_pipeline.inspection import targets

    clip = {"id": "c", "rel": "c.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 100, "inspected": [0, 100], "origin": None}
    first = _plan_event(1, (30, 31), "continuity", ["glitch"], [{"kind": "replace_frames", "frames": [30, 31], "with": [29, 31]},
                                                               {"kind": "regenerate_segment", "frames": [30, 31]}])
    second = _plan_event(2, (34, 35), "continuity", ["glitch"], [{"kind": "replace_frames", "frames": [34, 35], "with": [33, 35]},
                                                                {"kind": "regenerate_segment", "frames": [34, 35]}])
    segments = targets.plan([clip], [first, second], inspect_config.THRESHOLDS)["segments"]
    assert len(segments) == 1 and segments[0]["source_frames"] == [28, 37]
    assert [a["frames"] for a in segments[0]["actions"] if a["kind"] == "replace_frames"] == [[30, 31], [34, 35]]


def test_plan_lists_every_dance_use_of_the_padded_window():
    from mv_pipeline.inspection import targets

    usage = [(30, 0, 30, 1.0), (31, 0, 31, 1.0), (100, 1, 32, 1.0), (101, 1, 33, 1.0)]   # (出力のコマ, clip, 元のコマ, 重み)
    clip = {"id": "c", "rel": "c.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 100, "inspected": [0, 100], "origin": None,
            "spec": "spec.json", "usage": usage, "shot_fps": 24.0}
    event = _plan_event(1, (30, 31), "shape", ["silhouette_pop"], [{"kind": "regenerate_segment", "frames": [30, 31]}])
    event["target"] = {"uses": targets.dance_uses(usage, [30, 31], 99, 24.0)}
    assert [u["pointer"] for u in event["target"]["uses"]] == ["/performer/clips/0"]
    segment = targets.plan([clip], [event], inspect_config.THRESHOLDS)["segments"][0]
    assert segment["source_frames"] == [28, 33]                       # 元のコマ 32 も生成し直すので、clip 1 の出力 100 にも出る
    assert [(u["pointer"], u["shot_frames"]) for u in segment["uses"]] == [("/performer/clips/0", [[30, 32]]), ("/performer/clips/1", [[100, 101]])]


def test_the_vlm_packet_shows_the_configured_crop_of_a_sheet(tmp_path):
    from PIL import Image

    sheet = np.zeros((100, 200, 3), np.uint8)
    sheet[:, :100] = (220, 30, 30)
    sheet[:, 100:] = (30, 30, 220)
    Image.fromarray(sheet).save(tmp_path / "two.png")
    entry = {"path": tmp_path / "two.png", "rel": "two.png", "view": "front", "box": [0.5, 0.0, 1.0, 1.0]}
    _, request = vlm.write_packet(tmp_path / "out", [], {}, [entry], {**inspect_config.VLM_DEFAULTS})
    assert request["reference"][0]["box"] == [0.5, 0.0, 1.0, 1.0]
    image = video.load_rgb(tmp_path / "out" / "vlm" / request["reference"][0]["file"])
    assert image.shape[:2] == (100, 100) and image[..., 2].min() > 200 and image[..., 0].max() < 60    # 右半分（青）だけ


def test_vlm_packet_and_vlm_together_are_refused(small_demo, tmp_path):
    with pytest.raises(PipelineError, match="--vlm-packet"):
        run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=_adapter(tmp_path), vlm_packet=True)


def test_an_empty_checks_list_runs_no_check_and_says_so(small_demo, tmp_path):
    config = read_json(small_demo / "inspect.json")
    config.update(checks=[], clips=[{**config["clips"][0], "path": str(small_demo / "clip.mp4")}], reference=[{"path": str(small_demo / "sheet.png")}])
    (tmp_path / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    result, _ = run.run(tmp_path / "inspect.json", tmp_path / "out")
    assert result["events"] == [] and result["overall"] == "clean_with_skips"
    assert {s["check"] for s in result["skipped"]} == {"continuity", "motion", "shape", "costume"}


def test_a_vlm_costume_remark_adds_costume_remedies_to_the_event(small_demo, tmp_path):
    result, out = run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=_adapter(tmp_path, "--echo-events", "costume"))
    glitch = next(e for e in result["events"] if e["frames"] == [30, 31])
    assert glitch["category"] == "continuity" and "costume" in glitch["categories"]
    assert {"regenerate_with_reference", "color_match"} <= {a["kind"] for a in glitch["actions"]}
    segment = next(s for s in read_json(out / "fix_plan.json")["segments"] if glitch["id"] in s["events"])
    assert "regenerate_with_reference" in {a["kind"] for a in segment["actions"]}


def test_a_hanging_vlm_program_is_stopped_and_its_output_is_logged(small_demo, tmp_path):
    path = tmp_path / "slow.json"
    path.write_text(json.dumps({"tool": "slow", "version": "0", "sends_data": False, "timeout_s": 1,
                                "argv": [sys.executable, "-c", "import time; print('working', flush=True); time.sleep(30)"]}), encoding="utf-8")
    with pytest.raises(PipelineError, match="終わりませんでした") as error:
        run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=path)
    logs = list((tmp_path / "out" / "failed").glob("*/adapter.log"))     # 失敗した実行のログは failed/<run_id>/ に残る
    assert len(logs) == 1 and str(logs[0]) in str(error.value)            # エラーの文は残した場所を指す
    log = logs[0].read_text(encoding="utf-8")
    assert log.startswith("timeout after 1") and "b'" not in log
    record = run.runs(tmp_path / "out")[-1]
    assert record["status"] == "failed" and record["kept"] == [str(logs[0])] and not (tmp_path / "out" / "vlm").exists()


def test_the_vlm_request_is_bound_to_the_frame_contents_and_offers_every_category(tmp_path):
    first = tmp_path / "a"
    demo.create(first, anomalies=[])
    config = read_json(first / "inspect.json")
    config["checks"] = []                                    # VLM だけの検品: 見本のコマ（0・24・48・72）だけを見せる
    (first / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    run.run(first / "inspect.json", first / "out", vlm_packet=True)
    raw = (first / "out" / "vlm" / "request.json").read_bytes()
    request = json.loads(raw)
    assert set(request["categories"]) == {"shape", "motion", "costume", "continuity", "other"}
    assert all(len(item["sha256"]) == 64 for item in request["frames"] + request["reference"])
    response = {"schema_version": vlm.RESPONSE_SCHEMA, "tool": {"name": "hand"}, "request_sha256": hashlib.sha256(raw).hexdigest(),
                "findings": [{"frame_id": "demo@72", "category": "costume", "description": "前の素材への指摘"}]}
    (tmp_path / "old.json").write_text(json.dumps(response), encoding="utf-8")
    assert run.run(first / "inspect.json", first / "again", vlm_response=tmp_path / "old.json")[0]["vlm"]["findings"] == 1
    other = tmp_path / "b"
    demo.create(other, anomalies=["costume"])                # 同じ長さで、コマ 60〜74 だけ違う素材
    (first / "clip.mp4").write_bytes((other / "clip.mp4").read_bytes())
    with pytest.raises(PipelineError, match="request_sha256"):
        run.run(first / "inspect.json", first / "replaced", vlm_response=tmp_path / "old.json")


def test_a_vlm_remark_on_the_frame_after_an_event_widens_what_to_fix(small_demo, tmp_path):
    config = read_json(small_demo / "inspect.json")
    config.update(vlm={"every_s": 31 / 24}, clips=[{**config["clips"][0], "path": str(small_demo / "clip.mp4")}],
                  reference=[{"path": str(small_demo / "sheet.png")}])                      # 見本のコマが 31（乱れの次のコマ）に来る
    (tmp_path / "inspect.json").write_text(json.dumps(config), encoding="utf-8")
    result, out = run.run(tmp_path / "inspect.json", tmp_path / "out", vlm_config=_adapter(tmp_path, "--flag", "demo@31:shape:乱れの次のコマの指摘"))
    glitch = next(e for e in result["events"] if e["frames"][0] == 30)
    assert glitch["frames"] == [30, 32] and glitch["target"]["source_frames"] == [30, 32] and glitch["t"] == [1.25, 1.3333]
    assert "shape" in glitch["categories"] and glitch["evidence"] == "measured+estimated"
    segment = next(s for s in read_json(out / "fix_plan.json")["segments"] if glitch["id"] in s["events"])
    assert segment["event_frames"] == [30, 32] and segment["source_frames"][1] >= 32


def _vlm_signal(frame, category, box=None):
    return {"check": "vlm", "category": category, "label": "VLM の指摘", "kind": "point", "frames": [frame, frame + 1], "peak": frame,
            "value": None, "baseline": None, "threshold": None, "unit": None, "z": None, "region": box, "detail": "x"}


def test_the_still_draws_each_vlm_box_on_the_frame_it_describes():
    from mv_pipeline.inspection import report

    event = {"id": "c-F01", "clip": "c", "frames": [30, 32], "peak": 30, "region": [0.1, 0.1, 0.4, 0.4],
             "signals": [_signal("glitch", "continuity", (30, 31)), _vlm_signal(31, "shape", [0.5, 0.5, 0.9, 0.9])]}
    assert report.needed_frames([event], [])["c"] == {29, 30, 31, 32}
    frames = {n: np.full((64, 40, 3), 90, np.uint8) for n in (29, 30, 31, 32)}
    sheet = report.contact_sheet(event, frames, [])
    panels = [sheet[:, k * (40 + report.GAP):k * (40 + report.GAP) + 40] for k in range(4)]     # コマ 29・30・31・32
    yellow = [int(((p[..., 0] > 200) & (p[..., 1] > 180) & (p[..., 2] < 100)).sum()) for p in panels]
    red = [int(((p[..., 0] > 200) & (p[..., 1] < 100) & (p[..., 2] < 100)).sum()) for p in panels]
    assert yellow[2] > 0 and yellow[0] == yellow[1] == yellow[3] == 0                      # VLM の箱はコマ 31 だけ
    assert red[1] > 0 and red[2] == 0                                                       # 数値の箱は山のコマ 30


def test_vlm_remarks_join_only_short_events_or_events_of_the_same_kind_and_never_a_cadence():
    def event(frames, category, check):
        return {"clip": "c", "frames": list(frames), "category": category, "categories": [category], "evidence": "measured",
                "signals": [_signal(check, category, frames, kind="segment")]}

    cadence, costume, glitch = event((3, 95), "continuity", "duplicate_cadence"), event((60, 75), "costume", "costume_pop"), event((30, 31), "continuity", "glitch")
    request = {"frames": [{"id": f"c@{f}", "clip": "c", "frame": f} for f in (31, 62, 70, 10)]}
    response = {"tool": {"name": "t"}, "findings": [
        {"frame_id": "c@31", "category": "shape", "description": "短い出来事の次のコマ"},
        {"frame_id": "c@62", "category": "other", "description": "衣装の出来事の中の別の種類"},
        {"frame_id": "c@70", "category": "costume", "description": "衣装の出来事の中の同じ種類"},
        {"frame_id": "c@10", "category": "shape", "description": "周期の中の局所の指摘"}]}
    added = vlm.attach([cadence, costume, glitch], response, request, [{"id": "c", "fps": 24.0}])
    assert glitch["frames"] == [30, 32] and "shape" in glitch["categories"]
    assert [s["frame_id"] for s in costume["signals"] if s["check"] == "vlm"] == ["c@70"]
    assert not any(s["check"] == "vlm" for s in cadence["signals"])
    assert sorted(e["frames"][0] for e in added) == [10, 62] and all(e["evidence"] == "estimated" for e in added)


def _load_claude_adapter():
    spec = importlib.util.spec_from_file_location("vlm_claude", CLAUDE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Block:
    def __init__(self, text):
        self.type, self.text = "text", text


class _Usage:
    def __init__(self, input_tokens, output_tokens, cache_creation_input_tokens=0, cache_read_input_tokens=0):
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.cache_creation_input_tokens, self.cache_read_input_tokens = cache_creation_input_tokens, cache_read_input_tokens


class _Message:
    def __init__(self, text, stop_reason="end_turn", usage=None):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason, self.model, self.stop_details = stop_reason, "served-model", None
        self.usage = usage


class _FakeClient:
    """anthropic.Anthropic の client.beta.messages.create の形だけを真似る（送信しない）。"""

    def __init__(self, replies):
        self.replies, self.calls = list(replies), []
        self.beta = self
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.replies.pop(0)


def test_claude_adapter_sends_the_sheets_as_a_cached_prefix_and_keeps_refused_frames_unreviewed(small_demo, tmp_path):
    module = _load_claude_adapter()
    run.run(small_demo / "inspect.json", tmp_path / "out", vlm_packet=True)
    request_path = tmp_path / "out" / "vlm" / "request.json"
    request = read_json(request_path)
    ids = [item["id"] for item in request["frames"]]
    planned = module.batches(request, request_path.parent, 3)
    assert [len(chunk) for chunk, _ in planned] == [len(ids[i:i + 3]) for i in range(0, len(ids), 3)]
    head = planned[0][1][:2]
    assert head[1]["type"] == "image" and head[1]["source"]["media_type"] == "image/png" and head[1]["cache_control"] == {"type": "ephemeral"}
    assert planned[1][1][:2] == head                                               # どのバッチも同じ前置き（キャッシュに載る）
    first = json.dumps({"findings": [{"frame_id": ids[0], "category": "shape", "description": "腕の先に塊", "box": [0.7, -0.2, 1.4, 0.4]},
                                     {"frame_id": "other@1", "category": "shape", "description": "依頼に無い", "box": None}]})
    replies = [_Message(first, usage=_Usage(1000, 200, 3000, 0))] + [_Message(None, "refusal", usage=_Usage(900, 10, 0, 3000))] * (len(planned) - 1)
    client = _FakeClient(replies)
    findings, unreviewed, log, served = module.review(client, request, request_path.parent, module.DEFAULT_MODEL, "high", 3, say=lambda m: None)
    assert findings == [{"frame_id": ids[0], "category": "shape", "description": "腕の先に塊", "box": [0.7, 0.0, 1.0, 0.4]}]
    assert {item["frame_id"] for item in unreviewed} == set(ids[3:]) and served == "served-model"
    call = client.calls[0]
    assert call["model"] == module.DEFAULT_MODEL and call["fallbacks"] == "default" and call["betas"] == [module.FALLBACK_BETA]
    assert call["output_config"]["format"]["schema"]["properties"]["findings"]["items"]["properties"]["frame_id"]["enum"] == ids
    assert all(other["output_config"] == call["output_config"] and other["system"] == call["system"] for other in client.calls)  # 前置きを同じに
    assert len(log) == len(planned) and log[1]["error"].startswith("拒否")
    rest = len(planned) - 1                                                        # 拒否のバッチも課金されるので数える
    assert log[0]["usage"] == {"input_tokens": 1000, "cache_creation_input_tokens": 3000, "cache_read_input_tokens": 0, "output_tokens": 200}
    assert module.total_usage(log) == {"input_tokens": 1000 + 900 * rest, "cache_creation_input_tokens": 3000,
                                       "cache_read_input_tokens": 3000 * rest, "output_tokens": 200 + 10 * rest}
    assert module.total_usage([{"batch": 1, "error": "HTTP 500"}]) is None


def test_claude_adapter_follows_the_request_language():
    module = _load_claude_adapter()
    base = {"instructions": "x", "categories": {"shape": "s"}}
    assert "English" in module.system_prompt({**base, "language": "en"}) and "日本語" not in module.system_prompt({**base, "language": "en"})
    assert "日本語" in module.system_prompt({**base, "language": "ja"})


def test_claude_adapter_dry_run_does_not_send(small_demo, tmp_path, capsys):
    module = _load_claude_adapter()
    run.run(small_demo / "inspect.json", tmp_path / "out", vlm_packet=True)
    request_path = tmp_path / "out" / "vlm" / "request.json"
    assert module.main(["--request", str(request_path), "--output", str(tmp_path / "r.json"), "--dry-run"]) == 0
    assert json.loads(capsys.readouterr().out)["frames"] == len(read_json(request_path)["frames"])
    assert not (tmp_path / "r.json").exists()


@pytest.mark.parametrize("change, message", [
    ({"clips": [{"path": "clip.mp4"}], "dance_spec": "spec.json"}, "どちらか 1 つ"),
    ({"thresholds": {"jump": 0.1}}, "知らないしきい値"),
    ({"thresholds": {"jump_min": 0}}, "0 より大きく"),
    ({"clips": [{"path": "clip.mp4", "frames": [10, 5]}]}, "開始 < 終了"),
    ({"clips": [{"path": "missing.mp4"}]}, "見つかりません"),
    ({"clips": [{"path": "clip.mp4"}, {"path": "clip.mp4"}]}, "id が重なって"),
    ({"surprise": 1}, "形が違います"),
])
def test_config_errors_say_what_to_fix(small_demo, tmp_path, change, message):
    config = {**read_json(small_demo / "inspect.json"), **change}
    if "dance_spec" in change:
        config["clips"] = [{"path": str(small_demo / "clip.mp4")}]
    config["clips"] = [{**c, "path": str(small_demo / c["path"]) if not Path(c["path"]).is_absolute() else c["path"]} for c in config.get("clips", [])]
    config["reference"] = [{"path": str(small_demo / "sheet.png")}]
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(PipelineError, match=message):
        inspect_config.load(path)


def test_extension_keys_are_allowed_everywhere(small_demo, tmp_path):
    config = read_json(small_demo / "inspect.json")
    config["x_note"] = "memo"
    config["clips"][0]["x_take"] = 3
    config["clips"][0]["path"] = str(small_demo / "clip.mp4")
    config["reference"][0]["path"] = str(small_demo / "sheet.png")
    path = tmp_path / "inspect.json"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert inspect_config.load(path).clips[0]["id"] == "demo"


def test_the_same_input_gives_the_same_events(small_demo, tmp_path):
    first, _ = run.run(small_demo / "inspect.json", tmp_path / "a")
    second, _ = run.run(small_demo / "inspect.json", tmp_path / "b")
    strip = lambda events: [{k: v for k, v in e.items() if k != "still"} for e in events]  # noqa: E731
    assert strip(first["events"]) == strip(second["events"])


def test_cli_demo_then_check(tmp_path):
    code, made, err = run_cli("inspect", "demo", tmp_path / "d", "--width", "144", "--height", "256")
    assert code == 0, err
    code, summary, err = run_cli("inspect", "check", made["config"], "--quiet")
    assert code == 0, err
    assert summary["overall"] == "review" and summary["events"] == len(read_json(made["expected"])["anomalies"])
    assert Path(summary["report"]).is_file() and Path(summary["fix_plan"]).is_file()
    code, _, err = run_cli("inspect", "check", made["config"], "--vlm", tmp_path / "missing.json")
    assert code == 1 and "VLM の設定が見つかりません" in err


def _snapshot(out):
    """出力先の報告の run_id と、静止画などのファイルの一覧と中身の sha256。"""
    files = {str(p.relative_to(out)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob("*"))
             if p.is_file() and p.name not in (run.RUNS, run.MARKER) and not p.relative_to(out).parts[0].startswith(".")}
    return read_json(out / "inspect_report.json")["run"]["id"], files


def test_a_failed_rerun_keeps_the_last_good_report_and_its_files(small_demo, tmp_path, monkeypatch):
    out = tmp_path / "out"
    first, _ = run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    assert before[0] == first["run"]["id"] and any(name.startswith("stills") for name in before[1])

    def broken(*args, **kwargs):
        raise RuntimeError("静止画を書く途中で止まった")
    monkeypatch.setattr(run.report, "write_anchors", broken)
    with pytest.raises(RuntimeError, match="途中で止まった"):
        run.run(small_demo / "inspect.json", out)
    assert _snapshot(out) == before                                   # 前の成功した報告と、その静止画・計測がそのまま
    assert not list(out.glob(".staging-*")) and not list(out.glob(".previous-*"))
    records = run.runs(out)
    assert [r["status"] for r in records] == ["succeeded", "failed"] and "途中で止まった" in records[-1]["error"]
    assert records[0]["run_id"] == first["run"]["id"] and records[0]["inputs"] == first["run"]["inputs"]


def test_the_report_records_the_run_and_its_input_hashes(small_demo, tmp_path):
    result, out = run.run(small_demo / "inspect.json", tmp_path / "out")
    roles = {item["role"]: item for item in result["run"]["inputs"]}
    assert roles["config"]["sha256"] == result["config_sha256"] and roles["clip"]["sha256"] == result["clips"][0]["sha256"]
    assert roles["reference"]["sha256"] == hashlib.sha256((small_demo / "sheet.png").read_bytes()).hexdigest()
    assert result["run"]["started_at"] <= result["run"]["finished_at"] and result["run"]["id"] in (out / "inspect_report.md").read_text(encoding="utf-8")
    assert run.runs(out)[-1]["run_id"] == result["run"]["id"] and run.summarize(result, out)["run_id"] == result["run"]["id"]


def test_a_run_stopped_while_measuring_is_recorded_as_failed_and_its_work_is_cleared(small_demo, tmp_path):
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    stopped = "20261107T000000Z-dead"                                 # 強制終了した実行の跡: 作業用フォルダーと running の記録
    (out / f".staging-{stopped}" / "stills").mkdir(parents=True)
    (out / f".staging-{stopped}" / "stills" / "half.jpg").write_bytes(b"half")
    run._record_run(out, {"run_id": stopped, "status": "running", "started_at": "2026-11-07T00:00:00+00:00"})
    assert _snapshot(out) == before                                   # 止まった実行は前の結果に触れていない
    result, _ = run.run(small_demo / "inspect.json", out)
    entry = next(r for r in run.runs(out) if r["run_id"] == stopped)
    assert entry["status"] == "failed" and entry["interrupted"] is True
    assert not list(out.glob(".staging-*")) and any(stopped in note for note in result["notes"])


def test_a_publish_stopped_halfway_is_rolled_back_to_the_last_good_result(small_demo, tmp_path):
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    old = [name for name in run.OUTPUTS if (out / name).exists()]
    assert "vlm" not in old
    backup = out / ".previous-halfway"                                # 前の結果を外し、新しい結果を一部だけ入れた所で止まった
    backup.mkdir()
    for name in ("inspect_report.json", "inspect_report.md", "stills"):
        (out / name).rename(backup / name)
    (out / "stills").mkdir()
    (out / "stills" / "new.jpg").write_bytes(b"new")
    (out / "vlm").mkdir()                                             # 前には無かった、新しい結果のフォルダー
    (out / ".staging-halfway").mkdir()
    (out / run.JOURNAL).write_text(json.dumps({"run_id": "halfway", "staging": ".staging-halfway", "backup": backup.name, "old": old}),
                                   encoding="utf-8")
    notes = run.recover(out)
    assert _snapshot(out) == before and not (out / "vlm").exists()   # 前の結果に戻り、途中まで入れた新しい結果は消える
    assert not (out / run.JOURNAL).exists() and not backup.exists() and not (out / ".staging-halfway").exists()
    assert any("halfway" in note for note in notes)


def _locked(out, phase, error):
    """入れ替えの途中で 1 つだけ動かせない os.replace（ほかのアプリが開いている静止画・Ctrl+C など）。
    phase が old なら前の stills/ を外す所で、new なら新しい metrics/ を置く所で止まる。"""
    real = os.replace

    def replace(source, target):
        source = Path(source)
        if (phase == "old" and source == out / "stills") or (
                phase == "new" and source.name == "metrics" and source.parent.name.startswith(run.STAGING)):
            raise error("開かれていて動かせない")
        return real(source, target)
    return replace


@pytest.mark.parametrize("phase, error", [("old", PermissionError), ("new", PermissionError), ("new", KeyboardInterrupt)])
def test_a_publish_that_fails_is_rolled_back_at_once(small_demo, tmp_path, monkeypatch, phase, error):
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)     # 開かれたままのファイル: _replace が待ってやり直しても動かせない
    monkeypatch.setattr(run.os, "replace", _locked(out, phase, error))
    with pytest.raises(error):
        run.run(small_demo / "inspect.json", out)
    monkeypatch.undo()
    assert (out / "inspect_report.json").is_file() and _snapshot(out) == before   # 次の実行を待たずに、前の結果が公開の場所に戻る
    assert not (out / run.JOURNAL).exists() and not list(out.glob(".staging-*")) and not list(out.glob(".previous-*"))
    assert [r["status"] for r in run.runs(out)] == ["succeeded", "failed"]


def test_a_publish_that_cannot_be_rolled_back_keeps_the_backup_for_the_next_run(small_demo, tmp_path, monkeypatch):
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    real_rollback = run._rollback

    def stuck(out):                                                   # 戻す跡（控え）があるときだけ戻せない（実行の初めの recover は本物）
        if (Path(out) / run.JOURNAL).is_file():
            raise PermissionError("戻す所も開かれている")
        return real_rollback(out)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(run.os, "replace", _locked(out, "new", PermissionError))
    monkeypatch.setattr(run, "_rollback", stuck)
    with pytest.raises(PermissionError) as caught:
        run.run(small_demo / "inspect.json", out)
    monkeypatch.undo()
    assert str(caught.value) == "開かれていて動かせない"                            # 元の失敗のまま止まり、戻せなかったことは注に書く
    assert "前の結果へ戻せませんでした" in caught.value.__notes__[0]
    assert (out / run.JOURNAL).is_file() and len(list(out.glob(".previous-*"))) == 1   # 前の結果は控えと一緒に残す
    assert "前の結果へ戻せませんでした" in run.runs(out)[-1]["error"]
    notes = run.recover(out)                                                       # 次の実行の初めに戻す
    assert _snapshot(out) == before and any("入れ替えの途中" in note for note in notes)


def test_a_second_run_on_the_same_output_leaves_a_run_in_progress_alone(small_demo, tmp_path):
    from mv_pipeline.runner import project_lock
    out = tmp_path / "out"
    run.run(small_demo / "inspect.json", out)
    before = _snapshot(out)
    active = "20261107T000000Z-live"                                  # 測っている途中の実行（出力先のロックを持っている）
    (out / f".staging-{active}" / "metrics").mkdir(parents=True)
    run._record_run(out, {"run_id": active, "status": "running", "started_at": "2026-11-07T00:00:00+00:00"})
    with project_lock(out):
        with pytest.raises(PipelineError, match="ほかの mv inspect の実行が使っています"):
            run.run(small_demo / "inspect.json", out)
        code, _, err = run_cli("inspect", "check", small_demo / "inspect.json", "--out", out, "--quiet")
        assert code == 1 and "ほかの mv inspect の実行が使っています" in err
    assert (out / f".staging-{active}" / "metrics").is_dir() and _snapshot(out) == before   # 作業用フォルダーも前の結果もそのまま
    assert [r["status"] for r in run.runs(out)] == ["succeeded", "running"]               # 動いている実行を止まった扱いにしない
    run.run(small_demo / "inspect.json", out)                         # ロックが外れた後なら、止まった実行の跡として片付ける
    assert next(r for r in run.runs(out) if r["run_id"] == active)["status"] == "failed"
    assert not (out / f".staging-{active}").exists()


def test_a_vlm_response_without_the_request_hash_needs_the_legacy_flag(small_demo, tmp_path):
    run.run(small_demo / "inspect.json", tmp_path / "first", vlm_packet=True)
    unbound = {"schema_version": vlm.RESPONSE_SCHEMA, "tool": {"name": "hand"},
               "findings": [{"frame_id": "demo@30", "category": "continuity", "description": "どの依頼への返答か分からない指摘"}]}
    (tmp_path / "unbound.json").write_text(json.dumps(unbound), encoding="utf-8")
    with pytest.raises(PipelineError, match="--vlm-legacy-response"):
        run.run(small_demo / "inspect.json", tmp_path / "second", vlm_response=tmp_path / "unbound.json")
    result, out = run.run(small_demo / "inspect.json", tmp_path / "third", vlm_response=tmp_path / "unbound.json", vlm_legacy=True)
    assert result["vlm"]["status"] == "imported" and result["vlm"]["legacy_response"] is True and result["vlm"]["findings"] == 1
    assert any("互換の扱い" in w for w in result["vlm"]["warnings"])
    assert "互換の扱い" in (out / "inspect_report.md").read_text(encoding="utf-8")
    roles = {item["role"]: item for item in result["run"]["inputs"]}
    assert roles["vlm_response"]["sha256"] == hashlib.sha256((tmp_path / "unbound.json").read_bytes()).hexdigest()


def _clip(clip_id, frames=100, fps=10.0):
    return {"id": clip_id, "fps": fps, "inspected": [0, frames]}


def test_too_many_vlm_candidates_are_shared_between_clips_and_spread_over_time():
    """前は出来事の山を先頭から上限まで取り、後ろの出来事と後のクリップを見せなかった。"""
    events = [{"id": f"a-F{k:02d}", "clip": "a", "peak": 5 * k, "frames": [5 * k, 5 * k + 1]} for k in range(20)]
    events += [{"id": "b-F01", "clip": "b", "peak": 10, "frames": [10, 11]}, {"id": "b-F02", "clip": "b", "peak": 90, "frames": [90, 91]}]
    clips = [_clip("a"), _clip("b")]
    chosen = vlm.select(clips, events, {"every_s": 1.0, "max_frames": 6})
    assert [(c["clip"], c["frame"]) for c in chosen] == [("a", 0), ("a", 30), ("a", 65), ("a", 95), ("b", 10), ("b", 90)]
    described = vlm.describe_selection(chosen, events, clips)
    assert described["events_total"] == 22 and described["events_unsent"] == 16 and described["event_peaks"] == 6
    assert {item["id"] for item in described["unsent"]} == {e["id"] for e in events} - {"a-F00", "a-F06", "a-F13", "a-F19", "b-F01", "b-F02"}
    assert [(c["clip"], c["events"], c["events_sent"], c["span_t"]) for c in described["clips"]] == [("a", 20, 4, [0.0, 9.5]), ("b", 2, 2, [1.0, 9.0])]


def test_vlm_samples_spread_over_each_clip_instead_of_its_start():
    chosen = vlm.select([_clip("a"), _clip("b", frames=40)], [], {"every_s": 1.0, "max_frames": 6})
    assert [(c["clip"], c["frame"], c["why"]) for c in chosen] == [("a", 0, "sample"), ("a", 40, "sample"), ("a", 90, "sample"),
                                                                  ("b", 0, "sample"), ("b", 20, "sample"), ("b", 30, "sample")]
    everything = vlm.select([_clip("a", frames=30)], [{"id": "a-F01", "clip": "a", "peak": 12, "frames": [12, 13]}],
                            {"every_s": 1.0, "max_frames": 24})
    assert [c["frame"] for c in everything] == [0, 10, 12, 20] and vlm.describe_selection(everything)["events_unsent"] == 0


def _track(fps=30.0):
    """カメラが T 20 → 21 で 2 m 飛び（カット）、T 40〜45 で速く動く、作り物のトラック（model3d-stage-track/0.1）。"""
    frames, x = [], 0.0
    for T in range(1, 61):
        if T == 21:
            x += 2.0
        if 41 <= T <= 46:
            x += 0.1
        frames.append({"T": T, "f": T, "camera": {"location": [x, -3.0, 1.2], "target": [x, 0.0, 1.0], "lens": 35}})
    return {"schema_version": "model3d-stage-track/0.1", "fps": fps, "frames": frames}


def test_cuts_and_fast_moves_are_read_from_a_model3d_track(tmp_path):
    from mv_pipeline.inspection import cues
    cut_times, moves, lights = cues.from_track(_track()["frames"], 30.0)
    assert cut_times == [round(20 / 30, 4)]                            # 新しいショットの最初のコマ（動画のコマ 20）の時刻
    assert moves == [[round(39 / 30, 4), round(45 / 30, 4)]] and lights == []
    flash = _track()["frames"]
    for frame in flash:
        frame["wall"] = {"strength": 0.6 if frame["T"] == 31 else 0.2, "color": [1, 0, 0] if frame["T"] >= 50 else [0, 0, 1]}
    assert cues.from_track(flash, 30.0)[2] == [round(30 / 30, 4), round(31 / 30, 4), round(49 / 30, 4)]   # 跳ね上がり・戻り・色の切り替え
    first, second = _track()["frames"][:20], _track()["frames"][20:]     # 2 つのトラックの境目のカットも見る
    (tmp_path / "a.json").write_text(json.dumps({**_track(), "frames": first}), encoding="utf-8")
    (tmp_path / "b.json").write_text(json.dumps({**_track(), "frames": second}), encoding="utf-8")
    assert cues.load({"track": ["a.json", "b.json"]}, tmp_path)["cuts"] == [round(20 / 30, 4)]
    (tmp_path / "track.json").write_text(json.dumps(_track()), encoding="utf-8")
    (tmp_path / "keys.json").write_text(json.dumps({"schema_version": "model3d-light-keys/0.1", "fps": 30, "frames": 60,
                                                    "grid": {"period": 0.5, "k0_time": 0.25}, "low": [0], "mid": [0], "high": [0]}),
                                        encoding="utf-8")
    loaded = cues.load({"track": "track.json", "light_keys": "keys.json", "cuts": [1.9], "offset_s": 0.1}, tmp_path)
    assert loaded["cuts"] == pytest.approx([round(20 / 30, 4) - 0.1, 1.8]) and loaded["grid"] == {"period": 0.5, "k0_time": 0.15}
    assert [s["kind"] for s in loaded["sources"]] == ["track", "light_keys"] and loaded["sources"][0]["cuts"] == 1
    (tmp_path / "other.json").write_text(json.dumps({**_track(fps=24.0), "frames": second}), encoding="utf-8")
    with pytest.raises(PipelineError, match="fps"):
        cues.load({"track": ["a.json", "other.json"]}, tmp_path)
    (tmp_path / "bad.json").write_text(json.dumps({"schema_version": "something"}), encoding="utf-8")
    with pytest.raises(PipelineError, match="model3d-stage-track"):
        cues.load({"track": "bad.json"}, tmp_path)


def test_a_track_may_leave_out_the_camera_or_its_lens(tmp_path):
    from mv_pipeline.inspection import cues
    frames = _track()["frames"]
    for frame in frames:
        del frame["camera"]["lens"]                                    # レンズを省いたコマ（描く側の既定のレンズ）
    assert cues.from_track(frames, 30.0)[:2] == ([round(20 / 30, 4)], [[round(39 / 30, 4), round(45 / 30, 4)]])   # 位置と向きは見る
    mixed = _track()["frames"]
    for frame in mixed:                                                # 前半はレンズを省き、後半だけ 50 mm（片方だけ省いた組は比べない）
        if frame["T"] <= 30:
            del frame["camera"]["lens"]
        else:
            frame["camera"]["lens"] = 50
    assert cues.from_track(mixed, 30.0)[0] == [round(20 / 30, 4)]
    bare = _track()["frames"]
    for frame in bare:
        if frame["T"] >= 15:
            del frame["camera"]                                        # カメラを省いたコマ（描く側の、腰を追う既定のカメラ）
        frame["wall"] = {"strength": 0.6 if frame["T"] == 31 else 0.2}
    cut_times, moves, lights = cues.from_track(bare, 30.0)
    assert cut_times == [] and moves == [] and lights == [round(30 / 30, 4), round(31 / 30, 4)]   # カットと動きは見ず、照明は見る
    (tmp_path / "bare.json").write_text(json.dumps({**_track(), "frames": bare}), encoding="utf-8")
    assert cues.load({"track": "bare.json"}, tmp_path)["sources"][0]["frames_without_camera"] == 46


def _cue_signal(check, frames, peak=None):
    return {"check": check, "frames": list(frames), "peak": frames[0] if peak is None else peak}


def test_cues_are_noted_only_for_the_signals_they_can_explain():
    from mv_pipeline.inspection import cues
    loaded = cues.load({"cuts": [1.0], "moves": [[2.0, 2.5]], "beats": {"period": 0.5, "k0_time": 0.0}}, Path("."))
    at_cut = {"signals": [_cue_signal("jump", [30, 31]), _cue_signal("silhouette_pop", [30, 32])]}
    assert [(m["kind"], m["t"], m["checks"]) for m in cues.match(at_cut, loaded, 30.0)] == [("cut", 1.0, ["jump", "silhouette_pop"])]
    flash = {"signals": [_cue_signal("flicker", [46, 47])]}                 # 1.533 秒: 拍 1.5 から 0.067 拍
    assert [(m["kind"], m["t"]) for m in cues.match(flash, loaded, 30.0)] == [("beat", 1.5)]
    broken = {"signals": [_cue_signal("fragment", [45, 47])]}               # 拍の上でも、形の信号には拍を重ねない
    assert cues.match(broken, loaded, 30.0) == []
    swing = {"signals": [_cue_signal("motion_spike", [66, 67])]}
    assert [(m["kind"], m["t"]) for m in cues.match(swing, loaded, 30.0)] == [("move", [2.0, 2.5])]
    late = {"signals": [_cue_signal("flicker", [52, 53])]}                  # 1.733 秒: 拍から 0.47 拍
    assert cues.match(late, loaded, 30.0) == []


def test_events_at_cues_stay_as_candidates_and_say_which_cue(small_demo, tmp_path):
    config = read_json(small_demo / "inspect.json")
    config["clips"][0]["path"] = str(small_demo / config["clips"][0]["path"])
    config["reference"][0]["path"] = str(small_demo / config["reference"][0]["path"])
    plain, _ = run.run(_write(tmp_path / "plain.json", config), tmp_path / "plain")
    config["cues"] = {"cuts": [30 / 24], "beats": {"period": 50 / 24, "k0_time": 0.0}}
    result, out = run.run(_write(tmp_path / "cued.json", config), tmp_path / "cued")
    assert [e["frames"] for e in result["events"]] == [e["frames"] for e in plain["events"]]   # 演出と重なっても消さない
    by_frames = {tuple(e["frames"]): e for e in result["events"]}
    glitch, fragment = by_frames[(30, 31)], by_frames[(50, 53)]
    assert [m["kind"] for m in glitch["cues"]] == ["cut"] and glitch["status"] == "review"
    assert fragment["cues"] == []                                     # 拍の上の形の崩れは、演出として書かない
    assert result["summary"]["cues"] == {"events_with_cue": 1, "events_without_cue": len(result["events"]) - 1, "events_unchecked": 0}
    assert result["clips"][0]["cues"]["cuts"] == 1
    text = (out / "inspect_report.md").read_text(encoding="utf-8")
    assert "演出の時刻と重なる: カット 1.25 秒" in text and "重ならない出来事から見てください" in text


def _write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_cue_files_are_in_the_run_inputs(small_demo, tmp_path):
    config = read_json(small_demo / "inspect.json")
    config["clips"][0]["path"] = str(small_demo / config["clips"][0]["path"])
    config["reference"][0]["path"] = str(small_demo / config["reference"][0]["path"])
    track = _write(tmp_path / "track.json", _track(fps=24.0))
    keys = _write(tmp_path / "keys.json", {"schema_version": "model3d-light-keys/0.1", "fps": 24, "frames": 96,
                                           "grid": {"period": 0.5, "k0_time": 0.0}, "low": [0], "mid": [0], "high": [0]})
    config["cues"] = {"track": "track.json", "light_keys": "keys.json"}       # 全体の cues
    result, _ = run.run(_write(tmp_path / "cues.json", config), tmp_path / "global")
    roles = {item["role"]: item for item in result["run"]["inputs"]}
    assert roles["cues_track"] == {"role": "cues_track", "path": "track.json", "sha256": hashlib.sha256(track.read_bytes()).hexdigest()}
    assert roles["cues_light_keys"]["sha256"] == hashlib.sha256(keys.read_bytes()).hexdigest()
    edited = _track(fps=24.0)
    edited["frames"][30]["camera"]["location"][0] += 3.0                      # その場で書き換えたトラック
    _write(track, edited)
    del config["cues"]
    config["clips"][0]["cues"] = {"track": "track.json"}                     # クリップごとの cues
    again, _ = run.run(_write(tmp_path / "cues.json", config), tmp_path / "clip")
    after = [item for item in again["run"]["inputs"] if item["role"] == "cues_track"]
    assert after == [{"role": "cues_track", "path": "track.json", "sha256": hashlib.sha256(track.read_bytes()).hexdigest()}]
    assert after[0]["sha256"] != roles["cues_track"]["sha256"]                # 書き換えたトラックは、記録の sha256 で見分けられる


def test_cues_are_refused_with_a_dance_spec(dance_root, tmp_path):
    config = {"schema_version": "inspect/0.1", "dance_spec": str(dance_root / "spec.json"), "cues": {"cuts": [1.0]}}
    with pytest.raises(PipelineError, match="cues は clips"):
        inspect_config.load(_write(tmp_path / "inspect.json", config))
