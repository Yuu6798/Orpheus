"""Add VRM 1.0 shoulder and back-shield capsules for hair springs."""

import argparse
import json
import math
from pathlib import Path
import sys


SCHEMA = "model3d-vrm-hair-colliders/0.1"
SHOULDERS = "HairShoulders"
BACK_SHIELD = "HairBackShield"
SIDES = ("left", "right")
REQUIRED = tuple(f"{side}_{part}" for side in SIDES for part in ("shoulder", "upper_arm"))
OPTIONAL = ("left_lower_arm", "right_lower_arm", "upper_chest", "neck")
ROLES = (*REQUIRED, *OPTIONAL)


def check_settings(parameters):
    hair_prefix = parameters.get("hair_prefix", "Hair_")
    back_prefix = parameters.get("back_prefix", f"{hair_prefix}back_")
    for key, value in (("hair_prefix", hair_prefix), ("back_prefix", back_prefix)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} は空でない文字列にしてください")
    mode = parameters.get("back_shield", "back")
    if mode not in ("back", "all", "off"):
        raise ValueError("back_shield は back・all・off のいずれかにしてください")
    if mode == "back" and not back_prefix.casefold().startswith(hair_prefix.casefold()):
        raise ValueError("back_prefix は hair_prefix で始まる名前にしてください")
    scale = parameters.get("radius_scale", 1.0)
    if type(scale) not in (int, float) or not 0.1 <= scale <= 4:
        raise ValueError("radius_scale は 0.1〜4 の数にしてください")
    bones = parameters.get("bones", {})
    if not isinstance(bones, dict) or set(bones) - set(ROLES) or any(
        not isinstance(name, str) or not name.strip() for name in bones.values()
    ):
        raise ValueError(f"bones は役割から空でない骨名への対応にしてください: {', '.join(ROLES)}")
    return {"hair_prefix": hair_prefix, "back_prefix": back_prefix, "back_shield": mode,
            "radius_scale": scale, "bones": bones}


def choose_armature(objects, name):
    candidates = []
    for obj in objects:
        if obj.type != "ARMATURE" or (name and obj.name != name):
            continue
        extension = getattr(obj.data, "vrm_addon_extension", None)
        spring_bone = getattr(extension, "spring_bone1", None)
        if getattr(extension, "spec_version", None) == "1.0" and spring_bone and len(spring_bone.springs):
            candidates.append(obj)
    if len(candidates) != 1:
        raise ValueError(f"揺れもののある VRM 1.0 アーマチュアが {len(candidates)} 個あります。armature で 1 個選んでください")
    armature = candidates[0]
    if any(obj != armature and obj.type == "ARMATURE" and obj.data == armature.data for obj in objects):
        raise ValueError(f"アーマチュアのデータを複数の物体で共有しています: {armature.name}")
    return armature


def hair_targets(springs, settings):
    mode = settings["back_shield"]
    hair_prefix, back_prefix = (settings[key].casefold() for key in ("hair_prefix", "back_prefix"))
    targets = {SHOULDERS: [], BACK_SHIELD: []}
    for index, spring in enumerate(springs):
        name = spring.vrm_name.casefold()
        if not name.startswith(hair_prefix):
            continue
        if len(spring.joints) < 2:
            continue
        targets[SHOULDERS].append(index)
        if mode == "all" or (mode == "back" and name.startswith(back_prefix)):
            targets[BACK_SHIELD].append(index)
    if not targets[SHOULDERS]:
        raise ValueError(f"{settings['hair_prefix']!r} で始まり、節が 2 個以上の揺れものがありません")
    return {name: indices for name, indices in targets.items() if indices}


