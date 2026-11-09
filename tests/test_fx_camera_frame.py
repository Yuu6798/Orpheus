"""fx-shot/0.3 のカメラの視野: camera.frame（cover / source）・clamp・punch と、顔の枠（faces → 画面の座標 → fx-timeline の faces.json）。

視野・寄り・顔の写し方は、実制作の縦のレンダラの式を写した関数（private_*）と比べる（拍の脈は除く）。
"""
import json
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

import fx_synth  # noqa: E402
from mv_pipeline.fx import camera, render, sequence  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx.color import to_srgb8  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402


# ---- 実制作の縦のレンダラ（写し。拍の脈 beat_pulse は 0） ---------------------------------------

def private_ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def private_ease_out(x):
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def private_cam(sh, t):
    u = private_ease((t - sh["start"]) / max(sh["end"] - sh["start"], 1e-3))
    p = 1.0
    if sh.get("punch"):
        amt, dur = sh["punch"]
        v = (t - sh["start"]) / dur
        if v < 1:
            p *= 1 - amt * (1 - private_ease_out(v))
    z = max(1.0, (sh["z"][0] + (sh["z"][1] - sh["z"][0]) * u) * p)
    cx = sh["c"][0][0] + (sh["c"][1][0] - sh["c"][0][0]) * u
    cy = sh["c"][0][1] + (sh["c"][1][1] - sh["c"][0][1]) * u
    return u, z, cx, cy


def private_window(sw, sh, z, cx, cy, width, height):
    """crop_M の視野 (x0, y0, vw, vh)（元画像の画素）。"""
    cover = max(width / sw, height / sh)
    vw, vh = width / (cover * z), height / (cover * z)
    x0 = min(max(cx * sw - vw / 2, 0), sw - vw)
    y0 = min(max(cy * sh - vh / 2, 0), sh - vh)
    return x0, y0, vw, vh


def private_point(window, sw, sh, base, x, y, width):
    """Geo.pt（視差なし）: 基準座標 (x, y) → 画面の画素。"""
    x0, y0, vw, _ = window
    s = vw / width
    return (x / base[0] * sw - x0) / s, (y / base[1] * sh - y0) / s


def camera_dict(shot, length, frame="source", clamp=True):
    (z0, z1), (c0, c1) = shot["z"], shot["c"]
    data = {"keys": [{"t": 0.0, "zoom": z0, "center": list(c0)}, {"t": length, "zoom": z1, "center": list(c1), "ease": "in_out"}],
            "frame": frame, "clamp": clamp}
    if shot.get("punch"):
        data["punch"] = {"amount": shot["punch"][0], "duration_s": shot["punch"][1]}
    return data


def path_for(camera_raw, length, size, plate):
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1], "fps": 30}, "duration_s": length,
           "assets": {"base": "base.png"}, "camera": camera_raw}
    data = fxspec.parse_spec(raw, ".").data
    return camera.CameraPath(data["camera"], length, size, (0.0, 0.0, 1.0, 1.0), plate=plate)


SHOTS = [  # 縦 1080×1920 に横 1672×941 を写すショット（寄り・中心・押し込みは実制作の設定の形）
    {"z": [1.66, 1.7], "c": [[0.5, 0.9], [0.5, 0.9]], "punch": [0.1, 0.4], "length": 1.46},
    {"z": [1.0, 1.06], "c": [[0.55, 0.5], [0.56, 0.5]], "length": 1.38},
    {"z": [1.0, 1.0], "c": [[0.36, 0.5], [0.7, 0.5]], "length": 2.76},
    {"z": [1.6, 1.64], "c": [[0.54, 0.9], [0.53, 0.9]], "length": 2.77},
    {"z": [1.0, 1.02], "c": [[0.02, 0.45], [0.98, 0.42]], "punch": [0.3, 1.0], "length": 2.0},
]


@pytest.mark.parametrize("shot", SHOTS)
def test_source_view_zoom_and_clamp_equal_the_private_crop(shot):
    sw, sh, size = 1672, 941, (1080, 1920)
    length = shot["length"]
    path = path_for(camera_dict(shot, length), length, size, (sw, sh))
    private = dict(shot, start=0.0, end=length)
    for n in range(int(length * 30) + 1):
        t = n / 30
        _, z, cx, cy = private_cam(private, t)
        theirs = private_window(sw, sh, z, cx, cy, *size)
        zoom, ux, uy = path.at(t)
        half_w, half_h = path.half_view(zoom)
        mine = ((ux - half_w) * sw, (uy - half_h) * sh, 2 * half_w * sw, 2 * half_h * sh)
        assert zoom == pytest.approx(z, rel=1e-12, abs=1e-12)
        assert mine == pytest.approx(theirs, rel=1e-9, abs=1e-6), t


