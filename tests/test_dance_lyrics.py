"""dance-shot/0.2 の歌詞: 書体・層・rows・reveal・enter/exit・glow・follow_camera・timeline の記録。"""
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from PIL import ImageFont  # noqa: E402

from fx_synth import run_cli  # noqa: E402
from mv_pipeline import dance  # noqa: E402
from mv_pipeline.dance import compose, demo, lyrics  # noqa: E402
from mv_pipeline.dance import spec as dancespec  # noqa: E402
from mv_pipeline.fx import render as fxrender  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

SIZE = (1080, 1920)
DT = 1 / 24


def base(**changes):
    raw = {"schema_version": dance.SCHEMA_VERSION, "output": {"width": 1080, "height": 1920, "fps": 24}, "duration_s": 4.0,
           "stage": {"path": "stage.png"}, "performer": {"clips": [{"path": "a.mp4", "start": 0, "end": 4}]}}
    raw.update(changes)
    return raw


def parsed(tmp_path, layers=None, lines=(), **extra):
    return dancespec.parse_spec(base(lyrics={"layers": layers or {}, "lines": list(lines), **extra}), tmp_path).data


def line_of(tmp_path, layer="back", text="AB CD", start=1.0, end=3.0, layers=None, **fields):
    item = {"layer": layer, "text": text, "start": start, "end": end, **fields}
    data = parsed(tmp_path, layers, [item])
    return lyrics.prepare(data, None, SIZE)[0][0]


THREE = [{"text": "ONE", "t": 1.0, "row": 0}, {"text": " TWO", "t": 1.5, "row": 1}, {"text": " THREE", "t": 2.0, "row": 2}]


def canvas():
    return np.zeros((1920, 1080, 3), np.float32)


def painted(line, t):
    out = canvas()
    lyrics.draw(out, line, t, DT)
    return out


def bounds(image):
    ys, xs = np.nonzero(image.sum(2) > 0.05)
    return xs.min(), ys.min(), xs.max() + 1, ys.max() + 1


# ---- 書体 ------------------------------------------------------------------------

def test_a_path_font_becomes_the_default_font(tmp_path):
    data = dancespec.parse_spec(base(lyrics={"font": "a.ttf", "font_index": 2}), tmp_path).data
    assert data["lyrics"]["font"] == "default" and data["lyrics"]["fonts"] == {"default": {"path": "a.ttf", "index": 2}}
    assert dancespec.asset_refs(data)["font:default"] == "a.ttf"


def test_named_fonts_are_chosen_by_layer_and_by_line(tmp_path, monkeypatch):
    bytes_ = ImageFont.load_default(20).font_bytes
    for name in ("display.ttf", "text.ttf"):
        (tmp_path / name).write_bytes(bytes_)
    used = []
    real = ImageFont.truetype
    monkeypatch.setattr(lyrics.ImageFont, "truetype", lambda path, size, index=0: used.append((Path(path).name, index)) or real(path, size))
    fonts = {"display": {"path": "display.ttf"}, "text": {"path": "text.ttf", "index": 0}}
    data = parsed(tmp_path, {"back": {"font": "display"}}, [
        {"layer": "back", "text": "A", "start": 0, "end": 1}, {"layer": "band", "text": "B", "start": 0, "end": 1},
        {"layer": "band", "text": "C", "start": 1, "end": 2, "style": {"font": "display"}}], fonts=fonts, font="text")
    lyrics.prepare(data, lambda rel: tmp_path / rel, SIZE)
    assert used[0][0] == "display.ttf" and {path for path, _ in used} == {"display.ttf", "text.ttf"}


@pytest.mark.parametrize("settings, message", [
    ({"fonts": {"a": {"path": "a.ttf"}}, "font": "b"}, "lyrics.font"),
    ({"layers": {"back": {"font": "x"}}, "fonts": {"a": {"path": "a.ttf"}}}, "書体"),
])
def test_unknown_font_names_are_errors(tmp_path, settings, message):
    settings = {**settings, "lines": [{"layer": "back", "text": "A", "start": 0, "end": 1}]}
    with pytest.raises(PipelineError, match=message):
        dancespec.parse_spec(base(lyrics=settings), tmp_path)


# ---- 層 --------------------------------------------------------------------------

def test_layers_keep_the_written_order_and_defaults(tmp_path):
    data = parsed(tmp_path, {"glowing": {"z": "front"}, "under": {"z": "behind"}})
    assert [layer["name"] for layer in data["lyrics"]["layers"]] == ["back", "band", "glowing", "under"]
    back, band = data["lyrics"]["layers"][:2]
    assert (back["z"], band["z"]) == ("behind", "front") and back["size_px"] == 180 and band["stroke_px"] == 6


def test_other_layers_need_a_z(tmp_path):
    with pytest.raises(PipelineError, match="z"):
        parsed(tmp_path, {"title": {"size_px": 100}})


