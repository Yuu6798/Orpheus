"""設定画との照合: 設定画の人物を切り抜いて主な色を取り、コマごとに人物の画素との色の重なりを測る。

- 切り抜き（`background`）: `alpha`（透明の外）/ `key`（緑抜きと同じ式）/ `border`（画像の縁の色を背景とみなす推定）/ `none`（全体）。
  `auto` はアルファに透明があれば alpha、無ければ border。border は推定なので、報告にその旨を残す。
- 主な色: 人物の内側の画素（縁を削る）を Lab で k-means（決まった種で決定論）し、近い色をまとめ、2% 未満を捨てる。
- 色の差: ΔE（Lab の距離）。陰影で明るさが変わりやすいので、明るさの差は半分に数える。
- コマごと: 人物の内側の画素が、どの設定画の色に何割入るか（coverage）、どの色からも遠い画素の割合（extra）、
  設定画の割合との重なり Σ min(coverage, 設定画の割合)（overlap。1 = 同じ配分）。
- 設定画が複数（正面・側面・背面など）なら、欠けと余りはそのコマをいちばんよく説明する 1 枚と比べる（explaining）。
  同じ衣装の向き違いは色の配分がほぼ同じで、重なりの大きさだけで選ぶとコマごとに入れ替わり、背中を向いたコマを正面と比べてしまう。

数値は測った値で、配色の違いが異常かどうか（陰影・向き・意図した衣装替え）は人が見て決める。
"""
import math
from collections import Counter

import cv2
import numpy as np

from ..dance import key as dancekey
from ..io import PipelineError
from . import detect, video

K = 6
DE_MERGE = 10.0
MIN_SHARE = 0.02
SAMPLE = 20000
MAX_SIDE = 400
BORDER_DE = 12.0


def to_lab(rgb):
    """uint8 RGB (h, w, 3) → float32 Lab（L 0..100）。"""
    return cv2.cvtColor(rgb.astype(np.float32) / 255.0, cv2.COLOR_RGB2Lab)


def lab_to_hex(color):
    pixel = cv2.cvtColor(np.asarray(color, np.float32).reshape(1, 1, 3), cv2.COLOR_Lab2RGB).reshape(3)
    r, g, b = (int(round(min(1.0, max(0.0, float(v))) * 255)) for v in pixel)
    return f"#{r:02x}{g:02x}{b:02x}"


def distance(pixels, colors):
    """(M, 3) と (K, 3) の Lab → (M, K) の色の差（明るさの差は半分に数える）。"""
    diff = pixels[:, None, :] - np.asarray(colors, np.float32)[None, :, :]
    diff[..., 0] *= 0.5
    return np.sqrt(np.sum(diff * diff, axis=2))


def kmeans(pixels, k=K, iterations=25):
    """決定論の k-means（k-means++ の種は固定）。→ (色 (n, 3), 割合 (n,))。近い色はまとめ、2% 未満は捨てる。"""
    if len(pixels) > SAMPLE:
        pixels = pixels[np.linspace(0, len(pixels) - 1, SAMPLE).astype(np.int64)]
    rng = np.random.default_rng(0)
    k = min(k, len(pixels))
    centers = [pixels[int(rng.integers(len(pixels)))]]
    for _ in range(1, k):
        d = distance(pixels, np.array(centers)).min(axis=1) ** 2
        if d.sum() <= 0:
            break
        centers.append(pixels[int(rng.choice(len(pixels), p=d / d.sum()))])
    centers = np.array(centers, np.float32)
    for _ in range(iterations):
        nearest = distance(pixels, centers).argmin(axis=1)
        moved = np.array([pixels[nearest == c].mean(axis=0) if np.any(nearest == c) else centers[c] for c in range(len(centers))], np.float32)
        if np.allclose(moved, centers, atol=1e-3):
            centers = moved
            break
        centers = moved
    nearest = distance(pixels, centers).argmin(axis=1)
    counts = np.bincount(nearest, minlength=len(centers)).astype(np.float64)
    groups = [[centers[c].astype(np.float64), counts[c]] for c in range(len(centers)) if counts[c] > 0]
    while len(groups) > 1:
        pairs = [(float(distance(groups[a][0][None].astype(np.float32), groups[b][0][None].astype(np.float32))[0, 0]), a, b)
                 for a in range(len(groups)) for b in range(a + 1, len(groups))]
        gap, a, b = min(pairs)
        if gap >= DE_MERGE:
            break
        total = groups[a][1] + groups[b][1]
        groups[a] = [(groups[a][0] * groups[a][1] + groups[b][0] * groups[b][1]) / total, total]
        del groups[b]
    total = sum(count for _, count in groups)
    groups = [(color, count / total) for color, count in groups if count / total >= MIN_SHARE]
    share = sum(weight for _, weight in groups)
    groups.sort(key=lambda item: -item[1])
    return np.array([c for c, _ in groups], np.float32), np.array([w / share for _, w in groups])


