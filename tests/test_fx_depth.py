"""fx-shot/0.2 の深度視差: 動かない深さ（pivot）・keys の depth_shift と dolly・深度の下ごしらえ・画面の端の検査。

実制作の 2.5D の式（寄り・引きつきの式と、ずらしだけの式）と深度の下ごしらえ（平面の当てはめ・太らせ・ぼかし）を
独立に書き写した参照実装と、合成の配列で照らし合わせる。素材はすべてプログラムで作る。
"""
import copy

import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project, spec_dict, write_spec  # noqa: E402
from mv_pipeline import fx  # noqa: E402
from mv_pipeline.fx import assets as fxassets, camera as fxcamera, render, runs  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

SIZE = (192, 108)


def v02(data=None):
    data = copy.deepcopy(data or spec_dict())
    data["schema_version"] = fx.SCHEMA_VERSION
    return data


def parse(tmp_path, data):
    return fxspec.parse_spec(data, tmp_path)


def path_for(keys, pivot=0.5, parallax_px=0.0, direction=(1, 0), size=SIZE, duration=2.0):
    """正規化済みの camera（keys に ease・zoom・center・depth_shift・dolly を補う）から CameraPath を作る。"""
    full = [{"ease": "linear", "zoom": 1.0, "center": [0.5, 0.5], "depth_shift": [0.0, 0.0], "dolly": 0.0, **k} for k in keys]
    return fxcamera.CameraPath({"keys": full, "parallax_px": parallax_px, "parallax_dir": list(direction)}, duration, size,
                               pivot=pivot)


def noisy_depth(size, seed=3):
    width, height = size
    rng = np.random.default_rng(seed)
    ramp = np.linspace(0.1, 0.9, height, dtype=np.float32)[:, None] + np.linspace(0.0, 0.1, width, dtype=np.float32)[None, :]
    return np.clip(ramp + rng.normal(0, 0.02, (height, width)).astype(np.float32), 0, 1).astype(np.float32)


def identity(size=SIZE):
    return fxcamera.affine(1.0, 0.5, 0.5, 0.0, 0.0, size)


# ---- 実制作の式を書き写した参照実装 --------------------------------------------------------

def production_maps(depth, size, pivot, par):
    """実制作の 2.5D（寄り・引きつき）の参照位置。視野 = 画像全体・同じ大きさ（恒等のカメラ）。"""
    width, height = size
    xs, ys = np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32)
    kx = ky = 1.0
    sx = sy = 1.0
    u = ((0 + (xs + 0.5) * kx) * sx - 0.5)[None, :].repeat(height, 0).astype(np.float32, copy=False)
    v = ((0 + (ys + 0.5) * ky) * sy - 0.5)[:, None].repeat(width, 1).astype(np.float32, copy=False)
    d = cv2.remap(depth, u, v, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
    px, py, dolly = par
    k = d - pivot
    so = width / 1920.0
    dx = (px * so + (xs[None, :] - width / 2) * dolly) * k
    dy = (py * so + (ys[:, None] - height / 2) * dolly) * k
    return (u - dx * kx * sx).astype(np.float32, copy=False), (v - dy * ky * sy).astype(np.float32, copy=False)


def production_shift_maps(depth_out, size, pivot, px, py):
    """実制作の 2.5D（ずらしだけ）: 視点の変換の後の深度 depth_out で xs − px·k、ys − py·k。"""
    width, height = size
    k = depth_out - pivot
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    return (xs - px * k).astype(np.float32), (ys - py * k).astype(np.float32)


def production_flatten(d, quad, grow=1.03, feather=4.0):
    """実制作の深度の平面の当てはめ（四隅は画素）。"""
    h, w = d.shape
    q = np.array(quad, np.float32)
    c = q.mean(axis=0)
    inner = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(inner, (c + (q - c) * 0.92).astype(np.int32), 1)
    ys, xs = np.nonzero(inner)
    a = np.stack([xs, ys, np.ones_like(xs)], 1).astype(np.float64)
    coef, *_ = np.linalg.lstsq(a, d[ys, xs].astype(np.float64), rcond=None)
    yy, xx = np.mgrid[0:h, 0:w]
    plane = (coef[0] * xx + coef[1] * yy + coef[2]).astype(np.float32)
    m = np.zeros((h, w), np.uint8)
    cv2.fillConvexPoly(m, (c + (q - c) * grow).astype(np.int32), 255)
    m = cv2.GaussianBlur(m.astype(np.float32) / 255.0, (0, 0), feather)
    return d * (1 - m) + plane * m


def production_depth_map(raw16, quads):
    """実制作の深度の下ごしらえ: /65535 → 平面 → 太らせ（7×7 の楕円）→ ぼかし σ2.5。"""
    d = raw16.astype(np.float32) / 65535.0
    for q in quads:
        d = production_flatten(d, q)
    d = cv2.dilate(d, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)))
    return cv2.GaussianBlur(d, (0, 0), 2.5)


