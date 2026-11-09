"""fx-timeline/0.1（fx/sequence.py）: ショットの並び・つなぎ（cut・xfade・black・flash）・snap・timeline の look・gate の記録・run。

つなぎと snap の式は、実制作のレンダラの式を写した関数（private_*）と合成の配列で比べる。描画を使うテストは cv2（fx extra）が要る。
"""
import contextlib
import io
import json
import math

import numpy as np
import pytest

import fx_synth
from mv_pipeline.fx import sequence
from mv_pipeline.io import PipelineError


# ---- 実制作のレンダラの式（写し） ----------------------------------------------------------

def private_snap(t, beats, duration):
    if t <= 0:
        return 0.0
    if t >= duration - 0.01:
        return duration
    return float(beats[np.argmin(np.abs(beats - t))])


def private_shots(raw, beats, duration):
    shots = []
    for i, r in enumerate(raw):
        st = private_snap(r["t"], beats, duration) if r.get("snap", True) else r["t"]
        nxt = raw[i + 1] if i + 1 < len(raw) else None
        en = (private_snap(nxt["t"], beats, duration) if nxt.get("snap", True) else nxt["t"]) if nxt else duration
        shots.append(dict(r, start=st, end=en))
    return shots


def private_index(shots, t):
    for i, s in enumerate(shots):
        if s["start"] <= t < s["end"]:
            return i
    return len(shots) - 1


def private_ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def private_frame(shots, t, render_shot):
    i = private_index(shots, t)
    sh = shots[i]
    f = render_shot(sh, t).astype(np.float32)
    td = sh.get("td", 0)
    if i > 0 and sh.get("tin") == "xfade" and t < sh["start"] + td:
        a = private_ease((t - sh["start"]) / td)
        f = render_shot(shots[i - 1], t).astype(np.float32) * (1 - a) + f * a
    elif sh.get("tin") == "black" and t < sh["start"] + td:
        f = f * private_ease((t - sh["start"]) / td)
    if sh.get("tin") == "flash" and t - sh["start"] < 0.30:
        f = f + (np.array([235, 210, 255], np.float32) - f) * (sh.get("flash", 0.75) * math.exp(-(t - sh["start"]) * 13))
    return np.clip(f, 0, 255).astype(np.uint8)


# ---- 合成の timeline ----------------------------------------------------------------------

