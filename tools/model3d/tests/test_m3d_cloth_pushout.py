"""布の押し出し（cloth.pushout）と裏地を表から離す（cloth.lining_gap）: 筒の輪の太さの測り方・ならし・層の距離・輪を動かす骨（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import cloth_layers as cl
from model3d.blender_adapter import cloth_pushout as cp
from model3d.io import ToolError, atomic_json


def test_sector_radii_of_a_round_and_an_oval_slice():
    angle = np.linspace(0, 2 * math.pi, 200, endpoint=False)
    radii = cl.sector_radii(0.1 * np.cos(angle), 0.1 * np.sin(angle))
    assert radii == pytest.approx(np.full(16, 0.1), abs=1e-3)              # 丸い輪は、どの向きも同じ太さ
    oval = cl.sector_radii(0.12 * np.cos(angle), 0.06 * np.sin(angle))
    assert oval[0] > oval[4] and oval[8] > oval[12]                          # 左右（u）が前後（v）より太い
    assert cl.sector_radii(np.zeros(5), np.zeros(5)) is None                # 頂点が足りない


def test_missing_sectors_are_filled_from_both_sides():
    angle = np.linspace(-math.pi / 2, math.pi / 2, 80)                       # 右半分だけに頂点がある
    radii = cl.sector_radii(0.1 * np.cos(angle), 0.1 * np.sin(angle))
    assert radii is not None and np.isfinite(radii).all() and radii.min() > 0.05


def test_rings_are_smoothed_along_except_the_ends():
    rings = [{"radii": np.full(16, r)} for r in (0.1, 0.2, 0.1, 0.2)]
    cl.smooth_along(rings)
    assert rings[0]["radii"][0] == 0.1 and rings[-1]["radii"][0] == 0.2      # 両端はそのまま
    assert rings[1]["radii"][0] == pytest.approx(0.25 * 0.1 + 0.5 * 0.2 + 0.25 * 0.1)


def test_layer_offsets_follow_the_script():
    assert cp.layer_offsets([0.006], 0.0015) == [0.006]
    assert cp.layer_offsets([0.003, 0.006], 0.0015) == [0.006, (0.003 + 0.006) / 2, 0.003, 0.003 - 0.0015]   # 外・間・内・当て布
    assert cp.layer_offsets([0.001, 0.006], 0.0015)[3] == 0.0005           # 当て布は 0.5 mm より内へは出さない


def test_pelvis_rings_follow_the_bone_below_the_next_head():
    heads = {"spine": 1.05, "chest": 1.20}
    follow = ["hips", "spine", "chest"]
    assert [cp.follow_bone(z, follow, heads) for z in (0.9, 1.1, 1.3)] == ["hips", "spine", "chest"]
    assert cp.follow_bone(1.3, ["hips", "spine"], {"spine": 1.05}) == "spine"



def test_meshes_shared_or_with_shape_keys_are_refused():
    from types import SimpleNamespace as NS
    def obj(users=1, fake=False, keys=None):
        return NS(name="Skirt", data=NS(name="skirt_mesh", users=users, use_fake_user=fake, shape_keys=keys))
    assert cl.mesh_problem(obj()) == "" and cl.mesh_problem(obj(users=2, fake=True)) == ""     # 偽の使い手は数えない
    assert "共有" in cl.mesh_problem(obj(users=2))                                              # リンク複製は全部に効く
    assert cl.mesh_problem(obj(keys=NS())) == "" and "形状キー" in cl.mesh_problem(obj(keys=NS()), shape_keys=True)

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
if settings.get("only_lining_gap"):
    report = {"schema_version": "model3d-cloth-pushout/0.1", "objects": {c["mesh"]: {"verts_moved": 3} for c in settings["cloths"]}, "settings": settings}
else:
    tubes = {"LowerProxy.pelvis": {"rings": 12, "sign_wrong_points": 0}, "LowerProxy.thigh.L": {"rings": 8, "sign_wrong_points": 1}}
    report = {"schema_version": "model3d-cloth-pushout/0.1", "targets": list(tubes), "tubes": tubes,
              "objects": {c["mesh"]: {"layers": {"pushout_body": {"verts": 9}}} for c in settings["cloths"]}, "settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''

CLOTHS = [{"mesh": "Skirt", "offsets": [0.003, 0.006]}, {"mesh": "Panels", "offsets": [0.009]}]
PELVIS = {"bones": ["hips", "spine", "upper_leg.L", "upper_leg.R"], "centre": "hips", "follow": ["hips", "spine"]}
THIGHS = [{"name": s, "upper": f"upper_leg.{s}", "lower": f"lower_leg.{s}", "bones": [f"upper_leg.{s}", f"lower_leg.{s}", "hips"]} for s in "LR"]


def run(tmp_path, operation, parameters):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "cloth", "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "step", "operation": operation, "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": {"blender": blender, **parameters},
                                                      "outputs": {"blend": "out.blend", "report": "report.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_pushout_step_flags_misjudged_tubes(tmp_path):
    result = run(tmp_path, "cloth.pushout", {"body": "Body", "cloths": CLOTHS, "pelvis": PELVIS, "thighs": THIGHS, "axis_bone": "hips"})
    step = result["steps"]["step"]
    assert step["status"] == "needs_review" and any("thigh.L" in u for u in step["unresolved"])
    sent = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert sent["cloths"] == CLOTHS and sent["pelvis"] == PELVIS and "only_lining_gap" not in sent


def test_lining_gap_step(tmp_path):
    result = run(tmp_path, "cloth.lining_gap", {"cloths": CLOTHS, "axis_bone": "hips", "lining_gap": 0.006})
    assert result["status"] == "succeeded", result
    sent = json.loads(Path(result["steps"]["step"]["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert sent["only_lining_gap"] is True and sent["lining_gap"] == 0.006


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


BASE = {"body": "Body", "cloths": CLOTHS, "pelvis": PELVIS, "thighs": THIGHS, "axis_bone": "hips"}


@pytest.mark.parametrize("operation, parameters, message", [
    ("cloth.pushout", {**BASE, "cloths": [{"mesh": "Skirt", "offsets": [0.006, 0.003]}]}, "内 ≤ 外"),
    ("cloth.pushout", {**BASE, "cloths": [{"mesh": "Skirt", "offsets": [0]}]}, "offsets"),
    ("cloth.pushout", {**BASE, "cloths": CLOTHS + CLOTHS[:1]}, "重なって"),
    ("cloth.pushout", {**BASE, "pelvis": {**PELVIS, "centre": "pelvis"}}, "centre"),
    ("cloth.pushout", {**BASE, "thighs": []}, "thighs"),
    ("cloth.pushout", {**BASE, "proxy": "mesh"}, "知らない"),
    ("cloth.pushout", {**BASE, "layer_gap": 0}, "layer_gap"),
    ("cloth.pushout", {k: v for k, v in BASE.items() if k != "axis_bone"}, "axis_bone"),
    ("cloth.lining_gap", {"cloths": CLOTHS, "axis_bone": "hips"}, "lining_gap"),
    ("cloth.lining_gap", {"cloths": [CLOTHS[1]], "axis_bone": "hips", "lining_gap": 0.006}, "層に分ける布"),
    ("cloth.lining_gap", {"cloths": CLOTHS, "axis_bone": "hips", "lining_gap": 0.006, "body": "Body"}, "知らない"),
])
def test_cloth_pushout_rejects_bad_settings(tmp_path, operation, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get(operation).function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
