"""低音が抜ける所（`mv audio lows`）。曲の音から、低音の帯の強さが下がる区間（拍か窓）を測る。演出の切り替え（光を落とす・影絵に
する・打音で色を変える）を置く所の候補を、カット割りの前に見つけるため。

    mv audio lows AUDIO|PROJECT [--grid GRID.json] [--window 0.5] [--band 20 150] [--drop-db 10] [--floor-db -30] [--out FILE] [--force]

リポジトリ外の 3 作目の VRM のダンスで、曲の低音が 2 拍だけ抜ける所を測り、そこを振り返りの影絵にして、抜けの後の打音で色を切り替えた
使い捨ての計算を移した。値は測ったもの（推定ではない。どこを「抜け」と見るかの下がり幅は仮の値）。librosa は要らない（ffmpeg と numpy）。

- 窓: --grid（同じ音源の mv audio grid の記録）があれば拍ごと（拍 k の頭から次の拍の頭まで。拍の番号は round((時刻 − phase_s) ÷ period_s)）、
  無ければ 0 秒から --window 秒ごと（最後の半端な窓は数えない）。
- 強さ: 窓の音（モノ・22050 Hz）にハン窓を掛けた FFT の、--band の帯（既定 20〜150 Hz）の強さの和と、全体の強さの和を、それぞれ 10 log10
  （dB。相対の値）で出す。
- 鳴っている窓: 全体の強さが、いちばん大きい窓から --floor-db（既定 −30 dB）以内の窓（曲の終わりの無音などを除く）。強さが 0 の窓
  （標本がどれも 0 の完全な無音など）は、dB を null にして、相対の決め方の前に除く（全部が無音なら、鳴っている窓も抜けも無い）。
- 抜け: 鳴っている窓のうち、低音の帯の強さが、鳴っている窓の低音の帯の中央値から --drop-db（既定 10 dB）以上下がった窓。続いた窓を
  1 つの区間にまとめる（頭の秒・終わりの秒・拍の番号・いちばん下がった dB）。
- 記録（audio-lows/0.1。contracts/ の外）は、音源の隣の `<名前>.lows.json`（analysis.json のあるフォルダなら `analysis.lows.json`）。
  音源の sha256、測り方、窓ごとの表、抜けの区間。
"""
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, read_json, sha256
from . import files
from .end import RATE, decode
from .grid import SCHEMA as GRID_SCHEMA

SCHEMA = "audio-lows/0.1"
WINDOW_S = 0.5
BAND = (20.0, 150.0)
DROP_DB = 10.0
FLOOR_DB = -30.0
TINY = 1e-20


