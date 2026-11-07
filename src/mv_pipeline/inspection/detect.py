"""特徴から信号を作り、同じ出来事の信号をまとめて、規則で 4 つに分ける。

信号（signal）は 1 つの測り方が見つけた所。急変は「前のコマとの差」の列で、局所の中央値から大きく外れた所（spike）を取り、
変わった絵が `pair_max_s` 以内に元へ戻れば、その間のコマ [入り, 戻り) を異常の候補にする（pair）。戻らなければ、
変わり目のコマ 1 つ（step。以降は変わったまま）。配色のずれのような区間の信号（segment）は、しきい値を超えたコマの続きを取る。

出来事（event）は、同じ分類で区間が半分以上重なる（または短い方がほぼ長い方の中にある）信号か、どちらも 3 コマ以下で
1 コマ以内に接する信号をまとめたもの。
分類は規則で決める（推定ではなく決め方の約束）: 連続性（人物の外＝背景まで変わった・明滅・止まった）→ 形状 → 衣装 → 動作 の順に、
含まれる信号の分類のうち先のものを主な分類にする。ほかの分類は categories に残す。

数値は測った値（evidence: measured）。異常かどうかは人が見て決める（status: review = 要確認）。
"""
import math

import numpy as np

from . import CATEGORIES

LABELS = {
    "glitch": "数コマだけの乱れ", "jump": "画の切り替わり", "flicker": "明るさの明滅", "freeze": "止まったコマ",
    "duplicate_cadence": "重複コマの周期", "vanish": "人物が消えたコマ",
    "motion_spike": "動きの急変", "teleport": "位置の飛び", "jitter": "小刻みな揺れ",
    "silhouette_pop": "輪郭の急変", "area_pop": "大きさの急変", "fragment": "部分の分離・増減",
    "costume_pop": "配色の急変", "costume_drift": "冒頭からの配色のずれ",
    "reference_missing": "設定画の色の欠け", "reference_extra": "設定画に無い色",
    "vlm": "VLM の指摘",
}
PRIORITY = ("continuity", "shape", "costume", "motion", "other")
BANDS = ((0.0, 0.2), (0.2, 0.55), (0.55, 1.0))     # 頭・上半身・下半身（人物の囲みの高さの割合。立った全身の目安）
FLOORS = {"jump": 0.004, "flicker": 0.004, "motion": 0.005, "teleport": 0.004, "shape": 0.01, "area": 0.02, "palette": 0.01}
SHORT = 3            # この長さ以下の信号どうしは、1 コマ以内に接していればまとめる
MOTION_PAIR_S = 0.5  # 動きの急変が逆向きに戻るのを探す長さ（秒）


def bhattacharyya(p, q):
    """合計 1 の分布どうしの距離 sqrt(1 − Σ√(pq))（0 = 同じ、1 = 重ならない）。どちらかが無ければ nan。"""
    if p is None or q is None:
        return math.nan
    return math.sqrt(max(0.0, 1.0 - float(np.sum(np.sqrt(p * q)))))


def _round(value, digits=5):
    return None if value is None or not math.isfinite(value) else round(float(value), digits)


def make_signal(check, category, features, a, b, kind, peak, value, baseline, threshold, unit, region, detail, z=None, extra=None):
    """信号 1 つ。a・b・peak は features の中の番号で、記録は元動画のコマ番号 [a, b) にする。"""
    return {"check": check, "category": category, "label": LABELS[check], "kind": kind,
            "frames": [features.frame(a), features.frame(b)], "peak": features.frame(peak),
            "value": _round(value), "baseline": _round(baseline), "threshold": _round(threshold), "unit": unit, "z": _round(z, 2),
            "region": region, "detail": detail, **(extra or {})}


