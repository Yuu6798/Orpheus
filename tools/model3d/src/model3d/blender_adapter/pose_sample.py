"""Blender 内で静止姿勢を作る。ファイルは書き換えない。"""
import math


def prepare_pose_samples(armature, rotations):
    missing = sorted({step["bone"] for step in rotations} - set(armature.pose.bones.keys()))
    if missing:
        raise SystemExit(f"{armature.name} に姿勢を与える骨がありません: {missing}")
    if armature.animation_data:
        armature.animation_data_clear()
    armature.data.pose_position = "POSE"


def apply_pose_sample(armature, rotations, view_layer):
    from mathutils import Matrix, Vector

    for bone in armature.pose.bones:
        bone.matrix_basis = Matrix.Identity(4)
    view_layer.update()
    for step in rotations:
        bone = armature.pose.bones[step["bone"]]
        axis = Vector((step["axis"] == "X", step["axis"] == "Y", step["axis"] == "Z"))
        if step["space"] == "local":
            axis = (bone.matrix.to_3x3() @ axis).normalized()
        head = bone.matrix.to_translation()
        rotation = Matrix.Rotation(math.radians(step["degrees"]), 4, axis)
        bone.matrix = Matrix.Translation(head) @ rotation @ Matrix.Translation(-head) @ bone.matrix
        view_layer.update()
