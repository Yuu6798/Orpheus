"""blender5_safe（Blender 5.2 安全化ヘルパー）の単体テスト。"""
import numpy as np
import pytest

from model3d.blender_adapter import blender5_safe as b5


class _FakeSocket:
    def __init__(self, name, default_value=None):
        self.name = name
        self.default_value = default_value


class _FakeSocketCollection:
    def __init__(self, sockets):
        self._list = list(sockets)
        self._by_name = {s.name: s for s in self._list}

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._list[key]
        return self._by_name[key]

    def __contains__(self, name):
        return name in self._by_name


class _FakeNode:
    def __init__(self, node_type):
        self.type = node_type
        self.name = node_type
        self.operation = ""
        if node_type == "ShaderNodeVectorMath":
            # inputs[1] は未使用 Vector B、inputs[3] が Scale（Blender 実仕様を再現）
            self.inputs = _FakeSocketCollection([
                _FakeSocket("Vector", (0.0, 0.0, 0.0)),
                _FakeSocket("Vector_B", (0.0, 0.0, 0.0)),
                _FakeSocket("Vector_C", (0.0, 0.0, 0.0)),
                _FakeSocket("Scale", 1.0),
            ])
            self.outputs = _FakeSocketCollection([_FakeSocket("Vector")])
        elif node_type == "ShaderNodeMath":
            self.inputs = _FakeSocketCollection([_FakeSocket("Value_0", 0.5), _FakeSocket("Value_1", 0.5)])
            self.outputs = _FakeSocketCollection([_FakeSocket("Value")])
        elif node_type == "ShaderNodeBackground":
            self.inputs = _FakeSocketCollection([_FakeSocket("Color", (0, 0, 0, 1)), _FakeSocket("Strength", 1.0)])
            self.outputs = _FakeSocketCollection([_FakeSocket("Background")])
        elif node_type == "ShaderNodeOutputWorld":
            self.inputs = _FakeSocketCollection([_FakeSocket("Surface")])
            self.outputs = _FakeSocketCollection([])
        elif node_type == "ShaderNodeBsdfPrincipled":
            self.inputs = _FakeSocketCollection([
                _FakeSocket("Base Color", (0.8, 0.8, 0.8, 1.0)),
                _FakeSocket("Metallic", 0.0),
                _FakeSocket("Roughness", 0.5),
                _FakeSocket("Coat Weight", 0.0),
                _FakeSocket("Coat Roughness", 0.03),
            ])
            self.outputs = _FakeSocketCollection([_FakeSocket("BSDF")])
        elif node_type == "ShaderNodeOutputMaterial":
            self.inputs = _FakeSocketCollection([_FakeSocket("Surface")])
            self.outputs = _FakeSocketCollection([])
        else:
            self.inputs = _FakeSocketCollection([])
            self.outputs = _FakeSocketCollection([])


class _FakeNodes:
    def __init__(self, initial=None):
        self._items = dict(initial or {})

    def clear(self):
        self._items.clear()

    def new(self, node_type):
        n = _FakeNode(node_type)
        key = f"{node_type}_{len(self._items)}"
        self._items[key] = n
        return n

    def __getitem__(self, key):
        return self._items[key]


class _FakeLinks:
    def __init__(self):
        self.edges = []

    def new(self, from_socket, to_socket):
        self.edges.append((from_socket, to_socket))


class _FakeNodeTree:
    def __init__(self, initial=None):
        self.nodes = _FakeNodes(initial)
        self.links = _FakeLinks()


class _FakeKeyPoint:
    def __init__(self):
        self.interpolation = "BEZIER"


class _FakeKeyPoints:
    def __init__(self):
        self._pts = []
        self.co = None

    def add(self, n):
        self._pts.extend(_FakeKeyPoint() for _ in range(n))

    def foreach_set(self, prop, vals):
        assert prop == "co"
        self.co = np.array(vals, dtype=np.float32)

    def __iter__(self):
        return iter(self._pts)


class _FakeFCurve:
    def __init__(self, data_path, index):
        self.data_path = data_path
        self.array_index = index
        self.keyframe_points = _FakeKeyPoints()
        self.updated = False

    def update(self):
        self.updated = True


