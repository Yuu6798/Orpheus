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
