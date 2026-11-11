"""打点（`mv audio hits`）。曲の音から、強打（strong）と帯域差分の打点（onsets）の時刻と強さを推定する。

    mv audio hits AUDIO|PROJECT [--grid GRID.json] [--strong-shift-ms -16.7] [--onset-shift-ms 18.7] [--out FILE] [--force]

どちらも実制作の台本と同じ手順（値はすべて推定）。音は librosa.load で 22050 Hz のモノにしてから調べる。
- strong（強打。実制作の解析の台本の選び方。稲妻の明滅など、ときどき光らせるものに使う疎な一覧）:
  onset_strength（hop 512）の山を peak_pick（前後の最大 10・前後の平均 50 フレーム、delta = onset_strength の 97 パーセンタイル × 0.5、
  間隔 20 フレーム）で選ぶ。時刻 = 山のフレームの時刻 + shift（既定 −16.7 ms）。0 秒より前になったものは捨てる。
  強さ = 山の onset_strength ÷（onset_strength の 99 パーセンタイル + 1e-9）（実制作の小節の表の hit と同じ割り方）。
- onsets（帯域差分の打点。実制作の縦の Shorts の格子の台本。カットを合わせるときなどに使う密な一覧）:
  n_fft 1024・hop 64 の |STFT|² を 30〜11000 Hz で足し、e = log1p(1000 × 和) の前のフレームとの差（負は 0）を d とする。
  d の山を peak_pick（前後の最大 20・前後の平均 40、delta 0.05、間隔 20）で選ぶ。時刻 = 山のフレームの時刻 + shift（既定 +18.7 ms）。
  強さ = 山の d ÷ 山の d の 99 パーセンタイル（1 を超えることがある）。
- shift の既定値は、実制作が合成のクリック（雑音の立ち上がりと低い打音）で測った検出の時刻の偏り（strong は 16.7 ms 遅れ、
  onsets は 18.7 ms 早い）を打ち消す値で、この手順のまま使うときだけ当てはまる。0 にすれば、検出したフレームの時刻のまま。
- --grid（同じ音源の mv audio grid の記録）を渡すと、strong と onsets に小節と拍の位置を付ける（実制作の縦の Shorts の台本と同じ式）:
  b = (t − 最初の小節の頭) ÷ period、bar = floor(b ÷ 拍数) + 1、beat = b − 拍数 × (bar − 1) + 1。最初の小節の頭より前は 0 小節目。

記録（audio-hits/0.1。contracts/ の外）は、音源の隣の `<名前>.hits.json`（analysis.json のあるフォルダなら `analysis.hits.json`）。
光の強さのキーは `mv audio keys`（keys.py）がこの記録から作る。
"""
import math
from decimal import Decimal
from pathlib import Path

import numpy as np

from ..io import PipelineError, read_json, sha256
from . import files
from .grid import SCHEMA as GRID_SCHEMA
from .grid import _librosa

SCHEMA = "audio-hits/0.1"
RATE = 22050
MIN_SECONDS = 1.0
STRONG_HOP = 512
STRONG_PEAKS = {"pre_max": 10, "post_max": 10, "pre_avg": 50, "post_avg": 50, "wait": 20}
STRONG_DELTA_PERCENTILE, STRONG_DELTA_SCALE, STRENGTH_PERCENTILE = 97, 0.5, 99
STRONG_SHIFT_MS = -16.7
ONSET_N_FFT, ONSET_HOP, ONSET_LOW_HZ, ONSET_HIGH_HZ, ONSET_GAIN = 1024, 64, 30, 11000, 1000
ONSET_PEAKS = {"pre_max": 20, "post_max": 20, "pre_avg": 40, "post_avg": 40, "delta": 0.05, "wait": 20}
ONSET_SHIFT_MS = 18.7
MAX_SHIFT_MS = 1000.0


def seconds(ms):
    """ミリ秒を秒に。10 進のまま 1000 で割ってから float にする（18.7 / 1000 は、実制作の台本の 0.0187 と 1 ulp 違う）。"""
    return float(Decimal(repr(float(ms))) / 1000)