def test_a_line_on_an_unknown_layer_is_an_error(tmp_path):
    with pytest.raises(PipelineError, match="lyrics.layers"):
        parsed(tmp_path, {}, [{"layer": "nope", "text": "A", "start": 0, "end": 1}])


def test_01_defaults_stay_off_for_new_features(tmp_path):
    raw = base(schema_version=dance.SCHEMA_V01, lyrics={"back": {"size_px": 100}})
    layers = dancespec.parse_spec(raw, tmp_path).data["lyrics"]["layers"]
    assert [layer["name"] for layer in layers] == ["back", "band"] and layers[0]["size_px"] == 100
    assert layers[0]["enter"] is None and layers[0]["reveal"] == "line" and layers[0]["follow_camera"] is False


# ---- rows ------------------------------------------------------------------------

def test_rows_share_one_size_and_stay_inside_the_box(tmp_path):
    layers = {"back": {"box": [0.1, 0.2, 0.9, 0.6], "size_px": 900, "rows": {"gap_em": 0.05}, "stroke_px": 0}}
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers=layers)
    x0, y0, x1, y1 = line.box_px
    assert x0 >= 0.1 * 1080 - 2 and x1 <= 0.9 * 1080 + 2 and y0 >= 0.2 * 1920 - 2 and y1 <= 0.6 * 1920 + 2 and not line.overflow
    heights = [run.fill.shape[0] for run in line.runs]
    assert max(heights) <= 1.05 * min(heights)    # 同じ大きさ（語で字の高さが少し違うだけ）
    tops = sorted(run.y for run in line.runs)
    assert tops[1] - tops[0] > 100 and tops[1] - tops[0] == pytest.approx(tops[2] - tops[1], abs=8)


def test_the_head_space_of_a_row_is_dropped(tmp_path):
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers={"back": {"box": [0.1, 0.2, 0.9, 0.6]}})
    centers = [(b["box_px"][0] + b["box_px"][2]) / 2 for b in line.words]
    assert all(c == pytest.approx(540, abs=3) for c in centers)
    assert [word["text"] for word in line.words] == ["ONE", "TWO", "THREE"]


@pytest.mark.parametrize("align, edges", [("left", ("left", "left", "left")), ("right", ("right", "right", "right")),
                                          ("stagger", ("left", "center", "right"))])
def test_row_alignment(tmp_path, align, edges):
    layers = {"back": {"box": [0.1, 0.2, 0.9, 0.6], "rows": {"align": align}}}
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers=layers)
    left, right = 0.1 * 1080, 0.9 * 1080
    for word, edge in zip(line.words, edges):
        x0, _, x1, _ = word["box_px"]
        if edge == "left":
            assert x0 == pytest.approx(left, abs=2)
        elif edge == "right":
            assert x1 == pytest.approx(right, abs=2)
        else:
            assert (x0 + x1) / 2 == pytest.approx(540, abs=2)


def test_one_row_is_placed_like_01(tmp_path):
    plain = line_of(tmp_path, text="HOLD")
    words = line_of(tmp_path, text="HOLD", words=[{"text": "HOLD", "accent": False}])
    assert plain.box_px == words.box_px


# ---- reveal ----------------------------------------------------------------------

def test_reveal_word_hides_a_word_until_its_time_and_never_moves_it(tmp_path):
    layers = {"back": {"reveal": "word", "fade_in_s": 0, "box": [0.1, 0.2, 0.9, 0.6]}}
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers=layers)
    second = line.words[1]["box_px"]
    region = lambda image: image[second[1]:second[3], second[0]:second[2]]  # noqa: E731
    before, at, later = painted(line, 1.49), painted(line, 1.5), painted(line, 2.5)
    assert region(before).sum() == 0 and region(at).sum() > 0
    assert (region(at) == region(later)).all()
    assert (before.sum(2) > 0).sum() < (at.sum(2) > 0).sum() < (later.sum(2) > 0).sum()


def test_reveal_line_shows_every_word_from_the_start(tmp_path):
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers={"back": {"box": [0.1, 0.2, 0.9, 0.6]}})
    assert (painted(line, 1.1).sum(2) > 0).sum() == (painted(line, 2.5).sum(2) > 0).sum()


# ---- 色 --------------------------------------------------------------------------

def test_line_style_overrides_the_layer(tmp_path):
    line = line_of(tmp_path, style={"color": [255, 0, 0], "fade_in_s": 0}, text="HOLD")
    image = painted(line, 2.0)
    assert image[..., 0].max() > 0.8 and image[..., 1].max() == 0


