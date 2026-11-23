"""レシピから与える骨の回し方を確かめる。"""
import math

from ..io import ToolError


def pose_rotations(value, where):
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
