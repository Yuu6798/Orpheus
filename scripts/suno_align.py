"""Suno の歌詞アラインメント（語ごとの秒）を、mv import-alignment の共通形式へ変換する。

標準ライブラリだけで動き、ネットワークには出ない（Suno の応答 JSON は先に保存しておく）。
Suno の秒はモデルの推定なので、取り込んだ行は unreviewed のまま。語の並びから歌詞の行を
一意に決められないとき（行数や字の不一致、行の境目をまたぐ語）は、時刻を推さず unknown・null にする。
"""
import argparse
import json
import math
import re
import sys
import unicodedata
from pathlib import Path

TOOL = "suno-lyrics-alignment"
DEFAULT_VERSION = "not reported by Suno"
TAG = re.compile(r"\[[^\[\]\n]*\]")  # [Intro] [Verse] [Chorus] などの見出し（歌われない）
OUTSIDE = "切り出しの範囲の外（--source-offset-s と mv init の --start・--duration を確かめる）"


def normalized(text):
    """字の比較用。全角半角・大文字小文字・空白・記号の違いを無視する。"""
    return "".join(c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum())


def seconds(value):
    """有限の数なら float、それ以外（null・文字列・真偽値・NaN）は None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return float(value)


def response_words(response):
    if not isinstance(response, dict) or not isinstance(response.get("words"), list):
        raise ValueError("Suno の応答に words の配列がありません")
    status = response.get("status", "complete")
    if status != "complete":
        raise ValueError(f"Suno の整列が使えません（status={status}）。processing なら待って取り直し、"
                         "unavailable なら Suno ではこの曲の整列を作れません")
    if not all(isinstance(w, dict) and isinstance(w.get("word"), str) for w in response["words"]):
        raise ValueError("Suno の words の要素に word の文字列がありません")
    return response["words"]


def sung_lines(words):
    """Suno の語を、歌われる行に分ける。

    語の文字列をつないだ全文を改行で切り、見出しと行の両端の空白を外す。改行は次の行の頭の語に
    付いて来る（"\\nしろい"）が、前の語の後ろにあっても全文の上では同じ。最後の語の後ろの改行
    （"…\\n\\n"）は空の行になるだけで、数えない。行の中の空白（" そら"）は行の字に残す。
    行の秒は頭の語の start_s から終わりの語の end_s まで。頭か終わりの語が success: false なら、
    Suno が隣の語から推した秒なので interpolated にする。
    """
    text = "".join(w["word"] for w in words)
    owner = [index for index, w in enumerate(words) for _ in w["word"]]
    hidden = {i for m in TAG.finditer(text) for i in range(m.start(), m.end())}
    lines, begin = [], 0
    for end in [i for i, c in enumerate(text) if c == "\n"] + [len(text)]:
        keep = [i for i in range(begin, end) if i not in hidden]
        while keep and text[keep[0]].isspace():
            keep.pop(0)
        while keep and text[keep[-1]].isspace():
            keep.pop()
        if keep:
            parts = {}
            for i in keep:
                parts[owner[i]] = parts.get(owner[i], "") + text[i]
            lines.append({"text": "".join(parts.values()), "parts": list(parts.items())})
        begin = end + 1
    uses = {}
    for line in lines:
        for index, _ in line["parts"]:
            uses[index] = uses.get(index, 0) + 1
    for line in lines:
        first, last = line["parts"][0][0], line["parts"][-1][0]
        start, end = seconds(words[first].get("start_s")), seconds(words[last].get("end_s"))
        line.update(start_s=None, end_s=None, timing_source="unknown")
        if uses[first] > 1 or uses[last] > 1:
            line["reason"] = "1 つの語が行の境目をまたいでいる（行の境目の秒が分からない）"
        elif start is None or end is None or not start < end:
            line["reason"] = "行の頭か終わりの語に使える秒がない"
        else:
            line.update(start_s=start, end_s=end, timing_source="model")
            if words[first].get("success") is not True or words[last].get("success") is not True:
                line.update(timing_source="interpolated", reason="行の頭か終わりの語を Suno が置けず、隣の語から推した秒")
    return lines


def inside(line, offset_s, duration_s):
    return line["start_s"] is not None and 0 <= line["start_s"] + offset_s < line["end_s"] + offset_s <= duration_s


def choose(lines, count, offset_s, duration_s):
    """歌詞の行に当てる Suno の行と、当てられないときの理由。

    Suno の行が歌詞と同じ数ならそのまま。違えば、切り出しの中に収まる行（続いた並び）が
    歌詞と同じ数のときだけ、それを当てる（mv init --start/--duration で曲の一部を使うとき）。
    """
    if len(lines) == count:
        return lines, None
    within = [k for k, line in enumerate(lines) if inside(line, offset_s, duration_s)]
    starts = [line["start_s"] for line in lines if line["start_s"] is not None]
    ordered = all(a <= b for a, b in zip(starts, starts[1:]))
    if within and len(within) == count and ordered and within == list(range(within[0], within[-1] + 1)):
        if all(lines[k]["start_s"] is not None for k in (within[0] - 1, within[-1] + 1) if 0 <= k < len(lines)):
            return [lines[k] for k in within], None
        return None, "切り出しのすぐ外にある Suno の行の秒が分からず、切り出しに入る行を決められない"
    return None, f"行数が違う（Suno {len(lines)} 行・うち切り出しの中 {len(within)} 行、歌詞 {count} 行）"


def same_text(text, lyric):
    if text == lyric["text"]:
        return True
    key = normalized(text)
    return bool(key) and key in {normalized(lyric["text"]), normalized(lyric.get("alignment_text") or "")}


def match(lyrics, words, offset_s, duration_s):
    """(共通形式の行, 当てた Suno の行または None)。1 行でも字が違えば、行の対応を決められないので全部 unknown。"""
    rows = [{"id": r["id"], "text": r["text"], "start_s": None, "end_s": None, "timing_source": "unknown"} for r in lyrics]
    chosen, reason = choose(sung_lines(words), len(lyrics), offset_s, duration_s)
    if chosen is not None and not all(same_text(line["text"], r) for r, line in zip(lyrics, chosen)):
        for row, lyric, line in zip(rows, lyrics, chosen):
            row["reason"] = (f"Suno の行の字が歌詞と違う（Suno: {line['text']}）" if not same_text(line["text"], lyric)
                             else "ほかの行の字が違うので、行の対応を決められない")
        return rows, [None] * len(rows)
    if chosen is None:
        for row in rows:
            row["reason"] = reason
        return rows, [None] * len(rows)
    for row, line in zip(rows, chosen):
        if line["start_s"] is not None and not inside(line, offset_s, duration_s):
            row["reason"] = OUTSIDE
        elif line["start_s"] is None:
            row["reason"] = line["reason"]
        else:
            row.update(start_s=line["start_s"], end_s=line["end_s"], timing_source=line["timing_source"])
            if "reason" in line:
                row["reason"] = line["reason"]
    return rows, chosen


def word_lines(rows, chosen, words, offset_s):
    """dance spec の lyrics.lines[] に貼れる形（start・end・words[].t は切り出しの秒）。words をつなぐと text になる。"""
    def shift(value):
        return None if value is None else round(value + offset_s, 6)
    out = []
    for row, line in zip(rows, chosen):
        item = {"id": row["id"], "text": row["text"], "timing_source": row["timing_source"],
                "start": shift(row["start_s"]), "end": shift(row["end_s"]), "words": None}
        starts = [] if line is None else [seconds(words[k].get("start_s")) for k, _ in line["parts"]]
        if row["timing_source"] == "unknown":
            item["reason"] = row["reason"]
        elif line["text"] != row["text"]:
            item["reason"] = f"Suno の字（{line['text']}）が歌詞の行と字面で違うので、語に分けていない"
        elif None in starts:
            item["reason"] = "秒のない語があるので、語に分けていない"
        else:
            item["words"] = [{"text": piece, "t": shift(t)} for (_, piece), t in zip(line["parts"], starts)]
            item["interpolated_words"] = [i for i, (k, _) in enumerate(line["parts"]) if words[k].get("success") is not True]
            if "reason" in row:
                item["reason"] = row["reason"]
        out.append(item)
    return out


def convert(analysis, response, source_offset_s, tool_version=DEFAULT_VERSION):
    """(mv import-alignment の共通形式, 語の秒) を返す。

    source_offset_s は Suno の秒から mv init に渡した音源の秒へのずれ（Suno からダウンロードした音源なら 0）。
    切り出しの頭（audio.source_start_s）を引いた値を offset_s に残す（切り出しの秒 = Suno の秒 + offset_s）。
    共通形式の秒は Suno の秒のまま（offset_s は取り込みのときに足される）。
    """
    words = response_words(response)
    audio = analysis["audio"]
    offset_s = round(source_offset_s - audio["source_start_s"], 9)
    duration_s = audio["sample_count"] / audio["sample_rate"]  # validate.analysis と同じ式で範囲を見る
    rows, chosen = match(analysis["lyrics"], words, offset_s, duration_s)
    tool = {"name": TOOL, "version": tool_version, "model": None}
    alignment = {"audio_sha256": audio["sha256"], "offset_s": offset_s, "tool": tool, "lyrics": rows,
                 "suno": {key: response[key] for key in ("audio_id", "status") if key in response},
                 "raw_words": words}
    timed = {"tool": tool, "review_status": "unreviewed", "offset_s": offset_s,
             "note": "start・end・t はプロジェクトの切り出し音源の秒（Suno の秒 + offset_s）。Suno の推定で、未確認",
             "lines": word_lines(rows, chosen, words, offset_s)}
    return alignment, timed


def report(alignment, words, analysis):
    rows, offset_s = alignment["lyrics"], alignment["offset_s"]
    counts = {s: sum(r["timing_source"] == s for r in rows) for s in ("model", "interpolated", "unknown")}
    print(f"suno_align: 歌詞 {len(rows)} 行 → model {counts['model']}・interpolated {counts['interpolated']}・"
          f"unknown {counts['unknown']}（Suno の推定。取り込んでも unreviewed）", file=sys.stderr)
    print(f"  offset_s {offset_s:g}（切り出しの秒 = Suno の秒 + offset_s。切り出しは mv init に渡した音源の "
          f"{analysis['audio']['source_start_s']:g} 秒から）", file=sys.stderr)
    for r in rows:
        span = " " * 15 if r["start_s"] is None else f"{r['start_s'] + offset_s:7.3f}-{r['end_s'] + offset_s:7.3f}"
        note = f"  ※{r['reason']}" if r.get("reason") else ""
        print(f"  {r['id']}  {r['timing_source']:<12} {span}  {r['text']}{note}", file=sys.stderr)
    if all(r["timing_source"] == "unknown" for r in rows):
        print("  Suno の行（秒は切り出しの秒）:", file=sys.stderr)
        for k, line in enumerate(sung_lines(words), 1):
            span = " " * 15 if line["start_s"] is None else f"{line['start_s'] + offset_s:7.3f}-{line['end_s'] + offset_s:7.3f}"
            print(f"  {k:>3}  {span}  {line['text']}", file=sys.stderr)


def main(argv=None):
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="backslashreplace")  # cp932 の端末でも歌詞の字で止まらない
    p = argparse.ArgumentParser(description="Suno の歌詞アラインメントを mv import-alignment の共通形式へ変換する")
    p.add_argument("--suno", required=True, help="Suno の歌詞アラインメント応答の JSON（status が complete のもの）")
    p.add_argument("--analysis", required=True, help="プロジェクトの analysis.json（work/analysis.raw.json でもよい）")
    p.add_argument("--output", required=True, help="書き出す共通形式の JSON（mv import-alignment に渡す）")
    p.add_argument("--source-offset-s", type=float, required=True,
                   help="mv init に渡した音源の秒 = Suno の秒 + これ。Suno からダウンロードした音源をそのまま使ったなら 0。"
                        "切り出しの頭（analysis の audio.source_start_s）は自動で引く")
    p.add_argument("--tool-version", default=DEFAULT_VERSION, help=f"tool.version に残す文字列（既定: {DEFAULT_VERSION}）")
    p.add_argument("--words-output", help="語ごとの秒も書き出す（dance spec の lyrics.lines[].words に貼れる形）")
    args = p.parse_args(argv)
    if not math.isfinite(args.source_offset_s):
        p.error("--source-offset-s は有限の数にしてください")
    try:
        analysis = json.loads(Path(args.analysis).read_text(encoding="utf-8-sig"))
        response = json.loads(Path(args.suno).read_text(encoding="utf-8-sig"))
        alignment, timed = convert(analysis, response, args.source_offset_s, args.tool_version)
    except (OSError, ValueError) as error:  # JSON の読み違いも ValueError
        raise SystemExit(f"suno_align: {error}")
    except KeyError as error:
        raise SystemExit(f"suno_align: analysis か Suno の応答に {error} がありません")
    Path(args.output).write_text(json.dumps(alignment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.words_output:
        Path(args.words_output).write_text(json.dumps(timed, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report(alignment, alignment["raw_words"], analysis)


if __name__ == "__main__":
    main()
