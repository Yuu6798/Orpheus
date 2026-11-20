"""フレーム単位の 3 帯域 FFT 音声特徴量（`mv audio features`）。

曲の音から、描画フレーム（`--fps`）ごとの低音（bass）・中音（mid）・高音（high）・全体 RMS・低音立ち上がり（onset）を
測って JSON に書く。`--grid`（同じ音源の `mv audio grid` 記録）を渡すと拍番号（`beat_num`）と拍パルス（`beat_pulse`）も
添える。2D の `hooks.py` や 3D の音響連動ギミック（柱・床波紋・発光強度）の共通入力として使える。

    mv audio features AUDIO|PROJECT [--fps 30] [--grid GRID.json]
        [--bass-hz 25 140] [--mid-hz 250 2600] [--high-hz 4000 10500]
        [--smooth 0.35] [--out FILE] [--force]

librosa や scipy には頼らず、`end.decode`（ffmpeg・モノ・22050 Hz）と numpy のハン窓 FFT だけで測る。
記録（`audio-features/0.1`、`contracts/` の外）は、音源の隣の `<名前>.features.json`
（`analysis.json` のあるフォルダなら `analysis.features.json`）。
"""
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, sha256
from . import files
from .end import RATE, decode
from .lows import load_grid

SCHEMA = "audio-features/0.1"
FPS = 30.0
BASS_HZ = (25.0, 140.0)
MID_HZ = (250.0, 2600.0)
HIGH_HZ = (4000.0, 10500.0)
SMOOTH = 0.35
CLIP_MAX = 1.35
BEAT_DECAY = 6.5


def _validate_band(name, band, nyquist):
    if len(band) != 2:
        raise PipelineError(f"{name} は [低い Hz, 高い Hz] の 2 つにしてください: {list(band)}")
    lo, hi = float(band[0]), float(band[1])
    if not (math.isfinite(lo) and math.isfinite(hi)) or not (0.0 <= lo < hi <= nyquist):
        raise PipelineError(f"{name} は 0 <= 低い Hz < 高い Hz <= {nyquist:.1f} Hz にしてください: {[lo, hi]}")
    return (lo, hi)


def _norm_smooth(arr, alpha, floor=1e-12):
    """98 パーセンタイル基準で正規化し、急な立ち上がりを保ちつつ減衰を平滑化する。floor 以下は無音扱い（0）。"""
    if len(arr) == 0:
        return []
    p98 = float(np.percentile(arr, 98))
    if not math.isfinite(p98) or p98 <= floor:
        return [0.0] * len(arr)
    clipped = np.clip(arr / p98, 0.0, CLIP_MAX)
    out = np.empty_like(clipped)
    prev = 0.0
    keep = 1.0 - alpha
    for idx, val in enumerate(clipped):
        v = float(val)
        smoothed = prev * keep + v * alpha
        prev = max(v, smoothed) if v > prev else smoothed
        out[idx] = prev
    return [round(float(v), 4) for v in out]


