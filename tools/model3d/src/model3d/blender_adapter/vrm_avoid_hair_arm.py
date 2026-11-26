"""Bake S-shaped corrections to side hair and audit mesh overlap in Blender."""

import argparse
from collections import defaultdict
from contextlib import contextmanager
import json
import math
from pathlib import Path
import sys

import numpy as np


SCHEMA = "model3d-hair-arm/0.1"
OUTLINE_NODE_GROUP = "VRM Add-on MToon 1.0 Outline Geometry Revision 1"
SETTINGS = {"chains", "colliders", "min_weight", "sample_step", "extra_frames", "angle_step_deg", "max_angle_deg",
            "envelope_frames", "smooth_sigma_frames", "outline_modifiers"}


def _name(value):
    return isinstance(value, str) and bool(value.strip())


def _names(value, label):
    if not isinstance(value, list) or not value or not all(map(_name, value)) or len(set(value)) != len(value):
        raise ValueError(f"{label} に重複のない名前の配列を指定してください")
    return value


def _number(settings, key, default, lower, upper, *, integer=False):
    value = settings.get(key, default)
    if type(value) not in ((int,) if integer else (int, float)) or not lower <= value <= upper:
        raise ValueError(f"{key} は {lower}〜{upper} の{'整数' if integer else '数'}にしてください")
    return value


def check_settings(settings):
    unknown = set(settings) - SETTINGS
    if unknown:
        raise ValueError(f"知らない設定があります: {sorted(unknown)}")
    chains, colliders = settings.get("chains"), settings.get("colliders")
    if not isinstance(chains, list) or not chains or not isinstance(colliders, list) or not colliders:
        raise ValueError("chains と colliders には 1 件以上のオブジェクトを指定してください")
    seen_bones, seen_groups = set(), set()
    checked_chains = []
    for entry in chains:
        if (not isinstance(entry, dict) or set(entry) - {"object", "bones", "groups", "direction", "outward_ratio"}
                or not _name(entry.get("object"))):
            raise ValueError("chains の各要素に object と bones を指定してください")
        bones = _names(entry.get("bones"), "chains.bones")
        if len(bones) != 3 or seen_bones.intersection(bones):
            raise ValueError("chains.bones は重複しない 3 本の骨を根元から指定してください")
        seen_bones.update(bones)
        obj = entry["object"]
        groups = _names(entry.get("groups", bones), "chains.groups")
        keys = {(obj, group) for group in groups}
        if seen_groups.intersection(keys):
            raise ValueError(f"同じ髪メッシュ内の groups を鎖の間で共有できません: {obj}")
        seen_groups.update(keys)
        direction = entry.get("direction", 1)
        if type(direction) is not int or direction not in (-1, 1):
            raise ValueError("chains.direction は 1 または -1 にしてください")
        ratio = _number(entry, "outward_ratio", 0.0, -1.0, 1.0)
        checked_chains.append({"object": obj, "bones": bones, "groups": groups, "direction": direction, "outward_ratio": ratio})

    checked_colliders = []
    for entry in colliders:
        if not isinstance(entry, dict) or set(entry) - {"object", "groups"} or not _name(entry.get("object")):
            raise ValueError("colliders の各要素に object を指定してください")
        names = _names(entry["groups"], "colliders.groups") if "groups" in entry else None
        checked_colliders.append({"object": entry["object"], "groups": names})
    collider_names = {item["object"] for item in checked_colliders}
    if len(collider_names) != len(checked_colliders):
        raise ValueError("colliders に同じ物体を複数回指定できません")

    outlines = settings.get("outline_modifiers", {})
    if not isinstance(outlines, dict) or set(outlines) - (collider_names | {item["object"] for item in checked_chains}):
        raise ValueError("outline_modifiers のキーは対象メッシュの名前にしてください")
    for names in outlines.values():
        _names(names, "outline_modifiers")
    extra_frames = settings.get("extra_frames", [])
    if not isinstance(extra_frames, list) or not all(type(frame) is int for frame in extra_frames):
        raise ValueError("extra_frames はコマ番号（整数）の並びにしてください")

    max_angle = _number(settings, "max_angle_deg", 56, 1, 90)
    return {"chains": checked_chains, "colliders": checked_colliders,
            "min_weight": _number(settings, "min_weight", 0.25, 0.0, 0.99),
            "sample_step": _number(settings, "sample_step", 3, 1, 120, integer=True), "extra_frames": sorted(set(extra_frames)),
            "angle_step_deg": _number(settings, "angle_step_deg", min(4, max_angle), 1, max_angle), "max_angle_deg": max_angle,
            "envelope_frames": _number(settings, "envelope_frames", 5, 0, 120, integer=True),
            "smooth_sigma_frames": _number(settings, "smooth_sigma_frames", 5.0, 0, 120), "outline_modifiers": outlines}


