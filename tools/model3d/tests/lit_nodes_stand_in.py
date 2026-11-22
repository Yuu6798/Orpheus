"""look.lit の材質のノードの作りを、Blender なしで調べる代役。型の名前・設定・入れた値・つなぎを、作った順に控える。
Blender 5.2 で同じ名前のソケットが型ごとにあるノード（Mix）は、名前で引くと最初の型（Float）のソケットが返るようにしてある
（型の名前 identifier で引かないと、別の型のソケットにつないだことが分かる）。"""
import hashlib
import json
from types import SimpleNamespace

PRESETS = {
    "ShaderNodeVectorMath": ([("Vector", "Vector"), ("Vector", "Vector_001"), ("Vector", "Vector_002"), ("Scale", "Scale")],
                             [("Vector", "Vector"), ("Value", "Value")]),
    "ShaderNodeMix":([("Factor", "Factor_Float"), ("Factor", "Factor_Vector"), ("A", "A_Float"), ("B", "B_Float"),
                       ("A", "A_Vector"), ("B", "B_Vector"), ("A", "A_Color"), ("B", "B_Color")],
                      [("Result", "Result_Float"), ("Result", "Result_Vector"), ("Result", "Result_Color")]),
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
    def __init__(self, node, preset=()):
        self.node = node
        self._list = [Socket(node, name, ident) for name, ident in preset]

    def __getitem__(self, key):
        if isinstance(key, int):
            while len(self._list) <= key:
                self._list.append(Socket(self.node, f"#{len(self._list)}"))
            return self._list[key]
        for socket in self._list:
            if socket.name == key:
                return socket
        socket = Socket(self.node, key)
        self._list.append(socket)
        return socket

    def __iter__(self):
        return iter(self._list)


class Node:
    def __init__(self, kind):
        self.bl_idname, self.type, self.name, self.label = kind, TYPES.get(kind, kind), kind, ""
        inputs, outputs = PRESETS.get(kind, ((), ()))
        self.inputs, self.outputs = Sockets(self, inputs), Sockets(self, outputs)


class Nodes(list):
    def new(self, kind):
        node = Node(kind)
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
        self.name, self.nodes, self.links, self.interface = name, Nodes(), Links(), Interface()
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
            if hasattr(n, key):
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