def strong(y, rate, shift_s, librosa):
    """強打 [{t, strength}]（実制作の解析の台本の選び方。時刻は shift_s ずらし、0 秒より前は捨てる）。"""
    onset = librosa.onset.onset_strength(y=y, sr=rate, hop_length=STRONG_HOP)
    peaks = librosa.util.peak_pick(onset, delta=np.percentile(onset, STRONG_DELTA_PERCENTILE) * STRONG_DELTA_SCALE, **STRONG_PEAKS)
    times = librosa.frames_to_time(peaks, sr=rate, hop_length=STRONG_HOP) + shift_s
    scale = np.percentile(onset, STRENGTH_PERCENTILE) + 1e-9
    return [{"t": float(t), "strength": float(onset[p] / scale)} for t, p in zip(times, peaks) if t >= 0]


def onsets(y, rate, shift_s, librosa):
    """帯域差分の打点 [{t, strength}]（実制作の縦の Shorts の格子の台本と同じ。時刻は shift_s ずらし、0 秒より前は捨てる）。"""
    power = np.abs(librosa.stft(y, n_fft=ONSET_N_FFT, hop_length=ONSET_HOP)) ** 2
    freqs = librosa.fft_frequencies(sr=rate, n_fft=ONSET_N_FFT)
    energy = np.log1p(ONSET_GAIN * power[(freqs >= ONSET_LOW_HZ) & (freqs < ONSET_HIGH_HZ)].sum(0))
    rise = np.maximum(np.diff(energy, prepend=energy[0]), 0)
    times = librosa.frames_to_time(np.arange(len(rise)), sr=rate, hop_length=ONSET_HOP) + shift_s
    peaks = librosa.util.peak_pick(rise, **ONSET_PEAKS)
    if not len(peaks):
        return []
    scale = float(np.percentile(rise[peaks], STRENGTH_PERCENTILE))
    return [{"t": float(times[p]), "strength": float(rise[p] / scale)} for p in peaks if times[p] >= 0]