# ---- spec -------------------------------------------------------------------------------

def test_a_02_spec_without_the_new_fields_is_the_01_spec(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    old = fxspec.load_spec(spec_path)
    new = parse(tmp_path, v02())
    assert fxspec.canonical_json(old.data) == fxspec.canonical_json(new.data)       # 同じ正規形 = 同じキャッシュ key
    a, b = render.prepare(old), render.prepare(fxspec.parse_spec(v02(), tmp_path))
    for t in (0.0, 0.9, 2.0):
        assert np.array_equal(render.frame(a, t, "both"), render.frame(b, t, "both"))


def test_the_new_fields_normalize_with_their_defaults(tmp_path):
    data = v02()
    data["camera"] = {"keys": [{"t": 0, "depth_shift": [10, -2]}, {"t": 2, "depth_shift": [-10, 0], "dolly": 0.03, "ease": "in_out"}],
                      "pivot": {"mask": "subject"}}
    data["assets"]["depth"] = {"path": "depth.png", "normalize": "none", "resolution": "base", "dilate_px": 3, "blur_px": 1.5,
                               "regions": [{"box": [0.1, 0.1, 0.3, 0.3], "mode": "value", "value": 0.2}, {"quad": [[0, 0], [1, 0], [1, 1], [0, 1]]}]}
    out = parse(tmp_path, data).data
    assert out["camera"]["pivot"] == {"mask": "subject"} and out["camera"]["parallax_px"] == 0.0
    assert [k["depth_shift"] for k in out["camera"]["keys"]] == [[10.0, -2.0], [-10.0, 0.0]]
    assert [k["dolly"] for k in out["camera"]["keys"]] == [0.0, 0.03]
    depth = out["assets"]["depth"]
    assert (depth["normalize"], depth["resolution"], depth["dilate_px"], depth["blur_px"], depth["near"]) == ("none", "base", 3, 1.5, "white")
    assert depth["regions"][0] == {"box": [0.1, 0.1, 0.3, 0.3], "mode": "value", "value": 0.2, "inner": 0.92, "grow": 1.03, "blur_px": 4.0}
    assert depth["regions"][1]["mode"] == "plane" and depth["regions"][1]["value"] is None
    assert fxspec.depth_motion_keys(out["camera"]) and not fxspec.depth_motion_keys(parse(tmp_path, v02()).data["camera"])


def test_the_01_schema_rejects_the_new_fields(tmp_path):
    data = spec_dict()
    data["camera"]["pivot"] = 0.35
    with pytest.raises(PipelineError, match="未知の鍵"):
        parse(tmp_path, data)
    data = spec_dict()
    data["camera"]["keys"][1]["dolly"] = 0.02
    with pytest.raises(PipelineError, match="未知の鍵"):
        parse(tmp_path, data)


@pytest.mark.parametrize("mutate, message", [
    (lambda d: d["camera"]["keys"][1].__setitem__("depth_shift", [4, 0]), "一緒に使えません"),       # parallax_px 6 と一緒
    (lambda d: d["camera"].__setitem__("pivot", {"mask": "ghost"}), "pivot"),
    (lambda d: d["camera"].__setitem__("pivot", {"box": [0.5, 0.2, 0.4, 0.6]}), "x0 < x1"),
    (lambda d: d["assets"]["depth"].__setitem__("regions", [{"quad": [[0, 0], [1, 0], [1, 0], [0, 0]]}]), "面積 0"),
    (lambda d: d["assets"]["depth"].__setitem__("regions", [{"box": [0, 0, 1, 1], "mode": "value"}]), "value"),
    (lambda d: d["assets"]["depth"].__setitem__("regions", [{"box": [0, 0, 1, 1], "quad": [[0, 0], [1, 0], [1, 1], [0, 1]]}]), "invalid"),
    (lambda d: d["assets"]["depth"].__setitem__("dilate_px", 4), "奇数"),
    (lambda d: d["assets"]["depth"].__setitem__("normalize", "minmax"), "invalid"),
    (lambda d: d["camera"].__setitem__("pivot", 1.5), "invalid"),
])
def test_bad_depth_settings_are_refused(tmp_path, mutate, message):
    data = v02()
    mutate(data)
    with pytest.raises(PipelineError, match=message):
        parse(tmp_path, data)


def test_settings_that_do_nothing_leave_a_warning(tmp_path):
    data = v02()
    del data["assets"]["depth"]
    data["camera"] = {"keys": [{"t": 0}, {"t": 2, "depth_shift": [8, 0]}], "pivot": 0.3}
    warnings = parse(tmp_path, data).warnings
    assert any("depth_shift" in w and "assets.depth" in w for w in warnings) and any("camera.pivot" in w for w in warnings)
    data = v02()
    data["camera"] = {"keys": [{"t": 0}], "pivot": {"mask": "subject"}}
    assert any("効きません" in w for w in parse(tmp_path, data).warnings)
    data = v02()
    data["assets"]["depth"]["regions"] = [{"box": [0, 0, 0.5, 0.5], "value": 0.3}]
    assert any("mode \"value\" のときだけ" in w for w in parse(tmp_path, data).warnings)


# ---- 深度視差の式 ------------------------------------------------------------------------

def test_the_pivot_depth_stays_still_and_nearer_moves_more():
    path = path_for([{"t": 0.0, "depth_shift": [12.0, -6.0]}], pivot=0.35)
    motion = path.depth_motion(1.0)
    kpx = SIZE[0] / 1920.0
    assert motion.shift == pytest.approx((12 * kpx, -6 * kpx)) and motion.legacy is None
    depth = np.full((SIZE[1], SIZE[0]), 0.35, np.float32)
    depth[:, 100:] = 1.0
    depth[:, :20] = 0.0
    dx, dy = fxcamera.depth_offsets(depth, SIZE, motion)
    assert dx[5, 50] == 0.0 and dy[5, 50] == 0.0                                       # pivot の深さは動かない
    assert dx[5, 150] == pytest.approx(12 * kpx * 0.65) and dy[5, 150] == pytest.approx(-6 * kpx * 0.65)
    assert dx[5, 5] == pytest.approx(-12 * kpx * 0.35)                                 # 奥は逆向き
    map_x, map_y = fxcamera.sample_maps(identity(), SIZE, depth, motion)
    assert map_x[5, 150] == pytest.approx(150 - dx[5, 150]) and map_y[5, 150] == pytest.approx(5 - dy[5, 150])


def test_keys_interpolate_the_shift_and_dolly_with_their_ease_and_may_start_away_from_zero():
    path = path_for([{"t": 0.0, "depth_shift": [10.0, -4.0]},
                     {"t": 2.0, "depth_shift": [-10.0, 0.0], "dolly": 0.03, "ease": "in_out"}])
    kpx = SIZE[0] / 1920.0
    assert path.depth_motion(0.0).shift == pytest.approx((10 * kpx, -4 * kpx))         # 始まりから動いている
    half = path.depth_motion(1.0)                                                       # smoothstep(0.5) = 0.5
    assert half.shift == pytest.approx((0.0, -2 * kpx)) and half.dolly == pytest.approx(0.015)
    quarter = path.depth_motion(0.5)
    w = 0.25 * 0.25 * (3 - 2 * 0.25)
    assert quarter.shift[0] == pytest.approx((10 - 20 * w) * kpx) and quarter.dolly == pytest.approx(0.03 * w)
    assert path.depth_motion(5.0).shift == pytest.approx((-10 * kpx, 0.0))            # 最後のキーの後はそのまま
    assert path_for([{"t": 0.0}, {"t": 2.0}]).depth_motion(1.0) is None                 # 動きが無ければ warp だけ


def test_dolly_scales_about_the_screen_centre():
    path = path_for([{"t": 0.0, "dolly": 0.02}], pivot=0.0)
    motion = path.depth_motion(0.0)
    depth = np.full((SIZE[1], SIZE[0]), 0.5, np.float32)
    dx, dy = fxcamera.depth_offsets(depth, SIZE, motion)
    assert dx[0, SIZE[0] // 2] == 0.0 and dy[SIZE[1] // 2, 0] == 0.0
    assert dx[0, 0] == pytest.approx(-SIZE[0] / 2 * 0.02 * 0.5) and dy[-1, 0] == pytest.approx((SIZE[1] - 1 - SIZE[1] / 2) * 0.02 * 0.5)


@pytest.mark.parametrize("par", [(10.0, -4.0, 0.0), (-8.0, 6.0, 0.03), (0.0, 0.0, -0.025)])
def test_the_maps_equal_the_production_formula_with_dolly(par):
    depth = noisy_depth(SIZE)
    path = path_for([{"t": 0.0, "depth_shift": [par[0], par[1]], "dolly": par[2]}], pivot=0.35)
    map_x, map_y = fxcamera.sample_maps(identity(), SIZE, fxcamera.warp(depth, identity(), SIZE), path.depth_motion(0.0))
    ref_x, ref_y = production_maps(depth, SIZE, 0.35, par)
    assert np.array_equal(map_x, ref_x) and np.array_equal(map_y, ref_y)


def test_the_maps_equal_the_production_formula_without_dolly():
    depth = noisy_depth(SIZE)
    sc = SIZE[0] / 1920.0
    for start, end, t in [((10, 0), (-10, 0), 0.7), ((-6, 3), (6, -3), 1.4)]:
        path = path_for([{"t": 0.0, "depth_shift": list(start)}, {"t": 2.0, "depth_shift": list(end), "ease": "in_out"}], pivot=0.35)
        u = t / 2.0
        u = u * u * (3 - 2 * u)
        px = (start[0] + (end[0] - start[0]) * u) * sc
        py = (start[1] + (end[1] - start[1]) * u) * sc
        map_x, map_y = fxcamera.sample_maps(identity(), SIZE, depth, path.depth_motion(t))
        ref_x, ref_y = production_shift_maps(depth, SIZE, 0.35, px, py)
        assert np.array_equal(map_x, ref_x) and np.array_equal(map_y, ref_y)


def test_the_01_parallax_keeps_the_01_formula_and_takes_the_pivot():
    depth = noisy_depth(SIZE)
    path = path_for([{"t": 0.0}, {"t": 2.0}], parallax_px=6.0, direction=(1, 0))
    motion = path.depth_motion(1.3)
    amplitude = path.depth_amplitude(1.3)
    shift = (depth - np.float32(0.5)) * np.float32(amplitude)                           # 0.1 の式のまま
    dx, dy = fxcamera.depth_offsets(depth, SIZE, motion)
    assert motion.legacy == (amplitude, (1.0, 0.0)) and np.array_equal(dx, shift * np.float32(1.0))
    assert path.depth_motion(0.0) is None                                               # t=0 は振幅 0（0.1 と同じ近道）
    lower = path_for([{"t": 0.0}, {"t": 2.0}], pivot=0.2, parallax_px=6.0).depth_motion(1.3)
    assert np.array_equal(fxcamera.depth_offsets(depth, SIZE, lower)[0], (depth - np.float32(0.2)) * np.float32(amplitude) * np.float32(1.0))


# ---- 深度の下ごしらえ ------------------------------------------------------------------------

def depth_spec(**changes):
    base = {"path": "depth.png", "near": "white", "normalize": "percentile", "resolution": "depth", "regions": [],
            "dilate_px": 0, "blur_px": 0.0}
    return {**base, **changes}


def region(points, size, **changes):
    width, height = size
    return {"quad": [[x / width, y / height] for x, y in points], "mode": "plane", "value": None, "inner": 0.92, "grow": 1.03,
            "blur_px": 4.0, **changes}


def test_the_production_depth_preparation_is_reproduced_exactly(tmp_path):
    size = (160, 90)
    raw = (noisy_depth(size) * 65535).astype(np.uint16)
    Image.fromarray(raw).save(tmp_path / "depth.png")
    quads = [[[40, 20], [100, 25], [96, 70], [38, 64]], [[110, 10], [150, 12], [150, 40], [112, 38]]]
    with Image.open(tmp_path / "depth.png") as image:
        ours, informative = fxassets.prepare_depth(
            image, depth_spec(normalize="none", regions=[region(q, size) for q in quads], dilate_px=7, blur_px=2.5), size,
            (0.0, 0.0, 1.0, 1.0))
    assert informative and np.array_equal(ours, production_depth_map(raw, quads))


def test_base_resolution_resizes_before_blurring_like_production(tmp_path):
    raw = (noisy_depth((80, 45)) * 65535).astype(np.uint16)
    Image.fromarray(raw).save(tmp_path / "depth.png")
    with Image.open(tmp_path / "depth.png") as image:
        ours, _ = fxassets.prepare_depth(image, depth_spec(normalize="none", resolution="base", blur_px=3), (160, 90), (0, 0, 1, 1))
    expected = cv2.GaussianBlur(cv2.resize(raw.astype(np.float32) / 65535.0, (160, 90), interpolation=cv2.INTER_LINEAR), (0, 0), 3)
    assert ours.shape == (90, 160) and np.array_equal(ours, expected)


def test_normalize_none_divides_by_the_bit_depth_and_refuses_floats_outside_zero_to_one(tmp_path):
    raw8 = (noisy_depth((40, 30)) * 255).astype(np.uint8)
    Image.fromarray(raw8).save(tmp_path / "d8.png")
    with Image.open(tmp_path / "d8.png") as image:
        ours, _ = fxassets.prepare_depth(image, depth_spec(normalize="none", blur_px=0.5), (40, 30), (0, 0, 1, 1))
    assert np.array_equal(ours, cv2.GaussianBlur(raw8.astype(np.float32) / 255.0, (0, 0), 0.5))
    Image.fromarray((noisy_depth((40, 30)) * 3).astype(np.float32)).save(tmp_path / "big.tif")          # float32 は F モード
    with Image.open(tmp_path / "big.tif") as image, pytest.raises(PipelineError, match="0..1 の外"):
        fxassets.prepare_depth(image, depth_spec(normalize="none", blur_px=1), (40, 30), (0, 0, 1, 1))


def test_a_value_region_sets_the_depth_and_a_box_equals_its_quad(tmp_path):
    size = (160, 90)
    values = noisy_depth(size)
    box = {"box": [0.25, 0.2, 0.5, 0.6], "mode": "value", "value": 0.0, "inner": 0.92, "grow": 1.0, "blur_px": 0.0}
    out = fxassets.apply_region(values, fxassets.region_quad(box, size), "value", 0.0, 0.92, 1.0, 0.0)
    assert out[30, 60] == 0.0 and out[5, 5] == values[5, 5]
    quad = {**box, "quad": [[0.25, 0.2], [0.5, 0.2], [0.5, 0.6], [0.25, 0.6]]}
    del quad["box"]
    assert np.array_equal(fxassets.region_quad(box, size), fxassets.region_quad(quad, size))
    outside = fxassets.region_quad({"quad": [[1.2, 0.1], [1.5, 0.1], [1.5, 0.5], [1.2, 0.5]]}, size)      # 画像の外
    for quad in (outside, np.array([[10, 10], [11, 10], [11, 11], [10, 11]], np.float32)):                # 1 画素ほど
        with pytest.raises(PipelineError, match="平面を当てる画素"):
            fxassets.apply_region(values, quad, "plane")
    assert np.array_equal(fxassets.apply_region(values, outside, "value", 0.5, blur_px=0.0), values)    # value は外なら何も変えない


def test_the_02_depth_path_drops_a_flat_depth_with_a_warning(tmp_path):
    spec_path = make_project(tmp_path, 160, 90)
    Image.fromarray(np.full((90, 160), 30000, np.uint16)).save(tmp_path / "depth.png")
    data = v02()
    data["assets"]["depth"] = {"path": "depth.png", "normalize": "none", "blur_px": 2}
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    assert ctx.assets.depth is None and ctx.depth_info is None and any("ほぼ一定" in w for w in ctx.warnings)
    assert spec_path.is_file()


def test_value_regions_count_before_a_flat_depth_is_dropped(tmp_path):
    make_project(tmp_path, 160, 90)
    Image.fromarray(np.full((90, 160), round(0.2 * 65535), np.uint16)).save(tmp_path / "depth.png")     # 一定の 0.2
    data = v02()
    data["assets"]["depth"] = {"path": "depth.png", "normalize": "none",
                               "regions": [{"box": [0.3, 0.3, 0.7, 0.7], "mode": "value", "value": 0.8}]}   # 手前を 0.8 に書く
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    assert ctx.assets.depth is not None and not any("ほぼ一定" in w for w in ctx.warnings)               # 書いた差は捨てない
    assert float(ctx.assets.depth.min()) == pytest.approx(0.2, abs=1e-4) and float(ctx.assets.depth.max()) == pytest.approx(0.8, abs=1e-4)
    Image.fromarray((noisy_depth((160, 90)) * 65535).astype(np.uint16)).save(tmp_path / "depth.png")
    data["assets"]["depth"]["regions"] = [{"box": [0, 0, 1, 1], "mode": "value", "value": 0.5}]          # 画面の全部を 1 つの値に
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    assert ctx.assets.depth is None and any("ほぼ一定" in w for w in ctx.warnings)                     # 下ごしらえの後で平らなら使わない


def test_a_depth_without_preparation_stays_on_the_01_path(tmp_path):
    make_project(tmp_path, 160, 90)
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, v02())))
    with Image.open(tmp_path / "depth.png") as image:
        raw = fxassets._crop_resize(fxassets._raw_values(image), ctx.assets.box, ctx.size)
    assert np.array_equal(ctx.assets.depth, fxassets.normalize_depth(raw, "white")[0])


