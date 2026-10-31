"""dance-shot/0.2 の基盤: 拍の参照・人物の配置・fit・blend・音声・timeline・plan・進み具合。"""
import json
import subprocess
import wave

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance, media  # noqa: E402
from mv_pipeline.dance import beats, clips, compose, demo, fit, plan, runs  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import probe  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

KEY = {"color": [0.0, 255.0, 0.0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 0.0, "feather_px": 0.0}
GREEN = (0, 255, 0)
SIZE = (180, 320)


def base(**changes):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 12}, "duration_s": 2.0,
           "stage": {"path": "stage.png"}, "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 2}]}}
    raw.update(changes)
    return raw


def parse(tmp_path, **changes):
    return dancespec.parse_spec(base(**changes), tmp_path)


def box_frame(width, height, top, bottom, left, right, color=(200, 60, 120)):
    image = np.full((height, width, 3), GREEN, np.uint8)
    image[top:bottom, left:right] = color
    return image


def write_video(path, frames, fps=12):
    height, width = frames[0].shape[:2]
    fxrender.write_video(path, iter(frames), (width, height), fps, 0, len(frames))


def write_stage(path, size=SIZE, value=30):
    cv2.imwrite(str(path), np.full((size[1], size[0], 3), value, np.uint8))


# ---- 拍の参照 --------------------------------------------------------------------

GRID = beats.Grid([0.0, 0.5, 1.0, 1.6], [0.0, 1.0])


@pytest.mark.parametrize("ref, seconds", [
    (1.25, 1.25), ({"beat": 1}, 0.5), ({"beat": 0.5}, 0.25), ({"beat": 2.5}, 1.3),
    ({"beat": 4}, 2.2), ({"beat": -1}, None), ({"bar": 1}, 1.0), ({"bar": 1, "beat": 1.5}, 1.9),
    ({"bar": 0, "beat": 1}, 0.5), ({"bar": 2}, 2.0), ({"beat": 2, "offset_s": -0.1}, 0.9),
    ({"bar": 1, "offset_s": 0.25}, 1.25),
])
def test_time_references_resolve_to_seconds(ref, seconds):
    if seconds is None:
        with pytest.raises(PipelineError, match="負"):
            beats.resolve_time(ref, GRID, "t")
    else:
        assert beats.resolve_time(ref, GRID, "t") == pytest.approx(seconds)


def test_a_reference_without_beats_is_an_error(tmp_path):
    with pytest.raises(PipelineError, match="beats が必要"):
        parse(tmp_path, performer={"clips": [{"path": "a.mp4", "start": 0, "end": {"beat": 4}}]})


def test_a_bar_reference_needs_downbeats():
    with pytest.raises(PipelineError, match="downbeats"):
        beats.resolve_time({"bar": 1}, beats.Grid([0.0, 1.0], []), "t")


def test_grid_from_bpm_and_downbeat_phase(tmp_path):
    data = parse(tmp_path, beats={"bpm": 120, "offset_s": 0.1, "beats_per_bar": 4, "count": 9, "downbeat_phase": 1}).data
    assert data["beats"]["beats"][:3] == pytest.approx([0.1, 0.6, 1.1])
    assert data["beats"]["downbeats"] == pytest.approx([0.6, 2.6])


def test_grid_from_a_file_is_recorded_as_an_asset(tmp_path):
    (tmp_path / "beats.json").write_text(json.dumps({"beats_s": [0, 1, 2], "downbeats": [0, 2]}), encoding="utf-8")
    data = parse(tmp_path, beats={"path": "beats.json"}, performer={"clips": [{"path": "a.mp4", "start": 0, "end": {"bar": 1}}]}).data
    assert data["performer"]["clips"][0]["end"] == 2.0 and dancespec.asset_refs(data)["beats"] == "beats.json"


def test_spec_data_has_only_seconds(tmp_path):
    data = parse(tmp_path, beats={"bpm": 120}, flashes=[{"t": {"beat": 2}}], camera={"keys": [{"t": {"beat": 1, "offset_s": 0.1}}]},
                 lyrics={"lines": [{"layer": "band", "text": "ab", "start": {"beat": 1}, "end": {"beat": 2},
                                    "words": [{"text": "a", "t": {"beat": 1.5}}, {"text": "b"}]}]}).data
    assert data["flashes"][0]["t"] == 1.0 and data["camera"]["keys"][0]["t"] == pytest.approx(0.6)
    line = data["lyrics"]["lines"][0]
    assert (line["start"], line["end"], line["words"][0]["t"]) == (0.5, 1.0, 0.75)


