"""胸・頭に近づく袖の骨を、揺れもののキーの上から下・外・後ろへ向ける。"""

import argparse
import json
import math
import sys
from pathlib import Path


DEFAULTS = {"strength": 1.0, "smooth_s": 0.15, "lead_s": 0.13, "height_m": 0.18,
            "distance_m": 0.42, "max_angle_deg": 105.0, "left_axis": "+X", "back_axis": "-Z"}
AXES = {sign + axis: tuple((1 if sign == "+" else -1) if component == axis else 0 for component in "XYZ")
        for sign in "+-" for axis in "XYZ"}


def smooth_weights(values, sigma, lead):
    """前後に効く区間を伸ばし、端ではあるコマだけで重みをならす。"""
    count = len(values)
    lead = min(lead, count - 1)
    envelope = [max(values[max(0, index - lead):index + lead + 1]) for index in range(count)]
    if sigma <= 1e-12:
        return envelope
    radius = math.ceil(min(3 * sigma, count - 1))
    kernel = [math.exp(-0.5 * (offset / sigma) ** 2) for offset in range(radius + 1)]
    result = []
    for index in range(count):
        neighbours = range(max(0, index - radius), min(count, index + radius + 1))
        weights = [kernel[abs(other - index)] for other in neighbours]
        result.append(sum(envelope[other] * weight for other, weight in zip(neighbours, weights)) / sum(weights))
    return result


def proximity(height, distance, forward, settings):
    above = min(1.0, max(0.0, height / settings["height_m"]))
    near = min(1.0, max(0.0, 2 * (1 - distance / settings["distance_m"])))
    return max(above * (0.55 + 0.45 * forward), near * (0.65 + 0.35 * forward))


def angle_between(first, second):
    return math.degrees(math.atan2(first.cross(second).length, first.dot(second)))


def choose_armature(scene, name):
    matches = [obj for obj in scene.objects if obj.type == "ARMATURE" and (not name or obj.name == name)]
    if len(matches) != 1:
        raise ValueError("シーンのアーマチュアを 1 つ指定してください（armature）")
    return matches[0]


def chains_in(armature, settings):
    bones = armature.pose.bones
    for name in (settings["chest"], settings["head"]):
        if name not in bones:
            raise ValueError(f"姿勢を測る骨がありません: {name}")
    chains = []
    for side, sign in (("left", 1.0), ("right", -1.0)):
        for chain_names in settings[side]:
            chain = []
            for name in chain_names:
                bone = bones.get(name)
                if bone is None:
                    raise ValueError(f"袖の骨がありません: {name}")
                if chain and bone.parent != chain[-1]:
                    raise ValueError(f"袖の鎖は親から直接の子へ並べてください: {chain[-1].name}, {name}")
                if bone.rotation_mode != "QUATERNION" or any(not c.mute and c.influence for c in bone.constraints):
                    raise ValueError(f"袖の骨は拘束のない四元数の焼き付けを使ってください: {name}")
                chain.append(bone)
            chains.append((side, sign, chain))
    return chains


def chest_axes(world, chest, left_axis, back_axis):
    orientation = (world @ chest.matrix).to_quaternion()
    left = orientation @ left_axis
    back = orientation @ back_axis
    left.z = back.z = 0.0
    if min(left.length, back.length) < 1e-6:
        raise ValueError("胸の左右・後ろの軸を水平面に写せません（left_axis・back_axis）")
    return left.normalized(), back.normalized()


def read_weights(scene, armature, settings, chains, frames, fps):
    from mathutils import Vector

    raw = {"left": [], "right": []}
    chest = armature.pose.bones[settings["chest"]]
    head = armature.pose.bones[settings["head"]]
    left_axis = Vector(AXES[settings["left_axis"]])
    back_axis = Vector(AXES[settings["back_axis"]])
    bases = []
    for frame in frames:
        scene.frame_set(frame)
        world = armature.matrix_world
        chest_pos = world @ chest.head
        head_pos = world @ head.head
        left, back = chest_axes(world, chest, left_axis, back_axis)
        bases.append((left, back))
        current = {"left": 0.0, "right": 0.0}
        for side, _, chain in chains:
            root = chain[0]
            start, end = world @ root.head, world @ root.tail
            height = (max(start.z, end.z) - chest_pos.z) * scene.unit_settings.scale_length
            distance = min((start - head_pos).length, (end - head_pos).length) * scene.unit_settings.scale_length
            direction = (end - start).normalized()
            forward = min(1.0, (max(0.0, direction.z + 0.35) + max(0.0, -direction.dot(back) - 0.15)) / 0.65)
            current[side] = max(current[side], proximity(height, distance, forward, settings))
        for side, value in current.items():
            raw[side].append(value)
    lead = round(min(fps * settings["lead_s"], len(frames) - 1))
    return bases, {side: smooth_weights(values, fps * settings["smooth_s"], lead)
                   for side, values in raw.items()}


