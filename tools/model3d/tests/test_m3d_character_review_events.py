from pathlib import Path

import pytest

from model3d import character_review as review
from model3d.io import ToolError, atomic_json, read_json, sha256


@pytest.mark.parametrize("events", [
    [{"id": "../escape", "frame": 1}],
    [{"id": "con", "frame": 1}],
    [{"id": "pose", "frame": 1}, {"id": "POSE", "frame": 2}],
    [{"id": "pose", "peak": {"object": "face", "shape_key": "blink", "range": [4, 1]}}],
    [{"id": "pose", "rest": True, "finding": {"kind": "sections", "label": "arm"}}],
    [{"id": "pose", "frame": 1, "disable": {"object": "face", "hide": True, "shape_key": "blink"}}],
    [{"id": "pose", "frame": 1, "framing": {"scale": 0}}],
    [{"id": "pose", "frame": 1, "framing": {"offset_z": "high"}}],
])
def test_ambiguous_or_nonportable_events_are_rejected(events):
    with pytest.raises(ToolError):
        review._events(events)


def make_study(tmp_path):
    assets = {}
    for side in ("before", "after"):
        path = tmp_path / (side + ".blend")
        path.write_bytes(side.encode())
        assets[side] = {"path": path.name, "armature": "rig", "left": "L", "right": "R", "anchor": "head"}
    (tmp_path / "art.png").write_bytes(b"art")
    config = dict(assets, references={"left": "art.png"}, scale=1, events=[
        {"id": "arm", "region": "forearm", "focus_view": "left", "finding": {"kind": "sections", "label": "ring"}},
        {"id": "eye", "finding": {"kind": "expressions", "label": "blink"}},
    ])
    spec = tmp_path / "study.json"
    atomic_json(spec, config)
    report = {"schema_version": "model3d-character-roundtrip/0.1",
              "native_sha256": sha256(tmp_path / "after.blend"), "delivered_sha256": "f" * 64,
              "worst": {"sections": {"ring": {"frame": 3, "ratio": 0.84}},
                        "expressions": {"blink": {"frame": 2, "delta": -0.2}}}}
    return spec, report


def fake_renderer(blender, script, blend, args, log):
    job = read_json(args[0])
    directory = Path(job["output"])
    for event in job["events"]:
        for view in job["views"]:
            dest = directory / event["name"] / view
            dest.mkdir(parents=True)
            for variant in ("before", "after"):
                (dest / (variant + ".png")).write_bytes(b"render")
    atomic_json(directory / "cameras.json", {"fps": 24, "first_frame": 1})
    return {"blender_version": "test"}


def test_roundtrip_findings_address_the_measured_pose_and_keep_the_full_report(tmp_path, monkeypatch):
    spec, report = make_study(tmp_path)
    evidence = tmp_path / "evidence.json"
    atomic_json(evidence, report)
    monkeypatch.setattr(review.launcher, "run_blender", fake_renderer)
    run = review.run(spec, tmp_path / "out", "blender", roundtrip_report=evidence)
    index = Path(run["index"])
    events = read_json(index.parent / "events.json")
    assert [(e["id"], e["frame"]) for e in events] == [("arm", 3), ("eye", 2)]
    assert "ratio = 0.84" in events[0]["observations"][0]
    assert "delta = -0.2" in events[1]["observations"][0]
    assert read_json(index.parent / "roundtrip.json") == report
    page = index.read_text(encoding="utf-8")
    assert 'href="#event-1-left"' in page and 'href="./roundtrip.json"' in page
    sources = read_json(index.parent / "sources.json")
    assert sources["models"]["after"]["sha256"] == report["native_sha256"]


@pytest.mark.parametrize("error", ["missing", "stale", "unmeasured", "nonnumeric", "changed"])
def test_invalid_or_changing_evidence_leaves_previous_package_intact(tmp_path, monkeypatch, error):
    spec, report = make_study(tmp_path)
    evidence = tmp_path / "evidence.json"
    if error == "stale":
        report["native_sha256"] = "0" * 64
    elif error == "unmeasured":
        report["worst"]["sections"] = {}
    elif error == "nonnumeric":
        report["worst"]["sections"]["ring"]["ratio"] = "unknown"
    atomic_json(evidence, report)
    out = tmp_path / "out"
    previous = out / "review-prior" / "index.html"
    previous.parent.mkdir(parents=True)
    previous.write_text("prior")

    def changed_renderer(*args, **kwargs):
        assert error == "changed"
        (tmp_path / "after.blend").write_bytes(b"new revision")
        return fake_renderer(*args, **kwargs)

    monkeypatch.setattr(review.launcher, "run_blender", changed_renderer)
    with pytest.raises(ToolError):
        review.run(spec, out, "blender", roundtrip_report=None if error == "missing" else evidence)
    assert list(out.iterdir()) == [previous.parent]
    assert previous.read_text() == "prior"
