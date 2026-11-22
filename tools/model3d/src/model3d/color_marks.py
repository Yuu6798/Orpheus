"""絵の中の色の印（口の線など）を、色の決まりと、つながった画素のかたまりで探す部品（Blender なし。numpy だけ）。
texture.warp_mouth（もとの口の線を測る）が使う。目で読まず、色で測る（4 作目の 3D のダンスで、目で読んで口の中心を 5.7 mm ずらした）。"""
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