def runs(flags, minimum=1, gap=0):
    """True の続き [(a, b)]。gap コマ以下のすき間はつなぎ、minimum コマより短い続きは捨てる。"""
    out = []
    start, last = None, None
    for i, flag in enumerate(flags):
        if not flag:
            continue
        if start is not None and i - last - 1 <= gap:
            last = i
            continue
        if start is not None:
            out.append((start, last + 1))
        start = last = i
    if start is not None:
        out.append((start, last + 1))
    return [(a, b) for a, b in out if b - a >= minimum]


def spikes(series, window, minimum, z_min, ratio_min, floor):
    """[(i, 値, 局所の中央値, z)]。値が minimum 以上で、前後 window の中央値から z_min（MAD×1.4826 の倍）以上、
    中央値の ratio_min 倍以上のもの。前後に 4 つ以上の値が無い所は見ない。"""
    out = []
    n = len(series)
    for i in range(n):
        value = series[i]
        if not math.isfinite(value) or value < minimum:
            continue
        others = np.array([series[j] for j in range(max(0, i - window), min(n, i + window + 1)) if j != i and math.isfinite(series[j])])
        if len(others) < 4:
            continue
        median = float(np.median(others))
        spread = max(1.4826 * float(np.median(np.abs(others - median))), floor)
        z = (value - median) / spread
        if z >= z_min and value >= ratio_min * median:
            out.append((i, float(value), median, float(z)))
    return out


def pair(found, distance, max_gap, return_ratio):
    """急変を「入り」と「戻り」に組む。→ [(a, b, kind, 主な急変)]。kind は pair（[a, b) が変わった絵）か step（a から変わったまま）。

    戻りは、入りの前のコマ a−1 と戻りのコマ j の差が、入りの変化の return_ratio 倍以下のもの。"""
    items = sorted(found)
    used, out = set(), []
    for index, item in enumerate(items):
        i = item[0]
        if i in used:
            continue
        partner = None
        for other in items[index + 1:]:
            j = other[0]
            if j - i > max_gap:
                break
            if j in used:
                continue
            gap = distance(i - 1, j)
            if math.isfinite(gap) and gap <= return_ratio * item[1]:
                partner = other
                break
        used.add(i)
        if partner:
            used.add(partner[0])
            out.append((i, partner[0], "pair", item))
        else:
            out.append((i, i + 1, "step", item))
    return out


def _window(th, fps):
    return max(4, int(round(th["window_s"] * fps)))


def _pairs_to_signals(check, category, features, found, distance, th, fps, unit, minimum, describe, region):
    out = []
    for a, b, kind, (i, value, median, z) in pair(found, distance, max(2, int(round(th["pair_max_s"] * fps))), th["return_ratio"]):
        out.append(make_signal(check, category, features, a, b, kind, i, value, median, minimum, unit, region(i), describe(a, b, kind, value), z))
    return out


def _spike_args(th, key):
    return th["spike_z"], th["spike_ratio"], FLOORS[key]


