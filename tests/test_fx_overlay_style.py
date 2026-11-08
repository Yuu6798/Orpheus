"""`mv overlay` の 0.2: レイヤーごとの設定（--layers）。仕上げ（色づけ・影・光彩・後光）は、実制作の 2 つの重ねの式
（歌詞の連番に掛けた式と、最後のカードに掛けた式）を書き写した参照実装と float32 の値まで照らし合わせる。素材はプログラムで作る。"""
import json

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import overlay  # noqa: E402
from mv_pipeline.fx.timeline import ease  # noqa: E402
from mv_pipeline.io import PipelineError, read_json  # noqa: E402

SIZE = (96, 64)


def glyphs(size=SIZE, seed=1):
    """白い字の代わり: いくつかの楕円の、縁のやわらかいアルファ（uint8）。"""
    mask = np.zeros((size[1], size[0]), np.uint8)
    for (cx, cy, rx, ry) in [(30, 30, 12, 8), (60, 34, 9, 14), (78, 20, 5, 5)]:
        cv2.ellipse(mask, (cx, cy), (rx, ry), 15, 0, 360, 255, -1)
    return cv2.GaussianBlur(mask, (0, 0), 1.2)


def background(size=SIZE, seed=2):
    rng = np.random.default_rng(seed)
    return rng.uniform(20, 200, (size[1], size[0], 3)).astype(np.float32)


# ---- 実制作の式の書き写し --------------------------------------------------------------

def production_lyric_overlay(f, im_bgra, sc=1.0, core=(255, 250, 255), glow=(150, 70, 230), shadow=0.45, glow_amt=0.9):
    """歌詞の連番（白い単色の字 + アルファ）に掛けた式。im は cv2 で読んだ BGRA。"""
    a = im_bgra[..., 3:4].astype(np.float32) / 255.0
    if a.max() <= 0:
        return f
    lum = (im_bgra[..., :3].astype(np.float32) / 255.0).mean(-1, keepdims=True)
    core, glow = np.array(core, np.float32), np.array(glow, np.float32)
    sh = cv2.GaussianBlur(a[..., 0], (0, 0), 4 * sc)[..., None]
    g = cv2.GaussianBlur(a[..., 0], (0, 0), 14 * sc)[..., None]
    f = f * (1 - shadow * np.clip(sh * 1.5, 0, 1)) + glow * g * glow_amt
    return f * (1 - a) + core * lum * a


def production_card(f, mask, tal, core=(255, 246, 232), glow=(235, 150, 60), halo_amt=0.55, glow_amt=0.75):
    """最後のカード（字のマスク）に掛けた式。tal は出入りの不透明度。"""
    a = mask.astype(np.float32) / 255.0
    g, s = cv2.GaussianBlur(a, (0, 0), 18), cv2.GaussianBlur(a, (0, 0), 5)
    halo = np.clip(cv2.GaussianBlur(cv2.dilate(a, np.ones((5, 5), np.uint8), iterations=3), (0, 0), 26) * 1.6, 0, 1)
    core, glow = np.array(core, np.float32), np.array(glow, np.float32)
    f = f * (1 - halo_amt * halo[..., None] * tal)
    f = f * (1 - 0.7 * np.clip(s * 1.5, 0, 1)[..., None] * tal) + glow * g[..., None] * glow_amt * tal
    return f * (1 - a[..., None] * tal) + core * a[..., None] * tal


def production_card_opacity(t, start, end, hold=False):
    def smooth(x):
        x = min(max(x, 0.0), 1.0)
        return x * x * (3 - 2 * x)
    return min(smooth((t - start) / 1.5), 1.0) * 0.9 if hold else min(smooth((t - start) / 1.5), smooth((end - t) / 1.5))


def options(style=None, **changes):
    return {**overlay.PLAIN, **changes, "style": overlay._normalize_style(style)}


# ---- 仕上げの式 ---------------------------------------------------------------------

def test_the_lyric_style_equals_the_production_formula():
    rng = np.random.default_rng(3)
    rgb = np.clip(240 + rng.integers(-15, 16, (SIZE[1], SIZE[0], 3)), 0, 255).astype(np.uint8)    # ほぼ白で、ばらつきのある字
    rgba = np.dstack([rgb, glyphs()])
    f = background()
    style = {"tint": [255, 250, 255], "shadow": {}, "glow": {"color": [150, 70, 230]}}               # 量・σ・gain は既定値（実制作の値）
    ours = overlay.apply_layer(f.copy(), rgba, options(style))
    theirs = production_lyric_overlay(f.copy(), np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]))
    assert ours.dtype == np.float32 and np.array_equal(ours, theirs)
    empty = rgba.copy()
    empty[..., 3] = 0
    assert overlay.apply_layer(f, empty, options(style)) is f                                        # 透明なコマは何もしない


