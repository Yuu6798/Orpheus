"""look.lit の材質のノードの作りを、Blender なしで調べる代役。型の名前・設定・入れた値・つなぎを、作った順に控える。
ノードの型ごとに、Blender 5.2 のソケットの名前と identifier を、同じ並びで持つ（SOCKETS）。知らない名前・範囲の外の番号で引くと
止まる（つづりの間違いを CI で見つける）。同じ名前のソケットが型ごとにあるノード（Mix・Map Range）は、名前で引くと最初の型
（Float）のソケットが返る（Blender と同じ。identifier で引かないと、別の型のソケットにつないだことが分かる）。
ノードグループの出力（ShaderNodeGroup）とグループの出力ノード（NodeGroupOutput）のソケットは、グループの interface から作る。
限界: ソケットの名前は look.lit が使う型だけを、Blender 5.2.4 で見た並びで書き写したもの。使っていないソケット（Weight など）や、
ほかの型は入れていない（知らない型を作ると止まる）。"""
import hashlib
import json
from types import SimpleNamespace


def _same(*names):
    return [(name, name) for name in names]


SOCKETS = {   # 型 → (入力の (名前, identifier) の並び, 出力の並び)
    "ShaderNodeOutputMaterial": (_same("Surface", "Volume", "Displacement", "Thickness"), []),
    "ShaderNodeTexImage": (_same("Vector"), _same("Color", "Alpha")),
    "ShaderNodeRGB": ([], _same("Color")),
    "ShaderNodeValue": ([], _same("Value")),
    "ShaderNodeVertexColor": ([], _same("Color", "Alpha")),
    "ShaderNodeBsdfDiffuse": (_same("Color", "Roughness", "Normal", "Weight"), _same("BSDF")),
    "ShaderNodeShaderToRGB": (_same("Shader"), _same("Color", "Alpha")),
    "ShaderNodeSeparateColor": (_same("Color"), _same("Red", "Green", "Blue")),
    "ShaderNodeMath": ([("Value", "Value"), ("Value", "Value_001"), ("Value", "Value_002")], _same("Value")),
    "ShaderNodeMapRange": ([("Value", "Value"), ("From Min", "From_Min"), ("From Max", "From_Max"), ("To Min", "To_Min"),
                            ("To Max", "To_Max"), ("Steps", "Steps"), ("Vector", "Vector"), ("From Min", "From_Min_FLOAT3"),
                            ("From Max", "From_Max_FLOAT3"), ("To Min", "To_Min_FLOAT3"), ("To Max", "To_Max_FLOAT3"),
                            ("Steps", "Steps_FLOAT3")], _same("Result", "Vector")),
    "ShaderNodeVectorMath": ([("Vector", "Vector"), ("Vector", "Vector_001"), ("Vector", "Vector_002"), ("Scale", "Scale")],
                             _same("Vector", "Value")),
    "ShaderNodeMix": ([("Factor", "Factor_Float"), ("Factor", "Factor_Vector"), ("A", "A_Float"), ("B", "B_Float"),
                       ("A", "A_Vector"), ("B", "B_Vector"), ("A", "A_Color"), ("B", "B_Color"), ("A", "A_Rotation"),
                       ("B", "B_Rotation")],
                      [("Result", "Result_Float"), ("Result", "Result_Vector"), ("Result", "Result_Color"), ("Result", "Result_Rotation")]),
    "ShaderNodeEmission": (_same("Color", "Strength", "Weight"), _same("Emission")),
    "ShaderNodeLayerWeight": (_same("Blend", "Normal"), _same("Fresnel", "Facing")),
    "ShaderNodeNewGeometry": ([], _same("Position", "Normal", "Tangent", "True Normal", "Incoming", "Parametric", "Backfacing",
                                        "Pointiness", "Random Per Island")),
    "ShaderNodeVectorTransform": (_same("Vector"), _same("Vector")),
    "ShaderNodeSeparateXYZ": (_same("Vector"), _same("X", "Y", "Z")),
    "ShaderNodeBsdfPrincipled": (_same("Base Color"), _same("BSDF")),
    "ShaderNodeGroup": ([], []),                                  # 出力は node_tree を入れたときに、グループの interface から
    "NodeGroupOutput": ([], []),                                  # 入力は、グループの interface から
}
TYPES = {"ShaderNodeEmission": "EMISSION", "ShaderNodeTexImage": "TEX_IMAGE", "ShaderNodeRGB": "RGB",
         "ShaderNodeBsdfPrincipled": "BSDF_PRINCIPLED", "ShaderNodeVertexColor": "VERTEX_COLOR", "ShaderNodeGroup": "GROUP"}
SETTINGS = ("operation", "interpolation_type", "data_type", "vector_type", "convert_from", "convert_to", "layer_name",
            "interpolation", "image", "node_tree")


class Socket:
    def __init__(self, node, name, identifier=None):
        self.node, self.name, self.identifier = node, name, identifier or name
        self.default_value = None
        self.links = []