def continuity(features, th, fps):
    """画の乱れ・切り替わり（人物の外の構造の差）、明るさの明滅、止まったコマ・重複の周期、人物が消えたコマ。"""
    n, window = features.count, _window(th, fps)
    plain, structure = np.full(n, math.nan), np.full(n, math.nan)
    for i in range(1, n):
        plain[i], structure[i] = features.diffs(i - 1, i)
    luma = np.full(n, math.nan)
    luma[1:] = np.abs(np.diff(features.luma))
    out = []
    jumps = spikes(structure, window, th["jump_min"], *_spike_args(th, "jump"))
    jumped = {i for i, *_ in jumps}

    def describe_jump(a, b, kind, value):
        if kind == "pair":
            return f"{b - a} コマだけ画が変わって戻ります（構造の差 {value:.3f}）"
        return f"コマ {features.frame(a)} で画が切り替わり、そのまま戻りません（構造の差 {value:.3f}）"

    jump_signals = []
    for a, b, kind, (i, value, median, z) in pair(jumps, lambda x, y: features.diffs(x, y)[1],
                                                  max(2, int(round(th["pair_max_s"] * fps))), th["return_ratio"]):
        jump_signals.append(make_signal("glitch" if kind == "pair" else "jump", "continuity", features, a, b, kind, i, value, median,
                                        th["jump_min"], "構造の差", features.change_box(i - 1, i), describe_jump(a, b, kind, value), z))
    flickers = [s for s in spikes(luma, window, th["flicker_min"], *_spike_args(th, "flicker")) if s[0] not in jumped]
    for a, b, kind, (i, value, median, z) in pair(flickers, lambda x, y: abs(features.luma[y] - features.luma[x]),
                                                  max(2, int(round(th["pair_max_s"] * fps))), th["return_ratio"]):
        detail = (f"{b - a} コマだけ明るさが {value:+.3f} 変わって戻ります" if kind == "pair"
                  else f"コマ {features.frame(a)} で画面の明るさが {value:.3f} 変わり、そのままです")
        out.append(make_signal("flicker", "continuity", features, a, b, kind, i, value, median, th["flicker_min"], "明るさの差", [0, 0, 1, 1], detail, z))
    out += _frozen(features, th, fps, jump_signals)
    out += jump_signals
    if features.has_foreground:
        out += _vanished(features, th, fps)
    return out


def _frozen(features, th, fps, jump_signals):
    """前のコマと同じ絵（差が dup_max 以下）が、動いている所（周りの差が dup_max × dup_context 以上）に出た所。

    1 つだけの重複が一定の間隔で 3 回以上なら、水増しの周期（duplicate_cadence）として 1 つにまとめる。同じ絵が freeze_frames コマ以上
    続いた所を「止まったコマ」にする。止まった続きの直後の画の切り替わり（止まった分の追い付き）は、止まったコマの信号に含める。"""
    n, window = features.count, _window(th, fps)
    diff = features.fdiff
    duplicate = np.array([math.isfinite(d) and d <= th["dup_max"] for d in diff])
    moving = np.zeros(n, bool)
    for i in range(1, n):
        around = [diff[j] for j in range(max(1, i - window), min(n, i + window + 1)) if not duplicate[j] and math.isfinite(diff[j])]
        moving[i] = bool(around) and float(np.median(around)) >= th["dup_max"] * th["dup_context"]
    chosen = duplicate & moving
    groups = runs(chosen)
    singles = [a for a, b in groups if b - a == 1]
    cadence, out = None, []
    if len(singles) >= 3:
        gaps = np.diff(singles)
        values, counts = np.unique(gaps, return_counts=True)
        if counts.max() >= 0.7 * len(gaps) and values[np.argmax(counts)] >= 2:
            cadence = int(values[np.argmax(counts)])
    if cadence:
        a, b = singles[0] - 1, singles[-1] + 1
        out.append(make_signal("duplicate_cadence", "continuity", features, a, b, "segment", singles[0], float(len(singles)), None,
                               3, "回", None, f"{cadence} コマごとに前のコマと同じ絵があります（{len(singles)} 回。水増しの可能性）",
                               extra={"period": cadence, "duplicates": [features.frame(i) for i in singles]}))
    for a, b in groups:
        if b - a + 1 < th["freeze_frames"]:       # 短い重複は「止まった」にしない（周期の判定にだけ使った）
            continue
        start, end = a - 1, b          # 差が 0 の続き [a, b) は、コマ a−1 から b−1 までが同じ絵
        catch_up = [s for s in jump_signals if s["kind"] == "step" and s["frames"][0] in (features.frame(end), features.frame(end) + 1)]
        for signal in catch_up:
            jump_signals.remove(signal)
        detail = f"{end - start} コマ同じ絵です（{end - start - 1} コマ止まっています）"
        if catch_up:
            detail += "。直後に止まった分を追い付くように画が飛びます"
        out.append(make_signal("freeze", "continuity", features, start, end + (1 if catch_up else 0), "segment", a, float(diff[a]), None,
                               th["dup_max"], "差", None, detail, extra={"held_frames": end - start}))
    return out


