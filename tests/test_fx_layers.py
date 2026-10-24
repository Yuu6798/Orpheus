import copy

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from fx_synth import FULL, make_project  # noqa: E402
from mv_pipeline.fx import camera as fxcamera, layers as fxlayers, light as fxlight, render  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402

T = 1.0
PLANE = {"id": "subject", "mask": "subject", "z": 2, "parallax": 1.0}
FOG = {"id": "fog", "type": "fog", "z": 1, "color": [170, 175, 200], "opacity": 0.6, "scale": 0.25,
       "drift": [0.01, 0.0], "parallax": 0.6}
EMBERS = {"id": "embers", "type": "particles", "z": 3, "count": 600, "color": [255, 170, 80],
          "size_px": [4.0, 8.0], "velocity": [0.0, -0.06], "flicker": 0.4, "parallax": 1.3}
STILL = {"keys": [{"t": 0.0}]}


def layer_spec(planes=(PLANE,), effects=(), inpaint_px=3):
    return {"background": {"parallax": 0.4, "inpaint_px": inpaint_px},
            "planes": [dict(p) for p in planes], "effects": [dict(e) for e in effects]}


def build(tmp_path, width=160, height=90, **changes):
    return render.prepare(fxspec.load_spec(make_project(tmp_path, width, height, **changes)))


def changed(a, b):
    return (a != b).any(axis=2)


def core_of(ctx):
    """人物 plane が完全に不透明な画素（静止カメラのとき画面上の位置と一致する）。"""
    return ctx.planes[0].alpha >= 1.0


def test_fog_behind_the_subject_contributes_nothing_inside_its_opaque_core(tmp_path):
    clear = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[{**FOG, "opacity": 0.0}]))
    foggy = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[FOG]))
    a, b = render.frame(clear, T, "layers"), render.frame(foggy, T, "layers")
    core = core_of(foggy)
    assert core.sum() > 400
    assert not changed(a, b)[core].any()                      # z=1 の霧は z=2 の人物の芯では 0
    outside = ~(foggy.planes[0].alpha > 0)
    assert changed(a, b)[outside].mean() > 0.9                # 霧は背景にはちゃんと出ている


def test_fog_in_front_of_the_subject_does_cover_it(tmp_path):
    clear = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[{**FOG, "opacity": 0.0, "z": 3}]))
    front = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[{**FOG, "z": 3}]))
    a, b = render.frame(clear, T, "layers"), render.frame(front, T, "layers")
    assert changed(a, b)[core_of(front)].mean() > 0.9         # z を前にすれば人物の上にも載る


def test_embers_in_front_are_drawn_over_the_subject_but_behind_ones_are_not(tmp_path):
    none = build(tmp_path, camera=STILL, lights=None, layers=layer_spec())
    front = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[EMBERS]))
    behind = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(effects=[{**EMBERS, "z": 1}]))
    reference = render.frame(none, T, "layers")
    core = core_of(front)
    assert changed(reference, render.frame(front, T, "layers"))[core].sum() > 10    # 前: 人物の上にも描く
    assert not changed(reference, render.frame(behind, T, "layers"))[core].any()    # 後ろ: 人物に隠れる
    assert changed(reference, render.frame(behind, T, "layers"))[~core].any()


def test_layers_equals_base_at_t0_except_the_inpainted_ring(tmp_path):
    for camera in (STILL, {"keys": FULL["camera"]["keys"]}):                      # 動くカメラでも t=0 は恒等
        ctx = build(tmp_path, camera=camera, lights=None, layers=layer_spec())
        base, layered = render.frame(ctx, 0.0, "base"), render.frame(ctx, 0.0, "layers")
        diff = changed(base, layered)
        alpha, hole = ctx.planes[0].alpha, ctx.hole
        assert not diff[(hole == 0) | (alpha >= 1.0)].any()   # 穴の外と人物の芯は元画像どおり（羽根・穴埋めの帯を除く）
        assert diff.any() and diff.mean() < 0.25              # 帯では穴埋めに置き換わっている


