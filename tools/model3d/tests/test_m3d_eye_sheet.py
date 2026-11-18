"""目の確かめの絵（render.eye_sheet）: 視線から骨の回し方・キーの名前の広げ方・見る向き・絵の並べ方（Blender なし）、工程の受け渡し
（代役の Blender）と悪い設定を止めること。本物の Blender 5.2.4 での照合は docs/model3d.md（CI には Blender が無い）。例の値は、どの頭とも
関係の無い値。"""
import json
import math
from pathlib import Path

import pytest
from PIL import Image

from m3d_helpers import stand_in_executable
from model3d import eye_sheet
from model3d.blender_adapter import eye_math
from model3d.blender_adapter import eye_sheet as drawer
from model3d.blender_adapter.face_sheet import image_names
from model3d.io import atomic_json


def test_gaze_turns_the_eye_bones_by_slide_over_radius():
    assert drawer.gaze_rotation([0.005, -0.002], 0.1) == pytest.approx((-0.02, 0.0, 0.05))   # たては X、横は Z（試しと同じ）
    names = {"blink_L", "blink_R", "brow_up_L", "brow_up_R", "mouth"}
    assert drawer.key_targets({"blink": 0.5, "brow_up": 1.0, "mouth": 0.3, "nothing": 1.0}, names) == {
        "blink_L": 0.5, "blink_R": 0.5, "brow_up_L": 1.0, "brow_up_R": 1.0, "mouth": 0.3}
    assert drawer.view_direction(0, 0) == pytest.approx((0.0, -1.0, 0.0))                     # 正面（手前の −y）から
    assert drawer.view_direction(90, 0) == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)          # キャラの左側から
    assert drawer.view_direction(0, 30) == pytest.approx((0.0, -math.cos(math.radians(30)), 0.5))


def test_drawing_uses_the_armature_scene_and_relative_keys():
    from model3d.blender_adapter import eye_keys, vrm_apply_motion
    assert drawer.use_scene_of is vrm_apply_motion.use_scene_of               # 作業用のシーンを開いたまま保存されたファイルでも、頭のあるシーンで描く
    assert drawer.absolute_keys_problem is eye_keys.absolute_keys_problem      # 絶対の形状キーは値で姿勢を作れないので止める（目のキーと同じ）


def test_drawing_prepares_keys_and_render_settings_like_face_sheet():
    from model3d.blender_adapter import face_sheet
    for name in ("key_changes", "setting_changes", "hidden_paths", "RENDER_FIXED", "VIEW_FIXED", "DISPLAY_FIXED", "IMAGE_FIXED"):
        assert getattr(drawer, name) is getattr(face_sheet, name)             # 入れる値が効くように・絵がすり替わらないように（顔の絵と同じ）
    assert drawer.RENDER_FIXED["use_sequencer"] is False and drawer.RENDER_FIXED["use_compositing"] is False
    # 範囲の外の値（1.5）と 0 に戻すことが効くように範囲を広げ、ミュートしたキーは外す
    combos = [("wide", drawer.key_targets({"eye_wide": 1.5}, {"eye_wide_L", "eye_wide_R"}))]
    unmute, widen = drawer.key_changes(combos, {"eye_wide_L": (True, 0.0, 1.0), "eye_wide_R": (False, 0.2, 1.0)})
    assert unmute == ["eye_wide_L"] and widen == {"eye_wide_L": (0.0, 1.5), "eye_wide_R": (0.0, 1.5)}


def _images(folder, poses, views, size=(96, 54)):
    for (pose, view), name in image_names(poses, views).items():
        Image.new("RGB", size, (40 * len(pose) % 255, 30 * len(view) % 255, 90)).save(folder / name)


def test_join_keeps_the_picture_ratio(tmp_path):
    _images(tmp_path, ["rest", "blink"], ["front", "side", "below"])
    size = eye_sheet.join(tmp_path, ["rest", "blink"], ["front", "side", "below"], tmp_path / "sheet.png", cell=48)
    assert size == (48 * 3, (27 + eye_sheet.STRIP) * 2)                                     # こまの縦は絵と同じ 16:9
    sheet = Image.open(tmp_path / "sheet.png").convert("RGB")
    assert sheet.getpixel((48 + 20, eye_sheet.STRIP + 10)) != eye_sheet.BACKGROUND           # 2 列目の絵が入っている
    with pytest.raises(ValueError, match="ありません"):
        eye_sheet.join(tmp_path, ["rest", "wide"], ["front"], tmp_path / "x.png", cell=48)


def _geometry(plate=True):
    def eye(sign):
        out = {"sphere": {"centre": [0.04 * sign, 0.1, 1.3], "radius": 0.1, "normal": [0.0, -1.0, 0.0]}, "pupil": [0.04 * sign, 1.3],
               "iris": {"a": 0.006, "b": 0.005}, "rim": {"x": [0.04 * sign - 0.01, 0.04 * sign + 0.01], "upper": [1.305, 1.305],
                                                      "lower": [1.295, 1.295]},
               "window": [0.04 * sign - 0.02, 0.04 * sign + 0.02, 1.28, 1.32], "iris_vertices": [1, 2] if sign < 0 else [3, 4]}
        if plate:
            out["plate_vertices"] = [5, 6] if sign < 0 else [7, 8]
        return out
    return {"schema_version": eye_math.SCHEMA, "object": "Head", "eyes": {"R": eye(-1.0), "L": eye(1.0)}}


