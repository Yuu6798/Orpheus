"""The example must preserve timing provenance and use analysis in its direction.

Only audio analysis is replaced by a labelled fixture here: the unit-test job
does not install librosa. The CI render smoke runs the real analyzer and render.
"""
import importlib.util

import pytest

from mv_pipeline import pipeline, validate
from mv_pipeline.io import REPO, PipelineError, read_json


@pytest.fixture
def example(monkeypatch):
    spec = importlib.util.spec_from_file_location("workflow_example", REPO / "examples/workflow/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    def fixture_analysis(root):
        analysis = read_json(root / "work/analysis.raw.json")
        analysis.update(bpm=120, beats_s=[.51 + .5 * index for index in range(15)])
        analysis["provenance"].append({"stage": "beats", "tool": "unit-test-fixture", "version": "1", "model": None})
        pipeline.save_analysis(root, analysis, raw=True)

    monkeypatch.setattr(module.pipeline, "analyze", fixture_analysis)
    return module


def test_example_preserves_estimates_and_connects_lyrics_and_beats(example, tmp_path):
    root = tmp_path / "example"
    result = example.prepare(root)
    unknown = read_json(root / "work/example/01-unknown.json")
    estimated = read_json(root / "work/example/02-unreviewed.json")
    confirmed = read_json(root / "work/example/03-fixture-confirmed.json")
    assert all(row["start_s"] is None and row["timing_source"] == "unknown" for row in unknown["lyrics"])
    assert all(row["timing_source"] == "interpolated" and row["review_status"] == "unreviewed"
               for row in estimated["lyrics"])
    assert all(row["timing_source"] == "manual" and row["review_status"] == "confirmed"
               for row in confirmed["lyrics"])
    assert estimated["lyrics"][0]["start_s"] != confirmed["lyrics"][0]["start_s"]
    review = read_json(root / "work/example/fixture-review.json")
    assert review["contains_singing"] is False and review["human_review"] == "not_performed"
    assert review["accepted"] is False and result["accepted"] is False

    analysis, story, _, captions, warnings = validate.bundle(root)
    decision = read_json(root / "work/example/direction.json")
    assert not warnings
    assert decision["detected_beat_s"] in analysis["beats_s"]
    assert abs(decision["beat_rounding_error_s"]) <= .5 / story["render"]["fps"]
    cut = story["scenes"][1]["start_frame"]
    assert story["scenes"][0]["end_frame"] == cut == decision["cut_frame"]
    assert captions[0]["endFrame"] > cut
    assert "lyric-001" in story["scenes"][1]["lyric_ids"]
    assert "lyric-002" in story["scenes"][1]["lyric_ids"]
    assert not list((root / "runs").iterdir())


def test_example_will_not_replace_existing_work(example, tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("user work", encoding="utf-8")
    with pytest.raises(PipelineError, match="never replaces"):
        example.prepare(tmp_path)
    assert marker.read_text(encoding="utf-8") == "user work"
    assert sorted(path.name for path in tmp_path.iterdir()) == ["keep.txt"]


def test_example_does_not_substitute_known_tempo_when_no_beats_are_found(example, tmp_path, monkeypatch):
    monkeypatch.setattr(example.pipeline, "analyze", lambda root: None)
    root = tmp_path / "no-beats"
    with pytest.raises(PipelineError, match="No detected beat"):
        example.prepare(root)
    assert read_json(root / "analysis.json")["beats_s"] == []
    assert not (root / "storyboard.json").exists()
    assert not list((root / "runs").iterdir())


def test_example_rejects_invalid_dimensions_before_writing(example, tmp_path):
    root = tmp_path / "odd-size"
    with pytest.raises(PipelineError, match="positive even"):
        example.prepare(root, width=319)
    assert not root.exists()
