"""look.toon: 設定の重ね方（Blender なしで動く部分）と、台本の引数と工程の受け渡し（代役の Blender）を確かめる。
材質のノードと輪郭線の写しを作る所は本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import toon_look
from model3d.io import ToolError, atomic_json


def test_merged_fills_defaults_and_keeps_each_section_key():
    settings = toon_look.merged({"meshes": ["body"], "toon": {"rim_from": 0.76, "rim_to": 0.86}, "outline": {"enabled": False}})
    assert settings["meshes"] == ["body"] and settings["faces"] == []
    assert settings["toon"]["rim_from"] == 0.76 and settings["toon"]["threshold"] == 0.10 and settings["toon"]["tint"] == 0.22
    assert settings["toon"]["weak_rim"] == [{"attribute": "hand", "scale": 0.75}, {"attribute": "norim", "scale": 0.85}]
    assert settings["outline"]["enabled"] is False and settings["outline"]["dark"] == 0.12 and settings["outline"]["ratio"] == {}
    assert settings["face"] == {"lit": 0.20, "emit": 0.62}
    assert toon_look.DEFAULTS["toon"]["rim_from"] == 0.60                # 既定は書き換えない


def test_metres_per_local_combines_object_scale_and_scene_units():
    import numpy as np
    half = np.diag([0.5, 0.5, 0.5, 1.0])
    assert toon_look.metres_per_local(np.eye(4), 1.0) == 1.0
    assert toon_look.metres_per_local(half, 1.0) == 0.5                 # 0.5 倍の物体では、中の 1 は 0.5 m
    assert toon_look.metres_per_local(half, 0.01) == pytest.approx(0.005)   # 1 Blender 単位 = 1 cm のシーン


def test_bound_modifiers_names_the_ones_an_outline_copy_cannot_keep():
    from types import SimpleNamespace as M
    ob = M(modifiers=[M(name="Armature", type="ARMATURE"), M(name="Mirror", type="MIRROR"), M(name="Subdivision", type="SUBSURF"),
                      M(name="SurfaceDeform", type="SURFACE_DEFORM"), M(name="Cache", type="MESH_SEQUENCE_CACHE"),
                      M(name="Smooth", type="CORRECTIVE_SMOOTH", rest_source="ORCO"),
                      M(name="Smooth.001", type="CORRECTIVE_SMOOTH", rest_source="BIND")])
    assert toon_look.bound_modifiers(ob) == ["SurfaceDeform", "Cache", "Smooth.001"]
    assert toon_look.bound_modifiers(M(modifiers=[])) == []


def _node(name, kind, *sources, image=None):
    """ノードの代役: 入力の口ごとに、つながった元のノードを並べる"""
    from types import SimpleNamespace as M
    return M(name=name, type=kind, image=image, inputs=[M(links=[M(from_node=s)]) for s in sources])


def _bsdf(*sources, color=(0.8, 0.1, 0.1, 1.0)):
    from types import SimpleNamespace as M
    return M(name="bsdf", type="BSDF_PRINCIPLED",
             inputs={"Base Color": M(links=[M(from_node=s) for s in sources], default_value=color)})


def test_base_image_follows_the_base_color_links_and_skips_other_images():
    tex = _node("tex", "TEX_IMAGE", image="albedo")
    mix = _node("mix", "MIX", _node("rgb", "RGB"), _node("reroute", "REROUTE", tex))   # 色の値と、継ぎを通った絵を混ぜる
    assert toon_look.base_image(_bsdf(mix)) == "albedo"
    assert toon_look.base_image(_bsdf(_node("empty", "TEX_IMAGE"), _node("attr", "VERTEX_COLOR"))) is None   # 絵のない絵の枠は飛ばす
    assert toon_look.base_image(_bsdf()) is None


def test_hull_source_uses_the_base_color_image_or_value():
    from types import SimpleNamespace as M

    def material(*nodes):
        return M(use_nodes=True, node_tree=M(nodes=list(nodes)))

    normal = _node("normal", "TEX_IMAGE", image="normal_map")          # Base Color につながらない絵（法線の絵など）は使わない
    assert toon_look.hull_source(material(normal, _bsdf(color=(0.2, 0.4, 0.6, 1.0)))) == (None, (0.2, 0.4, 0.6, 1.0))
    assert toon_look.hull_source(material(normal, _bsdf(_node("tex", "TEX_IMAGE", image="albedo"))))[0] == "albedo"
    assert toon_look.hull_source(material(normal)) == ("normal_map", (0.5, 0.5, 0.5, 1.0))          # Principled BSDF が無ければ最初の絵
    assert toon_look.hull_source(None) == (None, (0.5, 0.5, 0.5, 1.0))


def test_parse_args():
    args = toon_look.parse_args(["--blend", "i.blend", "--out-blend", "o.blend", "--report", "r.json", "--settings", "s.json"])
    assert args.settings == "s.json"


# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数と設定を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"looked")
settings = json.load(open(value("--settings"), encoding="utf-8"))
json.dump({"schema_version": "model3d-toon-look/0.1", "meshes": ["body", "arm"], "faces": ["face"],
           "toon_materials": ["skin", "cloth"], "face_materials": ["face"],
           "outline_hulls": [{"mesh": "body", "hull": "hull_body", "triangles": 1200, "ratio": 0.5},
                             {"mesh": "arm", "hull": "hull_arm", "triangles": 300, "ratio": 1.0}],
           "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''


def test_the_operation_writes_the_settings_and_passes_them_to_blender(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "meshes": ["body", "arm"], "faces": ["face"],
              "toon": {"rim_from": 0.76, "rim_to": 0.86, "light": [0.8, -5, 4]}, "outline": {"meshes": ["body"], "ratio": {"body": 0.5}}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "look",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "look", "operation": "look.toon", "inputs": {"blend": "$inputs.dance"},
                                                      "parameters": params,
                                                      "outputs": {"blend": "looked.blend", "report": "look.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["look"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "toon_look.py"
    assert report["settings"] == {"meshes": ["body", "arm"], "faces": ["face"],
                                  "toon": {"rim_from": 0.76, "rim_to": 0.86, "light": [0.8, -5.0, 4.0]}, "face": {},
                                  "outline": {"meshes": ["body"], "ratio": {"body": 0.5}}}
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"looked"
    observation = step["observations"][0]
    assert observation["kind"] == "toon_look" and observation["toon_materials"] == 2 and observation["face_materials"] == 1
    assert observation["outline_hulls"] == 2 and observation["outline_triangles"] == 1500
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["look"]["reused_from"] is None


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"sprung")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("look.toon").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"meshes": "body"}, "meshes"),
    ({"meshes": ["body", "body"]}, "meshes"),
    ({"faces": None}, "faces"),
    ({"meshes": ["body"], "faces": ["body"]}, "両方"),
    ({"toon": {"ramp": 3}}, "知らない鍵"),
    ({"toon": {"light": [0, 0, 0]}}, "toon.light"),
    ({"toon": {"light": [1, 2]}}, "toon.light"),
    ({"toon": {"shade": [0.5, 0.5, 1.5]}}, "toon.shade"),
    ({"toon": {"tint": 1.5}}, "toon.tint"),
    ({"toon": {"rim_from": 0.8, "rim_to": 0.7}}, "rim_from"),
    ({"toon": {"rim_colors": [[1, 0, 0]]}}, "rim_colors"),
    ({"toon": {"weak_rim": [{"attribute": "hand"}]}}, "weak_rim"),
    ({"toon": {"weak_rim": [{"attribute": "hand", "scale": 2}]}}, "weak_rim"),
    ({"toon": {"face_attribute": ""}}, "face_attribute"),
    ({"toon": {"soft": -0.1}}, "soft"),
    ({"face": {"glow": 1}}, "知らない鍵"),
    ({"face": {"emit": -1}}, "emit"),
    ({"outline": {"enabled": "yes"}}, "outline.enabled"),
    ({"outline": {"ratio": {"body": 0}}}, "outline.ratio"),
    ({"outline": {"ratio": {"body": 1.5}}}, "outline.ratio"),
    ({"outline": {"width_mm": 0}}, "width_mm"),
    ({"outline": {"hand_line": 1.5}}, "hand_line"),
    ({"outline": "on"}, "outline"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **parameters})


def test_the_operation_needs_blender_and_both_outputs(tmp_path):
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN))}, {"blend": "o.blend"})
