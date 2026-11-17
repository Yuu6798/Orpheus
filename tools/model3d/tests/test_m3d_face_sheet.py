"""表情の一覧の絵（render.face_sheet）: カメラの向き・並べ方（Blender なし）と、工程の受け渡しと悪い設定（代役の Blender）。
本物の Blender 5.2.4 で描いた絵の突き合わせは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from m3d_helpers import stand_in_executable
from model3d import face_sheet
from model3d.blender_adapter import face_sheet as render_side
from model3d.io import ToolError, atomic_json


def test_camera_offset_turns_to_the_characters_left_and_up():
    assert render_side.camera_offset(0, 0) == pytest.approx((0.0, -1.0, 0.0))          # 正面（キャラは −Y を向く）
    assert render_side.camera_offset(90, 0) == pytest.approx((1.0, 0.0, 0.0), abs=1e-12)   # 正の方位はキャラの左（+x）へ
    assert render_side.camera_offset(0, 90) == pytest.approx((0.0, 0.0, 1.0), abs=1e-12)   # 上から
    assert np.linalg.norm(render_side.camera_offset(30, -20)) == pytest.approx(1.0)
    assert render_side.image_name("happy", "q30") == "happy_q30.png"


def test_image_names_refuse_combinations_that_share_a_file():
    names = render_side.image_names(["neutral", "blink"], ["front", "q30"])
    assert len(names) == 4 and names[("blink", "q30")] == "blink_q30.png"
    with pytest.raises(ValueError, match="重なります"):
        render_side.image_names(["smile_left", "smile"], ["front", "left_front"])     # どちらも smile_left_front.png
    with pytest.raises(ValueError, match="重なります"):
        render_side.image_names(["Smile", "smile"], ["front"])                         # Windows では同じファイル


def test_absolute_shape_keys_are_refused():
    assert render_side.absolute_keys_problem("Head", True) is None                     # 相対の形状キー（いつもの）はそのまま
    assert "絶対" in render_side.absolute_keys_problem("Head", False)                   # 時間で形を選ぶので、値で組み合わせを描けない


def test_key_changes_make_the_requested_values_take_effect():
    states = {"blink": (True, 0.0, 1.0), "smile": (False, 0.0, 1.0), "brow": (False, 0.2, 1.0), "sad": (True, 0.0, 1.0)}
    unmute, widen = render_side.key_changes([["a", {"blink": 1.0}], ["b", {"smile": 1.5, "brow": 0.5}]], states)
    assert unmute == ["blink"]                                         # 入れないキー（sad）は 0 にするだけなので、ミュートのまま
    assert widen == {"smile": (0.0, 1.5), "brow": (0.0, 1.0)}          # 範囲の外の値（1.5）と、0 にできないキー（下の端 0.2）
    assert render_side.key_changes([["n", {}]], {"x": (False, 0.0, 1.0)}) == ([], {})


def test_render_settings_that_could_replace_the_picture_are_fixed():
    current = {**render_side.RENDER_FIXED, "use_sequencer": True, "pixel_aspect_x": 2.0}
    assert render_side.setting_changes(current, render_side.RENDER_FIXED) == {"use_sequencer": [True, False], "pixel_aspect_x": [2.0, 1.0]}
    assert render_side.setting_changes(dict(render_side.VIEW_FIXED), render_side.VIEW_FIXED) == {}   # 同じなら変えない
    assert {"use_sequencer", "use_compositing", "use_border", "film_transparent"} <= set(render_side.RENDER_FIXED)
    assert render_side.IMAGE_FIXED["color_mode"] == "RGBA" and list(render_side.IMAGE_FIXED)[0] == "file_format"   # 白黒にしない。形式を先に


class _Layer:
    def __init__(self, name, objects=(), exclude=False, hide_render=False, children=()):
        self.name, self.exclude, self.children = name, exclude, list(children)
        self.collection = type("Collection", (), {"hide_render": hide_render, "objects": list(objects)})()


def test_hidden_paths_find_the_collections_that_keep_the_head_from_rendering():
    shown = _Layer("Scene Collection", children=[_Layer("face", ["Head"]), _Layer("props", ["Hat"], hide_render=True)])
    assert render_side.hidden_paths(shown, "Head") == [[]]                                # 描かれる道がある
    hidden = _Layer("Scene Collection", children=[_Layer("rig", hide_render=True, children=[_Layer("face", ["Head"])]),
                                                  _Layer("spare", ["Head"], exclude=True)])
    assert render_side.hidden_paths(hidden, "Head") == [["rig"], ["spare"]]                # どの道にも描かない設定がある
    assert render_side.hidden_paths(shown, "Body") == []                                   # シーンに無い


def test_view_lengths_in_metres_become_blender_units():
    view = {"name": "q30", "azimuth": 30, "elevation": 0, "scale": 0.17, "centre": [0.0, -0.05, 1.49]}
    assert render_side.view_in_units(view) == ([0.0, -0.05, 1.49], 1.0, 0.17)           # 1 Blender 単位 = 1 m なら値はそのまま
    centre, distance, scale = render_side.view_in_units(view, 0.01)                      # 1 Blender 単位 = 1 cm のシーン
    assert centre == pytest.approx([0.0, -5.0, 149.0]) and distance == pytest.approx(100.0) and scale == pytest.approx(17.0)
    assert render_side.view_in_units({**view, "centre": None}, 0.01)[0] is None            # 狙う点は箱の中心（もとから Blender 単位）


def _images(folder, labels, views, size=(40, 36)):
    folder.mkdir(parents=True, exist_ok=True)
    for r, label in enumerate(labels):
        for c, view in enumerate(views):
            Image.new("RGBA", size, (40 * r + 30, 60 * c + 50, 200, 255)).save(folder / f"{label}_{view}.png")


def test_join_places_expressions_in_rows_and_views_in_columns(tmp_path):
    _images(tmp_path / "views", ["neutral", "blink"], ["front", "q30", "q60"])
    size = face_sheet.join(tmp_path / "views", ["neutral", "blink"], ["front", "q30", "q60"], tmp_path / "sheet.png", cell=100)
    assert size == (300, 180)                                                           # こまは cell × int(cell × 0.9)
    sheet = np.asarray(Image.open(tmp_path / "sheet.png").convert("RGB"))
    assert sheet[150, 250].tolist() == [70, 170, 200]                                   # 2 行目（blink）・3 列目（q60）のこまの真ん中
    assert sheet[50, 150].tolist() == [30, 110, 200]
    assert sheet[5, 5].tolist() != [30, 50, 200] and sheet[5, 105].tolist() == [30, 110, 200]   # 札は左の列にだけ
    face_sheet.join(tmp_path / "views", ["neutral", "blink"], ["front", "q30", "q60"], tmp_path / "sheet.jpg", cell=100, quality=80)
    assert Image.open(tmp_path / "sheet.jpg").format == "JPEG"
    with pytest.raises(ValueError, match="絵がありません"):
        face_sheet.join(tmp_path / "views", ["neutral", "wink"], ["front"], tmp_path / "x.png", cell=100)
    with pytest.raises(ValueError, match="重なります"):                                    # 2 つのこまに同じ絵を入れない
        face_sheet.join(tmp_path / "views", ["a_b", "a"], ["c", "b_c"], tmp_path / "y.png", cell=100)


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
os.makedirs(value("--out-dir"), exist_ok=True)
images = []
size = settings["size"]
for label, values in settings["combos"]:
    for view in settings["views"]:
        name = f"{label}_{view['name']}.png"
        Image.new("RGBA", (size, int(size * 0.9)), (int(255 * sum(values.values())) % 256, 80, 120, 255)).save(os.path.join(value("--out-dir"), name))
        images.append({"label": label, "view": view["name"], "file": name})
json.dump({"schema_version": "model3d-face-sheet/0.1", "object": settings["object"], "images": images, "settings": settings},
          open(value("--report"), "w"))
'''


def _recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "face.blend").write_bytes(b"face")
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "sheet", "inputs": {"model": {"path": "face.blend"}},
        "steps": [{"id": "sheet", "operation": "render.face_sheet", "inputs": {"blend": "$inputs.model"},
                   "parameters": {"blender": blender, **parameters}, "outputs": outputs or {"sheet": "sheet.jpg", "report": "sheet.json"}}]})
    return tmp_path / "recipe.json"


PARAMETERS = {"object": "Head", "combos": [["neutral", {}], ["blink", {"blink_L": 1.0, "blink_R": 1.0}]],
              "views": [{"name": "front", "azimuth": 0, "elevation": 0, "scale": 0.17}, {"name": "q30", "azimuth": 30, "elevation": 0,
                                                                                          "scale": 0.17, "centre": [0, -0.05, 1.49]}],
              "hide": ["Hair"], "size": 60}


def test_face_sheet_renders_every_combination_and_joins_them(tmp_path):
    from model3d import executor
    result = executor.run_recipe(_recipe(tmp_path, PARAMETERS), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["sheet"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert [(i["label"], i["view"]) for i in report["images"]] == [("neutral", "front"), ("neutral", "q30"), ("blink", "front"), ("blink", "q30")]
    assert report["settings"]["hide"] == ["Hair"] and report["settings"]["views"][1]["centre"] == [0.0, -0.05, 1.49]
    assert report["settings"]["background"] == [0.18, 0.18, 0.2] and report["settings"]["samples"] == 16    # 既定
    assert report["sheet"] == {"size": [640, 576], "cell": 320, "font": None, "font_size": 16, "quality": 90}
    assert Image.open(step["artifacts"]["sheet"]).size == (640, 576)
    assert step["observations"][0]["images"] == 4 and step["reused_from"] is None


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


def _with(**changes):
    return {**PARAMETERS, **changes}


@pytest.mark.parametrize("parameters, message", [
    (_with(object=""), "object"),
    (_with(combos=[]), "combos"),
    (_with(combos=[["a b", {}]]), "札"),
    (_with(combos=[["neutral", {}], ["neutral", {"blink_L": 1}]]), "重なって"),
    (_with(combos=[["neutral", {"blink_L": "x"}]]), "有限の数"),
    (_with(combos=[["neutral", {"blink_L": 11}]]), "10.0 以下"),
    (_with(views=[{"name": "front", "azimuth": 0, "elevation": 95, "scale": 0.17}]), "elevation"),
    (_with(views=[{"name": "front", "azimuth": 0, "elevation": 0, "scale": 0}]), "scale"),
    (_with(views=[{"name": "front", "azimuth": 0, "elevation": 0, "scale": 0.1, "roll": 3}]), "知らない設定"),
    (_with(views=[{"name": "f", "azimuth": 0, "elevation": 0, "scale": 0.1}, {"name": "f", "azimuth": 9, "elevation": 0, "scale": 0.1}]), "重なって"),
    (_with(views=[{"name": "f", "azimuth": 0, "elevation": 0, "scale": 0.1, "centre": [0, 0]}]), "centre"),
    (_with(combos=[["smile_left", {}], ["smile", {}]], views=[{"name": "front", "azimuth": 0, "elevation": 0, "scale": 0.1},
                                                             {"name": "left_front", "azimuth": 30, "elevation": 0, "scale": 0.1}]), "重なります"),
    (_with(combos=[["Smile", {}], ["smile", {}]]), "重なります"),
    (_with(background=[0.1, 0.2]), "background"),
    (_with(size=8000), "4096"),
    (_with(font="no_such_font.ttf"), "書体"),
    (_with(quality=0), "quality"),
    (_with(hide="Hair"), "hide"),
    (_with(hide=["Hair", "Head"]), "描く頭が消える"),
])
def test_face_sheet_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "face.blend").write_bytes(b"face")
    with pytest.raises(ToolError, match=message):
        get("render.face_sheet").function({"blend": tmp_path / "face.blend"}, {"blender": str(blender), **parameters},
                                          {"sheet": tmp_path / "s.jpg", "report": tmp_path / "r.json"}, Context(tmp_path))


def test_face_sheet_needs_an_image_output(tmp_path):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "face.blend").write_bytes(b"face")
    with pytest.raises(ToolError, match=".jpg か .png"):
        get("render.face_sheet").function({"blend": tmp_path / "face.blend"}, {"blender": str(blender), **PARAMETERS},
                                          {"sheet": tmp_path / "s.gif", "report": tmp_path / "r.json"}, Context(tmp_path))
