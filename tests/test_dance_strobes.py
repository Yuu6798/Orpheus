"""dance-shot/0.3 のフラッシュ: at（複数の時刻・拍の格子）・snap（time / peak）・curve_frames（コマごとの表・1 コマのストロボ）。"""
import math
import random

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import Image  # noqa: E402

from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import beats, compose, demo, plan  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

FPS = 30


def base(flashes, schema=dance.SCHEMA_VERSION, **extra):
    raw = {"schema_version": schema, "output": {"width": 64, "height": 96, "fps": FPS}, "duration_s": 2.0, "stage": {"path": "stage.png"},
           "performer": {"clips": [{"path": "still.png", "start": 0, "end": 2}]}, "flashes": flashes}
    raw.update(extra)
    return raw


def parse(tmp_path, flashes, **extra):
    return dancespec.parse_spec(base(flashes, **extra), tmp_path)


class Ctx:
    def __init__(self, sp):
        self.fps = float(sp.data["output"]["fps"])
        self.flashes = [{**f, "display": np.asarray(f["color"], np.float32) / 255.0, "linear": np.zeros(3, np.float32)}
                        for f in sp.data["flashes"]]


def levels(sp, frames):
    ctx = Ctx(sp)
    return [[round(s, 9) for s, _, _ in compose._active_flashes(ctx, k / FPS)] for k in frames]


# ---- 書き方と展開 ---------------------------------------------------------------------

@pytest.mark.parametrize("flash, message", [
    ({"t": 0.5, "at": [0.5]}, "invalid"),
    ({"strength": 0.3}, "invalid"),
    ({"t": 0.5, "curve_frames": {"levels": []}}, "invalid"),
    ({"t": 0.5, "curve_frames": {"levels": [1.5]}}, "invalid"),
    ({"t": 0.5, "snap": "time", "curve_frames": {"levels": [1]}}, "snap は使えません"),
    ({"at": [], "snap": "peak", "curve_frames": {"levels": [1]}}, "snap は使えません"),      # 時刻が無くても書き方の誤りは止める
])
def test_a_flash_needs_t_or_at_and_a_sane_shape(tmp_path, flash, message):
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, [flash])


@pytest.mark.parametrize("flash", [{"at": [0.5]}, {"t": 0.5, "snap": "peak"}, {"t": 0.5, "curve_frames": {"levels": [1]}}])
def test_the_new_fields_are_refused_in_a_02_spec(tmp_path, flash):
    with pytest.raises(PipelineError, match="invalid"):
        parse(tmp_path, [flash], schema=dance.SCHEMA_V02)


def test_at_expands_into_one_flash_per_time_with_the_entry_number(tmp_path):
    sp = parse(tmp_path, [{"t": 0.2, "strength": 0.5},
                          {"at": [1.2, {"frame": 21}, 0.4], "strength": 0.3, "x_note": "strobes"},
                          {"at": {"grid": "beats", "from": 0.5, "to": 1.5, "except": [1.0]}, "decay_s": 0.1}],
               beats={"bpm": 120, "count": 8})
    flashes = sp.data["flashes"]
    assert [(f["entry"], round(f["t"], 6)) for f in flashes] == [(0, 0.2), (1, 0.4), (1, 0.7), (1, 1.2), (2, 0.5), (2, 1.5)]
    assert all(f["x_note"] == "strobes" for f in flashes if f["entry"] == 1)
    assert {f["snap"] for f in flashes} == {"none"} and {f["curve_frames"] is None for f in flashes} == {True}


def test_snap_time_moves_the_flash_to_the_nearest_frame(tmp_path):
    sp = parse(tmp_path, [{"t": 0.05, "snap": "time"}, {"t": 0.049, "snap": "time"}, {"at": [1.01, 1.02], "snap": "time"}])
    assert [round(f["t"] * FPS, 9) for f in sp.data["flashes"]] == [2, 1, 30, 31]     # 1.5 コマは切り上げ、1.47 は 1


