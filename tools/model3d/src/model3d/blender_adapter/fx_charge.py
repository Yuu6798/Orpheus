"""溜めて、撃って、余韻: 3 段の出来事。向きは 2 点（根もと base → 先 tip）で決める。numpy だけを使う（render.show のフックが
Blender の中で `import fx_charge` で読む。CI でも試せる）。

- 溜め（始まりの charge 秒前から）: 先の光の玉が育ち、まわりから短い線が先へ集まり、決まった向きの粒が外から内へ流れる。
- 撃つ（始まりから hold 秒）: 先の閃光の玉・十字の光条（flash 秒）・広がる輪（ring 秒）。向きへ伸びる芯（length m）に線が巻きつき
  （wraps 本。毎コマ形が変わる）、枝が跳ねる（forks 本）。
- 余韻（その後 after 秒）: 先の玉が小さくなり、点の列 along（先・手首・ひじなど）に沿って短い線が残る。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、先から撃つ一撃の作り方を、同じ式・同じ乱数の引き方で移した。
細かい部分の大きさ（玉・集まる線・粒・巻きつく線・枝・光条・輪・余韻の線の幅と距離）は、`size`（m）に掛ける割合（FORM）にした。

    glows = draw(table, event, f, camera, layers, gain=1.0, mono=0.0)

layers: {"orb": 玉, "core": 芯・枝・光条, "halo": 芯のにじみ, "fine": 集まる線・巻きつく線・輪・余韻の線, "motes": 集まる粒}
（fx_strips.Quads か None）。mono はそのコマの白黒の値（fx の白黒のコマ）: 0.5 より大きい（白い地）なら玉とにじみを作らず、
大きさが 0.5 より大きいなら光条を作らない（足し算の物は、白い地で黒い四角になる。しきいは fx_blender.mono_frame と同じ）。

出来事の欄（どれも書く）: `tip`・`base`（点）、`along`（点の並び。省くと [tip, base]）、`charge`・`hold`・`after`・`flash`・`ring`（秒）、
`length`・`width`（芯の長さと幅 m）、`size`（細かい部分の大きさ m）、`halo`（にじみの幅の倍率）、`ring_speed`（輪の広がる速さ m/s）、
`wraps`・`forks`・`motes`（本数・粒の数）、`seed`、`light`（W）、`colour`（光の色。1 つか {"charge", "release", "after"}）。`gain` は既定 1。
"""
import math

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import fx_bundle, fx_events, fx_strips
except ImportError:   # Blender が台本として動かすとき
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fx_bundle
    import fx_events
    import fx_strips

UP = np.array([0.0, 0.0, 1.0])
FORM = {   # 形の作りの定数（台本と同じ。長さは size に掛ける割合）
    "orb_charge": (0.03, 0.11), "orb_release": (0.07, 0.15), "orb_after": (0.035, 0.012),
    "gather": (0.07, 0.16, 0.0015, 0.003), "gather_jag": 0.30,
    "mote_reach": (0.45, 0.02, 0.03, 0.05), "mote_width": (0.002, 0.004), "mote_speed": 2.5,
    "beam_points": 14, "beam_curve": 1.6, "beam_pinch": 3.0,
    "wrap_points": 26, "wrap_twist": (3.0, 6.0), "wrap_reach": (0.015, 0.09, 1.0), "wrap_width": 0.009,
    "fork_root": (0.03, 0.30), "fork_ahead": (0.25, 0.7), "fork_out": (0.15, 0.45), "fork_jag": 0.25, "fork_width": 0.012,
    "rays": ((0.0, 1.0), (math.pi / 2, 0.7), (math.pi / 4, 0.4), (-math.pi / 4, 0.4)), "ray_reach": (0.25, 0.55),
    "ray_width": (0.001, 0.008, 0.018, 0.004),
    "ring_start": 0.05, "ring_ahead": 0.06, "ring_points": 29, "ring_width": (0.010, 0.002),
    "after_lines": 2, "after_jitter": (0.03, 0.05), "after_jag": 0.35, "after_width": 0.003,
    "glow_ahead": (0.05, 0.08),
}


def _colours(event):
    colour = fx_events.need(event, "colour")
    if isinstance(colour, dict):
        return tuple(colour["charge"]), tuple(colour["release"]), tuple(colour["after"])
    return (tuple(colour),) * 3


