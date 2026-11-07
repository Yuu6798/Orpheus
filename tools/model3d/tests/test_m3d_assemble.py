"""render.assemble: 秒の書き方・フェードのずらし方・ffmpeg の引数・コマの並びの確かめ（ffmpeg なしで動く部分）と、
本物の ffmpeg（imageio-ffmpeg）での小さな書き出し（色の印・音・描いたコマの一覧との照合）。"""
import json
import wave
from pathlib import Path

import pytest

from model3d.io import ToolError, atomic_json, sha256
from model3d.ops import assemble_ops as asm


def test_seconds_are_written_short_and_exact_enough():
    assert asm.seconds(31.4) == "31.4" and asm.seconds(0.6) == "0.6" and asm.seconds(30.0) == "30" and asm.seconds(0) == "0"
    assert asm.seconds(10 / 3) == "3.333333" and asm.seconds(-0.0) == "0"


def test_fades_are_written_in_window_seconds_and_shifted_to_the_first_frame():
    out = {"start": 31.4, "duration": 0.6}
    assert asm.fade_filters(None, out, 1, 30) == ["fade=t=out:st=31.4:d=0.6"]
    # 後ろの範囲は、時刻を 842 コマ進めて窓の秒のまま掛け、戻して、コマの長さを付け直す（fade の st は負にできない）
    assert asm.fade_filters({"start": 0.0, "duration": 0.5}, out, 843, 30) == [
        "setpts=PTS+842", "fade=t=in:st=0:d=0.5", "fade=t=out:st=31.4:d=0.6", "setpts=PTS-842", "fps=30"]
    assert asm.fade_filters(None, None, 1, 30) == [] and asm.fade_filters(None, None, 843, 30) == []


def test_the_ffmpeg_arguments_follow_the_bt709_recipe():
    argv = asm.assemble_command("ffmpeg", "frames", 1, 960, 30, "out.mp4", audio="song.wav", fade_out={"start": 31.4, "duration": 0.6})
    assert argv == ["ffmpeg", "-y", "-v", "error", "-framerate", "30", "-start_number", "1", "-i", str(Path("frames") / "f%04d.png"),
                    "-i", "song.wav", "-vf", "fade=t=out:st=31.4:d=0.6," + asm.YUV, *asm.TAGS, "-c:v", "libx264", "-crf", "18",
                    "-preset", "medium", "-frames:v", "960", "-c:a", "aac", "-b:a", "192k", "-shortest", "out.mp4"]
    later = asm.assemble_command("ffmpeg", "frames", 843, 118, 30, "d.mp4", audio="song.wav", audio_offset=28.066666666666666)
    assert later[later.index("-ss") + 1] == "28.066667" and later.index("-ss") < later.index("song.wav")
    silent = asm.assemble_command("ffmpeg", "frames", 1, 3, 10, "s.mp4", crf=20, preset="fast")
    assert "-c:a" not in silent and "-shortest" not in silent and silent[silent.index("-crf") + 1] == "20"


def test_the_frames_must_follow_one_another_and_exist(tmp_path):
    assert asm.consecutive([3, 1, 2], "x") == (1, 3)
    with pytest.raises(ToolError, match="続いていません"):
        asm.consecutive([1, 2, 4], "x")
    with pytest.raises(ToolError, match="続いていません"):
        asm.consecutive([1, 2, 2], "x")
    (tmp_path / "f0001.png").write_bytes(b"png")
    with pytest.raises(ToolError, match="まとめるコマがありません"):
        asm.frame_list(1, 3, tmp_path)


def _frames(directory, count=3, first=1, size=(32, 48)):
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for k in range(count):
        Image.new("RGB", size, (40 * k, 120, 200)).save(directory / f"f{first + k:04d}.png")
    return directory


def _wav(path, seconds_=1.0, rate=8000):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds_))
    return path


