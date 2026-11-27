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


def _world_corners(obj, unit):
    mesh = obj.data
    matrix = np.asarray(obj.matrix_world, dtype=np.float64)
    coords = np.empty(len(mesh.vertices) * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    world = (coords.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]) * unit
    loops = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loops)
    return world[loops], matrix[:3, :3]


def _write_normals(obj, old, new, selected, preserve_auto=False):
    if not np.isfinite(new).all():
        raise ValueError("計算したカスタム法線に有限でない値があります")
    mesh = obj.data
    copied = False
    if not np.array_equal(new, old):
        if preserve_auto and not mesh.has_custom_normals:
            # 0 は Blender の自動法線を使う印。マスク外を固定法線に変えない。
            new[~selected] = 0
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
    world, linear = _world_corners(obj, unit)
    old = _corner_normals(mesh)
    new = old.copy()
    new[selected] = custom_normals(world[selected], old[selected], linear, settings.get("face"), settings.get("neck"))
    return _write_normals(obj, old, new, selected)


def crown_normals(points, corners, linear, settings):
    """中心から放射状に楕円体へ投影し、その点の法線を高さマスクで混ぜる。"""
    low, high = settings["height"]
    if not np.isfinite(points).all():
        raise ValueError("世界座標に有限でない値があります")
    selected = points[:, 2] > low
    mapped = corners.copy()
    if not selected.any() or not settings["weight"]:
        return mapped, selected
    try:
        inverse = np.linalg.inv(linear)
    except np.linalg.LinAlgError as error:
        raise ValueError("物体の変換に逆行列がありません。縮尺 0 の軸を確認してください") from error
    if not np.isfinite(inverse).all():
        raise ValueError("物体の変換に有限でない値があります")
    original = normalized(corners[selected] @ inverse, corners[selected])
    radii = np.asarray(settings["radii"])
    # 投影点と元の点の位置ベクトルは平行なので、楕円体上の距離を解かずに勾配の向きを求められる。
    with np.errstate(over="ignore", invalid="ignore"):
        outward = (points[selected] - settings["centre"]) / radii / radii
    if not np.isfinite(outward).all():
        raise ValueError("楕円体の法線を計算できません。centre と radii を確認してください")
    target = normalized(outward, original)
    ramp = np.clip((points[selected, 2] - low) / (high - low), 0, 1)
    weight = (settings["weight"] * ramp * ramp * (3 - 2 * ramp))[:, None]
    current = normalized((1 - weight) * original + weight * target, original)
    mapped[selected] = np.where(np.any(current != original, axis=1, keepdims=True),
                                normalized(current @ linear, corners[selected]), corners[selected])
    return mapped, selected


def _apply_crown(obj, settings, unit):
    points, linear = _world_corners(obj, unit)
    old = _corner_normals(obj.data)
    new, selected = crown_normals(points, old, linear, settings)
    if not selected.any():
        raise ValueError(f"{obj.name} に height の下端より高い面の角がありません")
    return {"object": obj.name, **_write_normals(obj, old, new, selected, preserve_auto=True)}


def main(argv=None):
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    for argument in ("blend", "settings", "out-blend", "report"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    parser.add_argument("--crown", action="store_true")
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    names = settings["objects"] if args.crown else [settings["object"]]
    objects = [bpy.context.scene.objects.get(name) for name in names]
    for name, obj in zip(names, objects):
        if obj is None or obj.type != "MESH":
            raise ValueError(f"対象のメッシュがありません: {name}")
    unit = bpy.context.scene.unit_settings.scale_length
    if args.crown:
        report = {"schema_version": "model3d-crown-normals/0.1", "settings": settings,
                  "objects": [_apply_crown(obj, settings, unit) for obj in objects]}
    else:
        report = {"schema_version": "model3d-face-normals/0.1", "settings": settings, **_apply(objects[0], settings, unit)}
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()), copy=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
