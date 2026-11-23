"""指定した光線が当たる面を近い順に調べる。Blender の入力は保存しない。"""

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np

if __package__:
    from .mesh_export import to_world
    from .model_edit import islands, mesh_edges
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from mesh_export import to_world
    from model_edit import islands, mesh_edges


def ray_intersections(points, origin, direction):
    """光線と三角形 (T, 3, 3) の交点の番号・距離・重みを、両面について返す。"""
    v0 = points[:, 0]
    first, second = points[:, 1] - v0, points[:, 2] - v0
    cross = np.cross(direction, second)
    determinant = np.einsum("ij,ij->i", first, cross)
    scale = np.linalg.norm(first, axis=1) * np.linalg.norm(second, axis=1)
    nonparallel = np.abs(determinant) > 1e-12 * scale
    divisor = np.where(nonparallel, determinant, 1.0)
    delta = origin - v0
    u = np.einsum("ij,ij->i", delta, cross) / divisor
    other_cross = np.cross(delta, first)
    v = (other_cross @ direction) / divisor
    distance = np.einsum("ij,ij->i", second, other_cross) / divisor
    weights = np.column_stack((1.0 - u - v, u, v))
    hit = np.flatnonzero(nonparallel & (distance >= 0.0) & (weights >= -1e-10).all(axis=1))
    hit = hit[np.argsort(distance[hit], kind="stable")]
    chosen = weights[hit].clip(0.0, 1.0)
    return hit, distance[hit], chosen / chosen.sum(axis=1, keepdims=True)


def _texture_node(material):
    if material is None or material.node_tree is None:
        return None
    nodes = material.node_tree.nodes
    node = nodes.get("Mtoon1BaseColorTexture.Image")
    if node is not None and getattr(node, "image", None) is not None:
        return node
    # 通常材質では Principled のベース色へ直につながる画像を読む。
    images = [link.from_node for node in nodes if node.type == "BSDF_PRINCIPLED"
              for link in node.inputs["Base Color"].links if link.from_node.type == "TEX_IMAGE" and link.from_node.image]
    return images[0] if len(images) == 1 else None


def texture_sample(node, uv):
    if node is None or uv is None:
        return None
    image = node.image
    width, height = image.size
    channels = image.channels
    if width == 0 or height == 0 or channels == 0:
        return None
    if node.extension == "REPEAT":
        uv = [value % 1.0 for value in uv]
    elif node.extension == "MIRROR":
        uv = [1.0 - abs(value % 2.0 - 1.0) for value in uv]
    elif node.extension == "CLIP" and any(not 0.0 <= value <= 1.0 for value in uv):
        return None
    x, y = (min(size - 1, max(0, math.floor(value * size))) for value, size in zip(uv, (width, height)))
    start = (y * width + x) * channels
    color = list(image.pixels[start:start + min(3, channels)])
    if not color or not all(math.isfinite(value) for value in color):
        return None
    if len(color) < 3:
        color = [color[0]] * 3
    return {"image": image.name, "pixel": [x, y], "rgb": color, "color_space": image.colorspace_settings.name}


