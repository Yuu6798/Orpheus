"""生成した動画の下ごしらえ（`mv fx clips PLAN.json`）: 揺れ止めと色のゲイン（実験的。docs/fx.md）。

計画の書式は fx-clips/0.1（fx_clips.schema.json。contracts/ の外）。clips の順に source を読み、out に書き、記録
（clips_record.json）を残す。どの手順も実制作の 2 つの下ごしらえの台本と同じ OpenCV の演算（同じ順・同じ型）。

揺れ止め（stabilize）: 1 コマ目に合わせて、カメラの流れを打ち消す。

    1. 測る大きさ（幅 measure_width。既定 640、高さは縦横比から）に縮めて灰色にし、σ1 でぼかす
    2. 背景（mask の白の外。dilate_px の楕円で太らせた白を除く）で、1 コマ目への ECC（回転 + 移動。200 回・1e-6・
       gaussFiltSize 5）を測る。各コマの初期値は前のコマの値で、収束しなかったコマは前のコマの値のまま（記録に残す）
    3. 移動 x・y と回転を smooth_frames（σ。既定 1.5 コマ）のガウスでならし（端は端の値。scipy の gaussian_filter1d と同じ計算）、
       1 コマ目の値を引く（1 コマ目は生成したままで、静止画とつながる）
    4. 2 コマ目からは、ならした動きを逆に掛ける（INTER_CUBIC・端の画素を伸ばす）。移動は 出力の幅 / 測る幅 倍
    5. コマごとに「元の絵が届いている内側の矩形」を記録する（寄りの範囲を決める手がかり）

  measure_from を書くと、その動画（同じコマ数。例えば超解像の前の元の動画）で測り、source に掛ける。

色のゲイン（color）: 1 コマ目を reference（元の静止画）に合わせる。

    幅 480 に縮めた 1 コマ目と reference で、色ごとに 倍率·値 + 足し算 を最小二乗で求める。合わせた後の平均の差が
    min_improvement（既定 0.5）より縮み、倍率がどれも gain_range（既定 0.85〜1.15）に入るときだけ、全コマに掛ける
    （0..255 に切って切り捨てた表を引く）。使わなかったときも、求めた値と理由を記録する。

順番は 揺れ止め → 色。書き出しは fx と同じ（libx264・BT.709 のタグ・crf 既定 12）で、コマ数と fps は source と同じ。音は書かない。
数（平均の差・動きの量・内側の矩形）は記録に残すだけで、合否は出さない。揺れ止めの後の揺れの測り直しはしない。
"""
import hashlib
import json
import os
from pathlib import Path

import jsonschema
import numpy as np

from ..io import PipelineError, atomic_json, read_json, safe_path, sha256
from ..runner import now
from . import VERSION, fingerprint, spec as fxspec

SCHEMA_VERSION = "fx-clips/0.1"
RECORD_VERSION = "fx-clips-record/0.1"
RECORD_NAME = "clips_record.json"
_SCHEMA_PATH = Path(__file__).with_name("fx_clips.schema.json")
STABILIZE = {"mask": None, "dilate_px": 25, "smooth_frames": 1.5, "measure_width": 640}
COLOR = {"gain_range": [0.85, 1.15], "min_improvement": 0.5}
ECC_ITERATIONS, ECC_EPS, ECC_GAUSS = 200, 1e-6, 5
MEASURE_BLUR = 1.0          # 測る前のぼかし（σ、測る大きさの画素）
COLOR_WIDTH = 480           # 色を合わせる大きさ（幅。高さは縦横比から）
CRF = 12


def schema():
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


# ---- 計画 ------------------------------------------------------------------------

