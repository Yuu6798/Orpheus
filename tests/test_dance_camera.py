"""dance-shot/0.2 のカメラ: アクセント・非対称の seams・回転の継続・pivot・cover_rotation・ブレ・ステージの動画と視差・フラッシュ。"""
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import camera as dancecamera  # noqa: E402
from mv_pipeline.dance import compose, plan  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import camera as fxcamera  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.fx.color import from_srgb_float, to_srgb_float  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

SIZE = (180, 320)
FPS = 30.0


def base(**changes):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 180, "height": 320, "fps": 30}, "duration_s": 4.0,
           "stage": {"path": "stage.png"}, "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 4}]}}
    raw.update(changes)
    return raw


def camera_of(tmp_path, fps=30, **camera):
    sp = dancespec.parse_spec(base(camera=camera, beats={"bpm": 120, "count": 16}, output={"width": 180, "height": 320, "fps": fps}), tmp_path)
    return dancecamera.Camera(sp.data["camera"], SIZE, float(fps)), sp


# ---- アクセント ------------------------------------------------------------------

def test_envelope_is_zero_before_the_beat_peaks_on_it_and_decays():
    assert dancecamera.envelope(-0.01, 0.0, 0.1) == 0.0
    assert dancecamera.envelope(0.0, 0.0, 0.1) == 1.0
    assert dancecamera.envelope(0.1, 0.0, 0.1) == pytest.approx(math.exp(-1))
    assert dancecamera.envelope(0.5, 0.0, 0.1) == 0.0


def test_envelope_with_an_attack_rises_before_the_beat():
    assert dancecamera.envelope(-0.11, 0.1, 0.1) == 0.0
    assert dancecamera.envelope(-0.05, 0.1, 0.1) == pytest.approx(0.5)


def test_accent_multiplies_the_zoom_only_after_the_beat(tmp_path):
    cam, _ = camera_of(tmp_path, accents=[{"at": [{"beat": 2}], "zoom": 1.1, "decay_s": 0.1}])
    assert cam.state(0.99).zoom == 1.0
    assert cam.state(1.0).zoom == pytest.approx(1.1)
    assert cam.state(1.1).zoom == pytest.approx(1.0 + 0.1 * math.exp(-1))
    assert cam.state(2.0).zoom == 1.0


def test_shake_is_deterministic_and_scaled_to_the_width(tmp_path):
    cam, _ = camera_of(tmp_path, accents=[{"at": [1.0], "zoom": 1.0, "decay_s": 0.1, "shake_px": 18, "shake_decay_s": 0.2}])
    dt = 0.03
    moved = cam.state(1.0 + dt).shake
    size = 18 * (180 / 1080) * math.exp(-dt / 0.2)
    assert moved == pytest.approx((size * math.sin(2 * math.pi * 15.1 * dt), size * math.cos(2 * math.pi * 12.7 * dt)))
    assert cam.state(1.0 + dt).shake == moved and cam.state(0.99).shake == (0.0, 0.0)


def test_shake_moves_the_picture_in_screen_pixels(tmp_path):
    cam, _ = camera_of(tmp_path, keys=[{"t": 0, "zoom": 2.0}], accents=[{"at": [1.0], "zoom": 1.0, "decay_s": 0.1, "shake_px": 60}])
    still = cam.matrix(cam.state(0.5))
    moved = cam.matrix(cam.state(1.0))
    assert (moved - still)[:, 2] == pytest.approx([0.0, 10.0])
    assert (moved - still)[:, :2] == pytest.approx(np.zeros((2, 2)))


def test_grid_accents_follow_the_grid_and_the_exceptions(tmp_path):
    _, sp = camera_of(tmp_path, accents=[{"at": {"grid": "downbeats", "from": {"beat": 4}, "to": 7.0, "except": [{"beat": 8}]}, "zoom": 1.02}])
    assert sp.data["camera"]["accents"][0]["times"] == [2.0, 6.0]
    _, sp = camera_of(tmp_path, accents=[{"at": {"grid": "beats", "to": 3.0, "every": 2}}])
    assert sp.data["camera"]["accents"][0]["times"] == [0.0, 1.0, 2.0, 3.0]
    _, sp = camera_of(tmp_path, accents=[{"at": {"grid": "beats", "to": 1.0, "except": [0.47], "except_tolerance_s": 0.05}}])
    assert sp.data["camera"]["accents"][0]["times"] == [0.0, 1.0]


