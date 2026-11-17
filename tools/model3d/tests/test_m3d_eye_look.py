"""VRM の視線を目の骨で（vrm.look_at）: 回す角度の計算と範囲の並び（Blender なし）、工程の受け渡し（代役の Blender）と悪い設定を止めること。
本物の Blender 5.2.4（VRM Add-on）での照合は docs/model3d.md（CI には Blender が無い）。例の値は、どの頭とも関係の無い値。"""
import json
import math
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import eye_look, eye_math
from model3d.io import atomic_json


def test_turn_angle_slides_the_iris_by_the_given_amount():
    # 骨を θ 回すと、瞳は板の上を 半径 × θ 滑る: 半径 10 cm で 5 mm 滑らせるのに 2.865°
    assert eye_look.output_scale(0.005, 0.1) == pytest.approx(math.degrees(0.05))
    assert math.radians(eye_look.output_scale(0.003, 0.12)) * 0.12 == pytest.approx(0.003)
    assert [axis for _, axis in eye_look.RANGE_MAPS] == ["horizontal", "horizontal", "vertical", "vertical"]
    assert {name for name, _ in eye_look.RANGE_MAPS} == {"range_map_horizontal_inner", "range_map_horizontal_outer",
                                                          "range_map_vertical_down", "range_map_vertical_up"}


def test_only_vrm_1_look_at_is_written():
    assert eye_look.version_problem("Armature", "1.0") is None
    assert "VRM 0.0" in eye_look.version_problem("Armature", "0.0")              # VRM 0.x の視線は別の所にあり、書いても書き出しに入らない


def _geometry(radius_r=0.1, radius_l=0.1):
    def eye(radius, sign):
        return {"sphere": {"centre": [0.04 * sign, 0.1, 1.3], "radius": radius, "normal": [0.0, -1.0, 0.0]}, "pupil": [0.04 * sign, 1.3],
                "iris": {"a": 0.006, "b": 0.005}, "rim": {"x": [0.03 * sign - 0.01, 0.03 * sign + 0.01], "upper": [1.305, 1.305],
                                                       "lower": [1.295, 1.295]},
                "window": [0.04 * sign - 0.02, 0.04 * sign + 0.02, 1.28, 1.32], "iris_vertices": [1, 2] if sign < 0 else [3, 4]}
    return {"schema_version": eye_math.SCHEMA, "object": "Head", "eyes": {"R": eye(radius_r, -1.0), "L": eye(radius_l, 1.0)}}


STAND_IN = r'''
import json, math, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out"))
degrees = {axis: round(math.degrees(settings["slide"][axis] / settings["radius"]), 4) for axis in ("horizontal", "vertical")}
json.dump({"blender": "5.2.4", "armature": "Armature", "radius": settings["radius"], "degrees": degrees,
           "before": {"type": "expression"}, "after": {"type": "bone"}, "x_settings": settings, "x_argv": sys.argv[1:]},
          open(value("--report"), "w", encoding="utf-8"))
'''


def _recipe(tmp_path, parameters, data=None):
    (tmp_path / "head.blend").write_bytes(b"blend")
    atomic_json(tmp_path / "eyes.json", data or _geometry())
    step = {"id": "look", "operation": "vrm.look_at", "inputs": {"blend": "$inputs.head", "geometry": "$inputs.eyes"},
            "parameters": parameters, "outputs": {"blend": "look.blend", "report": "look.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "look",
                                           "inputs": {"head": {"path": "head.blend"}, "eyes": {"path": "eyes.json"}}, "steps": [step]})
    return tmp_path / "recipe.json"


SLIDE = {"horizontal": 0.005, "vertical": 0.0012}


def test_look_at_step_passes_the_radius_and_amounts(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "slide": SLIDE, "input_max": 45}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["look"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"] == {"radius": 0.1, "radii": {"R": 0.1, "L": 0.1}, "slide": SLIDE, "input_max": 45.0, "armature": None}
    assert step["observations"][0] == {"kind": "look_at", "type": "bone", "radius": 0.1, "degrees": {"horizontal": 2.8648, "vertical": 0.6875}}
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "eye_look.py" and "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]
    # 左右の球の半径が違えば、平均で決めて、両方を報告に残す
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "slide": SLIDE, "input_max": 45}, _geometry(0.1, 0.12)), tmp_path / "runs")
    report = json.loads(Path(result["steps"]["look"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["radius"] == pytest.approx(0.11) and report["radii"] == {"R": 0.1, "L": 0.12}


@pytest.mark.parametrize("parameters, data, message", [
    ({"input_max": 45}, None, "slide"), ({"slide": {"horizontal": 0.005}, "input_max": 45}, None, "slide"),
    ({"slide": {"horizontal": 0.005, "vertical": -0.001}, "input_max": 45}, None, "slide.vertical"),
    ({"slide": SLIDE}, None, "input_max"), ({"slide": SLIDE, "input_max": 200}, None, "180"),
    ({"slide": SLIDE, "input_max": 45, "spin": 1}, None, "知らないパラメーター"), ({"slide": SLIDE, "input_max": 45, "armature": ""}, None, "armature"),
    ({"slide": SLIDE, "input_max": 45}, {"schema_version": "other"}, "目の形の値")])
def test_look_at_refuses_bad_settings(tmp_path, parameters, data, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}, data), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["look"]["error"]