def test_an_at_without_times_is_noted(tmp_path):
    sp = parse(tmp_path, [{"at": {"grid": "downbeats", "from": 1.9, "to": 1.95}}], beats={"bpm": 120, "count": 8})
    assert sp.data["flashes"] == [] and any("at に時刻が 1 つもない" in w for w in sp.warnings)


def test_a_02_spec_keeps_one_plain_flash_per_entry(tmp_path):
    sp = parse(tmp_path, [{"t": 0.3}, {"t": 0.4, "mode": "add_display"}], schema=dance.SCHEMA_V02)
    assert [(f["entry"], f["snap"], f["curve_frames"]) for f in sp.data["flashes"]] == [(0, "none", None), (1, "none", None)]


# ---- 強さ ----------------------------------------------------------------------------

def _old_active(flashes, t):
    """0.2 の _active_flashes（写し）。"""
    out = []
    for flash in flashes:
        age = t - flash["t"]
        if 0 <= age < 5 * flash["decay_s"]:
            out.append((float(flash["strength"] * math.exp(-age / flash["decay_s"])), flash["mode"]))
    return out


def test_plain_flashes_give_the_02_strengths(tmp_path):
    rng = random.Random(3)
    raw = [{"t": round(rng.uniform(0, 1.8), 3), "strength": round(rng.uniform(0, 1), 3), "decay_s": round(rng.uniform(0.02, 0.3), 3),
            "mode": rng.choice(["lerp_linear", "add_display"])} for _ in range(12)]
    sp = parse(tmp_path, raw)
    ctx = Ctx(sp)
    for k in range(60):
        t = k / FPS
        assert [(s, m) for s, _, m in compose._active_flashes(ctx, t)] == _old_active(sp.data["flashes"], t)


def _flash_amt(s, flashes, fps, decay):
    """私用の制作の指数のフラッシュ（写し）: 半コマ前から山の値、減衰は t から、0.4 秒（= 5·decay）まで。複数は強い方。"""
    v = 0.0
    for t, a in flashes:
        d = s - t
        if -0.5 / fps <= d < 5 * decay:
            v = max(v, a * math.exp(-max(d, 0) / decay))
    return v


def test_snap_peak_lights_the_nearest_frame_like_the_production_formula(tmp_path):
    pairs = [(0.512, 0.3), (0.9, 0.2), (1.333, 0.25)]                       # 0.512 秒はコマ 15.36 → コマ 15 が山
    sp = parse(tmp_path, [{"t": t, "strength": a, "decay_s": 0.08, "snap": "peak", "mode": "add_display"} for t, a in pairs])
    ctx = Ctx(sp)
    for k in range(60):
        got = [s for s, _, _ in compose._active_flashes(ctx, k / FPS)]
        expected = _flash_amt(k / FPS, pairs, FPS, 0.08)
        assert (max(got) if got else 0.0) == expected
    assert compose.flash_level(sp.data["flashes"][0], 15 / FPS, FPS) == 0.3                 # 半コマ前でも山の値
    assert compose.flash_level({**sp.data["flashes"][0], "snap": "none"}, 15 / FPS, FPS) is None
    for t in (0.5 + 0.5 / FPS, 0.5 + 0.4999 / FPS, 0.5 + 0.5001 / FPS):                    # 半コマの境目も私用の式と同じ
        edge = {**sp.data["flashes"][0], "t": t}
        assert (compose.flash_level(edge, 0.5, FPS) or 0.0) == _flash_amt(0.5, [(t, 0.3)], FPS, 0.08)
    assert compose.flash_level({**sp.data["flashes"][0], "t": 0.5 + 0.4999 / FPS}, 0.5, FPS) == 0.3


def _events(events, curve):
    """私用の制作のコマごとの表（写し）: t にいちばん近いコマから curve の差で、重なりは強い方。"""
    table = {}
    for t, strength in events:
        k0 = int(math.floor(t * FPS + 0.5))
        for dk, v in curve.items():
            table[k0 + dk] = max(table.get(k0 + dk, 0.0), v * strength)
    return table


