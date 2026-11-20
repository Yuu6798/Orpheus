"""下見（--scale）と本番の大きさで見え方が変わる部品（accent.smoke・accent.flicker・sky.lamps の小さな灯）と、
その部品だけ下見の大きさで計算して拡大する方法（docs/fx.md の hooks の節「下見（--scale）と本番の大きさで見え方が変わる部品」）。

素材はすべて合成（平らな画と、なめらかな模様に暖色の四角を置いた画）。geo は基準座標 1672×941 の絵を画面いっぱいに写すもの。
方法の関数（*_at）は docs に書いた手順をそのまま書いたもの。部品に基準の高さ（ref_h）を書くと、煙と dot の灯はこの関数と
バイト単位で同じ画になる（後半のテスト）。docs の数（相関・濃さの比）は、ここと同じ設定で測った値。
"""
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import accent, fire, sky  # noqa: E402
from mv_pipeline.fx.fire import screen, tex, tex_win  # noqa: E402

BASE_W, BASE_H = 1672, 941
PREVIEW_H, FINAL_H = 540, 1080
SMOKE = {"src": [836, 700], "w": 120, "h": 420, "amt": 0.8, "speed": 40}


class Geo:
    """基準座標の絵を高さ H の画面いっぱいに写す（画素の中心が整数。view.base と同じ写し方）。"""

    def __init__(self, H):
        self.k = H / BASE_H

    def pt(self, x, y):
        return ((x + 0.5) * self.k - 0.5, (y + 0.5) * self.k - 0.5)


class PreviewGeo:
    """画面の画素の点を下見の画面（s 倍）へ写す。docs: (X + 0.5)·s − 0.5、長さは k·s。"""

    def __init__(self, geo, s):
        self.geo, self.s, self.k = geo, s, geo.k * s

    def pt(self, x, y):
        X, Y = self.geo.pt(x, y)
        return ((X + 0.5) * self.s - 0.5, (Y + 0.5) * self.s - 0.5)


def flat(H, value=60.0):
    return np.full((H, int(round(H * 16 / 9)), 3), value, np.float32)


def preview_size(f, ref_h):
    H, W = f.shape[:2]
    s = ref_h / H
    return s, int(round(H * s)), int(round(W * s))


def smoke_at(f, geo, t, P, ref_h):
    """煙: 黒い画に白 1 で描くと値が濃さ × 0.55。それを拡大し、元の式 f·(1 − A) + col·A で混ぜる。"""
    if f.shape[0] <= ref_h:
        return accent.smoke(f, geo, t, P)
    s, h, w = preview_size(f, ref_h)
    probe = np.zeros((h, w, 3), np.float32)
    accent.smoke(probe, PreviewGeo(geo, s), t, dict(P, col=[1.0, 1.0, 1.0]))
    A = np.clip(cv2.resize(probe[..., 0], f.shape[1::-1], interpolation=cv2.INTER_LINEAR), 0, None)[..., None]
    c = np.array(P.get("col", [120, 125, 140]), np.float32)
    f[:] = f * (1 - A) + c[None, None] * A
    return f


def lamps_at(f, geo, t, P, ref_h):
    """灯（dot）: 黒い画に描いた光を拡大して、スクリーン合成する。"""
    if f.shape[0] <= ref_h:
        return sky.lamps(f, geo, t, P)
    s, h, w = preview_size(f, ref_h)
    layer = sky.lamps(np.zeros((h, w, 3), np.float32), PreviewGeo(geo, s), t, P)
    return screen(f, cv2.resize(layer, f.shape[1::-1], interpolation=cv2.INTER_LINEAR))


def flicker_noise(H, W, t):
    """accent.flicker のむら（sc は既定の 画の高さ / 1080）。"""
    return tex_win(tex(55, 512, 40, 60), t * 120 * (H / 1080.0), 9, H, W)


def shrink(a, like):
    return cv2.resize(a, like.shape[1::-1], interpolation=cv2.INTER_AREA)


def corr(a, b):
    return float(np.corrcoef(a.ravel(), b.ravel())[0, 1])


