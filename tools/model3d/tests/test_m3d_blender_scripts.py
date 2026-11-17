"""Blender の部品を台本として読めるか（リファクタリングの安全網）: Blender は `--python` で渡した台本のフォルダを sys.path に足さず、
Blender の Python には model3d も入っていない。部品ごとに、子プロセスで model3d・mv_pipeline・bpy を読めなくし、runpy で
（`__main__` でない名前で、main を動かさずに）台本として読み込む。同じフォルダの部品は、部品が自分のフォルダを sys.path に足して
から読む（パッケージとして読むテストでは相対 import）。CI には Blender が無いので、部品の main の本体はここでは動かさない。"""
import subprocess
import sys
from pathlib import Path

import pytest

from model3d import blender_adapter

FOLDER = Path(blender_adapter.__file__).resolve().parent
NOT_SCRIPTS = {"__init__.py", "launcher.py"}          # launcher は Blender の外で Blender を起動する側（パッケージの中でだけ読む）
SCRIPTS = sorted(p.name for p in FOLDER.glob("*.py") if p.name not in NOT_SCRIPTS)

LOAD = r'''
import runpy, sys
for blocked in ("model3d", "mv_pipeline", "bpy"):
    sys.modules[blocked] = None
script, folder = sys.argv[1], sys.argv[2]
if any(entry and entry.rstrip("/\\") == folder for entry in sys.path):
    raise SystemExit(f"部品のフォルダが最初から sys.path にある: {folder}")
runpy.run_path(script, run_name="blender_part")
'''


def test_the_parts_are_listed():
    assert len(SCRIPTS) >= 30 and {"point_track.py", "stage_render.py", "pass_render.py", "observe.py"} <= set(SCRIPTS)


@pytest.mark.parametrize("name", SCRIPTS)
def test_each_part_loads_as_a_script_outside_the_package(name, tmp_path):
    completed = subprocess.run([sys.executable, "-c", LOAD, str(FOLDER / name), str(FOLDER)], cwd=tmp_path, capture_output=True,
                               text=True, encoding="utf-8", errors="replace")
    assert completed.returncode == 0, completed.stderr[-3000:]
