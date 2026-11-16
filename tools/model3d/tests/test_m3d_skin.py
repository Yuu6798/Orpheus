"""肌の島の塗り直し（model.smooth_skin・model.clean_skin）: 色の引き方・肌色・隣の平均・三角形の塗り・範囲の重み・縮まないならし
（Blender なし）、工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import skin_repaint as sr
from model3d.io import ToolError, atomic_json


def test_sample_reads_texel_centres_bilinearly():
    texture = np.zeros((2, 2, 4), dtype=np.float32)
    texture[0, 0, :3], texture[0, 1, :3] = 0.0, 1.0           # 下の行（Blender の画素は下から）: 黒・白
    uv = np.array([[0.25, 0.25], [0.75, 0.25], [0.5, 0.25]])
    assert sr.sample(texture, uv)[:, 0] == pytest.approx([0.0, 0.999, 0.5], abs=1e-3)   # 端は 1.001 画素手前で切る（台本と同じ）


def test_vertex_colors_average_the_corners():
    texture = np.zeros((2, 2, 4), dtype=np.float32)
    texture[0, 1, :3] = 1.0
    uv = np.array([[0.25, 0.25], [0.75, 0.25], [0.25, 0.25]], dtype=np.float32)
    colors = sr.vertex_colors(texture, uv, np.array([0, 0, 1]), 3)
    assert colors[0, 0] == pytest.approx(0.4995, abs=1e-3) and colors[1, 0] == 0 and colors[2, 0] == 0   # 角の無い頂点は 0


def test_skin_colour_test():
    colors = np.array([[0.98, 0.75, 0.66], [0.95, 0.94, 0.90], [0.20, 0.15, 0.12], [0.5, 0.6, 0.9]])
    skin = {"hue_deg": [4, 32], "saturation": [0.12, 0.60], "value_min": 0.70}
    assert list(sr.skin_colored(colors, skin)) == [True, False, False, False]   # 生成り・暗い・青は肌ではない


def test_neighbour_mean_with_and_without_usable():
    values = np.array([[0.0], [1.0], [4.0]])
    edges = np.array([[0, 1], [1, 2]])
    mean, has = sr.neighbour_mean(values, edges, 3)
    assert mean[:, 0] == pytest.approx([1.0, 2.0, 1.0]) and has.all()
    mean, has = sr.neighbour_mean(values, edges, 3, usable=np.array([True, False, True]))
    assert mean[1, 0] == pytest.approx(2.0) and not has[0] and not has[2]   # 使えない隣（頂点 1）は数えない


def test_triangle_paint_fills_inside_and_margin_then_inside_wins():
    texture = np.zeros((32, 32, 4), dtype=np.float32)
    colors = np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]])
    weights = np.ones(4)
    corners = np.array([[10, 10], [20, 10], [10, 20]], dtype=np.float32) / 32
    other = np.array([[20, 10], [20, 20], [10, 20]], dtype=np.float32) / 32
    jobs = sr.paint_jobs([([0, 1, 2], corners), ([1, 3, 2], other)], colors, weights, 32, 32, 2.0)
    result, painted = sr.paint(texture, jobs)
    assert result[11, 11, 0] == pytest.approx(1.0) and result[19, 19, 2] > 0.5    # 2 つの三角形の内側
    assert result[9, 9, 0] > 0                                                   # 外側への塗り足し（重心座標の許しで、角の近くは広め）
    assert result[0, 0].sum() == 0 and result[31, 31].sum() == 0 and painted > 0  # 遠い所は塗らない
    inner_only = sr.paint(texture, [job for job in jobs if job[0]])[0]
    assert (result[10:20, 10:20] == inner_only[10:20, 10:20]).all()             # 内側は塗り足しに上書きされない


def test_smooth_weights_ramp_in_height_side_and_facing():
    positions = np.array([[0.13, 0.0, 1.17], [0.13, 0.0, 0.90], [0.02, 0.0, 1.17], [0.13, 0.0, 1.17]])
    normals = np.array([[1.0, 0, 0], [1.0, 0, 0], [1.0, 0, 0], [0.0, -1.0, 0]])
    settings = {"z_range": [1.00, 1.34], "z_soft": 0.03, "facing": [0.25, 0.60], "x_min": 0.06, "x_max": 0.20, "x_soft": 0.03}
    weight = sr.smooth_weights(positions, normals, np.ones(4, bool), settings)
    assert weight[0] == pytest.approx(1.0) and weight[1] == 0 and weight[2] == 0 and weight[3] == 0   # 範囲の外・前を向いた面は 0
    assert sr.smooth_weights(positions, normals, np.zeros(4, bool), settings).sum() == 0              # 肌でない頂点は 0


def test_taubin_removes_a_groove_without_shrinking():
    count = 40
    angles = np.linspace(0, 2 * np.pi, count, endpoint=False)
    positions = np.stack([np.cos(angles), np.sin(angles), np.zeros(count)], axis=1)
    positions[0, :2] *= 0.8                                                      # 溝（半径 1 の輪の 1 頂点を内側へ）
    edges = np.stack([np.arange(count), (np.arange(count) + 1) % count], axis=1)
    weight = np.ones(count)
    taubin = sr.taubin(positions, edges, count, weight, {"iterations": 10, "factor": 0.5, "inflate": -0.53})
    shrink = sr.taubin(positions, edges, count, weight, {"iterations": 10, "factor": 0.5, "inflate": 0.0})   # ただの平均だけ
    radius = lambda points: np.hypot(points[:, 0], points[:, 1])
    assert radius(taubin)[0] > 0.9                                               # 溝が浅くなる
    assert radius(taubin)[1:].mean() > 0.985 > radius(shrink)[1:].mean()        # 縮まない（ただの平均は輪ごと縮む）


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out-blend"))
open(value("--texture"), "wb").write(b"png")
report = {"schema_version": "x", "x_settings": settings, "x_mode": value("--mode"), "texels_painted": 5}
if value("--mode") == "smooth":
    report.update(skin_island_verts=100, selected_verts=40, shape_moved_mm={"mean": 1.0, "max": 3.0})
else:
    report.update(islands=2, region_verts=50, dirty_verts=7, not_filled=1 if settings["island_groups"] == ["stuck"] else 0)
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''

SKIN = {"hue_deg": [4, 32], "saturation": [0.12, 0.60], "value_min": 0.70}
SMOOTH = {"body": "Body", "image": "upper", "skin": SKIN, "seed_z": [1.02, 1.12], "z_range": [1.0, 1.34], "x_min": 0.06, "x_max": 0.2,
          "facing": [0.25, 0.6], "shape": {"iterations": 10, "factor": 0.5, "inflate": -0.53, "z_max": 1.125}}
CLEAN = {"body": "Body", "image": "upper", "skin": SKIN, "island_groups": ["hand", "thumb"], "region_groups": ["upper_arm", "lower_arm"]}


def run_recipe(tmp_path, steps):
    from model3d import executor
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "skin",
                                           "inputs": {"model": {"path": "model.blend"}}, "steps": steps})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_skin_steps(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    steps = [{"id": "flank", "operation": "model.smooth_skin", "inputs": {"blend": "$inputs.model"}, "parameters": {"blender": blender, **SMOOTH},
              "outputs": {"blend": "a.blend", "texture": "upper_flank.png", "report": "a.json"}},
             {"id": "arms", "operation": "model.clean_skin", "inputs": {"blend": "$steps.flank.blend"}, "parameters": {"blender": blender, **CLEAN},
              "outputs": {"blend": "b.blend", "texture": "upper_arms.png", "report": "b.json"}}]
    result = run_recipe(tmp_path, steps)
    assert result["status"] == "succeeded", result
    flank = json.loads(Path(result["steps"]["flank"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert flank["x_mode"] == "smooth" and flank["x_settings"]["shape"] == SMOOTH["shape"]
    arms = json.loads(Path(result["steps"]["arms"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert arms["x_mode"] == "clean" and arms["x_settings"]["region_groups"] == ["upper_arm", "lower_arm"]


def test_clean_skin_that_cannot_fill_needs_review(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = run_recipe(tmp_path, [{"id": "arms", "operation": "model.clean_skin", "inputs": {"blend": "$inputs.model"},
                                    "parameters": {"blender": blender, **CLEAN, "island_groups": ["stuck"]},
                                    "outputs": {"blend": "b.blend", "texture": "t.png", "report": "b.json"}}])
    assert result["status"] == "needs_review" and any("埋めきれない" in u for u in result["steps"]["arms"]["unresolved"])


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("operation, parameters, message", [
    ("model.smooth_skin", {**SMOOTH, "skin": {"hue_deg": [4, 32], "saturation": [0.1, 0.6]}}, "value_min"),
    ("model.smooth_skin", {**SMOOTH, "skin": {**SKIN, "hue_deg": [40, 4]}}, "hue_deg"),
    ("model.smooth_skin", {**SMOOTH, "x_max": 0.05}, "x_max"),
    ("model.smooth_skin", {**SMOOTH, "facing": [0.2, 1.5]}, "facing"),
    ("model.smooth_skin", {**SMOOTH, "shape": {"inflate": 0.5}}, "inflate"),
    ("model.smooth_skin", {**SMOOTH, "seed_z": None}, "seed_z"),
    ("model.smooth_skin", {**SMOOTH, "wobble": 1}, "知らない設定"),
    ("model.clean_skin", {**CLEAN, "island_groups": []}, "island_groups"),
    ("model.clean_skin", {**CLEAN, "smooth_factor": 1.5}, "smooth_factor"),
    ("model.clean_skin", {**CLEAN, "image": ""}, "image"),
])
def test_skin_ops_reject_bad_settings(tmp_path, operation, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get(operation).function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                {"blend": tmp_path / "o.blend", "texture": tmp_path / "t.png", "report": tmp_path / "r.json"}, Context(tmp_path))
