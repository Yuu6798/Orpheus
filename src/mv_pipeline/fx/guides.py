"""目盛り（座標の格子）と、光・マスクの印。見るための道具で、合否も数の判定も出さない。

    image_grid(rgb, crop, units, step, width)   元画像に格子を重ねる（`mv fx guides`。光の位置や pivot の箱・regions の四隅を読む）
    frame_guides(ctx, t, image, kinds, step)    描いたコマに、元画像の正規化座標の格子（cover の切り出しと camera で写したもの）・
                                                光の位置と半径・マスクの輪郭を重ねる（`mv fx stills --guides`）

格子の色は、縦線と x の値が緑、横線と y の値が黄（実制作の格子の台本と同じ）。光は桃色、マスクの輪郭は水色。
image_grid の線の位置・字・縮尺は実制作の格子の台本と同じ式（cv2 の INTER_AREA で幅 width に縮尺してから描く）で、
同じ画像と範囲なら画素まで同じ絵になる。
"""
import math

import cv2
import numpy as np

from ..io import PipelineError, atomic_image, image_format
from .assets import to_screen, width_scale

X_COLOR = (0, 255, 0)          # RGB: 縦線と x の値（緑）
Y_COLOR = (255, 255, 0)        # RGB: 横線と y の値（黄）
LIGHT_COLOR = (255, 90, 220)   # RGB: 光の位置と半径（桃）
MASK_COLOR = (60, 220, 255)    # RGB: マスクの輪郭（水色）
KINDS = ("grid", "lights", "masks")
DEFAULT_KINDS = ("grid", "lights")
FONT = cv2.FONT_HERSHEY_SIMPLEX


def auto_step(span):
    """見えている範囲の幅（正規化座標）に合わせた格子の間隔。広ければ 0.05、狭ければ 0.02・0.01。"""
    return 0.05 if span > 0.5 else 0.01 if span <= 0.15 else 0.02


def _decimals(step):
    return max(2, math.ceil(-math.log10(step) - 1e-9)) if step < 1 else 0


def parse_kinds(value):
    """`--guides` の値（"grid,lights" など）→ KINDS の順に並べたタプル。空なら既定（grid と lights）。"""
    if value is None:
        return ()
    names = [n.strip() for n in str(value).split(",") if n.strip()]
    unknown = [n for n in names if n not in KINDS]
    if unknown:
        raise PipelineError(f"--guides は {', '.join(KINDS)} からカンマ区切りで選んでください（不明: {unknown}）")
    return tuple(k for k in KINDS if k in names) or DEFAULT_KINDS


def image_grid(rgb, crop=None, units="norm", step=None, width=1600):
    """元画像 rgb（uint8 H×W×3）の crop の範囲を幅 width に縮尺し、格子と値を描いた画像と、使った設定を返す。

    units "norm": crop と値は正規化座標（0..1）。step を省くと範囲の幅で 0.05 / 0.02 / 0.01。
    units "px":   crop と値は元画像の画素。step を省くと 100 画素。
    """
    if units not in ("norm", "px"):
        raise PipelineError(f"units は norm か px にしてください: {units!r}")
    height, full_width = rgb.shape[:2]
    scale_x, scale_y = (1.0, 1.0) if units == "norm" else (float(full_width), float(height))
    x0, y0, x1, y1 = (0.0, 0.0, scale_x, scale_y) if crop is None else (float(v) for v in crop)
    if not (0 <= x0 < x1 <= scale_x and 0 <= y0 < y1 <= scale_y):
        raise PipelineError(f"--crop は画像の中の [x0 y0 x1 y1]（x0 < x1、y0 < y1）にしてください: {crop}"
                            f"（{'正規化座標 0..1' if units == 'norm' else f'画素 0..{full_width}x{height}'}）")
    roi = rgb[int(y0 / scale_y * height):int(y1 / scale_y * height), int(x0 / scale_x * full_width):int(x1 / scale_x * full_width)]
    if roi.shape[0] < 1 or roi.shape[1] < 1:
        raise PipelineError(f"--crop の範囲が 1 画素より小さくなります: {crop}")
    factor = width / roi.shape[1]
    roi = np.ascontiguousarray(cv2.resize(roi, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA))
    out_h, out_w = roi.shape[:2]
    if step is None:
        step = auto_step((x1 - x0) / scale_x) if units == "norm" else 100.0
    if step <= 0:
        raise PipelineError(f"--step は正の数にしてください: {step}")
    digits = _decimals(step) if units == "norm" else 0
    stop = 1.0001 if units == "norm" else max(scale_x, scale_y) + step * 1e-4     # norm は実制作の台本と同じ値の並び
    for v in np.arange(0, stop, step):
        text = f"{v:.{digits}f}"
        if x0 <= v <= x1 and v <= scale_x:
            x = int((v - x0) / (x1 - x0) * out_w)
            cv2.line(roi, (x, 0), (x, out_h), X_COLOR, 1)
            cv2.putText(roi, text, (x + 2, 14), FONT, 0.4, X_COLOR, 1)
        if y0 <= v <= y1 and v <= scale_y:
            y = int((v - y0) / (y1 - y0) * out_h)
            cv2.line(roi, (0, y), (out_w, y), Y_COLOR, 1)
            cv2.putText(roi, text, (2, y - 2), FONT, 0.4, Y_COLOR, 1)
    return roi, {"units": units, "crop": [x0, y0, x1, y1], "step": float(step), "size": [out_w, out_h],
                 "source_size": [full_width, height]}


