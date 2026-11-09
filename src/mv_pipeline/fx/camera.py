"""カメラ: zoom・center のキーフレーム、plane ごとの視差付き変換、深度視差、視野の取り方（frame・clamp・punch。fx-shot/0.3）。

視野の取り方: camera.frame が cover（既定）なら下の「切った後の画面」の上で動く。source なら板は元画像の全体
（assets.Assets.plate_size）で、center は元画像の正規化座標のまま、視野の大きさは zoom 1 で元画像に収まる出力の縦横比の
いちばん大きい範囲（実制作の縦のレンダラの crop と同じ取り方。実制作は出力の画素の番号をそのまま元画像の画素の番号へ写すので、
画素の端の座標で写すこちらとは最大で半画素ずれる）。clamp なら視野を画像の中に止めるよう center を寄せる。punch は 0〜duration_s 秒の zoom に
1 − amount·(1 − ease_out(t/duration_s)) を掛け、zoom は 1 より小さくしない。

座標系: spec の camera.center は**元画像**の正規化座標（0..1）。出力の縦横比が元画像と違うときは cover で
中央に合わせて切るので、CameraPath が box（assets.Assets.box）を使って「切った後の画面」の正規化座標
(u, v) に直してから使う。画面の正規化座標 (u, v) は描画サイズ W×H の画素へ
x_out = (u − cx)·zoom·W + W/2 + drift の関係で写る（画素中心規約。cv2.warpAffine の 2×3 行列で表す）。

plane の視差（parallax = p）: キー 0 番の状態 (z0, c0) を基準に
    zoom_p = z0·(zoom(t)/z0)^p,   center_p = c0 + (center(t) − c0)·p,   さらに drift_px × t
とする。p=1 は camera そのもの、p=0 は z0 のまま。**どの plane も最初のキーの状態では同じ位置に重なる**
（最初のキーが zoom 1・center (0.5, 0.5) なら元画像どおり）。

zoom は対数空間で p 倍する。zoom は倍率なので「z0 から見た何倍か」を p 乗するのが自然で、zoom(t) と z0 が
正なら zoom_p は p によらず常に正になる。線形の z0 + (zoom − z0)·p は、ズームアウト（zoom が下がる）で
p > 1 の plane のスケールが 0 や負になり、plane が潰れて反転し、背景の深度視差（スケールで割る）も壊れる。
線形との差が小さいのは zoom の変化が小さいときだけ（デモの 1.0→1.08 で p=0.4〜1.3 の最大差は 1920 幅で約 1.2px。1→2 で p=0.4 だと倍率は 1.40 から 1.32 になる）。
center は画面上の位置（符号つきの移動）なので線形のままでよい。

深度視差（fx-shot/0.2）: 出力画素ごとに k = D − pivot（D は出力側の深度、pivot は動かない深さ）とし、
    変位 = (shift + (画素 − 画面の中心) × dolly) × k         （出力画素。内容がこの向きへ動く）
で入力をずらして引く。shift は keys の depth_shift（1920 換算 px）を補間して W/1920 を掛けたもの、dolly は keys の dolly。
画面の中心は (W/2, H/2)（画素の番号で数える。実制作の 2.5D と同じ）。0.1 の parallax_px は shift の省略形で、
振幅 parallax_px × (W/1920) × in_out(t/尺) と向き parallax_dir に分けた 0.1 の式のまま計算する（pivot 0.5 で 0.1 とビット一致）。
"""
import functools
from dataclasses import dataclass

import cv2
import numpy as np

from . import timeline
from .assets import to_screen


@dataclass(frozen=True)
class DepthMotion:
    """時刻 t の深度視差。shift は出力画素（深さの差 1 あたり）、dolly は深さの差 1 あたりの拡大率。
    legacy は 0.1 の parallax_px の (振幅, 向き)。0.1 と同じ丸めの順で計算するために別に持つ。"""
    pivot: float
    shift: tuple = (0.0, 0.0)
    dolly: float = 0.0
    legacy: tuple | None = None


def ease_out(x):
    """1 − (1 − x)³（x は 0..1 に切る）。punch の戻り方（実制作の縦のレンダラと同じ）。"""
    x = min(max(x, 0.0), 1.0)
    return 1 - (1 - x) ** 3


