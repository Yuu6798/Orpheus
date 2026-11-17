"""model3d の CLI の金型（リファクタリングの安全網）: 全部のサブコマンドの help と引数の表を、tests/data/cli_golden_model3d.txt と比べる。

help は COLUMNS を広く固定して、折り返さずに比べる（折り返しの位置は Python の版で違う）。引数の表には、help に出ない型・既定値・
const も並べる。並びは登録の順（help の並びと同じ）。CLI を意図して変えたときは、環境変数 ORPHEUS_UPDATE_GOLDEN=1 で走らせて
金型を書き直し、差分を確かめてからコミットする。"""
import argparse
import difflib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from model3d import cli

GOLDEN = Path(__file__).parent / "data" / "cli_golden_model3d.txt"


def _parsers(parser, path):
    """(コマンドの道, parser) を、登録の順に深さ優先で。"""
    yield path, parser
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield from _parsers(sub, f"{path} {name}")


def _row(action):
    """引数 1 つの表の行（JSON 1 行）。サブコマンドの choices は名前だけ。--version の版の文字は help に出ないので並べない。"""
    choices = list(action.choices) if isinstance(action, argparse._SubParsersAction) else action.choices
    return json.dumps({"options": action.option_strings, "dest": action.dest, "action": type(action).__name__,
                       "nargs": action.nargs, "const": action.const, "default": action.default,
                       "type": getattr(action.type, "__name__", action.type), "choices": choices,
                       "required": action.required, "metavar": action.metavar}, ensure_ascii=False, default=str)


def golden_text(parser, name):
    return "".join(f"==== {path}\n{sub.format_help()}---- arguments\n" + "".join(f"{_row(a)}\n" for a in sub._actions)
                   for path, sub in _parsers(parser, name))


def test_every_model3d_command_matches_the_golden(monkeypatch):
    monkeypatch.setenv("COLUMNS", "1000")
    text = golden_text(cli.parser(), "model3d")
    if os.environ.get("ORPHEUS_UPDATE_GOLDEN") == "1":
        GOLDEN.write_text(text, encoding="utf-8", newline="\n")
        pytest.skip(f"金型を書き直した: {GOLDEN}")
    expected = GOLDEN.read_text(encoding="utf-8")
    if text != expected:
        diff = "".join(difflib.unified_diff(expected.splitlines(True), text.splitlines(True), "金型", "いまの CLI", n=1))
        pytest.fail(f"model3d の CLI が金型と違います（意図した変更なら ORPHEUS_UPDATE_GOLDEN=1 で書き直す）:\n{diff[:20000]}")
    assert text.count("\n==== ") + 1 >= 35         # サブコマンドを全部たどっている（いまは model3d を含めて 39）


def test_the_module_entry_point_uses_the_same_parser(monkeypatch):
    """`python -m model3d.cli`（model3d.exe の代わりの入口）も同じ parser を使い、同じ help を出す。"""
    env = {**os.environ, "COLUMNS": "1000", "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run([sys.executable, "-m", "model3d.cli", "--help"], capture_output=True, text=True, encoding="utf-8",
                               env=env, check=True)
    monkeypatch.setenv("COLUMNS", "1000")
    assert completed.stdout == cli.parser().format_help()
