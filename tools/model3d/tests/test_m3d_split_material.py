"""部品の中の面を別の材質に分ける（model.split_material）: 面の選び方（箱・色の絵の色・箱だけ）と画素の引き方（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import split_material as sm
from model3d.io import ToolError, atomic_json


def test_boxes_leave_open_ends_and_exclude_the_edges():
    points = np.array([[0.0, -0.2, 1.5], [0.1, -0.2, 1.5], [0.0, 0.1, 1.5], [0.0, -0.2, 2.0], [0.05, -0.2, 1.5]])
    box = {"x": [-0.05, 0.05], "y": [None, -0.08], "z": [1.4, 1.6]}
    assert sm.in_box(points, box).tolist() == [True, False, False, False, False]   # 端（0.05）は入らない
    assert sm.in_box(points, {"z": [None, 1.7]}).tolist() == [True, True, True, False, True]


def test_the_colour_of_the_face_is_read_at_the_uv_centre_rows_from_the_bottom():
    pixels = np.zeros((4, 4, 4), dtype=np.float32)
    pixels[0, 3, :3] = [0.9, 0.6, 0.5]                                              # 下の行（Blender の並び）の右端
    colors = sm.face_pixels(np.array([[0.99, 0.01], [0.5, 0.5], [1.2, -0.3]]), pixels)
    assert colors[0].tolist() == pytest.approx([0.9, 0.6, 0.5]) and colors[1].tolist() == [0, 0, 0]
    assert colors[2].tolist() == pytest.approx([0.9, 0.6, 0.5])                    # 外は端の画素
    rule = {"rgb_min": [0.5, 0.28, 0.26], "red_at_least_blue": True, "red_at_least_green": True}
    sample = np.array([[0.9, 0.6, 0.5], [0.6, 0.7, 0.5], [0.6, 0.5, 0.65], [0.4, 0.3, 0.3]])
    assert sm.color_match(sample, rule).tolist() == [True, False, False, False]
    assert sm.color_match(sample, {"rgb_min": [0.5, 0.28, 0.26]}).tolist() == [True, True, True, False]


def test_faces_are_chosen_by_colour_or_by_box_inside_the_region():
    centres = np.array([[0.0, -0.2, 1.5], [0.0, -0.15, 1.55], [0.0, -0.15, 1.55], [0.3, -0.2, 1.5]])
    colors = np.array([[0.9, 0.6, 0.5], [0.1, 0.1, 0.1], [0.1, 0.1, 0.1], [0.9, 0.6, 0.5]])
    settings = {"region": {"x": [-0.1, 0.1]}, "color": {"rgb_min": [0.5, 0.28, 0.26]},
                "boxes": [{"y": [-0.17, -0.12], "z": [1.5, 1.6]}]}
    colors[2] = [0.1, 0.1, 0.1]
    centres[2] = [0.0, -0.3, 1.55]                                                  # 箱より手前（前髪）
    chosen, by_color, by_box_only = sm.choose(centres, colors, settings)
    assert chosen.tolist() == [True, True, False, False]                            # 箱 region の外（x 0.3）は肌の色でも選ばない
    assert by_color.tolist() == [True, False, False, False] and by_box_only.tolist() == [False, True, False, False]
    assert sm.choose(centres, colors, {"region": {"x": [-0.1, 0.1]}, "boxes": settings["boxes"]})[0].tolist() == [False, True, False, False]


def test_a_mesh_shared_with_other_objects_is_found():
    from types import SimpleNamespace as Thing
    mesh, other_mesh = object(), object()
    body = Thing(name="head", data=mesh)
    objects = [body, Thing(name="head.001", data=mesh), Thing(name="hair", data=other_mesh), Thing(name="Camera", data=None),
               Thing(name="head.002", data=mesh)]
    assert sm.shared_with(body, objects) == ["head.001", "head.002"]           # リンクの複製（同じメッシュを使う物体）
    assert sm.shared_with(objects[2], objects) == []                               # 自分だけが使うメッシュ


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
if settings["object"] == "linked":                                           # 本物の台本と同じく、分け合うメッシュは止める
    raise SystemExit("linked のメッシュ linked_mesh を、ほかの物体（linked.001）も使っています")
shutil.copyfile(value("--blend"), value("--out-blend"))
moved = 0 if settings["material"] == "none" else 12
json.dump({"x_settings": settings, "object": settings["object"], "faces": 40, "chosen_faces": moved, "by_color": moved, "by_box_only": 0,
           "moved_faces": moved, "material": settings["material"]}, open(value("--report"), "w", encoding="utf-8"))
'''

FACE = {"object": "head", "material": "head_face", "region": {"x": [-0.1, 0.1], "z": [1.4, 1.6], "y": [None, -0.08]},
        "color": {"rgb_min": [0.5, 0.28, 0.26], "red_at_least_blue": True}, "boxes": [{"x": [-0.05, 0.0], "y": [-0.17, -0.12]}]}


def run(tmp_path, parameters):
    from model3d import executor
    (tmp_path / "model.blend").write_bytes(b"model")
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "face", "inputs": {"model": {"path": "model.blend"}},
                                           "steps": [{"id": "face", "operation": "model.split_material", "inputs": {"blend": "$inputs.model"},
                                                      "parameters": {"blender": blender, **parameters},
                                                      "outputs": {"blend": "face.blend", "report": "face.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_split_material_step_passes_the_settings(tmp_path):
    result = run(tmp_path, FACE)
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["face"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"]["source_slot"] == 0 and report["x_settings"]["boxes"] == FACE["boxes"]
    assert result["steps"]["face"]["observations"][0]["moved_faces"] == 12


def test_split_material_with_nothing_moved_needs_review(tmp_path):
    result = run(tmp_path, {**FACE, "material": "none"})
    assert result["status"] == "needs_review" and "分けた面がありません" in result["steps"]["face"]["unresolved"][0]


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({**FACE, "object": ""}, "object"),
    ({**FACE, "region": {}}, "region"),
    ({**FACE, "region": {"x": [0.1, -0.1]}}, "region.x"),
    ({**FACE, "region": {"w": [0, 1]}}, "知らない設定"),
    ({**FACE, "color": {"rgb_min": [0.5, 2, 0.2]}}, "rgb_min"),
    ({**FACE, "color": None, "boxes": None}, "少なくとも"),
    ({**FACE, "color": None, "image": "face"}, "image"),
    ({**FACE, "source_slot": -1}, "source_slot"),
])
def test_split_material_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("model.split_material").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                             {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))


def test_split_material_stops_on_a_shared_mesh(tmp_path):
    result = run(tmp_path, {**FACE, "object": "linked"})
    assert result["status"] == "failed" and "linked.001" in result["steps"]["face"]["error"]