def test_background_under_the_subject_is_a_fill_not_the_subject(tmp_path):
    ctx = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(inpaint_px=3))
    core = core_of(ctx)
    eroded = cv2.erode(core.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    plate, background = ctx.assets.plate, ctx.background
    ring = cv2.dilate(core.astype(np.uint8), np.ones((9, 9), np.uint8)) > 0
    ring &= ~core
    fill, subject, around = background[eroded].mean(axis=0), plate[eroded].mean(axis=0), plate[ring].mean(axis=0)
    assert np.linalg.norm(fill - subject) > 0.003                                # 人物の色のままではない
    assert np.linalg.norm(fill - around) < np.linalg.norm(subject - around)      # 周囲の色に近づいている
    assert (ctx.hole == 0).mean() > 0.5 and (ctx.hole > 0).mean() < 0.5          # 穴は人物の周りだけ
    assert np.array_equal(background[ctx.hole == 0], plate[ctx.hole == 0])       # 穴の外は元画像のまま


DEPTH_CAMERA = {"keys": FULL["camera"]["keys"], "parallax_px": 6, "parallax_dir": [1, 0]}


def test_layers_equals_base_at_t0_even_with_depth_parallax(tmp_path):
    ctx = build(tmp_path, 320, 180, camera=DEPTH_CAMERA, lights=None, layers=layer_spec())
    assert ctx.assets.depth is not None and ctx.path.parallax_px > 0
    assert ctx.path.depth_amplitude(0.0) == 0.0
    base, layered = render.frame(ctx, 0.0, "base"), render.frame(ctx, 0.0, "layers")
    diff = changed(base, layered)
    assert not diff[(ctx.hole == 0) | (ctx.planes[0].alpha >= 1.0)].any()   # 穴の外と人物の芯は base と同じ（深度視差を含めて恒等）
    flat = build(tmp_path, 320, 180, camera={"keys": FULL["camera"]["keys"]}, lights=None, layers=layer_spec())
    assert np.array_equal(base, render.frame(flat, 0.0, "base"))             # 深度視差を切った base と同じ = 元画像どおり


def test_depth_parallax_grows_from_zero_to_the_full_swing(tmp_path):
    ctx = build(tmp_path, 320, 180, camera=DEPTH_CAMERA, lights=None, layers=layer_spec())
    path = ctx.path
    amplitudes = [path.depth_amplitude(t) for t in np.linspace(0.0, 2.0, 9)]
    assert amplitudes[0] == 0.0 and amplitudes == sorted(amplitudes)
    assert amplitudes[-1] == pytest.approx(6 * 320 / 1920)                  # 全体の振れ幅は parallax_px（W/1920 換算）のまま
    still = {"keys": [{"t": 0.0}], "parallax_px": 6, "parallax_dir": [1, 0]}
    moving = build(tmp_path, 320, 180, camera=still, lights=None, layers=None)
    a, b = render.frame(moving, 0.0, "base"), render.frame(moving, 2.0, "base")
    assert changed(a, b).any()                                              # 最後は近い面が動いている（視差は働いている）


def test_planes_share_the_first_key_and_split_after_it(tmp_path):
    ctx = build(tmp_path, camera={"keys": FULL["camera"]["keys"]}, lights=None, layers=layer_spec())
    path = ctx.path
    for p in (0.0, 0.4, 1.0, 1.3):
        assert fxcamera.is_identity(path.matrix(0.0, p))
    t2 = [path.matrix(2.0, p) for p in (0.0, 0.4, 1.0, 1.3)]
    zooms = [float(m[0, 0]) for m in t2]
    assert zooms[0] == pytest.approx(1.0) and zooms[2] == pytest.approx(1.08) and zooms == sorted(zooms)
    assert len({round(float(m[0, 2]), 6) for m in t2}) == 4
    shifted = build(tmp_path, camera={"keys": [{"t": 0.0, "zoom": 1.2, "center": [0.4, 0.5]},
                                               {"t": 2.0, "zoom": 1.5, "center": [0.5, 0.5]}]},
                    lights=None, layers=layer_spec())
    start = [shifted.path.matrix(0.0, p) for p in (0.0, 0.4, 1.0)]
    assert all(np.allclose(m, start[0]) for m in start)                          # 最初のキーでは全 plane が重なる
    assert not np.allclose(shifted.path.matrix(2.0, 0.4), shifted.path.matrix(2.0, 1.0))


def test_drift_moves_a_plane_in_output_pixels(tmp_path):
    ctx = build(tmp_path, 320, 180, camera=STILL, lights=None, layers=layer_spec())
    m0 = ctx.path.matrix(2.0, 1.0)
    m1 = ctx.path.matrix(2.0, 1.0, drift_px=(10.0, -4.0))
    assert (m1[0, 2] - m0[0, 2], m1[1, 2] - m0[1, 2]) == pytest.approx((10.0 * 2.0 * 320 / 1920, -4.0 * 2.0 * 320 / 1920))


def test_subject_plane_tracks_the_full_camera_like_base(tmp_path):
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.15, "center": [0.56, 0.47]}]}
    ctx = build(tmp_path, 320, 180, camera=camera, lights=None, layers=layer_spec())
    a, b = render.frame(ctx, 2.0, "base"), render.frame(ctx, 2.0, "layers")
    matrix = ctx.path.matrix(2.0, 1.0)
    moved = cv2.warpAffine(ctx.planes[0].alpha, matrix, ctx.size, flags=cv2.INTER_LINEAR) >= 0.999
    core = cv2.erode(moved.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    assert core.sum() > 1000
    assert np.abs(a.astype(int) - b.astype(int))[core].max() <= 1                # p=1 の人物は base の絵と一致
    sky = np.zeros(ctx.size[::-1], dtype=bool)
    sky[:int(0.2 * 180), :40] = True
    assert changed(a, b)[sky].any()                                              # 背景 (p=0.4) は人物より動きが小さい


def test_effects_only_layers_use_the_plain_plate_as_background(tmp_path):
    ctx = build(tmp_path, camera=STILL, lights=None, layers=layer_spec(planes=(), effects=[FOG]))
    assert ctx.has_layers and not ctx.planes and ctx.hole is None
    assert ctx.background is ctx.assets.plate
    assert changed(render.frame(ctx, T, "layers"), render.frame(ctx, T, "base")).any()


def test_empty_or_missing_layers_make_every_variant_a_noted_copy_of_base(tmp_path):
    for layers in (None, layer_spec(planes=(), effects=())):
        ctx = build(tmp_path, lights=None, layers=layers)
        assert not ctx.has_layers
        for variant in ("layers", "both"):
            assert np.array_equal(render.frame(ctx, T, variant), render.frame(ctx, T, "base"))
        assert ctx.variant_notes()["layers"]["empty"] and ctx.variant_notes()["both"]["empty"]


def test_stack_is_sorted_by_z_with_planes_before_effects_on_ties(tmp_path):
    effects = [{**FOG, "id": "mist", "z": 2}, {**EMBERS, "id": "sparks", "z": 2, "count": 3}, {**FOG, "z": -1}]
    ctx = build(tmp_path, lights=None, layers=layer_spec(effects=effects))
    assert [item.id for item in ctx.stack] == ["fog", "subject", "mist", "sparks"]


def test_fog_and_particles_follow_the_seed(tmp_path):
    layers = layer_spec(effects=[FOG, {**EMBERS, "count": 80}])
    a = build(tmp_path, lights=None, layers=layers, seed=7)
    again = build(tmp_path, lights=None, layers=layers, seed=7)
    other = build(tmp_path, lights=None, layers=layers, seed=8)
    fa, fo = render.frame(a, T, "layers"), render.frame(other, T, "layers")
    assert np.array_equal(fa, render.frame(again, T, "layers"))
    assert changed(fa, fo).mean() > 0.2
    fog_a, fog_o = a.stack[0], other.stack[0]
    assert isinstance(fog_a, fxlayers.Fog) and not np.array_equal(fog_a.texture, fog_o.texture)
    particles_a = [i for i in a.stack if isinstance(i, fxlayers.Particles)][0]
    particles_o = [i for i in other.stack if isinstance(i, fxlayers.Particles)][0]
    assert not np.array_equal(particles_a.x0, particles_o.x0)


def test_whole_float_integers_render_like_ints(tmp_path):
    ints = build(tmp_path, lights=None, layers=layer_spec(effects=[{**EMBERS, "count": 60}]))
    floats = build(tmp_path, lights=None, layers=layer_spec(effects=[{**EMBERS, "count": 60.0}]))   # 検証は通り、描画も落ちない
    assert np.array_equal(render.frame(ints, T, "both"), render.frame(floats, T, "both"))


def test_fog_texture_is_normalized_and_periodic(tmp_path):
    ctx = build(tmp_path, 320, 180, lights=None, layers=layer_spec(effects=[FOG]))
    texture = ctx.stack[0].texture
    assert texture.dtype == np.float32 and texture.min() == pytest.approx(0.0) and texture.max() == pytest.approx(1.0)
    inner = np.abs(np.diff(texture, axis=1)).mean()
    assert np.abs(texture[:, 0] - texture[:, -1]).mean() < 3 * inner + 1e-3      # 端と端がつながる（ずらしても継ぎ目なし）
    assert np.abs(texture[0] - texture[-1]).mean() < 3 * np.abs(np.diff(texture, axis=0)).mean() + 1e-3


def test_particles_move_by_velocity_and_wrap(tmp_path):
    effect = {**EMBERS, "count": 50, "velocity": [0.0, -0.1], "parallax": 0.0}
    ctx = build(tmp_path, 320, 180, camera=STILL, lights=None, layers=layer_spec(effects=[effect]))
    particles = ctx.stack[-1]
    x0, y0 = fxlayers.particle_positions(particles, ctx.path, 0.0, ctx.size)
    x1, y1 = fxlayers.particle_positions(particles, ctx.path, 2.0, ctx.size)
    assert np.allclose(x0, x1)
    assert np.allclose(((y0 + 0.5) / 180 - 0.2) % 1.0, (y1 + 0.5) / 180)         # 2 秒で 0.2 だけ上へ（画面端で回り込む）
    assert ((y1 >= -0.5) & (y1 < 180 - 0.5)).all()
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "center": [0.6, 0.5]}]}
    moving = build(tmp_path, 320, 180, camera=camera, lights=None, layers=layer_spec(effects=[{**effect, "parallax": 1.0}]))
    mx0, _ = fxlayers.particle_positions(moving.stack[-1], moving.path, 0.0, moving.size)
    mx1, _ = fxlayers.particle_positions(moving.stack[-1], moving.path, 2.0, moving.size)
    assert np.allclose((mx0 / 320 + 0.5 / 320 - 0.1) % 1.0, (mx1 + 0.5) / 320)    # カメラが右へ → 画面では左へ流れる


