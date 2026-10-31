"""カメラ: キー・拍のアクセント・揺れ・切れ目の動き（seams）から、出力画素 → 出力画素の行列を作る。cv2 を使わない。

変換の順番は 基本（keys + accents + 揺れ）→ seams（pivot のまわり）。pivot が画面の中央で揺れが無ければ、回転は交換できるので
基本と seams を 1 つの行列にまとめる（0.1 と同じ式・同じ掛け順で、0.1 のデモは変更前とバイト単位で同じになる）。

視差（stage.parallax = p）の式は fx の camera と同じ:
    zoom_p = z0·(zoom/z0)^p, center_p = c0 + (center − c0)·p, 回転 × p
"""
import bisect
import math
from dataclasses import dataclass

import numpy as np

from ..fx import timeline

BLUR_MARGIN_S = 0.05      # ブレが効く窓を seams の前後からこれだけ広げる
BLUR_PEAK_S = 0.04        # シャッターが切れ目で開く山の時定数
PEAK_LIMIT = 0.6          # 回転の頂点は post_s のこの割合までに置く
SCREEN_CENTER = (0.5, 0.5)


def camera_matrix(zoom, center, rotate_deg, size):
    """出力画素 → 出力画素の 2×3 行列。center（正規化）の点が画面の中央に来る。zoom=1・center=中央・回転 0 で恒等。"""
    width, height = size
    angle = math.radians(rotate_deg)
    a, b = zoom * math.cos(angle), zoom * math.sin(angle)
    px, py = center[0] * width - 0.5, center[1] * height - 0.5
    return np.array([[a, -b, width / 2.0 - 0.5 - (a * px - b * py)],
                     [b, a, height / 2.0 - 0.5 - (b * px + a * py)]], dtype=np.float64)


def is_identity(matrix):
    return bool(np.all(np.abs(matrix - np.array([[1, 0, 0], [0, 1, 0]], dtype=np.float64)) < 1e-9))


def envelope(dt, attack, decay):
    """アクセントの包絡。dt = t − アクセントの時刻。拍の前は attack の smoothstep、拍で 1、その後は指数で減る。"""
    if dt < 0:
        return float(timeline.ease("in_out", 1.0 + dt / attack)) if attack > 0 and dt >= -attack else 0.0
    return math.exp(-dt / decay) if dt < 5 * decay else 0.0


def shake(dt, accent, scale):
    """揺れの平行移動（画素）。"""
    if dt < 0 or accent["shake_px"] <= 0 or dt >= 5 * accent["shake_decay_s"]:
        return 0.0, 0.0
    size = accent["shake_px"] * scale * math.exp(-dt / accent["shake_decay_s"])
    return size * math.sin(2 * math.pi * accent["shake_hz"][0] * dt), size * math.cos(2 * math.pi * accent["shake_hz"][1] * dt)


@dataclass(frozen=True)
class RotateCurve:
    """切れ目の後も同じ向きに回り続ける回転。m・c2・c3 は頂点までの 3 次式の係数（絶対値で計算し sign を掛ける）。"""
    sign: float
    start: float
    m: float
    c2: float
    c3: float
    peak_at: float
    peak: float
    post_s: float

    def at(self, x):
        """切れ目から x 秒後の角度。"""
        if x < self.peak_at:
            return self.sign * (self.start + x * (self.m + x * (self.c2 + x * self.c3)))
        u = (x - self.peak_at) / (self.post_s - self.peak_at)
        return self.sign * self.peak * (1.0 - float(timeline.ease("in_out", u)))


