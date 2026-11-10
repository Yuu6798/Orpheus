"""fx-timeline/0.2 のつなぎ「焼け抜け」（fx/burn.py と fx/sequence.py の burn）と、0.1 の timeline の読み方。

場と式は numpy だけなので、描画を使わないテストは cv2 なしで動く。素材はすべて合成（乱数の画と fx_synth の絵）。
"""
import json
import math

import numpy as np
import pytest

import fx_synth
from mv_pipeline.fx import burn, sequence
from mv_pipeline.fx import timeline as fxtimeline
from mv_pipeline.io import PipelineError

SIZE = (160, 90)
COARSE = 0.5                # 160x90 の合成の画でむらが画素より粗くなる scale（細かいむらの升目が 5 画素）
FULL = {"type": "burn", "duration_s": 0.5, "seed": 3, "scale": COARSE, "soft": 0.035,
        "edge": {"color": [255, 125, 35], "width": 0.05, "gain": 0.9}, "char": 0.6, "from": [0.5, 0.5], "bias": 0.0}


def conf(**changes):
    """正規化済みの burn の設定（sequence._transition を通したもの）。"""
    return sequence._transition({**FULL, **changes})


def planes(seed=5, low=-40.0, high=300.0):
    """0〜255 の外の値も含む合成の画 2 枚（hooks を通った float の画を模す）。"""
    rng = np.random.default_rng(seed)
    return [rng.uniform(low, high, (SIZE[1], SIZE[0], 3)).astype(np.float32) for _ in range(2)]


def write_shots(directory, names=("a", "b", "c"), **changes):
    for name in names:
        (directory / f"{name}.json").write_text(json.dumps(fx_synth.spec_dict(**changes)), encoding="utf-8")


def timeline_dict(shots, version=sequence.SCHEMA_VERSION, **extra):
    raw = {"schema_version": version, "output": {"width": 160, "height": 90, "fps": 12}, "duration_s": 3.0, "shots": shots}
    raw.update(extra)
    return raw


def burn_shots(**changes):
    return [{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.0, "transition_in": {**FULL, **changes}}, {"spec": "c.json", "start": 2.0}]


# ---- 場 --------------------------------------------------------------------------------------

def test_the_field_depends_on_the_seed_only():
    """同じ seed なら同じ場、違う seed なら違う模様。値は 0..1。"""
    first, again, other = burn.field(conf(), SIZE), burn.field(conf(), SIZE), burn.field(conf(seed=4), SIZE)
    assert first.shape == (90, 160) and first.dtype == np.float32 and 0.0 <= first.min() < 0.1 and 0.9 < first.max() <= 1.0
    assert np.array_equal(first, again)
    assert np.abs(first - other).mean() > 0.05


@pytest.mark.parametrize("changes, size", [({}, SIZE), ({"scale": 1.0}, SIZE), ({"bias": 0.5, "from": [0.2, 0.8]}, SIZE), ({}, (90, 160)),
                                           ({"scale": 0.08}, (960, 540))])
def test_the_field_is_the_same_pattern_at_twice_the_resolution(changes, size):
    """2 倍の解像度で作った場を 2×2 の平均で縮めると、1 倍の場とほぼ同じ（正規化座標の上の同じ模様を、画素の中心で読んでいる）。
    最後の組は既定の scale 0.08 を、むらが画素より粗くなる大きさ（960x540 と 1920x1080）で比べる。"""
    one = burn.field(conf(**changes), size)
    two = burn.field(conf(**changes), (size[0] * 2, size[1] * 2))
    shrunk = two.reshape(size[1], 2, size[0], 2).mean((1, 3))
    assert np.abs(shrunk - one).max() < 0.02 and np.abs(shrunk - one).mean() < 0.003
    assert np.abs(burn.field(conf(seed=4, **changes), size) - one).mean() > 0.05         # 比べる差は、seed を変えた差よりずっと小さい


def test_scale_is_the_size_of_the_pattern():
    """scale を 2 倍にすると模様が 2 倍に粗くなる（行に沿って場の中央の値をまたぐ回数がおよそ半分になる）。"""
    def crossings(scale):
        field = burn.field(conf(scale=scale), (640, 360))
        above = field > np.median(field)
        return int((above[:, 1:] != above[:, :-1]).sum())
    fine, middle, coarse = crossings(0.125), crossings(0.25), crossings(0.5)
    assert 1.6 < fine / middle < 2.6 and 1.6 < middle / coarse < 2.6


