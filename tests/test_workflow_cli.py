"""CLI completion status is usable by a calling agent, without starting production."""
import json

from mv_pipeline.cli import main


def test_plan_before_init_then_check_is_incomplete_without_running_upstream(tmp_path, capsys):
    project = tmp_path / "song"
    assert main(["workflow", "plan", str(project), "--purpose", "lyric-mv"]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert main(["workflow", "check", plan, "--out", str(tmp_path / "report")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["overall"] == "incomplete"
    assert result["accepted"] is False
    assert not (project / "project.json").exists()
    assert not (project / "runs").exists()


def test_omission_requires_a_reason_and_can_be_restored(tmp_path, capsys):
    assert main(["workflow", "plan", str(tmp_path / "song"), "--purpose", "lyric-mv"]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert main(["workflow", "omit", plan, "beats", "--reason", "   "]) == 1
    assert "Error:" in capsys.readouterr().err
    assert main(["workflow", "omit", plan, "beats", "--reason", "Intentional free-time edit"]) == 0
    capsys.readouterr()
    assert main(["workflow", "restore", plan, "beats"]) == 0
    capsys.readouterr()
    assert main(["workflow", "check", plan]) == 1
    assert json.loads(capsys.readouterr().out)["overall"] == "incomplete"


def test_missing_plan_returns_a_normal_cli_error(tmp_path, capsys):
    assert main(["workflow", "check", str(tmp_path / "missing.json")]) == 1
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "Traceback" not in captured.err


def test_the_inspection_stage_is_set_on_the_plan_and_reports_are_passed_to_check(tmp_path, capsys):
    assert main(["workflow", "plan", str(tmp_path / "song"), "--purpose", "lyric-mv", "--inspection", "required"]) == 0
    plan = json.loads(capsys.readouterr().out)["plan"]
    assert json.loads((tmp_path / "song" / "workflow.json").read_text(encoding="utf-8"))["inspection"] == "required"
    report = tmp_path / "inspect_report.json"
    report.write_text(json.dumps({"schema_version": "inspect-report/0.1", "overall": "clean", "clips": [{"sha256": "0" * 64}],
                                  "summary": {"events": 0}, "skipped": [], "vlm": {"status": "not_run"}}), encoding="utf-8")
    assert main(["workflow", "check", plan, "--inspect-report", str(report)]) == 1
    result = json.loads(capsys.readouterr().out)
    stage = next(row for row in result["stages"] if row["id"] == "inspection")
    assert stage["required"] is True and stage["inspection_state"] == "stale" and "inspection" in result["missing"]