# ---- pivot ----------------------------------------------------------------------------------

def test_the_pivot_can_come_from_a_mask_or_a_box(tmp_path):
    make_project(tmp_path, 160, 90)
    data = v02()
    data["camera"]["pivot"] = {"mask": "subject"}
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    expected = float(np.median(ctx.assets.depth[ctx.assets.masks["subject"] > 0.5]))
    assert ctx.depth_info == {"pivot": round(expected, 6), "pivot_from": "mask:subject", "motion": True}
    assert ctx.path.pivot == expected
    data["camera"]["pivot"] = {"box": [0.0, 0.8, 1.0, 1.0]}
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    assert ctx.depth_info["pivot_from"] == "box" and ctx.path.pivot == float(np.median(ctx.assets.depth[72:90, :]))


def test_a_pivot_mask_or_box_outside_the_view_is_refused(tmp_path):
    make_project(tmp_path, 160, 90)
    Image.new("L", (160, 90), 0).save(tmp_path / "mask_empty.png")
    data = v02()
    data["assets"]["masks"]["empty"] = "mask_empty.png"
    data["camera"]["pivot"] = {"mask": "empty"}
    with pytest.raises(PipelineError, match="ほぼ空"):
        render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    data = v02()
    data["output"].update(width=90, height=90)                                          # cover で左右を切る
    data["camera"]["pivot"] = {"box": [0.0, 0.0, 0.1, 1.0]}
    with pytest.raises(PipelineError, match="外です"):
        render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))


