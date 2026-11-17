"""import の道（リファクタリングの安全網）: model3d は mv_pipeline を import しない約束（model3d/__init__.py）。mv_pipeline と cv2 を
読めなくした子プロセスで、model3d の全部のモジュール（Blender の部品を含む。bpy は関数の中でだけ読む）をパッケージとして読む。"""
import json
import pkgutil
import subprocess
import sys

import model3d

LOAD = r'''
import importlib, json, sys
for blocked in ("mv_pipeline", "cv2"):
    sys.modules[blocked] = None
failed = {}
for name in json.loads(sys.argv[1]):
    try:
        importlib.import_module(name)
    except ImportError as error:
        failed[name] = repr(error)
print(json.dumps(failed))
'''


def test_every_model3d_module_imports_without_mv_pipeline(tmp_path):
    names = sorted(m.name for m in pkgutil.walk_packages(model3d.__path__, "model3d."))
    assert len(names) >= 70 and "model3d.cli" in names and "model3d.blender_adapter.pass_render" in names
    completed = subprocess.run([sys.executable, "-c", LOAD, json.dumps(names)], cwd=tmp_path, capture_output=True, text=True,
                               encoding="utf-8", check=True)
    assert json.loads(completed.stdout) == {}
