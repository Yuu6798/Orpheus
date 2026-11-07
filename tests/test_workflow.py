"""Workflow evidence regressions; synthetic fixtures are not song evaluations."""
import shutil
import wave
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from mv_pipeline import pipeline, validate, workflow
from mv_pipeline.demo import create
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256
from mv_pipeline.runner import Runner


def rows(report):
    return {row["id"]: row for row in report["stages"]}


@pytest.fixture
def mv(tmp_path):
    root = create(tmp_path / "song", 160, 90)
    return root, Path(workflow.create_plan(root, "lyric-mv")["plan"])


def change_analysis(root, edit, *, update_story=True):
    analysis = read_json(root / "analysis.json")
    edit(analysis)
    atomic_json(root / "analysis.json", analysis)
    if update_story:
        story = read_json(root / "storyboard.json")
        story["analysis_sha256"] = sha256(root / "analysis.json")
        atomic_json(root / "storyboard.json", story)


def journal_fixture(root, *, beats=True):
    """Model the existing successful-run evidence; no claim to have encoded video.

    The real executable example and tiny dance fixture exercise actual rendering.
    Here we isolate hashing, freshness, profiles and required-stage behavior.
    """
    if beats:
        change_analysis(root, lambda a: a.update(beats_s=[i / 2 for i in range(12)], bpm=120))
    a, story, assets, captions, warnings = validate.bundle(root)
    run = pipeline.snapshot(root, a, story, assets, captions, warnings, "final")
    (run / "video.mp4").write_bytes(b"synthetic output for journal integrity unit tests")
    atomic_json(run / "verification.json", {
        "technical_pass": True, "profile": "final", "accepted": False,
        "input_hashes": {name: sha256(root / f"{name}.json") for name in ("analysis", "storyboard", "assets")},
    })
    update_journal(root, run)
    return run


def update_journal(root, run):
    runner = Runner(root)
    runner.job["current_run_id"] = run.name
    runner.job["stages"]["render"] = {
        "status": "succeeded",
        "outputs": {path.relative_to(root).as_posix(): sha256(path) for path in run.rglob("*") if path.is_file()},
    }
    runner.save()


def test_plan_before_inputs_is_incomplete_and_does_not_trust_edited_requirements(tmp_path):
    root = tmp_path / "new-song"
    made = workflow.create_plan(root, "lyric-mv")
    plan = Path(made["plan"])
    assert plan == root / "workflow.json"
    assert [row["id"] for row in made["stages"]] == list(workflow.STAGES)
    data = read_json(plan)
    data["stages"] = []  # Guidance is editable; the purpose still defines requirements.
    atomic_json(plan, data)
    report = workflow.check(plan)
    assert report["overall"] == "incomplete"
    assert {"inputs", "beats", "lyrics", "render", "verification"} <= set(report["missing"])
    assert not report["accepted"]
    with pytest.raises(PipelineError, match="already exists"):
        workflow.create_plan(root, "lyric-mv")


def test_current_hashed_mv_evidence_completes_but_never_accepts_artwork(mv, tmp_path):
    root, plan = mv
    run = journal_fixture(root)
    report = workflow.check(plan, out=tmp_path / "report")
    assert report["overall"] == "complete" and report["accepted"] is False
    assert all(row["status"] == "pass" for row in report["stages"])
    assert read_json(tmp_path / "report/workflow_report.json")["overall"] == "complete"
    assert (tmp_path / "report/workflow_report.md").is_file()
    # An exported copy is valid when its inputs and every recorded output match.
    exported = tmp_path / "exported-run"
    shutil.copytree(run, exported)
    assert workflow.check(plan, run_dir=exported)["overall"] == "complete"


def test_missing_beats_is_not_success_and_reasoned_omission_stays_visible(mv):
    root, plan = mv
    journal_fixture(root, beats=False)
    report = workflow.check(plan)
    assert report["overall"] == "incomplete" and rows(report)["beats"]["status"] == "pending"
    workflow.omit(plan, "beats", "自由拍で編集するため、拍候補の取得を省略")
    report = workflow.check(plan)
    assert report["overall"] == "complete_with_omissions"
    assert rows(report)["beats"]["observed_status"] == "pending"
    assert rows(report)["beats"]["status"] == "omitted"
    assert rows(report)["beats"]["omission_reason"]
    workflow.restore(plan, "beats")
    assert workflow.check(plan)["overall"] == "incomplete"


