"""出来事の時刻の候補（plan.motion_marks。model3d/motion_marks.py と ops/event_ops.py）: 足の着地・山と谷・打音の最初のコマ、悪い設定。
値は作り物（Blender なし）。作品の骨の通り道での照合は docs/model3d.md。"""
import numpy as np
import pytest

from model3d import motion_marks
from model3d.io import ToolError
from model3d.ops import event_ops


FPS = 30


def _path():
    """60 標本。左足は 20 で着地（25 cm から下りて止まる）、右足は 40 で着地。腰は 30 でいちばん低い。手は 45 でいちばん前（−y）。"""
    n = 60
    frames = list(range(1, n + 1))
    i = np.arange(n)
    foot_l = np.where(i < 12, 0.05, np.where(i < 20, 0.25 - 0.025 * (i - 12), 0.05))
    foot_r = np.where(i < 32, 0.05, np.where(i < 40, 0.25 - 0.025 * (i - 32), 0.05))
    hips = 1.0 - 0.3 * np.exp(-((i - 30) / 5.0) ** 2)
    hand_y = -0.4 * np.exp(-((i - 45) / 4.0) ** 2)
    col = lambda z, y=None: [[0.0, float(y[k]) if y is not None else 0.0, float(z[k])] for k in range(n)]
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "fps": FPS, "frames": frames, "bones": {},
            "points": {"foot_l": col(foot_l), "foot_r": col(foot_r), "hips": col(hips), "hand": col(np.full(n, 1.2), hand_y)}}


LANDING = {"points": ["foot_l", "foot_r"], "drop": 0.05, "fall": 0.5, "still": 0.3, "window": 4, "gap": 0.2}


def test_marks_find_landings_extremes_and_first_frames():
    settings = event_ops.marks_settings({"landing": LANDING, "hits": [0.5, 29.467 - 28.0, 1.0 / 3 + 0.01],
                                         "extremes": [{"name": "low", "point": "hips", "axis": [0, 0, 1], "kind": "min", "prominence": 0.1, "gap": 0.3},
                                                      {"name": "front", "point": "hand", "axis": [0, -1, 0], "kind": "max", "prominence": 0.1, "gap": 0.3}]})
    report = motion_marks.build(settings, _path())
    assert [(m["point"], m["frame"]) for m in report["landings"]] == [("foot_l", 21.0), ("foot_r", 41.0)]
    assert report["landings"][0]["t"] == pytest.approx(20 / FPS)
    assert [m["frame"] for m in report["extremes"]["low"]] == [31.0] and [m["frame"] for m in report["extremes"]["front"]] == [46.0]
    # 0.5 秒 → コマ 16。小数 3 桁に丸めた 1.467 秒（コマ 45 の 1.4666… 秒の 0.01 コマ後）→ コマ 45。0.3433… 秒 → コマ 12（0.3 コマ後は次）
    assert [h["frame"] for h in report["hits"]] == [16, 45, 12]
    assert report["counts"] == {"landings": 2, "extremes": {"low": 1, "front": 1}, "hits": 3}


def test_first_frame_of_a_time():
    assert motion_marks.first_frame(0.0, 30, 1) == 1 and motion_marks.first_frame(1 / 30, 30, 1) == 2
    assert motion_marks.first_frame(0.034, 30, 1) == 2                    # コマ 2 の頭の 0.02 コマ後は、丸めた秒とみなしてコマ 2
    assert motion_marks.first_frame(0.04, 30, 1) == 3                    # 0.2 コマ後は、次のコマ


@pytest.mark.parametrize("parameters, message", [
    ({}, "どれか 1 つ以上"),
    ({"landing": {**LANDING, "drop": None}}, "数に"),
    ({"landing": {k: v for k, v in LANDING.items() if k != "gap"}}, "全部書いて"),
    ({"landing": {**LANDING, "window": 0}}, "window"),
    ({"extremes": [{"name": "a", "point": "hips", "axis": [0, 0, 0], "kind": "min", "prominence": 0.1, "gap": 0.3}]}, "長さのある"),
    ({"extremes": [{"name": "a", "point": "hips", "axis": [0, 0, 1], "kind": "low", "prominence": 0.1, "gap": 0.3}]}, "max か min"),
    ({"hits": "1.0"}, "秒の並び"),
    ({"hits": [], "wobble": 1}, "知らないパラメーター"),
])
def test_bad_marks_settings_stop(parameters, message):
    with pytest.raises(ToolError, match=message):
        event_ops.marks_settings(parameters)
