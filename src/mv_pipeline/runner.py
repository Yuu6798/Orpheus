"""Single-project process lock and content-addressed stage journal."""
import hashlib
import json
import os
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .io import PipelineError, atomic_json, read_json, safe_path, sha256


def now():
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def project_lock(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    # The OS releases this lock even if the process is killed. Do not unlink it:
    # unlinking allows two processes to lock different inodes at the same path.
    with (root / ".lock").open("a+b") as stream:
        stream.seek(0, 2)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise PipelineError("Project is already running in another process") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def cache_key(stage, inputs, settings=None):
    return hashlib.sha256(json.dumps([stage, inputs, settings], sort_keys=True).encode()).hexdigest()


class Runner:
    def __init__(self, root):
        self.root = Path(root)
        self.path = self.root / "job.json"
        self.job = read_json(self.path) if self.path.exists() else {
            "job_id": uuid.uuid4().hex, "input_hashes": {}, "stages": {}, "current_run_id": None}

    def save(self):
        atomic_json(self.path, self.job)

    def stale(self, *names):
        for name in names:
            if name in self.job["stages"]:
                self.job["stages"][name]["status"] = "stale"
        self.save()

    def execute(self, name, key, action, validate_output=None):
        previous = self.job["stages"].get(name, {})
        outputs = previous.get("outputs", {})
        valid = bool(outputs) and all(safe_path(self.root, p).is_file() and sha256(safe_path(self.root, p)) == h for p, h in outputs.items())
        if previous.get("key") == key and previous.get("status") in {"succeeded", "running"} and valid:
            if validate_output:
                validate_output()
            previous["status"] = "succeeded"
            self.save()
            return list(outputs)
        stage = {"status": "running", "key": key, "started_at": now(), "ended_at": None,
                 "outputs": {}, "exit_code": None, "error": None}
        self.job["stages"][name] = stage
        self.save()
        try:
            paths = action()
            if validate_output:
                validate_output()
            stage.update(status="succeeded", outputs={p: sha256(safe_path(self.root, p)) for p in paths}, exit_code=0)
            return paths
        except Exception as error:
            stage.update(status="failed", exit_code=1, error=str(error)[:3000])
            raise
        finally:
            stage["ended_at"] = now()
            self.save()

    def waiting(self, name, message):
        self.job["stages"][name] = {"status": "waiting_input", "error": message, "ended_at": now()}
        self.save()


def run_id():
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
