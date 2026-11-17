"""model.neck_junction: 頭の重みの段・栓の輪の半径（Blender なし）と、工程の受け渡しと悪い設定（代役の Blender）。
本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import neck_junction
from model3d.io import ToolError, atomic_json

GOOD = {"body": "Body", "neck_bone": "neck", "head_bone": "head", "radius": 0.05, "ramp_from_z": 2.0, "head_weight_top": 0.8,
        "plug_from_below_top": 0.01, "plug_height": 0.05}


def test_head_weight_rises_smoothly_from_the_ramp_start_to_the_top():
    assert neck_junction.ramp(1.9, 2.0, 2.2, 0.8) == 0.0                        # 始まりより下は 0
    assert neck_junction.ramp(2.0, 2.0, 2.2, 0.8) == 0.0
    assert neck_junction.ramp(2.1, 2.0, 2.2, 0.8) == pytest.approx(0.4)         # 真ん中で半分（なめらかな段）
    assert neck_junction.ramp(2.2, 2.0, 2.2, 0.8) == pytest.approx(0.8)
    assert neck_junction.ramp(2.5, 2.0, 2.2, 0.8) == pytest.approx(0.8)         # いちばん上より上も head_weight_top
    low, high = neck_junction.ramp(2.002, 2.0, 2.2, 1.0), neck_junction.ramp(2.198, 2.0, 2.2, 1.0)
    assert low < 0.01 and high > 0.99                                          # 端で傾きが 0


def test_plug_radius_follows_the_ring_fills_missing_directions_and_smooths():
    angles = np.linspace(-math.pi, math.pi, 200, endpoint=False) + 0.01
    circle = np.stack([0.5 + 0.03 * np.cos(angles), -0.2 + 0.03 * np.sin(angles)], axis=1)
    radius = neck_junction.plug_radius(circle, (0.5, -0.2), 28, 0.985)
    assert radius.shape == (28,) and radius == pytest.approx(np.full(28, 0.03 * 0.985), abs=1e-6)
    # 半分の向きにしか頂点が無くても、無い向きは輪の上で隣から補う（一定の半径なら一定のまま）
    half = circle[circle[:, 1] > -0.2]
    assert neck_junction.plug_radius(half, (0.5, -0.2), 28, 1.0) == pytest.approx(np.full(28, 0.03), abs=1e-5)
    # 1 つの向きだけ遠い頂点は、ならして両隣へ分ける（[0.1, 0.2, 0.4, 0.2, 0.1]）
    bump = np.concatenate([circle, [[0.5 + 0.05, -0.2 + 0.0001]]])
    out = neck_junction.plug_radius(bump, (0.5, -0.2), 28, 1.0)
    peak = int(np.argmax(out))
    assert out[peak] == pytest.approx(0.03 + 0.4 * 0.02, abs=1e-4) and out[(peak + 1) % 28] == pytest.approx(0.03 + 0.2 * 0.02, abs=1e-4)


def test_defaults_are_the_fine_values_only():
    settings = neck_junction.merged(GOOD)
    assert settings["remove_groups"] == [] and settings["plug_segments"] == 28 and settings["slab"] == 0.007
    assert all(key in GOOD for key in neck_junction.REQUIRED)


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"neck")
json.dump({"schema_version": "model3d-neck-junction/0.1", "body": settings["body"], "region_verts": 50, "z_top": 2.2,
           "removed_weight_verts": 12, "ramped_verts": 48, "neck_top_faces_recoloured": 20,
           "remove_groups_missing": [g for g in settings.get("remove_groups", []) if g.startswith("missing")],
           "plug": {"verts": 145, "faces": 144, "radius_cm": [2.0, 2.2], "material": "Lower"}, "settings": settings, "argv": sys.argv[1:]},
          open(value("--report"), "w", encoding="utf-8"))
'''


def test_neck_junction_passes_the_settings_to_blender(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    step = {"id": "neck", "operation": "model.neck_junction", "inputs": {"blend": "$inputs.avatar"},
            "parameters": {"blender": str(blender), **GOOD, "remove_groups": ["eye.L", "eye.R"], "plug_segments": 24},
            "outputs": {"blend": "neck.blend", "report": "neck.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "neck",
                                           "inputs": {"avatar": {"path": "avatar.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["neck"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "neck_junction.py" and "--python-expr" not in argv   # アドオンは挙げたときだけ
    assert report["settings"] == {**GOOD, "remove_groups": ["eye.L", "eye.R"], "plug_segments": 24}
    observation = step["observations"][0]
    assert observation["kind"] == "neck_junction" and observation["region_verts"] == 50 and observation["plug"]["verts"] == 145
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["neck"]["reused_from"] is None   # 毎回実行する


def test_neck_junction_asks_for_a_review_when_a_group_to_remove_is_not_on_the_body(tmp_path):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    result = get("model.neck_junction").function({"blend": tmp_path / "avatar.blend"},
                                                 {"blender": str(blender), **GOOD, "remove_groups": ["eye.L", "missing.R"]},
                                                 {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context())
    assert result.review is True and "missing.R" in result.unresolved[0]
    assert result.observations[0]["remove_groups_missing"] == ["missing.R"]


@pytest.mark.parametrize("change, message", [
    ({"body": None}, "body"),
    ({"neck_bone": ""}, "neck_bone"),
    ({"head_bone": "neck"}, "別の骨"),
    ({"radius": None}, "radius"),
    ({"radius": 0}, "radius"),
    ({"ramp_from_z": None}, "ramp_from_z"),
    ({"ramp_from_z": float("nan")}, "ramp_from_z"),
    ({"head_weight_top": 1.5}, "head_weight_top"),
    ({"head_weight_top": 0}, "head_weight_top"),
    ({"plug_height": -0.01}, "plug_height"),
    ({"plug_from_below_top": None}, "plug_from_below_top"),
    ({"plug_segments": 2}, "plug_segments"),
    ({"plug_rings": 1.5}, "plug_rings"),
    ({"plug_inset": 0}, "plug_inset"),
    ({"remove_groups": ["neck"]}, "remove_groups"),
    ({"remove_groups": "eye.L"}, "remove_groups"),
    ({"wobble": 1}, "知らないパラメーター"),
])
def test_neck_junction_rejects_bad_settings(tmp_path, change, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    parameters = {"blender": str(blender), **GOOD, **change}
    parameters = {k: v for k, v in parameters.items() if v is not None}

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("model.neck_junction").function({"blend": tmp_path / "avatar.blend"}, parameters,
                                            {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context())