def rotate_curve(seam, fps):
    """rotate_post_deg の曲線。切れ目の前の 1 コマの変化量と、後の 1 コマの変化量が同じになるように解く。

    前の 1 コマの変化量 D が rotate_post_deg − rotate_deg より大きいと、1 コマ目で頂点を越えるので頂点を D まで上げる
    （切れ目で回転を止めないことを優先する。`raised` で分かる）。戻り値は (RotateCurve, raised)。"""
    dt = 1.0 / fps
    start, peak = abs(seam["rotate_deg"]), abs(seam["rotate_post_deg"])
    step = start * (1.0 - (1.0 - min(1.0, dt / seam["pre_s"])) ** 3)
    rise = max(peak - start, step)
    raised = rise > peak - start
    at = dt / (1.0 - math.sqrt(max(0.0, 1.0 - step / rise)))   # 等加速度で減速して止まる時刻
    at = max(dt, min(at, PEAK_LIMIT * seam["post_s"]))
    if at <= dt:
        m, c2, c3 = 2.0 * rise / dt, -rise / (dt * dt), 0.0
    else:
        m, c2, c3 = (float(v) for v in np.linalg.solve(
            [[dt, dt * dt, dt ** 3], [at, at * at, at ** 3], [1.0, 2.0 * at, 3.0 * at * at]], [step, rise, 0.0]))
    return RotateCurve(math.copysign(1.0, seam["rotate_deg"]), start, m, c2, c3, at, start + rise, seam["post_s"]), raised


def seam_lift(seam, curve, t):
    """切れ目の動きの (zoom の倍率, 回転の度)。窓の外は (1, 0)。pre_s が無ければ前後対称の式（0.1 と同じ）。"""
    dt = t - seam["t"]
    if seam["pre_s"] is None:
        u = abs(dt) / seam["span_s"]
        if u >= 1.0:
            return 1.0, 0.0
        lift = 1.0 - float(timeline.ease("in_out", u))
        return 1.0 + (seam["zoom"] - 1.0) * lift, seam["rotate_deg"] * lift
    if -seam["pre_s"] <= dt < 0:
        lift = (1.0 + dt / seam["pre_s"]) ** 3
    elif 0 <= dt < seam["post_s"]:
        lift = (1.0 - dt / seam["post_s"]) ** 2.5
    else:
        return 1.0, 0.0
    rotate = curve.at(dt) if curve is not None and dt >= 0 else seam["rotate_deg"] * lift
    return 1.0 + (seam["zoom"] - 1.0) * lift, rotate


def shutter_s(blur, dt, fps):
    """シャッター幅（秒）= (shutter + (peak − shutter)·exp(−|t − T|/0.04)) / fps。"""
    return (blur["shutter_frames"] + (blur["peak_shutter_frames"] - blur["shutter_frames"]) * math.exp(-abs(dt) / BLUR_PEAK_S)) / fps


def cover_zoom(rotate_deg, size):
    """回転しても画面の角に縁が出ない最小の zoom = cos|θ| + (長辺/短辺)·sin|θ|。"""
    angle = abs(math.radians(rotate_deg))
    return math.cos(angle) + max(size) / min(size) * math.sin(angle)


@dataclass
class State:
    """時刻 t のカメラ。zoom・rotate は seams を含まない基本の値。seam_zoom・seam_rotate・pivot が seams の分。"""
    zoom: float
    center: list
    rotate: float
    shake: tuple
    seam_zoom: float
    seam_rotate: float
    pivot: tuple


