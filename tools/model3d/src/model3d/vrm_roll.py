"""VRMC_node_constraint の roll を、保存された node のローカル回転から検算する。"""

from itertools import product
from pathlib import Path

import numpy as np

from .animation_check import quaternion
from .glb import read_glb
from .io import ToolError
from .motion_remix import mul_quat
from .vrm import _dict, _items, _list, _node, _number, _vector


DEFAULT_TWISTS = (-120, -60, 0, 60, 120)
DEFAULT_SWINGS = (0, 30, 60, 90, 120)
SWING_DIRECTIONS = (0, 90, 180, 270)
IDENTITY = np.array([1., 0., 0., 0.])
CONJUGATE = np.array([1., -1., -1., -1.])


def _rest_rotation(node):
    """glTF のローカル回転を (w, x, y, z) にする。matrix は列優先。"""
    if "matrix" in node:
        matrix = node["matrix"]
        if not _vector(matrix, 16) or any(key in node for key in ("translation", "rotation", "scale")):
            raise ValueError("matrix は 16 個の有限な数で、TRS と同時に指定できません")
        matrix = np.array(matrix, dtype=float).reshape(4, 4).T
        if not np.allclose(matrix[3], (0, 0, 0, 1), rtol=0, atol=1e-8):
            raise ValueError("matrix はアフィン変換にしてください")
        rotation = matrix[:3, :3]
        largest = np.max(np.abs(rotation), axis=0)
        if (largest == 0).any():
            raise ValueError("matrix のスケールが 0 です")
        rotation = rotation / largest
        rotation /= np.linalg.norm(rotation, axis=0)
        if not np.allclose(rotation.T @ rotation, np.eye(3), rtol=0, atol=1e-5):
            raise ValueError("matrix は回転とスケールに分けられません")
        if np.linalg.det(rotation) < 0:  # three.js の分解と同じく反転を X のスケール側に置く
            rotation[:, 0] *= -1
        return quaternion(rotation)
    if "rotation" not in node:
        return IDENTITY
    rotation = node["rotation"]
    if not _vector(rotation, 4) or not any(rotation):
        raise ValueError("rotation は 0 でない四元数にしてください")
    result = np.array([rotation[3], *rotation[:3]], dtype=float)
    result /= np.max(np.abs(result))
    return result / np.linalg.norm(result)


def _axis_angles(axes, degrees):
    half = np.deg2rad(degrees)[:, None] / 2
    return np.concatenate([np.cos(half), axes * np.sin(half)], axis=1)


