"""camera.plate: full（fx-shot/0.3。frame: source のときだけ）: 板を元画像の画素のまま持ち、寄りでも元画像の画素から引く。

縮める写し（倍率 < 1）は面積平均でそのコマの倍率まで縮めてから写す。plate を書かない spec（fit）は以前と同じ正規形・同じ絵。
"""
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import camera, render  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.fx.color import to_linear, to_srgb8  # noqa: E402
from mv_pipeline.fx.view import View  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402


def checker(directory, width=640, height=360):
    """1 画素ごとの白黒の市松（いちばん細かい模様。縮めて引くと荒れ、拡大した板から引くとぼける）。"""
    yy, xx = np.mgrid[0:height, 0:width]
    image = np.where((xx + yy) % 2 == 0, 230, 25).astype(np.uint8)
    rgb = np.repeat(image[..., None], 3, axis=2)
    cv2.imencode(".png", rgb)[1].tofile(str(directory / "base.png"))
    return rgb


def spec_for(directory, keys, plate=None, frame="source", size=(320, 180), name="spec.json"):
    camera_raw = {"keys": keys, "frame": frame, "clamp": True}
    if plate is not None:
        camera_raw["plate"] = plate
    raw = {"schema_version": "fx-shot/0.3", "output": {"width": size[0], "height": size[1], "fps": 12}, "duration_s": 2.0,
           "assets": {"base": "base.png"}, "camera": camera_raw}
    path = directory / name
    path.write_text(json.dumps(raw), encoding="utf-8")
    return fxspec.load_spec(path)


def test_plate_is_written_to_the_normal_form_only_when_full(tmp_path):
    checker(tmp_path)
    keys = [{"t": 0, "zoom": 1.0}]
    absent = spec_for(tmp_path, keys).data["camera"]
    fit = spec_for(tmp_path, keys, "fit", name="fit.json").data["camera"]
    full = spec_for(tmp_path, keys, "full", name="full.json").data["camera"]
    assert "plate" not in absent and fit == absent            # fit は書かない（plate の無い spec の正規形・キャッシュ key は以前のまま）
    assert full["plate"] == "full"
    with pytest.raises(PipelineError, match="plate: full は camera.frame: source"):
        spec_for(tmp_path, keys, "full", frame="cover", name="cover.json")
    with pytest.raises(PipelineError):
        spec_for(tmp_path, keys, "sharp", name="bad.json")
    raw = json.loads((tmp_path / "full.json").read_text(encoding="utf-8"))
    raw["schema_version"] = "fx-shot/0.2"
    raw["camera"] = {"keys": keys, "plate": "full"}           # 0.2 の spec には 0.3 の項目を書けない
    with pytest.raises(PipelineError, match="未知の鍵"):
        fxspec.parse_spec(raw, tmp_path)


def test_full_plate_samples_source_pixels_when_zooming_in(tmp_path):
    source = checker(tmp_path)
    keys = [{"t": 0, "zoom": 2.0, "center": [0.5, 0.5]}]     # cover 0.5 × zoom 2 = 元画像の 1 画素が画面の 1 画素
    full = render.prepare(spec_for(tmp_path, keys, "full", name="full.json"))
    fit = render.prepare(spec_for(tmp_path, keys, name="fit.json"))
    assert full.assets.plate_size == (640, 360) and fit.assets.plate_size == (320, 180)
    sharp, soft = render.frame(full, 0.0, "base"), render.frame(fit, 0.0, "base")
    expected = to_srgb8(to_linear(source[90:270, 160:480]))
    assert np.abs(sharp.astype(int) - expected.astype(int)).max() <= 1      # 元画像の画素がそのまま出る
    assert float(sharp.std()) > 5 * float(soft.std())                         # fit は縮めた板を 2 倍に引き伸ばす（市松が灰色になる）