def test_faces_map_like_the_private_geometry():
    sw, sh, size, base = 1672, 941, (1080, 1920), (1672, 941)
    shot = SHOTS[0]
    path = path_for(camera_dict(shot, shot["length"]), shot["length"], size, (sw, sh))
    face = [760, 540, 925, 700]
    for t in (0.0, 0.2, 0.7, 1.4):
        _, z, cx, cy = private_cam(dict(shot, start=0.0, end=shot["length"]), t)
        window = private_window(sw, sh, z, cx, cy, *size)
        (x0, y0), (x1, y1) = private_point(window, sw, sh, base, face[0], face[1], size[0]), private_point(window, sw, sh, base, face[2], face[3], size[0])
        mine = camera.screen_box(path, t, [face[0] / base[0], face[1] / base[1], face[2] / base[0], face[3] / base[1]])
        assert [mine[0] * size[0], mine[1] * size[1], mine[2] * size[0], mine[3] * size[1]] == pytest.approx([x0, y0, x1, y1], abs=0.02)


def test_punch_is_the_private_envelope_and_zoom_stays_at_least_one():
    punch = {"amount": 0.25, "duration_s": 0.5}
    for t in (-0.1, 0.0, 0.1, 0.25, 0.49, 0.5, 2.0):
        v = t / 0.5
        expected = 1 - 0.25 * (1 - private_ease_out(v)) if v < 1 else 1.0
        assert camera.punch_factor(punch, t) == expected
    path = path_for({"keys": [{"t": 0, "zoom": 1.0}], "punch": punch}, 1.0, (160, 90), None)
    assert path.at(0.0)[0] == 1.0 and path.at(0.25)[0] == 1.0
    zoomed = path_for({"keys": [{"t": 0, "zoom": 2.0}], "punch": punch}, 1.0, (160, 90), None)
    assert zoomed.at(0.0)[0] == pytest.approx(1.5) and zoomed.at(0.6)[0] == 2.0


def landscape(directory, width=320, height=180):
    """左半分が赤・右半分が青、右端の 10% だけ緑の横長の元画像。"""
    image = np.zeros((height, width, 3), np.uint8)
    image[:, : width // 2] = (200, 30, 30)
    image[:, width // 2:] = (30, 30, 200)
    image[:, width - width // 10:] = (30, 200, 30)
    cv2.imencode(".png", cv2.cvtColor(image, cv2.COLOR_RGB2BGR))[1].tofile(str(directory / "base.png"))


def vertical_spec(directory, center_x, frame="source", clamp=True, **extra):
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": 12}, "duration_s": 1.0,
           "assets": {"base": "base.png"}, "camera": {"keys": [{"t": 0, "zoom": 1.0, "center": [center_x, 0.5]}], "frame": frame, "clamp": clamp}}
    raw.update(extra)
    path = directory / f"spec_{frame}_{center_x}_{clamp}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    return fxspec.load_spec(path)


def test_source_frame_pans_a_landscape_still_to_both_edges_in_a_vertical_output(tmp_path):
    landscape(tmp_path)
    reds, greens = {}, {}
    for center in (0.05, 0.95):
        sp = vertical_spec(tmp_path, center)
        ctx = render.prepare(sp)
        image = render.frame(ctx, 0.0, "base")
        assert image.shape == (160, 90, 3) and ctx.assets.plate_size == (284, 160) and ctx.warnings == []
        expected = to_srgb8(cv2.warpAffine(ctx.assets.plate, ctx.path.matrix(0.0, 1.0), (90, 160), flags=cv2.INTER_LINEAR,
                                           borderMode=cv2.BORDER_REPLICATE))
        assert np.array_equal(image, expected)
        reds[center] = float((image[..., 0] > 150).mean())
        greens[center] = float((image[..., 1] > 150).mean())
    assert reds[0.05] == 1.0 and greens[0.05] == 0.0            # 左端の視野は全部赤
    assert reds[0.95] == 0.0 and greens[0.95] > 0.2             # 右端の視野には右端の緑が入る
    cover = render.prepare(vertical_spec(tmp_path, 0.95, frame="cover", clamp=False))
    assert float((render.frame(cover, 0.0, "base")[..., 1] > 150).mean()) == 0.0      # cover の中央の帯からは届かない
    assert any("はみ出します" in w for w in cover.warnings)


def test_views_outside_the_image_warn_unless_clamped(tmp_path):
    landscape(tmp_path)
    loose = render.prepare(vertical_spec(tmp_path, 0.95, clamp=False))
    assert any("はみ出します" in w for w in loose.warnings)
    assert loose.path.at(0.0)[1] == 0.95
    assert not any("はみ出します" in w for w in render.prepare(vertical_spec(tmp_path, 0.95)).warnings)
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 160, "height": 90, "fps": 12}, "duration_s": 1.0, "assets": {"base": "base.png"},
           "camera": {"keys": [{"t": 0, "zoom": 1.5, "center": [0.95, 0.1]}], "clamp": True}}
    (tmp_path / "cover.json").write_text(json.dumps(raw), encoding="utf-8")
    ctx = render.prepare(fxspec.load_spec(tmp_path / "cover.json"))
    zoom, cx, cy = ctx.path.at(0.0)
    assert ctx.warnings == [] and (cx, cy) == pytest.approx((1 - 0.5 / 1.5, 0.5 / 1.5))


