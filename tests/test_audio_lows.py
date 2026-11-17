"""低音が抜ける所（mv audio lows）: 窓（拍か秒）、低音の帯と全体の強さ、鳴っている窓、中央値からの下がり幅、続いた窓のまとめ、
完全な無音（強さが 0 の窓は鳴っていない）、拍の格子の記録、記録と報告、悪い設定。合成の音（低い正弦波と高い正弦波と無音）で確かめる。値は作り物。リポジトリ外の 3 作目の VRM の
ダンスの曲での確かめは docs/audio.md。"""
import json
import math
import wave

import numpy as np
import pytest

from mv_pipeline.audio import lows
from mv_pipeline.io import PipelineError, sha256

RATE = 48000


def _song(parts):
    """parts = [(秒, 低音があるか, 高い音があるか)]。60 Hz と 2 kHz の正弦波（どちらも振幅 0.3）を 22050 Hz のモノの float32 で。"""
    chunks, t0 = [], 0.0
    for seconds, low, high in parts:
        n = int(round(seconds * lows.RATE))
        t = t0 + np.arange(n) / lows.RATE
        chunks.append((0.3 * np.sin(2 * math.pi * 60 * t) * low + 0.3 * np.sin(2 * math.pi * 2000 * t) * high).astype(np.float32))
        t0 += seconds
    return np.concatenate(chunks)


def _wav(path, samples):
    resampled = np.interp(np.arange(int(len(samples) * RATE / lows.RATE)) / RATE, np.arange(len(samples)) / lows.RATE, samples)
    data = (resampled * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())
    return path


SONG = [(2.0, 1, 1), (1.0, 0, 1), (2.0, 1, 1), (1.0, 0, 0)]        # 2〜3 秒に低音が抜け、5 秒から無音


def test_windows_drop_out_where_the_low_band_falls_and_the_sound_goes_on():
    m = lows.measure(_song(SONG), window_s=0.5)
    assert m["windows"] == 12 and m["sounding_windows"] == 10                   # 終わりの 1 秒（2 つの窓）は鳴っていない
    assert [r["drop"] for r in m["rows"]] == [False] * 4 + [True] * 2 + [False] * 6
    assert m["drops"] == [{"start_s": 2.0, "end_s": 3.0, "windows": 2, "beats": None,
                           "lowest_rel_db": min(r["low_rel_db"] for r in m["rows"][4:6])}]
    assert m["drops"][0]["lowest_rel_db"] < -40                                    # 正弦波の帯の強さが抜けると、ずっと下がる
    assert all(abs(r["low_rel_db"]) < 1.0 for r in m["rows"][:4])
    assert m["rows"][4]["total_db"] > m["rows"][0]["total_db"] - 6                 # 全体の強さは高い音でまだ鳴っている


def test_digital_silence_is_never_sounding(tmp_path):
    # 全部が無音: いちばん大きい窓からの相対で決めると、無音の窓がみな鳴っている窓になる。強さが 0 の窓は先に除く
    m = lows.measure(np.zeros(int(3 * lows.RATE), dtype=np.float32), window_s=0.5)
    assert m["windows"] == 6 and m["sounding_windows"] == 0 and m["low_median_db"] is None and m["drops"] == []
    assert all(r["low_db"] is None and r["total_db"] is None and r["low_rel_db"] is None and not r["sounding"] and not r["drop"]
               for r in m["rows"])
    m = lows.measure(_song(SONG), window_s=0.5)                                       # 終わりの完全な無音の窓も dB は null
    assert [r["total_db"] is None for r in m["rows"]] == [False] * 10 + [True] * 2 and m["sounding_windows"] == 10
    from mv_pipeline import cli                                                        # CLI でも（音の読み込みを通して）同じ
    audio = _wav(tmp_path / "silence.wav", np.zeros(int(3 * lows.RATE), dtype=np.float32))
    assert cli.main(["audio", "lows", str(audio), "--out", str(tmp_path / "silence.lows.json")]) == 0
    record = json.loads((tmp_path / "silence.lows.json").read_text(encoding="utf-8"))["measure"]
    assert record["sounding_windows"] == 0 and record["low_median_db"] is None and record["drops"] == []


def test_a_wider_band_or_a_deeper_drop_changes_what_counts():
    song = _song(SONG)
    assert lows.measure(song, window_s=0.5, band=(1000.0, 3000.0))["drops"] == []   # 高い帯は抜けない
    assert len(lows.measure(song, window_s=0.5, drop_db=200.0)["drops"]) == 0
    for kwargs, word in (({"window_s": 0}, "window"), ({"band": (150.0, 20.0)}, "band"), ({"drop_db": 0}, "drop-db"),
                         ({"floor_db": 1}, "floor-db")):
        with pytest.raises(PipelineError, match=word):
            lows.measure(song, **kwargs)


def _grid(path, audio, period=0.5, phase=0.25):
    beats = [phase + period * k for k in range(0, 13)]
    record = {"schema_version": "audio-grid/0.1", "estimated": True, "audio": {"sha256": sha256(audio)}, "bpm": 60 / period,
              "period_s": period, "phase_s": phase, "beats_per_bar": 4, "downbeat_phase": 0, "beats_s": beats, "downbeats": beats[::4]}
    path.write_text(json.dumps(record), encoding="utf-8")
    return path


def test_a_grid_makes_one_window_per_beat_and_names_the_beats(tmp_path):
    audio = _wav(tmp_path / "song.wav", _song(SONG))
    grid = _grid(tmp_path / "song.grid.json", audio)
    summary = lows.run(audio, grid)
    record = json.loads((tmp_path / "song.lows.json").read_text(encoding="utf-8"))
    assert summary["state"] == "written" and record["schema_version"] == "audio-lows/0.1"
    assert record["method"]["window"] == "beats" and record["grid"]["phase_s"] == 0.25 and record["source"]["sha256"] == sha256(audio)
    beats = [(r["beat"], r["start_s"]) for r in record["measure"]["rows"]][:3]
    assert beats == [(0, 0.25), (1, 0.75), (2, 1.25)]
    drop = record["measure"]["drops"]
    assert [d["beats"] for d in drop] == [[4]]                                         # 抜け（2〜3 秒）にまるごと入る拍は 4（2.25〜2.75 秒）だけ
    half = record["measure"]["rows"][5]
    assert half["beat"] == 5 and -10 < half["low_rel_db"] < -2                          # 半分だけ入る拍 5 は、少ししか下がらない
    assert summary["drops"] == [{k: drop[0][k] for k in ("start_s", "end_s", "beats", "lowest_rel_db")}]
    assert lows.run(audio, grid)["state"] == "unchanged"
    other = _wav(tmp_path / "other.wav", _song([(1.0, 1, 1)]))
    with pytest.raises(PipelineError, match="別の音源"):
        lows.run(other, grid)
    with pytest.raises(PipelineError, match="同じファイル"):
        lows.run(audio, grid, out=grid)


def test_the_cli_writes_the_record(tmp_path):
    from mv_pipeline import cli
    audio = _wav(tmp_path / "song.wav", _song(SONG))
    assert cli.main(["audio", "lows", str(audio), "--window", "0.5", "--out", str(tmp_path / "lows.json")]) == 0
    record = json.loads((tmp_path / "lows.json").read_text(encoding="utf-8"))
    assert record["method"]["window_s"] == 0.5 and record["measure"]["drops"][0]["start_s"] == 2.0
