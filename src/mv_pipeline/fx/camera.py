"""カメラ: zoom・center のキーフレーム、plane ごとの視差付き変換、深度視差。

座標系: spec の camera.center は**元画像**の正規化座標（0..1）。出力の縦横比が元画像と違うときは cover で
中央に合わせて切るので、CameraPath が box（assets.Assets.box）を使って「切った後の画面」の正規化座標
(u, v) に直してから使う。画面の正規化座標 (u, v) は描画サイズ W×H の画素へ
x_out = (u − cx)·zoom·W + W/2 + drift の関係で写る（画素中心規約。cv2.warpAffine の 2×3 行列で表す）。

plane の視差（parallax = p）: キー 0 番の状態 (z0, c0) を基準に
    zoom_p = z0 + (zoom(t) − z0)·p,   center_p = c0 + (center(t) − c0)·p,   さらに drift_px × t
とする。p=1 は camera そのもの、p=0 は動かない。**どの plane も最初のキーの状態では同じ位置に重なる**
（最初のキーが zoom 1・center (0.5, 0.5) なら元画像どおり。設計メモの式 1 + (zoom−1)·p はこの特別な場合）。
"""
import functools

import cv2
import numpy as np

from . import timeline
from .assets import to_screen


class CameraPath:
    def __init__(self, camera, duration_s, size, box=(0.0, 0.0, 1.0, 1.0)):
        self.keys = camera["keys"]
        self.box = tuple(box)
        self.parallax_px = camera["parallax_px"]
        direction = np.asarray(camera["parallax_dir"], dtype=np.float64)
        norm = float(np.hypot(*direction))
        self.direction = (0.0, 0.0) if norm == 0 else (direction[0] / norm, direction[1] / norm)
        self.duration = duration_s
        self.size = size
        self.kpx = size[0] / 1920.0
        first = self.keys[0]
        self.z0, self.c0 = first["zoom"], to_screen(self.box, *first["center"])

    def at(self, t):
        """時刻 t のカメラ (zoom, cx, cy)。cx, cy は切った後の画面の正規化座標（keys は元画像の座標）。"""
        state = timeline.interp(self.keys, t, ["zoom", "center"])
        cx, cy = to_screen(self.box, *state["center"])
        return state["zoom"], cx, cy

    def plane_view(self, t, parallax, drift_px=(0.0, 0.0)):
        """parallax p の層の (zoom_p, cx_p, cy_p, drift_x, drift_y)。drift は出力画素。"""
        zoom, cx, cy = self.at(t)
        z = self.z0 + (zoom - self.z0) * parallax
        x = self.c0[0] + (cx - self.c0[0]) * parallax
        y = self.c0[1] + (cy - self.c0[1]) * parallax
        return z, x, y, drift_px[0] * t * self.kpx, drift_px[1] * t * self.kpx

    def shift(self, t, parallax):
        """カメラの center 移動が層に与える正規化シフト（画面上の向き。右へ動くカメラ → 負）。

        fog / particles は zoom を使わず、この平行移動だけで視差を表す。
        """
        _, cx, cy = self.at(t)
        return -(cx - self.c0[0]) * parallax, -(cy - self.c0[1]) * parallax

    def matrix(self, t, parallax, drift_px=(0.0, 0.0)):
        """画素座標 → 出力画素座標の 2×3 行列（float32）。"""
        z, cx, cy, dx, dy = self.plane_view(t, parallax, drift_px)
        return affine(z, cx, cy, dx, dy, self.size)

    def depth_amplitude(self, t):
        """深度視差の振幅（出力画素）= parallax_px × (W/1920) × s(t)。s は −0.5→+0.5 を in_out で。"""
        if self.parallax_px <= 0 or self.duration <= 0:
            return 0.0
        s = -0.5 + float(timeline.ease("in_out", t / self.duration))
        return self.parallax_px * self.kpx * s

    def out_of_bounds(self, samples=41):
        """カメラの視野が画像の外へ出る時刻があれば、その説明文を返す（なければ None）。"""
        worst = 0.0
        for t in np.linspace(0.0, self.duration, samples):
            z, cx, cy, _, _ = self.plane_view(float(t), 1.0)
            worst = max(worst, 0.5 / z - cx, cx + 0.5 / z - 1.0, 0.5 / z - cy, cy + 0.5 / z - 1.0)
        if worst > 1e-6:
            return f"camera の視野が画像の外へ最大 {worst * 100:.1f}% はみ出します（端の画素を引き伸ばして埋めます）"
        return None


def affine(zoom, cx, cy, dx, dy, size):
    width, height = size
    tx = zoom * (0.5 - cx * width) + width / 2.0 - 0.5 + dx
    ty = zoom * (0.5 - cy * height) + height / 2.0 - 0.5 + dy
    return np.array([[zoom, 0.0, tx], [0.0, zoom, ty]], dtype=np.float32)


def is_identity(matrix, tolerance=1e-6):
    return bool(np.all(np.abs(matrix - np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)) < tolerance))


def warp(img, matrix, size, border=cv2.BORDER_REPLICATE):
    """img を matrix で変換する。恒等ならコピーせずそのまま返す（ビット単位で同じ）。"""
    if is_identity(matrix):
        return img
    return cv2.warpAffine(img, matrix, size, flags=cv2.INTER_LINEAR, borderMode=border)


@functools.lru_cache(maxsize=4)
def _grid(size):
    width, height = size
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    xs.setflags(write=False)
    ys.setflags(write=False)
    return xs, ys


def warp_with_depth(img, matrix, size, depth, amplitude, direction):
    """camera 変換 + 深度視差。近いほど direction へ動く: 画素の移動量 = amplitude × (D − 0.5) × direction。

    D は出力側の深度（元の深度を同じ行列で変換したもの）。amplitude が 0 なら warp と同じ。
    """
    if depth is None or amplitude == 0.0 or direction == (0.0, 0.0):
        return warp(img, matrix, size)
    depth_out = warp(depth, matrix, size)
    shift = (depth_out - np.float32(0.5)) * np.float32(amplitude)
    xs, ys = _grid(size)
    scale = float(matrix[0, 0])
    map_x = ((xs - shift * np.float32(direction[0])) - np.float32(matrix[0, 2])) / np.float32(scale)
    map_y = ((ys - shift * np.float32(direction[1])) - np.float32(matrix[1, 2])) / np.float32(scale)
    return cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