def test_bias_mixes_in_the_distance_from_the_origin():
    """bias 0 は純粋なノイズ（from を変えても同じ）。bias > 0 は from からの距離を混ぜ、bias 1 は距離そのもの（いちばん遠い隅で 1）。"""
    assert np.array_equal(burn.field(conf(bias=0.0, **{"from": [0.1, 0.9]}), SIZE), burn.noise(3, COARSE, SIZE))
    origin = [39.5 / 160, 67.5 / 90]                                    # 画素 (39, 67) の中心
    far = burn.field(conf(bias=1.0, **{"from": origin}), SIZE)
    assert np.array_equal(far, burn.distance(origin, SIZE))
    y, x = np.unravel_index(np.argmin(far), far.shape)
    assert (x, y) == (39, 67) and far.min() < 1e-6                      # from の画素がいちばん小さい
    assert far[0, 159] == far.max() and 0.99 < far.max() <= 1.0         # from からいちばん遠い隅（右上）の画素がいちばん大きい
    assert far[67, 79] == pytest.approx(far[27, 39], abs=1e-6)          # 横に 40 画素と縦に 40 画素は同じ距離（縦横で同じ物差し）
    mixed = burn.field(conf(bias=0.4, **{"from": origin}), SIZE)
    assert np.allclose(mixed, burn.noise(3, COARSE, SIZE) * 0.6 + far * 0.4, atol=1e-6)


# ---- 式 --------------------------------------------------------------------------------------

def reference(before, now, field, p, c):
    """docs/fx.md と fx/burn.py の冒頭に書いた式を、float64 でそのまま計算したもの。"""
    a, b, n = before.astype(np.float64), now.astype(np.float64), field.astype(np.float64)
    width = c["edge"]["width"] if c["edge"] else 0.0
    r = c["soft"] + width
    thr = -r + p * (1 + 2 * r)
    m = np.clip((thr - n) / c["soft"], 0, 1)[..., None]
    char = np.clip(1 - np.abs(n - thr - r * 0.03 / 0.085) / (r * 0.04 / 0.085), 0, 1)[..., None]
    image = (a * (1 - m) + b * m) * (1 - c["char"] * char)
    if c["edge"]:
        e = np.clip(1 - np.abs(n - thr) / width, 0, 1)[..., None]
        light = np.minimum(np.asarray(c["edge"]["color"]) * c["edge"]["gain"] * e, 255)
        image = 255 - (255 - image) * (255 - light) / 255                # スクリーン合成
    return image


@pytest.mark.parametrize("changes", [{}, {"edge": None}, {"char": 0.0}, {"edge": None, "char": 0.0}, {"bias": 0.6, "from": [0.1, 0.2]},
                                     {"edge": {"color": [200, 220, 255], "width": 0.12, "gain": 3.0}, "soft": 0.1}])
def test_composite_follows_the_written_formulas(changes):
    c = conf(**changes)
    field = burn.field(c, SIZE)
    before, now = planes()
    for p in (0.001, 0.07, 0.25, 0.5, 0.75, 0.93, 0.999):
        mine = burn.composite(before, now, field, p, c)
        assert mine.dtype == np.float32 and np.allclose(mine, reference(before, now, field, p, c), atol=2e-3), (changes, p)


def private_burn(before, now, n, p):
    """リポジトリの外の試作のつなぎの式（混ぜ方・縁・焦げ）を写したもの。値は既定（soft 0.035・width 0.05・gain 0.9・char 0.6）で、
    しきい値は試作の p·1.25 − 0.12。縁の光は、同じ e を見本の色でスクリーン合成に置き換えてある（試作は発光のバッファに足してぼかす）。"""
    thr = p * 1.25 - 0.12
    m = np.clip((thr - n) / 0.035, 0, 1)[..., None]
    out = before * (1 - m) + now * m
    edge = np.clip(1 - np.abs(n - thr) / 0.05, 0, 1)
    out = out * (1 - 0.6 * np.clip(1 - np.abs(n - thr - 0.03) / 0.04, 0, 1)[..., None])
    light = np.minimum(np.array([255, 125, 35], np.float32) * (0.9 * edge)[..., None], 255)
    return 255 - (255 - out) * (255 - light) / 255