def emitter_light(position, **emitter):
    """明滅なし・受光なしで、光点だけを描く光（深度ゲートなし）。"""
    light = copy.deepcopy(FULL["lights"][0])
    light.update(position=position, receivers=[], intensity=1.0, flicker={"amount": 0.0, "hz": 5.0})
    light["emitter"] = {"size": 0.03, "glow": 0.5, **emitter}
    del light["depth"]
    return light


def host_of(tmp_path, position, planes=(PLANE,), **emitter):
    return build(tmp_path, camera=STILL, layers=layer_spec(planes=planes),
                 lights=[emitter_light(position, **emitter)]).lights[0].host


def test_emitter_rides_the_plane_within_its_reach(tmp_path):
    # size 0.03 → 影響半径 4×size = 画像幅の 0.12。人物の右の縁（y=0.5 で x≈0.49）から 0.07 離れた点は半径内、0.15 は外
    assert host_of(tmp_path, [0.4, 0.5]) == "subject"                              # 人物の上
    assert host_of(tmp_path, [0.56, 0.5]) == "subject"                             # マスクの外でも、半径内に人物がある
    assert host_of(tmp_path, [0.64, 0.5]) is None                                  # 半径内に人物がない → 背景
    assert host_of(tmp_path, [0.8, 0.2]) is None                                   # 空の上 → 背景と一緒
    assert host_of(tmp_path, [0.64, 0.5], size=0.05) == "subject"                  # 半径は size に比例する


