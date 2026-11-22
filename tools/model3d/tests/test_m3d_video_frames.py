"""check.video_frames（書き出した動画からコマを取り出して、もとのコマと比べる）: 比べる T の選び方、合う・ずれた・見分けられない、
大きさの違い、悪いパラメーター、工程の受け渡し。動画は本物の ffmpeg（imageio-ffmpeg）で、render.assemble と同じ書き方で作る。
値は作り物。リポジトリ外の 3 作目の VRM のダンスの演出版での確かめは docs/model3d.md。"""
import json
import subprocess

import numpy as np
import pytest
from PIL import Image

from model3d.io import ToolError, atomic_json, sha256
from model3d.ops import assemble_ops, video_ops

imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()


def _frames(folder, count=24, size=(64, 96), still=False, first=1):
    """動く帯と、T ごとに違う色のコマ f<T>.png。still なら全部同じコマ。"""
    folder.mkdir(parents=True, exist_ok=True)
    w, h = size
    for k in range(count):
        T = first + k
        img = np.zeros((h, w, 3), np.uint8)
        if not still:
            img[:, :, 0] = (k * 37) % 256
            img[:, (k * 3) % w:(k * 3) % w + 8, 1] = 255
            img[(k * 5) % h:(k * 5) % h + 6, :, 2] = 200
        Image.fromarray(img).save(folder / f"f{T:04d}.png")
    return folder


def _video(frames_dir, out, first=1, count=24):
    argv = assemble_ops.assemble_command(FFMPEG, frames_dir, first, count, 30, out, crf=12, preset="fast")
    subprocess.run(argv, check=True, capture_output=True)
    return out


def test_the_samples_include_both_ends_and_are_spread():
    assert video_ops.samples_of(range(1, 910), None, 8) == [1, 131, 260, 390, 520, 650, 779, 909]
    assert video_ops.samples_of([5, 6, 7], None, 8) == [5, 6, 7] and video_ops.samples_of([5, 6, 7], [7, 5], 8) == [5, 7]
    with pytest.raises(ToolError, match="もとのコマに無い T"):
        video_ops.samples_of([1, 2], [3], 8)


