import json
import wave

import numpy as np
import pytest
from fx_synth import run_cli

from mv_pipeline.gate import checks as gate
from mv_pipeline.gate import lines as lines_table

TH = gate.THRESHOLDS


def timeline(**changes):
    base = {"fps": 24, "duration_s": 6.0, "size": [1080, 1920], "seams": [3.0], "flashes": [3.0],
            "ui_regions": [{"name": "right", "box": [0.86, 0.45, 1.0, 0.88]}],
            "lyrics": [{"layer": "back", "text": "HOLD", "start": 0.5, "end": 2.9, "fade_in_s": 0.04, "fade_out_s": 0.08, "box": [0.2, 0.2, 0.8, 0.3]},
                       {"layer": "back", "text": "LIGHT", "start": 3.0, "end": 5.8, "fade_in_s": 0.04, "fade_out_s": 0.08, "box": [0.2, 0.2, 0.8, 0.3]},
                       {"layer": "band", "text": "one", "start": 0.5, "end": 2.9, "fade_in_s": 0.04, "fade_out_s": 0.08, "box": [0.2, 0.07, 0.8, 0.12]},
                       {"layer": "band", "text": "two", "start": 3.0, "end": 5.8, "fade_in_s": 0.04, "fade_out_s": 0.08, "box": [0.2, 0.07, 0.8, 0.12]}]}
    return {**base, **changes}


@pytest.mark.parametrize("sung, status", [(0.6, "pass"), (0.5, "pass"), (0.7, "pass"), (0.45, "fail"), (0.8, "fail"), (2.0, "fail")])
def test_lyric_must_lead_the_singing_by_zero_to_point_two_seconds(sung, status):
    measured = [{"start": sung}, {"start": 3.1}]
    assert gate.check_lyric_time(timeline(), measured, "band", TH)["status"] == status


def test_the_inclusive_boundary_survives_floating_point_subtraction():
    line = timeline()
    line["lyrics"][2]["start"] = 0.6
    assert 0.8 - 0.6 > 0.2
    assert gate.check_lyric_time(line, [{"start": 0.8}, {"start": 3.1}], "band", TH)["status"] == "pass"


def test_lyric_time_fails_on_a_line_count_mismatch_and_warns_on_unmeasured_lines():
    assert gate.check_lyric_time(timeline(), [{"start": 0.6}], "band", TH)["status"] == "fail"
    measured = [{"start": 0.6, "method": "not_found"}, {"start": 3.1}]
    assert gate.check_lyric_time(timeline(), measured, "band", TH)["status"] == "warn"


def test_estimates_are_never_treated_as_measurements():
    estimates = [{"start": 0.6, "method": "estimate"}, {"start": 3.1, "method": "estimate"}]
    assert gate.check_lyric_time(timeline(), estimates, "band", TH)["status"] == "skip"
    partly = [{"start": 0.6, "method": "vocals_onset"}, {"start": 9.0, "method": "estimate"}]
    check = gate.check_lyric_time(timeline(), partly, "band", TH)
    assert check["status"] == "warn" and "1 行を照合" in check["summary"]


@pytest.mark.parametrize("statuses, overall", [(["pass"], "pass"), (["pass", "warn"], "pass_with_warnings"),
                                               (["warn", "skip"], "pass_with_skips"), (["skip", "fail"], "fail")])
