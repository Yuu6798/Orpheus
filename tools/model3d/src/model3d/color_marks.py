"""絵の中の色の印（口の線など）を、色の決まりと、つながった画素のかたまりで探す部品（Blender なし。numpy だけ）。
texture.warp_mouth（もとの口の線を測る）と check.drawn_mark（描いた印のずれを測る）が使う。目で読まず、色で測る（4 作目の 3D のダンスで、
目で読んで口の中心を 5.7 mm ずらした）。"""
import numpy as np

RULE_KEYS = ("red_minus_green_above", "red_above", "green_below")


def color_rule(image, rule):
    """色の決まりに入る画素 (H, W)。image は RGB (H, W, 3)。rule は {"red_minus_green_above": R − G がこれより大きい,
    "red_above": R がこれより大きい（任意）, "green_below": G がこれより小さい（任意）}（赤みが強く、肌より暗い色）。"""
    r, g = image[..., 0].astype(np.float64), image[..., 1].astype(np.float64)
    ok = (r - g) > rule["red_minus_green_above"]
    if rule.get("red_above") is not None:
        ok &= r > rule["red_above"]
    if rule.get("green_below") is not None:
        ok &= g < rule["green_below"]
    return ok


def components(mask):
    """8 近傍でつながった真の画素のかたまりの番号 (H, W)（0 は無し、1 から）と、かたまりの数。"""
    height, _ = mask.shape
    parent = [0]

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    runs, previous = [], []
    for y in range(height):
        edges = np.flatnonzero(np.diff(np.concatenate([[0], mask[y].astype(np.int8), [0]])))
        current = []
        for a, e in zip(edges[::2], edges[1::2]):
            label = len(parent)
            parent.append(label)
            for pa, pe, other in previous:
                if pa <= e and pe >= a:                                 # 上の行の区間と、斜めも含めて触れる
                    ra, rb = find(label), find(other)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
            current.append((a, e, label))
            runs.append((y, a, e, label))
        previous = current
    labels = np.zeros(mask.shape, dtype=np.int64)
    roots = {}
    for y, a, e, label in runs:
        root = find(label)
        labels[y, a:e] = roots.setdefault(root, len(roots) + 1)
    return labels, len(roots)


def largest_component(mask):
    """いちばん大きいかたまり (H, W)。かたまりが無ければ None（同じ大きさなら、上の行から先に現れるかたまり）。"""
    labels, count = components(mask)
    if not count:
        return None
    areas = np.bincount(labels.ravel(), minlength=count + 1)[1:]
    return labels == 1 + int(np.argmax(areas))


def measure_mark(image, rule, window, close_size=5):
    """探す範囲 window（[x0, y0, x1, y1]。右と下は含まない）の中で、色の決まり rule に入る画素を close_size の窓で閉じ、いちばん大きいかたまりの
    左右・上下の端（画素。端を含む）・中心の x（左右の端の真ん中）・重心の y・真ん中の 2 割の列の上の縁と下の縁・幅・面積。無ければ None。"""
    x0, y0, x1, y1 = window
    found = color_rule(image[y0:y1, x0:x1], rule)
    if close_size > 1:
        found = close(found, close_size)
    blob = largest_component(found)
    if blob is None:
        return None
    ys, xs = np.nonzero(blob)
    left, right, top, bottom = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    width = right - left + 1
    centre = left + (width - 1) / 2.0                                   # 左右の端の真ん中（centre_x と同じ所）
    middle = np.abs(xs - centre) < width * 0.1
    if not middle.any():                                                # 細い印（幅 5 以下）は、真ん中の列（偶数の幅なら 2 列）
        middle = np.abs(xs - centre) <= 0.5
    return {"left": x0 + left, "right": x0 + right, "top": y0 + top, "bottom": y0 + bottom, "centre_x": x0 + centre,
            "centroid_y": round(y0 + float(ys.mean()), 1), "mid_top": int(y0 + ys[middle].min()), "mid_bottom": int(y0 + ys[middle].max()),
            "width": int(width), "area": int(len(ys))}


def mark_shift(reference, image, settings):
    """描いた印のずれ（check.drawn_mark）。reference と image は同じ大きさの RGB。戻り: 報告。"""
    original = measure_mark(reference, settings["reference_rule"], settings["window"], settings["close"])
    if original is None:
        raise ValueError(f"もとの絵の探す範囲 {settings['window']} に、印の色（reference_rule）の画素がありません")
    left, top, right, bottom = settings["search_margin"]
    near = [max(original["left"] - left, 0), max(original["top"] - top, 0), original["right"] + right + 1, original["bottom"] + bottom + 1]
    drawn = measure_mark(image, settings["mark_rule"], near, settings["close"])
    report = {"original": original, "search": near, "drawn": drawn}
    if drawn is not None:
        report.update(centre_dx_px=drawn["centre_x"] - original["centre_x"], top_dy_px=drawn["mid_top"] - original["mid_top"],
                      width_ratio=round(drawn["width"] / original["width"], 4), height_px=drawn["mid_bottom"] - drawn["mid_top"] + 1)
        mpp = settings.get("metres_per_pixel")
        if mpp is not None:
            mm = mpp * 1000
            report.update(centre_dx_mm=round(report["centre_dx_px"] * mm, 3), top_dy_mm=round(report["top_dy_px"] * mm, 3),
                          width_mm=round(drawn["width"] * mm, 3), height_mm=round(report["height_px"] * mm, 3))
    changed = np.abs(image.astype(np.float64) - reference.astype(np.float64)).max(axis=2) > settings["change_threshold"]
    rows, cols = np.nonzero(changed)
    report.update(changed_pixels=int(len(rows)),
                  changed_box=[int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())] if len(rows) else None)
    if settings.get("allowed_margin") is not None:                     # 印のまわりの許す枠の外で変わった画素（0 のはず）
        left, top, right, bottom = settings["allowed_margin"]
        allowed = [max(original["left"] - left, 0), max(original["top"] - top, 0), original["right"] + right + 1, original["bottom"] + bottom + 1]
        far = changed.copy()
        far[allowed[1]:allowed[3], allowed[0]:allowed[2]] = False
        report.update(allowed=allowed, changed_outside=int(far.sum()))
    return report


def close(mask, size):
    """size × size の四角の窓で閉じる（ふくらませてから縮める。絵の外は、ふくらませるときは無い物、縮めるときはある物として数える）。"""
    half = size // 2
    height, width = mask.shape
    padded = np.pad(mask, half, constant_values=False)
    grown = np.zeros_like(mask)
    for dy in range(size):
        for dx in range(size):
            grown |= padded[dy:dy + height, dx:dx + width]
    padded = np.pad(grown, half, constant_values=True)
    shrunk = np.ones_like(mask)
    for dy in range(size):
        for dx in range(size):
            shrunk &= padded[dy:dy + height, dx:dx + width]
    return shrunk