def test_emitter_host_prefers_the_front_most_plane_and_attach_overrides(tmp_path):
    floor = {"id": "floor", "mask": "ground", "z": 1}
    both = lambda z: (PLANE, {**floor, "z": z})  # noqa: E731
    assert host_of(tmp_path, [0.4, 0.62], planes=both(1)) == "subject"            # 人物（z=2）と地面（z=1）の境 → 前の人物
    assert host_of(tmp_path, [0.4, 0.62], planes=both(3)) == "floor"              # z を逆にすれば地面
    assert host_of(tmp_path, [0.4, 0.62], planes=both(2)) == "floor"              # 同じ z は宣言の後ろ（描画で手前）
    assert host_of(tmp_path, [0.4, 0.5], attach="background") is None             # 明示した背景が優先
    assert host_of(tmp_path, [0.9, 0.1], attach="subject") == "subject"           # 明示した plane は距離に依らない


def glow_diff(tmp_path, t, variant, position, camera, width=320, height=180, **emitter):
    """光点だけの寄与（光あり − 光 0）の輝度合計の画像と、光ありの ctx。"""
    light = emitter_light(position, **emitter)
    dark = copy.deepcopy(light)
    dark["intensity"] = 0.0
    lit = build(tmp_path, width, height, camera=camera, layers=layer_spec(), lights=[light])
    off = build(tmp_path, width, height, camera=camera, layers=layer_spec(), lights=[dark])
    delta = render.frame(lit, t, variant).astype(int) - render.frame(off, t, variant).astype(int)
    return delta.sum(axis=2), lit