def smoke_density(H, t, ref_h=None):
    f = np.zeros_like(flat(H))
    P = dict(SMOKE, col=[1.0, 1.0, 1.0])
    if ref_h is None:
        accent.smoke(f, Geo(H), t, P)
    else:
        smoke_at(f, Geo(H), t, P, ref_h)
    return f[..., 0]


def lamp_light(H, r, ref_h=None):
    P = {"lamps": [{"x": 600, "y": 400, "r": r, "kind": "dot", "on": -1}]}
    f = np.zeros_like(flat(H))
    f = sky.lamps(f, Geo(H), 0.5, P) if ref_h is None else lamps_at(f, Geo(H), 0.5, P, ref_h)
    return f.mean(-1)


# ---- 方法の前提（部品の式を分ける） ------------------------------------------------------------------

def test_smoke_drawn_white_on_black_is_the_mix_weight():
    """煙を黒い画に白 1 で描いた値 A で混ぜると、元の画に煙を描いたのと同じになる（同じ大きさ）。"""
    base = flat(PREVIEW_H)
    base[:, :, 0] += np.linspace(0, 80, base.shape[1], dtype=np.float32)[None]          # 平らでない画
    probe = np.zeros_like(base)
    accent.smoke(probe, Geo(PREVIEW_H), 1.0, dict(SMOKE, col=[1.0, 1.0, 1.0]))
    A = probe[..., 0:1]
    assert np.allclose(probe[..., 0], probe[..., 1]) and 0 < A.max() <= 0.55 + 1e-6
    col = np.array([120, 125, 140], np.float32)
    want = accent.smoke(base.copy(), Geo(PREVIEW_H), 1.0, dict(SMOKE))
    assert np.abs(base * (1 - A) + col * A - want).max() < 1e-3


def test_dot_lamps_are_one_screen_layer():
    """dot の灯は、黒い画に描いた光をスクリーン合成したものと同じ（スクリーン合成は順に依らない）。"""
    P = {"lamps": [{"x": 600, "y": 400, "r": 9, "kind": "dot", "on": -1}, {"x": 640, "y": 410, "r": 4, "kind": "dot", "on": -1}]}
    base = flat(PREVIEW_H, 90.0)
    want = sky.lamps(base.copy(), Geo(PREVIEW_H), 0.5, P)
    layer = sky.lamps(np.zeros_like(base), Geo(PREVIEW_H), 0.5, P)
    assert np.abs(screen(base, layer) - want).max() < 1e-3


def test_preview_geo_maps_pixel_centres():
    geo = Geo(FINAL_H)
    small = PreviewGeo(geo, PREVIEW_H / FINAL_H)
    X, Y = geo.pt(836, 700)
    x, y = small.pt(836, 700)
    assert (x, y) == pytest.approx(Geo(PREVIEW_H).pt(836, 700)) and small.k == pytest.approx(Geo(PREVIEW_H).k)
    assert ((x + 0.5) * 2 - 0.5, (y + 0.5) * 2 - 0.5) == pytest.approx((X, Y))


# ---- 大きさで見え方が変わること（docs の数） ----------------------------------------------------------

@pytest.mark.parametrize("t", [0.0, 1.0, 2.5])
def test_smoke_changes_with_the_output_size(t):
    preview, final = smoke_density(PREVIEW_H, t), smoke_density(FINAL_H, t)
    small = shrink(final, preview)
    assert 0.5 < corr(preview, small) < 0.65                                   # 形が変わる（docs: 0.56〜0.58）
    assert 1.15 < small.sum() / preview.sum() < 1.75                           # 濃さの合計（docs: 1.2〜1.7 倍）


@pytest.mark.parametrize("t", [0.0, 1.0])
def test_flicker_noise_is_another_pattern_at_another_size(t):
    m = 0.11 * 2.5                                                             # 揺れの式のむらの係数（amt 0.11）
    preview = m * (flicker_noise(PREVIEW_H, int(round(PREVIEW_H * 16 / 9)), t) - 0.5)
    final = m * (flicker_noise(FINAL_H, int(round(FINAL_H * 16 / 9)), t) - 0.5)
    small = shrink(final, preview)
    assert abs(corr(preview, small)) < 0.15                                   # 別の模様（docs: −0.08〜−0.07）
    assert final.std() == pytest.approx(preview.std(), rel=0.05)              # 揺れの強さは同じ


