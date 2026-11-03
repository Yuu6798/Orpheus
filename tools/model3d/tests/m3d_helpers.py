"""テストの共通部品: CLI の実行と、簡単なメッシュ。"""
import io
import json
from contextlib import redirect_stderr, redirect_stdout

from model3d.cli import main
from model3d.mesh import Mesh, extrude_profile


def run_cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([str(a) for a in args])
    text = out.getvalue()
    try:
        result = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        result = text
    return code, result, err.getvalue()


def box(size=1.0, center=(0.0, 0.0, 0.0)):
    """閉じた立方体（外向きの面）。"""
    half = size / 2.0
    mesh = extrude_profile([[-half, -half], [half, -half], [half, half], [-half, half]], size)
    mesh.vertices += [center[0], center[1], center[2] - half]
    return Mesh(mesh.vertices, mesh.faces, None, "box", mesh.units, mesh.axes, mesh.parts)
