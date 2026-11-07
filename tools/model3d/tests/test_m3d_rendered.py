"""メッシュを選ぶ工程（床に足を付ける・手を押し出す・揺れ物の当たり・セル調の塗り）の、レンダーに出るメッシュの見分け方。
台本は 1 本ずつ Blender で動くので同じ関数を写して持つ。写しが同じであることと、コレクションの隠しを見ることを確かめる。"""
import inspect
from types import SimpleNamespace as M

import pytest

from model3d.blender_adapter import ground_feet, push_hands, spring_bones, toon_look

SCRIPTS = (ground_feet, push_hands, spring_bones, toon_look)


def test_every_script_carries_the_same_rule():
    assert len({inspect.getsource(script.rendered) for script in SCRIPTS}) == 1


def _layer(names, children=(), hide=False, exclude=False):
    return M(exclude=exclude, collection=M(hide_render=hide, objects=set(names)), children=list(children))


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda s: s.__name__.rsplit(".", 1)[-1])
def test_rendered_follows_collection_hiding_and_view_layer_exclusion(script):
    root = _layer([], [_layer(["body", "both"]), _layer(["alt", "both"], hide=True), _layer(["excl"], exclude=True),
                       _layer([], [_layer(["nested"])], hide=True)])     # 隠したコレクションの子のコレクションも隠れる
    view = M(use=True, layer_collection=root)
    unused = M(use=False, layer_collection=_layer(["alt"]))             # 使わないビューレイヤーは見ない
    picked = [n for n in ("body", "both", "alt", "excl", "nested", "loose")
              if script.rendered(M(name=n, hide_render=False), [view, unused])]
    assert picked == ["body", "both"]                                    # 隠した所と見える所の両方にあれば出る
    assert not script.rendered(M(name="body", hide_render=True), [view])
