"""dance-shot/0.3: コマ番号の時刻・元のコマの丸め・retime・replace_frames と、0.2 の互換・検査・fit・plan・デモ。"""
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import Image  # noqa: E402

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import beats, clips, compose, demo, fit, plan  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.fx import timeline  # noqa: E402
from mv_pipeline.inspection import targets  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

KEY = {"color": [0.0, 255.0, 0.0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 0.0, "feather_px": 0.0}
GREEN = (0, 255, 0)
W, H = 64, 96


def base(**performer):
    return {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 30}, "duration_s": 2.0,
            "stage": {"path": "stage.png"},
            "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 1}, {"path": "b.mp4", "start": 1, "end": 2}], **performer}}


def parse(tmp_path, raw):
    return dancespec.parse_spec(raw, tmp_path)


class Stub:
    """素材を読まずに対応だけを見るための読み手（fps と寸法だけ）。"""

    def __init__(self, fps=30.0):
        self.fps, self.width, self.height = fps, W, H

    def close(self):
        pass


@pytest.fixture
def stub_fps(monkeypatch):
    fps = {}
    monkeypatch.setattr(clips.Timeline, "_source", staticmethod(lambda spec, clip: Stub(fps.get(clip["path"], 30.0))))
    return fps


def shown_numbers(sp):
    """出力のコマごとの (clip の番号, 見せる元のコマ)。"""
    fps = sp.data["output"]["fps"]
    tl = clips.Timeline(sp)
    try:
        return [tuple(tl.mapping(timeline.frame_time(k, fps))[0][:2]) for k in range(timeline.frame_count(sp.data["duration_s"], fps))]
    finally:
        tl.close()


def write_video(path, frames, fps=30):
    fxrender.write_video(path, iter(frames), (frames[0].shape[1], frames[0].shape[0]), fps, 0, len(frames))


def box(i, top=20, height=50, left=None):
    image = np.full((H, W, 3), GREEN, np.uint8)
    left = 20 if left is None else left
    image[top:top + height, left:left + 16] = (200, 60, 120 + i)
    return image


# ---- 版と時刻のコマ番号 ------------------------------------------------------------

def test_frame_refs_resolve_to_output_frames(tmp_path):
    raw = base()
    raw["performer"]["clips"] = [{"path": "a.mp4", "start": 0, "end": {"frame": 31}}, {"path": "b.mp4", "start": {"frame": 31}, "end": {"frame": 60}}]
    raw["camera"] = {"keys": [{"t": 0}, {"t": {"frame": 45}, "zoom": 1.1}]}
    data = parse(tmp_path, raw).data
    assert data["performer"]["clips"][0]["end"] == data["performer"]["clips"][1]["start"] == 31 / 30
    assert data["camera"]["keys"][1]["t"] == 1.5 and data["performer"]["clips"][1]["end"] == 2.0
    assert beats.resolve_time({"frame": 12}, None, "t", 24) == 0.5
    with pytest.raises(PipelineError, match="output.fps"):
        beats.resolve_time({"frame": 12}, None, "t")


@pytest.mark.parametrize("change", [
    lambda raw: raw["performer"].update(frame_rounding="nearest"),
    lambda raw: raw["performer"].update(replace_frames=[{"path": "a.mp4", "frames": [1, 2]}]),
    lambda raw: raw["performer"]["clips"][0].update(retime=[{"from": 0, "to": 0.5, "source": [0, 0]}]),
    lambda raw: raw["performer"]["clips"][0].update(end={"frame": 30}),
])
def test_a_02_spec_cannot_use_the_03_fields(tmp_path, change):
    raw = base()
    raw["schema_version"] = dance.SCHEMA_V02
    change(raw)
    with pytest.raises(PipelineError, match="invalid"):
        parse(tmp_path, raw)


def test_a_02_spec_reads_with_the_03_defaults_and_maps_like_before(tmp_path, stub_fps):
    stub_fps["b.mp4"] = 24.0
    raw = base()
    raw["performer"]["clips"][0]["source_start"] = -0.04
    raw["performer"]["clips"][1].update(source_start=0.3, speed=0.85)
    old = parse(tmp_path, {**raw, "schema_version": dance.SCHEMA_V02})
    assert old.data["source_schema"] == dance.SCHEMA_V02 and old.data["schema_version"] == dance.SCHEMA_VERSION
    assert old.data["performer"]["frame_rounding"] == "floor" and old.data["performer"]["replace_frames"] == []
    assert all(clip["retime"] == [] for clip in old.data["performer"]["clips"])
    mapped = shown_numbers(old)
    assert mapped == shown_numbers(parse(tmp_path, raw))
    expected = []
    for k in range(60):                                      # 0.2 の式（切り捨て。誤差で 1 つ前に落ちないよう微小値を足す）
        t = k / 30
        index, (source_start, speed, fps, start) = (0, (-0.04, 1.0, 30.0, 0.0)) if t < 1 else (1, (0.3, 0.85, 24.0, 1.0))
        expected.append((index, max(0, int(math.floor((source_start + (t - start) * speed) * fps + 1e-6)))))
    assert mapped == expected


def test_nearest_rounds_half_frames_up_and_floor_keeps_the_02_rule(tmp_path, stub_fps):
    assert [clips.round_frame(v, "nearest") for v in (2.5, 2.49, 3.5, -0.6, -0.4)] == [3, 2, 4, -1, 0]
    assert [clips.round_frame(v, "floor") for v in (2.9999999, 2.99, 3.0)] == [3, 2, 3]
    raw = base(frame_rounding="nearest")
    raw["performer"]["clips"][0]["source_start"] = -0.045           # (t − 0.045)·30 = k − 1.35
    near = [n for _, n in shown_numbers(parse(tmp_path, raw))[:5]]
    raw["performer"]["frame_rounding"] = "floor"
    assert near == [0, 0, 1, 2, 3] and [n for _, n in shown_numbers(parse(tmp_path, raw))[:5]] == [0, 0, 0, 1, 2]


# ---- retime -----------------------------------------------------------------------

def test_retime_moves_and_holds_frames_and_reads_seconds(tmp_path, stub_fps):
    raw = base(frame_rounding="nearest")
    raw["performer"]["clips"][1]["retime"] = [{"from": 1.5, "to": 1.8, "source": [40, 60]}, {"from": 1.8, "to": 9.0, "source": [60, 60]}]
    raw["performer"]["clips"][0]["retime"] = [{"from": 0.5, "to": 0.75, "source": [0.2, 0.2], "unit": "s", "x_note": "hold"}]
    sp = parse(tmp_path, raw)
    assert sp.warnings == ()
    shown = [n for _, n in shown_numbers(sp)]
    assert shown[14:24] == [14] + [6] * 8 + [23]                  # 0.5〜0.75 秒は元の 0.2 秒（コマ 6）で止まる
    assert shown[30:45] == list(range(15))                         # 区間の外は既定の対応
    assert shown[45:49] == [40, 42, 44, 47] and shown[53] == 58    # 40 + 20·(t − 1.5)/0.3 の四捨五入（46.67 → 47、57.78 → 58）
    assert set(shown[54:]) == {60}


@pytest.mark.parametrize("segments, clip_index, message", [
    ([{"from": 0.6, "to": 0.6, "source": [1, 2]}], 0, "to は from より後"),
    ([{"from": 0.2, "to": 0.6, "source": [1, 2]}, {"from": 0.5, "to": 0.8, "source": [3, 4]}], 0, "重なって"),
])
def test_retime_segments_must_be_ordered_and_apart(tmp_path, segments, clip_index, message):
    raw = base()
    raw["performer"]["clips"][clip_index]["retime"] = segments
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, raw)


