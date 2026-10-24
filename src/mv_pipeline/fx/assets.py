"""素材（基準画像・深度・マスク）の読み込みと前処理。描画サイズごとに 1 回だけ行う。

- 基準画像: 出力の縦横比に cover で中央合わせし（余る側を切る）、描画サイズへ縮尺して線形 float32 に。
  切った割合は warnings に残す（黙って切らない）。切り出し範囲は Assets.box（元画像の正規化座標）。
  spec の座標（light.position・radius・emitter.size・camera.center）は**元画像**の正規化座標で、
  to_screen() / width_scale() が box を使って出力画素へ写す（出力の縦横比が変わっても対象に付いてくる）。
- マスク: 輝度（RGBA で alpha が一定でなければ alpha）を 0..1 にし、feather_px でぼかす
  （σ = feather_px/2、出力幅 1920 換算）。基準画像と同じ縦横比（誤差 1% 以内）であること。
- 深度: 8/16bit/float/RGB を 2〜98 パーセンタイルで 0..1 に正規化（外れ値で潰れない）。
  結果は 1 = 手前、0 = 奥（near: white|black で向きを揃える）。
"""
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..io import PipelineError, sha256
from . import spec as fxspec
from .color import to_linear

CROP_WARN_FRACTION = 0.005


@dataclass
class Assets:
    size: tuple                      # (W, H) 描画サイズ
    plate_u8: np.ndarray             # uint8 (H, W, 3) sRGB（inpaint 用に保持）
    plate: np.ndarray                # float32 (H, W, 3) 線形
    masks: dict                      # id -> float32 (H, W) 0..1（ぼかし済み）
    depth: np.ndarray | None         # float32 (H, W) 0..1（1 = 手前）
    warnings: list = field(default_factory=list)
    box: tuple = (0.0, 0.0, 1.0, 1.0)   # cover の切り出し (x0, y0, x1, y1)。元画像の正規化座標


def _open(path, role):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"素材が見つかりません ({role}): {path.name}。spec のあるディレクトリからの相対パスを確認してください")
    try:
        image = Image.open(path)
        image.load()
        return image
    except Exception as error:  # Pillow は形式ごとに別の例外を投げる
        raise PipelineError(f"素材を画像として読めません ({role}: {path.name}): {error}") from error


def cover_box(src_w, src_h, out_w, out_h):
    """cover で中央合わせする切り出し (x0, y0, x1, y1) を元画像の正規化座標 0..1 で返す。"""
    target = out_w / out_h
    if src_w / src_h > target:  # 横が余る
        keep = (src_h * target) / src_w
        return ((1 - keep) / 2, 0.0, (1 + keep) / 2, 1.0)
    keep = (src_w / target) / src_h
    return (0.0, (1 - keep) / 2, 1.0, (1 + keep) / 2)


def _pixel_box(box, size):
    """box を基準画像の画素格子に丸める（実際に切り出す範囲と一致させ、座標変換の誤差を 1 画素未満にする）。"""
    width, height = size
    return (round(box[0] * width) / width, round(box[1] * height) / height,
            round(box[2] * width) / width, round(box[3] * height) / height)


def to_screen(box, u, v):
    """元画像の正規化座標 (u, v) → cover で切った後の画面の正規化座標（0..1 が画面の端）。"""
    return (u - box[0]) / (box[2] - box[0]), (v - box[1]) / (box[3] - box[1])


def width_scale(box):
    """元画像の幅 1.0 が、画面の幅の何倍か（= 1 / 切り出し幅）。半径・size など元画像の幅基準の長さに掛ける。"""
    return 1.0 / (box[2] - box[0])


def _crop_resize(arr, box, size):
    height, width = arr.shape[:2]
    x0, x1 = int(round(box[0] * width)), int(round(box[2] * width))
    y0, y1 = int(round(box[1] * height)), int(round(box[3] * height))
    arr = arr[y0:y1, x0:x1]
    out_w, out_h = size
    if arr.shape[1] == out_w and arr.shape[0] == out_h:
        return np.ascontiguousarray(arr)
    shrinking = arr.shape[1] > out_w or arr.shape[0] > out_h
    return cv2.resize(arr, (out_w, out_h), interpolation=cv2.INTER_AREA if shrinking else cv2.INTER_CUBIC)


