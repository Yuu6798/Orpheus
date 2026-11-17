"""キャラを作る段の確認地点で、絵を測る（numpy と Pillow だけ。docs/model3d.md の「キャラを作る段の確認地点」）。

人物の画素（背景の色でない画素か、透けていない画素）を行ごとに区間（横につながった画素）に分け、高さの割合の行で幅を測る。
数を出すだけで、良し悪しは決めない。3 作目の VRM のダンスで、リポジトリの外の台本（OpenCV）でしていた測り方を移した。
数は台本と同じになる。確認用の絵は、線と字の描き方が台本と違う。
"""
from pathlib import Path

import numpy as np

from .io import ToolError

# 色の決まり: 画素の R・G・B と差（g-r など）の範囲 [下, 上]（両端を含む整数）。全部に入る画素が真
CHANNELS = ("r", "g", "b", "r-g", "r-b", "g-r", "g-b", "b-r", "b-g")
GREEN_BACKGROUND = {"g": [151, 255], "g-r": [61, 255], "g-b": [61, 255]}   # 緑の背景（緑 > 150、緑 − 赤 > 60、緑 − 青 > 60）
ALPHA_MIN = 128                                                              # 透けの値がこれを超える画素を人物にする
KINDS = ("torso", "arms", "legs")
RED, CYAN, MAGENTA, BLUE = (255, 0, 0), (0, 255, 255), (255, 0, 255), (0, 0, 255)


def check_rule(rule, where):
    """色の決まりを確かめて返す（ToolError で止める）。"""
    if not isinstance(rule, dict) or not rule or set(rule) - set(CHANNELS):
        raise ToolError(f"{where} は {{{'・'.join(CHANNELS)} のどれか: [下, 上]}}（1 つ以上）にしてください: {rule!r}")
    for key, span in rule.items():
        if (not isinstance(span, list) or len(span) != 2 or any(isinstance(v, bool) or not isinstance(v, int) for v in span)
                or not -255 <= span[0] <= span[1] <= 255):
            raise ToolError(f"{where}.{key} は [下, 上]（-255〜255 の整数、下 ≤ 上。両端を含む）にしてください: {span!r}")
    return rule


def color_rule(rgb, rule):
    """色の決まりに入る画素の (H, W) の真偽。"""
    value = {name: rgb[:, :, i].astype(int) for i, name in enumerate("rgb")}
    out = np.ones(rgb.shape[:2], dtype=bool)
    for key, (low, high) in rule.items():
        channel = value[key] if len(key) == 1 else value[key[0]] - value[key[2]]
        out &= (channel >= low) & (channel <= high)
    return out


def _deep(im):
    """開いたばかりの絵の読み方（tile の raw mode）が、1 色 16 ビットか（Pillow は 16 ビットの色の PNG を RGB・RGBA として開き、
    8 ビットに直して読む。灰色の 16 ビットは I;16 の mode になる）。"""
    for tile in im.tile or []:
        args = tile[3] if len(tile) > 3 else None
        raw = args[0] if isinstance(args, tuple) and args else args
        if isinstance(raw, str) and ";16" in raw:
            return True
    return False


def read_image(path):
    """絵を 8 ビットの (H, W, 3) の RGB と、透けの (H, W)（透けの無い絵は None）で読む。16 ビット（色の絵も）・浮動小数の絵は止める
    （8 ビットに直すと値が丸められ、色の決まりの範囲が元の絵と合わなくなる）。透明色キー（tRNS）の絵も透けのある絵として読む。"""
    from PIL import Image
    try:
        with Image.open(path) as im:
            if im.mode.startswith("I") or im.mode == "F" or _deep(im):
                raise ToolError(f"絵は 8 ビットの絵にしてください（{im.mode}。16 ビットや浮動小数の絵は読みません）: {path}")
            keep = im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info
            data = np.asarray(im.convert("RGBA" if keep else "RGB")).copy()
    except OSError as error:
        raise ToolError(f"絵を読めません: {path}（{error}）") from error
    return (data[:, :, :3], data[:, :, 3]) if keep else (data, None)


def person_mask(rgb, alpha, mask="auto", background=None, alpha_min=ALPHA_MIN):
    """人物の画素の (H, W) の真偽と、使った決め方（alpha か color）。mask: alpha（透けが alpha_min を超える画素）・color（背景の色の
    決まり background に入らない画素。既定は緑の背景）・auto（透けの値に alpha_min より小さい画素があれば alpha、無ければ color）。"""
    if mask == "auto":
        mask = "alpha" if alpha is not None and alpha.min() < alpha_min else "color"
    if mask == "alpha":
        if alpha is None:
            raise ToolError("mask: alpha には透けのある絵が要ります")
        return alpha > alpha_min, "alpha"
    if mask != "color":
        raise ToolError(f"mask は auto・alpha・color のどれかにしてください: {mask!r}")
    return ~color_rule(rgb, background or GREEN_BACKGROUND), "color"