def _poses(twists, swings, axis):
    cases = np.array(list(product(twists, swings, SWING_DIRECTIONS)), dtype=float)
    twist, swing, direction = cases.T
    axes = np.eye(3)
    perpendicular = np.delete(axes, axis, axis=0)
    chosen = np.array([perpendicular[0], perpendicular[1], -perpendicular[0], -perpendicular[1]])
    swing_axes = chosen[direction.astype(int) // 90]
    return cases, mul_quat(_axis_angles(swing_axes, swing), _axis_angles(axes[axis], twist))


def _measure_roll(source_rest, target_rest, poses, axis, weight):
    """読む側の q を作り、軸方向の四元数成分から最小の swing を除いた roll を求める。"""
    cases, src_delta = poses
    relative = mul_quat(target_rest * CONJUGATE, source_rest)
    dst_delta = mul_quat(mul_quat(relative, src_delta), relative * CONJUGATE)
    # dst_delta = swing × roll。roll の (w, 軸成分) は dst_delta を軸に射影したものと同じ比。
    # 各入力角が 180 度未満なので最短回転を使い、重みに応じた slerp を符号付き角度の積で表せる。
    sign = np.where(dst_delta[:, 0] < 0, -1., 1.)
    roll = 2 * weight * np.rad2deg(np.arctan2(dst_delta[:, axis + 1] * sign, np.abs(dst_delta[:, 0])))
    expected = weight * cases[:, 0]
    error = np.abs((roll - expected + 180.) % 360. - 180.)
    error[error < 1e-12] = 0.
    worst = int(np.argmax(error))
    return {
        "rest_differs_deg": float(2 * np.rad2deg(np.arctan2(np.linalg.norm(relative[1:]), abs(relative[0])))),
        "max_error_deg": float(error[worst]),
        "max_error_case": dict(zip(("twist", "swing", "swing_direction", "got", "want"),
                                   [*cases[worst].tolist(), float(roll[worst]), float(expected[worst])])),
        "max_twist_when_only_swung_deg": float(np.max(np.abs(roll[cases[:, 0] == 0]))),
    }


def _angles(parameters, key, defaults, signed):
    values = parameters.get(key, list(defaults))
    minimum = -180 if signed else 0
    if not isinstance(values, list) or not values or not all(
            _number(v) and minimum <= v < 180 and v != -180 for v in values):
        bounds = "-180 より大きく 180 未満" if signed else "0 以上 180 未満"
        raise ToolError(f"{key} は {bounds}の度数の並びにしてください")
    return sorted({0, *values})


def inspect(document, parameters):
    """roll ごとに単独で元 node を動かし、重み × 元 node のねじれからの差を測る。"""
    twists = _angles(parameters, "twists_deg", DEFAULT_TWISTS, signed=True)
    swings = _angles(parameters, "swings_deg", DEFAULT_SWINGS, signed=False)
    if len(twists) * len(swings) * len(SWING_DIRECTIONS) > 10_000:
        raise ToolError("twists_deg と swings_deg の組は、倒す 4 方向を含めて 10,000 以下にしてください")
    tolerance = parameters.get("tolerance_deg")
    if "tolerance_deg" in parameters and (not _number(tolerance) or tolerance < 0):
        raise ToolError("tolerance_deg は 0 以上の有限な数にしてください")

    nodes = _items(document, "nodes")
    names = [node.get("name") if isinstance(node.get("name"), str) else None for node in nodes]
    parents = {child: i for i, node in enumerate(nodes) for child in _list(node.get("children"))
               if _node(child, len(nodes))}
    parent_names = {child: names[parent] for child, parent in parents.items()}
    poses = {}
    constraints, failures = [], []
    for i, node in enumerate(nodes):
        extension = _dict(_dict(node.get("extensions")).get("VRMC_node_constraint"))
        constraint = _dict(extension.get("constraint"))
        if "roll" not in constraint:
            continue
        body = _dict(constraint["roll"])
        source, axis, weight = body.get("source"), body.get("rollAxis"), body.get("weight", 1.)
        source_ok = _node(source, len(nodes))
        axis_ok = axis in ("X", "Y", "Z")
        weight_ok = isinstance(weight, (int, float)) and not isinstance(weight, bool) and 0 <= weight <= 1
        row = {"node_index": i, "node": names[i], "source_index": source if source_ok else None,
               "source": names[source] if source_ok else None, "axis": axis if axis_ok else None,
               "weight": weight if weight_ok else None, "parent": parent_names.get(i)}
        constraints.append(row)
        try:
            if not source_ok or source == i:
                raise ValueError("source は別の node の番号にしてください")
            if not axis_ok or not weight_ok:
                raise ValueError("rollAxis は X、Y、Z のいずれかで、weight は 0〜1 にしてください")
            row["source_parent"] = parent_names.get(source)
            axis_index = "XYZ".index(axis)
            if axis_index not in poses:
                poses[axis_index] = _poses(twists, swings, axis_index)
            row.update(_measure_roll(_rest_rotation(nodes[source]), _rest_rotation(node),
                                     poses[axis_index], axis_index, weight))
        except (ValueError, OverflowError) as error:
            failures.append(f"node {i}: {error}")
            row["unread"] = str(error)
            continue
        if tolerance is not None and row["max_error_deg"] > tolerance:
            failures.append(f"node {i}: 最大のずれ {row['max_error_deg']:.6g} 度が許容値 {tolerance} 度を超えています")

    return {"schema_version": "model3d-vrm-roll-check/0.1",
            "twists_deg": twists, "swings_deg": swings, "swing_directions_deg": list(SWING_DIRECTIONS),
            "tolerance_deg": tolerance, "roll_constraints": len(constraints), "constraints": constraints,
            "pass": not failures if constraints else None, "failures": failures,
            "reason": None if constraints else "roll の拘束がありません"}


def inspect_file(path, parameters):
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ToolError(f"VRM を読めません: {path}: {error}") from error
    document, _ = read_glb(data, str(path))
    return inspect(document, parameters)
