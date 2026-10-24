import copy
import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import FULL, make_project  # noqa: E402
from mv_pipeline.fx import camera as fxcamera, color, light as fxlight, render  # noqa: E402
from mv_pipeline.fx.assets import cover_box, to_screen, width_scale  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402

T = 1.0


def one_light(drop=(), **over):
    """FULL の光を、時間に依らない一定の強さ・明滅なしにしたもの。drop の鍵は取り除く。"""
    lt = copy.deepcopy(FULL["lights"][0])
    lt["intensity"] = 1.0
    lt["flicker"] = {"amount": 0.0, "hz": 5.0}
    lt.update(over)
    return [{k: v for k, v in lt.items() if k not in drop}]


def build(tmp_path, width=160, height=90, **changes):
    return render.prepare(fxspec.load_spec(make_project(tmp_path, width, height, **changes)))


def lum(image):
    return image.astype(np.int32).sum(axis=2)


def changed(a, b):
    return (a != b).any(axis=2)


def test_zero_intensity_is_exactly_base_even_with_a_moving_camera(tmp_path):
    for intensity in (0.0, [{"t": 0.0, "v": 0.0}, {"t": 2.0, "v": 0.0}]):
        ctx = build(tmp_path, lights=one_light(intensity=intensity, flicker={"amount": 0.5, "hz": 3.0}))
        assert ctx.has_lights and ctx.assets.depth is not None and ctx.path.parallax_px > 0
        for t in (0.0, 0.7, 1.3, 2.0):
            assert np.array_equal(render.frame(ctx, t, "light"), render.frame(ctx, t, "base"))


def test_spec_without_lights_makes_light_equal_base_and_is_recorded(tmp_path):
    ctx = build(tmp_path, lights=None)
    assert not ctx.has_lights
    assert np.array_equal(render.frame(ctx, T, "light"), render.frame(ctx, T, "base"))
    notes = ctx.variant_notes()
    assert notes["light"]["empty"] and "lights" in notes["light"]["note"]
    assert notes["both"]["features"] == notes["layers"]["features"] and not notes["layers"]["empty"]
    assert not notes["base"]["empty"]


def test_light_is_local_pixels_outside_receivers_and_emitter_are_bit_identical(tmp_path):
    ctx = build(tmp_path, camera=None, lights=one_light())          # 静止カメラ・深度視差なし = 恒等変換
    width, height = ctx.size
    masks = ctx.assets.masks
    ys, xs = np.mgrid[0:height, 0:width]
    emitter = ctx.spec.data["lights"][0]["emitter"]
    px, py = 0.64 * width, 0.42 * height
    near_emitter = np.hypot(xs + 0.5 - px, ys + 0.5 - py) <= 4 * emitter["size"] * width + 1.0
    allowed = (masks["subject"] > 0) | (masks["ground"] > 0) | near_emitter
    diff = changed(render.frame(ctx, T, "light"), render.frame(ctx, T, "base"))
    assert diff.sum() > 500                                          # 光は実際に当たっている
    assert (~allowed).sum() > 3000                                   # 検査する「外」が十分ある
    assert not (diff & ~allowed).any()                               # 外は 1 画素も変わらない


def test_locality_without_any_receiver_leaves_only_the_emitter(tmp_path):
    ctx = build(tmp_path, camera=None, lights=one_light(receivers=[]))
    width, height = ctx.size
    ys, xs = np.mgrid[0:height, 0:width]
    reach = 4 * 0.03 * width + 1.0
    inside = np.hypot(xs + 0.5 - 0.64 * width, ys + 0.5 - 0.42 * height) <= reach
    diff = changed(render.frame(ctx, T, "light"), render.frame(ctx, T, "base"))
    assert diff.any() and not (diff & ~inside).any()


def test_receivers_get_brighter_and_gain_scales_the_effect(tmp_path):
    ctx = build(tmp_path, camera=None, lights=one_light(receivers=[{"mask": "subject", "gain": 1.0}], drop=("emitter",)))
    base, lit = render.frame(ctx, T, "base"), render.frame(ctx, T, "light")
    subject = ctx.assets.masks["subject"] > 0.99
    assert (lum(lit)[subject] > lum(base)[subject]).mean() > 0.95
    weak = build(tmp_path, camera=None, lights=one_light(receivers=[{"mask": "subject", "gain": 0.2}], drop=("emitter",)))
    weaker = render.frame(weak, T, "light")
    assert lum(base)[subject].sum() < lum(weaker)[subject].sum() < lum(lit)[subject].sum()
    assert (lit.astype(int) >= base.astype(int)).all()               # 光は足すだけ。暗くしない


