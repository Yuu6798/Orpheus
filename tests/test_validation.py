import copy

import pytest

from mv_pipeline import validate
from mv_pipeline.io import REPO, PipelineError, read_json, safe_path, sha256


@pytest.fixture
def a():
    return read_json(REPO / "examples/analysis.json")


@pytest.fixture
def story():
    return read_json(REPO / "examples/storyboard.json")


def test_supplied_contracts(a, story):
    validate.analysis(a)
    ids = validate.assets(read_json(REPO / "examples/assets.json"))
    validate.storyboard(story, a, sha256(REPO / "examples/analysis.json"), ids)


@pytest.mark.parametrize("path", ["../private.png", "/image.png", "C:/image.png", "images/../../secret", "a\\b", "https://example.com/a"])
def test_unsafe_paths(path, tmp_path):
    with pytest.raises(PipelineError):
        safe_path(tmp_path, path)


def test_beat_order(a):
    a["beats_s"] = [1, 1]
    with pytest.raises(PipelineError, match="strictly"):
        validate.analysis(a)


def test_unknown_cannot_have_numeric_timing(a):
    a["lyrics"][0]["timing_source"] = "unknown"
    with pytest.raises(PipelineError):
        validate.analysis(a)


@pytest.mark.parametrize("mutation", ["gap", "reference", "hash", "duration", "preset", "first_fade", "duplicate"])
def test_storyboard_rejects_inconsistency(a, story, mutation):
    if mutation == "gap": story["scenes"][1]["start_frame"] += 1
    if mutation == "reference": story["scenes"][0]["lyric_ids"] = ["absent"]
    if mutation == "hash": story["analysis_sha256"] = "0" * 64
    if mutation == "duration": story["render"]["duration_frames"] -= 1
    if mutation == "preset": story["scenes"][0]["motion"] = "execute-code"
    if mutation == "first_fade": story["scenes"][0]["transition_in"] = {"type": "crossfade", "duration_frames": 3}
    if mutation == "duplicate": story["scenes"][1]["id"] = story["scenes"][0]["id"]
    with pytest.raises(PipelineError):
        validate.storyboard(story, a, sha256(REPO / "examples/analysis.json"), {"image-001", "image-002", "reference-001"})


def test_rounding_overlap_prefers_next(a):
    a["lyrics"][0].update(start_s=0, end_s=1.01)
    a["lyrics"][1].update(start_s=1.01, end_s=2)
    rows = validate.captions(a, 30, 300)
    assert rows[0]["endFrame"] == rows[1]["startFrame"] == 30


def test_actual_singing_overlap_is_rejected(a):
    a["lyrics"][1]["start_s"] = 3
    with pytest.raises(PipelineError, match="Overlapping"):
        validate.captions(a, 30, 300)


def test_missing_image(tmp_path):
    with pytest.raises(PipelineError, match="Missing image"):
        validate.assets(read_json(REPO / "examples/assets.json"), tmp_path)


def test_duplicate_lyrics(a):
    a["lyrics"].append(copy.deepcopy(a["lyrics"][0]))
    with pytest.raises(PipelineError, match="Duplicate"):
        validate.analysis(a)
