"""繰り返し物の位置検出（`mv fx points PLAN.json`）。同じ形の小物（並んだ灯の皿など）の位置を、数個の例（種）から拾う。

実験的。計画の書式は fx-points/0.1（fx_points.schema.json。contracts/ の外）。hooks の部品（sky.lamps など）に渡す位置の表を作る
下ごしらえで、`mv fx masks` の隣に置く。

    plan, base_dir = load_plan("points_plan.json")
    result = find_points("points_plan.json", out=None)      # points.json・points_preview.jpg・template.png

手順: 種の周りの切り抜き（patch_px 四方）を 1 枚ずつ平均 0・分散 1 にそろえ、画素ごとの中央値で型紙を作る → 画像の輝度と
正規化相互相関（cv2.matchTemplate の TM_CCOEFF_NORMED）→ threshold 以上の極大を、点数の高い順に min_distance_px の間隔で拾う →
refine 回、点数の高い検出（上位 REFINE_SHARE）から型紙を作り直して拾い直す。include・exclude は検出の中心で見る。

乱数を使わない。同じ入力（計画・画像）と同じ環境（fx と OpenCV の版）なら同じ出力（points.json に時刻と絶対パスは入れない）。
点数としきい値は報告するだけで、合否は出さない。採用は人が points_preview.jpg を見て決める。しきい値の既定は合成素材で決めた仮の値。
"""
import json
import math
import os
from pathlib import Path

import cv2
import jsonschema
import numpy as np

from ..io import JPEG_MAX_SIDE, PipelineError, atomic_image, atomic_json, read_json, safe_path, sha256
from . import VERSION, fingerprint

SCHEMA_VERSION = "fx-points/0.1"
RESULT_VERSION = "fx-points-result/0.1"
_SCHEMA_PATH = Path(__file__).with_name("fx_points.schema.json")
DEFAULT_THRESHOLD = 0.75       # 仮の値（合成素材で決めた。tests/test_fx_points.py の場面で、輪と形の違う点の点数の間）
DEFAULT_REFINE = 1
REFINE_SHARE = 0.5             # 型紙の作り直しに使う検出: 点数の上位この割合（仮の値。MIN_PATCHES 個は下回らない）
MIN_PATCHES = 3                # 型紙を作る切り抜きの最少の数（種の最少の数と同じ）
MAX_CANDIDATES = 200_000       # しきい値以上の極大がこれを超えたら止める（しきい値が低すぎて、画面じゅうを拾っている）
POINTS_NAME, PREVIEW_NAME, TEMPLATE_NAME = "points.json", "points_preview.jpg", "template.png"
POINT_COLOR = (0, 255, 255)    # RGB: 検出の丸と番号（水色）
SEED_COLOR = (255, 90, 220)    # RGB: 種の十字（桃）
INCLUDE_COLOR = (0, 255, 0)    # RGB: include の範囲の線（緑）
EXCLUDE_COLOR = (255, 60, 60)  # RGB: exclude の範囲の線（赤）
FONT = cv2.FONT_HERSHEY_SIMPLEX


def schema():
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# ---- 計画 ------------------------------------------------------------------------

