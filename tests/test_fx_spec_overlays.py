"""fx-shot の overlays（描画の中で重ねる透過レイヤー。FX-LYRIC-1）。素材はすべてプログラムで作る。

- 見えるものが無い時刻は、overlays の無い spec とバイト単位で同じ絵。
- look が無ければ、描いたコマ（8 bit）に mv overlay と同じ式で重ねた値と同じ。look があれば、切り捨ての前の float に重ねてから切り捨てる。
"""
import copy
import json

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, run_cli, spec_dict, write_spec  # noqa: E402
from mv_pipeline import fx  # noqa: E402
from mv_pipeline.fx import look as fxlook, layering, render, runs, spec_layers  # noqa: E402
from mv_pipeline.fx import overlay as fxoverlay  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx.color import to_srgb8  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

W, H, FPS, FRAMES = 160, 90, 12, 24
LOOK = {"grade": {"keys": [[0.0, 0.9, [1.0, 0.95, 1.05], 0.1]]}, "vignette": {}, "grain": {"sigma": 2.0}}


def lyric_sequence(folder, numbers, size=(W, H)):
    """番号のついた透過の連番。各コマは白い箱（位置が番号でずれる）。"""
    folder.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        rgba = np.zeros((size[1], size[0], 4), np.uint8)
        x = 10 + (n % 5) * 4
        rgba[30:60, x:x + 40] = (255, 255, 255, 255)
        rgba[30:60, x + 40:x + 44, 3] = 128                                     # 縁は半透明
        Image.fromarray(rgba, "RGBA").save(folder / f"{n:05d}.png")


def card(path, size=(W, H)):
    rgba = np.zeros((size[1], size[0], 4), np.uint8)
    rgba[10:30, 90:150] = (250, 230, 200, 255)
    Image.fromarray(rgba, "RGBA").save(path)
    return path


def project(tmp_path, overlays, look=None, **changes):
    make_project(tmp_path, W, H)
    data = spec_dict(**changes)
    data["schema_version"] = fx.SCHEMA_VERSION
    data["overlays"] = overlays
    if look:
        data["look"] = look
    return write_spec(tmp_path, data)


def plain_context(spec_path, scale=1.0):
    sp = fxspec.load_spec(spec_path)
    return render.prepare(spec_layers.without(sp), scale)


STYLE = {"tint": [255, 250, 255], "shadow": {}, "glow": {"color": [150, 70, 230], "sigma_px": 6}}


# ---- spec ------------------------------------------------------------------------

def test_overlays_normalize_and_are_checked(tmp_path):
    spec_path = project(tmp_path, [{"path": "ov", "first_frame": 100, "missing": "transparent", "style": STYLE},
                                   {"image": "card.png", "start_s": 0.5, "end_s": 1.5, "fade_in_s": 0.25}])
    data = fxspec.load_spec(spec_path).data
    first, second = data["overlays"]
    assert first["kind"] == "frames" and first["source"] == "ov" and first["first_frame"] == 100 and first["style"]["glow"]["amount"] == 0.9
    assert second["kind"] == "image" and second["start_s"] == 0.5 and second["fade_out_s"] == 0.0
    assert fxspec.load_spec(project(tmp_path, [])).data["overlays"] == []
    for bad, message in [([{"path": "ov", "start_s": 0}], "start_s"), ([{"image": "c.png", "start_s": 1}], "正しくありません"),
                         ([{"path": "../ov"}], "escapes"), ([{"path": "ov", "missing": "transparent"}], "first_frame と一緒")]:
        data = spec_dict(overlays=bad)
        data["schema_version"] = fx.SCHEMA_VERSION
        with pytest.raises(PipelineError, match=message):
            fxspec.parse_spec(data, tmp_path)
    old = spec_dict(overlays=[{"path": "ov"}])                                    # 0.1 は overlays を知らない
    with pytest.raises(PipelineError, match="未知の鍵"):
        fxspec.parse_spec(old, tmp_path)


def test_bad_overlay_inputs_are_refused_before_drawing(tmp_path):
    lyric_sequence(tmp_path / "ov", range(5))
    with pytest.raises(PipelineError, match="ショットの 24 コマ"):
        render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "ov"}])))       # 並びの順なら全コマが要る
    lyric_sequence(tmp_path / "big", range(24), size=(W * 2, H * 2))
    with pytest.raises(PipelineError, match="resize"):
        render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "big"}])))
    assert render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "big", "resize": True}]))).overlays
    with pytest.raises(PipelineError, match="5〜25 のうち 21 コマ"):              # 番号 2〜25 のうち 0〜4 しか無い
        render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "ov", "first_frame": 2}])))
    (tmp_path / "clip.mov").write_bytes(b"not a folder")
    with pytest.raises(PipelineError, match="mv overlay"):
        render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "clip.mov"}])))