# ---- 0.1 との互換 ----------------------------------------------------------------

def test_a_01_spec_is_validated_by_the_01_schema(tmp_path):
    raw = base(schema_version=dance.SCHEMA_V01)
    assert dancespec.parse_spec(raw, tmp_path).data["source_schema"] == dance.SCHEMA_V01
    raw["performer"]["clips"][0]["fit"] = False
    with pytest.raises(PipelineError, match="invalid"):
        dancespec.parse_spec(raw, tmp_path)


def test_a_01_spec_keeps_the_timeline_01(tmp_path):
    demo.create(tmp_path, 180, 320, 2.0, 12, "0.1")
    ctx = compose.prepare(dancespec.load_spec(tmp_path / "spec.json"))
    record = compose.timeline_record(ctx)
    ctx.close()
    assert record["schema_version"] == "dance-timeline/0.1" and "frames" not in record


# ---- 人物 ------------------------------------------------------------------------

@pytest.fixture
def scene(tmp_path):
    """左半分に箱のある 6 コマの動画と、同じ箱の静止画。"""
    write_stage(tmp_path / "stage.png")
    write_video(tmp_path / "a.mp4", [box_frame(180, 320, 40 + 4 * i, 120 + 4 * i, 20, 60) for i in range(6)])
    cv2.imwrite(str(tmp_path / "still.png"), box_frame(180, 320, 100, 200, 20, 60)[..., ::-1])
    return tmp_path


def alpha_at(sp, t, scale=1.0):
    ctx = compose.prepare(sp, scale)
    try:
        return compose._performer(ctx, t, compose.camera_matrix(1.0, [0.5, 0.5], 0.0, ctx.size))[1]
    finally:
        ctx.close()


def extent(alpha):
    ys, xs = np.nonzero(alpha > 0.5)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


PLACE = {"anchor": [0.5, 1.0], "pivot": [0.5, 1.0], "height": 1.0}
SHARP = {"choke_px": 0, "feather_px": 0}


def test_negative_source_start_holds_the_first_frame(scene):
    sp = dancespec.parse_spec(base(performer={"clips": [{"path": "a.mp4", "start": 0, "end": 2, "source_start": -0.5}], "placement": PLACE, "key": SHARP}), scene)
    line = clips.Timeline(sp)
    clip = sp.data["performer"]["clips"][0]
    assert line.source_frame(clip, 0.0) == 0 and line.source_frame(clip, 0.4) == 0 and line.source_frame(clip, 0.75) == 3
    line.close()
    assert extent(alpha_at(sp, 0.0)) == extent(alpha_at(sp, 0.25))


def test_a_still_image_is_a_clip_and_ignores_speed(scene):
    sp = dancespec.parse_spec(base(performer={"clips": [{"path": "still.png", "start": 0, "end": 2, "speed": 3.0}], "placement": PLACE, "key": SHARP}), scene)
    assert sp.data["performer"]["clips"][0]["kind"] == "image"
    assert extent(alpha_at(sp, 0.1)) == extent(alpha_at(sp, 1.9)) == (20, 100, 60, 200)


def test_flip_x_mirrors_around_the_pivot(scene):
    plain = {"clips": [{"path": "still.png", "start": 0, "end": 2}], "placement": PLACE, "key": SHARP}
    flipped = {"clips": [{"path": "still.png", "start": 0, "end": 2, "placement": {"flip_x": True}}], "placement": PLACE, "key": SHARP}
    both = {"clips": flipped["clips"], "placement": {**PLACE, "flip_x": True}, "key": SHARP}
    a = extent(alpha_at(dancespec.parse_spec(base(performer=plain), scene), 0.5))
    b = extent(alpha_at(dancespec.parse_spec(base(performer=flipped), scene), 0.5))
    c = extent(alpha_at(dancespec.parse_spec(base(performer=both), scene), 0.5))
    assert b == (180 - a[2], a[1], 180 - a[0], a[3]) and c == a