def test_composite_equals_the_private_burns_at_the_same_threshold():
    """既定の値では、混ぜ方・縁・焦げの帯が試作の式と同じ値になる（しきい値の余白だけ違うので、しきい値が同じになる進みで比べる）。"""
    c = conf()
    field = burn.field(c, SIZE)
    before, now = planes()
    for p in (0.2, 0.35, 0.5, 0.65, 0.8):
        same_threshold = (p * 1.25 - 0.12 + 0.085) / 1.17
        assert burn.threshold(c, same_threshold) == pytest.approx(p * 1.25 - 0.12)
        mine = burn.composite(before, now, field, same_threshold, c)
        assert np.allclose(mine, private_burn(before.astype(np.float64), now.astype(np.float64), field.astype(np.float64), p), atol=2e-3), p


@pytest.mark.parametrize("changes", [{}, {"edge": None}, {"edge": None, "char": 0.0}, {"bias": 1.0}, {"soft": 1.0}, {"scale": 4.0}, {"scale": 0.01},
                                     {"soft": 0.001, "edge": {"width": 1.0, "gain": 4.0}, "char": 1.0}])
def test_the_ends_are_exactly_the_two_shots(changes):
    """p = 0 は前の画、p = 1 は今の画とバイト単位で同じ（xfade の重み 1・0 と同じ厳しさ。0〜255 の外の float でも、uint8 でも）。"""
    c = conf(**changes)
    real = burn.field(c, SIZE)
    for before, now in (planes(), [plane.clip(0, 255).astype(np.uint8) for plane in planes(8)]):
        # 場の値がちょうど 0 や 1 の画素でも同じ（式のままだと float の丸めで 1 段ずれうるので、端は式を通さずに返している）
        for field in (real, np.zeros_like(real), np.ones_like(real)):
            assert np.array_equal(burn.composite(before, now, field, 0.0, c), before.astype(np.float32))
            assert np.array_equal(burn.composite(before, now, field, 1.0, c), now.astype(np.float32))
            # 端のすぐ内側も、式の値がそのままつながる（端で絵が飛ばない）
            assert np.abs(burn.composite(before, now, field, 1e-7, c) - before).max() < 1e-2
            assert np.abs(burn.composite(before, now, field, 1 - 1e-7, c) - now).max() < 1e-2


@pytest.mark.parametrize("changes", [{}, {"bias": 0.5, "from": [0.0, 1.0]}, {"edge": None, "soft": 0.2}, {"soft": 0.001, "edge": {"width": 0.3}}])
def test_the_threshold_keeps_every_pixel_on_one_side_at_the_ends(changes):
    """しきい値は p = 0 でどの画素の場の値より R = soft + 縁の幅 だけ下、p = 1 で同じだけ上にある。式のうえでも、p = 0 では今のショット・縁・焦げが
    1 画素も出ず、p = 1 では前のショットが 1 画素も残らない（端のコマを別扱いにしなくても、絵が飛ばない）。"""
    c = conf(**changes)
    field, margin = burn.field(c, SIZE), burn.reach(c)
    assert margin == pytest.approx(c["soft"] + (c["edge"]["width"] if c["edge"] else 0.0))
    assert burn.threshold(c, 0.0) == pytest.approx(-margin) and burn.threshold(c, 1.0) == pytest.approx(1 + margin)
    assert burn.threshold(c, 0.5) == pytest.approx(0.5)
    assert (field - burn.threshold(c, 0.0)).min() >= margin - 1e-6         # p = 0: 全部の画素が前線の R より先（m・e・焦げの帯は 0）
    assert (burn.threshold(c, 1.0) - field).min() >= margin - 1e-6         # p = 1: 全部の画素が前線の R より後ろ（m は 1、e・焦げの帯は 0）
    assert margin * (burn.CHAR_AHEAD + burn.CHAR_HALF) < margin and margin * (burn.CHAR_HALF - burn.CHAR_AHEAD) < margin    # 焦げの帯は R の内側


@pytest.mark.parametrize("changes", [{"edge": None, "char": 0.0}, {"edge": None, "char": 0.0, "bias": 0.7, "from": [0.9, 0.1]}])
def test_the_revealed_share_grows_with_the_progress(changes):
    """今のショットが見えている画素の割合は、p について減らず、0 から 1 まで行く。"""
    c = conf(**changes)
    field = burn.field(c, SIZE)
    before, now = np.full((90, 160, 3), 10, np.uint8), np.full((90, 160, 3), 200, np.uint8)
    shares = [float((burn.composite(before, now, field, p, c)[..., 0] > 10).mean()) for p in np.linspace(0, 1, 41)]
    assert shares[0] == 0.0 and shares[-1] == 1.0
    assert all(b >= a for a, b in zip(shares, shares[1:]))
    assert 0.2 < shares[20] < 0.8 and len(set(shares)) > 20
    done = [float((burn.composite(before, now, field, p, c)[..., 0] == 200).mean()) for p in np.linspace(0, 1, 41)]   # 今のショットだけになった画素
    assert done[0] == 0.0 and done[-1] == 1.0 and all(b >= a for a, b in zip(done, done[1:]))


