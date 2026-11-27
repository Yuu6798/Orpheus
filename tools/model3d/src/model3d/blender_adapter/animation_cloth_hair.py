"""Blender 側で、兄弟 SpringBone 鎖の境界と髪の肩への貫通を測る。"""
import numpy as np

HAIR_ROLES = ("left_shoulder", "right_shoulder", "left_upper_arm", "right_upper_arm", "upper_chest")
HAIR_WEIGHT_MIN = 0.25


def dominant(vertices, names):
    """指定の頂点グループのうち重みが最大の名前。正の重みが無ければ None。同点では先のもの。"""
    out = []
    for groups in vertices:
        best, weight = None, 0.0
        for index, value in groups:
            if index in names and value > weight:
                best, weight = names[index], value
        out.append(best)
    return out


def spring_chain_roots(chains, parents, excluded):
    """有効な SpringBone だけを使い、各骨に (鎖の根, 根の親) を対応させる。複数鎖に属する骨は除く。"""
    memberships = {}
    for chain in chains:
        if not chain or any(name not in parents for name in chain):
            continue
        root = chain[0]
        parent = parents[root]
        if parent is None or root in excluded:
            continue
        for name in chain:
            if name not in excluded:
                memberships.setdefault(name, set()).add((root, parent))
    return {name: next(iter(roots)) for name, roots in memberships.items() if len(roots) == 1}


def world_coordinates(obj):
    co = np.empty(len(obj.data.vertices) * 3, dtype=np.float32)
    obj.data.vertices.foreach_get("co", co)
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    return co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]


def vertex_edges(mesh):
    edges = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edges)
    return edges.reshape(-1, 2)


def hem_candidates(edges, coordinates, dominant, roots, rest_min):
    """同じ親から出た別の鎖に支配される頂点を結ぶ辺だけを選ぶ。"""
    selected, boundaries = [], []
    for edge, (a, b) in enumerate(edges):
        first, second = roots.get(dominant[a]), roots.get(dominant[b])
        if first and second and first[0] != second[0] and first[1] == second[1]:
            selected.append(edge)
            boundaries.append(sorted((first[0], second[0])))
    selected = np.array(selected, dtype=np.int32)
    a, b = edges[selected].T
    rest = np.linalg.norm(coordinates[a] - coordinates[b], axis=1)
    use = np.flatnonzero((rest > 0) & (rest >= rest_min))
    return edges[selected[use]], rest[use], [boundaries[i] for i in use]


def hem_stretches(co, edges, rest, boundaries, ratio_min, delta_min):
    a, b = edges.T
    length = np.linalg.norm(co[a] - co[b], axis=1)
    delta, ratio = length - rest, length / rest
    strongest = {}
    for i in np.flatnonzero((ratio >= ratio_min) & (delta >= delta_min)):
        pair = tuple(boundaries[i])
        if pair in strongest and strongest[pair]["depth_m"] >= delta[i]:
            continue
        strongest[pair] = {
            "chain_roots": list(pair),
            "edge": edges[i].tolist(),
            "depth_m": float(delta[i]),
            "stretch_ratio": float(ratio[i]),
            "location": ((co[a[i]] + co[b[i]]) * 0.5).tolist(),
        }
    return strongest.values()


def shoulder_bones(humanoid, parents, spring_names):
    """人型の役割から対象と、その補助骨を選ぶ。前腕・手など別の役割に当たったら止める。"""
    wanted = set(HAIR_ROLES)
    if "upper_chest" not in humanoid:
        wanted.add("chest")
    human = {name: role for role, name in humanoid.items()}
    regions = {}
    for name in parents:
        ancestor = name
        while ancestor is not None and ancestor not in human and ancestor not in spring_names:
            ancestor = parents.get(ancestor)
        role = human.get(ancestor)
        if role in wanted and name not in spring_names:
            regions[name] = role
    return regions


def polygon_regions(polygons, vertices, names, regions):
    group_regions = {index: regions[name] for index, name in names.items() if name in regions}
    vertex_regions = []
    for groups in vertices:
        weights = {}
        for index, weight in groups:
            if index in group_regions:
                role = group_regions[index]
                weights[role] = weights.get(role, 0.0) + weight
        region = max(weights, key=weights.get) if weights else None
        vertex_regions.append((region, weights.get(region, 0.0)))
    return [region if weight > HAIR_WEIGHT_MIN else None
            for region, weight in (max((vertex_regions[i] for i in face), key=lambda entry: entry[1]) for face in polygons)]


def hair_penetration(points, tree, regions, near, depth_min, minimum):
    """最寄りの体表が対象部位で、面の内側にある点を数える。"""
    count, deepest = 0, None
    for point in points:
        loc, normal, face, _ = tree.find_nearest(point, near)
        if loc is None or regions[face] is None:
            continue
        depth = sum((x - p) * n for x, p, n in zip(loc, point, normal))
        if depth <= 0 or depth < depth_min:
            continue
        count += 1
        if deepest is None or depth > deepest["depth_m"]:
            deepest = {"depth_m": float(depth), "location": list(loc), "normal": list(normal), "body_region": regions[face]}
    if count >= minimum and deepest is not None:
        return {**deepest, "vertices": count}
    return None


