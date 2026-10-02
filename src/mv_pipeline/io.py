"""Atomic files, hashes, and strictly project-relative paths."""
import hashlib
import json
import os
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


def run(args, log=None, cwd=None):
    result = subprocess.run([str(a) for a in args], cwd=cwd, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", shell=False)
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(result.stdout + result.stderr, encoding="utf-8")
    if result.returncode:
        raise PipelineError(f"{Path(args[0]).name} exited {result.returncode}: {result.stderr[-2500:]}")
    return result.stdout