def centroid(diff, floor=30):
    ys, xs = np.nonzero(diff > floor)
    return float(xs.mean()), float(ys.mean())


def test_hosted_emitter_matches_the_flat_light_version(tmp_path):
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.15, "center": [0.56, 0.47]}]}
    source = np.array([0.4 * 320 - 0.5, 0.5 * 180 - 0.5, 1.0])
    for t in (0.0, 1.0, 2.0):
        both, ctx = glow_diff(tmp_path, t, "both", [0.4, 0.5], camera, glow=5.0)
        flat, _ = glow_diff(tmp_path, t, "light", [0.4, 0.5], camera, glow=5.0)
        assert ctx.lights[0].host == "subject"
        cx, cy = centroid(both)
        assert (cx, cy) == pytest.approx(centroid(flat), abs=0.3)                  # light 版と光点の中心が一致
        assert (cx, cy) == pytest.approx(tuple((ctx.path.matrix(t, 1.0) @ source)[:2]), abs=1.0)   # p=1 の plane の動き
        assert both.max() == pytest.approx(flat.max(), abs=6)                      # 芯の見え方もそろう（ソフトニー）
        assert (both > 0).sum() == pytest.approx((flat > 0).sum(), rel=0.03)


def test_hosted_emitter_core_goes_through_the_same_soft_knee_as_the_flat_route(tmp_path):
    light = emitter_light([0.4, 0.5], size=0.05, glow=1.5)
    light["intensity"] = 2.0                                                        # 芯は 0.8 を大きく超え、裾は 0.8 の手前に入る
    ctx = build(tmp_path, camera=STILL, layers=layer_spec(), lights=[light])
    both, flat, base = (render.frame(ctx, T, v).astype(int) for v in ("both", "light", "base"))
    core = core_of(ctx)
    lit = (flat > base).any(axis=2) & core
    knee = lit & (flat.max(axis=2) > 200) & (flat.max(axis=2) < 250)               # ソフトニーが効いている帯
    assert lit.sum() > 50 and knee.sum() > 10
    assert np.abs(both - flat)[core].max() <= 2                                     # 加算の素通しではなく flat と同じ圧縮


