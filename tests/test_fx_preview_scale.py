"""下見（--scale）と本番の大きさで見え方が変わる部品（accent.smoke・accent.flicker・sky.lamps の小さな灯）と、
その部品だけ下見の大きさで計算して拡大する方法（docs/fx.md の hooks の節「下見（--scale）と本番の大きさで見え方が変わる部品」）。

素材はすべて合成（平らな画）。geo は基準座標 1672×941 の絵を画面いっぱいに写すもの。方法の関数（*_at）は docs に書いた手順を
そのまま書いたもので、部品は変えていない。docs の数（相関・濃さの比）は、ここと同じ設定で測った値。
"""
import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from mv_pipeline.fx import accent, sky  # noqa: E402
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
