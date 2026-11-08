"""人物の輪郭から縮尺・左右・足元を求める（performer.fit）。

これは**人物を置くための合成の入力**で、評価にも technical_pass にも使わない（`mv gate` の外にある理由）。

クリップを 1 回だけ読み、毎フレームの次の値を元動画の画素で求める（解析は analysis_scale の縮小で行う）:

    height  アルファ 0.5 超の画素の上端から下端までの高さ
    cx      アルファの中心 x
    foot    足元（アルファ 0.5 超の画素が 3 個以上ある最下行の下端）

結果は `<out>/analysis/<clip の sha256>-<key と fit の設定の sha256>.json` にキャッシュする。名前も中身も内容だけで決まる。

足元の固定（ground: lock）は、窓の中の最大値（低い方の足 = 接地している足）を取り、続けて同じ窓の最小値を取って
（最大値だけだと足元の流れより窓の半分だけ先走るので、それを打ち消す）、最後にならす。窓より短いジャンプは残り、窓より
長いジャンプは接地として扱われて跳んだ分が消える。
"""
import hashlib
import json

import cv2
import numpy as np

from ..fx.spec import canonical_json
from ..io import PipelineError, atomic_json, read_json, sha256
from . import key as dancekey

ANALYSIS_VERSION = 2
FOOT_PIXELS = 3     # 足元とみなす行に要るアルファ 0.5 超の画素数


def settings_digest(key, fit, skip=()):
    """解析の結果を左右する設定の sha256。skip（replace_frames で差し替えるコマの区間）は、あるときだけ入れる（無ければ 0.2 と同じ値）。"""
    payload = {"version": ANALYSIS_VERSION, "key": key, "fit": fit}
    if skip:
        payload["skip"] = [list(run) for run in skip]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def cache_path(out, clip_sha256, digest):
    return out / "analysis" / f"{clip_sha256}-{digest}.json"


def _measure(image, key, scale):
    """1 フレームの (高さ, 中心 x, 足元)（元動画の画素）。人物が写っていなければ None の組。"""
    small = image if scale == 1.0 else cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    solid = dancekey.alpha_of(small, key) > 0.5
    rows = solid.sum(axis=1)
    filled = np.nonzero(rows)[0]
    if not len(filled):
        return None, None, None
    columns = solid.sum(axis=0)
    cx = float((columns * (np.arange(len(columns)) + 0.5)).sum() / columns.sum())
    feet = np.nonzero(rows >= FOOT_PIXELS)[0]
    foot = int(feet[-1] if len(feet) else filled[-1]) + 1
    return (int(filled[-1]) + 1 - int(filled[0])) / scale, cx / scale, foot / scale


def _fill(values):
    """None を前後の値で埋めた float64 の配列（人物の写っていないフレームで足元が跳ばないように）。"""
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        raise PipelineError("fit: 人物がどのフレームにも写っていません（key の設定か素材を確認してください）")
    return np.interp(np.arange(len(values)), known, [values[i] for i in known])


