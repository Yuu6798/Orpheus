"""`mv export remotion-props`: analysis.json（と storyboard）からコマ番号だけの props.json を書く。丸めは validate と同じ関数。"""
import json

import pytest
from fx_synth import run_cli

from mv_pipeline import remotion_props, validate
from mv_pipeline.demo import create
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256


@pytest.fixture
def project(tmp_path):
    return create(tmp_path / "song", 320, 180)


@pytest.mark.parametrize("start_s, end_s, start, end", [
    (3.02, 5.48, 90, 165),      # 3.02 秒 → 90 コマ（floor）、5.48 秒の終わりは 165（ceil）
    (5.48, 6.0, 164, 180),      # 5.48 秒を始まりにすれば 164 コマ
    (0.0, 0.01, 0, 1),
    (2.999999, 3.0, 89, 90),
])
def test_fractional_times_round_down_at_the_start_and_up_at_the_end(start_s, end_s, start, end):
    assert validate.frame_span(start_s, end_s, 30) == (start, end)


def test_frame_span_is_clamped_to_the_render_and_captions_use_it():
    assert validate.frame_span(5.9, 7.0, 30, 180) == (177, 180)
    assert validate.frame_at(0.5, 30) == 15 and validate.frame_at(0.51666, 30) == 15 and validate.frame_at(0.51667, 30) == 16


def test_props_carry_frames_only_and_match_the_render_captions(project):
    props = remotion_props.build(project)
    a = read_json(project / "analysis.json")
    captions = validate.captions(a, 30, props["duration_frames"])
    assert props["schema_version"] == "remotion-props/0.1" and (props["fps"], props["width"], props["height"]) == (30, 320, 180)
    assert props["duration_frames"] == 180
    assert [(line["start_frame"], line["end_frame"]) for line in props["lines"]] == [(c["startFrame"], c["endFrame"]) for c in captions]
    assert props["lines"][0] == {"id": "lyric-001", "text": "窓の向こうへ", "display": "窓の向こうへ", "sung": True, "start_frame": 22,
                                 "end_frame": 102, "timing_source": "manual", "review_status": "confirmed", "confidence": None}
    assert props["untimed"] == [] and props["beats"] == {"frames": [], "downbeat_frames": [], "bpm": None, "source": {"kind": "analysis", "downbeats": "none"}}
    assert [s["id"] for s in props["scenes"]] == ["scene-001", "scene-002"] and props["show_lyrics"] is True
    assert props["sources"]["analysis_sha256"] == sha256(project / "analysis.json") and "storyboard_sha256" in props["sources"]
    assert "start_s" not in json.dumps(props) and "end_s" not in json.dumps(props)   # 秒は出さない


def test_untimed_lines_are_kept_apart_and_downbeats_come_from_beats_per_bar_or_a_file(project, tmp_path):
    a = read_json(project / "analysis.json")
    a["lyrics"].append({"id": "lyric-003", "text": "まだ時刻の無い行", "alignment_text": "まだ時刻の無い行", "start_s": None, "end_s": None,
                        "timing_source": "unknown", "review_status": "unreviewed", "confidence": None})
    a.update(bpm=120.0, beats_s=[0.5 * i for i in range(12)])
    atomic_json(project / "analysis.json", a)
    props = remotion_props.build(project, beats_per_bar=4, downbeat_phase=1, storyboard=False)
    assert props["untimed"] == [{"id": "lyric-003", "text": "まだ時刻の無い行", "timing_source": "unknown"}] and len(props["lines"]) == 2
    assert props["beats"]["frames"] == [15 * i for i in range(12)] and props["beats"]["downbeat_frames"] == [15, 75, 135]
    assert props["beats"]["source"] == {"kind": "beats_per_bar", "beats_per_bar": 4, "downbeat_phase": 1} and props["scenes"] is None
    grid = tmp_path / "beats.json"
    atomic_json(grid, {"beats": [0.0, 1.0, 2.0], "downbeats": [0.0, 2.0]})
    props = remotion_props.build(project, beats_path=grid, storyboard=False)
    assert props["beats"]["frames"] == [0, 30, 60] and props["beats"]["downbeat_frames"] == [0, 60]
    assert props["beats"]["source"] == {"kind": "file", "path": str(grid), "sha256": sha256(grid)}
    with pytest.raises(PipelineError, match="同時に"):
        remotion_props.build(project, beats_path=grid, beats_per_bar=4)
    with pytest.raises(PipelineError, match="--beats-per-bar"):
        remotion_props.build(project, beats_per_bar=4, downbeat_phase=4)


def test_a_stale_storyboard_is_rejected_not_exported(project):
    a = read_json(project / "analysis.json")
    a["lyrics"][0]["start_s"] = 0.9
    atomic_json(project / "analysis.json", a)
    with pytest.raises(PipelineError):
        remotion_props.build(project)
    assert remotion_props.build(project, storyboard=False)["scenes"] is None


def test_cli_writes_the_props_and_a_summary(project, tmp_path):
    out = tmp_path / "props.json"
    code, result, err = run_cli("export", "remotion-props", project, "--out", out, "--beats-per-bar", "4")
    assert code == 0, err
    assert result["props"] == str(out) and result["lines"] == 2 and result["scenes"] == 2 and result["downbeats"] == 0
    assert read_json(out)["schema_version"] == "remotion-props/0.1"
