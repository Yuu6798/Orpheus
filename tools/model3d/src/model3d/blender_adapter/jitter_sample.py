"""Blender の中で動かす: メッシュの頂点が、前後のコマの中間からどれだけずれるか（ちらつき）を測る（check.jitter）。

    blender --background --factory-startup --python jitter_sample.py -- --blend dance.blend --settings settings.json \
        --report report.json

- settings.json（check.jitter が書く）:
  - objects（測るメッシュの名前の並び）、bone（この骨から見た座標で測る）、armature（省くと最初のアーマチュア）
  - frames（動きのコマの並び。小数のコマは前後のコマの間の時刻。null ならシーンの全コマ）、frames_source（報告に写す）
  - jump_m（このずれ以上を「飛び」と数える。m）、toggle（変形を 1 つずつ切って測り直す）・toggle_types、top
- コマごとに、各頂点の位置を骨から見た座標で読み、前後のコマの中間からのずれ（2 階差分の半分）を測る。骨に付いて動く所は
  骨から見ると動かないので、体の動きそのものはずれにならず、布の震えや 1 コマだけの跳びが残る。長さは m（シーンの
  scale_length で直す）。全部のコマの位置を持つので、頂点 × コマが多いとメモリを使う。
- toggle: 物体ごとに、変形（toggle_types の種類で、表示の印の付いたもの）を 1 つずつ切って測り直す。どの変形がちらつきを
  作るかを見分ける。切った変形は測った後に戻す。.blend は保存しない（測るだけ）。

**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys
import time

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .stage_render import motion_frame, set_frame
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stage_render import motion_frame, set_frame
    from vrm_apply_motion import use_scene_of

TOGGLE_TYPES = ("SHRINKWRAP", "SURFACE_DEFORM", "CLOTH", "SMOOTH")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="jitter_sample.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def offsets(track):
    """(コマ, 頂点, 3) の位置 → 前後のコマの中間からのずれ（コマ - 2, 頂点）。リポジトリの外の台本と同じ式。"""
    middle = (track[:-2] + track[2:]) / 2
    return np.linalg.norm(track[1:-1] - middle, axis=2)


def summary(track, jump, frames, top=8):
    """1 つのメッシュの頂点の列（コマ, 頂点, 3。m）のずれの要約。長さは mm（丸めない）。frames は列の各コマの (T, f)。

    - mean_mm・p99_mm・peak_mm: 全部のコマ（最初と最後を除く）と頂点のずれの平均・99 百分位・最大
    - over_percent: ずれが jump（m）を超えた（コマ, 頂点）の割合（%）
    - worst_frames: ずれの最大が大きいコマ（T・f・そのコマの最大と平均）、worst_vertices: ずれの最大が大きい頂点の番号
    """
    offset = offsets(track)
    per_frame, per_vertex = offset.max(axis=1), offset.max(axis=0)
    order = np.argsort(-per_frame, kind="stable")[:top]
    vertex_order = np.argsort(-per_vertex, kind="stable")[:top]
    return {"vertices": int(track.shape[1]),
            "mean_mm": float(offset.mean() * 1000), "p99_mm": float(np.percentile(offset, 99) * 1000),
            "over_percent": float((offset > jump).mean() * 100), "peak_mm": float(offset.max() * 1000),
            "worst_frames": [{"T": frames[k + 1][0], "f": frames[k + 1][1], "peak_mm": float(per_frame[k] * 1000),
                              "mean_mm": float(offset[k].mean() * 1000)} for k in order],
            "worst_vertices": [[int(v), float(per_vertex[v] * 1000)] for v in vertex_order]}


def sample(bpy, scene, armature, bone, objects, steps, unit):
    """コマごとの、骨から見た頂点の位置 {名前: (コマ, 頂点, 3)}（m）。リポジトリの外の台本と同じ順に読む
    （コマを進め、骨の逆行列を mathutils で求めてから、物体ごとに変形した形を読む）。"""
    result = {obj.name: [] for obj in objects}
    for f in steps:
        set_frame(scene, f)
        depsgraph = bpy.context.evaluated_depsgraph_get()
        to_bone = np.array((armature.matrix_world @ armature.pose.bones[bone].matrix).inverted())
        for obj in objects:
            evaluated = obj.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            world = np.array(evaluated.matrix_world)
            local = np.empty(len(mesh.vertices) * 3)
            mesh.vertices.foreach_get("co", local)
            local = local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
            result[obj.name].append(local @ to_bone[:3, :3].T + to_bone[:3, 3])
            evaluated.to_mesh_clear()
    for name, values in result.items():
        counts = sorted({len(value) for value in values})
        if counts == [0]:
            raise SystemExit(f"{name} に頂点がありません")
        if len(counts) > 1:
            raise SystemExit(f"{name} の頂点の数がコマによって違います（{counts}）。頂点を足し引きする変形があると測れません")
    return {name: np.array(values) * unit for name, values in result.items()}


def measure(bpy, scene, armature, bone, objects, plan, unit, jump, top):
    tracks = sample(bpy, scene, armature, bone, objects, [f for _, f in plan], unit)
    return {name: summary(track, jump, plan, top) for name, track in tracks.items()}


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    if settings.get("armature"):
        armature = bpy.data.objects.get(settings["armature"])
        if armature is None or armature.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    else:
        armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"]
        if not armatures:
            raise SystemExit("アーマチュアがありません")
        armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    bone = settings["bone"]
    if bone not in armature.pose.bones:
        raise SystemExit(f"{armature.name} に骨 {bone} がありません（bone_prefix と bone を確かめてください）")
    missing = [name for name in settings["objects"] if bpy.data.objects.get(name) is None or bpy.data.objects[name].type != "MESH"]
    if missing:
        raise SystemExit(f"測るメッシュがありません: {missing}")
    objects = [bpy.data.objects[name] for name in settings["objects"]]
    if settings.get("frames") is None:
        plan = [(k + 1, f) for k, f in enumerate(range(scene.frame_start, scene.frame_end + 1))]
    else:
        plan = [(int(T), motion_frame(f)) for T, f in settings["frames"]]
    report = {"schema_version": "model3d-jitter/0.1", "check": "jitter", "blender": bpy.app.version_string,
              "armature": armature.name, "bone": bone, "objects": [obj.name for obj in objects],
              "frames": {"source": settings.get("frames_source", "scene"), "count": len(plan),
                         "first": plan[0][1] if plan else None, "last": plan[-1][1] if plan else None},
              "jump_mm": settings["jump_m"] * 1000, "scale_length": float(scene.unit_settings.scale_length)}
    if len(plan) < 3:
        report.update({"status": "skipped", "reason": f"3 コマ以上が要ります（{len(plan)} コマ）", "measured": {}, "toggled": []})
    else:
        began = time.time()
        unit, jump, top = report["scale_length"], settings["jump_m"], int(settings.get("top", 8))
        report["status"] = "done"
        report["measured"] = measure(bpy, scene, armature, bone, objects, plan, unit, jump, top)
        toggled = []
        kinds = set(settings.get("toggle_types") or TOGGLE_TYPES)
        for obj in (objects if settings.get("toggle") else []):
            for modifier in obj.modifiers:
                if modifier.type in kinds and modifier.show_viewport:
                    modifier.show_viewport = False
                    try:
                        toggled.append({"object": obj.name, "modifier": modifier.name, "type": modifier.type,
                                        "measured": measure(bpy, scene, armature, bone, objects, plan, unit, jump, top)})
                    finally:
                        modifier.show_viewport = True
        report["toggled"] = toggled
        report["seconds"] = round(time.time() - began, 1)
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"status": report["status"], "frames": len(plan), "objects": len(objects)}))


if __name__ == "__main__":
    main()
