"""`mv gate timeline`: 透過の連番から、字が見えているコマと箱を α で実測して timeline を書く。合成の連番（numpy の箱）で確かめる。"""
import json

import numpy as np
import pytest
from fx_synth import run_cli
from PIL import Image

from mv_pipeline.gate import checks as gate
from mv_pipeline.gate import frames_timeline
from mv_pipeline.io import PipelineError, atomic_json, read_json

W, H, FPS, N = 64, 96, 24, 48
LINE_A = {"id": "lyric-001", "text": "one", "display": "one", "sung": True, "start_frame": 6, "end_frame": 18, "timing_source": "manual",
          "review_status": "confirmed", "confidence": None}
LINE_B = {"id": "lyric-002", "text": "two", "display": "two", "sung": True, "start_frame": 20, "end_frame": 40, "timing_source": "manual",
          "review_status": "confirmed", "confidence": None}
BOX_A, BOX_B = (8, 10, 40, 20), (8, 60, 56, 80)      # 画素 [x0, y0, x1, y1)


def _props(path, lines=(LINE_A, LINE_B), **extra):
    atomic_json(path, {"schema_version": "remotion-props/0.1", "fps": FPS, "width": W, "height": H, "duration_frames": N, "lines": list(lines),
                       "untimed": [], "beats": {"frames": [0, 12, 24, 36], "downbeat_frames": [0, 24], "bpm": 120, "source": {"kind": "beats_per_bar"}},
                       "scenes": None, "show_lyrics": True, "sources": {"analysis_sha256": "0" * 64, "audio_sha256": "0" * 64}, **extra})


def _sequence(folder, drawings, count=N, alpha=200):
    """drawings: [(最初のコマ, 最後のコマ + 1, 画素の箱)]。箱の外は α 0 の黒。"""
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        rgba = np.zeros((H, W, 4), np.uint8)
        for first, last, (x0, y0, x1, y1) in drawings:
            if first <= i < last:
                rgba[y0:y1, x0:x1] = (255, 255, 255, alpha)
        Image.fromarray(rgba, "RGBA").save(folder / f"f_{i:04d}.png")


def test_separate_sequences_per_line_measure_boxes_and_frames_exactly(tmp_path):
    _props(tmp_path / "props.json")
    _sequence(tmp_path / "a", [(6, 18, BOX_A)])
    _sequence(tmp_path / "b", [(21, 40, BOX_B)])          # 1 コマ遅く出る
    timeline, measured = frames_timeline.build([f"{tmp_path / 'a'}:lyric-001", f"{tmp_path / 'b'}:lyric-002"], tmp_path / "props.json", "band")
    assert timeline["schema_version"] == "dance-timeline/0.1" and timeline["fps"] == FPS and timeline["duration_s"] == N / FPS
    assert timeline["lyric_fade"] == "inside" and timeline["interval"]["frames"] == N
    one, two = timeline["lyrics"]
    assert one["box_px"] == list(BOX_A) and one["box"] == [round(v, 5) for v in (8 / W, 10 / H, 40 / W, 20 / H)]
    assert (one["start"], one["end"]) == (6 / FPS, 18 / FPS) and (two["start"], two["end"]) == (21 / FPS, 40 / FPS)
    assert one["layer"] == "band" and one["exclusive"] is True and one["fade_out_s"] == 0.0 and one["lyric_id"] == "lyric-001"
    assert timeline["beats"] == {"beats": [0.0, 0.5, 1.0, 1.5], "downbeats": [0.0, 1.0]} and timeline["flashes"] == []
    rows = {row["id"]: row for row in measured["lines"]}
    assert rows["lyric-001"]["diff_frames"] == {"start": 0, "end": 0} and rows["lyric-001"]["note"] == "宣言と一致"
    assert rows["lyric-002"]["diff_frames"] == {"start": 1, "end": 0} and rows["lyric-002"]["note"] == "開始が 1 コマ遅い"
    assert rows["lyric-002"]["measured"] == [21, 40] and rows["lyric-002"]["visible_frames"] == 19
    assert len(timeline["measured"]["sources"]["frames"][0]["sha256"]) == N
    gate._validate_timeline(timeline)       # mv gate check が読める形


