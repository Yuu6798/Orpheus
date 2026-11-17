"""描いたコマのフォルダの入力の控え（model3d_inputs.json。model3d-render-inputs/0.1）。

render.stage と render.passes は、描いてあるコマのファイルをとばして続きから描ける。入力（.blend・トラック・カメラ・設定・描く台本・
Blender の版）を変えたのに同じフォルダへ描くと、とばしたコマは前の入力の絵のまま残る（リポジトリ外の 3 作目の VRM のダンスで、作り直した
踊りの下見に古いコマが混ざった）。そこで、描く前に、コマのファイルごとに「どの入力で描くか」をフォルダの控えに書いておき、次に同じフォルダへ
描くときは、とばすコマ（もうファイルがあるコマ）の控えが今の入力と同じときだけ続きから描く。違うコマか、控えの無いコマ（どの入力で描いたか
分からない）が 1 つでもあれば、描く前に止める。

- 入力のまとまり（key）: 工程の名前と、入力のファイルの sha256・設定（描く絵を変えない only などは除く）・描く台本（Blender が読む
  blender_adapter の台本と、それが読む同じフォルダの台本）の sha256・Blender の版など。sha256(正規の JSON) の先頭 16 文字を番号にする。
- 控え: {"keys": {番号: key}, "files": {コマのファイルのフォルダからの相対パス: 番号}}。コマのファイルごとなので、クリップごとに別の工程で
  同じフォルダへ描く（docs/model3d.md の「動画にまとめる」）ときも、ほかの工程のコマには触らない。
- 書くのは描く前（途中で止まっても、描いたコマには控えがある）。同じフォルダへ同時に描く別のプロセスと控えを取り合わないよう、書く間は
  フォルダの .lock で待ち合わせる。
"""
import ast
import time
from pathlib import Path

from .io import ToolError, atomic_json, canonical_json, directory_lock, read_json, sha256, sha256_text

NAME = "model3d_inputs.json"
SCHEMA = "model3d-render-inputs/0.1"
LOCK_WAIT_S = 10.0
LABELS = {"operation": "工程", "blend": ".blend", "track": "トラック", "cameras": "カメラ（cameras）", "bones": "骨の通り道（bones）",
          "settings": "設定", "scripts": "描く台本", "blender": "Blender の版", "addons": "アドオン", "images": "表情の絵"}


def script_files(script):
    """台本と、台本が読む同じフォルダの台本（import をたどって全部）。ファイルの名前の順。"""
    script = Path(script)
    folder, found, todo = script.parent, {}, [script]
    while todo:
        path = todo.pop()
        if path.name in found:
            continue
        found[path.name] = path
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [alias.name for alias in node.names] if node.level and node.module is None else \
                    [node.module.split(".")[0]] if node.module else []
            else:
                continue
            todo += [folder / f"{name}.py" for name in names if (folder / f"{name}.py").is_file()]
    return [found[name] for name in sorted(found)]


def scripts(script):
    """描く台本の sha256（ファイルの名前 → sha256）。"""
    return {path.name: sha256(path) for path in script_files(script)}


def file_sha(path):
    return None if path is None else sha256(path)


def key_id(key):
    return sha256_text(canonical_json(key))[:16]


def _setting_names(old, new):
    old, new = old or {}, new or {}
    return sorted(name for name in set(old) | set(new) if old.get(name) != new.get(name))


def differences(old, new):
    """2 つの key の違い（読める短い文の並び）。"""
    out = []
    for name in sorted(set(old or {}) | set(new or {})):
        a, b = (old or {}).get(name), (new or {}).get(name)
        if a == b:
            continue
        label = LABELS.get(name, name)
        if name == "settings":
            out.append(f"{label}（{'・'.join(_setting_names(a, b))}）")
        elif name == "scripts":
            out.append(f"{label}（{'・'.join(_setting_names(a, b))}）")
        elif name == "blender":
            out.append(f"{label}（{a} → {b}）")
        else:
            out.append(label)
    return out


def read(directory):
    """フォルダの控え（無ければ空の控え）。形の違う控えは止める。"""
    path = Path(directory) / NAME
    if not path.is_file():
        return {"schema_version": SCHEMA, "keys": {}, "files": {}}
    record = read_json(path)
    if (not isinstance(record, dict) or record.get("schema_version") != SCHEMA or not isinstance(record.get("keys"), dict)
            or not isinstance(record.get("files"), dict)):
        raise ToolError(f"コマのフォルダの入力の控えが読めません（{SCHEMA} ではない）: {path}")
    return record


def _first(names, count=5):
    shown = ", ".join(names[:count])
    return shown + (" …" if len(names) > count else "")


def check(directory, key, files, where):
    """フォルダにもうあるコマのファイル（files のうち、ファイルのあるもの）の控えが key と同じかを確かめる。違うか控えが無ければ止める。
    files はフォルダからの相対パス（/ 区切り）。(控え, とばす数) を返す。"""
    directory = Path(directory)
    record = read(directory)
    current = key_id(key)
    present = [name for name in files if (directory / name).is_file()]
    unknown = [name for name in present if name not in record["files"]]
    other = {}
    for name in present:
        recorded = record["files"].get(name)
        if recorded is not None and recorded != current:
            other.setdefault(recorded, []).append(name)
    if unknown:
        raise ToolError(f"{where}: コマのフォルダ {directory} に、入力の控え（{NAME}）の無いコマが {len(unknown)} 個あります"
                        f"（{_first(unknown)}）。どの入力で描いたコマか分からないので、続きから描けません。別のフォルダへ描くか、"
                        "そのコマを消してから描いてください")
    if other:
        names = sorted(n for group in other.values() for n in group)
        reasons = sorted({reason for recorded in other for reason in differences(record["keys"].get(recorded), key)})
        raise ToolError(f"{where}: コマのフォルダ {directory} の {len(names)} 個のコマ（{_first(names)}）は、別の入力で描いたものです"
                        f"（違うもの: {'、'.join(reasons) or '控えの番号'}）。続きから描けるのは、同じ入力で描くときだけです（とばしたコマが"
                        "前の絵のまま残るため）。別のフォルダへ描くか、そのコマを消してから描いてください")
    return record, len(present)


def _locked(directory):
    """フォルダの .lock を、LOCK_WAIT_S 秒まで待って取る。"""
    deadline = time.monotonic() + LOCK_WAIT_S
    while True:
        try:
            lock = directory_lock(directory)
            lock.__enter__()
            return lock
        except ToolError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.05)


def claim(directory, key, files):
    """files（フォルダからの相対パス）を key で描く、と控えに書く（前の控えは読み直してから足す）。使われなくなった key は消す。"""
    directory = Path(directory)
    lock = _locked(directory)
    try:
        record = read(directory)
        current = key_id(key)
        record["keys"][current] = key
        for name in files:
            record["files"][name] = current
        used = set(record["files"].values())
        record["keys"] = {k: v for k, v in sorted(record["keys"].items()) if k in used}
        record["files"] = dict(sorted(record["files"].items()))
        atomic_json(directory / NAME, record)
    finally:
        lock.__exit__(None, None, None)
    return current
