"""飛び散る粒（blender_adapter/fx_particles.py）: 寿命の外は 0、寿命の短い粒から消えること、床より下へ行かないこと、同じコマなら同じ形。
値は作り物（Blender なし）。作品の形の照合は docs/model3d.md。"""
import numpy as np

from model3d.blender_adapter import fx_particles, fx_strips
from model3d.blender_adapter.fx_events import Table


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