def _vanished(features, th, fps):
    out = []
    longest = max(1, int(round(th["vanish_max_s"] * fps)))
    for a, b in runs(~features.present):
        if a == 0 or b == features.count or b - a > longest:
            continue          # 端の出入りと長い不在は意図の出入りとして見ない
        out.append(make_signal("vanish", "continuity", features, a, b, "segment", a, float(b - a), None, longest, "コマ",
                               features.bbox[a - 1].round(4).tolist(), f"{b - a} コマ人物が消えて、また出てきます"))
    return out


def motion(features, th, fps):
    """動きの急変（オプティカルフローの p95）、位置の飛び（重心）、小刻みな揺れ（重心の 2 階差）。"""
    n, window = features.count, _window(th, fps)
    out = []
    flow = features.flow
    found = spikes(flow, window, th["motion_min"], *_spike_args(th, "motion"))
    used, max_gap = set(), max(2, int(round(MOTION_PAIR_S * fps)))
    for index, (i, value, median, z) in enumerate(sorted(found)):
        if i in used:
            continue
        partner = None
        for j, value_j, *_ in sorted(found)[index + 1:]:
            if j - i > max_gap:
                break
            u, v = features.flow_vec[i], features.flow_vec[j]
            norms = float(np.linalg.norm(u) * np.linalg.norm(v))
            if j not in used and norms > 0 and float(np.dot(u, v)) / norms < -0.3 and 1 / 3 <= value_j / value <= 3:
                partner = j
                break
        used.add(i)
        b, kind = (partner, "pair") if partner else (i + 1, "step")
        if partner:
            used.add(partner)
        unit = "体の高さ/コマ" if features.has_foreground else "画面の高さ/コマ"
        detail = (f"大きく動いて {b - i} コマで逆向きに戻ります（動きの p95 {value:.3f} {unit}、周りの中央値 {median:.3f}）" if partner
                  else f"動きが急に大きくなります（p95 {value:.3f} {unit}、周りの中央値 {median:.3f}）")
        out.append(make_signal("motion_spike", "motion", features, i, b, kind, i, value, median, th["motion_min"], unit,
                               features.flow_box[i], detail, z))
    if not features.has_foreground:
        return out
    centroid = np.full(n, math.nan)
    for i in range(1, n):
        centroid[i] = features.centroid_change(i - 1, i)

    def box(i):
        boxes = [features.bbox[k] for k in (i - 1, i) if features.present[k]]
        if not boxes:
            return None
        stack = np.array(boxes)
        return [round(float(stack[:, 0].min()), 4), round(float(stack[:, 1].min()), 4), round(float(stack[:, 2].max()), 4), round(float(stack[:, 3].max()), 4)]

    out += _pairs_to_signals("teleport", "motion", features, spikes(centroid, window, th["teleport_min"], *_spike_args(th, "teleport")),
                             features.centroid_change, th, fps, "体の高さ/コマ", th["teleport_min"],
                             lambda a, b, kind, value: (f"人物が {b - a} コマだけ位置を飛んで戻ります（重心の移動 {value:.3f} 体の高さ）" if kind == "pair"
                                                        else f"人物の位置がコマ {features.frame(a)} で飛びます（重心の移動 {value:.3f} 体の高さ）"), box)
    out += _jitter(features, th, fps)
    return out


