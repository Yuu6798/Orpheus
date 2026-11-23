"""レシピから与える骨の回し方を確かめる。"""
import math

from ..io import ToolError


def named_poses(parameters):
    poses = parameters["poses"]
    if parameters.get("frames") is not None or not isinstance(poses, list) or not poses:
        raise ToolError("poses は、frames を省いて空でない姿勢の並びで渡してください")
    checked = []
    for index, pose in enumerate(poses):
        where = f"poses[{index}]"
        if not isinstance(pose, dict) or set(pose) != {"name", "rotations"}:
            raise ToolError(f"{where} は name と rotations にしてください")
        name = pose["name"]
        if not isinstance(name, str) or not name:
            raise ToolError(f"{where}.name は空でない名前にしてください")
        checked.append({"name": name, "rotations": _pose_rotations(pose["rotations"], f"{where}.rotations")})
    if len({pose["name"] for pose in checked}) != len(checked):
        raise ToolError("poses の name が重なっています")
    return checked


def _pose_rotations(value, where):
    if not isinstance(value, list):
        raise ToolError(f"{where} は骨を回す順の配列にしてください")
    rotations = []
    for index, step in enumerate(value):
        label = f"{where}[{index}]"
        if not isinstance(step, dict) or set(step) - {"bone", "axis", "degrees", "space"}:
            raise ToolError(f"{label} は bone・axis・degrees と任意の space にしてください")
        bone, axis, degrees = step.get("bone"), step.get("axis"), step.get("degrees")
        space = step.get("space", "local")
        if not isinstance(bone, str) or not bone.strip():
            raise ToolError(f"{label}.bone は空でない骨の名前にしてください")
        if axis not in ("X", "Y", "Z") or space not in ("local", "armature"):
            raise ToolError(f"{label} の axis は X/Y/Z、space は local/armature にしてください")
        if isinstance(degrees, bool) or not isinstance(degrees, (float, int)) or not math.isfinite(degrees):
            raise ToolError(f"{label}.degrees は有限の角度にしてください")
        rotations.append({"bone": bone, "axis": axis, "degrees": degrees, "space": space})
    return rotations