def punch_factor(punch, t):
    """押し込みの zoom の倍率。t < duration_s の間 1 − amount·(1 − ease_out(t/duration_s))、その後は 1。"""
    if punch is None:
        return 1.0
    v = t / punch["duration_s"]
    return 1 - punch["amount"] * (1 - ease_out(v)) if v < 1 else 1.0


class CameraPath:
    """camera の時間変化。plate は板の大きさ（camera.frame: source の元画像の全体。None なら板は描画サイズ = cover）。

    source では、zoom 1 の視野が元画像に収まる出力の縦横比のいちばん大きい範囲になるよう、板の 1 画素を cover 倍
    （= max(W/板の幅, H/板の高さ)）して写す。center は元画像（= 板）の正規化座標。"""

    def __init__(self, camera, duration_s, size, box=(0.0, 0.0, 1.0, 1.0), pivot=0.5, plate=None):
        self.keys = camera["keys"]
        self.box = tuple(box)
        self.parallax_px = camera["parallax_px"]
        self.pivot = float(pivot)     # 数にしたもの（{"mask"}・{"box"} は render.prepare が深度から決めて渡す）
        self.depth_keys = any(any(v != 0.0 for v in key.get("depth_shift", (0.0, 0.0))) or key.get("dolly", 0.0) != 0.0
                              for key in self.keys)
        direction = np.asarray(camera["parallax_dir"], dtype=np.float64)
        norm = float(np.hypot(*direction))
        self.direction = (0.0, 0.0) if norm == 0 else (direction[0] / norm, direction[1] / norm)
        self.duration = duration_s
        self.size = size
        self.kpx = size[0] / 1920.0
        self.plate = None if plate is None else tuple(plate)
        self.cover = 1.0 if plate is None else max(size[0] / plate[0], size[1] / plate[1])
        self.clamp = bool(camera.get("clamp", False))
        self.punch = camera.get("punch")
        first = self.keys[0]
        if self.clamp or self.punch is not None or self.plate is not None:
            self.z0, cx, cy = self.at(0.0)          # 押し込み・止めた後の最初の状態（層が最初のコマで重なるように）
            self.c0 = (cx, cy)
        else:
            self.z0, self.c0 = first["zoom"], to_screen(self.box, *first["center"])

    def at(self, t):
        """時刻 t のカメラ (zoom, cx, cy)。cx, cy は切った後の画面の正規化座標（keys は元画像の座標）。"""
        state = timeline.interp(self.keys, t, ["zoom", "center"])
        zoom = state["zoom"]
        if self.punch is not None:              # 実制作と同じ: zoom·倍率 を 1 で止める
            zoom = max(1.0, zoom * punch_factor(self.punch, t))
        cx, cy = to_screen(self.box, *state["center"])
        if self.clamp:
            half_w, half_h = self.half_view(zoom)
            cx, cy = min(max(cx, half_w), 1.0 - half_w), min(max(cy, half_h), 1.0 - half_h)
        return zoom, cx, cy

    def half_view(self, zoom):
        """zoom の視野の半分の幅と高さ（板の正規化座標）。"""
        if self.plate is None:
            return 0.5 / zoom, 0.5 / zoom
        scale = self.cover * zoom
        return self.size[0] / (2.0 * scale * self.plate[0]), self.size[1] / (2.0 * scale * self.plate[1])

    def plane_view(self, t, parallax, drift_px=(0.0, 0.0)):
        """parallax p の層の (zoom_p, cx_p, cy_p, drift_x, drift_y)。drift は出力画素。"""
        zoom, cx, cy = self.at(t)
        z = self.z0 * (zoom / self.z0) ** parallax     # 対数空間: z0·(zoom/z0)^p（常に正。p=1 でカメラ、p=0 で z0）
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
        return affine(z, cx, cy, dx, dy, self.size, self.plate, self.cover)

    def depth_amplitude(self, t):
        """深度視差の振幅（出力画素）= parallax_px × (W/1920) × s(t)。s は 0→1 を in_out で（t=0 で恒等）。

        画素の移動量は amplitude × (D − 0.5) なので、近い面ほど大きく動く。ショット全体の振れ幅は
        parallax_px × (D − 0.5)。t=0 で 0 にしてあるので、最初のフレームでは base 版も layers 版も元画像どおり。
        """
        if self.parallax_px <= 0 or self.duration <= 0:
            return 0.0
        return self.parallax_px * self.kpx * float(timeline.ease("in_out", t / self.duration))

    @property
    def has_depth_motion(self):
        """spec に深度視差の動き（parallax_px か keys の depth_shift / dolly）があるか。"""
        return self.parallax_px > 0 or self.depth_keys

    def depth_motion(self, t):
        """時刻 t の深度視差（DepthMotion）。動かない時刻なら None（warp だけで描く。0.1 の t=0 と同じ近道）。"""
        dolly, shift = 0.0, (0.0, 0.0)
        if self.depth_keys:
            state = timeline.interp(self.keys, t, ["depth_shift", "dolly"])
            dolly = float(state["dolly"])
            shift = (float(state["depth_shift"][0]) * self.kpx, float(state["depth_shift"][1]) * self.kpx)
        if self.parallax_px > 0:      # 0.1 の経路（depth_shift と一緒には書けない。dolly は足せる）
            amplitude = self.depth_amplitude(t)
            if (amplitude == 0.0 or self.direction == (0.0, 0.0)) and dolly == 0.0:
                return None
            return DepthMotion(self.pivot, dolly=dolly, legacy=(amplitude, self.direction))
        if shift == (0.0, 0.0) and dolly == 0.0:
            return None
        return DepthMotion(self.pivot, shift=shift, dolly=dolly)

    def probe_times(self, samples=41):
        """深度視差の端の検査で見る時刻: 0〜duration の等間隔の samples 個と、keys の時刻。

        ease は単調なので、keys の値（depth_shift・dolly）の山と谷はキーの時刻にある。等間隔だけだと、
        キーの間の短い動き（0.40 → 0.45 → 0.49 秒の depth_shift など）を飛ばす。"""
        times = {float(t) for t in np.linspace(0.0, self.duration, samples)}
        times.update(float(key["t"]) for key in self.keys if 0.0 <= float(key["t"]) <= self.duration)
        return sorted(times)

    def out_of_bounds(self, samples=41):
        """カメラの視野が画像の外へ出る時刻があれば、その説明文を返す（なければ None）。"""
        worst = 0.0
        for t in np.linspace(0.0, self.duration, samples):
            z, cx, cy, _, _ = self.plane_view(float(t), 1.0)
            if self.plate is None:
                worst = max(worst, 0.5 / z - cx, cx + 0.5 / z - 1.0, 0.5 / z - cy, cy + 0.5 / z - 1.0)
            else:
                half_w, half_h = self.half_view(z)
                worst = max(worst, half_w - cx, cx + half_w - 1.0, half_h - cy, cy + half_h - 1.0)
        if worst > 1e-6:
            return f"camera の視野が画像の外へ最大 {worst * 100:.1f}% はみ出します（端の画素を引き伸ばして埋めます）"
        return None