def write_shots(directory, names=("a", "b", "c"), **changes):
    for name in names:
        data = fx_synth.spec_dict(**changes)
        (directory / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")


def timeline_dict(shots, **extra):
    raw = {"schema_version": sequence.SCHEMA_VERSION, "output": {"width": 160, "height": 90, "fps": 12}, "duration_s": 3.0, "shots": shots}
    raw.update(extra)
    return raw


def parse(tmp_path, raw):
    return sequence.parse_timeline(raw, tmp_path)


TRANSITIONS = [{"spec": "a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.5}},
               {"spec": "b.json", "start": 1.0, "offset_s": 0.25, "transition_in": {"type": "xfade", "duration_s": 0.4}},
               {"spec": "c.json", "start": 2.0, "transition_in": {"type": "flash"}}]


def test_shots_end_at_the_next_start_and_the_last_at_duration(tmp_path):
    write_shots(tmp_path)
    data = parse(tmp_path, timeline_dict(TRANSITIONS)).data
    assert [(s["start"], s["end"]) for s in data["shots"]] == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]
    assert data["shots"][2]["transition_in"] == {"type": "flash", **sequence.FLASH}
    assert [sequence.shot_index(data, t) for t in (0.0, 0.99, 1.0, 2.5, 3.0, 9.0)] == [0, 0, 1, 2, 2, 2]


def test_plan_frame_weights_follow_the_transition_formulas(tmp_path):
    write_shots(tmp_path)
    data = parse(tmp_path, timeline_dict(TRANSITIONS)).data
    layers, dim, flash = sequence.plan_frame(data, 0.25)                 # black の途中
    assert layers == [(0, 0.25, 1.0)] and dim == pytest.approx(sequence.ease(0.5)) and flash is None
    assert sequence.plan_frame(data, 0.5) == ([(0, 0.5, 1.0)], 1.0, None)  # black が終わった
    layers, dim, flash = sequence.plan_frame(data, 1.1)                  # xfade の途中。前のショットを続けて描く
    a = sequence.ease(0.1 / 0.4)
    assert [(i, round(local, 9)) for i, local, _ in layers] == [(0, 1.1), (1, 0.35)]
    assert [w for _, _, w in layers] == pytest.approx([1 - a, a]) and dim == 1.0
    assert sequence.plan_frame(data, 1.4)[0] == [(1, pytest.approx(0.65), 1.0)]
    layers, dim, flash = sequence.plan_frame(data, 2.1)                  # flash の窓の中
    assert flash[0] == pytest.approx(0.75 * math.exp(-0.1 * 13)) and flash[1] == [235.0, 210.0, 255.0]
    assert sequence.plan_frame(data, 2.35)[2] is None                   # 窓（0.30 秒）を過ぎた（2.3 − 2.0 は 0.2999… で窓の中。実制作も同じ）


def test_transitions_equal_the_private_formulas_on_synthetic_frames(tmp_path):
    """実制作の render_frame のつなぎの行と、公開の plan_frame → blend → finish が全コマでバイト単位で同じ。"""
    write_shots(tmp_path, names=("a", "b", "c", "d"))
    raw_private = [{"t": 0.0, "tin": "black", "td": 0.6, "snap": False}, {"t": 0.9, "tin": "xfade", "td": 0.5, "snap": False},
                   {"t": 1.7, "tin": "flash", "flash": 0.6, "snap": False}, {"t": 2.2, "tin": "cut", "snap": False}]
    rng = np.random.default_rng(3)
    planes = [rng.uniform(-40, 300, (6, 8, 3)).astype(np.float32) for _ in raw_private]

    def image(index, t):         # ショットごとに時刻で変わる合成の画（0〜255 の外の値も含む。描画の float の値を模す）
        return planes[index] * (1 + 0.1 * math.sin(3 * t)) + 7 * t

    private = private_shots(raw_private, np.array([0.0]), 3.0)
    for shot, i in zip(private, range(4)):
        shot["index"] = i
    timeline = timeline_dict([
        {"spec": "a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.6}},
        {"spec": "b.json", "start": 0.9, "transition_in": {"type": "xfade", "duration_s": 0.5}},
        {"spec": "c.json", "start": 1.7, "transition_in": {"type": "flash", "strength": 0.6}},
        {"spec": "d.json", "start": 2.2}])
    data = parse(tmp_path, timeline).data
    for n in range(36):
        t = n / 12
        layers, dim, flash = sequence.plan_frame(data, t)
        mine = sequence.finish(sequence.blend([(image(i, t), w) for i, _, w in layers], dim, flash))   # 実制作も前のショットを同じ t で描く
        theirs = private_frame(private, t, lambda sh, tt: image(sh["index"], tt))
        assert np.array_equal(mine, theirs), n


def test_snap_and_shot_bounds_equal_the_private_construction(tmp_path):
    """snap（いちばん近い拍・同じ距離なら前・端の扱い）と、各ショットの始まりと終わり・各コマのショットが実制作と同じ。"""
    rng = np.random.default_rng(11)
    beats = np.cumsum(rng.uniform(0.35, 0.6, 40)) + 0.02
    duration = float(beats[-1] + 0.3)
    picks = [2 + 3 * k + int(rng.integers(2)) for k in range(9)]                  # 拍 2 つ以上あけ、拍の間隔の 4 割より近くに置く
    starts = [0.0] + [float(beats[i] + rng.uniform(-0.15, 0.15)) for i in picks]
    snapped = [False] + [bool(rng.integers(2)) for _ in picks]
    write_shots(tmp_path, names=tuple(f"s{i}" for i in range(len(starts))))
    path = tmp_path / "beats.json"
    path.write_text(json.dumps({"beats": beats.tolist()}), encoding="utf-8")
    raw = timeline_dict([{"spec": f"s{i}.json", "start": t, "snap": s} for i, (t, s) in enumerate(zip(starts, snapped))],
                        duration_s=duration, beats={"path": "beats.json"})
    data = parse(tmp_path, raw).data
    private = private_shots([{"t": t, "snap": s} for t, s in zip(starts, snapped)], beats, duration)
    assert [(s["start"], s["end"]) for s in data["shots"]] == [(s["start"], s["end"]) for s in private]
    for n in range(int(duration * 12)):
        assert sequence.shot_index(data, n / 12) == private_index(private, n / 12)
    assert sequence.snap(0.0, beats, duration) == 0.0 and sequence.snap(duration - 0.005, beats, duration) == duration
    middle = float((beats[3] + beats[4]) / 2)
    assert sequence.snap(middle, beats, duration) == private_snap(middle, beats, duration)


def test_times_accept_beats_bars_and_frames(tmp_path):
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": {"beat": 2}},
                         {"spec": "c.json", "start": {"frame": 30}}], beats={"bpm": 120, "count": 16})
    data = parse(tmp_path, raw).data
    assert [s["start"] for s in data["shots"]] == [0.0, 1.0, 2.5]
    raw["shots"][1]["start"] = {"bar": 0, "beat": 3}
    assert parse(tmp_path, raw).data["shots"][1]["start"] == 1.5