def _run(tmp_path, video, params):
    from model3d import executor
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "video",
                                           "inputs": {"video": {"path": str(video)}},
                                           "steps": [{"id": "video", "operation": "check.video_frames", "inputs": {"video": "$inputs.video"},
                                                      "parameters": params, "outputs": {"report": "video.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    report = None
    if result["status"] != "failed":
        report = json.loads(open(result["steps"]["video"]["artifacts"]["report"], encoding="utf-8").read())
    return result, report


def test_a_video_of_these_frames_matches(tmp_path):
    frames = _frames(tmp_path / "frames")
    video = _video(frames, tmp_path / "v.mp4")
    result, report = _run(tmp_path, video, {"frames_dir": str(frames), "count": 6, "offset": 3})
    assert result["status"] == "succeeded", result["error"]
    assert report["pass"] is True and report["matched"] == 6 and report["samples"] == [1, 6, 10, 15, 19, 24]
    assert report["video_sha256"] == sha256(video)                                         # どの動画と比べたか（mv gate final が見る）
    row = report["rows"][1]
    assert row["T"] == 6 and row["video_frame"] == 5 and row["best_T"] == 6 and row["status"] == "matched"
    assert row["same_mean_abs"] < 0.5 * row["nearest_other"]["mean_abs"]                # 同じコマはほかのコマより十分近い
    assert row["frames_motion_mean_abs"] > 1.0 and report["ratio"] == 0.5 and report["max_mean_abs"] is None
    assert set(row["offset_mean_abs"]) == {"-3", "3"} and report["size"] == [64, 96]
    assert report["color"] == {"matrix": "bt709", "range": "tv"}                          # render.assemble の動画の印を読む


def test_the_colour_tags_choose_the_matrix_and_the_range(tmp_path):
    from model3d import video_frames
    frames = _frames(tmp_path / "frames", count=4)
    video = _video(frames, tmp_path / "v.mp4", count=4)
    assert video_frames.video_info(FFMPEG, video) == (64, 96, "bt709", "tv")
    full = tmp_path / "full.mp4"                                                          # 印の無い、pc の範囲の動画
    subprocess.run([FFMPEG, "-y", "-v", "error", "-framerate", "30", "-i", str(frames / "f%04d.png"), "-pix_fmt", "yuvj420p",
                    "-c:v", "libx264", "-crf", "12", str(full)], check=True, capture_output=True)
    assert video_frames.video_info(FFMPEG, full)[2:] == ("bt601", "pc")


def test_a_shifted_video_or_frames_of_another_run_mismatch(tmp_path):
    frames = _frames(tmp_path / "frames", count=25)
    shifted = _video(frames, tmp_path / "shifted.mp4", first=2, count=24)               # 1 コマずれた書き出し
    result, report = _run(tmp_path, shifted, {"frames_dir": str(frames), "range": [1, 24], "at": [5, 12], "offset": 3})
    assert result["status"] == "needs_review" and report["pass"] is False
    assert report["mismatched"] == [5, 12] and report["shifts"] == [1] and "ずれ" in report["reason"]
    aligned, report = _run(tmp_path, shifted, {"frames_dir": str(frames), "first": 2, "at": [5, 12], "offset": 3})
    assert report["pass"] is True                                                         # 動画の頭が T 2 なら合う
    other = _frames(tmp_path / "other", count=24)
    for path in other.glob("*.png"):                                                       # 別の版のコマ（色を変えた）
        img = np.asarray(Image.open(path)).copy()
        img[:, :, 1] = 255 - img[:, :, 1]
        Image.fromarray(img).save(path)
    video = _video(frames, tmp_path / "v.mp4")
    result, report = _run(tmp_path, video, {"frames_dir": str(other), "count": 3, "offset": 2, "strict": True})
    assert result["status"] == "failed" and "合わない" in result["error"]                # 動きは同じでも、別の版のコマとは合わない
    result, report = _run(tmp_path, video, {"frames_dir": str(frames), "range": [1, 24], "count": 3, "offset": 2, "max_mean_abs": 0.01})
    assert report["pass"] is False                                                         # 差の上限を渡せば、それも見る


def test_still_frames_cannot_be_told_apart(tmp_path):
    frames = _frames(tmp_path / "frames", still=True)
    video = _video(frames, tmp_path / "v.mp4")
    result, report = _run(tmp_path, video, {"frames_dir": str(frames), "count": 3})
    assert report["pass"] is None and report["indistinct"] == [1, 13, 24] and result["status"] == "needs_review"


@pytest.mark.parametrize("params, message", [
    ({}, "どちらか 1 つ"),
    ({"frames_dir": "nowhere"}, "フォルダがありません"),
    ({"frames_dir": "frames", "count": 0}, "count"),
    ({"frames_dir": "frames", "offset": 0}, "offset"),
    ({"frames_dir": "frames", "range": [5, 1]}, "range"),
    ({"frames_dir": "frames", "first": 3, "at": [1]}, "first"),
    ({"frames_dir": "frames", "ratio": 0}, "ratio"),
    ({"frames_dir": "frames", "ratio": 1.5}, "ratio"),
    ({"frames_dir": "frames", "still": -1}, "still"),
    ({"frames_dir": "frames", "timeout": 0}, "timeout"),
    ({"frames_dir": "frames", "wobble": 1}, "知らないパラメーター"),
])
def test_bad_parameters_stop(tmp_path, params, message):
    frames = _frames(tmp_path / "frames", count=6)
    video = _video(frames, tmp_path / "v.mp4", count=6)
    if "frames_dir" in params and params["frames_dir"] == "frames":
        params = {**params, "frames_dir": str(frames)}
    result, _ = _run(tmp_path, video, params)
    assert result["status"] == "failed" and message in result["error"]


def test_frames_of_another_size_stop(tmp_path):
    frames = _frames(tmp_path / "frames", count=6)
    video = _video(frames, tmp_path / "v.mp4", count=6)
    small = _frames(tmp_path / "small", count=6, size=(32, 48))
    result, _ = _run(tmp_path, video, {"frames_dir": str(small)})
    assert result["status"] == "failed" and "大きさが違います" in result["error"]