@pytest.mark.parametrize("tal", [1.0, 0.6, 0.15])
def test_the_card_style_with_a_backdrop_equals_the_production_formula(tal):
    mask = glyphs()
    rgba = np.dstack([np.full((SIZE[1], SIZE[0], 3), 255, np.uint8), mask])
    f = background(seed=5)
    style = {"tint": [255, 246, 232], "backdrop": {}, "shadow": {"amount": 0.7, "sigma_px": 5},
             "glow": {"color": [235, 150, 60], "amount": 0.75, "sigma_px": 18}}
    ours = overlay.apply_layer(f.copy(), rgba, options(style), opacity=tal)
    assert np.array_equal(ours, production_card(f.copy(), mask, tal))


def test_lengths_follow_the_short_side_of_the_output():
    rgba = np.dstack([np.full((SIZE[1], SIZE[0], 3), 255, np.uint8), glyphs()])
    f = background()
    style = {"shadow": {"sigma_px": 4}, "glow": {"color": [150, 70, 230], "sigma_px": 14}}
    half = overlay.apply_layer(f.copy(), rgba, options(style), scale=0.5)                        # 短い辺 540 = 長さは半分
    reference = production_lyric_overlay(f.copy(), np.ascontiguousarray(rgba[..., [2, 1, 0, 3]]), sc=0.5, core=(255, 255, 255))
    white_tint = overlay.apply_layer(f.copy(), rgba, options({**style, "tint": [255, 255, 255]}), scale=0.5)
    assert np.array_equal(white_tint, reference) and np.array_equal(half, reference)                 # 白い字なら tint なしでも同じ色


def test_premultiplied_layers_are_straightened_first():
    rgb = np.full((SIZE[1], SIZE[0], 3), 200, np.uint8)
    alpha = glyphs()
    straight = np.dstack([rgb, alpha])
    premultiplied = np.dstack([(rgb.astype(np.float32) * alpha[..., None] / 255.0 + 0.5).astype(np.uint8), alpha])
    f = background()
    a = overlay.apply_layer(f.copy(), straight, options())
    b = overlay.apply_layer(f.copy(), premultiplied, options(alpha="premultiplied"))
    assert np.abs(a - b).max() < 2.0                                                                # 8 bit に丸めた分だけ違う


def test_card_opacity_follows_the_production_fades():
    card = {"kind": "image", "options": {**overlay.PLAIN, "opacity": 1.0, "start_s": 2.5, "end_s": 11.5, "fade_in_s": 1.5, "fade_out_s": 1.5}}
    hold = {"kind": "image", "options": {**overlay.PLAIN, "opacity": 0.9, "start_s": 233.5, "end_s": 240.4, "fade_in_s": 1.5, "fade_out_s": 0.0}}
    fps = 24
    for index in range(0, 300):
        assert overlay.opacity_at(card, index, fps) == pytest.approx(production_card_opacity(index / fps, 2.5, 11.5) if 2.5 <= index / fps < 11.5 else 0.0)
    for index in range(5600, 5780):
        t = index / fps
        expected = production_card_opacity(t, 233.5, 240.4, hold=True) if 233.5 <= t < 240.4 else 0.0
        assert overlay.opacity_at(hold, index, fps) == pytest.approx(expected)
    assert float(ease("in_out", 0.5)) == 0.5


# ---- --layers の入口 ----------------------------------------------------------------

def write_layers(folder, layers, name="layers.json"):
    (folder / name).write_text(json.dumps({"schema_version": "fx-overlay-layers/0.1", "layers": layers}), encoding="utf-8")
    return folder / name


def numbered_sequence(folder, numbers, size=SIZE):
    folder.mkdir(parents=True, exist_ok=True)
    for n in numbers:
        rgba = np.zeros((size[1], size[0], 4), np.uint8)
        rgba[10:20, 10:20] = (n % 256, 255, 255, 255)                        # 番号を赤に入れる
        Image.fromarray(rgba, "RGBA").save(folder / f"{n:05d}.png")


def base_image(path, color=(40, 80, 160), size=SIZE):
    Image.new("RGB", size, color).save(path)
    return path


def test_first_frame_takes_a_window_of_a_globally_numbered_sequence(tmp_path):
    numbered_sequence(tmp_path / "ov", [100, 101, 103, 104, 105])                            # 102 が無い
    base_image(tmp_path / "base.png")
    entries = overlay.load_layers(write_layers(tmp_path, [{"path": "ov", "first_frame": 101, "missing": "transparent"}]))
    layer = overlay.select_frames(overlay.describe_layer(*entries[0]), 4)
    assert [f["number"] for f in layer["files"]] == [101, 103, 104] and layer["missing_frames"] == 1
    base = {"kind": "image", "path": str(tmp_path / "base.png"), "size": list(SIZE), "frames": None, "fps": 12.0}
    reds = [int(frame[15, 15, 0]) for frame in overlay.frames(base, [layer], 4)]
    assert reds == [101, 40, 103, 104]                                                       # 102 は透明（土台の色）
    strict = overlay.load_layers(write_layers(tmp_path, [{"path": "ov", "first_frame": 101}], "strict.json"))
    with pytest.raises(PipelineError, match="102〜102"):
        overlay.select_frames(overlay.describe_layer(*strict[0]), 4)