def test_flicker_part_uses_the_same_noise():
    """accent.flicker の明るい暖色の所の揺れは、上の flicker_noise と正弦の和で決まる（docs の「揺らぎ」の分け方の前提）。"""
    f = flat(PREVIEW_H)
    f[:] = np.array([220, 140, 60], np.float32)
    t = 1.0
    gain = accent.flicker(f.copy(), Geo(PREVIEW_H), t, {"amt": 0.11})[..., 0] / f[..., 0]
    nz = flicker_noise(*f.shape[:2], t)
    m = np.clip((220 - 60 - 40) / 80.0, 0, 1) * np.clip((f.mean() - 60) / 80.0, 0, 1)
    want = 1 + m * 0.11 * ((nz - 0.5) * 2.5 + 0.6 * math.sin(t * 11.3) + 0.4 * math.sin(t * 6.1))
    assert np.abs(gain - want).max() < 1e-5


@pytest.mark.parametrize("r, low, high", [(4, 0.75, 0.85), (8, 0.98, 1.02), (20, 0.98, 1.02)])
def test_small_lamps_are_brighter_in_the_preview(r, low, high):
    preview, final = lamp_light(PREVIEW_H, r), lamp_light(FINAL_H, r)
    assert low < shrink(final, preview).sum() / preview.sum() < high            # docs: 半径 4 で 0.80 倍、8 と 20 は 1.00 倍


# ---- 下見の大きさで計算して拡大すると、下見の見え方に戻る ------------------------------------------------

@pytest.mark.parametrize("t", [0.0, 1.0, 2.5])
def test_smoke_computed_at_the_preview_height_keeps_the_preview_look(t):
    preview = smoke_density(PREVIEW_H, t)
    kept = shrink(smoke_density(FINAL_H, t, ref_h=PREVIEW_H), preview)
    assert corr(preview, kept) > 0.99 and kept.sum() / preview.sum() == pytest.approx(1.0, abs=0.02)
    assert np.array_equal(smoke_density(PREVIEW_H, t, ref_h=PREVIEW_H), preview)        # 下見の大きさでは元のまま


@pytest.mark.parametrize("r", [4, 9])
def test_lamps_computed_at_the_preview_height_keep_the_preview_look(r):
    preview = lamp_light(PREVIEW_H, r)
    kept = shrink(lamp_light(FINAL_H, r, ref_h=PREVIEW_H), preview)
    assert corr(preview, kept) > 0.99 and kept.sum() / preview.sum() == pytest.approx(1.0, abs=0.02)
    assert np.array_equal(lamp_light(PREVIEW_H, r, ref_h=PREVIEW_H), preview)


def test_flicker_noise_drawn_at_the_preview_height_keeps_the_pattern():
    h, w = PREVIEW_H, int(round(PREVIEW_H * 16 / 9))
    preview = flicker_noise(h, w, 1.0)
    kept = cv2.resize(preview, (int(round(FINAL_H * 16 / 9)), FINAL_H), interpolation=cv2.INTER_LINEAR)
    assert corr(preview, shrink(kept, preview)) > 0.99


# ---- 部品に基準の高さ（ref_h）を書く ------------------------------------------------------------------

def busy(H):
    """平らでない画（なめらかな模様と、明るい暖色の四角。揺れ・紙の灯が掛かる所）。"""
    W = int(round(H * 16 / 9))
    y, x = np.mgrid[0:H, 0:W].astype(np.float32)
    f = np.stack([90 + 60 * np.sin(x / W * 6.3), 70 + 40 * np.cos(y / H * 5.1), 60 + 30 * np.sin((x + y) / W * 4)], -1)
    f[int(H * .3):int(H * .5), int(W * .4):int(W * .55)] = (230, 170, 80)
    return f.astype(np.float32)


