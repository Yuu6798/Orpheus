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
from fractions import Fraction

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


def ntsc_segment(path, count):
    """24000/1001 fps（表示は 23.98 fps）の区間。"""
    subprocess.run([media.ffmpeg(), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x36:rate=24000/1001",
                    "-frames:v", str(count), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_fractional_fps_segments_use_the_exact_duration(tmp_path):
    # 表示の fps（23.98）で割ると 48 コマが 2.0017 秒になり（正しくは 2.002 秒）、音の切り出しと元データの長さが短くなっていた
    folder = tmp_path / "ntsc"
    folder.mkdir()
    for first in (0, 24):
        ntsc_segment(folder / f"seg_{first:05d}_{first + 24:05d}.mp4", 24)
    result = deliver.join([folder], tmp_path / "master.mp4", audio=tone(tmp_path / "song.wav"))
    assert result["duration_s"] == 2.002 and result["frames"] == 48 and result["technical_pass"]
    commands = [" ".join(command) for command in read_json(result["record"])["commands"]]
    assert any("-t 2.002000" in command and "afade=t=out:st=1.752000" in command for command in commands)
    assert deliver.video_seconds(folder / "seg_00000_00024.mp4") == Fraction(1001, 1000)


def test_a_video_only_join_does_not_check_the_audio_fades(tmp_path):
    # フェードは音にだけ掛ける。1 コマ（12 fps で 0.083 秒）の区間も、音を付けなければつなげる
    one = segment(tmp_path / "one.mp4", 0, 1)
    result = deliver.join([one], tmp_path / "master.mp4")
    assert result["frames"] == 1 and result["technical_pass"]
    with pytest.raises(PipelineError, match="フェード"):
        deliver.join([one], tmp_path / "with_audio.mp4", audio=tone(tmp_path / "song.wav"))


def test_out_never_replaces_an_input_even_with_force(segments, tmp_path):
    # 区間のフォルダの区間や --audio の名前を --out にすると、置き換えで入力が消える
    first = sorted(segments.glob("seg_*.mp4"))[0]
    before = first.read_bytes()
    with pytest.raises(PipelineError, match="入力"):
        deliver.join([segments], first, force=True)
    assert first.read_bytes() == before
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(before)
    with pytest.raises(PipelineError, match="入力"):
        deliver.join([segments], clip, audio=clip, force=True)
    assert clip.read_bytes() == before and not list(tmp_path.glob(".deliver-*")) and not list(segments.glob(".deliver-*"))


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


# ---- 確認用の小さい版（review-copy） -------------------------------------------------------------------

def ref_two_pass(master, out, vb, work, threads=None):
    """実制作の仕上げの 2 パス（引数をそのまま写したもの。threads を渡すと -c:v libx264 の後に -threads を足す）。"""
    def run(args):
        subprocess.run([media.ffmpeg(), '-y', '-hide_banner', '-loglevel', 'error'] + args, check=True)
    plog = str(work / '2pass_ref')
    extra = ['-threads', str(threads)] if threads is not None else []
    run(['-i', str(master), '-c:v', 'libx264', *extra, '-preset', 'slow', '-b:v', str(vb), '-maxrate', '11M', '-bufsize', '22M',
         '-pix_fmt', 'yuv420p', '-pass', '1', '-passlogfile', plog, '-an', '-f', 'null', os.devnull])
    run(['-i', str(master), '-c:v', 'libx264', *extra, '-preset', 'slow', '-b:v', str(vb), '-maxrate', '11M', '-bufsize', '22M',
         '-pix_fmt', 'yuv420p', '-pass', '2', '-passlogfile', plog, '-c:a', 'copy', '-movflags', '+faststart', str(out)])
    return out


@pytest.fixture
def master(segments, tmp_path):
    out = tmp_path / "master.mp4"
    deliver.join([segments], out, tone(tmp_path / "song.wav"))
    return out


def test_review_copy_under_the_limit_is_the_master_itself(master, tmp_path):
    result = deliver.review_copy(master, tmp_path / "review.mp4")
    assert (tmp_path / "review.mp4").read_bytes() == master.read_bytes()
    assert "そのまま" in result["method"] and result["ssim_vs_master"] == 1.0 and result["repeatable"] and result["technical_pass"]
    record = read_json(tmp_path / "review.review.json")
    assert record["schema_version"] == "deliver-review/0.1" and record["commands"] == [] and record["settings"]["video_bitrate"] is None


def test_two_pass_follows_the_private_commands_and_one_thread_repeats(master, tmp_path):
    first = deliver.review_copy(master, tmp_path / "a.mp4", limit=1, target=60_000)
    again = deliver.review_copy(master, tmp_path / "b.mp4", limit=1, target=60_000)
    assert first["sha256"] == again["sha256"] and first["repeatable"]                    # 1 スレッドは毎回同じバイト列
    vb = int((60_000 * 8 / (18 / FPS) - 192e3) * 0.985)
    assert first["method"] == f"2 パス {vb / 1e6:.2f} Mbps（maxrate 11M）" and 0.5 < first["ssim_vs_master"] <= 1.0
    work = tmp_path / "ref"
    work.mkdir()
    ref = ref_two_pass(master, work / "ref.mp4", vb, work, threads=1)
    assert sha256(ref) == first["sha256"]
    record = read_json(tmp_path / "a.review.json")
    assert record["settings"]["video_bitrate"] == vb and record["commands"][0][-3:] == ["-f", "null", os.devnull]
    assert probe.decode(tmp_path / "a.mp4", "v")["frames"] == 18 and probe.inspect(tmp_path / "a.mp4")["audio"] is not None
    auto = deliver.review_copy(master, tmp_path / "c.mp4", limit=1, target=60_000, threads=0)
    assert not auto["repeatable"] and "-threads" not in read_json(tmp_path / "c.review.json")["commands"][0]


@pytest.mark.parametrize("kwargs, message", [
    ({"out": "x.mov"}, ".mp4"),
    ({"threads": -1}, "--threads"),
    ({"target": 0}, "0 より大きく"),
    ({"limit": 1, "target": 1000}, "ビットレート"),
])
def test_review_copy_stops_with_the_cause(master, tmp_path, kwargs, message):
    out = tmp_path / kwargs.pop("out", "r.mp4")
    with pytest.raises(PipelineError, match=message):
        deliver.review_copy(master, out, **kwargs)
    with pytest.raises(PipelineError, match="同じファイル"):
        deliver.review_copy(master, master)


# ---- 受け渡しの片（split） --------------------------------------------------------------------------

REF_BAT = '''@echo off
rem {title} の元データ（{nbytes:,} バイト）を片から結合して SHA-256 を照合します（このフォルダで実行）
cd /d "%~dp0"
for %%F in ({parts}) do if not exist "%%F" (echo 見つからない片があります: %%F & pause & exit /b 1)
copy /y /b {plus} {name} >nul
if errorlevel 1 (echo 片を結合できませんでした & pause & exit /b 1)
certutil -hashfile {name} SHA256
echo 期待する SHA-256: {sha}
certutil -hashfile {name} SHA256 | findstr /i /c:"{sha}" >nul
if errorlevel 1 (echo 【NG】一致しません。片がそろっているか確かめてください) else (echo 【OK】一致しました。{name} が元の動画です)
pause
'''


def ref_split(master, odir, name, title, part):
    """実制作の受け渡しの道具の片・bat・SHA256SUMS（書き方をそのまま写したもの。bat の実行は除く）。"""
    os.makedirs(odir, exist_ok=True)
    parts = []
    with open(master, 'rb') as f:
        for i, b in enumerate(iter(lambda: f.read(part), b'')):
            pn = f'{name}.part{i:02d}'
            open(os.path.join(odir, pn), 'wb').write(b)
            parts.append(pn)
    nbytes, msha = os.path.getsize(master), sha256(master)
    with open(os.path.join(odir, 'join_master.bat'), 'w', encoding='cp932', newline='\r\n') as f:
        f.write(REF_BAT.format(title=title, nbytes=nbytes, parts=' '.join(parts), plus=' + '.join(parts), name=name, sha=msha))
    sums_p = os.path.join(odir, name.rsplit('_master', 1)[0] + '_parts_SHA256SUMS.txt')
    psha = {p: sha256(os.path.join(odir, p)) for p in parts}
    open(sums_p, 'w', encoding='utf-8', newline='\n').write(''.join(f'{h}  {p}\n' for p, h in psha.items()) + f'{msha}  {name}\n')
    return parts


def test_split_writes_the_same_parts_bat_and_sums_as_the_private_tool(master, tmp_path):
    result = deliver.split(master, tmp_path / "parts", 9000, "SONG_r1_master.mp4", "テスト Shorts r1")
    ref = ref_split(master, tmp_path / "ref", "SONG_r1_master.mp4", "テスト Shorts r1", 9000)
    assert [os.path.basename(p) for p in result["parts"]] == ref and len(ref) >= 2
    for name in ref + ["join_master.bat", "SONG_r1_parts_SHA256SUMS.txt"]:
        assert (tmp_path / "parts" / name).read_bytes() == (tmp_path / "ref" / name).read_bytes(), name
    assert b"".join((tmp_path / "parts" / p).read_bytes() for p in ref) == master.read_bytes() and result["join_check"]
    bat = (tmp_path / "parts" / "join_master.bat").read_bytes()
    assert b"\r\n" in bat and b"\n" not in bat.replace(b"\r\n", b"") and "テスト Shorts r1".encode("cp932") in bat
    record = read_json(tmp_path / "parts.split.json")
    assert record["schema_version"] == "deliver-split/0.1" and record["master"]["top_boxes"][:2] == ["ftyp", "moov"]
    with pytest.raises(PipelineError, match="--force"):
        deliver.split(master, tmp_path / "parts", 9000, "SONG_r1_master.mp4")
    assert deliver.split(master, tmp_path / "parts", 9000, "SONG_r1_master.mp4", force=True)["join_check"]
    assert deliver.split(master, tmp_path / "plain")["sums"].endswith("master_parts_SHA256SUMS.txt")     # _master の無い名前


@pytest.mark.parametrize("kwargs, message", [
    ({"name": "has space.mp4"}, "ASCII"),
    ({"name": "曲.mp4"}, "ASCII"),
    ({"title": "A & B"}, "使えない字"),
    ({"title": "☀️"}, "CP932"),
    ({"part_bytes": 0}, "1 以上"),
])
def test_split_refuses_names_that_break_the_bat(master, tmp_path, kwargs, message):
    with pytest.raises(PipelineError, match=message):
        deliver.split(master, tmp_path / "parts", **kwargs)


def test_split_needs_a_faststart_master(master, tmp_path):
    slow = tmp_path / "slow.mp4"
    subprocess.run([media.ffmpeg(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(master), "-c", "copy", str(slow)], check=True)
    assert deliver.top_boxes(slow).index("mdat") < deliver.top_boxes(slow).index("moov")
    with pytest.raises(PipelineError, match="faststart"):
        deliver.split(slow, tmp_path / "parts")


def test_cli_review_copy_and_split(master, tmp_path):
    code, result, err = run_cli("deliver", "review-copy", master, "--out", tmp_path / "r.mp4")
    assert code == 0 and result["technical_pass"], err
    code, result, err = run_cli("deliver", "split", master, "--out", tmp_path / "parts", "--part-bytes", "8000", "--title", "試し")
    assert code == 0 and result["join_check"] and len(result["parts"]) >= 2, err