def test_overall_never_hides_a_warning(tmp_path, monkeypatch, statuses, overall):
    (tmp_path / "timeline.json").write_text(json.dumps(timeline()), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json"}), encoding="utf-8")
    fixed = iter(statuses + ["pass"] * 9)
    for name in ("check_lyric_time", "check_confirmed", "check_faces", "check_ui", "check_jitter",
                 "check_seams", "check_hook_overlap", "check_beats", "check_export"):
        monkeypatch.setattr(gate, name, lambda *args: gate.result("x", next(fixed), "s"))
    assert gate.run(tmp_path / "gate.json")[0]["overall"] == overall


def test_missing_material_is_a_skip_and_never_a_pass():
    assert gate.check_lyric_time(timeline(), None, "band", TH)["status"] == "skip"
    assert gate.check_faces(timeline(), None)["status"] == "skip"
    assert gate.check_jitter(None, TH)["status"] == "skip"
    assert gate.check_seams(timeline(), None, TH)["status"] == "skip"
    assert gate.check_beats(timeline(), None, TH)["status"] == "skip"
    assert gate.check_export(timeline(), None, None, None, TH)["status"] == "skip"


def test_confirmed_time_must_not_move():
    assert gate.check_confirmed(timeline(), [{"text": "two", "start": 3.0}], TH)["status"] == "pass"
    assert gate.check_confirmed(timeline(), [{"text": "two", "start": 3.1}], TH)["status"] == "fail"
    assert gate.check_confirmed(timeline(), [{"text": "gone", "start": 3.0}], TH)["status"] == "fail"


def test_band_over_the_main_face_fails_but_the_back_layer_and_others_do_not():
    face = {"start": 0.0, "end": 6.0, "box": [0.4, 0.05, 0.6, 0.15], "role": "main"}
    assert gate.check_faces(timeline(), [face])["status"] == "fail"
    assert len(gate.check_faces(timeline(), [face])["problems"]) == 2
    assert gate.check_faces(timeline(), [{**face, "role": "other"}])["status"] == "pass"
    assert gate.check_faces(timeline(), [{**face, "box": [0.4, 0.2, 0.6, 0.3]}])["status"] == "pass"   # back は人物の後ろ


def test_overlap_only_during_the_fade_is_allowed():
    face = {"start": 2.85, "end": 2.95, "box": [0.4, 0.05, 0.6, 0.15]}
    assert gate.check_faces(timeline(), [face])["status"] == "pass"


def test_text_in_a_ui_region_fails_with_a_frame_count():
    moved = timeline()
    moved["lyrics"][0]["box"] = [0.8, 0.5, 0.95, 0.6]
    check = gate.check_ui(moved)
    assert check["status"] == "fail" and "58" in check["summary"]
    assert gate.check_ui(timeline())["status"] == "pass"


def test_partial_render_is_checked_against_its_interval(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "v.mp4"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (64, 64))
    for i in range(48):
        writer.write(np.full((64, 64, 3), i, np.uint8))
    writer.release()
    partial = timeline(interval={"start": 1.0, "duration": 2.0, "frames": 48})
    assert gate.check_export(partial, path, path, None, TH)["status"] == "pass"
    assert gate.check_export(timeline(), path, path, None, TH)["status"] == "fail"


def test_ssim_of_a_video_with_itself_is_one_and_sizes_may_differ(tmp_path):
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(1)
    frames = [rng.integers(0, 255, (64, 64, 3), np.uint8) for _ in range(24)]
    paths = {}
    for name, size in (("a", (64, 64)), ("b", (32, 32))):
        paths[name] = tmp_path / f"{name}.mp4"
        writer = cv2.VideoWriter(str(paths[name]), cv2.VideoWriter_fourcc(*"mp4v"), 24, size)
        for frame in frames:
            writer.write(cv2.resize(frame, size))
        writer.release()
    assert gate._size(paths["a"]) == (64, 64) and gate._size(paths["b"]) == (32, 32)
    assert gate._ssim(paths["a"], paths["a"]) == pytest.approx(1.0)
    assert 0 < gate._ssim(paths["b"], paths["a"]) < 1


def test_text_that_does_not_fit_fails_the_ui_check():
    moved = timeline()
    moved["lyrics"][0]["overflow"] = True
    check = gate.check_ui(moved)
    assert check["status"] == "fail" and "収まらず" in check["problems"][0]["detail"]


def test_jitter_catches_shake_but_not_smooth_motion():
    smooth = [[i / 24, 100 + 5.0 * i, 200.0] for i in range(48)]
    shaky = [[i / 24, 100 + 5.0 * i + (6 if i % 2 else -6), 200.0] for i in range(48)]
    assert gate.check_jitter({"flame": smooth}, TH)["status"] == "pass"
    assert gate.check_jitter({"flame": shaky}, TH)["status"] == "fail"


def test_tracks_too_short_to_measure_are_not_passed():
    smooth = [[i / 24, 100 + 5.0 * i, 200.0] for i in range(48)]
    assert gate.check_jitter({"main": [[0, 0, 0]]}, TH)["status"] == "skip"
    check = gate.check_jitter({"flame": smooth, "main": [[0, 0, 0]]}, TH)
    assert check["status"] == "warn" and "main" in check["summary"]


def test_big_pose_jump_at_a_seam_is_a_proposal():
    poses = [{"t": 3.0 - 1 / 24, "points": [[0.5, 0.5]]}, {"t": 3.0, "points": [[0.6, 0.5]]}]
    assert gate.check_seams(timeline(), poses, TH)["status"] == "warn"
    still = [{"t": 3.0 - 1 / 24, "points": [[0.5, 0.5]]}, {"t": 3.0, "points": [[0.51, 0.5]]}]
    assert gate.check_seams(timeline(), still, TH)["status"] == "pass"
    assert gate.check_seams(timeline(seams=[]), None, TH)["status"] == "pass"


def test_empty_pose_points_are_not_a_zero_difference():
    poses = [{"t": 3.0 - 1 / 24, "points": []}, {"t": 3.0, "points": []}]
    assert gate.check_seams(timeline(), poses, TH)["status"] == "warn"


@pytest.mark.parametrize("times", [[0.0], [3.0], [2.9], [2.0, 3.0], [2.9, 4.0]])
def test_poses_must_exist_on_both_sides_of_the_seam(times):
    poses = [{"t": t, "points": [[0.5, 0.5]]} for t in times]
    check = gate.check_seams(timeline(), poses, TH)
    assert check["status"] == "warn" and "見ていません" in check["problems"][0]["detail"]


def test_hooks_may_not_overlap_for_long():
    overlapping = timeline()
    overlapping["lyrics"][0]["end"] = 3.2
    assert gate.check_hook_overlap(overlapping, TH)["status"] == "fail"
    overlapping["lyrics"][0]["end"] = 3.05
    assert gate.check_hook_overlap(overlapping, TH)["status"] == "pass"


def test_overlap_uses_the_earlier_end_and_every_pair():
    hooks = timeline()
    hooks["lyrics"][0].update(start=0.0, end=5.0)
    hooks["lyrics"][1].update(start=1.0, end=1.08)
    assert gate.check_hook_overlap(hooks, TH)["status"] == "pass"
    hooks["lyrics"].append({**hooks["lyrics"][1], "text": "LATE", "start": 2.0, "end": 4.0})
    check = gate.check_hook_overlap(hooks, TH)    # 隣り合わない 1 行目と 3 行目の重なりも見る
    assert check["status"] == "fail" and "HOLD / LATE" in check["problems"][0]["text"]


def test_a_decoding_failure_fails_the_export_check_even_with_the_right_frame_count(tmp_path, monkeypatch):
    path = tmp_path / "v.mp4"
    path.write_bytes(b"x")
    monkeypatch.setattr(gate, "decode", lambda *args: {"ok": False, "frames": 144, "seconds": 6.0, "error": "bad nal"})
    monkeypatch.setattr(gate, "_info", lambda *args: (6.0, True))
    check = gate.check_export(timeline(), path, None, None, TH)
    assert check["status"] == "fail" and "bad nal" in check["problems"][0]["detail"]


def test_events_must_land_on_a_downbeat_within_one_frame():
    assert gate.check_beats(timeline(), {"downbeats": [1.0, 3.03, 5.0]}, TH)["status"] == "pass"
    assert gate.check_beats(timeline(), {"downbeats": [1.0, 3.2, 5.0]}, TH)["status"] == "fail"
    assert gate.check_beats(timeline(), {"beats": [3.0]}, TH)["status"] == "pass"
    assert gate.check_beats(timeline(), {"beats_s": [3.0]}, TH)["status"] == "pass"
    assert gate.check_beats(timeline(), {"beats_s": [1.0]}, TH)["status"] == "fail"


def test_scope_keeps_only_what_appears_in_the_rendered_interval():
    partial = timeline(interval={"start": 0.0, "duration": 2.0, "frames": 48})
    scoped = gate._scoped(partial)
    assert [ln["text"] for ln in scoped["lyrics"]] == ["HOLD", "one"] and scoped["lyrics"][0]["end"] == 2.0
    assert scoped["seams"] == [] and scoped["flashes"] == []
    whole = gate._scoped(timeline())["lyrics"]
    assert [(ln["start"], ln["end"]) for ln in whole] == [(ln["start"], ln["end"]) for ln in timeline()["lyrics"]]


def test_a_lyric_outside_the_interval_does_not_fail_a_partial_render(tmp_path):
    moved = timeline(interval={"start": 0.0, "duration": 2.0, "frames": 48})
    moved["lyrics"][1]["box"] = [0.8, 0.5, 0.95, 0.6]     # 3 秒からの字が UI 枠に入る
    assert gate.check_ui(moved)["status"] == "fail" and gate.check_ui(gate._scoped(moved))["status"] == "pass"


def test_face_check_keeps_the_original_fade_when_the_interval_cuts_a_lyric():
    """字が出ている途中で区間が切れても、切れ目に出入りがあることにしない。"""
    line = {"layer": "band", "text": "x", "start": 0.0, "end": 5.0, "fade_in_s": 0.5, "fade_out_s": 0.5, "box": [0.2, 0.07, 0.8, 0.12]}
    partial = timeline(lyrics=[line], interval={"start": 2.0, "duration": 0.4, "frames": 10})
    face = {"start": 0.0, "end": 5.0, "box": [0.4, 0.05, 0.6, 0.15]}
    assert gate.check_faces(gate._scoped(partial), [face])["status"] == "fail"


def test_jitter_only_looks_at_samples_inside_the_interval():
    still = [[i / 24, 100.0, 200.0] for i in range(48, 72)]
    shaky = [[i / 24, 100.0 + (9 if i % 2 else -9), 200.0] for i in range(24)]
    tracks = {"flame": shaky + still}
    assert gate.check_jitter(tracks, TH)["status"] == "fail"
    assert gate.check_jitter(tracks, TH, (2.0, 3.0))["status"] == "pass"
    assert gate.check_jitter(tracks, TH, (5.0, 6.0))["status"] == "skip"


def test_ui_frames_are_counted_inside_the_interval_only():
    moved = timeline(interval={"start": 1.0, "duration": 1.0, "frames": 24})
    moved["lyrics"][0]["box"] = [0.8, 0.5, 0.95, 0.6]
    assert "24" in gate.check_ui(gate._scoped(moved))["summary"]


def test_lyric_time_on_a_partial_render_keeps_the_pairing_of_the_whole_song():
    partial = timeline(interval={"start": 3.0, "duration": 2.0, "frames": 48})
    measured = [{"start": 99.0}, {"start": 3.1}]    # 区間の外の行のずれは見ない。2 行目は曲全体の 2 行目と対応する
    check = gate.check_lyric_time(partial, measured, "band", TH)
    assert check["status"] == "pass" and "1 行を照合" in check["summary"]


def test_end_to_end_report_on_a_rendered_video(tmp_path):
    cv2 = pytest.importorskip("cv2")
    path = tmp_path / "v.mp4"
    frames = [np.full((64, 64, 3), (i * 3) % 255, np.uint8) for i in range(24)]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), 24, (64, 64))
    for frame in frames:
        writer.write(frame)
    writer.release()
    (tmp_path / "timeline.json").write_text(json.dumps(timeline(duration_s=1.0)), encoding="utf-8")
    (tmp_path / "measured.json").write_text(json.dumps([{"start": 3.0}, {"start": 3.1}]), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "video": "v.mp4", "master": "v.mp4", "measured_lines": "measured.json"}), encoding="utf-8")
    report, out = gate.run(tmp_path / "gate.json")
    by_name = {c["check"]: c for c in report["checks"]}
    assert report["overall"] == "fail" and by_name["歌詞の時刻"]["status"] == "fail" and by_name["書き出し"]["status"] == "pass"
    assert (out / "gate_report.json").is_file() and "品質ゲート" in (out / "gate_report.md").read_text(encoding="utf-8")
    assert by_name["歌詞の時刻"]["problems"][0].get("still")
    assert len(report["checks"]) == 9


