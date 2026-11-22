"""順番に点く部品（blender_adapter/fx_sequence.py）: 順番と間隔、点きはじめのちらつき、点いた直後の明るすぎ、後の出来事の上書き、
出来事の無い並び。値は作り物（Blender なし）。作品の形の照合は docs/model3d.md。"""
import pytest

from model3d.blender_adapter import fx_sequence
from model3d.blender_adapter.fx_events import Table


ON = {"frame": 11, "group": "row", "from": 0.1, "to": 1.0, "step": 2 / 30, "order": [2, 0, 1, 3], "flicker": 0.1, "seed": 6,
      "over": 0.2, "surge": 1.0}
OFF = {"frame": 41, "group": "row", "to": 0.0, "step": 0.0}


def _levels(f, events=(ON, OFF)):
    table = Table({"fps": 30, "events": [dict(e) for e in events]})
    return fx_sequence.levels(table, table.events, 4, f)


def test_parts_come_on_in_order_with_flicker_then_surge():
    assert _levels(10) == [0.1] * 4
    first = _levels(11)
    assert first[2] != 0.1 and first[0] == first[1] == first[3] == 0.1
    assert 0.25 <= first[2] <= 1.0                                        # ちらつき（0.25〜1 倍）
    assert _levels(13)[0] != 0.1 and _levels(13)[1] == 0.1                # 2 番目（部品 0）は 2 コマ後から
    surge = _levels(11 + 4)[2]                                            # ちらつき 3 コマの後は、明るすぎから下がる
    assert 1.0 < surge < 2.0 and _levels(11 + 10)[2] == pytest.approx(1.0)
    assert _levels(41) == [0.0] * 4                                       # 後の出来事が上書き
    assert _levels(13) == _levels(13)


def test_no_events_means_fully_on():
    table = Table({"fps": 30, "events": []})
    assert fx_sequence.levels(table, [], 3, 5) == [1.0, 1.0, 1.0]
