"""plan.light_keys（曲の音 → 光のキー）の確かめ。音は作り物（どの曲の時刻とも関係ない）。
librosa・scipy・soundfile がある環境では、同じ値になることも確かめる（無ければその確かめだけ skip）。"""
import json
import struct
import sys
import warnings
import wave
from pathlib import Path

import numpy as np
import pytest

from model3d import contracts
from model3d import light_keys as lk
from model3d.io import ToolError, atomic_json

RATE, FPS = 16000, 20                                    # 1 コマ 800 標本


def _write_wav(path, samples, rate=RATE, width=2):
    """samples（(標本,) か (標本, 左右) の -1〜1）を PCM の WAV に。"""
    x = np.asarray(samples, dtype=np.float64)
    x = x[:, None] if x.ndim == 1 else x
    if width == 1:
        data = np.clip(np.round(x * 128 + 128), 0, 255).astype(np.uint8).tobytes()
    elif width == 2:
        data = np.clip(np.round(x * 32768), -32768, 32767).astype("<i2").tobytes()
    elif width == 3:
        v = np.clip(np.round(x * 2 ** 23), -2 ** 23, 2 ** 23 - 1).astype(np.int64).reshape(-1)
        data = np.stack([(v >> s) & 0xFF for s in (0, 8, 16)], axis=1).astype(np.uint8).tobytes()
    else:
        data = np.clip(np.round(x * 2 ** 31), -2 ** 31, 2 ** 31 - 1).astype("<i4").tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(x.shape[1])
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def _float_wav(path, samples, rate=RATE):
    """IEEE float の WAV（標準の wave は読めない形式）。"""
    data = np.asarray(samples, "<f4").tobytes()
    fmt = struct.pack("<HHIIHH", 3, 1, rate, rate * 4, 4, 32)
    body = b"WAVE" + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(data)) + data
    path.write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return path


def _ramp(t, start, rise=0.01):
    """start 秒から rise 秒で 0 → 1 になる滑らかな立ち上がり（急な立ち上がりは帯の外に漏れる）。"""
    return 0.5 - 0.5 * np.cos(np.pi * np.clip((t - start) / rise, 0, 1))


def _song(seconds=2.0, rate=RATE):
    """作り物の曲: 0.25 秒から 0.5 秒ごとに 60 Hz の打音（0.06 秒）、1 秒から 1000 Hz、1.5 秒から 6000 Hz の音。左右で少し違う。"""
    t = np.arange(int(seconds * rate)) / rate
    kick = sum(np.sin(2 * np.pi * 60 * t) * np.sin(np.pi * np.clip((t - s) / 0.06, 0, 1)) ** 2
               for s in np.arange(0.25, seconds, 0.5))
    mid = 0.3 * np.sin(2 * np.pi * 1000 * t) * _ramp(t, 1.0)
    high = 0.2 * np.sin(2 * np.pi * 6000 * t) * _ramp(t, 1.5)
    noise = np.random.default_rng(1).standard_normal(len(t)) * 1e-4
    left = 0.5 * kick + mid + high + noise
    return np.stack([left, left * 0.9], axis=1)


def test_norm_attack_release_and_the_beats():
    x = np.arange(101, dtype=np.float32)
    n = lk.norm(x)
    assert n[20] == 0 and n[0] == 0 and n[99] == pytest.approx(1.0) and n[100] == 1 and n[60] == pytest.approx(40 / 79)
    assert lk.attack_release(np.array([0, 1, 0, 0, 0.5, 0.0]), 0.5).tolist() == [0, 1, 0.5, 0.25, 0.5, 0.25]
    beats = lk.grid_beats(1.3, 0.5, 40, 20)            # 窓 2 秒: 0.3・0.8・1.3・1.8 秒
    assert beats == [{"k": -2, "t": 0.3, "frame": 6}, {"k": -1, "t": 0.8, "frame": 16}, {"k": 0, "t": 1.3, "frame": 26},
                     {"k": 1, "t": 1.8, "frame": 36}]
    assert lk.grid_beats(5.0, 0.5, 40, 20)[0] == {"k": -10, "t": 0.0, "frame": 0}    # 0 秒ちょうどの拍は入る
    assert lk.grid_beats(-3.0, 1.0, 20, 10)[-1]["t"] == 1.0                      # 2 秒ちょうど（窓の終わり）は入らない
    with pytest.raises(ToolError, match="period"):
        lk.grid_beats(0.0, 1e-4, 10, 10)