def sample_frames(first, last, stride, extra):
    if last < first or any(frame < first or frame > last for frame in extra):
        raise ValueError("extra_frames は有効なシーンのコマ範囲内にしてください")
    return sorted({*range(first, last + 1, stride), last, *extra})


def best_angle(measure, baseline, step, limit):
    """最も小さい改善角を優先し、探索内で交差が減らなければ元に戻す。"""
    if baseline == 0:
        return 0.0, 0
    best = 0.0, baseline
    for probe in range(1, math.ceil(limit / step) + 1):
        angle = min(probe * step, limit)
        hits = measure(angle)
        if hits < best[1]:
            best = angle, hits
            if hits == 0:
                break
    return best


def smooth_angles(frames, sampled, angles, radius, sigma):
    full = np.interp(frames, sampled, angles)
    envelope = np.array([max(full[max(0, i - radius):i + radius + 1]) for i in range(len(full))])
    if not sigma:
        return envelope
    half_width = min(len(full) - 1, math.ceil(3 * sigma))
    delta = np.arange(-half_width, half_width + 1)
    weights = np.exp(-0.5 * (delta / sigma) ** 2)
    filtered = np.convolve(np.pad(envelope, half_width), weights, mode="valid")
    divisor = np.convolve(np.pad(np.ones_like(full), half_width), weights, mode="valid")
    return np.maximum(full, filtered / divisor)


@contextmanager
def hide_outlines(objects, selected):
    changes = []
    try:
        for obj in objects.values():
            named = selected.get(obj.name, [])
            for name in named:
                modifier = obj.modifiers.get(name)
                if modifier is None or modifier.type != "NODES":
                    raise ValueError(f"輪郭線の Geometry Nodes が見つかりません: {obj.name}/{name}")
            for modifier in obj.modifiers:
                group = getattr(modifier, "node_group", None)
                if modifier.type == "NODES" and (modifier.name in named or (group and group.name == OUTLINE_NODE_GROUP)):
                    changes.append((modifier, modifier.show_viewport))
                    modifier.show_viewport = False
        yield
    finally:
        for modifier, visible in reversed(changes):
            modifier.show_viewport = visible


def _mesh_layout(mesh):
    loops = np.empty(len(mesh.loops), dtype=np.int32)
    sizes = np.empty(len(mesh.polygons), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loops)
    mesh.polygons.foreach_get("loop_total", sizes)
    return len(mesh.vertices), loops, sizes


