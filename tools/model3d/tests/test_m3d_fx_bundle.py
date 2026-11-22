"""折れ線の束（blender_adapter/fx_bundle.py）: 同じコマなら同じ形、根もとと先、大きな形は種だけで決まること、伸びる → 光る → 消える、
寿命の外とそのコマだけに出る出来事、にじみ、光の候補。値は作り物（Blender なし）。作品の形の照合は docs/model3d.md。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_bundle, fx_strips
from model3d.blender_adapter.fx_events import EventError, Table

CAMERA = {"matrix": np.eye(4).tolist(), "position": [0.0, -6.0, 1.5]}
EVENT = {"frame": 10, "from": [0.0, 0.0, 1.0], "to": [1.5, 0.5, 3.0], "width": 0.04, "jag": 0.2, "levels": [3, 2], "forks": 3,
         "fork_length": [0.2, 0.4], "seed": 17, "grow": 0.1, "hold": 0.1, "fade": 0.2, "every": 2, "halo": 4.0,
         "light": 100.0, "light_at": 0.8, "colour": [0.5, 0.6, 1.0]}


def _draw(f, event=EVENT, values=None, halo=True):
    table = Table({"fps": 30, "frame0": 1, "events": [dict(event)]})
    core, glow = fx_strips.Quads(CAMERA["position"]), fx_strips.Quads(CAMERA["position"])
    light = fx_bundle.draw(table, table.events[0], f, CAMERA, core, glow if halo else None, 1.0, values)
    return core.arrays(), glow.arrays(), light


def test_same_frame_gives_the_same_shape():
    one, two = _draw(14), _draw(14)
    _draw(20)
    assert all(np.array_equal(a, b) for a, b in zip(one[0], two[0])) and np.array_equal(one[2][1], two[2][1])


def test_trunk_runs_from_root_to_tip_and_the_big_shape_depends_only_on_the_seed():
    lines = fx_bundle.strands(EVENT["from"], EVENT["to"], 17, 1, 0.2, (3, 2), 3, 0.04, (0.2, 0.4))
    other = fx_bundle.strands(EVENT["from"], EVENT["to"], 17, 2, 0.2, (3, 2), 3, 0.04, (0.2, 0.4))
    assert np.allclose(lines[0]["points"][0], EVENT["from"]) and np.allclose(lines[0]["points"][-1], EVENT["to"])
    forks = [line for line in lines if line["taper"] == fx_bundle.FORM["taper"][1]]
    assert len(forks) == 3
    for mine, theirs in zip(lines[1:], other[1:]):                       # 枝の出どころは、細かい折れ（tick）で動かない
        assert np.allclose(mine["points"][0], theirs["points"][0])
    assert not np.allclose(lines[0]["points"][1], other[0]["points"][1])   # 細かい折れは替わる
    assert 0 < min(line["start"] for line in lines[1:]) < 1


def test_grows_then_holds_then_fades():
    lengths = []
    for f in (10, 11, 12):                                                # 伸びる（0.1 秒 = 3 コマ）
        (co, _, value), _, _ = _draw(f)
        lengths.append(len(co))
    assert lengths[0] < lengths[1] <= lengths[2]
    (_, _, grow), _, _ = _draw(10)
    (_, _, hold), _, _ = _draw(14)
    (_, _, fade), _, _ = _draw(20)
    assert grow.max() == pytest.approx(fx_bundle.FORM["grow_level"]) and hold.max() == pytest.approx(1.0)
    assert 0 < fade.max() < 0.7


def test_outside_its_life_and_without_its_value_nothing_is_drawn():
    assert len(_draw(9)[0][0]) == 0 and len(_draw(23)[0][0]) == 0 and _draw(23)[2] is None     # 0.4 秒 = 12 コマの後
    gated = {**EVENT, "when": "mono"}
    assert len(_draw(14, gated, {"mono": 0.0})[0][0]) == 0 and len(_draw(14, gated, {"mono": 1.0})[0][0]) > 0


def test_halo_is_wider_and_optional_and_light_sits_on_the_line():
    (co, _, _), (halo, _, _), light = _draw(14)
    assert len(halo) == len(co)
    core_width = np.linalg.norm(co[:, 1] - co[:, 0], axis=1)
    halo_width = np.linalg.norm(halo[:, 1] - halo[:, 0], axis=1)
    assert np.allclose(halo_width, core_width * 4.0)
    assert len(_draw(14, halo=False)[1][0]) == 0
    energy, position, colour = light
    assert energy == pytest.approx(100.0) and colour == (0.5, 0.6, 1.0)
    assert np.allclose(position, np.array(EVENT["from"]) + (np.array(EVENT["to"]) - np.array(EVENT["from"])) * 0.8)


def test_missing_fields_stop():
    event = {k: v for k, v in EVENT.items() if k != "width"}
    with pytest.raises(EventError, match="width"):
        _draw(14, event)


@pytest.mark.parametrize("change, message", [
    ({"seed": "17"}, "整数"), ({"seed": 3.7}, "整数"), ({"levels": [0, 2]}, "1 以上"), ({"levels": [3]}, "levels"),
    ({"levels": [3, 1.5]}, "0 以上の整数"), ({"forks": 2.5}, "0 以上の整数"),
])
def test_bad_seed_levels_and_forks_stop(change, message):
    with pytest.raises(EventError, match=message):
        _draw(14, {**EVENT, **change})


def test_a_straight_trunk_without_forks_is_drawn():
    (co, _, _), _, _ = _draw(14, {**EVENT, "levels": [0, 0], "forks": 0})
    assert len(co) == 1                                                   # 折らない幹は 1 区間


ARGS = (EVENT["from"], EVENT["to"], 17, 1, 0.2, (3, 2), 3, 0.04, (0.2, 0.4))


def test_branch_start_by_index_is_the_default_and_unchanged():
    """既定（index）は、台本と同じく出どころの折れ目の番号の割合（幹 9 点の i / 8。孫の枝は親の真ん中）。"""
    default, index = fx_bundle.strands(*ARGS), fx_bundle.strands(*ARGS, branch_start="index")
    assert [line["start"] for line in default] == [0.0, 0.75, 0.875, 0.125, 0.5625, 0.75, 0.875]
    assert all(np.array_equal(a["points"], b["points"]) and a["start"] == b["start"] for a, b in zip(default, index))
    for f in (10, 11, 12, 14):
        explicit = _draw(f, {**EVENT, "branch_start": "index"})
        assert all(np.array_equal(a, b) for a, b in zip(_draw(f)[0], explicit[0]))


def test_branch_start_by_length_meets_the_growing_tip():
    """length では、親の線をその割合で切った先端が、枝（孫の枝）の出どころと重なる。線の形は index と同じ。"""
    lines = fx_bundle.strands(*ARGS, branch_start="length")
    index = fx_bundle.strands(*ARGS)
    assert all(np.array_equal(a["points"], b["points"]) for a, b in zip(lines, index))
    trunk, parent = lines[0], None
    forks = twigs = 0
    for line in lines[1:]:
        if line["taper"] == fx_bundle.FORM["taper"][1]:                 # 枝: 幹の先端が出どころに届いた所で出る
            tip = fx_strips.cut(trunk["points"], line["start"])[-1]
            parent, forks = line, forks + 1
        else:                                                             # 孫の枝: 親の枝の先端が出どころに届いた所で出る
            tip = fx_strips.cut(parent["points"], (line["start"] - parent["start"]) / (1 - parent["start"]))[-1]
            twigs += 1
        assert np.allclose(tip, line["points"][0], atol=1e-9)
    assert forks == 3 and twigs == 3
    assert [line["start"] for line in lines] != [line["start"] for line in index]
    with pytest.raises(EventError, match="branch_start"):
        _draw(14, {**EVENT, "branch_start": "arc"})