def test_retime_outside_the_clip_warns_and_a_still_cannot_retime(tmp_path):
    raw = base()
    raw["performer"]["clips"][0]["retime"] = [{"from": 1.2, "to": 1.5, "source": [1, 2]}]
    assert any("効きません" in w for w in parse(tmp_path, raw).warnings)
    raw = base()
    raw["performer"]["clips"][1] = {"path": "end.png", "start": 1, "end": 2, "retime": [{"from": 1.2, "to": 1.5, "source": [0, 0]}]}
    with pytest.raises(PipelineError, match="静止画"):
        parse(tmp_path, raw)


# ---- replace_frames ---------------------------------------------------------------

def test_replace_frames_show_the_nearest_clean_frame_or_the_named_one(tmp_path, stub_fps):
    raw = base(replace_frames=[{"path": "b.mp4", "frames": [5, 8]}, {"path": "b.mp4", "frames": [10, 11], "with": "after"},
                               {"path": "b.mp4", "frames": [14, 16], "with": 3}, {"path": "b.mp4", "frames": [20, 22], "with": "before"},
                               {"path": "a.mp4", "frames": [0, 2]}])
    shown = [n for _, n in shown_numbers(parse(tmp_path, raw))]
    a, b = shown[:30], shown[30:]
    assert b[4:9] == [4, 4, 4, 8, 8]               # 5 は前、6 は同じ距離なので前、7 は後
    assert b[9:12] == [9, 11, 11] and b[13:17] == [13, 3, 3, 16] and b[19:23] == [19, 19, 19, 22]
    assert a[:3] == [2, 2, 2]                      # 0 コマ目からの区間の nearest は後ろのコマ


