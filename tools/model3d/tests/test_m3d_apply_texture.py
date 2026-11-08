""".blend の画像の差し替え（texture.apply_to_blend）: 名前の比べ方（Blender の中の台本の純粋な部分）と、工程の設定・Blender への
渡し方・報告の確かめ（代役の Blender）。画像ノードを差し替える所は本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import apply_texture
from model3d.io import ToolError, atomic_json


def test_names_are_compared_by_file_name_without_case_or_duplicate_marks():
    assert apply_texture.canonical_name("basecolor_fix3.png") == "basecolor_fix3.png"
    assert apply_texture.canonical_name("BaseColor_Fix3.PNG.001") == "basecolor_fix3.png"
    assert apply_texture.canonical_name("//textures\\atlas.png") == "atlas.png"            # Blender の相対パス（共有フォルダと読まない）
    assert apply_texture.canonical_name("//textures/atlas.png") == "atlas.png"
    assert apply_texture.canonical_name("C:/work/models/atlas.png") == "atlas.png"
    assert apply_texture.canonical_name(None) == "" and apply_texture.canonical_name("atlas.png.1234") == "atlas.png.1234"


# 代役の Blender: --version に答え、ふだんは設定を報告に写し、差し替えた体で書く（match に bad が入れば数が足りない報告）
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"applied")
replaced = 0 if "bad" in settings["match"] else 3
json.dump({"schema_version": "model3d-apply-texture/0.1", "blender": "4.5.15", "replaced_node_count": replaced,
           "unrelated_image_nodes_verified_unchanged": 2, "replacement_texture_packed": True, "input_files_unchanged": True,
           "atlas_size_wh": [64, 64], "packed_texture_sha256": "0" * 64, "settings": settings, "argv": args,
           "script": sys.argv[sys.argv.index("--python") + 1]}, open(value("--report"), "w"))
'''


def _recipe(tmp_path, parameters, outputs=None):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "apply",
                                           "inputs": {"char": {"path": "char.blend"}, "atlas": {"path": "atlas.png"}},
                                           "steps": [{"id": "apply", "operation": "texture.apply_to_blend",
                                                      "inputs": {"blend": "$inputs.char", "texture": "$inputs.atlas"},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"blend": "char.blend", "report": "apply.json"}}]})
    return tmp_path / "recipe.json"


def _files(tmp_path):
    (tmp_path / "char.blend").write_bytes(b"char")
    (tmp_path / "atlas.png").write_bytes(b"png")
    return stand_in_executable(tmp_path, "blender", STAND_IN)


def test_the_operation_passes_the_settings_and_checks_the_report(tmp_path):
    from model3d import executor
    blender = _files(tmp_path)
    result = executor.run_recipe(_recipe(tmp_path, {"blender": str(blender), "match": "basecolor.png"}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["apply"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "apply_texture.py" and "--texture" in report["argv"]
    assert report["settings"] == {"match": "basecolor.png", "min_replacements": 1, "image_name": "atlas.png"}   # 既定の名前はテクスチャのファイル名
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"applied"
    observation = step["observations"][0]
    assert observation["kind"] == "apply_texture" and observation["replaced"] == 3 and observation["unrelated_unchanged"] == 2
    again = executor.run_recipe(_recipe(tmp_path, {"blender": str(blender), "match": "basecolor.png"}), tmp_path / "runs")
    assert again["steps"]["apply"]["reused_from"] is None             # Blender は外部の実行ファイルなので毎回実行


def test_a_report_that_breaks_the_contract_fails_the_step(tmp_path):
    from model3d import executor
    blender = _files(tmp_path)
    result = executor.run_recipe(_recipe(tmp_path, {"blender": str(blender), "match": "bad.png", "min_replacements": 1}),
                                 tmp_path / "runs")
    assert result["status"] == "failed" and "確かめの決まり" in result["steps"]["apply"]["error"]


class Context:
    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir
        self.tools = {}


@pytest.mark.parametrize("parameters, outputs, message", [
    ({}, {"blend": "o.blend", "report": "r.json"}, "match"),
    ({"match": "  "}, {"blend": "o.blend", "report": "r.json"}, "match"),
    ({"match": "a.png", "min_replacements": 0}, {"blend": "o.blend", "report": "r.json"}, "min_replacements"),
    ({"match": "a.png", "image_name": ""}, {"blend": "o.blend", "report": "r.json"}, "image_name"),
    ({"match": "a.png", "wobble": 1}, {"blend": "o.blend", "report": "r.json"}, "知らないパラメーター"),
    ({"match": "a.png"}, {"blend": "o.png", "report": "r.json"}, ".blend"),
    ({"match": "a.png"}, {"blend": "o.blend"}, "report"),
    ({"match": "a.png"}, {"blend": "o.blend", "report": "r.json"}, "Blender の実行ファイル"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    (tmp_path / "char.blend").write_bytes(b"char")
    (tmp_path / "atlas.png").write_bytes(b"png")
    with pytest.raises(ToolError, match=message):
        get("texture.apply_to_blend").function({"blend": tmp_path / "char.blend", "texture": tmp_path / "atlas.png"}, parameters,
                                               {k: tmp_path / v for k, v in outputs.items()}, Context(tmp_path))
