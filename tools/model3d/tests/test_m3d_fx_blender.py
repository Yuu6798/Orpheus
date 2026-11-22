"""出来事の形を Blender に置く側（blender_adapter/fx_blender.py）の受け渡し（代役の bpy）: 四角形からメッシュへの頂点・面・UV・属性と
材質を引き継ぐこと、白黒のコマ、光の書き方、物体の属性の書き方。足し算で光る材質と、物体の属性が材質に届くことは、本物の
Blender 5.2.4 で確かめた（docs/model3d.md。CI には Blender が無い）。"""
from types import SimpleNamespace

from model3d.blender_adapter import fx_blender, fx_strips


class _Collection(list):
    def __init__(self):
        super().__init__()
        self.removed = []

    def new(self, name, *args):
        item = SimpleNamespace(name=name, materials=[], args=args)
        self.append(item)
        return item

    def remove(self, item):
        self.removed.append(item)


class _Mesh(SimpleNamespace):
    def __init__(self, name):
        super().__init__(name=name, materials=[], uv=None, attr=None, pydata=None)
        mesh = self

        class _Data:
            def __init__(self, key):
                self.key = key

            def foreach_set(self, field, values):
                setattr(mesh, self.key, (field, list(values)))
        self.uv_layers = SimpleNamespace(new=lambda name: SimpleNamespace(data=_Data("uv")))
        self.attributes = SimpleNamespace(new=lambda name, kind, domain: SimpleNamespace(data=_Data("attr")))

    def from_pydata(self, verts, edges, faces):
        self.pydata = (verts, edges, faces)


class _Meshes(_Collection):
    def new(self, name):
        mesh = _Mesh(name)
        self.append(mesh)
        return mesh


def test_write_turns_quads_into_a_mesh_and_keeps_the_materials():
    bpy = SimpleNamespace(data=SimpleNamespace(meshes=_Meshes()))
    old = bpy.data.meshes.new("fx_core")
    old.materials.append("glow")
    ob = SimpleNamespace(name="fx_core", data=old)
    quads = fx_strips.Quads([0, -5, 1])
    quads.strip([[0, 0, 1], [1, 0, 1], [2, 0, 1.5]], [0.1, 0.1, 0.05], [1.0, 0.5, 0.2])
    quads.dot([0, 0, 2], 0.2, 3.0)
    assert fx_blender.write(bpy, ob, quads.arrays()) == 3
    verts, _, faces = ob.data.pydata
    assert len(verts) == 12 and faces[2] == (8, 9, 10, 11)
    assert ob.data.uv[0] == "uv" and len(ob.data.uv[1]) == 24 and ob.data.attr[1] == [1.0, 1.0, 0.5, 0.5, 0.5, 0.5, 0.2, 0.2, 3.0, 3.0, 3.0, 3.0]
    assert ob.data.materials == ["glow"] and bpy.data.meshes.removed == [old]
    empty = SimpleNamespace(name="fx_core", data=ob.data)
    assert fx_blender.write(bpy, empty, fx_strips.Quads([0, 0, 0]).arrays()) == 0 and empty.data.pydata is None


def test_mono_frame_lights_and_levels():
    stage = SimpleNamespace(hide_render=False)
    layer = SimpleNamespace(material_override=None)
    scene = SimpleNamespace(view_layers=[layer])
    sockets = [SimpleNamespace(default_value=None), SimpleNamespace(default_value=None)]
    background = SimpleNamespace(inputs=sockets)
    kw = {"hide": [stage], "override": "black", "background": background, "white": ((1, 1, 1), 1.3), "normal": ((0.1, 0.1, 0.2), 1.0)}
    fx_blender.mono_frame(scene, 1.0, **kw)
    assert stage.hide_render and layer.material_override == "black" and sockets[0].default_value == (1, 1, 1, 1) and sockets[1].default_value == 1.3
    fx_blender.mono_frame(scene, -1.0, **kw)
    assert stage.hide_render and layer.material_override is None and sockets[0].default_value == (0.0, 0.0, 0.0, 1)
    fx_blender.mono_frame(scene, 0.0, **kw)
    assert not stage.hide_render and sockets[0].default_value == (0.1, 0.1, 0.2, 1) and sockets[1].default_value == 1.0
    light = SimpleNamespace(location=None, data=SimpleNamespace(energy=1.0, color=None))
    fx_blender.apply_lights([light, SimpleNamespace(data=SimpleNamespace(energy=2.0))], [(8.0, [2.0, 0.0, 4.0], [1, 0, 0]), None], unit=2.0)
    assert light.location == (1.0, 0.0, 2.0) and light.data.energy == 2.0 and light.data.color == (1, 0, 0)

    class _Object(dict):
        tagged = 0

        def update_tag(self):
            self.tagged += 1
    parts = [_Object(), _Object()]
    fx_blender.set_level(parts, 0.5)
    fx_blender.set_level(parts, 0.5)
    assert [p["fx_level"] for p in parts] == [0.5, 0.5] and [p.tagged for p in parts] == [1, 1]