@pytest.mark.parametrize("change, message", [
    (lambda r: r["shots"][0].update(start=0.5), "0 秒から"),
    (lambda r: r["shots"][1].update(start=2.5), "長さが 0 以下"),
    (lambda r: r["shots"][2].update(start=3.0), "長さが 0 以下"),
    (lambda r: r["shots"][0].update(transition_in={"type": "xfade", "duration_s": 0.2}), "最初のショットは xfade"),
    (lambda r: r["shots"][1].update(transition_in={"type": "xfade", "duration_s": 1.5}), "ショットの長さ"),
    (lambda r: r["shots"][1].update(snap=True), "snap には beats"),
    (lambda r: r["shots"][1].update(offset_s=-1), "invalid"),
    (lambda r: r.pop("duration_s"), "invalid"),
    (lambda r: r.update(fps=24), "invalid"),
    (lambda r: r["shots"][2].update(transition_in={"type": "flash", "strength": 2}), "invalid"),
])
def test_timeline_errors_name_the_cause(tmp_path, change, message):
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.0}, {"spec": "c.json", "start": 2.0}])
    change(raw)
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, raw)


def test_snap_onto_the_same_beat_is_an_error(tmp_path):
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.1, "snap": True},
                         {"spec": "c.json", "start": 1.2, "snap": True}], beats={"bpm": 60, "count": 8})
    with pytest.raises(PipelineError, match="snap で寄せた後"):
        parse(tmp_path, raw)


def test_warnings_for_long_shots_other_fps_and_far_snaps(tmp_path):
    write_shots(tmp_path)
    (tmp_path / "c.json").write_text(json.dumps(fx_synth.spec_dict(output={"width": 160, "height": 90, "fps": 24})), encoding="utf-8")
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 0.6, "snap": True, "offset_s": 1.5},
                         {"spec": "c.json", "start": 2.0}], beats={"bpm": 60, "count": 8})
    warnings = parse(tmp_path, raw).warnings
    assert any("b.json" in w and "spec の長さ 2 秒を超えて 2.5 秒まで" in w for w in warnings)
    assert any("c.json" in w and "fps 24" in w for w in warnings)
    assert any("snap で start が 0.6 → 1 秒" in w for w in warnings)


