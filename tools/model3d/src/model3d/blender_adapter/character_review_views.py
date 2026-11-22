"""同じ身体基準のカメラを修正前後に置き、左右の横・斜めの PNG を描く。入力の .blend は保存しない。"""
import json
import math
import sys
from contextlib import contextmanager
from pathlib import Path

if __package__:
    from .face_sheet import DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED
    from .vrm_apply_motion import use_scene_of
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from face_sheet import DISPLAY_FIXED, IMAGE_FIXED, RENDER_FIXED, VIEW_FIXED
    from vrm_apply_motion import use_scene_of


ANGLES = {"left-front": 45, "left": 90, "left-back": 135,
          "right-front": -45, "right": -90, "right-back": -135}


def side_of(left, right):
    """両肩を結ぶ水平方向。視点の符号には解剖学上の左・右を使う。"""
    across = left - right
    across.z = 0
    if across.length < 1e-9:
        raise ValueError("left/right bones have coincident horizontal positions")
    return across.normalized()


def front_of(side):
    from mathutils import Vector
    return Vector((side.y, -side.x, 0))


def landmarks(rig, model, rest, anchor=None):
    bones = rig.data.bones if rest else rig.pose.bones
    points = []
    for label, name in (("left", model["left"]), ("right", model["right"]), ("anchor", anchor or model["anchor"])):
        bone = bones.get(name)
        if bone is None:
            raise ValueError(f"{rig.name}: unknown {label} bone {name!r}")
        head = bone.head_local if rest else bone.head
        points.append(rig.matrix_world @ head)
    return points[2], side_of(points[0], points[1])


def subject_objects(scene, rig, model):
    if "meshes" in model:
        objects = set()
        for name in model["meshes"]:
            obj = scene.objects.get(name)
            if obj is None or obj.type != "MESH":
                raise ValueError(f"unknown character mesh: {name!r}")
            objects.add(obj)
    else:
        objects = set(rig.children_recursive)
        objects.update(obj for obj in scene.objects
                       if any(mod.type == "ARMATURE" and mod.object == rig for mod in obj.modifiers))
    return objects


def camera_setup(bpy, scene, objects, size):
    if not any(obj.type == "MESH" and not obj.hide_render for obj in objects):
        raise ValueError("character contains no renderable meshes")
    for obj in scene.objects:
        if obj.type == "LIGHT" or (obj not in objects and (obj.type not in {"ARMATURE", "EMPTY"} or obj.is_instancer)):
            # This source is a disposable process-local load. A keyed stage/light
            # visibility must stay off when Blender reevaluates the frame to draw.
            pin_value(obj, "hide_render", True)
    for marker in scene.timeline_markers:
        marker.camera = None
    render = scene.render
    render.engine = "BLENDER_EEVEE_NEXT" if bpy.app.version < (5, 0, 0) else "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 8
    render.resolution_x = render.resolution_y = size
    render.resolution_percentage = 100
    scene.view_settings.view_transform = "Standard"
    if hasattr(render.image_settings, "media_type"):
        render.image_settings.media_type = "IMAGE"
    for owner, values in [(render, {**RENDER_FIXED, "film_transparent": True, "use_stamp": False}),
                          (scene.display_settings, DISPLAY_FIXED), (scene.view_settings, VIEW_FIXED),
                          (render.image_settings, IMAGE_FIXED)]:
        for key, value in values.items():
            setattr(owner, key, value)
    world = bpy.data.worlds.new("Character review light")
    scene.world = world
    world.use_nodes = True
    background = next(n for n in world.node_tree.nodes if n.type == "BACKGROUND")
    background.inputs[0].default_value = (.38, .38, .38, 1)
    camera = bpy.data.objects.new("Character review", bpy.data.cameras.new("Review lens"))
    camera.data.type = "ORTHO"
    scene.collection.objects.link(camera)
    scene.camera = camera
    lights = []
    for label, strength in (("Key", 2.1), ("Fill", .85)):
        data = bpy.data.lights.new(label, "SUN")
        data.energy = strength
        data.angle = math.radians(15)
        light = bpy.data.objects.new("Review " + label, data)
        scene.collection.objects.link(light)
        lights.append(light)
    return camera, lights


def pin_value(owner, attribute, value):
    """Keep a scalar fixed through render reevaluation. Return a function restoring this property only."""
    saved = getattr(owner, attribute)
    animation = owner.id_data.animation_data
    curve = None
    old_driver = None
    if animation and owner.bl_rna.properties[attribute].is_animatable:
        path = owner.path_from_id(attribute)
        curve = animation.drivers.find(path)
        if curve is None:
            curve = owner.driver_add(attribute)
        else:
            old_driver = (curve.driver.type, curve.driver.expression, curve.mute)
        curve.mute = False
        curve.driver.type = "SCRIPTED"
        curve.driver.expression = str(float(value))
    setattr(owner, attribute, value)

    def restore():
        if old_driver is not None:
            kind, expression, mute = old_driver
            curve.driver.expression = expression
            curve.driver.type = kind
            curve.mute = mute
        elif curve is not None:
            owner.driver_remove(attribute)
        setattr(owner, attribute, saved)

    return restore


