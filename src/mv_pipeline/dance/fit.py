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

ANALYSIS_VERSION = 1
FOOT_PIXELS = 3     # 足元とみなす行に要るアルファ 0.5 超の画素数


def settings_digest(key, fit):
    """解析の結果を左右する設定の sha256。"""
    return hashlib.sha256(canonical_json({"version": ANALYSIS_VERSION, "key": key, "fit": fit}).encode("utf-8")).hexdigest()


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


def analyze(source, key, fit):
    """Source を先頭から読んで解析の結果（JSON に書ける辞書）を返す。"""
    heights, centers, feet, index = [], [], [], 0
    scale = fit["analysis_scale"]
    while True:
        image = source.read(index)
        if image is None or source.ended_at(index):
            break
        height, cx, foot = _measure(image, key, scale)
        heights.append(height)
        centers.append(cx)
        feet.append(foot)
        index += 1
    fps = source.fps or 1.0
    foot = _fill(feet)
    cx = _fill(centers)
    return {"schema_version": "dance-analysis/0.1", "frames": index, "fps": fps, "width": source.width, "height": source.height,
            "p90_height": float(np.percentile(_fill(heights), 90)), "median_cx": float(np.median(cx)),
            "median_foot": float(np.median(foot)),
            "cx": [round(float(v), 3) for v in smooth(cx, fit["x_smooth_s"], fps)],
            "ground": [round(float(v), 3) for v in lock_ground(foot, fit["ground_window_s"], fit["ground_smooth_s"], fps)]}


def load(source, clip_sha256, key, fit, out):
    """(解析の結果, キャッシュのパス)。無ければ解析して書く。"""
    path = cache_path(out, clip_sha256, settings_digest(key, fit))
    if path.is_file():
        try:
            return read_json(path), path
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            pass    # 壊れたキャッシュは作り直す
    result = analyze(source, key, fit)
    atomic_json(path, result)
    return result, path


def record(clip, result, path):
    """timeline と manifest に残す 1 クリップ分の記録。"""
    return {"path": clip, "sha256": sha256(path), "p90_height": result["p90_height"], "median_cx": result["median_cx"],
            "ground": result["median_foot"], "file": f"analysis/{path.name}"}