def runs(row, min_run=1):
    """真偽の 1 行の、真の区間 [(x0, x1), …]（x1 は含まない）。min_run より狭い区間は除く。"""
    edges = np.diff(np.concatenate([[0], row.astype(np.int8), [0]]))
    return [(int(a), int(e)) for a, e in zip(np.where(edges == 1)[0], np.where(edges == -1)[0]) if e - a >= min_run]


def _present(mask):
    present = np.where(mask.any(axis=1))[0]
    if not len(present):
        raise ToolError("絵に人物の画素がありません（mask と背景の色の決まりを確かめてください）")
    return present


def measure_standing(mask, rows, top_y=None, chin_y=None, min_run=6):
    """立ち絵（正面）の比率。身長は top_y（省くと人物のいちばん上の行）から人物のいちばん下の行まで。rows の行ごと（at は
    身長に対する割合、0 = 頭のてっぺん、1 = 足の裏）に、区間の幅を kind で読む: torso は絵の横の真ん中をまたぐ区間、arms は真ん中を
    またがない区間の平均、legs は広い 2 つの区間の平均（1 つなら半分）。chin_y（あごの行）があれば頭身。"""
    present = _present(mask)
    top = int(present[0]) if top_y is None else int(top_y)
    bottom = int(present[-1])
    height = bottom - top
    if height <= 0:
        raise ToolError(f"top_y（{top}）が人物のいちばん下の行（{bottom}）より上にありません")
    centre = mask.shape[1] / 2
    result = {"size": [int(mask.shape[1]), int(mask.shape[0])], "top_y": top, "bottom_y": bottom, "height_px": height,
              "min_run": min_run, "rows": {}}
    if chin_y is not None:
        if not top < chin_y <= bottom:
            raise ToolError(f"chin_y（{chin_y}）は top_y（{top}）より下・足の裏（{bottom}）より上にしてください")
        result["head_px"] = int(chin_y) - top
        result["heads_tall"] = height / (int(chin_y) - top)
    for row in rows:
        y = int(round(top + row["at"] * height))
        rr = runs(mask[y], min_run)
        widths = [e - a for a, e in rr]
        entry = {"y": y, "at": row["at"], "kind": row["kind"], "runs_px": widths}
        if row["kind"] == "torso":
            body = [(a, e) for a, e in rr if a <= centre <= e]
            if body:
                entry["width_px"] = body[0][1] - body[0][0]
                entry["arms_separate"] = len(rr) >= 3
        elif row["kind"] == "arms":
            side = [(a, e) for a, e in rr if not (a <= centre <= e)]
            if side:
                entry["width_px"] = float(np.mean([e - a for a, e in side]))
        elif len(rr) >= 2:
            entry["width_px"] = float(np.mean(sorted(widths, reverse=True)[:2]))
        elif rr:
            entry["width_px"] = widths[0] / 2.0
            entry["note"] = "脚が 1 つの区間につながっている（半分にした）"
        if "width_px" in entry:
            entry["width_per_height"] = entry["width_px"] / height
        result["rows"][row["name"]] = entry
    return result


def measure_tpose(mask, waist_range=(0.80, 0.92), step=0.005):
    """T ポーズの体の絵（正面）を、骨の無い 3D の候補と同じ物差しで測る: S = 床（人物のいちばん下の行）から腕（横にいちばん広い行）
    までの高さ。くびれは、床から waist_range の高さ（S に対する割合、step おき）で、体の中心（床から 0.85 S の行の人物の画素の横の
    中央値）をまたぐ区間がいちばん狭い行。"""
    present = _present(mask)
    extent = np.array([np.ptp(np.where(mask[y])[0]) if mask[y].any() else 0 for y in range(mask.shape[0])])
    arm_y = int(np.argmax(extent))
    floor_y = int(present[-1])
    S = floor_y - arm_y
    if S <= 0:
        raise ToolError("腕（横にいちばん広い行）が床より上にありません（T ポーズの正面の絵か確かめてください）")
    middle = mask[int(floor_y - 0.85 * S)]
    if not middle.any():
        raise ToolError("体の中心を決める行（床から 0.85 S）に人物の画素がありません")
    centre = float(np.median(np.where(middle)[0]))
    best = None
    for frac in np.arange(waist_range[0], waist_range[1] + 0.2 * step, step):
        y = int(round(floor_y - frac * S))
        if not 0 <= y < mask.shape[0]:
            continue
        for a, e in runs(mask[y]):
            if a <= centre <= e and (best is None or e - a < best[1]):
                best = (float(frac), int(e - a), y, int(a), int(e))
    out = {"arm_y": arm_y, "floor_y": floor_y, "S_px": S, "centre_x": centre, "waist": None}
    if best:
        out["waist"] = {"at": best[0], "width": best[1] / S, "width_px": best[1], "y": best[2], "x": [best[3], best[4]]}
    return out


def save_picture(image, path):
    """確認用の絵を書く（拡張子で .png か .jpg）。"""
    suffix = Path(path).suffix.lower()
    if suffix == ".png":
        image.save(path, format="PNG")
    elif suffix in (".jpg", ".jpeg"):
        image.save(path, format="JPEG", quality=95)
    else:
        raise ToolError(f"確認用の絵は .png か .jpg にしてください: {path}")


