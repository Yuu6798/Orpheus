"""唇の裏の開いた輪に、形状キーと骨の重みを引き継ぐ内壁とふたを作る。"""

import argparse
from itertools import accumulate, pairwise
import json
import math
import os
from pathlib import Path
import sys

import numpy as np

try:
    from .model_edit import islands
except ImportError:  # Blender の台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import islands


def rim_cycle(co, edges, boundary, limits):
    """大きい島に属し、指定範囲に収まる開いた辺を、左の口角から上の縁を通る順にする。"""
    if not len(co) or not len(edges):
        raise ValueError("口の縁を探せる頂点や辺がありません")
    labels = islands(len(co), edges)
    roots, sizes = np.unique(labels, return_counts=True)
    largest = roots[sizes == sizes.max()]
    if len(largest) != 1:
        raise ValueError("いちばん大きい島が 1 つに決まりません")
    z_min, z_max = limits["window"]["z"]
    x, y, z = co.T
    eligible = ((labels == largest[0]) & (np.abs(x) < limits["zone"]["half_width"])
                & (z > z_min) & (z < z_max) & (y < limits["front_y"]))
    adjacency = {}
    for a, b in edges[boundary & eligible[edges].all(axis=1)]:
        adjacency.setdefault(int(a), []).append(int(b))
        adjacency.setdefault(int(b), []).append(int(a))
    if len(adjacency) < 4 or any(len(neighbors) != 2 for neighbors in adjacency.values()):
        raise ValueError("指定した範囲の口の開いた縁が 1 本の輪になっていません")
    start = min(adjacency, key=lambda i: (co[i, 0], i))
    current = max(adjacency[start], key=lambda i: (co[i, 2], -i))
    cycle = [start]
    while current != start:
        previous = cycle[-1]
        cycle.append(current)
        a, b = adjacency[current]
        current = a if b == previous else b
    if len(cycle) != len(adjacency):
        raise ValueError("指定した範囲に開いた縁の輪が複数あります")
    return cycle


def arc_fractions(points):
    lengths = [math.dist(a, b) for a, b in pairwise(points)]
    if any(length == 0 for length in lengths):
        raise ValueError("口の縁に同じ位置の頂点が続いています")
    total = sum(lengths)
    return [length / total for length in accumulate(lengths, initial=0.0)]


def ring_angles(points):
    x = [p.x for p in points]
    left, right = min(x), max(x)
    corner = next((i for i in range(2, len(x) - 1) if x[i] == right), None)
    if right <= left or corner is None:
        raise ValueError("口の左右の端と上下の縁を決められません")
    # 元の輪を横に投影して、上と下をそれぞれ半円へ写す。
    angles = [math.pi * (1 - (value - left) / (right - left)) if i <= corner
              else math.pi * (1 + (value - left) / (right - left)) for i, value in enumerate(x)]
    # 口角が平たく、横の座標が重なったり戻ったりする縁には、輪の長さを使って頂点の間を保つ。
    if any(a >= b for a, b in pairwise(x[:corner + 1])):
        angles[:corner] = [math.pi * (1 - t) for t in arc_fractions(points[:corner + 1])[:-1]]
    if any(a <= b for a, b in pairwise(x[corner:] + x[:1])):
        angles[corner + 1:] = [math.pi * (2 - t) for t in arc_fractions(points[corner:] + points[:1])[1:-1]]
    return angles, corner


def clearance_front(objects, rule, unit):
    body = objects.get(rule["object"])
    if body is None or body.type != "MESH":
        raise ValueError(f"clearance.object のメッシュがありません: {rule['object']}")
    z_min, z_max = rule["z"]
    points = ((body.matrix_world @ vertex.co) * unit for vertex in body.data.vertices)
    front = min((p.y for p in points if abs(p.x) < rule["half_width"] and z_min < p.z < z_max), default=None)
    if front is None:
        raise ValueError("clearance の範囲に頂点がありません")
    return front