def aim_bone(bone, world, target, strength, max_angle):
    from mathutils import Quaternion

    matrix = world @ bone.matrix
    transform = matrix.to_3x3()
    sizes = [column.length for column in transform.col]
    directions = [column.normalized() for column in transform.col]
    skew = max(abs(directions[a].dot(directions[b])) for a, b in ((0, 1), (0, 2), (1, 2)))
    if min(sizes) <= 1e-8 or max(sizes) - min(sizes) > 1e-5 * max(sizes) or transform.determinant() <= 0 or skew > 1e-5:
        raise ValueError(f"袖の骨に反転、せん断、または一様でない拡大があります: {bone.name}")
    orientation = matrix.to_quaternion()
    direction = (world.to_3x3() @ (bone.tail - bone.head)).normalized()
    axis, angle = direction.rotation_difference(target).to_axis_angle()
    swing = Quaternion(axis, min(angle, max_angle) * strength)
    local = orientation.inverted() @ swing @ orientation
    original = bone.rotation_quaternion
    result = (original @ local).normalized()
    if result.dot(original) < 0:
        result.negate()
    bone.rotation_quaternion = result
    return swing @ direction


def bake(scene, view_layer, armature, chains, frames, bases, weights, settings):
    from mathutils import Vector

    max_angle = math.radians(settings["max_angle_deg"])
    down = Vector((0, 0, -1))
    expected = {}
    for index, (frame, (left, back)) in enumerate(zip(frames, bases)):
        scene.frame_set(frame)
        applied = {}
        for side, sign, chain in chains:
            strength = settings["strength"] * weights[side][index]
            if strength < 0.02:
                continue
            for i, bone in enumerate(chain):
                segment = i / max(1, len(chain) - 1)
                target = ((0.72 + 0.20 * segment) * down
                          + (0.44 - 0.20 * segment) * sign * left + (0.54 - 0.24 * segment) * back).normalized()
                direction = aim_bone(bone, armature.matrix_world, target, strength * (0.88 - 0.23 * segment), max_angle)
                if not bone.keyframe_insert("rotation_quaternion", frame=frame):
                    raise ValueError(f"袖の回転をキーにできません: {bone.name}、{frame} コマ")
                applied[bone.name] = direction
                view_layer.update()
        if applied:
            expected[frame] = applied
    worst = 0.0
    for frame, directions in expected.items():
        scene.frame_set(frame)
        world = armature.matrix_world.to_3x3()
        for name, direction in directions.items():
            bone = armature.pose.bones[name]
            worst = max(worst, angle_between(world @ (bone.tail - bone.head), direction))
    return expected, worst


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for name in ("blend", "out-blend", "settings", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    if args.blend.resolve() == args.out_blend.resolve():
        raise ValueError("出力の .blend は入力と別のファイルにしてください")
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    scene = bpy.context.scene
    armature = choose_armature(scene, settings["armature"])
    chains = chains_in(armature, settings)
    data = armature.animation_data
    if data is None or data.action is None or any(not track.mute for track in data.nla_tracks):
        raise ValueError("袖を焼き付けた単一のアクションが要ります（NLA で混ぜた動きは事前に焼いてください）")
    original_frame = scene.frame_current, scene.frame_subframe
    first, last = settings.get("frames") or [scene.frame_start, scene.frame_end]
    if not scene.frame_start <= first <= last <= scene.frame_end:
        raise ValueError("frames は .blend のシーンのコマの範囲内にしてください")
    frames = range(first, last + 1)
    fps = scene.render.fps / scene.render.fps_base
    bases, weights = read_weights(scene, armature, settings, chains, frames, fps)
    if data.action.users > 1 and any(settings["strength"] * max(values) >= 0.02 for values in weights.values()):
        data.action = data.action.copy()
    modified, worst = bake(scene, bpy.context.view_layer, armature, chains, frames, bases, weights, settings)
    if worst > 0.1:
        raise ValueError(f"袖の焼き付けを読み直した角度が狙いと違います（最大 {worst:.4g}°）")
    scene.frame_set(original_frame[0], subframe=original_frame[1])
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()))
    report = {"schema_version": "model3d-vrm-drape-sleeves/0.1", "blender": bpy.app.version_string,
              "armature": armature.name, "frames": [first, last], "fps": fps, "frames_modified": len(modified),
              "keys": sum(len(bones) for bones in modified.values()), "bake_check_deg": worst,
              "sides": {side: {"max_weight": max(values), "active_frames": sum(
                  settings["strength"] * value >= 0.02 for value in values)} for side, values in weights.items()},
              "settings": settings}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
