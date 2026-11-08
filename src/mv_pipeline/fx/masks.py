"""マスク作り（`mv fx masks PLAN.json`）。外の切り抜き・手描きの多角形・GrabCut から、人物や地面のマスクを作る。

実験的。計画の書式は fx-masks/0.1（fx_masks.schema.json。contracts/ の外）。値は 8 bit の 0..255 のまま扱い、
どの手順も実制作の 2 つのマスク作りの台本と同じ OpenCV の演算（同じ順・同じ型）なので、同じ入力と同じ OpenCV なら同じマスクになる。

    plan = load_plan("masks.json")
    result = make_masks("masks.json", out=None, force=False)   # mask_<id>.png・masks_preview.png・masks_record.json

手順（steps）: image（外で作ったマスクを読む）/ polygon（多角形を塗る）/ grabcut（多角形を手がかりに色で縁を詰める）/
close（穴とすき間を閉じる）/ islands（飛び地を消す）/ cut（先に作ったマスクを避ける）/ blur（ぼかす）。
各マスクは steps の後に blur（仕上げのぼかし）と exclusive_of（先に作ったマスクと重ねない）を掛ける。

数（面積・マスクどうしの重なり・GrabCut が変えた画素）は記録に残すだけで、合否は出さない。
rembg などの学習モデルはここでは動かさない（外で動かし、出力を image で受け取る）。
"""
import json
from pathlib import Path

import cv2
import jsonschema
import numpy as np
from PIL import Image

from ..io import PipelineError, atomic_json, read_json, safe_path, sha256
from ..runner import now
from . import VERSION, fingerprint

SCHEMA_VERSION = "fx-masks/0.1"
RECORD_VERSION = "fx-masks-record/0.1"
_SCHEMA_PATH = Path(__file__).with_name("fx_masks.schema.json")
OUTLINE = (0, 255, 255)        # 確認用の絵の GrabCut の多角形（RGB 水色。実制作の台本と同じ色）
OVERLAP_LEVEL = 20             # 重なりとして数える値（両方のマスクがこれを超える画素）
AREA_LEVEL = 127               # 面積として数える値
STEP_DEFAULTS = {
    "image": {"combine": "max"},
    "polygon": {"blur": 0.0, "combine": "max"},
    "grabcut": {"band_px": 14, "iterations": 5, "seed": 7, "combine": "max"},
    "close": {},
    "islands": {"keep": "share", "share": 0.02, "threshold": 127, "grow_px": 0},
    "cut": {"threshold": 0, "grow_px": 0, "before_blur": False},
    "blur": {},
}


def schema():
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# ---- 計画 ------------------------------------------------------------------------