def test_word_color_replaces_the_accent_from_its_time(tmp_path):
    words = [{"text": "HO", "t": 1.5, "color": [0, 0, 255]}, {"text": "LD", "accent": False}]
    line = line_of(tmp_path, text="HOLD", words=words, layers={"back": {"fade_in_s": 0, "opacity": 1.0}})
    before, after = painted(line, 1.4), painted(line, 1.6)
    word = line.words[0]["box_px"]
    assert before[word[1]:word[3], word[0]:word[2], 1].max() > 0.5
    assert after[word[1]:word[3], word[0]:word[2], 0].max() < 0.05 and after[word[1]:word[3], word[0]:word[2], 2].max() > 0.8


def test_word_color_without_a_time_works_from_the_start(tmp_path):
    line = line_of(tmp_path, text="HOLD", words=[{"text": "HOLD", "color": [255, 0, 0]}], layers={"back": {"fade_in_s": 0, "opacity": 1.0}})
    assert painted(line, 1.0)[..., 1].max() == 0


# ---- enter / exit / glow ---------------------------------------------------------

def test_enter_moves_around_the_word_center_and_settles(tmp_path):
    layers = {"back": {"fade_in_s": 0, "enter": {"scale_from": 1.5, "rise_px": 0, "dur_s": 0.2}}}
    line = line_of(tmp_path, text="HOLD", layers=layers)
    first, settled = bounds(painted(line, 1.0)), bounds(painted(line, 1.5))
    assert first[2] - first[0] > settled[2] - settled[0]
    assert (first[0] + first[2]) / 2 == pytest.approx((settled[0] + settled[2]) / 2, abs=2)
    assert (first[1] + first[3]) / 2 == pytest.approx((settled[1] + settled[3]) / 2, abs=2)
    assert settled == tuple(line.box_px)


def test_enter_rises_into_place(tmp_path):
    layers = {"back": {"fade_in_s": 0, "enter": {"scale_from": 1.0, "rise_px": 100, "dur_s": 0.2}}}
    line = line_of(tmp_path, text="HOLD", layers=layers)
    assert bounds(painted(line, 1.0))[1] < bounds(painted(line, 1.5))[1]


def test_enter_is_per_word_with_reveal_word(tmp_path):
    layers = {"back": {"reveal": "word", "fade_in_s": 0, "enter": {"scale_from": 1.0, "rise_px": 100, "dur_s": 0.2}, "box": [0.1, 0.2, 0.9, 0.6]}}
    line = line_of(tmp_path, text="ONE TWO THREE", words=THREE, layers=layers)
    assert line.move(line.runs[1], 1.5, DT) is not None and line.move(line.runs[0], 1.5, DT) is None


def test_exit_stays_inside_the_line_and_replaces_the_fade(tmp_path):
    layers = {"back": {"exit": {"scale_to": 1.5, "dur_s": 0.3}, "fade_out_s": 0.0}}
    line = line_of(tmp_path, text="HOLD", layers=layers)
    assert line.fall_s == 0.3 and line.fade(2.85, DT) == pytest.approx(0.5) and line.fade(3.0, DT) == 0
    assert painted(line, 3.0).sum() == 0 and painted(line, 2.99).sum() > 0
    grown, steady = bounds(painted(line, 2.97)), bounds(painted(line, 2.0))
    assert grown[2] - grown[0] > steady[2] - steady[0]