class _FakeFCurves:
    def __init__(self):
        self._items = []

    def new(self, data_path, index=0):
        fc = _FakeFCurve(data_path, index)
        self._items.append(fc)
        return fc

    def remove(self, fc):
        self._items.remove(fc)

    def __iter__(self):
        return iter(self._items)

    def __len__(self):
        return len(self._items)


class _FakeChannelBag:
    def __init__(self):
        self.fcurves = _FakeFCurves()


class _FakeStrip:
    def __init__(self):
        self.channelbags = [_FakeChannelBag()]


class _FakeLayer:
    def __init__(self):
        self.strips = [_FakeStrip()]


class _FakeAction52:
    def __init__(self, name):
        self.name = name
        self.layers = []

    def _ensure_native_seed(self):
        if not self.layers:
            self.layers.append(_FakeLayer())
        cb = self.layers[0].strips[0].channelbags[0]
        cb.fcurves.new('pose.bones["root"].rotation_quaternion', 0)


class _FakeAction4x:
    def __init__(self, name):
        self.name = name
        self.fcurves = _FakeFCurves()

    def _ensure_native_seed(self):
        self.fcurves.new('pose.bones["root"].rotation_quaternion', 0)


class _FakeAnimData:
    def __init__(self):
        self.action = None

    @property
    def action_slot(self):
        return None

    @action_slot.setter
    def action_slot(self, _value):
        raise AssertionError("action_slot への手動代入は Blender 5.2 でクラッシュするため禁止")


class _FakePoseBone:
    def __init__(self, arm):
        self._arm = arm

    def keyframe_insert(self, data_path, frame):
        assert data_path == "rotation_quaternion" and frame == 1
        self._arm.animation_data.action._ensure_native_seed()


class _FakePose:
    def __init__(self, arm):
        self.bones = [_FakePoseBone(arm)]


class _FakeArmature:
    def __init__(self, name="Rig", initially_no_pose=False):
        self.name = name
        self.pose = None if initially_no_pose else _FakePose(self)
        self.animation_data = None

    def animation_data_create(self):
        self.animation_data = _FakeAnimData()


class _FakeBpy:
    def __init__(self, action_cls, arm):
        self.view_layer_updates = 0
        self._arm = arm

        class _ViewLayer:
            def update(_self):
                self.view_layer_updates += 1
                self._arm.pose = _FakePose(self._arm)

        class _Context:
            view_layer = _ViewLayer()

        class _Actions:
            def new(_self, name):
                return action_cls(name)

        class _Data:
            actions = _Actions()

        self.context = _Context()
        self.data = _Data()


def test_enforce_quat_continuity_2d_and_3d():
    q2 = np.array([[1, 0, 0, 0], [-1, 0, 0, 0], [-1, 0, 0, 0], [1, 0, 0, 0]], dtype=np.float64)
    out2 = b5.enforce_quat_continuity(q2)
    assert np.all(out2[:, 0] > 0)
    # 元配列を変更しない
    assert q2[1, 0] == -1.0

    q3 = np.array([
        [[1, 0, 0, 0], [0, 1, 0, 0]],
        [[-1, 0, 0, 0], [0, -1, 0, 0]],
        [[1, 0, 0, 0], [0, -1, 0, 0]],
    ], dtype=np.float64)
    out3 = b5.enforce_quat_continuity(q3)
    for t in range(1, len(out3)):
        dots = np.sum(out3[t - 1] * out3[t], axis=-1)
        assert np.all(dots >= 0.0)


def test_enforce_quat_continuity_rejects_bad_shape_and_non_finite():
    with pytest.raises(ValueError):
        b5.enforce_quat_continuity(np.zeros((4, 3)))
    with pytest.raises(ValueError):
        b5.enforce_quat_continuity(np.array([[1.0, 0.0, 0.0, np.nan]]))


def test_write_linear_fcurve_rejects_non_finite():
    cb = _FakeChannelBag()
    with pytest.raises(ValueError):
        b5.write_linear_fcurve(cb, 'pose.bones["hips"].location', 0, [1.0, 2.0], [0.0, float("inf")])