class MeshParts:
    def __init__(self, obj):
        self.obj = obj
        self.layout = _mesh_layout(obj.data)
        obj.data.calc_loop_triangles()
        self.triangles = [(tri.polygon_index, tuple(tri.vertices)) for tri in obj.data.loop_triangles]

    def faces(self, polygon_ids):
        triangles = [tri for index, tri in self.triangles if index in polygon_ids]
        if not triangles:
            raise ValueError(f"対象の面が見つかりません: {self.obj.name}")
        used = sorted({vertex for tri in triangles for vertex in tri})
        index = {vertex: i for i, vertex in enumerate(used)}
        return used, [tuple(index[vertex] for vertex in tri) for tri in triangles]

    def geometry(self, depsgraph, faces):
        evaluated = self.obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        if mesh is None:
            raise ValueError(f"評価済みメッシュを取得できません: {self.obj.name}")
        try:
            count, loops, sizes = _mesh_layout(mesh)
            original_count, original_loops, original_sizes = self.layout
            if count != original_count or not np.array_equal(loops, original_loops) or not np.array_equal(sizes, original_sizes):
                raise ValueError(f"モディファイアが面や頂点の対応を変えています: {self.obj.name}")
            matrix = evaluated.matrix_world
            used, triangles = faces
            return [matrix @ mesh.vertices[vertex].co for vertex in used], triangles
        finally:
            evaluated.to_mesh_clear()


def _group_indices(obj, names):
    found = {group.name: group.index for group in obj.vertex_groups}
    missing = set(names) - found.keys()
    if missing:
        raise ValueError(f"{obj.name} に頂点グループがありません: {sorted(missing)}")
    return {found[name] for name in names}


def _hair_polygons(obj, group_lists, min_weight):
    owners = {index: owner for owner, groups in enumerate(group_lists) for index in _group_indices(obj, groups)}
    weights = [[(owners[g.group], g.weight) for g in vertex.groups if g.group in owners and g.weight > min_weight]
               for vertex in obj.data.vertices]
    polygons = [set() for _ in group_lists]
    for polygon in obj.data.polygons:
        picks = (item for vertex in polygon.vertices for item in weights[vertex])
        owner, _ = max(picks, key=lambda pair: (pair[1], -pair[0]), default=(-1, 0))
        if owner >= 0:
            polygons[owner].add(polygon.index)
    return polygons


def _collider_polygons(obj, groups, min_weight):
    if groups is None:
        return set(range(len(obj.data.polygons)))
    indices = _group_indices(obj, groups)
    active = [any(g.group in indices and g.weight > min_weight for g in vertex.groups) for vertex in obj.data.vertices]
    return {poly.index for poly in obj.data.polygons if any(active[i] for i in poly.vertices)}


def _parts(objects, settings):
    meshes = {name: MeshParts(obj) for name, obj in objects.items()}
    by_object = defaultdict(list)
    for i, chain in enumerate(settings["chains"]):
        by_object[chain["object"]].append((i, chain["groups"]))
    hair_faces = [None] * len(settings["chains"])
    claimed = defaultdict(set)
    for name, entries in by_object.items():
        mesh = meshes[name]
        for (index, _), ids in zip(entries, _hair_polygons(mesh.obj, [groups for _, groups in entries], settings["min_weight"])):
            hair_faces[index] = mesh, mesh.faces(ids)
            claimed[name].update(ids)
    colliders = []
    for entry in settings["colliders"]:
        mesh = meshes[entry["object"]]
        ids = _collider_polygons(mesh.obj, entry["groups"], settings["min_weight"])
        if ids & claimed[mesh.obj.name]:
            raise ValueError(f"髪と衝突相手に同じ面が含まれています: {mesh.obj.name}")
        colliders.append((mesh, mesh.faces(ids)))
    return hair_faces, colliders


def _collider_tree(depsgraph, colliders, tree_cls):
    points, triangles = [], []
    for mesh, faces in colliders:
        vertices, indices = mesh.geometry(depsgraph, faces)
        offset = len(points)
        points.extend(vertices)
        triangles.extend(tuple(offset + index for index in tri) for tri in indices)
    return tree_cls.FromPolygons(points, triangles, all_triangles=True)


def _overlap(tree_cls, depsgraph, hair, collider_tree):
    mesh, faces = hair
    vertices, triangles = mesh.geometry(depsgraph, faces)
    hair_tree = tree_cls.FromPolygons(vertices, triangles, all_triangles=True)
    return len(hair_tree.overlap(collider_tree))


