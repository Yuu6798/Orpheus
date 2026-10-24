"""plane ごとの派生ズーム zoom_p = z0·(zoom(t)/z0)^p の性質。

旧式 z0 + (zoom − z0)·p は、ズームアウト（keys の zoom が下がる）で p > 1 の plane のスケールが 0 や負になり、
plane が潰れて反転し、背景の深度視差はそのスケールで割って壊れた。対数空間の式は常に正。
"""
import numpy as np
import pytest
from PIL import Image

cv2 = pytest.importorskip("cv2")

from fx_synth import make_project  # noqa: E402
from mv_pipeline.fx import VARIANTS, camera as fxcamera, layers as fxlayers, render  # noqa: E402
from mv_pipeline.fx import spec as fxspec  # noqa: E402

ZOOM_OUT = {"keys": [{"t": 0.0, "zoom": 2.0, "center": [0.5, 0.5]},
                     {"t": 2.0, "zoom": 1.0, "center": [0.5, 0.5]}],
            "parallax_px": 6, "parallax_dir": [1, 0]}
TIMES = [0.0, 0.5, 1.0, 1.5, 1.9, 2.0]
STRICT = dict(divide="raise", invalid="raise", over="raise")     # 0 割りや NaN は例外にして見逃さない


def camera_dict(keys, parallax_px=0, direction=(1, 0)):
    """spec.load_spec が作る正規化済みの camera 辞書（ease を補ったもの）。"""
    return {"keys": [{"ease": "linear", **k} for k in keys], "parallax_px": parallax_px,
            "parallax_dir": list(direction)}


def layer_spec(subject_p, ground_p, background_p, effects=()):
    planes = []
    if subject_p is not None:
        planes.append({"id": "subject", "mask": "subject", "z": 2, "parallax": subject_p})
    if ground_p is not None:
        planes.append({"id": "ground", "mask": "ground", "z": 1.5, "parallax": ground_p})
    return {"background": {"parallax": background_p, "inpaint_px": 3}, "planes": planes,
            "effects": [dict(e) for e in effects]}


def build(tmp_path, width=160, height=90, **changes):
    return render.prepare(fxspec.load_spec(make_project(tmp_path, width, height, **changes)))


@pytest.mark.parametrize("parallax", [0.0, 0.4, 1.0, 1.3, 2.0, 3.0, 4.0])
def test_derived_zoom_stays_positive_and_is_log_linear_in_parallax(parallax):
    path = fxcamera.CameraPath(camera_dict(ZOOM_OUT["keys"]), 2.0, (320, 180))
    for t in np.linspace(0.0, 2.0, 21):
        z = path.plane_view(float(t), parallax)[0]
        camera_zoom = path.at(float(t))[0]
        assert z > 0.0
        assert z == pytest.approx(2.0 * (camera_zoom / 2.0) ** parallax)          # z0·(zoom/z0)^p
        assert float(path.matrix(float(t), parallax)[0, 0]) == pytest.approx(z, rel=1e-6)
        assert path.plane_view(float(t), 1.0)[0] == pytest.approx(camera_zoom)    # p=1 はカメラそのもの
        assert path.plane_view(float(t), 0.0)[0] == pytest.approx(2.0)            # p=0 は z0 のまま
    assert path.plane_view(0.0, parallax)[0] == 2.0                               # t=0 は全 plane で z0（恒等）


def test_zoom_out_with_large_parallax_renders_every_variant_without_errors(tmp_path):
    for subject_p, ground_p, background_p in [(2.0, 4.0, 2.0), (4.0, 2.0, 4.0), (1.3, 3.0, 3.0)]:
        spec_layers = layer_spec(subject_p, ground_p, background_p)
        with np.errstate(**STRICT):
            ctx = build(tmp_path, 160, 90, camera=ZOOM_OUT, layers=spec_layers)
            assert ctx.assets.depth is not None and ctx.path.parallax_px > 0       # 背景に深度視差が掛かる
            for t in TIMES:
                for p in (subject_p, ground_p, background_p):
                    assert float(ctx.path.matrix(t, p)[0, 0]) > 0.0
                for variant in VARIANTS:
                    frame = render.frame(ctx, t, variant)
                    assert frame.shape == (90, 160, 3) and frame.dtype == np.uint8


def _halves(ctx, plane, axis):
    """plane のマスクを不透明部分の中央で axis 方向（1 = 左右、0 = 上下）に二分した 2 枚の Plane。"""
    occupied = np.nonzero(plane.alpha.max(axis=1 - axis) > 0.5)[0]
    mid = (int(occupied[0]) + int(occupied[-1]) + 1) // 2
    first, second = plane.alpha.copy(), plane.alpha.copy()
    if axis == 1:
        first[:, mid:], second[:, :mid] = 0.0, 0.0
    else:
        first[mid:, :], second[:mid, :] = 0.0, 0.0
    spec_plane = {"id": plane.id, "mask": plane.mask_id, "z": plane.z, "parallax": plane.parallax,
                  "drift_px": plane.drift_px}
    return [fxlayers.make_plane(spec_plane, alpha, ctx.assets.plate) for alpha in (first, second)]


