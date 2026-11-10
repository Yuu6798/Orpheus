"""目盛り（`mv fx guides` と `mv fx stills --guides`）と、静止画シートの基準の列。素材はすべてプログラムで作る。

元画像の格子は、実制作の格子の台本（正規化座標の線と値・幅 1600 に縮尺）を書き写した参照実装と画素まで照らし合わせる。
"""
import io
import json

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, run_cli, spec_dict, write_spec  # noqa: E402
from mv_pipeline import sheet  # noqa: E402
from mv_pipeline.fx import guides, render, runs  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402


def production_grid(bgr, *crop):
    """実制作の格子の台本（書き写し）。bgr は cv2 で読んだ画像。"""
    h, w = bgr.shape[:2]
    x0, y0, x1, y1 = (float(v) for v in crop) if crop else (0.0, 0.0, 1.0, 1.0)
    roi = bgr[int(y0 * h):int(y1 * h), int(x0 * w):int(x1 * w)]
    scale = 1600 / roi.shape[1]
    roi = cv2.resize(roi, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    rh, rw = roi.shape[:2]
    step = 0.05 if (x1 - x0) > 0.5 else 0.01 if (x1 - x0) <= 0.15 else 0.02
    for v in np.arange(0, 1.0001, step):
        if x0 <= v <= x1:
            x = int((v - x0) / (x1 - x0) * rw)
            cv2.line(roi, (x, 0), (x, rh), (0, 255, 0), 1)
            cv2.putText(roi, f"{v:.2f}", (x + 2, 14), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 0), 1)
        if y0 <= v <= y1:
            y = int((v - y0) / (y1 - y0) * rh)
            cv2.line(roi, (0, y), (rw, y), (0, 255, 255), 1)
            cv2.putText(roi, f"{v:.2f}", (2, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 255, 255), 1)
    return roi


def noisy_rgb(width=400, height=300, seed=5):
    return np.random.default_rng(seed).integers(0, 256, (height, width, 3), dtype=np.uint8)


@pytest.mark.parametrize("crop", [(), (0.2, 0.1, 0.6, 0.5), (0.4, 0.4, 0.5, 0.48), (0.0, 0.25, 0.3, 1.0)])
def test_the_image_grid_equals_the_production_grid_pixel_for_pixel(crop):
    rgb = noisy_rgb()
    ours, info = guides.image_grid(rgb, crop or None)
    theirs = production_grid(np.ascontiguousarray(rgb[..., ::-1]), *crop)[..., ::-1]
    assert ours.shape == theirs.shape and np.array_equal(ours, theirs)
    assert info["size"] == [1600, ours.shape[0]] and info["units"] == "norm"
    assert info["step"] == ({(): 0.05, (0.2, 0.1, 0.6, 0.5): 0.02, (0.4, 0.4, 0.5, 0.48): 0.01, (0.0, 0.25, 0.3, 1.0): 0.02}[crop])


def test_pixel_units_draw_a_line_every_step_in_image_pixels():
    black = np.zeros((480, 640, 3), np.uint8)
    out, info = guides.image_grid(black, units="px", width=640)                       # 縮尺 1
    assert info["step"] == 100.0 and info["crop"] == [0.0, 0.0, 640.0, 480.0]
    for x in (100, 200, 600):
        assert tuple(out[240, x]) == guides.X_COLOR and tuple(out[240, x + 1]) == (0, 0, 0)
    for y in (100, 300):
        assert tuple(out[y, 330]) == guides.Y_COLOR
    cropped, info = guides.image_grid(black, crop=(100, 100, 420, 340), units="px", step=50, width=640)
    assert info["size"] == [640, 480] and tuple(cropped[240, 100]) == guides.X_COLOR      # 150 px の線は (150 − 100)·2 = 100
    with pytest.raises(PipelineError, match="--crop"):
        guides.image_grid(black, crop=(0, 0, 700, 100), units="px")
    with pytest.raises(PipelineError, match="--crop"):
        guides.image_grid(black, crop=(0.5, 0.2, 0.4, 0.6))
    with pytest.raises(PipelineError, match="--step"):
        guides.image_grid(black, step=0)
    with pytest.raises(PipelineError, match="units"):
        guides.image_grid(black, units="cm")


