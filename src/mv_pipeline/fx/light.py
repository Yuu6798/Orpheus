"""局所照明: 元画像空間（カメラの前）で、点光源が受光マスクの内側だけを照らす。

光の場は時間に依らない形（位置・半径・マスク・深度）と、時間で変わるスカラー
level(t) = intensity(t) × flicker(t) の積なので、形は prepare() で 1 回だけ作り、
フレームごとには level と色を掛けて足すだけにしている。

座標規約: light.position・radius・emitter.size は**元画像**の正規化座標（半径・size は元画像の幅が基準）。
出力の縦横比が違って cover で切られても、光は元画像の同じ画素に付く（prepare が assets.Assets.box で
出力画素へ写す。9:16 の出力にしても、マスク・被写体に対する光の位置と半径は変わらない）。

    r      = 光源からの距離（元画像の幅で正規化）
    falloff(r) = 1/(1+(r/(0.25R))²) × clamp(1−(r/R)⁴, 0, 1)²      （r ≥ R で厳密に 0）
    diffuse    = 基準画像 × color × level × falloff × gain × EXPOSURE × M_r
    rim        = 受光マスクの内側の輪郭帯 × color × level × falloff × rim × 向き重み × M_r
    reflection = color × level × falloff'(縦に潰した距離 dy×0.10・半径 0.18R) × reflection × M_r
    emitter    = color × level × ガウス核+裾（半径 4×size で厳密に 0）
    depth      = 受光の 3 項に exp(−((D−depth)/depth_falloff)²) を掛ける

局所性: すべての受光項に M_r が掛かり、emitter は 4×size の外で厳密に 0 なので、受光マスクが 0 で
emitter の外の画素の加算量は**厳密に 0**。color.soft_add は加算量 0 の画素を元の値のまま返す。
rim の向き重みは輪郭の外向き法線と光源方向の内積から (0.5+0.5·cos)² （設計メモにない追加。輪郭が
全周同じ強さで光る安っぽさを避けるため）。
"""
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import timeline
from .assets import to_screen, width_scale
from .color import color_to_linear, soft_add

EXPOSURE = 4.0
REFLECT_RADIUS = 0.18     # 照り返しの横の半径（光の半径 R に対する割合）。狭いので光源の真下の縦長の筋になる
REFLECT_SQUASH = 0.10     # 縦の距離に掛ける係数（縦の半径 = 横の半径 / 0.10）。霧の塊のように広がらない
EMITTER_REACH = 4.0


@dataclass
class Receiver:
    mask_id: str
    diffuse_w: np.ndarray | None = None   # float32 (h, w) roi_main 内。falloff × gain × EXPOSURE × M_r × depth
    spec_w: np.ndarray | None = None      # float32 (h, w) roi_spec 内。falloff' × reflection × M_r × depth
    rim: tuple | None = None              # (ys, xs, weight) 画素列と重み（roi_main 内の輪郭帯）


@dataclass
class Light:
    id: str
    spec: dict
    color: np.ndarray                     # float32 (3,) 線形
    phases: tuple
    roi_main: tuple                       # (y0, y1, x0, x1)
    roi_spec: tuple
    roi_glow: tuple
    glow_w: np.ndarray | None             # float32 (h, w) roi_glow 内
    receivers: list = field(default_factory=list)
    host: str | None = None               # emitter が載る層。plane の id、None = 背景


def _roi(cx, cy, half_x, half_y, size):
    width, height = size
    return (max(0, int(math.floor(cy - half_y))), min(height, int(math.ceil(cy + half_y)) + 1),
            max(0, int(math.floor(cx - half_x))), min(width, int(math.ceil(cx + half_x)) + 1))


def _coords(roi):
    y0, y1, x0, x1 = roi
    return (np.arange(y0, y1, dtype=np.float32) + np.float32(0.5))[:, None], \
           (np.arange(x0, x1, dtype=np.float32) + np.float32(0.5))[None, :]


def falloff(q):
    """q = (r/R)² から falloff を返す。q ≥ 1 で厳密に 0。"""
    window = np.clip(np.float32(1.0) - q * q, np.float32(0.0), np.float32(1.0))
    return window * window / (np.float32(1.0) + np.float32(16.0) * q)


def _field(roi, px, py, radius_px, squash=1.0):
    ys, xs = _coords(roi)
    dx, dy = xs - np.float32(px), (ys - np.float32(py)) * np.float32(squash)
    return falloff((dx * dx + dy * dy) / np.float32(radius_px * radius_px))


def _depth_gate(depth, center, spread):
    return np.exp(-(((depth - np.float32(center)) / np.float32(spread)) ** 2)).astype(np.float32)