def test_flip_x_keeps_an_off_center_pivot_on_the_anchor(scene):
    place = {"anchor": [0.5, 1.0], "pivot": [40 / 180, 1.0], "height": 1.0}
    flipped = {"clips": [{"path": "still.png", "start": 0, "end": 2, "placement": {"flip_x": True}}], "placement": place, "key": SHARP}
    x0, _, x1, _ = extent(alpha_at(dancespec.parse_spec(base(performer=flipped), scene), 0.5))
    assert (x0 + x1) / 2 == pytest.approx(90, abs=1)      # 箱の中央が pivot なので、反転しても anchor に来る


def test_clip_offset_and_scale_apply_after_the_placement(scene):
    shifted = {"clips": [{"path": "still.png", "start": 0, "end": 2, "placement": {"offset": [0.1, -0.1]}}], "placement": PLACE, "key": SHARP}
    assert extent(alpha_at(dancespec.parse_spec(base(performer=shifted), scene), 0.5)) == (38, 68, 78, 168)
    grown = {"clips": [{"path": "still.png", "start": 0, "end": 2, "placement": {"scale": 0.5}}], "placement": PLACE, "key": SHARP}
    x0, y0, x1, y1 = extent(alpha_at(dancespec.parse_spec(base(performer=grown), scene), 0.5))
    assert x1 - x0 == pytest.approx(20, abs=2) and y1 - y0 == pytest.approx(50, abs=2)


# ---- fit -------------------------------------------------------------------------

BODY = {"mode": "body", "body_height": 0.5, "x": "median", "ground": "lock", "ground_window_s": 0.5, "ground_smooth_s": 0.15}
ANCHOR = [0.5, 0.9]


def drifting(tmp_path, name, tall, x, foot0, slope, n=48, jump=None):
    """高さ tall・中心 x の箱。足元が 1 コマ slope 画素流れ、jump=(開始, コマ数, 高さ) で跳ぶ。"""
    frames = []
    for i in range(n):
        foot = foot0 + slope * i
        if jump and jump[0] <= i < jump[0] + jump[1]:
            foot -= jump[2]
        foot = int(round(foot))
        frames.append(box_frame(180, 320, foot - tall, foot, x - 10, x + 10))
    write_video(tmp_path / name, frames)


def fit_spec(tmp_path, performer=None, **changes):
    write_stage(tmp_path / "stage.png")
    return dancespec.parse_spec(base(duration_s=8.0, **changes, performer={
        "clips": [{"path": "a.mp4", "start": 0, "end": 4}, {"path": "b.mp4", "start": 4, "end": 8}],
        "placement": {"anchor": ANCHOR}, "key": SHARP, "fit": BODY, **(performer or {})}), tmp_path)


def test_body_fit_makes_scaled_clips_the_same_height_and_centers_them(tmp_path):
    drifting(tmp_path, "a.mp4", 60, 60, 200, 0)
    drifting(tmp_path, "b.mp4", 120, 120, 280, 0)
    sp = fit_spec(tmp_path)
    a, b = extent(alpha_at(sp, 1.0)), extent(alpha_at(sp, 5.0))
    assert a[3] - a[1] == pytest.approx(b[3] - b[1], rel=0.01) and a[3] - a[1] == pytest.approx(160, abs=2)
    assert (a[0] + a[2]) / 2 == pytest.approx(90, abs=1) and (b[0] + b[2]) / 2 == pytest.approx(90, abs=1)


def test_ground_lock_holds_a_drifting_foot_on_the_anchor(tmp_path):
    drifting(tmp_path, "a.mp4", 100, 60, 200, 1.5)
    drifting(tmp_path, "b.mp4", 100, 60, 260, -1.0)
    sp = fit_spec(tmp_path, performer={"fit": {**BODY, "analysis_scale": 1.0}})
    ctx = compose.prepare(sp)
    try:
        matrix = compose.camera_matrix(1.0, [0.5, 0.5], 0.0, ctx.size)
        feet = [extent(compose._performer(ctx, t, matrix)[1])[3] for t in np.arange(0.0, 7.9, 0.5)]
    finally:
        ctx.close()
    assert all(abs(foot - 0.9 * 320) <= 1 for foot in feet), feet


def test_a_short_jump_survives_and_a_long_jump_is_flattened():
    foot = np.full(120, 200.0)
    short = foot.copy()
    short[50:56] -= 40
    held = fit.lock_ground(short, 0.7, 0.0, 24)
    assert (held == 200).all()
    long = foot.copy()
    long[40:80] -= 40
    assert fit.lock_ground(long, 0.7, 0.0, 24)[60] == 160


