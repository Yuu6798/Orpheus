"""折れ線の束: 2 点のあいだを、中点をずらして折った線（幹）と、途中から出る枝・孫の枝。時刻で、伸びる → 光る → 消える。
numpy だけを使う（render.show のフックが Blender の中で `import fx_bundle` で読む。CI でも試せる）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本の、束の作り方を、同じ式・同じ乱数の引き方で移した（形の照合は docs/model3d.md）。
大きな形（幹の折れと枝の出どころ）は種だけで決まり、細かい折れは every コマごとに替わる。

    glow = draw(table, event, f, camera, core, halo=None, gain=1.0)

出来事の欄（既定の無い欄は書く。値は作品ごとに決める）:
- `from`・`to`: 根もとと先の点（fx_events の 3 通りの書き方）。
- `width`: 幹の幅（m）。枝は × 0.42、孫の枝は × 0.22（FORM）。
- `jag`: 折れの大きさ（区間の長さに対する割合）。`levels`: [幹の折る回数, 細かい折れの回数]。
- `forks`: 枝の数。`fork_length`: 枝の長さの範囲（根もとと先の距離に対する割合 [下, 上]。forks が 0 なら要らない）。
- `seed`: 種（整数）。`grow`・`hold`・`fade`: 伸びる・光る・消える秒。`every`: 細かい折れを替えるコマの数（既定 1）。
- `halo`: にじみの帯の幅の倍率（省くと、にじみを作らない）。`gain`: 強さの倍率（既定 1）。
- `light`: 光の強さ（W。省くと光を出さない）。`light_at`: 光を置く所（根もとからの割合）。`colour`: 光の色。
- `when`: そのコマだけに出す値の名前（fx_events.Table.gated）。

伸びる間は、先が根もとから道のりで進む（届く割合 = (年齢 + 1 コマ) ÷ grow）。枝は、幹の出どころまで届いてから伸びる。
消える間は、太さが減り（1 − 0.55 k）、明るさが (1 − k)^1.5 で落ちながら、コマごとにちらつく。
"""
import math

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import fx_events, fx_strips
except ImportError:   # Blender が台本として動かすとき
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fx_events
    import fx_strips

UP = np.array([0.0, 0.0, 1.0])
X = np.array([1.0, 0.0, 0.0])
FORM = {   # 形の作りの定数（台本と同じ）
    "push": (0.35, 1.0),          # 中点をずらす量（区間の長さ × jag × この範囲）
    "min_segment": 0.025,         # これより短い区間は折らない（m）
    "detail": 0.55,               # 細かい折れの jag の倍率
    "fork_angle": (0.35, 0.95),   # 枝が幹から開く角度（ラジアン）
    "fork_min": 0.12,             # 枝の長さの下限（m）
    "fork_shorten": 0.6,          # 幹の先の方の枝ほど短く
    "fork_jag": 1.15,             # 枝の jag の倍率
    "fork_width": 0.42, "twig_width": 0.22,
    "taper": (0.30, 0.92, 0.95),  # 幹・枝・孫の枝の、先の細り
    "twig_chance": 0.5,
    "grow_level": 0.45, "grow_width": 0.6, "fade_thin": 0.55, "flicker": 0.45,
    "value_taper": 0.5, "halo_taper": 0.7,
}


def displace(points, rng, jag, levels, min_segment=FORM["min_segment"]):
    """折れ線の区間の真ん中を、区間に垂直な向きへ、長さ × jag × push までずらす（levels 回くり返す）。"""
    points = [np.asarray(p, float) for p in points]
    low, high = FORM["push"]
    for _ in range(levels):
        new = [points[0]]
        for p, q in zip(points[:-1], points[1:]):
            d = q - p
            length = float(np.linalg.norm(d))
            mid = (p + q) * 0.5
            if length > min_segment:
                axis = d / length
                n1 = fx_strips.unit(np.cross(axis, UP if abs(axis[2]) < 0.9 else X))
                n2 = np.cross(axis, n1)
                a = rng.uniform(0.0, 2.0 * math.pi)
                mid = mid + (n1 * math.cos(a) + n2 * math.sin(a)) * (length * jag * rng.uniform(low, high))
            new += [mid, q]
        points = new
    return points