def test_one_sequence_with_hints_and_a_line_that_never_appears(tmp_path):
    _props(tmp_path / "props.json", flash_frames=[12, 36])
    _sequence(tmp_path / "all", [(6, 18, BOX_A), (20, 42, BOX_B)])   # 2 行目は 2 コマ長い
    hints = tmp_path / "hints.json"
    atomic_json(hints, {"lyric-001": [0, 0, 1, 0.5], "lyric-002": [0, 0.5, 1, 1]})
    timeline, measured = frames_timeline.build([str(tmp_path / "all")], tmp_path / "props.json", "band", hints_path=hints)
    rows = {row["id"]: row for row in measured["lines"]}
    assert rows["lyric-001"]["note"] == "宣言と一致" and rows["lyric-002"]["note"] == "終了が 2 コマ遅い"
    assert timeline["lyrics"][1]["box_px"] == list(BOX_B) and timeline["flashes"] == [0.5, 1.5]
    assert timeline["measured"]["sources"]["hints"]["path"] == str(hints)
    with pytest.raises(PipelineError, match="--hints"):
        frames_timeline.build([str(tmp_path / "all")], tmp_path / "props.json", "band")
    _props(tmp_path / "three.json", lines=(LINE_A, LINE_B, {**LINE_B, "id": "lyric-003", "text": "three", "start_frame": 42, "end_frame": 46}))
    atomic_json(hints, {"lyric-001": [0, 0, 1, 0.5], "lyric-002": [0, 0.5, 1, 1], "lyric-003": [0, 0.9, 1, 1]})
    _, measured = frames_timeline.build([str(tmp_path / "all")], tmp_path / "three.json", "band", hints_path=hints)
    rows = {row["id"]: row for row in measured["lines"]}
    assert rows["lyric-003"]["measured"] is None and "見えているコマがありません" in rows["lyric-003"]["note"]


def test_alpha_threshold_gaps_and_mismatches(tmp_path):
    _props(tmp_path / "props.json", lines=(LINE_A,))
    _sequence(tmp_path / "faint", [(6, 10, BOX_A), (12, 18, BOX_A)], alpha=40)   # 薄い字。途中 2 コマ抜け
    timeline, measured = frames_timeline.build([str(tmp_path / "faint")], tmp_path / "props.json", "band", threshold=30)
    assert measured["lines"][0]["measured"] == [6, 18] and measured["lines"][0]["gap_frames"] == 2
    assert "途中で見えないコマ 2" in measured["lines"][0]["note"]
    _, measured = frames_timeline.build([str(tmp_path / "faint")], tmp_path / "props.json", "band")      # 既定 64 では見えない
    assert measured["lines"][0]["measured"] is None
    _sequence(tmp_path / "short", [(6, 18, BOX_A)], count=N - 1)
    _props(tmp_path / "two.json")
    with pytest.raises(PipelineError, match="コマ数が合いません"):
        frames_timeline.build([f"{tmp_path / 'faint'}:lyric-001", f"{tmp_path / 'short'}:lyric-002"], tmp_path / "two.json", "band")
    with pytest.raises(PipelineError, match="2 つの連番に"):
        frames_timeline.build([f"{tmp_path / 'faint'}:lyric-001", f"{tmp_path / 'faint'}:lyric-001"], tmp_path / "two.json", "band")
    with pytest.raises(PipelineError, match="props にありません"):
        frames_timeline.build([f"{tmp_path / 'faint'}:lyric-009"], tmp_path / "props.json", "band")
    (tmp_path / "bad.json").write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    with pytest.raises(PipelineError, match="remotion-props/0.1"):
        frames_timeline.build([str(tmp_path / "faint")], tmp_path / "bad.json", "band")


def test_frames_argument_parsing_keeps_windows_drives_and_paths():
    assert frames_timeline.parse_frames_arg("out/frames:lyric-001") == (frames_timeline.Path("out/frames"), "lyric-001")
    assert frames_timeline.parse_frames_arg("out/frames") == (frames_timeline.Path("out/frames"), None)
    assert frames_timeline.parse_frames_arg("C:\\work\\frames") == (frames_timeline.Path("C:\\work\\frames"), None)
    assert frames_timeline.parse_frames_arg("C:\\work\\frames:lyric-002") == (frames_timeline.Path("C:\\work\\frames"), "lyric-002")


def test_cli_writes_the_timeline_and_the_measured_report_and_gate_check_reads_it(tmp_path):
    _props(tmp_path / "props.json")
    _sequence(tmp_path / "a", [(6, 18, BOX_A)])
    _sequence(tmp_path / "b", [(20, 40, BOX_B)])
    out = tmp_path / "timeline.json"
    code, result, err = run_cli("gate", "timeline", "--frames", f"{tmp_path / 'a'}:lyric-001", "--frames", f"{tmp_path / 'b'}:lyric-002",
                                "--lyrics", tmp_path / "props.json", "--layer", "band", "--out", out)
    assert code == 0, err
    assert result["differing"] == [] and result["lines"] == 2 and result["measured"] == str(tmp_path / "timeline_measured.json")
    atomic_json(tmp_path / "gate.json", {"timeline": "timeline.json"})
    report, _ = gate.run(tmp_path / "gate.json")
    by_name = {c["check"]: c for c in report["checks"]}
    assert by_name["字の重なり"]["status"] == "pass" and by_name["UI 枠"]["status"] == "pass" and by_name["拍"]["status"] == "pass"
    assert read_json(out)["measured"]["tool"] == "mv gate timeline"
