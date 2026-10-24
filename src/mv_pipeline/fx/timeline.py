"""時間まわりの純関数: ease・キーフレーム補間・フレーム数・seed 付き乱数。"""
import math
import zlib

import numpy as np

EASES = ("linear", "in", "out", "in_out")


def _smooth(u):
    return u * u * (3.0 - 2.0 * u)


def ease(name, u):
    """u (0..1、範囲外は端に丸める) を name の曲線で 0..1 に写す。端点は 0→0、1→1、単調増加。

    in_out = smoothstep、in = 加速 (smoothstep の前半を引き伸ばしたもの)、out = 減速 (in の鏡像)。
    """
    u = np.clip(u, 0.0, 1.0) if isinstance(u, np.ndarray) else min(1.0, max(0.0, float(u)))
    if name == "linear":
        return u
    if name == "in_out":
        return _smooth(u)
    if name == "in":
        return 2.0 * _smooth(0.5 * u)
    if name == "out":
        return 2.0 * _smooth(0.5 * u + 0.5) - 1.0
    raise ValueError(f"unknown ease: {name!r}")


def frame_count(duration_s, fps):
    """区間 duration_s・fps のフレーム数 = round(duration×fps)（Python の round）。最低 1。

    全モジュール（出力・検査・legacy）がこの 1 関数で数える。
    """
    return max(1, int(round(float(duration_s) * float(fps))))


def frame_time(index, fps, start=0.0):
    """index 番目のフレームのショット時刻（秒）。start は区間の開始時刻。"""
    return float(start) + index / float(fps)


def _blend(a, b, w):
    if isinstance(a, (list, tuple)):
        return [x + (y - x) * w for x, y in zip(a, b)]
    return a + (b - a) * w


def interp(keys, t, fields):
    """キーフレーム列 keys (t 昇順の dict の列) を時刻 t で補間し、{field: 値} を返す。

    - 最初のキーより前は最初の値、最後のキーより後は最後の値（外挿しない）。
    - 区間 (k_i → k_i+1) の曲線は **到達側のキー k_i+1 の "ease"**（無指定は linear）。
    - fields の値が長さ n のリストなら要素ごとに補間する（center など）。
    """
    if not keys:
        raise ValueError("keys is empty")
    if t <= keys[0]["t"] or len(keys) == 1:
        return {f: keys[0][f] for f in fields}
    if t >= keys[-1]["t"]:
        return {f: keys[-1][f] for f in fields}
    for left, right in zip(keys, keys[1:]):
        if left["t"] <= t <= right["t"]:
            span = right["t"] - left["t"]
            w = ease(right.get("ease", "linear"), (t - left["t"]) / span if span > 0 else 1.0)
            return {f: _blend(left[f], right[f], w) for f in fields}
    raise AssertionError("unreachable")  # keys が昇順なら必ずどれかの区間に入る


def interp_scalar(keys, t, field="v"):
    return float(interp(keys, t, [field])[field])


def seeded_rng(seed, label):
    """seed と文字列ラベルから決まる乱数生成器。fx の乱数はすべてここを通す（決定論の唯一の入口）。

    ラベルが違えば独立な系列になる。Python の hash() は使わない（実行ごとに変わるため）。
    """
    return np.random.default_rng(np.random.SeedSequence([int(seed), zlib.crc32(label.encode("utf-8"))]))


def flicker_phases(seed, label):
    """明滅の位相 (φ1, φ2) を seed から決める。"""
    rng = seeded_rng(seed, "flicker:" + label)
    return float(rng.random() * 2 * math.pi), float(rng.random() * 2 * math.pi)


def flicker(t, amount, hz, phases):
    """1 + amount × (0.6 sin(2π·hz·t+φ1) + 0.4 sin(2π·hz·2.37·t+φ2))。負にしない。"""
    if amount <= 0:
        return 1.0
    wave = 0.6 * math.sin(2 * math.pi * hz * t + phases[0]) + 0.4 * math.sin(2 * math.pi * hz * 2.37 * t + phases[1])
    return max(0.0, 1.0 + amount * wave)
