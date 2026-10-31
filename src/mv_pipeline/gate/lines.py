"""歌詞の「行・秒」確認表を作る。ボーカルがあれば、推定の行頭の近くで歌い出しの立ち上がりを探して実測する。

    mv gate lines LINES.lrc OUT_DIR [--vocals vocals.flac] [--window 0.8] [--offset 0.0]

入力は LRC か JSON（[{"start": 秒, "line" か "text" か "display": 歌詞}, …]）。空の行（終わりの印）は飛ばす。
出力:
    lines_table.csv       試聴で確かめる表（Excel で開ける UTF-8 BOM）。確認の欄は空のまま渡す
    lines_measured.json   品質ゲートの measured_lines に渡す [{"text", "start", "estimate", "method"}]

method: vocals_onset = ボーカルの立ち上がりから実測 / estimate = ボーカル無し（推定のまま）/
        not_found = 窓の中に立ち上がりが無く推定のまま。
これは行頭の候補で、**確定はユーザーの試聴**。同じ音が続く行や語の途中で歌が切れない行は誤ることがある。
"""
import csv
import json
import re
from pathlib import Path

import numpy as np

from ..io import PipelineError

HOP_S = 0.01
FRAME_S = 0.03
SPAN = 8              # 前後の平均を取るコマ数（80ms）。残響の中でも谷と山を比べられる長さ
GAP = 2               # 立ち上がりの点の前後で飛ばすコマ数
MIN_RISE_DB = 8.0     # 谷から山へこれだけ上がる点を歌い出しの候補にする（実曲の行間の谷は -20dB 前後で、無音にはならない）
LOUD_DB = -12.0       # 上がった先がこの音量以上（95% 点基準）
NEAR_PENALTY_DB = 6.0 # 窓の端の候補を、推定に近い候補よりこれだけ不利に数える


def read_lines(path):
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        lines = []
        for row in json.loads(text):
            words = str(row.get("display") or row.get("line") or row.get("text") or "").replace("　", " ").strip()
            if words:
                lines.append({"text": words, "start": float(row["start"])})
        return lines
    lines = []
    for raw in text.splitlines():
        found = re.match(r"\[(\d+):(\d+(?:\.\d+)?)\](.*)", raw.strip())
        if found and found[3].strip():
            lines.append({"text": found[3].strip(), "start": int(found[1]) * 60 + float(found[2])})
    return lines


def envelope(path):
    """(dB の音量 10ms 刻み, 95% 点が 0 dB)。"""
    try:
        import soundfile
    except ModuleNotFoundError as error:
        raise PipelineError("--vocals には soundfile が必要です。`uv sync --locked --extra analysis` を実行してください") from error
    audio, rate = soundfile.read(path, dtype="float32", always_2d=True)
    mono = audio.mean(axis=1)
    hop, frame = int(HOP_S * rate), int(FRAME_S * rate)
    power = np.concatenate([[0.0], np.cumsum(mono.astype(np.float64) ** 2)])
    starts = np.arange(0, max(1, len(mono) - frame), hop)
    rms = np.sqrt((power[starts + frame] - power[starts]) / frame)
    db = 20 * np.log10(rms + 1e-7)
    return db - np.percentile(db, 95)


def find_onset(db, estimate, window):
    """推定の前後 window 秒で、谷から山への上がり幅が最も大きい点の (時刻, 上がり幅 dB)。候補が無ければ None。

    語の途中の小さな谷でも候補は出るので、上がり幅で比べ、推定から離れるほど少し不利にする。
    上がり切る途中の点を返さないよう、選んだ点から上がり始めの方へ戻す（谷の平均から MIN_RISE_DB の半分を超えた最初の点）。
    """
    first = max(SPAN + GAP, int((estimate - window) / HOP_S))
    last = min(len(db) - SPAN - GAP - 1, int((estimate + window) / HOP_S))
    best = None
    for i in range(first, last + 1):
        before = db[i - SPAN - GAP:i - GAP].mean()
        after = db[i + GAP:i + SPAN + GAP].mean()
        rise = after - before
        if rise < MIN_RISE_DB or after < LOUD_DB:
            continue
        score = rise - NEAR_PENALTY_DB * abs(i * HOP_S - estimate) / window
        if best is None or score > best[0]:
            best = (score, i, before, rise)
    if best is None:
        return None
    _, i, before, rise = best
    while i > first and db[i - 1] > before + MIN_RISE_DB / 2:
        i -= 1
    return i * HOP_S, float(rise)


def stamp(seconds):
    minutes, rest = divmod(seconds, 60)
    return f"{int(minutes):02d}:{rest:05.2f}"


def build(lines, vocals, window, offset):
    db = envelope(vocals) if vocals else None
    rows = []
    for number, line in enumerate(lines, 1):
        estimate = line["start"] + offset
        onset = find_onset(db, estimate, window) if db is not None else None
        method = "estimate" if db is None else "vocals_onset" if onset is not None else "not_found"
        start, rise = onset if onset is not None else (estimate, None)
        rows.append({"number": number, "text": line["text"], "estimate": round(estimate, 2), "start": round(start, 2),
                     "delta": round(start - estimate, 2), "rise_db": None if rise is None else round(rise, 1), "method": method})
    return rows


def write(rows, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with (out / "lines_table.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        table = csv.writer(stream)
        table.writerow(["行", "歌詞", "時刻", "秒", "推定との差", "上がり幅dB", "方法", "確認（OK / 直す秒）", "メモ"])
        for row in rows:
            table.writerow([row["number"], row["text"], stamp(row["start"]), row["start"], row["delta"], row["rise_db"], row["method"], "", ""])
    measured = [{"text": r["text"], "start": r["start"], "estimate": r["estimate"], "method": r["method"]} for r in rows]
    (out / "lines_measured.json").write_text(json.dumps(measured, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def make_table(lines_path, out, vocals=None, window=0.8, offset=0.0):
    """確認表を書き、件数の要約を返す。"""
    rows = build(read_lines(lines_path), vocals, window, offset)
    write(rows, out)
    counts = {m: sum(r["method"] == m for r in rows) for m in ("vocals_onset", "not_found", "estimate")}
    moved = [abs(r["delta"]) for r in rows if r["method"] == "vocals_onset"]
    return {"lines": len(rows), **counts, "median_abs_delta": round(float(np.median(moved)), 3) if moved else None, "out": str(out)}