def test_grid_accents_need_beats(tmp_path):
    with pytest.raises(PipelineError, match="beats"):
        dancespec.parse_spec(base(camera={"accents": [{"at": {"grid": "beats"}}]}), tmp_path)


# ---- seams -----------------------------------------------------------------------

def test_a_span_only_seam_is_the_01_formula(tmp_path):
    cam, _ = camera_of(tmp_path, seams=[{"t": 2.0, "span_s": 0.4, "zoom": 1.3, "rotate_deg": 8.0}], cover_rotation=False)
    for t in (1.7, 1.9, 2.0, 2.15, 2.39):
        state = cam.state(t)
        lift = 1.0 - float(dancecamera.timeline.ease("in_out", abs(t - 2.0) / 0.4))
        assert state.seam_zoom == pytest.approx(1 + 0.3 * lift) and state.seam_rotate == pytest.approx(8 * lift)
    assert cam.state(1.6).seam_zoom == 1.0 and cam.state(2.4).seam_rotate == 0.0


def test_a_shaped_seam_is_asymmetric(tmp_path):
    cam, _ = camera_of(tmp_path, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.5, "rotate_deg": 6}], cover_rotation=False)
    assert cam.state(1.7).seam_zoom == 1.0 and cam.state(2.5).seam_zoom == 1.0
    assert cam.state(1.85).seam_zoom == pytest.approx(1 + 0.5 * 0.5 ** 3)
    assert cam.state(2.0).seam_zoom == pytest.approx(1.5) and cam.state(2.0).seam_rotate == pytest.approx(6)
    assert cam.state(2.25).seam_zoom == pytest.approx(1 + 0.5 * 0.5 ** 2.5)


@pytest.mark.parametrize("fps", [12, 24, 30, 60])
@pytest.mark.parametrize("rotate, post", [(6.0, 9.0), (-6.0, -9.0), (2.0, 12.0), (6.0, 6.0), (6.0, 6.5)])
def test_rotation_keeps_going_through_the_cut(tmp_path, fps, rotate, post):
    cam, _ = camera_of(tmp_path, fps, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.5, "rotate_deg": rotate, "rotate_post_deg": post}], cover_rotation=False)
    angle = lambda t: cam.state(t).seam_rotate  # noqa: E731
    dt = 1.0 / fps
    before, after = angle(2.0) - angle(2.0 - dt), angle(2.0 + dt) - angle(2.0)
    assert before * after > 0                                  # 条件 2: 切れ目で向きが反転しない
    assert abs(after - before) <= 0.1 * abs(before)            # 条件 1: 1 コマの変化量の差が 10% 以内
    assert angle(2.0) == pytest.approx(rotate)
    assert angle(2.5) == 0.0 and abs(angle(2.5 - 1e-4)) < 0.01  # post_s の終わりで 0（傾き 0）


def test_a_short_post_lowers_the_peak_instead_of_turning_back(tmp_path):
    seam = {"t": 2.0, "pre_s": 0.3, "post_s": 0.1, "zoom": 1.5, "rotate_deg": 2, "rotate_post_deg": 12}
    cam, sp = camera_of(tmp_path, seams=[seam], cover_rotation=False)
    assert any("頂点" in warning for warning in sp.warnings)
    curve = cam.curves[0]
    values = [curve.at(curve.peak_at * i / 100) for i in range(101)]
    assert all(b >= a - 1e-9 for a, b in zip(values, values[1:])) and curve.peak < 12
    before, after = cam.state(2.0).seam_rotate - cam.state(2.0 - 1 / 30).seam_rotate, cam.state(2.0 + 1 / 30).seam_rotate - cam.state(2.0).seam_rotate
    assert before * after > 0 and abs(after - before) <= 0.1 * abs(before)


def test_rotation_peaks_at_the_post_angle(tmp_path):
    cam, _ = camera_of(tmp_path, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.5, "rotate_deg": 6, "rotate_post_deg": 9}], cover_rotation=False)
    values = [cam.state(2.0 + i * 0.001).seam_rotate for i in range(500)]
    assert max(values) == pytest.approx(9.0, abs=0.01)
    rising = values[:values.index(max(values))]
    assert all(b >= a - 1e-9 for a, b in zip(rising, rising[1:]))


