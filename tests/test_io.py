import errno
import json
import sys
from pathlib import Path

import pytest

from mv_pipeline.io import PipelineError, run


ERROR_PREFIX = "[orpheus-render-error] "


def command(stdout="", stderr="", returncode=0):
    return [sys.executable, "-c", "import sys; "
            f"sys.stdout.write({stdout!r}); sys.stderr.write({stderr!r}); sys.exit({returncode})"]


def render_error(phase="bundle", error=None):
    return ERROR_PREFIX + json.dumps({"phase": phase, "error": error or {
        "message": "Render failed", "cause": {
            "code": "ENOSPC", "message": "no space left on device, copyfile",
        },
    }}) + "\n"


def test_render_cause_survives_tail_truncation_and_later_cleanup_failure(tmp_path):
    stdout = "render started\n"
    stderr = render_error() + "webpack detail\n" * 500 + render_error("cleanup", {
        "code": "EACCES", "message": "could not remove bundle directory",
    })
    log = tmp_path / "run" / "render.log"

    with pytest.raises(PipelineError) as caught:
        run(command(stdout, stderr, 7), log)

    first_line = str(caught.value).splitlines()[0]
    assert "exited 7" in first_line
    assert "render bundle:" in first_line
    assert "ENOSPC" in first_line
    assert "cleanup" not in first_line
    assert stderr[-2500:] in str(caught.value)
    assert log.read_text(encoding="utf-8") == stdout + stderr


def test_cleanup_does_not_replace_original_error_with_empty_message():
    stderr = render_error(error={"name": "Error", "message": ""}) + render_error("cleanup")

    with pytest.raises(PipelineError) as caught:
        run(command(stderr=stderr, returncode=1))

    assert str(caught.value).splitlines()[0].endswith("render bundle: Error")


@pytest.mark.parametrize("field", ["message", "stack"])
def test_multiline_wrapped_enospc_is_visible_without_code_or_cause(field):
    error = {"message": "The browser may have run out of memory"}
    error[field] = ("The browser may have run out of memory\n"
                    + "build output " * 60 + "ENOSPC: no space left on device, copyfile\n"
                    + "wrapper stack\n" * 300)
    stderr = render_error(error=error) + "additional output\n" * 300

    with pytest.raises(PipelineError) as caught:
        run(command(stderr=stderr, returncode=1))

    # Runner also saves only the first 3,000 characters of PipelineError.
    summary = str(caught.value).splitlines()[0]
    assert "render bundle:" in summary
    assert "ENOSPC: no space left on device" in summary
    assert len(summary) < 3000


@pytest.mark.parametrize("payload", ["not json", "[]", '{"phase": [], "error": null}'])
def test_malformed_renderer_diagnostics_do_not_mask_subprocess_failure(payload):
    stderr = ERROR_PREFIX + payload + "\noriginal failure\n"

    with pytest.raises(PipelineError) as caught:
        run(command(stderr=stderr, returncode=3))

    assert str(caught.value) == f"{Path(sys.executable).name} exited 3: {stderr}"


def test_non_renderer_failure_keeps_existing_stderr_tail(tmp_path):
    stderr = "encoder details\n" * 400 + "encoder failed\n"
    log = tmp_path / "encoder.log"

    with pytest.raises(PipelineError) as caught:
        run(command(stderr=stderr, returncode=2), log)

    assert str(caught.value) == f"{Path(sys.executable).name} exited 2: {stderr[-2500:]}"
    assert log.read_text(encoding="utf-8") == stderr


def test_success_returns_stdout_and_preserves_full_log(tmp_path):
    log = tmp_path / "run" / "render.log"
    stdout, stderr = "描画完了\n", "diagnostic warning\n"

    assert run(command(stdout, stderr), log) == stdout
    assert log.read_text(encoding="utf-8") == stdout + stderr


def test_python_children_write_utf8_unless_the_user_chose_an_encoding(monkeypatch):
    probe = [sys.executable, "-c", "import os, sys; sys.stdout.write(os.environ.get('PYTHONIOENCODING', ''))"]

    monkeypatch.delenv("PYTHONIOENCODING", raising=False)
    assert run(probe) == "utf-8"

    monkeypatch.setenv("PYTHONIOENCODING", "utf-8:strict")
    assert run(probe) == "utf-8:strict"


@pytest.mark.parametrize("operation", ["mkdir", "write_text"])
def test_log_failure_does_not_mask_original_render_failure(tmp_path, monkeypatch, operation):
    log = tmp_path / "run" / "render.log"

    def cannot_write(*args, **kwargs):
        raise PermissionError(errno.EACCES, "cannot save render log")

    monkeypatch.setattr(Path, operation, cannot_write)
    with pytest.raises(PipelineError) as caught:
        run(command(stderr=render_error(), returncode=1), log)

    assert "render bundle:" in str(caught.value).splitlines()[0]
    assert "ENOSPC" in str(caught.value).splitlines()[0]
    assert f"Could not write log {log}:" in str(caught.value)
    assert "cannot save render log" in str(caught.value)


def test_successful_process_still_reports_log_write_failure(tmp_path, monkeypatch):
    failure = OSError(errno.ENOSPC, "log filesystem full")

    def cannot_write(*args, **kwargs):
        raise failure

    monkeypatch.setattr(Path, "write_text", cannot_write)
    with pytest.raises(OSError) as caught:
        run(command(stdout="render succeeded\n"), tmp_path / "render.log")

    assert caught.value is failure