def test_hosted_glow_over_diffuse_goes_through_one_soft_knee_like_the_flat_route(tmp_path):
    light = emitter_light([0.4, 0.5], size=0.05, glow=1.5)
    light["receivers"] = [{"mask": "subject", "gain": 1.5}]                         # 受光と光点が人物の上で重なる
    light["intensity"] = 2.0                                                        # 重なりは 0.8 を大きく超える（ソフトニーが効く）
    ctx = build(tmp_path, 320, 180, camera=STILL, layers=layer_spec(), lights=[light])   # 静止・深度視差なし
    assert ctx.lights[0].host == "subject" and ctx.path.parallax_px == 0
    both, flat = (render.frame(ctx, T, v).astype(int) for v in ("both", "light"))
    lt = ctx.lights[0]
    diffuse = np.zeros(ctx.size[::-1], dtype=bool)
    glow = np.zeros_like(diffuse)
    y0, y1, x0, x1 = lt.roi_main
    diffuse[y0:y1, x0:x1] = lt.receivers[0].diffuse_w > 0
    y0, y1, x0, x1 = lt.roi_glow
    glow[y0:y1, x0:x1] = lt.glow_w > 0
    (active,) = fxlight.levels(ctx.lights, T)
    add, _ = fxlight.compose([active], ctx.assets.plate, ctx.size, None, "all")
    hot = ((ctx.assets.plate + add) > 0.8).any(axis=2)                              # 合計がニーに入る画素
    overlap = core_of(ctx) & diffuse & glow & hot
    assert overlap.sum() > 50                                                       # 検査する重なりが十分ある
    assert np.abs(both - flat)[overlap].max() <= 1                                  # 二重にニーを掛けず、flat と同じ 1 回の合成


def test_attach_decides_which_layer_hides_the_emitter(tmp_path):
    spot = dict(glow=2.0, size=0.015)                                               # 影響半径 0.06 = 人物の幅の内側
    on_plane, _ = glow_diff(tmp_path, T, "both", [0.4, 0.5], STILL, **spot)                        # 既定: 人物の層
    behind, ctx = glow_diff(tmp_path, T, "both", [0.4, 0.5], STILL, attach="background", **spot)   # 背景に付けた
    flat, _ = glow_diff(tmp_path, T, "light", [0.4, 0.5], STILL, **spot)
    assert ctx.lights[0].host is None
    assert on_plane.max() >= 0.9 * flat.max()                                       # 載った層は自分の層に隠れない
    assert behind.max() == 0                                                        # 背景に付ければ人物（前の層）の後ろに隠れる


def test_emitter_on_a_plane_is_not_covered_by_effects_behind_it(tmp_path):
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.15, "center": [0.56, 0.47]}]}
    light = emitter_light([0.4, 0.5], glow=2.0)
    dark = copy.deepcopy(light)
    dark["intensity"] = 0.0
    dense_fog = {**FOG, "opacity": 1.0}                                             # z=1（人物の後ろ）の濃い霧
    lit = build(tmp_path, camera=camera, layers=layer_spec(effects=[dense_fog]), lights=[light])
    off = build(tmp_path, camera=camera, layers=layer_spec(effects=[dense_fog]), lights=[dark])
    delta = (render.frame(lit, 2.0, "both").astype(int) - render.frame(off, 2.0, "both").astype(int)).sum(axis=2)
    clear, _ = glow_diff(tmp_path, 2.0, "both", [0.4, 0.5], camera, glow=2.0)
    assert delta.max() >= 0.95 * clear.max()


