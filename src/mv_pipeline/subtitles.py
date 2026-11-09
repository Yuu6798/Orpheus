"""字幕ファイルの書き出し（`mv export subtitles`）。歌詞の行の時刻から SRT・WebVTT・LRC と、動画の概要欄に貼る歌詞を書く。

    mv export subtitles SOURCE --out DIR [--name STEM] [--formats srt,vtt,lrc,description] [--field text]
                        [--lead 0.15] [--tail 0.40] [--duration SEC] [--vtt-settings "line:8% position:50% align:center"]
                        [--description-heading "■ Lyrics"] [--description-body DESCRIPTION.txt] [--force]

SOURCE（行の本文と、歌い出し・歌い終わりの秒）:
- プロジェクトのフォルダか analysis.json: lyrics の text（--field alignment_text も可）・start_s・end_s。時刻の無い行（start_s が null）は
  字幕に入れず untimed に残す（`mv export remotion-props` と同じ）。
- 行の JSON: [{"start", "end", "text", "section", "id", ...}]（"start_s"・"end_s" でもよい。本文は --field の欄。既定は text、無ければ
  display か line）。
- LRC: [mm:ss.xx]本文。行の終わりは、空の行（終わりの印）か次の行の頭。最後の行に終わりの印が無ければ --duration まで。

字幕の出入り（実制作の公開準備の字幕と同じ式）:
    出る   = 歌い出し − lead（既定 0.15 秒。0 秒より前にはしない）
    消える = min(歌い終わり + tail（既定 0.40 秒）, 次の字幕の出る時刻)。字幕は重ねない。--duration があればそこで切る
行は歌い出しの順に並べる。歌い出しが同じ行、歌い終わりが歌い出しより前の行、字幕が出ている時間が無くなる行（0 秒に寄せた
字幕のすぐ後に次の行が来る、--duration の後に出る）があれば止める。

書き出すもの（UTF-8、BOM なし、改行は LF）:
    <STEM>.srt              番号・"HH:MM:SS,mmm --> HH:MM:SS,mmm"・本文を空行で区切る
    <STEM>.vtt              "WEBVTT" と同じ字幕（時刻の小数点は "."）。--vtt-settings を時刻の後ろに付ける（例: 画面の上 8% に置く）
    <STEM>.lrc              [mm:ss.xx]本文（歌い出しの時刻。lead を引かない）。次の行まで間があれば、歌い終わりに空の行（終わりの印）
    <STEM>_description.txt  見出しと、区間（section）ごとに空行で分けた本文（時刻の無い行も入れる）。動画の概要欄に貼る形
    <STEM>_subtitles.json   記録（入力の sha256・設定・行の数・時刻の出所・書いたファイルの sha256・注意）
時刻は秒をミリ秒（LRC は 1/100 秒）に丸める（Python の round）。行の時刻の出所（推定か確認済みか）は記録と結果に数で残し、確定扱いにしない。
既にあるファイルと中身が違えば、--force を付けない限り何も書かない。

形式 subtitles-export/0.1 の記録は contracts/ の外（この module が正）。
"""
import os
import re
import tempfile
from pathlib import Path

from .io import PipelineError, atomic_json, read_json, sha256

SCHEMA = "subtitles-export/0.1"
FORMATS = ("srt", "vtt", "lrc", "description")
LEAD_S, TAIL_S = 0.15, 0.40
DESCRIPTION_HEADING = "■ Lyrics"
DESCRIPTION_LIMIT = 5000          # 動画の概要欄の上限（バイト。YouTube Data API の仕様）
_LRC = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)")


# ---- 読み込み -----------------------------------------------------------------------------------