def draw_view(rgb, mask, report, path, green_background=False):
    """測った行に線を引いた確認用の絵。立ち絵は、行ごとに赤い線・区間に水色の太い線・行の名前、いちばん上と下（とあご）に紫の線。
    T ポーズは、腕の高さに青、床に紫、くびれの区間に赤。透けで人物を決めた絵は、人物の外を緑にする。"""
    from PIL import Image, ImageDraw, ImageFont
    base = rgb.copy()
    if green_background:
        base[~mask] = (0, 255, 0)
    image = Image.fromarray(base)
    draw = ImageDraw.Draw(image)
    width = image.width
    size = max(12, image.height // 80)                     # 字の大きさ（画素）
    font = ImageFont.load_default(size=size)
    if "S_px" in report:
        draw.line([(0, report["arm_y"]), (width, report["arm_y"])], fill=BLUE, width=2)
        draw.line([(0, report["floor_y"]), (width, report["floor_y"])], fill=MAGENTA, width=1)
        if report["waist"]:
            y, (x0, x1) = report["waist"]["y"], report["waist"]["x"]
            draw.line([(x0, y), (x1, y)], fill=RED, width=3)
    else:
        if "head_px" in report:
            chin = report["top_y"] + report["head_px"]
            draw.line([(0, chin), (width, chin)], fill=MAGENTA, width=1)
        for name, entry in report["rows"].items():
            y = entry["y"]
            draw.line([(0, y), (width, y)], fill=RED, width=1)
            for a, e in runs(mask[y], report["min_run"]):
                draw.line([(a, y), (e, y)], fill=CYAN, width=3)
            draw.text((8, y - 4 - size), name, fill=RED, font=font)
        for y in (report["top_y"], report["bottom_y"]):
            draw.line([(0, y), (width, y)], fill=MAGENTA, width=1)
    save_picture(image, path)


# ---------- 絵どうしを同じ物差しで比べる ----------

LEG_SEARCH = {"start_above_sole": 3, "misses": 6, "follow": 0.2, "smooth": 9}
BODY_TABLE = (("waist_w", "ウエストの幅"), ("thigh_w", "太ももの幅（0.65 U）"), ("knee_w", "ひざの幅"), ("calf_w", "ふくらはぎの幅"),
              ("ankle_w", "足首の幅"), ("hem_h", "裾の高さ"), ("knee_h", "ひざの高さ"), ("calf_h", "ふくらはぎの高さ"), ("ankle_h", "足首の高さ"))


def measure_legs(mask, min_run=6):
    """正面の全身の絵（腕の形は問わない）を、足の裏からウエストまでの高さ U を物差しに測る。脚は、足の裏の少し上から上へ、体の中心を
    またがない左右の区間の組が続く行（中心はなめらかに追う）。裾は脚の見えるいちばん上の行。ウエストは、裾から上へ、脚の見える高さの
    0.05〜0.55 倍の行で、中心をまたぐ区間がいちばん狭い行。足首は 0.10〜0.30 U でいちばん細い行、ふくらはぎはその上 0.50 U まででいちばん
    太い行、ひざはふくらはぎの上 0.62 U までで、いちばん細い幅の 3% 以内の行の真ん中（ひざは細い所が上下に長く続くので 1 行に決めない）、
    太ももは 0.65 U の行（脚の幅は 9 行でならす）。幅と高さは U に対する割合。"""
    sole = int(_present(mask)[-1])
    leg_w, centre_x = {}, {}
    y, misses, c = sole - LEG_SEARCH["start_above_sole"], 0, None
    while y > 0 and misses < LEG_SEARCH["misses"]:
        rr = runs(mask[y], min_run)
        if c is None and len(rr) == 2:
            c = (rr[0][1] + rr[1][0]) / 2.0
        if c is not None and len(rr) >= 2 and not any(a <= c <= e for a, e in rr):
            left = max((r for r in rr if r[1] < c), key=lambda r: r[1], default=None)
            right = min((r for r in rr if r[0] > c), key=lambda r: r[0], default=None)
            if left and right:
                leg_w[y] = (left[1] - left[0] + right[1] - right[0]) / 2.0
                centre_x[y] = (left[1] + right[0]) / 2.0
                c = (1 - LEG_SEARCH["follow"]) * c + LEG_SEARCH["follow"] * centre_x[y]
                misses = 0
            else:
                misses += 1
        else:
            misses += 1
        y -= 1
    if not leg_w:
        raise ToolError("脚（体の中心の左右の 2 つの区間）が見つかりません（正面の全身の絵か確かめてください）")
    ys = np.array(sorted(leg_w))
    hem = int(ys[0])
    visible = sole - hem
    cx = float(np.median([centre_x[v] for v in ys]))
    waist = None
    for yy in range(max(0, int(hem - 0.55 * visible)), int(hem - 0.05 * visible)):
        for a, e in runs(mask[yy], min_run):
            if a <= cx <= e and (waist is None or e - a < waist[1]):
                waist = (yy, e - a)
    if waist is None:
        raise ToolError("ウエスト（裾の上で体の中心をまたぐ区間）が見つかりません")
    U = sole - waist[0]
    h = sole - ys
    w = np.array([leg_w[v] for v in ys])
    order = np.argsort(h)
    half = LEG_SEARCH["smooth"] // 2
    h, w = h[order], np.convolve(np.pad(w[order], half, mode="edge"), np.ones(LEG_SEARCH["smooth"]) / LEG_SEARCH["smooth"], mode="valid")

    def pick(low, high, choose, what):
        sel = np.where((h >= low) & (h <= high))[0]
        if not len(sel):
            raise ToolError(f"{what}を探す高さ（足の裏から {low / U:.2f}〜{high / U:.2f} U）に脚が見えません")
        i = sel[choose(w[sel])]
        return float(h[i]), float(w[i])

    ankle_h, ankle_w = pick(0.10 * U, 0.30 * U, np.argmin, "足首")
    calf_h, calf_w = pick(ankle_h, 0.50 * U, np.argmax, "ふくらはぎ")
    sel = np.where((h > calf_h) & (h <= 0.62 * U))[0]
    if not len(sel):
        raise ToolError("ひざを探す高さ（ふくらはぎの上、0.62 U まで）に脚が見えません")
    knee_w = float(w[sel].min())
    knee_h = float(h[sel][w[sel] <= knee_w * 1.03].mean())
    thigh = float(np.interp(0.65 * U, h, w)) if h.max() >= 0.65 * U else None
    return {"sole_y": sole, "waist_y": int(waist[0]), "hem_y": hem, "U_px": int(U), "centre_x": cx,
            "waist_w": waist[1] / U, "thigh_w": None if thigh is None else thigh / U, "knee_w": knee_w / U, "calf_w": calf_w / U,
            "ankle_w": ankle_w / U, "hem_h": visible / U, "knee_h": knee_h / U, "calf_h": calf_h / U, "ankle_h": ankle_h / U}


def compare_legs(reference, body):
    """元絵と体の絵の measure_legs の表（値と、体の絵 ÷ 元絵）。"""
    return {key: {"label": label, "reference": reference[key], "body": body[key],
                  "body_vs_reference": None if reference[key] is None or body[key] is None else body[key] / reference[key]}
            for key, label in BODY_TABLE}


def whitened(rgb, mask):
    """人物の外を白にした (H, W, 3)。"""
    out = rgb.copy()
    out[~mask] = (255, 255, 255)
    return out


def outline(mask, width=2):
    """人物の輪郭の画素（人物の画素のうち、上下左右のどこかが人物でない画素）を width 画素の太さにした (H, W) の真偽。"""
    inner = mask.copy()
    inner[1:, :] &= mask[:-1, :]
    inner[:-1, :] &= mask[1:, :]
    inner[:, 1:] &= mask[:, :-1]
    inner[:, :-1] &= mask[:, 1:]
    edge = mask & ~inner
    for _ in range(width - 1):
        grown = edge.copy()
        grown[1:, :] |= edge[:-1, :]
        grown[:, 1:] |= edge[:, :-1]
        edge = grown
    return edge


def _affine(image, size, scale, offset, resample, fill):
    """出力 = scale × 元 + offset の絵（Pillow の AFFINE には、出力から元へ戻す式を渡す）。"""
    from PIL import Image
    return image.transform(size, Image.Transform.AFFINE,
                           (1 / scale, 0, -offset[0] / scale, 0, 1 / scale, -offset[1] / scale), resample=resample, fillcolor=fill)


def body_overlay(ref_rgb, ref_mask, body_rgb, body_mask, reference, body, path):
    """体の絵を、足の裏とウエストの行が元絵と重なるように拡大・縮小して重ねた絵: 左は元絵に両方の輪郭（元絵は水色・体の絵は赤）、右は
    重ねた体の絵に元絵の輪郭。足の裏とウエストに灰色の線。高さは足の裏の 1.25 U 上から 20 画素下まで。"""
    from PIL import Image
    s = reference["U_px"] / body["U_px"]
    offset = (reference["centre_x"] - s * body["centre_x"], reference["sole_y"] - s * body["sole_y"])
    height, width = ref_mask.shape
    mask_image = Image.fromarray(body_mask.astype(np.uint8) * 255)
    warped_mask = np.asarray(_affine(mask_image, (width, height), s, offset, Image.Resampling.BILINEAR, 0)) > 127
    warped = np.asarray(_affine(Image.fromarray(whitened(body_rgb, body_mask)), (width, height), s, offset,
                                Image.Resampling.BILINEAR, (255, 255, 255))).copy()
    left = whitened(ref_rgb, ref_mask)
    left[outline(ref_mask)] = (0, 200, 255)
    left[outline(warped_mask)] = (255, 0, 0)
    warped[outline(ref_mask)] = (0, 200, 255)
    sheet = np.concatenate([left, warped], axis=1)
    for y in (reference["sole_y"], reference["waist_y"]):
        sheet[y] = (160, 160, 160)
    top = max(0, int(reference["sole_y"] - 1.25 * reference["U_px"]))
    save_picture(Image.fromarray(sheet[top:min(height, reference["sole_y"] + 20)]), path)


def _shift(mask, dy, dx, fill):
    """out[y, x] = mask[y + dy, x + dx]（絵の外は fill）。"""
    h, w = mask.shape
    out = np.full((h, w), fill, dtype=bool)
    y0, y1, x0, x1 = max(0, -dy), min(h, h - dy), max(0, -dx), min(w, w - dx)
    if y0 < y1 and x0 < x1:
        out[y0:y1, x0:x1] = mask[y0 + dy:y1 + dy, x0 + dx:x1 + dx]
    return out


def open_mask(mask, size):
    """OpenCV の morphologyEx（MORPH_OPEN、size × size の四角、中心 (size // 2, size // 2)）と同じ開き（縮めてから広げる）。縮めるときは
    絵の外を真、広げるときは偽として扱う。縮めると広げるで同じ向きに寄せるので、size が偶数だと OpenCV と同じく 1 画素ずれる。"""
    offsets = range(-(size // 2), size - size // 2)
    eroded = np.ones_like(mask, dtype=bool)
    for dy in offsets:
        for dx in offsets:
            eroded &= _shift(mask, dy, dx, True)
    opened = np.zeros_like(mask, dtype=bool)
    for dy in offsets:
        for dx in offsets:
            opened |= _shift(eroded, dy, dx, False)
    return opened


def components(mask):
    """8 近傍でつながった真の画素のかたまりの [(面積, 重心 x, 重心 y)]。並びは OpenCV の connectedComponentsWithStats（既定の
    Spaghetti）の番号の順: 2 × 2 の区画を上から 2 行ずつ・左から見て、かたまりが最初に現れる区画の順。"""
    segments = [(y, a, e) for y in range(mask.shape[0]) for a, e in runs(mask[y])]
    parent = list(range(len(segments)))

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    previous, start = [], 0
    for y in range(mask.shape[0]):
        current = []
        while start < len(segments) and segments[start][0] == y:
            current.append(start)
            start += 1
        for k in current:
            _, a, e = segments[k]
            for j in previous:
                _, pa, pe = segments[j]
                if pa <= e and pe >= a:                 # 上の行の区間と、斜めも含めて触れる
                    ra, rb = find(k), find(j)
                    if ra != rb:
                        parent[max(ra, rb)] = min(ra, rb)
        previous = current
    blobs = {}
    for k, (y, a, e) in enumerate(segments):
        n = e - a
        entry = blobs.setdefault(find(k), [0, 0.0, 0.0, (y // 2, a // 2)])
        entry[0] += n
        entry[1] += (a + e - 1) * n / 2.0
        entry[2] += y * n
        entry[3] = min(entry[3], (y // 2, a // 2))
    return [(area, sx / area, sy / area) for area, sx, sy, _ in sorted(blobs.values(), key=lambda b: b[3])]


EYE_PAIR = {"blobs": 14, "min_dx": 20, "max_dy": 0.25, "max_area_ratio": 2.5}


def find_eyes(rgb, rule, kernel=3):
    """瞳の色の決まり rule に入る画素を開いて（kernel × kernel）かたまりに分け、大きい順の 14 個から、ほぼ同じ高さに並ぶ（横に 20 画素
    以上離れ、縦のずれが横の 0.25 倍以下）、同じくらいの大きさ（2.5 倍以内）の 2 つの組のうち、小さいほうがいちばん大きい組を両目とする
    （左、右の順の [x, y]）。見つからなければ None。"""
    blobs = sorted(components(open_mask(color_rule(rgb, rule), kernel)), key=lambda b: -b[0])[:EYE_PAIR["blobs"]]
    best = None
    for i in range(len(blobs)):
        for j in range(i + 1, len(blobs)):
            (a1, x1, y1), (a2, x2, y2) = blobs[i], blobs[j]
            dx, dy = abs(x1 - x2), abs(y1 - y2)
            if dx < EYE_PAIR["min_dx"] or dy > EYE_PAIR["max_dy"] * dx or max(a1, a2) > EYE_PAIR["max_area_ratio"] * min(a1, a2):
                continue
            if best is None or min(a1, a2) > best[0]:
                best = (min(a1, a2), (x1, y1), (x2, y2))
    if best is None:
        return None
    left, right = sorted([best[1], best[2]], key=lambda p: p[0])
    return [list(left), list(right)]


def face_crop(rgb, eyes, size):
    """両目の間隔を size 画素・傾きを 0 に合わせた顔のまわり（幅 4.4 size、高さ 5.4 size。目は上から 2.2 size、横の真ん中）と、
    目の間隔（画素）と傾き（度）。"""
    from PIL import Image
    left, right = np.asarray(eyes[0], float), np.asarray(eyes[1], float)
    d = float(np.linalg.norm(right - left))
    if d <= 0:
        raise ToolError(f"両目が同じ位置です: {eyes}")
    angle = float(np.arctan2(right[1] - left[1], right[0] - left[0]))
    s = size / d
    width, height = int(4.4 * size), int(5.4 * size)
    image, mid = Image.fromarray(rgb), (left + right) / 2
    if s < 1:                                      # 縮めるときは先に全体を縮めて、細かい模様のちらつきを抑える
        image = image.resize((max(1, round(image.width * s)), max(1, round(image.height * s))), Image.Resampling.LANCZOS)
        mid, scale = mid * s, 1.0
    else:
        scale = s
    c, sn = np.cos(angle), np.sin(angle)
    cx, cy = width / 2, 2.2 * size
    data = (c / scale, -sn / scale, mid[0] - (c * cx - sn * cy) / scale, sn / scale, c / scale, mid[1] - (sn * cx + c * cy) / scale)
    out = image.transform((width, height), Image.Transform.AFFINE, data, resample=Image.Resampling.BICUBIC, fillcolor=(255, 255, 255))
    return np.asarray(out).copy(), d, float(np.degrees(angle))


def face_guides(crop, size):
    """目の高さ（橙）から下へ 0.5 size ごとの線と目盛り、目の真ん中から左右へ 0.5 size の縦の線。"""
    from PIL import Image, ImageDraw, ImageFont
    image = Image.fromarray(crop)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=max(12, size // 9))
    for k in range(7):
        y = int(2.2 * size + k * 0.5 * size)
        draw.line([(0, y), (image.width, y)], fill=(255, 160, 0) if k == 0 else (200, 200, 200), width=1)
        draw.text((4, y - 2 - max(12, size // 9)), "%.1fd" % (k * 0.5), fill=(120, 120, 120), font=font)
    for dx in (-0.5, 0.5):
        x = int(image.width / 2 + dx * size)
        draw.line([(x, 0), (x, image.height)], fill=(220, 220, 220), width=1)
    return np.asarray(image)


def face_zoom(crop, size):
    """目の 0.5 size 上から 1.4 size 下までを 2 倍にし、0.1 size ごとの目盛りを引いた絵（口とあごの位置を読むため）。"""
    from PIL import Image, ImageDraw, ImageFont
    eye_y, cx = int(2.2 * size), crop.shape[1] // 2
    font = ImageFont.load_default(size=max(12, size // 8))
    part = Image.fromarray(crop[eye_y - int(0.5 * size):eye_y + int(1.4 * size), cx - int(1.2 * size):cx + int(1.2 * size)])
    part = part.resize((part.width * 2, part.height * 2), Image.Resampling.BICUBIC)
    draw = ImageDraw.Draw(part)
    for k in range(-5, 15):
        y = int((0.5 * size + k * 0.1 * size) * 2)
        major = k % 5 == 0
        draw.line([(0, y), (part.width, y)], fill=(255, 160, 0) if k == 0 else ((150, 150, 150) if major else (225, 225, 225)), width=1)
        if major:
            draw.text((4, y - 2 - max(12, size // 8)), "%.1fd" % (k * 0.1), fill=(90, 90, 90), font=font)
    for k in range(-12, 13, 2):
        x = int(part.width / 2 + k * 0.1 * size * 2)
        draw.line([(x, 0), (x, part.height)], fill=(225, 225, 225) if k % 10 else (150, 150, 150), width=1)
    return np.asarray(part)


def side_by_side(images, gap=8):
    """横に並べる（間は白）。高さは、いちばん高い絵にそろえて下を白で埋める（低い絵に合わせて切ると、高い絵の下が消える）。"""
    height = max(image.shape[0] for image in images)
    parts = []
    for k, image in enumerate(images):
        if k:
            parts.append(np.full((height, gap, 3), 255, np.uint8))
        rgb = image[:, :, :3]
        if rgb.shape[0] < height:
            rgb = np.concatenate([rgb, np.full((height - rgb.shape[0], rgb.shape[1], 3), 255, np.uint8)])
        parts.append(rgb)
    return np.concatenate(parts, axis=1)


# ---------- 三面図 ----------

MARK_COLOURS = ((255, 0, 0), (0, 160, 0), (160, 0, 160), (0, 0, 255), (255, 140, 0), (0, 160, 160))
WIDEST_COLOUR = (0, 120, 255)


def view_rows(rgb, mask, marks):
    """三面図の 1 枚の、人物のいちばん上と下の行・全高・体の中央（下 1/4 の人物の画素の横の中央値）・横にいちばん広い行（正面と後ろ
    では腕の高さ）と、印の行（marks。色の決まり rule に入る人物の画素が、体の中央の帯（±strip × 全高）に least 個以上ある行のうち、
    全高に対する割合 range の中で、pick が top ならいちばん上・bottom ならいちばん下の行）。割合は全高（いちばん上から足の裏）に対する値。"""
    present = _present(mask)
    top, sole = int(present[0]), int(present[-1])
    height = sole - top
    if height <= 0:
        raise ToolError("人物の高さが 0 です")
    lower = np.where(mask[int(top + 0.75 * height):sole])[1]
    if not len(lower):
        raise ToolError("人物の下 1/4 に画素がありません（体の中央を決められない）")
    cx = int(np.median(lower))
    extent = np.array([np.ptp(np.where(mask[y])[0]) if mask[y].any() else 0 for y in range(mask.shape[0])])
    widest = int(np.argmax(extent))
    out = {"top_y": top, "sole_y": sole, "height_px": height, "centre_x": cx, "widest_y": widest,
           "widest_frac": (widest - top) / height, "marks": {}}
    for mark in marks:
        hit = color_rule(rgb, mark["rule"]) & mask
        half = int(mark["strip"] * height)
        strip = hit[:, max(0, cx - half):cx + half].sum(axis=1)
        rows = [y for y in range(int(top + mark["range"][0] * height), int(top + mark["range"][1] * height))
                if 0 <= y < mask.shape[0] and strip[y] >= mark["least"]]
        y = (min(rows) if mark["pick"] == "top" else max(rows)) if rows else None
        out["marks"][mark["name"]] = {"y": y, "frac": None if y is None else (y - top) / height}
    return out


def head_blobs(rgb, mask, rule, rows, kernel=2, above=0.2, count=3):
    """頭のあたり（いちばん上から全高の above まで）の、色の決まり rule に入る人物の画素のかたまり（kernel × kernel で開く）の、
    大きい順の count 個の [面積, [重心 x, 重心 y]]（重心は 0.1 画素に丸める）。瞳の行を読む手がかり。"""
    limit = rows["top_y"] + above * (rows["sole_y"] - rows["top_y"])
    blobs = [(area, np.round(np.array([x, y]), 1).tolist()) for area, x, y in components(open_mask(color_rule(rgb, rule) & mask, kernel))
             if y < limit]
    return [[area, centre] for area, centre in sorted(blobs, reverse=True)[:count]]


def three_view_sheet(views, path):
    """三面図を、全高を最初の絵（正面）にそろえ、足の裏を合わせて横に並べ、最初の絵の行（いちばん広い行と印）に線を引いた絵。
    views は [(人物の外を白にした RGB, view_rows の結果)]。"""
    from PIL import Image
    front = views[0][1]
    height = views[0][0].shape[0]
    half = max(1, int(0.286 * front["height_px"]))
    panels = []
    for rgb, rows in views:
        s = front["height_px"] / rows["height_px"]
        offset = (0.0, front["sole_y"] - s * rows["sole_y"])
        image = _affine(Image.fromarray(rgb), (max(1, int(rgb.shape[1] * s)), height), s, offset, Image.Resampling.BILINEAR,
                        (255, 255, 255))
        x0 = max(0, int(s * rows["centre_x"]) - half)
        panel = np.full((height, 2 * half, 3), 255, np.uint8)
        part = np.asarray(image)[:, x0:x0 + 2 * half]
        panel[:, :part.shape[1]] = part
        panels.append(panel)
    sheet = np.concatenate(panels, axis=1)
    lines = [(front["widest_y"], WIDEST_COLOUR)] + [(mark["y"], MARK_COLOURS[k % len(MARK_COLOURS)])
                                                    for k, mark in enumerate(front["marks"].values())]
    for y, colour in lines:
        if y is not None and 0 <= y < height:
            sheet[y] = colour
    save_picture(Image.fromarray(sheet), path)


def heads_sheet(views, names, path, zoom=3):
    """三面図の頭のあたり（いちばん上の 5 画素上から全高の 0.286 倍、体の中央から左右に全高の 0.165 倍）を zoom 倍にして、10 画素ごとの線と
    50 画素ごとの行番号を引いて並べた絵（髪のてっぺん・目・あごの行を目で読むため）。"""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=8 * zoom)
    panels = []
    for (rgb, rows), name in zip(views, names):
        height, half = int(0.286 * rows["height_px"]), int(0.165 * rows["height_px"])
        y0, x0 = max(0, rows["top_y"] - 5), max(0, rows["centre_x"] - half)
        part = Image.fromarray(rgb[y0:rows["top_y"] + height, x0:x0 + 2 * half])
        part = part.resize((part.width * zoom, part.height * zoom), Image.Resampling.BICUBIC)
        draw = ImageDraw.Draw(part)
        for y in range((y0 // 10 + 1) * 10, rows["top_y"] + height, 10):
            yy = (y - y0) * zoom
            major = y % 50 == 0
            draw.line([(0, yy), (part.width, yy)], fill=(255, 140, 0) if major else (225, 225, 225), width=1)
            if major:
                draw.text((4, yy - 2 - 8 * zoom), str(y), fill=(255, 90, 0), font=font)
        draw.text((part.width - 30 * zoom, 4), name, fill=(255, 0, 0), font=font)
        panels.append(np.asarray(part))
    save_picture(Image.fromarray(side_by_side(panels, gap=0)), path)


# ---------- 頭の絵の目から下を詰める ----------

TAB = 32        # OpenCV の INTER_TAB_SIZE（小数の位置を 1/32 に丸める）


def lanczos_table():
    """OpenCV の interpolateLanczos4 と同じ重みの表（(32, 8) の float32。i/32 の位置の 8 つの重み）。"""
    import math
    s45 = 0.70710678118654752440084436210485
    cs = ((1, 0), (-s45, -s45), (0, 1), (s45, -s45), (-1, 0), (s45, s45), (0, -1), (-s45, s45))
    table = np.zeros((TAB, 8), dtype=np.float32)
    for i in range(TAB):
        x = float(np.float32(i) * np.float32(1.0 / TAB))
        if x < float(np.finfo(np.float32).eps):
            table[i, 3] = 1.0
            continue
        y0 = -(x + 3) * math.pi * 0.25
        s0, c0 = math.sin(y0), math.cos(y0)
        coeffs = np.zeros(8, dtype=np.float32)
        total = np.float32(0)
        for k in range(8):
            y = -(x + 3 - k) * math.pi * 0.25
            coeffs[k] = np.float32((cs[k][0] * s0 + cs[k][1] * c0) / (y * y))
            total = np.float32(total + coeffs[k])
        table[i] = coeffs * np.float32(np.float32(1.0) / total)
    return table


def remap_rows(image, source_rows):
    """出力の行 y に、元の行 source_rows[y]（小数）を Lanczos4 で引く（列はそのまま）。image は (H, W, C) の float32。OpenCV の remap
    （INTER_LANCZOS4・BORDER_CONSTANT の 0）と同じく、位置を 1/32 に丸め、元の絵の外の行は足さず、8 行の重みを上から順に float32 で足す。"""
    height = image.shape[0]
    table = lanczos_table()
    fixed = np.rint(source_rows.astype(np.float32) * np.float32(TAB)).astype(np.int64)
    out = np.zeros((len(source_rows),) + image.shape[1:], dtype=np.float32)
    for y, value in enumerate(fixed):
        base, frac = int(value >> 5) - 3, int(value & (TAB - 1))
        if base >= height or base + 8 <= 0:
            continue
        acc = np.zeros(image.shape[1:], dtype=np.float32)
        for r in range(8):
            row = base + r
            if 0 <= row < height:
                acc = acc + image[row] * table[frac, r]
        out[y] = acc
    return out


ABOVE_BAND = ("resample", "copy")    # 帯より上の行: resample（既定。台本と同じく、ほかの行と同じ付け替えを通す）か copy（元の絵から写す）


def shorten_face(rgba, eye_y, chin_y, d, k, start=0.10, above_band="resample"):
    """透けつきの 8 ビットの (H, W, 4) の絵の、目の少し下（目の中心の行から start × d）からあごの先の行までの帯を、縦に k 倍にする
    （1 より小さいと詰める。帯の両端では変えず、真ん中ほど強く。行の対応は 元の行 = y0 + L × (t − (1 − k) sin(2πt) / 2π)、
    t = (出力の行 − y0) / (k L)）。帯より上は動かさず、帯より下は帯の長さの差だけずらす。絵の幅いっぱいに同じ付け替えをするので、
    髪の筋が横にずれない。透けの縁がにじまないよう、色に透けを掛けてから付け替え、あとで戻す（透けが 0 の画素に残っていた色は 0 になる）。
    above_band: resample（既定。台本と同じ）は帯より上の行も同じ付け替えを通す（見える画素は変わらず、透けが 0 の画素の色は 0 になる）。
    copy は帯より上の行を元の絵からそのまま写す（透けが 0 の画素の色も元のまま）。"""
    import math
    h = rgba.shape[0]
    y0 = eye_y + start * d
    L = chin_y - y0
    Lk = k * L
    shift = L - Lk
    rows = np.arange(h, dtype=np.float64)
    t = np.clip((rows - y0) / Lk, 0.0, 1.0)
    E = t - (1.0 - k) * np.sin(2 * math.pi * t) / (2 * math.pi)
    source = np.where(rows <= y0, rows, np.where(rows <= y0 + Lk, y0 + L * E, rows + shift))
    data = rgba.astype(np.float32) / 255.0
    data[:, :, :3] *= data[:, :, 3:4]
    warped = remap_rows(data, source)
    alpha = np.clip(warped[:, :, 3:4], 0, 1)
    colour = np.where(alpha > 1e-4, warped[:, :, :3] / np.maximum(alpha, 1e-4), 0)
    out = np.round(np.concatenate([np.clip(colour, 0, 1), alpha], axis=2) * 255).astype(np.uint8)
    if above_band == "copy":
        top = min(h, int(math.floor(y0)) + 1)                  # 元の行 = 出力の行 の所（帯より上）
        out[:top] = rgba[:top]
    elif above_band != "resample":
        raise ToolError(f"above_band は {' か '.join(ABOVE_BAND)} にしてください: {above_band!r}")
    return out, {"band_y": [y0, chin_y], "k": k, "start": start, "shift_px": shift, "chin_y_after": y0 + Lk, "above_band": above_band}