def load_viewable(path):
    """画像を表示用の uint8 RGB で読む。16 bit（深度など）は 257 で割り、float は 0..1 を 255 倍する。アルファは捨てる（cv2 で読むのと同じ）。"""
    from .assets import _open
    image = _open(path, "guides")
    if image.mode.startswith("I"):
        values = np.asarray(image, dtype=np.float64)
        gray = np.clip(values / 257.0 + 0.5, 0, 255).astype(np.uint8) if values.max() > 255 else np.clip(values, 0, 255).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)
    if image.mode == "F":
        gray = np.clip(np.asarray(image, dtype=np.float64) * 255.0 + 0.5, 0, 255).astype(np.uint8)
        return np.repeat(gray[..., None], 3, axis=2)
    return np.asarray(image.convert("RGB"))


def write_image_grid(image_path, out, crop=None, units="norm", step=None, width=1600):
    """`mv fx guides`: 元画像に格子を重ねた絵を out に書き、設定を返す。形式は out の拡張子（.png か、.jpg・.jpeg の品質 90）。"""
    from pathlib import Path
    if not 16 <= int(width) <= 8192:
        raise PipelineError(f"--width は 16〜8192 にしてください: {width}")
    out = Path(out).resolve()
    kind = image_format(out)                          # 知らない拡張子は、画像を読む前に止める（.jpg に PNG の中身を書かない）
    grid, info = image_grid(load_viewable(image_path), crop, units, step, int(width))
    atomic_image(out, grid)
    return {"path": str(out), "format": kind.lower(), "image": str(Path(image_path)), **info}


def _screen_mapper(ctx, t):
    """元画像の正規化座標 (u, v) → 時刻 t の出力画素 (x, y)（画素の中心が整数。camera の視差 1 の変換。深度視差は含めない）。"""
    width, height = ctx.size
    matrix = ctx.path.matrix(t, 1.0)
    zoom, tx, ty = float(matrix[0, 0]), float(matrix[0, 2]), float(matrix[1, 2])
    box = ctx.assets.box

    def forward(u, v):
        us, vs = to_screen(box, u, v)
        return zoom * (us * width - 0.5) + tx, zoom * (vs * height - 0.5) + ty

    def backward(x, y):
        us, vs = ((x - tx) / zoom + 0.5) / width, ((y - ty) / zoom + 0.5) / height
        return box[0] + us * (box[2] - box[0]), box[1] + vs * (box[3] - box[1])

    return forward, backward, matrix, zoom


def frame_guides(ctx, t, image, kinds=DEFAULT_KINDS, step=None):
    """描いたコマ image（uint8 H×W×3）の写しに、kinds の印を重ねて返す（image は変えない）。

    grid: 元画像の正規化座標の格子。cover の切り出しと時刻 t の camera（視差 1）で写すので、絵と一緒に動く（深度視差の変位は含めない）。
    lights: spec の光の位置（十字）と影響半径（円）と id。masks: 各マスクの 0.5 の輪郭と id。
    """
    out = np.ascontiguousarray(image.copy())
    width, height = ctx.size
    unit = max(1.0, width / 1600.0)
    thick, font_scale = max(1, round(unit)), 0.4 * unit
    forward, backward, matrix, zoom = _screen_mapper(ctx, t)
    if "masks" in kinds:
        from . import camera
        for mask_id, mask in ctx.assets.masks.items():
            warped = camera.warp(mask, matrix, ctx.size)
            contours, _ = cv2.findContours((warped > 0.5).astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if not contours:
                continue
            cv2.drawContours(out, contours, -1, MASK_COLOR, thick)
            top = min((tuple(point[0]) for contour in contours for point in contour), key=lambda p: (p[1], p[0]))
            cv2.putText(out, mask_id, (int(top[0]) + 3, max(int(14 * unit), int(top[1]) - 4)), FONT, font_scale, MASK_COLOR, thick, cv2.LINE_AA)
    if "grid" in kinds:
        left, top_v = backward(-0.5, -0.5)
        right, bottom = backward(width - 0.5, height - 0.5)
        grid_step = step or auto_step(right - left)
        digits = _decimals(grid_step)
        for v in np.arange(0, 1 + grid_step * 1e-4, grid_step):
            text = f"{v:.{digits}f}"
            x = int(round(forward(v, 0.0)[0]))
            if 0 <= x < width:
                cv2.line(out, (x, 0), (x, height - 1), X_COLOR, thick)
                cv2.putText(out, text, (x + 2 * thick, int(14 * unit)), FONT, font_scale, X_COLOR, thick, cv2.LINE_AA)
            y = int(round(forward(0.0, v)[1]))
            if 0 <= y < height:
                cv2.line(out, (0, y), (width - 1, y), Y_COLOR, thick)
                cv2.putText(out, text, (2 * thick, y - 2 * thick), FONT, font_scale, Y_COLOR, thick, cv2.LINE_AA)
    if "lights" in kinds:
        for light in ctx.spec.data["lights"]:
            x, y = forward(*light["position"])
            center = (int(round(x)), int(round(y)))
            radius = light["radius"] * width_scale(ctx.assets.box) * width * zoom
            cv2.drawMarker(out, center, LIGHT_COLOR, cv2.MARKER_CROSS, int(18 * unit), thick)
            cv2.circle(out, center, max(1, int(round(min(radius, 4 * max(width, height))))), LIGHT_COLOR, thick, cv2.LINE_AA)
            cv2.putText(out, light["id"], (center[0] + int(8 * unit), center[1] - int(8 * unit)), FONT, font_scale, LIGHT_COLOR, thick, cv2.LINE_AA)
    return out
