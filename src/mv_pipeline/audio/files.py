"""mv audio の記録の置き場所と書き出し（grid・hits・keys で共通）。

- 入力が音源なら、記録は音源の隣の `<音源の名前><suffix>`。analysis.json のあるフォルダなら、analysis.json の音源を使い
  （sha256 が違えば止める）、隣の `analysis<suffix>` に書く。
- 出力が入力（音源・analysis.json など）と同じファイルなら、--force でも書かない。
- 同じ中身のファイルがあれば書き直さず unchanged、中身が違えば --force が要る。新しく作った一時ファイル（ランダムな名前）から
  置き換える（決まった名前だと、既にある同じ名前のファイルを消したり、それが入力へのリンクなら入力を書き換えたりする）。
"""
import json
import os
import tempfile
from pathlib import Path

from ..io import PipelineError, read_json, safe_path, sha256


def same_file(path):
    """同じファイルかを比べるための形（絶対パスにして、Windows では大文字と小文字をそろえる）。"""
    return os.path.normcase(str(Path(path).resolve()))


def source(source, suffix):
    """(音源のパス, 記録に書く音源のパス, 既定の出力, 入力 {same_file: 名前})。"""
    source = Path(source)
    if source.is_dir():
        analysis_path = source / "analysis.json"
        if not analysis_path.is_file():
            raise PipelineError(f"フォルダに analysis.json がありません: {source}（音源のファイルを直接渡すこともできます）")
        analysis = read_json(analysis_path)
        audio = analysis.get("audio") if isinstance(analysis, dict) else None
        if not isinstance(audio, dict) or not audio.get("path"):
            raise PipelineError(f"analysis.json に audio.path がありません: {analysis_path}")
        path = safe_path(source, audio["path"])
        if not path.is_file():
            raise PipelineError(f"analysis.json の音源が見つかりません: {path}")
        if audio.get("sha256") and audio["sha256"] != sha256(path):
            raise PipelineError(f"音源が analysis.json の sha256 と違います（{audio['path']}）。音源か analysis.json を確かめてください")
        return path, audio["path"], source / f"analysis{suffix}", {same_file(path): "音源", same_file(analysis_path): "analysis.json"}
    if not source.is_file():
        raise PipelineError(f"音源が見つかりません: {source}")
    return source, source.name, source.with_name(source.stem + suffix), {same_file(source): "音源"}


def refuse_inputs(out, inputs):
    """out が入力のどれかと同じファイルなら止める（置き換えると入力が消えるので、--force でも書かない）。"""
    if same_file(out) in inputs:
        raise PipelineError(f"--out が{inputs[same_file(out)]}と同じファイルです（入力は --force でも上書きしません）: {out}")


def write_json(out, record, force, indent=1):
    """record を JSON で書き、状態（written・unchanged）を返す。中身の違うファイルがあれば force が要る。"""
    out = Path(out)
    text = json.dumps(record, ensure_ascii=False, indent=indent, allow_nan=False) + "\n"
    if out.exists():
        if out.read_text(encoding="utf-8", errors="replace") == text:
            return "unchanged"
        if not force:
            raise PipelineError(f"{out} は中身の違うファイルとして既にあります。置き換えるなら --force を付けてください")
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
        os.replace(temporary, out)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return "written"
