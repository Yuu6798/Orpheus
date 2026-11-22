"""面を走る枝分かれ（blender_adapter/fx_surface.py）: 道のりが根もとから先へ増えること、面に寝ること、先端が進むこと、内向きは
外向きの鏡、寿命の外、カメラから見た根もとを止めること、どのコマから作っても同じ形。値は作り物（Blender なし）。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_strips, fx_surface
from model3d.blender_adapter.fx_events import EventError, Table

EVENT = {"frame": 5, "at": [0.5, 1.0, 1.2], "radius": 2.0, "arms": 4, "step": 0.2, "width": 0.05, "depth": 2, "branch": 0.3, "seed": 3,
         "speed": 6.0, "fade": 0.5, "tail": 1.0, "rest": 0.2, "height": 0.01, "light": 50.0, "light_height": 0.3, "light_decay": 0.2,
         "colour": [1.0, 0.5, 0.2]}


def _tree(event=EVENT):
    table = Table({"fps": 30, "frame0": 1, "events": [dict(event)]})
    return table, fx_surface.Tree(table, table.events[0])


def _draw(f, event=EVENT):
    table, tree = _tree(event)
    quads = fx_strips.Quads([0.0, -5.0, 2.0])
    light = tree.draw(table, f, quads)
    return quads.arrays(), light, tree


def test_path_length_grows_from_the_root_and_branches_start_where_they_leave():
    _, tree = _tree()
    ends = {tuple(np.round(s[1], 9)): s[3] for s in tree.segments}
    starts_at_root = 0
    for p, q, d0, d1, w0, w1 in tree.segments:
        assert d1 > d0 >= 0 and w1 <= w0
        if d0 == 0:
            starts_at_root += 1
            assert np.allclose(p, tree.base)
        else:
            assert ends[tuple(np.round(p, 9))] == pytest.approx(d0)      # どの区間も、前の区間の終わり（同じ道のり）から出る
    assert starts_at_root == EVENT["arms"]
    assert np.allclose([s[0][2] for s in tree.segments], 0.01) and np.allclose(tree.base, [0.5, 1.0, 0.01])


def test_front_moves_out_and_the_strips_lie_on_the_surface():
    counts = [len(_draw(f)[0][0]) for f in (5, 7, 9, 13)]
    assert counts[0] < counts[1] < counts[2] <= counts[3]
    (co, _, value), light, tree = _draw(9)
    assert np.allclose(co[..., 2], 0.01) and len(co) <= len(tree.segments)
    energy, position, colour = light
    assert np.allclose(position, tree.base + [0, 0, 0.3]) and colour == (1.0, 0.5, 0.2) and energy > 0


def test_inward_is_the_mirror_of_outward():
    inward = {**EVENT, "inward": True}
    (out_early, _, _), _, tree = _draw(6)
    (in_early, _, _), _, _ = _draw(6, inward)
    longest = tree.longest
    reached = [s for s in tree.segments if longest - s[3] <= (1 / 30 + 1 / 30) * EVENT["speed"]]
    assert len(in_early) == len(reached)                                  # 内向きは、先（道のりの長い所）から始まる
    late = 5 + int(30 * longest / EVENT["speed"]) + 2                     # 全部に届いたあと: 同じ区間を、逆向きに描く
    (out_late, _, _), _, _ = _draw(late)
    (in_late, _, _), _, _ = _draw(late, inward)
    assert len(out_late) == len(in_late) == len(tree.segments)
    out_ends = sorted(map(tuple, np.round((out_late[:, 0] + out_late[:, 1]) / 2, 6)))
    in_ends = sorted(map(tuple, np.round((in_late[:, 2] + in_late[:, 3]) / 2, 6)))
    assert out_ends == in_ends


def test_life_and_bad_root():
    _, tree = _tree()
    end = 5 + int(np.floor(tree.life() * 30))
    assert len(_draw(4)[0][0]) == 0 and len(_draw(end + 1)[0][0]) == 0 and _draw(end + 1)[1] is None
    with pytest.raises(EventError, match="カメラから見た点"):
        _tree({**EVENT, "at": {"camera": [0, 0, 2]}})


def test_shape_does_not_depend_on_which_frame_builds_it():
    (a, _, _), _, _ = _draw(9)
    table, tree = _tree()
    tree.draw(table, 20, fx_strips.Quads([0.0, -5.0, 2.0]))
    quads = fx_strips.Quads([0.0, -5.0, 2.0])
    tree.draw(table, 9, quads)
    assert np.array_equal(a, quads.arrays()[0])