def test_the_window_and_the_frame_centres():
    w = lk.hann(2048)
    k = np.arange(2048)
    assert w[0] == 0 and w[1024] == 1 and np.allclose(w, 0.5 - 0.5 * np.cos(2 * np.pi * k / 2048), atol=1e-15)
    assert np.allclose(w[1:], w[1:][::-1], atol=1e-15)                           # 周期的（端の 0 は 1 つだけ）
    assert lk.centers(3, 48000, 30).tolist() == [0, 1600, 3200]
    assert lk.centers(4, 44100, 24).tolist() == [0, 1838, 3675, 5512]             # 割り切れないときも、コマの時刻に合わせる
    assert lk.centers(7201, 44100, 24)[-1] == 300 * 44100                         # 長くてもずれがたまらない


def test_the_window_is_the_scipy_hann():
    signal = pytest.importorskip("scipy.signal")
    for n in (16, 1000, 2048, 4096):
        assert np.array_equal(lk.hann(n), signal.get_window("hann", n, fftbins=True))


def test_the_power_is_the_librosa_stft():
    librosa = pytest.importorskip("librosa")
    rng = np.random.default_rng(0)
    for rate, fps, seconds in ((16000, 20, 1.3), (48000, 30, 2.0), (22050, 25, 0.05), (8000, 40, 0.5)):
        hop = int(rate / fps)
        y = (rng.standard_normal(int(seconds * rate)) * 0.3).astype(np.float32)
        frames = int(round(seconds * fps)) + 4                                    # 音より 4 コマ長い窓
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            ref = np.abs(librosa.stft(y, n_fft=2048, hop_length=hop, center=True)) ** 2
        ref = ref[:, :frames]
        if ref.shape[1] < frames:
            ref = np.pad(ref, ((0, 0), (0, frames - ref.shape[1])))
        mine, shown = lk.power(y, rate, fps, frames)
        assert mine.dtype == ref.dtype and np.array_equal(mine, ref) and shown == 1 + len(y) // hop
        assert mine.flags.f_contiguous == ref.flags.f_contiguous                  # 帯の和の足し方の順も同じになる
        assert np.array_equal(lk.frequencies(rate), librosa.fft_frequencies(sr=rate, n_fft=2048))


def test_the_keys_follow_the_bands_of_the_song():
    keys, shown = lk.light_keys(_song().astype(np.float32), RATE, 40, FPS, {"period": 0.5, "k0_time": 0.25}, song_start=3.0)
    assert contracts.validate("light-keys", keys) and shown == 40 and keys["start_s"] == 3.0 and keys["frames"] == 40
    low, mid, high = (np.array(keys[name]) for name in ("low", "mid", "high"))
    onsets = [5, 15, 25, 35]                                                      # 打音の頭のコマ（0.25 秒から 0.5 秒ごと）
    assert all(low[f - 1:f + 2].max() > 0.9 for f in onsets) and all(low[f + 5] < 0.5 for f in onsets[:-1])
    for band, rel in ((low, 0.78), (mid, 0.86), (high, 0.72)):                    # 下がりは前のコマの release 倍まで
        assert np.all(band[1:] >= band[:-1] * rel - 1e-3)
    assert mid[:15].max() < 0.2 and mid[22:].min() > 0.9                          # 1000 Hz は 1 秒から
    assert high[:25].max() < 0.2 and high[32:].min() > 0.9                        # 6000 Hz は 1.5 秒から
    assert [b["frame"] for b in keys["beats"]] == onsets and [b["k"] for b in keys["beats"]] == [0, 1, 2, 3]


