"""別の担当から届いた絵の機械的な検査（`mv handoff check`。実験的）。

    mv handoff check BASE DELIVERED --region x0,y0,x1,y1 [--region …] [--regions-width W] [--tolerance 8] [--max-changed 0]
                     [--allow-resize] [--restore OUT.png] [--feather 12] [--out DIR]

絵の描き直しを別の担当（画像を生成する別のエージェントなど）に頼むとき、「変えてよい範囲」を決めて渡す。届いた絵の範囲の外が少しでも
変わっていると、切り替えたとき（表情の差分の顔など）に絵全体がちらつく。リポジトリ外の 2 作目の VRM のダンスで、表情の差分の顔の絵を受け取った
台本を移した。数を出し、機械で決まる合否だけを出す（絵の良し悪しは判定しない）。

- 元の絵（BASE）と届いた絵（DELIVERED）を 8 ビットの RGB で読み（アルファは捨てる）、画素ごとの差（3 色の差の絶対値の最大。0〜255）を測る。
- 範囲（--region。くり返し可。[x0, y0, x1, y1]、右と下は含まない）は元の絵の画素で書く。--regions-width を渡すと、その幅の絵で書いた範囲として
  元の絵の幅に合わせて伸ばす（例: 1254 画素の絵で書いた範囲を 2048 画素の絵に）。
- 大きさ: 届いた絵の大きさが元と違えば fail。--allow-resize なら、届いた絵を元の大きさへ面積の平均で縮めて（Pillow の BOX）比べ、warn。
- 範囲の外: 差が --tolerance（既定 8）を超える画素の数が --max-changed（既定 0）を超えたら fail。差の最大・平均と、差が 24 を超える画素の
  外接する箱（範囲を書いた座標）も出す。
- 範囲の中: 差が --tolerance を超える画素が 1 つも無ければ warn（頼んだ所が変わっていない）。
- --restore OUT.png: 範囲の外を元の絵に戻した絵を書く（範囲の縁は --feather（既定 12。範囲を書いた座標の画素）でなめらかにつなぐ。範囲を
  内側へ縮めてからぼかすので、範囲の外には一切にじまない）。合否に関係なく書く（大きさが違って --allow-resize が無いときは書けない）。
- 出力（既定は届いた絵の隣の `<届いた絵の名前>_handoff_report/`）は handoff_report.json。同じ入力と設定なら同じ報告になる（時刻や絶対パスは書かない）。
"""
import json
import math
from pathlib import Path

import numpy as np
from PIL import Image

from .io import PipelineError, atomic_json, sha256

SCHEMA = "handoff-check/0.1"
REPORT_NAME = "handoff_report.json"
BIG = 24             # 外接する箱に数える、大きな差（0〜255）
NOTE = "届いた絵の機械的な検査です（範囲の外が元と同じか・大きさ）。絵の良し悪しは判定しません。"


def read_rgb(path):
    """画像を 8 ビットの RGB（float32、0〜255）で読む。アルファは捨てる（合成しない）。"""
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"), dtype=np.float32)
    except OSError as error:
        raise PipelineError(f"画像を読めません: {path}（{error}）") from error


def parse_region(text):
    """"x0,y0,x1,y1" を 4 つの数に。"""
    try:
        values = [float(v) for v in str(text).split(",")]
    except ValueError as error:
        raise PipelineError(f"--region は x0,y0,x1,y1（数 4 つ）にしてください: {text!r}") from error
    if len(values) != 4 or not all(math.isfinite(v) for v in values) or values[2] <= values[0] or values[3] <= values[1]:
        raise PipelineError(f"--region は x0,y0,x1,y1（x1 > x0、y1 > y0）にしてください: {text!r}")
    return values


def region_mask(regions, width, height, scale):
    """範囲の中を 1 にした (H, W) の float32。範囲の座標は scale 倍して丸める（台本と同じ。Python の round）。"""
    inside = np.zeros((height, width), np.float32)
    for region in regions:
        x0, y0, x1, y1 = (int(round(v * scale)) for v in region)
        inside[max(y0, 0):max(y1, 0), max(x0, 0):max(x1, 0)] = 1.0
    return inside


