"""原子的な書き込み、ハッシュ、正規化 JSON、ロック。mv_pipeline と同じ約束だが、依存はしない。"""
import hashlib
import json
import os
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


class ToolError(Exception):
    """利用者に見せる失敗。CLI は Error: に続けて表示し、終了コード 1 で終わる。"""


def now():
    return datetime.now(timezone.utc).isoformat()


def stamp():
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def sha256_text(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_json(data):
    """鍵の順と浮動小数点の表記を固定した JSON（ハッシュと key 用）。NaN・無限は拒否する。"""
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def read_json(path):
    def invalid(value):
        raise ToolError(f"JSON に非有限の数があります: {value}（{path}）")
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)
    except json.JSONDecodeError as error:
        raise ToolError(f"JSON を読めません: {path}（{error}）") from error


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
    """root の中の相対パスだけを許す（`..`・絶対パス・ドライブ文字・バックスラッシュを拒否）。"""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise ToolError(f"相対パスとして読めません: {value!r}")
    rel = PurePosixPath(value)
    if rel.is_absolute() or ".." in rel.parts:
        raise ToolError(f"パスが外へ出ています: {value!r}")
    root = Path(root).resolve()
    result = (root / value).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ToolError(f"パスが外へ出ています: {value!r}")
    return result


def resolve_path(base, value):
    """recipe や設定が参照するファイル。絶対パスはそのまま、相対パスは base の中だけ（外へは出られない）。"""
    if not isinstance(value, str) or not value:
        raise ToolError(f"パスが文字列ではありません: {value!r}")
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return safe_path(base, value)


@contextmanager
def directory_lock(root):
    """同じ出力先を 2 つのプロセスで同時に書かないためのロック。OS がプロセス終了時に外す（ファイルは消さない）。"""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
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
            raise ToolError(f"出力先を別のプロセスが使っています: {root}") from error
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
