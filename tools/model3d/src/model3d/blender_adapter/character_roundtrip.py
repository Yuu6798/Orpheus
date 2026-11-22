"""Sample native meshes, then import the actual GLB into an empty Blender scene."""
import json
import math
import sys
from pathlib import Path

import numpy as np

if __package__:
    from .arm_pinch import MIN_RING_VERTS, group_weight, ring_vertices, spread
    from .model_edit import bone_world, world_points
else:  # Blender --python runs this file outside the installation's Python package.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from arm_pinch import MIN_RING_VERTS, group_weight, ring_vertices, spread
    from model_edit import bone_world, world_points


def mesh_object(scene, name, aliases):
    obj = scene.objects.get(name)
    if obj and obj.type == "MESH":
        return obj
    meshes = [o for o in scene.objects if o.type == "MESH"]
    matches = [o for o in meshes if o.data.name == aliases.get(name)]
    if len(matches) == 1:
        return matches[0]
    raise ValueError(f"missing or ambiguous mesh {name}; scene mesh objects: {[o.name for o in meshes]}")


def select_native_scene(bpy, settings):
    meshes = {r["native"]["mesh"] for kind in ("expressions", "sections") for r in settings[kind]}

    def contains_meshes(scene):
        return all((obj := scene.objects.get(name)) and obj.type == "MESH" for name in meshes)

    active = bpy.context.scene
    if contains_meshes(active):
        return active
    matches = [s for s in bpy.data.scenes if contains_meshes(s)]
    if len(matches) != 1:
        raise ValueError(f"expected one native scene containing meshes {sorted(meshes)}; found {[s.name for s in matches]}")
    bpy.context.window.scene = matches[0]
    return matches[0]


def set_frame(scene, frame):
    integer = math.floor(frame)
    scene.frame_set(integer, subframe=frame - integer)


def section(scene, row, side, aliases):
    binding = row[side]
    obj = mesh_object(scene, binding["mesh"], aliases)
    rigs = {m.object for m in obj.modifiers if m.type == "ARMATURE" and m.object and m.show_viewport}
    if len(rigs) != 1:
        raise ValueError(f"{side}.{obj.name}: expected one enabled armature")
    rig, = rigs
    bone, tip = binding["bone"], binding["tip"]
    if not {bone, tip}.issubset(rig.data.bones.keys()):
        raise ValueError(f"{side}.{rig.name}: missing start or end joint ({bone}, {tip})")
    if not set(binding["groups"]).issubset(obj.vertex_groups.keys()):
        raise ValueError(f"{side}.{obj.name}: missing section vertex groups {binding['groups']}")
    start, end = (bone_world(rig, name)[0] for name in (bone, tip))
    length = float(np.linalg.norm(end - start))
    if length <= 1e-12:
        raise ValueError(f"{side}.{row['label']}: coincident rest joints")
    axis = (end - start) / length
    rest = world_points(obj)
    indices = ring_vertices(rest, start + row["at"] * length * axis, axis, row["half_width"] * length,
                            row["max_radius"] * length, group_weight(obj, binding["groups"]), 0.0)
    # A glTF UV seam can create several vertices at exactly the same bind position.
    # Count it once in each mesh and keep that material ring fixed through the dance.
    _, unique = np.unique(rest[indices], axis=0, return_index=True)
    indices = indices[unique]
    if len(indices) < MIN_RING_VERTS or spread(rest[indices], axis) < 1e-12:
        raise ValueError(f"{side}.{row['label']}: insufficient bind section ({len(indices)} distinct vertices)")
    return obj, rig, bone, tip, indices, length


def tag_bind_vertices(mesh):
    # The loaded scene is disposable. Preserve the bind indices through evaluation,
    # including modifiers that reorder vertices without changing their count.
    indices = np.arange(len(mesh.vertices), dtype=np.int32)
    attribute = mesh.attributes.new("model3d_bind_vertex", "INT", "POINT")
    attribute.data.foreach_set("value", indices)
    mesh.update()
    return attribute.name, indices


def points_in_bind_order(obj, graph, tracking, side, frame):
    evaluated = obj.evaluated_get(graph)
    name, expected = tracking
    mesh = evaluated.data
    if len(mesh.vertices) != len(expected):
        raise ValueError(f"{side}.{obj.name}: evaluated mesh changes topology at frame {frame}")
    attribute = mesh.attributes.get(name)
    message = f"{side}.{obj.name}: cannot track bind vertices at frame {frame}"
    if attribute is None or attribute.domain != "POINT" or attribute.data_type != "INT":
        raise ValueError(message)
    indices = np.empty_like(expected)
    attribute.data.foreach_get("value", indices)
    points = world_points(evaluated)
    if np.array_equal(indices, expected):
        return points
    order = np.argsort(indices)
    if not np.array_equal(indices[order], expected):
        raise ValueError(message)
    return points[order]