def test_init_layered_action_blender52_and_4x():
    arm52 = _FakeArmature("Clone_L", initially_no_pose=True)
    bpy52 = _FakeBpy(_FakeAction52, arm52)
    act, container = b5.init_layered_action_channelbag(bpy52, arm52, "Test52")
    assert bpy52.view_layer_updates == 1
    assert arm52.animation_data.action is act
    assert len(container.fcurves) == 0

    fc = b5.write_linear_fcurve(container, 'pose.bones["hips"].location', 2, [1, 2, 3], [0.0, 0.5, 1.0])
    assert fc.updated is True
    assert list(fc.keyframe_points.co) == [1.0, 0.0, 2.0, 0.5, 3.0, 1.0]
    assert all(kp.interpolation == "LINEAR" for kp in fc.keyframe_points)

    # Blender 4.x フォールバックも同様に動作
    arm4 = _FakeArmature("Rig4")
    bpy4 = _FakeBpy(_FakeAction4x, arm4)
    act4, container4 = b5.init_layered_action_channelbag(bpy4, arm4, "Test4x")
    assert container4 is act4 and len(container4.fcurves) == 0


def test_locale_independent_world_and_principled_builders():
    # Blender 4.x 相当: use_nodes=False の間は node_tree=None、日本語ノード名ツリーでも失敗しない
    class _World4:
        def __init__(self):
            self.use_nodes = False
            self._nt = _FakeNodeTree({"背景": _FakeNode("ShaderNodeBackground")})

        @property
        def node_tree(self):
            return self._nt if self.use_nodes else None

    w = _World4()
    bg, out = b5.clear_and_build_world_bg(w, (0.01, 0.02, 0.03), strength=1.5)
    assert w.use_nodes is True
    assert bg.inputs[0].default_value == (0.01, 0.02, 0.03, 1.0)
    assert bg.inputs[1].default_value == 1.5
    assert (bg.outputs[0], out.inputs[0]) in w.node_tree.links.edges

    # Blender 5.2+ 相当: 既に node_tree があれば非推奨の use_nodes へ触れない
    class _Mat52:
        def __init__(self):
            self.node_tree = _FakeNodeTree({"プリンシプルBSDF": _FakeNode("ShaderNodeBsdfPrincipled")})

        @property
        def use_nodes(self):
            raise AssertionError("node_tree が既に存在する場合は非推奨 use_nodes を参照しないこと")

    m = _Mat52()
    bsdf, mout = b5.clear_and_build_principled(m, (0.02, 0.02, 0.03), metallic=0.3, roughness=0.05, coat_weight=0.8)
    assert bsdf.inputs["Base Color"].default_value == (0.02, 0.02, 0.03, 1.0)
    assert bsdf.inputs["Metallic"].default_value == 0.3
    assert bsdf.inputs["Roughness"].default_value == 0.05
    assert bsdf.inputs["Coat Weight"].default_value == 0.8
    assert (bsdf.outputs["BSDF"], mout.inputs["Surface"]) in m.node_tree.links.edges


def test_make_vector_scale_and_lerp_bind_scale_socket_explicitly():
    nt = _FakeNodeTree()
    va, vb, fac = _FakeSocket("A"), _FakeSocket("B"), _FakeSocket("Fac")

    out_s = b5.make_vector_scale(nt, va, fac)
    assert out_s.name == "Vector"
    # inputs[1] (Vector_B) ではなく必ず Scale ソケットに繋がること
    to_names = [dst.name for _, dst in nt.links.edges]
    assert "Scale" in to_names
    assert "Vector_B" not in to_names

    nt2 = _FakeNodeTree()
    out_l = b5.make_vector_lerp(nt2, va, vb, fac)
    assert out_l.name == "Vector"
    to_names2 = [dst.name for _, dst in nt2.links.edges]
    assert to_names2.count("Scale") == 2
    # Vector_B は最後の ADD ノードの inputs[1] のみ（SCALE ノードの inputs[1] には繋がらない）
    assert to_names2.count("Vector_B") == 1
    scale_nodes = [n for n in nt2.nodes._items.values() if n.operation == "SCALE"]
    assert len(scale_nodes) == 2
    for sn in scale_nodes:
        dsts = [dst for _, dst in nt2.links.edges if dst in sn.inputs._list]
        assert {d.name for d in dsts} == {"Vector", "Scale"}


def test_set_blended_alpha_supports_eevee_next_and_legacy():
    class _MatNext:
        surface_render_method = "DITHERED"

    class _MatOld:
        blend_method = "OPAQUE"

    m1 = b5.set_blended_alpha(_MatNext())
    m2 = b5.set_blended_alpha(_MatOld())
    assert m1.surface_render_method == "BLENDED"
    assert m2.blend_method == "BLEND"
