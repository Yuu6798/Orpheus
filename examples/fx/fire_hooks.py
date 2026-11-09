"""fx-timeline の hooks の見本: 火の演出（`mv fx timeline render timeline.json --hooks examples/fx/fire_hooks.py`）。

`mv fx timeline demo` の timeline に合わせた値で、`mv_pipeline.fx.fire` の部品を掛ける。
- 1 本目のショット（shot_a）: 人物が掲げる炎の位置に、小さな炎と立ち上る火の粉。
- 3 本目のショット（shot_c）: 地面の上の短い刃に沿って燃える火（刃の火）と、切っ先の小さな炎。

位置は元画像の正規化座標（0..1）、長さは元画像の幅の割合で書き、view で画面の画素へ写す（カメラが寄っても絵に付いて動く）。
実制作の設定のように基準座標（例えば 1672×941 の画素）で書くなら、geo = view.base(1672, 941) の geo.pt(x, y)・geo.k を使う。
値は見本なので、作品ごとに変える。
"""
from mv_pipeline.fx import fire

FLAME = (0.61, 0.43)                                     # デモの白い炎の位置（元画像の正規化座標）
BLADE = [(0.70, 0.80), (0.76, 0.76), (0.82, 0.72)]       # 刃の点列（根元 → 切っ先。元画像の正規化座標）


def shot(img, t, view):
    if view.index == 0:
        X, Y = view.point(*FLAME)
        k = view.scale / 1000                            # 元画像の幅を 1000 とした長さ → 画面の画素
        fire.candle(img, X, Y, t, 40 * k, seed=5, white=0.4, sway=0.1, glow_amt=0.4)
        fire.embers(img, lambda u: (X + (u - 0.5) * 12 * k, Y - 10 * k), t, seed=7, n=24, cycle=1.6, k=k * 1.5)
    elif view.index == 2:
        blade = view.blade(BLADE, 0.006, 0.003)          # 幅は元画像の幅の割合（根元・切っ先）
        fire.blade_fire(img, blade, t, {"h": 0.04, "cov": [0.1, 1.0], "patch": 0.3, "heat": 0.3, "embers": 10, "seed": 3})
        fire.blade_candle(img, blade, t, {"at": 0.9, "h": 0.03, "seed": 9})
    return img