def erode(mask, size):
    """正方形 size × size の最小値の窓（中心が基準。画像の外は 1 として扱う。OpenCV の erode の既定と同じ）。窓をずらした写しは積まず、
    1 枚の作業配列に最小を重ねる（大きな絵と広い窓でもメモリは絵の数枚分）。"""
    half = size // 2
    height, width = mask.shape
    padded = np.pad(mask, half, mode="constant", constant_values=1.0)
    rows = padded[:, :width].copy()
    for i in range(1, size):
        np.minimum(rows, padded[:, i:i + width], out=rows)
    out = rows[:height, :].copy()
    for i in range(1, size):
        np.minimum(out, rows[i:i + height, :], out=out)
    return out


def gaussian_blur(mask, sigma):
    """σ sigma のガウスのぼかし（窓の大きさは OpenCV の浮動小数の画像と同じ round(8σ + 1) の奇数、縁は鏡写し（端の画素は重ねない））。"""
    size = int(round(sigma * 8 + 1)) | 1
    half = size // 2
    x = np.arange(size, dtype=np.float64) - half
    kernel = np.exp(-(x * x) / (2 * sigma * sigma))
    kernel /= kernel.sum()
    out = mask.astype(np.float64)
    for axis in (1, 0):
        pad = [(0, 0), (0, 0)]
        pad[axis] = (half, half)
        padded = np.pad(out, pad, mode="reflect")
        length = out.shape[axis]
        out = sum(kernel[i] * (padded[:, i:i + length] if axis == 1 else padded[i:i + length, :]) for i in range(size))
    return out.astype(np.float32)


def restored(base, delivered, inside, feather_px):
    """範囲の外を元の絵に戻した絵（float32）。範囲を内側へ feather_px 縮めてから σ feather_px ÷ 2 でぼかす（台本と同じ）。"""
    size = int(round(feather_px)) * 2 + 1
    soft = gaussian_blur(erode(inside, size), feather_px / 2) if feather_px > 0 else inside
    soft = np.minimum(soft, inside)[..., None]
    return base * (1 - soft) + delivered * soft


def check(base_path, delivered_path, regions, regions_width=None, tolerance=8.0, max_changed=0, allow_resize=False):
    """(報告, 元の絵, 比べた届いた絵, 範囲の中の印, 範囲の座標の倍率)。"""
    if not regions:
        raise PipelineError("--region（変えてよい範囲）を 1 つ以上渡してください")
    if tolerance < 0 or max_changed < 0:
        raise PipelineError("--tolerance と --max-changed は 0 以上にしてください")
    base, delivered = read_rgb(base_path), read_rgb(delivered_path)
    height, width = base.shape[:2]
    scale = width / regions_width if regions_width else 1.0
    status, reasons = "pass", []
    size = {"base": [width, height], "delivered": [int(delivered.shape[1]), int(delivered.shape[0])], "same": delivered.shape == base.shape,
            "resized": False}
    if delivered.shape != base.shape:
        if not allow_resize:
            reasons.append(f"大きさが違います（元 {width}×{height}、届いた絵 {size['delivered'][0]}×{size['delivered'][1]}）")
            report = {"schema_version": SCHEMA, "status": "fail", "reasons": reasons, "note": NOTE, "size": size,
                      "base": {"name": Path(base_path).name, "sha256": sha256(base_path)},
                      "delivered": {"name": Path(delivered_path).name, "sha256": sha256(delivered_path)}}
            return report, base, None, None, scale
        with Image.open(delivered_path) as image:
            delivered = np.asarray(image.convert("RGB").resize((width, height), Image.BOX), dtype=np.float32)
        size["resized"] = True
        status = "warn"
        reasons.append("大きさが違うので、届いた絵を元の大きさへ面積の平均で縮めて比べました（位置が合っている前提）")
    inside = region_mask(regions, width, height, scale)
    difference = np.abs(delivered - base).max(axis=2)
    outside = inside < 0.5
    changed = int((difference[outside] > tolerance).sum())
    ys, xs = np.nonzero((difference > BIG) & outside)
    measured = {
        "outside": {"pixels": int(outside.sum()), "changed_over_tolerance": changed,
                    "max_difference": float(difference[outside].max()) if outside.any() else 0.0,
                    "mean_difference": float(difference[outside].mean()) if outside.any() else 0.0,
                    "big_change_box": [int(xs.min() / scale), int(ys.min() / scale), int(xs.max() / scale), int(ys.max() / scale)] if len(xs) else None},
        "inside": {"pixels": int((~outside).sum()), "changed_over_tolerance": int((difference[~outside] > tolerance).sum())},
    }
    if changed > max_changed:
        status = "fail"
        reasons.append(f"範囲の外で差が {tolerance:g} を超える画素が {changed}（上限 {max_changed}）")
    if measured["inside"]["changed_over_tolerance"] == 0:
        status = "warn" if status == "pass" else status
        reasons.append(f"範囲の中で差が {tolerance:g} を超える画素がありません（頼んだ所が変わっていない）")
    report = {"schema_version": SCHEMA, "status": status, "reasons": reasons, "note": NOTE, "size": size,
              "base": {"name": Path(base_path).name, "sha256": sha256(base_path)},
              "delivered": {"name": Path(delivered_path).name, "sha256": sha256(delivered_path)},
              "settings": {"regions": [list(r) for r in regions], "regions_width": regions_width, "scale": scale, "tolerance": tolerance,
                           "max_changed": max_changed, "allow_resize": allow_resize, "big": BIG},
              "measured": measured}
    return report, base, delivered, inside, scale