def _vocals(path, onsets, rate=16000, seconds=6.0):
    samples = np.zeros(int(seconds * rate), np.float32)
    for onset in onsets:
        start, end = int(onset * rate), int((onset + 0.6) * rate)
        samples[start:end] = 0.3 * np.sin(2 * np.pi * 440 * np.arange(end - start) / rate)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((samples * 32767).astype(np.int16).tobytes())


def test_json_lines_skip_empty_end_markers(tmp_path):
    path = tmp_path / "a.json"
    path.write_text(json.dumps([{"start": 1, "text": "hello"}, {"start": 2, "text": ""}, {"start": 3, "line": "　"}, {"start": 4}]), encoding="utf-8")
    assert lines_table.read_lines(path) == [{"text": "hello", "start": 1.0}]


def test_lrc_is_parsed_and_the_end_marker_skipped(tmp_path):
    path = tmp_path / "a.lrc"
    path.write_text("[00:01.50]一行目\n[01:02.25]二行目\n[01:10.00]\n", encoding="utf-8")
    assert lines_table.read_lines(path) == [{"text": "一行目", "start": 1.5}, {"text": "二行目", "start": 62.25}]


def test_onset_is_found_near_the_estimate(tmp_path):
    pytest.importorskip("soundfile")
    path = tmp_path / "v.wav"
    _vocals(path, [1.0, 3.0])
    rows = lines_table.build([{"text": "a", "start": 1.3}, {"text": "b", "start": 2.7}], path, 0.8, 0.0)
    assert [r["method"] for r in rows] == ["vocals_onset", "vocals_onset"]
    assert rows[0]["start"] == pytest.approx(1.0, abs=0.04) and rows[1]["start"] == pytest.approx(3.0, abs=0.04)


