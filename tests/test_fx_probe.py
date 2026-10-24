import subprocess

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline import media  # noqa: E402
from mv_pipeline.fx import probe, render  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

SIZE = (160, 90)


@pytest.fixture(scope="module")
def good(tmp_path_factory):
    """12fps・18 枚・bt709 タグ付き・1.5 秒の AAC 音声つきの MP4。"""
    import wave
    root = tmp_path_factory.mktemp("probe")
    rate = 48000
    t = np.arange(int(2.0 * rate)) / rate
    samples = (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype("<i2")
    with wave.open(str(root / "a.wav"), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.stack([samples, samples], axis=1).tobytes())
    image = np.zeros((90, 160, 3), dtype=np.uint8)
    image[:, 80:] = (200, 100, 50)
    render.write_video(root / "good.mp4", (image for _ in range(18)), SIZE, 12, 18, 18,
                       audio={"path": root / "a.wav", "start": 0.0, "duration": 1.5})
    render.write_video(root / "silent.mp4", (image for _ in range(18)), SIZE, 12, 18, 18)
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=160x90:rate=12:duration=1.5",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(root / "untagged.mp4")], check=True)
    return root


def test_a_good_file_passes_every_check(good):
    result = probe.check_video(good / "good.mp4", width=160, height=90, fps=12, frames=18, audio=True, audio_s=1.5, bt709=True)
    assert result["pass"] is True and result["failures"] == [] and result["frames"] == 18 and result["decode_ok"]
    assert result["audio"]["codec"] == "aac" and result["audio"]["decoded_s"] == pytest.approx(1.5, abs=0.1)
    assert result["video"]["matrix"] == "bt709" and result["video"]["range"] == "tv"


@pytest.mark.parametrize("expect, text", [
    ({"width": 320}, "寸法"), ({"height": 100}, "寸法"), ({"fps": 24}, "fps"), ({"frames": 17}, "フレーム数"),
    ({"frames": (19, 20)}, "フレーム数"), ({"audio": False}, "想定外の音声"), ({"audio_s": 1.0}, "音声の尺"),
])
def test_each_mismatch_is_reported_as_a_failure_not_an_exception(good, expect, text):
    result = probe.check_video(good / "good.mp4", **expect)
    assert result["pass"] is False and any(text in f for f in result["failures"]), result["failures"]


def test_frames_may_be_given_as_a_range_and_audio_may_be_required(good):
    assert probe.check_video(good / "good.mp4", frames=(16, 18))["pass"] is True
    assert probe.check_video(good / "good.mp4", frames=(18, 20))["pass"] is True
    assert probe.check_video(good / "good.mp4", frames=(10, 17))["pass"] is False
    silent = probe.check_video(good / "silent.mp4", audio=True)
    assert silent["pass"] is False and "音声ストリームがありません" in silent["failures"][0]
    assert probe.check_video(good / "silent.mp4", audio=False)["pass"] is True and probe.check_video(good / "silent.mp4")["audio"] is None


def test_missing_color_tags_fail_the_bt709_contract_only_when_required(good):
    assert probe.check_video(good / "untagged.mp4")["pass"] is True
    result = probe.check_video(good / "untagged.mp4", bt709=True)
    assert result["pass"] is False and "bt709" in result["failures"][0]


def test_truncated_and_garbage_files_fail_instead_of_raising(good, tmp_path):
    data = (good / "good.mp4").read_bytes()
    (tmp_path / "half.mp4").write_bytes(data[:len(data) // 2])
    half = probe.check_video(tmp_path / "half.mp4", frames=18, audio=True)
    assert half["pass"] is False and half["decode_ok"] is False and half["failures"]
    bare = probe.check_video(tmp_path / "half.mp4")                                          # 期待値を何も渡さなくても、復号エラーだけで不合格
    assert bare["pass"] is False and bare["decode_ok"] is False and any(f.startswith("復号エラー") for f in bare["failures"])
    (tmp_path / "junk.mp4").write_bytes(b"this is not a video" * 50)
    junk = probe.check_video(tmp_path / "junk.mp4")
    assert junk["pass"] is False and junk["video"] is None and "映像ストリーム" in junk["failures"][0]
    gone = probe.check_video(tmp_path / "gone.mp4")
    assert gone["pass"] is False and "見つかりません" in gone["failures"][0]
    with pytest.raises(PipelineError, match="見つかりません"):
        probe.inspect(tmp_path / "gone.mp4")


def test_inspect_reads_streams_without_ffprobe(good):
    info = probe.inspect(good / "good.mp4")
    assert info["video"]["codec"] == "h264" and (info["video"]["width"], info["video"]["height"]) == SIZE
    assert info["video"]["fps"] == 12.0 and info["audio"] == {"codec": "aac", "sample_rate": 48000, "channels": 2}
    assert probe.inspect(good / "silent.mp4")["audio"] is None


def test_color_tag_parsing_handles_short_and_long_forms():
    assert probe._color("tv, bt709, progressive") == {"range": "tv", "matrix": "bt709", "primaries": "bt709", "transfer": "bt709"}
    assert probe._color("pc, bt709/bt470bg/smpte170m") == {"range": "pc", "matrix": "bt709", "primaries": "bt470bg", "transfer": "smpte170m"}
    assert probe._color(None) == {"range": None, "matrix": None, "primaries": None, "transfer": None}
    assert probe._color("tv, progressive")["matrix"] is None
    assert [probe._channels(x) for x in ("mono", "stereo", "5.1", "6 channels", "weird")] == [1, 2, 6, 6, None]