def blade(H):
    geo = Geo(H)
    points = np.array([geo.pt(x, y) for x, y in [[700, 600], [760, 510], [830, 400], [900, 300]]], np.float32)
    return fire.Blade(points=points, s=np.array([0.0, 0.33, 0.66, 1.0], np.float32),
                      widths=np.array([14, 12, 9, 6], np.float32) * geo.k, k=geo.k, px=H / 1080)


def at(H):
    W = H * 16 / 9
    return lambda u: (0.3 * W + u * 0.4 * W - 0.5, 0.7 * H - 0.5)


def opt(P, ref_h):
    return P if ref_h is None else dict(P, ref_h=ref_h)


T = 1.3
LAMP4 = {"lamps": [{"x": 600, "y": 400, "r": 4, "on": -1}]}
LAMP_BOX = {"lamps": [{"x": 800, "y": 380, "r": 30, "kind": "box", "box": [700, 300, 900, 450], "on": -1}, {"x": 600, "y": 400, "r": 4, "on": -1}]}
FLICKER = {"amt": 0.11, "region": [600, 250, 1000, 520]}
PARTS = {   # 名前 → (描き方, 本番を縮めたときの下限の相関, |差| の合計の比の範囲)
    "smoke": (lambda f, H, r: accent.smoke(f, Geo(H), T, opt(SMOKE, r)), 0.99, (0.98, 1.02)),
    "flicker": (lambda f, H, r: accent.flicker(f, Geo(H), T, opt(FLICKER, r)), 0.99, (0.98, 1.02)),
    "lamps": (lambda f, H, r: sky.lamps(f, Geo(H), T, opt(LAMP4, r)), 0.99, (0.98, 1.02)),
    "lamps_box": (lambda f, H, r: sky.lamps(f, Geo(H), T, opt(LAMP_BOX, r)), 0.99, (0.98, 1.02)),
    "embers": (lambda f, H, r: fire.embers(f, at(H), T, 5, 60, 1.5, k=H / 1080, ref_h=r), 0.95, (0.97, 1.03)),
    "rain": (lambda f, H, r: sky.rain(f, T, opt({"amt": 1.0}, r)), 0.99, (0.97, 1.03)),
    "blade_fire": (lambda f, H, r: fire.blade_fire(f, blade(H), T, opt({"h": 60, "embers": 12, "patch": 0.3, "heat": 0.3}, r)),
                   0.99, (0.97, 1.03)),
    "steel": (lambda f, H, r: accent.steel(f, blade(H), T, opt({"grain": 0.1, "spec": 0.35, "phase": 0.5}, r)), 0.99, (0.9, 1.02)),
}


def part_delta(name, H, ref_h=None):
    draw = PARTS[name][0]
    f = busy(H)
    return (draw(f.copy(), H, ref_h) - f).mean(-1)


def against_preview(name, final):
    preview = part_delta(name, PREVIEW_H)
    small = shrink(final, preview)
    return corr(preview, small), float(np.abs(small).sum() / np.abs(preview).sum())


@pytest.mark.parametrize("name", list(PARTS))
def test_ref_h_keeps_the_preview_look_at_the_final_size(name):
    """本番の大きさに ref_h = 下見の高さで描いて縮めると、下見と同じ見え方（書かないと、煙・揺らぎ・小さな灯・火の粉・雨・
    刃の火・鋼の質感は形か量が変わる）。"""
    least, (low, high) = PARTS[name][1:]
    c, ratio = against_preview(name, part_delta(name, FINAL_H, PREVIEW_H))
    assert c > least and low < ratio < high
    if name != "lamps_box":                                             # 紙の灯は大きさに依らない（芯の小さな灯が混ざるだけ）
        c0, ratio0 = against_preview(name, part_delta(name, FINAL_H))
        assert c0 < 0.95 or not 0.95 < ratio0 < 1.05