@pytest.mark.parametrize("stage,reason", [("beats", ""), ("lyrics", " \n "), ("render", "省略"), ("inputs", "省略")])
def test_omission_requires_reason_and_cannot_remove_core_requirements(mv, stage, reason):
    _, plan = mv
    before = plan.read_bytes()
    with pytest.raises(PipelineError):
        workflow.omit(plan, stage, reason)
    assert plan.read_bytes() == before


def test_unknown_and_unreviewed_lyrics_stay_pending(mv):
    root, plan = mv
    def unknown(a):
        a["lyrics"][0].update(start_s=None, end_s=None, timing_source="unknown", review_status="unreviewed")
    change_analysis(root, unknown)
    report = workflow.check(plan)
    assert rows(report)["lyrics"]["status"] == "pending" and report["overall"] == "incomplete"
    change_analysis(root, lambda a: a["lyrics"][0].update(start_s=.75, end_s=3.4, timing_source="model"))
    assert rows(workflow.check(plan))["lyrics"]["status"] == "pending"


def test_stale_storyboard_fails_and_cannot_be_hidden_by_omission(mv):
    root, plan = mv
    change_analysis(root, lambda a: a.update(beats_s=[0.5, 1.0]), update_story=False)
    workflow.omit(plan, "beats", "省略して進めたい")
    report = workflow.check(plan)
    assert report["overall"] == "failed" and rows(report)["direction"]["status"] == "fail"


@pytest.mark.parametrize("changed", ["video", "input", "profile", "verification"])
def test_old_wrong_preview_or_replaced_mv_run_cannot_complete(mv, changed):
    root, plan = mv
    run = journal_fixture(root)
    if changed == "video":
        (run / "video.mp4").write_bytes(b"replaced output")
    elif changed == "input":
        change_analysis(root, lambda a: a.update(bpm=121))
    elif changed == "profile":
        manifest = read_json(run / "manifest.json")
        manifest["profile"] = "preview"
        atomic_json(run / "manifest.json", manifest)
        update_journal(root, run)
    else:
        verification = read_json(run / "verification.json")
        verification["input_hashes"]["analysis"] = "0" * 64
        atomic_json(run / "verification.json", verification)
        update_journal(root, run)
    report = workflow.check(plan)
    assert report["overall"] == "failed"
    assert set(report["failed"]) & {"render", "verification"}


def test_pending_reference_image_review_blocks_completion(mv):
    root, plan = mv
    journal_fixture(root)
    atomic_json(root / "work/asset-review.json", {"review_required": ["image-002"]})
    report = workflow.check(plan)
    assert report["overall"] == "incomplete" and rows(report)["assets"]["status"] == "pending"