def test_emitter_on_the_subject_moves_with_the_plane_not_the_background(tmp_path):
    light = emitter_light([0.4, 0.5], glow=0.5)
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.15, "center": [0.56, 0.47]}]}
    ctx = build(tmp_path, 320, 180, camera=camera, layers=layer_spec(), lights=[light])
    assert ctx.lights[0].host == "subject"
    diff, _ = glow_diff(tmp_path, 2.0, "both", [0.4, 0.5], camera, glow=0.5)
    ys, xs = np.nonzero(diff > 30)
    source = np.array([0.4 * 320 - 0.5, 0.5 * 180 - 0.5, 1.0])
    on_plane = ctx.path.matrix(2.0, 1.0) @ source
    on_background = ctx.path.matrix(2.0, 0.4) @ source
    assert np.hypot(*(on_plane[:2] - on_background[:2])) > 4
    assert np.hypot(xs.mean() - on_plane[0], ys.mean() - on_plane[1]) < 1.5       # 載っている層（人物）と一緒に動く


def test_both_composes_layers_and_light_and_degrades_exactly(tmp_path):
    ctx = build(tmp_path)
    layered, lit, both = (render.frame(ctx, 1.2, v) for v in ("layers", "light", "both"))
    assert changed(both, layered).any() and changed(both, lit).any() and changed(layered, lit).any()
    dark = copy.deepcopy(FULL["lights"])
    dark[0]["intensity"] = 0.0
    off = build(tmp_path, lights=dark)
    assert np.array_equal(render.frame(off, 1.2, "both"), render.frame(off, 1.2, "layers"))   # 光 0 → layers と完全一致
    no_layers = build(tmp_path, layers=None)
    assert np.array_equal(render.frame(no_layers, 1.2, "both"), render.frame(no_layers, 1.2, "light"))


def test_light_on_the_subject_plane_lights_that_plane_only(tmp_path):
    receivers = [{"mask": "subject", "gain": 1.0}]
    light = copy.deepcopy(FULL["lights"][0])
    light.update(receivers=receivers, intensity=1.0, flicker={"amount": 0.0, "hz": 5.0})
    del light["depth"], light["emitter"]
    ctx = build(tmp_path, camera=STILL, layers=layer_spec(), lights=[light])
    dark = copy.deepcopy(light)
    dark["intensity"] = 0.0
    off = build(tmp_path, camera=STILL, layers=layer_spec(), lights=[dark])
    diff = changed(render.frame(ctx, T, "both"), render.frame(off, T, "both"))
    assert diff.sum() > 100 and not diff[~(ctx.planes[0].alpha > 0)].any()        # 人物の外（背景）は受光に含まれない


def test_light_for_the_subject_never_lands_on_the_revealed_background(tmp_path):
    light = copy.deepcopy(FULL["lights"][0])
    light.update(receivers=[{"mask": "subject", "gain": 2.0}], intensity=1.0, flicker={"amount": 0.0, "hz": 5.0})
    del light["depth"], light["emitter"]
    camera = {"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.15, "center": [0.56, 0.47]}]}
    ctx = build(tmp_path, 320, 180, camera=camera, layers=layer_spec(), lights=[light])
    dark = copy.deepcopy(light)
    dark["intensity"] = 0.0
    off = build(tmp_path, 320, 180, camera=camera, layers=layer_spec(), lights=[dark])
    diff = changed(render.frame(ctx, 2.0, "both"), render.frame(off, 2.0, "both"))
    plane = cv2.warpAffine(ctx.planes[0].alpha, ctx.path.matrix(2.0, 1.0), ctx.size, flags=cv2.INTER_LINEAR) > 0
    plane = cv2.dilate(plane.astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    bg_shift = abs(float(ctx.path.matrix(2.0, 1.0)[0, 2] - ctx.path.matrix(2.0, 0.4)[0, 2]))
    assert bg_shift > 4 and diff.sum() > 300                   # 人物と背景は実際にずれていて、光は当たっている
    assert not (diff & ~plane).any()                            # 人物の形の光が背景（のぞいた部分）に残らない


def test_camera_leaving_the_image_is_reported_only_when_it_does(tmp_path):
    inside = build(tmp_path, lights=None)
    assert not any("はみ出" in w for w in inside.warnings)
    outside = build(tmp_path, lights=None, camera={"keys": [{"t": 0.0}, {"t": 2.0, "zoom": 1.1, "center": [0.9, 0.5]}]})
    assert any("はみ出" in w for w in outside.warnings)
