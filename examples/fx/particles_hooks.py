"""fx-timeline の hooks の見本: 粒が集まって形になる演出（`mv fx timeline render timeline.json --hooks examples/fx/particles_hooks.py`）。

`mv fx timeline demo` の timeline に合わせた値で、`mv_pipeline.fx.particles` の部品を掛ける。
- つないだ後（final）: 光の粒が画面の外周から渦を巻いて集まり、0.5 秒から 2.0 秒の間に、画面の中央に輪と横棒の形を作る。
  着いた後は、火の粉のように上へ流れて 1 秒で薄れる。ショットの区切りとつなぎには関係しない（画面に固定の題字と同じ置き方）。

形は、この見本が自分で作る合成のアルファ（輪と横棒）。実際の作品では、透過の題字カードのアルファ（2 次元の配列）を渡す。
行き先の点はアルファの画素座標で取れるので、正規化座標に直してから画面の大きさに合わせる（`--scale` を変えても同じ所に集まる）。
長さ（粒の太さ・渦・弧・昇る速さ）は幅 1920 の画面の画素で書き、k = 画面の幅 / 1920 を渡す。
絵に付けて動かすなら、shot の中で view.points(正規化座標) を points に、view.px を k に渡す。
値は見本なので、作品ごとに変える。
"""
import numpy as np

from mv_pipeline.fx import particles

CARD = (320, 180)                                        # 合成のアルファの大きさ（幅, 高さ）


def card():
    """合成のアルファ（0..1）: 輪と、その下の横棒。"""
    width, height = CARD
    yy, xx = np.mgrid[0:height, 0:width].astype(np.float32)
    distance = np.hypot(xx - 160, yy - 76)
    ring = (distance >= 36) & (distance <= 50)
    bar = (np.abs(xx - 160) <= 60) & (np.abs(yy - 150) <= 5)
    return (ring | bar).astype(np.float32)


UV = (particles.targets(card(), 1200, seed=7) + 0.5) / np.float32(CARD)      # 行き先（正規化座標。画素の中心が整数なので 0.5 を足す）
GATHER = {"t0": 0.5, "t1": 2.0, "origin": "ring", "stagger": 0.5, "swirl": 150, "lift": 60, "twinkle": 0.4, "gain": 1.5,
          "after": {"rise": 240, "fade_s": 1.0}, "seed": 7}


def final(img, t, ctx):
    width, height = ctx.size
    points = UV * np.float32([width, height]) - 0.5      # 正規化座標 → 画面の画素
    return particles.gather(img, t, GATHER, points, k=width / 1920)
