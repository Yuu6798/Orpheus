"""布の当て布（cloth.patch_lining）と層の順番の検査（check.cloth_layers）: 裏地の色の決め方・要約と合否（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import cloth_patch as cp
from model3d.io import ToolError, atomic_json
from model3d.ops.cloth_ops import layers_summary

RED = {"channel": "r", "min": 0.10, "ratio": 0.3}


def test_lining_colour_rule_matches_the_script():
    colours = np.array([[0.356, 0.002, 0.005], [0.05, 0.0, 0.0], [0.4, 0.2, 0.0], [0.02, 0.02, 0.02], [0.5, 0.15, 0.15]], dtype=np.float32)
    script = (colours[..., 0] >= 0.10) & (colours[..., 1] <= 0.3 * colours[..., 0]) & (colours[..., 2] <= 0.3 * colours[..., 0])
    assert list(cp.is_lining(colours, RED)) == list(script) == [True, False, False, False, True]
    blue = cp.is_lining(np.array([[0.0, 0.05, 0.5], [0.5, 0.0, 0.0]]), {"channel": "b", "min": 0.1, "ratio": 0.3})
    assert list(blue) == [True, False]                                         # ほかの色の裏地も選べる


def test_layers_summary_counts_swapped_frames():
    raw = {"objects": ["Skirt"], "patches": "ignore", "rows": [{"frame": f, "total": t} for f, t in ((1, [0, 0.0]), (2, [3, 1.2]), (3, [9, 4.0]),
                                                                                                    (4, [1, 0.6]), (5, [5, 2.0]))]}
    report = layers_summary(raw)
    assert report["frames_swapped"] == 3 and report["max_swapped_verts"] == 9 and report["max_poke_mm"] == 4.0
    assert [run["frames"] for run in report["runs"]] == [[2, 3], [5, 5]] and report["pass"] is None
    assert layers_summary(raw, max_frames=2)["pass"] is False and layers_summary(raw, max_frames=3)["pass"] is True
    assert report["rest_caches_off"] == [] and report["modifiers_synced"] == []
    cached = layers_summary({**raw, "rest_caches_off": ["Skirt:baked_motion"]})   # 初期姿勢の間だけ外したファイル読みの変形は、報告に残す
    assert cached["rest_caches_off"] == ["Skirt:baked_motion"]


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
script = sys.argv[sys.argv.index("--python") + 1]
if script.endswith("cloth_patch.py"):
    shutil.copyfile(value("--blend"), value("--out-blend"))
    report = {"schema_version": "model3d-cloth-patch/0.1", "object": settings["object"], "faces_added": 3, "verts_added": 5, "settings": settings}
else:
    report = {"schema_version": "model3d-cloth-layers/0.1", "objects": settings["objects"], "patches": settings["patches"],
              "rows": [{"frame": 1, "total": [0, 0.0]}, {"frame": 2, "total": [4, 2.0]}], "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def test_patch_and_layer_steps(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "patch", "inputs": {"model": {"path": "model.blend"}},
                                           "steps": [{"id": "patch", "operation": "cloth.patch_lining", "inputs": {"blend": "$inputs.model"},
                                                      "parameters": {"blender": blender, "object": "Skirt", "axis_bone": "hips", "lining_color": RED,
                                                                     "cover_objects": ["Body"]},
                                                      "outputs": {"blend": "patch.blend", "report": "patch.json"}},
                                                     {"id": "layers", "operation": "check.cloth_layers", "inputs": {"blend": "$steps.patch.blend"},
                                                      "parameters": {"blender": blender, "objects": ["Skirt"], "axis_bone": "hips", "patches": "only",
                                                                     "max_frames": 0},
                                                      "outputs": {"report": "layers.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    patch = json.loads(Path(result["steps"]["patch"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert patch["settings"]["lining_color"] == RED and patch["settings"]["offset"] == 0.002 and patch["settings"]["cover_objects"] == ["Body"]
    layers = json.loads(Path(result["steps"]["layers"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert result["steps"]["layers"]["status"] == "needs_review" and layers["pass"] is False and layers["frames_swapped"] == 1


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("operation, parameters, message", [
    ("cloth.patch_lining", {"object": "Skirt", "axis_bone": "hips"}, "lining_color"),
    ("cloth.patch_lining", {"object": "Skirt", "axis_bone": "hips", "lining_color": {**RED, "channel": "x"}}, "lining_color"),
    ("cloth.patch_lining", {"object": "Skirt", "axis_bone": "hips", "lining_color": {**RED, "min": 2}}, "lining_color.min"),
    ("cloth.patch_lining", {"object": "Skirt", "axis_bone": "hips", "lining_color": RED, "min_dark": 0}, "min_dark"),
    ("cloth.patch_lining", {"object": "Skirt", "lining_color": RED}, "axis_bone"),
    ("cloth.patch_lining", {"object": "Skirt", "axis_bone": "hips", "lining_color": RED, "colour": "red"}, "知らない"),
    ("check.cloth_layers", {"objects": ["Skirt"], "axis_bone": "hips", "patches": "all"}, "patches"),
    ("check.cloth_layers", {"objects": ["Skirt"], "axis_bone": "hips", "pair_from": "Panels"}, "pair_from"),
    ("check.cloth_layers", {"objects": [], "axis_bone": "hips"}, "objects"),
])
def test_patch_and_layers_reject_bad_settings(tmp_path, operation, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    outputs = {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"} if operation == "cloth.patch_lining" else {"report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match=message):
        get(operation).function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters}, outputs, Context(tmp_path))