def affine(zoom, cx, cy, dx, dy, size, plate=None, cover=1.0):
    """板の画素 → 出力の画素の 2×3（画素中心規約）。plate が None なら板は描画サイズ。"""
    width, height = size
    if plate is None:
        tx = zoom * (0.5 - cx * width) + width / 2.0 - 0.5 + dx
        ty = zoom * (0.5 - cy * height) + height / 2.0 - 0.5 + dy
        return np.array([[zoom, 0.0, tx], [0.0, zoom, ty]], dtype=np.float32)
    scale = cover * zoom
    tx = scale * (0.5 - cx * plate[0]) + width / 2.0 - 0.5 + dx
    ty = scale * (0.5 - cy * plate[1]) + height / 2.0 - 0.5 + dy
    return np.array([[scale, 0.0, tx], [0.0, scale, ty]], dtype=np.float32)


def screen_box(path, t, box):
    """元画像の正規化座標の箱 [x0, y0, x1, y1] → 時刻 t の画面の正規化座標の箱（camera の層 parallax 1。深度視差は入れない）。
    箱の辺は画素の端の座標で写す（板の u → 板の画素の端 u·幅 → 出力の画素の端 → 出力の幅で割る）。"""
    matrix = path.matrix(t, 1.0).astype(np.float64)
    plate_w, plate_h = path.plate or path.size
    (u0, v0), (u1, v1) = to_screen(path.box, box[0], box[1]), to_screen(path.box, box[2], box[3])
    x = lambda u: (matrix[0, 0] * (u * plate_w - 0.5) + matrix[0, 2] + 0.5) / path.size[0]  # noqa: E731
    y = lambda v: (matrix[1, 1] * (v * plate_h - 0.5) + matrix[1, 2] + 0.5) / path.size[1]  # noqa: E731
    return [x(u0), y(v0), x(u1), y(v1)]