def test_a_whole_number_target_is_kept_as_an_integer(tmp_path):
    raw = base(replace_frames=[{"path": "a.mp4", "frames": [3, 4], "with": 6.0}])
    assert parse(tmp_path, raw).data["performer"]["replace_frames"][0]["with"] == 6


@pytest.mark.parametrize("runs, message", [
    ([{"path": "c.mp4", "frames": [1, 2]}], "動画ではありません"),
    ([{"path": "a.mp4", "frames": [5, 5]}], r"\[a, b\)"),
    ([{"path": "a.mp4", "frames": [0, 2], "with": "before"}], "before は使えません"),
    ([{"path": "a.mp4", "frames": [5, 8]}, {"path": "a.mp4", "frames": [7, 9]}], "重なって"),
    ([{"path": "a.mp4", "frames": [5, 7]}, {"path": "a.mp4", "frames": [7, 9], "with": 2}], "接して"),
    ([{"path": "a.mp4", "frames": [5, 8], "with": 6}], "区間 \\[5, 8\\] の中"),
    ([{"path": "a.mp4", "frames": [5, 8], "with": 9}, {"path": "a.mp4", "frames": [9, 10], "with": 2}], "区間 \\[9, 10\\] の中"),
])
def test_replace_frames_are_checked_against_the_clips(tmp_path, runs, message):
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, base(replace_frames=runs))


def test_touching_runs_are_fine_when_neither_uses_its_neighbours(tmp_path):
    raw = base(replace_frames=[{"path": "a.mp4", "frames": [5, 7], "with": 3}, {"path": "a.mp4", "frames": [7, 9], "with": 3}])
    assert len(parse(tmp_path, raw).data["performer"]["replace_frames"]) == 2
    raw = base(replace_frames=[{"path": "end.png", "frames": [0, 1]}])
    raw["performer"]["clips"][1]["path"] = "end.png"
    with pytest.raises(PipelineError, match="静止画の clip は差し替えられません"):
        parse(tmp_path, raw)


@pytest.fixture
def two_clips(tmp_path):
    write_video(tmp_path / "a.mp4", [box(i) for i in range(12)])
    write_video(tmp_path / "b.mp4", [box(i) for i in range(30)])
    return tmp_path