def load_plan(path):
    """計画を読み、検証して既定値を埋めた (plan, 計画のディレクトリ) を返す。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"マスクの計画が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"マスクの計画が JSON として読めません ({path.name}): {error}") from error
    return parse_plan(raw, path.resolve().parent), path.resolve().parent


def parse_plan(raw, base_dir):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"マスクの計画の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("マスクの計画が正しくありません:\n" + "\n".join(lines))
    plan = {
        "schema_version": SCHEMA_VERSION, "base": raw["base"], "size": raw.get("size"), "units": raw.get("units", "norm"),
        "preview": raw.get("preview", True),
        "masks": [{"id": m["id"], "blur": float(m.get("blur", 0.0)), "exclusive_of": list(m.get("exclusive_of", [])),
                   "out": m.get("out", f"mask_{m['id']}.png"),
                   "steps": [{**STEP_DEFAULTS[s["op"]], **{k: v for k, v in s.items() if not k.startswith("x_")}} for s in m["steps"]]}
                  for m in raw["masks"]],
    }
    seen = []
    for mask in plan["masks"]:
        if mask["id"] in seen:
            raise PipelineError(f"マスクの id が重複しています: {mask['id']!r}")
        for step in mask["steps"]:
            if step["op"] == "cut" and step["mask"] not in seen:
                raise PipelineError(f"マスク {mask['id']!r} の cut は、先に作るマスクを指してください（{step['mask']!r} はまだありません）")
            if step["op"] == "close" and step["px"] % 2 == 0:
                raise PipelineError(f"マスク {mask['id']!r} の close の px は奇数にしてください: {step['px']}")
        for other in mask["exclusive_of"]:
            if other not in seen:
                raise PipelineError(f"マスク {mask['id']!r} の exclusive_of は、先に作るマスクを指してください（{other!r}）")
        seen.append(mask["id"])
    outs = [m["out"] for m in plan["masks"]]
    if len(set(outs)) != len(outs):
        raise PipelineError(f"マスクの書き出し先が重なっています: {outs}")
    base_dir = Path(base_dir)
    safe_path(base_dir, plan["base"])
    for mask in plan["masks"]:
        for step in mask["steps"]:
            if step["op"] == "image":
                safe_path(base_dir, step["path"])
    return plan


# ---- 演算（実制作の台本と同じ OpenCV の呼び方） ------------------------------------------

def ellipse(px):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(px), int(px)))


def points_px(points, shape, units):
    """多角形の頂点を画素の int32 にする（正規化座標は幅・高さを掛けてから切り捨て）。"""
    height, width = shape
    if units == "norm":
        return np.array([(x * width, y * height) for x, y in points], np.int32)
    return np.array(points, np.int32)


def polygon(shape, points, units):
    mask = np.zeros(shape, np.uint8)
    cv2.fillPoly(mask, [points_px(points, shape, units)], 255)
    return mask


def combine(current, layer, how):
    if how == "max":
        return cv2.max(current, layer)
    if how == "min":
        return cv2.min(current, layer)
    if how == "subtract":
        return np.minimum(current, 255 - layer)
    return layer.copy()


def islands(current, keep="share", share=0.02, threshold=127, grow_px=0):
    """threshold を超える画素の連結成分を残す範囲を決め、その外を 0 にする（中の値はそのまま）。成分が無ければ変えない。"""
    count, labels, stats, _ = cv2.connectedComponentsWithStats((current > threshold).astype(np.uint8))
    if count <= 1:
        return current
    areas = stats[1:, cv2.CC_STAT_AREA]
    if keep == "largest":
        region = labels == 1 + int(np.argmax(areas))
    else:
        region = np.isin(labels, 1 + np.flatnonzero(areas >= share * areas.max()))
    region = region.astype(np.uint8)
    if grow_px:
        region = cv2.dilate(region, ellipse(grow_px))
    return np.where(region > 0, current, 0).astype(np.uint8)


def grabcut(base_bgr, points, units, band_px=14, iterations=5, seed=7):
    """多角形の内側を band_px 縮めた所を前景、band_px 広げた所の外を背景と決めて GrabCut する。(切り抜き, 多角形, 変わった画素の数)。"""
    shape = base_bgr.shape[:2]
    person = polygon(shape, points, units)
    kernel = ellipse(2 * band_px + 1)
    init = np.full(shape, cv2.GC_BGD, np.uint8)
    init[cv2.dilate(person, kernel) > 0] = cv2.GC_PR_BGD
    init[person > 0] = cv2.GC_PR_FGD
    init[cv2.erode(person, kernel) > 0] = cv2.GC_FGD
    background, foreground = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    cv2.setRNGSeed(int(seed))
    try:
        cv2.grabCut(base_bgr, init, None, background, foreground, int(iterations), cv2.GC_INIT_WITH_MASK)
    except cv2.error as error:
        raise PipelineError(f"GrabCut が止まりました（多角形が画像のほぼ全体か、ほぼ空ではありませんか）: {str(error).strip()[-200:]}") from error
    cut = np.where((init == cv2.GC_FGD) | (init == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return cut, person, int(np.count_nonzero((person > 0) != (cut > 0)))


def cut_out(current, other, threshold=0, grow_px=0):
    """other の threshold を超える所を grow_px だけ広げ、そこを 0 にした写しを返す。"""
    region = (other > threshold).astype(np.uint8)
    if grow_px:
        region = cv2.dilate(region, ellipse(grow_px))
    out = current.copy()
    out[region > 0] = 0
    return out


# ---- 素材 ------------------------------------------------------------------------

def _resize(array, size, role, warnings):
    width, height = size
    if (array.shape[1], array.shape[0]) == (width, height):
        return array
    if abs(array.shape[1] * height - width * array.shape[0]) > 0.01 * width * array.shape[0]:
        raise PipelineError(f"{role} の縦横比が作る大きさ {width}x{height} と違います（{array.shape[1]}x{array.shape[0]}）")
    shrinking = array.shape[1] > width
    warnings.append(f"{role} は {array.shape[1]}x{array.shape[0]} を {width}x{height} へ縮尺しました")
    return cv2.resize(array, (width, height), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_LINEAR)


def _open(path, role):
    if not Path(path).is_file():
        raise PipelineError(f"素材が見つかりません ({role}): {Path(path).name}。計画のあるディレクトリからの相対パスを確認してください")
    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception as error:  # Pillow は形式ごとに別の例外を投げる
        raise PipelineError(f"素材を画像として読めません ({role}: {Path(path).name}): {error}") from error


def read_gray(path, role="image"):
    """マスクの画像 → uint8。RGBA でアルファが一定でなければアルファ、16 bit は 257 で割る、それ以外は輝度。

    16 bit（I;16 などの I モード。fx の深度の _full_scale と同じ決め方）かは画像の形式で決める。値の大きさで決めると、
    255 以下の画素だけの 16 bit のマスクを 8 bit とみなして、ほぼ 0 の値を不透明にしてしまう。"""
    image = _open(path, role)
    if image.mode in ("RGBA", "LA", "PA"):
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
        if alpha.min() != alpha.max():
            return np.ascontiguousarray(alpha)
    if image.mode.startswith("I"):
        values = np.asarray(image, dtype=np.float64)
        return np.clip(values / 257.0 + 0.5, 0, 255).astype(np.uint8)
    return np.asarray(image.convert("L"))


# ---- 実行 ------------------------------------------------------------------------

def build(plan, base_dir):
    """計画からマスクを作る。{"size", "base_rgb", "masks": {id: {"final", "before_blur"}}, "grabcuts": [...], "warnings", "inputs"}。"""
    base_dir = Path(base_dir)
    warnings, inputs = [], {}
    base_path = safe_path(base_dir, plan["base"])
    base_rgb = np.asarray(_open(base_path, "base").convert("RGB"))
    inputs["base"] = {"path": plan["base"], "sha256": sha256(base_path), "size": [base_rgb.shape[1], base_rgb.shape[0]]}
    size = tuple(plan["size"]) if plan["size"] else (base_rgb.shape[1], base_rgb.shape[0])
    base_rgb = np.ascontiguousarray(_resize(base_rgb, size, "base", warnings))
    base_bgr = np.ascontiguousarray(base_rgb[..., ::-1])
    shape = (size[1], size[0])
    states, grabcuts = {}, []
    for mask in plan["masks"]:
        current = np.zeros(shape, np.uint8)
        for index, step in enumerate(mask["steps"]):
            op = step["op"]
            if op == "image":
                path = safe_path(base_dir, step["path"])
                layer = _resize(read_gray(path, f"image:{step['path']}"), size, f"image:{step['path']}", warnings)
                inputs.setdefault("images", {})[step["path"]] = {"sha256": sha256(path)}
                current = combine(current, layer, step["combine"])
            elif op == "polygon":
                layer = polygon(shape, step["points"], plan["units"])
                if step["blur"] > 0:
                    layer = cv2.GaussianBlur(layer, (0, 0), step["blur"])
                current = combine(current, layer, step["combine"])
            elif op == "grabcut":
                layer, person, changed = grabcut(base_bgr, step["points"], plan["units"], step["band_px"], step["iterations"], step["seed"])
                grabcuts.append({"mask": mask["id"], "step": index, "changed_px": changed,
                                 "points_px": points_px(step["points"], shape, plan["units"]).tolist()})
                current = combine(current, layer, step["combine"])
            elif op == "close":
                current = cv2.morphologyEx(current, cv2.MORPH_CLOSE, ellipse(step["px"]))
            elif op == "islands":
                current = islands(current, step["keep"], step["share"], step["threshold"], step["grow_px"])
            elif op == "cut":
                other = states[step["mask"]]["before_blur" if step["before_blur"] else "final"]
                current = cut_out(current, other, step["threshold"], step["grow_px"])
            else:
                current = cv2.GaussianBlur(current, (0, 0), step["sigma"])
        before_blur = current
        if mask["blur"] > 0:
            current = cv2.GaussianBlur(current, (0, 0), mask["blur"])
        for other in mask["exclusive_of"]:
            current = np.minimum(current, 255 - states[other]["final"])
        states[mask["id"]] = {"final": np.ascontiguousarray(current, dtype=np.uint8), "before_blur": before_blur}
    return {"size": list(size), "base_rgb": base_rgb, "masks": states, "grabcuts": grabcuts, "warnings": warnings, "inputs": inputs}


def preview(base_rgb, masks, grabcuts=()):
    """確認用の絵: 基準画像 × 0.6 に、マスクを R・G・B の順（4 つ目からは巡回して重ねる）に × 0.45 で足し、GrabCut の多角形を水色の線で描く。"""
    image = base_rgb.astype(np.float32)
    tint = np.zeros_like(image)
    for index, mask in enumerate(masks):
        channel = index % 3
        tint[..., channel] = np.maximum(tint[..., channel], mask)
    out = np.clip(image * 0.6 + tint * 0.45, 0, 255).astype(np.uint8)
    for item in grabcuts:
        cv2.polylines(out, [np.array(item["points_px"], np.int32)], True, OUTLINE, 1)
    return out


def counts(states):
    """記録に残す数（合否なし）: 各マスクの面積（127 を超える画素）と 0 でない画素、マスクどうしの重なり（両方が 20 を超える画素）。"""
    names = list(states)
    out = {"masks": {name: {"area_px": int(np.count_nonzero(states[name]["final"] > AREA_LEVEL)),
                            "nonzero_px": int(np.count_nonzero(states[name]["final"]))} for name in names},
           "overlap_px": {}}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            both = (states[a]["final"] > OVERLAP_LEVEL) & (states[b]["final"] > OVERLAP_LEVEL)
            out["overlap_px"][f"{a}|{b}"] = int(np.count_nonzero(both))
    return out


def _png(path, array, force, protect):
    """8 bit の PNG を書く。protect なら、中身の違う既存のファイルは force が無ければ上書きしない。(書いたか, sha256)。"""
    path = Path(path)
    if path.is_file() and protect:
        with Image.open(path) as old:
            same = old.mode in ("L", "RGB") and np.array_equal(np.asarray(old), array)
        if same:
            return False, sha256(path)
        if not force:
            raise PipelineError(f"{path.name} が既にあり、中身が違います（手で直したマスクかもしれません）。上書きするなら --force を付けてください")
    from .compare import save_png_atomic
    save_png_atomic(array, path)
    return True, sha256(path)


PREVIEW_NAME, RECORD_NAME = "masks_preview.png", "masks_record.json"


def output_targets(plan, out_dir):
    """マスクごとの書き出し先（解決した絶対パス）。確認用の絵と記録の名前や、ほかのマスクと同じファイルを指せば、何も書く前に止める
    （綴りが違っても同じファイルなら同じ。確認用の絵は preview を切っていても名前を空けておく）。"""
    import os

    def key(path):
        return os.path.normcase(str(path))

    reserved = {key((Path(out_dir) / name).resolve()) for name in (PREVIEW_NAME, RECORD_NAME)}
    targets, seen = {}, {}
    for mask in plan["masks"]:
        target = safe_path(out_dir, mask["out"])
        if key(target) in reserved:
            raise PipelineError(f"マスク {mask['id']!r} の書き出し先 {mask['out']!r} は、確認用の絵か記録（{PREVIEW_NAME}・{RECORD_NAME}）と"
                                f"同じです。別の名前にしてください")
        if key(target) in seen:
            raise PipelineError(f"マスク {mask['id']!r} と {seen[key(target)]!r} の書き出し先が同じファイルです: {mask['out']!r}")
        seen[key(target)] = mask["id"]
        targets[mask["id"]] = target
    return targets


def make_masks(plan_path, out=None, force=False):
    """`mv fx masks`: 計画からマスクを作って書き、記録（masks_record.json）を残す。結果の要約を返す。"""
    plan, base_dir = load_plan(plan_path)
    out_dir = Path(out).resolve() if out else base_dir
    targets = output_targets(plan, out_dir)
    built = build(plan, base_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    written = {}
    for mask in plan["masks"]:
        target = targets[mask["id"]]
        changed, digest = _png(target, built["masks"][mask["id"]]["final"], force, protect=True)
        written[mask["id"]] = {"path": str(target), "sha256": digest, "written": changed}
    preview_path = None
    if plan["preview"]:
        preview_path = out_dir / PREVIEW_NAME
        _png(preview_path, preview(built["base_rgb"], [built["masks"][m["id"]]["final"] for m in plan["masks"]], built["grabcuts"]),
             force, protect=False)
    numbers = counts(built["masks"])
    for item in built["grabcuts"]:
        numbers.setdefault("grabcut_changed_px", {})[f"{item['mask']}#{item['step']}"] = item["changed_px"]
    record = {"schema_version": RECORD_VERSION, "created_at": now(),
              "plan": {"path": str(Path(plan_path).resolve()), "sha256": sha256(plan_path), "normalized": plan},
              "inputs": built["inputs"], "size": built["size"], "masks": written, "preview": str(preview_path) if preview_path else None,
              "counts": numbers, "warnings": built["warnings"],
              "engine": {"version": VERSION, "fingerprint": fingerprint(), "opencv": cv2.__version__}}
    atomic_json(out_dir / RECORD_NAME, record)
    return {"masks": {k: v["path"] for k, v in written.items()}, "written": [k for k, v in written.items() if v["written"]],
            "preview": record["preview"], "record": str(out_dir / RECORD_NAME), "size": built["size"],
            "counts": numbers, "warnings": built["warnings"]}