def _recipe(tmp_path, parameters, inputs=None, step_inputs=None, outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "assemble", "inputs": inputs or {},
                                           "steps": [{"id": "assemble", "operation": "render.assemble", "inputs": step_inputs or {},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"video": "out.mp4", "report": "assemble.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_a_bt709_video_with_the_audio(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    from model3d import executor
    _frames(tmp_path / "frames")
    _wav(tmp_path / "song.wav")
    result = executor.run_recipe(_recipe(tmp_path, {"frames_dir": "frames", "range": [1, 3], "fps": 10,
                                                    "fade_out": {"start": 0.1, "duration": 0.2}},
                                         {"song": {"path": "song.wav"}}, {"audio": "$inputs.song"}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["assemble"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    video = next(s for s in report["probe"]["streams"] if "Video:" in s)
    assert "h264" in video and "yuv420p(tv, bt709" in video and "32x48" in video
    assert any("Audio: aac" in s for s in report["probe"]["streams"])
    assert report["frames"]["count"] == 3 and report["seconds"] == pytest.approx(0.3) and report["audio"]["offset"] == 0.0
    assert report["video"]["sha256"] == sha256(step["artifacts"]["video"])
    observation = step["observations"][0]
    assert observation["kind"] == "assemble" and observation["frames"] == 3 and observation["audio"] is True


def test_the_operation_checks_the_rendered_frames_list(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    from model3d import executor
    frames = _frames(tmp_path / "frames", first=5)
    listing = {"schema_version": "model3d-frames/0.1", "directory": str(frames.resolve()), "fps": 10, "resolution": [32, 48],
               "frames": [{"T": T, "f": T, "file": f"f{T:04d}.png", "sha256": sha256(frames / f"f{T:04d}.png"),
                           "bytes": (frames / f"f{T:04d}.png").stat().st_size} for T in (5, 6, 7)], "missing": []}
    atomic_json(tmp_path / "frames.json", listing)
    run = lambda: executor.run_recipe(_recipe(tmp_path, {}, {"frames": {"path": "frames.json"}}, {"frames": "$inputs.frames"},
                                              {"video": "out.mp4"}), tmp_path / "runs")
    ok = run()
    assert ok["status"] == "succeeded" and set(ok["steps"]["assemble"]["artifacts"]) == {"video"}
    listing["frames"][1]["sha256"] = "0" * 64                           # 描いた後にコマが変わった
    atomic_json(tmp_path / "frames.json", listing)
    with pytest.raises(ToolError, match="違います"):
        call(tmp_path, {}, {"frames": tmp_path / "frames.json"})
    listing["missing"] = [8]
    atomic_json(tmp_path / "frames.json", listing)
    with pytest.raises(ToolError, match="描いていないコマ"):
        call(tmp_path, {}, {"frames": tmp_path / "frames.json"})


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, inputs=None, outputs=None):
    from model3d.ops import get
    declared = {k: tmp_path / v for k, v in (outputs or {"video": "o.mp4"}).items()}
    return get("render.assemble").function(inputs or {}, parameters, declared, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"frames_dir": "frames"}, "range"),
    ({"frames_dir": "frames", "range": [3, 1], "fps": 10}, "range"),
    ({"frames_dir": "frames", "range": [1, 3]}, "fps"),
    ({"frames_dir": "frames", "range": [1, 3], "fps": 10, "crf": 60}, "crf"),
    ({"frames_dir": "frames", "range": [1, 3], "fps": 10, "preset": "turbo"}, "preset"),
    ({"frames_dir": "frames", "range": [1, 3], "fps": 10, "audio_bitrate": "loud"}, "audio_bitrate"),
    ({"frames_dir": "frames", "range": [1, 3], "fps": 10, "fade_out": {"start": 1}}, "fade_out"),
    ({"frames_dir": "frames", "range": [1, 3], "fps": 10, "ffmpeg": "no/such/ffmpeg.exe"}, "ffmpeg"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    _frames(tmp_path / "frames")
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_the_operation_needs_a_video_output_and_one_frame_source(tmp_path):
    _frames(tmp_path / "frames")
    with pytest.raises(ToolError, match="video"):
        call(tmp_path, {"frames_dir": "frames", "range": [1, 3], "fps": 10}, outputs={"report": "r.json"})
    atomic_json(tmp_path / "frames.json", {"schema_version": "model3d-frames/0.1", "directory": str(tmp_path / "frames"), "fps": 10,
                                           "resolution": [32, 48], "frames": [], "missing": []})
    with pytest.raises(ToolError, match="一緒に使えません"):
        call(tmp_path, {"range": [1, 3]}, {"frames": tmp_path / "frames.json"})


def _decoded(video, size=(32, 48)):
    """動画のコマを RGB に戻した明るさの平均（コマごと）。"""
    import imageio_ffmpeg
    import numpy as np
    import subprocess
    raw = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(video), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, size[1], size[0], 3)
    return frames.reshape(len(frames), -1).mean(axis=1)


@pytest.mark.parametrize("fade_in, first_level", [({"start": 0.0, "duration": 0.5}, 0.6),    # 範囲の頭（0.3 秒）はフェードの途中
                                                  ({"start": 0.0, "duration": 0.2}, 1.0),    # 範囲より前に終わったフェード
                                                  ({"start": 0.5, "duration": 0.2}, 0.0)])   # 範囲の後で始まるフェード
def test_a_later_range_keeps_the_fade_of_the_window(tmp_path, fade_in, first_level):
    pytest.importorskip("imageio_ffmpeg")
    from PIL import Image
    from model3d import executor
    (tmp_path / "frames").mkdir()
    for T in range(1, 11):
        Image.new("RGB", (32, 48), (255, 255, 255)).save(tmp_path / "frames" / f"f{T:04d}.png")
    result = executor.run_recipe(_recipe(tmp_path, {"frames_dir": "frames", "range": [4, 10], "fps": 10, "fade_in": fade_in}),
                                 tmp_path / "runs")
    assert result["status"] == "succeeded"                         # 前は fade の st が負（-0.3）になり、ffmpeg が止まった
    levels = _decoded(result["steps"]["assemble"]["artifacts"]["video"])
    assert len(levels) == 7 and levels[-1] > 240                   # T 10 は窓の 0.9 秒で、フェードは終わっている
    assert levels[0] / levels[-1] == pytest.approx(first_level, abs=0.05)
