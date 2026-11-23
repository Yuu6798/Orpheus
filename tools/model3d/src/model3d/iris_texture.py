"""設定画から色を測り、瞳と白目を 1 枚のアトラスに描く。"""

import math

import numpy as np
from PIL import Image, ImageFilter

from .io import ToolError


def sample_swatches(image, sample):
    """中心・半径で指定した画素を取り、採色ごとのチャンネル中央値を求める。"""
    source = image.astype(np.float32) / 255.0
    height, width = source.shape[:2]
    cx, cy = sample["centre"]
    radius = sample["radius"]
    swatches, positions = {}, {}
    for name, offsets in sample["swatches"].items():
        pixels = []
        for x, y in offsets:
            px, py = cx + x * radius, cy + y * radius
            if not math.isfinite(px) or not math.isfinite(py):
                raise ToolError(f"採色 {name!r} の座標を計算できません")
            px, py = round(px), round(py)
            if not (0 <= px < width and 0 <= py < height):
                raise ToolError(f"採色 {name!r} の位置 {[px, py]} が設定画の外です")
            pixels.append([px, py])
        swatches[name] = np.median([source[py, px, :3] for px, py in pixels], axis=0)
        positions[name] = pixels
    return swatches, positions


def _smooth(value, edge):
    low, high = edge
    position = np.clip((value - low) / (high - low), 0.0, 1.0)
    return position ** 2 * (3.0 - 2.0 * position)


def _layer_weight(layer, x, y, distance):
    kind, edge, strength = layer["kind"], layer["edge"], layer["strength"]
    if kind == "band":
        return _smooth(-(y + layer["curvature"] * x * x), edge) * strength
    if kind == "ellipse":
        cx, cy = layer["centre"]
        rx, ry = layer["radius"]
        ellipse = np.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)
        return (1.0 - _smooth(ellipse, edge)) * strength
    if kind == "ring":
        return _smooth(distance, edge) * strength
    return np.exp(-((distance - layer["radius"]) / layer["width"]) ** 2) * strength * _smooth(-y, edge)


def _color(value, swatches):
    return swatches[value] if isinstance(value, str) else np.asarray(value, dtype=np.float32) / 255.0


def draw_iris(settings, swatches):
    size = settings["size"]
    radius = size * settings["radius"]
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    x, y = (xx + 0.5 - size / 2.0) / radius, (yy + 0.5 - size / 2.0) / radius
    distance = np.sqrt(x * x + y * y)
    color = np.broadcast_to(_color(settings["base"], swatches), (size, size, 3)).copy()
    for layer in settings["layers"]:
        weight = _layer_weight(layer, x, y, distance)[..., None]
        paint = _color(layer["color"], swatches) * layer["color_scale"]
        color = color * (1.0 - weight) + paint * weight
    picture = Image.fromarray((np.clip(color, 0, 1) * 255).astype(np.uint8))
    if settings["blur"] > 0:
        picture = picture.filter(ImageFilter.GaussianBlur(settings["blur"]))
    return np.asarray(picture)


def draw_sclera(settings):
    width, height = settings["size"]
    top = np.asarray(settings["dark"], dtype=np.float32)
    bottom = np.asarray(settings["light"], dtype=np.float32)
    t = _smooth(np.linspace(0, 1, height), settings["gradient"])[:, None]
    color = top * (1.0 - t) + bottom * t
    return np.broadcast_to(color[:, None, :], (height, width, 3)).astype(np.uint8)


def draw_atlas(reference, settings):
    swatches, positions = sample_swatches(reference, settings["sample"])
    iris = draw_iris(settings["iris"], swatches)
    sclera = draw_sclera(settings["sclera"])
    size = iris.shape[0]
    height_s, width_s = sclera.shape[:2]
    gap = settings["padding"]
    width, height = size + width_s + 4 * gap, max(size, height_s) + 2 * gap
    atlas = np.zeros((height, width, 3), dtype=np.uint8)
    for left, patch in ((0, iris), (size + 2 * gap, sclera)):
        padded = np.pad(patch, ((gap, gap), (gap, gap), (0, 0)), mode="edge")
        atlas[:padded.shape[0], left:left + padded.shape[1]] = padded
    iris_centre = gap + size / 2.0
    radius = settings["iris"]["radius"] * size
    sclera_left = size + 3 * gap
    report = {
        "schema_version": "model3d-iris-atlas/0.1", "size": [width, height],
        "iris_rect": [gap, gap, size, size], "sclera_rect": [sclera_left, gap, width_s, height_s],
        "iris_uv": [(iris_centre - radius) / width, 1.0 - (iris_centre + radius) / height,
                    (iris_centre + radius) / width, 1.0 - (iris_centre - radius) / height],
        "plate_uv": [(sclera_left + width_s / 2.0) / width, 1.0 - (gap + 0.5) / height,
                     1.0 - (gap + height_s - 0.5) / height],
        "swatches": {name: {"pixels": positions[name], "rgb": (color * 255.0).tolist()} for name, color in swatches.items()},
    }
    return atlas, report