@pytest.mark.parametrize("seam, message", [
    ({"rotate_deg": 6, "rotate_post_deg": 5}, "rotate_post_deg"),
    ({"rotate_deg": 6, "rotate_post_deg": -9}, "同じ符号"),
    ({"rotate_deg": 0, "rotate_post_deg": 9}, "同じ符号"),
    ({"rotate_deg": 6, "rotate_post_deg": 9, "post_s": 0.05}, "3 コマ"),
])
def test_bad_rotation_settings_are_errors(tmp_path, seam, message):
    with pytest.raises(PipelineError, match=message):
        camera_of(tmp_path, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, **seam}])


def test_a_peak_too_close_to_the_cut_is_raised_and_reported(tmp_path):
    _, sp = camera_of(tmp_path, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "rotate_deg": 6, "rotate_post_deg": 6}])
    assert any("頂点" in warning for warning in sp.warnings)


def test_pivot_stays_fixed_while_the_seam_zooms_and_rotates(tmp_path):
    cam, _ = camera_of(tmp_path, seams=[{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.5, "rotate_deg": 6, "pivot": [0.25, 0.75]}], cover_rotation=False)
    matrix = cam.matrix(cam.state(2.0))
    point = np.array([0.25 * 180 - 0.5, 0.75 * 320 - 0.5, 1.0])
    assert matrix @ point == pytest.approx(point[:2])


def test_center_pivot_gives_the_01_matrix(tmp_path):
    cam, _ = camera_of(tmp_path, keys=[{"t": 0, "zoom": 1.2, "center": [0.4, 0.6]}], seams=[{"t": 2.0, "span_s": 0.4, "zoom": 1.3, "rotate_deg": 8}], cover_rotation=False)
    state = cam.state(2.0)
    expected = dancecamera.camera_matrix(1.2 * 1.3, [0.4, 0.6], 8.0, SIZE)
    assert (cam.matrix(state) == expected).all()
    # 別の掛け方（基本 → seams）でも同じ変換になる
    shaken = dancecamera.State(state.zoom, state.center, state.rotate, (1e-9, 0.0), state.seams)
    assert cam.matrix(shaken) == pytest.approx(expected, abs=1e-6)


def test_overlapping_seams_turn_around_their_own_pivots(tmp_path):
    seams = [{"t": 2.0, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.5, "rotate_deg": 6, "pivot": [0.25, 0.75]},
             {"t": 2.1, "pre_s": 0.3, "post_s": 0.5, "zoom": 1.2, "rotate_deg": 4, "pivot": [0.75, 0.25]}]
    cam, _ = camera_of(tmp_path, seams=seams, cover_rotation=False)
    both, first_only = cam.state(2.1), cam.state(2.1)
    first_only.seams = first_only.seams[:1]
    start = lambda at: dancecamera.seam_lift(cam.seams[1], None, at)  # noqa: E731
    assert start(1.8) == (1.0, 0.0)
    matrix = np.vstack([cam.matrix(both), [0, 0, 1]])
    before = np.vstack([cam.matrix(first_only), [0, 0, 1]])
    second = matrix @ np.linalg.inv(before)                     # 2 つ目の seams の分だけを取り出す
    point = np.array([0.75 * 180 - 0.5, 0.25 * 320 - 0.5, 1.0])
    assert second @ point == pytest.approx(point)               # 2 つ目は自分の pivot のまわりで効く


@pytest.mark.parametrize("rotate", [3.0, 9.0, 15.0, -12.0, 120.0, 200.0])
def test_cover_rotation_leaves_no_edge(tmp_path, rotate):
    cam, _ = camera_of(tmp_path, keys=[{"t": 0, "rotate_deg": rotate}])
    matrix = np.vstack([cam.matrix(cam.state(1.0)), [0, 0, 1]])
    inverse = np.linalg.inv(matrix)
    for x, y in ((0, 0), (179, 0), (0, 319), (179, 319)):
        sx, sy, _ = inverse @ np.array([x, y, 1.0])
        assert -0.5 <= sx <= 179.5 and -0.5 <= sy <= 319.5


