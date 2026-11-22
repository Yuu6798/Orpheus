"""render.afterimage: 目の残像（明るいコマの明るい所だけを、ぼかして決めた色でうすく足す。決めたコマ数で 1/e に薄れる）。
合成のコマ（黒いコマと、白い四角のコマ）で確かめる（Blender なし）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from model3d import afterimage as A
from model3d.io import ToolError, atomic_json, sha256

SIZE = (64, 96)


def _frames(directory, count=12):
    """T 1〜3 は白い四角（明るいコマ）、T 4 から黒いコマ。"""
    from PIL import Image
    directory.mkdir(parents=True, exist_ok=True)
    for T in range(1, count + 1):
        image = np.zeros((SIZE[1], SIZE[0], 3), np.uint8)
        if T <= 3:
            image[30:66, 14:50] = 255
        Image.fromarray(image).save(directory / f"f{T:04d}.png")
    return directory


def _rgb(path):
    from PIL import Image
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB")).astype(int)


def _parameters(**changes):
    parameters = {"frames_dir": "frames", "range": [1, 12], "fps": 10, "source": 3, "span": [4, 12], "amount": 1.0,
                  "decay_frames": 4, "threshold": 0.1, "blur": 0.02, "color": [0.5, 1.0, 0.25]}
    parameters.update(changes)
    return parameters


def _run(tmp_path, parameters):
    from model3d import contracts, executor
    steps = [{"id": "ghost", "operation": "render.afterimage", "parameters": parameters,
              "outputs": {"frames": "frames.json", "report": "afterimage.json"}}]
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "ghost", "inputs": {}, "steps": steps})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    step = result["steps"]["ghost"]
    listing = contracts.load("frames", step["artifacts"]["frames"])
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    return result, step, Path(listing["directory"]), listing, report


def test_the_picture_is_the_blurred_bright_part_in_the_colour():
    bright = np.zeros((40, 40, 3), np.uint8)
    bright[10:30, 10:30] = 255
    picture = A.ghost(bright, 1.5, 0.2, [0.5, 1.0, 0.25])
    assert picture.dtype == np.float32 and picture.shape == (40, 40, 3)
    assert picture[20, 20] == pytest.approx([0.4, 0.8, 0.2], abs=1e-5)                  # (1 − 0.2) × 色
    assert not picture[:4].any() and not picture[:, :4].any()                            # 暗い所には足さない
    grey = np.full((8, 8, 3), 40, np.uint8)                                              # しきいより暗い面は 0
    assert not A.ghost(grey, 1.0, 0.2, [1, 1, 1]).any()
    assert A.strength(10, 4, 0.5, 3) == pytest.approx(0.5 * math.exp(-2))
    added = A.add(np.full((2, 2, 3), 250, np.uint8), np.ones((2, 2, 3), np.float32), 0.1)
    assert (added == 255).all()                                                          # 255 で切る


def test_only_the_square_lights_up_and_it_fades_to_1_over_e(tmp_path):
    _frames(tmp_path / "frames")
    result, step, out, listing, report = _run(tmp_path, _parameters())
    assert result["status"] == "succeeded"
    assert [fr["T"] for fr in listing["frames"]] == list(range(1, 13))
    assert all(fr["sha256"] == sha256(out / fr["file"]) for fr in listing["frames"])
    for T in (1, 2, 3):                                                                  # span の外は、ファイルまで同じ
        assert (out / f"f{T:04d}.png").read_bytes() == (tmp_path / "frames" / f"f{T:04d}.png").read_bytes()
    first = _rgb(out / "f0004.png")
    assert first[48, 32].tolist() == [114, 229, 57]                                      # (1 − 0.1) × 色 × 255（切り捨て）
    assert not first[:20].any() and not first[80:].any() and not first[:, :4].any()      # 四角の所だけ明るい
    centre = [int(_rgb(out / f"f{T:04d}.png")[48, 32, 1]) for T in range(4, 13)]
    assert all(a > b for a, b in zip(centre, centre[1:]))                                # だんだん薄れる
    assert centre[4] / (centre[0] + 1) == pytest.approx(math.exp(-1), abs=0.01)          # decay_frames（4）コマで 1/e
    assert [row["T"] for row in report["applied"]] == list(range(4, 13)) and report["copied"] == 3
    assert report["applied"][4]["amount"] == pytest.approx(math.exp(-1), abs=1e-6)
    assert report["bright"]["T"] == 3 and report["scale"] == 96 and report["blur_px"] == pytest.approx(1.92)
    assert report["schema_version"] == "model3d-afterimage/0.1" and report["lit_pixels"] > 0
    observation = step["observations"][0]
    assert observation["kind"] == "afterimage" and observation["applied"] == 9 and observation["first_applied"] == 4


def test_frames_fainter_than_min_amount_are_copied(tmp_path):
    _frames(tmp_path / "frames")
    _result, _step, out, _listing, report = _run(tmp_path, _parameters(min_amount=math.exp(-1.5)))
    assert [row["T"] for row in report["applied"]] == [4, 5, 6, 7, 8, 9]                 # 濃さ e^(−1.5) 以下（T 10 から）は足さない
    for T in (10, 11, 12):
        assert (out / f"f{T:04d}.png").read_bytes() == (tmp_path / "frames" / f"f{T:04d}.png").read_bytes()


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, inputs=None):
    from model3d.ops import get
    return get("render.afterimage").function(inputs or {}, parameters, {"frames": tmp_path / "out.json"}, Context(tmp_path))


@pytest.mark.parametrize("changes, message", [
    ({"source": 20}, "source"),
    ({"source": None}, "source"),
    ({"span": [20, 30]}, "1 つもありません"),
    ({"span": [5, 4]}, "span"),
    ({"amount": 0}, "amount"),
    ({"decay_frames": 0}, "decay_frames"),
    ({"threshold": 1.0}, "threshold"),
    ({"blur": 0}, "blur"),
    ({"color": [1, 2, 0]}, "color"),
    ({"min_amount": -1}, "min_amount"),
    ({"out_dir": "frames"}, "同じ"),
    ({"echo": 1}, "知らないパラメーター"),
])
def test_the_operation_rejects_bad_settings(tmp_path, changes, message):
    _frames(tmp_path / "frames")
    parameters = _parameters(out_dir="out")
    parameters.update({k: v for k, v in changes.items() if v is not None})
    for key in [k for k, v in changes.items() if v is None]:
        parameters.pop(key)
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_the_rendered_frames_list_is_checked(tmp_path):
    frames = _frames(tmp_path / "frames", count=5)
    listing = {"schema_version": "model3d-frames/0.1", "directory": str(frames.resolve()), "fps": 10, "resolution": list(SIZE),
               "frames": [{"T": T, "f": T, "file": f"f{T:04d}.png", "sha256": sha256(frames / f"f{T:04d}.png"),
                           "bytes": (frames / f"f{T:04d}.png").stat().st_size} for T in range(1, 6)], "missing": []}
    atomic_json(tmp_path / "frames.json", listing)
    base = {"source": 3, "span": [4, 5], "amount": 0.5, "decay_frames": 2, "threshold": 0.1, "blur": 0.02, "color": [1, 1, 1]}
    call(tmp_path, dict(base, out_dir="out"), {"frames": tmp_path / "frames.json"})
    assert json.loads((tmp_path / "out.json").read_text(encoding="utf-8"))["fps"] == 10
    listing["frames"][2]["sha256"] = "0" * 64                                            # 明るいコマが、描いた後に変わった
    atomic_json(tmp_path / "frames.json", listing)
    with pytest.raises(ToolError, match="違います"):
        call(tmp_path, dict(base, out_dir="out2"), {"frames": tmp_path / "frames.json"})