def depth_spec(directory, depth, center_x, shift, size):
    """右へ depth_shift する source の spec（深度は灰色の 8 bit。2〜98 パーセンタイルで 0..1 に伸ばされる）。"""
    cv2.imencode(".png", depth)[1].tofile(str(directory / "depth.png"))
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1], "fps": 12}, "duration_s": 1.0,
           "assets": {"base": "base.png", "depth": {"path": "depth.png"}},
           "camera": {"keys": [{"t": 0, "zoom": 1.0, "center": [center_x, 0.5]},
                               {"t": 1, "zoom": 1.0, "center": [center_x, 0.5], "depth_shift": [shift, 0]}], "frame": "source", "clamp": True}}
    path = directory / f"depth_{center_x}_{shift}_{size[0]}x{size[1]}.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    ctx = render.prepare(fxspec.load_spec(path))
    matrix = ctx.path.matrix(1.0, 1.0)                      # 最後のコマが最も大きく動く
    map_x, _ = camera.sample_maps(matrix, size, camera.warp(ctx.assets.depth, matrix, size), ctx.path.depth_motion(1.0))
    return ctx, (float(map_x.min()), float(map_x.max())), any("深度視差の変位" in w for w in ctx.warnings)


def test_the_depth_edge_check_compares_with_the_plate_in_source_frame(tmp_path):
    # source の板は出力と大きさが違う。端の検査は、参照する位置（板の画素）を板の大きさで比べる（出力の大きさではない）
    landscape(tmp_path)                                                      # 320×180 → 縦 90×160 の板は 284×160
    ramp = np.tile(np.linspace(255, 0, 320).astype(np.uint8), (180, 1))     # 左が手前
    ctx, (low, high), warned = depth_spec(tmp_path, ramp, 0.5, 400, (90, 160))
    assert ctx.assets.plate_size == (284, 160) and 90 < low and high < 200 and not warned          # 板の中だけを引く
    ctx, (low, high), warned = depth_spec(tmp_path, ramp, 0.95, 40, (90, 160))
    assert high > 283 and warned                                                                # 板の右端の外を引く


def test_the_depth_edge_check_sees_past_a_plate_narrower_than_the_output(tmp_path):
    square = np.zeros((100, 100, 3), np.uint8)                              # 横 160×90 には 100×100 の板（出力より幅が狭い）
    cv2.imencode(".png", square)[1].tofile(str(tmp_path / "base.png"))
    steps = np.full((100, 100), 255, np.uint8)
    steps[:, :20], steps[:, 80:] = 128, 0                                    # 左端は動かない深さ、右端だけ奥
    ctx, (low, high), warned = depth_spec(tmp_path, steps, 0.5, 400, (160, 90))
    assert ctx.assets.plate_size == (100, 100) and low > -0.5 and high > 105 and warned            # 板の右端の外を 6 画素以上引く


@pytest.mark.parametrize("extra, message", [
    ({"lights": [{"id": "l", "position": [0.5, 0.5], "intensity": [{"t": 0, "v": 1}]}]}, "lights"),
    ({"layers": {"planes": [], "effects": [{"id": "fog", "type": "fog"}]}}, "layers"),
    ({"faces": [{"box": [0.5, 0.2, 0.4, 0.3]}]}, "faces\\[0\\].box"),
])
def test_source_frame_refuses_layers_and_lights_and_faces_need_ordered_boxes(tmp_path, extra, message):
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": 12}, "duration_s": 1.0, "assets": {"base": "base.png"},
           "camera": {"keys": [{"t": 0}], "frame": "source"}}
    raw.update(extra)
    with pytest.raises(PipelineError, match=message):
        fxspec.parse_spec(raw, tmp_path)