def test_cover_rotation_only_raises_the_zoom_when_it_is_needed(tmp_path):
    cam, _ = camera_of(tmp_path, keys=[{"t": 0, "zoom": 2.0, "rotate_deg": 3.0}])
    assert cam.view(cam.state(1.0)).boost == 1.0


def test_01_specs_have_no_cover_rotation_and_02_specs_do(tmp_path):
    raw = base(schema_version=dance.SCHEMA_V01)
    assert dancespec.parse_spec(raw, tmp_path).data["camera"]["cover_rotation"] is False
    assert dancespec.parse_spec(base(), tmp_path).data["camera"]["cover_rotation"] is True


# ---- 実描画（ステージ・ブレ・フラッシュ） --------------------------------------------

def write_video(path, frames, fps=12):
    height, width = frames[0].shape[:2]
    fxrender.write_video(path, iter(frames), (width, height), fps, 0, len(frames))


def green(n=4):
    return [np.full((320, 180, 3), (0, 255, 0), np.uint8)] * n


@pytest.fixture
def scene(tmp_path):
    stage = np.zeros((320, 180, 3), np.uint8)
    stage[:, 90:100] = 220       # 縦の線。カメラが動くと幅に出る
    cv2.imwrite(str(tmp_path / "stage.png"), stage)
    write_video(tmp_path / "a.mp4", green())
    return tmp_path


def prepared(root, **changes):
    return compose.prepare(dancespec.parse_spec(base(**changes), root), out=root / "runs")


def rendered(ctx, t):
    try:
        return compose.frame(ctx, t)
    finally:
        ctx.close()


def test_stage_video_maps_frames_with_loop_and_hold(tmp_path):
    frames = [np.full((64, 64, 3), 40 * i, np.uint8) for i in range(4)]
    write_video(tmp_path / "stage.mp4", frames)
    write_video(tmp_path / "a.mp4", green())
    ctx = prepared(tmp_path, stage={"path": "stage.mp4"})
    assert [ctx.stage.frame_number(t) for t in (0.0, 0.3, 0.34, 0.67, 1.0)] == [0, 3, 0, 0, 0]
    ctx.close()
    ctx = prepared(tmp_path, stage={"path": "stage.mp4", "loop": False, "source_start": 1 / 12})
    assert [ctx.stage.frame_number(t) for t in (0.0, 0.2, 2.0)] == [1, 3, 3]
    ctx.close()


def test_stage_video_shows_the_mapped_frame_and_is_covered(tmp_path):
    frames = [np.full((40, 80, 3), 40 * (i + 1), np.uint8) for i in range(4)]
    write_video(tmp_path / "stage.mp4", frames)
    write_video(tmp_path / "a.mp4", green())
    ctx = prepared(tmp_path, stage={"path": "stage.mp4"})
    first, last = compose.frame(ctx, 0.0), compose.frame(ctx, 0.3)
    again = compose.frame(ctx, 0.0)
    ctx.close()
    assert first.shape == (320, 180, 3) and int(first.mean()) == pytest.approx(40, abs=4) and int(last.mean()) == pytest.approx(160, abs=6)
    assert (first == again).all()        # 戻っても同じ絵


def test_a_missing_stage_video_is_reported(tmp_path):
    write_video(tmp_path / "a.mp4", green())
    with pytest.raises(PipelineError, match="ステージの動画"):
        prepared(tmp_path, stage={"path": "stage.mp4"})


def test_parallax_uses_the_fx_formula(tmp_path):
    keys = [{"t": 0.0, "zoom": 1.1, "center": [0.5, 0.5], "ease": "linear"}, {"t": 4.0, "zoom": 1.5, "center": [0.6, 0.4], "ease": "linear"}]
    cam, _ = camera_of(tmp_path, keys=keys, cover_rotation=False)
    path = fxcamera.CameraPath({"keys": keys, "parallax_px": 0, "parallax_dir": [1, 0]}, 4.0, SIZE)
    for p in (0.0, 0.4, 1.3):
        view = cam.view(cam.state(2.0), p)
        z, x, y, _, _ = path.plane_view(2.0, p)
        assert view.zoom == pytest.approx(z) and view.center == pytest.approx([x, y])