def test_ramp_is_kept_exactly_up_to_the_ends():
    ramp = 100.0 + 2.0 * np.arange(60)
    assert fit.lock_ground(ramp, 0.7, 0.3, 24) == pytest.approx(ramp)


def test_analysis_cache_depends_only_on_the_content(tmp_path):
    drifting(tmp_path, "a.mp4", 60, 60, 200, 0)
    drifting(tmp_path, "b.mp4", 60, 60, 200, 0)
    sp = fit_spec(tmp_path)
    ctx = compose.prepare(sp, out=tmp_path / "runs")
    ctx.close()
    assert len(list((tmp_path / "runs" / "analysis").glob("*.json"))) == 1
    assert len(ctx.analysis) == 2 and ctx.analysis[0]["file"] == ctx.analysis[1]["file"]
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "renamed.mp4").write_bytes((tmp_path / "a.mp4").read_bytes())
    write_stage(tmp_path / "other" / "stage.png")
    moved = dancespec.parse_spec(base(performer={"clips": [{"path": "renamed.mp4", "start": 0, "end": 2}], "key": SHARP, "fit": BODY}), tmp_path / "other")
    ctx = compose.prepare(moved, out=tmp_path / "runs2")
    ctx.close()
    assert [p.name for p in (tmp_path / "runs" / "analysis").glob("*.json")] == [p.name for p in (tmp_path / "runs2" / "analysis").glob("*.json")]


def test_analysis_key_changes_with_the_key_and_the_fit_settings():
    assert fit.settings_digest(KEY, BODY) != fit.settings_digest({**KEY, "tolerance": 0.3}, BODY)
    assert fit.settings_digest(KEY, BODY) != fit.settings_digest(KEY, {**BODY, "ground_window_s": 0.2})


def test_a_clip_can_opt_out_of_fit(tmp_path):
    drifting(tmp_path, "a.mp4", 60, 60, 200, 0)
    drifting(tmp_path, "b.mp4", 120, 120, 280, 0)
    sp = fit_spec(tmp_path, performer={"clips": [{"path": "a.mp4", "start": 0, "end": 4}, {"path": "b.mp4", "start": 4, "end": 8, "fit": False}]})
    ctx = compose.prepare(sp)
    try:
        assert ctx.place[0]["ys"] is not None and ctx.place[1]["ys"] is None
        assert [item["path"] for item in ctx.analysis] == ["a.mp4"]
    finally:
        ctx.close()


def test_a_clip_with_nobody_in_it_stops_with_a_reason(tmp_path):
    write_video(tmp_path / "a.mp4", [np.full((320, 180, 3), GREEN, np.uint8)] * 3)
    write_video(tmp_path / "b.mp4", [np.full((320, 180, 3), GREEN, np.uint8)] * 3)
    with pytest.raises(PipelineError, match="人物が"):
        compose.prepare(fit_spec(tmp_path))


# ---- blend -----------------------------------------------------------------------

def test_blend_weight_is_a_smoothstep_inside_the_window(scene):
    sp = dancespec.parse_spec(base(performer={"clips": [
        {"path": "a.mp4", "start": 0, "end": 1},
        {"path": "still.png", "start": 1, "end": 2, "transition_in": {"type": "blend", "frames": 3}}], "placement": PLACE, "key": SHARP}), scene)
    line = clips.Timeline(sp)
    clip = sp.data["performer"]["clips"][1]
    weights = [line.blend_weight(clip, 1 + i / 12) for i in range(-1, 4)]
    assert weights == [None, pytest.approx(0.15625), pytest.approx(0.5), pytest.approx(0.84375), None]
    assert [m[0] for m in line.mapping(1 - 1 / 12)] == [0] and [m[0] for m in line.mapping(1 + 3 / 12)] == [1]
    assert [m[0] for m in line.mapping(1.0)] == [0, 1]
    line.close()


def test_the_blended_frame_mixes_both_clips_in_their_own_places(scene):
    sp = dancespec.parse_spec(base(performer={"clips": [
        {"path": "still.png", "start": 0, "end": 1},
        {"path": "still.png", "start": 1, "end": 2, "placement": {"offset": [0.4, 0.0]}, "transition_in": {"type": "blend", "frames": 1}}],
        "placement": PLACE, "key": SHARP}), scene)
    alpha = alpha_at(sp, 1.0)
    assert alpha[150, 40] == pytest.approx(0.5, abs=0.01) and alpha[150, 112] == pytest.approx(0.5, abs=0.01)


