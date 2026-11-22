"""check.colors（色の数を、絵の組で比べる）: 明るさ・百分位・色の比・彩度（OpenCV の 8 ビットの HSV の S と同じ計算）・決めた所・
切り抜き、動画の時刻 → コマの番号、組の平均と差、悪いパラメーター、工程の受け渡し。絵は作り物（どの作品とも関係ない）。
照合の元（リポジトリ外の 4 作目の 3D のダンスの台本）との照合は docs/model3d.md。"""
import json
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


def _opencv_s(rgb):
    """OpenCV の RGB2HSV（8 ビット）の S の式を、画素ごとに写したもの（sdiv_table と hsv_shift 12）。"""
    out = np.zeros(rgb.shape[:2], int)
    for (y, x), _ in np.ndenumerate(out):
        r, g, b = (int(v) for v in rgb[y, x])
        v, vmin = max(r, g, b), min(r, g, b)
        table = int(round((255 << 12) / v)) if v else 0
        out[y, x] = ((v - vmin) * table + (1 << 11)) >> 12
    return out


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


def test_saturation_is_the_opencv_integer_formula():
    rng = np.random.default_rng(7)
    rgb = rng.integers(0, 256, (24, 24, 3)).astype(np.uint8)
    rgb[0, 0] = (0, 0, 0)                                                          # 最大 0 は 0
    rgb[0, 1] = (255, 255, 255)
    assert np.array_equal(color_stats.saturation(rgb), _opencv_s(rgb))
    assert color_stats.saturation(rgb)[0, 0] == 0 and color_stats.saturation(rgb)[0, 1] == 0


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


def test_the_frame_shown_at_a_time():
    assert color_stats.video_index(0.0, 30) == 0 and color_stats.video_index(40.5, 30) == 1215
    assert color_stats.video_index(1 / 30 * 7, 30) == 7                            # 丸めで 6.999… になっても 7
    assert color_stats.video_index(0.049, 30) == 1


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
    monkeypatch.setattr(color_stats, "video_fps", lambda ffmpeg, path: 30.0)
    (tmp_path / "clip.mp4").write_bytes(b"video")
    (tmp_path / "ffmpeg.exe").write_bytes(b"")
    _run(tmp_path, {"left": [{"video": "clip.mp4", "at": 1.0}, {"video": "clip.mp4", "at": 0.5, "name": "half"}],
                    "ffmpeg": str(tmp_path / "ffmpeg.exe")})
    report = json.loads((tmp_path / "colors.json").read_text(encoding="utf-8"))
    assert asked == {"indices": [15, 30], "matrix": ("bt709", "tv")}
    assert [(r["name"], r["index"], r["at"]) for r in report["left"]] == [("clip.mp4@1s", 30, 1.0), ("half", 15, 0.5)]
    assert report["left"][0]["rgb"] == pytest.approx([120 / 255] * 3, abs=1e-6) and report["left"][1]["mean"] == pytest.approx(60 / 255, abs=1e-6)
    assert report["right"] == [] and report["difference"] is None
    with pytest.raises(ToolError, match="取り出せません"):
        _run(tmp_path, {"left": [{"video": "clip.mp4", "at": 9.0}], "ffmpeg": str(tmp_path / "ffmpeg.exe")})


@pytest.mark.parametrize("parameters, message", [
    ({}, "left"),
    ({"left": ["a.png"], "zoom": 1}, "知らないパラメーター"),
    ({"left": ["missing.png"]}, "ありません"),
    ({"left": "a.png"}, "並び"),
    ({"left": [{"path": "a.png", "video": "a.png", "at": 1}]}, "どちらか 1 つ"),
    ({"left": [{"video": "a.png"}]}, "at"),
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