def test_stills_record_the_resolved_pivot_and_the_feature(tmp_path):
    make_project(tmp_path, 160, 90)
    data = v02()
    data["camera"] = {"keys": [{"t": 0, "depth_shift": [6, 0]}, {"t": 2, "depth_shift": [-6, 0], "ease": "in_out"}], "pivot": 0.3}
    data["layers"], data["lights"] = None, []
    data = {k: v for k, v in data.items() if v is not None}
    result = runs.stills(write_spec(tmp_path, data), [0.0, 1.0], variants="base")
    assert result["depth"] == {"pivot": 0.3, "pivot_from": "spec", "motion": True}
    ctx = render.prepare(fxspec.load_spec(tmp_path / "spec.json"))
    assert "depth_parallax" in ctx.variant_notes()["base"]["features"]                 # parallax_px が無くても深度視差
    assert not np.array_equal(render.frame(ctx, 0.0, "base"), render.frame(ctx, 2.0, "base"))


# ---- 画面の端 -------------------------------------------------------------------------------

def brute_force_overflow(path, depth, parallaxes, samples=41, times=None):
    """画面全体の参照位置から数えた、camera の視野を超えたはみ出しの最大（1920 幅換算の px）。times で時刻を渡せる。"""
    width, height = path.size
    worst = 0.0
    for t in (np.linspace(0.0, path.duration, samples) if times is None else times):
        motion = path.depth_motion(float(t))
        if motion is None:
            continue
        for parallax in parallaxes:
            matrix = path.matrix(float(t), parallax)
            scale, tx, ty = float(matrix[0, 0]), float(matrix[0, 2]), float(matrix[1, 2])
            map_x, map_y = fxcamera.sample_maps(matrix, path.size, fxcamera.warp(depth, matrix, path.size), motion)
            cx, cy = (np.array([0.0, width - 1.0]) - tx) / scale, (np.array([0.0, height - 1.0]) - ty) / scale
            before = (-cx[0], cx[1] - (width - 1), -cy[0], cy[1] - (height - 1))
            after = (-map_x.min(), map_x.max() - (width - 1), -map_y.min(), map_y.max() - (height - 1))
            worst = max(worst, max(a - max(b, 0.0) for a, b in zip(after, before)) * scale)
    return worst / path.kpx


