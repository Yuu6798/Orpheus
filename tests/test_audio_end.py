"""音の終わり（mv audio end）と、完成動画の音の終わりと絵の終わり（mv gate tail）: 0.1 秒ごとの音量の窓、鳴っている窓の決め方、
完全な無音、音が鳴っている所で終わる音、記録と報告、悪い設定。合成の音（正弦波と無音）と小さな動画で確かめる。"""
import json
import math
import wave

import numpy as np
import pytest

from mv_pipeline import encode
from mv_pipeline.audio import end as audio_end
from mv_pipeline.gate import tail
from mv_pipeline.io import PipelineError

RATE = 48000


def _wav(path, parts):
    """parts = [(秒, 振幅)]。振幅 0 は完全な無音、ほかは 440 Hz の正弦波。左右に同じ音の 16 ビットの WAV。"""
    chunks = []
    for seconds, amplitude in parts:
        n = int(round(seconds * RATE))
        t = np.arange(n) / RATE
        chunks.append(amplitude * np.sin(2 * math.pi * 440 * t))
    data = (np.concatenate(chunks) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes(np.repeat(data[:, None], 2, axis=1).tobytes())
    return path


def test_windows_and_the_sounding_end():
    samples = np.concatenate([np.full(int(0.25 * audio_end.RATE), 0.5, np.float32), np.zeros(int(0.3 * audio_end.RATE), np.float32)])
    starts, rms, zero, size = audio_end.windows(samples, audio_end.RATE, 0.1)
    assert size == 2205 and len(rms) == 6 and np.allclose(starts, np.arange(6) * 0.1)
    assert rms[0] == pytest.approx(0.5) and rms[2] == pytest.approx(0.5 * math.sqrt(1102 / 2205), rel=1e-3) and rms[3] == 0.0
    assert list(zero) == [False, False, False, True, True, True]
    m = audio_end.measure(samples)
    assert m["end_s"] == pytest.approx(0.3) and m["tail_s"] == pytest.approx(0.25, abs=1e-3)       # 標本の数の丸め
    assert m["digital_silence_from_s"] == pytest.approx(0.3)
    assert not m["cut_while_sounding"] and m["peak_dbfs"] == pytest.approx(20 * math.log10(0.5), abs=1e-3)


def test_quiet_tails_below_the_floor_do_not_count():
    rate = audio_end.RATE
    loud = np.full(rate, 0.5, np.float32)
    hum = np.full(rate, 0.5 * 10 ** (-70 / 20), np.float32)                     # いちばん大きい窓から −70 dB の音は鳴っていない扱い
    m = audio_end.measure(np.concatenate([loud, hum]))
    assert m["end_s"] == pytest.approx(1.0) and m["digital_silence_from_s"] is None
    assert audio_end.measure(np.concatenate([loud, hum]), floor_db=-80)["end_s"] == pytest.approx(2.0)
    assert audio_end.measure(loud)["cut_while_sounding"]                           # 最後の窓まで鳴っている
    silent = audio_end.measure(np.zeros(rate, np.float32))
    assert silent["end_s"] == 0.0 and silent["peak_dbfs"] is None and silent["digital_silence_from_s"] == 0.0
    for kwargs, word in (({"window_s": 0}, "window"), ({"floor_db": 3}, "floor-db")):
        with pytest.raises(PipelineError, match=word):
            audio_end.measure(loud, **kwargs)


def test_the_record_is_written_beside_the_song(tmp_path):
    song = _wav(tmp_path / "song.wav", [(1.0, 0.5), (0.45, 0.0)])
    summary = audio_end.run(song)
    record = json.loads((tmp_path / "song.end.json").read_text(encoding="utf-8"))
    assert record["schema_version"] == "audio-end/0.1" and record["source"]["path"] == "song.wav"
    assert record["measure"]["end_s"] == pytest.approx(1.0) and summary["tail_s"] == pytest.approx(0.45, abs=1e-3)
    assert record["tail"][0][0] == 0.0 and record["tail"][-1][1] is None             # 終わりの 5 秒の窓（無音は null）
    assert audio_end.run(song)["state"] == "unchanged"
    with pytest.raises(PipelineError):
        audio_end.run(song, out=song)


def _video(tmp_path, song, seconds, fps=10):
    frames = (np.full((16, 16, 3), 40 * (k % 5), np.uint8) for k in range(int(seconds * fps)))
    path = tmp_path / "final.mp4"
    encode.write_video(path, frames, (16, 16), fps, 23, int(seconds * fps), {"path": song, "start": 0.0, "duration": seconds})
    return path


def test_the_gate_reports_how_long_the_picture_stays_after_the_sound(tmp_path):
    song = _wav(tmp_path / "song.wav", [(1.0, 0.5), (2.0, 0.0)])
    report, out = tail.run(_video(tmp_path, song, 1.5))
    m = report["measure"]
    assert report["schema_version"] == "gate-tail-report/0.1" and report["video"]["seconds"] == pytest.approx(1.5)
    assert m["audio_end_s"] == pytest.approx(1.0, abs=0.11) and m["picture_after_s"] == pytest.approx(0.5, abs=0.11)
    assert report["status"] == "pass" and (out / "tail_report.json").is_file()
    long_tail, _ = tail.run(_video(tmp_path, song, 3.0), out=tmp_path / "long", max_tail_s=1.0)
    assert long_tail["status"] == "warn" and "続きます" in long_tail["reasons"][0]
    cut = _wav(tmp_path / "cut.wav", [(3.0, 0.5)])
    sounding, _ = tail.run(_video(tmp_path, cut, 2.0), out=tmp_path / "cut")
    assert sounding["status"] == "warn" and sounding["measure"]["cut_while_sounding"]


def test_a_late_audio_stream_is_measured_on_the_picture_timeline(tmp_path):
    """音の流れが映像より 2 秒遅れて始まる動画（mp4 の編集リスト）: 音の終わりは映像の最初のコマから数える（Codex の指摘）。"""
    import subprocess
    from mv_pipeline import media
    song = _wav(tmp_path / "song.wav", [(2.5, 0.5), (2.5, 0.0)])
    aligned = _video(tmp_path, song, 5.0)
    late = tmp_path / "late.mp4"
    subprocess.run([media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(aligned), "-itsoffset", "2.0", "-i", str(aligned),
                    "-map", "0:v:0", "-map", "1:a:0", "-c", "copy", str(late)], check=True)
    report, _ = tail.run(late)
    m = report["measure"]
    assert m["audio_start_s"] == pytest.approx(2.0, abs=0.05)
    assert m["audio_end_s"] == pytest.approx(4.5, abs=0.11) and m["picture_after_s"] == pytest.approx(0.5, abs=0.11)
    assert report["status"] == "pass" and report["tail"][0][0] >= 2.0 - 0.05
    on_time, _ = tail.run(aligned, out=tmp_path / "aligned")
    assert on_time["measure"]["audio_start_s"] == pytest.approx(0.0, abs=0.05)


def test_the_gate_settings_and_silent_videos_are_checked(tmp_path):
    assert tail.settings({}) == {"max_tail_s": 1.0, "window_s": 0.1, "floor_db": -60.0}
    for value, word in (({"max_tail": 1}, "知らない欄"), ({"max_tail_s": -1}, "範囲"), ({"floor_db": "low"}, "数"), ([], "形")):
        with pytest.raises(PipelineError, match=word):
            tail.settings(value)
    frames = (np.zeros((16, 16, 3), np.uint8) for _ in range(5))
    mute = tmp_path / "mute.mp4"
    encode.write_video(mute, frames, (16, 16), 10, 23, 5)
    with pytest.raises(PipelineError, match="音がありません"):
        tail.run(mute)


def _gate(tmp_path, song, seconds):
    """音の付いた合成の動画と、それを指す最小の timeline・gate.json（gate の flash の確かめと同じ形）。"""
    video = _video(tmp_path, song, seconds)
    (tmp_path / "timeline.json").write_text(json.dumps({"fps": 10, "duration_s": seconds, "lyrics": [], "ui_regions": []}),
                                            encoding="utf-8")
    config = {"timeline": "timeline.json", "video": video.name}
    (tmp_path / "gate.json").write_text(json.dumps(config), encoding="utf-8")
    return tmp_path / "gate.json", config


def test_gate_check_adds_the_tail_row_only_when_tail_is_written(tmp_path):
    from mv_pipeline.gate import checks
    song = _wav(tmp_path / "song.wav", [(1.0, 0.5), (3.0, 0.0)])
    path, config = _gate(tmp_path, song, 2.5)
    before, out = checks.run(path, tmp_path / "before")
    assert len(before["checks"]) == 9 and "tail" not in before and not (out / "tail_report.json").exists()
    path.write_text(json.dumps({**config, "tail": {}}), encoding="utf-8")
    after, out = checks.run(path, tmp_path / "after")
    assert after["checks"][:9] == before["checks"] and len(after["checks"]) == 10
    row = after["checks"][9]
    assert row["check"] == "音の終わり" and row["status"] == "warn"                 # 1.5 秒の余韻は目安 1 秒を超える（要確認）
    assert "絵はその後 1.50 秒続く" in row["summary"] and row["problems"][0]["t"] == pytest.approx(1.0, abs=0.11)
    assert after["overall"] != "fail" and after["tail"] == {"max_tail_s": 1.0, "window_s": 0.1, "floor_db": -60.0, "report": "tail_report.json"}
    assert json.loads((out / "tail_report.json").read_text(encoding="utf-8"))["status"] == "warn"
    path.write_text(json.dumps({**config, "tail": {"max_tail_s": 2.0}}), encoding="utf-8")
    assert checks.run(path, tmp_path / "loose")[0]["checks"][9]["status"] == "pass"
    path.write_text(json.dumps({**config, "tail": {"max": 1}}), encoding="utf-8")
    with pytest.raises(PipelineError, match="知らない欄"):
        checks.run(path, tmp_path / "bad")


def test_the_commands_write_the_record_and_the_report(tmp_path):
    from test_gate_flashes import run_cli
    song = _wav(tmp_path / "song.wav", [(1.0, 0.5), (0.5, 0.0)])
    code, result, _ = run_cli("audio", "end", song)
    assert code == 0 and result["end_s"] == pytest.approx(1.0) and (tmp_path / "song.end.json").is_file()
    video = _video(tmp_path, song, 1.4)
    code, result, _ = run_cli("gate", "tail", video, "--max-tail-s", "0.2")
    assert code == 0 and result["status"] == "warn" and result["picture_after_s"] == pytest.approx(0.4, abs=0.11)
    assert (tmp_path / "final_tail_report" / "tail_report.json").is_file()