def capsule_layout(armature, settings, humanoid_bone, *, back_shield):
    bones = armature.data.bones
    overrides = settings["bones"]
    mapped = {}
    for role in ROLES:
        name = overrides.get(role) or humanoid_bone(armature, role)
        if role == "upper_chest" and not name:
            name = humanoid_bone(armature, "chest")
        if name:
            bone = bones.get(name)
            if bone is None or bone.length <= 1e-7:
                raise ValueError(f"コライダーを付ける有効な骨がありません: {role} → {name}")
            mapped[role] = bone
    missing = set(REQUIRED) - mapped.keys()
    if missing:
        raise ValueError(f"VRM の人型の割り当てか bones に肩・上腕を指定してください: {', '.join(sorted(missing))}")
    if len({bone.name for bone in mapped.values()}) != len(mapped):
        raise ValueError("コライダーの役割に同じ骨を複数指定できません")

    from mathutils import Vector

    left, right = (mapped[f"{side}_upper_arm"] for side in SIDES)
    width_axis = (left.head_local - right.head_local).normalized()
    if back_shield or "upper_chest" in mapped:
        head = bones.get(humanoid_bone(armature, "head"))
        hips = bones.get(humanoid_bone(armature, "hips"))
        up = (head.head_local - hips.head_local).normalized() if head and hips else Vector((0, 0, 1))
        front = width_axis.cross(up)
        if front.length <= 1e-7:
            raise ValueError("上腕と人型の骨の基本姿勢から体の前方を決められません")
        front.normalize()

    # 上腕の長さで半径を変え、カプセルの先端はモデルの骨の長さに合わせる。
    size = (left.length + right.length) / 2
    radius_scale = settings["radius_scale"]
    layout = {SHOULDERS: []}
    if back_shield:
        layout[BACK_SHIELD] = []

    def capsule(group, bone, radius, start, end, shift=None):
        local = bone.matrix_local.to_3x3().inverted() @ shift if shift is not None else Vector((0, 0, 0))
        layout[group].append({"bone": bone.name, "radius": radius * size * radius_scale,
                              "offset": tuple(local + Vector((0, start, 0))),
                              "tail": tuple(local + Vector((0, end, 0)))})

    for side in SIDES:
        shoulder = mapped[f"{side}_shoulder"]
        upper = mapped[f"{side}_upper_arm"]
        capsule(SHOULDERS, shoulder, 0.24, shoulder.length * 0.1, shoulder.length)
        capsule(SHOULDERS, upper, 0.23, -0.06 * size, upper.length)
        lower = mapped.get(f"{side}_lower_arm")
        if lower:
            capsule(SHOULDERS, lower, 0.20, 0, lower.length)
        if back_shield:
            capsule(BACK_SHIELD, shoulder, 0.28, 0, shoulder.length, front * (0.05 * size))
    chest = mapped.get("upper_chest")
    if chest:
        for sign in (-1, 1):
            shift = (width_axis * (0.23 * sign) + front * 0.13) * size
            capsule(SHOULDERS, chest, 0.30, -0.15 * size, 0.32 * size, shift)
    neck = mapped.get("neck")
    if back_shield and neck:
        capsule(BACK_SHIELD, neck, 0.22, -0.04 * size, neck.length, front * (0.06 * size))
    return layout, {role: bone.name for role, bone in mapped.items()}


def matches_capsule(collider, spec):
    if (collider.node.bone_name != spec["bone"] or collider.shape_type != "Capsule"
            or collider.extensions.vrmc_spring_bone_extended_collider.enabled):
        return False
    shape = collider.shape.capsule
    tolerance = max(1e-6, spec["radius"] * 1e-4)
    return (math.dist(shape.offset, spec["offset"]) <= tolerance
            and math.dist(shape.tail, spec["tail"]) <= tolerance
            and math.isclose(shape.radius, spec["radius"], abs_tol=tolerance))