def test_a_timed_card_appears_only_in_its_window(tmp_path):
    base_image(tmp_path / "base.png")
    card = np.zeros((SIZE[1], SIZE[0], 4), np.uint8)
    card[20:44, 30:66] = (250, 250, 250, 255)
    Image.fromarray(card, "RGBA").save(tmp_path / "card.png")
    layers = write_layers(tmp_path, [{"image": "card.png", "start_s": 0.5, "end_s": 1.5, "fade_in_s": 0.25,
                                      "style": {"glow": {"color": [255, 120, 40], "sigma_px": 6}}}])
    result = overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers, frames_count=24)
    assert result["technical_pass"] and result["frames"] == 24
    record = read_json(tmp_path / "runs" / result["run_id"] / "manifest.json")["overlay"]
    assert record["layers"][0]["kind"] == "image" and record["layers"][0]["options"]["start_s"] == 0.5
    assert record["layers"][0]["options"]["style"]["glow"] == {"color": [255, 120, 40], "amount": 0.9, "sigma_px": 6}
    base = {"kind": "image", "path": str(tmp_path / "base.png"), "size": list(SIZE), "frames": None, "fps": 12.0}
    layer = overlay.describe_layer(*overlay.load_layers(layers)[0])
    images = list(overlay.frames(base, [layer], 24))
    assert np.array_equal(images[5][30, 40], [40, 80, 160]) and images[12][30, 40, 0] > 200          # 0.42 秒は前、1.0 秒は字
    assert 40 < images[7][30, 40, 0] < images[9][30, 40, 0]                                           # 出だし（0.25 秒）は薄い
    assert np.array_equal(images[18][30, 40], [40, 80, 160])                                         # 1.5 秒以降は消える


def test_plain_layers_in_a_layers_file_match_the_01_composite(tmp_path):
    numbered_sequence(tmp_path / "ov", range(6))
    base_image(tmp_path / "base.png")
    base = {"kind": "image", "path": str(tmp_path / "base.png"), "size": list(SIZE), "frames": None, "fps": 12.0}
    plain = overlay.describe_layer(tmp_path / "ov")
    from_file = overlay.describe_layer(*overlay.load_layers(write_layers(tmp_path, [{"path": "ov"}]))[0])
    for a, b in zip(overlay.frames(base, [plain], 6), overlay.frames(base, [from_file], 6)):
        assert np.array_equal(a, b)


def test_resize_scales_a_layer_with_the_same_aspect(tmp_path):
    big = (SIZE[0] * 2, SIZE[1] * 2)
    numbered_sequence(tmp_path / "big", range(3), size=big)
    base_image(tmp_path / "base.png")
    with pytest.raises(PipelineError, match="resize"):
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "big"], out=tmp_path / "runs", fps=12, quiet=True)
    layers = write_layers(tmp_path, [{"path": "big", "resize": True}])
    result = overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers)
    assert result["technical_pass"] and result["frames"] == 3


def test_resize_space_premultiplied_keeps_the_edges_and_straight_stays_the_production_resize(tmp_path):
    block = np.zeros((2, 2, 4), np.uint8)
    block[0, 0] = (255, 255, 255, 255)                                                       # 白い 1 画素と、透明な黒い 3 画素
    production = cv2.resize(block, (1, 1), interpolation=cv2.INTER_AREA)[0, 0].tolist()
    assert overlay._fit(block, (1, 1), options(resize=True))[0, 0].tolist() == production == [64, 64, 64, 64]   # 既定は実制作と同じ
    weighted = overlay._fit(block, (1, 1), options(resize=True, resize_space="premultiplied"))[0, 0].tolist()
    assert weighted == [255, 255, 255, 64]                                                   # 黒に重ねると 64（straight は 16）
    already = options(resize=True, alpha="premultiplied", resize_space="premultiplied")
    assert overlay._fit(block, (1, 1), already)[0, 0].tolist() == [64, 64, 64, 64]          # 色にアルファが掛かった入力はそのまま
    big = (SIZE[0] * 2, SIZE[1] * 2)
    numbered_sequence(tmp_path / "big", range(3), size=big)
    base_image(tmp_path / "base.png")
    layers = write_layers(tmp_path, [{"path": "big", "resize": True, "resize_space": "premultiplied"}])
    result = overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers)
    manifest = read_json(f"{result['run_dir']}/manifest.json")
    assert result["technical_pass"] and manifest["overlay"]["layers"][0]["options"]["resize_space"] == "premultiplied"


