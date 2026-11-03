"""テストの共通部品: CLI の実行、別プロセスの代役、簡単なメッシュ。"""
import io
import json
import os
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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


def parts_npz(directory, names=("body", "head"), with_uv=True):
    """部品ごとの npz（names と co{i}・f{i}・uv{i}）と法線の npz（n{i}）を書く。部品 i は y = i の板で -Y を向く。
    取り違えに気付けるよう、偶数番目の部品は面の分け方・u の左右・法線（+Z）を変える。"""
    import numpy as np
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    quad = np.array([[-1, 0, -1], [1, 0, -1], [1, 0, 1], [-1, 0, 1]], dtype=np.float32)
    data = {"names": np.array(names)}
    uv = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], dtype=np.float32)
    for i in range(len(names)):
        data[f"co{i}"] = quad + np.float32([0, i, 0])
        data[f"f{i}"] = np.array([[1, 2, 3], [1, 3, 0]] if i % 2 else [[0, 1, 2], [0, 2, 3]], dtype=np.int32)
        if with_uv:
            data[f"uv{i}"] = uv if i % 2 else uv[[1, 0, 3, 2]]
    np.savez(directory / "parts.npz", **data)
    np.savez(directory / "normals.npz", **{f"n{i}": np.tile(np.float32([0, -1, 0] if i % 2 else [0, 0, 1]), (4, 1))
                                           for i in range(len(names))})
    return directory / "parts.npz", directory / "normals.npz"


def stand_in_executable(directory, name, source):
    """Python の source を実行する代役の実行ファイル（引数は sys.argv[1:] に入る）。POSIX はシバン付きのスクリプト、
    Windows はシバンを使えないので、この Python で source を呼ぶ .cmd にする。"""
    directory = Path(directory)
    if os.name == "nt":
        script = directory / f"{name}_stand_in.py"
        script.write_text(source, encoding="utf-8")
        wrapper = directory / f"{name}.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\nexit /b %ERRORLEVEL%\r\n', encoding="utf-8")
        return wrapper
    wrapper = directory / name
    wrapper.write_text(f"#!{sys.executable}\n" + source, encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def symlinks_available(directory):
    """この環境でシンボリックリンクを作れるか。Windows は権限か開発者モードが無いと作れない（WinError 1314）。"""
    target, link = Path(directory) / "symlink-probe-target", Path(directory) / "symlink-probe"
    target.write_bytes(b"")
    try:
        link.symlink_to(target)
    except OSError:
        return False
    else:
        link.unlink()
        return True
    finally:
        target.unlink()


def box(size=1.0, center=(0.0, 0.0, 0.0)):
    """閉じた立方体（外向きの面）。"""
    half = size / 2.0
    mesh = extrude_profile([[-half, -half], [half, -half], [half, half], [-half, half]], size)
    mesh.vertices += [center[0], center[1], center[2] - half]
    return Mesh(mesh.vertices, mesh.faces, None, "box", mesh.units, mesh.axes, mesh.parts)