def _gray01(image):
    """マスク画像 → float32 0..1。RGBA は alpha が一定でなければ alpha、それ以外は輝度。"""
    if image.mode in ("RGBA", "LA", "PA"):
        alpha = np.asarray(image.convert("RGBA"))[..., 3]
        if alpha.min() != alpha.max():
            return alpha.astype(np.float32) / 255.0
    if image.mode.startswith("I"):
        return np.clip(np.asarray(image, dtype=np.float32) / 65535.0, 0.0, 1.0)
    if image.mode == "F":
        return np.clip(np.asarray(image, dtype=np.float32), 0.0, 1.0)
    return np.asarray(image.convert("L")).astype(np.float32) / 255.0


def _raw_values(image):
    """深度画像 → float32（値域はそのまま。正規化は呼び出し側）。"""
    if image.mode.startswith("I") or image.mode == "F":
        return np.asarray(image, dtype=np.float32)
    return np.asarray(image.convert("L")).astype(np.float32)


def _check_aspect(role, image, base_size):
    bw, bh = base_size
    w, h = image.size
    if abs(w * bh - bw * h) > 0.01 * bw * h:
        raise PipelineError(f"{role} の縦横比が基準画像と違います（{w}x{h} と {bw}x{bh}）。同じ縦横比で作り直してください")


def normalize_depth(values, near):
    """深度を 2〜98 パーセンタイルで 0..1 にし、1 = 手前に揃える。平坦なら (0.5 一定, False) を返す。"""
    lo, hi = np.percentile(values, [2, 98])
    if hi - lo < 1e-6:
        return np.full(values.shape, 0.5, dtype=np.float32), False
    out = np.clip((values - lo) / (hi - lo), 0.0, 1.0).astype(np.float32)
    return (out if near == "white" else 1.0 - out).astype(np.float32), True


def load_assets(spec, size):
    """spec と描画サイズ (W, H) から Assets を作る。"""
    width, height = size
    kpx = width / 1920.0
    data = spec.data
    warnings = []
    base_image = _open(spec.resolve(data["assets"]["base"]), "base")
    base_size = base_image.size
    box = _pixel_box(cover_box(base_size[0], base_size[1], width, height), base_size)
    cut = 1.0 - (box[2] - box[0]) * (box[3] - box[1])
    if cut > CROP_WARN_FRACTION:
        axis = "左右" if box[0] > 0 else "上下"
        warnings.append(f"基準画像 {base_size[0]}x{base_size[1]} は出力 {width}x{height} と縦横比が違うため、"
                        f"cover で中央に合わせて{axis}を計 {cut * 100:.1f}% 切りました")
    if base_size[0] < width or base_size[1] < height:
        warnings.append(f"基準画像 {base_size[0]}x{base_size[1]} は描画サイズ {width}x{height} より小さく、拡大されます")
    rgb = np.asarray(base_image.convert("RGB"))
    plate_u8 = _crop_resize(rgb, box, size)
    masks = {}
    for mask_id, mask in data["assets"]["masks"].items():
        image = _open(spec.resolve(mask["path"]), f"mask:{mask_id}")
        _check_aspect(f"mask:{mask_id}", image, base_size)
        values = np.clip(_crop_resize(_gray01(image), box, size), 0.0, 1.0)
        sigma = mask["feather_px"] * kpx / 2.0
        if sigma >= 0.3:
            values = cv2.GaussianBlur(values, (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
        masks[mask_id] = np.ascontiguousarray(values, dtype=np.float32)
    depth = None
    if data["assets"]["depth"]:
        image = _open(spec.resolve(data["assets"]["depth"]["path"]), "depth")
        _check_aspect("depth", image, base_size)
        values = _crop_resize(_raw_values(image), box, size)
        depth, informative = normalize_depth(values, data["assets"]["depth"]["near"])
        if not informative:
            warnings.append("深度画像がほぼ一定のため、深度は使いません（0.5 一定）")
    return Assets(size=size, plate_u8=plate_u8, plate=to_linear(plate_u8), masks=masks, depth=depth, warnings=warnings,
                  box=box)


def describe(spec):
    """manifest 用: 全素材の {役割: {path, sha256, width, height}}（音声は寸法なし）。存在しなければ PipelineError。"""
    out = {}
    for role, rel in fxspec.asset_refs(spec.data).items():
        path = spec.resolve(rel)
        if not path.is_file():
            raise PipelineError(f"素材が見つかりません ({role}): {rel}")
        entry = {"path": rel, "sha256": sha256(path)}
        if role != "audio":
            with Image.open(path) as image:
                entry["width"], entry["height"] = image.size
        out[role] = entry
    return out