def reported(message):
    import re
    return float(re.search(r"最大 ([0-9.]+)px", message).group(1)) if message else 0.0


@pytest.mark.parametrize("keys, pivot, expected_warning", [
    ([{"t": 0.0, "depth_shift": [40, 0]}], 0.5, True),
    ([{"t": 0.0, "zoom": 1.3, "depth_shift": [40, 0]}], 0.5, False),                      # 寄っていれば余白に収まる
    ([{"t": 0.0, "dolly": 0.05}, {"t": 2.0, "dolly": -0.05, "zoom": 1.2}], 0.6, True),
    ([{"t": 0.0, "zoom": 1.1, "center": [0.45, 0.5], "depth_shift": [0, 120]}], 0.9, True),       # camera は左、深度は下へはみ出す
])
def test_the_edge_check_warns_only_beyond_the_camera_margin_and_matches_a_full_scan(keys, pivot, expected_warning):
    depth = noisy_depth(SIZE)
    path = path_for(keys, pivot=pivot)
    message = fxcamera.depth_out_of_bounds(path, depth, [1.0, 0.4])
    brute = brute_force_overflow(path, depth, [1.0, 0.4])
    if expected_warning:
        assert reported(message) == pytest.approx(brute, abs=0.15)
    else:
        assert message is None and brute * path.kpx <= 0.5                               # 0.5 出力画素までは警告しない


