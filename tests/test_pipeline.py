import json
from pathlib import Path

import pytest

from mv_pipeline import pipeline, validate
from mv_pipeline.demo import create
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256
from mv_pipeline.normalize import alignment, apply_edits
from mv_pipeline.runner import Runner, cache_key, project_lock


@pytest.fixture
def project(tmp_path):
    return create(tmp_path / "song", 320, 180)


def test_demo_contract_and_global_captions(project):
    a, s, _, captions, warnings = validate.bundle(project)
    assert not warnings
    assert captions[0]["endFrame"] > s["scenes"][1]["start_frame"]
    assert a["audio"]["sample_count"] == 288000


def test_final_blocks_interpolated_unreviewed(project):
    a = read_json(project / "analysis.json")
    a["lyrics"][0].update(timing_source="interpolated", review_status="unreviewed")
    atomic_json(project / "analysis.json", a)
    s = read_json(project / "storyboard.json")
    s["analysis_sha256"] = sha256(project / "analysis.json")
    atomic_json(project / "storyboard.json", s)
    with pytest.raises(PipelineError, match="Unresolved"):
        validate.bundle(project)
    assert validate.bundle(project, "preview")[-1]
    s["render"]["show_lyrics"] = False
    atomic_json(project / "storyboard.json", s)
    validate.bundle(project)


def test_model_reimport_preserves_manual_edits(project):
    a = read_json(project / "work/analysis.raw.json")
    imported = {"audio_sha256": a["audio"]["sha256"], "offset_s": -0.1,
                "tool": {"name": "test", "version": "1", "model": None},
                "lyrics": [{"id": "lyric-001", "text": "窓の向こうへ", "start_s": 0.3, "end_s": 1.5}]}
    atomic_json(project / "model.json", imported)
    pipeline.import_alignment(project, project / "model.json")
    assert read_json(project / "analysis.json")["lyrics"][0]["start_s"] == .75
    assert read_json(project / "work/analysis.raw.json")["lyrics"][0]["start_s"] == pytest.approx(.2)


def test_lyrics_change_does_not_reuse_wrong_manual_time(project):
    text = project / "new-lyrics.txt"
    text.write_text("別の歌詞\n声をつないで\n", encoding="utf-8")
    pipeline.update_lyrics(project, text)
    a = read_json(project / "analysis.json")
    assert a["lyrics"][0]["start_s"] is None
    assert a["issues"][0]["code"] == "stale_lyric_edit"
    assert a["lyrics"][1]["start_s"] == 3.4


def test_source_change_rejected(project):
    source = project / read_json(project / "project.json")["source_audio"]
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(PipelineError, match="Source audio changed"):
        validate.bundle(project)


def test_failed_render_resume_never_runs_upstream(tmp_path):
    calls = []
    runner = Runner(tmp_path)
    def separation():
        calls.append("separation")
        (tmp_path / "stem.txt").write_text("stem")
        return ["stem.txt"]
    runner.execute("separation", "stem-key", separation)
    def failure():
        calls.append("render-failed")
        raise PipelineError("simulated encoder failure")
    with pytest.raises(PipelineError):
        runner.execute("render", "render-key", failure)
    def success():
        calls.append("render-ok")
        (tmp_path / "movie.txt").write_text("movie")
        return ["movie.txt"]
    Runner(tmp_path).execute("render", "render-key", success)
    assert calls == ["separation", "render-failed", "render-ok"]
    assert read_json(tmp_path / "job.json")["stages"]["separation"]["status"] == "succeeded"


def test_cache_rejects_changed_output(tmp_path):
    calls = []
    def action():
        calls.append(1)
        (tmp_path / "output").write_text("ok")
        return ["output"]
    Runner(tmp_path).execute("test", "key", action)
    Runner(tmp_path).execute("test", "key", action)
    assert len(calls) == 1
    (tmp_path / "output").write_text("corrupt")
    Runner(tmp_path).execute("test", "key", action)
    assert len(calls) == 2


def test_double_lock_fails(tmp_path):
    with project_lock(tmp_path):
        with pytest.raises(PipelineError, match="already running"):
            with project_lock(tmp_path):
                pass


def test_analysis_hash_invalidates_storyboard(project):
    a = read_json(project / "analysis.json")
    a["bpm"] = 120
    atomic_json(project / "analysis.json", a)
    with pytest.raises(PipelineError, match="Analysis hash mismatch"):
        validate.bundle(project)


def test_image_change_requires_reregistration(project):
    image = project / "images/fixture-1.png"
    image.write_bytes(image.read_bytes() + b"changed")
    with pytest.raises(PipelineError, match="Image hash mismatch"):
        validate.bundle(project)
