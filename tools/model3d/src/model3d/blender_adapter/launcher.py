"""Blender をバックグラウンドで起動し、スクリプトと引数を渡す。版と終了コードを記録する。

アドオン（VRM Add-on など）が要るときは、利用者の設定は読まずに（--factory-startup のまま）、名前を挙げたアドオンだけを
有効にする。拡張機能（Blender 4.2 以降の extensions）は、そのフォルダを環境変数 BLENDER_USER_EXTENSIONS で渡す。
"""
import os
import re
import subprocess
import time
import tomllib
from pathlib import Path

from ..adapters.process import write_log
from ..io import ToolError

OBSERVE_SCRIPT = Path(__file__).resolve().parent / "observe.py"
_VERSIONS = {}   # (path, mtime_ns, size) -> 版。同じ実行ファイルに何度も --version を聞かない
# アドオンの Python のモジュール名（拡張機能は bl_ext.<リポジトリ>.<id>。例 bl_ext.user_default.vrm）
ADDON_MODULE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*")


def addon_code(addons):
    """前置きの中身。名前を挙げたアドオンを有効にし、1 つでも有効にならなければ例外で止める。

    `--addons` の起動引数でも有効にはなるが、利用者の設定の一覧に載らないので、読み込みの後の処理（load_post）で
    アドオンの設定を探すもの（VRM Add-on 4.10.5）がエラーを出す。default_set=True で一覧に載せる。
    --factory-startup の起動では、利用者の設定のファイルは保存されない（Blender 5.2.4 で確かめた。2026-11-16）。
    """
    return ("import addon_utils\n"
            f"modules = {list(addons)!r}\n"
            "for name in modules:\n"
            "    addon_utils.enable(name, default_set=True, persistent=True)\n"
            "missing = [name for name in modules if not addon_utils.check(name)[1]]\n"
            "if missing:\n"
            "    raise RuntimeError('model3d: add-on not enabled: ' + ', '.join(missing))\n")


def addon_bootstrap(addons):
    """--python-expr で渡す 1 行の前置き。改行を引数に入れない（Windows の .cmd を通すと改行で命令が切れる）ため、
    中身（addon_code）を exec の文字列にする。"""
    return f"exec({addon_code(addons)!r})"


def argv_for(blender, script, blend=None, args=(), background=True, factory_startup=True, addons=()):
    """Python 例外を非ゼロ終了にしてから script を実行する。`--` の後ろは script の引数。

    factory_startup（既定）は利用者の設定・アドオン・起動用 .blend を読まずに起動し、PC ごとの差を結果に入れない。
    Blender 4.5.15 の Python は環境変数を見ず（ignore_environment）、Windows でも UTF-8 モードで動く（2026-11-06 実測）。
    addons を挙げると、.blend を読む前に、前置き（addon_bootstrap）でそのアドオンだけを有効にする。有効にならなければ、
    .blend を読む前に終了コード 1 で止まる（Blender 5.2.4 で確かめた。2026-11-16）。
    """
    argv = [str(blender)]
    if background:
        argv.append("--background")
    if factory_startup:
        argv.append("--factory-startup")
    if addons:
        # 引数は左から処理される。.blend より前に置き、読み込みの後の処理が動く前にアドオンを有効にする。
        # --python-exit-code は前置きの例外も非ゼロ終了にするので、前置きより前に置く。
        argv.extend(["--python-exit-code", "1", "--python-expr", addon_bootstrap(addons)])
    if blend is not None:
        argv.append(str(blend))
    # Blender は指定が無いと Python 例外があっても終了コード 0 になり得る。
    # 引数は左から処理されるので、必ず --python より前に置く。
    if not addons:
        argv.extend(["--python-exit-code", "1"])
    argv.extend(["--python", str(script)])
    if args:
        argv.append("--")
        argv.extend(str(a) for a in args)
    return argv