def test_pixels_away_from_the_front_keep_one_shot():
    """縁と焦げは前線の近くだけ。前線から soft + 縁の幅より遠い画素は、前のショットか今のショットの値そのまま。"""
    c = conf()
    field, margin = burn.field(c, SIZE), burn.reach(conf())
    before, now = planes()
    for p in (0.2, 0.5, 0.8):
        image, ahead = burn.composite(before, now, field, p, c), field - burn.threshold(c, p)
        untouched, replaced, lit = ahead > margin + 1e-6, ahead < -margin - 1e-6, np.abs(ahead) < c["edge"]["width"] - 1e-3
        assert untouched.any() and replaced.any() and lit.any()
        assert np.array_equal(image[untouched], before[untouched]) and np.array_equal(image[replaced], now[replaced])
        changed = (image != before).any(-1) & (image != now).any(-1)
        assert changed[lit].all() and not changed[untouched | replaced].any()            # 縁の中はどちらの画とも違い、遠い所は 1 画素も変わらない


def test_edge_lights_the_front_and_char_darkens_just_ahead_of_it():
    field = burn.field(conf(), SIZE)
    before, now = np.full((90, 160, 3), 100, np.uint8), np.full((90, 160, 3), 100, np.uint8)
    p = 0.5
    plain = burn.composite(before, now, field, p, conf(edge=None, char=0.0))
    assert np.allclose(plain, 100, atol=1e-4)                                           # 同じ画どうしなら、ただの焼け抜けは何も変えない（float の丸めの範囲）
    lit = burn.composite(before, now, field, p, conf(char=0.0))
    ahead = field - burn.threshold(conf(), p)
    inside, outside = np.abs(ahead) < 0.05 - 1e-4, np.abs(ahead) > 0.05 + 1e-4
    assert inside.any() and (lit[inside] > 100).all() and np.allclose(lit[outside], 100, atol=1e-4)     # 縁は前線の前後 width だけ
    assert lit[..., 0].max() > lit[..., 1].max() > lit[..., 2].max() > 100             # 縁の色（赤が強く、青が弱い）のスクリーン合成
    charred = burn.composite(before, now, field, p, conf(char=0.6, edge={"gain": 0}))   # 焦げだけ（縁の光なし。R = soft）
    ahead = field - burn.threshold(conf(edge=None), p)
    center, half = 0.035 * burn.CHAR_AHEAD, 0.035 * burn.CHAR_HALF
    band = np.abs(ahead - center) < half - 1e-4
    assert center - half < 0 < center < center + half < 0.035                           # 帯は前線の少し後ろから先まで。中心は前線の先（前のショットの側）
    assert band.any() and (charred[band] < 100).all() and np.allclose(charred[np.abs(ahead - center) > half + 1e-4], 100, atol=1e-4)
    assert charred.min() >= 100 * (1 - 0.6) - 1e-3                                     # いちばん暗くても char の割合まで
    deepest = np.unravel_index(np.argmin(charred[..., 0]), charred.shape[:2])
    assert ahead[deepest] == pytest.approx(center, abs=3e-3)                           # いちばん暗いのは帯の中心


def test_gain_zero_is_the_same_as_no_edge():
    """edge が無いのと gain 0 は同じ設定になり、同じ絵になる。edge を書けば既定の色・幅・強さが入る。"""
    assert conf(edge={"color": [1, 2, 3], "width": 0.3, "gain": 0}) == conf(edge=None)
    assert sequence._transition({"type": "burn", "duration_s": 0.5}) == {
        "type": "burn", "duration_s": 0.5, "seed": 0, "scale": 0.08, "soft": 0.035, "edge": None, "char": 0.0, "from": [0.5, 0.5], "bias": 0.0}
    assert sequence._transition({"type": "burn", "duration_s": 0.5, "edge": {}})["edge"] == {"color": [255.0, 125.0, 35.0], "width": 0.05, "gain": 0.9}


# ---- timeline -----------------------------------------------------------------------------------

