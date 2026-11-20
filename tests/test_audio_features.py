"""mv audio features（フレーム単位の 3 帯域 FFT 特徴量抽出）のテスト。"""
import json
import math
import wave
from pathlib import Path

import numpy as np
import pytest

from mv_pipeline import cli
from mv_pipeline.audio import features
from mv_pipeline.audio.grid import SCHEMA as GRID_SCHEMA
from mv_pipeline.io import PipelineError, sha256


def _write_wav(path: Path, samples: np.ndarray, rate: int = 22050) -> None:
    pcm = np.clip(samples * 32767.0, -32767.0, 32767.0).astype("<i2")
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())


def test_measure_separates_bass_mid_high_bands():
    rate = 22050
    dur = 3.0
    t = np.arange(int(rate * dur), dtype=np.float64) / rate
    # 0..1s: 80 Hz (bass), 1..2s: 1000 Hz (mid), 2..3s: 6000 Hz (high)
    sig = np.zeros_like(t)
    m0 = t < 1.0
    m1 = (t >= 1.0) & (t < 2.0)
    m2 = t >= 2.0
    sig[m0] = 0.8 * np.sin(2.0 * math.pi * 80.0 * t[m0])
    sig[m1] = 0.8 * np.sin(2.0 * math.pi * 1000.0 * t[m1])
    sig[m2] = 0.8 * np.sin(2.0 * math.pi * 6000.0 * t[m2])

    res = features.measure(sig, rate=rate, fps=30.0)
    assert res["frames"] == 90
    assert res["beat_num"] is None and res["beat_pulse"] is None

    # 各区間の中央で対応帯域が他帯域を明確に上回る
    assert res["bass"][15] > 0.7 and res["mid"][15] < 0.05 and res["high"][15] < 0.05
    assert res["mid"][45] > 0.7 and res["bass"][45] < 0.05 and res["high"][45] < 0.05
    assert res["high"][75] > 0.7 and res["bass"][75] < 0.05 and res["mid"][75] < 0.05


def test_measure_preserves_low_band_resolution_at_high_fps():
    rate = 22050
    t = np.arange(rate, dtype=np.float64) / rate
    sig = 0.8 * np.sin(2.0 * math.pi * 60.0 * t)
    res = features.measure(sig, rate=rate, fps=120.0)
    assert res["frames"] == 120
    assert res["bass"][60] > 0.7 and res["mid"][60] < 0.05


def test_measure_handles_silence_without_nan():
    sig = np.zeros(22050, dtype=np.float64)
    res = features.measure(sig, rate=22050, fps=30.0)
    assert res["frames"] == 30
    for key in ("bass", "mid", "high", "rms", "onset"):
        assert all(v == 0.0 for v in res[key])


def test_measure_with_grid_computes_beat_pulse():
    rate = 22050
    sig = 0.5 * np.sin(2.0 * math.pi * 100.0 * np.arange(rate * 2) / rate)
    grid = {"phase_s": 0.5, "period_s": 0.5}
    res = features.measure(sig, rate=rate, fps=30.0, grid=grid)
    # t = 0.5s (frame 15) は拍 0 ちょうど → pulse ≈ 1.0
    assert abs(res["beat_num"][15] - 0.0) < 1e-3
    assert res["beat_pulse"][15] > 0.95
    # t = 0.75s (frame 22〜23) は拍間 → 減衰している
    assert res["beat_pulse"][23] < 0.15


def test_measure_rejects_invalid_params():
    sig = np.ones(1000, dtype=np.float64)
    with pytest.raises(PipelineError):
        features.measure(sig, fps=0.0)
    with pytest.raises(PipelineError):
        features.measure(sig, smooth=0.0)
    with pytest.raises(PipelineError):
        features.measure(sig, bass_hz=(200.0, 100.0))
    with pytest.raises(PipelineError):
        features.measure(sig, high_hz=(4000.0, 20000.0))  # Nyquist 11025 Hz 超過


def test_cli_audio_features_roundtrip_and_force(tmp_path):
    wav = tmp_path / "song.wav"
    t = np.arange(22050, dtype=np.float64) / 22050.0
    _write_wav(wav, 0.7 * np.sin(2.0 * math.pi * 90.0 * t))

    grid_path = tmp_path / "song.grid.json"
    grid_path.write_text(json.dumps({
        "schema_version": GRID_SCHEMA,
        "audio": {"path": wav.name, "sha256": sha256(wav)},
        "period_s": 0.5,
        "phase_s": 0.0,
        "beats_s": [0.0, 0.5, 1.0],
    }), encoding="utf-8")

    out_path = tmp_path / "song.features.json"
    res1 = features.run(wav, fps=30.0, grid=grid_path, out=out_path)
    assert res1["state"] == "written"
    assert res1["frames"] == 30 and res1["has_grid"] is True

    # 同一入力の再実行は unchanged
    res2 = features.run(wav, fps=30.0, grid=grid_path, out=out_path)
    assert res2["state"] == "unchanged"

    # 別設定の上書きは --force が必須
    with pytest.raises(PipelineError):
        features.run(wav, fps=24.0, grid=grid_path, out=out_path, force=False)
    res3 = features.run(wav, fps=24.0, grid=grid_path, out=out_path, force=True)
    assert res3["state"] == "written" and res3["frames"] == 24

    # 入力ファイルへの上書きは --force でも拒否
    with pytest.raises(PipelineError):
        features.run(wav, out=wav, force=True)

    # CLI 経由の正常終了確認
    rc = cli.main(["audio", "features", str(wav), "--fps", "24", "--grid", str(grid_path), "--out", str(out_path)])
    assert rc == 0