def test_look_runs_after_the_transitions(tmp_path):
    """黒から上げる頭のコマは、look が無ければ真っ黒、粒状感の look があれば粒だけが乗る（つなぎの後に掛かる。実制作と同じ順）。"""
    from mv_pipeline.fx import look as fxlook
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.5}}],
                        look={"grain": {"sigma": 3.0}, "vignette": {}})
    data = parse(tmp_path, raw).data
    layers, dim, flash = sequence.plan_frame(data, 0.0)
    lit = np.full((90, 160, 3), 120, np.uint8)
    dark = sequence.blend([(lit, 1.0)], dim, flash)
    assert not sequence.finish(dark).any()
    look = fxlook.prepare(data["look"], (160, 90), 12)
    grained = sequence.finish(dark, look, 0.0)
    assert grained.any() and grained.max() < 16
    assert np.array_equal(sequence.finish(lit.astype(np.float32), look, 0.5), fxlook.finish(look, lit.astype(np.float32) / 255, 0.5))


def test_record_is_a_gate_timeline_and_passes_the_beat_check(tmp_path):
    from mv_pipeline.gate import checks
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": {"beat": 2}, "transition_in": {"type": "xfade", "duration_s": 0.3}},
                         {"spec": "c.json", "start": 2.05, "snap": True, "transition_in": {"type": "flash"}, "align": "downbeat"}],
                        beats={"bpm": 120, "count": 16})
    timeline = parse(tmp_path, raw)
    record = sequence.timeline_record(timeline, (160, 90))
    checks._validate_timeline(record)
    assert record["schema_version"] == "dance-timeline/0.1" and record["interval"] == {"start": 0.0, "duration": 3.0, "frames": 36}
    assert [(s["t"], s["kind"], s["align"]) for s in record["seam_info"]] == [(1.0, "xfade", "beat"), (2.0, "flash", "downbeat")]
    assert record["flash_info"] == [{"t": 2.0, "strength": 0.75, "decay_s": 1 / 13, "window_s": 0.3, "mode": "lerp_display",
                                     "beat_exempt": False, "align": "downbeat"}]
    result = checks.check_beats(record, None, checks.THRESHOLDS)
    assert result["status"] == "pass", result
    record["seam_info"][0]["t"] = 1.2
    assert checks.check_beats(record, None, checks.THRESHOLDS)["status"] == "fail"


def test_plan_text_lists_shots_beats_and_snaps(tmp_path):
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.1, "snap": True, "transition_in": {"type": "xfade", "duration_s": 0.3}},
                         {"spec": "c.json", "start": 2.0}], beats={"bpm": 120, "count": 16})
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    text = sequence.plan_text(path)
    assert "ショット 3" in text and "xfade 0.3s" in text and "(snap 1.100→)" in text and " 2+0.000" in text


# ---- 描画（cv2） ----------------------------------------------------------------------------

@pytest.fixture
def scene(tmp_path):
    pytest.importorskip("cv2")
    fx_synth.make_scene(tmp_path)
    write_shots(tmp_path, names=("a", "b"))
    data = fx_synth.spec_dict(camera={"keys": [{"t": 0, "zoom": 1.3, "center": [0.4, 0.5]}, {"t": 2.0, "zoom": 1.1, "center": [0.55, 0.5]}]})
    (tmp_path / "b.json").write_text(json.dumps(data), encoding="utf-8")
    return tmp_path


def test_a_cut_frame_is_the_fx_frame_of_the_shot(scene):
    """look の無い timeline のカットのコマは、そのショットの fx の 1 コマ（offset_s + (t − start)）とバイト単位で同じ。"""
    from mv_pipeline.fx import render
    from mv_pipeline.fx import spec as fxspec
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.0, "offset_s": 0.5, "variant": "layers"}], duration_s=2.0)
    ctx = sequence.Context(parse(scene, raw))
    fx_b = render.prepare(fxspec.load_spec(scene / "b.json"), size=(160, 90))
    fx_a = render.prepare(fxspec.load_spec(scene / "a.json"), size=(160, 90))
    assert np.array_equal(ctx.frame(0.5), render.frame(fx_a, 0.5, "both"))
    assert np.array_equal(ctx.frame(1.25), render.frame(fx_b, 0.75, "layers"))


