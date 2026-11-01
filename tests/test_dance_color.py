"""動画の色の読み方。タグが bt709 の動画は書いた色のまま読み、タグの無い動画は BT.601 として読んで warnings に残す。

素材はその場で書く単色の動画（合成素材）で、実素材の色の評価ではない。
"""
import subprocess

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline import media  # noqa: E402
from mv_pipeline.dance import clips, compose, demo  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402

WIDTH, HEIGHT = 64, 48
GREEN = (30, 220, 60)


def solid(color, frames=3):
    return [np.full((HEIGHT, WIDTH, 3), color, np.uint8) for _ in range(frames)]


def encode(path, images, filters, tags=(), rate="30", size=(WIDTH, HEIGHT)):
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{size[0]}x{size[1]}", "-r", rate,
                    "-i", "pipe:0", "-vf", filters, "-c:v", "libx264", "-qp", "4", *tags, str(path)],
                   input=b"".join(image.tobytes() for image in images), check=True)


def write_bt601(path, images, tagged, size=(WIDTH, HEIGHT)):
    name = "smpte170m" if tagged else "unknown"      # scale がフレームに付ける行列のタグを setparams で上書きする
    filters = f"scale=out_color_matrix=bt601:out_range=tv,format=yuv420p,setparams=colorspace={name}:color_primaries={name}:color_trc={name}:range=tv"
    tags = ("-colorspace", name, "-color_primaries", name, "-color_trc", name) if tagged else ()
    encode(path, images, filters, tags, size=size)


def middle(image):
    return image[HEIGHT // 2, WIDTH // 2].astype(int)


@pytest.mark.parametrize("color", [GREEN, (235, 190, 160), (150, 80, 255), (0, 255, 0), (255, 0, 0)])
def test_a_bt709_clip_reads_back_the_colors_it_was_written_with(tmp_path, color):
    path = tmp_path / "clip.mp4"
    fxrender.write_video(path, solid(color), (WIDTH, HEIGHT), 30, 12, 3)
    source = clips.Source(path)
    assert source.matrix == "bt709" and np.abs(middle(source.read(0)) - color).max() <= 2
    source.close()


def test_a_full_range_bt709_clip_reads_right(tmp_path):
    path = tmp_path / "clip.mp4"
    encode(path, solid(GREEN), "scale=out_color_matrix=bt709:out_range=pc,format=yuv420p,"
           "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=pc",
           ("-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "pc"))
    source = clips.Source(path)
    assert source.matrix == "bt709" and np.abs(middle(source.read(0)) - GREEN).max() <= 2
    source.close()


def test_opencv_alone_reads_a_bt709_clip_with_the_wrong_matrix(tmp_path):
    """Source が bt709 を ffmpeg で読む理由。OpenCV がタグを見るようになったら、この分岐は要らなくなる。"""
    path = tmp_path / "clip.mp4"
    fxrender.write_video(path, solid(GREEN), (WIDTH, HEIGHT), 30, 12, 3)
    capture = cv2.VideoCapture(str(path))
    _, bgr = capture.read()
    capture.release()
    assert np.abs(middle(bgr[..., ::-1]) - GREEN).max() > 15


def test_a_tagged_bt601_clip_stays_with_opencv_and_reads_right(tmp_path):
    path = tmp_path / "clip.mp4"
    write_bt601(path, solid(GREEN), tagged=True)
    source = clips.Source(path)
    assert source.matrix == "smpte170m" and isinstance(source.cap, cv2.VideoCapture)
    assert np.abs(middle(source.read(0)) - GREEN).max() <= 2
    source.close()


def test_an_untagged_clip_stays_with_opencv_and_reads_as_bt601(tmp_path):
    path = tmp_path / "clip.mp4"
    write_bt601(path, solid(GREEN), tagged=False)
    source = clips.Source(path)
    assert source.matrix is None and isinstance(source.cap, cv2.VideoCapture)
    assert np.abs(middle(source.read(0)) - GREEN).max() <= 2
    source.close()


def test_a_tag_that_says_unknown_counts_as_untagged(tmp_path, monkeypatch):
    monkeypatch.setattr(clips.probe, "inspect", lambda path: {"video": {"matrix": "unknown"}})
    assert clips._matrix(tmp_path / "clip.mp4") is None


@pytest.mark.parametrize("schema, name, size", [("0.1", "clip_a.mp4", None), ("0.2", "stage.mp4", (180, 320))])
def test_a_video_without_a_usable_tag_is_warned_about_by_name(tmp_path, schema, name, size):
    demo.create(tmp_path, width=180, height=320, duration=2, schema=schema)
    tagged_warnings = _color_warnings(tmp_path)
    size = size or (WIDTH, HEIGHT)
    write_bt601(tmp_path / name, [np.full((size[1], size[0], 3), GREEN, np.uint8)] * 3, tagged=False, size=size)
    found = _color_warnings(tmp_path)
    assert tagged_warnings == [] and len(found) == 1 and found[0].startswith(name)


def _color_warnings(root):
    ctx = compose.prepare(dancespec.load_spec(root / "spec.json"))
    try:
        return [warning for warning in ctx.warnings if "色の行列" in warning]
    finally:
        ctx.close()


def test_a_variable_frame_rate_bt709_clip_gets_no_extra_frames(tmp_path):
    """rawvideo の既定（固定レート）のままだと、時刻の隙間に同じコマが足される。"""
    path = tmp_path / "clip.mp4"
    images = [np.full((HEIGHT, WIDTH, 3), (i * 25, i * 25, i * 25), np.uint8) for i in range(8)]
    encode(path, images, "setpts='N*N/TB/30',scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
           "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=tv",
           ("-fps_mode", "vfr", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709"))
    source = clips.Source(path)
    assert source.matrix == "bt709"
    count = source.count()
    values = [int(source.read(i)[0, 0, 0]) for i in range(count)]
    assert values == sorted(set(values)) and not source.ended_at(count - 1)
    source.read(count)
    assert source.ended_at(count)
    source.close()


def test_a_rotated_bt709_clip_comes_out_like_opencv(tmp_path):
    base, rotated = tmp_path / "base.mp4", tmp_path / "rotated.mp4"
    image = np.zeros((HEIGHT, WIDTH, 3), np.uint8)
    image[:16, :24] = 255
    fxrender.write_video(base, [image] * 3, (WIDTH, HEIGHT), 30, 12, 3)
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-display_rotation", "90", "-i", str(base), "-c", "copy", str(rotated)], check=True)
    source = clips.Source(rotated)
    capture = cv2.VideoCapture(str(rotated))
    _, bgr = capture.read()
    capture.release()
    assert (source.width, source.height) == (HEIGHT, WIDTH)
    assert source.read(0).shape == bgr.shape and np.abs(source.read(0).astype(int) - bgr[..., ::-1].astype(int)).mean() < 2
    source.close()


def test_closing_stops_the_decoder_and_seeking_back_restarts_it(tmp_path):
    path = tmp_path / "clip.mp4"
    images = [np.full((HEIGHT, WIDTH, 3), (i * 40, 0, 0), np.uint8) for i in range(4)]
    fxrender.write_video(path, images, (WIDTH, HEIGHT), 30, 12, 4)
    source = clips.Source(path)
    first = source.read(3).copy()
    process = source.cap.process
    assert (source.read(1)[0, 0, 0] < first[0, 0, 0]) and process.poll() is not None and source.cap.process is not process
    current = source.cap.process
    source.close()
    assert current.poll() is not None
