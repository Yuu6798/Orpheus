"""Blender の中で動かす: ねじれを分ける補助の骨（model.twist_bones）。上腕などの骨と同じ位置に「ねじらない骨」を足し、メッシュの重みを、
付け根に近いほどその骨へ、先に近いほど元の骨へ振り分ける。

    blender --background --factory-startup --python twist_bones.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 2 作目の VRM のダンスで分かったこと）: 画像から作ったモデルには、肩から上腕のねじれを受け持つ骨が無いことがある。
踊りで上腕の骨が軸のまわりに 90° を超えてねじれるコマが数百あり（check.twist で測る）、上腕のメッシュ全体が付け根から一気にねじれて、
絞ったように見えた。踊りの動きは変えずに、ねじれを付け根から先へ徐々に分ける。

やり方（bones の 1 つごと）:
  1. 骨 bone と同じ位置（付け根・先・ロール）に、名前 name の骨を足す（親は bone の親、変形に使う）。Damped Track の拘束で骨 aim の
     付け根を向くだけにする（いちばん小さい回転で向くので、軸のまわりには回らない）。
  2. メッシュ（meshes。省くとこのアーマチュアで動く全部のメッシュ）の bone の頂点グループの重みを、骨に沿った位置 t（0 = 付け根、1 = 先。初期姿勢）で
     分ける: 元の骨に t、足した骨に 1 − t。腕輪のように bone だけにほぼ付いた小さな島（rigid_max_verts 頂点以下で、bone が
     いちばん重い頂点が rigid_share を超える島）は、島の t の平均で分ける（島の中でねじれて模様が歪まないように）。
骨の拘束は、アプリによって効かない（VRM に書き出すと腕が崩れうる）。ねじれを分ける骨は動画の中だけにする。

報告は model3d-twist-bones/0.1。**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ骨と重みになる
ことを確かめた（docs/model3d.md）。CI には Blender が無い（重みの分け方はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import bone_world, deform_names, deformed_by, islands, mesh_edges, world_points
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import bone_world, deform_names, deformed_by, islands, mesh_edges, world_points
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"bones": [], "meshes": None, "armature": None, "rigid_max_verts": 600, "rigid_share": 0.95}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="twist_bones.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    return {**DEFAULTS, **settings}


def along_bone(positions, head, tail):
    """頂点の骨に沿った位置（0 = 付け根、1 = 先。範囲の外は 0 と 1 に切る）。台本と同じ計算の順。"""
    axis = tail - head
    length = float(np.linalg.norm(axis))
    return np.clip((positions - head) @ (axis / length) / length, 0.0, 1.0)


def split_shares(along, roots, touched, dominant, max_verts, share):
    """元の骨に残す割合 (N,) と、島ごとに 1 つの割合にした島の数。bone だけにほぼ付いた小さな島は島の平均。"""
    out = along.copy()
    rigid = 0
    for root in np.unique(roots[touched]):
        members = roots == root
        if members.sum() <= max_verts and dominant[members].mean() > share:
            out[members] = float(along[members].mean())
            rigid += 1
    return out, rigid


def group_state(mesh, group_index, bone_groups=None):
    """頂点ごとの、その頂点グループの重み (N,) と、そのグループがいちばん重いか (N,)（同じ重みは先のもの。台本と同じ）。
    いちばん重いかは、bone_groups（変形に使う骨の頂点グループの番号。None なら全部）の中で比べる。"""
    count = len(mesh.vertices)
    weights = np.zeros(count)
    dominant = np.zeros(count, dtype=bool)
    for vertex in mesh.vertices:
        best, best_weight = -1, 0.0
        for item in vertex.groups:
            if item.group == group_index:
                weights[vertex.index] = item.weight
            if (bone_groups is None or item.group in bone_groups) and item.weight > best_weight:
                best, best_weight = item.group, item.weight
        dominant[vertex.index] = best == group_index
    return weights, dominant


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
    bones = armature.data.bones
    for entry in settings["bones"]:
        for key in ("bone", "aim"):
            if entry[key] not in bones:
                raise SystemExit(f"{armature.name} に骨 {entry[key]} がありません（bones の {key}）")
        if entry["name"] in bones:
            raise SystemExit(f"{armature.name} に骨 {entry['name']} がもうあります（model.twist_bones を 2 回かけない）")
    if settings["meshes"] is None:                       # 既定: このアーマチュアで動くメッシュだけ（ほかのキャラの重みは触らない）
        meshes = [obj for obj in bpy.data.objects if obj.type == "MESH" and deformed_by(obj, armature)]
    else:
        meshes = []
        for name in settings["meshes"]:
            obj = bpy.data.objects.get(name)
            if obj is None or obj.type != "MESH":
                raise SystemExit(f"meshes のメッシュがありません: {name}")
            if not deformed_by(obj, armature):
                raise SystemExit(f"meshes の {name} は {armature.name} で動きません（アーマチュアのモディファイアも、アーマチュアの親も無い）")
            meshes.append(obj)

    # 1. ねじらない骨を足す（親は元の骨の親。Damped Track で aim の付け根を向くだけ）
    bpy.context.view_layer.objects.active = armature
    armature.hide_viewport = False
    armature.hide_set(False)
    bpy.ops.object.mode_set(mode="EDIT")
    for entry in settings["bones"]:
        source = armature.data.edit_bones[entry["bone"]]
        bone = armature.data.edit_bones.new(entry["name"])
        bone.head, bone.tail, bone.roll = source.head.copy(), source.tail.copy(), source.roll
        bone.parent = source.parent
        bone.use_connect = False
        bone.use_deform = True
    bpy.ops.object.mode_set(mode="POSE")
    for entry in settings["bones"]:
        pose_bone = armature.pose.bones[entry["name"]]
        for constraint in list(pose_bone.constraints):
            pose_bone.constraints.remove(constraint)
        track = pose_bone.constraints.new("DAMPED_TRACK")
        track.target = armature
        track.subtarget = entry["aim"]
        track.track_axis = "TRACK_Y"
    bpy.ops.object.mode_set(mode="OBJECT")

    # 2. 重みを振り分ける
    weights_report = {}
    deform = deform_names(armature)                     # 足した骨も変形に使う（いちばん重い骨の判定は、変形に使う骨のグループの中で）
    for obj in meshes:
        mesh = obj.data
        positions = world_points(obj)
        roots = islands(len(positions), mesh_edges(mesh))
        for entry in settings["bones"]:
            source_group = obj.vertex_groups.get(entry["bone"])
            if source_group is None:
                continue
            head, tail = bone_world(armature, entry["bone"])
            along = along_bone(positions, head, tail)
            bone_groups = {group.index for group in obj.vertex_groups if group.name in deform}   # 前に足した骨のグループも入れる
            weights, dominant = group_state(mesh, source_group.index, bone_groups)
            touched = weights > 0
            if not touched.any():
                continue
            share, rigid = split_shares(along, roots, touched, dominant, settings["rigid_max_verts"], settings["rigid_share"])
            swing_group = obj.vertex_groups.get(entry["name"]) or obj.vertex_groups.new(name=entry["name"])
            for index in np.flatnonzero(touched):
                keep = float(weights[index] * share[index])
                source_group.add([int(index)], keep, "REPLACE")
                swing_group.add([int(index)], float(weights[index] - keep), "REPLACE")
            weights_report.setdefault(obj.name, {})[entry["bone"]] = {"verts": int(touched.sum()), "rigid_islands": rigid}

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report = {"schema_version": "model3d-twist-bones/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "armature": armature.name, "bones": [{"name": e["name"], "copy_of": e["bone"], "aims_at": e["aim"]} for e in settings["bones"]],
              "weights": weights_report, "meshes": [obj.name for obj in meshes], "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "bones": len(settings["bones"])}))


if __name__ == "__main__":
    main()
