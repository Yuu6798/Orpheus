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


def test_the_long_shot_warning_counts_the_next_xfade(tmp_path):
    """次のショットが xfade なら、前のショットはその間も描き続けるので、spec の長さを超える分の注意に含める。"""
    write_shots(tmp_path)                                                       # spec の長さは 2 秒
    shots = [{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 1.8, "transition_in": {"type": "xfade", "duration_s": 0.4}},
             {"spec": "c.json", "start": 2.5}]
    warnings = parse(tmp_path, timeline_dict(shots)).warnings
    assert any("a.json" in w and "spec の長さ 2 秒を超えて 2.2 秒まで" in w and "xfade" in w for w in warnings), warnings
    shots[1]["transition_in"] = {"type": "cut"}
    assert not any("a.json" in w for w in parse(tmp_path, timeline_dict(shots)).warnings)


def test_shot_audio_is_dropped_on_load(tmp_path):
    """ショットの spec の audio は使わない（音は timeline の audio）。読み込みで外し、spec のファイルの記録はそのまま。"""
    write_shots(tmp_path, audio={"path": "gone.wav"})
    spec = parse(tmp_path, timeline_dict([{"spec": "a.json", "start": 0}])).specs[0]
    assert spec.data["audio"] is None and spec.path == (tmp_path / "a.json").resolve() and spec.sha256


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


