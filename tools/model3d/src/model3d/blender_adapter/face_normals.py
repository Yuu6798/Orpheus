"""Blender の面の角ごとに顔・首のカスタム法線を設定する。"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np


def normalized(vectors, fallback):
    # 値域の大きな法線変換や、中心・筒軸上のゼロベクトルを扱う。
    largest = np.abs(vectors).max(axis=1, keepdims=True)
    scaled = np.divide(vectors, largest, out=np.zeros_like(vectors), where=largest > 0)
    length = np.linalg.norm(scaled, axis=1, keepdims=True)
    return np.divide(scaled, length, out=fallback.copy(), where=length > 0)


def custom_normals(points, corners, linear, face=None, neck=None):
    """対象の角の世界座標と元の物体法線から、新しい物体法線を計算する。"""
    try:
        inverse = np.linalg.inv(linear)
    except np.linalg.LinAlgError as error:
        raise ValueError("物体の変換に逆行列がありません。縮尺 0 の軸を確認してください") from error
    if not np.isfinite(inverse).all() or not np.isfinite(points).all():
        raise ValueError("世界座標または物体の変換に有限でない値があります")
    original = normalized(corners @ inverse, corners)
    current = original
    if face is not None and face["weight"] > 0:
        outward = points - face["centre"]
        outward[:, 2] /= face["stretch_z"]
        target = normalized(outward, current)
        weight = face["weight"]
        current = normalized((1.0 - weight) * current + weight * target, current)
    if neck is not None:
        low, high = neck["height"]
        ramp = np.clip((points[:, 2] - low) / (high - low), 0.0, 1.0)
        weight = (1.0 - ramp * ramp * (3.0 - 2.0 * ramp))[:, None]
        outward = np.zeros_like(points)
        outward[:, :2] = points[:, :2] - neck["centre_xy"]
        target = normalized(outward, current)
        current = normalized((1.0 - weight) * current + weight * target, current)
    mapped = normalized(current @ linear, corners)
    return np.where(np.any(current != original, axis=1, keepdims=True), mapped, corners)


def _corner_normals(mesh):
    out = np.empty(len(mesh.loops) * 3, dtype=np.float64)
    mesh.corner_normals.foreach_get("vector", out)
    return out.reshape(-1, 3)


def _turns(before, after):
    cross = np.linalg.norm(np.cross(before, after), axis=1)
    dot = (before * after).sum(axis=1)
    return np.degrees(np.arctan2(cross, dot))


def _apply(obj, settings, unit):
    mesh = obj.data
    slots = {index for index, slot in enumerate(obj.material_slots)
             if slot.material is not None and slot.material.name == settings["material"]}
    selected = np.zeros(len(mesh.loops), dtype=bool)
    for polygon in mesh.polygons:
        if polygon.material_index in slots:
            selected[polygon.loop_start:polygon.loop_start + polygon.loop_total] = True
    if not selected.any():
        raise ValueError(f"{obj.name} に指定材質の面がありません: {settings['material']}")
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    world = (coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]) * unit
    loops = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loops)
    old = _corner_normals(mesh)
    new = old.copy()
    new[selected] = custom_normals(world[loops[selected]], old[selected], matrix[:3, :3], settings.get("face"), settings.get("neck"))
    if not np.isfinite(new).all():
        raise ValueError("計算したカスタム法線に有限でない値があります")
    copied = False
    if not np.array_equal(new, old):
        copied = mesh.users > 1
        if copied:
            mesh = mesh.copy()
            obj.data = mesh
        mesh.normals_split_custom_set(new.tolist())
        mesh.update()
    angle = _turns(old, _corner_normals(mesh))
    chosen = angle[selected]
    return {"corners": len(selected), "target_corners": int(selected.sum()), "changed_corners": int((chosen > 1e-4).sum()),
            "target_turn_deg": {"median": float(np.median(chosen)), "p95": float(np.percentile(chosen, 95)), "max": float(chosen.max())},
            "other_corners_max_turn_deg": float(angle[~selected].max(initial=0.0)), "mesh_copied": copied}


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for argument in ("blend", "settings", "out-blend", "report"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    obj = bpy.context.scene.objects.get(settings["object"])
    if obj is None or obj.type != "MESH":
        raise ValueError(f"対象のメッシュがありません: {settings['object']}")
    values = _apply(obj, settings, bpy.context.scene.unit_settings.scale_length)
    report = {"schema_version": "model3d-face-normals/0.1", "settings": settings, **values}
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()), copy=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