def load_grid(path, audio_sha256):
    """mv audio grid の記録の拍の時刻（同じ音源のものだけ）。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--grid の記録が見つかりません: {path}")
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema_version") != GRID_SCHEMA:
        raise PipelineError(f"--grid には mv audio grid の記録（{GRID_SCHEMA}）を渡してください: {path}")
    if (record.get("audio") or {}).get("sha256") != audio_sha256:
        raise PipelineError(f"--grid の記録は別の音源のものです（sha256 が違います）: {path}")
    beats, period, phase = record.get("beats_s"), record.get("period_s"), record.get("phase_s")
    if (not isinstance(beats, list) or len(beats) < 2 or isinstance(period, bool) or not isinstance(period, (int, float))
            or not period > 0 or isinstance(phase, bool) or not isinstance(phase, (int, float))):
        raise PipelineError(f"--grid の記録に beats_s（2 つ以上）・period_s・phase_s がありません: {path}")
    return {"path": path.name, "sha256": sha256(path), "period_s": float(period), "phase_s": float(phase),
            "beats_s": [float(t) for t in beats]}


def windows(seconds, grid, window_s):
    """[(頭の秒, 終わりの秒, 拍の番号か None)]。"""
    if grid is not None:
        beats = grid["beats_s"]
        return [(a, b, int(round((a - grid["phase_s"]) / grid["period_s"]))) for a, b in zip(beats, beats[1:]) if a >= 0 and b <= seconds]
    count = int(math.floor(seconds / window_s + 1e-9))
    return [(k * window_s, (k + 1) * window_s, None) for k in range(count)]


def levels(samples, rate, start, end, band):
    """窓の (低音の帯の強さ dB, 全体の強さ dB)。強さが 0 の窓（標本がどれも 0 の完全な無音など）と短すぎる窓は (None, None)。"""
    x = samples[int(round(start * rate)):int(round(end * rate))].astype(np.float64)
    if len(x) < 2:
        return None, None
    power = np.abs(np.fft.rfft(x * np.hanning(len(x)))) ** 2
    total = float(power.sum())
    if total == 0:
        return None, None
    freqs = np.fft.rfftfreq(len(x), 1 / rate)
    low = float(power[(freqs >= band[0]) & (freqs < band[1])].sum())
    return 10 * math.log10(low + TINY), 10 * math.log10(total)


def measure(samples, rate=RATE, grid=None, window_s=WINDOW_S, band=BAND, drop_db=DROP_DB, floor_db=FLOOR_DB):
    """窓ごとの表と抜けの区間（記録の measure の欄）。"""
    if not window_s > 0:
        raise PipelineError(f"--window は 0 より大きい秒にしてください: {window_s}")
    if not 0 <= band[0] < band[1]:
        raise PipelineError(f"--band は 低い Hz < 高い Hz（0 以上）にしてください: {list(band)}")
    if not drop_db > 0:
        raise PipelineError(f"--drop-db は正の dB（中央値からの下がり幅）にしてください: {drop_db}")
    if not floor_db < 0:
        raise PipelineError(f"--floor-db は負の dB（いちばん大きい窓からの下がり幅）にしてください: {floor_db}")
    seconds = len(samples) / rate
    spans = windows(seconds, grid, window_s)
    if not spans:
        raise PipelineError("窓がありません（音が窓より短いか、拍の格子が音の外です）")
    rows = []
    for start, end, beat in spans:
        low, total = levels(samples, rate, start, end, band)
        rows.append({"start_s": round(start, 6), "end_s": round(end, 6), "beat": beat,
                     "low_db": None if low is None else round(low, 3), "total_db": None if total is None else round(total, 3)})
    # 強さが 0 の窓（完全な無音）は、相対の決め方の前に除く。全部が無音のとき、いちばん大きい窓からの相対で決めると、無音の窓が
    # みな鳴っている窓になる
    measured = [r["total_db"] for r in rows if r["total_db"] is not None]
    loudest = max(measured) if measured else None
    for r in rows:
        r["sounding"] = loudest is not None and r["total_db"] is not None and r["total_db"] >= loudest + floor_db
    sounding = [r["low_db"] for r in rows if r["sounding"]]
    median = float(np.median(sounding)) if sounding else None
    groups, current = [], []
    for r in rows:
        r["low_rel_db"] = None if median is None or r["low_db"] is None else round(r["low_db"] - median, 3)
        r["drop"] = bool(r["sounding"] and median is not None and r["low_db"] <= median - drop_db)
        if r["drop"]:
            current.append(r)
            continue
        if current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    drops = [{"start_s": g[0]["start_s"], "end_s": g[-1]["end_s"], "windows": len(g),
              "beats": [r["beat"] for r in g] if g[0]["beat"] is not None else None,
              "lowest_rel_db": min(r["low_rel_db"] for r in g)} for g in groups]
    return {"seconds": round(seconds, 6), "windows": len(rows), "sounding_windows": len(sounding),
            "low_median_db": None if median is None else round(median, 3), "drops": drops, "rows": rows}


def record(path, grid=None, window_s=WINDOW_S, band=BAND, drop_db=DROP_DB, floor_db=FLOOR_DB, label=None):
    """音源の記録（audio-lows/0.1）。"""
    path = Path(path)
    audio_sha256 = sha256(path)
    grid_info = load_grid(grid, audio_sha256) if grid is not None else None
    samples = decode(path)
    data = measure(samples, RATE, grid_info, window_s, tuple(band), drop_db, floor_db)
    return {"schema_version": SCHEMA, "source": {"path": label or path.name, "sha256": audio_sha256},
            "method": {"rate": RATE, "channels": "mean", "window": "beats" if grid_info is not None else "seconds",
                       "window_s": None if grid_info is not None else window_s, "band_hz": list(band), "level": "hann_fft_power_db",
                       "drop_db": drop_db, "floor_db": floor_db},
            "grid": None if grid_info is None else {k: grid_info[k] for k in ("path", "sha256", "period_s", "phase_s")},
            "measure": data}


def run(source, grid=None, out=None, window_s=WINDOW_S, band=BAND, drop_db=DROP_DB, floor_db=FLOOR_DB, force=False):
    """mv audio lows。記録を書き、要約を返す。"""
    path, label, default_out, inputs = files.source(source, ".lows.json")
    out = Path(out) if out else default_out
    if grid is not None:
        inputs[files.same_file(grid)] = "--grid の記録"
    files.refuse_inputs(out, inputs)
    data = record(path, grid, window_s, band, drop_db, floor_db, label)
    state = files.write_json(out, data, force)
    m = data["measure"]
    return {"out": str(out), "state": state, "seconds": m["seconds"], "windows": m["windows"], "low_median_db": m["low_median_db"],
            "drops": [{k: d[k] for k in ("start_s", "end_s", "beats", "lowest_rel_db")} for d in m["drops"]]}
