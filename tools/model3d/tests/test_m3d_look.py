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


def test_line_sources_take_the_base_color_and_alpha_of_the_principled_bsdf():
    from types import SimpleNamespace as M

    def socket(sources=(), value=None):                                # 入力の口の代役: つながった元の出口と、値
        return M(links=[M(from_socket=s) for s in sources], default_value=value)

    color_out, alpha_out = M(name="Color"), M(name="Alpha")
    normal = M(type="TEX_IMAGE", image="normal_map", outputs={"Color": M(name="normal")})   # Base Color につながらない絵は使わない
    textured = M(type="BSDF_PRINCIPLED", inputs={"Base Color": socket([color_out], (0.8, 0.1, 0.1, 1.0)),
                                                 "Alpha": socket([alpha_out], 1.0)})
    assert toon_look.line_sources(M(nodes=[normal, textured])) == (color_out, (0.8, 0.1, 0.1, 1.0), alpha_out, 1.0)
    plain = M(type="BSDF_PRINCIPLED", inputs={"Base Color": socket((), (0.2, 0.4, 0.6, 1.0)), "Alpha": socket((), 0.5)})
    assert toon_look.line_sources(M(nodes=[normal, plain])) == (None, (0.2, 0.4, 0.6, 1.0), None, 0.5)
    assert toon_look.line_sources(M(nodes=[normal])) == (normal.outputs["Color"], (0.5, 0.5, 0.5, 1.0), None, 1.0)  # 最初の絵
    assert toon_look.line_sources(M(nodes=[])) == (None, (0.5, 0.5, 0.5, 1.0), None, 1.0)


def test_rendered_follows_collection_hiding_and_view_layer_exclusion():
    from types import SimpleNamespace as M

    def layer(names, children=(), hide=False, exclude=False):
        return M(exclude=exclude, collection=M(hide_render=hide, objects=set(names)), children=list(children))

    root = layer([], [layer(["body", "both"]), layer(["alt", "both"], hide=True), layer(["excl"], exclude=True),
                      layer([], [layer(["nested"])], hide=True)])     # 隠したコレクションの子のコレクションも隠れる
    view = M(use=True, layer_collection=root)
    unused = M(use=False, layer_collection=layer(["alt"]))             # 使わないビューレイヤーは見ない
    picked = [n for n in ("body", "both", "alt", "excl", "nested", "loose")
              if toon_look.rendered(M(name=n, hide_render=False), [view, unused])]
    assert picked == ["body", "both"]                                   # 隠した所と見える所の両方にあれば出る
    assert not toon_look.rendered(M(name="body", hide_render=True), [view])


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


def test_collider_proxies_are_left_out_of_the_default_meshes():
    from model3d.blender_adapter import prepare_push

    class Thing(dict):                                   # Blender の物体の custom property（get）のまね
        pass

    proxy, body = Thing({prepare_push.COLLIDER_MARK: True}), Thing()
    assert toon_look.COLLIDER_MARK == prepare_push.COLLIDER_MARK      # 写しを作る側と選ぶ側で同じ印
    assert toon_look.is_collider(proxy) and not toon_look.is_collider(body)


SHADING = {"bands": [{"at": 0.55, "soft": 0.05, "weight": 0.2}, {"at": 0.10, "soft": 0.04, "weight": 0.6},
                     {"at": -0.40, "soft": 0.05, "weight": 0.2}],
           "face_shadow": 0.35, "skin_meshes": ["body"], "skin_shade": [0.86, 0.6, 0.56],
           "skin_highlight": {"at": 0.75, "soft": 0.08, "amount": 0.12, "color": [1, 0.94, 0.88]},
           "rim_scale": 1.3, "real_light": 0.25, "real_light_gain": 0.8, "real_light_max": 1.4}


def test_the_shading_settings_are_checked_and_passed_on():
    from model3d.ops.look_ops import _look_settings
    s = _look_settings({"meshes": ["body", "arm"], "toon": SHADING})["toon"]
    assert s["bands"] == SHADING["bands"] and s["face_shadow"] == 0.35 and s["skin_meshes"] == ["body"]
    assert s["skin_highlight"] == {"at": 0.75, "soft": 0.08, "amount": 0.12, "color": [1.0, 0.94, 0.88]}
    assert s["rim_scale"] == 1.3 and s["real_light"] == 0.25 and s["real_light_gain"] == 0.8 and s["real_light_max"] == 1.4
    assert _look_settings({"toon": {"bands": None, "skin_highlight": None}})["toon"] == {}     # null は省いたのと同じ
    merged = toon_look.merged({"toon": {}})["toon"]                                         # 省けば今の 2 段の塗りのまま
    assert merged["bands"] is None and merged["face_shadow"] == 0 and merged["skin_meshes"] is None
    assert merged["skin_highlight"] is None and merged["rim_scale"] == 1 and merged["real_light"] == 0


@pytest.mark.parametrize("toon, message", [
    ({"bands": []}, "toon.bands"),
    ({"bands": [{"at": 0.1, "soft": 0.05}]}, "toon.bands"),
    ({"bands": [{"at": 0.1, "soft": 0.05, "weight": 1.0}] * 5}, "toon.bands"),
    ({"bands": [{"at": 1.5, "soft": 0.05, "weight": 1.0}]}, "at"),
    ({"bands": [{"at": 0.1, "soft": 0, "weight": 1.0}]}, "soft"),
    ({"bands": [{"at": 0.1, "soft": 0.05, "weight": 0.5}]}, "weight の和"),
    ({"bands": [{"at": 0.1, "soft": 0.05, "weight": 0.5}, {"at": 0.1, "soft": 0.05, "weight": 0.5}]}, "重なって"),
    ({"face_shadow": 1.5}, "face_shadow"),
    ({"skin_meshes": []}, "skin_meshes"),
    ({"skin_meshes": ["leg"]}, "skin_meshes"),
    ({"skin_highlight": {"at": 0.7, "soft": 0.1, "amount": 0.2}}, "skin_highlight"),
    ({"skin_highlight": {"at": 0.7, "soft": 0.1, "amount": 2, "color": [1, 1, 1]}}, "amount"),
    ({"skin_highlight": {"at": 0.7, "soft": 0.1, "amount": 0.2, "color": [1, 1, 2]}}, "color"),
    ({"rim_scale": -1}, "rim_scale"),
    ({"real_light": 1.2}, "real_light"),
    ({"real_light_gain": 0}, "real_light_gain"),
    ({"real_light_max": -1}, "real_light_max"),
])
def test_bad_shading_settings_are_refused(toon, message):
    from model3d.ops.look_ops import _look_settings
    with pytest.raises(ToolError, match=message):
        _look_settings({"meshes": ["body", "arm"], "toon": toon})


def test_the_face_shadow_scales_the_shadow_and_keeps_the_bands():
    bands = [1.0, 0.75, 0.2, 0.0]                                    # 4 段の当たり方
    on_face = [toon_look.face_lit(b, 1.0, 0.35) for b in bands]
    assert on_face == pytest.approx([1.0, 0.9125, 0.72, 0.65])        # 影の深さ (1 - L) が 0.35 倍。段の違いは残る
    assert [toon_look.face_lit(b, 0.0, 0.35) for b in bands] == bands  # 印の無い所はふつうのまま
    assert toon_look.face_lit(0.2, 0.5, 0.35) == pytest.approx(0.2 + 0.5 * 0.65 * 0.8)   # 印の境は混ぜる
