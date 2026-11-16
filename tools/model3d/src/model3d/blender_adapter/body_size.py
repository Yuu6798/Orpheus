"""Blender の中で動かす: 体の太さを初期姿勢で測る（check.body_size。測るだけで、.blend は変えない）。

    blender --background --factory-startup --python body_size.py -- --blend model.blend --settings settings.json --report report.json

- 腕・脚（limbs の [骨, 位置] ごと）: 骨に沿った位置 at（0 = 付け根、1 = 先）の ±window の帯にある、groups（既定はその骨）が
  いちばん重い頂点を、骨の軸に垂直な面へ写し、広がりの大きい 2 つの向き（主成分）の幅を直径として測る。
- 胴（torso の高さごと）: groups がいちばん重い頂点のうち、高さ z の ±band・|x| < half_width の帯の、左右の幅と前後の厚み。
- 背の高さ: 体のメッシュのいちばん高い頂点。
長さは cm（台本は 0.1 cm に丸めて出した。ここは丸めない）。頂点が min_points に満たない所は null。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import bone_world, top_groups, world_points
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import bone_world, top_groups, world_points
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"body": None, "armature": None, "limbs": [], "torso": None, "window": 0.06, "min_points": 6}
TORSO = {"groups": [], "heights": [], "band": 0.012, "half_width": 0.25, "min_points": 7}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="body_size.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    out = {**DEFAULTS, **settings}
    if out.get("torso") is not None:
        out["torso"] = {**TORSO, **out["torso"]}
    return out


def limb_diameters(points, top, head, tail, groups, at, window, min_points):
    """骨に沿った位置 at の帯の、骨の軸に垂直な広がりの大きい 2 つの向きの幅（m）と頂点の数。足りなければ (None, 数)。"""
    axis = np.asarray(tail) - np.asarray(head)
    length = np.linalg.norm(axis)
    axis /= length
    relative = points - head
    along = relative @ axis / length
    sel = np.isin(top, groups) & (np.abs(along - at) < window)
    if sel.sum() < min_points:
        return None, int(sel.sum())
    perpendicular = relative[sel] - np.outer(relative[sel] @ axis, axis)
    u = np.linalg.svd(perpendicular - perpendicular.mean(0), full_matrices=False)[2]
    a, b = perpendicular @ u[0], perpendicular @ u[1]
    return [float(a.max() - a.min()), float(b.max() - b.min())], int(sel.sum())


def torso_sizes(points, top, torso):
    """高さごとの胴の左右の幅と前後の厚み（m）。"""
    rows = []
    family = np.isin(top, torso["groups"])
    for z in torso["heights"]:
        sel = family & (np.abs(points[:, 2] - z) < torso["band"]) & (np.abs(points[:, 0]) < torso["half_width"])
        if sel.sum() >= torso["min_points"]:
            rows.append({"z_m": z, "width_cm": float(points[sel, 0].max() - points[sel, 0].min()) * 100,
                         "depth_cm": float(points[sel, 1].max() - points[sel, 1].min()) * 100, "vertices": int(sel.sum())})
        else:
            rows.append({"z_m": z, "width_cm": None, "depth_cm": None, "vertices": int(sel.sum())})
    return rows


def measure(points, top, bones, settings):
    """体の太さの報告（limbs・torso・背の高さ）。bones は {骨: (付け根, 先)}（世界の座標）。"""
    limbs = []
    for entry in settings["limbs"]:
        groups = entry.get("groups") or [entry["bone"]]
        for at in entry["at"]:
            diameters, count = limb_diameters(points, top, *bones[entry["bone"]], groups, at, settings["window"], settings["min_points"])
            limbs.append({"bone": entry["bone"], "at": at, "diameters_cm": None if diameters is None else [v * 100 for v in diameters],
                          "vertices": count})
    torso = torso_sizes(points, top, settings["torso"]) if settings.get("torso") else []
    return {"height_m": float(points[:, 2].max()), "limbs": limbs, "torso": torso}


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not settings["armature"] or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + settings['armature'] + '）' if settings['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    body = bpy.data.objects.get(settings["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"body のメッシュがありません: {settings['body']}")
    wanted = sorted({entry["bone"] for entry in settings["limbs"]})
    absent = [bone for bone in wanted if bone not in armature.data.bones]
    if absent:
        raise SystemExit(f"{armature.name} に無い骨があります（limbs）: {absent}")
    points = world_points(body)                 # 頂点の基本の形（骨の姿勢に左右されない）
    report = measure(points, top_groups(body), {name: bone_world(armature, name) for name in wanted}, settings)
    report = {"schema_version": "model3d-body-size/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "armature": armature.name, "body": body.name, "verts": len(points), **report, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"limbs": len(report["limbs"]), "torso": len(report["torso"])}))


if __name__ == "__main__":
    main()
