"""`mv deliver join` と、cv2 を使わない共通の書き出し（encode.py）。

つなぎと音は、実制作の仕上げの手順（ffmpeg の引数をそのまま写した ref_join）と、同じ入力で同じバイト列になるかを比べる。
"""
import json
import math
import os
import struct
import subprocess
import sys
import wave

import numpy as np
import pytest
from fx_synth import run_cli

from mv_pipeline import deliver, encode, media
from mv_pipeline.fx import probe
from mv_pipeline.io import PipelineError, read_json, sha256

SIZE, FPS = (64, 36), 12


def segment(path, first, count, size=SIZE, fps=FPS):
    """コマ番号 first から count コマの区間の動画（色が少しずつ変わる）。"""
    def frames():
        for n in range(first, first + count):
            image = np.zeros((size[1], size[0], 3), np.uint8)
            image[..., 0] = (n * 9) % 256
            image[: size[1] // 2, :, 1] = 200
            image[:, n % size[0]] = 255
            yield image
    encode.write_video(path, frames(), size, fps, 18, count)
    return path


def tone(path, seconds=4.0, rate=48000):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        samples = (int(12000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(int(seconds * rate)))
        stream.writeframes(b"".join(struct.pack("<hh", s, s) for s in samples))
    return path


@pytest.fixture
def segments(tmp_path):
    folder = tmp_path / "render"
    folder.mkdir()
    for first, count in ((0, 6), (6, 5), (11, 7)):
        segment(folder / f"seg_{first:05d}_{first + count:05d}.mp4", first, count)
    return folder


# ---- 実制作の仕上げの手順（写し） ---------------------------------------------------------------------

def ref_join(segs, wav, t0, dur, fade_in, fade_out, out_dir):
    def run(args):
        subprocess.run([media.ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error'] + args, check=True)
    lst = out_dir / 'concat.txt'
    lst.write_text(''.join(f"file '{str(s).replace(os.sep, '/')}'\n" for s in segs), encoding='utf-8')
    vid = out_dir / 'video_r.mp4'
    run(['-f', 'concat', '-safe', '0', '-i', str(lst), '-c', 'copy', str(vid)])
    aud = out_dir / 'audio_r.m4a'
    run(['-ss', f'{t0:.6f}', '-t', f'{dur:.6f}', '-i', str(wav), '-af',
         f"afade=t=in:st=0:d={fade_in},afade=t=out:st={dur - fade_out:.6f}:d={fade_out}",
         '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', str(aud)])
    master = out_dir / 'master_r.mp4'
    run(['-i', str(vid), '-i', str(aud), '-map', '0:v', '-map', '1:a', '-c', 'copy', '-t', f'{dur:.6f}', '-movflags', '+faststart', str(master)])
    return master


def test_join_equals_the_private_finish_and_records_it(segments, tmp_path):
    wav = tone(tmp_path / "song.wav")
    result = deliver.join([segments], tmp_path / "out" / "master.mp4", wav, audio_start=0.5, fade_in=0.02, fade_out=0.5)
    ref_dir = tmp_path / "ref"
    ref_dir.mkdir()
    ref = ref_join(sorted(segments.glob("seg_*.mp4")), wav, 0.5, 18 / FPS, 0.02, 0.5, ref_dir)
    assert result["sha256"] == sha256(ref) and result["frames"] == 18 and result["technical_pass"], result
    record = read_json(tmp_path / "out" / "master.deliver.json")
    assert record["schema_version"] == "deliver-join/0.1" and [s["range"] for s in record["segments"]] == [[0, 6], [6, 11], [11, 18]]
    assert record["audio"]["start_s"] == 0.5 and record["audio"]["sha256"] == sha256(wav) and record["master"]["state"] == "written"
    assert record["ffmpeg"]["version"].startswith("ffmpeg version") and len(record["commands"]) == 3
    assert "afade=t=in:st=0:d=0.02,afade=t=out:st=1.000000:d=0.5" in record["commands"][1]
    assert record["check"]["audio"]["decoded_s"] == pytest.approx(1.5, abs=0.1)
    again = deliver.join([segments], tmp_path / "out" / "master.mp4", wav, audio_start=0.5, fade_in=0.02, fade_out=0.5)
    assert again["state"] == "unchanged" and again["sha256"] == result["sha256"]
    assert not [p for p in (tmp_path / "out").iterdir() if p.name.startswith(".deliver-")]       # 作業フォルダは残さない


def test_existing_master_is_kept_without_force(segments, tmp_path):
    out = tmp_path / "master.mp4"
    out.write_bytes(b"older master")
    with pytest.raises(PipelineError, match="--force"):
        deliver.join([segments], out)
    assert out.read_bytes() == b"older master"
    result = deliver.join([segments], out, force=True)
    assert result["state"] == "written" and result["audio"] is None and result["technical_pass"]
    assert probe.inspect(out)["audio"] is None and probe.decode(out, "v")["frames"] == 18


def test_explicit_files_skip_the_range_check_and_keep_the_order(tmp_path):
    first = segment(tmp_path / "b.mp4", 0, 4)
    second = segment(tmp_path / "a.mp4", 4, 3)
    result = deliver.join([first, second], tmp_path / "m.mp4")
    record = read_json(tmp_path / "m.deliver.json")
    assert result["frames"] == 7 and [os.path.basename(s["path"]) for s in record["segments"]] == ["b.mp4", "a.mp4"]
    assert all(s["range"] is None for s in record["segments"])


@pytest.mark.parametrize("names, message", [
    ((("seg_00000_00006.mp4", 0, 6), ("seg_00007_00012.mp4", 7, 5)), "つながっていません"),
    ((("seg_00000_00006.mp4", 0, 5),), "合いません"),
    ((("seg_00006_00006.mp4", 0, 6),), "以下"),
])
def test_bad_segments_stop_with_the_cause(tmp_path, names, message):
    folder = tmp_path / "render"
    folder.mkdir()
    for name, first, count in names:
        segment(folder / name, first, count)
    with pytest.raises(PipelineError, match=message):
        deliver.join([folder], tmp_path / "m.mp4")


def test_segments_must_share_size_and_fps(tmp_path):
    segment(tmp_path / "a.mp4", 0, 4)
    segment(tmp_path / "b.mp4", 4, 4, size=(48, 36))
    with pytest.raises(PipelineError, match="そろっていません"):
        deliver.join([tmp_path / "a.mp4", tmp_path / "b.mp4"], tmp_path / "m.mp4")
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(PipelineError, match="seg_"):
        deliver.join([empty], tmp_path / "m.mp4")
    with pytest.raises(PipelineError, match=".mp4"):
        deliver.join([tmp_path / "a.mp4"], tmp_path / "m.mov")


def test_cli_joins_and_prints_a_summary(segments, tmp_path):
    wav = tone(tmp_path / "song.wav")
    code, result, err = run_cli("deliver", "join", segments, "--out", tmp_path / "cli.mp4", "--audio", wav, "--fade-out", "0.3")
    assert code == 0, err
    assert result["frames"] == 18 and result["technical_pass"] and result["record"].endswith("cli.deliver.json")
    code, _, err = run_cli("deliver", "join", segments, "--out", tmp_path / "cli.mp4", "--audio", tmp_path / "none.wav")
    assert code == 1 and "音声" in err


# ---- 共通の書き出し（encode.py） ------------------------------------------------------------------------

def test_encode_is_the_fx_writer_and_needs_no_cv2():
    from mv_pipeline.fx import render as fxrender
    assert fxrender.write_video is encode.write_video and fxrender.ffmpeg_command is encode.ffmpeg_command
    assert fxrender.FADE_OUT_S == encode.FADE_OUT_S == 0.25
    code = "import sys, mv_pipeline.encode, mv_pipeline.deliver; print(json.dumps(['cv2' in sys.modules]))"
    out = subprocess.run([sys.executable, "-c", "import json; " + code], capture_output=True, text=True, check=True).stdout
    assert json.loads(out) == [False]


def test_fx_fingerprint_covers_the_shared_writer(monkeypatch, tmp_path):
    from mv_pipeline import fx
    before = fx.fingerprint()
    original = fx.Path.read_bytes

    def changed(self):
        data = original(self)
        return data + b"#" if self.name == "encode.py" else data
    monkeypatch.setattr(fx.Path, "read_bytes", changed)
    assert fx.fingerprint() != before
