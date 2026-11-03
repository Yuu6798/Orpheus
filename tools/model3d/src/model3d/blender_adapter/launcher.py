"""Blender をバックグラウンドで起動し、スクリプトと引数を渡す。版と終了コードを記録する。"""
import os
import re
import subprocess
import time
from pathlib import Path

from ..io import ToolError

OBSERVE_SCRIPT = Path(__file__).resolve().parent / "observe.py"


def argv_for(blender, script, blend=None, args=(), background=True):
    """[blender, --background, (blend), --python, script, --, *args]。Blender は `--` の後ろをスクリプトに渡す。"""
    argv = [str(blender)]
    if background:
        argv.append("--background")
    if blend is not None:
        argv.append(str(blend))
    argv.extend(["--python", str(script)])
    if args:
        argv.append("--")
        argv.extend(str(a) for a in args)
    return argv


def blender_version(blender, timeout=60):
    """`blender --version` の 1 行目（例: Blender 4.5.15）。読めなければ None。"""
    try:
        completed = subprocess.run([str(blender), "--version"], capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"Blender\s+([0-9][0-9A-Za-z.\-]*)", completed.stdout + completed.stderr)
    return match.group(1) if match else None


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
        raise ToolError(f"Blender が timeout（{timeout} 秒）を超えました") from error
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(completed.stdout + completed.stderr, encoding="utf-8")
    record = {"argv": argv, "exit_code": completed.returncode, "seconds": round(time.monotonic() - began, 3),
              "blender_version": blender_version(blender)}
    if completed.returncode:
        raise ToolError(f"Blender が終了コード {completed.returncode} で失敗しました: {completed.stderr[-2000:]}")
    return record
