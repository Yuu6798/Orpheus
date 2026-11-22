"""溜めて・撃って・余韻（blender_adapter/fx_charge.py）: 3 段の切り替わりと段ごとの形、撃つ芯の向きと長さ、白黒のコマで玉・にじみ・光条を
作らないこと、同じコマなら同じ形。値は作り物（Blender なし）。作品の形の照合は docs/model3d.md。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_charge, fx_strips
from model3d.blender_adapter.fx_events import EventError, Table

CAMERA = {"matrix": np.eye(4).tolist(), "position": [0.0, -4.0, 1.4]}
SHOT = {"frame": 30, "tip": [0.3, 0.0, 1.5], "base": [0.0, 0.0, 1.4], "charge": 0.3, "hold": 0.2, "after": 0.3, "flash": 0.1, "ring": 0.15,
        "length": 3.0, "width": 0.03, "size": 1.0, "halo": 4.0, "ring_speed": 3.0, "wraps": 3, "forks": 2, "motes": 10, "seed": 8,
        "light": 200.0, "colour": [0.8, 0.8, 1.0]}
LAYERS = ("orb", "core", "halo", "fine", "motes")


def _shot(f, mono=0.0, event=SHOT):
    table = Table({"fps": 30, "events": [dict(event)]})
    layers = {k: fx_strips.Quads(CAMERA["position"]) for k in LAYERS}
    glows = fx_charge.draw(table, table.events[0], f, CAMERA, layers, 1.0, mono)
    return glows, {k: len(v) for k, v in layers.items()}, layers


def test_three_stages():
    assert _shot(30 - 10)[0] == []                                        # 溜めの前（0.3 秒 = 9 コマ前から）
    glows, counts, _ = _shot(25)                                          # 溜め
    assert counts["orb"] == 1 and counts["fine"] > 0 and counts["motes"] == SHOT["motes"] and counts["core"] == 0
    assert glows[0][2] == (0.8, 0.8, 1.0) and glows[0][0] > 0
    glows, counts, layers = _shot(31)                                     # 撃つ（閃光の間）
    assert counts["core"] > 0 and counts["halo"] == 13 and counts["orb"] == 1 and counts["fine"] > 0
    co = layers["core"].arrays()[0]
    beam = (co[:13, 0] + co[:13, 1]) / 2                                  # 芯の帯の真ん中は、根もと → 先の向きに並ぶ
    aim = np.array(SHOT["tip"]) - np.array(SHOT["base"])
    aim /= np.linalg.norm(aim)
    assert np.allclose(np.cross(beam - SHOT["tip"], aim), 0, atol=1e-12)
    assert np.allclose(np.linalg.norm(beam[-1] - SHOT["tip"]), SHOT["length"] * (12 / 13) ** 1.6)
    glows, counts, _ = _shot(30 + 9)                                      # 余韻
    assert counts["core"] == 0 and counts["fine"] > 0 and counts["orb"] == 1 and glows[0][1].tolist() == SHOT["tip"]
    assert _shot(30 + 16)[0] == []                                        # 撃って 0.2 + 余韻 0.3 秒 = 15 コマの後


def test_mono_frames_drop_the_soft_parts():
    _, plain, _ = _shot(31)
    _, white, _ = _shot(31, 1.0)
    _, dark, _ = _shot(31, -1.0)
    assert white["orb"] == 0 and white["halo"] == 0 and dark["orb"] == 1 and dark["halo"] == 13
    assert plain["core"] - white["core"] == 4 * 4 and plain["core"] - dark["core"] == 4 * 4      # 光条 4 本 × 4 区間
    _, charge_white, _ = _shot(25, 1.0)
    assert charge_white["orb"] == 0 and charge_white["fine"] > 0


def test_same_frame_gives_the_same_shape():
    one, two = _shot(31)[2], _shot(31)[2]
    assert all(np.array_equal(one[k].arrays()[0], two[k].arrays()[0]) for k in LAYERS)


def test_half_mono_is_an_ordinary_frame():
    """ちょうど ±0.5 は、fx_blender.mono_frame と同じく、ふだんのコマ（玉・にじみ・光条を作る）。"""
    _, plain, _ = _shot(31)
    assert _shot(31, 0.5)[1] == plain and _shot(31, -0.5)[1] == plain


def test_shooting_straight_up_still_has_a_ring_and_wraps():
    """真上へ撃つ（根もとの真上に先）と、輪と巻きつく線が芯から離れる。カメラが先の真上でも玉の大きさがある。"""
    up = {**SHOT, "tip": [0.0, 0.0, 2.0], "base": [0.0, 0.0, 1.0]}
    _, counts, layers = _shot(31, event=up)
    fine = layers["fine"].arrays()[0]
    away = np.linalg.norm(fine.reshape(-1, 3)[:, :2], axis=1)            # 芯（z の軸）からの距離
    assert counts["fine"] > 0 and away.max() > 0.05
    table = Table({"fps": 30, "events": [dict(up)]})
    layers = {k: fx_strips.Quads([0.0, 0.0, 9.0]) for k in LAYERS}
    fx_charge.draw(table, table.events[0], 31, {"matrix": np.eye(4).tolist(), "position": [0.0, 0.0, 9.0]}, layers)
    orb = layers["orb"].arrays()[0][0]
    assert np.linalg.norm(orb[1] - orb[0]) > 0


@pytest.mark.parametrize("change, message", [
    ({"hold": 0}, "hold"), ({"charge": 0}, "charge"), ({"after": 0}, "after"), ({"flash": 0}, "flash"), ({"ring": -1}, "ring"),
    ({"seed": "8"}, "整数"), ({"seed": 8.5}, "整数"),
])
def test_bad_events_stop(change, message):
    with pytest.raises(EventError, match=message):
        _shot(31, event={**SHOT, **change})