def _jitter(features, th, fps):
    """重心の 1 階差 v と 2 階差 a の大きさの中央値を、コマ k の前後 half コマで取る。なめらかな動きは |a| が |v| よりずっと小さく、
    毎コマ向きの入れ替わる揺れは |a| が |v| の 2 倍ほどになる。中央値なので、1 回の飛び（位置の飛び・乱れ）には引きずられない。
    さらに、前のコマと逆向きに動いたコマが窓の jitter_reversals 以上あること（跳ねる・止まるのように 1〜2 回向きが変わるだけの動きを除く）。"""
    n = features.count
    half = max(2, int(round(0.25 * fps)))
    aspect = features.size[0] / features.size[1]
    position = np.stack([features.centroid[:, 0] * aspect, features.centroid[:, 1]], axis=1)
    body = np.where(features.present, features.body, np.nan)
    velocity = np.full(n, math.nan)
    accel = np.full(n, math.nan)
    reversed_ = np.full(n, math.nan)      # 前のコマと逆向きに動いたか（1 / 0。どちらかがほとんど動いていなければ nan）
    for i in range(1, n):
        if features.present[i] and features.present[i - 1]:
            step = (position[i] - position[i - 1]) / max(1e-6, 0.5 * (body[i] + body[i - 1]))
            velocity[i] = float(np.hypot(*step))
            if i >= 2 and features.present[i - 2]:
                before = (position[i - 1] - position[i - 2]) / max(1e-6, 0.5 * (body[i - 1] + body[i - 2]))
                accel[i] = float(np.hypot(*(step - before)))
                if min(velocity[i], float(np.hypot(*before))) > 0.25 * th["jitter_min"]:
                    reversed_[i] = float(np.dot(step, before) < 0)
    flags, values = np.zeros(n, bool), np.full(n, math.nan)
    for k in range(n):
        span = [j for j in range(max(0, k - half), min(n, k + half + 1)) if math.isfinite(accel[j]) and math.isfinite(velocity[j])]
        if len(span) < 2 * half:
            continue
        med_a, med_v = float(np.median(accel[span])), float(np.median(velocity[span]))
        turns = [reversed_[j] for j in span if math.isfinite(reversed_[j])]
        values[k] = med_a
        flags[k] = (med_a >= th["jitter_min"] and med_a >= th["jitter_ratio"] * max(med_v, 1e-9)
                    and len(turns) >= half and float(np.mean(turns)) >= th["jitter_reversals"])
    out = []
    for a, b in runs(flags, minimum=max(2, half), gap=1):
        peak = a + int(np.nanargmax(values[a:b]))
        a, b = max(0, a - 1), max(1, b - 1)          # 2 階差 a[i] はコマ i−2〜i から決まるので、中央のコマ i−1 にそろえる
        out.append(make_signal("jitter", "motion", features, a, b, "segment", peak, float(values[peak]), None, th["jitter_min"],
                               "体の高さ/コマ²", features.bbox[peak].round(4).tolist() if features.present[peak] else None,
                               f"人物が小刻みに揺れています（重心の 2 階差の中央値 {values[peak]:.4f} 体の高さ。向きが毎コマのように入れ替わる）"))
    return out