def test_the_edge_check_looks_at_the_key_times_between_the_even_probes():
    depth = noisy_depth(SIZE)
    depth[:, :20] = 1.0                                                                  # 左の端に手前
    keys = [{"t": 0.0}, {"t": 0.40}, {"t": 0.45, "depth_shift": [200, 0]}, {"t": 0.49}]  # 等間隔の 41 時刻は 0.375 と 0.5
    path = path_for(keys, duration=5.0)
    message = fxcamera.depth_out_of_bounds(path, depth, [1.0])
    assert reported(message) == pytest.approx(brute_force_overflow(path, depth, [1.0], times=[0.45]), abs=0.15)
    assert reported(message) > 50                                                        # 山は 0.45 秒（約 100px）
    assert brute_force_overflow(path, depth, [1.0]) == 0.0                               # 等間隔だけなら見落とす


def test_the_01_parallax_at_zoom_one_now_warns(tmp_path):
    make_project(tmp_path, 160, 90)
    data = spec_dict()
    data["camera"]["keys"], data["camera"]["parallax_px"] = [{"t": 0.0}], 40
    ctx = render.prepare(fxspec.load_spec(write_spec(tmp_path, data)))
    assert any("深度視差の変位" in w for w in ctx.warnings)
    assert not any("深度視差の変位" in w for w in render.prepare(fxspec.load_spec(make_project(tmp_path, 160, 90))).warnings)