def test_each_sequence_frame_is_checked_against_the_output_size(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    Image.new("RGBA", (W + 20, H), (0, 0, 0, 0)).save(tmp_path / "ov" / "00005.png")      # 途中の 1 コマだけ寸法が違う
    ctx = render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "ov"}])))
    render.frame(ctx, 4 / FPS, "base")
    with pytest.raises(PipelineError, match="寸法が合いません"):
        render.frame(ctx, 5 / FPS, "base")


def test_each_sequence_frame_must_keep_its_alpha(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    Image.new("RGB", (W, H), (255, 255, 255)).save(tmp_path / "ov" / "00005.png")             # 途中の 1 コマだけアルファが無い
    ctx = render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "ov"}])))
    render.frame(ctx, 4 / FPS, "base")
    with pytest.raises(PipelineError, match="アルファがありません"):                            # 不透明な白で絵を覆わない
        render.frame(ctx, 5 / FPS, "base")


# ---- 絵 --------------------------------------------------------------------------

@pytest.mark.parametrize("look", [None, LOOK])
def test_times_without_a_visible_overlay_are_byte_identical(tmp_path, look):
    lyric_sequence(tmp_path / "ov", [105, 106, 107])                             # 12 fps で 0.42〜0.58 秒だけ
    spec_path = project(tmp_path, [{"path": "ov", "first_frame": 100, "missing": "transparent", "style": STYLE}], look=look)
    ctx, plain = render.prepare(fxspec.load_spec(spec_path)), plain_context(spec_path)
    for t in (0.0, 0.25, 1.0, 2.0):
        for variant in fx.VARIANTS:
            assert np.array_equal(render.frame(ctx, t, variant), render.frame(plain, t, variant)), (t, variant)
    assert not np.array_equal(render.frame(ctx, 0.5, "both"), render.frame(plain, 0.5, "both"))
    assert "overlays" in ctx.variant_notes()["base"]["features"]


