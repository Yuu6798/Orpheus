"""look.lit: 設定の重ね方・絵の色の出どころの選び方・既定のメッシュの選び方（Blender なしで動く部分）と、台本の引数と工程の
受け渡し（代役の Blender）を確かめる。材質のノードを作る所は本物の Blender が要るので、CI では確かめない（docs/model3d.md の
手元の確認）。"""
import json
from pathlib import Path
from types import SimpleNamespace as M

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import lit_look, toon_look
from model3d.io import ToolError, atomic_json


def test_merged_fills_defaults_and_keeps_each_section_key():
    settings = lit_look.merged({"soft": ["face"], "lit": {"gain": 0.9}, "extra": {"to": [0.0, 0.5]}})
    assert settings["soft"] == ["face"] and settings["texture"] == "BaseColor" and settings["meshes"] is None
    assert settings["lit"] == {"band": [0.12, 0.32], "gain": 0.9, "ambient": 1.0}
    assert settings["soft_lit"] == {"band": [0.02, 0.50], "gain": 0.78, "ambient": 1.5}
    assert settings["extra"] == {"from": [1.0, 6.0], "to": [0.0, 0.5]}
    assert settings["floor"] == 0.02 and settings["ambient"] == [0.2, 0.2, 0.2]
    assert lit_look.DEFAULTS["lit"]["gain"] == 0.86                       # 既定は書き換えない


def socket(links=(), value=(0.8, 0.8, 0.8, 1.0)):
    return M(links=[M(from_node=node) for node in links], default_value=value)


def image_node(name, image="tex.png"):
    return M(type="TEX_IMAGE", name=name, image=image)


def test_the_color_comes_from_the_named_image_node_first():
    shade = image_node("Mtoon1ShadeMultiplyTexture.Image", "shade.png")
    base = image_node("Mtoon1BaseColorTexture.Image", "base.png")
    empty = image_node("Mtoon1BaseColorTexture.Image.001", None)          # 画像の無い同じ名前のノードは使わない
    principled = M(type="BSDF_PRINCIPLED", inputs={"Base Color": socket([shade])})
    assert lit_look.color_source([shade, empty, principled, base], "BaseColor") == ("image", base)
    assert lit_look.color_source([shade, principled, base], "Lit") == ("image", shade)   # 名前に無ければ Base Color の絵


def test_the_color_falls_back_to_the_principled_bsdf():
    linked = image_node("Image Texture", "albedo.png")
    principled = M(type="BSDF_PRINCIPLED", inputs={"Base Color": socket([linked])})
    assert lit_look.color_source([principled, linked], "BaseColor") == ("image", linked)
    other = M(type="MIX", image=None)
    mixed = M(type="BSDF_PRINCIPLED", inputs={"Base Color": socket([other], (0.2, 0.4, 0.6, 1.0))})
    assert lit_look.color_source([mixed], "BaseColor") == ("color", (0.2, 0.4, 0.6, 1.0))   # 絵でないつなぎは、色の値
    assert lit_look.color_source([M(type="EMISSION")], "BaseColor") is None


def test_the_default_meshes_are_the_rendered_deformed_meshes():
    class Thing(dict):                                   # Blender の物体（custom property は get で読む）のまね
        def __init__(self, name, kind="MESH", parent=None, modifiers=(), hide=False, **marks):
            super().__init__(marks)
            self.name, self.type, self.parent, self.modifiers, self.hide_render = name, kind, parent, list(modifiers), hide

    arm = Thing("Armature", "ARMATURE")
    deform = [M(type="ARMATURE", object=arm)]
    objects = [arm, Thing("Body", modifiers=deform), Thing("Hair", parent=arm), Thing("Hidden", modifiers=deform, hide=True),
               Thing("Prop"), Thing("proxy_leg", modifiers=deform, **{toon_look.COLLIDER_MARK: True}),
               Thing("hull_Body", modifiers=deform, **{lit_look.OUTLINE_MARK: "Body"})]
    names = {o.name for o in objects}
    layer = M(exclude=False, collection=M(hide_render=False, objects=names), children=[])
    bpy = M(data=M(objects=objects), context=M(scene=M(view_layers=[M(use=True, layer_collection=layer)])))
    assert lit_look.default_meshes(bpy) == ["Body", "Hair"]
    with pytest.raises(SystemExit, match="アーマチュア"):
        lit_look.default_meshes(M(data=M(objects=[Thing("Body")]), context=bpy.context))


def test_parse_args():
    args = lit_look.parse_args(["--blend", "i.blend", "--out-blend", "o.blend", "--report", "r.json", "--settings", "s.json"])
    assert args.out_blend == "o.blend"


# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数と設定を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"lit")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-lit-look/0.1", "meshes": ["Body", "Hair", "Head"],
           "materials": [{"source": "body", "material": "body.lit", "soft": False},
                         {"source": "hair", "material": "hair.lit", "soft": False},
                         {"source": "head", "material": "head.lit", "soft": True}],
           "object_slots": ["Head/0"], "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''


def test_the_operation_writes_the_settings_and_passes_them_to_blender(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"dance")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "soft": ["head"],
              "lit": {"band": [0.1, 0.3]}, "soft_lit": {"ambient": 2}, "extra": {"from": [1, 4]}, "floor": 0.05,
              "ambient": [0.1, 0.1, 0.12]}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "lit",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "lit", "operation": "look.lit", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": params,
                                                      "outputs": {"blend": "lit.blend", "report": "lit.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["lit"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "lit_look.py"
    assert report["settings"] == {"meshes": None, "soft": ["head"], "lit": {"band": [0.1, 0.3]}, "soft_lit": {"ambient": 2.0},
                                  "extra": {"from": [1.0, 4.0]}, "floor": 0.05, "ambient": [0.1, 0.1, 0.12]}
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"lit"
    observation = step["observations"][0]
    assert observation["kind"] == "lit_look" and observation["meshes"] == 3 and observation["materials"] == 3
    assert observation["soft_materials"] == 1 and observation["object_slots"] == 1
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["lit"]["reused_from"] is None


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"dance")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("look.lit").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"meshes": []}, "meshes"),
    ({"meshes": "Body"}, "meshes"),
    ({"soft": "head"}, "soft"),
    ({"soft": ["head", "head"]}, "soft"),
    ({"texture": ""}, "texture"),
    ({"lit": {"curve": 1}}, "知らない鍵"),
    ({"lit": {"band": [0.3, 0.1]}}, "lit.band"),
    ({"soft_lit": {"band": [0.1]}}, "soft_lit.band"),
    ({"lit": {"gain": -1}}, "gain"),
    ({"soft_lit": {"ambient": -0.5}}, "ambient"),
    ({"extra": {"from": [6, 1]}}, "extra.from"),
    ({"extra": {"to": [0, "a"]}}, "extra.to"),
    ({"extra": {"gain": 1}}, "知らない鍵"),
    ({"floor": 0}, "floor"),
    ({"ambient": [0.1, 0.1]}, "ambient"),
    ({"ambient": [0.1, -0.1, 0.1]}, "ambient"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **parameters})


def test_the_operation_needs_blender_and_both_outputs(tmp_path):
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN))}, {"blend": "o.blend"})


def test_the_extra_range_may_fall():
    from model3d.ops.look_ops import _lit_settings
    assert _lit_settings({"extra": {"to": [0.3, 0.0]}})["extra"] == {"to": [0.3, 0.0]}   # 写す先は下がってもよい
    assert _lit_settings({}) == {"meshes": None}                                         # 省いたものは書かない