def test_burn_is_planned_in_the_xfade_window(tmp_path):
    """窓は xfade と同じ（start から duration_s の間、前のショットを描き続ける）。進みは直線。"""
    write_shots(tmp_path)
    data = sequence.parse_timeline(timeline_dict(burn_shots()), tmp_path).data
    crossfade = burn_shots()
    crossfade[1]["transition_in"] = {"type": "xfade", "duration_s": 0.5}
    crossfade = sequence.parse_timeline(timeline_dict(crossfade), tmp_path).data
    for t in (0.99, 1.0, 1.125, 1.49, 1.5, 2.2):      # 重ねる 2 つのショットとその中の時刻は、同じ長さの xfade と同じ
        layers, dim, flash = sequence.plan_frame(data, t)
        theirs = sequence.plan_frame(crossfade, t)[0]
        assert [(i, local) for i, local, _ in layers] == [(i, local) for i, local, _ in theirs] and dim == 1.0 and flash is None
    assert sequence.burn_at(data, 0.99) is None and sequence.burn_at(data, 1.5) is None and sequence.burn_at(data, 2.2) is None
    index, transition, p = sequence.burn_at(data, 1.125)
    assert (index, p) == (1, 0.25) and transition == data["shots"][1]["transition_in"] == conf()
    assert sequence.burn_at(data, 1.0)[2] == 0.0
    assert [w for _, _, w in sequence.plan_frame(data, 1.125)[0]] == [0.75, 0.25]


def test_timeline_frames_start_at_the_old_shot_and_end_at_the_new_one(tmp_path):
    """つないだコマ（plan_frame → blend → finish）は、窓の頭で前のショット、窓の終わりで今のショットとバイト単位で同じ。途中は式どおり。"""
    write_shots(tmp_path)
    data = sequence.parse_timeline(timeline_dict(burn_shots()), tmp_path).data
    images = planes(21)
    field = burn.field(data["shots"][1]["transition_in"], SIZE)

    def frame(t):
        layers, dim, flash = sequence.plan_frame(data, t)
        burning = sequence.burn_at(data, t)
        mixed = sequence.blend([(images[i % 2], weight) for i, _, weight in layers], dim, flash,
                               None if burning is None else (burning[1], burning[2], field))
        return sequence.finish(mixed)

    old, new = sequence.finish(images[0]), sequence.finish(images[1])
    assert np.array_equal(frame(1.0), old) and np.array_equal(frame(11 / 12), old)       # 窓の頭（p = 0）とその前
    assert np.array_equal(frame(1.5), new) and np.array_equal(frame(19 / 12), new)       # 窓の終わりとその後
    middle = frame(1.25)
    assert np.array_equal(middle, sequence.finish(burn.composite(images[0], images[1], field, 0.5, conf())))
    assert (middle != old).any() and (middle != new).any()


def test_burn_errors_name_the_cause(tmp_path):
    write_shots(tmp_path)
    cases = [(lambda r: r["shots"][0].update(transition_in=dict(FULL)), "最初のショットは burn"),
             (lambda r: r["shots"][1]["transition_in"].update(duration_s=1.5), "burn の duration_s 1.5 秒がショットの長さ"),
             (lambda r: r["shots"][1]["transition_in"].pop("duration_s"), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(scale=0), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(scale=5), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(soft=0), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(bias=1.5), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(char=-0.1), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(seed=1.5), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update({"from": [0.5, 1.5]}), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(edge={"width": 0}), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(edge={"colour": [1, 2, 3]}), "invalid"),
             (lambda r: r["shots"][1]["transition_in"].update(ease="in"), "invalid")]
    for change, message in cases:
        raw = timeline_dict(burn_shots())
        change(raw)
        with pytest.raises(PipelineError, match=message):
            sequence.parse_timeline(raw, tmp_path)


def test_the_long_shot_warning_counts_the_next_burn(tmp_path):
    """次のショットが burn なら、前のショットはその間も描き続けるので、spec の長さを超える分の注意に含める（xfade と同じ）。"""
    write_shots(tmp_path)                                                       # spec の長さは 2 秒
    shots = [{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.8, "transition_in": {"type": "burn", "duration_s": 0.4}},
             {"spec": "c.json", "start": 2.5}]
    warnings = sequence.parse_timeline(timeline_dict(shots), tmp_path).warnings
    assert any("a.json" in w and "spec の長さ 2 秒を超えて 2.2 秒まで" in w and "burn" in w for w in warnings), warnings


