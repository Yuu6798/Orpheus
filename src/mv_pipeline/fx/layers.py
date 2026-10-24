"""空間レイヤー: 背景の穴埋め板・plane（マスクで切り出した層）・霧・火の粉。

層は「事前に作る静的な部分」と「フレームごとの純関数」に分かれる。合成は線形 float32 で、
下から順に `out = c + (1 − a)·out`（c は alpha 乗算済みの色）。火の粉は加算。
乱数はすべて timeline.seeded_rng（seed と effect id から決まる）を通す。
"""
import math
from dataclasses import dataclass

import cv2
import numpy as np

from . import camera as cam
from . import timeline
from .color import color_to_linear, to_linear

INPAINT_MAX_SIDE = 480


@dataclass
class Plane:
    id: str
    mask_id: str
    z: float
    parallax: float
    drift_px: tuple
    alpha: np.ndarray        # float32 (H, W)
    premult: np.ndarray      # float32 (H, W, 4) = [基準画像 × alpha, alpha]


def make_plane(spec_plane, alpha, plate):
    premult = np.empty(plate.shape[:2] + (4,), dtype=np.float32)
    premult[..., :3] = plate * alpha[..., None]
    premult[..., 3] = alpha
    return Plane(spec_plane["id"], spec_plane["mask"], spec_plane["z"], spec_plane["parallax"],
                 tuple(spec_plane["drift_px"]), alpha, premult)


def build_background(plate_u8, plate, alphas, inpaint_px, kpx):
    """全 plane マスクの和集合（inpaint_px だけ膨らませる）を cv2.inpaint (TELEA) で埋めた板を返す。

    速度のため縮小して埋め、穴の周辺だけを元画像とぼかして混ぜる。plane が無ければ元画像をそのまま返す。
    戻り値: (背景 float32 線形, 穴の重み float32 or None)
    """
    if not alphas:
        return plate, None
    height, width = plate.shape[:2]
    union = np.maximum.reduce(alphas)
    sigma = max(1.0, 1.5 * kpx)
    grow = int(round(inpaint_px * kpx)) + int(math.ceil(2 * sigma))
    solid = (union > 0.02).astype(np.uint8)
    if not solid.any():
        return plate, None
    if grow > 0:
        solid = cv2.dilate(solid, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1)))
    shrink = min(1.0, INPAINT_MAX_SIDE / max(width, height))
    small = (max(8, int(round(width * shrink))), max(8, int(round(height * shrink))))
    small_img = cv2.resize(plate_u8, small, interpolation=cv2.INTER_AREA)
    small_mask = (cv2.resize(solid * 255, small, interpolation=cv2.INTER_AREA) > 0).astype(np.uint8) * 255
    filled = cv2.inpaint(small_img, small_mask, 3, cv2.INPAINT_TELEA)
    filled = cv2.resize(filled, (width, height), interpolation=cv2.INTER_CUBIC)
    weight = cv2.GaussianBlur(solid.astype(np.float32), (0, 0), sigma, borderType=cv2.BORDER_REPLICATE)
    weight = np.ascontiguousarray(np.clip(weight, 0.0, 1.0), dtype=np.float32)
    background = plate * (np.float32(1) - weight[..., None]) + to_linear(filled) * weight[..., None]
    return background.astype(np.float32), weight


def over(below, color, alpha):
    """alpha 乗算済みの色 color・不透明度 alpha の層を below の上に重ねる。"""
    return color + (np.float32(1.0) - alpha)[..., None] * below


def plane_layer(plane, path, t, size, lit=None, glow=None):
    """plane をカメラ変換して (premult 色, alpha) を返す。

    lit: 光を当てた基準画像（線形 H×W×3）。None なら光なしの静的な premult を使う。
    glow: この plane が持つ光源の発光（層と一緒に動く加算分。alpha の外にも載る）。light.hosted_glow が
          「受光と発光を合わせて 1 回 soft_add した色 − lit」として作るので、lit と足すとソフトニーは 1 回だけ掛かる。
    """
    matrix = path.matrix(t, plane.parallax, plane.drift_px)
    if lit is None and glow is None:
        src = plane.premult
    else:
        src = plane.premult.copy()
        if lit is not None:
            src[..., :3] = lit * plane.alpha[..., None]
        if glow is not None:
            src[..., :3] += glow
    warped = cam.warp(src, matrix, size, cv2.BORDER_CONSTANT)
    return warped[..., :3], warped[..., 3]


# ---- 霧 -------------------------------------------------------------------

@dataclass
class Fog:
    id: str
    z: float
    color: np.ndarray        # float32 (3,) 線形
    opacity: float
    drift: tuple
    parallax: float
    texture: np.ndarray      # float32 (H, W) 0..1。縦横とも周期的（BORDER_WRAP でずらせる）


