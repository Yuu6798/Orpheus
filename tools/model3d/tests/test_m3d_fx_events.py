"""出来事の表と時計（blender_adapter/fx_events.py）: 整数だけの種の乱数、始まりをコマの番号で比べること（秒を丸めて 1 コマ遅れる
出来事を notes に書くこと）、効く間、そのコマだけに出る出来事、点の 3 通りの書き方、悪い表。値は作り物（Blender なし）。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_events
from model3d.blender_adapter.fx_events import EventError, Table

PATH = {"schema_version": "model3d-bone-path/0.1", "frames": [1, 2, 3, 4, 5],
        "points": {"hand": [[0, 0, 1.0], [0.1, 0, 1.0], [0.2, 0, 1.0], [0.3, 0, 1.0], [0.4, 0, 1.0]]}}


def test_random_numbers_depend_only_on_integer_keys():
    assert fx_events.rng(3, 1, 7).random() == fx_events.rng(3, 1, 7).random()
    assert fx_events.rng(3, 1, 7).random() != fx_events.rng(3, 1, 8).random()
    assert fx_events.rng(np.int64(5)).random() == fx_events.rng(5).random()
    for bad in ("3", 1.5, True, None):
        with pytest.raises(EventError, match="整数"):
            fx_events.rng(1, bad)


def test_start_is_compared_in_frames():
    table = Table({"fps": 30, "frame0": 1, "events": [{"t": 1.0}, {"frame": 31}, {"t": 0.5}]})
    assert [table.start(e) for e in table.events] == [16.0, 31.0, 31.0]     # 始まりの順（同じなら表の順）
    assert table.events[0]["t"] == 0.5 and table.events[1]["t"] == 1.0
    exact = table.events[2]
    assert table.within(exact, 31, 0.0, 0.0) and not table.within(exact, 30, 0.0, 0.0)
    assert table.within(exact, 30, 1 / 30, 0.0)                           # 1 コマ前から（溜めのある出来事）
    assert table.within(exact, 33, 0.0, 2 / 30) and not table.within(exact, 34, 0.0, 2 / 30)
    assert table.age(exact, 34) == pytest.approx(0.1) and table.seconds(31) == 1.0
    assert table.notes == []


def test_seconds_rounded_after_a_frame_head_start_one_frame_late_and_are_noted():
    """コマ 72 の秒 2.3666… を 2.367 と書くと、コマ 72 ではまだ始まっていない（秒を丸めると、1 コマだけの出来事が出ないことがある）。"""
    table = Table({"fps": 30, "frame0": 1, "events": [{"t": 2.367}, {"frame": 72}, {"t": 1.27}]})
    rounded = next(e for e in table.events if e.get("t") == 2.367)
    frame = next(e for e in table.events if "frame" in e)
    assert not table.within(rounded, 72, 0.0, 0.07) and table.within(rounded, 73, 0.0, 0.07)
    assert table.within(frame, 72, 0.0, 0.07)
    assert len(table.notes) == 1 and "2.367" in table.notes[0] and "72" in table.notes[0]   # 1.27 秒（0.1 コマ後）は書かない


def test_tick_and_gate():
    table = Table({"fps": 30, "events": []})
    assert [table.tick(f, 2) for f in (10, 11, 12)] == [5, 5, 6]
    assert Table.gated({}, {}) and not Table.gated({"when": "mono"}, {"mono": 0.0})
    assert Table.gated({"when": "mono"}, {"mono": -1.0}) and not Table.gated({"when": "mono"}, {})


def test_points_world_bone_and_camera():
    table = Table({"fps": 30, "frame0": 1, "events": []}, PATH)
    assert np.allclose(table.point([1, 2, 3], 1), [1, 2, 3])
    assert np.allclose(table.point({"point": "hand"}, 3), [0.2, 0, 1.0])
    assert np.allclose(table.point({"point": "hand", "frame": 2, "offset": [0, 0, 0.5]}, 5), [0.1, 0, 1.5])
    assert np.allclose(table.point({"point": "hand", "t": 0.1}, 1), [0.3, 0, 1.0])     # 0.1 秒 = コマ 4
    assert np.allclose(table.point({"point": "hand"}, 99), [0.4, 0, 1.0])              # 範囲の外は端の標本
    matrix = np.eye(4)
    matrix[:3, 3] = [0, -5, 1]
    camera = {"matrix": matrix.tolist(), "position": [0, -5, 1]}
    assert np.allclose(table.point({"camera": [0.5, 0.2, 2.0]}, 1, camera), [0.5, -4.8, -1.0])     # 右 x・上 y・後ろ z の列
    assert table.fixed([0, 0, 0]) and not table.fixed({"camera": [0, 0, 1]})


@pytest.mark.parametrize("spec, path, match", [
    ({"events": []}, None, "fps"),
    ({"fps": 0, "events": []}, None, "正の数"),
    ({"fps": 30, "events": [{"t": 1, "frame": 3}]}, None, "どちらか 1 つ"),
    ({"fps": 30, "events": [{}]}, None, "どちらか 1 つ"),
    ({"fps": 30, "events": [{"t": "1"}]}, None, "数に"),
    ({"fps": 30, "events": [3]}, None, "辞書"),
    ({"fps": 30, "events": []}, {"frames": [1]}, "2 つより少ない"),
    ({"fps": 30, "events": []}, {"frames": [1, 2, 4]}, "等間隔"),
])
def test_bad_tables_stop(spec, path, match):
    with pytest.raises(EventError, match=match):
        Table(spec, path)


def test_bad_points_stop():
    table = Table({"fps": 30, "events": []}, PATH)
    with pytest.raises(EventError, match="点がありません"):
        table.point({"point": "foot"}, 1)
    with pytest.raises(EventError, match="カメラが要ります"):
        table.point({"camera": [0, 0, 1]}, 1)
    with pytest.raises(EventError, match="骨の通り道が要ります"):
        Table({"fps": 30, "events": []}).point({"point": "hand"}, 1)
    with pytest.raises(EventError, match="分かりません"):
        table.point({"where": 1}, 1)
    camera = {"matrix": np.eye(4).tolist(), "position": [0, 0, 0]}
    for spec in ({"camera": [0.5, 0.2]}, {"camera": "0, 0, 1"}, {"point": "hand", "offset": [0, 1]}):
        with pytest.raises(EventError, match="3 つの数"):
            table.point(spec, 1, camera)