@pytest.mark.parametrize("run, message", [
    ({"frames": [10, 12]}, "最後のコマ"),
    ({"frames": [11, 12], "with": "after"}, "最後のコマ"),
    ({"frames": [3, 4], "with": 15}, "動画（12 コマ"),
])
def test_replacements_that_would_show_a_missing_frame_stop_before_drawing(two_clips, run, message):
    sp = parse(two_clips, base(replace_frames=[{"path": "a.mp4", **run}]))
    tl = clips.Timeline(sp)
    try:
        with pytest.raises(PipelineError, match=message):
            clips.check_replacements(tl)
    finally:
        tl.close()


def test_replacements_at_the_end_with_before_pass_and_outside_runs_warn(two_clips):
    sp = parse(two_clips, base(replace_frames=[{"path": "a.mp4", "frames": [10, 12], "with": "before"},
                                               {"path": "b.mp4", "frames": [40, 42]}]))
    tl = clips.Timeline(sp)
    try:
        warnings = clips.check_replacements(tl)
    finally:
        tl.close()
    assert len(warnings) == 1 and "[40, 42)" in warnings[0] and "30 コマ" in warnings[0]


def test_an_image_stands_in_for_one_frame(two_clips):
    fix = np.full((H, W, 3), (0, 0, 200), np.uint8)
    Image.fromarray(fix).save(two_clips / "fix.png")
    sp = parse(two_clips, base(replace_frames=[{"path": "a.mp4", "frames": [3, 4], "with": {"image": "fix.png"}}]))
    assert dancespec.asset_refs(sp.data)["replace:0"] == "fix.png"
    tl = clips.Timeline(sp)
    try:
        assert clips.check_replacements(tl) == []
        shown = tl.shown(3 / 30)
        assert shown[0].frame == 3 and (shown[0].image == fix).all()
        assert tl.mapping(3 / 30) == [(0, 3, 1.0)] and not (tl.shown(2 / 30)[0].image == fix).all()
    finally:
        tl.close()
    usage, _, _ = targets.dance_usage(sp)
    assert 3 not in [number for _, _, number, _ in usage["a.mp4"]] and 2 in [number for _, _, number, _ in usage["a.mp4"]]
    Image.fromarray(np.zeros((32, 32, 3), np.uint8)).save(two_clips / "small.png")
    tl = clips.Timeline(parse(two_clips, base(replace_frames=[{"path": "a.mp4", "frames": [3, 4], "with": {"image": "small.png"}}])))
    try:
        with pytest.raises(PipelineError, match="寸法"):
            clips.check_replacements(tl)
    finally:
        tl.close()


# ---- fit ---------------------------------------------------------------------------

def test_fit_treats_replaced_frames_as_missing(tmp_path):
    frames = [box(i) for i in range(12)]
    frames[4] = box(4, left=40)                                   # 壊れたコマ: 人物が横に跳ぶ
    write_video(tmp_path / "jump.mp4", frames)
    settings = dancespec._normalize_fit({"mode": "body", "x": "track", "ground": "lock", "x_smooth_s": 0.0, "analysis_scale": 1.0})
    assert fit.settings_digest(KEY, settings) == fit.settings_digest(KEY, settings, ())
    assert fit.settings_digest(KEY, settings) != fit.settings_digest(KEY, settings, [[4, 5]])
    plain = fit.analyze(clips.Source(tmp_path / "jump.mp4"), KEY, settings)
    skipped = fit.analyze(clips.Source(tmp_path / "jump.mp4"), KEY, settings, [[4, 5]])
    assert "skipped" not in plain and skipped["skipped"] == [[4, 5]]
    assert plain["cx"][4] > plain["cx"][3] + 10 and skipped["cx"][4] == pytest.approx(skipped["cx"][3], abs=0.01)


# ---- plan・デモ・検品の提案 -----------------------------------------------------------

def test_plan_shows_the_03_tables_only_for_03_specs(tmp_path):
    raw = base(frame_rounding="nearest", replace_frames=[{"path": "a.mp4", "frames": [3, 5], "with": 2}])
    raw["performer"]["clips"][1]["retime"] = [{"from": 1.5, "to": 2.0, "source": [10, 10]}]
    text = plan.build(parse(tmp_path, raw).data)
    assert "元のコマの丸め nearest" in text and "## retime" in text and "10→10 frame（止め）" in text
    assert "## replace_frames" in text and "[3, 5)" in text and "コマ 2" in text
    old = plan.build(parse(tmp_path, {**base(), "schema_version": dance.SCHEMA_V02}).data)
    assert "## retime" not in old and "丸め" not in old and "## clips" in old


