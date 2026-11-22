"""面を走る枝分かれ: 根もとから、決めた数の腕が、進みたい向きへ引き戻されながら折れて進み、鋭い角度で枝を出す。先端が道のりで
進み（外向き = 根もとから先へ、内向き = 先から根もとへ）、先端が明るく、通った後は薄く残る。水平の面（高さ一定）だけ。
numpy だけを使う（render.show のフックが Blender の中で `import fx_surface` で読む。CI でも試せる）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、床を走る枝分かれの作り方を、同じ式・同じ乱数の引き方で移した。
形は出来事ごとに 1 回だけ作る（始まりのコマの根もとの点と種で決まる。どのコマから描いても同じ）。

    tree = Tree(table, event)                   区間の並び（根もとからの道のりつき）
    glow = tree.draw(table, f, quads, gain=1.0) そのコマの帯を quads（fx_strips.Quads）にためる。光の候補か None

出来事の欄（既定の無い欄は書く）:
- `at`: 根もとの点（世界の座標か骨の点。カメラから見た点は使えない）。根もとの高さは使わず、`height`（面の高さ m。既定 0）に置く。
- `radius`: 腕の長さ（m。腕ごとに × 0.6〜1.0）。`arms`: 腕の数。`step`: 折れの間隔（m）。`width`: 根もとの幅（m）。
- `depth`: 枝分かれの深さ。`branch`: 1 歩ごとに枝を出す割合（枝の上では × 0.7）。`seed`: 種（整数）。
- `speed`: 先端の進む速さ（m/s）。`fade`: 届いたあと消える秒。`tail`: 先端の明るさが道のりで落ちる長さ（m）。`rest`: 通った後に残る明るさ。
- `inward`: true なら内向き（先から根もとへ集まる。外向きの鏡）。`gain`: 強さの倍率（既定 1）。
- `light`・`colour`・`light_height`・`light_decay`: 光（W・色・根もとからの高さ m・強さが e 分の 1 になる秒。light を省くと出さない）。

効く間は、始まりから radius × 1.8 ÷ speed + fade 秒。radius × 1.3 ÷ speed 秒から、fade 秒で消える。
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

FORM = {   # 形の作りの定数（台本と同じ）
    "pull": 0.55,                 # 前の向きのずれを、この割合だけ残す（進みたい向きへ引き戻す。輪を描かない）
    "turn": (0.22, 0.30),         # 1 歩ごとの向きの揺れ（ガウスの σ。腕・枝）
    "stride": (0.6, 1.4),         # 1 歩の長さの揺れ
    "narrow": 0.80,               # 先へ向かって細る割合
    "branch_turn": (0.40, 0.95),  # 枝が開く角度（ラジアン）
    "branch_rest": (0.35, 0.75),  # 枝の長さ（残りの長さに対する割合）
    "branch_extra": 0.3,          # 枝の長さに足す長さ（m）
    "branch_width": 0.55, "branch_step": 0.8, "branch_chance": 0.7,
    "arm_jitter": 0.3, "arm_reach": (0.6, 1.0),
    "flicker": 0.25,
    "spread_end": 1.8, "spread_done": 1.3,
}


class Tree:
    def __init__(self, table, event):
        spec = fx_events.need(event, "at")
        if not table.fixed(spec):
            raise fx_events.EventError(f"面を走る枝分かれの根もとに、カメラから見た点は使えません（形を 1 回だけ作るため）: {spec!r}")
        self.event = event
        for key in ("radius", "step", "speed", "tail") + (("light_decay",) if event.get("light") else ()):
            value = fx_events.need(event, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
                raise fx_events.EventError(f"面を走る枝分かれの {key} は 0 より大きい数にしてください: {value!r}")
        fade = fx_events.need(event, "fade")
        if isinstance(fade, bool) or not isinstance(fade, (int, float)) or not fade >= 0:
            raise fx_events.EventError(f"面を走る枝分かれの fade は 0 以上の数にしてください: {fade!r}")
        origin = table.point(spec, table.start(event))
        rng = fx_events.rng(fx_events.need(event, "seed"), 11)
        f = FORM
        radius, arms = float(fx_events.need(event, "radius")), int(fx_events.need(event, "arms"))
        step0, depth_max, branch = float(fx_events.need(event, "step")), int(fx_events.need(event, "depth")), float(fx_events.need(event, "branch"))
        segments = []                                                    # (p, q, 道のり p, 道のり q, 幅 p, 幅 q)

        def grow(p, heading, length, width, walked, depth):
            step = step0 * (1.0 if depth == 0 else f["branch_step"])
            count = max(2, int(length / step))
            angle = heading
            for i in range(count):
                angle = heading + (angle - heading) * f["pull"] + rng.gauss(0.0, f["turn"][1] if depth else f["turn"][0])
                q = p + np.array([math.cos(angle), math.sin(angle), 0.0]) * step * rng.uniform(*f["stride"])
                narrow = width * (1.0 - f["narrow"] * (i + 1) / count)
                segments.append((p, q, walked, walked + step, width * (1.0 - f["narrow"] * i / count), narrow))
                p, walked = q, walked + step
                if depth < depth_max and rng.random() < (branch if depth == 0 else branch * f["branch_chance"]):
                    turn = rng.choice((-1.0, 1.0)) * rng.uniform(*f["branch_turn"])
                    rest = length * (1.0 - (i + 1) / count)
                    grow(p, heading + turn, rest * rng.uniform(*f["branch_rest"]) + f["branch_extra"], narrow * f["branch_width"], walked, depth + 1)

        self.base = np.array([origin[0], origin[1], float(event.get("height", 0.0))])
        for k in range(arms):
            grow(self.base, 2.0 * math.pi * k / arms + rng.uniform(-f["arm_jitter"], f["arm_jitter"]), radius * rng.uniform(*f["arm_reach"]),
                 float(fx_events.need(event, "width")), 0.0, 0)
        self.segments = segments
        self.longest = max(s[3] for s in segments) if segments else 0.0

    def life(self):
        e = self.event
        return float(e["radius"]) * FORM["spread_end"] / float(fx_events.need(e, "speed")) + float(fx_events.need(e, "fade"))

    def draw(self, table, f, quads, gain=1.0):
        """そのコマの帯を quads にためる（床に寝た帯）。光の候補 (強さ, 位置, 色) か None。"""
        e = self.event
        if not table.within(e, f, 0.0, self.life()):
            return None
        age = table.age(e, f)
        speed, fade = float(e["speed"]), float(e["fade"])
        front = (age + 1.0 / table.fps) * speed
        done = max(0.0, age - float(e["radius"]) * FORM["spread_done"] / speed)
        gain = float(e.get("gain", 1.0)) * gain * max(0.0, 1.0 - done / max(fade, 1e-6))
        flick = 1.0 - FORM["flicker"] + FORM["flicker"] * fx_events.rng(e["seed"], 12, table.tick(f)).random()
        inward = bool(e.get("inward"))
        tail, rest = float(fx_events.need(e, "tail")), float(fx_events.need(e, "rest"))
        for p, q, d0, d1, w0, w1 in self.segments:
            if inward:                                                    # 外向きの鏡: 道のりを先から数え、区間の向きを返す
                p, q, d0, d1, w0, w1 = q, p, self.longest - d1, self.longest - d0, w1, w0
            if d0 > front:
                continue
            k = max(0.0, min(1.0, (front - d0) / max(1e-6, d1 - d0)))
            end = p + (q - p) * k
            head0, head1 = math.exp(-(front - d0) / tail), math.exp(-(front - d0 - (d1 - d0) * k) / tail)
            quads.strip([p, end], [w0, w0 + (w1 - w0) * k], [gain * flick * (rest + head0), gain * flick * (rest + head1)], flat=True)
        if e.get("light"):
            height, decay = float(fx_events.need(e, "light_height")), float(fx_events.need(e, "light_decay"))
            return float(e["light"]) * gain * math.exp(-age / decay), self.base + np.array([0.0, 0.0, height]), tuple(fx_events.need(e, "colour"))
        return None
