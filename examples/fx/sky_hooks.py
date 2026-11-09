"""fx-timeline の hooks の見本: 空と天気・明かりの演出（`mv fx timeline render timeline.json --hooks examples/fx/sky_hooks.py`）。

`mv fx timeline demo` の timeline（夜の丘と人物の絵）に合わせた値で、`mv_pipeline.fx.sky` と `mv_pipeline.fx.accent` の部品を掛ける。
- 1 本目のショット（shot_a）: 丘の上の遠い灯（順に点く）と、差し出した手の暖色の照り返し。
- 2 本目のショット（shot_b）: 奥の空の稲光（深度で奥だけ明るくする）と稲妻の筋。
- つないだ後（final）: 暗い奥に薄い紫を足し（深度は view_at(t).depth()）、画面全体に雨を降らせる。timeline の look の前に掛かる。

位置と長さは実制作の設定と同じく基準座標（1672×941 の画素。元画像の幅と高さをこの数に当てる）で書き、geo = view.base(1672, 941)
で画面へ写す（カメラが寄っても絵に付いて動く。部品の中の決まった長さも、この大きさの画素を前提にしている）。
値は見本なので、作品ごとに変える。
"""
from mv_pipeline.fx import accent, sky

BASE = (1672, 941)
HILL_LAMPS = {"amt": 1.0, "lamps": [{"x": 134, "y": 560, "r": 7, "on": 0.3, "flick": 2.0},
                                    {"x": 251, "y": 572, "r": 6, "on": 0.6, "flick": 2.0},
                                    {"x": 1371, "y": 548, "r": 8, "on": 0.9, "flick": 2.0},
                                    {"x": 1505, "y": 556, "r": 6, "on": 1.2, "flick": 2.0}]}
HAND = {"x": 1020, "y": 470, "rx": 120, "ry": 90, "amt": 0.5, "mode": "mul", "seed": 4, "flick": 2.0}
LIGHTNING = {"t0": 1.25, "p0": [1250, 0], "p1": [1130, 430], "seed": 11, "flash": 0.55, "bolt": 1.0, "width": 2.4,
             "sky": "depth", "far": 0.32, "center": [1180, 200], "radius": 520, "depth": 6}
GRADE = {"amt": 1.0, "tint": [9, -2, 20], "far": 0.3, "top": 0.42}
RAIN = {"amt": 0.8, "density": 0.6, "angle": -0.12, "ambient": 0.2, "catch": 1.5}


def shot(img, t, view):
    geo = view.base(*BASE)
    if view.index == 0:
        sky.lamps(img, geo, t, HILL_LAMPS)
        accent.warm_light(img, geo, t, HAND)
    elif view.index == 1:
        # 稲光の時刻はショットの中の秒（view.t。shot_b は offset_s 1.0 なので 1.0 秒から始まる）で書く
        sky.lightning(img, geo, view.t, LIGHTNING, screen_depth=view.depth())
    return img


def final(img, t, ctx):
    img = sky.grade(img, GRADE, ctx.view_at(t).depth())
    return sky.rain(img, t, RAIN)