def existing_groups(spring_bone, layout, targets):
    """Validate both named groups before editing so an existing rig is safe to rerun."""
    existing = {}
    colliders = {}
    for collider in spring_bone.colliders:
        colliders.setdefault(collider.uuid, []).append(collider)
    for name, specs in layout.items():
        named = [group for group in spring_bone.collider_groups if group.vrm_name == name]
        if not named:
            continue
        group = named[0]
        unrelated = [spring.vrm_name for index, spring in enumerate(spring_bone.springs) if index not in targets[name]
                     and any(ref.collider_group_uuid == group.uuid for ref in spring.collider_groups)]
        refs = [ref.collider_uuid for ref in group.colliders]
        # Compare as an unordered collection so reordering in Blender is safe.
        remaining = list(specs)
        for ref in refs:
            candidates = colliders.get(ref, [])
            match = next((i for i, spec in enumerate(remaining) if len(candidates) == 1 and ref
                          and matches_capsule(candidates[0], spec)), None)
            if match is not None:
                remaining.pop(match)
        if (len(named) != 1 or not group.uuid or sum(other.uuid == group.uuid for other in spring_bone.collider_groups) != 1
                or len(set(refs)) != len(refs) or len(refs) != len(specs) or remaining or unrelated):
            raise ValueError(f"既存の {name} が今回の形・参照と一致しません。入力側のグループと鎖の指定を確認してください"
                             + (f"（対象外の鎖: {', '.join(unrelated)}）" if unrelated else ""))
        existing[name] = group.uuid
    return existing


def add_groups(context, armature, spring_bone, layout, targets):
    existing = existing_groups(spring_bone, layout, targets)
    results = {}
    for name, specs in layout.items():
        group_uuid = existing.get(name)
        if not group_uuid:
            # The VRM Add-on helpers assign UUIDs before the references are written.
            group = spring_bone.add_collider_group()
            group.vrm_name = name
            group_uuid = group.uuid
            if not group_uuid:
                raise ValueError(f"VRM Add-on が {name} に UUID を割り当てられませんでした")
            for spec in specs:
                collider = spring_bone.add_collider(context, armature)
                collider.node.bone_name = spec["bone"]
                collider.shape_type = "Capsule"
                context.view_layer.update()
                shape = collider.shape.capsule
                shape.offset = spec["offset"]
                context.view_layer.update()
                shape.tail = spec["tail"]
                shape.radius = spec["radius"]
                context.view_layer.update()
                if not collider.uuid or not matches_capsule(collider, spec):
                    raise ValueError(f"カプセルの設定を読み戻せません: {spec['bone']}（{name}）")
                reference = group.add_collider()
                reference.collider_uuid = collider.uuid
                if reference.collider_uuid != collider.uuid:
                    raise ValueError(f"カプセルの参照を設定できません: {spec['bone']}（{name}）")
        attached = []
        for index in targets[name]:
            spring = spring_bone.springs[index]
            if not any(ref.collider_group_uuid == group_uuid for ref in spring.collider_groups):
                reference = spring.add_collider_group()
                reference.collider_group_uuid = group_uuid
                if reference.collider_group_uuid != group_uuid:
                    raise ValueError(f"揺れものにグループを接続できません: {spring.vrm_name}（{name}）")
                attached.append(spring.vrm_name)
        results[name] = {"uuid": group_uuid, "created": name not in existing, "capsules": specs,
                         "springs": [spring_bone.springs[i].vrm_name for i in targets[name]], "attached": attached}
    return results


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for name in ("blend", "out-blend", "settings", "report"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.blend.resolve() == args.out_blend.resolve():
        raise ValueError("出力の .blend は入力と別のファイルにしてください")
    raw = json.loads(args.settings.read_text(encoding="utf-8"))
    settings = check_settings(raw)
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    armature = choose_armature(bpy.data.objects, raw["armature"])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from vrm_bake_springs import humanoid_bone, use_scene_of

    use_scene_of(bpy, armature)
    spring_bone = armature.data.vrm_addon_extension.spring_bone1
    targets = hair_targets(spring_bone.springs, settings)
    layout, bones = capsule_layout(armature, settings, humanoid_bone, back_shield=BACK_SHIELD in targets)
    groups = add_groups(bpy.context, armature, spring_bone, layout, targets)
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()))
    report = {"schema_version": SCHEMA, "armature": armature.name, "settings": settings, "bones": bones,
              "groups": groups, "blender": bpy.app.version_string}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
