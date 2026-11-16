"""音の終わり（`mv audio end`）。曲の音（か動画の音）から、音が切れる時刻を測る。

    mv audio end AUDIO|PROJECT [--window 0.1] [--floor-db -60] [--out FILE] [--force]

リポジトリ外の 2 作目の VRM のダンスの演出版で、絵の終わりを決めるために 0.1 秒ごとの音量で曲の終わりを測った手順を移した（その曲は、
最後の打音から少しして音が切れ、あとは完全な無音だった。絵は、音が切れてから 0.5 秒ほどで終えた）。値は測ったもの。
完成した動画の音の終わりと絵の終わりを比べるのは `mv gate tail`（gate/tail.py）。

- 音を ffmpeg でモノ（左右の平均）・RATE Hz の 32 ビット浮動小数に直し、window 秒ごとの窓（0 秒から。最後の半端な窓も 1 つ）の
  RMS を dBFS で出す（0 なら −inf）。
- 鳴っている窓: RMS が、いちばん大きい窓の RMS から floor_db（既定 −60 dB）以内の窓。
- 音が切れる時刻 `end_s`: 最後の鳴っている窓の終わり（音の長さを超えない）。`tail_s` は、そこから音の終わりまでの長さ。
- 完全な無音 `digital_silence_from_s`: 後ろから見て、標本がどれも 0 の窓が続く所の頭（無ければ null）。
- 最後の窓まで鳴っていれば `cut_while_sounding` が true（音が鳴っている所で終わっている）。
- 記録（audio-end/0.1。contracts/ の外）は、音源の隣の `<名前>.end.json`（analysis.json のあるフォルダなら `analysis.end.json`。--out で変える）。
  終わりの 5 秒の窓ごとの dBFS も残す。
"""
import math
import subprocess
from pathlib import Path

import numpy as np

from ..io import PipelineError, sha256
from . import files

SCHEMA = "audio-end/0.1"
RATE = 22050
WINDOW_S = 0.1
FLOOR_DB = -60.0
TAIL_REPORT_S = 5.0


def decode(path, rate=RATE):
    """音（動画なら最初の音のストリーム）をモノの float32 で読む。音が無ければ PipelineError。"""
    from .. import media
    result = subprocess.run([media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0", "-vn",
                             "-ac", "1", "-ar", str(rate), "-f", "f32le", "-"], capture_output=True)
    if result.returncode != 0:
        message = result.stderr.decode("utf-8", errors="replace").strip()[-300:]
        raise PipelineError(f"音を読めません（{Path(path).name}）: {message or 'ffmpeg が失敗しました'}")
    samples = np.frombuffer(result.stdout, dtype="<f4")
    if not len(samples):
        raise PipelineError(f"音がありません: {Path(path).name}")
    return samples


def windows(samples, rate, window_s):
    """window 秒ごとの (窓の頭の秒, RMS, どの標本も 0 か, 窓の標本の数)。最後の半端な窓も 1 つに数える。"""
    size = max(1, int(round(window_s * rate)))
    count = int(math.ceil(len(samples) / size))
    padded = np.zeros(count * size, dtype=np.float64)
    padded[:len(samples)] = samples
    blocks = padded.reshape(count, size)
    lengths = np.full(count, size, dtype=np.float64)
    lengths[-1] = len(samples) - size * (count - 1)
    rms = np.sqrt((blocks * blocks).sum(axis=1) / lengths)
    zero = ~np.any(blocks != 0, axis=1)
    return np.arange(count) * size / rate, rms, zero, size


def to_db(rms):
    with np.errstate(divide="ignore"):
        return 20 * np.log10(rms)


def measure(samples, rate=RATE, window_s=WINDOW_S, floor_db=FLOOR_DB):
    """音の終わりの数（記録の measure の欄）。"""
    if not window_s > 0:
        raise PipelineError(f"--window は 0 より大きい秒にしてください: {window_s}")
    if not floor_db < 0:
        raise PipelineError(f"--floor-db は負の dB にしてください（いちばん大きい窓からの下がり幅）: {floor_db}")
    starts, rms, zero, size = windows(samples, rate, window_s)
    seconds = len(samples) / rate
    db = to_db(rms)
    peak = float(db.max())
    k = len(zero)
    while k > 0 and zero[k - 1]:
        k -= 1
    silent_from = float(starts[k]) if k < len(zero) else None
    if not math.isfinite(peak):                          # 全部が無音
        return {"seconds": seconds, "peak_dbfs": None, "end_s": 0.0, "tail_s": round(seconds, 6), "last_sounding_window": None,
                "digital_silence_from_s": silent_from, "cut_while_sounding": False, "windows": len(rms)}
    sounding = np.nonzero(db >= peak + floor_db)[0]
    last = int(sounding[-1])
    end_s = min((last + 1) * size / rate, seconds)
    return {"seconds": seconds, "peak_dbfs": round(peak, 3), "end_s": round(end_s, 6), "tail_s": round(seconds - end_s, 6),
            "last_sounding_window": {"start_s": round(float(starts[last]), 6), "dbfs": round(float(db[last]), 3)},
            "digital_silence_from_s": silent_from, "cut_while_sounding": last == len(rms) - 1, "windows": len(rms)}


def tail_profile(samples, rate=RATE, window_s=WINDOW_S, span_s=TAIL_REPORT_S):
    """終わりの span_s 秒の窓ごとの [頭の秒, dBFS]（無音は null）。"""
    starts, rms, _, _ = windows(samples, rate, window_s)
    db = to_db(rms)
    first = max(0.0, len(samples) / rate - span_s)
    return [[round(float(s), 6), round(float(d), 2) if math.isfinite(d) else None] for s, d in zip(starts, db) if s >= first - 1e-9]


def record(path, window_s=WINDOW_S, floor_db=FLOOR_DB, label=None):
    """音源の記録（audio-end/0.1。sha256 と測った数と終わりの音量）。"""
    path = Path(path)
    samples = decode(path)
    return {"schema_version": SCHEMA, "source": {"path": label or path.name, "sha256": sha256(path)},
            "method": {"rate": RATE, "channels": "mean", "level": "rms_dbfs", "window_s": window_s, "floor_db": floor_db},
            "measure": measure(samples, RATE, window_s, floor_db), "tail": tail_profile(samples, RATE, window_s)}


def run(source, out=None, window_s=WINDOW_S, floor_db=FLOOR_DB, force=False):
    """mv audio end。記録を書き、要約を返す。"""
    path, label, default_out, inputs = files.source(source, ".end.json")
    out = Path(out) if out else default_out
    files.refuse_inputs(out, inputs)
    data = record(path, window_s, floor_db, label)
    state = files.write_json(out, data, force)
    m = data["measure"]
    return {"out": str(out), "state": state, "seconds": m["seconds"], "end_s": m["end_s"], "tail_s": m["tail_s"],
            "digital_silence_from_s": m["digital_silence_from_s"], "cut_while_sounding": m["cut_while_sounding"]}