def test_glow_adds_light_around_the_text(tmp_path):
    layers = {"back": {"fade_in_s": 0, "glow": {"color": [255, 150, 70], "radius_px": 20, "strength": 1.0}}}
    glowing, plain = painted(line_of(tmp_path, text="HOLD", layers=layers), 2.0), painted(line_of(tmp_path, text="HOLD"), 2.0)
    box = line_of(tmp_path, text="HOLD").box_px
    outside = glowing[box[1] - 12, (box[0] + box[2]) // 2]
    assert outside[0] > 0 and outside[0] > outside[2] and plain[box[1] - 12, (box[0] + box[2]) // 2].sum() == 0


# ---- 合成 ------------------------------------------------------------------------

def write_scene(root):
    cv2.imwrite(str(root / "stage.png"), np.full((96, 54, 3), 20, np.uint8))
    frame = np.full((96, 54, 3), (0, 255, 0), np.uint8)
    fxrender.write_video(root / "a.mp4", iter([frame] * 3), (54, 96), 12, 0, 3)


def small(root, **lyrics_):
    return dancespec.parse_spec(base(output={"width": 270, "height": 480, "fps": 24}, lyrics=lyrics_,
                                     camera=lyrics_.pop("camera", {})), root)


def test_front_layers_draw_over_the_flash_and_behind_layers_under_it(tmp_path):
    write_scene(tmp_path)
    line = {"layer": "top", "text": "HH", "start": 0, "end": 4}
    settings = lambda z: {"layers": {"top": {"z": z, "color": [255, 0, 0], "box": [0.1, 0.3, 0.9, 0.7], "size_px": 300, "stroke_px": 0, "fade_in_s": 0}}, "lines": [line]}  # noqa: E731
    flashes = [{"t": 1.0, "strength": 1.0, "decay_s": 0.5}]
    for z, expected in (("front", True), ("behind", False)):
        sp = dancespec.parse_spec(base(output={"width": 270, "height": 480, "fps": 24}, lyrics=settings(z), flashes=flashes), tmp_path)
        ctx = compose.prepare(sp)
        image = compose.frame(ctx, 1.0)
        ctx.close()
        assert bool(image[..., 1].min() < 40) is expected


def test_layers_draw_in_the_written_order_within_a_z(tmp_path):
    write_scene(tmp_path)
    style = {"box": [0.1, 0.3, 0.9, 0.7], "size_px": 300, "stroke_px": 0, "fade_in_s": 0, "opacity": 1.0}
    settings = {"layers": {"first": {"z": "front", "color": [255, 0, 0], **style}, "second": {"z": "front", "color": [0, 0, 255], **style}},
                "lines": [{"layer": "second", "text": "H", "start": 0, "end": 4}, {"layer": "first", "text": "H", "start": 0, "end": 4}]}
    sp = dancespec.parse_spec(base(output={"width": 270, "height": 480, "fps": 24}, lyrics=settings), tmp_path)
    ctx = compose.prepare(sp)
    image = compose.frame(ctx, 1.0)
    ctx.close()
    assert image[..., 2].max() > 250 and image[image[..., 2] > 250, 0].max() < 60     # 後に書いた層が上


def test_follow_camera_rides_the_camera(tmp_path):
    write_scene(tmp_path)
    settings = lambda follow: {"layers": {"back": {"follow_camera": follow, "fade_in_s": 0}}, "lines": [{"layer": "back", "text": "HOLD", "start": 0, "end": 4}]}  # noqa: E731
    camera = {"keys": [{"t": 0, "zoom": 1.6, "center": [0.3, 0.5]}]}
    frames = {}
    for follow in (False, True):
        sp = dancespec.parse_spec(base(output={"width": 270, "height": 480, "fps": 24}, lyrics=settings(follow), camera=camera), tmp_path)
        ctx = compose.prepare(sp)
        frames[follow] = compose.frame(ctx, 1.0).astype(int)
        box = ctx.lines[0].box_px
        ctx.close()
    assert (np.abs(frames[True] - frames[False]).sum(2) > 30).any()
    bright = lambda image: np.nonzero((image.sum(2) > 300).any(0))[0]  # noqa: E731
    assert bright(frames[False]).min() == pytest.approx(box[0], abs=2) and bright(frames[True]).min() != pytest.approx(box[0], abs=2)


def test_a_01_line_runs_the_old_exact_path(tmp_path):
    """fade・位置・語の切り分けは test_dance.py と互換のテストが見る。ここは 0.1 の行に新しい動きが乗らないこと。"""
    data = dancespec.parse_spec(base(schema_version=dance.SCHEMA_V01, lyrics={"lines": [{"layer": "back", "text": "A", "start": 0, "end": 1}]}), tmp_path).data
    line = lyrics.prepare(data, None, SIZE)[0][0]
    assert line.move(line.runs[0], 0.0, DT) is None and line.runs[0].glow is None and line.sung is True


# ---- timeline --------------------------------------------------------------------

@pytest.fixture(scope="module")
def shot(tmp_path_factory):
    root = tmp_path_factory.mktemp("lyrics")
    code, _, err = run_cli("dance", "demo", root, "--width", "180", "--height", "320", "--duration", "3", "--fps", "12")
    assert code == 0, err
    code, result, err = run_cli("dance", "render", root / "spec.json", "--out", root / "runs", "--quiet")
    assert code == 0, err
    return root, result, read_json(root / "runs" / result["run_id"] / "timeline.json")


def test_timeline_records_layers_sung_and_word_boxes(shot):
    _, result, record = shot
    assert result["technical_pass"] is True
    assert [layer["name"] for layer in record["layers"]] == ["back", "band", "title"]
    title = next(line for line in record["lyrics"] if line["layer"] == "title")
    assert title["sung"] is False and title["reveal"] == "line" and title["exclusive"] is True
    hook = record["lyrics"][0]
    assert hook["reveal"] == "word" and [word["row"] for word in hook["words"]] == [0, 1, 2]
    assert all(len(word["box"]) == 4 and 0 <= word["box"][0] < word["box"][2] <= 1 for word in hook["words"])
    assert hook["fade_out_s"] == 0.3 and hook["fade_in_s"] == 0.14


def test_the_demo_text_avoids_the_default_ui_regions(tmp_path):
    data = dancespec.parse_spec(demo._spec(540, 960, 6.0, 24), tmp_path).data
    assert lyrics.prepare(data, None, (540, 960))[1] == []