def shape(features, th, fps):
    """輪郭の急変（囲みにそろえた輪郭の 1 − IoU）、大きさの急変（面積の比）、部分の分離・増減（塊の数）。"""
    n, window = features.count, _window(th, fps)
    change, area = np.full(n, math.nan), np.full(n, math.nan)
    for i in range(1, n):
        change[i], area[i] = features.shape_change(i - 1, i), features.area_change(i - 1, i)
    out = _pairs_to_signals("silhouette_pop", "shape", features, spikes(change, window, th["shape_min"], *_spike_args(th, "shape")),
                            features.shape_change, th, fps, "1−IoU", th["shape_min"],
                            lambda a, b, kind, value: (f"輪郭が {b - a} コマだけ大きく変わって戻ります（1−IoU {value:.3f}）" if kind == "pair"
                                                       else f"輪郭がコマ {features.frame(a)} で大きく変わります（1−IoU {value:.3f}）"),
                            lambda i: features.change_box(i - 1, i))
    out += _pairs_to_signals("area_pop", "shape", features, spikes(area, window, th["area_min"], *_spike_args(th, "area")),
                             features.area_change, th, fps, "|ln 面積比|", th["area_min"],
                             lambda a, b, kind, value: (f"人物の面積が {b - a} コマだけ {math.exp(value):.2f} 倍ほど変わって戻ります" if kind == "pair"
                                                        else f"人物の面積がコマ {features.frame(a)} で {math.exp(value):.2f} 倍ほど変わります"),
                             lambda i: features.change_box(i - 1, i))
    present = [c for c, p in zip(features.components, features.present) if p]
    if present:
        values, counts = np.unique(present, return_counts=True)
        usual = int(values[np.argmax(counts)])
        longest = max(1, int(round(th["fragment_max_s"] * fps)))
        odd = np.array([p and c != usual for c, p in zip(features.components, features.present)])
        for a, b in runs(odd):
            if b - a > longest or a == 0 or b == n or not (features.present[a - 1] and features.present[b]):
                continue
            counted = int(features.components[a])
            box = features.extra_box[a] or (features.bbox[a].round(4).tolist() if features.present[a] else None)
            out.append(make_signal("fragment", "shape", features, a, b, "segment", a, float(counted), float(usual), None, "塊", box,
                                   f"人物の塊が {usual} つから {counted} つに変わります（{b - a} コマ）"))
    return out


def costume(features, th, fps):
    """配色の急変（前のコマとの距離）と、冒頭の配色からのずれ（区間）。どちらも人物の内側の画素。"""
    n, window = features.count, _window(th, fps)
    change = np.full(n, math.nan)
    for i in range(1, n):
        change[i] = features.palette_change(i - 1, i)
    out = _pairs_to_signals("costume_pop", "costume", features, spikes(change, window, th["palette_min"], *_spike_args(th, "palette")),
                            features.palette_change, th, fps, "Bhattacharyya", th["palette_min"],
                            lambda a, b, kind, value: (f"人物の配色が {b - a} コマだけ変わって戻ります（距離 {value:.3f}）" if kind == "pair"
                                                       else f"人物の配色がコマ {features.frame(a)} で変わります（距離 {value:.3f}）"),
                            lambda i: _band_change_box(features, i - 1, i))
    anchor_n = max(3, int(round(th["anchor_s"] * fps)))
    opening = [i for i in range(n) if features.hist[i] is not None][:anchor_n]
    if len(opening) < 2:
        return out
    anchor = _median_hist([features.hist[i] for i in opening])
    anchor_bands = [_median_hist([features.band_hist[i][k] for i in opening if features.band_hist[i][k] is not None]) for k in range(3)]
    drift = np.array([bhattacharyya(features.hist[i], anchor) for i in range(n)])
    flagged = np.array([math.isfinite(d) and d >= th["drift_min"] for d in drift])
    for a, b in runs(flagged, max(1, int(round(th["min_segment_s"] * fps))), gap=1):
        peak = a + int(np.nanargmax(drift[a:b]))
        distances = [bhattacharyya(features.band_hist[peak][k], anchor_bands[k]) for k in range(3)]
        band = int(np.nanargmax(distances)) if any(math.isfinite(d) for d in distances) else 1
        out.append(make_signal("costume_drift", "costume", features, a, b, "segment", peak, float(drift[peak]), 0.0, th["drift_min"],
                               "Bhattacharyya", band_box(features, peak, band),
                               f"冒頭 {len(opening)} コマの配色から離れています（距離 {drift[peak]:.3f}。いちばん違うのは{('頭', '上半身', '下半身')[band]}）"))
    return out


def _median_hist(items):
    items = [h for h in items if h is not None]
    if not items:
        return None
    median = np.median(np.stack(items), axis=0)
    total = median.sum()
    return median / total if total > 0 else None


def band_box(features, i, band):
    """コマ i の人物の囲みのうち、帯 band（0 頭・1 上半身・2 下半身）の正規化の箱。人物がいなければ None。"""
    if not features.present[i]:
        return None
    x0, y0, x1, y1 = features.bbox[i]
    a, b = BANDS[band]
    return [round(float(x0), 4), round(float(y0 + a * (y1 - y0)), 4), round(float(x1), 4), round(float(y0 + b * (y1 - y0)), 4)]


