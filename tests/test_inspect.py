"""mv inspect（生成動画の検品）のテスト。素材はすべてプログラムで描いた合成物（inspection/demo.py）。

合成素材で決めたしきい値が合成素材で働くことを確かめるだけで、実作品の動画での見落とし・誤検出の少なさは保証しない。
"""
import hashlib
import importlib.util
import json
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
    with pytest.raises(PipelineError, match="終わりませんでした"):
        run.run(small_demo / "inspect.json", tmp_path / "out", vlm_config=path)
    log = (tmp_path / "out" / "vlm" / "adapter.log").read_text(encoding="utf-8")
    assert log.startswith("timeout after 1") and "b'" not in log


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


class _Message:
    def __init__(self, text, stop_reason="end_turn"):
        self.content = [_Block(text)] if text is not None else []
        self.stop_reason, self.model, self.stop_details = stop_reason, "served-model", None


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
    replies = [_Message(first)] + [_Message(None, "refusal")] * (len(planned) - 1)
    client = _FakeClient(replies)
    findings, unreviewed, log, served = module.review(client, request, request_path.parent, module.DEFAULT_MODEL, "high", 3, say=lambda m: None)
    assert findings == [{"frame_id": ids[0], "category": "shape", "description": "腕の先に塊", "box": [0.7, 0.0, 1.0, 0.4]}]
    assert {item["frame_id"] for item in unreviewed} == set(ids[3:]) and served == "served-model"
    call = client.calls[0]
    assert call["model"] == module.DEFAULT_MODEL and call["fallbacks"] == "default" and call["betas"] == [module.FALLBACK_BETA]
    assert call["output_config"]["format"]["schema"]["properties"]["findings"]["items"]["properties"]["frame_id"]["enum"] == ids[:3]
    assert len(log) == len(planned) and log[1]["error"].startswith("拒否")


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
