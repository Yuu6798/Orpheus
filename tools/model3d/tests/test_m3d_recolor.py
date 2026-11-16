"""色の絵の塗り替え（model.recolor・model.recolor_part）と部品を外す（model.remove_parts）: 色の計算と uv の範囲（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import recolor_texture as rt
from model3d.io import ToolError, atomic_json


def test_hsv_round_trip():
    rng = np.random.default_rng(3)
    rgb = rng.random((200, 3))
    assert rt.to_rgb(*rt.to_hsv(rgb)) == pytest.approx(rgb, abs=1e-9)
    hue, saturation, value = rt.to_hsv(np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.5, 0.5, 0.5]]))
    assert hue == pytest.approx([0.0, 120.0, 0.0]) and saturation == pytest.approx([1.0, 1.0, 0.0]) and value == pytest.approx([1.0, 1.0, 0.5])


def test_recolor_changes_only_the_hue_range():
    pixels = np.array([[0.8, 0.4, 0.3, 1.0],      # 色相 12°・彩度 0.625（範囲の中）
                       [0.2, 0.4, 0.9, 1.0],      # 青（範囲の外）
                       [0.6, 0.58, 0.57, 1.0]])   # 色相は中だが彩度が低い（生成りの布など）
    rule = {"image": "x", "hue_range_deg": [4, 26], "soft": 4, "min_saturation": 0.2, "hue_shift": 10.0, "sat_scale": 0.5}
    result, weight = rt.recolor_pixels(pixels, rule)
    assert weight[0] == pytest.approx(1.0) and weight[1] == 0 and weight[2] == 0
    assert (result[1:] == pixels[1:]).all() and result[0, 3] == 1.0
    hue, saturation, _ = rt.to_hsv(result[:1, :3])
    assert hue[0] == pytest.approx(22.0) and saturation[0] == pytest.approx(0.625 / 2)
    wrapped, weight = rt.recolor_pixels(np.array([[0.8, 0.3, 0.35, 1.0]]), {"hue_range_deg": [-25, 24], "soft": 5})   # 色相 354° は −6°
    assert weight[0] == pytest.approx(1.0)


def test_rules_on_the_same_image_build_on_each_other():
    rng = np.random.default_rng(3)
    images = {"hair": rng.random((50, 4)), "skin": rng.random((50, 4))}
    images["hair"][:, 0], images["hair"][:, 1:3] = 0.9, 0.2                  # 赤い髪
    reads = []

    def read(name):
        reads.append(name)
        return images[name].copy()

    first = {"hue_range_deg": [-30, 30], "sat_scale": 0.5}
    second = {"hue_range_deg": [-30, 30], "value_scale": 0.5}
    third = {"hue_range_deg": [0, 360], "hue_shift": 40.0, "min_saturation": 0.0}
    current, reports = rt.recolor_all([first, third, second], ["hair", "skin", "hair"], read)
    assert reads == ["hair", "skin"] and list(current) == ["hair", "skin"]         # 元の絵は 1 回ずつ読む・初めて出た順
    expected = rt.recolor_pixels(rt.recolor_pixels(images["hair"], first)[0], second)[0]
    assert current["hair"] == pytest.approx(expected)                             # 2 つ目は 1 つ目の結果に重ねる
    assert current["skin"] == pytest.approx(rt.recolor_pixels(images["skin"], third)[0])
    assert [r["image"] for r in reports] == ["hair", "skin", "hair"] and reports[0]["pixels_changed_share"] == 1.0


def test_uv_mask_covers_the_triangles_and_the_margin():
    triangle = np.array([[[2.0, 2.0], [12.0, 2.0], [2.0, 12.0]]])
    tight = rt.uv_mask(triangle, 16, 16, 0.0)
    assert tight[3, 3] and not tight[11, 11] and not tight[0, 0]
    wide = rt.uv_mask(triangle, 16, 16, 3.0)
    assert wide[1, 1] and wide.sum() > tight.sum() and not wide[15, 15]


def test_gain_on_dark_texels_only():
    texture = np.zeros((2, 2, 4), dtype=np.float32)
    texture[0, 0, :3] = 0.2                           # 暗い布
    texture[0, 1, :3] = 0.9                           # 明るい縁取り
    texture[1, 0, :3] = 0.5                           # 境目（0.45〜0.55 でなめらかに）
    texture[..., 3] = 1.0
    mask = np.array([[True, True], [True, False]])
    gain = np.array([0.5, 1.0, 1.0], dtype=np.float32)
    result, amount = rt.gain_pixels(texture, mask, gain, 0.45, 0.1)
    assert result[0, 0, 0] == pytest.approx(0.1) and result[0, 1, 0] == pytest.approx(0.9)
    assert amount[1, 0] == pytest.approx(0.5) and result[1, 0, 0] == pytest.approx(0.5 * 0.75)
    assert amount[1, 1] == 0                           # 範囲の外は変えない
    every, amount = rt.gain_pixels(texture, mask, gain, None, 0.1)
    assert every[0, 1, 0] == pytest.approx(0.45) and amount.sum() == 3   # dark_below なし: 範囲の全部


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
mode = value("--mode")
shutil.copyfile(value("--blend"), value("--out-blend"))
report = {"schema_version": "x", "x_settings": settings, "x_mode": mode}
if mode == "remove":
    report.update(removed=[{"name": n, "verts": 3} for n in settings["objects"] if n != "Ghost"],
                  missing=[n for n in settings["objects"] if n == "Ghost"])
elif mode == "part":
    open(value("--texture"), "wb").write(b"png")
    report.update(object=settings["object"], image="lower", texels_in_part=10, texels_changed=4)
else:
    report.update(rules=[{"image": r["image"], "pixels_changed_share": 0.1} for r in settings["rules"]])
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def run_recipe(tmp_path, steps):
    from model3d import executor
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "recolor",
                                           "inputs": {"model": {"path": "model.blend"}}, "steps": steps})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_recolor_steps(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    steps = [{"id": "remove", "operation": "model.remove_parts", "inputs": {"blend": "$inputs.model"},
              "parameters": {"blender": blender, "objects": ["SleeveL", "SleeveR"]}, "outputs": {"blend": "a.blend", "report": "a.json"}},
             {"id": "part", "operation": "model.recolor_part", "inputs": {"blend": "$steps.remove.blend"},
              "parameters": {"blender": blender, "object": "Skirt", "gain": [0.84, 1.0, 0.93], "dark_below": 0.45},
              "outputs": {"blend": "b.blend", "texture": "skirt.png", "report": "b.json"}},
             {"id": "hue", "operation": "model.recolor", "inputs": {"blend": "$steps.part.blend"},
              "parameters": {"blender": blender, "rules": [{"image": "hair", "hue_range_deg": [-25, 24], "hue_shift": -4.0, "_note": "x"}]},
              "outputs": {"blend": "c.blend", "report": "c.json"}}]
    result = run_recipe(tmp_path, steps)
    assert result["status"] == "succeeded", result
    part = json.loads(Path(result["steps"]["part"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert part["x_settings"] == {"object": "Skirt", "image": None, "gain": [0.84, 1.0, 0.93], "dark_below": 0.45, "feather": 0.1,
                                  "margin": 3.0}
    hue = json.loads(Path(result["steps"]["hue"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert hue["x_settings"]["rules"] == [{"image": "hair", "hue_range_deg": [-25, 24], "hue_shift": -4.0}]   # メモの鍵は渡さない


def test_missing_part_needs_review(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = run_recipe(tmp_path, [{"id": "remove", "operation": "model.remove_parts", "inputs": {"blend": "$inputs.model"},
                                    "parameters": {"blender": blender, "objects": ["SleeveL", "Ghost"]},
                                    "outputs": {"blend": "a.blend", "report": "a.json"}}])
    assert result["status"] == "needs_review" and any("Ghost" in u for u in result["steps"]["remove"]["unresolved"])


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("operation, parameters, outputs, message", [
    ("model.remove_parts", {}, ("blend", "report"), "objects"),
    ("model.remove_parts", {"objects": ["a"], "armature": "Rig"}, ("blend", "report"), "知らない設定"),
    ("model.recolor_part", {"gain": [1, 1, 1]}, ("blend", "texture", "report"), "object"),
    ("model.recolor_part", {"object": "a", "gain": [1, 1]}, ("blend", "texture", "report"), "gain"),
    ("model.recolor_part", {"object": "a", "gain": [1, -1, 1]}, ("blend", "texture", "report"), "gain"),
    ("model.recolor_part", {"object": "a", "gain": [1, 1, 1], "dark_below": 2}, ("blend", "texture", "report"), "dark_below"),
    ("model.recolor_part", {"object": "a", "gain": [1, 1, 1]}, ("blend", "report"), "texture"),
    ("model.recolor", {}, ("blend", "report"), "rules"),
    ("model.recolor", {"rules": [{"image": "a", "hue_range_deg": [20, 10]}]}, ("blend", "report"), "hue_range_deg"),
    ("model.recolor", {"rules": [{"image": "a", "hue_range_deg": [0, 10], "value_gamma": 0}]}, ("blend", "report"), "value_gamma"),
    ("model.recolor", {"rules": [{"image": "a", "hue_range_deg": [0, 10], "tint": 1}]}, ("blend", "report"), "知らない設定"),
])
def test_recolor_ops_reject_bad_settings(tmp_path, operation, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    paths = {"blend": tmp_path / "o.blend", "texture": tmp_path / "t.png", "report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match=message):
        get(operation).function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                {name: paths[name] for name in outputs}, Context(tmp_path))