def is_identity(matrix, tolerance=1e-6):
    return bool(np.all(np.abs(matrix - np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float32)) < tolerance))


def warp(img, matrix, size, border=cv2.BORDER_REPLICATE):
    """img を matrix で変換する。恒等で大きさも同じならコピーせずそのまま返す（ビット単位で同じ。source の板は出力と大きさが違う）。"""
    if is_identity(matrix) and img.shape[1] == size[0] and img.shape[0] == size[1]:
        return img
    return cv2.warpAffine(img, matrix, size, flags=cv2.INTER_LINEAR, borderMode=border)


@functools.lru_cache(maxsize=16)
def _edge_band(size, band):
    """画面の端から band 画素の帯（上下左右）の座標 (xs, ys)。float32 の (行, 1024)（cv2.remap は 1 辺 32767 未満しか受けない）。
    端数は帯の最初の画素を繰り返して埋める（最小・最大は変わらない）。"""
    width, height = size
    band = max(1, min(band, width // 2, height // 2))
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    edge = (xs < band) | (xs >= width - band) | (ys < band) | (ys >= height - band)
    flat_x, flat_y = xs[edge], ys[edge]
    pad = -len(flat_x) % 1024
    out = (np.concatenate([flat_x, np.full(pad, flat_x[0], np.float32)]).reshape(-1, 1024),
           np.concatenate([flat_y, np.full(pad, flat_y[0], np.float32)]).reshape(-1, 1024))
    for array in out:
        array.setflags(write=False)
    return out


@functools.lru_cache(maxsize=4)
def _grid(size):
    width, height = size
    xs, ys = np.meshgrid(np.arange(width, dtype=np.float32), np.arange(height, dtype=np.float32))
    xs.setflags(write=False)
    ys.setflags(write=False)
    return xs, ys


def depth_offsets(depth_out, size, motion, coords=None):
    """出力画素ごとの深度視差の変位 (dx, dy)（出力画素。内容がこの向きへ動く）。depth_out は出力側の深度。

    coords = (xs, ys) は depth_out と同じ形の出力画素の座標（省略時は画面全体の格子）。"""
    k = depth_out - np.float32(motion.pivot)
    if motion.legacy is not None:        # 0.1 の parallax_px: 0.1 と同じ式・同じ丸めの順
        amplitude, direction = motion.legacy
        shift = k * np.float32(amplitude)
        dx, dy = shift * np.float32(direction[0]), shift * np.float32(direction[1])
        if motion.dolly != 0.0:
            xs, ys = _grid(size) if coords is None else coords
            dolly = np.float32(motion.dolly)
            dx = dx + (xs - np.float32(size[0] / 2.0)) * dolly * k
            dy = dy + (ys - np.float32(size[1] / 2.0)) * dolly * k
        return dx, dy
    sx, sy = np.float32(motion.shift[0]), np.float32(motion.shift[1])
    if motion.dolly == 0.0:
        return sx * k, sy * k
    xs, ys = _grid(size) if coords is None else coords
    dolly = np.float32(motion.dolly)                  # 実制作の 2.5D と同じ順: (shift + (x − W/2)·dolly)·k
    return (sx + (xs - np.float32(size[0] / 2.0)) * dolly) * k, (sy + (ys - np.float32(size[1] / 2.0)) * dolly) * k


def sample_maps(matrix, size, depth_out, motion):
    """cv2.remap の参照位置（入力画像の画素）。深度視差の変位を引いてから camera の逆変換を掛ける。"""
    dx, dy = depth_offsets(depth_out, size, motion)
    xs, ys = _grid(size)
    scale = float(matrix[0, 0])
    map_x = ((xs - dx) - np.float32(matrix[0, 2])) / np.float32(scale)
    map_y = ((ys - dy) - np.float32(matrix[1, 2])) / np.float32(scale)
    return map_x, map_y


def warp_with_depth(img, matrix, size, depth, motion):
    """camera 変換 + 深度視差。D は出力側の深度（元の深度を同じ行列で変換したもの）。motion が None なら warp と同じ。"""
    if depth is None or motion is None:
        return warp(img, matrix, size)
    map_x, map_y = sample_maps(matrix, size, warp(depth, matrix, size), motion)
    return cv2.remap(img, map_x, map_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)


def depth_out_of_bounds(path, depth, parallaxes=(1.0,), samples=41, tolerance_px=0.5):
    """深度視差の変位で、画面の端が入力画像の外を参照する時刻があれば、その説明文を返す（なければ None）。

    camera の視野のはみ出し（CameraPath.out_of_bounds）を超えた分だけを数える。parallaxes は深度視差を掛ける層の
    視差（平らな版は 1.0、layers のある版は背景の parallax も）。値は 1920 幅換算の px。
    調べる点は出力の端の画素で、参照する位置は入力画像（板）の画素なので、端は板の大きさで比べる（camera.frame: source の
    板は出力と大きさが違う。cover の板は出力と同じ大きさ）。
    """
    if depth is None or not path.has_depth_motion:
        return None
    width, height = path.size
    plate_w, plate_h = path.plate or path.size
    reach = max(abs(float(depth.max()) - path.pivot), abs(float(depth.min()) - path.pivot))   # |D − pivot| の最大
    motions = [(t, path.depth_motion(t)) for t in path.probe_times(samples)]
    motions = [(t, motion) for t, motion in motions if motion is not None]
    if not motions:
        return None

    def reach_px(motion):
        if motion.legacy is not None:
            amplitude, direction = motion.legacy
            sx, sy = amplitude * abs(direction[0]), amplitude * abs(direction[1])
        else:
            sx, sy = abs(motion.shift[0]), abs(motion.shift[1])
        return (sx + sy + abs(motion.dolly) * max(width, height) / 2.0) * reach

    # 変位は (|shift| + |dolly|·半幅)·reach を超えないので、それより内側の画素は端より外を引けない。端の帯だけ調べる
    xs, ys = _edge_band(path.size, int(np.ceil(max(reach_px(motion) for _, motion in motions))) + 2)
    worst = 0.0
    for t, motion in motions:
        for parallax in parallaxes:
            matrix = path.matrix(float(t), parallax)
            scale, tx, ty = float(matrix[0, 0]), float(matrix[0, 2]), float(matrix[1, 2])
            source_x, source_y = (xs - np.float32(tx)) / np.float32(scale), (ys - np.float32(ty)) / np.float32(scale)
            depth_out = cv2.remap(depth, source_x, source_y, interpolation=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            dx, dy = depth_offsets(depth_out, path.size, motion, (xs, ys))
            map_x, map_y = ((xs - dx) - np.float32(tx)) / np.float32(scale), ((ys - dy) - np.float32(ty)) / np.float32(scale)
            camera_x = (np.array([0.0, width - 1.0]) - tx) / scale       # 変位なしの参照位置（出力の端の画素 → 板の画素）
            camera_y = (np.array([0.0, height - 1.0]) - ty) / scale
            before = (-camera_x[0], camera_x[1] - (plate_w - 1), -camera_y[0], camera_y[1] - (plate_h - 1))   # 左・右・上・下
            after = (-float(map_x.min()), float(map_x.max()) - (plate_w - 1), -float(map_y.min()), float(map_y.max()) - (plate_h - 1))
            worst = max(worst, max(a - max(b, 0.0) for a, b in zip(after, before)) * scale)
    if worst > tolerance_px:
        return (f"深度視差の変位で、画面の端が画像の外を最大 {worst / path.kpx:.1f}px（1920 幅換算）参照します"
                f"（端の画素を引き伸ばして埋めます。camera を寄せるか、depth_shift・dolly・parallax_px を小さくしてください）")
    return None
