"""飛び散る粒（blender_adapter/fx_particles.py）: 寿命の外は 0、寿命の短い粒から消えること、床より下へ行かないこと、同じコマなら同じ形、
抵抗なし、カメラから見た出どころと悪い種を止めること。値は作り物（Blender なし）。作品の形の照合は docs/model3d.md。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_particles, fx_strips
from model3d.blender_adapter.fx_events import EventError, Table

CAMERA = {"matrix": np.eye(4).tolist(), "position": [0.0, -4.0, 1.4]}
BURST = {"frame": 10, "at": [0.0, 0.0, 0.3], "count": 40, "speed": [1.0, 3.0], "up": 0.5, "life": 0.5, "drag": 1.5, "gravity": 9.8,
         "floor": 0.02, "streak": 0.03, "size": 0.01, "seed": 4}


def _burst(f, event=BURST):
    table = Table({"fps": 30, "events": [dict(event)]})
    quads = fx_strips.Quads(CAMERA["position"])
    return fx_particles.draw(table, table.events[0], f, CAMERA, quads), quads.arrays()


def test_particles_live_their_life_and_stay_above_the_floor():
    assert _burst(9)[0] == 0 and _burst(10 + 25)[0] == 0                  # 寿命 0.5 × 1.6 秒 = 24 コマ
    drawn, (co, _, value) = _burst(14)
    assert 0 < drawn <= BURST["count"] and len(co) == drawn and (value >= 0).all()
    assert (co[..., 2] >= BURST["floor"] - BURST["size"]).all()
    late, _ = _burst(30)
    assert late < drawn                                                   # 寿命の短い粒から消える
    assert np.array_equal(_burst(14)[1][0], co)


def test_no_drag_moves_in_straight_lines():
    """抵抗 0 は、抵抗の小さい極限と同じ所（進む距離 = 速さ × 年齢）。"""
    _, (free, _, _) = _burst(14, {**BURST, "drag": 0, "gravity": 0, "floor": None})
    _, (tiny, _, _) = _burst(14, {**BURST, "drag": 1e-9, "gravity": 0, "floor": None})
    assert np.allclose(free, tiny, atol=1e-6)


@pytest.mark.parametrize("change, message", [
    ({"at": {"camera": [0, 0, 2]}}, "カメラから見た点"), ({"drag": -1}, "drag"), ({"seed": "4"}, "整数"), ({"seed": 4.5}, "整数"),
])
def test_bad_events_stop(change, message):
    with pytest.raises(EventError, match=message):
        _burst(14, {**BURST, **change})
