"""コマの比べ（check.frames と model3d frames compare）: コマの並びの読み方、同じファイルの近道、違う画素・いちばん大きい差・
色の差の平均（リポジトリ外の試作と同じ値）・透けの違い、片方にしか無いコマと大きさの違うコマ、しきい値の合否、見比べの PNG。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from m3d_helpers import run_cli
from model3d import checks
from model3d.io import ToolError, atomic_json, sha256


def _frames(directory, count=3, size=(8, 6), mode="RGB", start=1):
    directory.mkdir(parents=True, exist_ok=True)
    for T in range(start, start + count):
        color = (10 * T, 100, 200) if mode == "RGB" else (10 * T, 100, 200, 255)
        Image.new(mode, size, color).save(directory / f"f{T:04d}.png")
    return directory


def _poke(path, xy, color):
    with Image.open(path) as image:
        image = image.copy()
    image.putpixel(xy, color)
    image.save(path)


def _private(left, right):
    """リポジトリ外の試作の比べと同じ計算（色だけ、int16）。"""
    a = np.asarray(Image.open(left).convert("RGB"), dtype=np.int16)
    b = np.asarray(Image.open(right).convert("RGB"), dtype=np.int16)
    diff = np.abs(a - b)
    return int((diff.max(axis=2) > 0).sum()), int(diff.max()), float(diff.mean())


def test_frames_are_read_from_a_folder_or_a_frames_list(tmp_path):
    folder = _frames(tmp_path / "a")
    (folder / "notes.txt").write_text("x")
    (folder / "f12.jpg").write_bytes(b"x")
    assert sorted(checks.frame_files(folder)) == [1, 2, 3]
    listing = {"schema_version": "model3d-frames/0.1", "directory": str(folder.resolve()), "fps": 10, "resolution": [8, 6],
               "frames": [{"T": 7, "f": 7, "file": "f0002.png", "sha256": sha256(folder / "f0002.png"), "bytes": 1}], "missing": []}
    atomic_json(tmp_path / "frames.json", listing)
    assert checks.frame_files(tmp_path / "frames.json") == {7: folder.resolve() / "f0002.png"}


def test_pixel_differences_match_the_prototype_and_alpha_is_counted_apart(tmp_path):
    left, right = _frames(tmp_path / "l", mode="RGBA"), _frames(tmp_path / "r", mode="RGBA")
    _poke(right / "f0002.png", (3, 2), (10 * 2 + 40, 100, 200, 255))           # 色が 1 画素だけ 40 違う
    _poke(right / "f0003.png", (0, 0), (30, 100, 200, 128))                     # 透けだけが 1 画素違う
    report = checks.compare_frames(checks.frame_files(left), checks.frame_files(right))
    rows = {r["T"]: r for r in report["rows"]}
    assert rows[1]["identical"] and rows[1]["same_file"] and report["identical_frames"] == 1
    assert (rows[2]["differing_pixels"], rows[2]["max_channel_diff"], rows[2]["mean_abs_diff"]) == _private(left / "f0002.png",
                                                                                                         right / "f0002.png")
    assert rows[3]["differing_pixels"] == 1 and rows[3]["alpha_differing_pixels"] == 1 and rows[3]["mean_abs_diff"] == 0.0
    assert rows[3]["max_channel_diff"] == 127 and report["pixels"] == 3 * 48 and report["differing_pixels"] == 2
    assert report["pixels_over"] == {">0": 2, ">2": 2, ">8": 2, ">16": 2, ">64": 1, ">128": 0}
    assert [r["T"] for r in report["worst_frames"]] == [2, 3, 1] and report["pass"] is None   # しきい値が無ければ測るだけ


def test_missing_and_resized_frames_are_listed_and_fail_when_limits_are_given(tmp_path):
    left = _frames(tmp_path / "l", count=3)
    right = _frames(tmp_path / "r", count=3, start=2)                            # 1 は右に無く、4 は左に無い
    Image.new("RGB", (4, 4)).save(right / "f0003.png")                           # 3 は大きさが違う
    report = checks.compare_frames(checks.frame_files(left), checks.frame_files(right), {"max_differing_pixels": 0})
    assert report["missing_on_right"] == [1] and report["missing_on_left"] == [4] and report["size_mismatch"] == [3]
    assert report["compared"] == 1 and report["pass"] is False and len(report["failures"]) == 2       # 右に無い 1 と大きさの違う 3（左に無い 4 は並べるだけ）
    same = checks.compare_frames(checks.frame_files(left), checks.frame_files(left), {"max_differing_pixels": 0, "max_mean_abs": 0})
    assert same["pass"] is True and same["identical_frames"] == 3


def test_the_view_puts_left_right_and_the_amplified_difference_side_by_side(tmp_path):
    left, right = _frames(tmp_path / "l"), _frames(tmp_path / "r")
    _poke(right / "f0001.png", (1, 1), (10 + 5, 100, 200))
    info = checks.frame_view(left / "f0001.png", right / "f0001.png", tmp_path / "view.png", amplify=8)
    with Image.open(tmp_path / "view.png") as view:
        assert view.size == (24, 6) and view.getpixel((16 + 1, 1)) == (40, 40, 40) and view.getpixel((16, 0)) == (0, 0, 0)
    assert info["fraction_over"][">2"] == pytest.approx(1 / 48, abs=1e-6)


def _recipe(tmp_path, parameters, inputs=None, step_inputs=None, outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "frames", "inputs": inputs or {},
                                           "steps": [{"id": "compare", "operation": "check.frames", "inputs": step_inputs or {},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"report": "frames.json", "view": "view.png"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_compares_folders_or_lists_and_writes_the_view(tmp_path):
    from model3d import executor
    _frames(tmp_path / "l", count=4)
    _frames(tmp_path / "r", count=4)
    _poke(tmp_path / "r" / "f0003.png", (2, 2), (0, 0, 0))
    result = executor.run_recipe(_recipe(tmp_path, {"left_dir": "l", "right_dir": "r", "range": [2, 4], "report_only": True}),
                                 tmp_path / "runs")
    step = result["steps"]["compare"]
    assert result["status"] == "succeeded" and step["observations"][0]["frames"] == 3 and "rows" not in step["observations"][0]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert len(report["rows"]) == 3 and report["view"]["T"] == 3 and Path(step["artifacts"]["view"]).is_file()
    strict = executor.run_recipe(_recipe(tmp_path, {"left_dir": "l", "right_dir": "r", "max_differing_pixels": 0, "strict": True}),
                                 tmp_path / "runs2")
    assert strict["status"] == "failed" and "differing_pixels" in strict["steps"]["compare"]["error"]
    review = executor.run_recipe(_recipe(tmp_path, {"left_dir": "l", "right_dir": "r", "max_differing_pixels": 0}), tmp_path / "runs3")
    assert review["steps"]["compare"]["status"] == "needs_review"
    listing = {"schema_version": "model3d-frames/0.1", "directory": str((tmp_path / "l").resolve()), "fps": 10, "resolution": [8, 6],
               "frames": [{"T": T, "f": T, "file": f"f{T:04d}.png", "sha256": sha256(tmp_path / "l" / f"f{T:04d}.png"), "bytes": 1}
                          for T in (1, 2)], "missing": []}
    atomic_json(tmp_path / "frames.json", listing)
    mixed = executor.run_recipe(_recipe(tmp_path, {"right_dir": "r", "max_differing_pixels": 0}, {"left": {"path": "frames.json"}},
                                        {"left": "$inputs.left"}, {"report": "frames.json"}), tmp_path / "runs4")
    assert mixed["status"] == "succeeded" and mixed["steps"]["compare"]["observations"][0]["pass"] is True   # 左の 2 コマは同じ


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


@pytest.mark.parametrize("parameters, inputs, message", [
    ({"right_dir": "r"}, {}, "left は"),
    ({"left_dir": "l", "right_dir": "r"}, {"left": "frames.json"}, "left は"),
    ({"left_dir": "nope", "right_dir": "r"}, {}, "フォルダがありません"),
    ({"left_dir": "l", "right_dir": "r", "range": [3, 1]}, {}, "range"),
    ({"left_dir": "l", "right_dir": "r", "range": [9, 9]}, {}, "左にコマがありません"),
    ({"left_dir": "l", "right_dir": "r", "wobble": 1}, {}, "知らないパラメーター"),
    ({"left_dir": "l", "right_dir": "r", "view_frame": 99}, {}, "view"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, inputs, message):
    from model3d.ops import get
    _frames(tmp_path / "l")
    _frames(tmp_path / "r")
    with pytest.raises(ToolError, match=message):
        get("check.frames").function(inputs, parameters, {"report": tmp_path / "o.json", "view": tmp_path / "v.png"}, Context(tmp_path))


def test_the_cli_prints_a_summary_and_fails_only_when_limits_are_exceeded(tmp_path):
    left, right = _frames(tmp_path / "l"), _frames(tmp_path / "r")
    _poke(right / "f0002.png", (0, 0), (0, 0, 0))
    code, result, _ = run_cli("frames", "compare", left, right, "--out", tmp_path / "full.json", "--view", tmp_path / "v.png")
    assert code == 0 and result["differing_pixels"] == 1 and "rows" not in result and result["view"]["T"] == 2
    assert len(json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))["rows"]) == 3
    code, result, _ = run_cli("frames", "compare", left, right, "--max-differing-pixels", "0")
    assert code == 1 and result["pass"] is False
    code, result, _ = run_cli("frames", "compare", left, right, "--max-channel-diff", "255")
    assert code == 0 and result["pass"] is True