def strands(a, b, seed, tick, jag, levels, forks, width, fork_length=None):
    """束の線の並び [{"points", "width", "taper", "start"}, …]。start は、幹の道のりのどこで枝が出るか（0〜1）。"""
    f = FORM
    coarse, fine = levels
    shape, detail = fx_events.rng(seed, 1), fx_events.rng(seed, 2, tick)
    a, b = np.asarray(a, float), np.asarray(b, float)
    main = displace([a, b], shape, jag, coarse)
    span = float(np.linalg.norm(b - a))
    roots = []
    for k in range(forks):
        i = shape.randrange(1, max(2, len(main) - 1))
        along = fx_strips.unit(main[min(i + 1, len(main) - 1)] - main[i - 1])
        side = fx_strips.unit(np.cross(along, UP if abs(along[2]) < 0.9 else X))
        side = fx_strips.unit(side * math.cos(shape.uniform(0, 6.283)) + np.cross(along, side) * math.sin(shape.uniform(0, 6.283)))
        angle = shape.uniform(*f["fork_angle"])
        length = max(f["fork_min"], span * shape.uniform(*fork_length) * (1.0 - f["fork_shorten"] * i / len(main)))
        roots.append((i / (len(main) - 1), main[i], main[i] + (along * math.cos(angle) + side * math.sin(angle)) * length, k))
    out = [{"points": displace(main, detail, jag * f["detail"], fine), "width": width, "taper": f["taper"][0], "start": 0.0}]
    for start, base, end, k in roots:
        points = displace(displace([base, end], fx_events.rng(seed, 3, k), jag * f["fork_jag"], 2), detail, jag * f["detail"], 1)
        out.append({"points": points, "width": width * f["fork_width"], "taper": f["taper"][1], "start": start})
        if shape.random() < f["twig_chance"] and len(points) > 3:            # 孫の枝
            j = len(points) // 2
            reach = points[-1] - points[j]
            tip = points[j] + fx_strips.unit(np.cross(reach, UP)) * float(np.linalg.norm(reach)) * 0.5 + reach * 0.5
            out.append({"points": displace([points[j], tip], fx_events.rng(seed, 4, k), jag, 2), "width": width * f["twig_width"],
                        "taper": f["taper"][2], "start": start + (1 - start) * 0.5})
    return out


def _whole(value, where):
    """0 以上の整数（整数に等しい数も受ける）。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != int(value) or value < 0:
        raise fx_events.EventError(f"{where} は 0 以上の整数にしてください: {value!r}")
    return int(value)


def _levels(event, forks):
    """[幹を折る回数, 細かく折る回数]。枝を出すときは、幹を 1 回以上折る（折らないと、枝の出どころが先になって伸びない）。"""
    levels = fx_events.need(event, "levels")
    if not isinstance(levels, (list, tuple)) or len(levels) != 2:
        raise fx_events.EventError(f"levels は [幹を折る回数, 細かく折る回数] にしてください: {levels!r}")
    coarse, fine = (_whole(v, "levels") for v in levels)
    if forks and coarse < 1:
        raise fx_events.EventError(f"枝を出す（forks {forks}）ときは、levels の 1 つ目（幹を折る回数）を 1 以上にしてください: {levels!r}")
    return coarse, fine


def life(event):
    return float(fx_events.need(event, "grow")) + float(fx_events.need(event, "hold")) + float(fx_events.need(event, "fade"))


def phase(table, event, f):
    """(届く割合, 明るさ, 太さ) か None（効いていない）。"""
    grow, hold, fade = (float(fx_events.need(event, k)) for k in ("grow", "hold", "fade"))
    if not table.within(event, f, 0.0, grow + hold + fade):
        return None
    age = table.age(event, f)
    reach = min(1.0, (age + 1.0 / table.fps) / max(grow, 1e-6))
    if age < grow:
        level, thick = FORM["grow_level"], FORM["grow_width"]
    elif age < grow + hold:
        level, thick = 1.0, 1.0
    else:
        k = min(1.0, (age - grow - hold) / max(fade, 1e-6))
        level = (1.0 - k) ** 1.5 * (1.0 - FORM["flicker"] + FORM["flicker"] * fx_events.rng(event["seed"], 5, table.tick(f)).random())
        thick = 1.0 - FORM["fade_thin"] * k
    return reach, level, thick


def draw(table, event, f, camera, core, halo=None, gain=1.0, values=None):
    """束を core（と halo）の fx_strips.Quads にためる。光の候補 (強さ, 位置, 色) か None を返す。
    halo は、にじみの帯をためる先（None なら作らない。白い地のコマでは呼ぶ側が None にする）。"""
    if not table.gated(event, values):
        return None
    state = phase(table, event, f)
    if state is None:
        return None
    reach, level, thick = state
    level *= float(event.get("gain", 1.0)) * gain
    a, b = table.point(fx_events.need(event, "from"), f, camera), table.point(fx_events.need(event, "to"), f, camera)
    forks = _whole(fx_events.need(event, "forks"), "forks")
    lines = strands(a, b, fx_events.need(event, "seed"), table.tick(f, event.get("every", 1)), float(fx_events.need(event, "jag")),
                    _levels(event, forks), forks, float(fx_events.need(event, "width")),
                    tuple(fx_events.need(event, "fork_length")) if forks else None)
    wide = event.get("halo")
    for line in lines:
        if reach <= line["start"]:
            continue
        part = 1.0 if reach >= 1.0 else (reach - line["start"]) / max(1e-6, 1.0 - line["start"])
        points = fx_strips.cut(line["points"], part)
        n = len(points)
        widths = [line["width"] * thick * (1.0 - line["taper"] * i / max(1, n - 1)) for i in range(n)]
        core.strip(points, widths, [level * (1.0 - FORM["value_taper"] * line["taper"] * i / max(1, n - 1)) for i in range(n)])
        if wide and halo is not None:
            halo.strip(points, [w * wide for w in widths], [level * (1.0 - FORM["halo_taper"] * line["taper"] * i / max(1, n - 1)) for i in range(n)])
    if event.get("light"):
        at = min(reach, float(fx_events.need(event, "light_at")))
        return float(event["light"]) * level, a + (b - a) * at, tuple(fx_events.need(event, "colour"))
    return None