def test_the_record_and_the_plan_name_the_burn(tmp_path):
    from mv_pipeline.gate import checks
    write_shots(tmp_path)
    raw = timeline_dict(burn_shots(), beats={"bpm": 120, "count": 16})
    timeline = sequence.parse_timeline(raw, tmp_path)
    record = sequence.timeline_record(timeline, SIZE)
    checks._validate_timeline(record)                                           # mv gate check が読める形
    assert [(s["t"], s["kind"]) for s in record["seam_info"]] == [(1.0, "burn"), (2.0, "cut")] and record["flash_info"] == []
    assert record["clips"][1]["transition_in"] == conf()
    assert checks.check_beats(record, None, checks.THRESHOLDS)["status"] == "pass"
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    text = sequence.plan_text(path)
    assert text.startswith("# fx-timeline/0.2 ") and "burn 0.5s" in text


# ---- 版（0.1 と 0.2） ---------------------------------------------------------------------------

OLD_SHOTS = [{"spec": "a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.5}},
             {"spec": "b.json", "start": 1.0, "offset_s": 0.25, "transition_in": {"type": "xfade", "duration_s": 0.4}},
             {"spec": "c.json", "start": 2.0, "transition_in": {"type": "flash"}}]


def frame_0_1(data, t, images):
    """0.1 のつなぎ（black・xfade・flash）の式を、burn を知らない書き方でそのまま計算したコマ（実制作のレンダラのつなぎの行と同じ形）。"""
    shots = data["shots"]
    index = max(i for i, shot in enumerate(shots) if shot["start"] <= t)
    shot, kind = shots[index], shots[index]["transition_in"]
    image, since = images[index % 2].astype(np.float32), t - shot["start"]
    if kind["type"] == "xfade" and t < shot["start"] + kind["duration_s"]:
        a = sequence.ease(since / kind["duration_s"])
        image = images[(index - 1) % 2].astype(np.float32) * (1 - a) + image * a
    elif kind["type"] == "black" and t < shot["start"] + kind["duration_s"]:
        image = image * sequence.ease(since / kind["duration_s"])
    if kind["type"] == "flash" and since < kind["window_s"]:
        image = image + (np.asarray(kind["color"], np.float32) - image) * (kind["strength"] * math.exp(-since * kind["rate"]))
    return np.clip(image, 0, 255).astype(np.uint8)


def test_a_0_1_timeline_reads_as_before(tmp_path):
    """0.1 の timeline は 0.1 の schema で検証して読め、0.2 と書いた同じ timeline と同じ正規形・同じ計画になる。
    つないだコマは、burn を足す前のつなぎの式とバイト単位で同じ。"""
    write_shots(tmp_path)
    old = sequence.parse_timeline(timeline_dict(OLD_SHOTS, version="fx-timeline/0.1", look={"grain": {"sigma": 2.0}}), tmp_path)
    new = sequence.parse_timeline(timeline_dict(OLD_SHOTS, look={"grain": {"sigma": 2.0}}), tmp_path)
    assert sequence.SCHEMA_VERSION == "fx-timeline/0.2" and sequence.SCHEMA_V01 == "fx-timeline/0.1"
    assert (old.source_schema, new.source_schema) == ("fx-timeline/0.1", "fx-timeline/0.2")
    assert old.data == new.data and old.data["schema_version"] == "fx-timeline/0.2" and old.warnings == new.warnings
    images = planes(13)
    for n in range(36):
        t = n / 12
        assert sequence.plan_frame(old.data, t) == sequence.plan_frame(new.data, t) and sequence.burn_at(old.data, t) is None
        layers, dim, flash = sequence.plan_frame(old.data, t)
        assert np.array_equal(sequence.finish(sequence.blend([(images[i % 2], w) for i, _, w in layers], dim, flash)), frame_0_1(old.data, t, images)), n


def test_the_0_1_schema_is_the_schema_before_burn():
    """0.1 の schema は burn を知らない（0.2 の schema だけが持つ）。ほかの定義は同じ。"""
    old, new = sequence.schema(sequence.SCHEMA_V01), sequence.schema()
    kinds = lambda data: [item["properties"]["type"]["const"] for item in data["properties"]["shots"]["items"]["properties"]["transition_in"]["oneOf"]]  # noqa: E731
    assert kinds(old) == ["cut", "xfade", "black", "flash"] and kinds(new) == ["cut", "xfade", "black", "flash", "burn"]
    assert old["properties"]["schema_version"] == {"const": "fx-timeline/0.1"} and new["properties"]["schema_version"] == {"const": "fx-timeline/0.2"}
    new["properties"]["shots"]["items"]["properties"]["transition_in"]["oneOf"].pop()
    for data in (old, new):
        data.pop("title"), data.pop("description"), data["properties"].pop("schema_version")
    assert old == new


def test_burn_in_a_0_1_timeline_is_refused(tmp_path):
    import jsonschema
    write_shots(tmp_path)
    raw = timeline_dict(burn_shots(), version="fx-timeline/0.1")
    with pytest.raises(PipelineError, match="burn（焼け抜け）は fx-timeline/0.2 のつなぎ"):
        sequence.parse_timeline(raw, tmp_path)
    assert list(jsonschema.Draft202012Validator(sequence.schema(sequence.SCHEMA_V01)).iter_errors(raw))       # 0.1 の schema でも通らない
    assert not list(jsonschema.Draft202012Validator(sequence.schema()).iter_errors({**raw, "schema_version": "fx-timeline/0.2"}))
    for version in ("fx-timeline/0.3", None, 0.2):
        with pytest.raises(PipelineError, match="schema_version must be 'fx-timeline/0.2' or 'fx-timeline/0.1'"):
            sequence.parse_timeline({**raw, "schema_version": version}, tmp_path)


def test_plan_text_names_the_version_of_the_file(tmp_path):
    write_shots(tmp_path)
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(timeline_dict(OLD_SHOTS, version="fx-timeline/0.1")), encoding="utf-8")
    assert sequence.plan_text(path).startswith("# fx-timeline/0.1 ")