def test_older_specs_read_with_cover_defaults_and_no_faces(tmp_path):
    raw = fx_synth.spec_dict()
    data = fxspec.parse_spec(raw, tmp_path).data
    assert data["schema_version"] == "fx-shot/0.3" and data["faces"] == []
    assert (data["camera"]["frame"], data["camera"]["clamp"], data["camera"]["punch"]) == ("cover", False, None)
    raw["schema_version"] = "fx-shot/0.2"
    assert fxspec.parse_spec(raw, tmp_path).data["camera"]["frame"] == "cover"
    raw["camera"]["clamp"] = True                       # 0.2 の spec には 0.3 の項目を書けない
    with pytest.raises(PipelineError, match="未知の鍵"):
        fxspec.parse_spec(raw, tmp_path)


def test_timeline_writes_faces_for_gate_from_spec_and_video_shots(tmp_path):
    from mv_pipeline.fx import render as fxrender
    from mv_pipeline.gate import checks
    landscape(tmp_path)
    spec = {"schema_version": "fx-shot/0.3", "output": {"width": 90, "height": 160, "fps": 8}, "duration_s": 1.0, "assets": {"base": "base.png"},
            "camera": {"keys": [{"t": 0, "zoom": 1.2, "center": [0.3, 0.5]}, {"t": 1.0, "zoom": 1.2, "center": [0.4, 0.5]}], "frame": "source", "clamp": True},
            "faces": [{"box": [0.25, 0.3, 0.35, 0.45]}]}
    (tmp_path / "a.json").write_text(json.dumps(spec), encoding="utf-8")
    frames = [np.full((90, 160, 3), 60 + 10 * k, np.uint8) for k in range(10)]
    fxrender.write_video(tmp_path / "clip.mp4", iter(frames), (160, 90), 10, 18, 10)
    raw = {"schema_version": "fx-timeline/0.1", "output": {"width": 90, "height": 160, "fps": 8}, "duration_s": 2.0,
           "shots": [{"spec": "a.json", "start": 0},
                     {"video": {"path": "clip.mp4"}, "start": 1.0, "camera": {"keys": [{"t": 0, "zoom": 1.0, "center": [0.8, 0.5]}], "frame": "source",
                                                                              "clamp": True}, "faces": [{"box": [0.7, 0.2, 0.9, 0.4], "role": "other", "estimated": False}]}]}
    path = tmp_path / "timeline.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    summary = sequence.render_run(path, quiet=True)
    assert summary["technical_pass"] and summary["faces"]
    faces = json.loads(open(summary["faces"], encoding="utf-8").read())
    assert len(faces) == 16 and all(0 <= v <= 1 for f in faces for v in f["box"])
    first, last = faces[0], faces[-1]
    assert first["role"] == "main" and first["estimated"] is True and first["start"] == 0.0 and first["end"] == 0.125
    assert last["role"] == "other" and last["estimated"] is False
    ctx = sequence.Context(sequence.load_timeline(path))
    try:
        assert ctx.shot(1).plate_size == (160, 90)
        expected = camera.screen_box(ctx.shot(1).path, 0.875, [0.7, 0.2, 0.9, 0.4])
    finally:
        ctx.close()
    assert last["box"] == pytest.approx([min(max(v, 0.0), 1.0) for v in expected], abs=1e-5)
    lyric = {"layer": "main", "text": "x", "start": 0.0, "end": 0.5, "fade_in_s": 0.0, "fade_out_s": 0.0, "box": first["box"]}
    report = checks.check_faces({"fps": 8, "lyrics": [lyric], "layers": []}, faces)
    assert report["status"] == "warn" and "推定" in report["summary"]           # 推定の顔との重なりは要確認（不合格にしない）
    assert sequence.render_run(path, quiet=True)["reused"]


def test_spec_shots_take_faces_from_the_spec(tmp_path):
    fx_synth.make_scene(tmp_path)
    (tmp_path / "a.json").write_text(json.dumps(fx_synth.spec_dict()), encoding="utf-8")
    raw = {"schema_version": "fx-timeline/0.1", "output": {"width": 160, "height": 90, "fps": 12}, "duration_s": 1.0,
           "shots": [{"spec": "a.json", "start": 0, "faces": [{"box": [0.1, 0.1, 0.2, 0.2]}]}]}
    with pytest.raises(PipelineError, match="faces は spec のショットには使えません"):
        sequence.parse_timeline(raw, tmp_path)


def test_ease_out_clamps_its_input():
    assert camera.ease_out(0.5) == 0.875 and camera.ease_out(2.0) == 1.0 and math.isclose(camera.ease_out(-1), 0.0)