def test_an_empty_band_is_refused():
    with pytest.raises(ToolError, match="帯 high"):                               # 8000 Hz の音に 5000〜16000 Hz の帯は無い
        lk.light_keys(np.zeros((8000, 1), np.float32), 8000, 10, 10, {"period": 0.5, "k0_time": 0.0})
    keys, _ = lk.light_keys(np.zeros((8000, 1), np.float32), 8000, 10, 10, {"period": 0.5, "k0_time": 0.0},
                            bands={"high": [2000.0, 4000.0]})
    assert keys["high"] == [0.0] * 10                                             # 無音は 0


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_pcm_wav_is_read_without_soundfile(tmp_path, monkeypatch, width):
    x = np.array([[0.0, -1.0], [0.5, 0.25], [-0.5, 0.75], [0.125, -0.25]])
    path = _write_wav(tmp_path / f"w{width}.wav", x, rate=8000, width=width)
    monkeypatch.setitem(sys.modules, "soundfile", None)                          # soundfile が無い環境
    samples, rate, frames, length = lk.read_window(path, 0.0, None, 2000)        # 4 標本 = 0.5 ミリ秒 = 1 コマ
    assert rate == 8000 and frames == 1 and length == 4 / 8000
    assert samples.dtype == np.float32 and samples.shape == (4, 2) and samples.flags.c_contiguous
    assert np.array_equal(samples, x.astype(np.float32))                          # どの幅でも割り切れる値はそのまま


def test_pcm_wav_reads_the_same_as_soundfile(tmp_path, monkeypatch):
    soundfile = pytest.importorskip("soundfile")
    x = np.random.default_rng(2).uniform(-1, 1, (3000, 2))
    for width in (1, 2, 3, 4):
        path = _write_wav(tmp_path / f"w{width}.wav", x, rate=8000, width=width)
        a, ra, fa, _ = lk.read_window(path, 0.05, 10, 100)
        with monkeypatch.context() as m:
            m.setitem(sys.modules, "soundfile", None)
            b, rb, fb, _ = lk.read_window(path, 0.05, 10, 100)
        assert ra == rb == 8000 and fa == fb == 10 and a.shape == b.shape and np.array_equal(a, b), width
    assert soundfile.info(str(path)).channels == 2


def test_a_float_wav_needs_soundfile(tmp_path, monkeypatch):
    path = _float_wav(tmp_path / "f.wav", np.zeros(800))
    monkeypatch.setitem(sys.modules, "soundfile", None)
    with pytest.raises(ToolError, match="PCM の WAV"):
        lk.read_window(path, 0.0, None, 20)


def _recipe(tmp_path, parameters, outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "keys",
                                           "inputs": {"song": {"path": "song.wav"}},
                                           "steps": [{"id": "keys", "operation": "plan.light_keys",
                                                      "inputs": {"audio": "$inputs.song"}, "parameters": parameters,
                                                      "outputs": outputs or {"keys": "keys.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_keys_that_plan_stage_reads(tmp_path):
    from model3d import executor
    _write_wav(tmp_path / "song.wav", _song(seconds=2.5))
    grid = {"period": 0.5, "k0_time": 0.75}
    result = executor.run_recipe(_recipe(tmp_path, {"grid": grid, "fps": FPS, "start": 0.5}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["keys"]
    keys = contracts.load("light-keys", step["artifacts"]["keys"])
    assert keys["frames"] == 40 and keys["fps"] == FPS and keys["start_s"] == 0.5 and keys["grid"] == grid   # 窓は 0.5〜2.5 秒
    assert [b["frame"] for b in keys["beats"]] == [5, 15, 25, 35]                # 窓の頭からの拍（0.25 秒から）
    observation = step["observations"][0]
    assert observation == {"kind": "light_keys", "frames": 40, "fps": FPS, "seconds": 2.0, "rate": RATE, "channels": 2,
                           "audio_seconds": 2.5, "silent_frames": 0, "beats": 4, "mean": observation["mean"]}
    again = executor.run_recipe(_recipe(tmp_path, {"grid": grid, "fps": FPS, "start": 0.5}), tmp_path / "runs")
    assert again["steps"]["keys"]["reused_from"] is not None                     # 計算だけなので、同じ音と設定なら再利用する
    from model3d.stage_plan import build_track
    bones = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": list(range(1, 41)),
             "bones": {"mixamorig:Hips": [[0, 0, 1]] * 40, "mixamorig:Head": [[0, 0, 1.6]] * 40}}
    track, report = build_track({"schema_version": "model3d-stage-plan/0.1"}, keys, bones)
    assert report["keys"] == {"frames": 40, "beats": 4, "beyond": 0} and len(track["frames"]) == 40


def test_a_window_longer_than_the_song_reports_the_silent_frames(tmp_path):
    from model3d import executor
    _write_wav(tmp_path / "song.wav", _song(seconds=1.0))
    result = executor.run_recipe(_recipe(tmp_path, {"grid": {"period": 0.5, "k0_time": 0.25}, "fps": FPS, "frames": 30,
                                                    "song_start": 12.0}), tmp_path / "runs")
    observation = result["steps"]["keys"]["observations"][0]
    assert observation["silent_frames"] == 9 and observation["frames"] == 30     # 中心が 1 秒より後の 9 コマは無音
    keys = json.loads(Path(result["steps"]["keys"]["artifacts"]["keys"]).read_text(encoding="utf-8"))
    assert keys["start_s"] == 12.0 and len(keys["low"]) == 30


@pytest.mark.parametrize("parameters, message", [
    ({}, "grid"),
    ({"grid": {"period": 0.5}}, "grid"),
    ({"grid": {"period": 0.5, "k0_time": 0, "bpm": 120}}, "grid"),
    ({"grid": {"period": 0, "k0_time": 0}}, "grid.period"),
    ({"grid": {"period": True, "k0_time": 0}}, "grid.period"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "fps": 0}, "fps"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "frames": 0}, "frames"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "frames": 2.5}, "frames"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "start": -1}, "start"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "n_fft": 1023}, "n_fft"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "n_fft": 8}, "n_fft"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "bands": {"sub": [20, 60]}}, "bands"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "bands": {"low": [130, 30]}}, "bands.low"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "bands": {"low": 60}}, "bands.low"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "release": {"mid": 1.5}}, "release.mid"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "percentiles": [99, 20]}, "percentiles"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "low_floor": 2}, "low_floor"),
    ({"grid": {"period": 0.5, "k0_time": 0}, "bpm": 120}, "知らないパラメーター"),
])
def test_bad_settings_are_refused(parameters, message):
    with pytest.raises(ToolError, match=message):
        lk.settings(parameters)