@pytest.mark.parametrize("name", list(PARTS))
def test_ref_h_equal_to_the_height_is_the_part_unchanged(name):
    assert np.array_equal(part_delta(name, PREVIEW_H, PREVIEW_H), part_delta(name, PREVIEW_H))


@pytest.mark.parametrize("t", [0.0, 1.0, 2.5])
def test_smoke_ref_h_is_the_method_above(t):
    P = dict(SMOKE, drift=0.4, col=[70, 75, 96])
    want = smoke_at(busy(FINAL_H), Geo(FINAL_H), t, P, PREVIEW_H)
    assert np.array_equal(accent.smoke(busy(FINAL_H), Geo(FINAL_H), t, dict(P, ref_h=PREVIEW_H)), want)


@pytest.mark.parametrize("r", [4, 9])
def test_dot_lamps_ref_h_is_the_method_above(r):
    P = {"lamps": [{"x": 600, "y": 400, "r": r, "on": -1}, {"x": 900, "y": 300, "r": 6, "on": 0.2, "flick": 2.0, "col": [255, 180, 90]}]}
    want = lamps_at(busy(FINAL_H), Geo(FINAL_H), 0.5, P, PREVIEW_H)
    assert np.array_equal(sky.lamps(busy(FINAL_H), Geo(FINAL_H), 0.5, dict(P, ref_h=PREVIEW_H)), want)


def test_flicker_ref_h_draws_the_noise_at_the_reference_height():
    """むらを基準の高さで引いて拡大し、揺れの式は本番の画に掛ける（docs の「揺らぎ」の手順。範囲なし）。"""
    f = busy(FINAL_H)
    got = accent.flicker(f.copy(), Geo(FINAL_H), T, {"amt": 0.11, "ref_h": PREVIEW_H})
    w = int(round(FINAL_H * 16 / 9 * PREVIEW_H / FINAL_H))
    nz = cv2.resize(flicker_noise(PREVIEW_H, w, T), (f.shape[1], f.shape[0]), interpolation=cv2.INTER_LINEAR)
    m = np.clip((f[..., 0] - f[..., 2] - 40) / 80.0, 0, 1) * np.clip((f.mean(-1) - 60) / 80.0, 0, 1)
    fl = 1 + 0.11 * ((nz - 0.5) * 2.5 + 0.6 * math.sin(T * 11.3) + 0.4 * math.sin(T * 6.1))
    assert np.array_equal(got, f * (1 + (fl - 1) * m)[..., None])


@pytest.mark.parametrize("draw", [
    lambda f, sc, P: accent.flicker(f, Geo(FINAL_H), T, P, sc),
    lambda f, sc, P: sky.rain(f, T, dict(P, amt=1.0), sc),
])
def test_ref_h_converts_an_explicit_sc_to_the_reference_height(draw):
    """sc（流れる速さ・長さの倍率。実制作は出力の高さ / 1080）を渡しても、基準の高さへ同じ割合で直す。"""
    P = {"amt": 0.11, "ref_h": PREVIEW_H}
    assert np.array_equal(draw(busy(FINAL_H), FINAL_H / 1080, P), draw(busy(FINAL_H), None, P))


def test_ref_h_above_the_height_draws_large_and_shrinks():
    """基準の高さが画より高いときは、基準の高さで描いて縮める（下見を本番の見え方に寄せる）。"""
    final = smoke_density(FINAL_H, 1.0)
    f = np.zeros_like(flat(PREVIEW_H))
    accent.smoke(f, Geo(PREVIEW_H), 1.0, dict(SMOKE, col=[1.0, 1.0, 1.0], ref_h=FINAL_H))
    small = shrink(final, f[..., 0])
    assert corr(f[..., 0], small) > 0.99 and f[..., 0].sum() / small.sum() == pytest.approx(1.0, abs=0.02)


@pytest.mark.parametrize("value", [0, 1.5, float("nan"), float("inf"), -540])
def test_ref_h_must_be_a_height(value):
    with pytest.raises(ValueError, match="ref_h"):
        accent.smoke(busy(PREVIEW_H), Geo(PREVIEW_H), 1.0, dict(SMOKE, ref_h=value))