def test_shot_audio_is_neither_required_nor_in_the_key(scene):
    """ショットの spec の audio のファイルが無くても描け、中身を変えても描き直さない（使わない音で止めない・作り直さない）。"""
    (scene / "song.wav").write_bytes(b"not read")
    for name, audio in (("a.json", "gone.wav"), ("b.json", "song.wav")):
        data = json.loads((scene / name).read_text(encoding="utf-8"))
        data["audio"] = {"path": audio}
        (scene / name).write_text(json.dumps(data), encoding="utf-8")
    path = scene / "timeline.json"
    path.write_text(json.dumps(timeline_dict([{"spec": "a.json", "start": 0}, {"spec": "b.json", "start": 0.5}], duration_s=1.0)),
                    encoding="utf-8")
    first = sequence.render_run(path, quiet=True)
    assert first["technical_pass"] and not first["reused"]
    manifest = json.loads((scene / "fx_runs" / first["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert [sorted(shot["assets"]) for shot in manifest["shots"]] == [["base", "depth", "mask:ground", "mask:subject"]] * 2
    (scene / "song.wav").write_bytes(b"changed")
    assert sequence.render_run(path, quiet=True)["reused"]


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
    assert code == 0 and "black 0.5s" in text and "xfade 0.4s" in text and "flash 0.75" in text and "loop 4〜40 mix 0.5" in text
    text.encode("cp932")        # Windows でパイプやファイルへ出す既定の符号化でも書ける（書けない文字があると plan が止まる）
    code, out, err = run_cli("fx", "timeline", "render", path, "--quiet")
    summary = json.loads(out)
    assert code == 0 and summary["technical_pass"] and not summary["reused"] and summary["warnings"] == []
    assert summary["interval"]["frames"] == 24 and summary["size"] == [160, 90] and summary["look"] == "applied"
    manifest = json.loads((tmp_path / "fx_runs" / summary["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["kind"] == "timeline" and set(manifest["variants"]) == {"timeline"} and len(manifest["shots"]) == 4
    assert manifest["shots"][3]["video"] == "shots/clip.mp4" and len(manifest["shots"][3]["sha256"]) == 64
    record = json.loads((tmp_path / "fx_runs" / summary["run_id"] / "timeline.json").read_text(encoding="utf-8"))
    checks._validate_timeline(record)
    assert [c["kind"] for c in record["clips"]] == ["fx-shot", "fx-shot", "fx-shot", "video"]
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


# ---- 動画のショット ---------------------------------------------------------------------------
# 実制作の式（写し）: 横の MV のレンダラ（render_shot の動画の行）、縦の Shorts のレンダラ（render_img の動画の行と video_frame）、
# 演出の部品（_loop1・loop_pos）。

def private_pos_mv(sh, t, vfps):
    speed = sh.get("speed", 1.0)
    off = sh.get("offset", 0.0)
    return (off + (t - sh["start"]) * speed) * vfps


def private_pos_shorts(sh, t):
    return (sh.get("offset", 0.0) + (t - sh.get("vstart", sh["start"])) * sh.get("speed", 1.0)) * 16.0


def private_loop1(t, loop, rate, xf):
    a, b = loop
    n = b - a
    per = n - xf
    p = (t * 16.0 * rate) % per
    if p < xf:
        w = p / xf
        return [(a + p, w), (a + per + p, 1 - w)]
    return [(a + p, 1.0)]


def private_loop_pos(t, loop, rate=1.0, xf=6, mix=0.5):
    first = private_loop1(t, loop, rate, xf)
    if mix <= 0:
        return first
    second = private_loop1(t * 0.77 + 0.41, loop, rate, xf)
    return [(p, w * (1 - mix)) for p, w in first] + [(p, w * mix) for p, w in second]


def private_video_frame(fr, pos):
    import cv2
    pos = min(max(pos, 0), len(fr) - 1.001)
    i0 = int(pos)
    a = pos - i0
    return fr[i0] if a < 1e-3 else cv2.addWeighted(fr[i0], 1 - a, fr[min(i0 + 1, len(fr) - 1)], a, 0)


def private_loop_frame(fr, sh, t):
    src = None
    for pos, w in private_loop_pos(t - sh["start"] + sh.get("loop_t0", 0.0), sh["loop"], sh.get("rate", 1.0), sh.get("xf", 6)):
        f = private_video_frame(fr, pos).astype(np.float32)
        src = f * w if src is None else src + f * w
    return np.clip(src, 0, 255).astype(np.uint8)


def video(**loop):
    data = {"path": "clip.mp4", "fps": None, "speed": loop.pop("speed", 1.0), "offset_s": loop.pop("offset_s", 0.0), "loop": None}
    if loop:
        data["loop"] = {"frames": loop["frames"], "crossfade_frames": loop.get("crossfade_frames", 6.0), "start_s": loop.get("start_s", 0.0),
                        "mix": loop.get("mix", 0.0), "mix_speed": 0.77, "mix_offset_s": 0.41}
    return data


def test_video_positions_equal_the_private_formulas():
    times = [i / 30 for i in range(0, 400, 7)] + [12.345, 0.0]
    for start, speed, offset in ((132.7, 1.0, 0.0), (66.94, 1.0, 2.5), (69.08, 0.91, 0.0), (154.92, 0.55, 0.0), (166.32, 1.4, 1.0)):
        for t in times:
            mine = sequence.video_positions(video(speed=speed, offset_s=offset), (start + t) - start, 24.0)
            assert mine == [(private_pos_mv({"start": start, "speed": speed, "offset": offset}, start + t, 24.0), 1.0)]
            mine = sequence.video_positions(video(speed=speed, offset_s=offset), (start + t) - start, 16.0)
            assert mine == [(private_pos_shorts({"start": start, "speed": speed, "offset": offset}, start + t), 1.0)]
    for frames, rate, xf, t0, mix in (([6, 24], 0.8, 6, 0.0, 0.5), ([2, 30], 0.8, 6, 1.3, 0.5), ([0, 40], 1.25, 3, 0.0, 0.0)):
        for t in times:
            mine = sequence.video_positions(video(frames=frames, crossfade_frames=xf, speed=rate, start_s=t0, mix=mix), (208.31 + t) - 208.31, 16.0)
            assert mine == private_loop_pos((208.31 + t) - 208.31 + t0, frames, rate, xf, mix)


class FakeFrames(sequence.VideoFrames):
    """復号の代わりに合成のコマを返す（at は本物）。"""

    def __init__(self, frames):
        self.count, self._list = len(frames), frames

    def get(self, index):
        return self._list[index]


def test_frame_blend_and_loop_sum_equal_the_private_code():
    pytest.importorskip("cv2")
    rng = np.random.default_rng(4)
    frames = [rng.integers(0, 256, (6, 8, 3), dtype=np.uint8) for _ in range(30)]
    fake = FakeFrames(frames)
    for pos in [-3.0, 0.0, 0.0004, 0.5, 3.25, 7.999, 12.0011, 28.4, 28.999, 29.5, 31.0]:
        assert np.array_equal(fake.at(pos), private_video_frame(frames, pos)), pos
    shot = {"start": 208.31, "loop": [6, 24], "rate": 0.8, "xf": 6}
    for t in [i / 30 for i in range(0, 200, 3)]:
        mine = None
        for pos, w in sequence.video_positions(video(frames=[6, 24], speed=0.8, mix=0.5), (208.31 + t) - 208.31, 16.0):
            part = fake.at(pos).astype(np.float32)
            mine = part * w if mine is None else mine + part * w
        assert np.array_equal(np.clip(mine, 0, 255).astype(np.uint8), private_loop_frame(frames, shot, 208.31 + t))


VIDEO_SHOT = {"video": {"path": "clip.mp4"}, "start": 0}


@pytest.mark.parametrize("change, message", [
    (lambda s: s.update(spec="a.json"), "どちらか一方"),
    (lambda s: s.pop("video"), "どちらか一方"),
    (lambda s: s.update(offset_s=1.0), "offset_s は動画のショットには使えません"),
    (lambda s: s.update(variant="base"), "variant は動画のショットには使えません"),
    (lambda s: s["video"].update(offset_s=1.0, loop={"frames": [2, 20]}), "loop と offset_s"),
    (lambda s: s["video"].update(loop={"frames": [2, 8]}), "crossfade_frames 6 コマより長くありません"),
    (lambda s: s["video"].update(loop={"frames": [9, 3], "crossfade_frames": 0}), "より長くありません"),
    (lambda s: s["video"].update(loop={"frames": [0, 10]}), "crossfade_frames 6 コマが、区間 0〜10（10 コマ）の半分より長いです"),
    (lambda s: s["video"].update(path="missing.mp4"), "動画が見つかりません"),
    (lambda s: s.update(camera={"keys": [{"t": 1.0}, {"t": 0.5}]}), "厳密な昇順"),
    (lambda s: s["video"].update(speed=0), "invalid"),
])
def test_video_shot_errors_name_the_cause(tmp_path, change, message):
    (tmp_path / "clip.mp4").write_bytes(b"")
    shot = json.loads(json.dumps(VIDEO_SHOT))
    change(shot)
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, timeline_dict([shot]))


def test_a_loop_crossfade_must_end_before_the_loop_wraps(tmp_path):
    """重ねは区間の半分まで。周期（区間 − 重ね）より長い重ねは終わる前に折り返し、境目で絵が飛ぶので止める。ちょうど半分なら途切れない。"""
    def ramp(frames, local_t):              # コマ k の値が k の動画を描いたときの値（重みつきのコマ位置）
        return sum(p * w for p, w in sequence.video_positions(video(frames=frames), local_t, 10.0))

    def jump(frames):                       # 折り返し（周期 = 区間 − 6 コマ、10 fps）の直前と直後の差
        wrap = (frames[1] - frames[0] - 6) / 10.0
        return ramp(frames, wrap - 1e-9) - ramp(frames, wrap + 1e-9)

    assert jump([0, 10]) == pytest.approx(4 / 3, abs=1e-6)       # 4 と 8 を 2/3 : 1/3 で混ぜた絵から、4 だけの絵へ飛ぶ
    assert jump([0, 12]) == pytest.approx(0.0, abs=1e-6)
    (tmp_path / "clip.mp4").write_bytes(b"")
    with pytest.raises(PipelineError, match="半分より長い"):
        parse(tmp_path, timeline_dict([{"video": {"path": "clip.mp4", "loop": {"frames": [0, 10]}}, "start": 0}]))
    assert parse(tmp_path, timeline_dict([{"video": {"path": "clip.mp4", "loop": {"frames": [0, 12]}}, "start": 0}])).data["shots"][0]["video"]["loop"]


def test_spec_shots_refuse_a_camera(tmp_path):
    write_shots(tmp_path)
    with pytest.raises(PipelineError, match="camera は spec のショットには使えません"):
        parse(tmp_path, timeline_dict([{"spec": "a.json", "start": 0, "camera": {"keys": [{"t": 0}]}}]))


def test_video_shot_is_normalized_and_recorded(tmp_path):
    (tmp_path / "clip.mp4").write_bytes(b"")
    write_shots(tmp_path)
    raw = timeline_dict([{"spec": "a.json", "start": 0},
                         {"video": {"path": "clip.mp4", "speed": 0.9, "loop": {"frames": [2, 20], "mix": 0.5}}, "start": 1.5,
                          "camera": {"keys": [{"t": 0, "zoom": 1.2}]}, "transition_in": {"type": "xfade", "duration_s": 0.5}}])
    timeline = parse(tmp_path, raw)
    shot = timeline.data["shots"][1]
    assert shot["kind"] == "video" and shot["offset_s"] == 0.0 and timeline.specs[1] is None
    assert shot["video"]["loop"] == {"frames": [2, 20], "crossfade_frames": 6.0, "start_s": 0.0, "mix": 0.5, "mix_speed": 0.77, "mix_offset_s": 0.41}
    assert shot["camera"]["keys"] == [{"t": 0.0, "ease": "linear", "zoom": 1.2, "center": [0.5, 0.5]}]
    record = sequence.timeline_record(timeline, (160, 90))
    assert record["clips"][1]["path"] == "clip.mp4" and record["clips"][1]["kind"] == "video"
    layers, _, _ = sequence.plan_frame(timeline.data, 1.75)
    assert [(i, round(local, 9)) for i, local, _ in layers] == [(0, 1.75), (1, 0.25)]


def write_clip(path, frames, fps=10):
    from mv_pipeline.fx import render
    render.write_video(path, iter(frames), (frames[0].shape[1], frames[0].shape[0]), fps, 18, len(frames))


def test_a_still_video_shot_is_the_fx_still_of_its_frame(tmp_path):
    """同じコマが続く動画のショットは、そのコマ（復号した値）を絵にした fx の静止画のショットとバイト単位で同じ（同じ道筋で描く）。"""
    pytest.importorskip("cv2")
    from PIL import Image

    from mv_pipeline.dance.clips import Source
    from mv_pipeline.fx import render
    from mv_pipeline.fx import spec as fxspec
    fx_synth.make_scene(tmp_path)
    base = np.asarray(Image.open(tmp_path / "base.png").convert("RGB"))
    write_clip(tmp_path / "clip.mp4", [base] * 6)
    source = Source(tmp_path / "clip.mp4")
    Image.fromarray(source.read(2)).save(tmp_path / "decoded.png")
    source.close()
    keys = [{"t": 0, "zoom": 1.1, "center": [0.45, 0.5]}, {"t": 2.0, "zoom": 1.3, "center": [0.55, 0.45], "ease": "in_out"}]
    still = fx_synth.spec_dict(assets={"base": "decoded.png"}, camera={"keys": keys}, layers=None, lights=None)
    (tmp_path / "still.json").write_text(json.dumps(still), encoding="utf-8")
    raw = timeline_dict([{"video": {"path": "clip.mp4"}, "start": 0, "camera": {"keys": keys}}], duration_s=2.0)
    ctx = sequence.Context(parse(tmp_path, raw))
    fx_ctx = render.prepare(fxspec.load_spec(tmp_path / "still.json"), size=(160, 90))
    try:
        for t in (0.0, 0.7, 1.95):
            assert np.array_equal(ctx.frame(t), render.frame(fx_ctx, t, "base")), t
    finally:
        ctx.close()


def test_video_shot_reads_speed_offset_and_loop_positions(tmp_path):
    """平らな色のコマ（コマ k の値は 20k + 10）で、speed・offset・loop が選ぶコマを確かめる（圧縮の誤差は ±2 まで）。"""
    pytest.importorskip("cv2")
    frames = [np.full((36, 64, 3), 20 * k + 10, np.uint8) for k in range(12)]
    write_clip(tmp_path / "clip.mp4", frames, fps=10)
    raw = timeline_dict([{"video": {"path": "clip.mp4", "speed": 2.0, "offset_s": 0.3}, "start": 0},
                         {"video": {"path": "clip.mp4", "loop": {"frames": [2, 8], "crossfade_frames": 0}}, "start": 1.0}],
                        duration_s=3.0, output={"width": 64, "height": 36, "fps": 10})
    timeline = parse(tmp_path, raw)
    ctx = sequence.Context(timeline)
    try:
        def value(t):
            index = sequence.shot_index(timeline.data, t)
            return float(ctx.shot(index).source_frame(t - timeline.data["shots"][index]["start"]).mean())
        assert value(0.0) == pytest.approx(70, abs=2)          # (0.3 + 0)·10 = コマ 3
        assert value(0.2) == pytest.approx(150, abs=2)         # (0.3 + 0.2·2)·10 = コマ 7
        assert value(0.275) == pytest.approx(180, abs=2)       # 8.5 コマ → 8 と 9 の中間
        assert value(0.9) == pytest.approx(230, abs=2)         # コマ 21 → 最後のコマ 11 で止まる
        assert value(1.0) == pytest.approx(50, abs=2)          # loop の頭（コマ 2）
        assert value(1.45) == pytest.approx(140, abs=2)        # 4.5 コマ → 6 と 7 の中間
        assert value(1.6) == pytest.approx(50, abs=2)          # 6 コマで 1 周（区間 8 − 2）
        assert any("最後のコマ 11 を越えて描く" in w for w in ctx.shot(0).warnings)
    finally:
        ctx.close()


def test_a_loop_beyond_the_video_stops_at_render(tmp_path):
    pytest.importorskip("cv2")
    frames = [np.full((36, 64, 3), 100, np.uint8) for _ in range(8)]
    write_clip(tmp_path / "clip.mp4", frames, fps=10)
    raw = timeline_dict([{"video": {"path": "clip.mp4", "loop": {"frames": [2, 9], "crossfade_frames": 2}}, "start": 0}],
                        duration_s=1.0, output={"width": 64, "height": 36, "fps": 10})
    ctx = sequence.Context(parse(tmp_path, raw))
    with pytest.raises(PipelineError, match="最後のコマ 7 を越えています"):
        ctx.frame(0.0)
    ctx.close()


def test_the_video_end_warning_counts_the_next_xfade(tmp_path):
    """次のショットが xfade なら、動画のショットはその間も描くので、動画の最後のコマを越える注意にその分も数える。"""
    pytest.importorskip("cv2")
    write_clip(tmp_path / "clip.mp4", [np.full((36, 64, 3), 100, np.uint8) for _ in range(12)], fps=10)   # 最後のコマ 11（1.1 秒）
    write_shots(tmp_path, names=("a",))
    for transition, warned in (({"type": "xfade", "duration_s": 0.3}, True), ({"type": "cut"}, False)):
        raw = timeline_dict([{"video": {"path": "clip.mp4"}, "start": 0}, {"spec": "a.json", "start": 1.0, "transition_in": transition}],
                            duration_s=2.0, output={"width": 64, "height": 36, "fps": 10})
        ctx = sequence.Context(parse(tmp_path, raw))
        try:
            notes = ctx.shot(0).warnings
        finally:
            ctx.close()
        assert any("最後のコマ 11 を越えて描く" in w and "xfade の間も描く分を含む" in w for w in notes) == warned, notes
        assert any("最後のコマ" in w for w in notes) == warned


def test_the_video_camera_check_covers_the_next_xfade(tmp_path):
    """次のショットが xfade なら、動画のショットのカメラはその間も動くので、視野が動画の外へ出る確かめもその間まで見る。"""
    pytest.importorskip("cv2")
    write_clip(tmp_path / "clip.mp4", [np.full((36, 64, 3), 100, np.uint8) for _ in range(30)], fps=10)   # 3 秒（最後のコマは越えない）
    write_shots(tmp_path, names=("a",))
    keys = [{"t": 0, "center": [0.5, 0.5]}, {"t": 1.0, "center": [0.5, 0.5]}, {"t": 1.4, "center": [0.9, 0.5]}]   # 1 秒の後だけ右の外へ
    for transition, warned in (({"type": "xfade", "duration_s": 0.4}, True), ({"type": "cut"}, False)):
        raw = timeline_dict([{"video": {"path": "clip.mp4"}, "start": 0, "camera": {"keys": keys}},
                             {"spec": "a.json", "start": 1.0, "transition_in": transition}],
                            duration_s=2.0, output={"width": 64, "height": 36, "fps": 10})
        ctx = sequence.Context(parse(tmp_path, raw))
        try:
            notes = ctx.shot(0).warnings
        finally:
            ctx.close()
        assert any("camera の視野が画像の外へ" in w for w in notes) == warned, notes