def _border_mask(rgb):
    """画像の縁 2 画素の色の中央値を背景とみなし、そこから ΔE が BORDER_DE より遠い画素を人物とする（推定）。"""
    lab = to_lab(rgb)
    ring = np.concatenate([lab[:2].reshape(-1, 3), lab[-2:].reshape(-1, 3), lab[:, :2].reshape(-1, 3), lab[:, -2:].reshape(-1, 3)])
    background = np.median(ring, axis=0)
    mask = (distance(lab.reshape(-1, 3), background[None])[:, 0] > BORDER_DE).reshape(rgb.shape[:2]).astype(np.uint8)
    return cv2.morphologyEx(mask, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))) > 0


def prepare(entry):
    """設定画 1 枚 → {"rel", "view", "method", "colors", "weights", "hex", "bands", "thumb", "foreground_share"}。"""
    rgba = video.load_rgba(entry["path"])
    if entry.get("box"):
        height, width = rgba.shape[:2]
        x0, y0, x1, y1 = entry["box"]
        rgba = rgba[int(round(y0 * height)):int(round(y1 * height)), int(round(x0 * width)):int(round(x1 * width))]
    scale = min(1.0, MAX_SIDE / max(rgba.shape[:2]))
    if scale < 1.0:
        size = (max(2, int(round(rgba.shape[1] * scale))), max(2, int(round(rgba.shape[0] * scale))))
        rgba = cv2.resize(rgba, size, interpolation=cv2.INTER_AREA)
    rgb, alpha = np.ascontiguousarray(rgba[..., :3]), rgba[..., 3]
    method = entry.get("background", "auto")
    if entry.get("key"):
        method = "key"
        mask = dancekey.alpha_of(rgb, entry["key"]) > 0.5
    elif method == "alpha" or (method == "auto" and alpha.min() < 250):
        method, mask = "alpha", alpha > 127
    elif method == "none":
        mask = np.ones(alpha.shape, bool)
    else:
        method, mask = "border", _border_mask(rgb)
    inner = cv2.erode(mask.astype(np.uint8), cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)), iterations=1) > 0
    if inner.sum() < 64:
        inner = mask
    if inner.sum() < 64:
        raise PipelineError(f"設定画から人物を切り抜けません（{entry['rel']}、方法 {method}）。background か box を指定してください")
    lab = to_lab(rgb)
    colors, weights = kmeans(lab[inner])
    rows = np.nonzero(inner)[0]
    top, bottom = rows.min(), rows.max() + 1
    nearest = distance(lab[inner], colors).argmin(axis=1)
    bands = []
    for c in range(len(colors)):
        where = rows[nearest == c]
        share = (np.median(where) - top) / max(1, bottom - top) if len(where) else 0.5
        bands.append(next(k for k, (_, upper) in enumerate(detect.BANDS) if share < upper or k == len(detect.BANDS) - 1))
    thumb_scale = min(1.0, 240 / max(rgb.shape[:2]))
    thumb_size = (max(2, int(rgb.shape[1] * thumb_scale)), max(2, int(rgb.shape[0] * thumb_scale)))
    thumb = cv2.resize(rgb, thumb_size, interpolation=cv2.INTER_AREA)
    kept = cv2.resize(mask.astype(np.uint8), thumb_size, interpolation=cv2.INTER_NEAREST) > 0
    yy, xx = np.mgrid[:thumb.shape[0], :thumb.shape[1]]
    checker = np.where(((yy // 8 + xx // 8) % 2 == 0)[..., None], 205, 235).astype(np.uint8)
    view = entry.get("view")
    return {"rel": entry["rel"], "view": view, "box": entry.get("box"), "label": entry["rel"] + (f"（{view}）" if view else ""),
            "method": method, "colors": colors, "weights": weights, "hex": [lab_to_hex(c) for c in colors], "bands": bands, "thumb": thumb,
            "cutout": np.where(kept[..., None], thumb, checker).astype(np.uint8), "foreground_share": round(float(mask.mean()), 4)}


def prepare_all(entries):
    """設定画を全部用意し、報告で見分けられる名前（label）を付ける。同じファイルを向き（view）なしで何度も使えば #番号 を足す。"""
    palettes = [prepare(entry) for entry in entries]
    counts = Counter(p["label"] for p in palettes)
    for number, palette in enumerate(palettes, 1):
        if counts[palette["label"]] > 1:
            palette["label"] += f"#{number}"
    return palettes


def describe(palette, cutout=None):
    """報告に書く設定画の要約（配列は書かない）。cutout は切り抜きの確認画像の出力先からのパス。"""
    return {"path": palette["rel"], "view": palette["view"], "label": palette["label"], "box": palette["box"], "method": palette["method"],
            "estimated": palette["method"] == "border", "foreground_share": palette["foreground_share"], "cutout": cutout,
            "colors": [{"hex": h, "share": round(float(w), 4), "band": ("head", "upper", "lower")[b]}
                       for h, w, b in zip(palette["hex"], palette["weights"], palette["bands"])]}


def frame_stats(rgb, inner, palettes, thresholds):
    """1 コマの人物の内側の画素と、設定画それぞれの色の重なり。人物がいなければ None。"""
    if inner is None or inner.sum() < 16:
        return None
    pixels = to_lab(rgb)[inner]
    rows, cols = np.nonzero(inner)
    height, width = inner.shape
    out = []
    for palette in palettes:
        d = distance(pixels, palette["colors"])
        nearest, closest = d.argmin(axis=1), d.min(axis=1)
        match = closest <= thresholds["ref_match_de"]
        coverage = np.bincount(nearest[match], minlength=len(palette["colors"])) / len(pixels)
        extra = closest > thresholds["ref_extra_de"]
        box = None
        if extra.sum() >= 8:
            box = [round(cols[extra].min() / width, 4), round(rows[extra].min() / height, 4),
                   round((cols[extra].max() + 1) / width, 4), round((rows[extra].max() + 1) / height, 4)]
        out.append({"coverage": coverage, "extra": float(extra.mean()), "extra_lab": pixels[extra].mean(axis=0) if extra.any() else None,
                    "extra_box": box, "overlap": float(np.minimum(coverage, palette["weights"]).sum())})
    return out


def best(stats):
    """そのコマでいちばん重なる設定画の番号（重なりの記録用。欠けと余りは explaining で選ぶ）。人物がいなければ None。"""
    if not stats:
        return None
    return int(np.argmax([s["overlap"] for s in stats]))


def missing_colors(stats, palette, th):
    """そのコマで、設定画の主な色（割合 ref_min_weight 以上）のうち欠けた色の番号。"""
    return [c for c, weight in enumerate(palette["weights"])
            if weight >= th["ref_min_weight"] and stats["coverage"][c] < th["ref_missing_ratio"] * weight]


def explaining(stats, palettes, th):
    """そのコマをいちばんよく説明する設定画 → (欠けの比べ先, その欠けた色の番号, 余りの比べ先)。人物がいなければ None。

    欠け: 欠けた主な色の割合の和がいちばん小さい設定画（同じなら重なりの大きい方）。どれか 1 枚で欠けなく説明できれば欠けにしない。
    余り: 設定画に無い色の割合がいちばん小さい設定画。どの設定画でも説明できない色だけを余りにする。"""
    if not stats:
        return None
    missing = [missing_colors(s, p, th) for s, p in zip(stats, palettes)]
    order = range(len(palettes))
    r_missing = min(order, key=lambda r: (sum(float(palettes[r]["weights"][c]) for c in missing[r]), -stats[r]["overlap"], r))
    r_extra = min(order, key=lambda r: (stats[r]["extra"], -stats[r]["overlap"], r))
    return r_missing, missing[r_missing], r_extra


def summary(features, palettes):
    """クリップの設定画との重なりの中央値・最小とそのコマ・いちばん重なった設定画の内訳。"""
    values = [(i, stats[best(stats)]["overlap"], best(stats)) for i, stats in enumerate(features.ref) if stats]
    if not values:
        return {"frames": 0}
    overlaps = np.array([v for _, v, _ in values])
    worst = min(values, key=lambda item: item[1])
    used = np.bincount([r for _, _, r in values], minlength=len(palettes))
    return {"frames": len(values), "overlap_median": round(float(np.median(overlaps)), 4), "overlap_min": round(float(worst[1]), 4),
            "overlap_min_frame": features.frame(worst[0]),
            "best_reference": [{"path": palettes[r]["rel"], "view": palettes[r]["view"], "label": palettes[r]["label"], "frames": int(used[r])}
                               for r in range(len(palettes))]}


def color_groups(palettes, th):
    """設定画をまたいで同じ色とみなす組 → [[(設定画の番号, 色の番号), …], …]。

    向き違いの設定画の色は k-means の中心が少しずつ違うので、別の設定画の色と ΔE ref_match_de 以内なら、近い組み合わせから順に
    つなぐ。1 つの組には 1 枚の設定画から 1 色しか入れない（同じ設定画の別の色は、間に別の設定画の色を挟んでも同じ組にしない）。
    比べる設定画がコマごとに入れ替わっても、同じ色の欠けを 1 本の区間で数え、別の色の欠けは別の区間に残すため。"""
    members = [(r, c) for r, palette in enumerate(palettes) for c in range(len(palette["weights"]))]
    links = []
    for i, (r, c) in enumerate(members):
        for q, k in members[i + 1:]:
            if q == r:
                continue
            gap = float(distance(np.asarray(palettes[r]["colors"][c], np.float32)[None], np.asarray(palettes[q]["colors"][k], np.float32)[None])[0, 0])
            if gap <= th["ref_match_de"]:
                links.append((gap, (r, c), (q, k)))
    group = {member: frozenset([member]) for member in members}
    for _, one, other in sorted(links):
        a, b = group[one], group[other]
        if a == b or {r for r, _ in a} & {r for r, _ in b}:        # 同じ設定画の色が 2 つ入るつなぎはしない
            continue
        for member in a | b:
            group[member] = a | b
    return [sorted(found) for found in dict.fromkeys(group[member] for member in members)]


def signals(features, palettes, th, fps):
    """設定画の色の欠け（reference_missing）と設定画に無い色（reference_extra）の区間の信号。

    コマごとに explaining で比べる設定画を選ぶ（区間の中で入れ替わってもよい）。欠けは色ごと（color_groups の組ごと）に区間を作り、
    値と山は、書く色が実際に欠けたコマだけで測る（別の色が続けて欠けても、区間が欠けの無いコマをはさんでつながっても、
    欠けの値を薄めない）。"""
    minimum = max(1, int(round(th["min_segment_s"] * fps)))
    picks = [explaining(stats, palettes, th) for stats in features.ref]
    several = "。ほかの設定画でも欠けなく説明できないコマ" if len(palettes) > 1 else ""
    out = []
    for members in color_groups(palettes, th):
        own = set(members)
        flagged = np.array([bool(pick) and any((pick[0], c) in own for c in pick[1]) for pick in picks])
        for a, b in detect.runs(flagged, minimum, gap=1):
            counts = Counter((picks[i][0], c) for i in range(a, b) if flagged[i] for c in picks[i][1] if (picks[i][0], c) in own)
            r, c = max(counts, key=lambda pair: (counts[pair], float(palettes[pair[0]]["weights"][pair[1]]), -pair[0], -pair[1]))
            palette, weight, hex_color, band = palettes[r], float(palettes[r]["weights"][c]), palettes[r]["hex"][c], palettes[r]["bands"][c]
            compared = [i for i in range(a, b) if flagged[i] and picks[i][0] == r and c in picks[i][1]]   # その色が欠けたコマだけで測る
            covered = [features.ref[i][r]["coverage"][c] for i in compared]
            peak = min(compared, key=lambda i: (features.ref[i][r]["coverage"][c], i))
            out.append(detect.make_signal(
                "reference_missing", "costume", features, a, b, "segment", peak, value=float(np.median(covered)),
                baseline=weight, threshold=th["ref_missing_ratio"] * weight, unit="割合", region=detect.band_box(features, peak, band),
                detail=f"設定画の色 {hex_color}（設定画の {weight:.0%}・{('頭', '上半身', '下半身')[band]}）が {np.median(covered):.1%} しかありません"
                       f"（{palette['label']} と照合{several}）",
                extra={"color": hex_color, "reference": palette["label"], "view": palette["view"]}))
    flagged = np.array([bool(pick) and features.ref[i][pick[2]]["extra"] >= th["ref_extra_min"] for i, pick in enumerate(picks)])
    for a, b in detect.runs(flagged, minimum, gap=1):
        counts = Counter(picks[i][2] for i in range(a, b) if flagged[i])
        r = max(counts, key=lambda q: (counts[q], -q))
        palette = palettes[r]
        compared = [i for i in range(a, b) if picks[i] and picks[i][2] == r]
        shares = [features.ref[i][r]["extra"] for i in compared]
        peak = max(compared, key=lambda i: (features.ref[i][r]["extra"], -i))
        colors = [features.ref[i][r]["extra_lab"] for i in compared if features.ref[i][r]["extra_lab"] is not None]
        hex_color = lab_to_hex(np.median(np.array(colors), axis=0)) if colors else None
        out.append(detect.make_signal(
            "reference_extra", "costume", features, a, b, "segment", peak, value=float(np.median(shares)), baseline=0.0,
            threshold=th["ref_extra_min"], unit="割合", region=features.ref[peak][r]["extra_box"],
            detail=f"設定画に無い色 {hex_color} が人物の {np.median(shares):.0%} あります"
                   f"（{palette['label']} と照合{'。ほかの設定画にも無い色' if len(palettes) > 1 else ''}）",
            extra={"color": hex_color, "reference": palette["label"], "view": palette["view"]}))
    return out


def overlap_series(features):
    """CSV 用: コマごとの、いちばん重なる設定画との重なり（人物がいなければ nan）。"""
    return [stats[best(stats)]["overlap"] if stats else math.nan for stats in features.ref]