STAND_IN = r'''
import json, os, sys
from PIL import Image
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
folder = value("--out-dir")
os.makedirs(folder, exist_ok=True)
images = []
for pose in settings["poses"]:
    for view in settings["views"]:
        name = "%s_%s.png" % (pose["name"], view["name"])
        Image.new("RGB", tuple(settings["size"]), (200, 120, 60)).save(os.path.join(folder, name))
        images.append({"pose": pose["name"], "view": view["name"], "file": name})
json.dump({"blender": "5.2.4", "object": settings["object"], "images": images, "unknown_keys": {}, "painted_faces": {"plate": 4} if settings["paint"] else {},
           "x_settings": settings, "x_argv": sys.argv[1:]}, open(value("--report"), "w", encoding="utf-8"))
'''

POSES = [{"name": "rest"}, {"name": "lookL", "gaze": [0.004, 0.0]}, {"name": "blink", "keys": {"blink": 1.0}}]
VIEWS = [{"name": "front", "yaw": 0, "pitch": 0, "target": [0.0, -0.05, 1.3], "span": 0.12},
         {"name": "y+60", "yaw": 60, "pitch": 0, "target": [0.03, -0.04, 1.3], "span": 0.1, "distance": 0.6}]


def _recipe(tmp_path, parameters, data=None, sheet="sheet.jpg"):
    (tmp_path / "head.blend").write_bytes(b"blend")
    atomic_json(tmp_path / "eyes.json", data or _geometry())
    step = {"id": "sheet", "operation": "render.eye_sheet", "inputs": {"blend": "$inputs.head", "geometry": "$inputs.eyes"},
            "parameters": parameters, "outputs": {"sheet": sheet, "report": "sheet.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "sheet",
                                           "inputs": {"head": {"path": "head.blend"}, "eyes": {"path": "eyes.json"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_eye_sheet_step_draws_and_joins(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "poses": POSES, "views": VIEWS, "size": [96, 54], "cell": 48,
                                                    "paint": True}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["sheet"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    settings = report["x_settings"]
    assert settings["radius"] == 0.1 and settings["paint"] == {"plate": [1.0, 0.0, 1.0], "iris": [0.0, 1.0, 0.0]}
    assert settings["plate_vertices"] == {"R": [5, 6], "L": [7, 8]} and settings["iris_vertices"] == {"R": [1, 2], "L": [3, 4]}
    assert [p["gaze"] for p in settings["poses"]] == [[0.0, 0.0], [0.004, 0.0], [0.0, 0.0]] and settings["views"][0]["distance"] == 0.5
    assert settings["aa"] == "8" and settings["background"] == [0.40, 0.39, 0.38]
    assert report["schema_version"] == "model3d-eye-sheet/0.1" and report["sheet"]["size"] == [96, (27 + eye_sheet.STRIP) * 3]
    assert step["observations"][0]["images"] == 6 and Image.open(step["artifacts"]["sheet"]).size == (96, (27 + eye_sheet.STRIP) * 3)
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "eye_sheet.py"


@pytest.mark.parametrize("parameters, data, sheet, message", [
    ({"views": VIEWS}, None, "sheet.jpg", "poses"), ({"poses": POSES}, None, "sheet.jpg", "views"),
    ({"poses": [{"name": "a", "gaze": [0.1]}], "views": VIEWS}, None, "sheet.jpg", "gaze"),
    ({"poses": [{"name": "a", "keys": {"blink": 20}}], "views": VIEWS}, None, "sheet.jpg", "-10〜10"),
    ({"poses": [{"name": "a b"}], "views": VIEWS}, None, "sheet.jpg", "poses[0].name"),
    ({"poses": [{"name": "a"}, {"name": "a"}], "views": VIEWS}, None, "sheet.jpg", "重なって"),
    ({"poses": [{"name": "smile_left"}, {"name": "smile"}], "views": [{**VIEWS[0], "name": "front"}, {**VIEWS[0], "name": "left_front"}]},
     None, "sheet.jpg", "重なります"),
    ({"poses": POSES, "views": [{**VIEWS[0], "pitch": 90}]}, None, "sheet.jpg", "pitch"),
    ({"poses": POSES, "views": [{**VIEWS[0], "target": [0, 0]}]}, None, "sheet.jpg", "target"),
    ({"poses": POSES, "views": VIEWS, "paint": True}, _geometry(plate=False), "sheet.jpg", "plate_vertices"),
    ({"poses": POSES, "views": VIEWS, "paint": {"plate": [1, 0, 1]}}, None, "sheet.jpg", "paint"),
    ({"poses": POSES, "views": VIEWS, "size": [8, 8]}, None, "sheet.jpg", "size"), ({"poses": POSES, "views": VIEWS, "aa": "4"}, None, "sheet.jpg", "aa"),
    ({"poses": POSES, "views": VIEWS, "hide": ["Head"]}, None, "sheet.jpg", "hide"),
    ({"poses": POSES, "views": VIEWS, "spin": 1}, None, "sheet.jpg", "知らないパラメーター"),
    ({"poses": POSES, "views": VIEWS}, None, "sheet.gif", ".jpg か .png")])
def test_eye_sheet_refuses_bad_settings(tmp_path, parameters, data, sheet, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}, data, sheet), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["sheet"]["error"]