def load_plan(path):
    """計画を読み、検証して既定値を埋めた (plan, 計画のディレクトリ) を返す。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"点の計画が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"点の計画が JSON として読めません ({path.name}): {error}") from error
    return parse_plan(raw, path.resolve().parent), path.resolve().parent


def _range(raw, where):
    """範囲 1 つを検証して、x_ の鍵を落とした写しを返す。"""
    if "box" in raw:
        x0, y0, x1, y1 = (float(v) for v in raw["box"])
        if not (x0 < x1 and y0 < y1):
            raise PipelineError(f"{where} の box は [x0, y0, x1, y1]（x0 < x1、y0 < y1）にしてください: {raw['box']}")
        return {"box": [x0, y0, x1, y1]}
    if "polygon" in raw:
        return {"polygon": [[float(u), float(v)] for u, v in raw["polygon"]]}
    ellipse = raw["ellipse"]
    out = {"center": [float(v) for v in ellipse["center"]], "rx": float(ellipse["rx"]),
           "ry_ratio": float(ellipse.get("ry_ratio", 1.0)), "r_min": float(ellipse.get("r_min", 0.0))}
    if out["r_min"] >= out["rx"]:
        raise PipelineError(f"{where} の ellipse の r_min は rx より小さくしてください（r_min {out['r_min']}、rx {out['rx']}）")
    return {"ellipse": out}


def parse_plan(raw, base_dir):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"点の計画の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("点の計画が正しくありません:\n" + "\n".join(lines))
    patch = int(raw["patch_px"])
    if patch % 2 == 0:
        raise PipelineError(f"patch_px は奇数にしてください（中心の画素の両側を同じ幅で切り抜く）: {patch}")
    plan = {
        "schema_version": SCHEMA_VERSION, "image": raw["image"], "seeds": [[float(u), float(v)] for u, v in raw["seeds"]],
        "patch_px": patch, "threshold": float(raw.get("threshold", DEFAULT_THRESHOLD)),
        "min_distance_px": int(raw.get("min_distance_px", (patch + 1) // 2)), "refine": int(raw.get("refine", DEFAULT_REFINE)),
        "include": [_range(r, f"include[{i}]") for i, r in enumerate(raw.get("include", []))],
        "exclude": [_range(r, f"exclude[{i}]") for i, r in enumerate(raw.get("exclude", []))],
    }
    safe_path(base_dir, plan["image"])
    return plan


# ---- 範囲（include・exclude） ---------------------------------------------------------

def ellipse_radius(u, v, center, ry_ratio, size):
    """正規化座標 (u, v) の、楕円の中心からの距離（画像の幅を 1 とした横の半径。縦は画素で ry_ratio 倍につぶれた楕円の上で同じ値）。

    hooks で「輪の内側から順に」の時刻を作るときにも同じ式を使える（u・v は配列でもよい）。"""
    width, height = size
    return np.hypot((np.asarray(u, np.float64) - center[0]) * width,
                    (np.asarray(v, np.float64) - center[1]) * height / ry_ratio) / width


def region(item, size):
    """範囲 1 つ → 画素ごとの真偽（H×W）。box と ellipse は、画素の中心（正規化座標 (x + 0.5) / 幅）が範囲に入れば真。
    polygon は `mv fx masks` の polygon と同じ塗り方（頂点を画素に切り捨てて cv2.fillPoly。縁の画素を含む）。"""
    width, height = size
    if "polygon" in item:
        mask = np.zeros((height, width), np.uint8)
        cv2.fillPoly(mask, [np.array([(u * width, v * height) for u, v in item["polygon"]], np.int32)], 255)
        return mask > 0
    u = ((np.arange(width, dtype=np.float64) + 0.5) / width)[None, :]
    v = ((np.arange(height, dtype=np.float64) + 0.5) / height)[:, None]
    if "box" in item:
        x0, y0, x1, y1 = item["box"]
        return (u >= x0) & (u <= x1) & (v >= y0) & (v <= y1)
    ellipse = item["ellipse"]
    radius = ellipse_radius(u, v, ellipse["center"], ellipse["ry_ratio"], size)
    return (radius >= ellipse["r_min"]) & (radius <= ellipse["rx"])


def allowed_area(plan, size):
    """検出の中心を置ける画素（H×W の真偽）: include のどれかに入り（include が無ければ全体）、exclude のどれにも入らない所。"""
    width, height = size
    allowed = np.ones((height, width), bool)
    if plan["include"]:
        allowed = np.zeros((height, width), bool)
        for item in plan["include"]:
            allowed |= region(item, size)
    for item in plan["exclude"]:
        allowed &= ~region(item, size)
    return allowed


# ---- 型紙と照合 -------------------------------------------------------------------

def to_pixel(u, v, size):
    """正規化座標 → その点を含む画素（整数の中心）。1.0 ちょうどは最後の画素。"""
    width, height = size
    return min(int(u * width), width - 1), min(int(v * height), height - 1)


def to_norm(x, y, size):
    """画素（整数の中心）→ 正規化座標（view.point に渡すと画素の中心に戻る）。"""
    width, height = size
    return (x + 0.5) / width, (y + 0.5) / height


def patches(gray, centers, half):
    """中心 (x, y) の周りの (2·half + 1) 四方の切り抜きを、1 枚ずつ平均 0・分散 1 にそろえて積む（float32）。平らな切り抜きは None。"""
    out = []
    for x, y in centers:
        patch = gray[y - half:y + half + 1, x - half:x + half + 1].astype(np.float64)
        spread = patch.std()
        out.append(None if spread <= 0 else ((patch - patch.mean()) / spread).astype(np.float32))
    return out


def template_of(stack):
    """そろえた切り抜きの、画素ごとの中央値（float32）。例の 1 つにだけ写り込んだもの（隣の物・汚れ）に引きずられにくい。"""
    template = np.median(np.stack(stack), axis=0).astype(np.float32)
    if float(template.std()) < 1e-6:
        raise PipelineError("型紙が平らになりました（種の周りに共通の形がありません）。種の位置と patch_px を確認してください")
    return template


def correlate(gray, template, flat):
    """正規化相互相関の点数（−1..1）。結果の (y, x) は中心が画像の (y + half, x + half) の窓。平らな窓（flat）は 0 にする。"""
    score = cv2.matchTemplate(gray, template, cv2.TM_CCOEFF_NORMED)
    score = np.nan_to_num(score, nan=0.0, posinf=0.0, neginf=0.0)
    np.clip(score, -1.0, 1.0, out=score)
    score[flat] = 0.0
    return score


def flat_windows(gray8, patch):
    """patch 四方の窓の画素がすべて同じ値の所（分散 0 で相関が決まらない）。correlate の結果と同じ並び。"""
    half = patch // 2
    kernel = np.ones((patch, patch), np.uint8)
    same = cv2.dilate(gray8, kernel) == cv2.erode(gray8, kernel)
    return same[half:gray8.shape[0] - half, half:gray8.shape[1] - half]


def peaks(score, threshold, min_distance, allowed):
    """threshold 以上の極大（周りの 8 画素より低くない画素）のうち allowed の所を、点数の高い順に拾う。先に拾った点から
    min_distance 画素より近いものは捨てる。同点は上から・左から。[(x, y, 点数)]（x・y は score の添え字）。"""
    top = cv2.dilate(score, np.ones((3, 3), np.uint8))
    ys, xs = np.nonzero((score >= threshold) & (score == top) & allowed)
    if len(ys) > MAX_CANDIDATES:
        raise PipelineError(f"threshold {threshold} 以上の極大が {len(ys)} 個あります（上限 {MAX_CANDIDATES}）。"
                            f"画面じゅうを拾っているので、threshold を上げてください")
    values = score[ys, xs]
    order = np.lexsort((xs, ys, -values))
    cells, found, limit = {}, [], min_distance * min_distance
    for index in order:
        x, y = int(xs[index]), int(ys[index])
        cx, cy = x // min_distance, y // min_distance
        near = (other for i in (cx - 1, cx, cx + 1) for j in (cy - 1, cy, cy + 1) for other in cells.get((i, j), ()))
        if any((x - ox) ** 2 + (y - oy) ** 2 < limit for ox, oy in near):
            continue
        cells.setdefault((cx, cy), []).append((x, y))
        found.append((x, y, float(values[index])))
    return found


def detect(gray8, seeds, patch, threshold, min_distance, refine, allowed):
    """種（画素の中心 [(x, y)]）から型紙を作って拾う。{"template", "points": [(x, y, 点数)]（上から・左から）, "seed_scores", "rounds", "warnings"}。

    gray8 は uint8 の輝度（H×W）、allowed は検出の中心を置ける画素（H×W の真偽）。点の x・y は画像の画素（整数の中心）。"""
    half = patch // 2
    height, width = gray8.shape
    gray = gray8.astype(np.float32)
    flat = flat_windows(gray8, patch)
    inner = allowed[half:height - half, half:width - half]
    warnings, rounds = [], []
    stack = patches(gray, seeds, half)
    for index, item in enumerate(stack):
        if item is None:
            raise PipelineError(f"種 {index} の周り {patch} 画素が平らです（同じ値の画素だけ）。物の上の点を選んでください")
    template = template_of(stack)
    score = correlate(gray, template, flat)
    found = peaks(score, threshold, min_distance, inner)
    rounds.append({"template": "seeds", "patches": len(stack), "count": len(found)})
    for _ in range(refine):
        if len(found) < MIN_PATCHES:
            warnings.append(f"検出が {len(found)} 個なので、型紙の作り直し（refine）をやめました（{MIN_PATCHES} 個以上で作り直す）")
            break
        best = sorted(found, key=lambda p: (-p[2], p[1], p[0]))[:max(MIN_PATCHES, math.ceil(len(found) * REFINE_SHARE))]
        stack = [item for item in patches(gray, [(x + half, y + half) for x, y, _ in best], half) if item is not None]
        template = template_of(stack)
        score = correlate(gray, template, flat)
        found = peaks(score, threshold, min_distance, inner)
        rounds.append({"template": "detections", "patches": len(stack), "count": len(found)})
    if not found:
        warnings.append(f"threshold {threshold} 以上の検出がありません（種の点数は seeds を参照）")
    points = sorted(((x + half, y + half, value) for x, y, value in found), key=lambda p: (p[1], p[0]))
    seed_scores = [float(score[y - half, x - half]) for x, y in seeds]
    for index, ((x, y), value) in enumerate(zip(seeds, seed_scores)):
        near = any((x - px) ** 2 + (y - py) ** 2 <= min_distance * min_distance for px, py, _ in points)
        if found and allowed[y, x] and not near:
            warnings.append(f"種 {index} の近く（{min_distance} 画素以内）に検出がありません（種の位置の点数 {value:.2f}）")
    return {"template": template, "points": points, "seed_scores": seed_scores, "rounds": rounds, "warnings": warnings}


# ---- 素材と見るための絵 --------------------------------------------------------------

def read_image(path):
    """画像を uint8 の RGB で読む（`mv fx guides` と同じ読み方。16 bit は 8 bit に、アルファは捨てる）。"""
    from .guides import viewable
    from .masks import _open
    return np.ascontiguousarray(viewable(_open(path, "image")))


def template_image(template):
    """型紙を見るための 8 bit（最小〜最大を 0〜255 に伸ばす。照合に使う値そのものではない）。"""
    low, high = float(template.min()), float(template.max())
    return np.clip((template - low) / (high - low) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def _outline(image, item, color, thick):
    height, width = image.shape[:2]
    if "box" in item:
        x0, y0, x1, y1 = item["box"]
        cv2.rectangle(image, (int(x0 * width), int(y0 * height)), (int(x1 * width), int(y1 * height)), color, thick)
    elif "polygon" in item:
        cv2.polylines(image, [np.array([(u * width, v * height) for u, v in item["polygon"]], np.int32)], True, color, thick)
    else:
        ellipse = item["ellipse"]
        center = (int(ellipse["center"][0] * width), int(ellipse["center"][1] * height))
        for radius in (ellipse["rx"], ellipse["r_min"]):
            if radius > 0:
                axes = (int(round(radius * width)), int(round(radius * width * ellipse["ry_ratio"])))
                cv2.ellipse(image, center, axes, 0, 0, 360, color, thick, cv2.LINE_AA)


def preview(rgb, plan, points, seeds):
    """見るための絵: 画像の写しに、範囲の線（include は緑・exclude は赤）、種の十字（桃）、検出の丸と番号（水色。0 から。points の並び）。"""
    out = np.ascontiguousarray(rgb.copy())
    unit = max(1.0, out.shape[1] / 1600.0)
    thick, font_scale, radius = max(1, round(unit)), 0.4 * unit, plan["patch_px"] // 2 + 1
    for item in plan["include"]:
        _outline(out, item, INCLUDE_COLOR, thick)
    for item in plan["exclude"]:
        _outline(out, item, EXCLUDE_COLOR, thick)
    for x, y in seeds:
        cv2.drawMarker(out, (x, y), SEED_COLOR, cv2.MARKER_CROSS, 2 * radius + 1, thick)
    for index, (x, y, _) in enumerate(points):
        cv2.circle(out, (x, y), radius, POINT_COLOR, thick, cv2.LINE_AA)
        cv2.putText(out, str(index), (x + radius + 1, y - radius), FONT, font_scale, POINT_COLOR, thick, cv2.LINE_AA)
    return out


# ---- 実行 ------------------------------------------------------------------------

def output_targets(out_dir, plan_path, image_path):
    """書き出し先（points.json・points_preview.jpg・template.png）。入力（計画・画像）と同じファイルなら、何も書く前に止める。
    画像の書き出しの一時ファイル（io.atomic_image の <名前>.tmp）も同じに扱う。"""
    def key(path):
        return os.path.normcase(str(Path(path).resolve()))

    inputs = {key(plan_path): "計画", key(image_path): "画像"}
    targets = {name: Path(out_dir) / name for name in (POINTS_NAME, PREVIEW_NAME, TEMPLATE_NAME)}
    written = {**targets, **{f"{name}.tmp": Path(out_dir) / f"{name}.tmp" for name in (PREVIEW_NAME, TEMPLATE_NAME)}}
    for name, target in written.items():
        if key(target) in inputs:
            raise PipelineError(f"{name} を書くと、入力（{inputs[key(target)]}）を上書きします。入力の名前を変えるか、--out で別のフォルダーに書いてください")
    return targets


def find_points(plan_path, out=None):
    """`mv fx points`: 計画から点を拾い、points.json・points_preview.jpg・template.png を書く。結果の要約を返す。"""
    plan, base_dir = load_plan(plan_path)
    out_dir = Path(out).resolve() if out else base_dir
    image_path = safe_path(base_dir, plan["image"])
    targets = output_targets(out_dir, plan_path, image_path)
    rgb = read_image(image_path)
    height, width = rgb.shape[:2]
    size, patch = (width, height), plan["patch_px"]
    half = patch // 2
    if patch > min(width, height):
        raise PipelineError(f"patch_px {patch} が画像（{width}x{height}）より大きいです")
    if max(width, height) > JPEG_MAX_SIDE:
        raise PipelineError(f"画像（{width}x{height}）が大きすぎて {PREVIEW_NAME} を書けません（JPEG は 1 辺 {JPEG_MAX_SIDE} 画素まで）")
    seeds = [to_pixel(u, v, size) for u, v in plan["seeds"]]
    for index, (x, y) in enumerate(seeds):
        if not (half <= x < width - half and half <= y < height - half):
            raise PipelineError(f"種 {index} {plan['seeds'][index]} の周り {patch} 画素が画像（{width}x{height}）からはみ出します。"
                                f"端から {half} 画素以上内側の例を選んでください")
    if len(set(seeds)) < len(seeds):
        raise PipelineError(f"同じ画素を指す種があります（画素: {seeds}）。別々の例を {MIN_PATCHES} 個以上選んでください")
    allowed = allowed_area(plan, size)
    if not allowed[half:height - half, half:width - half].any():
        raise PipelineError("include・exclude の結果、探す範囲が残りません（範囲と、端から patch_px の半分の余白を確認してください）")
    found = detect(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY), seeds, patch, plan["threshold"], plan["min_distance_px"], plan["refine"], allowed)

    def entry(x, y, value):
        u, v = to_norm(x, y, size)
        return {"u": round(u, 6), "v": round(v, 6), "score": round(value, 4)}

    points = [entry(x, y, value) for x, y, value in found["points"]]
    result = {"schema_version": RESULT_VERSION,
              "plan": {"sha256": sha256(plan_path), "normalized": plan},
              "image": {"path": plan["image"], "sha256": sha256(image_path), "size": [width, height]},
              "template": {"size": [patch, patch], "patches": found["rounds"][-1]["patches"]},
              "threshold": plan["threshold"], "min_distance_px": plan["min_distance_px"], "rounds": found["rounds"],
              "seeds": [entry(x, y, value) for (x, y), value in zip(seeds, found["seed_scores"])],
              "count": len(points), "points": points, "warnings": found["warnings"],
              "engine": {"version": VERSION, "fingerprint": fingerprint(), "opencv": cv2.__version__}}
    out_dir.mkdir(parents=True, exist_ok=True)
    atomic_image(targets[TEMPLATE_NAME], template_image(found["template"]))
    atomic_image(targets[PREVIEW_NAME], preview(rgb, plan, found["points"], seeds))
    atomic_json(targets[POINTS_NAME], result)
    scores = [p["score"] for p in points]
    return {"points": str(targets[POINTS_NAME]), "preview": str(targets[PREVIEW_NAME]), "template": str(targets[TEMPLATE_NAME]),
            "count": len(points), "threshold": plan["threshold"], "scores": {"min": min(scores), "max": max(scores)} if scores else None,
            "size": [width, height], "rounds": found["rounds"], "warnings": found["warnings"]}