def load_plan(path):
    """計画を読み、検証して既定値を埋めた (plan, 計画のディレクトリ) を返す。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"下ごしらえの計画が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"下ごしらえの計画が JSON として読めません ({path.name}): {error}") from error
    return parse_plan(raw, path.resolve().parent), path.resolve().parent


def parse_plan(raw, base_dir):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"下ごしらえの計画の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("下ごしらえの計画が正しくありません:\n" + "\n".join(lines))
    plan = {"schema_version": SCHEMA_VERSION, "crf": int(raw.get("crf", CRF)), "clips": []}
    seen = set()
    for clip in raw["clips"]:
        if clip["id"] in seen:
            raise PipelineError(f"下ごしらえの id が重複しています: {clip['id']!r}")
        seen.add(clip["id"])
        stabilize, color = clip.get("stabilize"), clip.get("color")
        if stabilize is None and color is None:
            raise PipelineError(f"クリップ {clip['id']!r} は stabilize も color もありません（何もしないなら下ごしらえは要りません）")
        if stabilize is not None:
            stabilize = {**STABILIZE, **{k: v for k, v in stabilize.items() if not k.startswith("x_")}}
            stabilize["smooth_frames"] = float(stabilize["smooth_frames"])
        if color is not None:
            color = {**COLOR, **{k: v for k, v in color.items() if not k.startswith("x_")}}
            low, high = (float(v) for v in color["gain_range"])
            if not low < high:
                raise PipelineError(f"クリップ {clip['id']!r} の color.gain_range は [小, 大] にしてください: {color['gain_range']}")
            color.update(gain_range=[low, high], min_improvement=float(color["min_improvement"]))
        plan["clips"].append({"id": clip["id"], "source": clip["source"], "out": clip["out"], "measure_from": clip.get("measure_from"),
                              "stabilize": stabilize, "color": color})
        if clip.get("measure_from") is not None and stabilize is None:
            raise PipelineError(f"クリップ {clip['id']!r} の measure_from は stabilize と一緒に使います")
    base_dir = Path(base_dir)
    for clip in plan["clips"]:
        for rel in _inputs(clip):
            safe_path(base_dir, rel)
    return plan


def _inputs(clip):
    """クリップが読むファイル（計画のディレクトリからの相対パス）。"""
    refs = [clip["source"]]
    if clip["measure_from"]:
        refs.append(clip["measure_from"])
    if clip["stabilize"] and clip["stabilize"]["mask"]:
        refs.append(clip["stabilize"]["mask"])
    if clip["color"]:
        refs.append(clip["color"]["reference"])
    return refs


def output_targets(plan, out_dir, plan_path, base_dir):
    """クリップごとの書き出し先。何も書く前に、入力（計画・source・measure_from・mask・reference）や記録と同じファイル、
    ほかのクリップと同じ書き出し先を止める（綴りが違っても同じファイルなら同じ）。"""
    def key(path):
        return os.path.normcase(str(Path(path).resolve()))

    inputs = {key(plan_path): "計画"}
    for clip in plan["clips"]:
        for rel in _inputs(clip):
            inputs.setdefault(key(safe_path(base_dir, rel)), rel)
    record = key(Path(out_dir) / RECORD_NAME)
    if record in inputs:
        raise PipelineError(f"{RECORD_NAME} を書くと、入力（{inputs[record]}）を上書きします。--out で別のフォルダーに書いてください")
    targets, seen = {}, {}
    for clip in plan["clips"]:
        target = safe_path(out_dir, clip["out"])
        if key(target) == record:
            raise PipelineError(f"クリップ {clip['id']!r} の書き出し先は記録（{RECORD_NAME}）と同じです。別の名前にしてください")
        if key(target) in inputs:
            raise PipelineError(f"クリップ {clip['id']!r} の書き出し先 {clip['out']!r} は、入力（{inputs[key(target)]}）と同じファイルです")
        if key(target) in seen:
            raise PipelineError(f"クリップ {clip['id']!r} と {seen[key(target)]!r} の書き出し先が同じファイルです: {clip['out']!r}")
        seen[key(target)] = clip["id"]
        targets[clip["id"]] = target
    return targets


# ---- 演算（実制作の台本と同じ OpenCV の呼び方） ------------------------------------------

def measure_size(width, height, measure_width=STABILIZE["measure_width"]):
    """測る大きさ (幅, 高さ)。1280×720 と 1920×1080 は (640, 360)。"""
    return int(measure_width), max(1, int(round(measure_width * height / width)))


def gray_small(frame, size):
    """測る大きさに縮めた灰色（float32、σ MEASURE_BLUR でぼかす）。"""
    import cv2
    gray = cv2.cvtColor(cv2.resize(frame, size, interpolation=cv2.INTER_AREA), cv2.COLOR_RGB2GRAY).astype(np.float32)
    return cv2.GaussianBlur(gray, (0, 0), MEASURE_BLUR)


def read_gray(path):
    """8 bit の灰色の画像（日本語のパスも読めるよう、バイト列から decode する）。"""
    import cv2
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise PipelineError(f"画像として読めません: {Path(path).name}")
    return image


def read_rgb(path):
    import cv2
    image = cv2.imdecode(np.fromfile(str(path), np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PipelineError(f"画像として読めません: {Path(path).name}")
    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def background_mask(mask, size, dilate_px):
    """mask（白 = 人物など動くもの）から、測る背景の 8 bit マスク（255 = 使う）を作る。"""
    import cv2
    inside = cv2.resize(mask, size, interpolation=cv2.INTER_NEAREST) > 127
    grown = inside.astype(np.uint8)
    if dilate_px > 1:
        grown = cv2.dilate(grown, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_px, dilate_px)))
    return np.where(grown > 0, 0, 255).astype(np.uint8)


def measure(grays, background=None):
    """warps[i]: 1 コマ目の座標 → i コマ目の座標（測る大きさの単位、ユークリッド）。grays は 1 コマ目から順の灰色。
    (warps (n, 2, 3) float32, 収束しなかったコマの番号の list)。"""
    import cv2
    grays = iter(grays)
    template = next(grays)
    warp = np.eye(2, 3, dtype=np.float32)
    warps, failed = [warp.copy()], []
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, ECC_ITERATIONS, ECC_EPS)
    for index, gray in enumerate(grays, start=1):
        try:
            _, warp = cv2.findTransformECC(template, gray, warp.copy(), cv2.MOTION_EUCLIDEAN, criteria, background, ECC_GAUSS)
        except cv2.error:
            failed.append(index)                 # 前のコマの値のまま
        warps.append(warp.copy())
    return np.array(warps), failed


def params(warps):
    """(移動 x, 移動 y, 回転（ラジアン）)。どれも float64 の配列。"""
    theta = np.arctan2(warps[:, 1, 0], warps[:, 0, 0])
    return warps[:, 0, 2].astype(np.float64), warps[:, 1, 2].astype(np.float64), theta.astype(np.float64)


def gaussian_smooth(values, sigma, truncate=4.0):
    """scipy.ndimage.gaussian_filter1d(values, sigma, mode="nearest") と同じ計算（重みの作り方と、左右を足してから重みを掛ける順）。"""
    x = np.asarray(values, dtype=np.float64)
    if sigma <= 0 or len(x) == 0:
        return x.copy()
    radius = int(truncate * float(sigma) + 0.5)
    offsets = np.arange(-radius, radius + 1)
    phi = np.exp(-0.5 / (sigma * sigma) * offsets ** 2)
    weights = (phi / phi.sum())[::-1]
    n = len(x)
    padded = np.concatenate([np.full(radius, x[0]), x, np.full(radius, x[-1])])
    out = padded[radius:radius + n] * weights[radius]
    for j in range(radius, 0, -1):
        out += (padded[radius - j:radius - j + n] + padded[radius + j:radius + j + n]) * weights[radius - j]
    return out


def smooth(tx, ty, theta, sigma):
    """ならして、1 コマ目の値を引く（1 コマ目は生成したまま）。"""
    out = [gaussian_smooth(values, sigma) for values in (tx, ty, theta)]
    return [values - values[0] for values in out]


def matrix(tx, ty, theta, scale):
    """出力の座標 → 元のコマの座標（WARP_INVERSE_MAP で掛ける 2×3）。移動は scale 倍（出力の幅 / 測る幅）。"""
    c, s = np.cos(theta), np.sin(theta)
    return np.float32([[c, -s, tx * scale], [s, c, ty * scale]])


def inner_rect(m, width, height):
    """出力の座標で、元の絵が届いている軸に平行な内側の矩形 [x0, y0, x1, y1]。dst(x) = src(M x) なので、元の絵の四隅を
    M の逆で戻した平行四辺形の内側をとる。"""
    a, t = m[:, :2].astype(np.float64), m[:, 2].astype(np.float64)
    inverse = np.linalg.inv(a)
    corners = np.array([[0, 0], [width, 0], [width, height], [0, height]], np.float64)
    p = (inverse @ (corners - t).T).T
    x0 = max(p[0, 0], p[3, 0], 0.0)
    x1 = min(p[1, 0], p[2, 0], float(width))
    y0 = max(p[0, 1], p[1, 1], 0.0)
    y1 = min(p[2, 1], p[3, 1], float(height))
    return [round(float(x0), 2), round(float(y0), 2), round(float(x1), 2), round(float(y1), 2)]


def stabilized(frame, index, m):
    """index 番目のコマに揺れ止めを掛ける（1 コマ目はそのまま）。"""
    import cv2
    if index == 0:
        return frame
    height, width = frame.shape[:2]
    return cv2.warpAffine(frame, m, (width, height), flags=cv2.INTER_CUBIC | cv2.WARP_INVERSE_MAP, borderMode=cv2.BORDER_REPLICATE)


def color_fit(first, reference, gain_range=COLOR["gain_range"], min_improvement=COLOR["min_improvement"]):
    """1 コマ目を reference に合わせる色ごとの (倍率, 足し算)。(fit, 合わせる前の平均の差, 合わせた後の平均の差, 使うか)。"""
    import cv2
    size = (COLOR_WIDTH, max(1, int(round(COLOR_WIDTH * first.shape[0] / first.shape[1]))))
    s = cv2.resize(reference, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    v = cv2.resize(first, size, interpolation=cv2.INTER_AREA).astype(np.float32)
    fit = []
    for c in range(3):
        a = np.stack([v[..., c].ravel(), np.ones(v[..., c].size, np.float32)], 1)
        g, o = np.linalg.lstsq(a, s[..., c].ravel(), rcond=None)[0]
        fit.append((float(g), float(o)))
    fitted = np.stack([v[..., c] * fit[c][0] + fit[c][1] for c in range(3)], -1)
    before, after = float(np.abs(s - v).mean()), float(np.abs(s - fitted).mean())
    low, high = gain_range
    use = after < before - min_improvement and all(low <= g <= high for g, _ in fit)
    return fit, round(before, 2), round(after, 2), use


def color_lut(fit):
    """(256, 3) の表。0..255 に切って切り捨てる。"""
    return np.stack([np.clip(np.arange(256) * g + o + 0.0, 0, 255) for g, o in fit], 1).astype(np.uint8)


def apply_lut(frame, lut):
    return np.stack([lut[..., c][frame[..., c]] for c in range(3)], -1)


# ---- 1 本の下ごしらえ ---------------------------------------------------------------------

def _reader(path, role):
    from ..dance.clips import Source
    try:
        source = Source(path)
        count = source.count()
    except PipelineError as error:
        raise PipelineError(f"{role} の動画を読めません（{Path(path).name}）: {error}") from error
    return source, count


def _frames(source, count):
    for index in range(count):
        yield index, source.read(index)


def _file(path, rel):
    return {"path": rel, "sha256": sha256(path)}


def prepare_clip(clip, base_dir, target, crf, log_path=None):
    """1 本を下ごしらえして target に書き、記録の 1 項目を返す。"""
    from . import render

    resolve = lambda rel: safe_path(base_dir, rel)  # noqa: E731
    source, count = _reader(resolve(clip["source"]), "source")
    warnings = []
    try:
        width, height, fps = source.width, source.height, source.fps
        record = {"id": clip["id"], "source": {**_file(resolve(clip["source"]), clip["source"]), "frames": count, "fps": fps,
                                               "size": [width, height]},
                  "measure_from": None, "stabilize": None, "color": None}
        matrices = None
        if clip["stabilize"]:
            options = clip["stabilize"]
            measured, measured_count = (source, count) if not clip["measure_from"] else _reader(resolve(clip["measure_from"]), "measure_from")
            try:
                if measured_count != count:
                    raise PipelineError(f"クリップ {clip['id']!r} の measure_from のコマ数 {measured_count} が source の {count} と違います")
                size = measure_size(measured.width, measured.height, options["measure_width"])
                background = None
                if options["mask"]:
                    background = background_mask(read_gray(resolve(options["mask"])), size, int(options["dilate_px"]))
                warps, failed = measure((gray_small(frame, size) for _, frame in _frames(measured, count)), background)
            finally:
                if measured is not source:
                    measured.close()
            if clip["measure_from"]:
                record["measure_from"] = _file(resolve(clip["measure_from"]), clip["measure_from"])
            tx, ty, theta = params(warps)
            sx, sy, sth = smooth(tx, ty, theta, options["smooth_frames"])
            scale = width / size[0]
            matrices = [matrix(sx[i], sy[i], sth[i], scale) for i in range(count)]
            valid = [inner_rect(m, width, height) for m in matrices]
            narrowest = min((r[2] - r[0]) / width for r in valid), min((r[3] - r[1]) / height for r in valid)
            record["stabilize"] = {
                "measure_size": list(size), "scale": scale, "mask": _file(resolve(options["mask"]), options["mask"]) if options["mask"] else None,
                "dilate_px": int(options["dilate_px"]), "smooth_frames": options["smooth_frames"], "ecc_failed": failed,
                "tx": [round(float(v), 3) for v in sx], "ty": [round(float(v), 3) for v in sy], "th_deg": [round(float(np.degrees(v)), 4) for v in sth],
                "raw_tx": [round(float(v), 3) for v in tx], "raw_ty": [round(float(v), 3) for v in ty],
                "raw_th_deg": [round(float(np.degrees(v)), 4) for v in theta], "valid": valid,
                "end_px": {"x": round(float(sx[-1] * scale), 2), "y": round(float(sy[-1] * scale), 2), "th_deg": round(float(np.degrees(sth[-1])), 4)},
                "narrowest_valid": [round(float(narrowest[0]), 4), round(float(narrowest[1]), 4)]}
            if failed:
                warnings.append(f"{clip['id']}: ECC が {len(failed)} コマで収束せず、前のコマの動きのままにしました（コマ {failed[:8]}）")
            if min(narrowest) < 0.9:
                warnings.append(f"{clip['id']}: 揺れ止めの後、絵の届く内側の矩形が最も狭いコマで 幅 {narrowest[0] * 100:.1f}%・高さ {narrowest[1] * 100:.1f}% です"
                                "（外側は端の画素を伸ばしています。寄りで隠すか、smooth_frames を上げてください）")
        lut = None
        if clip["color"]:
            options = clip["color"]
            reference = read_rgb(resolve(options["reference"]))
            fit, before, after, use = color_fit(source.read(0), reference, options["gain_range"], options["min_improvement"])
            reason = "使った" if use else ("倍率が gain_range の外" if after < before - options["min_improvement"] else
                                         f"平均の差が {options['min_improvement']:g} より縮まない")
            record["color"] = {"reference": _file(resolve(options["reference"]), options["reference"]),
                               "fit": [[round(g, 4), round(o, 2)] for g, o in fit], "mae_before": before, "mae_after": after, "used": use,
                               "reason": reason, "gain_range": options["gain_range"], "min_improvement": options["min_improvement"]}
            lut = color_lut(fit) if use else None

        def frames():
            for index, frame in _frames(source, count):
                if matrices is not None:
                    frame = stabilized(frame, index, matrices[index])
                if lut is not None:
                    frame = apply_lut(frame, lut)
                yield np.ascontiguousarray(frame)

        target.parent.mkdir(parents=True, exist_ok=True)
        render.write_video(target, frames(), (width, height), fps, crf, count, None, log_path)
    finally:
        source.close()
    record["out"] = {"path": str(target), "sha256": sha256(target), "frames": count, "crf": crf}
    return record, warnings


def _clip_key(clip, base_dir, crf, libraries):
    """クリップの出力を決める入力すべての sha256（正規化した計画の項目・読むファイル・fx の指紋・ライブラリ版）。"""
    files = {rel: sha256(safe_path(base_dir, rel)) for rel in _inputs(clip)}
    payload = {"clip": clip, "crf": crf, "files": files, "engine": fingerprint(), "libraries": libraries}
    return hashlib.sha256(fxspec.canonical_json(payload).encode("utf-8")).hexdigest()


def make_clips(plan_path, out=None, force=False, only=None, progress=None):
    """`mv fx clips`: 計画のクリップを下ごしらえして書き、記録（clips_record.json）を残す。結果の要約を返す。

    同じ入力の出力が記録どおりに残っていれば作り直さない。記録に無いか中身の違う既存の出力は、force が無ければ上書きしない。"""
    from . import render

    say = progress or (lambda message: None)
    plan, base_dir = load_plan(plan_path)
    out_dir = Path(out).resolve() if out else base_dir
    targets = output_targets(plan, out_dir, plan_path, base_dir)
    chosen = [clip for clip in plan["clips"] if not only or clip["id"] in only]
    unknown = sorted(set(only or ()) - {clip["id"] for clip in plan["clips"]})
    if unknown:
        raise PipelineError(f"計画に無いクリップの id です: {unknown}")
    for clip in chosen:
        for rel in _inputs(clip):
            if not safe_path(base_dir, rel).is_file():
                raise PipelineError(f"クリップ {clip['id']!r} の入力が見つかりません: {rel}")
    record_path = out_dir / RECORD_NAME
    previous = read_json(record_path) if record_path.is_file() else {}
    previous_clips = previous.get("clips", {}) if isinstance(previous, dict) else {}
    libraries = render.library_versions()
    out_dir.mkdir(parents=True, exist_ok=True)
    ids = {clip["id"] for clip in plan["clips"]}
    results = {key: value for key, value in previous_clips.items() if key in ids}     # 計画から消えたクリップの記録は残さない
    warnings, written = [], []
    for clip in chosen:
        target = targets[clip["id"]]
        key = _clip_key(clip, base_dir, plan["crf"], libraries)
        old = previous_clips.get(clip["id"])
        recorded = isinstance(old, dict) and target.is_file() and old.get("out", {}).get("sha256") == sha256(target)
        if recorded and old.get("key") == key:
            say(f"[fx clips] {clip['id']}: 同じ入力の出力があるので作り直しません")
            continue
        if target.is_file() and not recorded and not force:
            raise PipelineError(f"{target.name} が既にあり、記録に無いか中身が違います（手で直したクリップかもしれません）。"
                                "上書きするなら --force を付けてください")
        say(f"[fx clips] {clip['id']}: {clip['source']} → {clip['out']}")
        record, notes = prepare_clip(clip, base_dir, target, plan["crf"], log_path=out_dir / "clips.log")
        record["key"] = key
        results[clip["id"]] = record
        warnings += notes
        written.append(clip["id"])
        atomic_json(record_path, _record(plan_path, plan, results, warnings, libraries))
    atomic_json(record_path, _record(plan_path, plan, results, warnings, libraries))
    return {"clips": {k: v["out"]["path"] for k, v in results.items() if k in {c["id"] for c in chosen}}, "written": written,
            "record": str(record_path), "warnings": warnings}


def _record(plan_path, plan, clips, warnings, libraries):
    return {"schema_version": RECORD_VERSION, "created_at": now(),
            "plan": {"path": str(Path(plan_path).resolve()), "sha256": sha256(plan_path), "normalized": plan},
            "clips": clips, "warnings": warnings,
            "engine": {"version": VERSION, "fingerprint": fingerprint(), "libraries": libraries,
                       "ecc": {"iterations": ECC_ITERATIONS, "eps": ECC_EPS, "gauss": ECC_GAUSS, "blur": MEASURE_BLUR},
                       "color_width": COLOR_WIDTH}}