def test_an_xfade_frame_blends_both_fx_frames(scene):
    from mv_pipeline.fx import render
    from mv_pipeline.fx import spec as fxspec
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.0, "transition_in": {"type": "xfade", "duration_s": 0.5}}],
                        duration_s=2.0)
    ctx = sequence.Context(parse(scene, raw))
    a = render.frame(render.prepare(fxspec.load_spec(scene / "a.json"), size=(160, 90)), 1.25, "both")
    b = render.frame(render.prepare(fxspec.load_spec(scene / "b.json"), size=(160, 90)), 0.25, "both")
    w = sequence.ease(0.25 / 0.5)
    expected = np.clip(a.astype(np.float32) * (1 - w) + b.astype(np.float32) * w, 0, 255).astype(np.uint8)
    assert np.array_equal(ctx.frame(1.25), expected)


def test_shots_render_at_the_timeline_size_and_only_two_stay_prepared(scene):
    write_shots(scene, names=("c",))
    raw = timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 0.5}, {"spec": "c.json", "start": 1.0},
                         {"spec": "a.json", "start": 1.5}], duration_s=2.0, output={"width": 96, "height": 96, "fps": 12})
    ctx = sequence.Context(parse(scene, raw))
    assert ctx.frame(0.1).shape == (96, 96, 3)
    for t in (0.6, 1.1, 1.6):
        ctx.frame(t)
        assert len(ctx._contexts) <= sequence.KEEP_SHOTS
    assert list(ctx._contexts) == ["c.json", "a.json"]            # 古い b を捨て、a は作り直した


def run_cli(*argv):
    from mv_pipeline import cli
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def test_cli_demo_render_plan_and_reuse(tmp_path):
    pytest.importorskip("cv2")
    from mv_pipeline.gate import checks
    code, out, _ = run_cli("fx", "timeline", "demo", tmp_path, "--width", "160", "--height", "90", "--duration", "3", "--fps", "8")
    assert code == 0
    path = tmp_path / "timeline.json"
    code, text, _ = run_cli("fx", "timeline", "plan", path)
    assert code == 0 and "black 0.5s" in text and "xfade 0.4s" in text and "flash 0.75" in text
    code, out, err = run_cli("fx", "timeline", "render", path, "--quiet")
    summary = json.loads(out)
    assert code == 0 and summary["technical_pass"] and not summary["reused"] and summary["warnings"] == []
    assert summary["interval"]["frames"] == 24 and summary["size"] == [160, 90] and summary["look"] == "applied"
    manifest = json.loads((tmp_path / "fx_runs" / summary["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "timeline" and set(manifest["variants"]) == {"timeline"} and len(manifest["shots"]) == 3
    record = json.loads((tmp_path / "fx_runs" / summary["run_id"] / "timeline.json").read_text(encoding="utf-8"))
    checks._validate_timeline(record)
    assert checks.check_beats(record, None, checks.THRESHOLDS)["status"] == "pass"
    code, out, _ = run_cli("fx", "timeline", "render", path, "--quiet")
    assert code == 0 and json.loads(out)["reused"]
    shot = tmp_path / "shots" / "shot_b.json"
    shot.write_text(shot.read_text(encoding="utf-8").replace('"zoom": 1.35', '"zoom": 1.36', 1), encoding="utf-8")
    code, out, _ = run_cli("fx", "timeline", "render", path, "--quiet")
    assert code == 0 and not json.loads(out)["reused"]           # ショットの spec を変えたら描き直す


def test_demo_needs_three_seconds(tmp_path):
    with pytest.raises(PipelineError, match="3 秒以上"):
        sequence.demo(tmp_path, duration=2.0)
