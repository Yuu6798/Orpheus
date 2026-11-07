"""正解の分かっている試験素材（`mv gate demo DIR`）。gate の検査を、合成素材で端から端まで通して確かめるためのもの。

    mv gate demo DIR [--duration 10] [--fps 30] [--bpm 120] [--beats-per-bar 4] [--no-downbeats]
                     [--shifts 0,-0.333,0,0.15] [--flashes 5.5,6,6.5,7,7.5] [--flash-align beat|downbeat]
                     [--attack-ms 30] [--floor-db -40]

DIR に書くもの:
    vocals.wav        声の代役。行ごとに、正解の歌い出し（true_onset）から持続する倍音の音（立ち上がり attack_ms、背景は floor_db の雑音）
    lyrics.lrc        宣言した行の時刻（正解の 0.1 秒前 + 行ごとのずらし shifts）。`mv gate lines` の入力
    timeline.json     手書きの dance-timeline/0.1（層 band の行・フラッシュ・拍の格子。lyric_fade は after）
    beats.json        拍の格子（bpm から作る。--no-downbeats で小節頭を省く）
    gate.json         `mv gate check` の設定（timeline・measured_lines・lyric_layer・beats）
    expected.json     正解: 行ごとの true_onset・declared・期待する lead と合否（境目は uncertain）、フラッシュごとの照合先からのずれと合否、
                      検査ごとの期待する結果と overall・終了コード

使い方（出力の後に）:
    uv run mv gate lines DIR/lyrics.lrc DIR/lines --vocals DIR/vocals.wav
    uv run mv gate check DIR/gate.json        # expected.json の checks と比べる（既定のずらしでは歌詞の時刻が fail、拍が pass）

これは工程の接続と検査の論理を確かめる素材で、実曲の歌い出しの推定精度を保証しない。音は倍音だけで子音も残響も無い。
数値はすべて素材の設計値（正解）であり、推定ではない。
"""
import json
import math
import wave
from pathlib import Path

import numpy as np

from ..io import PipelineError, atomic_json
from .checks import EPS, _nearest

RATE = 48000
LEAD_S = 0.1                 # 宣言した行の時刻は正解の歌い出しのこれだけ前（gate の許容 0〜0.2 秒の中）
TRUE_ONSETS = (0.55, 3.10, 5.80, 7.45)
TEXTS = ("demo line one", "demo line two", "demo line three", "demo line four")
DEFAULT_SHIFTS = (0.0, -0.333, 0.0, 0.15)   # 2 行目は 0.333 秒早い、4 行目は正解の 0.05 秒後（どちらも不合格になる）
DEFAULT_FLASHES = (5.5, 6.0, 6.5, 7.0, 7.5)
NOTE_S = 1.2                 # 1 行の音の長さ（次の行の 0.3 秒前まで）
RELEASE_S = 0.08
MEASURE_TOLERANCE_S = 0.04   # 歌い出しの推定が正解からこれ以上ずれない前提で合否を予想する（合成素材での実測は約 -0.02 秒）
MAX_SHIFT_S = 0.6            # 宣言のずらしの上限。gate lines の探索窓（既定 0.8 秒）の中に正解が残り、隣の行と入れ替わらない範囲


def _floats(value, name):
    if value is None:
        return None
    if isinstance(value, str):
        value = [v.strip() for v in value.split(",") if v.strip()]
    try:
        return [float(v) for v in value]
    except (TypeError, ValueError) as error:
        raise PipelineError(f"{name} は数値をコンマで区切って指定してください: {value!r}") from error