def run(base, delivered, regions, regions_width=None, tolerance=8.0, max_changed=0, allow_resize=False, restore=None, feather=12.0,
        out=None):
    """mv handoff check。報告を書き、(報告, 出力フォルダ) を返す。"""
    parsed = [parse_region(r) for r in regions or []]
    if regions_width is not None and regions_width <= 0:
        raise PipelineError("--regions-width は 0 より大きくしてください")
    if feather < 0:
        raise PipelineError("--feather は 0 以上にしてください")
    if restore is not None and Path(restore).suffix.lower() != ".png":   # 非可逆の書き出し（JPEG など）は、戻した範囲の外の画素まで変える
        raise PipelineError(f"--restore は PNG（.png）にしてください: {Path(restore).name}（JPEG などは範囲の外の画素まで変え、"
                            "切り替えたときのちらつきを戻してしまう）")
    report, base_image, compared, inside, scale = check(base, delivered, parsed, regions_width, tolerance, max_changed, allow_resize)
    if restore is not None:
        if compared is None:
            raise PipelineError("大きさが違うので、範囲の外を戻した絵を書けません（--allow-resize で縮めて比べると書けます）")
        result = restored(base_image, compared, inside, feather * scale)
        Path(restore).parent.mkdir(parents=True, exist_ok=True)
        Image.fromarray(np.clip(result + 0.5, 0, 255).astype(np.uint8)).save(restore, format="PNG")
        report["restored"] = {"name": Path(restore).name, "sha256": sha256(restore), "feather": feather}
    delivered = Path(delivered)
    out = Path(out) if out else delivered.with_name(delivered.stem + "_handoff_report")
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / REPORT_NAME, report)
    return report, out


def summary(report, out):
    measured = report.get("measured") or {}
    return {"status": report["status"], "reasons": report["reasons"], "size": report["size"],
            "outside_changed": (measured.get("outside") or {}).get("changed_over_tolerance"),
            "outside_max_difference": (measured.get("outside") or {}).get("max_difference"),
            "inside_changed": (measured.get("inside") or {}).get("changed_over_tolerance"),
            "restored": (report.get("restored") or {}).get("name"), "report": str(Path(out) / REPORT_NAME)}


def dumps(report):
    return json.dumps(report, ensure_ascii=False, indent=2)
