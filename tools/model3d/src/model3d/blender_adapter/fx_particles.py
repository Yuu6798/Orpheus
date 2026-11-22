"""飛び散る粒: 出来事の点から、向きと速さを種で決めて飛び散り、空気の抵抗で遅くなりながら重力で落ちる。動く向きに伸びる筋
（短い間に動いた長さが粒の大きさより短ければ、カメラを向いた四角形）。numpy だけを使う（render.show のフックが Blender の中で
`import fx_particles` で読む。CI でも試せる）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、飛び散る粒の作り方を、同じ式・同じ乱数の引き方で移した。
`fx_shapes` の「はじけ」（render.passes の効果。種から作り置いた numpy の乱数で、画面の上の速さで筋の長さを決め、色を出す）とは、
乱数の引き方と筋の作り方が違う。こちらは出来事ごとの点・粒ごとの寿命・重力・床の高さを持ち、強さ（足し算で光る材質が掛ける数）を出す。

    draw(table, event, f, camera, quads, gain=1.0)

出来事の欄（既定の無い欄は書く）:
- `at`: 出どころ（始まりのコマの点）。`count`: 粒の数。`speed`: 速さの範囲 [下, 上]（m/s）。
- `up`: 向きの上への寄せ（標準正規の向きの z に足す。負なら下へ）。`life`: 寿命の目安（秒。粒ごとに × 0.5〜1.6）。
- `drag`: 空気の抵抗（1/s）。`gravity`: 重力の加速度（m/s²）。`floor`: これより下へは行かない高さ（m。省くと止めない）。
- `streak`: 筋の長さ（何秒前の位置からの筋か）。`size`: 粒の大きさ（m）。`seed`: 種（整数）。`gain`: 強さの倍率（既定 1）。

効く間は、始まりから life × 1.6 秒。粒の明るさは (1 − 年齢 ÷ その粒の寿命)^0.7。
"""
import math

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import fx_events
except ImportError:   # Blender が台本として動かすとき
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fx_events

FORM = {"own_life": (0.5, 1.6), "fade": 0.7, "tail_width": 0.6, "tail_value": 0.4}


def life(event):
    return float(fx_events.need(event, "life")) * FORM["own_life"][1]


def draw(table, event, f, camera, quads, gain=1.0):
    """粒を quads（fx_strips.Quads）にためる。描いた粒の数を返す。"""
    if not table.within(event, f, 0.0, life(event)):
        return 0
    age = table.age(event, f)
    origin = table.point(fx_events.need(event, "at"), table.start(event), camera)
    rng = fx_events.rng(int(fx_events.need(event, "seed")), 8)
    low, high = (float(v) for v in fx_events.need(event, "speed"))
    up, span = float(fx_events.need(event, "up")), float(event["life"])
    drag, gravity = float(fx_events.need(event, "drag")), float(fx_events.need(event, "gravity"))
    floor, streak, size = event.get("floor"), float(fx_events.need(event, "streak")), float(fx_events.need(event, "size"))
    level0 = float(event.get("gain", 1.0)) * gain
    drawn = 0
    for _ in range(int(fx_events.need(event, "count"))):
        direction = np.array([rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1) + up])
        direction = direction / float(np.linalg.norm(direction))
        speed, own = rng.uniform(low, high), span * rng.uniform(*FORM["own_life"])
        if age > own:
            continue

        def where(tau):
            travel = (1.0 - math.exp(-drag * tau)) / drag
            p = origin + direction * speed * travel + np.array([0.0, 0.0, -0.5 * gravity * tau * tau])
            if floor is not None:
                p[2] = max(p[2], float(floor))
            return p
        head, tail = where(age), where(max(0.0, age - streak))
        level = level0 * (1.0 - age / own) ** FORM["fade"]
        if float(np.linalg.norm(head - tail)) < size:
            quads.dot(head, size, level)
        else:
            quads.strip([tail, head], [size * FORM["tail_width"], size], [level * FORM["tail_value"], level])
        drawn += 1
    return drawn