def test_onset_is_found_at_a_shallow_dip_without_silence(tmp_path):
    """実曲の行間は無音にならない（残響で -20dB 前後の谷）。谷からの立ち上がりで見つける。"""
    pytest.importorskip("soundfile")
    rate = 16000
    samples = 0.3 * np.sin(2 * np.pi * 440 * np.arange(4 * rate) / rate)
    samples[int(1.8 * rate):int(2.0 * rate)] *= 0.1      # -20dB の谷
    path = tmp_path / "v.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((samples * 32767).astype(np.int16).tobytes())
    rows = lines_table.build([{"text": "a", "start": 2.2}], path, 0.6, 0.0)
    assert rows[0]["method"] == "vocals_onset" and rows[0]["start"] == pytest.approx(2.0, abs=0.05) and rows[0]["rise_db"] > 8


def test_no_onset_in_the_window_keeps_the_estimate_and_says_so(tmp_path):
    pytest.importorskip("soundfile")
    path = tmp_path / "v.wav"
    _vocals(path, [1.0])
    rows = lines_table.build([{"text": "a", "start": 4.0}], path, 0.5, 0.0)
    assert rows[0]["method"] == "not_found" and rows[0]["start"] == 4.0


def test_without_vocals_the_table_is_marked_as_an_estimate(tmp_path):
    rows = lines_table.build([{"text": "a", "start": 1.0}], None, 0.8, 0.5)
    assert rows[0]["method"] == "estimate" and rows[0]["start"] == 1.5
    lines_table.write(rows, tmp_path)
    assert json.loads((tmp_path / "lines_measured.json").read_text(encoding="utf-8"))[0]["method"] == "estimate"
    assert "確認" in (tmp_path / "lines_table.csv").read_text(encoding="utf-8-sig")