def test_reflection_only_changes_its_own_mask(tmp_path):
    off = build(tmp_path, camera=None, lights=one_light(receivers=[{"mask": "ground", "gain": 0.6, "reflection": 0.0}]))
    on = build(tmp_path, camera=None, lights=one_light(receivers=[{"mask": "ground", "gain": 0.6, "reflection": 0.8}]))
    a, b = render.frame(off, T, "light"), render.frame(on, T, "light")
    diff = changed(a, b)
    assert diff.sum() > 100
    assert not (diff & ~(off.assets.masks["ground"] > 0)).any()
    assert (b.astype(int) >= a.astype(int)).all()


def test_rim_lights_only_the_inside_edge_band(tmp_path):
    receivers = lambda rim: [{"mask": "subject", "gain": 0.0, "rim": rim}]  # noqa: E731
    off = build(tmp_path, camera=None, lights=one_light(receivers=receivers(0.0), drop=("emitter",)))
    on = build(tmp_path, camera=None, lights=one_light(receivers=receivers(1.0), drop=("emitter",)))
    a, b = render.frame(off, T, "light"), render.frame(on, T, "light")
    diff = changed(a, b)
    subject = on.assets.masks["subject"] > 0
    assert diff.sum() > 20
    assert not (diff & ~subject).any()                               # 輪郭の外へは漏れない
    core = cv2.erode((on.assets.masks["subject"] > 0.99).astype(np.uint8), np.ones((13, 13), np.uint8)) > 0
    assert core.sum() > 50 and not (diff & core).any()               # 深い内側は変わらない（輪郭帯だけ）
    assert (b.astype(int) >= a.astype(int)).all()


def test_depth_gate_keeps_light_to_the_matching_depth(tmp_path):
    receivers = [{"mask": "subject", "gain": 1.0}]
    gated = build(tmp_path, camera=None, lights=one_light(receivers=receivers, drop=("emitter",), depth=0.75, depth_falloff=0.05))
    free = build(tmp_path, camera=None, lights=one_light(receivers=receivers, drop=("depth", "emitter")))
    base = render.frame(gated, T, "base")
    height = gated.size[1]
    far = np.zeros(gated.size[::-1], dtype=bool)
    far[int(height * 0.3):int(height * 0.45)] = True                 # 深度の勾配で奥側（D≈0.3-0.45）
    near = np.zeros_like(far)
    near[int(height * 0.7):int(height * 0.8)] = True                 # 手前側（D≈0.7-0.8）
    subject = gated.assets.masks["subject"] > 0.5
    lit_gated, lit_free = render.frame(gated, T, "light"), render.frame(free, T, "light")
    assert not changed(lit_gated, base)[far & subject].any()         # 奥は照らさない
    assert changed(lit_free, base)[far & subject].any()              # 深度なしなら照らす
    assert changed(lit_gated, base)[near & subject].any()
    assert lum(lit_gated)[near & subject].sum() > lum(base)[near & subject].sum()


def test_depth_in_light_is_ignored_without_a_depth_asset(tmp_path):
    ctx = build(tmp_path, camera=None, assets={"base": "base.png", "masks": FULL["assets"]["masks"]},
                lights=one_light(depth=0.9))
    assert any("assets.depth" in w for w in ctx.warnings)
    assert changed(render.frame(ctx, T, "light"), render.frame(ctx, T, "base")).any()


def test_emitter_is_a_bright_local_spot(tmp_path):
    ctx = build(tmp_path, 320, 180, camera=None, lights=one_light(receivers=[], emitter={"size": 0.04, "glow": 0.5}))
    base, lit = render.frame(ctx, T, "base"), render.frame(ctx, T, "light")
    px, py = int(0.64 * 320), int(0.42 * 180)
    assert lum(lit)[py, px] > lum(base)[py, px] + 400
    assert lum(lit)[py, px] > lum(lit)[py, px + 10] > lum(lit)[py, px + 25] >= lum(base)[py, px + 25]
    assert np.array_equal(lit[:, :px - 4 * int(0.04 * 320) - 3], base[:, :px - 4 * int(0.04 * 320) - 3])


