"""動く顔の目印とカメラの間に、指定したメッシュがあるかを調べる。"""

import argparse
import json
import sys
from pathlib import Path

if __package__:
    from .stage_render import set_frame, sync_to_render
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from stage_render import set_frame, sync_to_render


def pixel_from_plan(camera_point, focal, principal):
    """カメラの座標（前は -Z、上は +Y）から左上原点の画素へ写す。"""
    x, y, z = camera_point
    if z >= 0:
        return None
    return [principal[0] - focal * x / z, principal[1] + focal * y / z]


def frame_rows(scene, settings):
    record = settings["cameras"]
    if record is None:
        span = settings["frames"] or (scene.frame_start, scene.frame_end)
        if not scene.frame_start <= span[0] <= span[1] <= scene.frame_end:
            raise ValueError("frames は .blend のシーンのコマの範囲内にしてください")
        return [(frame, frame, None) for frame in range(span[0], span[1] + 1)]
    span = settings["frames"] or (-float("inf"), float("inf"))
    return sorted((row["T"], row.get("f", row["T"]), row) for row in record["frames"] if span[0] <= row["T"] <= span[1])


def camera_at(scene, depsgraph, settings, row, unit):
    from mathutils import Matrix

    if row is not None:
        record = settings["cameras"]
        world = Matrix(row["matrix"])
        world.translation = world.translation / unit
        local = world.inverted()
        width, height = record["resolution"]
        near, far = record.get("clip_m", [0.0, float("inf")])
        return world, local, near / unit, far / unit, False, (width, height), row["focal_px"], row["principal_px"]
    camera = scene.objects.get(settings["camera"]) if settings["camera"] else scene.camera
    if camera is None or camera.type != "CAMERA" or camera.data.type not in {"PERSP", "ORTHO"}:
        raise ValueError("透視投影または平行投影のカメラが必要です（camera または cameras）")
    camera = camera.evaluated_get(depsgraph)
    world = camera.matrix_world.normalized()
    return (world, world.inverted(), camera.data.clip_start, camera.data.clip_end,
            camera.data.type == "ORTHO", None, camera, None)


def camera_ray(scene, camera, point, margin):
    from bpy_extras.object_utils import world_to_camera_view
    from mathutils import Vector

    world, to_camera, near, far, ortho, size, focal, principal = camera
    local = to_camera @ point
    depth = -local.z
    if not near < depth <= far:
        return None, None, None
    if size is None:
        u, v, _ = world_to_camera_view(scene, focal, point)
        uv = [float(u), 1.0 - float(v)]
    else:
        pixel = pixel_from_plan(local, focal, principal)
        uv = [pixel[0] / size[0], pixel[1] / size[1]]
    if any(not 0 <= axis <= 1 for axis in uv):
        return uv, None, None
    if ortho:
        start = world @ Vector((local.x, local.y, -near))
    else:
        start = world.translation.lerp(point, near / depth)
    ray = point - start
    end = point - ray.normalized() * margin
    return (uv, start, end) if ray.length > margin else (uv, None, None)


def render_collections(layer_collection):
    if layer_collection.exclude or layer_collection.collection.hide_render:
        return set()
    names = {obj.name for obj in layer_collection.collection.objects}
    for child in layer_collection.children:
        names.update(render_collections(child))
    return names


def closest_hit(meshes, start, end, point, unit):
    nearest = None
    for obj, inverse in meshes:
        local_start = inverse @ start
        local_ray = (inverse @ end) - local_start
        hit, location, _, face = obj.ray_cast(local_start, local_ray.normalized(), distance=local_ray.length)
        if hit:
            world = obj.matrix_world @ location
            gap = (point - world).length * unit
            if nearest is None or gap > nearest["gap_to_landmark_m"]:
                nearest = {"object": obj.name, "face": face, "gap_to_landmark_m": gap, "point_m": list(world * unit)}
    return nearest


def sample(bpy, scene, settings, armature, objects, rows):
    from mathutils import Vector

    unit = scene.unit_settings.scale_length
    marks = {name: (item["bone"], Vector(item["offset"]) / unit) for name, item in settings["landmarks"].items()}
    counts = {name: {"clear": 0, "occluded": 0, "out_of_view": 0, "too_near": 0} for name in marks}
    output = []
    for T, f, row in rows:
        set_frame(scene, f)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        camera = camera_at(scene, depsgraph, settings, row, unit)
        actor = armature.evaluated_get(depsgraph)
        meshes = [obj.evaluated_get(depsgraph) for obj in objects if not obj.hide_render]
        meshes = [(obj, obj.matrix_world.inverted()) for obj in meshes]
        entries = {}
        for name, (bone, offset) in marks.items():
            pose = actor.pose.bones[bone]
            point = (actor.matrix_world @ pose.matrix).normalized() @ offset
            uv, start, end = camera_ray(scene, camera, point, settings["margin_m"] / unit)
            hit = None
            if start is None:
                status = "too_near" if uv and all(0 <= axis <= 1 for axis in uv) else "out_of_view"
            else:
                hit = closest_hit(meshes, start, end, point, unit)
                status = "occluded" if hit else "clear"
            counts[name][status] += 1
            entries[name] = {"status": status, "point_m": list(point * unit), "uv": uv, "hit": hit}
        output.append({"T": T, "f": f, "landmarks": entries})
    return output, counts


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for name in ("blend", "settings", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    scene = bpy.context.scene
    arms = [obj for obj in scene.objects if obj.type == "ARMATURE" and (
        not settings["armature"] or obj.name == settings["armature"])]
    if len(arms) != 1:
        raise ValueError("シーンのアーマチュアを 1 つ指定してください（armature）")
    armature = arms[0]
    absent = [mark["bone"] for mark in settings["landmarks"].values() if mark["bone"] not in armature.pose.bones]
    if absent:
        raise ValueError(f"顔の目印の骨がありません: {absent}")
    objects = [scene.objects.get(name) for name in settings["occluders"]]
    absent = [name for name, obj in zip(settings["occluders"], objects) if obj is None or obj.type != "MESH"]
    if absent:
        raise ValueError(f"調べるメッシュがシーンにありません: {absent}")
    rendered = render_collections(bpy.context.view_layer.layer_collection)
    objects = [obj for obj in objects if obj.name in rendered]
    synced = [modifier for obj in objects for modifier in sync_to_render(obj)]
    rows = frame_rows(scene, settings)
    if not rows or any(not scene.frame_start <= f <= scene.frame_end for _, f, _ in rows):
        raise ValueError("選んだ動きのコマが .blend のシーンの範囲にありません")
    frames, counts = sample(bpy, scene, settings, armature, objects, rows)
    occluded = [item["T"] for item in frames if any(mark["status"] == "occluded" for mark in item["landmarks"].values())]
    report = {"schema_version": "model3d-face-occlusion/0.1", "armature": armature.name,
              "blender": bpy.app.version_string, "frames": frames, "occluded_T": occluded, "counts": counts,
              "occluders": settings["occluders"], "occluders_excluded_by_collections": [
                  name for name in settings["occluders"] if name not in rendered],
              "synced_modifiers": synced, "margin_m": settings["margin_m"]}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