def vocals(path, onsets, duration, attack_ms=30.0, floor_db=-40.0, seed=7):
    """行ごとに正解の歌い出しから持続する倍音の音を置いた 16 bit モノラル WAV を書く。背景は floor_db（音の山に対して）の雑音。"""
    count = int(round(duration * RATE))
    t = np.arange(count) / RATE
    rng = np.random.default_rng(seed)
    amplitude = 0.5
    signal = rng.standard_normal(count).astype(np.float64) * amplitude * 10 ** (floor_db / 20)
    attack = max(1e-4, attack_ms / 1000.0)
    for index, onset in enumerate(onsets):
        nxt = onsets[index + 1] if index + 1 < len(onsets) else duration
        length = min(NOTE_S, nxt - onset - 0.3, duration - onset)
        if length <= attack:
            raise PipelineError(f"行 {index + 1} の音を置く時間がありません（onset {onset}、次 {nxt}）")
        base = 196.0 * (1.0 + 0.25 * (index % 4))
        local = t[int(onset * RATE):int((onset + length) * RATE)] - onset
        tone = sum(np.sin(2 * math.pi * base * k * local) / k for k in (1, 2, 3))
        envelope = np.minimum(1.0, local / attack) * np.minimum(1.0, (length - local) / RELEASE_S)
        signal[int(onset * RATE):int(onset * RATE) + len(local)] += amplitude * tone / 1.84 * np.clip(envelope, 0.0, 1.0)
    pcm = (np.clip(signal, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(pcm.tobytes())


def beat_grid(bpm, duration, beats_per_bar=4, downbeats=True):
    step = 60.0 / bpm
    beats = [round(i * step, 6) for i in range(int(math.floor(duration / step)) + 1) if i * step < duration]
    return {"bpm": bpm, "beats": beats, "downbeats": beats[::beats_per_bar] if downbeats else []}


def create(directory, duration=10.0, fps=30, bpm=120.0, beats_per_bar=4, downbeats=True, shifts=None, flashes=None,
           flash_align="beat", attack_ms=30.0, floor_db=-40.0, width=1080, height=1920):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    onsets = [o for o in TRUE_ONSETS if o + 0.5 < duration]
    if not onsets:
        raise PipelineError(f"--duration {duration} では行を置けません（最初の歌い出しは {TRUE_ONSETS[0]} 秒）")
    shifts = _floats(shifts, "--shifts") if shifts is not None else list(DEFAULT_SHIFTS)
    if len(shifts) < len(onsets):
        shifts = shifts + [0.0] * (len(onsets) - len(shifts))
    shifts = shifts[:len(onsets)]
    for index, (onset, shift) in enumerate(zip(onsets, shifts)):
        if abs(shift - LEAD_S) > MAX_SHIFT_S + EPS:   # 宣言 = 正解 - LEAD_S + shift。正解からの距離で見る
            raise PipelineError(f"行 {index + 1} の宣言が正解から {abs(shift - LEAD_S):.3f} 秒離れます（上限 {MAX_SHIFT_S} 秒。gate lines の探索窓の中に正解が残る範囲）")
        if onset - LEAD_S + shift < 0:
            raise PipelineError(f"行 {index + 1} の宣言した時刻が負になります（正解 {onset} 秒、ずらし {shift:+.3f} 秒）")
    flashes = _floats(flashes, "--flashes") if flashes is not None else [f for f in DEFAULT_FLASHES if f < duration]
    if flash_align not in ("beat", "downbeat"):
        raise PipelineError(f"--flash-align は beat か downbeat にしてください: {flash_align!r}")
    bad = [f for f in flashes if not 0 <= f < duration]
    if bad:
        raise PipelineError(f"フラッシュの時刻が 0〜{duration:g} 秒の外です: {bad}")
    grid = beat_grid(bpm, duration, beats_per_bar, downbeats)
    limit = 1.0 / fps
    vocals(directory / "vocals.wav", onsets, duration, attack_ms, floor_db)

    rows, lines = [], []
    for index, (onset, shift) in enumerate(zip(onsets, shifts)):
        declared = round(onset - LEAD_S + shift, 3)
        nxt = onsets[index + 1] - LEAD_S + shifts[index + 1] if index + 1 < len(onsets) else duration
        end = round(min(declared + 2.0, nxt - 0.2, duration), 3)
        if end <= declared:
            raise PipelineError(f"行 {index + 1} の表示の終わり {end} 秒が始まり {declared} 秒より前になります（隣の行と近すぎます）")
        lead = round(onset - declared, 3)        # 正 = 字が歌より先に出る。gate の許容は 0〜0.2
        margin = min(lead - 0.0, 0.2 - lead)
        status = "pass" if margin >= MEASURE_TOLERANCE_S else "fail" if margin <= -MEASURE_TOLERANCE_S else "uncertain"
        rows.append({"text": TEXTS[index], "true_onset": onset, "declared": declared, "shift": shift, "lead": lead, "status": status})
        top = 0.12 + 0.2 * index
        lines.append({"layer": "band", "text": TEXTS[index], "start": declared, "end": end, "fade_in_s": 0.04, "fade_out_s": 0.08,
                      "box": [0.1, round(top, 3), 0.9, round(top + 0.08, 3)], "sung": True, "exclusive": True})
    flash_rows = []
    for t in flashes:
        if flash_align == "downbeat" and not grid["downbeats"]:   # gate は小節頭が無いと拍で代用せず「見ていない」（要確認）にする
            flash_rows.append({"t": t, "align": flash_align, "compared_with": None, "offset_s": None, "status": "unseen"})
            continue
        compared = "downbeat" if flash_align == "downbeat" else "beat"
        off = _nearest(grid["downbeats"] if compared == "downbeat" else grid["beats"], t)
        flash_rows.append({"t": t, "align": flash_align, "compared_with": compared, "offset_s": round(off, 6),
                           "status": "pass" if off <= limit + EPS else "fail"})
    timeline = {
        "schema_version": "dance-timeline/0.1", "size": [width, height], "fps": fps, "duration_s": duration,
        "lyric_fade": "after",
        "lyrics": lines, "seams": [], "flashes": flashes,
        "flash_info": [{"t": t, "strength": 0.5, "decay_s": 0.1, "mode": "lerp_linear", "beat_exempt": False, "align": flash_align} for t in flashes],
        "ui_regions": [], "beats": {"beats": grid["beats"], "downbeats": grid["downbeats"]},
    }
    with (directory / "lyrics.lrc").open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            minutes, rest = divmod(row["declared"], 60)
            stream.write(f"[{int(minutes):02d}:{rest:06.3f}]{row['text']}\n")
    atomic_json(directory / "timeline.json", timeline)
    atomic_json(directory / "beats.json", {"bpm": bpm, "beats": grid["beats"], "downbeats": grid["downbeats"]})
    atomic_json(directory / "gate.json", {"timeline": "timeline.json", "measured_lines": "lines/lines_measured.json",
                                           "lyric_layer": "band", "beats": "beats.json"})
    # uncertain の行（許容の境目から推定の誤差の範囲）がある限り、歌詞の時刻の合否は予想しない
    lyric_status = ("fail" if any(r["status"] == "fail" for r in rows)
                    else "uncertain" if any(r["status"] == "uncertain" for r in rows) else "pass")
    beat_status = ("fail" if any(f["status"] == "fail" for f in flash_rows)
                   else "warn" if any(f["status"] == "unseen" for f in flash_rows) else "pass")
    expected = {
        "schema_version": "gate-demo-expected/0.1",
        "vocals": {"path": "vocals.wav", "rate": RATE, "attack_ms": attack_ms, "floor_db": floor_db, "note_s": NOTE_S},
        "lead_s": LEAD_S, "measure_tolerance_s": MEASURE_TOLERANCE_S,
        "lines": rows, "flashes": flash_rows,
        "beats": {"bpm": bpm, "beats_per_bar": beats_per_bar, "downbeats": bool(grid["downbeats"])},
        "checks": {"歌詞の時刻": lyric_status, "拍": beat_status},
        # 確認済みの時刻・字と顔・揺れ・書き出しは材料が無く skip になるので、fail が無ければ pass_with_skips
        "overall": "fail" if "fail" in (lyric_status, beat_status) else "uncertain" if lyric_status == "uncertain" else "pass_with_skips",
        "exit_code": 1 if "fail" in (lyric_status, beat_status) else 0,
        "failing_lines": [r["text"] for r in rows if r["status"] == "fail"],
        "uncertain_lines": [r["text"] for r in rows if r["status"] == "uncertain"],
        "failing_flashes": [f["t"] for f in flash_rows if f["status"] == "fail"],
        "commands": [f"uv run mv gate lines {directory / 'lyrics.lrc'} {directory / 'lines'} --vocals {directory / 'vocals.wav'}",
                     f"uv run mv gate check {directory / 'gate.json'}"],
    }
    atomic_json(directory / "expected.json", expected)
    return {"dir": str(directory), "lines": len(rows), "flashes": len(flashes), "downbeats": bool(grid["downbeats"]),
            "expected": expected["checks"], "overall": expected["overall"], "failing_lines": expected["failing_lines"],
            "uncertain_lines": expected["uncertain_lines"], "failing_flashes": expected["failing_flashes"], "commands": expected["commands"]}