@pytest.fixture(scope="module")
def dance_baseline(tmp_path_factory):
    pytest.importorskip("cv2")
    from mv_pipeline.dance import runs
    root = tmp_path_factory.mktemp("workflow-dance-baseline")
    Image.new("RGB", (160, 90), (30, 20, 50)).save(root / "stage.png")
    Image.new("RGBA", (30, 60), (230, 150, 80, 255)).save(root / "figure.png")
    t = np.arange(96000) / 48000
    samples = (0.15 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(root / "audio.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(samples.tobytes())
    spec = {
        "schema_version": "dance-shot/0.2", "duration_s": 2,
        "output": {"width": 160, "height": 90, "fps": 10},
        "audio": {"path": "audio.wav"}, "stage": {"path": "stage.png"},
        "beats": {"bpm": 120, "offset_s": 0, "count": 5, "beats_per_bar": 2},
        "performer": {"clips": [
            {"path": "figure.png", "start": 0, "end": 1, "fit": False},
            {"path": "figure.png", "start": 1, "end": 2, "fit": False},
        ]},
        "lyrics": {"ui_regions": [], "lines": []},
    }
    atomic_json(root / "spec.json", spec)
    result = runs.render_run(root / "spec.json", out=root / "runs", quiet=True)
    assert result["technical_pass"]
    run = Path(result["run_dir"])
    atomic_json(root / "gate.json", {"timeline": str((run / "timeline.json").relative_to(root)),
                                      "video": str((run / "dance.mp4").relative_to(root))})
    workflow.create_plan(root / "spec.json", "dance-edit", lyrics=False)
    return root, run.name


@pytest.fixture
def dance(dance_baseline, tmp_path):
    original, name = dance_baseline
    root = tmp_path / "dance"
    shutil.copytree(original, root)
    return root, root / "workflow.json", root / "runs" / name


def dance_check(dance):
    root, plan, run = dance
    return workflow.check(plan, run_dir=run, gate_config=root / "gate.json")


def rerender(dance, edit, **kwargs):
    from mv_pipeline.dance import runs
    root, plan, _ = dance
    spec = read_json(root / "spec.json")
    edit(spec)
    atomic_json(root / "spec.json", spec)
    result = runs.render_run(root / "spec.json", out=root / "runs", quiet=True, **kwargs)
    run = Path(result["run_dir"])
    atomic_json(root / "gate.json", {"timeline": str((run / "timeline.json").relative_to(root)),
                                      "video": str((run / "dance.mp4").relative_to(root))})
    return root, plan, run


def test_real_full_dance_requires_run_and_current_gate(dance):
    root, plan, run = dance
    assert workflow.check(plan)["overall"] == "incomplete"
    assert workflow.check(plan, run_dir=run)["overall"] == "incomplete"
    report = dance_check(dance)
    assert report["overall"] == "complete" and report["accepted"] is False
    assert rows(report)["beats"]["gate_status"] == "pass"
    assert rows(report)["lyrics"]["status"] == "not_applicable"


@pytest.mark.parametrize("change", ["partial", "scaled", "stale", "timeline", "wrong_gate", "dropped_downbeats"])
def test_dance_rejects_partial_stale_or_unrelated_evidence(dance, change):
    root, plan, run = dance
    if change in {"partial", "scaled"}:
        dance = rerender(dance, lambda _: None, **({"duration": 1} if change == "partial" else {"scale": .5}))
    elif change == "stale":
        (root / "figure.png").write_bytes((root / "figure.png").read_bytes() + b"changed")
    elif change == "timeline":
        timeline = read_json(run / "timeline.json")
        timeline["clips"][1]["start"] = .5
        atomic_json(run / "timeline.json", timeline)
    elif change == "wrong_gate":
        gate = read_json(root / "gate.json")
        gate["video"] = "unrelated.mp4"
        atomic_json(root / "gate.json", gate)
    else:
        atomic_json(root / "beats.json", {"beats": [0, .5, 1, 1.5, 2]})
        gate = read_json(root / "gate.json")
        gate["beats"] = "beats.json"
        atomic_json(root / "gate.json", gate)
    report = dance_check(dance)
    assert report["overall"] == "failed"


def test_failed_beat_alignment_is_not_waived(dance):
    dance = rerender(dance, lambda s: s["beats"].update(offset_s=.25))
    _, plan, _ = dance
    workflow.omit(plan, "beats", "理由があっても既に観測された不合格は消さない")
    report = dance_check(dance)
    assert report["overall"] == "failed"
    assert rows(report)["beats"]["status"] == rows(report)["beats"]["observed_status"] == "fail"


def test_missing_dance_beats_can_only_be_explicitly_omitted(dance):
    dance = rerender(dance, lambda s: s.pop("beats"))
    _, plan, _ = dance
    report = dance_check(dance)
    assert report["overall"] == "incomplete" and rows(report)["beats"]["gate_status"] == "skip"
    workflow.omit(plan, "beats", "この作品は自由拍で編集する")
    assert dance_check(dance)["overall"] == "complete_with_omissions"


@pytest.mark.parametrize("method,text,layer", [("estimate", "line", "band"), ("vocals_onset", "wrong song", "band"), ("vocals_onset", "line", "missing")])
def test_lyric_estimates_wrong_text_and_wrong_layer_do_not_certify_sync(dance, method, text, layer):
    def lyrics(spec):
        spec["lyrics"]["lines"] = [{"layer": "band", "text": "line", "start": .2, "end": .8}]
    dance = rerender(dance, lyrics)
    root, plan, _ = dance
    declaration = read_json(plan)
    declaration["lyrics"] = True
    atomic_json(plan, declaration)
    atomic_json(root / "measured.json", [{"text": text, "start": .2, "method": method}])
    gate = read_json(root / "gate.json")
    gate.update(measured_lines="measured.json", lyric_layer=layer)
    atomic_json(root / "gate.json", gate)
    report = dance_check(dance)
    if method == "estimate":
        assert report["overall"] == "incomplete" and rows(report)["lyrics"]["gate_status"] == "skip"
    else:
        assert report["overall"] == "failed"


def test_no_lyrics_declaration_cannot_hide_sung_lines(dance):
    dance = rerender(dance, lambda s: s["lyrics"]["lines"].append({"layer": "band", "text": "line", "start": .2, "end": .8}))
    report = dance_check(dance)
    assert report["overall"] == "failed" and rows(report)["lyrics"]["status"] == "fail"


@pytest.mark.parametrize("changed", ["hook_source", "timeline_hook"])
def test_dance_finishing_hook_is_bound_to_current_source_and_timeline(dance, changed):
    root, _, _ = dance
    hook = root / "finish.py"
    hook.write_text("def final(img, t, ctx, layers):\n    return img\n", encoding="utf-8")
    dance = rerender(dance, lambda _: None, hooks=hook)
    _, _, run = dance
    assert dance_check(dance)["overall"] == "complete"
    if changed == "hook_source":
        hook.write_text(hook.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")
    else:
        timeline = read_json(run / "timeline.json")
        timeline["finished"] = None
        atomic_json(run / "timeline.json", timeline)
    report = dance_check(dance)
    assert report["overall"] == "failed" and rows(report)["render"]["status"] == "fail"


def inspect_report(path, clips, *, overall="review", events=3, spec_sha256=None, without_cue=None):
    """mv inspect の報告（inspect-report/0.1）の、workflow が読む欄だけを持つ作り物。"""
    inputs = [{"role": "dance_spec", "path": "spec.json", "sha256": spec_sha256}] if spec_sha256 else []
    summary = {"events": events, **({"cues": {"events_with_cue": events - without_cue, "events_without_cue": without_cue,
                                               "events_unchecked": 0}} if without_cue is not None else {})}
    atomic_json(path, {"schema_version": "inspect-report/0.1", "overall": overall,
                       "clips": [{"id": f"c{i}", "sha256": value} for i, value in enumerate(clips)],
                       "summary": summary, "skipped": [], "vlm": {"status": "packet", "frames": {"events_unsent": 1}},
                       "run": {"id": "20261108T000000Z-test", "finished_at": "2026-11-08T00:00:00+00:00", "inputs": inputs}})
    return path


def test_the_inspection_stage_appears_only_when_planned_or_given(mv, tmp_path):
    root, plan = mv
    journal_fixture(root)
    assert "inspection" not in rows(workflow.check(plan))                       # 前の計画は前のまま
    report = inspect_report(tmp_path / "inspect.json", ["0" * 64])
    row = rows(workflow.check(plan, inspect_reports=[report]))["inspection"]
    assert row["required"] is False and row["status"] == "pending" and row["inspection_state"] == "stale"


def test_a_required_inspection_needs_a_report_of_the_final_video(mv, tmp_path):
    root, _ = mv
    plan = Path(workflow.create_plan(root, "lyric-mv", out=tmp_path / "plan.json", inspection="required")["plan"])
    assert read_json(plan)["inspection"] == "required" and read_json(plan)["stages"][-1]["id"] == "inspection"
    run = journal_fixture(root)
    report = workflow.check(plan)
    assert report["overall"] == "incomplete" and report["missing"] == ["inspection"]
    assert rows(report)["inspection"]["inspection_state"] == "not_run"
    seen = inspect_report(tmp_path / "seen.json", [sha256(run / "video.mp4")], without_cue=2)
    report = workflow.check(plan, inspect_reports=[seen], out=tmp_path / "out")
    row = rows(report)["inspection"]
    assert report["overall"] == "complete" and report["accepted"] is False                # 検品済みでも、採否は人
    assert row["status"] == "pass" and row["inspection_state"] == "review" and row["evidence"] == [str(seen.resolve())]
    assert row["inspections"][0]["target"] == "final_video" and row["inspections"][0]["run_id"] == "20261108T000000Z-test"
    assert "要確認 3 件（演出と重ならない 2 件）" in row["summary"] and "VLM に見せなかった出来事 1" in row["summary"]
    assert "検品（mv inspect）" in (tmp_path / "out" / "workflow_report.md").read_text(encoding="utf-8")
    old = inspect_report(tmp_path / "old.json", ["1" * 64])
    report = workflow.check(plan, inspect_reports=[old])
    assert report["overall"] == "incomplete" and rows(report)["inspection"]["inspection_state"] == "stale"
    clean = inspect_report(tmp_path / "clean.json", [sha256(run / "video.mp4")], overall="clean", events=0)
    assert rows(workflow.check(plan, inspect_reports=[clean]))["inspection"]["inspection_state"] == "clean"


def test_a_dance_inspection_of_the_current_spec_sources_is_recorded(dance, tmp_path):
    root, plan, run = dance
    sources = inspect_report(tmp_path / "sources.json", [sha256(root / "figure.png")], spec_sha256=sha256(root / "spec.json"))
    row = rows(workflow.check(plan, run_dir=run, gate_config=root / "gate.json", inspect_reports=[sources]))["inspection"]
    assert row["status"] == "pass" and row["inspections"][0]["target"] == "spec_sources"
    output = inspect_report(tmp_path / "output.json", [sha256(run / "dance.mp4")])
    assert rows(workflow.check(plan, run_dir=run, gate_config=root / "gate.json", inspect_reports=[output]))["inspection"]["inspections"][0]["target"] == "final_video"


def test_a_spec_inspection_of_replaced_sources_is_stale(dance, tmp_path):
    root, plan, run = dance
    before = inspect_report(tmp_path / "before.json", [sha256(root / "figure.png")], spec_sha256=sha256(root / "spec.json"))
    Image.new("RGBA", (30, 60), (80, 150, 230, 255)).save(root / "figure.png")   # 同じパスで素材を差し替えた（spec の sha256 は同じ）
    row = rows(workflow.check(plan, run_dir=run, gate_config=root / "gate.json", inspect_reports=[before]))["inspection"]
    assert row["status"] == "pending" and row["inspection_state"] == "stale" and row["inspections"][0]["target"] is None


def test_a_broken_inspection_record_fails_and_a_bad_mode_is_refused(mv, tmp_path):
    root, plan = mv
    atomic_json(tmp_path / "other.json", {"schema_version": "something-else"})
    report = workflow.check(plan, inspect_reports=[tmp_path / "other.json"])
    assert rows(report)["inspection"]["status"] == "fail" and "inspection" in report["failed"]
    with pytest.raises(PipelineError, match="inspection"):
        workflow.create_plan(root, "lyric-mv", out=tmp_path / "bad.json", inspection="sometimes")


def test_a_timeline_rendered_before_align_existed_still_matches_the_spec(dance):
    """seam_info / flash_info に align の無い run（前の版）も、"beat" として比べて完了になる。"""
    root, plan, run = dance
    record = read_json(run / "timeline.json")
    for key in ("seam_info", "flash_info"):
        for item in record[key]:
            item.pop("align", None)
    atomic_json(run / "timeline.json", record)
    assert dance_check(dance)["overall"] == "complete"
