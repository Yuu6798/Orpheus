"""Blender をバックグラウンドで起動し、スクリプトと引数を渡す。版と終了コードを記録する。"""
import os
import re
import subprocess
import time
from pathlib import Path

from ..adapters.process import write_log
from ..io import ToolError

OBSERVE_SCRIPT = Path(__file__).resolve().parent / "observe.py"
_VERSIONS = {}   # (path, mtime_ns, size) -> 版。同じ実行ファイルに何度も --version を聞かない


def argv_for(blender, script, blend=None, args=(), background=True):
    """Python 例外を非ゼロ終了にしてから script を実行する。`--` の後ろは script の引数。"""
    argv = [str(blender)]
    if background:
        argv.append("--background")
    if blend is not None:
        argv.append(str(blend))
    # Blender は指定が無いと Python 例外があっても終了コード 0 になり得る。
    # 引数は左から処理されるので、必ず --python より前に置く。
    argv.extend(["--python-exit-code", "1", "--python", str(script)])
    if args:
        argv.append("--")
        argv.extend(str(a) for a in args)
    return argv


def blender_version(blender, timeout=60):
    """`blender --version` の 1 行目（例: Blender 4.5.15）。読めなければ None。実行ファイルごとに 1 回だけ聞く。"""
    try:
        stat = Path(blender).stat()
        key = (str(blender), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return None
    if key in _VERSIONS:
        return _VERSIONS[key]
    try:
        completed = subprocess.run([str(blender), "--version"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Blender\s+([0-9][0-9A-Za-z.\-]*)", completed.stdout + completed.stderr)
    _VERSIONS[key] = match.group(1) if match else None
    return _VERSIONS[key]


def run_blender(blender, script, blend=None, args=(), log=None, timeout=None, cwd=None, background=True):
    """Blender を起動して待つ。終了コードが 0 でなければ ToolError。未検証（この環境に Blender が無い）。"""
    blender, script = Path(blender), Path(script)
    if not blender.is_absolute() or not blender.is_file():
        raise ToolError(f"Blender の実行ファイルは存在する絶対パスにしてください: {blender}")
    if not script.is_file():
        raise ToolError(f"スクリプトが見つかりません: {script}")
    if blend is not None and not Path(blend).is_file():
        raise ToolError(f".blend が見つかりません: {blend}")
    argv = argv_for(blender, script, blend, args, background)
    began = time.monotonic()
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        completed = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except OSError as error:
        raise ToolError(f"Blender を起動できません（{blender}）: {error}") from error
    except subprocess.TimeoutExpired as error:
        write_log(log, error.stdout, error.stderr)
        raise ToolError(f"Blender が timeout（{timeout} 秒）を超えました（ログ: {log}）") from error
    write_log(log, completed.stdout, completed.stderr)
    record = {"argv": argv, "exit_code": completed.returncode, "seconds": round(time.monotonic() - began, 3),
              "blender_version": blender_version(blender)}
    if completed.returncode:
        raise ToolError(f"Blender が終了コード {completed.returncode} で失敗しました: {completed.stderr[-2000:]}")
    return record