def add_bag(bm, mesh, matrix, unit, settings, slot, uv_name, clearance):
    from mathutils import Vector

    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    points = [(matrix @ v.co) * unit for v in bm.verts]
    edges = np.array([[v.index for v in edge.verts] for edge in bm.edges], dtype=np.int64)
    boundary = np.array([edge.is_boundary for edge in bm.edges], dtype=bool)
    rim = rim_cycle(np.asarray(points), edges, boundary, settings)
    rings = settings["rings"]
    rim_points = [points[i] for i in rim]
    angles, right = ring_angles(rim_points)
    n = len(rim)
    centre = sum(rim_points, Vector()) / n
    x_min, x_max = min(p.x for p in rim_points), max(p.x for p in rim_points)
    xc, zc = (x_min + x_max) / 2, centre.z - settings["drop"]
    y_rim = max(p.y for p in rim_points)
    width = (x_max - x_min) / 2 * settings["widen"]
    depth = settings["depth"]
    cap_extra = settings["cap_extra"]
    if clearance is not None:
        depth = min(depth, clearance - settings["clearance"]["gap"] - y_rim - cap_extra)
    if depth <= 0:
        raise ValueError("口の縁と clearance の間に奥行きがありません")

    inverse = matrix.inverted()
    shape_layers = list(bm.verts.layers.shape.values())
    basis = bm.verts.layers.shape.get(mesh.shape_keys.reference_key.name) if mesh.shape_keys else None
    deform = bm.verts.layers.deform.active
    uv = bm.loops.layers.uv[uv_name]
    rows = [[bm.verts[i] for i in rim]]
    rim_edges = [bm.edges.get((a, b)) for a, b in zip(rows[0], rows[0][1:] + rows[0][:1])]

    def new_vertex(point, source, follow):
        vertex = bm.verts.new(inverse @ (point / unit))
        for layer in shape_layers:
            vertex[layer] = vertex.co + (source[layer] - source[basis]) * follow
        if deform is not None:
            for group, weight in source[deform].items():
                vertex[deform][group] = weight
        return vertex

    for r in range(1, rings + 1):
        t = r / rings
        blend = t * t * (3 - 2 * t)
        y = y_rim + depth * t ** 0.8
        follow = (1 - t) ** 1.5
        scale = settings["last_ring_scale"] if r == rings else 1.0
        row = []
        for point, angle, source in zip(rim_points, angles, rows[0]):
            ellipse = Vector((xc + width * scale * math.cos(angle), y,
                              zc + settings["half_height"] * scale * math.sin(angle)))
            target = Vector((point.x, y, point.z)).lerp(ellipse, blend)
            row.append(new_vertex(target, source, follow))
        rows.append(row)
    cap = new_vertex(Vector((xc, y_rim + depth + cap_extra, zc)), rows[0][0], 0.0)

    faces = []
    for r, (front, back) in enumerate(zip(rows, rows[1:])):
        for k in range(n):
            next_k = (k + 1) % n
            faces.append((bm.faces.new((front[k], front[next_k], back[next_k], back[k])), r, k))
    for k in range(n):
        faces.append((bm.faces.new((rows[-1][k], rows[-1][(k + 1) % n], cap)), rings, k))
    bm.normal_update()
    axis = Vector((xc, y_rim + depth / 2, zc)) / unit
    to_local_direction = matrix.transposed().to_3x3()
    inward = sum(face.normal.dot(to_local_direction @ (axis - matrix @ face.calc_center_median())) > 0
                 for face, _, _ in faces)
    flipped = inward < len(faces) / 2
    tongue_faces = 0
    for face, r, k in faces:
        if face.calc_area() <= 0:
            raise ValueError("面積が 0 の面ができました。口の縁と袋の寸法を確かめてください")
        if flipped:
            face.normal_flip()
        face.material_index = slot
        face.smooth = True
        use_tongue = ("uv_tongue" in settings and r >= 1 and k >= right
                      and (r == rings or abs(math.cos(angles[k])) < settings["tongue_width"]))
        tongue_faces += int(use_tongue)
        for loop in face.loops:
            loop[uv].uv = settings["uv_tongue"] if use_tongue else settings["uv_inside"]
    if any(not edge.is_contiguous for edge in rim_edges):
        raise ValueError("唇の裏の面と袋の表裏がそろいません。元の縁の法線を確かめてください")
    return {"rim_vertices": n, "rim_edges": len(rim_edges), "rim_centre": list(centre), "rim_y_back": y_rim,
            "clearance_front_y": clearance, "depth": depth, "rings": rings, "half_width": width,
            "half_height": settings["half_height"], "new_vertices": n * rings + 1, "new_faces": len(faces),
            "tongue_faces": tongue_faces, "flipped": flipped, "shape_keys": len(shape_layers)}


def main(argv=None):
    import bmesh
    import bpy

    ap = argparse.ArgumentParser()
    ap.add_argument("--blend", required=True, type=Path)
    ap.add_argument("--settings", required=True, type=Path)
    ap.add_argument("--out-blend", required=True, type=Path)
    ap.add_argument("--report", required=True, type=Path)
    a = ap.parse_args(sys.argv[sys.argv.index("--") + 1:] if argv is None else argv)
    if a.blend.resolve() == a.out_blend.resolve():
        raise SystemExit("入力と出力には別の .blend を指定してください")
    settings = json.loads(a.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(a.blend.resolve()), use_scripts=False)
    obj = bpy.data.objects.get(settings["object"])
    if obj is None or obj.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    if obj.library or obj.data.library:
        raise SystemExit("リンクした外部のメッシュは、先にローカルへコピーしてください")
    mesh = obj.data
    slots = [slot.material.name if slot.material else None for slot in obj.material_slots]
    if settings["material"] not in slots:
        raise SystemExit(f"物体 {obj.name} に材質 {settings['material']} がありません")
    layer = mesh.uv_layers.get(settings["uv_layer"]) if "uv_layer" in settings else mesh.uv_layers.active
    if layer is None:
        raise SystemExit("割り当てに使う UV マップがありません")
    uv_name = layer.name
    matrix = obj.matrix_world.copy()
    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
    unit = scene.unit_settings.scale_length
    source_normals = [tuple(c.vector) for c in mesh.corner_normals] if mesh.has_custom_normals else None
    shared = mesh.users > 1
    if shared:
        mesh = obj.data = mesh.copy()

    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        rule = settings.get("clearance")
        clearance = clearance_front(bpy.data.objects, rule, unit) if rule else None
        report = add_bag(bm, mesh, matrix, unit, settings, slots.index(settings["material"]), uv_name, clearance)
        bm.to_mesh(mesh)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    finally:
        bm.free()
    mesh.update()
    if source_normals is not None:
        mesh.normals_split_custom_set(source_normals + [(0.0, 0.0, 0.0)] * (len(mesh.loops) - len(source_normals)))
    a.out_blend.parent.mkdir(parents=True, exist_ok=True)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(a.out_blend.resolve()), copy=True, compress=True)
    report = {"schema_version": "model3d-mouth-bag/0.1", **report, "object": obj.name, "uv_layer": uv_name,
              "separated_mesh": shared, "settings": settings}
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
