"""出来事の光（blender_adapter/fx_lights.py）: 強い順・同じ強さの順・しきい・候補が少ないとき。値は作り物（Blender なし）。"""
from model3d.blender_adapter import fx_lights


def test_lights_go_to_the_strongest():
    glows = [(5.0, [0, 0, 1], [1, 1, 1]), None, (50.0, [1, 0, 1], [1, 0, 0]), (0.01, [2, 0, 0], [0, 1, 0]), (5.0, [3, 0, 0], [0, 0, 1])]
    assigned = fx_lights.assign(glows, 4, 0.05)
    assert [a[0] if a else None for a in assigned] == [50.0, 5.0, 5.0, None]
    assert assigned[1][1] == [0.0, 0.0, 1.0] and assigned[2][1] == [3.0, 0.0, 0.0]       # 同じ強さなら並びの順
    assert fx_lights.assign([], 2, 0.05) == [None, None]
