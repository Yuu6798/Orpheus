"""look.lit: 設定の重ね方・絵の色の出どころの選び方・既定のメッシュの選び方（Blender なしで動く部分）と、台本の引数と工程の
受け渡し（代役の Blender）を確かめる。材質のノードの作りは、ノードの代役（lit_nodes_stand_in）で型・設定・値・つなぎを調べる。
描いた絵は本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
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


class FakeSlot:
    """Blender の材質の枠のまね: link が DATA ならメッシュの材質の並び（分け合う物体からも見える）、OBJECT なら物体の側に入れる。"""

    def __init__(self, ob, index):
        self.ob, self.index, self.link, self._own = ob, index, "DATA", None

    @property
    def material(self):
        return self.ob.data.materials[self.index] if self.link == "DATA" else self._own

    @material.setter
    def material(self, value):
        if self.link == "DATA":
            self.ob.data.materials[self.index] = value
        else:
            self._own = value


def _object(name, mesh):
    ob = M(name=name, data=mesh)
    ob.material_slots = [FakeSlot(ob, i) for i in range(len(mesh.materials))]
    return ob


def test_objects_that_share_a_mesh_are_swapped_from_the_materials_before_the_swap():
    body, cloth = M(name="body"), M(name="cloth")
    shared, alone = M(materials=[body, None]), M(materials=[cloth])
    a, b, c, d = _object("A", shared), _object("B", shared), _object("C", alone), _object("D", alone)
    made = {}

    def lit_for(m):                                       # 元の材質だけを知っている（替えた後の材質を渡すと KeyError）
        return made.setdefault(m.name, {"body": M(name="body.lit"), "cloth": M(name="cloth.lit")}[m.name])

    slots, moved = lit_look.swap_slots([a, b, c, d], ["A", "B", "C"], lit_for)
    assert [(s["object"], s["slot"], s["source"], s["material"]) for s in slots] == [
        ("A", 0, "body", "body.lit"), ("B", 0, "body", "body.lit"), ("C", 0, "cloth", "cloth.lit")]   # 空の枠は飛ばす
    assert a.material_slots[0].material is b.material_slots[0].material is made["body"]   # どちらも選んだので、メッシュの側で替える
    assert moved == ["C/0"] and c.material_slots[0].link == "OBJECT" and c.material_slots[0].material is made["cloth"]
    assert d.material_slots[0].material is cloth                                          # 塗らない D は元の材質のまま


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


# 縁の光（rim）。ノードの作りは、Blender の代役（lit_nodes_stand_in）で調べる。描いた絵は docs/model3d.md の手元の確認。
from lit_nodes_stand_in import describe, digest, fake_bpy, feeding  # noqa: E402

RIM = {"left": [0.2, 0.6, 1.0], "right": [1.0, 0.3, 0.1], "edge": [0.4, 0.9]}
SOURCE = M(name="body", use_backface_culling=True)
PICTURE = ("image", M(image=M(name="tex.png"), interpolation="Closest"))
# 縁の光を足さないときの塗りのノード（型・設定・値・つなぎ）。縁の光を足す前の look.lit と同じ（19 個・23 本）
UNCHANGED = {(False, "image"): "58d286fb0b7d1da462c564dc1d5de87b9487977fc7ad0b0c254a38096f78181e",
             (False, "color"): "5fc0a2bbf87904ad2d941aeddfeaa438407f5938d2ebf3da52d1c8f5552b743c",
             (True, "image"): "2bed158730abbec33139544c44cac2630f337a68fb77c2ce675125ed0ca87344",
             (True, "color"): "e6c25ceb2c656d222bc23c5150cda79656728320daf1d7041dbb75a0b208f6b0"}


@pytest.mark.parametrize("soft", [False, True])
@pytest.mark.parametrize("kind", ["image", "color"])
def test_without_rim_the_material_is_unchanged(soft, kind):
    source = PICTURE if kind == "image" else ("color", (0.2, 0.4, 0.6, 1.0))
    with_rim = lit_look.merged({"rim": RIM})
    for settings, make_group, amount in ((lit_look.merged({}), False, 0.0),
                                         (with_rim, False, 1.0),            # グループが無ければ足さない
                                         (with_rim, True, 0.0)):            # 割合 0 の材質にも足さない
        bpy = fake_bpy()
        group = lit_look.rim_control(bpy, lit_look.rim_settings(settings)) if make_group else None
        skin = lit_look.lit_material(bpy, SOURCE, source, soft, settings, group, amount)
        d = describe(skin.node_tree)
        assert (len(d["nodes"]), len(d["links"])) == (19, 23)
        assert digest(d) == UNCHANGED[(soft, kind)]
        assert lit_look.RIM_MARK not in skin


def test_rim_settings_fill_defaults_and_keep_the_given_values():
    assert lit_look.rim_settings(lit_look.merged({})) is None
    rim = lit_look.rim_settings(lit_look.merged({"rim": {**RIM, "gain": 0.5, "materials": {"face": 0.3}}}))
    assert rim == {**RIM, "gain": 0.5, "amount": 1.0, "materials": {"face": 0.3}, "side": [-0.25, 0.25],
                   "down": [-0.75, -0.15], "blend": 0.5}
    assert "rim" not in lit_look.DEFAULTS                                  # 既定の設定（報告の settings）は今と同じ


def test_the_rim_control_group_holds_the_two_colours_and_the_gain():
    bpy = fake_bpy()
    group = lit_look.rim_control(bpy, lit_look.rim_settings({"rim": {**RIM, "gain": 1.5}}))
    assert group.name == lit_look.RIM_GROUP
    assert group.interface.sockets == [("left", "OUTPUT", "NodeSocketColor"), ("right", "OUTPUT", "NodeSocketColor"),
                                       ("gain", "OUTPUT", "NodeSocketFloat")]
    assert group.nodes["left"].outputs[0].default_value == (0.2, 0.6, 1.0, 1)
    assert group.nodes["right"].outputs[0].default_value == (1.0, 0.3, 0.1, 1)
    assert group.nodes["gain"].outputs[0].default_value == 1.5
    output = group.nodes["NodeGroupOutput"]
    assert [feeding(group, output, name)[0].name for name in ("left", "right", "gain")] == ["left", "right", "gain"]
    lit_look.set_rim(bpy, left=[0.0, 0.0, 1.0], gain=0.0)                  # コマごとの書き換え（全部の材質に効く）
    assert group.nodes["left"].outputs[0].default_value == (0.0, 0.0, 1.0, 1.0)
    assert group.nodes["right"].outputs[0].default_value == (1.0, 0.3, 0.1, 1)
    assert group.nodes["gain"].outputs[0].default_value == 0.0
    with pytest.raises(SystemExit, match="もう縁の光"):
        lit_look.rim_control(bpy, lit_look.rim_settings({"rim": RIM}))
    with pytest.raises(SystemExit, match="ありません"):
        lit_look.set_rim(fake_bpy(), gain=1.0)


def test_the_rim_adds_the_side_coloured_edge_to_the_emission():
    bpy = fake_bpy()
    settings = lit_look.merged({"rim": {**RIM, "side": [-0.3, 0.3], "down": [-0.8, -0.2], "blend": 0.4}})
    group = lit_look.rim_control(bpy, lit_look.rim_settings(settings))
    skin = lit_look.lit_material(bpy, SOURCE, PICTURE, False, settings, group, 0.35)
    tree = skin.node_tree
    assert skin[lit_look.RIM_MARK] == 0.35
    d = describe(tree)
    assert (len(d["nodes"]), len(d["links"])) == (19 + 14, 23 + 17)        # 足すのはノード 14 個、つなぎ 17 本（Emission へは付け替え）
    emission = next(n for n in tree.nodes if n.type == "EMISSION")
    total, _ = feeding(tree, emission, "Color")
    assert total.bl_idname == "ShaderNodeVectorMath" and total.operation == "ADD"
    painted, _ = feeding(tree, total, "Vector")                                 # 足す前の塗り（絵の色 ×（まわり ＋ 光））
    assert painted.operation == "MULTIPLY" and feeding(tree, painted, "Vector")[0].bl_idname == "ShaderNodeTexImage"
    scaled, _ = feeding(tree, total, "Vector_001")
    assert scaled.operation == "SCALE"
    mix, socket = feeding(tree, scaled, "Vector")
    assert mix.bl_idname == "ShaderNodeMix" and mix.data_type == "RGBA" and socket.identifier == "Result_Color"
    control = next(n for n in tree.nodes if n.bl_idname == "ShaderNodeGroup")
    assert control.node_tree is group
    links = {(k.from_socket.identifier, k.to_socket.identifier) for k in tree.links if k.to_node is mix}
    assert links == {("left", "A_Color"), ("right", "B_Color"), ("#0", "Factor_Float")}   # 型の名前で引く（Float の A・B ではない）
    side, _ = feeding(tree, mix, "Factor_Float")
    assert (side.inputs["From Min"].default_value, side.inputs["From Max"].default_value) == (-0.3, 0.3)
    xyz, axis = feeding(tree, side, "Value")
    assert xyz.bl_idname == "ShaderNodeSeparateXYZ" and axis.name == "X"
    to_camera, _ = feeding(tree, xyz, "#0")
    assert (to_camera.vector_type, to_camera.convert_from, to_camera.convert_to) == ("NORMAL", "WORLD", "CAMERA")
    assert feeding(tree, to_camera, "#0")[1].name == "Normal"
    strength, _ = feeding(tree, scaled, "Scale")
    edge_and_down, _ = feeding(tree, strength, "#0")
    gain_and_amount, _ = feeding(tree, strength, "#1")
    assert feeding(tree, gain_and_amount, "#0")[1].name == "gain" and gain_and_amount.inputs[1].default_value == 0.35
    edge, _ = feeding(tree, edge_and_down, "#0")
    down, _ = feeding(tree, edge_and_down, "#1")
    assert (edge.inputs["From Min"].default_value, edge.inputs["From Max"].default_value) == (0.4, 0.9)
    assert feeding(tree, edge, "Value")[1].name == "Facing"
    assert feeding(tree, edge, "Value")[0].inputs["Blend"].default_value == 0.4
    assert (down.inputs["From Min"].default_value, down.inputs["From Max"].default_value) == (-0.8, -0.2)
    assert feeding(tree, down, "Value")[1].name == "Y"
    assert all(n.interpolation_type == "SMOOTHSTEP" for n in (edge, side, down))


def test_the_operation_checks_and_passes_the_rim():
    from model3d.ops.look_ops import _lit_settings
    rim = _lit_settings({"rim": {"left": [1, 0, 0], "right": [0, 0, 1], "edge": [0.5, 0.9], "gain": 2, "materials": {"face": 0}}})["rim"]
    assert rim == {"left": [1.0, 0.0, 0.0], "right": [0.0, 0.0, 1.0], "edge": [0.5, 0.9], "gain": 2.0, "materials": {"face": 0.0}}
    full = _lit_settings({"rim": {**RIM, "amount": 0.5, "side": [-0.1, 0.1], "down": [-0.9, -0.3], "blend": 1}})["rim"]
    assert full["amount"] == 0.5 and full["side"] == [-0.1, 0.1] and full["down"] == [-0.9, -0.3] and full["blend"] == 1.0


@pytest.mark.parametrize("rim, message", [
    ({"left": [1, 0, 0], "right": [0, 0, 1]}, "edge"),
    ({"right": [0, 0, 1], "edge": [0.5, 0.9]}, "left"),
    ({**RIM, "glow": 1}, "知らない鍵"),
    ({**RIM, "left": [1, 0]}, "rim.left"),
    ({**RIM, "right": [1, -0.1, 0]}, "rim.right"),
    ({**RIM, "edge": [0.9, 0.5]}, "rim.edge"),
    ({**RIM, "gain": -1}, "gain"),
    ({**RIM, "amount": "a"}, "amount"),
    ({**RIM, "materials": {"face": -0.1}}, "rim.materials"),
    ({**RIM, "materials": ["face"]}, "rim.materials"),
    ({**RIM, "side": [0.2, -0.2]}, "rim.side"),
    ({**RIM, "down": [0.1]}, "rim.down"),
    ({**RIM, "blend": 1.5}, "rim.blend"),
    ("on", "rim は辞書"),
])
def test_the_operation_rejects_bad_rim(tmp_path, rim, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "rim": rim})
