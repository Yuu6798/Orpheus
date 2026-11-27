"""揺れもののウェイトならしの計算と、Blender への工程の受け渡し。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter.cloth_smooth_weights import smooth_weights
from model3d.executor import run_recipe
from model3d.io import ToolError, atomic_json
from model3d.ops import get


def test_laplacian_crosses_only_connected_spring_weighted_vertices():
    rows = [
        {"hips": 0.25, "A": 0.75}, {"hips": 0.5, "B": 0.5}, {"hips": 1.0},
        {"B": 1.0}, {"A": 1.0}, {"B": 1.0},
    ]
    plan, trimmed = smooth_weights(rows, [(0, 1), (1, 2), (2, 3), (4, 5)], ["A", "B"], 1, 0.25)

    assert plan[0] == pytest.approx({"hips": 0.25, "A": 0.5625, "B": 0.1875})
    assert plan[1] == pytest.approx({"hips": 0.5, "A": 0.125, "B": 0.375})
    assert 2 not in plan and plan[3] == {"B": 1.0}
    assert plan[4] == pytest.approx({"A": 0.75, "B": 0.25})
    assert rows[0] == {"hips": 0.25, "A": 0.75}
    assert trimmed == 0


def test_four_bone_limit_keeps_anchors_and_normalizes_without_increasing_them():
    groups = ["A", "B", "C", "D", "E"]
    rows = [{"hips": 0.08, "pelvis": 0.02, "A": 0.45, "B": 0.3, "C": 0.2, "D": 0.1, "E": 0.03}]
    plan, trimmed = smooth_weights(rows, [], groups, 16, 0.5)
    weights = plan[0]
    assert list(weights) == ["hips", "pelvis", "A", "B"]
    assert weights["hips"] < rows[0]["hips"]
    assert weights["pelvis"] < rows[0]["pelvis"]
    assert sum(weights.values()) == pytest.approx(1)
    assert trimmed == 1
    underweight = {"hips": 0.25, "A": 0.5}
    assert smooth_weights([underweight], [], groups, 16, 0.5)[0] == {0: {"hips": 0.25, "A": 0.75}}


def test_cannot_trim_away_a_protected_bone_to_make_room():
    with pytest.raises(ValueError, match="頂点 0"):
        smooth_weights([{"hips": 0.2, "a": 0.2, "b": 0.2, "c": 0.2, "A": 0.2}], [], ["A"], 1, 0.5)


BLENDER = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
assert sys.argv[sys.argv.index("--python") + 1].endswith("cloth_smooth_weights.py")
shutil.copyfile(value("--blend"), value("--out-blend"))
result = {"mesh": settings["mesh"], "target_vertices": 20, "changed_vertices": 15, "trimmed_vertices": 8, "max_influences_after": 4,
          "settings": settings}
json.dump(result, open(value("--report"), "w", encoding="utf-8"))
'''


def test_weight_smoothing_recipe(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", BLENDER))
    (tmp_path / "cloth.blend").write_bytes(b"model")
    parameters = {"mesh": "Skirt", "groups": ["A", "B"], "armature": "Rig"}
    recipe = {
        "schema_version": "model3d-recipe/0.1",
        "name": "smooth weights",
        "inputs": {"source": {"path": "cloth.blend"}},
        "steps": [{
            "id": "smooth",
            "operation": "cloth.smooth_weights",
            "inputs": {"blend": "$inputs.source"},
            "parameters": {**parameters, "blender": blender},
            "outputs": {"blend": "smoothed.blend", "report": "report.json"},
        }],
    }
    path = tmp_path / "recipe.json"
    atomic_json(path, recipe)
    step = run_recipe(path, tmp_path / "runs")["steps"]["smooth"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == {**parameters, "iterations": 16, "alpha": 0.5}
    assert step["observations"][0]["max_influences_after"] == 4


@pytest.mark.parametrize("parameters, message", [
    ({"groups": []}, "groups"),
    ({"groups": ["A", "A"]}, "groups"),
    ({"iterations": True}, "iterations"),
    ({"iterations": 0}, "iterations"),
    ({"alpha": 1.1}, "alpha"),
    ({"alpha": 0}, "alpha"),
])
def test_weight_smoothing_validates_settings_before_starting_blender(tmp_path, parameters, message):
    blend = tmp_path / "model.blend"
    blend.write_bytes(b"model")
    settings = {"mesh": "Skirt", "groups": ["A", "B"], **parameters}
    with pytest.raises(ToolError, match=message):
        get("cloth.smooth_weights").function({"blend": blend}, settings, {}, SimpleNamespace(step_dir=tmp_path))
