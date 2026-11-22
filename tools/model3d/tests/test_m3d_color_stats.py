"""check.colors（色の数を、絵の組で比べる）: 明るさ・百分位・色の比・彩度（OpenCV の 8 ビットの HSV の S と同じ計算）・決めた所・
切り抜き、動画の時刻 → コマの番号、組の平均と差、悪いパラメーター、工程の受け渡し。絵は作り物（どの作品とも関係ない）。
照合の元（リポジトリ外の 4 作目の 3D のダンスの台本）との照合は docs/model3d.md。"""
import json
from fractions import Fraction
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import color_stats
from model3d.io import ToolError, atomic_json


def _save(path, rgb):
    Image.fromarray(np.asarray(rgb, np.uint8)).save(path)
    return path


def _two_tone(width=40, height=20, left=(200, 40, 40), right=(40, 40, 200)):
    rgb = np.zeros((height, width, 3), np.uint8)
    rgb[:, : width // 2] = left
    rgb[:, width // 2:] = right
    return rgb


def test_the_numbers_follow_the_display_values():
    rgb = _two_tone()
    s = color_stats.stats(rgb)
    lum_left = (0.2126 * 200 + 0.7152 * 40 + 0.0722 * 40) / 255
    lum_right = (0.2126 * 40 + 0.7152 * 40 + 0.0722 * 200) / 255
    assert s["mean"] == pytest.approx((lum_left + lum_right) / 2, abs=1e-6)
    assert s["p05"] == pytest.approx(min(lum_left, lum_right), abs=1e-6) and s["p95"] == pytest.approx(max(lum_left, lum_right), abs=1e-6)
    assert s["rgb"] == pytest.approx([120 / 255, 40 / 255, 120 / 255], abs=1e-6)
    assert s["g_r"] == pytest.approx(40 / 120, abs=1e-6) and s["b_r"] == pytest.approx(1.0, abs=1e-6)
    assert s["sat"] == pytest.approx(204 / 255, abs=1e-9)                         # (200 − 40) / 200 × 255 = 204


def test_saturation_is_the_opencv_hsv_s():
    cv2 = pytest.importorskip("cv2", reason="OpenCV が無い（fx extra）")
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (64, 64, 3)).astype(np.uint8)
    rgb[0, 0] = (0, 0, 0)                                                          # 最大 0 は 0
    rgb[0, 1] = (255, 255, 255)
    rgb[0, 2] = (1, 0, 0)
    every = np.stack(np.meshgrid(np.arange(256), np.arange(256), indexing="ij"), axis=-1).reshape(-1, 2)
    pairs = np.zeros((256, 256, 3), np.uint8)                                      # 最大と最小の組を全部（V と差の全部の組）
    pairs[..., 0] = every[:, 0].reshape(256, 256)
    pairs[..., 1] = np.minimum(every[:, 0], every[:, 1]).reshape(256, 256)
    for picture in (rgb, pairs):
        expected = cv2.cvtColor(np.ascontiguousarray(picture), cv2.COLOR_RGB2HSV)[..., 1]
        assert np.array_equal(color_stats.saturation(picture), expected.astype(np.int64))


def test_saturation_of_black_and_white_is_zero():
    assert color_stats.saturation(np.array([[[0, 0, 0], [255, 255, 255], [7, 7, 7]]], np.uint8)).tolist() == [[0, 0, 0]]


def test_a_black_picture_does_not_divide_by_zero():
    s = color_stats.stats(np.zeros((4, 4, 3), np.uint8))
    assert s["mean"] == 0 and s["g_r"] == 0 and s["b_r"] == 0 and s["sat"] == 0


def test_a_spot_is_the_square_around_the_centre_and_counts_clipping():
    rgb = np.zeros((20, 20, 3), np.uint8)
    rgb[5:8, 5:8] = (255, 100, 0)                                                  # 3×3 の白飛び（赤が 255）
    spot = color_stats.spot(rgb, 6, 6, 1)
    assert spot == {"rgb": [255.0, 100.0, 0.0], "max": 255.0, "clipped": 1.0, "pixels": 9}
    wide = color_stats.spot(rgb, 6, 6, 2)
    assert wide["pixels"] == 25 and wide["clipped"] == pytest.approx(9 / 25) and wide["rgb"][0] == pytest.approx(255 * 9 / 25)
    edge = color_stats.spot(rgb, 0, 0, 3)                                          # 画面の外は切る
    assert edge["pixels"] == 16
    with pytest.raises(ToolError, match="外"):
        color_stats.spot(rgb, 40, 40, 2)


def test_crop_measures_only_the_box_but_spots_use_the_whole_picture():
    rgb = _two_tone()
    row = color_stats.measure(rgb, {"name": "a", "crop": [0, 0, 20, 20], "spots": [{"name": "blue", "x": 30, "y": 10, "r": 1}]})
    assert row["rgb"] == pytest.approx([200 / 255, 40 / 255, 40 / 255], abs=1e-6) and row["size"] == [40, 20]
    assert row["spots"]["blue"]["rgb"] == [40.0, 40.0, 200.0]
    with pytest.raises(ToolError, match="切り抜き"):
        color_stats.measure(rgb, {"name": "a", "crop": [0, 0, 50, 20]})


def _framemd5(base, step, count, start=0, b_frames=True):
    """ffmpeg の framemd5 の表示（-c copy）の作り物: 時間の単位 base、コマの長さ step、count コマ（B フレームのある並びは、包みの順を入れ替える）。"""
    lines = ["#format: frame checksums", "#version: 2", f"#tb 0: {base}", "#media_type 0: video",
             "#stream#, dts,        pts, duration,     size, hash"]
    order = list(range(count))
    if b_frames:
        order = [order[0]] + [k for pair in zip(order[2::2], order[1::2]) for k in pair] + (order[-1:] if count % 2 == 0 else [])
    for n, k in enumerate(order):
        lines.append(f"0, {start + (n - 2) * step:10d}, {start + k * step:10d}, {step:8d}, 100, 0")
    return "\n".join(lines) + "\n"


def test_the_frame_shown_at_a_time_uses_the_exact_frame_times():
    times, end = color_stats.parse_frame_times(_framemd5("1/30000", 1001, 3100))   # 30000/1001（29.97）
    assert times[:3] == [0, Fraction(1001, 30000), Fraction(2002, 30000)] and end == Fraction(3100 * 1001, 30000)
    assert color_stats.frame_at(times, end, 1.001) == 30                            # 29.97 を掛けて切り捨てると 29
    assert color_stats.frame_at(times, end, 100.1) == 3000                          # 同じく 2999
    assert color_stats.frame_at(times, end, 1.0) == 29 and color_stats.frame_at(times, end, 0.0) == 0
    assert color_stats.frame_at(times, end, float(end)) is None                     # 終わりより後ろ
    times, end = color_stats.parse_frame_times(_framemd5("1/24000", 1001, 2600, start=2002))   # 24000/1001（23.976）。頭の pts が 0 でない
    assert color_stats.frame_at(times, end, 100.1) == 2400 and color_stats.frame_at(times, end, 100.0) == 2397
    times, end = color_stats.parse_frame_times(_framemd5("1/15360", 512, 100, b_frames=False))   # 30 fps
    assert color_stats.frame_at(times, end, 40 / 30) == 40 and color_stats.frame_at(times, end, 0.049) == 1
    with pytest.raises(ToolError, match="時刻が読めません"):
        color_stats.parse_frame_times("#format: frame checksums\n")


def test_the_frame_times_of_a_real_ntsc_video(tmp_path):
    import subprocess
    ffmpeg = pytest.importorskip("imageio_ffmpeg").get_ffmpeg_exe()
    video = tmp_path / "ntsc.mp4"
    run = subprocess.run([ffmpeg, "-hide_banner", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=64x32:rate=30000/1001:duration=4",
                          "-pix_fmt", "yuv420p", "-c:v", "libx264", "-bf", "2", str(video)], capture_output=True)
    if run.returncode:
        pytest.skip("ffmpeg で H.264 の動画を作れない")
    times, end = color_stats.video_frame_times(ffmpeg, video)
    assert len(times) == 120 and times[30] == Fraction(1001, 1000) and end == Fraction(120 * 1001, 30000)
    assert color_stats.frame_at(times, end, 1.001) == 30 and color_stats.frame_at(times, end, 4.1) is None


class Context:
    tools = {}

    def __init__(self, recipe_dir):
        self.recipe_dir = recipe_dir


def _run(tmp_path, parameters):
    from model3d.ops import get
    return get("check.colors").function({}, parameters, {"report": tmp_path / "colors.json"}, Context(tmp_path))


def test_the_operation_measures_both_sides_and_the_difference(tmp_path):
    _save(tmp_path / "ref.png", _two_tone(left=(100, 100, 100), right=(100, 100, 100)))
    _save(tmp_path / "ours.png", _two_tone(left=(120, 100, 80), right=(120, 100, 80)))
    _save(tmp_path / "ours2.png", _two_tone(left=(140, 100, 60), right=(140, 100, 60)))
    result = _run(tmp_path, {"left": ["ref.png"], "right": [{"path": "ours.png"}, {"path": "ours2.png", "name": "warm",
                                                                                     "spots": [{"name": "c", "x": 5, "y": 5, "r": 0}]}],
                             "spots": [{"name": "middle", "x": 20, "y": 10, "r": 2}]})
    report = json.loads((tmp_path / "colors.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-color-stats/0.1"
    assert [r["name"] for r in report["left"]] == ["ref.png"] and [r["name"] for r in report["right"]] == ["ours.png", "warm"]
    assert set(report["right"][1]["spots"]) == {"middle", "c"} and set(report["right"][0]["spots"]) == {"middle"}
    assert report["average"]["right"]["rgb"] == pytest.approx([130 / 255, 100 / 255, 70 / 255], abs=1e-6)
    assert report["difference"]["rgb"] == pytest.approx([30 / 255, 0.0, -30 / 255], abs=1e-6)
    assert report["difference"]["b_r"] == pytest.approx(np.mean([80 / 120, 60 / 140]) - 1.0, abs=1e-6)   # 組の平均は絵ごとの比の平均
    observation = result.observations[0]
    assert observation["kind"] == "color_stats" and observation["left"] == 1 and observation["right"] == 2
    assert observation["difference"] == report["difference"]


def test_the_operation_reads_a_video_frame_at_a_time(tmp_path, monkeypatch):
    from model3d import video_frames
    frames = {k: np.full((20, 40, 3), 4 * k, np.uint8) for k in range(60)}
    asked = {}

    def decode(ffmpeg, path, indices, size, timeout=600, matrix="bt709", in_range="tv"):
        asked["indices"], asked["matrix"] = list(indices), (matrix, in_range)
        return {i: frames[i] for i in indices if i in frames}

    monkeypatch.setattr(video_frames, "decode", decode)
    monkeypatch.setattr(video_frames, "video_info", lambda ffmpeg, path: (40, 20, "bt709", "tv"))
    monkeypatch.setattr(color_stats, "video_frame_times", lambda ffmpeg, path, timeout=600: color_stats.parse_frame_times(
        _framemd5("1/30", 1, 60, b_frames=False)))
    (tmp_path / "clip.mp4").write_bytes(b"video")
    (tmp_path / "ffmpeg.exe").write_bytes(b"")
    _run(tmp_path, {"left": [{"video": "clip.mp4", "at": 1.0}, {"video": "clip.mp4", "at": 0.5, "name": "half"}],
                    "ffmpeg": str(tmp_path / "ffmpeg.exe")})
    report = json.loads((tmp_path / "colors.json").read_text(encoding="utf-8"))
    assert asked == {"indices": [15, 30], "matrix": ("bt709", "tv")}
    assert [(r["name"], r["index"], r["at"], r["frame_time"]) for r in report["left"]] == [("clip.mp4@1s", 30, 1.0, 1.0),
                                                                                         ("half", 15, 0.5, 0.5)]
    assert report["left"][0]["rgb"] == pytest.approx([120 / 255] * 3, abs=1e-6) and report["left"][1]["mean"] == pytest.approx(60 / 255, abs=1e-6)
    assert report["right"] == [] and report["difference"] is None
    with pytest.raises(ToolError, match="終わり"):
        _run(tmp_path, {"left": [{"video": "clip.mp4", "at": 9.0}], "ffmpeg": str(tmp_path / "ffmpeg.exe")})


@pytest.mark.parametrize("parameters, message", [
    ({}, "left"),
    ({"left": ["a.png"], "zoom": 1}, "知らないパラメーター"),
    ({"left": ["missing.png"]}, "ありません"),
    ({"left": "a.png"}, "並び"),
    ({"left": [{"path": "a.png", "video": "a.png", "at": 1}]}, "どちらか 1 つ"),
    ({"left": [{"video": "a.png"}]}, "at"),
    ({"left": [{"video": "a.png", "at": float("inf")}]}, "at"),
    ({"left": [{"video": "a.png", "at": float("nan")}]}, "at"),
    ({"left": [{"path": "a.png", "at": 1}]}, "video といっしょ"),
    ({"left": ["a.png", "a.png"]}, "重なります"),
    ({"left": ["a.png"], "spots": [{"name": "s", "x": 1, "y": 1}]}, "spots"),
    ({"left": ["a.png"], "spots": [{"name": "s", "x": -1, "y": 1, "r": 1}]}, "0 以上の整数"),
    ({"left": [{"path": "a.png", "spots": [{"name": "s", "x": 1, "y": 1, "r": 1}]}], "spots": [{"name": "s", "x": 2, "y": 2, "r": 1}]},
     "重なります"),
    ({"left": ["a.png"], "crop": [0, 0, 10]}, "crop"),
    ({"left": [{"path": "a.png", "tilt": 1}]}, "絵のパスか"),
])
def test_bad_parameters_are_refused(tmp_path, parameters, message):
    _save(tmp_path / "a.png", _two_tone())
    with pytest.raises(ToolError, match=message):
        _run(tmp_path, parameters)


def test_the_operation_runs_in_a_recipe_and_is_not_reused(tmp_path):
    from model3d import executor
    _save(tmp_path / "a.png", _two_tone())
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "colors", "inputs": {},
                                           "steps": [{"id": "colors", "operation": "check.colors", "inputs": {},
                                                      "parameters": {"right": ["a.png"]}, "outputs": {"report": "colors.json"}}]})
    first = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert first["status"] == "succeeded"
    assert json.loads(Path(first["steps"]["colors"]["artifacts"]["report"]).read_text(encoding="utf-8"))["average"]["left"] is None
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["colors"]["reused_from"] is None