def _rim_geometry(mask, kpx):
    """マスクの輪郭帯 (ys, xs, band, nx, ny)。帯はマスクの内側だけ。法線は外向き。"""
    erode = max(1, int(round(3 * kpx)))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode + 1, 2 * erode + 1))
    band = np.clip(mask - cv2.erode(mask, kernel), 0.0, 1.0)
    band = cv2.GaussianBlur(band, (0, 0), max(0.8, kpx), borderType=cv2.BORDER_REPLICATE) * mask
    ys, xs = np.nonzero(band > 1e-3)
    smooth = cv2.GaussianBlur(mask, (0, 0), max(1.5, 2.5 * kpx), borderType=cv2.BORDER_REPLICATE)
    gx = cv2.Sobel(smooth, cv2.CV_32F, 1, 0, ksize=3)[ys, xs]
    gy = cv2.Sobel(smooth, cv2.CV_32F, 0, 1, ksize=3)[ys, xs]
    norm = np.sqrt(gx * gx + gy * gy) + np.float32(1e-6)
    return ys, xs, band[ys, xs], -gx / norm, -gy / norm


def _auto_host(px, py, reach, planes, size):
    """emitter の影響半径（reach 画素）内に不透明部分（alpha > 0.5）を持つ plane のうち、z が最も前のものの id。

    なければ None（背景）。z が同じなら宣言の後ろの plane（描画で手前に来る方）。
    """
    if reach <= 0 or not planes:
        return None
    roi = _roi(px, py, reach, reach, size)
    y0, y1, x0, x1 = roi
    if y1 <= y0 or x1 <= x0:
        return None
    ys, xs = _coords(roi)
    inside = ((xs - np.float32(px)) ** 2 + (ys - np.float32(py)) ** 2) <= np.float32(reach * reach)
    touching = [(p.z, index, p.id) for index, p in enumerate(planes) if ((p.alpha[y0:y1, x0:x1] > 0.5) & inside).any()]
    return max(touching)[2] if touching else None


def _host(attach, px, py, reach, planes, size):
    """emitter が載る層: attach が plane の id ならその plane、"background" なら背景、未指定なら _auto_host。"""
    if attach == "background":
        return None
    if attach is not None:
        return attach
    return _auto_host(px, py, reach, planes, size)


def prepare(data, assets, size, planes):
    """spec の lights から静的な光の形を作る。planes は layers.Plane の列（光源の載る層の判定用）。"""
    width, height = size
    kpx = width / 1920.0
    box = assets.box
    scale = width * width_scale(box)      # 元画像の幅 1.0 = 出力の何画素か（長さの変換）
    rims = {}
    lights = []
    for spec in data["lights"]:
        u, v = to_screen(box, *spec["position"])
        px, py = u * width, v * height
        radius = spec["radius"] * scale
        use_depth = spec["depth"] is not None and assets.depth is not None
        roi_main = _roi(px, py, radius, radius, size)
        roi_spec = _roi(px, py, REFLECT_RADIUS * radius, REFLECT_RADIUS * radius / REFLECT_SQUASH, size)
        emitter = spec["emitter"]
        emitter_px = 0.0 if emitter is None else emitter["size"] * scale
        reach = EMITTER_REACH * emitter_px
        roi_glow = _roi(px, py, reach, reach, size)
        light = Light(spec["id"], spec, color_to_linear(spec["color"]),
                      timeline.flicker_phases(data["seed"], spec["id"]), roi_main, roi_spec, roi_glow, None,
                      host=_host(None if emitter is None else emitter["attach"], px, py, reach, planes, size))
        if emitter is not None and reach > 0:
            ys, xs = _coords(roi_glow)
            q = ((xs - np.float32(px)) ** 2 + (ys - np.float32(py)) ** 2) / np.float32(emitter_px ** 2)
            window = np.clip(np.float32(1.0) - q / np.float32(EMITTER_REACH ** 2), np.float32(0.0), np.float32(1.0))
            core = np.exp(-q) + np.float32(emitter["glow"]) * (np.float32(1.0) + q) ** np.float32(-1.5)
            light.glow_w = (core * window * window).astype(np.float32)
        field_main = _field(roi_main, px, py, radius)
        field_spec = _field(roi_spec, px, py, REFLECT_RADIUS * radius, REFLECT_SQUASH)
        for receiver in spec["receivers"]:
            mask = assets.masks[receiver["mask"]]
            rt = Receiver(receiver["mask"])
            gate_main = mask[roi_main[0]:roi_main[1], roi_main[2]:roi_main[3]]
            gate_spec = mask[roi_spec[0]:roi_spec[1], roi_spec[2]:roi_spec[3]]
            if use_depth:
                gate_main = gate_main * _depth_gate(assets.depth[roi_main[0]:roi_main[1], roi_main[2]:roi_main[3]],
                                                    spec["depth"], spec["depth_falloff"])
                gate_spec = gate_spec * _depth_gate(assets.depth[roi_spec[0]:roi_spec[1], roi_spec[2]:roi_spec[3]],
                                                    spec["depth"], spec["depth_falloff"])
            if receiver["gain"] > 0:
                rt.diffuse_w = (field_main * gate_main * np.float32(receiver["gain"] * EXPOSURE)).astype(np.float32)
            if receiver["reflection"] > 0:
                rt.spec_w = (field_spec * gate_spec * np.float32(receiver["reflection"])).astype(np.float32)
            if receiver["rim"] > 0:
                if receiver["mask"] not in rims:
                    rims[receiver["mask"]] = _rim_geometry(mask, kpx)
                rt.rim = _rim_points(rims[receiver["mask"]], px, py, radius, receiver["rim"], roi_main,
                                     assets.depth if use_depth else None, spec)
            light.receivers.append(rt)
        lights.append(light)
    return lights