def test_every_frame_of_a_sequence_keeps_the_resize_rules(tmp_path):
    base_image(tmp_path / "base.png")
    numbered_sequence(tmp_path / "mixed", range(3))                                          # 1 枚目は土台と同じ寸法
    Image.fromarray(np.zeros((SIZE[1] * 2, SIZE[0] * 2, 4), np.uint8), "RGBA").save(tmp_path / "mixed" / "00002.png")   # 3 枚目だけ 2 倍
    with pytest.raises(PipelineError, match="寸法が合いません"):                              # --layer（resize なし）は黙って縮めない
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "mixed"], out=tmp_path / "runs", fps=12, quiet=True)
    allowed = write_layers(tmp_path, [{"path": "mixed", "resize": True}])
    result = overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=allowed)
    assert result["technical_pass"] and result["frames"] == 3                                # resize: true で縦横比が同じなら縮める
    Image.fromarray(np.zeros((SIZE[1] * 2, SIZE[0] * 3, 4), np.uint8), "RGBA").save(tmp_path / "mixed" / "00002.png")   # 縦横比が違う
    with pytest.raises(PipelineError, match="縦横比が合いません"):
        overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs2", fps=12, quiet=True, layers_file=allowed)


def test_a_video_layer_can_start_at_a_frame(tmp_path):
    import subprocess

    from mv_pipeline import media
    numbered_sequence(tmp_path / "seq", range(10))
    subprocess.run([media.ffmpeg(), "-y", "-v", "error", "-framerate", "12", "-i", str(tmp_path / "seq" / "%05d.png"),
                    "-c:v", "png", "-pix_fmt", "rgba", str(tmp_path / "layer.mov")], check=True)
    base_image(tmp_path / "base.png")
    entries = overlay.load_layers(write_layers(tmp_path, [{"path": "layer.mov", "first_frame": 7, "missing": "transparent"}]))
    layer = overlay.select_frames(overlay.describe_layer(*entries[0]), 5)
    base = {"kind": "image", "path": str(tmp_path / "base.png"), "size": list(SIZE), "frames": None, "fps": 12.0}
    assert [int(frame[15, 15, 0]) for frame in overlay.frames(base, [layer], 5)] == [7, 8, 9, 40, 40]   # 10 コマ目からは無い


@pytest.mark.parametrize("layers, message", [
    ([{"image": "card.png", "start_s": 1}], "正しくありません"),
    ([{"image": "card.png", "start_s": 2, "end_s": 1}], "end_s"),
    ([{"image": "card.png", "start_s": 0, "end_s": 1, "first_frame": 3}], "first_frame"),
    ([{"path": "ov", "start_s": 0}], "start_s"),
    ([{"path": "ov", "missing": "transparent"}], "first_frame と一緒"),
    ([{"path": "ov", "style": {"glow": {}}}], "正しくありません"),
    ([{"path": "ov", "resize": True, "resize_space": "linear"}], "正しくありません"),
    ([{"path": "../ov"}], "escapes"),
])
def test_bad_layers_files_are_refused(tmp_path, layers, message):
    with pytest.raises(PipelineError, match=message):
        overlay.load_layers(write_layers(tmp_path, layers))


def test_the_inputs_must_fix_the_frame_count(tmp_path):
    numbered_sequence(tmp_path / "ov", range(100, 104))
    base_image(tmp_path / "base.png")
    layers = write_layers(tmp_path, [{"path": "ov", "first_frame": 100}])
    with pytest.raises(PipelineError, match="--frames"):
        overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers)
    with pytest.raises(PipelineError, match="一緒に使えません"):
        overlay.run_overlay(tmp_path / "base.png", [tmp_path / "ov"], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers)
    result = overlay.run_overlay(tmp_path / "base.png", [], out=tmp_path / "runs", fps=12, quiet=True, layers_file=layers, frames_count=4)
    assert result["technical_pass"] and result["frames"] == 4


def test_the_overlay_command_takes_a_layers_file(tmp_path):
    from fx_synth import run_cli
    numbered_sequence(tmp_path / "ov", range(4))
    base_image(tmp_path / "base.png")
    layers = write_layers(tmp_path, [{"path": "ov", "style": {"shadow": {}, "glow": {"color": [150, 70, 230]}}}])
    code, result, err = run_cli("overlay", "--base", tmp_path / "base.png", "--layers", layers, "--fps", 12, "--out", tmp_path / "runs", "--quiet")
    assert code == 0, err
    assert result["technical_pass"] is True and result["frames"] == 4
