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