def test_viewable_images_turn_depth_and_float_into_gray(tmp_path):
    Image.fromarray(np.full((4, 6), 65535, np.uint16)).save(tmp_path / "d16.png")
    Image.fromarray(np.full((4, 6), 0.5, np.float32)).save(tmp_path / "f.tif")
    rgba = np.zeros((4, 6, 4), np.uint8)
    rgba[..., 0], rgba[..., 3] = 200, 0
    Image.fromarray(rgba).save(tmp_path / "a.png")
    assert guides.load_viewable(tmp_path / "d16.png")[0, 0].tolist() == [255, 255, 255]
    assert guides.load_viewable(tmp_path / "f.tif")[0, 0].tolist() == [128, 128, 128]
    assert guides.load_viewable(tmp_path / "a.png")[0, 0].tolist() == [200, 0, 0]          # アルファは捨てる（cv2 で読むのと同じ）


def test_the_guides_command_writes_the_png_and_reports_the_settings(tmp_path):
    Image.fromarray(noisy_rgb()).save(tmp_path / "base.png")
    code, result, err = run_cli("fx", "guides", tmp_path / "base.png", "--out", tmp_path / "g.png", "--crop", 0.1, 0.1, 0.5, 0.4)
    assert code == 0, err
    assert result["step"] == 0.02 and result["size"][0] == 1600 and result["source_size"] == [400, 300]
    with Image.open(tmp_path / "g.png") as image:
        assert image.size == tuple(result["size"])
    code, _, err = run_cli("fx", "guides", tmp_path / "base.png", "--out", tmp_path / "g2.png", "--width", 9)
    assert code == 1 and "--width" in err


def test_the_guides_command_picks_the_format_from_the_extension(tmp_path):
    Image.fromarray(noisy_rgb()).save(tmp_path / "base.png")
    results = {}
    for name in ("g.png", "g.jpg", "g.jpeg", "u.JPG"):
        code, results[name], err = run_cli("fx", "guides", tmp_path / "base.png", "--out", tmp_path / name, "--width", 400)
        assert code == 0, err
    assert results["g.png"]["format"] == "png" and (tmp_path / "g.png").read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    with Image.open(tmp_path / "g.png") as image:
        assert np.array_equal(np.asarray(image), guides.image_grid(noisy_rgb(), width=400)[0])
        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=90)
    for name in ("g.jpg", "g.jpeg", "u.JPG"):                                          # 以前は .jpg の名前で PNG の中身を書いていた
        assert results[name]["format"] == "jpeg" and (tmp_path / name).read_bytes()[:2] == b"\xff\xd8"
        assert (tmp_path / name).read_bytes() == buffer.getvalue()                    # 同じ絵の JPEG（品質 90）
        with Image.open(tmp_path / name) as image:
            assert image.format == "JPEG" and list(image.size) == results[name]["size"]
    for name in ("g.webp", "g.tif", "g"):
        code, _, err = run_cli("fx", "guides", tmp_path / "base.png", "--out", tmp_path / name)
        assert code == 1 and "拡張子" in err and not (tmp_path / name).exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_the_sheet_crop_cuts_the_same_pixels_as_the_guides_crop():
    rgb = noisy_rgb()                                                                 # 400×300
    for crop, units in (((0.2, 0.1, 0.6, 0.5), "norm"), ((0.333, 0.25, 0.9, 1.0), "norm"), ((0.0, 0.0, 1.0, 1.0), "norm"),
                        ((37, 20, 201, 133), "px"), ((0.5, 10.9, 399.5, 299.2), "px")):
        x0, y0, x1, y1 = sheet.crop_box(crop, units, (400, 300))
        step = 1.0 if units == "norm" else 10000                                      # 線は端の 0 と 1 だけ
        grid, info = guides.image_grid(rgb, crop, units, step, width=x1 - x0)         # 範囲の幅と同じ幅 = 縮尺 1
        assert info["size"] == [x1 - x0, y1 - y0]
        untouched = (grid == rgb[y0:y1, x0:x1]).all(axis=2)                           # 線と字の画素のほかは、同じ範囲の元の画素
        assert untouched.mean() > 0.9, (crop, units, untouched.mean())


# ---- 描いたコマに重ねる印 -----------------------------------------------------------------

def context(tmp_path, **changes):
    make_project(tmp_path, 160, 90)
    return render.prepare(fxspec.load_spec(write_spec(tmp_path, spec_dict(**changes))))


