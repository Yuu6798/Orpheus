"""Atomic files, hashes, and strictly project-relative paths."""
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]


class PipelineError(Exception):
    pass


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read_json(path):
    def invalid(value):
        raise PipelineError(f"Non-finite JSON number: {value}")
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def safe_path(root, value):
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise PipelineError(f"Invalid relative path: {value!r}")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise PipelineError(f"Path escapes project: {value!r}")
    root = Path(root).resolve()
    result = (root / value).resolve()
    if not result.is_relative_to(root) or result == root:
        raise PipelineError(f"Path escapes project: {value!r}")
    return result


def _render_error_summary(stderr):
    """Keep the renderer's cause visible before the truncated stderr tail."""
    def short(value):
        return value.strip().splitlines()[0][:300] if isinstance(value, str) and value.strip() else ""

    prefix = "[orpheus-render-error] "
    for line in stderr.splitlines():
        if not line.startswith(prefix):
            continue
        try:
            record = json.loads(line[len(prefix):])
        except (ValueError, RecursionError):
            continue
        if not isinstance(record, dict):
            continue
        error = record.get("error")
        if not isinstance(error, (dict, str)):
            continue
        causes, no_space = [], ""
        while isinstance(error, (dict, str)):
            if isinstance(error, str):
                error = {"message": error}
            detail = ": ".join(filter(None, (short(error.get("code"))[:80], short(error.get("message")))))
            detail = detail or short(error.get("name"))
            if detail:
                causes.append(detail)
            # Remotion can wrap ENOSPC in a multiline message without .code/.cause.
            for text in (error.get("message"), error.get("stack")):
                if not isinstance(text, str):
                    continue
                for cause_line in text.splitlines():
                    match = re.search(r"\bENOSPC\b", cause_line)
                    if match:
                        no_space = cause_line[max(0, match.start() - 80):match.end() + 300].strip()
                        break
            if error.get("code") == "ENOSPC" and not no_space:
                no_space = detail
            error = error.get("cause")
        # Bound the CLI summary while retaining the innermost cause. The log is complete.
        details = causes if len(causes) <= 4 else causes[:1] + causes[-3:]
        if no_space and no_space not in details:
            details.insert(0, no_space)
        phase = short(record.get("phase"))[:80] or "unknown"
        return f"render {phase}: " + " <- ".join(details or ["unknown error"])
    return ""


def run(args, log=None, cwd=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", shell=False)
    log_error = None
    if log:
        try:
            Path(log).parent.mkdir(parents=True, exist_ok=True)
            Path(log).write_text(result.stdout + result.stderr, encoding="utf-8")
        except OSError as error:
            if not result.returncode:
                raise
            log_error = f"Could not write log {log}: {error}"
    if result.returncode:
        details = [_render_error_summary(result.stderr), result.stderr[-2500:], log_error]
        raise PipelineError(f"{Path(args[0]).name} exited {result.returncode}: "
                            + "\n".join(detail for detail in details if detail))
    return result.stdout