def mesh_hits(obj, mesh, rays, unit):
    mesh.calc_loop_triangles()
    if not mesh.loop_triangles:
        return [[] for _ in rays]
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    world = to_world(coords.reshape(-1, 3), np.asarray(obj.matrix_world), unit)
    triangles = np.array([record.vertices[:] for record in mesh.loop_triangles], dtype=np.int64)
    points = world[triangles]
    layer = mesh.uv_layers.active
    if layer is not None:
        values = np.empty(len(layer.data) * 2, dtype=np.float64)
        layer.data.foreach_get("uv", values)
        uv = values.reshape(-1, 2)
    else:
        uv = None
    components = islands(len(mesh.vertices), mesh_edges(mesh))
    counts = np.bincount(components)
    textures = {}
    all_hits = []
    for ray in rays:
        ids, distances, weights = ray_intersections(points, np.asarray(ray["origin"]), np.asarray(ray["direction"]))
        hits, last_distance = [], {}
        for tri_id, distance, bary in zip(ids, distances, weights):
            tri = mesh.loop_triangles[int(tri_id)]
            face = mesh.polygons[tri.polygon_index]
            if face.index in last_distance and math.isclose(distance, last_distance[face.index], rel_tol=1e-12, abs_tol=1e-12):
                continue  # 四角形などの分割線上の同じ交点は同じ面として 1 つにする
            last_distance[face.index] = distance
            slot = face.material_index
            override = obj.material_slots[slot] if slot < len(obj.material_slots) else None
            material = mesh.materials[slot] if slot < len(mesh.materials) else None
            if override is not None and override.link == "OBJECT":
                material = override.material
            if slot not in textures:
                textures[slot] = _texture_node(material)
            hit_uv = bary @ uv[list(tri.loops)] if uv is not None else None
            vertex_points = points[tri_id]
            normal = np.cross(vertex_points[1] - vertex_points[0], vertex_points[2] - vertex_points[0])
            normal /= np.linalg.norm(normal)
            hits.append({"distance_m": float(distance), "position_m": (bary @ vertex_points).tolist(),
                         "object": obj.name, "face": face.index, "material": material.name if material is not None else None,
                         "face_centre_m": world[list(face.vertices)].mean(axis=0).tolist(), "normal_world": normal.tolist(),
                         "island_vertices": int(counts[components[face.vertices[0]]]),
                         "uv": hit_uv.tolist() if hit_uv is not None else None,
                         "face_uv_centre": uv[list(face.loop_indices)].mean(axis=0).tolist() if uv is not None else None,
                         "texture": texture_sample(textures[slot], hit_uv)})
        all_hits.append(hits)
    return all_hits


def _set_keys(objects, settings):
    grouped = {}
    for obj in objects:
        obj.show_only_shape_key = False
        values = settings.get(obj.name, {})
        if not values:
            continue
        keys = obj.data.shape_keys
        if keys is None or not keys.use_relative:
            raise ValueError(f"{obj.name} に相対の形状キーがありません")
        _, chosen = grouped.setdefault(keys.as_pointer(), (keys, {}))
        for name, value in values.items():
            block = keys.key_blocks.get(name)
            if block is None or name == keys.reference_key.name:
                raise ValueError(f"{obj.name} に指定できる形状キーがありません: {name}")
            if name in chosen and chosen[name] != value:
                raise ValueError(f"共有されている形状キー {name} に異なる値が指定されています")
            chosen[name] = value
    for keys, chosen in grouped.values():
        if keys.animation_data is not None:
            keys.animation_data_clear()
        for name, value in chosen.items():
            block = keys.key_blocks[name]
            block.mute = False
            block.slider_min = min(block.slider_min, value)
            block.slider_max = max(block.slider_max, value)
            block.value = value


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for argument in ("blend", "settings", "report"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    scene = bpy.context.scene
    objects = [scene.objects.get(name) for name in settings["objects"]]
    absent = [name for name, obj in zip(settings["objects"], objects) if obj is None or obj.type != "MESH"]
    if absent:
        raise ValueError(f"調べるメッシュがシーンにありません: {absent}")
    _set_keys(objects, settings["keys"])
    if not settings["modifiers"]:
        for obj in objects:
            for modifier in obj.modifiers:
                modifier.show_viewport = False
    depsgraph = bpy.context.evaluated_depsgraph_get()
    unit = scene.unit_settings.scale_length
    rays = [{**ray, "hits": []} for ray in settings["rays"]]
    for obj in objects:
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            for ray, hits in zip(rays, mesh_hits(evaluated, mesh, rays, unit)):
                ray["hits"].extend(hits)
        finally:
            evaluated.to_mesh_clear()
    for ray in rays:
        ray["hits"].sort(key=lambda hit: hit["distance_m"])
    report = {"schema_version": "model3d-view-point/0.1", "rays": rays, "objects": settings["objects"],
              "keys": settings["keys"], "modifiers": settings["modifiers"], "scale_length": unit}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