def make_fog(effect, seed, size):
    """seed から作った値ノイズ（3 オクターブ）を、スクリーン全体を周期とする 1 枚のテクスチャにする。"""
    width, height = size
    rng = timeline.seeded_rng(seed, "fog:" + effect["id"])
    nx = max(2, int(round(1.0 / effect["scale"])))
    ny = max(2, int(round(height / width / effect["scale"])))
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    texture = np.zeros((height, width), dtype=np.float32)
    for octave, amplitude in enumerate((1.0, 0.5, 0.25)):
        gx, gy = nx * 2 ** octave, ny * 2 ** octave
        lattice = rng.random((gy, gx)).astype(np.float32)
        map_x = (xs + 0.5) * (gx / width) - 0.5
        map_y = (ys + 0.5) * (gy / height) - 0.5
        texture += amplitude * cv2.remap(lattice, map_x, map_y, cv2.INTER_CUBIC, borderMode=cv2.BORDER_WRAP)
    lo, hi = float(texture.min()), float(texture.max())
    texture = np.ascontiguousarray((texture - lo) / max(hi - lo, 1e-6), dtype=np.float32)
    return Fog(effect["id"], effect["z"], color_to_linear(effect["color"]), effect["opacity"],
               tuple(effect["drift"]), effect["parallax"], texture)


def fog_layer(fog, path, t, size):
    """霧の (premult 色, alpha)。drift × t とカメラ移動 × parallax だけ平行移動する。"""
    width, height = size
    shift_x, shift_y = path.shift(t, fog.parallax)
    off_x = (fog.drift[0] * t + shift_x) * width
    off_y = (fog.drift[1] * t + shift_y) * height
    matrix = np.array([[1.0, 0.0, off_x], [0.0, 1.0, off_y]], dtype=np.float32)
    texture = cam.warp(fog.texture, matrix, size, cv2.BORDER_WRAP)
    alpha = texture * np.float32(fog.opacity)
    return alpha[..., None] * fog.color, alpha


# ---- 火の粉 ---------------------------------------------------------------

@dataclass
class Particles:
    id: str
    z: float
    color: np.ndarray        # float32 (3,) 線形
    velocity: tuple
    flicker: float
    parallax: float
    x0: np.ndarray
    y0: np.ndarray
    sigma: np.ndarray        # 画素
    phase: np.ndarray
    rate: np.ndarray         # 明滅 Hz


def make_particles(effect, seed, size):
    """初期位置・大きさ・明滅の位相と速さを seed から決める。以後は t の関数（状態を持たない）。"""
    rng = timeline.seeded_rng(seed, "particles:" + effect["id"])
    count = effect["count"]
    lo, hi = sorted(effect["size_px"])
    kpx = size[0] / 1920.0
    diameter = lo + (hi - lo) * rng.random(count)
    return Particles(
        effect["id"], effect["z"], color_to_linear(effect["color"]), tuple(effect["velocity"]), effect["flicker"],
        effect["parallax"], rng.random(count), rng.random(count),
        np.maximum(0.6, diameter * kpx / 2.5), rng.random(count) * 2 * math.pi, 0.8 + 2.7 * rng.random(count))


def particle_positions(particles, path, t, size):
    """時刻 t の各粒の位置（出力画素、画素中心規約）。(x0 + velocity·t + カメラ移動·parallax) mod 1。"""
    width, height = size
    shift_x, shift_y = path.shift(t, particles.parallax)
    u = (particles.x0 + particles.velocity[0] * t + shift_x) % 1.0
    v = (particles.y0 + particles.velocity[1] * t + shift_y) % 1.0
    return u * width - 0.5, v * height - 0.5


def draw_particles(out, particles, path, t, size):
    """火の粉を out（線形 H×W×3）に加算した新しい配列を返す。ソフトニーは掛けず、最終段で切る。"""
    width, height = size
    px, py = particle_positions(particles, path, t, size)
    bright = 1.0 - particles.flicker * (0.5 + 0.5 * np.sin(2 * math.pi * particles.rate * t + particles.phase))
    out = out.copy()
    color = particles.color
    for i in range(len(px)):
        sigma = float(particles.sigma[i])
        radius = int(math.ceil(3 * sigma))
        cx, cy = float(px[i]), float(py[i])
        x0, x1 = max(0, int(math.floor(cx)) - radius), min(width, int(math.floor(cx)) + radius + 2)
        y0, y1 = max(0, int(math.floor(cy)) - radius), min(height, int(math.floor(cy)) + radius + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        gx = np.exp(-((np.arange(x0, x1, dtype=np.float32) - cx) ** 2) / (2 * sigma * sigma))
        gy = np.exp(-((np.arange(y0, y1, dtype=np.float32) - cy) ** 2) / (2 * sigma * sigma))
        patch = (gy[:, None] * gx[None, :]) * np.float32(bright[i])
        out[y0:y1, x0:x1] += patch[..., None] * color
    return out