def measure(
    samples,
    rate=RATE,
    fps=FPS,
    grid=None,
    bass_hz=BASS_HZ,
    mid_hz=MID_HZ,
    high_hz=HIGH_HZ,
    smooth=SMOOTH,
):
    """フレームごとの 3 帯域 FFT 特徴量と拍パルスを測る（記録の `features` 欄）。"""
    if not math.isfinite(fps) or not fps > 0:
        raise PipelineError(f"--fps は正の数にしてください: {fps}")
    if not math.isfinite(smooth) or not (0.0 < smooth <= 1.0):
        raise PipelineError(f"--smooth は 0 より大きく 1 以下の数にしてください: {smooth}")
    nyquist = rate * 0.5
    b_band = _validate_band("--bass-hz", bass_hz, nyquist)
    m_band = _validate_band("--mid-hz", mid_hz, nyquist)
    h_band = _validate_band("--high-hz", high_hz, nyquist)

    x = np.asarray(samples, dtype=np.float64).reshape(-1)
    if len(x) == 0:
        raise PipelineError("音源の標本が空です")
    seconds = len(x) / float(rate)
    n_frames = max(1, int(round(seconds * fps)))
    hop = float(rate) / float(fps)
    # 高 fps でも低域 25..140 Hz の物理的な周波数分離を保つため最低 1024 標本（約 46 ms @ 22050 Hz）を確保
    win = max(1024, int(round(hop * 2.0)))
    # 2 の冪に切り上げて FFT を安定・高速化（高 fps でも低域 25..140 Hz の分解能 <= 10.8 Hz を保つため最低 2048 点）
    fft_n = max(2048, 1 << (win - 1).bit_length())
    hann = np.hanning(win)
    freqs = np.fft.rfftfreq(fft_n, 1.0 / float(rate))
    mask_b = (freqs >= b_band[0]) & (freqs < b_band[1])
    mask_m = (freqs >= m_band[0]) & (freqs < m_band[1])
    mask_h = (freqs >= h_band[0]) & (freqs < h_band[1])

    raw_b = np.zeros(n_frames, dtype=np.float64)
    raw_m = np.zeros(n_frames, dtype=np.float64)
    raw_h = np.zeros(n_frames, dtype=np.float64)
    raw_t = np.zeros(n_frames, dtype=np.float64)
    raw_r = np.zeros(n_frames, dtype=np.float64)

    half = win // 2
    for i in range(n_frames):
        center = int(round((i + 0.5) * hop))
        lo = center - half
        hi = lo + win
        seg = np.zeros(win, dtype=np.float64)
        src_lo = max(0, lo)
        src_hi = min(len(x), hi)
        if src_hi > src_lo:
            dst_lo = src_lo - lo
            seg[dst_lo:dst_lo + (src_hi - src_lo)] = x[src_lo:src_hi]
            raw_r[i] = math.sqrt(float(np.mean(seg[dst_lo:dst_lo + (src_hi - src_lo)] ** 2)))
        spec = np.abs(np.fft.rfft(seg * hann, n=fft_n)) ** 2
        raw_t[i] = math.sqrt(float(spec.sum()))
        if mask_b.any():
            raw_b[i] = math.sqrt(float(spec[mask_b].sum()))
        if mask_m.any():
            raw_m[i] = math.sqrt(float(spec[mask_m].sum()))
        if mask_h.any():
            raw_h[i] = math.sqrt(float(spec[mask_h].sum()))

    # 全体スペクトル RMS に対して -40 dB（1%）未満の微小サイドローブしか無い帯域を 1.0 へ誤増幅しない
    band_floor = max(1e-12, float(np.percentile(raw_t, 98)) * 0.01) if len(raw_t) else 1e-12
    bass = _norm_smooth(raw_b, smooth, floor=band_floor)
    mid = _norm_smooth(raw_m, smooth, floor=band_floor)
    high = _norm_smooth(raw_h, smooth, floor=band_floor)
    rms = _norm_smooth(raw_r, smooth, floor=1e-12)

    onset = []
    for i in range(n_frames):
        delta = max(0.0, bass[i] - (bass[i - 1] if i > 0 else 0.0))
        onset.append(round(min(1.0, delta * 3.5), 4))

    beat_num = None
    beat_pulse = None
    if grid is not None:
        phase_s = float(grid["phase_s"])
        period_s = float(grid["period_s"])
        beat_num = []
        beat_pulse = []
        for i in range(n_frames):
            t = i / float(fps)
            bn = (t - phase_s) / period_s
            beat_num.append(round(bn, 4))
            frac = bn - math.floor(bn)
            pulse = math.exp(-frac * BEAT_DECAY) if bn >= -0.05 else 0.0
            beat_pulse.append(round(pulse, 4))

    return {
        "fps": float(fps),
        "seconds": round(seconds, 6),
        "frames": n_frames,
        "bass": bass,
        "mid": mid,
        "high": high,
        "rms": rms,
        "onset": onset,
        "beat_num": beat_num,
        "beat_pulse": beat_pulse,
    }


def record(
    path,
    fps=FPS,
    grid=None,
    bass_hz=BASS_HZ,
    mid_hz=MID_HZ,
    high_hz=HIGH_HZ,
    smooth=SMOOTH,
    label=None,
):
    """音源の記録（`audio-features/0.1`）。"""
    path = Path(path)
    audio_sha256 = sha256(path)
    grid_info = load_grid(grid, audio_sha256) if grid is not None else None
    samples = decode(path)
    data = measure(samples, RATE, fps, grid_info, tuple(bass_hz), tuple(mid_hz), tuple(high_hz), smooth)
    return {
        "schema_version": SCHEMA,
        "source": {"path": label or path.name, "sha256": audio_sha256},
        "method": {
            "rate": RATE,
            "channels": "mean",
            "fps": float(fps),
            "level": "hann_fft_band_rms_p98",
            "bass_hz": [float(bass_hz[0]), float(bass_hz[1])],
            "mid_hz": [float(mid_hz[0]), float(mid_hz[1])],
            "high_hz": [float(high_hz[0]), float(high_hz[1])],
            "smooth": float(smooth),
        },
        "grid": None if grid_info is None else {k: grid_info[k] for k in ("path", "sha256", "period_s", "phase_s")},
        "features": data,
    }


def run(
    source,
    fps=FPS,
    grid=None,
    out=None,
    bass_hz=BASS_HZ,
    mid_hz=MID_HZ,
    high_hz=HIGH_HZ,
    smooth=SMOOTH,
    force=False,
):
    """`mv audio features`。記録を書き、要約を返す。"""
    path, label, default_out, inputs = files.source(source, ".features.json")
    out = Path(out) if out else default_out
    if grid is not None:
        inputs[files.same_file(grid)] = "--grid の記録"
    files.refuse_inputs(out, inputs)
    data = record(path, fps, grid, bass_hz, mid_hz, high_hz, smooth, label)
    state = files.write_json(out, data, force)
    f = data["features"]
    return {
        "out": str(out),
        "state": state,
        "fps": f["fps"],
        "seconds": f["seconds"],
        "frames": f["frames"],
        "has_grid": data["grid"] is not None,
    }
