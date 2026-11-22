"""絵の差し替えの表情（render.show の expressions の images・times・events）: 出来事の表 → コマごとの絵の名前（はさむ絵・まばたき・
目の区間・重ねた絵・絵の無い組み合わせで止める）、半コマのゆとりで絵を選ぶこと、時刻の決め方（clock）、設定の確かめと工程の受け渡し。
描く所（画像ノードの差し替え）は本物の Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json

import pytest

from model3d import face_pictures
from model3d.blender_adapter import show_render
from model3d.io import ToolError, atomic_json

NAMES = ["smile", "open_s", "open_m", "open", "half", "shut", "half+smile", "shut+smile"]


def _events(**extra):
    return face_pictures.check_events({"mouth": [[0.0, ""], [0.1, "open"], [0.2, "smile"]], "steps": {"open": ["open_s", "open_m"]},
                                       **extra})


def test_an_opening_mouth_puts_its_steps_in_one_frame_each_and_closes_in_reverse():
    names = face_pictures.frames(_events(), 30.0, 10, NAMES)
    assert names == ["", "", "", "open_s", "open_m", "open", "open_m", "open_s", "smile", "smile"]


def test_blinks_and_eye_spans_join_the_mouth_into_one_picture():
    events = _events(mouth=[[0.0, "smile"]], steps={}, blinks=[0.1], blink=["half", "shut", "half"], eyes=[[0.2, 0.3, "half"]])
    names = face_pictures.frames(events, 30.0, 10, NAMES)
    assert names == ["smile"] * 3 + ["half+smile", "shut+smile", "half+smile", "half+smile", "half+smile", "half+smile", "smile"]


def test_a_combination_without_a_picture_stops_and_says_when():
    events = _events(blinks=[0.1], blink=["shut", "shut"])
    with pytest.raises(ToolError, match=r"2 コマ.*0.1 秒（目 shut・口 open_s）"):
        face_pictures.frames(events, 30.0, 10, NAMES)


def test_seconds_are_turned_into_frames_before_they_are_compared():
    """表の秒を丸めて書いても（3 桁に丸めると、コマの時刻より 0.3 ミリ秒あとになることがある）、そのコマから始まる。"""
    start = round(884 / 30, 3)                                   # 29.467 は、コマ 884 の時刻 29.4666… より後ろ
    assert start > 884 / 30
    events = face_pictures.check_events({"mouth": [[0.0, ""], [start, "smile"]]})
    names = face_pictures.frames(events, 30.0, 890, NAMES)
    assert names[883] == "" and names[884] == "smile"
    assert face_pictures.times(names, 30.0) == [[0.0, ""], [884 / 30, "smile"]]


@pytest.mark.parametrize("events, message", [
    ({"mouth": [[0.2, ""], [0.1, "smile"]]}, "小さい順"), ({"mouth": [[0.1, "a"], [0.1, "b"]]}, "小さい順"),
    ({"mouth": [[-1, ""]]}, "0 以上"), ({"eyes": [[0.3, 0.2, "half"]]}, "後"), ({"blinks": [0.1]}, "blink"),
    ({"steps": {"open": []}}, "1 つ以上"), ({"wink": []}, "辞書"), ({"join": ""}, "空でない"),
])
def test_bad_event_tables_are_named(events, message):
    with pytest.raises(ToolError, match=message):
        face_pictures.check_events(events)


def test_a_picture_is_picked_with_half_a_frame_of_slack():
    times = [[0.0, ""], [round(884 / 30, 3), "smile"]]
    assert show_render.picture_at(times, 884 / 30, 30.0) == "smile"           # 丸めた秒の後ろでも、そのコマで始まる
    assert show_render.picture_at(times, 883 / 30, 30.0) == ""
    assert show_render.picture_at([[1.0, "a"], [2.0, "b"]], 0.0, 30.0) == "a"  # 最初の秒より前は最初の名前


def test_the_clock_is_the_drawn_frame_or_the_motion_frame():
    assert show_render.expression_time(31, 400, 30.0, {"clock": "T"}) == 1.0
    assert show_render.expression_time(31, 400, 30.0, {"clock": "frame", "frame0": 1}) == pytest.approx(399 / 30)
    assert show_render.expression_time(31, 400, 30.0, {}) == 1.0


# ---- 工程（代役の Blender は test_m3d_show_render の物を使う） ----

def _call(tmp_path, expressions):
    import test_m3d_show_render as show
    for name in ("smile", "half"):
        (tmp_path / f"{name}.png").write_bytes(b"png")
    return show.call(tmp_path, {"expressions": expressions})


def test_events_become_times_that_reach_the_script(tmp_path):
    expressions = {"objects": ["Head"], "images": {"smile": "smile.png", "half": "half.png"}, "materials": ["Face"],
                   "events": {"mouth": [[0.0, ""], [1 / 30, "smile"]]}}
    result = _call(tmp_path, expressions)
    settings = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["settings"]["expressions"]
    assert settings["times"] == [[0.0, ""], [1 / 30, "smile"]] and settings["clock"] == "T" and settings["materials"] == ["Face"]
    assert settings["images"]["smile"].endswith("smile.png")
    face = result.observations[0]["face_pictures"]
    assert face["changes"] == 2 and face["pictures"] == ["", "smile"] and set(face["images"]) == {"smile", "half"}


def test_shape_key_values_alone_still_work(tmp_path):
    _call(tmp_path, {"objects": ["Head"], "values": {"blink": [[0.0, 0.0], [1.0, 1.0]]}})
    settings = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["settings"]["expressions"]
    assert settings["values"] == {"blink": [[0.0, 0.0], [1.0, 1.0]]} and settings["images"] == {} and settings["times"] is None


@pytest.mark.parametrize("expressions, message", [
    ({"objects": ["Head"], "images": {"smile": "smile.png"}}, "どちらか 1 つ"),
    ({"objects": ["Head"], "images": {"smile": "smile.png"}, "times": [[0, "x"]]}, "images に無い絵"),
    ({"objects": ["Head"], "images": {"smile": "none.png"}, "times": [[0, "smile"]]}, "絵がありません"),
    ({"objects": ["Head"], "images": {"smile": "smile.png"}, "times": [[0, "smile"]], "clock": "beat"}, "clock"),
    ({"objects": ["Head"], "values": {"blink": [[0, 1]]}, "frame0": 3}, "frame0"),
    ({"objects": ["Head"], "values": {"blink": [[0, 1]]}, "times": [[0, ""]]}, "images"),
    ({"objects": ["Head"], "images": {"smile": "smile.png"}, "events": {"mouth": [[0.0, "frown"]]}}, "組み合わせ"),
])
def test_bad_picture_settings_are_named(tmp_path, expressions, message):
    with pytest.raises(ToolError, match=message):
        _call(tmp_path, expressions)


def test_events_need_the_fps_of_the_cameras(tmp_path):
    import test_m3d_show_render as show
    (tmp_path / "smile.png").write_bytes(b"png")
    cameras = show._cameras()
    del cameras["fps"]
    atomic_json(tmp_path / "nofps.json", cameras)
    from model3d.ops import get
    with pytest.raises(ToolError, match="fps"):
        get("render.show").function({"blend": tmp_path / "nofps.json", "cameras": tmp_path / "nofps.json"},
                                    {"expressions": {"objects": ["Head"], "images": {"smile": "smile.png"},
                                                     "events": {"mouth": [[0.0, "smile"]]}}},
                                    {"frames": tmp_path / "f.json", "report": tmp_path / "r.json"}, show.Context(tmp_path))
