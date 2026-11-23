"""開いた筒の両端、UV の色と外側の面の選択を矩形の合成画像で確かめる。"""
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from model3d import executor
from model3d.cylinder_map import coordinate_frame
from model3d.io import ToolError, atomic_json


def test_recipe_unwraps_front_seam_and_preserves_outer_surface(tmp_path):
    vertices = []
    for radius in (0.2, 0.35):
        for distance, degrees in ((0.1, -30), (0.6, 30), (0.1, 50)):
            angle = np.deg2rad(degrees)
            vertices.append([distance, -radius * np.cos(angle), radius * np.sin(angle)])
    mesh = tmp_path / "tube.npz"
    np.savez_compressed(mesh, co=np.array(vertices), tris=[[0, 1, 2], [3, 4, 5]], tri_loops=[[0, 1, 2], [3, 4, 5]],
                        uv=np.array([[0.15, 0.35]] * 3 + [[0.82, 0.71]] * 3))
    color = np.zeros((8, 16, 3), dtype=np.uint8)
    color[2, 13] = [25, 90, 240]
    image = tmp_path / "input.png"
    Image.fromarray(color).save(image)
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "unwrap",
                         "inputs": {"mesh": {"path": mesh.name}, "texture": {"path": image.name}},
                         "steps": [{"id": "unwrap", "operation": "texture.cylinder_map",
                                    "inputs": {"mesh": "$inputs.mesh", "texture": "$inputs.texture"},
                                    "parameters": {"origin": [0, 0, 0], "axis": [1, 0, 0], "zero_direction": [0, -1, 0],
                                                   "quarter_direction": [0, 0, 1], "interval_m": [0, 0.7], "radius_m": 0.4,
                                                   "resolution": [40, 48], "profile_stride": 5},
                                    "outputs": {"map": "pixels.npz", "image": "unwrap.png", "report": "unwrap.json"}}]})
    run = executor.run_recipe(recipe, tmp_path / "runs")
    assert run["status"] == "succeeded", run
    artifacts = run["steps"]["unwrap"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["triangles"] == 2 and report["covered_pixels"] > 0
    assert report["step_deg"] == 7.5
    with np.load(artifacts["map"], allow_pickle=False) as grid, Image.open(artifacts["image"]) as output:
        hit = grid["tri"] >= 0
        assert hit[0].any() and hit[-1].any() and not hit[24].any()
        assert hit.sum() == report["covered_pixels"]
        assert np.all(grid["tri"][hit] == 1)
        assert np.all(grid["px"][hit] == 13) and np.all(grid["py"][hit] == 2)
        assert np.allclose(grid["r"][hit], 0.35)
        assert np.all(np.asarray(output)[hit] == [25, 90, 240])
        assert not np.asarray(output)[~hit].any()


@pytest.mark.parametrize(("axis", "zero", "quarter", "message"), [
    ([0, 0, 0], [0, 1, 0], [0, 0, 1], "axis"),
    ([1, 0, 0], [2, 0, 0], [0, 0, 1], "zero_direction"),
    ([1, 0, 0], [0, 1, 0], [1, 1, 0], "quarter_direction"),
])
def test_axis_frame_rejects_degenerate_directions(axis, zero, quarter, message):
    with pytest.raises(ToolError, match=message):
        coordinate_frame(axis, zero, quarter)
