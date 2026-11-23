"""腕の関節の細り（check.arm_pinch）: 輪の頂点の選び方・太さの式・要約と合否（Blender なし）、工程の受け渡しと悪い設定（代役の Blender）。
本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace as NS

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import arm_pinch as ap
from model3d.io import ToolError, atomic_json
from model3d.ops.arm_ops import pinch_summary


def tube(radius=0.03, length=0.2, rings=11, around=16):
    """x 軸に沿った筒の頂点 (rings × around, 3)。"""
    angle = np.arange(around) * 2 * math.pi / around
    return np.array([[x, radius * math.cos(a), radius * math.sin(a)] for x in np.linspace(0, length, rings) for a in angle])


def test_ring_takes_the_band_inside_the_radius_and_heavy_enough():
    rest = np.vstack([tube(), [[0.1, 0.2, 0.0]]])                     # 最後の 1 点は軸から 20 cm（袖の飾りなど）
    weight = np.ones(len(rest))
    weight[:16] = 0.5                                                    # 付け根の輪は重みが足りない
    axis = np.array([1.0, 0.0, 0.0])
    chosen = ap.ring_vertices(rest, np.array([0.1, 0, 0]), axis, 0.012, 0.09, weight, 0.9)
    assert list(chosen) == list(range(80, 96))                          # x = 0.1 の輪だけ（遠い点は radius の外）
    assert list(ap.ring_vertices(rest, np.array([0.0, 0, 0]), axis, 0.012, 0.09, weight, 0.9)) == []


def test_spread_is_the_mean_distance_across_the_axis():
    ring = tube(rings=1)
    axis = np.array([1.0, 0.0, 0.0])
    assert ap.spread(ring, axis) == pytest.approx(0.03)
    squeezed = ring.copy()
    squeezed[:, 1:] *= 0.4                                               # 絞られると細くなる
    assert ap.spread(squeezed, axis) / ap.spread(ring, axis) == pytest.approx(0.4)
    slanted = ring + np.outer(np.linspace(0, 0.05, len(ring)), axis)     # 軸に沿ったずれは数えない
    assert ap.spread(slanted, axis) == pytest.approx(0.03)


def test_group_weights_add_up_in_the_vertex_order():
    groups = [NS(index=0, name="upper_arm.L"), NS(index=1, name="upper_arm_swing.L"), NS(index=2, name="spine")]
    vertices = [NS(groups=[NS(group=0, weight=0.5), NS(group=1, weight=0.45), NS(group=2, weight=0.05)]), NS(groups=[NS(group=2, weight=1.0)])]
    weights = ap.group_weight(NS(vertex_groups=groups, data=NS(vertices=vertices)), {"upper_arm.L", "upper_arm_swing.L"})
    assert list(weights) == [0.95, 0.0]


def test_summary_counts_thin_frames_and_judges_only_with_a_threshold():
    raw = {"frames": [1, 2, 3, 4], "rings": {"wrist.L": {"verts": 40, "rest_spread_mm": 25.0, "ratio": [1.0, 0.7, 0.45, 0.9]},
                                              "few.L": {"verts": 3, "rest_spread_mm": None, "ratio": [1.0, 1.0, 1.0, 1.0]}}}
    report = pinch_summary(raw)
    wrist = report["rings"]["wrist.L"]
    assert wrist["min"] == 0.45 and wrist["min_at_frame"] == 3 and wrist["frames_thin"] == 2 and wrist["frames_below_half"] == 1
    assert report["pass"] is None and report["unmeasured"] == ["few.L"]
    judged = pinch_summary(raw, min_ratio=0.5)
    assert judged["pass"] is False and "wrist.L" in judged["failures"][0]
    assert pinch_summary(raw, min_ratio=0.4)["pass"] is True


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
raw = {"schema_version": "model3d-arm-pinch/0.1", "armature": "Rig", "body": settings["body"], "modifiers_off": ["cuff_out_L"],
       "frames": [1, 2, 3], "rings": {r["name"]: {"verts": 20, "rest_spread_mm": 30.0, "ratio": [1.0, 0.6, 0.95]} for r in settings["rings"]},
       "x_settings": settings}
json.dump(raw, open(value("--report"), "w", encoding="utf-8"))
'''

RINGS = [{"name": "wrist.L", "at": "hand.L", "axis": "lower_arm.L", "band": 0.012, "radius": 0.09,
          "groups": ["lower_arm.L", "hand.L", "lower_arm_twist.L"]}]


def test_pinch_step_reports_and_judges(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "pinch", "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "pinch", "operation": "check.arm_pinch", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": blender, "body": "Body", "rings": RINGS, "min_ratio": 0.75},
                                                      "outputs": {"report": "pinch.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "needs_review", result                  # 0.6 のコマがある
    report = json.loads(Path(result["steps"]["pinch"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["pass"] is False and report["rings"]["wrist.L"]["frames_thin"] == 1 and report["modifiers_off"] == ["cuff_out_L"]
    raw = json.loads(Path(report["raw"]).read_text(encoding="utf-8"))
    assert raw["x_settings"]["rings"][0]["offset"] == 0.0 and raw["x_settings"]["min_weight"] == 0.9


POSES = [
    {"name": "rest", "rotations": []},
    {"name": "roll", "rotations": [{"bone": "turn", "axis": "Y", "degrees": 90}]},
    {"name": "opposite", "rotations": [{"bone": "turn", "axis": "Y", "degrees": -90, "space": "armature"}]},
]


def test_pinch_settings_and_report_name_each_static_pose():
    from model3d.ops.arm_ops import pinch_settings

    settings = pinch_settings({"body": "Tube", "rings": RINGS, "poses": POSES})
    assert settings["poses"][1]["rotations"][0]["space"] == "local"
    raw = {"frames": [1, 2, 3], "poses": [pose["name"] for pose in POSES],
           "rings": {"ring": {"verts": 8, "rest_spread_mm": 20.0, "ratio": [1, 0.7, 0.71]}}}
    report = pinch_summary(raw, min_ratio=0.8)
    assert report["rings"]["ring"]["min_at_pose"] == "roll"
    assert report["rings"]["ring"]["ratios_by_pose"] == {"rest": 1, "roll": 0.7, "opposite": 0.71}
    assert report["pass"] is False


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_blender_pinch_samples_without_an_action(tmp_path):
    from model3d import executor

    script = tmp_path / "fixture.py"
    script.write_text("""
