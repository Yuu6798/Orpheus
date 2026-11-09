"""曲の出来事の時刻（spec の cues）と、強さの曲線（envelope）。ステージのセットを hooks で描くときの手がかり。

spec の `cues` は名前 → 時刻（か時刻の並び）で、拍・小節・コマの参照で書ける。読み込み時に秒の昇順の並びになる
（`ctx.spec.data["cues"]`、`api.cues(ctx)`）。セットの絵そのものは作品ごとなので hooks に書き（`backdrop` はカメラの前）、
時刻と強さの曲線をここで共有する:

    from mv_pipeline.dance import api, cues

    def backdrop(img, t, ctx):
        when = api.cues(ctx)
        level = cues.envelope([(when["break"][0], 1.0), (when["break"][0] + 0.06, 0.25),
                               (when["pickup"][0], 0.25), (when["kick"][0], 0.6), (when["kick"][0], 1.0, "step")], t)
        ...

cv2 を使わない（plan や検品からも読めるように）。
"""
import bisect


def smoothstep(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def first(cues, name, default=None):
    """cues の name の最初の時刻。無ければ default。"""
    times = cues.get(name) or ()
    return times[0] if times else default


def envelope(keys, t):
    """キー [(時刻, 値[, つなぎ方]), …]（時刻の昇順）の t での値。

    最初のキーより前は最初の値、最後のキーから後は最後の値。キー i と i+1 の間は、キー i+1 のつなぎ方で決める:
    "linear"（既定。直線）・"smooth"（smoothstep）・"step"（i の値のまま、i+1 の時刻で跳ぶ）。同じ時刻のキーが並ぶと、
    その時刻から後の方の値に跳ぶ。直線は v_i + (v_{i+1} − v_i)·(t − t_i)/(t_{i+1} − t_i) の順で計算する。"""
    if not keys:
        raise ValueError("envelope のキーが空です")
    times = [key[0] for key in keys]
    if any(b < a for a, b in zip(times, times[1:])):
        raise ValueError(f"envelope のキーは時刻の昇順にしてください: {times}")
    index = bisect.bisect_right(times, t) - 1
    if index < 0:
        return keys[0][1]
    if index == len(keys) - 1:
        return keys[-1][1]
    (t0, v0), (t1, v1, *rest) = keys[index][:2], keys[index + 1]
    ease = rest[0] if rest else "linear"
    if ease == "step":
        return v0
    if ease == "smooth":
        return v0 + (v1 - v0) * smoothstep((t - t0) / (t1 - t0))
    if ease == "linear":
        return v0 + (v1 - v0) * (t - t0) / (t1 - t0)
    raise ValueError(f"envelope のつなぎ方は linear・smooth・step のどれかにしてください: {ease!r}")


def between(t, start, end):
    """start ≤ t < end か（区間の出来事の判定。start か end が None なら、その側は開いている）。"""
    return (start is None or start <= t) and (end is None or t < end)
