"""メッシュを書き出す（model.export_mesh）: 絵の選び方・世界の座標への移し方（Blender なし）と、工程の受け渡しと悪い設定（代役の Blender）。
本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import mesh_export
from model3d.io import ToolError, atomic_json


def test_choose_image_takes_the_only_image_or_the_one_that_matches():
    assert mesh_export.choose_image(["head_color"]) == "head_color"
    assert mesh_export.choose_image(["head_color", "head_normal"], "color") == "head_color"
    with pytest.raises(ValueError, match="2 つ以上"):
        mesh_export.choose_image(["head_color", "head_normal"])
    with pytest.raises(ValueError, match="2 つ以上"):
        mesh_export.choose_image(["head_color", "head_color_dark"], "color")
    with pytest.raises(ValueError, match="ありません"):
        mesh_export.choose_image(["head_color"], "body")
    with pytest.raises(ValueError, match="ありません"):
        mesh_export.choose_image([])


def test_to_world_applies_the_matrix_and_the_scene_unit():
    matrix = np.array([[2, 0, 0, 1], [0, 0, -2, 0], [0, 2, 0, 3], [0, 0, 0, 1]], dtype=np.float32)   # 2 倍・x のまわりに 90°・ずらし
    local = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]])
    world = mesh_export.to_world(local, matrix)
    assert world.dtype == np.float64
    assert world.tolist() == [[3.0, 0.0, 3.0], [1.0, 0.0, 5.0], [1.0, -2.0, 3.0]]
    assert (mesh_export.to_world(local, matrix, 0.01) == world * 0.01).all()   # 1 単位 = 1 cm のシーン（m に直す）


def test_write_npz_keeps_the_declared_name(tmp_path):
    arrays = {"co": np.zeros((2, 3)), "edges": np.array([[0, 1]])}
    for number, name in enumerate(("head.npz", "head.NPZ", "head.Npz")):              # 名前ごとに別のフォルダ（大文字と小文字を区別しない OS もある）
        folder = tmp_path / f"run{number}"
        mesh_export.write_npz(folder / name, arrays)
        assert [p.name for p in folder.iterdir()] == [name]                           # 大文字の拡張子でも .npz を足さない
        with np.load(folder / name, allow_pickle=False) as data:
            assert data.files == ["co", "edges"] and data["edges"].tolist() == [[0, 1]]


STAND_IN = r'''
import json, os, sys
import numpy as np
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = json.load(open(value("--settings"), encoding="utf-8"))
co = np.array([[0.0, -0.1, 1.5], [0.01, -0.1, 1.5], [0.0, -0.1, 1.51]])
np.savez_compressed(value("--out-mesh"), co=co, edges=np.array([[0, 1], [1, 2], [2, 0]]), tris=np.array([[0, 1, 2]]),
                    tri_loops=np.array([[0, 1, 2]]), uv=np.zeros((3, 2)), normals=np.tile([0.0, -1.0, 0.0], (3, 1)))
texture = None
if value("--out-texture"):
    open(value("--out-texture"), "wb").write(b"png")
    texture = {"image": settings.get("image", "head_color"), "size": [8, 8]}
json.dump({"schema_version": "model3d-export-mesh/0.1", "object": settings["object"], "vertices": 3, "edges": 3, "triangles": 1,
           "uv_layer": "UVMap", "shape_keys": False, "scale_length": 1.0, "texture": texture, "settings": settings},
          open(value("--report"), "w"))
'''


def _recipe(tmp_path, parameters, outputs):
    (tmp_path / "model.blend").write_bytes(b"model")
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "export",
        "inputs": {"model": {"path": "model.blend"}},
        "steps": [{"id": "export", "operation": "model.export_mesh", "inputs": {"blend": "$inputs.model"},
                   "parameters": {"blender": blender, **parameters}, "outputs": outputs}]})
    return tmp_path / "recipe.json"


def test_export_mesh_passes_the_object_and_writes_the_texture_only_when_asked(tmp_path):
    from model3d import executor
    outputs = {"mesh": "head.npz", "texture": "head.png", "report": "export.json"}
    result = executor.run_recipe(_recipe(tmp_path, {"object": "Head", "image": "color"}, outputs), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["export"]
    observed = step["observations"][0]
    assert observed["kind"] == "export_mesh" and observed["object"] == "Head" and observed["vertices"] == 3
    assert observed["texture"] == {"image": "color", "size": [8, 8]}
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == {"object": "Head", "image": "color"}
    with np.load(step["artifacts"]["mesh"], allow_pickle=False) as data:
        assert data.files == ["co", "edges", "tris", "tri_loops", "uv", "normals"]
    plain_dir = tmp_path / "plain"
    plain_dir.mkdir()
    plain = executor.run_recipe(_recipe(plain_dir, {"object": "Head"}, {"mesh": "head.npz", "report": "export.json"}), plain_dir / "runs")
    assert plain["status"] == "succeeded" and plain["steps"]["export"]["observations"][0]["texture"] is None   # 絵は頼んだときだけ


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, outputs, message", [
    ({}, {"mesh": "m.npz", "report": "r.json"}, "object"),
    ({"object": ""}, {"mesh": "m.npz", "report": "r.json"}, "object"),
    ({"object": "Head", "colour": 1}, {"mesh": "m.npz", "report": "r.json"}, "知らない設定"),
    ({"object": "Head"}, {"mesh": "m.npz"}, "report"),
    ({"object": "Head"}, {"mesh": "m.json", "report": "r.json"}, ".npz"),
    ({"object": "Head"}, {"mesh": "m.npz", "texture": "t.jpg", "report": "r.json"}, ".png"),
    ({"object": "Head", "image": "color"}, {"mesh": "m.npz", "report": "r.json"}, "texture"),
])
def test_export_mesh_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("model.export_mesh").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                          {key: tmp_path / name for key, name in outputs.items()}, Context(tmp_path))
