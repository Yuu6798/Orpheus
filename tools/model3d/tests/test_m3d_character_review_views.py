"""比較カメラの座標と、同じ姿勢での原因切り分けの回帰テスト。"""
from types import SimpleNamespace

import pytest
from model3d.blender_adapter import character_review_views as views


def test_disable_restores_property_even_on_error():
    class Mesh:
        name, type, hide_render = "ornament", "MESH", False
        id_data = SimpleNamespace(animation_data=None)
    target = Mesh()
    with pytest.raises(RuntimeError):
        with views.disabled([target], {"object": "ornament", "hide": True}):
            assert target.hide_render
            raise RuntimeError("render interrupted")
    assert not target.hide_render
    mod = SimpleNamespace(show_render=True, id_data=target.id_data)
    target.modifiers = {"outline": mod}
    with views.disabled([target], {"object": "ornament", "modifier": "outline"}):
        assert not mod.show_render
    assert mod.show_render


def test_render_requires_an_unblocked_collection_path_in_an_enabled_view_layer():
    def collection(name, objects=(), children=(), *, excluded=False, hidden=False):
        return SimpleNamespace(name=name, exclude=excluded, children=children,
                               collection=SimpleNamespace(hide_render=hidden, objects=objects))

    mesh = SimpleNamespace(name="character", type="MESH", hide_render=False)
    nested = collection("nested", [mesh.name])
    hidden = collection("hidden", children=[nested], hidden=True)
    excluded = collection("excluded", children=[nested], excluded=True)
    available = collection("visible", [mesh.name])
    scene = SimpleNamespace(view_layers=[SimpleNamespace(use=True, layer_collection=hidden),
                                         SimpleNamespace(use=True, layer_collection=excluded),
                                         SimpleNamespace(use=False, layer_collection=available)])
    with pytest.raises(ValueError, match="no renderable meshes"):
        views.camera_setup(None, scene, [mesh], 32)
    scene.view_layers[-1].use = True
    assert views.renderable_mesh(scene, mesh)


def test_report_scene_must_contain_the_armature():
    rig = SimpleNamespace(name="rig")
    bpy = SimpleNamespace(data=SimpleNamespace(scenes={"other": SimpleNamespace(objects={})}))
    with pytest.raises(ValueError, match="roundtrip scene 'other' does not contain armature 'rig'"):
        views.use_review_scene(bpy, rig, "other")