def test_the_settings_fill_the_defaults():
    s = lk.settings({"grid": {"period": 0.5, "k0_time": 1}, "start": 2.0, "bands": {"high": [4000, 8000]}, "release": {"low": 0.5}})
    assert s["fps"] == 30 and s["frames"] is None and s["song_start"] == 2.0 and s["n_fft"] == 2048
    assert s["bands"] == {"low": [30.0, 130.0], "mid": [300.0, 3000.0], "high": [4000.0, 8000.0]}
    assert s["release"] == {"low": 0.5, "mid": 0.86, "high": 0.72} and s["percentiles"] == [20.0, 99.0]


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def test_the_operation_checks_its_inputs_and_the_window(tmp_path):
    from model3d.ops import get
    op = get("plan.light_keys").function
    song = _write_wav(tmp_path / "song.wav", _song(seconds=1.0))
    grid = {"grid": {"period": 0.5, "k0_time": 0.25}, "fps": FPS}
    with pytest.raises(ToolError, match="audio"):
        op({}, grid, {"keys": tmp_path / "k.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="keys"):
        op({"audio": song}, grid, {}, Context(tmp_path))
    with pytest.raises(ToolError, match="start"):
        op({"audio": song}, {**grid, "start": 1.0}, {"keys": tmp_path / "k.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="1 コマ"):
        op({"audio": song}, {**grid, "start": 0.99}, {"keys": tmp_path / "k.json"}, Context(tmp_path))
    (tmp_path / "bad.wav").write_bytes(b"not a wav")
    with pytest.raises(ToolError, match="読めません"):
        op({"audio": tmp_path / "bad.wav"}, grid, {"keys": tmp_path / "k.json"}, Context(tmp_path))


@pytest.mark.parametrize("seconds, start, fps, frames", [(2.3, 0.1, 30, 66), (0.3, 0.2, 10, 1), (1.0, 0.7, 10, 3)])
def test_whole_frames_left_in_the_audio_are_all_counted(tmp_path, monkeypatch, seconds, start, fps, frames):
    path = _write_wav(tmp_path / "s.wav", np.zeros(int(round(seconds * 8000))), rate=8000)
    monkeypatch.setitem(sys.modules, "soundfile", None)
    assert lk.read_window(path, start, None, fps)[2] == frames   # (2.3 - 0.1) × 30 は浮動小数で 65.999… になる