# ---- 描画（cv2） ----------------------------------------------------------------------------------

@pytest.fixture
def scene(tmp_path):
    pytest.importorskip("cv2")
    fx_synth.make_scene(tmp_path)
    write_shots(tmp_path, names=("a",))
    bright = fx_synth.spec_dict(camera={"keys": [{"t": 0, "zoom": 1.3, "center": [0.4, 0.5]}, {"t": 2.0, "zoom": 1.1, "center": [0.55, 0.5]}]},
                                look={"grade": {"keys": [{"t": 0, "gain": [3.0, 2.4, 1.6]}]}})
    (tmp_path / "b.json").write_text(json.dumps(bright), encoding="utf-8")
    return tmp_path


def burn_timeline(**extra):
    return timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 0.5, "offset_s": 0.25, "transition_in": dict(FULL)}],
                         duration_s=1.5, **extra)


def test_rendered_burn_frames_are_the_two_fx_frames_composited(scene):
    """burn のコマは、前のショットを描き続けた fx の 1 コマと今のショットの 1 コマを、式で混ぜたもの。窓の頭と終わりは fx のコマそのまま。"""
    from mv_pipeline.fx import render
    from mv_pipeline.fx import spec as fxspec
    ctx = sequence.Context(sequence.parse_timeline(burn_timeline(), scene))
    fx_a = render.prepare(fxspec.load_spec(scene / "a.json"), size=SIZE)
    fx_b = render.prepare(fxspec.load_spec(scene / "b.json"), size=SIZE)
    assert np.array_equal(ctx.frame(0.5), render.frame(fx_a, 0.5, "both"))               # p = 0: 前のショットだけ
    assert np.array_equal(ctx.frame(1.0), render.frame(fx_b, 0.75, "both"))              # 窓の終わり: 今のショットだけ
    a, b = render.frame(fx_a, 0.75, "both"), render.frame(fx_b, 0.5, "both")
    expected = sequence.finish(burn.composite(a, b, burn.field(conf(), SIZE), 0.5, conf()))
    middle = ctx.frame(0.75)
    assert np.array_equal(middle, expected) and (middle != a).any() and (middle != b).any()
    ctx.close()


def test_a_section_renders_the_same_frames_as_the_whole(scene):
    """時刻だけで決まる: 窓の途中から描き始めても（--start）、頭から順に描いたときと同じコマ。順番を変えても同じ。"""
    timeline = sequence.parse_timeline(burn_timeline(look={"grain": {"sigma": 2.0}}), scene)
    fps = timeline.data["output"]["fps"]
    whole = sequence.Context(timeline)
    frames = [whole.frame(fxtimeline.frame_time(i, fps)) for i in range(18)]
    whole.close()
    assert len({frame.tobytes() for frame in frames[6:12]}) == 6                         # 窓（6〜11 コマ目）のコマは 1 つずつ違う
    section = sequence.Context(timeline)
    start = 8 / fps                                                                      # 窓の途中（p = 1/3）から
    for i in range(6):
        assert np.array_equal(section.frame(fxtimeline.frame_time(i, fps, start)), frames[8 + i]), i
    for i in (11, 3, 9, 6):
        assert np.array_equal(section.frame(i / fps), frames[i]), i
    section.close()