import math
import sys
import bpy

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
scene = bpy.context.scene
rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
for name in ("axis", "turn"):
    rig.data.edit_bones.new(name).tail = (0, 1, 0)
bpy.ops.object.mode_set(mode="OBJECT")
mesh = bpy.data.meshes.new("Tube")
angles = [index * math.tau / 8 for index in range(8)]
mesh.from_pydata([(0.25 * math.cos(a), 0.5, 0.25 * math.sin(a)) for a in angles], [], [])
tube = bpy.data.objects.new("Tube", mesh)
scene.collection.objects.link(tube)
for name in ("axis", "turn"):
    tube.vertex_groups.new(name=name).add(list(range(8)), 0.5, "REPLACE")
tube.modifiers.new("Armature", "ARMATURE").object = rig
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
""", encoding="utf-8")
    blender = os.environ["MODEL3D_BLENDER"]
    blend = tmp_path / "tube.blend"
    subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                    "--python", str(script), "--", str(blend)], check=True, capture_output=True, timeout=60)
    original = blend.read_bytes()
    ring = {"name": "middle", "at": "axis", "axis": "axis", "offset": 0.5, "band": 0.01,
            "radius": 0.3, "groups": ["axis", "turn"]}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "pinch",
                "inputs": {"tube": {"path": blend.name}}, "steps": [{"id": "pinch", "operation": "check.arm_pinch",
                "inputs": {"blend": "$inputs.tube"}, "outputs": {"report": "pinch.json"},
                "parameters": {"blender": blender, "body": "Tube", "poses": POSES, "rings": [ring], "min_ratio": 0.8}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    step = result["steps"]["pinch"]
    assert step["status"] == "needs_review", step
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    values = report["rings"]["middle"]["ratios_by_pose"]
    assert report["rings"]["middle"]["verts"] == 8
    assert values["rest"] == pytest.approx(1.0)
    assert values["roll"] == pytest.approx(math.cos(math.pi / 4), abs=1e-6)
    assert values["opposite"] == pytest.approx(values["roll"], abs=1e-7)
    assert blend.read_bytes() == original


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"rings": RINGS}, "body"),
    ({"body": "Body"}, "rings"),
    ({"body": "Body", "rings": [{**RINGS[0], "band": 0}]}, "band"),
    ({"body": "Body", "rings": [{**RINGS[0], "groups": []}]}, "groups"),
    ({"body": "Body", "rings": [{**RINGS[0], "size": 1}]}, "知らない設定"),
    ({"body": "Body", "rings": RINGS + RINGS}, "重なって"),
    ({"body": "Body", "rings": RINGS, "thin": 0}, "thin"),
    ({"body": "Body", "rings": RINGS, "min_ratio": 1.5}, "min_ratio"),
    ({"body": "Body", "rings": RINGS, "frames": [10, 1]}, "frames"),
    ({"body": "Body", "rings": RINGS, "frames": [1, 3], "poses": POSES}, "poses"),
    ({"body": "Body", "rings": RINGS, "poses": [POSES[0], POSES[0]]}, "重なっ"),
    ({"body": "Body", "rings": RINGS, "poses": [{"name": "invalid", "rotations": [
        {"bone": "turn", "axis": "W", "degrees": 90}]}]}, "axis"),
    ({"body": "Body", "rings": RINGS, "min_weight": 2}, "min_weight"),
])
def test_arm_pinch_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("check.arm_pinch").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                        {"report": tmp_path / "r.json"}, Context(tmp_path))