@pytest.mark.parametrize("clips_, message", [
    ([{"path": "a.mp4", "start": 0, "end": 1, "transition_in": {"type": "blend", "frames": 2}}], "最初の"),
    ([{"path": "a.mp4", "start": 0, "end": 1}, {"path": "b.mp4", "start": 1.5, "end": 2, "transition_in": {"type": "blend", "frames": 2}}], "隙間なく"),
])
def test_blend_needs_a_touching_previous_clip(tmp_path, clips_, message):
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, performer={"clips": clips_})


# ---- 音声 ------------------------------------------------------------------------

def write_tone(path, seconds, rate=48000):
    samples = (0.4 * np.sin(2 * np.pi * 440 * np.arange(int(seconds * rate)) / rate) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())


def tone_scene(tmp_path, seconds, **audio):
    write_stage(tmp_path / "stage.png")
    write_video(tmp_path / "a.mp4", [box_frame(180, 320, 40, 200, 40, 80)] * 4)
    write_tone(tmp_path / "song.wav", seconds)
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(base(audio={"path": "song.wav", **audio})), encoding="utf-8")
    return path


def peak_after(video, start):
    pcm = subprocess.run([media.ffmpeg(), "-v", "error", "-nostdin", "-ss", str(start), "-i", str(video), "-map", "0:a:0", "-f", "s16le", "-ac", "1", "-"],
                         capture_output=True).stdout
    return int(np.abs(np.frombuffer(pcm, np.int16)).max()) if pcm else 0


def test_strict_stops_on_short_audio(tmp_path):
    code, _, err = run_cli("dance", "render", tone_scene(tmp_path, 1.0), "--quiet")
    assert code == 1 and "技術検査" in err


def test_pad_fills_short_audio_with_silence_and_passes(tmp_path):
    code, result, err = run_cli("dance", "render", tone_scene(tmp_path, 1.0, fit="pad", fade_out_s=0.05), "--quiet")
    assert code == 0, err
    video = tmp_path / "dance_runs" / result["run_id"] / "dance.mp4"
    assert result["technical_pass"] is True and peak_after(video, 1.1) < 50 and peak_after(video, 0.5) > 3000


def test_end_s_silences_everything_after_it(tmp_path):
    code, result, err = run_cli("dance", "render", tone_scene(tmp_path, 3.0, end_s=1.0, fade_in_s=0.0, fade_out_s=0.06), "--quiet")
    assert code == 0, err
    video = tmp_path / "dance_runs" / result["run_id"] / "dance.mp4"
    assert result["technical_pass"] is True and peak_after(video, 1.1) < 50 and peak_after(video, 0.4) > 3000


def test_a_render_that_starts_after_the_song_is_silent(tmp_path):
    code, result, err = run_cli("dance", "render", tone_scene(tmp_path, 3.0, end_s=0.5), "--start", "1", "--quiet")
    assert code == 0, err
    assert result["technical_pass"] is True and peak_after(tmp_path / "dance_runs" / result["run_id"] / "dance.mp4", 0) == 0


def test_default_audio_plan_keeps_the_fx_shape(tmp_path):
    spec = tone_scene(tmp_path, 3.0)
    ctx = compose.prepare(dancespec.load_spec(spec))
    plan_ = runs._audio(ctx, 0.5, 12)
    ctx.close()
    assert sorted(plan_) == ["duration", "path", "start"]
    assert "afade=t=in:st=0:d=0.0200,afade=t=out:st=0.7500:d=0.2500" in fxrender.ffmpeg_command("o.mp4", SIZE, 12, 18, 12, plan_)


# ---- デモ・timeline・CLI ----------------------------------------------------------