def test_curve_frames_follow_the_table_and_take_the_stronger_inside_one_entry(tmp_path):
    curve = {-1: 0.3, 0: 1.0, 1: 0.6, 2: 0.2}
    times = [0.4, 0.47, 1.1]                                                  # 0.4 と 0.47 は表が重なる
    sp = parse(tmp_path, [{"at": times, "strength": 0.5, "mode": "add_display", "curve_frames": {"offset": -1, "levels": [0.3, 1.0, 0.6, 0.2]}}])
    table = _events([(t, 0.5) for t in times], curve)
    for k in range(60):
        got = levels(sp, [k])[0]
        assert got == ([round(table[k], 9)] if k in table else [])


def test_separate_entries_add_and_a_strobe_lights_one_frame(tmp_path):
    sp = parse(tmp_path, [{"t": 0.5, "strength": 0.2, "decay_s": 0.05, "mode": "add_display"},
                          {"at": [0.5, 0.8], "strength": 0.1, "mode": "add_display", "curve_frames": {"levels": [1]}}])
    assert levels(sp, [14, 15, 16, 24]) == [[], [0.2, 0.1], [round(0.2 * math.exp(-1 / 30 / 0.05), 9)], [0.1]]
    assert levels(sp, [23, 25]) == [[], []]


# ---- 描画・plan・デモ -----------------------------------------------------------------

def test_a_strobe_brightens_only_its_frame(tmp_path):
    Image.fromarray(np.full((96, 64, 3), 60, np.uint8)).save(tmp_path / "stage.png")
    Image.fromarray(np.full((96, 64, 3), (0, 255, 0), np.uint8)).save(tmp_path / "still.png")    # 緑で全部抜ける
    plain = dancespec.parse_spec(base([]), tmp_path)
    strobe = dancespec.parse_spec(base([{"at": [{"frame": 15}], "strength": 0.3, "mode": "add_display", "color": [255, 255, 255],
                                         "curve_frames": {"levels": [1]}}]), tmp_path)
    contexts = [compose.prepare(sp, out=tmp_path / "runs") for sp in (plain, strobe)]
    try:
        for k in (14, 15, 16):
            a, b = (compose.frame(ctx, k / FPS) for ctx in contexts)
            if k == 15:
                assert b.mean() > a.mean() + 40
            else:
                assert (a == b).all()
    finally:
        for ctx in contexts:
            ctx.close()


def test_plan_shows_entries_snap_and_shape_for_03_specs(tmp_path):
    sp = parse(tmp_path, [{"t": 0.5, "snap": "peak", "decay_s": 0.08}, {"at": [0.2, 0.9], "curve_frames": {"offset": -1, "levels": [0.5, 1]}}])
    text = plan.build(sp.data)
    assert "shape" in text and "decay 0.08" in text and "peak" in text and "コマ -1: 0.5 1" in text
    old = plan.build(parse(tmp_path, [{"t": 0.5}], schema=dance.SCHEMA_V02).data)
    assert "shape" not in old and "## flashes" in old


def test_the_03_demo_has_beat_strobes_on_clip_c(tmp_path):
    data = dancespec.parse_spec(demo._spec(180, 320, 6.0, 24), tmp_path).data
    strobes = [f for f in data["flashes"] if f["curve_frames"] is not None]
    assert [f["t"] for f in strobes] == [4.5, 5.0, 5.5] and {f["entry"] for f in strobes} == {1}
    assert data["flashes"][0]["mode"] == "add_display" and data["flashes"][0]["curve_frames"] is None
    assert [f["t"] for f in dancespec.parse_spec(demo._spec(180, 320, 6.0, 24, "0.2"), tmp_path).data["flashes"]] == [4.0]
    assert beats.nearest_frame(4.5, 24) == 108
