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


class SmoothCurve:
    """点 (times[i], values[i]) をすべて通り、速さが続く曲線（区間ごとの 3 次 Hermite。値と傾きが点でつながる）。

    列ごとの接線:
    - 既定（単調な形。Fritsch–Butland）: 前後の区間で増減が変わる点では 0、それ以外は前後の傾きの重み付き調和平均。区間の中の値は
      両端の値の間に収まる（zoom がキーの値を越えない）。
    - path に挙げた 2 列（中心の x, y）は 2 次元の道として: 点の接線は前の点から次の点への平均の速さ（各区間の速さを、その区間の
      長さの時間で重み付けした平均。角を丸めて通る）。大きさは前後の区間の遅い方の速さの 2 倍まで、向きが turn 度より大きく返る点と、
      前後に動かない区間のある点では 0。角度と速さは scale（列の 1 が何画素か）を掛けて数える。
    両端の接線は隣の区間の傾き。範囲の外は端の値。camera.curve: smooth（zoom の対数と center をこの曲線でつなぐ）が使う。"""

    def __init__(self, times, values, path=None, scale=(1.0, 1.0), turn=150.0):
        self.times = np.asarray(times, np.float64)
        self.values = np.asarray(values, np.float64).reshape(len(self.times), -1)
        if len(self.times) < 1 or np.any(np.diff(self.times) <= 0):
            raise ValueError("SmoothCurve: times は 1 つ以上の昇順の値にしてください")
        steps = np.diff(self.times)[:, None]
        slopes = np.diff(self.values, axis=0) / steps if len(self.times) > 1 else np.zeros((0, self.values.shape[1]))
        tangents = np.zeros_like(self.values)
        if len(slopes):
            tangents[0], tangents[-1] = slopes[0], slopes[-1]
        cols = list(path or ())
        scale = np.asarray(scale, np.float64)
        limit = math.cos(math.radians(turn))
        for i in range(1, len(self.times) - 1):
            h0, h1 = steps[i - 1, 0], steps[i, 0]
            w1, w2 = 2.0 * h1 + h0, h1 + 2.0 * h0
            d0, d1 = slopes[i - 1], slopes[i]
            same = d0 * d1 > 0
            safe0, safe1 = np.where(same, d0, 1.0), np.where(same, d1, 1.0)
            tangents[i] = np.where(same, (w1 + w2) / (w1 / safe0 + w2 / safe1), 0.0)
            if cols:
                v0, v1 = d0[cols] * scale, d1[cols] * scale
                n0, n1 = float(np.hypot(*v0)), float(np.hypot(*v1))
                m = np.zeros(2)
                if n0 > 1e-12 and n1 > 1e-12 and float(np.dot(v0, v1)) / (n0 * n1) >= limit:
                    m = (h0 * v0 + h1 * v1) / (h0 + h1)       # 前後の点を結ぶ向きと速さ（長い区間の速さに寄る）
                    size = float(np.hypot(*m))
                    if size > 2.0 * min(n0, n1):
                        m *= 2.0 * min(n0, n1) / size
                tangents[i, cols] = m / scale
        self.tangents = tangents

    def _segment(self, t):
        t = min(max(float(t), float(self.times[0])), float(self.times[-1]))
        i = min(int(np.searchsorted(self.times, t, side="right")) - 1, len(self.times) - 2)
        return max(i, 0), t

    def __call__(self, t):
        """時刻 t の値（成分の並び。float64）。"""
        if len(self.times) == 1:
            return self.values[0].copy()
        i, t = self._segment(t)
        h = self.times[i + 1] - self.times[i]
        s = (t - self.times[i]) / h
        h00, h10, h01, h11 = 2 * s ** 3 - 3 * s ** 2 + 1, s ** 3 - 2 * s ** 2 + s, -2 * s ** 3 + 3 * s ** 2, s ** 3 - s ** 2
        return (h00 * self.values[i] + h10 * h * self.tangents[i] + h01 * self.values[i + 1] + h11 * h * self.tangents[i + 1])

    def derivative(self, t):
        """時刻 t の傾き（成分の並び。範囲の外は 0）。"""
        if len(self.times) == 1 or t < self.times[0] or t > self.times[-1]:
            return np.zeros(self.values.shape[1])
        i, t = self._segment(t)
        h = self.times[i + 1] - self.times[i]
        s = (t - self.times[i]) / h
        d00, d10, d01, d11 = 6 * s ** 2 - 6 * s, 3 * s ** 2 - 4 * s + 1, -6 * s ** 2 + 6 * s, 3 * s ** 2 - 2 * s
        return (d00 * self.values[i] + d01 * self.values[i + 1]) / h + d10 * self.tangents[i] + d11 * self.tangents[i + 1]


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