def _band_change_box(features, i, j):
    distances = [bhattacharyya(features.band_hist[i][k], features.band_hist[j][k]) for k in range(3)]
    if not any(math.isfinite(d) for d in distances):
        return None
    return band_box(features, j, int(np.nanargmax(distances)))


DETECTORS = {"continuity": continuity, "motion": motion, "shape": shape, "costume": costume}
NEEDS_FOREGROUND = {"shape", "costume"}


def _strength(signal):
    value, threshold = signal.get("value"), signal.get("threshold")
    if value is None or not threshold:
        return 1.0
    return value / threshold


def _overlap(a, b):
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    union = max(a[1], b[1]) - min(a[0], b[0])
    return max(0, hi - lo) / union if union else 0.0


def _together(s, t):
    a, b = s["frames"], t["frames"]
    if "duplicate_cadence" in (s["check"], t["check"]):     # クリップ全体の周期。中の乱れや止まりを飲み込まない
        return s["check"] == t["check"]
    if s["category"] == t["category"]:
        shared = min(a[1], b[1]) - max(a[0], b[0])
        shorter = min(a[1] - a[0], b[1] - b[0])
        if _overlap(a, b) >= 0.5 or (shorter > 0 and shared >= 0.8 * shorter):   # 半分以上重なるか、短い方がほぼ長い方の中
            return True
    short = a[1] - a[0] <= SHORT and b[1] - b[0] <= SHORT
    return short and max(a[0], b[0]) - min(a[1], b[1]) <= 1


def classify(signals):
    """含まれる信号の分類のうち、PRIORITY の先のものを主な分類にする。"""
    present = {s["category"] for s in signals}
    primary = next(c for c in PRIORITY if c in present)
    return primary, [c for c in PRIORITY if c in present]


def events(signals, clip, fps):
    """信号を出来事にまとめる（Union-Find）。番号は付けない（targets が付ける）。"""
    signals = sorted(signals, key=lambda s: (s["frames"][0], s["frames"][1], s["check"]))
    parent = list(range(len(signals)))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(signals)):
        for j in range(i + 1, len(signals)):
            if _together(signals[i], signals[j]):
                parent[root(j)] = root(i)
    groups = {}
    for i, signal in enumerate(signals):
        groups.setdefault(root(i), []).append(signal)
    out = []
    for members in groups.values():
        primary, categories = classify(members)
        lead = max((s for s in members if s["category"] == primary), key=lambda s: (s["kind"] != "step", _strength(s)))
        # 区間は全部の信号から取る。ただし、組になった信号の戻りのコマにある変わり目だけの信号（戻りの動きを別に拾ったもの）は除く。
        # それ以外の変わり目（近くの別の不具合）は区間に入れる（直す場所から漏らさない）
        returns = {s["frames"][1] for s in members if s["kind"] == "pair"}
        spans = [s["frames"] for s in members if not (s["kind"] == "step" and s["frames"][0] in returns)]
        a, b = min(f[0] for f in spans), max(f[1] for f in spans)
        evidence = "estimated" if all(s["check"] == "vlm" for s in members) else "measured"
        out.append({"clip": clip, "frames": [a, b], "peak": lead["peak"],
                    "t": [round(a / fps, 4), round(b / fps, 4)] if fps else None,
                    "category": primary, "categories": categories, "label": lead["label"], "summary": lead["detail"],
                    "region": lead["region"], "evidence": evidence, "status": "review",
                    "signals": sorted(members, key=lambda s: (s["frames"][0], s["check"]))})
    return sorted(out, key=lambda e: (e["frames"][0], e["frames"][1]))


assert set(DETECTORS) == set(CATEGORIES)