def window(event):
    """(溜めの秒, 撃って余韻が終わるまでの秒)。段の秒（charge・hold・after・flash・ring）は 0 より大きい数だけ受ける。"""
    for key in ("charge", "hold", "after", "flash", "ring"):
        value = fx_events.need(event, key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
            raise fx_events.EventError(f"溜めて・撃って・余韻の {key} は 0 より大きい秒にしてください: {value!r}")
    return float(event["charge"]), float(event["hold"]) + float(event["after"])


def draw(table, event, f, camera, layers, gain=1.0, mono=0.0):
    """3 段のうちのそのコマの形を layers にためる。光の候補 [(強さ, 位置, 色)] を返す（効いていなければ []）。"""
    charge, end = window(event)
    if not table.within(event, f, charge, end):
        return []
    F = FORM
    s = float(fx_events.need(event, "size"))
    hold, after = float(event["hold"]), float(event["after"])
    age = table.age(event, f)
    tip, base = table.point(fx_events.need(event, "tip"), f, camera), table.point(fx_events.need(event, "base"), f, camera)
    aim = fx_strips.unit(tip - base)
    if float(np.linalg.norm(aim)) == 0.0:                                 # 根もとと先が同じ所なら、上へ撃つ
        aim = UP.copy()
    side = fx_strips.perpendicular(aim)                                   # 真上・真下へ撃つときも、輪と巻きつく線の向きを持つ
    lift = np.cross(side, aim)
    seed = fx_events.need(event, "seed")
    rng = fx_events.rng(seed, 21, table.tick(f))
    gain = float(event.get("gain", 1.0)) * gain
    view = fx_strips.unit(np.asarray(camera["position"], float) - tip)
    right = fx_strips.perpendicular(view)                                 # カメラが先の真上・真下でも、玉と光条の向きを持つ
    up = np.cross(right, view)
    power = float(fx_events.need(event, "light"))
    c_charge, c_release, c_after = _colours(event)
    orb, core, halo, fine, motes = (layers.get(k) for k in ("orb", "core", "halo", "fine", "motes"))
    soft = mono <= 0.5                                                    # 白い地（mono_frame と同じく 0.5 より大きい）では、玉とにじみを作らない

    def add(layer, method, *args, **kwargs):
        if layer is not None:
            getattr(layer, method)(*args, **kwargs)

    if age * table.fps < -fx_events.EPS:                                  # 溜め（段の境はコマの番号で比べる。秒の小数の丸めで 1 コマずれない）
        k = 1.0 + age / charge
        if soft:
            size = (F["orb_charge"][0] + F["orb_charge"][1] * k * k) * s * (0.85 + 0.3 * rng.random())
            add(orb, "dot", tip, size, gain * (0.5 + 1.3 * k), right, up)
        near, far, w0, w1 = F["gather"]
        for _ in range(2 + int(4 * k)):
            d = fx_strips.unit(np.array([rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)]))
            points = fx_bundle.displace([tip + d * ((near + far * rng.random()) * s), tip], rng, F["gather_jag"], 2)
            m = len(points)
            add(fine, "strip", points, [(w0 + w1 * i / (m - 1)) * s for i in range(m)], [gain * (0.5 + 0.6 * k)] * m)
        flow = fx_events.rng(seed, 22)                                    # 向きは決まっていて、外から内へ流れる
        reach, floor_, lead, back = F["mote_reach"]
        for _ in range(int(fx_events.need(event, "motes"))):
            d = fx_strips.unit(np.array([flow.gauss(0, 1), flow.gauss(0, 1), flow.gauss(0, 1)]))
            frac = (k * F["mote_speed"] + flow.random()) % 1.0
            dist = (reach * (1.0 - frac) ** 1.5 + floor_) * s
            add(motes, "strip", [tip + d * (dist + (lead + back * (1.0 - frac)) * s), tip + d * dist],
                [F["mote_width"][0] * s, F["mote_width"][1] * s], [gain * 0.3 * k, gain * 1.2 * k * frac])
        return [(gain * power * 0.25 * k * (0.7 + 0.3 * rng.random()), tip + view * (F["glow_ahead"][0] * s), c_charge)]
    if age * table.fps <= hold * table.fps + fx_events.EPS:               # 撃つ
        k = age / hold
        length = float(fx_events.need(event, "length"))
        width = float(fx_events.need(event, "width")) * (1.0 - 0.45 * k) * (0.75 + 0.5 * rng.random())
        level = gain * (1.7 - 0.6 * k) * (0.85 + 0.15 * rng.random())
        count = F["beam_points"]
        line = [tip + aim * (length * (i / (count - 1)) ** F["beam_curve"]) for i in range(count)]
        widths = [width * (0.5 + 0.5 * min(1.0, i / F["beam_pinch"])) for i in range(count)]
        add(core, "strip", line, widths, [level] * count)
        if soft:
            add(halo, "strip", line, [w * float(fx_events.need(event, "halo")) for w in widths], [level] * count)
        m = F["wrap_points"]
        r0, r1, ramp = F["wrap_reach"]
        for _ in range(int(fx_events.need(event, "wraps"))):             # 芯に巻きつく線（毎コマ形が変わる）
            phase, twist = rng.uniform(0.0, 6.283), rng.uniform(*F["wrap_twist"]) * rng.choice((-1.0, 1.0))
            points = []
            for i in range(m):
                along = length * (i / (m - 1)) ** F["beam_curve"]
                reach = (r0 + r1 * min(1.0, along / (ramp * s))) * s * (0.3 + 1.2 * rng.random())
                a = phase + twist * along
                points.append(tip + aim * along + (side * math.cos(a) + lift * math.sin(a)) * reach)
            points[0] = tip
            add(fine, "strip", points, [F["wrap_width"] * s] * m, [level * 1.1] * m)
        for _ in range(int(fx_events.need(event, "forks"))):             # 外へ跳ねる枝
            root = tip + aim * (length * rng.uniform(*F["fork_root"]))
            a = rng.uniform(0.0, 6.283)
            end_ = root + aim * (rng.uniform(*F["fork_ahead"]) * s) + (side * math.cos(a) + lift * math.sin(a)) * (rng.uniform(*F["fork_out"]) * s)
            points = fx_bundle.displace([root, end_], rng, F["fork_jag"], 3)
            m2 = len(points)
            add(core, "strip", points, [F["fork_width"] * s * (1.0 - 0.8 * i / (m2 - 1)) for i in range(m2)], [level * 0.8] * m2)
        flash = max(0.0, 1.0 - age / float(fx_events.need(event, "flash")))
        if soft:
            add(orb, "dot", tip, (F["orb_release"][0] + F["orb_release"][1] * flash) * s, gain * (1.0 + 1.4 * flash), right, up)
        if flash > 0.0 and abs(mono) <= 0.5:
            thin, mid, peak, base_w = F["ray_width"]
            for a, reach in F["rays"]:
                d = right * math.cos(a) + up * math.sin(a)
                far = (F["ray_reach"][0] + F["ray_reach"][1] * flash) * reach * s
                points = [tip - d * far, tip - d * far * 0.3, tip, tip + d * far * 0.3, tip + d * far]
                add(core, "strip", points, [thin * s, mid * s, (peak * flash + base_w) * s, mid * s, thin * s],
                    [0.0, level * 0.7, level * 1.2, level * 0.7, 0.0])
        ring = gain * max(0.0, 1.0 - age / float(fx_events.need(event, "ring")))
        if ring > 0.0:
            radius = F["ring_start"] * s + float(fx_events.need(event, "ring_speed")) * age
            centre = tip + aim * (F["ring_ahead"] * s)
            n = F["ring_points"]
            points = [centre + (side * math.cos(2 * math.pi * i / (n - 1)) + lift * math.sin(2 * math.pi * i / (n - 1))) * radius for i in range(n)]
            add(fine, "strip", points, [(F["ring_width"][0] * ring + F["ring_width"][1]) * s] * n, [ring * 1.3] * n)
        return [(gain * power * (0.6 + 1.2 * flash), tip + view * (F["glow_ahead"][1] * s), c_release)]
    k = (age - hold) / after                                              # 余韻
    level = gain * (1.0 - k) * (0.6 + 0.4 * rng.random())
    if soft:
        add(orb, "dot", tip, (F["orb_after"][0] * (1.0 - k) + F["orb_after"][1]) * s, level * 0.8, right, up)
    path = [table.point(spec, f, camera) for spec in event.get("along", [event["tip"], event["base"]])]
    jx, jz = F["after_jitter"]
    for _ in range(F["after_lines"]):
        i = rng.randrange(len(path) - 1)
        a = path[i] + (path[i + 1] - path[i]) * rng.uniform(0.0, 0.5)
        b = path[i] + (path[i + 1] - path[i]) * rng.uniform(0.5, 1.0) + np.array([rng.uniform(-jx, jx), rng.uniform(-jx, jx), rng.uniform(0.0, jz)]) * s
        points = fx_bundle.displace([a, b], rng, F["after_jag"], 2)
        add(fine, "strip", points, [F["after_width"] * s] * len(points), [level] * len(points))
    return [(gain * power * 0.12 * (1.0 - k), tip, c_after)]