def _rim_points(geometry, px, py, radius, strength, roi, depth, spec):
    ys, xs, band, nx, ny = geometry
    y0, y1, x0, x1 = roi
    inside = (ys >= y0) & (ys < y1) & (xs >= x0) & (xs < x1)
    ys, xs, band, nx, ny = ys[inside], xs[inside], band[inside], nx[inside], ny[inside]
    dx = np.float32(px) - (xs.astype(np.float32) + np.float32(0.5))
    dy = np.float32(py) - (ys.astype(np.float32) + np.float32(0.5))
    dist2 = dx * dx + dy * dy
    facing = np.float32(0.5) + np.float32(0.5) * (nx * dx + ny * dy) / np.sqrt(dist2 + np.float32(1e-6))
    weight = falloff(dist2 / np.float32(radius * radius)) * band * facing * facing * np.float32(strength)
    if depth is not None:
        weight = weight * _depth_gate(depth[ys, xs], spec["depth"], spec["depth_falloff"])
    keep = weight > 0
    return ys[keep], xs[keep], weight[keep].astype(np.float32)


def levels(lights, t):
    """時刻 t に光っている光の [(light, level)]。level = intensity(t) × flicker(t) > 0 のものだけ。"""
    out = []
    for light in lights:
        spec = light.spec
        level = timeline.interp_scalar(spec["intensity"], t) * timeline.flicker(
            t, spec["flicker"]["amount"], spec["flicker"]["hz"], light.phases)
        if level > 0:
            out.append((light, level))
    return out


def _union(a, b):
    if a is None:
        return b
    return (min(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]))


def compose(active, albedo, size, mask_ids=None, glow_for=None):
    """光の加算量 (H, W, 3) と、それが 0 でない範囲 roi を返す（光がなければ (None, None)）。

    active:   levels() の結果。
    albedo:   diffuse を掛ける地の色（線形 H×W×3）。plane 用は基準画像、背景用は穴埋めした板。
    mask_ids: 受光に使うマスク id の集合。None なら全受光、空集合なら受光なし。
    glow_for: None なら発光なし、"all" なら全光源、集合ならその light id だけ。
    """
    width, height = size
    add = None
    roi = None
    for light, level in active:
        lc = (light.color * np.float32(level)).astype(np.float32)
        for rt in light.receivers:
            if mask_ids is not None and rt.mask_id not in mask_ids:
                continue
            if rt.diffuse_w is None and rt.spec_w is None and rt.rim is None:
                continue
            if add is None:
                add = np.zeros((height, width, 3), dtype=np.float32)
            if rt.diffuse_w is not None:
                y0, y1, x0, x1 = light.roi_main
                add[y0:y1, x0:x1] += albedo[y0:y1, x0:x1] * (rt.diffuse_w[..., None] * lc)
                roi = _union(roi, light.roi_main)
            if rt.spec_w is not None:
                y0, y1, x0, x1 = light.roi_spec
                add[y0:y1, x0:x1] += rt.spec_w[..., None] * lc
                roi = _union(roi, light.roi_spec)
            if rt.rim is not None:
                ys, xs, weight = rt.rim
                add[ys, xs] += weight[:, None] * lc
                roi = _union(roi, light.roi_main)
        if light.glow_w is not None and glow_for is not None and (glow_for == "all" or light.id in glow_for):
            if add is None:
                add = np.zeros((height, width, 3), dtype=np.float32)
            y0, y1, x0, x1 = light.roi_glow
            add[y0:y1, x0:x1] += light.glow_w[..., None] * lc
            roi = _union(roi, light.roi_glow)
    return add, roi


def hosted_glow(active, plate, size, light_ids):
    """plane に載った光点の発光 (H, W, 3)。plane の色に足して一緒に動かす加算分（None = 発光なし）。

    flat 経路（light 版）と同じソフトニーを通す: 基準画像 plate に対して soft_add した差分
    （knee(plate + glow) − knee(plate)）を返すので、plane の不透明部分では light 版と同じ芯の見え方になる。
    """
    add, roi = compose(active, plate, size, set(), light_ids)
    if add is None:
        return None
    y0, y1, x0, x1 = roi
    out = np.zeros_like(add)
    out[y0:y1, x0:x1] = soft_add(plate, add, roi)[y0:y1, x0:x1] - plate[y0:y1, x0:x1]
    return out