@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    root = tmp_path_factory.mktemp("dance02")
    code, _, err = run_cli("dance", "demo", root, "--width", "180", "--height", "320", "--duration", "3", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--quiet")
    assert code == 0, err
    return root, result


def test_the_demo_renders_and_passes(shot):
    root, result = shot
    assert result["technical_pass"] is True
    checks = probe.check_video(root / "runs" / result["run_id"] / "dance.mp4", width=180, height=320, fps=12, frames=36, audio=True, audio_s=3.0, bt709=True)
    assert checks["pass"], checks["failures"]


def test_the_demo_uses_the_new_schema(tmp_path):
    data = dancespec.parse_spec(demo._spec(180, 320, 6.0, 24), tmp_path).data
    clips_ = data["performer"]["clips"]
    assert len(clips_) == 4 and clips_[-1]["kind"] == "image" and clips_[-1]["transition_in"] == {"type": "blend", "frames": 3}
    assert data["performer"]["fit"]["ground"] == "lock" and data["audio"]["fit"] == "pad"


def test_timeline_02_records_clips_seams_analysis_and_frames(shot):
    root, result = shot
    run_dir = root / "runs" / result["run_id"]
    record = read_json(run_dir / "timeline.json")
    assert record["schema_version"] == "dance-timeline/0.2" and record["finished"] is None
    assert record["audio"] == {"fit": "pad", "end_s": 2.75}
    assert [c["kind"] for c in record["clips"]] == ["video", "video", "video", "image"]
    assert record["seam_info"][-1] == {"t": 2.5, "from": 2, "to": 3, "kind": "blend", "beat_exempt": False}
    assert record["seams"] == [item["t"] for item in record["seam_info"]]
    assert len(record["analysis"]) == 4 and all((root / "runs" / item["file"]).is_file() for item in record["analysis"])
    fields = record["frames_fields"]
    assert len(record["frames"]) == 36 and all(len(row) == len(fields) for row in record["frames"])
    frame = dict(zip(fields, record["frames"][30]))
    assert frame["t"] == 2.5 and frame["clip"] == 3 and 0 < frame["blend"] < 1 and frame["p_a"] > 0
    assert dict(zip(fields, record["frames"][33]))["blend"] is None
    manifest = read_json(run_dir / "manifest.json")
    assert {item["sha256"] for item in manifest["analysis"]} == {item["sha256"] for item in record["analysis"]}
    assert "clip:3" in manifest["assets"]


def test_rendering_is_independent_of_the_order_with_fit_and_blend(shot):
    root, _ = shot
    sp = dancespec.load_spec(root / "spec.json")
    first, second = compose.prepare(sp, out=root / "runs"), compose.prepare(sp, out=root / "runs")
    try:
        times = (0.25, 1.75, 2.5, 2.6)
        forward = [compose.frame(first, t) for t in times]
        backward = [compose.frame(second, t) for t in reversed(times)][::-1]
        assert all((a == b).all() for a, b in zip(forward, backward))
    finally:
        first.close()
        second.close()


def test_plan_prints_the_resolved_times_as_tables(shot, capsys):
    root, _ = shot
    code, _, err = run_cli_text("dance", "plan", root / "spec.json")
    assert code == 0, err
    text = plan.text(root / "spec.json")
    assert "## clips" in text and "## つなぎ" in text and "## lyrics" in text and "1.000" in text and "blend 3f" in text


def run_cli_text(*args):
    from mv_pipeline.cli import main
    import contextlib
    import io
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def test_render_reports_progress_every_five_percent_unless_quiet():
    lines = []
    meter = runs._meter(lines.append, 100)
    for done in range(1, 101):
        meter(done)
    assert len(lines) == 20 and "5/100" in lines[0] and "100/100" in lines[-1] and "残り約" in lines[0]
    runs._meter(None, 100)(5)


def test_guides_draw_only_the_visible_boxes_unless_all(shot):
    root, _ = shot
    code, visible, err = run_cli("dance", "stills", root / "spec.json", "--times", "1.0", "--guides", "--out", root / "sheets")
    assert code == 0, err
    code, every, err = run_cli("dance", "stills", root / "spec.json", "--times", "1.0", "--guides", "all", "--out", root / "sheets")
    assert code == 0, err
    assert visible["path"] != every["path"]
    assert (cv2.imread(visible["path"]) != cv2.imread(every["path"])).any()


def test_placement_matrix_maps_the_source_to_the_screen():
    place = {"size": (90, 160), "source": (180, 320), "x": 10.0, "y": 20.0, "xs": None, "ys": np.array([5.0, 6.0]), "flip": False}
    assert compose.placement_matrix(place, 7) == [[0.5, 0.0, 10.0], [0.0, 0.5, 6.0]]
    assert compose.placement_matrix({**place, "flip": True}, 0) == [[-0.5, 0.0, 100.0], [0.0, 0.5, 5.0]]