def check_addons(addons):
    """アドオンのモジュール名の並び（重なりなし）を確かめて tuple で返す。"""
    if addons is None:
        return ()
    if isinstance(addons, str) or not isinstance(addons, (list, tuple)):
        raise ToolError(f"アドオンはモジュール名の並びにしてください（例 [\"bl_ext.user_default.vrm\"]）: {addons!r}")
    bad = [name for name in addons if not isinstance(name, str) or not ADDON_MODULE.fullmatch(name)]
    if bad:
        raise ToolError(f"アドオンのモジュール名が読めません: {bad!r}（例 bl_ext.user_default.vrm）")
    if len(set(addons)) != len(addons):
        raise ToolError(f"アドオンの名前が重なっています: {list(addons)!r}")
    return tuple(addons)


def check_extensions(extensions):
    """拡張機能のフォルダ（BLENDER_USER_EXTENSIONS に渡す）。存在する絶対パスに限る。"""
    if extensions is None:
        return None
    path = Path(extensions)
    if not path.is_absolute() or not path.is_dir():
        raise ToolError(f"拡張機能のフォルダは存在する絶対パスにしてください: {extensions}")
    return path


def addon_record(name, extensions=None):
    """アドオンの記録（モジュール名・版・ライセンス）。拡張機能は <フォルダ>/<リポジトリ>/<id>/blender_manifest.toml から読む。
    読めないもの（古い形式のアドオン、フォルダの外のもの）の版は None。"""
    record = {"module": name, "version": None}
    parts = name.split(".")
    if extensions is None or len(parts) != 3 or parts[0] != "bl_ext":
        return record
    manifest = Path(extensions) / parts[1] / parts[2] / "blender_manifest.toml"
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return record
    record["version"] = data.get("version") if isinstance(data.get("version"), str) else None
    if isinstance(data.get("license"), list):
        record["license"] = [str(item) for item in data["license"]]
    return record


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


def run_blender(blender, script, blend=None, args=(), log=None, timeout=None, cwd=None, background=True,
                factory_startup=True, addons=(), extensions=None):
    """Blender を起動して待つ。終了コードが 0 でなければ ToolError。

    Windows の Blender 4.5.15 で、起動・ログ・終了コード・版の記録を確かめた（2026-11-06）。CI には Blender が無い。
    addons は有効にするアドオンのモジュール名、extensions は拡張機能のフォルダ（BLENDER_USER_EXTENSIONS。Blender はそこに
    .cache/compat.dat を書くので、共有の設定のフォルダではなく、写しを指す）。Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、
    有効にする・有効にならなければ止まる・設定のファイルを保存しないことを確かめた（2026-11-16）。
    """
    blender, script = Path(blender), Path(script)
    if not blender.is_absolute() or not blender.is_file():
        raise ToolError(f"Blender の実行ファイルは存在する絶対パスにしてください: {blender}")
    if not script.is_file():
        raise ToolError(f"スクリプトが見つかりません: {script}")
    if blend is not None and not Path(blend).is_file():
        raise ToolError(f".blend が見つかりません: {blend}")
    addons, extensions = check_addons(addons), check_extensions(extensions)
    if addons and not factory_startup:
        # 前置きはアドオンを利用者の設定の一覧に載せる。利用者の設定で起動すると、それが利用者の設定に残りうる
        raise ToolError("アドオンを挙げるときは factory_startup のままにしてください（利用者の設定を書き換えないため）")
    argv = argv_for(blender, script, blend, args, background, factory_startup, addons)
    began = time.monotonic()
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    if extensions is not None:
        environment["BLENDER_USER_EXTENSIONS"] = str(extensions)
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
    if addons:
        record["addons"] = [addon_record(name, extensions) for name in addons]
    if extensions is not None:
        record["extensions"] = str(extensions)
    if completed.returncode and addons and "model3d: add-on not enabled:" in completed.stdout + completed.stderr:
        missing = re.search(r"model3d: add-on not enabled: ([^\r\n']*)", completed.stdout + completed.stderr)
        where = f"拡張機能のフォルダ {extensions} とモジュール名" if extensions is not None else "拡張機能のフォルダ（extensions）とモジュール名"
        raise ToolError(f"Blender でアドオンを有効にできませんでした: {missing.group(1) if missing else ', '.join(addons)}"
                        f"（{where}を確かめてください{f'。ログ: {log}' if log else ''}）")
    if completed.returncode:
        raise ToolError(f"Blender が終了コード {completed.returncode} で失敗しました: {completed.stderr[-2000:]}")
    return record
