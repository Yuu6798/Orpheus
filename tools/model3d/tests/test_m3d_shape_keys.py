"""形状キーを入れる（model.shape_keys）と VRM の表情に割り当てる（vrm.expressions）: 形の読み方・物体の中の座標への移し方・表情の名前と
割り当ての確かめ（Blender なし）と、工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 と VRM Add-on 4.10.5 での確かめは
docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import shape_keys, vrm_expressions
from model3d.io import ToolError, atomic_json


def test_read_shapes_keeps_the_order_and_checks_each_shape(tmp_path):
    path = tmp_path / "shapes.npz"
    np.savez(path, blink_R=np.zeros((4, 3)), blink_L=np.ones((4, 3), dtype=np.float32), mouth_smile=np.zeros((4, 3)))
    shapes = shape_keys.read_shapes(path, 4)
    assert [name for name, _ in shapes] == ["blink_R", "blink_L", "mouth_smile"] and shapes[1][1].dtype == np.float64
    with pytest.raises(ValueError, match="頂点の数"):
        shape_keys.read_shapes(path, 5)
    np.savez(path, bad=np.zeros((4, 2)))
    with pytest.raises(ValueError, match=r"\(N, 3\)"):
        shape_keys.read_shapes(path, 4)
    np.savez(path, bad=np.full((4, 3), np.nan))
    with pytest.raises(ValueError, match="非有限"):
        shape_keys.read_shapes(path, 4)
    np.savez(path, bad=np.zeros((4, 3), dtype=np.int64))
    with pytest.raises(ValueError, match=r"\(N, 3\)"):
        shape_keys.read_shapes(path, 4)
    np.savez(path)
    with pytest.raises(ValueError, match="形がありません"):
        shape_keys.read_shapes(path, 4)


def test_shape_coordinates_move_world_deltas_into_the_object():
    matrix = np.array([[0.5, 0, 0, 1], [0, 0, -0.5, 0], [0, 0.5, 0, 3], [0, 0, 0, 1]], dtype=np.float32)   # 半分・x のまわりに 90°・ずらし
    base = np.array([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]])
    delta = np.array([[0.0, 0.0, 0.01], [0.01, 0.0, 0.0]])                                   # 世界で上へ 1 cm、右へ 1 cm
    moved = shape_keys.shape_coordinates(base, delta, matrix)
    world = lambda p: p @ matrix[:3, :3].T.astype(float) + matrix[:3, 3]
    assert world(moved) - world(base) == pytest.approx(delta, abs=1e-9)                       # 世界で見ると、頼んだ動きだけ動く
    scaled = shape_keys.shape_coordinates(base, delta, matrix, unit=0.01)                     # 1 単位 = 1 cm のシーン
    assert scaled - base == pytest.approx((moved - base) * 100, abs=1e-9)


def test_a_shape_named_like_the_reference_key_is_refused():
    assert shape_keys.reference_problem(["blink_L", "blink_R"], "Basis") is None
    assert "基準のキー" in shape_keys.reference_problem(["Basis", "blink_L"], "Basis")   # 入れ直すと基準のキーを消してしまう
    assert shape_keys.reference_problem(["Base"], "Base")


def test_presets_are_the_vrm_1_expressions():
    assert set(vrm_expressions.PRESETS) == {"happy", "angry", "sad", "relaxed", "surprised", "aa", "ih", "ou", "ee", "oh", "blink",
                                            "blinkLeft", "blinkRight", "lookUp", "lookDown", "lookLeft", "lookRight", "neutral"}
    assert vrm_expressions.PRESETS["blinkLeft"] == "blink_left" and vrm_expressions.PRESETS["lookDown"] == "look_down"
    assert vrm_expressions.missing_keys({"happy": {"smile": 1.0, "wink": 0.5}, "blink": {}}, {"smile"}) == {"happy": ["wink"]}


SHAPE_STAND_IN = r'''
import json, os, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"with keys")
json.dump({"schema_version": "model3d-shape-keys/0.1", "object": settings["object"], "keys": [{"name": "blink_R", "replaced": False},
           {"name": "blink_L", "replaced": settings["replace"]}], "settings": settings, "argv": sys.argv[1:]}, open(value("--report"), "w"))
'''

BINDS_STAND_IN = r'''
import json, os, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"with binds")
json.dump({"schema_version": "model3d-vrm-expressions/0.1", "object": settings["object"], "changed": sorted(settings["binds"]),
           "bound": sorted(k for k, v in settings["binds"].items() if v), "name_only": ["lookUp"], "settings": settings,
           "argv": sys.argv[1:]}, open(value("--report"), "w"))
'''


def _recipe(tmp_path, steps):
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    np.savez(tmp_path / "shapes.npz", blink_R=np.zeros((4, 3)), blink_L=np.zeros((4, 3)))
    folder = tmp_path / "ext" / "user_default" / "vrm"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "blender_manifest.toml").write_text('version = "4.10.5"\n', encoding="utf-8")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "face-keys",
                                           "inputs": {"avatar": {"path": "avatar.blend"}, "shapes": {"path": "shapes.npz"}}, "steps": steps})
    return tmp_path / "recipe.json"


def test_shape_keys_then_expressions_pass_their_settings_to_blender(tmp_path):
    from model3d import executor
    keys_blender = str(stand_in_executable(tmp_path, "blender_keys", SHAPE_STAND_IN))
    binds_blender = str(stand_in_executable(tmp_path, "blender_binds", BINDS_STAND_IN))
    binds = {"blink": {"blink_L": 1.0, "blink_R": 1}, "blinkLeft": {"blink_L": 1.0}, "aa": {}}
    steps = [{"id": "keys", "operation": "model.shape_keys", "inputs": {"blend": "$inputs.avatar", "shapes": "$inputs.shapes"},
              "parameters": {"blender": keys_blender, "object": "Head", "replace": True}, "outputs": {"blend": "keys.blend", "report": "keys.json"}},
             {"id": "binds", "operation": "vrm.expressions", "inputs": {"blend": "$steps.keys.blend"},
              "parameters": {"blender": binds_blender, "blender_extensions": "ext", "object": "Head", "binds": binds},
              "outputs": {"blend": "face.blend", "report": "binds.json"}}]
    result = executor.run_recipe(_recipe(tmp_path, steps), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    keys = result["steps"]["keys"]
    report = json.loads(Path(keys["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == {"object": "Head", "replace": True}
    tail = report["argv"][report["argv"].index("--") + 1:]
    assert Path(tail[tail.index("--shapes") + 1]).name == "shapes.npz" and Path(tail[tail.index("--blend") + 1]).name == "avatar.blend"
    assert "--python-expr" not in report["argv"]                                         # 形状キーを入れるだけなら、アドオンは要らない
    assert keys["observations"][0]["keys"] == ["blink_R", "blink_L"] and keys["observations"][0]["replaced"] == ["blink_L"]
    step = result["steps"]["binds"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"]["binds"] == {"blink": {"blink_L": 1.0, "blink_R": 1.0}, "blinkLeft": {"blink_L": 1.0}, "aa": {}}
    assert list(report["settings"]["binds"]["blink"]) == ["blink_L", "blink_R"]           # 挙げた順に割り当てる
    assert "bl_ext.user_default.vrm" in report["argv"][report["argv"].index("--python-expr") + 1]   # VRM Add-on を有効にする前置き
    observed = step["observations"][0]
    assert observed["kind"] == "vrm_expressions" and observed["bound"] == ["blink", "blinkLeft"] and observed["name_only"] == ["lookUp"]
    assert executor.run_recipe(_recipe(tmp_path, steps), tmp_path / "runs")["steps"]["binds"]["reused_from"] is None   # 毎回実行する


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, outputs, message", [
    ({}, {"blend": "o.blend", "report": "r.json"}, "object"),
    ({"object": "Head", "replace": "yes"}, {"blend": "o.blend", "report": "r.json"}, "replace"),
    ({"object": "Head", "values": 1}, {"blend": "o.blend", "report": "r.json"}, "知らない設定"),
    ({"object": "Head"}, {"blend": "o.blend"}, "report"),
])
def test_shape_keys_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    with pytest.raises(ToolError, match=message):
        get("model.shape_keys").function({"blend": tmp_path / "avatar.blend", "shapes": tmp_path / "s.npz"},
                                         {"blender": str(blender), **parameters}, {k: tmp_path / v for k, v in outputs.items()},
                                         Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"binds": {"blink": {"blink_L": 1.0}}}, "object"),
    ({"object": "Head", "binds": {}}, "binds"),
    ({"object": "Head", "binds": {"blink_left": {"blink_L": 1.0}}}, "決まった表情"),       # VRM Add-on の中の名前ではなく VRM 1.0 の名前
    ({"object": "Head", "binds": {"wink": {"blink_L": 1.0}}}, "決まった表情"),
    ({"object": "Head", "binds": {"blink": ["blink_L"]}}, "binds.blink"),
    ({"object": "Head", "binds": {"blink": {"blink_L": 1.5}}}, "0〜1"),
    ({"object": "Head", "binds": {"blink": {"blink_L": True}}}, "0〜1"),
    ({"object": "Head", "binds": {"blink": {"": 1.0}}}, "空でない"),
    ({"object": "Head", "binds": {"blink": {"blink_L": 1.0}}, "wobble": 1}, "知らないパラメーター"),
])
def test_vrm_expressions_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    with pytest.raises(ToolError, match=message):
        get("vrm.expressions").function({"blend": tmp_path / "avatar.blend"}, {"blender": str(blender), **parameters},
                                        {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