def test_cli_check_fails_with_exit_code_1_and_reports_the_summary(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(timeline()), encoding="utf-8")
    (tmp_path / "measured.json").write_text(json.dumps([{"start": 3.0}, {"start": 3.1}]), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "measured_lines": "measured.json"}), encoding="utf-8")
    code, result, _ = run_cli("gate", "check", tmp_path / "gate.json")
    assert code == 1 and result["overall"] == "fail" and len(result["checks"]) == 9


def test_cli_check_with_nothing_wrong_is_pass_with_skips_not_pass(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(timeline()), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json"}), encoding="utf-8")
    code, result, _ = run_cli("gate", "check", tmp_path / "gate.json")
    assert code == 0 and result["overall"] == "pass_with_skips"


def test_cli_check_without_a_timeline_is_an_error(tmp_path):
    (tmp_path / "gate.json").write_text("{}", encoding="utf-8")
    code, _, err = run_cli("gate", "check", tmp_path / "gate.json")
    assert code == 1 and "timeline" in err


def test_cli_lines_writes_the_table(tmp_path):
    path = tmp_path / "a.lrc"
    path.write_text("[00:01.50]line\n", encoding="utf-8")
    code, result, err = run_cli("gate", "lines", path, tmp_path / "out")
    assert code == 0, err
    assert result["estimate"] == 1 and (tmp_path / "out" / "lines_table.csv").is_file()


# ---- timeline 0.2 ----------------------------------------------------------------

def test_lines_that_are_not_sung_are_not_counted():
    moved = timeline()
    moved["lyrics"].append({"layer": "band", "text": "TITLE", "start": 5.9, "end": 6.0, "sung": False, "box": [0.2, 0.07, 0.8, 0.12]})
    assert gate.check_lyric_time(moved, [{"start": 0.6}, {"start": 3.1}], "band", TH)["status"] == "pass"


def test_several_lyric_layers_can_be_checked():
    measured = [{"start": 0.6}, {"start": 3.1}, {"start": 0.6}, {"start": 3.1}]
    assert gate.check_lyric_time(timeline(), measured, ["back", "band"], TH)["status"] == "pass"


def test_a_confirmed_line_is_a_warning_not_a_failure():
    measured = [{"start": 1.5}, {"start": 3.1}]
    confirmed = [{"text": "one", "start": 0.5}]
    check = gate.check_lyric_time(timeline(), measured, "band", TH, confirmed)
    assert check["status"] == "warn" and "確定済み" in check["problems"][0]["detail"]
    assert gate.check_lyric_time(timeline(), measured, "band", TH)["status"] == "fail"
    strict = {**TH, "confirmed_overrides_measured": False}
    assert gate.check_lyric_time(timeline(), measured, "band", strict, confirmed)["status"] == "fail"


def test_a_confirmed_line_does_not_excuse_another_line():
    measured = [{"start": 1.5}, {"start": 9.0}]
    assert gate.check_lyric_time(timeline(), measured, "band", TH, [{"text": "one", "start": 0.5}])["status"] == "fail"


def test_every_exclusive_layer_is_checked_for_overlap():
    lines = timeline()["lyrics"]
    for line in lines:
        line["exclusive"] = line["layer"] == "band"
    lines[2]["end"] = 3.5
    lines[0]["end"] = 3.5
    check = gate.check_hook_overlap(timeline(lyrics=lines), TH)
    assert check["status"] == "fail" and check["problems"][0]["text"] == "one / two"
    for line in lines:
        line["exclusive"] = False
    assert gate.check_hook_overlap(timeline(lyrics=lines), TH)["status"] == "pass"


def test_timeline_01_still_only_checks_back_for_overlap():
    lines = timeline()["lyrics"]
    lines[2]["end"] = 3.5
    assert gate.check_hook_overlap(timeline(lyrics=lines), TH)["status"] == "pass"


def info(**changes):
    return timeline(seam_info=[{"t": 3.0, "from": 0, "to": 1, "kind": "cut", "beat_exempt": False}],
                    flash_info=[{"t": 3.0, "strength": 0.5, "decay_s": 0.1, "mode": "lerp_linear", "beat_exempt": False}], **changes)


def test_beats_come_from_the_timeline_when_the_config_has_none():
    assert gate.check_beats(info(beats={"beats": [3.0], "downbeats": []}), None, TH)["status"] == "pass"
    assert gate.check_beats(info(beats={"beats": [1.0], "downbeats": []}), None, TH)["status"] == "fail"
    assert gate.check_beats(info(), None, TH)["status"] == "skip"


def test_beat_exempt_events_are_counted_but_never_fail():
    moved = info(beats={"beats": [1.0], "downbeats": []})
    moved["seam_info"][0]["beat_exempt"] = True
    moved["flash_info"][0]["beat_exempt"] = True
    check = gate.check_beats(moved, None, TH)
    assert check["status"] == "pass" and "対象外（意図）2 件" in check["summary"]


def test_scope_filters_the_02_events():
    partial = info(interval={"start": 0.0, "duration": 2.0, "frames": 48})
    scoped = gate._scoped(partial)
    assert scoped["seam_info"] == [] and scoped["flash_info"] == []


def test_scope_keeps_the_events_of_a_01_timeline():
    """0.1 の timeline には seam_info が無い。空の欄を足すと拍の検査が何も見なくなる。"""
    scoped = gate._scoped(timeline())
    assert "seam_info" not in scoped and "flash_info" not in scoped
    assert gate.check_beats(scoped, {"beats": [1.0]}, TH)["status"] == "fail"


def test_a_seam_side_is_decided_on_frames_not_on_rounded_seconds():
    """4 桁に丸めた時刻は切れ目の少し前や後に落ちる。コマに丸めれば正しい側になる。"""
    fps = 29.97
    seam = 100 / fps
    before, after = round(99 / fps, 4), round(100 / fps, 4)
    moved = timeline(fps=fps, seams=[seam])
    assert after < seam or before < seam
    poses = [{"t": before, "points": [[0.5, 0.5]]}, {"t": after, "points": [[0.51, 0.5]]}]
    assert gate.check_seams(moved, poses, TH)["status"] == "pass"
    poses = [{"t": seam + 1e-5 - 1 / fps, "points": [[0.5, 0.5]]}, {"t": seam - 1e-5, "points": [[0.51, 0.5]]}]
    assert gate.check_seams(moved, poses, TH)["status"] == "pass"


def test_a_camera_seam_next_to_the_cut_is_mentioned():
    poses = [{"t": 3.0 - 1 / 24, "points": [[0.5, 0.5]]}, {"t": 3.0, "points": [[0.6, 0.5]]}]
    moved = timeline(camera_seams=[{"t": 3.0 + 1 / 24, "zoom": 1.3, "rotate_deg": 8.0}])
    check = gate.check_seams(moved, poses, TH)
    assert check["status"] == "warn" and "camera.seams あり" in check["problems"][0]["detail"] and "1.3" in check["problems"][0]["detail"]
    far = timeline(camera_seams=[{"t": 3.5, "zoom": 1.3, "rotate_deg": 8.0}])
    assert "camera.seams あり" not in gate.check_seams(far, poses, TH)["problems"][0]["detail"]


def test_faces_skip_every_layer_behind_the_performer():
    moved = timeline(layers=[{"name": "back", "z": "front", "exclusive": True, "sung": True, "follow_camera": False},
                             {"name": "band", "z": "behind", "exclusive": True, "sung": True, "follow_camera": False}])
    face = {"start": 0.0, "end": 6.0, "box": [0.4, 0.05, 0.6, 0.15]}
    assert gate.check_faces(moved, [face])["status"] == "pass"
    assert gate.check_faces(moved, [{**face, "box": [0.4, 0.2, 0.6, 0.3]}])["status"] == "fail"


def test_estimated_faces_never_fail_and_say_so():
    face = {"start": 0.0, "end": 6.0, "box": [0.4, 0.05, 0.6, 0.15], "estimated": True}
    check = gate.check_faces(timeline(), [face])
    assert check["status"] == "warn" and "推定" in check["summary"] and "推定" in check["problems"][0]["detail"]


def test_face_times_are_rounded_to_frames():
    line = {"layer": "band", "text": "x", "start": 1.0, "end": 2.0, "fade_in_s": 0, "fade_out_s": 0, "box": [0.2, 0.07, 0.8, 0.12]}
    face = {"start": 2.0 - 0.01, "end": 2.5, "box": [0.4, 0.05, 0.6, 0.15]}      # 丸めると 2.0 秒。字は 2.0 秒で消える
    assert gate.check_faces(timeline(lyrics=[line]), [face])["status"] == "pass"


def test_a_timeline_of_the_wrong_shape_stops_with_a_reason(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps({"fps": 24}), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json"}), encoding="utf-8")
    code, _, err = run_cli("gate", "check", tmp_path / "gate.json")
    assert code == 1 and "duration_s" in err


def test_unknown_timeline_fields_are_ignored(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(timeline(x_renderer="mine")), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json"}), encoding="utf-8")
    assert run_cli("gate", "check", tmp_path / "gate.json")[0] == 0


def test_faces_auto_needs_a_spec(tmp_path):
    (tmp_path / "timeline.json").write_text(json.dumps(timeline()), encoding="utf-8")
    (tmp_path / "gate.json").write_text(json.dumps({"timeline": "timeline.json", "faces": "auto"}), encoding="utf-8")
    code, _, err = run_cli("gate", "check", tmp_path / "gate.json")
    assert code == 1 and "spec" in err


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    pytest.importorskip("cv2")
    root = tmp_path_factory.mktemp("gate02")
    code, _, err = run_cli("dance", "demo", root, "--width", "270", "--height", "480", "--duration", "6", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--quiet")
    assert code == 0, err
    return root, result["run_id"]


def test_the_02_demo_has_no_unintended_failure(demo_run):
    root, run_id = demo_run
    config = {"timeline": f"runs/{run_id}/timeline.json", "video": f"runs/{run_id}/dance.mp4", "faces": "auto", "spec": "spec.json"}
    (root / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    report, _ = gate.run(root / "gate.json")
    by_name = {c["check"]: c for c in report["checks"]}
    assert report["overall"] == "pass_with_skips", by_name
    assert by_name["拍"]["status"] == "pass" and "対象外（意図）" in by_name["拍"]["summary"]
    assert by_name["字と顔"]["status"] == "pass" and "推定" in by_name["字と顔"]["summary"]


def test_faces_auto_finds_a_head_near_the_top_of_the_performer(demo_run):
    root, run_id = demo_run
    record = json.loads((root / "runs" / run_id / "timeline.json").read_text(encoding="utf-8"))
    faces = gate.estimate_faces(root / "spec.json", record)
    assert len(faces) == 36 and all(face["estimated"] and face["role"] == "main" for face in faces)
    x0, y0, x1, y1 = faces[3]["box"]
    assert 0.3 < (x0 + x1) / 2 < 0.7 and y1 - y0 < 0.15 and y0 < 0.45


def test_gate_reads_the_01_timeline_of_a_real_render(tmp_path):
    pytest.importorskip("cv2")
    code, _, err = run_cli("dance", "demo", tmp_path, "--width", "180", "--height", "320", "--duration", "2", "--fps", "12", "--schema", "0.1")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", tmp_path / "spec.json", "--quiet")
    assert code == 0, err
    config = {"timeline": f"dance_runs/{result['run_id']}/timeline.json", "video": f"dance_runs/{result['run_id']}/dance.mp4"}
    (tmp_path / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    assert gate.run(tmp_path / "gate.json")[0]["overall"] != "fail"


def test_nearby_lines_do_not_share_an_onset(tmp_path):
    pytest.importorskip("soundfile")
    path = tmp_path / "v.wav"
    _vocals(path, [1.0, 4.0])
    rows = lines_table.build([{"text": "a", "start": 1.1}, {"text": "b", "start": 1.3}], path, 0.8, 0.0)
    assert rows[0]["method"] == "vocals_onset" and rows[0]["start"] == pytest.approx(1.0, abs=0.04)
    assert rows[1]["method"] == "not_found" and rows[1]["start"] == 1.3


def test_the_window_stops_at_the_midpoint_to_the_neighbours(tmp_path):
    pytest.importorskip("soundfile")
    path = tmp_path / "v.wav"
    _vocals(path, [1.0, 2.6])
    rows = lines_table.build([{"text": "a", "start": 1.2}, {"text": "b", "start": 2.2}], path, 1.5, 0.0)
    assert rows[0]["start"] == pytest.approx(1.0, abs=0.04) and rows[1]["start"] == pytest.approx(2.6, abs=0.04)


def test_a_close_second_candidate_is_marked_ambiguous(tmp_path):
    pytest.importorskip("soundfile")
    path = tmp_path / "v.wav"
    _vocals(path, [1.0, 1.9])
    rows = lines_table.build([{"text": "a", "start": 1.45}], path, 0.8, 0.0)
    assert rows[0]["ambiguous"] is True
    clear = lines_table.build([{"text": "a", "start": 1.05}], path, 0.5, 0.0)
    assert clear[0]["ambiguous"] is False
    lines_table.write(rows, tmp_path)
    assert json.loads((tmp_path / "lines_measured.json").read_text(encoding="utf-8"))[0]["ambiguous"] is True
    assert "要確認" in (tmp_path / "lines_table.csv").read_text(encoding="utf-8-sig")
