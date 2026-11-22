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


# ---- 材質のノード（代役の node_tree: ノードの型と、つなぎを見る） ----
class _Socket:
    def __init__(self, node, key):
        self.node, self.key, self.default_value, self.links = node, key, None, []

    @property
    def is_linked(self):
        return bool(self.links)


class _Sockets:
    def __init__(self, node):
        self.node, self.items = node, {}

    def __getitem__(self, key):
        if key not in self.items:
            self.items[key] = _Socket(self.node, key)
        if self.node.type == "ShaderNodeMix" and key in ("A", "B"):
            self.node.mix_types.append(self.node.data_type)          # A・B を引いたときの型（型を決めてから引くこと）
        return self.items[key]

    def __iter__(self):
        return iter([self[0], self[1], self[2]])


class _Node:
    def __init__(self, kind):
        self.type, self.name, self.data_type, self.mix_types = kind, kind, None, []
        self.inputs, self.outputs = _Sockets(self), _Sockets(self)


class _Nodes(list):
    def new(self, kind):
        node = _Node(kind)
        self.append(node)
        return node


class _Links(list):
    def new(self, source, target):
        link = SimpleNamespace(from_socket=source, to_socket=target)
        target.links.append(link)
        self.append(link)
        return link


def _material_bpy():
    def new(name):
        return SimpleNamespace(name=name, use_nodes=False, surface_render_method="DITHERED", use_backface_culling=True,
                               node_tree=SimpleNamespace(nodes=_Nodes(), links=_Links()))
    return SimpleNamespace(data=SimpleNamespace(materials=SimpleNamespace(new=new)))


def _one(material, kind):
    found = [n for n in material.node_tree.nodes if n.type == kind]
    assert len(found) == 1, (kind, len(found))
    return found[0]


def _source(socket):
    """socket へつながる元のソケット（つながりは 1 本だけ）。"""
    assert len(socket.links) == 1, socket.key
    return socket.links[0].from_socket


def test_additive_material_glows_by_the_vertex_value_and_the_gain():
    material = fx_blender.additive_material(_material_bpy(), "fx_core", (0.5, 0.3, 1.0), (1, 1, 1), 6.0, soft=2.0)
    assert material.surface_render_method == "BLENDED" and material.use_backface_culling is False
    out, add = _one(material, "ShaderNodeOutputMaterial"), _source(_one(material, "ShaderNodeOutputMaterial").inputs["Surface"]).node
    assert add.type == "ShaderNodeAddShader"
    assert _source(add.inputs[0]).node.type == "ShaderNodeBsdfTransparent" and _source(add.inputs[1]).node.type == "ShaderNodeEmission"
    emission = _one(material, "ShaderNodeEmission")
    times_gain = _source(emission.inputs["Strength"]).node
    assert times_gain.type == "ShaderNodeMath" and times_gain.operation == "MULTIPLY"
    shape_times_value, gain = (_source(times_gain.inputs[k]).node for k in (0, 1))
    assert gain.type == "ShaderNodeValue" and gain.name == "fx_gain" and gain.outputs[0].default_value == 6.0
    assert shape_times_value.operation == "MULTIPLY"
    attribute = _source(shape_times_value.inputs[1]).node
    assert (attribute.type, attribute.attribute_type, attribute.attribute_name) == ("ShaderNodeAttribute", "GEOMETRY", "fx")
    power = _source(shape_times_value.inputs[0]).node
    assert power.operation == "POWER" and power.inputs[1].default_value == 2.0
    mix = _one(material, "ShaderNodeMix")
    assert mix.mix_types and set(mix.mix_types) == {"RGBA"}
    assert mix.inputs["A"].default_value == (0.5, 0.3, 1.0, 1) and mix.inputs["B"].default_value == (1, 1, 1, 1)
    white = _source(mix.inputs["Factor"]).node
    assert white.type == "ShaderNodeMapRange" and white.interpolation_type == "SMOOTHSTEP"
    assert (white.inputs["From Min"].default_value, white.inputs["From Max"].default_value) == (0.50, 0.90)
    uv = _source(_one(material, "ShaderNodeSeparateXYZ").inputs[0])
    assert uv.node.type == "ShaderNodeTexCoord" and uv.key == "UV"
    assert _source(_one(material, "ShaderNodeEmission").inputs["Color"]).node is mix and out is not None


def test_disc_and_flat_materials():
    disc = fx_blender.disc_material(_material_bpy(), "fx_orb", (0.5, 0.3, 1.0), (1, 1, 1), 5.0, power=3.0)
    assert disc.surface_render_method == "BLENDED"
    assert sorted(n.operation for n in disc.node_tree.nodes if n.type == "ShaderNodeVectorMath") == ["LENGTH", "SUBTRACT"]
    ranges = [n for n in disc.node_tree.nodes if n.type == "ShaderNodeMapRange"]
    inside = next(n for n in ranges if n.inputs["From Min"].default_value == 0.5)
    assert inside.inputs["From Max"].default_value == 0.0 and not hasattr(inside, "interpolation_type")   # 直線（ふちへ消える）
    assert any(n.type == "ShaderNodeMath" and n.operation == "POWER" and n.inputs[1].default_value == 3.0 for n in disc.node_tree.nodes)
    flat = fx_blender.flat_material(_material_bpy(), "black", (0.0, 0.0, 0.0))
    emission = _one(flat, "ShaderNodeEmission")
    assert emission.inputs["Color"].default_value == (0.0, 0.0, 0.0, 1)
    assert _source(_one(flat, "ShaderNodeOutputMaterial").inputs["Surface"]).node is emission


def test_drive_by_object_multiplies_the_socket_by_the_object_attribute():
    bpy = _material_bpy()
    material = fx_blender.flat_material(bpy, "panel", (1, 1, 1))
    emission = _one(material, "ShaderNodeEmission")
    emission.inputs["Strength"].default_value = 0.8
    base = fx_blender.drive_by_object(bpy, material, emission.inputs["Strength"])
    multiply = _source(emission.inputs["Strength"]).node
    assert multiply.operation == "MULTIPLY" and _source(multiply.inputs[0]).node is base and base.outputs[0].default_value == 0.8
    attribute = _source(multiply.inputs[1])
    assert attribute.key == "Fac" and (attribute.node.attribute_type, attribute.node.attribute_name) == ("OBJECT", "fx_level")
    linked = fx_blender.flat_material(bpy, "linked", (1, 1, 1))                  # 口に線がつながっていれば、その元 × 属性
    upstream = linked.node_tree.nodes.new("ShaderNodeValue").outputs[0]
    strength = _one(linked, "ShaderNodeEmission").inputs["Strength"]
    linked.node_tree.links.new(upstream, strength)
    assert fx_blender.drive_by_object(bpy, linked, strength, "level") is None
    multiply = strength.links[-1].from_socket.node
    assert _source(multiply.inputs[0]) is upstream and _source(multiply.inputs[1]).node.attribute_name == "level"