def _text(value, where):
    """本文を 1 行ずつの空白を除いた形にする（空の行は除く）。空なら None。"""
    if value is None:
        return None
    lines = [line.strip() for line in str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    text = "\n".join(line for line in lines if line)
    if "-->" in text:
        raise PipelineError(f"{where} の本文に \"-->\" があります（SRT・WebVTT の時刻の区切りと同じ字なので書けません）")
    return text or None


def _seconds(value, where):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0:
        raise PipelineError(f"{where} の時刻は 0 以上の秒にしてください: {value!r}")
    return float(value)


def _from_analysis(data, field):
    if field not in ("text", "alignment_text"):
        raise PipelineError(f"analysis.json の lyrics の本文は text か alignment_text です（--field {field}）")
    lines = []
    for number, row in enumerate(data["lyrics"]):
        where = f"lyrics[{number}]（{row.get('id', number)}）"
        lines.append({"id": str(row.get("id", number + 1)), "text": _text(row.get(field), where), "section": None,
                      "start": _seconds(row.get("start_s"), where), "end": _seconds(row.get("end_s"), where),
                      "timing": f"{row.get('timing_source', 'unknown')}/{row.get('review_status', 'unreviewed')}"})
    return lines


def _from_lines(rows, field):
    lines = []
    for number, row in enumerate(rows):
        if not isinstance(row, dict):
            raise PipelineError(f"行の JSON の {number} 番目が {{\"start\", \"end\", \"text\"}} の形ではありません")
        where = f"行 {number}（{row.get('id', number + 1)}）"
        if field == "text":
            value = next((row[key] for key in ("text", "display", "line") if row.get(key)), None)
        elif field in row:
            value = row[field]
        else:
            raise PipelineError(f"{where} に --field の欄 {field!r} がありません")
        start = row.get("start", row.get("start_s"))
        confidence = row.get("confidence")
        lines.append({"id": str(row.get("id", number + 1)), "text": _text(value, where), "section": row.get("section"),
                      "start": _seconds(start, where), "end": _seconds(row.get("end", row.get("end_s")), where),
                      "timing": confidence if isinstance(confidence, str) and confidence else "unknown"})
        if lines[-1]["start"] is None:
            raise PipelineError(f"{where} に歌い出しの時刻（start）がありません")
    return lines


def _from_lrc(text):
    lines = []
    for raw in text.splitlines():
        found = _LRC.match(raw.strip())
        if not found:
            continue
        t = int(found[1]) * 60 + float(found[2])
        words = _text(found[3], f"LRC の [{found[1]}:{found[2]}]")
        if words is None:                    # 終わりの印（空の行）
            if lines and lines[-1]["end"] is None:
                lines[-1]["end"] = t
            continue
        lines.append({"id": str(len(lines) + 1), "text": words, "section": None, "start": t, "end": None, "timing": "lrc"})
    return lines


def read_source(path, field="text"):
    """(行の並び, 入力の種類, 読んだファイル)。行は {id, text, section, start, end, timing}（end は None のことがある）。"""
    path = Path(path)
    if path.is_dir():
        path = path / "analysis.json"
    if not path.is_file():
        raise PipelineError(f"歌詞の行が見つかりません: {path}")
    if path.suffix.lower() == ".lrc":
        return _from_lrc(path.read_text(encoding="utf-8-sig")), "lrc", path
    data = read_json(path)
    if isinstance(data, dict) and isinstance(data.get("lyrics"), list):
        return _from_analysis(data, field), "analysis", path
    if isinstance(data, list):
        return _from_lines(data, field), "lines", path
    raise PipelineError(f"{path.name} は analysis.json（lyrics を持つ）でも行の JSON（[{{start, end, text}}, …]）でもありません")


# ---- 字幕の出入り ----------------------------------------------------------------------------------

def timed(lines, duration=None):
    """時刻のある行（歌い出しの順。終わりの無い行は次の行の頭、最後は duration）と、時刻の無い行の id。本文の無い行は除く。"""
    rows = sorted((line for line in lines if line["start"] is not None and line["text"]), key=lambda line: line["start"])
    untimed = [line["id"] for line in lines if line["start"] is None and line["text"]]
    out = []
    for index, line in enumerate(rows):
        end = line["end"]
        if end is None:
            end = rows[index + 1]["start"] if index + 1 < len(rows) else duration
            if end is None:
                raise PipelineError(f"最後の行（{line['id']}）の歌い終わりがわかりません。終わりの時刻を入れるか、--duration を渡してください")
        if end < line["start"]:
            raise PipelineError(f"行 {line['id']} の歌い終わり {end:g} 秒が歌い出し {line['start']:g} 秒より前です")
        if index and line["start"] == rows[index - 1]["start"]:
            raise PipelineError(f"行 {rows[index - 1]['id']} と {line['id']} の歌い出しが同じです（字幕は重ねないので、どちらか 1 行にしてください）")
        out.append({**line, "end": end})
    return out, untimed


def cues(rows, lead=LEAD_S, tail=TAIL_S, duration=None):
    """[(出る, 消える, 本文)] と注意の文。式は実制作の公開準備の字幕と同じ（重ねない。消える時刻は次の字幕の出る時刻まで）。"""
    if not lead >= 0 or not tail >= 0:
        raise PipelineError(f"--lead と --tail は 0 以上にしてください: {lead}, {tail}")
    out, warnings = [], []
    for index, row in enumerate(rows):
        a = row["start"] - lead
        e = row["end"] + tail
        if index + 1 < len(rows):
            e = min(e, rows[index + 1]["start"] - lead)      # 字幕は重ねない
        if a < 0:
            warnings.append(f"行 {row['id']}: 歌い出し {row['start']:g} 秒が lead {lead:g} 秒より早いので、0 秒から出します")
            a = 0.0
        if duration is not None:
            if a >= duration:
                raise PipelineError(f"行 {row['id']} の字幕の出る時刻 {a:g} 秒が --duration {duration:g} 秒より後です")
            if e > duration:
                warnings.append(f"行 {row['id']}: 消える時刻 {e:g} 秒を --duration {duration:g} 秒で切りました")
                e = duration
        if not e > a:
            raise PipelineError(f"行 {row['id']} の字幕が出ている時間がありません（出る {a:.3f} 秒、消える {e:.3f} 秒）。"
                                "次の行の歌い出しが近すぎます。行を 1 つにまとめるか、--lead を小さくしてください")
        out.append((a, e, row["text"]))
    return out, warnings


# ---- 書き出しの形 ---------------------------------------------------------------------------------

def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def vtt_time(t):
    return srt_time(t).replace(",", ".")


def _centiseconds(t):
    return int(round(t * 100))


def lrc_time(t):
    m, cs = divmod(_centiseconds(t), 6000)
    s, cs = divmod(cs, 100)
    return f"[{m:02d}:{s:02d}.{cs:02d}]"


def srt(cue_list):
    return "\n".join(f"{k}\n{srt_time(a)} --> {srt_time(e)}\n{t}\n" for k, (a, e, t) in enumerate(cue_list, 1))


def vtt(cue_list, settings=""):
    tail = f" {settings.strip()}" if settings and settings.strip() else ""
    return "WEBVTT\n\n" + "\n".join(f"{k}\n{vtt_time(a)} --> {vtt_time(e)}{tail}\n{t}\n" for k, (a, e, t) in enumerate(cue_list, 1))


def lrc(rows):
    """歌い出しの時刻の行と、次の行まで間があるときの終わりの印。複数行の本文は " / " でつなぐ（LRC は 1 行 1 時刻）。"""
    out = []
    for index, row in enumerate(rows):
        out.append(f"{lrc_time(row['start'])}{row['text'].replace(chr(10), ' / ')}")
        following = rows[index + 1]["start"] if index + 1 < len(rows) else None
        if following is None or _centiseconds(row["end"]) < _centiseconds(following):     # 次の行の頭と同じ印にはしない
            out.append(lrc_time(row["end"]))
    return "\n".join(out) + "\n"


def description(lines, heading=DESCRIPTION_HEADING):
    """見出しと、区間ごとに空行で分けた本文（入力の順。時刻の無い行も入れる）。"""
    blocks, current = [], object()
    for line in lines:
        if not line["text"]:
            continue
        if line["section"] != current:
            blocks.append([])
            current = line["section"]
        blocks[-1].append(line["text"])
    text = (f"{heading}\n" if heading else "") + "\n\n".join("\n".join(block) for block in blocks) + "\n"
    bad = sorted({ch for ch in text if ch in "<>"})
    if bad:
        raise PipelineError(f"概要欄の歌詞に使えない字 {' '.join(bad)} があります（YouTube の概要欄は < と > を受け付けません）")
    return text


# ---- 実行 ------------------------------------------------------------------------------------------

def _write_bytes(path, data):
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def export(source, out, name=None, formats=("srt", "vtt", "lrc"), field="text", lead=LEAD_S, tail=TAIL_S, duration=None,
           vtt_settings="", description_heading=DESCRIPTION_HEADING, description_body=None, force=False):
    """字幕ファイルを書き、結果の辞書を返す。"""
    unknown = [f for f in formats if f not in FORMATS]
    if unknown or not formats:
        raise PipelineError(f"--formats は {', '.join(FORMATS)} から選んでください: {', '.join(unknown) or '（空）'}")
    if duration is not None and not duration > 0:
        raise PipelineError(f"--duration は 0 より大きい秒にしてください: {duration}")
    lines, kind, path = read_source(source, field)
    rows, untimed = timed(lines, duration)
    if not rows:
        raise PipelineError(f"時刻のある歌詞の行がありません（{path.name}）")
    cue_list, warnings = cues(rows, lead, tail, duration)
    if untimed:
        warnings.append(f"時刻の無い行 {len(untimed)} 行は字幕に入れていません: {', '.join(untimed)}")
    empty = [line["id"] for line in lines if not line["text"]]
    if empty:
        warnings.append(f"本文の無い行 {len(empty)} 行は飛ばしました: {', '.join(empty)}")
    stem = name or (path.parent.name if kind == "analysis" else path.stem)
    out = Path(out)
    texts = {}
    if "srt" in formats:
        texts["srt"] = (out / f"{stem}.srt", srt(cue_list))
    if "vtt" in formats:
        texts["vtt"] = (out / f"{stem}.vtt", vtt(cue_list, vtt_settings))
    if "lrc" in formats:
        texts["lrc"] = (out / f"{stem}.lrc", lrc(rows))
    fit = None
    if "description" in formats:
        lyrics_text = description(lines, description_heading)
        texts["description"] = (out / f"{stem}_description.txt", lyrics_text)
        size = len(lyrics_text.encode("utf-8"))
        fit = {"bytes": size}
        if description_body is not None:
            body = Path(description_body).read_text(encoding="utf-8-sig")
            total = len((body + "\n" + lyrics_text).encode("utf-8"))
            fit.update({"body": str(description_body), "total_bytes": total, "limit_bytes": DESCRIPTION_LIMIT})
            if total > DESCRIPTION_LIMIT:
                raise PipelineError(f"概要欄の本文と歌詞を合わせると {total} バイトで、上限 {DESCRIPTION_LIMIT} バイトを超えます")
    data = {key: (target, text.encode("utf-8")) for key, (target, text) in texts.items()}
    changed = [str(target) for target, payload in data.values() if target.is_file() and target.read_bytes() != payload]
    if changed and not force:
        raise PipelineError("中身の違うファイルが既にあります（上書きするなら --force）: " + ", ".join(changed))
    timing = {}
    for row in rows:
        timing[row["timing"]] = timing.get(row["timing"], 0) + 1
    out.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for key, (target, payload) in data.items():
        state = "unchanged" if target.is_file() and target.read_bytes() == payload else "written"
        if state == "written":
            _write_bytes(target, payload)
        outputs[key] = {"path": target.name, "sha256": sha256(target), "bytes": len(payload), "state": state}
    record = {"schema_version": SCHEMA, "source": {"path": str(path), "kind": kind, "sha256": sha256(path)}, "field": field,
              "settings": {"lead_s": lead, "tail_s": tail, "duration_s": duration, "vtt_settings": vtt_settings.strip(),
                           "description_heading": description_heading if "description" in formats else None},
              "lines": len(lines), "cues": len(cue_list), "untimed": untimed, "timing": timing,
              "first": srt_time(cue_list[0][0]), "last": srt_time(cue_list[-1][1]), "description": fit,
              "outputs": outputs, "warnings": warnings}
    record_path = out / f"{stem}_subtitles.json"
    atomic_json(record_path, record)
    return {"record": str(record_path), "outputs": {key: str(out / value["path"]) for key, value in outputs.items()},
            "cues": len(cue_list), "untimed": len(untimed), "timing": timing, "first": record["first"], "last": record["last"],
            "description": fit, "warnings": warnings}