def _slope(part):
    """part の傾き（1 コマあたり）。半分離れた点どうしの差の中央値なので、丸めの誤差や途中のジャンプに引きずられない。"""
    lag = max(1, len(part) // 2)
    return float(np.median(part[lag:] - part[:len(part) - lag])) / lag


def _pad(series, radius):
    """端の傾きのまま radius コマ延ばす。窓の処理が端で値を引きずらないように。"""
    if len(series) < 2:
        return np.full(len(series) + 2 * radius, series[0])
    span = 2 * radius + 1
    head = series[0] + _slope(series[:span]) * np.arange(-radius, 0)
    tail = series[-1] + _slope(series[-span:]) * np.arange(1, radius + 1)
    return np.concatenate([head, series, tail])


def _rolling(series, radius, pick):
    size = 2 * radius + 1
    windows = np.lib.stride_tricks.sliding_window_view(_pad(series, radius), size)
    return pick(windows, axis=1)


def smooth(series, seconds, fps):
    """中心の窓の平均。"""
    radius = int(round(seconds * fps / 2))
    return series if radius <= 0 else _rolling(series, radius, np.mean)


def lock_ground(foot, window_s, smooth_s, fps):
    """毎フレームの足元の列から、接地している足の位置の列を作る。"""
    radius = int(round(window_s * fps / 2))
    grounded = foot if radius <= 0 else _rolling(_rolling(foot, radius, np.max), radius, np.min)
    return smooth(grounded, smooth_s, fps)


def analyze(source, key, fit, skip=()):
    """Source を先頭から読んで解析の結果（JSON に書ける辞書）を返す。skip の区間 [a, b) のコマは欠け（前後の値で埋める）として扱う。"""
    heights, centers, feet, index = [], [], [], 0
    scale = fit["analysis_scale"]
    while True:
        image = source.read(index)
        if image is None or source.ended_at(index):
            break
        if any(a <= index < b for a, b in skip):
            heights.append(None)
            centers.append(None)
            feet.append(None)
            index += 1
            continue
        height, cx, foot = _measure(image, key, scale)
        heights.append(height)
        centers.append(cx)
        feet.append(foot)
        index += 1
    fps = source.fps or 1.0
    foot = _fill(feet)
    cx = _fill(centers)
    result = {"schema_version": "dance-analysis/0.1", "frames": index, "fps": fps, "width": source.width, "height": source.height,
              "p90_height": float(np.percentile(_fill(heights), 90)), "median_cx": float(np.median(cx)),
              "median_foot": float(np.median(foot)),
              "cx": [round(float(v), 3) for v in smooth(cx, fit["x_smooth_s"], fps)],
              "ground": [round(float(v), 3) for v in lock_ground(foot, fit["ground_window_s"], fit["ground_smooth_s"], fps)]}
    if skip:
        result["skipped"] = [list(run) for run in skip]     # 差し替えたので測らなかったコマ（無いときは 0.2 と同じ中身）
    return result


def load(source, clip_sha256, key, fit, out, skip=()):
    """(解析の結果, キャッシュのパス)。無ければ解析して書く。"""
    path = cache_path(out, clip_sha256, settings_digest(key, fit, skip))
    if path.is_file():
        try:
            return read_json(path), path
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass    # 壊れたキャッシュは作り直す
    result = analyze(source, key, fit, skip)
    atomic_json(path, result)
    return result, path


# ---- 足元と中心の列（performer.rows。hooks と層の API に渡す。置き場所には使わない） ----------------------------------

ROWS_VERSION = 1


def measure_rows(source, key, scale, skip=()):
    """毎フレームの最下の画素の行と、x の中央値（元動画の画素。写っていなければ None）。

    アルファ 0.5 超の画素を、scale の縮小（cv2.resize の既定の補間）で数える。最下の行は画素の行の番号（下端ではない）、
    x は画素の列の番号の中央値で、どちらも ÷ scale で元動画の画素に戻す（非公開の制作と同じ定義）。skip の区間 [a, b)
    のコマは欠けとして扱う（replace_frames で差し替えるコマ）。"""
    bottoms, centers, index = [], [], 0
    while True:
        image = source.read(index)
        if image is None or source.ended_at(index):
            break
        bottom = center = None
        if not any(a <= index < b for a, b in skip):
            small = image if scale == 1.0 else cv2.resize(image, None, fx=scale, fy=scale)
            ys, xs = np.nonzero(dancekey.alpha_of(small, key) > 0.5)
            if len(ys):
                bottom, center = float(ys.max()) / scale, float(np.median(xs)) / scale
        bottoms.append(bottom)
        centers.append(center)
        index += 1
    return {"schema_version": "dance-rows/0.1", "frames": index, "scale": scale, "bottom": bottoms, "cx": centers}


def rows_digest(key, scale, skip=()):
    payload = {"version": ROWS_VERSION, "key": key, "scale": scale}
    if skip:
        payload["skip"] = [list(run) for run in skip]
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def load_rows(source, clip_sha256, key, scale, out, skip=()):
    """(毎フレームの測定, キャッシュのパス)。無ければ測って書く。

    名前は sha256 の頭 16 桁ずつにする（Windows のパスの長さの上限に掛かりにくく）。全桁は中に書き、読むときに照合する。"""
    digest = rows_digest(key, scale, skip)
    path = out / "analysis" / f"{clip_sha256[:16]}-rows-{digest[:16]}.json"
    if path.is_file():
        try:
            cached = read_json(path)
            if cached.get("clip_sha256") == clip_sha256 and cached.get("settings_sha256") == digest:
                return cached, path
        except (OSError, json.JSONDecodeError, UnicodeDecodeError, AttributeError):
            pass    # 壊れたキャッシュや別の内容は作り直す
    result = {**measure_rows(source, key, scale, skip), "clip_sha256": clip_sha256, "settings_sha256": digest}
    atomic_json(path, result)
    return result, path


def hold_ground(bottom, used, hold_frames, box_frames):
    """毎フレームの最下の行から足元の列を作る（非公開の制作と同じ式）。

    写っていないコマは前後の値で埋め、使う範囲 used = [i0, i1] の外は端の値で止める（出ないコマに列を引かせない）。
    前後 hold_frames コマ（端では窓が短くなる）の最大で接地している足の位置を取り、端の値で延ばして box_frames コマの平均でならす。"""
    b = _fill(bottom)
    i0, i1 = used
    b[:i0] = b[i0]
    b[i1 + 1:] = b[i1]
    k = int(hold_frames)
    g = np.array([b[max(0, i - k):i + k + 1].max() for i in range(len(b))])
    pad = int(box_frames) // 2
    return np.convolve(np.pad(g, pad, mode="edge"), np.ones(int(box_frames)) / int(box_frames), mode="valid")


def rows_for(measured, used, settings):
    """測定と使う範囲から hooks に渡す列: bottom・cx（毎フレーム。写っていないコマは前後の値で埋める）・ground（足元の列）・cx_med。

    cx_med は使う範囲の写ったコマの cx の中央値（非公開の制作と同じ）。使う範囲に写ったコマが 1 つも無い（全部を replace_frames の
    画像で差し替えた）ときは、前後の値で埋めた cx の中央値にする（NaN を hooks に渡さない）。"""
    i0, i1 = used
    centers = np.array([np.nan if v is None else v for v in measured["cx"]], dtype=np.float64)
    filled = _fill(measured["cx"])
    window = centers[i0:i1 + 1]
    cx_med = float(np.median(filled[i0:i1 + 1])) if np.isnan(window).all() else float(np.nanmedian(window))
    return {"frames": measured["frames"], "used": [i0, i1], "bottom": _fill(measured["bottom"]), "cx": filled,
            "ground": hold_ground(measured["bottom"], used, settings["hold_frames"], settings["box_frames"]), "cx_med": cx_med}


def record(clip, result, path):
    """timeline と manifest に残す 1 クリップ分の記録。"""
    return {"path": clip, "sha256": sha256(path), "p90_height": result["p90_height"], "median_cx": result["median_cx"],
            "ground": result["median_foot"], "file": f"analysis/{path.name}"}
