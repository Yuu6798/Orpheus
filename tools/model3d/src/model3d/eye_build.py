"""目の開きの測定と、白目の板・瞳の形を作る（numpy）。"""

import math

import numpy as np

from .blender_adapter.eye_math import GeometryError, _indices, _vector, sphere_y
from .face_shapes import raster_front


DISTANCES = {"resolution", "grid", "clearance", "plate_margin", "behind_skin", "wall_margin", "rim_search",
             "shade_depth", "iris_lift"}
BUILD_KEYS = DISTANCES | {"object", "head_group", "material", "front_y", "eyes", "plate_uv", "iris_uv", "iris_rings",
                          "iris_segments", "layout", "skin_vertex", "rim_outlier"}


def number(value, label, minimum=None):
    value, = _vector([value], label, 1)
    if minimum is not None and value <= minimum:
        raise GeometryError(f"{label} は {minimum} より大きくしてください")
    return value


def validate_settings(config):
    for key in ("object", "head_group", "material"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise GeometryError(f"{key} に名前を指定してください")
    for key in sorted(DISTANCES):
        if key == "wall_margin":
            if number(config.get(key, 0), key) < 0:
                raise GeometryError(f"{key} は 0 以上にしてください")
        else:
            number(config.get(key), key, 0)
    number(config.get("front_y"), "front_y")
    if config.get("rim_outlier") is not None:
        number(config["rim_outlier"], "rim_outlier", 0)
    if config.get("skin_vertex") is not None:
        _indices([config["skin_vertex"]], "skin_vertex")
    if config.get("layout", "projected") not in ("projected", "tangent"):
        raise GeometryError("layout は projected か tangent にしてください")
    for key, length in (("plate_uv", 3), ("iris_uv", 4)):
        if any(not 0 <= x <= 1 for x in _vector(config.get(key), key, length)):
            raise GeometryError(f"{key} の座標は 0〜1 にしてください")
    u0, v0, u1, v1 = config["iris_uv"]
    if u0 >= u1 or v0 >= v1:
        raise GeometryError("iris_uv の範囲を [u0, v0, u1, v1] の増える順にしてください")
    segments, rings = config.get("iris_segments"), config.get("iris_rings")
    if type(segments) is not int or not 3 <= segments <= 1024:
        raise GeometryError("iris_segments は 3〜1024 の整数にしてください")
    if not isinstance(rings, list) or not 1 <= len(rings) <= 128:
        raise GeometryError("iris_rings に輪の半径の割合を並べてください")
    rings = _vector(rings, "iris_rings", len(rings))
    if rings[-1] != 1 or any(a >= b for a, b in zip([0.0, *rings[:-1]], rings)):
        raise GeometryError("iris_rings は正の数の増える順で、最後を 1 にしてください")
    eyes = config.get("eyes")
    if not isinstance(eyes, dict) or set(eyes) != {"R", "L"}:
        raise GeometryError("eyes に R・L の設定を指定してください")
    for side, eye in eyes.items():
        label = f"eyes.{side}"
        if not isinstance(eye, dict) or eye.keys() - {"window", "remove", "pupil", "iris", "radius", "lid_vertices",
                                                     "fit_iris_vertices", "plate_removed_margin", "pitch_limit"}:
            raise GeometryError(f"{label} の設定を確かめてください")
        x0, x1, z0, z1 = _vector(eye.get("window"), f"{label}.window", 4)
        x, z = _vector(eye.get("pupil"), f"{label}.pupil", 2)
        if not (x0 < x < x1 and z0 < z < z1):
            raise GeometryError(f"{label}.pupil は window の内側にしてください")
        if min(_vector(eye.get("iris"), f"{label}.iris", 2)) <= 0:
            raise GeometryError(f"{label}.iris の半径を正の数にしてください")
        number(eye.get("radius"), f"{label}.radius", 0)
        remove = eye.get("remove")
        if not isinstance(remove, dict) or set(remove) not in ({"component_size"}, {"vertices"}):
            raise GeometryError(f"{label}.remove に component_size か vertices を指定してください")
        if "component_size" in remove and (type(remove["component_size"]) is not int or remove["component_size"] < 3):
            raise GeometryError(f"{label}.remove.component_size は 3 以上にしてください")
        if "vertices" in remove and not _indices(remove["vertices"], f"{label}.remove.vertices"):
            raise GeometryError(f"{label}.remove.vertices を指定してください")
        for key in ("lid_vertices", "fit_iris_vertices"):
            if key in eye:
                _indices(eye[key], f"{label}.{key}")
        if "fit_iris_vertices" in eye and len(eye["fit_iris_vertices"]) < 3:
            raise GeometryError(f"{label}.fit_iris_vertices を 3 頂点以上にしてください")
        if "pitch_limit" in eye and not (0 <= number(eye["pitch_limit"], f"{label}.pitch_limit") < 90):
            raise GeometryError(f"{label}.pitch_limit は 0 以上 90 未満の度数にしてください")
        if "plate_removed_margin" in eye and number(eye["plate_removed_margin"], f"{label}.plate_removed_margin") < 0:
            raise GeometryError(f"{label}.plate_removed_margin は 0 以上にしてください")
        widths = [(x1 - x0) / config["resolution"], (z1 - z0) / config["resolution"]]
        if min(widths) < 3 or math.prod(widths) > 4_000_000:
            raise GeometryError(f"{label} の window と resolution で作る絵の大きさを確かめてください")
    return config


def in_window(points, window, front_y):
    x0, x1, z0, z1 = window
    x, y, z = points.T
    return (x > x0) & (x < x1) & (z > z0) & (z < z1) & (y < front_y)


def removal(head, eye, skin, front_y):
    spec = eye["remove"]
    if "vertices" in spec:
        ids = _indices(spec["vertices"], "remove.vertices", head.n)
        if not in_window(head.co[ids], eye["window"], front_y).all():
            raise GeometryError("取り除く頂点を窓の内側と front_y の手前にしてください")
        return np.asarray(ids, dtype=int)
    candidates = []
    for component in np.flatnonzero(np.bincount(head.island_of) == spec["component_size"]):
        points = np.flatnonzero(head.island_of == component)
        if not skin[points[0]] and in_window(head.co[points].mean(axis=0)[None], eye["window"], front_y)[0]:
            candidates.append(points)
    if len(candidates) != 1:
        raise GeometryError(f"取り除く島を 1 つに絞れません（{len(candidates)} 個）")
    return candidates[0]


def opening_data(opening, skin_depth, window, grid, search):
    x0, _, _, z1 = window
    cols = np.flatnonzero(opening.any(axis=0))
    if len(cols) < 3:
        raise GeometryError("目の開きを 3 列以上で測れません")
    present = opening[:, cols]
    top = np.argmax(present, axis=0)
    bottom = len(opening) - 1 - np.argmax(present[::-1], axis=0)
    x = x0 + (cols + 0.5) * grid
    rows = max(1, math.ceil(min(len(opening), search / grid)))
    low_points, high_points = [], []
    for col, xc, t, b in zip(cols, x, top, bottom):
        if b <= t:
            continue
        for edge, direction, points in ((b, 1, low_points), (t, -1, high_points)):
            nearby = edge + direction * np.arange(1, rows + 1)
            nearby = nearby[(nearby >= 0) & (nearby < len(opening))]
            values = skin_depth[nearby, col]
            if np.isfinite(values).any():
                k = int(np.argmin(values))
                points.append([xc, values[k], z1 - (nearby[k] + 0.5) * grid])
    if len(low_points) < 3 or len(high_points) < 3:
        raise GeometryError("開きの上下で、まぶたのふちを 3 点以上ずつ測れません")
    return x, z1 - top * grid, z1 - (bottom + 1) * grid, np.array(low_points), np.array(high_points)


def plane(points):
    centre = points.mean(axis=0)
    _, lengths, axes = np.linalg.svd(points - centre, full_matrices=False)
    if lengths[1] <= np.finfo(float).eps:
        raise GeometryError("まぶたの点から平面を求められません")
    normal = axes[-1]
    return centre, normal if normal[1] <= 0 else -normal


def fit_sphere(head, eye, low, high, config, removed):
    radius = eye["radius"]
    if "fit_iris_vertices" in eye:
        ids = _indices(eye["fit_iris_vertices"], "fit_iris_vertices", head.n)
        if not set(ids).issubset(removed):
            raise GeometryError("fit_iris_vertices は取り除く頂点に含めてください")
        iris = head.co[ids]
        _, iris_normal = plane(iris)
        limit = math.radians(eye.get("pitch_limit", 89))
        pitch = np.clip(np.arcsin(iris_normal[2]), -limit, limit)
        k = max(1, len(low) // 20)
        chord = low[-k:].mean(axis=0) - low[:k].mean(axis=0)
        horizontal = np.array([chord[1], -chord[0], 0])
        normal = math.cos(pitch) * horizontal / np.linalg.norm(horizontal) + [0, 0, math.sin(pitch)]
        x, _, z = iris.mean(axis=0)
        anchor = np.array([x, np.interp(x, low[:, 0], low[:, 1]), z])
    else:
        points = np.concatenate((low, high))
        anchor, normal = plane(points)
        tolerance = config.get("rim_outlier")
        if tolerance is not None:
            for _ in range(3):
                keep = np.abs((points - anchor) @ normal) <= tolerance
                if keep.sum() < 3 or not keep[:len(low)].any():
                    raise GeometryError("rim_outlier の範囲にまぶたのふちが残りません")
                anchor, normal = plane(points[keep])
            low = low[keep[:len(low)]]
    if normal[1] >= -1e-6:
        raise GeometryError("目の板が正面を向きません")
    centre = anchor - radius * normal
    for _ in range(4):
        gap = np.linalg.norm(low - centre, axis=1) - radius
        centre -= (config["clearance"] - gap.min()) * normal
    return centre, normal


def dilated(mask, distance):
    height, width = mask.shape
    if distance >= math.hypot(height - 1, width - 1):
        return np.full_like(mask, mask.any())
    result = mask.copy()
    prefix = np.pad(np.cumsum(mask, axis=1, dtype=np.int32), ((0, 0), (1, 0)))
    max_rows = min(height - 1, math.floor(distance))
    cols = np.arange(width)
    for row in range(-max_rows, max_rows + 1):
        reach = math.floor(math.sqrt(max(0, distance * distance - row * row)))
        other = prefix[max(0, -row):height + min(0, -row)]
        filled = other[:, np.minimum(width, cols + reach + 1)] > other[:, np.maximum(0, cols - reach)]
        result[max(0, row):height + min(0, row)] |= filled
    return result


def front_points(x, z, centre, radius):
    return np.column_stack((x, sphere_y(centre, radius, x, z), z))


def plate_grid(region, window, resolution, grid, centre, radius, normal, axes, layout):
    height, width = region.shape
    x0, _, _, z1 = window
    if layout == "projected":
        step = max(1, round(grid / resolution))
        cols, rows = np.meshgrid(np.arange(0, width, step), np.arange(height - 1, -1, -step))
        points = front_points(x0 + (cols.ravel() + 0.5) * resolution, z1 - (rows.ravel() + 0.5) * resolution,
                              centre, radius)
        valid = np.linalg.norm(points - centre, axis=1) <= radius + 1e-10
        return points, rows, cols, valid
    rows, cols = np.nonzero(region)
    sample = front_points(x0 + (cols + 0.5) * resolution, z1 - (rows + 0.5) * resolution, centre, radius) - centre
    dividing = sample @ normal
    valid = (dividing > 0) & (np.linalg.norm(sample, axis=1) <= radius + 1e-10)
    if not valid.any():
        raise GeometryError("目の開きに球の前面が届きません")
    projected = (sample[valid] @ axes.T) * (radius / dividing[valid, None])
    extent = projected.max(axis=0) - projected.min(axis=0)
    if math.prod(extent / grid + 3) > 4_000_000:
        raise GeometryError("板の網目が大きすぎます。grid・window を確かめてください")
    uu, vv = np.meshgrid(*(np.arange(lo - grid, hi + 2 * grid, grid)
                          for lo, hi in zip(projected.min(axis=0), projected.max(axis=0))))
    offsets = radius * normal + np.column_stack((uu.ravel(), vv.ravel())) @ axes
    points = centre + radius * offsets / np.linalg.norm(offsets, axis=1)[:, None]
    cols = np.floor((points[:, 0] - x0) / resolution).astype(int).reshape(uu.shape)
    rows = np.floor((z1 - points[:, 2]) / resolution).astype(int).reshape(uu.shape)
    valid = ((rows >= 0) & (rows < height) & (cols >= 0) & (cols < width)).ravel()
    return points, rows.clip(0, height - 1), cols.clip(0, width - 1), valid


def plate_mesh(lid_depth, removed_depth, opening, window, centre, radius, normal, axes, rim, config, eye):
    resolution = config["resolution"]
    region = dilated(opening, config["plate_margin"] / resolution)
    if "plate_removed_margin" in eye:
        region |= dilated(np.isfinite(removed_depth), eye["plate_removed_margin"] / resolution)
    points, rows, cols, valid = plate_grid(region, window, resolution, config["grid"], centre, radius, normal, axes,
                                          config.get("layout", "projected"))
    keep = region[rows, cols] & valid.reshape(rows.shape)
    quads = keep[:-1, :-1] & keep[:-1, 1:] & keep[1:, 1:] & keep[1:, :-1]
    if not quads.any():
        raise GeometryError("目の板の面を作れません。球の位置、grid と開きを確かめてください")
    indices = np.flatnonzero(quads)
    start = (indices // quads.shape[1]) * keep.shape[1] + indices % quads.shape[1]
    faces = start[:, None] + [0, 1, keep.shape[1] + 1, keep.shape[1]]
    used, mapped = np.unique(faces, return_inverse=True)
    points, r, c = points[used], rows.ravel()[used], cols.ravel()[used]
    wall = opening.copy()
    wall_rows = math.ceil(min(len(opening) - 1, config.get("wall_margin", 0) / resolution))
    for step in range(1, wall_rows + 1):
        wall[step:] |= opening[:-step]
        wall[:-step] |= opening[step:]
    limit = lid_depth[r, c] + config["behind_skin"]
    bent = ~wall[r, c] & np.isfinite(limit) & (points[:, 1] < limit)
    points[bent, 1] = limit[bent]
    top = np.interp(points[:, 0], rim[0], rim[1])
    weight = np.clip((top - points[:, 2]) / config["shade_depth"], 0, 1)
    u, dark, light = config["plate_uv"]
    uvs = np.column_stack((np.full(len(points), u), dark + weight * (light - dark)))
    return points, uvs, mapped.reshape(-1, 4), bent


def iris_mesh(eye, config, centre, axes):
    segments = config["iris_segments"]
    t = 2 * math.pi * np.arange(segments) / segments
    offsets = np.concatenate(([[0, 0]], (np.array(config["iris_rings"])[:, None, None]
                            * np.column_stack((np.cos(t), np.sin(t)))).reshape(-1, 2)))
    radii = offsets * eye["iris"]
    radius = eye["radius"] + config["iris_lift"]
    pupil = np.asarray(eye["pupil"])
    xyz = radii + pupil
    points = front_points(*xyz.T, centre, radius)
    if config.get("layout") == "tangent":
        unit = points[0] - centre
        if abs(np.linalg.norm(unit) - radius) > 1e-10:
            raise GeometryError("瞳の中心が球の範囲から外れています")
        unit /= radius
        across = axes[0] - (axes[0] @ unit) * unit
        across /= np.linalg.norm(across)
        up = np.cross(unit, across)
        offsets_3d = radius * unit + radii @ np.stack((across, up))
        points = centre + radius * offsets_3d / np.linalg.norm(offsets_3d, axis=1)[:, None]
    if (np.linalg.norm(points - centre, axis=1) > radius + 1e-10).any():
        raise GeometryError("瞳が球の範囲から外れています")
    u0, v0, u1, v1 = config["iris_uv"]
    uvs = [u0, v0] + (offsets + 1) * [(u1 - u0) / 2, (v1 - v0) / 2]
    s = np.arange(segments)
    next_s = (s + 1) % segments
    fan = np.column_stack((np.zeros(segments, dtype=int), s + 1, next_s + 1))
    rings = (np.arange(len(config["iris_rings"]) - 1) * segments + 1)[:, None, None]
    panels = rings + np.column_stack((s, s + segments, next_s + segments, next_s))
    return points, uvs, fan, panels.reshape(-1, 4)


def removal_triangles(triangles, polygon_indices, vertices):
    # BMesh は頂点に接する元の面を丸ごと消すので、四角や N ゴンは分割した三角形を全部選ぶ。
    touched = np.isin(triangles, vertices).any(axis=1)
    return np.isin(polygon_indices, polygon_indices[touched])


def make_eye(head, eye, config, skin, previous_removed, polygon_indices):
    window, resolution, front_y = eye["window"], config["resolution"], config["front_y"]
    gone = removal(head, eye, skin, front_y)
    if set(gone) & set(previous_removed):
        raise GeometryError("左右で取り除く頂点が重なっています")
    removed_face = removal_triangles(head.tris, polygon_indices, gone)
    if not removed_face.any():
        raise GeometryError("取り除く頂点に面がありません")
    tris = head.tris
    _, lid_depth = raster_front(head.co, tris[~removed_face], *window, resolution, front_y)
    _, skin_depth = raster_front(head.co, tris[~removed_face & skin[tris].all(axis=1)], *window, resolution, front_y)
    _, removed_depth = raster_front(head.co, tris[removed_face], *window, resolution, front_y)
    opening = np.isfinite(removed_depth) & (removed_depth < lid_depth)
    x, upper, lower, low, high = opening_data(opening, skin_depth, window, resolution, config["rim_search"])
    centre, normal = fit_sphere(head, eye, low, high, config, gone)
    across = np.cross([0, 0, 1], normal)
    across /= np.linalg.norm(across)
    axes = np.stack((across, np.cross(normal, across)))
    plate = plate_mesh(lid_depth, removed_depth, opening, window, centre, eye["radius"], normal, axes,
                       (x, upper, lower), config, eye)
    iris = iris_mesh(eye, config, centre, axes)
    skin_indices = np.flatnonzero(skin & in_window(head.co, window, front_y) & ~np.isin(np.arange(head.n), gone))
    if plate[3].any() and len(skin_indices) < 3:
        raise GeometryError("板の端に形状キーを写す肌の頂点が 3 つありません")
    lids = eye.get("lid_vertices")
    lids = skin_indices if lids is None else np.asarray(_indices(lids, "lid_vertices", head.n), dtype=int)
    return {"remove": gone, "lids": lids, "skin": skin_indices, "plate": plate, "iris": iris,
            "sphere": {"centre": centre.tolist(), "radius": eye["radius"], "normal": normal.tolist()},
            "rim": {"x": x.tolist(), "upper": upper.tolist(), "lower": lower.tolist()},
            "opening_pixels": int(opening.sum())}
