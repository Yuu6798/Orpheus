"""設定 JSON の argv で既存の実行ファイルを呼ぶ（シェルは使わない）。Orpheus の run-adapter と同じ約束。

設定の形:
    {"tool": "generator-x", "version": "1.2.3", "license": "MIT", "weights": {"name": "…", "version": "…"},
     "argv": ["/abs/python", "/abs/wrapper.py", "--in", "{image}", "--out", "{mesh}"]}

`{名前}` は呼び出し側が渡す placeholders（入力・出力・作業フォルダ）で置き換える。実行ファイルは絶対パスで存在すること。
"""
import os
import subprocess
import time
from pathlib import Path

from ..io import ToolError, sha256


def check_config(config):
    if not isinstance(config, dict):
        raise ToolError("アダプターの設定はオブジェクトにしてください")
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise ToolError("アダプターの設定には argv（文字列の配列）が要ります")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.is_file():
        raise ToolError(f"アダプターの実行ファイルは存在する絶対パスにしてください: {argv[0]}")
    if not config.get("tool") or not config.get("version"):
        raise ToolError("アダプターの設定には tool と version が要ります")
    return config


def substitute(argv, placeholders):
    values = {"{" + key + "}": str(value) for key, value in placeholders.items()}
    result = []
    for part in argv:
        for key, value in values.items():
            part = part.replace(key, value)
        result.append(part)
    return result


def describe(config):
    """記録に残す道具の素性（コード版・重みの版・ライセンス・実行環境）。"""
    executable = Path(config["argv"][0])
    info = {"tool": config["tool"], "version": config["version"], "executable": str(executable),
            "executable_sha256": sha256(executable)}
    for key in ("license", "weights", "environment", "commit"):
        if key in config:
            info[key] = config[key]
    return info


def run(config, placeholders, log=None, cwd=None, timeout=None):
    """argv を実行し、標準出力と標準エラーを log に書く。終了コードが 0 でなければ ToolError。"""
    check_config(config)
    argv = substitute(config["argv"], placeholders)
    began = time.monotonic()
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    try:
        completed = subprocess.run(argv, cwd=cwd, env=environment, capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", shell=False, timeout=timeout, stdin=subprocess.DEVNULL)
    except OSError as error:
        raise ToolError(f"アダプターを起動できません（{argv[0]}）: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"アダプターが timeout（{timeout} 秒）を超えました: {config['tool']}") from error
    seconds = round(time.monotonic() - began, 3)
    if log:
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        Path(log).write_text(completed.stdout + completed.stderr, encoding="utf-8")
    if completed.returncode:
        raise ToolError(f"{config['tool']} が終了コード {completed.returncode} で失敗しました: {completed.stderr[-2000:]}")
    return {"argv": argv, "exit_code": completed.returncode, "seconds": seconds, **describe(config)}