def _chain_bones(armature, definitions):
    chains = []
    for names in (chain["bones"] for chain in definitions):
        bones = [armature.pose.bones.get(name) for name in names]
        if any(bone is None for bone in bones):
            raise ValueError(f"髪の骨が見つかりません: {names}")
        if any(parent not in child.parent_recursive for parent, child in zip(bones, bones[1:])):
            raise ValueError(f"髪の骨は親から子の順にしてください: {names}")
        if any(bone.rotation_mode != "QUATERNION" for bone in bones):
            raise ValueError(f"髪の骨は四元数の回転にしてください: {names}")
        chains.append(bones)
    return chains


def _rotation(original, definition, angle, euler_cls):
    x = math.radians(angle * definition["direction"])
    z = x * definition["outward_ratio"]
    deltas = ((x, 0, z), (-0.45 * x, 0, -0.30 * z), (-0.25 * x, 0, 0))
    return [quat @ euler_cls(delta, "XYZ").to_quaternion() for quat, delta in zip(original, deltas)]


def _assign(bones, rotations):
    for bone, rotation in zip(bones, rotations):
        bone.rotation_quaternion = rotation


def _sample_and_solve(bpy, scene, frames, sampled, bones, settings, hair, colliders, tree_cls, euler_cls):
    count = len(bones)
    before = np.zeros((len(frames), count), dtype=int)
    angles = np.zeros((len(sampled), count))
    probed = np.zeros((len(sampled), count), dtype=int)
    original = []
    sample_indices = {frame: i for i, frame in enumerate(sampled)}
    for row, frame in enumerate(frames):
        scene.frame_set(frame)
        original.append([[bone.rotation_quaternion.copy() for bone in chain] for chain in bones])
        dg = bpy.context.evaluated_depsgraph_get()
        collider_tree = _collider_tree(dg, colliders, tree_cls)
        before[row] = [_overlap(tree_cls, dg, part, collider_tree) for part in hair]
        if frame not in sample_indices:
            continue
        for i, (chain, definition) in enumerate(zip(bones, settings["chains"])):
            rest = original[row][i]

            def measure(angle):
                _assign(chain, _rotation(rest, definition, angle, euler_cls))
                bpy.context.view_layer.update()
                evaluated = bpy.context.evaluated_depsgraph_get()
                tree = _collider_tree(evaluated, colliders, tree_cls)
                return _overlap(tree_cls, evaluated, hair[i], tree)

            try:
                angle, hits = best_angle(measure, before[row, i], settings["angle_step_deg"], settings["max_angle_deg"])
            finally:
                _assign(chain, rest)
            index = sample_indices[frame]
            angles[index, i], probed[index, i] = angle, hits
    return before, angles, probed, original


def _bake(scene, frames, armature, bones, definitions, original, smooth, euler_cls):
    adjusted = np.any(smooth > 0.0, axis=0)
    expected = {}
    if not adjusted.any():
        return expected
    animation = armature.animation_data
    if animation and animation.action and animation.action.users > 1:
        animation.action = animation.action.copy()
    previous = [None] * len(bones)
    for row, frame in enumerate(frames):
        scene.frame_set(frame)
        for i, chain in enumerate(bones):
            if not adjusted[i]:
                continue
            rotations = _rotation(original[row][i], definitions[i], smooth[row, i], euler_cls)
            if previous[i] is not None:
                for prior, rotation in zip(previous[i], rotations):
                    if prior.dot(rotation) < 0.0:
                        rotation.negate()
            previous[i] = rotations
            _assign(chain, rotations)
            for bone, rotation in zip(chain, rotations):
                if not bone.keyframe_insert(data_path="rotation_quaternion", frame=frame, group=bone.name):
                    raise ValueError(f"髪の回転キーを作れません: {bone.name}, {frame}")
                expected.setdefault(frame, {})[bone.name] = rotation.normalized()
    return expected