def loop_vertices(mesh):
    vertices = np.empty(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", vertices)
    return vertices


def polygon_sizes(mesh):
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.polygons.foreach_get("loop_total", sizes)
    return sizes


class ClothHairSampler:
    """コマごとの別の検査と同じ依存グラフを使い、該当した測定だけをまとめる。"""

    def __init__(self, meshes, springs, humanoid, parents, head_tree, deform_bones, settings):
        self.settings = settings
        self.hem_enabled = settings.get("hem_tear", False)
        self.hair_enabled = settings.get("hair", False)
        self.hem_edges, self.shoulders = [], []
        selected = settings.get("hair_objects")
        if selected is None:
            self.hair_objects = [m["object"] for m in meshes if m["kind"] == "hair"]
        else:
            by_name = {m["object"].name: m["object"] for m in meshes}
            missing = [name for name in selected if name not in by_name]
            if missing:
                raise SystemExit(f"hair_objects に、描画するこのアーマチュアのメッシュがありません: {missing}")
            self.hair_objects = [by_name[name] for name in selected]
        self.meta = {
            "hem_tear": {"enabled": self.hem_enabled, "candidate_edges": {}, "unwatched": {}, "hits": []},
            "hair_shoulder": {"enabled": self.hair_enabled, "target_faces": {}, "unwatched": {}, "hits": []},
        }
        if self.hem_enabled:
            roots = spring_chain_roots(springs, parents, set(humanoid.values()) | head_tree)
            self.prepare_hem(meshes, roots, deform_bones)
        if self.hair_enabled and self.hair_objects:
            regions = shoulder_bones(humanoid, parents, {name for chain in springs for name in chain})
            self.prepare_shoulders(meshes, regions)

    def prepare_hem(self, meshes, roots, deform_bones):
        if not roots:
            return
        for mesh in meshes:
            bone_groups = {index: name for index, name in mesh["names"].items() if name in deform_bones}
            if not any(name in roots for name in bone_groups.values()):
                continue
            obj = mesh["object"]
            edges = vertex_edges(obj.data)
            selected, rest, boundaries = hem_candidates(edges, world_coordinates(obj), dominant(mesh["vertices"], bone_groups), roots,
                                                        self.settings["hem_tear_rest_min_m"])
            if len(selected):
                self.hem_edges.append((obj, edges, selected, rest, boundaries))
                self.meta["hem_tear"]["candidate_edges"][obj.name] = len(selected)

    def prepare_shoulders(self, meshes, regions):
        for mesh in meshes:
            if mesh["kind"] != "body" or mesh["object"] in self.hair_objects:
                continue
            obj = mesh["object"]
            if not any(name in regions for name in mesh["names"].values()):
                continue
            faces = [tuple(polygon.vertices) for polygon in obj.data.polygons]
            face_regions = polygon_regions(faces, mesh["vertices"], mesh["names"], regions)
            targets = {i for face, region in zip(faces, face_regions) if region is not None for i in face}
            if targets:
                sizes = polygon_sizes(obj.data)
                self.shoulders.append((obj, faces, face_regions, np.array(sorted(targets)), loop_vertices(obj.data), sizes,
                                       bool((sizes == 3).all())))
                self.meta["hair_shoulder"]["target_faces"][obj.name] = sum(region is not None for region in face_regions)

    def sample_hem(self, depsgraph, frame):
        result = self.meta["hem_tear"]
        for obj, topology, edges, rest, boundaries in self.hem_edges:
            evaluated = obj.evaluated_get(depsgraph)
            if (len(evaluated.data.vertices) != len(obj.data.vertices)
                    or not np.array_equal(vertex_edges(evaluated.data), topology)):
                result["unwatched"].setdefault(obj.name, []).append(frame)
                continue
            for hit in hem_stretches(world_coordinates(evaluated), edges, rest, boundaries,
                                     self.settings["hem_tear_ratio"], self.settings["hem_tear_delta_min_m"]):
                result["hits"].append({"frame": frame, "object": obj.name, **hit})

    def sample_hair(self, depsgraph, frame, bvh):
        if not self.shoulders:
            return
        result = self.meta["hair_shoulder"]
        near = self.settings["hair_near_m"]
        hair = [(obj.name, world_coordinates(obj.evaluated_get(depsgraph))) for obj in self.hair_objects]
        for obj, faces, regions, targets, topology, sizes, all_triangles in self.shoulders:
            evaluated = obj.evaluated_get(depsgraph)
            if (len(evaluated.data.vertices) != len(obj.data.vertices) or not np.array_equal(polygon_sizes(evaluated.data), sizes)
                    or not np.array_equal(loop_vertices(evaluated.data), topology)):
                result["unwatched"].setdefault(obj.name, []).append(frame)
                continue
            co = world_coordinates(evaluated)
            lo, hi = co[targets].min(axis=0) - near, co[targets].max(axis=0) + near
            candidates = [(name, points[((lo <= points) & (points <= hi)).all(axis=1)]) for name, points in hair]
            if all(len(points) < self.settings["hair_min_vertices"] for _, points in candidates):
                continue
            # 最寄り面が首・前腕などであれば拾わないため、対象外の面も BVH に入れる。
            # 負のスケールでは、世界座標で外向きになるよう面の順を返す。
            surface = [face[::-1] for face in faces] if evaluated.matrix_world.is_negative else faces
            tree = bvh.FromPolygons(co.tolist(), surface, all_triangles=all_triangles)
            for name, points in candidates:
                if len(points) < self.settings["hair_min_vertices"]:
                    continue
                hit = hair_penetration(points.tolist(), tree, regions, near, self.settings["hair_depth_min_m"],
                                       self.settings["hair_min_vertices"])
                if hit:
                    result["hits"].append({"frame": frame, "object": name, "body_object": obj.name, **hit})