def load_grid(path, audio_sha256):
    """mv audio grid の記録から、位置を付けるのに使う値（同じ音源のものだけ）。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"--grid の記録が見つかりません: {path}")
    record = read_json(path)
    if not isinstance(record, dict) or record.get("schema_version") != GRID_SCHEMA:
        raise PipelineError(f"--grid には mv audio grid の記録（{GRID_SCHEMA}）を渡してください: {path}")
    if (record.get("audio") or {}).get("sha256") != audio_sha256:
        raise PipelineError(f"--grid の記録は別の音源のものです（sha256 が違います）: {path}")
    period, per_bar, downbeats = record.get("period_s"), record.get("beats_per_bar"), record.get("downbeats")
    if (isinstance(period, bool) or not isinstance(period, (int, float)) or not period > 0 or isinstance(per_bar, bool)
            or not isinstance(per_bar, int) or per_bar < 1 or not isinstance(downbeats, list) or not downbeats):
        raise PipelineError(f"--grid の記録に period_s・beats_per_bar・downbeats（1 つ以上）がありません: {path}")
    return {"path": path.name, "sha256": sha256(path), "period_s": float(period), "beats_per_bar": per_bar,
            "first_bar_s": float(downbeats[0])}


def place(hits, grid):
    """打点に小節（bar。最初の小節の頭からが 1）と拍（beat。小節の頭が 1.0）の位置を付ける。"""
    period, per_bar, first = grid["period_s"], grid["beats_per_bar"], grid["first_bar_s"]
    for hit in hits:
        b = (hit["t"] - first) / period
        bar = int(np.floor(b / per_bar)) + 1
        hit["bar"], hit["beat"] = bar, float(b - per_bar * (bar - 1) + 1)
    return hits


def _shift(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or abs(value) > MAX_SHIFT_MS:
        raise PipelineError(f"{name} は −{MAX_SHIFT_MS:g}〜{MAX_SHIFT_MS:g} ミリ秒の数にしてください: {value!r}")
    return float(value)


def estimate(path, grid=None, strong_shift_ms=STRONG_SHIFT_MS, onset_shift_ms=ONSET_SHIFT_MS):
    """音源 path の打点の記録の本体（grid は mv audio grid の記録のパス。渡せば小節と拍の位置を付ける）。"""
    strong_shift_ms = _shift(strong_shift_ms, "--strong-shift-ms")
    onset_shift_ms = _shift(onset_shift_ms, "--onset-shift-ms")
    librosa = _librosa()
    import soundfile
    try:
        info = soundfile.info(str(path))
    except RuntimeError as error:
        raise PipelineError(f"音源を読めません（{path}）: {error}") from error
    audio_sha256 = sha256(path)
    grid_info = load_grid(grid, audio_sha256) if grid is not None else None
    y, rate = librosa.load(str(path), sr=RATE, mono=True)
    if len(y) / rate < MIN_SECONDS:
        raise PipelineError(f"音源が短すぎます（{len(y) / rate:.3f} 秒。{MIN_SECONDS:g} 秒以上のこと）")
    strong_hits = strong(y, rate, seconds(strong_shift_ms), librosa)
    onset_hits = onsets(y, rate, seconds(onset_shift_ms), librosa)
    if grid_info is not None:
        place(strong_hits, grid_info)
        place(onset_hits, grid_info)
    return {
        "schema_version": SCHEMA,
        "estimated": True,
        "audio": {"sha256": audio_sha256, "sample_rate": int(info.samplerate), "channels": int(info.channels),
                  "duration_s": info.frames / info.samplerate},
        "strong": strong_hits,
        "onsets": onset_hits,
        "grid": grid_info,
        "settings": {
            "sample_rate": RATE,
            "strong": {"hop": STRONG_HOP, "peaks": {**STRONG_PEAKS, "delta": f"{STRONG_DELTA_SCALE} * percentile {STRONG_DELTA_PERCENTILE}"},
                       "shift_ms": strong_shift_ms, "strength": f"/ (percentile {STRENGTH_PERCENTILE} + 1e-9)"},
            "onsets": {"n_fft": ONSET_N_FFT, "hop": ONSET_HOP, "band_hz": [ONSET_LOW_HZ, ONSET_HIGH_HZ], "gain": ONSET_GAIN,
                       "peaks": ONSET_PEAKS, "shift_ms": onset_shift_ms, "strength": f"/ percentile {STRENGTH_PERCENTILE} of the peaks"},
        },
        "tools": {"librosa": librosa.__version__, "numpy": np.__version__, "soundfile": soundfile.__version__},
    }


def run(source, grid=None, out=None, force=False, strong_shift_ms=STRONG_SHIFT_MS, onset_shift_ms=ONSET_SHIFT_MS):
    """打点を推定して記録を書く。同じ中身のファイルがあれば unchanged、違えば --force が要る。"""
    path, recorded, default_out, inputs = files.source(source, ".hits.json")
    out = Path(out) if out else default_out
    if grid is not None:
        inputs[files.same_file(grid)] = "--grid の記録"
    files.refuse_inputs(out, inputs)
    record = estimate(path, grid, strong_shift_ms, onset_shift_ms)
    record["audio"] = {"path": recorded, **record["audio"]}
    state = files.write_json(out, record, force)
    strongest = max(record["onsets"], key=lambda hit: hit["strength"], default=None)
    return {"hits": str(out), "state": state, "estimated": True, "duration_s": record["audio"]["duration_s"],
            "strong": len(record["strong"]), "onsets": len(record["onsets"]), "grid": record["grid"] is not None,
            "strong_shift_ms": record["settings"]["strong"]["shift_ms"], "onset_shift_ms": record["settings"]["onsets"]["shift_ms"],
            "first_strong_s": [round(hit["t"], 3) for hit in record["strong"][:8]],
            "strongest_onset": None if strongest is None else {"t": round(strongest["t"], 3), "strength": round(strongest["strength"], 3)}}
