"""顔の形を参照の比率へ寄せる（model.face_shape）: 寄せる量の表・奥行きの効き・あごを細くする（Blender なし）、工程の受け渡しと
悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import face_shape as fs
from model3d.io import ToolError, atomic_json

VIEW = {"from": "-Y", "center": [0.0, 1.5], "span": 0.32, "size": [1024, 1024]}       # 1 画素 0.3125 mm
LIFT = [{"vy": 572, "up": 0}, {"vy": 643.6, "up": 31.8}, {"vy": 722.8, "up": 33.6}, {"vy": 823.6, "up": 45.8}]



def test_narrow_pulls_toward_the_view_center():
    from model3d.blender_adapter import face_shape as fs
    settings = {"view": {"from": "-Y", "center": [0.3, 1.5], "span": 0.32, "size": [1024, 1024]}, "smooth": 4.0, "narrow": 0.2,
                "lift": [{"vy": 500.0, "up": 0.0}, {"vy": 700.0, "up": 20.0}], "depth": {"front": -0.1, "back": 0.0}}
    world = np.array([[0.25, -0.2, 1.42], [0.35, -0.2, 1.42], [0.3, -0.2, 1.42]])   # 顔の中心（x 0.3）の左右と中心、あごの高さ
    moved = fs.shaped(world, settings)
    assert moved[0, 0] > 0.25 and moved[1, 0] < 0.35                                  # 左右とも中心へ寄る
    assert moved[0, 0] - 0.3 == pytest.approx(-(moved[1, 0] - 0.3)) and moved[2, 0] == pytest.approx(0.3)

def test_lift_curve_is_smooth_and_keeps_the_order():
    grid, lift = fs.lift_curve(LIFT, 6)
    assert grid[0] == 452 and grid[-1] == 1023          # 1 画素おき（台本と同じ範囲: 最初の vy − 120 〜 最後の vy + 200）
    assert lift[grid < 540].max() == pytest.approx(0.0, abs=1e-9)          # 動かし始めより上は動かさない
    assert np.interp(723, grid, lift) == pytest.approx(33.6, abs=0.5)       # 角を丸めても、表の値の近く
    with pytest.raises(ValueError, match="入れ替わる"):
        fs.lift_curve([{"vy": 500, "up": 0}, {"vy": 510, "up": 40}], 1)     # 10 画素の間で 40 画素上げると、上下が入れ替わる


def test_lower_face_moves_up_only_in_front():
    pixel = 0.32 / 1024
    z_mouth = 1.5 - (722.8 - 512) * pixel                                    # 口の高さ（vy 722.8）
    world = np.array([[0.0, -0.05, z_mouth], [0.0, 0.05, z_mouth], [0.0, -0.05, 1.5 + 0.05]])   # 顔の前・耳の後ろ・額
    settings = {"view": VIEW, "lift": LIFT, "smooth": 6, "depth": {"front": -0.03, "back": 0.02}}
    moved = fs.shaped(world, settings)
    up = (moved[:, 2] - world[:, 2]) / pixel
    assert up[0] == pytest.approx(33.6, abs=0.6) and up[1] == 0 and up[2] == pytest.approx(0.0, abs=1e-9)
    narrow = fs.shaped(world + [0.03, 0, 0], {**settings, "narrow": 0.06})
    assert narrow[0, 0] < 0.03 and narrow[1, 0] == pytest.approx(0.03)      # 正面だけ、上げた量に比例して細く


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
moved = 0 if settings["head"] == "Still" else 120
json.dump({"schema_version": "model3d-face-shape/0.1", "head": settings["head"], "vertices": 300, "moved_over_1mm": moved,
           "max_up_mm": 14.3, "x_settings": settings}, open(value("--report"), "w", encoding="utf-8"))
'''


def run_recipe(tmp_path, parameters):
    from model3d import executor
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "face",
                                           "inputs": {"model": {"path": "model.blend"}},
                                           "steps": [{"id": "face", "operation": "model.face_shape", "inputs": {"blend": "$inputs.model"},
                                                      "parameters": parameters, "outputs": {"blend": "face.blend", "report": "face.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_face_shape_step(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    parameters = {"blender": blender, "head": "Head", "view": VIEW, "lift": LIFT, "smooth": 6, "depth": {"front": -0.03, "back": 0.02},
                  "narrow": 0.06}
    result = run_recipe(tmp_path, parameters)
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["face"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["lift"] == LIFT and report["x_settings"]["smooth"] == 6 and report["x_settings"]["narrow"] == 0.06
    still = run_recipe(tmp_path / "still", {**parameters, "head": "Still"})
    assert still["status"] == "needs_review"                                   # 何も動かなければ要確認


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"view": {**VIEW, "from": "-X"}}, "正面"),
    ({"view": {**VIEW, "span": 0}}, "span"),
    ({"lift": []}, "lift"),
    ({"lift": [{"vy": 600, "up": 10}, {"vy": 600, "up": 20}]}, "重なって"),
    ({"lift": [{"vy": 500, "up": 0}, {"vy": 510, "up": 40}], "smooth": 1}, "入れ替わる"),
    ({"depth": {"front": 0.02, "back": -0.03}}, "手前"),
    ({"narrow": 0.9}, "narrow"),
    ({"head": ""}, "head"),
])
def test_face_shape_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    base = {"blender": str(blender), "head": "Head", "view": VIEW, "lift": LIFT, "depth": {"front": -0.03, "back": 0.02}}
    with pytest.raises(ToolError, match=message):
        get("model.face_shape").function({"blend": tmp_path / "model.blend"}, {**base, **parameters},
                                         {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