def test_the_scale_option_shows_the_same_pattern(scene):
    """--scale 2 で描いた burn のコマを縮めると、等倍のコマとほぼ同じ（模様が解像度に付いてこない）。"""
    timeline = sequence.parse_timeline(burn_timeline(), scene)
    one, two = sequence.Context(timeline), sequence.Context(timeline, scale=2.0)
    small, large = one.frame(0.75).astype(np.float32), two.frame(0.75).astype(np.float32)
    one.close(), two.close()
    shrunk = large.reshape(SIZE[1], 2, SIZE[0], 2, 3).mean((1, 3))
    assert large.shape == (180, 320, 3) and np.abs(shrunk - small).mean() < 6.0
    other = sequence.Context(sequence.parse_timeline(timeline_dict(burn_timeline()["shots"][:1] + [
        {**burn_timeline()["shots"][1], "transition_in": {**FULL, "seed": 4}}], duration_s=1.5), scene))
    assert np.abs(other.frame(0.75).astype(np.float32) - small).mean() > 3 * np.abs(shrunk - small).mean()      # seed を変えた差よりずっと小さい
    other.close()


def test_a_0_1_timeline_renders_the_same_frames_as_its_0_2_twin(scene):
    write_shots(scene, names=("c",))
    old = sequence.Context(sequence.parse_timeline(timeline_dict(OLD_SHOTS, version="fx-timeline/0.1", look={"vignette": {}, "grain": {}}), scene))
    new = sequence.Context(sequence.parse_timeline(timeline_dict(OLD_SHOTS, look={"vignette": {}, "grain": {}}), scene))
    for n in range(36):
        assert np.array_equal(old.frame(n / 12), new.frame(n / 12)), n
    old.close(), new.close()


def test_the_demo_stays_a_0_1_timeline_without_burn(tmp_path):
    """デモの timeline は 0.1 のまま（burn を入れない）。0.2 の実装でそのまま読めて描ける。"""
    pytest.importorskip("cv2")
    sequence.demo(tmp_path, width=160, height=90, duration=3.0, fps=8)
    raw = json.loads((tmp_path / "timeline.json").read_text(encoding="utf-8"))
    assert raw["schema_version"] == "fx-timeline/0.1"
    assert [shot["transition_in"]["type"] for shot in raw["shots"]] == ["black", "xfade", "flash", "xfade"]
    timeline = sequence.load_timeline(tmp_path / "timeline.json")
    assert timeline.source_schema == "fx-timeline/0.1" and timeline.warnings == ()
    ctx = sequence.Context(timeline)
    assert ctx.frame(1.0).shape == (90, 160, 3)
    ctx.close()


def test_run_with_a_burn_and_a_section_of_it(scene):
    """burn のある timeline を run として描く（全体と、窓の途中からの区間）。記録の切れ目は burn、mv gate check が読める。"""
    from mv_pipeline.gate import checks
    path = scene / "timeline.json"
    path.write_text(json.dumps(burn_timeline()), encoding="utf-8")
    whole = sequence.render_run(path, quiet=True)
    assert whole["technical_pass"] and whole["interval"]["frames"] == 18
    record = json.loads(open(whole["timeline"], encoding="utf-8").read())
    checks._validate_timeline(record)
    assert [(s["t"], s["kind"]) for s in record["seam_info"]] == [(0.5, "burn")]
    manifest = json.loads((scene / "fx_runs" / whole["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    copied = json.loads((scene / "fx_runs" / whole["run_id"] / manifest["timeline"]["file"]).read_text(encoding="utf-8"))
    assert copied["schema_version"] == "fx-timeline/0.2" and copied["shots"][1]["transition_in"]["type"] == "burn"
    assert sequence.render_run(path, quiet=True)["reused"]
    part = sequence.render_run(path, start=8 / 12, duration=0.5, quiet=True)
    assert part["technical_pass"] and not part["reused"] and part["interval"]["frames"] == 6
    changed = burn_timeline()
    changed["shots"][1]["transition_in"]["seed"] = 4
    path.write_text(json.dumps(changed), encoding="utf-8")
    assert not sequence.render_run(path, quiet=True)["reused"]                           # seed を変えたら描き直す
