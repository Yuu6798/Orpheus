"""順番に点く・消える部品: 部品の並び（舞台の灯りの列など）に、出来事ごとに順番と間隔で明るさを入れる。点きはじめのちらつきと、
点いた直後の明るすぎ。numpy も Blender も使わない計算だけ（render.show のフックが Blender の中で `import fx_sequence` で読む）。
Blender の側は、物体ごとの明るさを物体の属性に書き、材質が属性を読んで光の強さに掛ける（fx_blender.drive_by_object）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、部品を順番に点ける作り方を、同じ式・同じ乱数の引き方で移し、
始まりの比べ方をコマの番号に直した。

    values = levels(table, events, count, f)    部品 count 個の、そのコマの明るさ

出来事の欄（events は 1 つの並びの出来事。表の始まりの順に後の物が上書きする）:
- `to`: 明るさ。`step`: 次の部品までの秒。`order`: 部品の番号の順（省くと 0, 1, 2, …）。
- `from`: まだどの出来事も届いていない部品の明るさ（いちばん早い出来事に書く。書かなければ 0。並びに出来事が 1 つも無ければ 1）。
- `flicker`: 点きはじめのちらつきの秒（0.25〜1 倍の乱数を掛ける。コマごとに替わる）。`seed`: ちらつきの種（flicker があるとき）。
- `over`・`surge`: 点いた直後の over 秒、明るさを 1 + surge 倍から 1 倍へ下げる。
"""
import math

try:   # パッケージとして読むとき（テスト）
    from . import fx_events
except ImportError:   # Blender が台本として動かすとき
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fx_events


def levels(table, events, count, f):
    """部品 count 個の明るさ（list）。events は table.events の中の、この並びの出来事。"""
    mine = [e for e in table.events if any(e is x for x in events)]
    level = [None] * count
    for event in mine:
        if f < table.start(event) - fx_events.EPS:
            continue
        order = event.get("order") or list(range(count))
        step = float(fx_events.need(event, "step")) * table.fps
        for rank, index in enumerate(order):
            if index >= count:
                continue
            frames = f - (table.start(event) + rank * step)                # この部品の年齢（コマ）
            if frames < -fx_events.EPS:
                continue
            age = frames / table.fps
            value = float(fx_events.need(event, "to"))
            flicker, over = float(event.get("flicker", 0.0)), float(event.get("over", 0.0))
            if flicker and age < flicker:
                tick = int(math.floor(frames + fx_events.EPS))
                value *= 0.25 + 0.75 * fx_events.rng(int(fx_events.need(event, "seed")), index, tick).random()
            elif over and age < over:
                value *= 1.0 + float(fx_events.need(event, "surge")) * (1.0 - age / over)
            level[index] = value
    rest = float(mine[0].get("from", 0.0)) if mine else 1.0
    return [rest if v is None else v for v in level]
