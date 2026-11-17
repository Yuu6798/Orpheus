"""vrm.color_factor: 色の掛け率を 1 に戻す計算（Blender なし）と、工程の受け渡しと悪い設定（代役の Blender）。
本物の Blender 5.2.4 と VRM Add-on での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import color_factor
from model3d.io import ToolError, atomic_json


def test_restored_sets_the_base_to_one_and_lifts_the_shade_by_the_same_ratio():
    base, shade = color_factor.restored([0.8, 0.8, 0.8, 1.0], [0.512, 0.512, 0.512])
    assert base == [1.0, 1.0, 1.0, 1.0] and shade == pytest.approx([0.64, 0.64, 0.64])
    base, shade = color_factor.restored([0.5, 0.25, 1.0, 0.7], [0.6, 0.1, 0.3])
    assert base == [1.0, 1.0, 1.0, 0.7]                               # 透け（A）は変えない
    assert shade == pytest.approx([1.0, 0.4, 0.3])                    # 1 を超えない（0.6 ÷ 0.5 = 1.2）、掛け率 1 の色は同じ
    assert color_factor.restored([0.0, 1e-7, 0.5, 1.0], [0.2, 0.3, 0.2])[1] == pytest.approx([0.2, 0.3, 0.4])   # 0 に近い掛け率では割らない


def test_parse_args_reads_the_files():
    args = color_factor.parse_args(["--blend", "in.blend", "--settings", "s.json", "--out-blend", "out.blend", "--report", "r.json"])
    assert (args.blend, args.settings, args.out_blend, args.report) == ("in.blend", "s.json", "out.blend", "r.json")


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
open(value("--out-blend"), "wb").write(b"restored")
before = {"Lower": {"base": [0.8, 0.8, 0.8, 1.0], "shade": [0.512, 0.512, 0.512]}, "Upper": {"base": [1, 1, 1, 1], "shade": [0.64] * 3}}
after = {"Lower": {"base": [1.0, 1.0, 1.0, 1.0], "shade": [0.64, 0.64, 0.64]}, "Upper": before["Upper"]}
json.dump({"schema_version": "model3d-color-factor/0.1", "materials": settings["materials"], "changed": ["Lower"], "before": before,
           "after": after, "argv": sys.argv[1:]}, open(value("--report"), "w", encoding="utf-8"))
'''


def test_color_factor_passes_the_materials_and_enables_the_vrm_addon(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    folder = tmp_path / "ext" / "user_default" / "vrm"
    folder.mkdir(parents=True)
    (folder / "blender_manifest.toml").write_text('version = "4.10.5"\n', encoding="utf-8")
    step = {"id": "factor", "operation": "vrm.color_factor", "inputs": {"blend": "$inputs.avatar"},
            "parameters": {"blender": str(blender), "blender_extensions": "ext", "materials": ["Lower", "Upper"]},
            "outputs": {"blend": "restored.blend", "report": "factor.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "factor",
                                           "inputs": {"avatar": {"path": "avatar.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["factor"]
    argv = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "color_factor.py"
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]          # VRM Add-on を有効にする前置き
    tail = argv[argv.index("--") + 1:]
    assert Path(tail[tail.index("--blend") + 1]).name == "avatar.blend"
    assert Path(tail[tail.index("--out-blend") + 1]) == Path(step["artifacts"]["blend"])
    settings = json.loads(Path(tail[tail.index("--settings") + 1]).read_text(encoding="utf-8"))
    assert settings == {"materials": ["Lower", "Upper"]}
    observation = step["observations"][0]
    assert observation["kind"] == "color_factor" and observation["changed"] == ["Lower"]
    assert observation["materials"]["Lower"]["after"]["shade"] == [0.64, 0.64, 0.64]
    assert observation["materials"]["Upper"]["before"] == observation["materials"]["Upper"]["after"]
    assert observation["addons"][0]["version"] == "4.10.5"
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["factor"]["reused_from"] is None   # 毎回実行する


@pytest.mark.parametrize("parameters, outputs, message", [
    ({}, None, "materials"),
    ({"materials": []}, None, "materials"),
    ({"materials": "Lower"}, None, "materials"),
    ({"materials": ["Lower", "Lower"]}, None, "materials"),
    ({"materials": ["Lower", ""]}, None, "materials"),
    ({"materials": ["Lower"], "factor": 0.9}, None, "知らないパラメーター"),
    ({"materials": ["Lower"]}, {"blend": "o.blend"}, "report"),
])
def test_color_factor_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("vrm.color_factor").function({"blend": tmp_path / "avatar.blend"}, {"blender": str(blender), **parameters}, declared,
                                         Context())