def test_the_demo_spec_versions(tmp_path):
    new = demo._spec(180, 320, 3.0, 12)
    old = demo._spec(180, 320, 3.0, 12, "0.2")
    assert new["schema_version"] == dance.SCHEMA_VERSION and old["schema_version"] == dance.SCHEMA_V02
    assert "replace_frames" not in old["performer"] and new["performer"]["replace_frames"][0]["frames"] == [4, 5]
    data = parse(tmp_path, new).data
    assert data["performer"]["clips"][2]["retime"][0] == {"from": 2.25, "to": 2.5, "source": [0.25, 0.25], "unit": "s"}
    code, _, err = run_cli("dance", "demo", tmp_path / "d02", "--width", "180", "--height", "320", "--duration", "2", "--fps", "12", "--schema", "0.2")
    assert code == 0, err
    assert read_json(tmp_path / "d02" / "spec.json")["schema_version"] == dance.SCHEMA_V02


def test_the_03_demo_hides_its_broken_frame(tmp_path):
    demo.create(tmp_path, 180, 320, 3.0, 12)
    raw = read_json(tmp_path / "spec.json")
    broken = raw["performer"]["replace_frames"][0]["frames"][0]
    (tmp_path / "spec_raw.json").write_text(json.dumps({**raw, "performer": {k: v for k, v in raw["performer"].items() if k != "replace_frames"}}),
                                            encoding="utf-8")
    red, green, blue = (lambda image, c=c: image[..., c].astype(int) for c in range(3))
    # 帯の紫 (200, 40, 220) だけを数える（歌詞のピンクは赤が青より強く、服の紫は赤が 150 に届かない）
    purple = lambda image: int(((red(image) > 150) & (green(image) < 80) & (blue(image) > red(image))).sum())  # noqa: E731
    counts = {}
    for name in ("spec.json", "spec_raw.json"):
        ctx = compose.prepare(dancespec.load_spec(tmp_path / name), out=tmp_path / "runs")
        try:
            clip = ctx.clips.clips[1]
            times = [k / 12 for k in range(36) if ctx.clips.index_at(k / 12) == 1 and ctx.clips.raw_frame(clip, k / 12) == broken]
            assert times
            counts[name] = [purple(compose.frame(ctx, t)) for t in times]
            if name == "spec.json":
                assert all(ctx.clips.mapping(t)[0][1] != broken for t in times)
        finally:
            ctx.close()
    assert max(counts["spec.json"]) == 0 and min(counts["spec_raw.json"]) > 1000


def test_inspect_offers_frame_replacement_as_a_spec_entry_only_for_dance_clips():
    event = {"frames": [30, 31], "category": "continuity", "signals": [{"check": "glitch", "category": "continuity", "kind": "pair", "frames": [30, 31]}]}
    clip = {"id": "c", "rel": "clip.mp4", "sha256": "0" * 64, "fps": 24.0, "frames_total": 96, "inspected": [0, 90], "origin": None}
    from mv_pipeline.inspection import config as inspect_config
    plain = next(a for a in targets.actions(event, clip, inspect_config.THRESHOLDS) if a["kind"] == "replace_frames")
    assert plain["in_orpheus"] is False and "spec_entry" not in plain
    action = next(a for a in targets.actions(event, {**clip, "spec": "spec.json"}, inspect_config.THRESHOLDS) if a["kind"] == "replace_frames")
    assert action["in_orpheus"] is True and action["with"] == [29, 31]
    assert action["spec_entry"] == {"pointer": "/performer/replace_frames/-", "value": {"path": "clip.mp4", "frames": [30, 31], "with": "nearest"}}