def test_the_grid_follows_the_cover_crop_and_the_camera(tmp_path):
    ctx = context(tmp_path, camera={"keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5]}]})
    plain = render.frame(ctx, 0.0, "base")
    out = guides.frame_guides(ctx, 0.0, plain, ("grid",), 0.25)
    assert not np.array_equal(out, plain) and np.array_equal(plain, render.frame(ctx, 0.0, "base"))   # 元のコマは変えない
    assert tuple(out[60, 80]) == guides.X_COLOR                                       # x = 0.5 は 0.5·160 − 0.5 = 79.5 → 80
    assert tuple(out[44, 100]) == guides.Y_COLOR                                      # y = 0.5 は 0.5·90 − 0.5 = 44.5 → 44
    (tmp_path / "z").mkdir()
    zoomed = context(tmp_path / "z", camera={"keys": [{"t": 0.0, "zoom": 2.0, "center": [0.25, 0.5]}]})
    grid = guides.frame_guides(zoomed, 0.0, render.frame(zoomed, 0.0, "base"), ("grid",), 0.25)
    assert tuple(grid[60, 80]) == guides.X_COLOR                                      # 寄った camera の中心 = 元画像の x 0.25


def test_lights_and_masks_are_marked_where_they_are_on_screen(tmp_path):
    ctx = context(tmp_path, camera={"keys": [{"t": 0.0}]})
    out = guides.frame_guides(ctx, 0.0, render.frame(ctx, 0.0, "base"), ("lights", "masks"))
    x, y = round(0.64 * 160 - 0.5), round(0.42 * 90 - 0.5)                          # 光の位置（元画像の正規化座標）
    assert tuple(out[y, x]) == guides.LIGHT_COLOR
    assert (out == np.array(guides.MASK_COLOR)).all(axis=2).sum() > 50               # マスクの輪郭


def test_guide_kinds_are_checked():
    assert guides.parse_kinds(None) == () and guides.parse_kinds("") == guides.DEFAULT_KINDS
    assert guides.parse_kinds("masks,grid") == ("grid", "masks")
    with pytest.raises(PipelineError, match="--guides"):
        guides.parse_kinds("grid,stars")


def test_stills_with_guides_get_their_own_file(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    plain = runs.stills(spec_path, [0.0, 1.0], variants="base,both")
    marked = runs.stills(spec_path, [0.0, 1.0], variants="base,both", guides="grid,lights,masks", grid_step=0.1)
    assert marked["guides"] == ["grid", "lights", "masks"] and "guides" not in plain
    assert marked["path"] != plain["path"]
    with Image.open(plain["path"]) as a, Image.open(marked["path"]) as b:
        assert a.size == b.size and not np.array_equal(np.asarray(a), np.asarray(b))
    with pytest.raises(PipelineError, match="--grid-step"):
        runs.stills(spec_path, [0.0], grid_step=2.0)


# ---- 静止画シートの基準の列 -------------------------------------------------------------

def counting_video(path, frames=30, size=(160, 90), fps=12):
    """コマ i を灰色 8·i で塗った動画（基準の動画の代わり）。"""
    images = (np.full((size[1], size[0], 3), 8 * i, np.uint8) for i in range(frames))
    render.write_video(path, images, size, fps, 10, frames)
    return path


def test_the_reference_column_holds_the_nearest_frames_of_the_reference(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    video = counting_video(tmp_path / "ref.mp4")
    result = runs.stills(spec_path, [0.0, 1.0], variants="base", reference=video, reference_offset=0.5, reference_label="published")
    assert result["reference"]["frames_used"] == [6, 18] and result["reference"]["label"] == "published"
    with Image.open(result["path"]) as image:
        sheet = np.asarray(image)
    for row, value in enumerate((48, 144)):                                           # 8·6、8·18
        y = 6 + row * (90 + 6)
        cell = sheet[y + 30:y + 84, 6 + 8:6 + 150].astype(int)                        # 左の列。ラベルの帯の下
        assert np.abs(cell - value).max() <= 4
    other = runs.stills(spec_path, [0.0, 1.0], variants="base", reference=video, reference_offset=0.25)
    assert other["path"] != result["path"]                                            # offset は key に入る
    with pytest.raises(PipelineError, match="コマがありません"):
        runs.stills(spec_path, [0.0, 1.0], variants="base", reference=video, reference_offset=2.0)
    with pytest.raises(PipelineError, match="ASCII"):
        runs.stills(spec_path, [0.0], variants="base", reference=video, reference_label="公開版")
    wide = counting_video(tmp_path / "wide.mp4", frames=4, size=(320, 90))
    assert runs.stills(spec_path, [0.0], variants="base", reference=wide)["reference"]["warnings"]   # 縦横比が違う


def test_the_stills_command_takes_the_reference_and_guides(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    video = counting_video(tmp_path / "ref.mp4")
    code, result, err = run_cli("fx", "stills", spec_path, "--times", 0.5, "--variants", "both", "--guides",
                                "--reference", video, "--reference-label", "legacy")
    assert code == 0, err
    assert result["guides"] == ["grid", "lights"] and result["reference"]["frames_used"] == [6]
    assert json.loads(json.dumps(result))["sheet"] == [2 * 160 + 3 * 6, 90 + 2 * 6]