def _centroid(alpha, axis):
    mass = float(alpha.sum())
    assert mass > 0.5                                       # 潰れて消えていない
    coords = np.arange(alpha.shape[axis], dtype=np.float64)
    return float((alpha.sum(axis=1 - axis) * coords).sum() / mass)


@pytest.mark.parametrize("parallax", [1.0, 2.0, 3.0, 4.0])
def test_zoomed_out_plane_keeps_its_orientation(tmp_path, parallax):
    ctx = build(tmp_path, 320, 180, camera=ZOOM_OUT, lights=None, layers=layer_spec(parallax, None, 0.4))
    plane = ctx.planes[0]
    for t in TIMES:
        for axis in (1, 0):
            first, second = _halves(ctx, plane, axis)
            a = fxlayers.plane_layer(first, ctx.path, t, ctx.size)[1]
            b = fxlayers.plane_layer(second, ctx.path, t, ctx.size)[1]
            assert _centroid(a, axis) < _centroid(b, axis)  # 左(上)半分は右(下)半分より左(上)のまま = 反転しない


@pytest.mark.parametrize("background_p", [2.0, 3.0, 4.0])
def test_background_with_depth_parallax_is_not_mirrored_when_zooming_out(tmp_path, background_p):
    fog = {"id": "fog", "type": "fog", "z": 1, "color": [170, 175, 200], "opacity": 0.0, "scale": 0.25,
           "drift": [0.01, 0.0], "parallax": 0.6}
    spec_path = make_project(tmp_path, 320, 180, camera=ZOOM_OUT, lights=None,
                             layers=layer_spec(None, None, background_p, [fog]))
    half = np.zeros((180, 320, 3), dtype=np.uint8)
    half[:, :160] = 230                                     # 左半分だけ明るい基準画像
    Image.fromarray(half).save(tmp_path / "base.png")
    with np.errstate(**STRICT):
        spec = fxspec.load_spec(spec_path)
        ctx = render.prepare(spec)
        assert ctx.assets.depth is not None and ctx.path.parallax_px > 0 and ctx.background is ctx.assets.plate
        for t in TIMES:
            luma = render.frame(ctx, t, "layers").astype(np.float64).mean(axis=(0, 2))
            centroid = float((luma * np.arange(320)).sum() / luma.sum())
            assert centroid < 160.0                         # 明るい左半分は左にある（ミラーなら右へ寄る）


def test_zoom_in_matches_the_old_linear_form_within_a_few_pixels():
    """デモ相当のズームイン（1.0→1.08）では、旧式との差は 1920 幅でも数 px 以内。"""
    keys = [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5]},
            {"t": 10.0, "zoom": 1.08, "center": [0.52, 0.48], "ease": "in_out"}]
    size = (1920, 1080)
    path = fxcamera.CameraPath(camera_dict(keys, 12), 10.0, size)
    points = np.array([[0, 0, 1], [1919, 0, 1], [0, 1079, 1], [1919, 1079, 1], [960, 540, 1]], dtype=np.float64).T
    worst = 0.0
    for t in np.linspace(0.0, 10.0, 41):
        for parallax in (0.0, 0.4, 0.6, 1.0, 1.3):
            z, cx, cy, _, _ = path.plane_view(float(t), parallax)
            old_z = path.z0 + (path.at(float(t))[0] - path.z0) * parallax
            new = fxcamera.affine(z, cx, cy, 0.0, 0.0, size).astype(np.float64)
            old = fxcamera.affine(old_z, cx, cy, 0.0, 0.0, size).astype(np.float64)
            worst = max(worst, float(np.abs(new @ points - old @ points).max()))
            if parallax in (0.0, 1.0):
                assert np.allclose(new, old, atol=1e-4)     # p=0（動かない）と p=1（カメラ）は旧式と同じ
    assert worst < 2.0                                      # p=0.4 / 0.6 / 1.3 の最大差（画素。実測は約 1.2px）
    assert worst > 0.0                                      # 式は実際に変わっている


def test_center_and_drift_keep_the_linear_form():
    moving = fxcamera.CameraPath(camera_dict([{"t": 0.0, "zoom": 2.0, "center": [0.4, 0.5]},
                                              {"t": 2.0, "zoom": 1.0, "center": [0.6, 0.45]}]), 2.0, (320, 180))
    z, x, y, dx, dy = moving.plane_view(2.0, 2.0, (10.0, -4.0))
    assert (x, y) == pytest.approx((0.4 + 0.2 * 2.0, 0.5 - 0.05 * 2.0))             # c0 + (c(t) − c0)·p のまま
    assert (dx, dy) == pytest.approx((10.0 * 2.0 * 320 / 1920, -4.0 * 2.0 * 320 / 1920))
    assert z == pytest.approx(2.0 * 0.5 ** 2.0)