def sample_scene(bpy, settings, side, times, seconds):
    scene = bpy.context.scene
    origin = settings[f"{side}_start"]
    set_frame(scene, origin)
    aliases = settings["node_meshes"] if side == "delivered" else {}
    sections = {r["label"]: section(scene, r, side, aliases) for r in settings["sections"]}
    tracking = {}
    for obj, rig, *_ in sections.values():
        # A saved REST display must not suppress the animation under measurement.
        rig.data.pose_position = "POSE"
        if obj.data not in tracking:
            tracking[obj.data] = tag_bind_vertices(obj.data)
    expressions = {}
    for r in settings["expressions"]:
        binding = r[side]
        obj = mesh_object(scene, binding["mesh"], aliases)
        keys = obj.data.shape_keys
        if not keys or binding["key"] not in keys.key_blocks:
            raise ValueError(f"{side}.{r['label']}: missing shape key {binding['mesh']}/{binding['key']}")
        if not keys.use_relative or keys.reference_key.name == binding["key"]:
            raise ValueError(f"{side}.{r['label']}: expression requires a relative, non-basis shape key")
        expressions[r["label"]] = (obj, binding["key"])
    probes = {k: {"mesh": obj.name, "armature": rig.name, "bone": bone, "tip": tip,
                  "bind_vertices": len(ids), "bind_joint_length": length}
              for k, (obj, rig, bone, tip, ids, length) in sections.items()}
    samples = []
    for frame, sec in zip(times, seconds, strict=True):
        set_frame(scene, frame)
        dg = bpy.context.evaluated_depsgraph_get()
        coords = {}
        row = {"frame": frame, "seconds": sec, "sections": {}, "expressions": {}}
        for label, (obj, rig, bone, tip, ids, length) in sections.items():
            if obj.name not in coords:
                coords[obj.name] = points_in_bind_order(obj, dg, tracking[obj.data], side, frame)
            arm = rig.evaluated_get(dg)
            axis = np.array(arm.matrix_world.to_3x3() @ (arm.pose.bones[tip].head - arm.pose.bones[bone].head))
            dist = np.linalg.norm(axis)
            if dist <= 1e-12:
                raise ValueError(f"{side}.{label}: coincident posed joints at frame {frame}")
            row["sections"][label] = spread(coords[obj.name][ids], axis / dist) / length
        for label, (obj, key) in expressions.items():
            if obj.evaluated_get(dg).show_only_shape_key:
                raise ValueError(f"{side}.{label}: disable the pinned shape-key preview")
            block = obj.data.shape_keys.evaluated_get(dg).key_blocks[key]
            row["expressions"][label] = 0.0 if block.mute else float(block.value)
        samples.append(row)
    return {"samples": samples, "probes": probes}


def main(settings_path, output_path):
    import bpy

    settings = json.loads(Path(settings_path).read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=settings["native"])
    scene = select_native_scene(bpy, settings)
    native_scene = scene.name
    frames = settings["frames"]
    if frames[0] < scene.frame_start or frames[-1] > scene.frame_end:
        raise ValueError(f"requested frames outside native scene range {scene.frame_start}..{scene.frame_end}")
    fps = scene.render.fps / scene.render.fps_base
    # A fixed integer import rate also handles native NTSC clocks. glTF timestamps
    # are seconds; using subframes avoids rounding to the nearest video frame.
    delivered_fps = scene.render.fps
    seconds = [(f - settings["native_start"]) / fps for f in frames]
    imported_frames = [settings["delivered_start"] + s * delivered_fps for s in seconds]
    # glTF input times are float32. Allow their rounding at the two endpoints.
    clip = settings["clip"]
    for frame in (imported_frames[0], imported_frames[-1]):
        t = frame / delivered_fps
        edge = min(max(t, clip["start_seconds"]), clip["end_seconds"])
        if not math.isclose(t, edge, rel_tol=6e-8, abs_tol=1e-8):
            raise ValueError(f"requested sample {t:g}s outside GLB range "
                             f"{clip['start_seconds']:g}..{clip['end_seconds']:g}s; check timeline origins")
    native = sample_scene(bpy, settings, "native", frames, seconds)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene.render.fps = delivered_fps
    bpy.context.scene.render.fps_base = 1.0
    if "FINISHED" not in bpy.ops.import_scene.gltf(filepath=settings["delivered"]):
        raise ValueError("Blender did not finish importing the GLB")
    delivered = sample_scene(bpy, settings, "delivered", imported_frames, seconds)
    Path(output_path).write_text(json.dumps({"native": native, "native_scene": native_scene, "delivered": delivered, "fps": fps,
                                             "delivered_fps": delivered_fps}, allow_nan=False), encoding="utf-8")


if __name__ == "__main__":
    main(*sys.argv[sys.argv.index("--") + 1:])
