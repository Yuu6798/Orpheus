"""Blender の中で動かす: 決めた姿勢で、メッシュの決まった所（いちばん高い頂点など）を、骨の付け根から見た骨の軸の位置に直す
（motion.bone_offset が呼ぶ。motion.bone_path の points の offset にそのまま書ける形）。

    blender --background --factory-startup --python bone_offset.py -- --blend in.blend --settings settings.json --out offsets.json

settings.json（motion.bone_offset が書く）: frame（動きのコマ）、armature（物体の名前。null なら最初のアーマチュア）、
targets（名前 → {bone（前置きを付けた名前）, name（前置きの無い名前）, at, object, pick, box・index・near}）。
変形した後のメッシュ（モディファイアを効かせた形）の頂点を、世界の座標 m で選ぶ:
highest・lowest（box の中で、いちばん高い・低い）、nearest（near にいちばん近い）、vertex（番号 index）。
骨の付け根と先と軸は motion.bone_path と同じ取り方（世界の行列 × 姿勢の行列）。.blend は保存しない。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from . import bone_path, stage_render
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import bone_path
    import stage_render


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="bone_offset.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    return ap.parse_args(argv)


def pick(world, target):
    """頂点の番号（world は (n, 3) の m）。選べなければ SystemExit。"""
    import numpy as np
    index = np.arange(len(world))
    if target["pick"] == "vertex":
        if target["index"] >= len(world):
            raise SystemExit(f"{target['object']} の頂点は {len(world)} 個です（index {target['index']}）")
        return int(target["index"])
    if "box" in target:
        low, high = (np.asarray(v, float) for v in target["box"])
        index = index[np.all((world >= low) & (world <= high), axis=1)]
    if not len(index):
        raise SystemExit(f"{target['object']} の box の中に頂点がありません: {target.get('box')}")
    if target["pick"] == "nearest":
        return int(index[np.argmin(np.linalg.norm(world[index] - np.asarray(target["near"], float), axis=1))])
    z = world[index, 2]
    return int(index[np.argmax(z) if target["pick"] == "highest" else np.argmin(z)])


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    import numpy as np

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    if settings.get("armature"):
        arm = bpy.data.objects.get(settings["armature"])
        if arm is None or arm.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    else:
        armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not armatures:
            raise SystemExit("アーマチュアがありません")
        arm = armatures[0]
    unit = float(sc.unit_settings.scale_length)
    stage_render.set_frame(sc, settings["frame"])
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points, measured = {}, {}
    for name, target in settings["targets"].items():
        if target["bone"] not in arm.pose.bones:
            raise SystemExit(f"{arm.name} に無い骨: {target['bone']}（bone_prefix を確かめてください）")
        ob = bpy.data.objects.get(target["object"])
        if ob is None or ob.type != "MESH":
            raise SystemExit(f"メッシュの物体がありません: {target['object']}")
        evaluated = ob.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        co = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", co)
        evaluated.to_mesh_clear()
        matrix = np.array(ob.matrix_world, float)
        world = (co.reshape(-1, 3).astype(float) @ matrix[:3, :3].T + matrix[:3, 3]) * unit
        i = pick(world, target)
        pose = arm.pose.bones[target["bone"]]
        rotation = arm.matrix_world.to_3x3() @ pose.matrix.to_3x3()
        head, tail = arm.matrix_world @ pose.head, arm.matrix_world @ pose.tail
        offset = bone_path.bone_offset(list(head), list(tail), [list(rotation.col[k]) for k in range(3)], (world[i] / unit).tolist(),
                                       target["at"], unit)
        points[name] = [{"bone": target["name"], "at": target["at"], "offset": offset}]
        measured[name] = {"object": ob.name, "vertex": i, "world": world[i].tolist()}
    out = {"schema_version": "model3d-bone-offset/0.1", "unit": "m", "frame": settings["frame"], "armature": arm.name,
           "points": points, "measured": measured}
    with open(a.out, "w", encoding="utf-8") as stream:
        json.dump(out, stream, ensure_ascii=False, indent=1)
    print(f"BONE_OFFSET {len(points)} points", flush=True)


if __name__ == "__main__":
    main()