class Camera:
    def __init__(self, camera, size, fps):
        self.keys, self.seams, self.cover = camera["keys"], camera["seams"], camera["cover_rotation"]
        self.size, self.fps = size, fps
        self.curves = [rotate_curve(seam, fps)[0] if seam["rotate_post_deg"] is not None else None for seam in self.seams]
        self.events = sorted(((t, accent) for accent in camera["accents"] for t in accent["times"]), key=lambda e: e[0])
        self.times = [event[0] for event in self.events]
        self.reach = max((max(5 * a["decay_s"], 5 * a["shake_decay_s"]) for _, a in self.events), default=0.0)
        self.lead = max((a["attack_s"] for _, a in self.events), default=0.0)
        self.z0, self.c0 = self.keys[0]["zoom"], self.keys[0]["center"]
        self.pixel = size[0] / 1080.0

    def state(self, t):
        keyed = timeline.interp(self.keys, t, ["zoom", "center", "rotate_deg"])
        zoom, sx, sy = keyed["zoom"], 0.0, 0.0
        for at, accent in self.events[bisect.bisect_left(self.times, t - self.reach):bisect.bisect_right(self.times, t + self.lead)]:
            dt = t - at
            zoom *= 1.0 + (accent["zoom"] - 1.0) * envelope(dt, accent["attack_s"], accent["decay_s"])
            dx, dy = shake(dt, accent, self.pixel)
            sx, sy = sx + dx, sy + dy
        seam_zoom, seam_rotate, pivot = 1.0, 0.0, SCREEN_CENTER
        for seam, curve in zip(self.seams, self.curves):
            z, r = seam_lift(seam, curve, t)
            if z != 1.0 or r != 0.0:
                seam_zoom, seam_rotate, pivot = seam_zoom * z, seam_rotate + r, tuple(seam["pivot"])
        return State(zoom, keyed["center"], keyed["rotate_deg"], (sx, sy), seam_zoom, seam_rotate, pivot)

    def view(self, state, parallax=1.0):
        """視差 parallax の層の (zoom, center, rotate, 揺れ, seam_zoom, seam_rotate)。cover_rotation もここで掛ける。"""
        if parallax == 1.0:
            zoom, center, rotate, moved = state.zoom, state.center, state.rotate, state.shake
            seam_zoom, seam_rotate = state.seam_zoom, state.seam_rotate
        else:
            zoom = self.z0 * (state.zoom / self.z0) ** parallax
            center = [c0 + (c - c0) * parallax for c, c0 in zip(state.center, self.c0)]
            rotate, moved = state.rotate * parallax, (state.shake[0] * parallax, state.shake[1] * parallax)
            seam_zoom, seam_rotate = state.seam_zoom ** parallax, state.seam_rotate * parallax
        if self.cover:
            need = cover_zoom(rotate + seam_rotate, self.size)
            if zoom * seam_zoom < need:
                seam_zoom = need / zoom
        return zoom, center, rotate, moved, seam_zoom, seam_rotate

    def matrix(self, state, parallax=1.0):
        zoom, center, rotate, moved, seam_zoom, seam_rotate = self.view(state, parallax)
        if moved == (0.0, 0.0) and tuple(state.pivot) == SCREEN_CENTER:
            return camera_matrix(zoom * seam_zoom, center, rotate + seam_rotate, self.size)
        base = np.vstack([camera_matrix(zoom, center, rotate, self.size), [0.0, 0.0, 1.0]])
        base[:2, 2] += moved
        pivot = (state.pivot[0], state.pivot[1])
        around = camera_matrix(seam_zoom, pivot, seam_rotate, self.size)
        # camera_matrix は center を画面の中央へ運ぶので、pivot の点が pivot に留まるように戻す
        around[0, 2] += (pivot[0] - 0.5) * self.size[0]
        around[1, 2] += (pivot[1] - 0.5) * self.size[1]
        return (np.vstack([around, [0.0, 0.0, 1.0]]) @ base)[:2]

    def blur_states(self, t):
        """時刻 t のブレのサンプルの State の列。ブレの窓の外なら空。"""
        for seam in self.seams:
            blur = seam["blur"]
            before = seam["span_s"] if seam["pre_s"] is None else seam["pre_s"]
            after = seam["span_s"] if seam["pre_s"] is None else seam["post_s"]
            if blur is None or not seam["t"] - before - BLUR_MARGIN_S <= t <= seam["t"] + after + BLUR_MARGIN_S:
                continue
            width = shutter_s(blur, t - seam["t"], self.fps)
            return [self.state(t + width * ((k + 0.5) / blur["samples"] - 0.5)) for k in range(blur["samples"])]
        return []
