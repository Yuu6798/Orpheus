"""UV で選んだ画素を塗り、同じ絵を使う別の面を調べる。"""
import numpy as np

if __package__:
    from .model_edit import islands, world_points
    from .split_material import in_box
else:
    from model_edit import islands, world_points
    from split_material import in_box


def mesh_texture_faces(obj, width, height):
    mesh = obj.data
    mesh.calc_loop_triangles()
    if not mesh.uv_layers.active:
        return None
    uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2).astype(np.float64) * np.array([width, height])
    if not np.isfinite(uv).all():
        raise SystemExit(f"{obj.name} の UV に有限ではない値があります")
    return uv


def choose_faces(obj, slots, region, scale_length):
    triangles = list(obj.data.loop_triangles)
    if not triangles:
        return np.zeros(0, dtype=bool)
    tris = np.asarray([list(tri.vertices) for tri in triangles], dtype=np.int64)
    co = world_points(obj) * scale_length

    def selected_box(points, box):
        if region.get("mirror_x"):
            points[:, 0] = np.abs(points[:, 0])
        return in_box(points, box)

    picked = np.array([tri.material_index in slots for tri in triangles], dtype=bool)
    if region.get("box"):
        picked &= selected_box(co[tris].mean(axis=1), region["box"])
    if "island_vertices" in region:
        edges = np.asarray([list(edge.vertices) for edge in obj.data.edges], dtype=np.int32).reshape(-1, 2)
        roots = islands(len(co), edges)
        lower, upper = region["island_vertices"]
        components, counts = np.unique(roots, return_counts=True)
        keep = set()
        for component in components[(counts >= lower) & (counts <= upper)]:
            points = co[roots == component]
            if "island_box" not in region or selected_box((points.min(axis=0) + points.max(axis=0))[None, :] / 2,
                                                          region["island_box"])[0]:
                keep.add(int(component))
        picked &= np.isin(roots[tris[:, 0]], list(keep))
    return picked


def smooth(low, high, values):
    t = np.clip((values - low) / (high - low), 0, 1)
    return t * t * (3 - 2 * t)


def neighbours(values):
    # シートの上下端・左右端はつながない。
    yield (slice(1, None), slice(None)), values[:-1, :]
    yield (slice(None, -1), slice(None)), values[1:, :]
    yield (slice(None), slice(1, None)), values[:, :-1]
    yield (slice(None), slice(None, -1)), values[:, 1:]


def box_mean(values, radius):
    summed = np.pad(values, radius + 1).cumsum(axis=0).cumsum(axis=1)
    width = 2 * radius + 1
    total = summed[width:, width:] - summed[:-width, width:] - summed[width:, :-width] + summed[:-width, :-width]
    return total[:values.shape[0], :values.shape[1]]


def paint_pixels(image, face_mask, paint):
    rgb = image[..., :3].astype(np.float64)
    area = face_mask.copy()
    for _ in range(paint["pad"]):
        grown = area.copy()
        for target_slice, nearby in neighbours(area):
            grown[target_slice] |= nearby
        area = grown

    condition = paint["select"]
    mode = condition["mode"]
    if mode == "all":
        weight = face_mask.astype(np.float64)
    else:
        high, low = rgb.max(axis=2), rgb.min(axis=2)
        softness = condition["softness"]
        bound = condition["value"]
        if mode == "dark":
            weight = (1 - smooth(bound, bound + softness, high)) * area
        else:
            saturation = (high - low) / np.maximum(high, 1 / 255)
            limit = condition["max_saturation"]
            weight = (1 - smooth(limit, limit + softness, saturation)) * smooth(bound - softness, bound, high) * area

    clean = paint["clean"]
    if clean:
        denominator = np.maximum(box_mean(area.astype(np.float64), clean), 1)
        for _ in range(2):
            weight = np.where(area, box_mean(weight * area, clean) / denominator, 0)
        weight = smooth(0.35, 0.65, weight)
    strong = weight > 0.5
    if not strong.any():
        return image.copy()
    luminance = rgb @ np.array([0.2126, 0.7152, 0.0722])
    ratio = np.clip(luminance / max(float(np.median(luminance[strong])), 1 / 255), 0.25, 2.5)
    target = np.clip(np.asarray(paint["rgb"]) * ratio[..., None] ** paint["keep"], 0, 1)
    updated = rgb * (1 - weight[..., None]) + target * weight[..., None]
    if mode == "all":
        done = face_mask.copy()
        for _ in range(paint["pad"]):
            total, count = np.zeros_like(updated), np.zeros(done.shape, dtype=np.int32)
            for (target_slice, nearby), (_, colors) in zip(neighbours(done), neighbours(updated), strict=True):
                total[target_slice] += np.where(nearby[..., None], colors, 0)
                count[target_slice] += nearby
            fill = (~done) & area & (count > 0)
            updated[fill] = total[fill] / count[fill][:, None]
            done |= fill
    result = image.copy()
    modified = np.any(updated != rgb, axis=2)
    result[modified, :3] = np.rint(updated[modified] * 255) / 255
    return result