def test_without_look_the_frame_equals_mv_overlay_on_the_drawn_frame(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    spec_path = project(tmp_path, [{"path": "ov"}, {"image": "card.png", "start_s": 0.0, "end_s": 2.0, "style": {"backdrop": {}}}])
    card(tmp_path / "card.png")
    ctx, plain = render.prepare(fxspec.load_spec(spec_path)), plain_context(spec_path)
    for index in (0, 7, 23):
        t = index / FPS
        drawn = render.frame(plain, t, "both")
        with Image.open(tmp_path / "ov" / f"{index:05d}.png") as image:
            lyric = np.asarray(image.convert("RGBA"))
        with Image.open(tmp_path / "card.png") as image:
            still = np.asarray(image.convert("RGBA"))
        expected = layering.apply_layer(drawn.astype(np.float32), lyric, ctx.overlays[0].entry, 1.0, H / 1080.0)
        expected = layering.apply_layer(expected, still, ctx.overlays[1].entry, 1.0, H / 1080.0)
        assert np.array_equal(render.frame(ctx, t, "both"), np.clip(expected + 0.5, 0, 255).astype(np.uint8))
        assert np.array_equal(fxoverlay.composite(drawn, lyric), np.clip(
            layering.apply_layer(drawn.astype(np.float32), lyric, ctx.overlays[0].entry) + 0.5, 0, 255).astype(np.uint8))   # mv overlay の 0.1 と同じ式


def test_with_look_the_overlay_goes_before_the_final_truncation(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    spec_path = project(tmp_path, [{"path": "ov", "style": STYLE}], look=LOOK)
    ctx, plain = render.prepare(fxspec.load_spec(spec_path)), plain_context(spec_path)
    t = 9 / FPS
    linear = render._flat(plain, t, [])
    with Image.open(tmp_path / "ov" / "00009.png") as image:
        lyric = np.asarray(image.convert("RGBA"))
    display = layering.apply_layer(fxlook.apply_float(plain.look, linear, t), lyric, ctx.overlays[0].entry, 1.0, H / 1080.0)
    assert np.array_equal(render.frame(ctx, t, "base"), np.clip(display, 0, 255).astype(np.uint8))
    assert np.array_equal(np.clip(fxlook.apply_float(plain.look, linear, t), 0, 255).astype(np.uint8), fxlook.apply(plain.look, linear, t))
    far = render.frame(ctx, t, "base")[85:, :], render.frame(plain, t, "base")[85:, :]
    assert np.array_equal(*far)                                                   # 重ねの届かない所は look だけの絵と同じ


def test_numbered_frames_follow_the_shot_time_and_cards_their_window(tmp_path):
    lyric_sequence(tmp_path / "ov", range(100, 124))
    card(tmp_path / "card.png")
    spec_path = project(tmp_path, [{"path": "ov", "first_frame": 100},
                                   {"image": "card.png", "start_s": 0.5, "end_s": 1.0, "fade_in_s": 0.25, "fade_out_s": 0.25}])
    ctx = render.prepare(fxspec.load_spec(spec_path))
    lyric, cards = ctx.overlays
    for index in (0, 5, 23):
        with Image.open(tmp_path / "ov" / f"{100 + index:05d}.png") as image:
            assert np.array_equal(lyric.rgba_at(index / FPS), np.asarray(image.convert("RGBA")))
    assert np.array_equal(lyric.rgba_at(2.0), lyric.rgba_at(23 / FPS))            # ショットの終わりの時刻は最後のコマ
    assert [round(layering.opacity("image", cards.entry, t), 4) for t in (0.4, 0.5, 0.625, 0.75, 0.875, 1.0)] == [0.0, 0.0, 0.5, 1.0, 0.5, 0.0]


def test_previews_at_a_smaller_scale_shrink_the_overlays(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    ctx = render.prepare(fxspec.load_spec(project(tmp_path, [{"path": "ov", "style": STYLE}])), scale=0.5)
    image = render.frame(ctx, 0.5, "both")
    assert image.shape[:2] == (ctx.size[1], ctx.size[0]) and ctx.size == render.scaled_size(ctx.spec, 0.5) == (80, 44)
    assert ctx.overlays[0].rgba_at(0.5).shape[:2] == (ctx.size[1], ctx.size[0])            # 連番も描画の大きさへ縮める


# ---- run -------------------------------------------------------------------------

def test_runs_record_the_overlays_and_can_leave_them_out(tmp_path):
    lyric_sequence(tmp_path / "ov", range(100, 124))
    spec_path = project(tmp_path, [{"path": "ov", "first_frame": 100, "style": STYLE}], layers=None, lights=[])
    first = runs.render_run(spec_path, variants="base", grid=False)
    assert first["overlays"] == "applied" and first["technical_pass"]
    manifest = json.loads((tmp_path / "fx_runs" / first["run_id"] / "manifest.json").read_text(encoding="utf-8"))
    record = manifest["overlays"]
    assert record["state"] == "applied" and len(record["layers"][0]["files"]) == FRAMES and record["layers"][0]["files"][0]["number"] == 100
    again = runs.render_run(spec_path, variants="base", grid=False)
    assert again["reused"] is True
    lyric_sequence(tmp_path / "ov", [110])                                        # 同じ番号のコマを描き直しても同じ中身
    img = np.zeros((H, W, 4), np.uint8)
    img[5:9, 5:9] = 255
    Image.fromarray(img, "RGBA").save(tmp_path / "ov" / "00111.png")              # 1 コマだけ変える
    changed = runs.render_run(spec_path, variants="base", grid=False)
    assert changed["reused"] is False and changed["run_id"] != first["run_id"]
    off = runs.render_run(spec_path, variants="base", grid=False, no_overlays=True)
    assert off["overlays"] == "disabled" and off["run_id"] not in (first["run_id"], changed["run_id"])
    sheet = runs.stills(spec_path, [0.5], variants="base")
    assert sheet["overlays"] == "applied"
    assert runs.stills(spec_path, [0.5], variants="base", no_overlays=True)["path"] != sheet["path"]


def test_the_cli_takes_no_overlays(tmp_path):
    lyric_sequence(tmp_path / "ov", range(FRAMES))
    spec_path = project(tmp_path, [{"path": "ov"}], layers=None, lights=[])
    code, result, err = run_cli("fx", "stills", spec_path, "--times", 0.5, "--variants", "base", "--no-overlays")
    assert code == 0, err
    assert result["overlays"] == "disabled"
    plain = copy.deepcopy(spec_dict(layers=None, lights=[]))
    assert "overlays" not in plain