def _audit(bpy, scene, frames, armature, expected, hair, colliders, tree_cls):
    after = []
    for frame in frames:
        scene.frame_set(frame)
        for name, wanted in expected.get(frame, {}).items():
            actual = armature.pose.bones[name].rotation_quaternion.normalized()
            if abs(actual.dot(wanted)) < 1.0 - 1e-6:
                raise ValueError(f"焼き付けた髪の回転を再生できません: {name}, {frame}")
        dg = bpy.context.evaluated_depsgraph_get()
        collider_tree = _collider_tree(dg, colliders, tree_cls)
        after.append([_overlap(tree_cls, dg, part, collider_tree) for part in hair])
    return np.array(after)


def run(bpy, settings, armature_name):
    from mathutils import Euler
    from mathutils.bvhtree import BVHTree

    scene = bpy.context.scene
    selected = [obj for obj in scene.objects if obj.type == "ARMATURE" and (not armature_name or obj.name == armature_name)]
    if len(selected) != 1:
        raise ValueError("シーン内のアーマチュアを 1 つ指定してください（armature）")
    armature = selected[0]
    spring = getattr(getattr(armature.data, "vrm_addon_extension", None), "spring_bone1", None)
    if spring and spring.enable_animation:
        raise ValueError("揺れもののコマ送りを止めてください（vrm.bake_springs の出力を使用できます）")
    bones = _chain_bones(armature, settings["chains"])
    objects = {}
    for name in dict.fromkeys(entry["object"] for entry in settings["chains"] + settings["colliders"]):
        obj = bpy.context.view_layer.objects.get(name)
        if obj is None or obj.type != "MESH":
            raise ValueError(f"現在のビューレイヤーに対象メッシュが見つかりません: {name}")
        objects[name] = obj
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    sampled = sample_frames(scene.frame_start, scene.frame_end, settings["sample_step"], settings["extra_frames"])
    start, subframe = scene.frame_current, scene.frame_subframe
    try:
        with hide_outlines(objects, settings["outline_modifiers"]):
            hair, colliders = _parts(objects, settings)
            before, raw, probed, original = _sample_and_solve(bpy, scene, frames, sampled, bones, settings, hair, colliders, BVHTree, Euler)
            smooth = np.column_stack([smooth_angles(frames, sampled, raw[:, i], settings["envelope_frames"], settings["smooth_sigma_frames"])
                                      for i in range(len(bones))])
            expected = _bake(scene, frames, armature, bones, settings["chains"], original, smooth, Euler)
            after = _audit(bpy, scene, frames, armature, expected, hair, colliders, BVHTree) if expected else before.copy()
    finally:
        scene.frame_set(start, subframe=subframe)

    return {"schema_version": SCHEMA, "armature": armature.name, "frames": [frames[0], frames[-1]], "sampled_frames": sampled,
            "before_pairs": int(before.sum()), "after_pairs": int(after.sum()),
            "chains": [{"object": item["object"], "bones": item["bones"], "max_applied_deg": float(smooth[:, i].max()),
                        "search": [{"frame": frame, "angle_deg": float(raw[row, i]), "pairs": int(probed[row, i])}
                                   for row, frame in enumerate(sampled)],
                        "frames": [{"frame": frame, "before": int(before[row, i]), "after": int(after[row, i]),
                                    "angle_deg": float(smooth[row, i])} for row, frame in enumerate(frames)]}
                       for i, item in enumerate(settings["chains"])]}


def main():
    import bpy

    parser = argparse.ArgumentParser()
    for option in ("blend", "out-blend", "settings", "report"):
        parser.add_argument(f"--{option}", required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    if Path(args.blend).resolve() == Path(args.out_blend).resolve():
        raise ValueError("入力と出力の blend は別のファイルにしてください")
    config = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    armature = config.pop("armature")
    settings = check_settings(config)
    bpy.ops.wm.open_mainfile(filepath=str(Path(args.blend).resolve()))
    report = run(bpy, settings, armature)
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(args.out_blend).resolve()), copy=True, compress=True)
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
