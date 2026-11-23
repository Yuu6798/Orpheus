import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import executor
from model3d.io import ToolError, atomic_json
from model3d.iris_texture import draw_atlas, sample_swatches
from model3d.ops.texture_ops import iris_atlas_settings


def _settings():
    return {
        "sample": {"centre": [4.0, 5.0], "radius": 2.0, "swatches": {"base": [[0, 0], [1, 0], [-1, 0]]}},
        "iris": {"size": 24, "radius": 0.375, "base": "base"},
        "sclera": {"size": [8, 12], "dark": [24, 48, 72], "light": [96, 120, 144], "gradient": [0.25, 0.75]},
        "padding": 2,
    }


def _reference():
    image = np.zeros((12, 12, 3), dtype=np.uint8)
    image[5, 2], image[5, 4], image[5, 6] = [0, 121, 140], [63, 85, 63], [100, 45, 99]
    return image


def test_sampling_uses_positions_and_per_channel_median_and_refuses_outside():
    settings = iris_atlas_settings(_settings())
    swatches, positions = sample_swatches(_reference(), settings["sample"])
    assert positions["base"] == [[4, 5], [6, 5], [2, 5]]
    assert 255.0 * swatches["base"] == pytest.approx([63, 85, 99])
    settings["sample"]["swatches"]["base"].append([-10, 0])
    with pytest.raises(ToolError, match="設定画の外"):
        sample_swatches(_reference(), settings["sample"])


def test_iris_layers_paint_in_order_and_atlas_reports_blender_uvs():
    parameters = _settings()
    parameters["iris"]["layers"] = [
        {"kind": "band", "color": [32, 64, 96], "edge": [0.25, 0.5], "curvature": 0.25},
        {"kind": "ring", "color": [12, 24, 36], "edge": [0.75, 1], "strength": 1},
        {"kind": "arc", "color": [192, 168, 144], "edge": [0, 0.5], "radius": 0.625, "width": 0.0625},
        {"kind": "ellipse", "color": [220, 224, 228], "edge": [0.4, 0.8], "centre": [0, 0], "radius": [0.25, 0.375]},
    ]
    atlas, report = draw_atlas(_reference(), iris_atlas_settings(parameters))
    assert report["iris_rect"] == [2, 2, 24, 24]
    assert report["sclera_rect"] == [30, 2, 8, 12]
    assert report["size"] == [40, 28]
    assert report["iris_uv"] == pytest.approx([5 / 40, 1 - 23 / 28, 23 / 40, 1 - 5 / 28])
    assert report["plate_uv"] == pytest.approx([34 / 40, 1 - 2.5 / 28, 1 - 13.5 / 28])
    assert atlas[14, 14].tolist() == [220, 224, 228]
    assert atlas[2, 2].tolist() == [12, 24, 36]
    assert atlas[2, 34].tolist() == [24, 48, 72]
    assert atlas[13, 34].tolist() == [96, 120, 144]
    assert atlas[0, 34].tolist() == atlas[2, 34].tolist()


@pytest.mark.parametrize(("section", "patch", "error"), [
    ("sample", {"radius": -1}, "sample.radius"),
    ("sample", {"swatches": {}}, "sample.swatches"),
    ("iris", {"radius": 0.625}, "iris.radius"),
    ("iris", {"size": True}, "iris.size"),
    ("iris", {"base": "unknown"}, "採色名"),
    ("iris", {"layers": [{"kind": "ellipse", "color": "base", "edge": [0.5, 0.25]}]}, "下限"),
    ("iris", {"layers": [{"kind": "arc", "color": "base", "edge": [0, 1], "radius": 0.5, "width": 0}]}, "width"),
    ("sclera", {"gradient": [-0.25, 0.75]}, "sclera.gradient"),
])
def test_iris_settings_reject_invalid_art_controls(section, patch, error):
    parameters = _settings()
    parameters[section].update(patch)
    with pytest.raises(ToolError, match=error):
        iris_atlas_settings(parameters)


def test_iris_atlas_recipe_writes_png_and_report_and_reuses_same_input(tmp_path):
    image = tmp_path / "source.png"
    Image.fromarray(_reference()).save(image)
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "iris", "inputs": {"sample": {"path": image.name}},
              "steps": [{"operation": "texture.iris_atlas", "id": "atlas", "inputs": {"reference": "$inputs.sample"},
                         "parameters": _settings(), "outputs": {"atlas": "eye.png", "report": "eye.json"}}]}
    path = tmp_path / "recipe.json"
    atomic_json(path, recipe)
    result = executor.run_recipe(path, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["atlas"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    with Image.open(step["artifacts"]["atlas"]) as atlas:
        assert atlas.format == "PNG" and atlas.mode == "RGB" and list(atlas.size) == report["size"]
    repeat = executor.run_recipe(path, tmp_path / "runs")["steps"]["atlas"]
    assert repeat["reused_from"] is not None