class Sockets:
    """ソケットの並び。名前で引くと、その名前の最初のソケット。無い名前・範囲の外の番号は止める（KeyError・IndexError）。"""

    def __init__(self, node, preset=(), side="inputs"):
        self.node, self.side = node, side
        self._list = [Socket(node, name, ident) for name, ident in preset]

    def _all(self):
        if self.node.bl_idname == "NodeGroupOutput" and self.side == "inputs" and self.node.tree is not None:
            known = {s.identifier for s in self._list}
            for name, _, _ in self.node.tree.interface.sockets:
                if name not in known:
                    self._list.append(Socket(self.node, name))
        return self._list

    def __getitem__(self, key):
        sockets = self._all()
        if isinstance(key, int):
            return sockets[key]
        for socket in sockets:
            if socket.name == key:
                return socket
        raise KeyError(f"{self.node.bl_idname} の {self.side} に {key!r} はありません（あるのは {[s.name for s in sockets]}）")

    def __iter__(self):
        return iter(self._all())


class Node:
    def __init__(self, kind, tree=None):
        if kind not in SOCKETS:
            raise KeyError(f"代役が知らないノードの型です: {kind}（SOCKETS に足す）")
        self.bl_idname, self.type, self.name, self.label, self.tree = kind, TYPES.get(kind, kind), kind, "", tree
        inputs, outputs = SOCKETS[kind]
        self.inputs, self.outputs = Sockets(self, inputs, "inputs"), Sockets(self, outputs, "outputs")
        self._node_tree = None

    @property
    def node_tree(self):
        return self._node_tree

    @node_tree.setter
    def node_tree(self, group):                                   # グループのノード: 出力はグループの interface の OUTPUT
        self._node_tree = group
        self.outputs = Sockets(self, [(name, name) for name, side, _ in group.interface.sockets if side == "OUTPUT"], "outputs")


class Nodes(list):
    def __init__(self, tree=None):
        super().__init__()
        self.tree = tree

    def new(self, kind):
        node = Node(kind, self.tree)
        self.append(node)
        return node

    def clear(self):
        del self[:]

    def get(self, name):
        return next((n for n in self if n.name == name), None)

    def __contains__(self, name):
        return self.get(name) is not None

    def __getitem__(self, key):
        if isinstance(key, str):
            node = self.get(key)
            if node is None:
                raise KeyError(key)
            return node
        return list.__getitem__(self, key)


class Links(list):
    def new(self, a, b):
        link = SimpleNamespace(from_socket=a, to_socket=b, from_node=a.node, to_node=b.node)
        for old in [k for k in self if k.to_socket is b]:          # 入力のソケットのつなぎは 1 本（Blender と同じく置き換える）
            self.remove(old)
            old.from_socket.links.remove(old)
        a.links.append(link)
        b.links = [link]
        self.append(link)
        return link


class Interface:
    def __init__(self):
        self.sockets = []

    def new_socket(self, name, in_out, socket_type):
        self.sockets.append((name, in_out, socket_type))


class Tree:
    def __init__(self, name=""):
        self.name, self.nodes, self.links, self.interface = name, Nodes(self), Links(), Interface()
        self.props = {}

    def __setitem__(self, key, value):
        self.props[key] = value

    def get(self, key, default=None):
        return self.props.get(key, default)


class Material(dict):
    def __init__(self, name):
        super().__init__()
        self.name, self.node_tree, self.use_backface_culling, self.use_nodes = name, Tree(), False, True


class Blocks(list):
    def __init__(self, make):
        super().__init__()
        self.make = make

    def new(self, name, *kind):
        item = self.make(name)
        self.append(item)
        return item

    def get(self, name):
        return next((m for m in self if m.name == name), None)


def fake_bpy():
    return SimpleNamespace(data=SimpleNamespace(materials=Blocks(Material), node_groups=Blocks(Tree)))


def _value(v):
    if isinstance(v, (tuple, list)):
        return [round(float(x), 9) for x in v]
    if isinstance(v, float):
        return round(v, 9)
    if hasattr(v, "name"):
        return v.name
    return v


def describe(tree):
    """作った順のノード（型・設定・入れた値）と、つなぎ（ノードの番号とソケットの identifier）。"""
    index = {id(n): k for k, n in enumerate(tree.nodes)}
    nodes = []
    for n in tree.nodes:
        entry = {"type": n.bl_idname}
        for key in SETTINGS:
            if hasattr(n, key) and not (key == "node_tree" and n.bl_idname != "ShaderNodeGroup"):
                entry[key] = _value(getattr(n, key))
        if n.name != n.bl_idname:
            entry["name"] = n.name
        entry["in"] = {s.identifier: _value(s.default_value) for s in n.inputs if s.default_value is not None}
        entry["out"] = {s.identifier: _value(s.default_value) for s in n.outputs if s.default_value is not None}
        nodes.append(entry)
    links = [[index[id(k.from_node)], k.from_socket.identifier, index[id(k.to_node)], k.to_socket.identifier] for k in tree.links]
    return {"nodes": nodes, "links": links}


def digest(description):
    return hashlib.sha256(json.dumps(description, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def feeding(tree, node, socket):
    """node の入力 socket（identifier か、無ければ名前）につながっている (ノード, 出力ソケット)。無ければ None。"""
    for key in ("identifier", "name"):
        for link in tree.links:
            if link.to_node is node and getattr(link.to_socket, key) == socket:
                return link.from_node, link.from_socket
    return None