def test_full_plate_shrinks_by_area_when_the_view_is_wider_than_the_output(tmp_path):
    checker(tmp_path)
    ctx = render.prepare(spec_for(tmp_path, [{"t": 0, "zoom": 1.0, "center": [0.5, 0.5]}], "full"))
    image = render.frame(ctx, 0.0, "base")
    expected = to_srgb8(cv2.resize(ctx.assets.plate, (0, 0), fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA))
    assert np.array_equal(image, expected)                 # 倍率 0.5 は面積平均で縮めた板そのもの（平行移動は 0）
    odd = render.prepare(spec_for(tmp_path, [{"t": 0, "zoom": 1.1, "center": [0.5, 0.5]}], "full", name="odd.json"))
    image = render.frame(odd, 0.0, "base")                 # 倍率 0.55
    assert float(image[2:-2, 2:-2].std()) < 1.5            # 市松がほぼ平均の灰色になる
    aliased = to_srgb8(cv2.warpAffine(odd.assets.plate, odd.path.matrix(0.0, 1.0), (320, 180), flags=cv2.INTER_LINEAR))
    assert float(aliased[2:-2, 2:-2].std()) > 10 * float(image[2:-2, 2:-2].std())   # 双線形で直接縮めると縞（モアレ）が残る


def test_full_plate_has_no_jump_around_scale_one_and_maps_points_like_fit(tmp_path):
    rng = np.random.default_rng(3)
    smooth = cv2.GaussianBlur(rng.uniform(0, 255, (360, 640, 3)).astype(np.float32), (0, 0), 6)
    cv2.imencode(".png", np.clip(smooth, 0, 255).astype(np.uint8))[1].tofile(str(tmp_path / "base.png"))
    below = render.prepare(spec_for(tmp_path, [{"t": 0, "zoom": 1.999, "center": [0.4, 0.6]}], "full", name="below.json"))
    above = render.prepare(spec_for(tmp_path, [{"t": 0, "zoom": 2.001, "center": [0.4, 0.6]}], "full", name="above.json"))
    a, b = render.frame(below, 0.0, "base").astype(int), render.frame(above, 0.0, "base").astype(int)
    assert float(np.abs(a - b).mean()) < 0.6                # 倍率 1 の前後で絵が跳ばない
    keys = [{"t": 0, "zoom": 1.3, "center": [0.4, 0.6]}, {"t": 2, "zoom": 2.4, "center": [0.6, 0.4], "ease": "in_out"}]
    full = render.prepare(spec_for(tmp_path, keys, "full", name="full.json"))
    fit = render.prepare(spec_for(tmp_path, keys, name="fit.json"))
    for t in (0.0, 0.7, 2.0):
        view_full = View(full.path, full.assets.box, full.assets.plate_size, full.size, t)
        view_fit = View(fit.path, fit.assets.box, fit.assets.plate_size, fit.size, t)
        for u, v in ((0.5, 0.5), (0.3, 0.7), (0.62, 0.41)):
            assert view_full.point(u, v) == pytest.approx(view_fit.point(u, v), abs=0.75)   # 視野は同じ（hooks の点も同じ所）
        assert view_full.scale == pytest.approx(view_fit.scale, rel=2e-3)


def test_warp_fine_keeps_the_depth_path():
    plate = np.random.default_rng(0).uniform(0, 1, (120, 200, 3)).astype(np.float32)
    depth = np.tile(np.linspace(0, 1, 200, dtype=np.float32)[None, :], (120, 1))
    motion = camera.DepthMotion(0.5, shift=(4.0, 0.0))
    matrix = camera.affine(1.0, 0.5, 0.5, 0.0, 0.0, (80, 48), (200, 120), 0.4)       # 倍率 0.4（縮める写し）
    out = camera.warp_fine(plate, matrix, (80, 48), depth, motion)
    assert out.shape == (48, 80, 3) and np.isfinite(out).all()
    still = camera.warp_fine(plate, matrix, (80, 48))
    assert not np.array_equal(out, still)                  # 深度視差の変位が掛かる
    big = camera.affine(1.0, 0.5, 0.5, 0.0, 0.0, (80, 48), (200, 120), 1.2)           # 倍率 1.2（寄る写し）は warp_with_depth と同じ
    assert np.array_equal(camera.warp_fine(plate, big, (80, 48), depth, motion), camera.warp_with_depth(plate, big, (80, 48), depth, motion))