@contextmanager
def disabled(objects, control):
    obj = next((obj for obj in objects if obj.name == control["object"]), None)
    if obj is None:
        raise ValueError(f"disabled object is not part of the character: {control['object']!r}")
    if "shape_key" in control:
        keys = obj.data.shape_keys if obj.type == "MESH" else None
        name = control["shape_key"]
        if not keys or not keys.use_relative or name not in keys.key_blocks or name == keys.reference_key.name:
            raise ValueError(f"{obj.name}: unknown or non-relative shape key {name!r}")
        owner, attribute, off = keys.key_blocks[name], "value", 0.0
    elif "modifier" in control:
        mod = obj.modifiers.get(control["modifier"])
        if mod is None:
            raise ValueError(f"{obj.name}: unknown modifier {control['modifier']!r}")
        owner, attribute, off = mod, "show_render", False
    else:
        owner, attribute, off = obj, "hide_render", True
    restore = pin_value(owner, attribute, off)
    try:
        yield
    finally:
        restore()


def timing(scene):
    return {"first_frame": scene.frame_start, "fps": scene.render.fps / scene.render.fps_base}


def draw(bpy, scene, path):
    scene.render.filepath = str(path)
    bpy.ops.render.render(write_still=True, scene=scene.name)


def render_job(job):
    import bpy
    from mathutils import Vector

    output = Path(job["output"])
    output.mkdir(parents=True, exist_ok=True)
    reference = {}
    report = {"angles": ANGLES, "cameras": []}
    up = Vector((0, 0, 1))
    for variant in ("before", "after"):
        model = job[variant]
        bpy.ops.wm.open_mainfile(filepath=model["path"], load_ui=False)
        rig = bpy.data.objects.get(model["armature"])
        if rig is None or rig.type != "ARMATURE":
            raise ValueError(f"unknown armature: {model['armature']!r}")
        scene = use_scene_of(bpy, rig)
        if variant == "before":
            report.update(timing(scene))
        elif report["first_frame"] != scene.frame_start or report["fps"] != timing(scene)["fps"]:
            raise ValueError("before and after must share a start frame and frame rate")
        unit = scene.unit_settings.scale_length
        scene.frame_set(scene.frame_start)
        base, side = landmarks(rig, model, rest=True)
        front = front_of(side)
        objects = subject_objects(scene, rig, model)
        camera, lights = camera_setup(bpy, scene, objects, job["size"])
        camera.data.clip_start = .0001 / unit
        for light, direction in zip(lights, (side * -.5 + front + up, side + front * .4 + up * .4)):
            light.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
        for event in job["events"]:
            name, frame = event["name"], event["frame"]
            if not scene.frame_start <= frame <= scene.frame_end:
                raise ValueError(f"{name}: frame {frame} outside {variant} timeline")
            rest = event.get("rest", False)
            rig.data.pose_position = "REST" if rest else "POSE"
            scene.frame_set(frame)
            framing = event.get("framing", {})
            anchor_name = framing.get("anchor", job["before"]["anchor"])
            scale_m = framing.get("scale", job["scale"])
            offset_m = framing.get("offset_z", job.get("offset_z", 0.0))
            camera.data.ortho_scale = scale_m / unit
            distance = max(10.0, scale_m * 4) / unit
            camera.data.clip_end = 2 * distance
            if variant == "before":
                anchor, direction = landmarks(rig, model, rest, anchor_name)
                delta = (anchor - base) * unit
                # Store metres and heading relative to the *rest* rig. Reusing the original
                # camera shows a change to the candidate pose rather than tracking it away.
                reference[name] = (delta.dot(side), delta.dot(front), delta.z,
                                   direction.dot(side), direction.dot(front))
            dx, dy, dz, ax, ay = reference[name]
            center = base + (side * dx + front * dy + up * (dz + offset_m)) / unit
            across = (side * ax + front * ay).normalized()
            for view in job.get("views", ANGLES):
                angle = math.radians(ANGLES[view])
                ray = front_of(across) * math.cos(angle) + across * math.sin(angle)
                camera.location = center + ray * distance
                camera.rotation_euler = (-ray).to_track_quat("-Z", "Y").to_euler()
                folder = output / name / view
                folder.mkdir(parents=True, exist_ok=True)
                draw(bpy, scene, folder / (variant + ".png"))
                if variant == "after" and event.get("disable"):
                    with disabled(objects, event["disable"]):
                        draw(bpy, scene, folder / "disabled.png")
                report["cameras"].append({"variant": variant, "event": name, "frame": frame, "view": view,
                                          "seconds": (frame - scene.frame_start) / report["fps"],
                                          "target_m": list(center * unit), "direction": list(ray),
                                          "matrix_world": [list(row) for row in camera.matrix_world],
                                          "ortho_scale_m": scale_m, "anchor_before": anchor_name})
    (output / "cameras.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    spec_path, = sys.argv[sys.argv.index("--") + 1:]
    render_job(json.loads(Path(spec_path).read_text(encoding="utf-8")))
