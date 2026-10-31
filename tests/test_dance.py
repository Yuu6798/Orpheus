import json
import subprocess
import sys

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import clips, compose, demo, key, lyrics, runs  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import probe  # noqa: E402
from mv_pipeline.io import PipelineError, read_json, sha256  # noqa: E402

KEY = {"color": [0.0, 255.0, 0.0], "tolerance": 0.22, "softness": 0.12, "spill": 1.0, "choke_px": 0.0, "feather_px": 0.0}


def raw_spec(**changes):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 12}, "duration_s": 2.0,
           "stage": {"path": "stage.png"}, "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 2}]}}
    raw.update(changes)
    return raw


def parse(tmp_path, **changes):
    return dancespec.parse_spec(raw_spec(**changes), tmp_path)


@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    """デモ素材（180x320・2 秒・12fps）と、その 1 回目の dance render の結果。"""
    root = tmp_path_factory.mktemp("dance")
    code, _, err = run_cli("dance", "demo", root, "--width", "180", "--height", "320", "--duration", "2", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs")
    assert code == 0, err
    return root, result


# ---- spec --------------------------------------------------------------------

def test_spec_fills_defaults_and_keeps_x_keys(tmp_path):
    data = parse(tmp_path, x_note="memo").data
    assert data["output"]["fps"] == 12 and data["output"]["crf"] == 18 and data["x_note"] == "memo"
    assert data["performer"]["key"]["color"] == [0.0, 255.0, 0.0] and data["camera"]["keys"][0]["zoom"] == 1.0
    assert {r["name"] for r in data["lyrics"]["ui_regions"]} == {"top", "right", "bottom"}


def test_landscape_has_no_default_ui_regions(tmp_path):
    assert parse(tmp_path, output={"width": 320, "height": 180}).data["lyrics"]["ui_regions"] == []


@pytest.mark.parametrize("changes, message", [
    ({"output": {"width": 181, "height": 320}}, "偶数"),
    ({"performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 1.5}, {"path": "b.mp4", "start": 1, "end": 2}]}}, "重なって"),
    ({"performer": {"clips": [{"path": "a.mp4", "start": 1, "end": 2}, {"path": "b.mp4", "start": 0, "end": 1}]}}, "昇順"),
    ({"performer": {"clips": [{"path": "a.mp4", "start": 1, "end": 1}]}}, "end"),
    ({"performer": {"clips": [{"path": "../a.mp4", "start": 0, "end": 2}]}}, "escapes"),
    ({"camera": {"keys": [{"t": 1}, {"t": 1}]}}, "昇順"),
    ({"lyrics": {"lines": [{"layer": "band", "text": "ab", "start": 0, "end": 1, "words": [{"text": "a"}]}]}}, "一致しません"),
    ({"lyrics": {"lines": [{"layer": "band", "text": "歌", "start": 0, "end": 1}]}}, "lyrics.font"),
    ({"lyrics": {"lines": [{"layer": "band", "text": "a", "start": 1, "end": 1}]}}, "end"),
    ({"lyrics": {"back": {"box": [0.5, 0.1, 0.4, 0.2]}}}, "box"),
    ({"unknown": 1}, "invalid"),
])
def test_spec_rejects_bad_input(tmp_path, changes, message):
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, **changes)


def test_spec_warns_about_gaps_without_a_performer(tmp_path):
    sp = parse(tmp_path, performer={"clips": [{"path": "a.mp4", "start": 0.5, "end": 1}]})
    assert sum("人物がいません" in w for w in sp.warnings) == 2


# ---- key ---------------------------------------------------------------------

def test_key_is_transparent_on_the_key_color_and_opaque_elsewhere():
    image = np.array([[[0, 255, 0], [0, 200, 10], [120, 60, 200], [235, 210, 200], [128, 128, 128], [20, 20, 20]]], np.uint8)
    alpha = key.alpha_of(image, KEY)[0]
    assert alpha[0] == 0 and alpha[1] == 0
    assert (alpha[2:] == 1).all()


def test_despill_removes_the_green_excess_only():
    image = np.array([[[100, 180, 90], [120, 60, 200]]], np.uint8)
    out = key.despill(image, KEY)
    assert out[0, 0].tolist() == [100, 100, 90] and out[0, 1].tolist() == [120, 60, 200]
    assert key.despill(image, {**KEY, "spill": 0.0}) is image


def test_cutout_is_premultiplied():
    image = np.zeros((4, 4, 3), np.uint8)
    image[:, :] = (0, 255, 0)
    image[1:3, 1:3] = (200, 100, 50)
    color, alpha = key.cutout(image, KEY)
    assert (color[alpha == 0] == 0).all() and color[1, 1, 0] > 0 and alpha[1, 1] == 1


def test_refine_shrinks_and_softens_the_edge():
    alpha = np.zeros((21, 21), np.float32)
    alpha[5:16, 5:16] = 1
    choked = key.refine(alpha, {**KEY, "choke_px": 2.0})
    assert choked.sum() < alpha.sum() and choked[10, 10] == 1 and choked[5, 10] == 0
    soft = key.refine(alpha, {**KEY, "feather_px": 2.0})
    assert 0 < soft[5, 10] < 1


# ---- カメラ -------------------------------------------------------------------

def test_camera_matrix_is_identity_at_rest_and_keeps_the_center():
    size = (180, 320)
    assert compose._is_identity(compose.camera_matrix(1.0, [0.5, 0.5], 0.0, size))
    matrix = compose.camera_matrix(1.5, [0.25, 0.75], 30.0, size)
    point = matrix @ np.array([0.25 * 180 - 0.5, 0.75 * 320 - 0.5, 1.0])
    assert point == pytest.approx([89.5, 159.5])


def test_seam_peaks_at_the_cut_and_ends_after_the_span():
    camera = {"keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5], "rotate_deg": 0.0}],
              "seams": [{"t": 3.0, "span_s": 0.4, "zoom": 1.3, "rotate_deg": 8.0}]}
    assert compose.camera_state(camera, 3.0)[0] == pytest.approx(1.3) and compose.camera_state(camera, 3.0)[2] == pytest.approx(8.0)
    before, after = compose.camera_state(camera, 2.9), compose.camera_state(camera, 3.1)
    assert 1.0 < before[0] < 1.3 and before[0] == pytest.approx(after[0])
    assert compose.camera_state(camera, 2.6) == (1.0, [0.5, 0.5], 0.0) and compose.camera_state(camera, 3.4)[0] == 1.0


# ---- 動画の読み出し --------------------------------------------------------------

def test_reading_order_does_not_change_the_frame(shot):
    root, _ = shot
    path = root / "clip_a.mp4"
    forward = clips.Source(path)
    images = [forward.read(i).copy() for i in range(6)]
    jumping = clips.Source(path)
    assert (jumping.read(5) == images[5]).all() and (jumping.read(2) == images[2]).all() and (jumping.read(2) is jumping.read(2))
    forward.close()
    jumping.close()


def test_reading_past_the_end_returns_the_true_last_frame(shot):
    root, _ = shot
    path = root / "clip_a.mp4"
    source = clips.Source(path)
    n = 0
    while source.read(n) is not None and source.end_index is None:
        n += 1
    last = source.read(source.end_index).copy()
    source.close()
    other = clips.Source(path)
    other.read(0)
    assert (other.read(10_000) == last).all() and (other.read(10_001) == last).all()
    other.close()


def test_timeline_maps_shot_time_to_source_frames(tmp_path, shot):
    root, _ = shot
    sp = dancespec.parse_spec({**raw_spec(), "performer": {"clips": [
        {"path": "clip_a.mp4", "start": 0.0, "end": 1.0, "source_start": 0.5, "speed": 0.5},
        {"path": "clip_b.mp4", "start": 1.0, "end": 2.0}]}}, root)
    line = clips.Timeline(sp)
    first, second = sp.data["performer"]["clips"]
    assert line.index_at(0.0) == 0 and line.index_at(1.0) == 1 and line.index_at(2.0) is None
    assert line.source_frame(first, 0.0) == 6 and line.source_frame(first, 1.0 - 1 / 12) == 11
    assert line.source_frame(second, 1.0) == 0 and line.seams() == [1.0]
    line.close()


# ---- 字 ----------------------------------------------------------------------

def layout_line(text, layer="back", words=(), **style):
    data = {"font": None, "font_index": 0, "back": {**dancespec.LAYER_DEFAULTS["back"], **style},
            "band": {**dancespec.LAYER_DEFAULTS["band"], **style}}
    return lyrics.layout({"layer": layer, "text": text, "start": 1.0, "end": 2.0, "words": list(words)}, data, None, (1080, 1920))


def test_long_text_shrinks_to_stay_inside_the_box():
    line = layout_line("A VERY LONG HOOK THAT DOES NOT FIT AT THE DEFAULT SIZE")
    x0, y0, x1, y1 = dancespec.LAYER_DEFAULTS["back"]["box"]
    box = line.box_px
    assert box[0] >= x0 * 1080 - 2 and box[2] <= x1 * 1080 + 2 and box[1] >= y0 * 1920 - 2 and box[3] <= y1 * 1920 + 2


def test_text_that_cannot_fit_is_reported_and_not_hidden_by_the_clipped_box(tmp_path):
    long_text = "LONG HOOK " * 40
    data = parse(tmp_path, lyrics={"lines": [{"layer": "back", "text": long_text, "start": 0, "end": 1}]}).data
    lines, warnings = lyrics.prepare(data, None, (1080, 1920))
    assert lines[0].overflow is True and any("収まりません" in w for w in warnings)
    assert lines[0].box_px[0] == 0 and lines[0].box_px[2] == 1080       # 箱は画面に切られるので、欠けは overflow でしか分からない


def test_preview_scale_fits_the_same_line_as_full_size(tmp_path):
    text = "A LINE THAT NEEDS A SMALL FONT TO FIT IN THE BOX " * 4
    data = parse(tmp_path, lyrics={"lines": [{"layer": "band", "text": text, "start": 0, "end": 1}]}).data
    full, _ = lyrics.prepare(data, None, (1080, 1920))
    preview, warnings = lyrics.prepare(data, None, (270, 480))
    assert full[0].overflow is False and preview[0].overflow is False and warnings == []


def test_text_is_centered_in_the_box():
    box = layout_line("HOLD").box_px
    assert (box[0] + box[2]) / 2 == pytest.approx(540, abs=3)


@pytest.mark.parametrize("text, cut", [("LIGHT", 2), ("AVATAR", 1), ("AVATAR", 3)])
def test_splitting_into_words_does_not_change_a_single_pixel(text, cut):
    """語の境目が字詰めの対（A と V など）をまたいでも、強調の無い絵は行全体を描いたものと同じ。"""
    words = [{"text": text[:cut], "t": None, "accent": False}, {"text": text[cut:], "t": None, "accent": False}]
    plain, split = layout_line(text), layout_line(text, words=words)
    a, b = np.zeros((1920, 1080, 3), np.float32), np.zeros((1920, 1080, 3), np.float32)
    lyrics.draw(a, plain, 1.5, 1 / 24)
    lyrics.draw(b, split, 1.5, 1 / 24)
    assert plain.box_px == split.box_px and len(split.runs) == 2 and (a == b).all()


def test_boundary_between_words_follows_the_kerned_position():
    line = layout_line("AVATAR", words=[{"text": "AV", "t": None, "accent": False}, {"text": "ATAR", "t": None, "accent": False}])
    font = lyrics.ImageFont.load_default(180)
    assert line.runs[1].x - line.runs[0].x == pytest.approx(font.getlength("AV") + 2 - font.getbbox("AVATAR", anchor="ls")[0], abs=2)


def test_accent_changes_only_the_color_at_the_word_time():
    line = layout_line("HOLD", words=[{"text": "HOLD", "t": 1.5, "accent": True}], fade_in_s=0.0, fade_out_s=0.0, opacity=1.0)
    canvas = lambda: np.zeros((1920, 1080, 3), np.float32)  # noqa: E731
    before, after = canvas(), canvas()
    lyrics.draw(before, line, 1.4, 1 / 24)
    lyrics.draw(after, line, 1.6, 1 / 24)
    assert ((before.sum(2) > 0) == (after.sum(2) > 0)).all()      # 字の形は同じ
    y, x = np.argwhere(before.sum(2) == before.sum(2).max())[0]
    assert before[y, x, 1] > after[y, x, 1]                       # 緑が減る = accent の色


def test_fade_is_visible_from_the_first_frame_and_gone_at_the_end():
    line = layout_line("HOLD", fade_in_s=0.1, fade_out_s=0.1)
    assert line.fade(0.99, 0.04) == 0 and line.fade(1.0, 0.04) == pytest.approx(0.4)
    assert line.fade(1.5, 0.04) == 1 and line.fade(1.95, 0.04) == pytest.approx(0.5) and line.fade(2.0, 0.04) == 0


def test_overlap_with_a_ui_region_is_reported(tmp_path):
    data = parse(tmp_path, lyrics={"back": {"box": [0.1, 0.5, 1.0, 0.7]}, "lines": [
        {"layer": "back", "text": "A LONG HOOK LINE GOES HERE AND HERE", "start": 0, "end": 1}]}).data
    _, warnings = lyrics.prepare(data, None, (1080, 1920))
    assert len(warnings) == 1 and "'right'" in warnings[0]


def test_text_in_the_default_boxes_avoids_the_default_ui_regions(tmp_path):
    data = parse(tmp_path, lyrics={"lines": [{"layer": "back", "text": "HOLD ME", "start": 0, "end": 1},
                                             {"layer": "band", "text": "band line", "start": 0, "end": 1}]}).data
    assert lyrics.prepare(data, None, (1080, 1920))[1] == []


# ---- 合成 --------------------------------------------------------------------

def test_layer_order_stage_back_text_performer_band(shot):
    root, _ = shot
    data = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    data["lyrics"]["back"] = {"box": [0.05, 0.05, 0.95, 0.6], "size_px": 500}   # 頭と胴に掛かる大きな字
    data["lyrics"]["lines"] = [{"layer": "back", "text": "HHHH", "start": 0, "end": 2}]
    data["flashes"], data["camera"] = [], {}
    with_back = compose.prepare(dancespec.parse_spec(data, root))
    data["lyrics"]["lines"] = []
    without = compose.prepare(dancespec.parse_spec(data, root))
    try:
        a, b = compose.frame(with_back, 0.5), compose.frame(without, 0.5)
        changed = (a != b).any(axis=2)
        box = with_back.lines[0].box_px
        assert changed[box[1]:box[3], box[0]:box[2]].any()
        matrix = compose.camera_matrix(1.0, [0.5, 0.5], 0.0, without.size)
        _, alpha = compose._performer(without, 0.5, matrix)
        opaque = alpha >= 1.0
        inside = np.zeros_like(opaque)
        inside[box[1]:box[3], box[0]:box[2]] = True
        assert (opaque & inside).any() and not changed[opaque].any()   # 人物の不透明な画素は後ろの字に影響されない
    finally:
        with_back.close()
        without.close()


def test_frame_is_deterministic_in_any_order(shot):
    root, _ = shot
    sp = dancespec.load_spec(root / "spec.json")
    first, second = compose.prepare(sp), compose.prepare(sp)
    try:
        forward = [compose.frame(first, t) for t in (0.25, 0.75, 1.25)]
        backward = [compose.frame(second, t) for t in (1.25, 0.75, 0.25)][::-1]
        assert all((a == b).all() for a, b in zip(forward, backward))
    finally:
        first.close()
        second.close()


def test_flash_fades_and_band_text_stays_above_it(shot):
    root, _ = shot
    data = json.loads((root / "spec.json").read_text(encoding="utf-8"))
    data["flashes"] = [{"t": 0.5, "strength": 1.0, "decay_s": 0.05}]
    data["camera"] = {}
    data["lyrics"]["band"] = {"color": [255, 0, 0], "stroke_px": 0}
    data["lyrics"]["lines"] = [{"layer": "band", "text": "HOLD", "start": 0, "end": 2}]
    ctx = compose.prepare(dancespec.parse_spec(data, root))
    try:
        peak, after = compose.frame(ctx, 0.5), compose.frame(ctx, 0.9)
        assert peak[-1, 0].min() > 240 and after[-1, 0].max() < 120
        box = ctx.lines[0].box_px
        assert peak[box[1]:box[3], box[0]:box[2], 1].min() < 40   # 字は白飛びしない
    finally:
        ctx.close()


# ---- run ---------------------------------------------------------------------

def test_render_writes_a_video_a_timeline_and_a_manifest(shot):
    root, result = shot
    run_dir = root / "runs" / result["run_id"]
    assert result["status"] == "succeeded" and result["technical_pass"] is True and result["reused"] is False
    assert result["interval"]["frames"] == 24 and result["size"] == [180, 320] and result["grid"] is None
    assert sorted(p.name for p in run_dir.iterdir()) == sorted(["dance.mp4", "manifest.json", "render.log", "spec.json", "timeline.json"])
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["kind"] == "dance-render" and manifest["engine"]["fingerprint"] == dance.fingerprint()
    assert set(manifest["assets"]) == {"stage", "clip:0", "clip:1", "audio"}
    checks = probe.check_video(run_dir / "dance.mp4", width=180, height=320, fps=12, frames=24, audio=True, audio_s=2.0, bt709=True)
    assert checks["pass"], checks["failures"]


def test_timeline_records_what_an_external_checker_needs(shot):
    root, result = shot
    record = read_json(root / "runs" / result["run_id"] / "timeline.json")
    assert record["schema_version"] == "dance-timeline/0.1" and record["size"] == [180, 320] and record["seams"] == [1.0]
    assert record["flashes"] == [1.0] and len(record["clips"]) == 2 and len(record["ui_regions"]) == 3
    assert record["interval"] == {"start": 0.0, "duration": 2.0, "frames": 24}
    assert [line["layer"] for line in record["lyrics"]] == ["back", "back", "band", "band"]
    first = record["lyrics"][0]
    assert first["text"] == "HOLD" and first["start"] == 0.5 and all(0 <= v <= 1 for v in first["box"])


def test_partial_render_records_its_interval_in_the_timeline(tmp_path, shot):
    root, _ = shot
    code, result, err = run_cli("dance", "render", root / "spec.json", "--start", "0.5", "--duration", "1", "--out", tmp_path / "runs")
    assert code == 0, err
    record = read_json(tmp_path / "runs" / result["run_id"] / "timeline.json")
    assert record["interval"] == {"start": 0.5, "duration": 1.0, "frames": 12} and record["duration_s"] == 2.0


def test_second_render_is_reused_and_a_changed_asset_is_not(tmp_path, shot):
    root, result = shot
    code, again, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs")
    assert code == 0, err
    assert again["reused"] is True and again["run_id"] == result["run_id"]
    other = tmp_path / "copy"
    other.mkdir()
    for name in ("spec.json", "stage.png", "clip_a.mp4", "clip_b.mp4", "audio.wav"):
        (other / name).write_bytes((root / name).read_bytes())
    code, base, err = run_cli("dance", "render", other / "spec.json", "--duration", "0.5")
    assert code == 0, err
    (other / "stage.png").write_bytes(_recolored(other / "stage.png"))
    code, changed, err = run_cli("dance", "render", other / "spec.json", "--duration", "0.5")
    assert code == 0, err
    assert changed["reused"] is False and changed["run_id"] != base["run_id"]


def _recolored(path):
    image = cv2.imread(str(path))
    return cv2.imencode(".png", 255 - image)[1].tobytes()


def test_missing_asset_stops_with_the_role_and_path(tmp_path):
    (tmp_path / "spec.json").write_text(json.dumps(raw_spec()), encoding="utf-8")
    code, _, err = run_cli("dance", "render", tmp_path / "spec.json")
    assert code == 1 and "stage" in err and "stage.png" in err


def test_stills_draws_guides_and_keeps_warnings(shot):
    root, _ = shot
    code, result, err = run_cli("dance", "stills", root / "spec.json", "--times", "0.5", "1.0", "--guides", "--out", root / "sheets")
    assert code == 0, err
    sheet = cv2.imread(result["path"])
    assert sheet.shape[:2] == (320, 720)
    code, plain, err = run_cli("dance", "stills", root / "spec.json", "--times", "0.5", "1.0", "--out", root / "sheets")
    assert code == 0, err
    assert plain["path"] != result["path"]
    assert run_cli("dance", "stills", root / "spec.json", "--times", "9", "--out", root / "sheets")[0] == 1


def test_accept_records_a_decision_on_the_dance_video(shot):
    root, result = shot
    run_dir = root / "runs" / result["run_id"]
    code, record, err = run_cli("dance", "accept", run_dir, "--decision", "hold", "--by", "tester")
    assert code == 0, err
    assert record["variant"] == "dance" and record["video_sha256"] == sha256(run_dir / "dance.mp4")


def test_importing_dance_does_not_load_cv2():
    code = "import sys; import mv_pipeline.dance, mv_pipeline.dance.spec; sys.exit('cv2' in sys.modules)"
    assert subprocess.run([sys.executable, "-c", code]).returncode == 0


def test_demo_uses_ascii_text_so_it_needs_no_font_file(tmp_path):
    spec = demo._spec(180, 320, 2.0, 12)
    assert all(line["text"].isascii() for line in spec["lyrics"]["lines"])
    assert dancespec.parse_spec(spec, tmp_path).data["duration_s"] == 2.0


def test_runs_default_out_is_next_to_the_spec(tmp_path):
    assert runs.default_out(parse(tmp_path)) == tmp_path.resolve() / "dance_runs"