def test_parallax_applies_to_the_stage_only(scene):
    camera = {"keys": [{"t": 0, "zoom": 1.0}, {"t": 4, "zoom": 2.0, "center": [0.8, 0.5]}], "cover_rotation": False}
    a = rendered(prepared(scene, camera=camera), 4.0)
    b = rendered(prepared(scene, camera=camera, stage={"path": "stage.png", "parallax": 0.0}), 4.0)
    assert (b[:, 90:100] > 150).all() and not (a[:, 90:100] > 150).all()


def test_blur_only_works_inside_the_window_and_is_deterministic(scene):
    seam = {"t": 2.0, "pre_s": 0.2, "post_s": 0.2, "zoom": 1.3, "rotate_deg": 6, "blur": {"samples": 8, "shutter_frames": 1.0, "peak_shutter_frames": 1.6}}
    plain = {"seams": [{k: v for k, v in seam.items() if k != "blur"}]}
    blurred = {"seams": [seam]}
    ctx = prepared(scene, camera=blurred)
    assert ctx.camera.blur_states(1.7) == [] and ctx.camera.blur_states(2.3) == []
    assert len(ctx.camera.blur_states(1.78)) == 8 and len(ctx.camera.blur_states(2.24)) == 8
    ctx.close()
    assert (rendered(prepared(scene, camera=plain), 1.5) == rendered(prepared(scene, camera=blurred), 1.5)).all()
    a, b = rendered(prepared(scene, camera=plain), 1.95), rendered(prepared(scene, camera=blurred), 1.95)
    assert (a != b).any()
    assert (b == rendered(prepared(scene, camera=blurred), 1.95)).all()


def test_shutter_is_widest_at_the_cut():
    blur = {"shutter_frames": 1.0, "peak_shutter_frames": 1.6}
    assert dancecamera.shutter_s(blur, 0.0, 30) == pytest.approx(1.6 / 30)
    assert dancecamera.shutter_s(blur, 0.04, 30) == pytest.approx((1 + 0.6 * math.exp(-1)) / 30)
    assert dancecamera.shutter_s(blur, -0.04, 30) == dancecamera.shutter_s(blur, 0.04, 30)
    assert dancecamera.shutter_s(blur, 1.0, 30) == pytest.approx(1 / 30, abs=1e-6)


def test_flash_modes_differ_in_the_shadows(scene):
    flash = {"t": 1.0, "strength": 0.2, "decay_s": 0.1}
    lerp = rendered(prepared(scene, flashes=[flash]), 1.0)
    add = rendered(prepared(scene, flashes=[{**flash, "mode": "add_display"}]), 1.0)
    assert 45 <= add[0, 0, 0] <= 56                       # 暗部は 0.2 × 255 だけ持ち上がる
    assert lerp[0, 0, 0] > 2 * add[0, 0, 0]               # 線形光の補間は暗部で強く見える


def test_add_display_is_the_sum_of_the_display_values():
    linear = np.full((2, 2, 3), 0.05, np.float32)
    out = from_srgb_float(to_srgb_float(linear) + np.float32(0.1))
    assert to_srgb_float(out) == pytest.approx(to_srgb_float(linear) + 0.1, abs=1e-5)


def test_timeline_records_the_camera_settings(tmp_path):
    code, _, err = run_cli("dance", "demo", tmp_path, "--width", "180", "--height", "320", "--duration", "3", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", tmp_path / "spec.json", "--quiet")
    assert code == 0, err
    assert result["technical_pass"] is True
    record = json.loads((tmp_path / "dance_runs" / result["run_id"] / "timeline.json").read_text(encoding="utf-8"))
    fields = record["frames_fields"]
    rows = [dict(zip(fields, row)) for row in record["frames"]]
    assert record["camera_seams"][0]["pre_s"] > 0 and record["accents"][0]["times"]
    assert record["flash_info"][0]["mode"] == "add_display"
    assert any(row["blur"] == 8 for row in rows) and any(row["shake_x"] != 0 for row in rows) and any(row["rotate"] != 0 for row in rows)
    assert "camera.accents" in plan.text(tmp_path / "spec.json")


def test_guides_mark_the_seam_pivot(tmp_path):
    code, _, err = run_cli("dance", "demo", tmp_path, "--width", "180", "--height", "320", "--duration", "3", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "stills", tmp_path / "spec.json", "--times", "2.0", "--guides", "--out", tmp_path / "sheets")
    assert code == 0, err
    sheet = cv2.imread(result["path"])
    assert sheet.shape[:2] == (320, 720)