def test_light_moves_with_the_camera(tmp_path):
    ctx = build(tmp_path, 320, 180, lights=one_light(receivers=[], emitter={"size": 0.03, "glow": 0.5}),
                camera={"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.2, "center": [0.6, 0.45]}]})
    expected = ctx.path.matrix(2.0, 1.0) @ np.array([0.64 * 320 - 0.5, 0.42 * 180 - 0.5, 1.0])
    assert abs(expected[0] - (0.64 * 320 - 0.5)) > 10                # カメラは実際に光源を動かす
    diff = lum(render.frame(ctx, 2.0, "light")) - lum(render.frame(ctx, 2.0, "base"))
    ys, xs = np.nonzero(diff > 30)
    assert abs(xs.mean() - expected[0]) < 1.0 and abs(ys.mean() - expected[1]) < 1.0


def test_intensity_keys_and_flicker_drive_brightness(tmp_path):
    ctx = build(tmp_path, camera=None)
    subject = ctx.assets.masks["subject"] > 0.99
    base = lum(render.frame(ctx, 0.0, "base"))[subject].sum()
    assert lum(render.frame(ctx, 0.0, "light"))[subject].sum() == base         # t=0 は強さ 0
    rising = [lum(render.frame(ctx, t, "light"))[subject].sum() for t in (0.1, 0.25, 0.5)]
    assert base < rising[0] < rising[1] < rising[2]
    flicker = build(tmp_path, camera=None, lights=one_light(flicker={"amount": 0.9, "hz": 3.0}))
    steady = build(tmp_path, camera=None, lights=one_light(flicker={"amount": 0.0, "hz": 3.0}))
    times = np.linspace(0.0, 1.0, 13)
    wobble = [lum(render.frame(flicker, t, "light"))[subject].sum() for t in times]
    flat = [lum(render.frame(steady, t, "light"))[subject].sum() for t in times]
    assert len(set(flat)) == 1 and np.std(wobble) > 0 and max(wobble) > min(wobble) * 1.2


def test_levels_are_positive_only_while_the_light_is_on(tmp_path):
    ctx = build(tmp_path, camera=None)
    assert fxlight.levels(ctx.lights, 0.0) == []
    ((lt, level),) = fxlight.levels(ctx.lights, 0.5)
    assert lt.id == "flame" and 1.0 < level < 1.4


def test_falloff_is_compact_and_monotone():
    q = np.linspace(0, 1.5, 301, dtype=np.float32)
    f = fxlight.falloff(q)
    assert f[0] == 1.0 and (f[q >= 1.0] == 0.0).all() and (np.diff(f) <= 1e-7).all() and (f >= 0).all()


def test_soft_add_properties():
    rng = np.random.default_rng(3)
    base = rng.random((20, 30, 3), dtype=np.float32)
    zero = np.zeros_like(base)
    assert np.array_equal(color.soft_add(base, zero), base)                      # 加算 0 は原画のまま（ビット一致）
    assert np.array_equal(color.soft_add(base, zero, (2, 10, 3, 20)), base)
    add = rng.random(base.shape, dtype=np.float32) * 2
    out = color.soft_add(base, add)
    assert (out >= base - 1e-6).all() and (out <= base + add + 1e-6).all()       # 足すだけ・圧縮は盛らない
    low = np.full((4, 4, 3), 0.3, dtype=np.float32)
    assert np.allclose(color.soft_add(low, low * 0.5), 0.45, atol=1e-6)         # 0.8 までは素通し
    stronger = color.soft_add(base, add * 2)
    assert (stronger >= out - 1e-6).all()                                        # 単調
    roi = color.soft_add(base, np.where(np.arange(30)[None, :, None] < 10, add, 0), (0, 20, 0, 12))
    full = color.soft_add(base, np.where(np.arange(30)[None, :, None] < 10, add, 0))
    assert np.array_equal(roi, full)                                             # roi は全画面計算と同じ
    xs = np.linspace(0, 4, 2001, dtype=np.float32)
    k = color.knee(xs)
    assert np.array_equal(k[xs <= 0.8], xs[xs <= 0.8]) and (np.diff(k) >= -1e-7).all() and k[-1] < 4.0
    assert abs(float(color.knee(np.float32(0.8001))) - 0.8001) < 1e-4            # つなぎ目で傾きも連続


def test_srgb_roundtrip_is_lossless_for_every_8bit_value():
    levels = np.arange(256, dtype=np.uint8)
    assert np.array_equal(color.to_srgb8(color.to_linear(levels)), levels)
    assert color.to_srgb8(np.float32(-1.0) * np.ones(3, dtype=np.float32)).tolist() == [0, 0, 0]
    assert color.to_srgb8(np.float32(7.0) * np.ones(3, dtype=np.float32)).tolist() == [255, 255, 255]


def contexts(tmp_path, **changes):
    """同じ素材（16:9）・同じ spec を 16:9 と 9:16 の出力で prepare した {名前: ctx}。"""
    raw = json.loads(make_project(tmp_path, 320, 180, **changes).read_text(encoding="utf-8"))
    out = {}
    for name, (width, height) in {"wide": (320, 180), "tall": (144, 256)}.items():
        data = copy.deepcopy(raw)
        data["output"].update(width=width, height=height)
        out[name] = render.prepare(fxspec.parse_spec(data, tmp_path))
    return out


def source_box(ctx):
    """cover の切り出し範囲を、ctx に頼らず素材（320x180）と出力寸法から求め直したもの。"""
    return cover_box(320, 180, *ctx.size)


def to_original(ctx, x, y):
    """出力の画素位置（画素中心規約）→ 元画像の正規化座標。"""
    x0, y0, x1, y1 = source_box(ctx)
    return x0 + (x + 0.5) / ctx.size[0] * (x1 - x0), y0 + (y + 0.5) / ctx.size[1] * (y1 - y0)


def test_light_position_and_lengths_are_in_original_image_coordinates(tmp_path):
    lights = one_light(position=[0.47, 0.4], radius=0.12, receivers=[], emitter={"size": 0.03, "glow": 0.5})
    pair = contexts(tmp_path, camera=None, lights=lights)
    assert source_box(pair["tall"])[2] - source_box(pair["tall"])[0] < 0.4       # 9:16 は元画像の幅の 3 割ほどしか見えない
    for name, ctx in pair.items():
        diff = lum(render.frame(ctx, T, "light")) - lum(render.frame(ctx, T, "base"))
        ys, xs = np.nonzero(diff > 0)
        weight = diff[ys, xs].astype(float)
        u, v = to_original(ctx, (xs * weight).sum() / weight.sum(), (ys * weight).sum() / weight.sum())
        assert (u, v) == pytest.approx((0.47, 0.4), abs=0.006), name              # 光点の中心は元画像の同じ位置
        light = ctx.lights[0]
        x0, y0, x1, y1 = source_box(ctx)
        for roi, expected in ((light.roi_glow, 4 * 0.03), (light.roi_main, 0.12)):  # 影響半径も元画像の幅が基準
            half = (roi[3] - roi[2] - 1) / 2 / ctx.size[0] * (x1 - x0)
            assert half == pytest.approx(expected, abs=0.008), name


def test_the_same_spot_of_the_original_image_gets_the_same_light_at_any_aspect(tmp_path):
    receivers = [{"mask": "subject", "gain": 1.0}]
    pair = contexts(tmp_path, camera=None, lights=one_light(position=[0.47, 0.45], radius=0.2, receivers=receivers,
                                                            drop=("emitter", "depth")))
    fields = []
    for ctx in pair.values():
        weight = ctx.lights[0].receivers[0].diffuse_w                           # 受光 × falloff（roi_main の中）
        y0, y1, x0, x1 = ctx.lights[0].roi_main
        x0b, y0b, x1b, y1b = source_box(ctx)
        samples = []
        for u, v in ((0.45, 0.4), (0.42, 0.55), (0.47, 0.6)):                   # 人物の上の 3 点（元画像の座標）
            px = int((u - x0b) / (x1b - x0b) * ctx.size[0])
            py = int((v - y0b) / (y1b - y0b) * ctx.size[1])
            assert x0 <= px < x1 and y0 <= py < y1 and ctx.assets.masks["subject"][py, px] > 0.99
            samples.append(float(weight[py - y0, px - x0]))
        fields.append(samples)
    assert fields[0] == pytest.approx(fields[1], rel=0.08)


def test_camera_center_is_in_original_image_coordinates(tmp_path):
    keys = [{"t": 0.0}, {"t": 2.0, "zoom": 1.2, "center": [0.56, 0.45]}]
    for name, ctx in contexts(tmp_path, lights=None, camera={"keys": keys}).items():
        width, height = ctx.size
        x0, y0, x1, y1 = source_box(ctx)
        source = np.array([(0.56 - x0) / (x1 - x0) * width - 0.5, (0.45 - y0) / (y1 - y0) * height - 0.5, 1.0])
        # 元画像の (0.56, 0.45) の画素が、t=2 に出力の中心へ来る（縦横比に依らない）
        assert ctx.path.matrix(2.0, 1.0) @ source == pytest.approx([width / 2 - 0.5, height / 2 - 0.5], abs=1.0), name   # cover は基準画像の画素格子に丸める（1 画素未満）
        assert fxcamera.is_identity(ctx.path.matrix(0.0, 1.0))


def test_original_coordinates_reduce_to_the_screen_when_the_aspect_matches(tmp_path):
    ctx = build(tmp_path, 320, 180)
    assert ctx.assets.box == (0.0, 0.0, 1.0, 1.0)
    assert to_screen(ctx.assets.box, 0.3, 0.7) == (0.3, 0.7) and width_scale(ctx.assets.box) == 1.0
    tall = contexts(tmp_path, lights=None)["tall"].assets.box
    assert to_screen(tall, 0.5, 0.5) == pytest.approx((0.5, 0.5)) and width_scale(tall) == pytest.approx(1 / (tall[2] - tall[0]))
