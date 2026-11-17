"""Blender の中で動かす: ねじれを分ける補助の骨（model.twist_bones）。上腕などの骨と同じ位置に「ねじらない骨」を足し、メッシュの重みを、
付け根に近いほどその骨へ、先に近いほど元の骨へ振り分ける。前腕などには、先の半分に「手の回転の一部を写す骨」（roll）も足せる。

    blender --background --factory-startup --python twist_bones.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 2 作目・3 作目の VRM のダンスで分かったこと）: 画像から作ったモデルには、肩から上腕のねじれを受け持つ骨が無いことがある。
踊りで上腕の骨が軸のまわりに 90° を超えてねじれるコマが数百あり（check.twist で測る）、上腕のメッシュ全体が付け根から一気にねじれて、
絞ったように見えた。前腕では、手が軸のまわりに回ると手首のまわりが絞られる。踊りの動きは変えずに、ねじれを付け根から先へ徐々に分ける。

やり方（bones の 1 つごと）:
  1. 骨 bone と同じ位置（付け根・先・ロール）に、名前 name の骨を足す（親は bone の親、変形に使う）。Damped Track の拘束で骨 aim の
     付け根を向くだけにする（いちばん小さい回転で向くので、軸のまわりには回らない）。
  2. roll があれば、bone の start（骨に沿った割合）から先までに、名前 roll.name の骨を足す（親は bone、ロールは bone と同じ、変形に使う）。
     Copy Rotation の拘束（Y だけ・Add・ローカルどうし、角度の分け方の順 euler_order）で、骨 roll.from の軸のまわりの回転を足す。
     この 2 つの拘束の形は、VRM Add-on が VRM 1.0 の拘束（VRMC_node_constraint の aim と roll）として書き出せる形。
     Blender の Copy Rotation は回転を 3 つの角度に分けた Y を使うので、手を大きく曲げたコマでは、VRM の roll（本当のねじれ）とずれる
     （3 作目で最大 30〜55°）。動画では、正確な値を骨のキーに焼いて拘束を止めるとよい（3 作目はそうした）。
  3. メッシュ（meshes。省くとこのアーマチュアで動く全部のメッシュ）の bone の頂点グループの重みを、骨に沿った位置 t（0 = 付け根、1 = 先。初期姿勢）で
     分ける: roll が無ければ、元の骨に t、足した骨に 1 − t。roll があれば、t が start までは足した骨から元の骨へ、start から先は元の骨から
     roll の骨へ、折れ線で移す。腕輪のように bone だけにほぼ付いた小さな島（rigid_max_verts 頂点以下で、bone が
     いちばん重い頂点が rigid_share を超える島）は、島の t の平均で分ける（島の中でねじれて模様が歪まないように）。
     min_weight があれば、重みがその値以下になる所は、その頂点グループ（元の骨・足した骨のどちらも）から外す（足した骨の名前の
     グループが前から残っていても、古い重みを残さない）。
     メッシュのデータをほかの物体と分け合う（リンク複製）物体は、先にその物体だけのデータにする（重みはデータに付くので）。
  4. max_influences があれば、重みを分けた頂点の骨（変形に使う骨）を、重みの大きい順にその本数までにする（VRM は 1 頂点 4 本まで）。
     切った重みは、残した骨に同じ割合で割り振る（合計は変えない）。
VRMC_node_constraint に対応しないアプリでは、足した骨が動かず、腕の形が崩れうる。

報告は model3d-twist-bones/0.1。**検証**: 本物の Blender 5.2.4 で、2 作目と 3 作目の VRM のダンスで、リポジトリの外の台本と同じ骨と重みに
なることを確かめた（docs/model3d.md）。CI には Blender が無い（重みの分け方はテストする）。
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

DEFAULTS = {"bones": [], "meshes": None, "armature": None, "rigid_max_verts": 600, "rigid_share": 0.95, "min_weight": None,
            "max_influences": None}
EULER_ORDERS = ("XYZ", "XZY", "YXZ", "YZX", "ZXY", "ZYX")
ROLL_DEFAULTS = {"start": 0.5, "euler_order": "ZYX"}   # 前腕の真ん中から先。ZYX は 3 作目で 6 通りを試していちばんずれが小さかった順


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="twist_bones.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    return {**DEFAULTS, **settings}


def check_roll(value, where):
    """bones[k].roll の設定を確かめ、既定を埋めた dict にする（悪ければ ValueError）。"""
    if not isinstance(value, dict):
        raise ValueError(f"{where} は {{\"name\": 足す骨, \"from\": 回転を写す骨}} にしてください: {value!r}")
    unknown = set(value) - {"name", "from", "start", "euler_order"}
    if unknown:
        raise ValueError(f"{where} の知らない設定: {sorted(unknown)}（使えるのは euler_order・from・name・start）")
    out = {**ROLL_DEFAULTS, **value}
    for key in ("name", "from"):
        if not isinstance(out.get(key), str) or not out.get(key):
            raise ValueError(f"{where}.{key} は名前（空でない文字列）にしてください: {value.get(key)!r}")
    start = out["start"]
    if isinstance(start, bool) or not isinstance(start, (int, float)) or not 0.0 < start < 1.0:
        raise ValueError(f"{where}.start は 0 より大きく 1 より小さい割合にしてください: {start!r}")
    if out["euler_order"] not in EULER_ORDERS:
        raise ValueError(f"{where}.euler_order は {', '.join(EULER_ORDERS)} のどれかにしてください: {out['euler_order']!r}")
    if out["name"] == out["from"]:
        raise ValueError(f"{where} の name と from は別の骨にしてください: {value!r}")
    return out


def part_shares(along, start=None):
    """骨に沿った位置 t (N,) から、(足した骨, 元の骨, roll の骨) の割合。start が None なら roll は無く、元の骨 t・足した骨 1 − t。
    start があれば、t が start までは足した骨から元の骨へ、start から先は元の骨から roll の骨へ、折れ線で移す
    （start が 0.5 なら台本の max(0, 1 − 2t)・1 − |2t − 1|・max(0, 2t − 1) と、ビットまで同じ値）。"""
    if start is None:
        return 1.0 - along, along, None
    swing = np.maximum(0.0, 1.0 - along / start)
    roll = np.maximum(0.0, (along - start) / (1.0 - start))
    return swing, 1.0 - swing - roll, roll


def writes(value, floor):
    """分けた重み value を頂点グループに書くか（True）、その頂点グループから外すか（False）。min_weight（floor）が無ければいつも書く。
    外すのは、元の骨のグループでも足した骨のグループでも同じ（足した骨の名前のグループが前から残っていても、古い重みを残さない）。"""
    return floor is None or value > floor


def capped(items, limit):
    """1 頂点の [(グループ, 重み)]（重み > 0）を、重みの大きい順に limit 本までにする。limit 本以下なら None。
    戻り: (残す [(グループ, 新しい重み)], 外すグループの並び, 外した重みの割合)。残す重みは、合計が元の合計になるよう同じ割合で
    増やす（同じ重みは先のもの。台本と同じ計算の順）。"""
    if len(items) <= limit:
        return None
    order = sorted(items, key=lambda item: -item[1])
    keep, drop = order[:limit], order[limit:]
    total_all, total_keep = sum(weight for _, weight in order), sum(weight for _, weight in keep)
    return ([(group, weight * total_all / total_keep) for group, weight in keep], [group for group, _ in drop],
            (total_all - total_keep) / total_all)


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


def separate_shared(objects, meshes):
    """meshes のうち、メッシュのデータをほかの物体と分け合う（リンク複製）物体を、その物体だけのデータにする。重みはメッシュのデータに
    付くので、分け合ったままだと、同じ重みを 2 回分けたり、選んでいない複製の重みまで変わったりする（prepare_push の make_groups と
    同じ）。戻り: {分けた物体の名前: 分け合っていた物体の名前}（選んだ複製どうしなら、片方を分ければもう片方は元のデータのまま）。"""
    separated = {}
    for obj in meshes:
        sharing = sorted(other.name for other in objects if other.data == obj.data and other.name != obj.name)
        if sharing:
            obj.data = obj.data.copy()
            separated[obj.name] = sharing
    return separated


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
        roll = entry.get("roll")
        for key, name in (("bone", entry["bone"]), ("aim", entry["aim"])) + ((("roll.from", roll["from"]),) if roll else ()):
            if name not in bones:
                raise SystemExit(f"{armature.name} に骨 {name} がありません（bones の {key}）")
        for name in (entry["name"],) + ((roll["name"],) if roll else ()):
            if name in bones:
                raise SystemExit(f"{armature.name} に骨 {name} がもうあります（model.twist_bones を 2 回かけない）")
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

    # 1. ねじらない骨を足す（親は元の骨の親。Damped Track で aim の付け根を向くだけ）。2. roll があれば、元の骨の start から先に、
    # 手の回転の一部を写す骨を足す（親は元の骨。Copy Rotation の Y だけ・Add・ローカルどうし）。骨は bones の順に、足した骨・roll の骨
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
        roll = entry.get("roll")
        if roll:
            bone = armature.data.edit_bones.new(roll["name"])
            # start 0.5 なら (付け根 + 先) / 2 とビットまで同じ（半分は正確に割れる）
            bone.head, bone.tail, bone.roll = source.head * (1.0 - roll["start"]) + source.tail * roll["start"], source.tail.copy(), source.roll
            bone.parent = source
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
        roll = entry.get("roll")
        if roll:
            pose_bone = armature.pose.bones[roll["name"]]
            for constraint in list(pose_bone.constraints):
                pose_bone.constraints.remove(constraint)
            copy = pose_bone.constraints.new("COPY_ROTATION")
            copy.target = armature
            copy.subtarget = roll["from"]
            copy.use_x, copy.use_y, copy.use_z = False, True, False
            copy.mix_mode = "ADD"
            copy.target_space = "LOCAL"
            copy.owner_space = "LOCAL"
            copy.euler_order = roll["euler_order"]
    bpy.ops.object.mode_set(mode="OBJECT")

    # 3. 重みを振り分ける（リンク複製は先にその物体だけのデータにする）
    separated = separate_shared(bpy.data.objects, meshes)
    weights_report, influences_report = {}, {}
    deform = deform_names(armature)                     # 足した骨も変形に使う（いちばん重い骨の判定は、変形に使う骨のグループの中で）
    floor = settings["min_weight"]
    for obj in meshes:
        mesh = obj.data
        positions = world_points(obj)
        roots = islands(len(positions), mesh_edges(mesh))
        touched_all = set()
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
            roll = entry.get("roll")
            share, rigid = split_shares(along, roots, touched, dominant, settings["rigid_max_verts"], settings["rigid_share"])
            swing_share, keep_share, roll_share = part_shares(share, roll["start"] if roll else None)
            keep = weights * keep_share
            # 足した骨・元の骨・roll の骨の順に書く（台本と同じ順。外すと頂点の中のグループの並びが変わるので、順まで合わせる）
            parts = [(obj.vertex_groups.get(entry["name"]) or obj.vertex_groups.new(name=entry["name"]),
                      weights - keep if roll is None else weights * swing_share), (source_group, keep)]
            if roll:
                parts.append((obj.vertex_groups.get(roll["name"]) or obj.vertex_groups.new(name=roll["name"]), weights * roll_share))
            for index in np.flatnonzero(touched):
                for group, values in parts:
                    value = float(values[index])
                    if writes(value, floor):
                        group.add([int(index)], value, "REPLACE")
                    else:                                # 足した骨のグループが前からあっても、古い重みを残さない（無い頂点を外すのは何もしない）
                        group.remove([int(index)])
                touched_all.add(int(index))
            weights_report.setdefault(obj.name, {})[entry["bone"]] = {"verts": int(touched.sum()), "rigid_islands": rigid}
        # 4. 重みを分けた頂点の骨の数を max_influences 本までにする（変形に使う骨のグループだけを数える）
        if settings["max_influences"] is not None and touched_all:
            names = {group.index: group.name for group in obj.vertex_groups}
            most, cut, largest = 0, 0, 0.0
            for index in touched_all:
                items = [(item.group, item.weight) for item in mesh.vertices[index].groups if names[item.group] in deform and item.weight > 0]
                most = max(most, len(items))
                result = capped(items, settings["max_influences"])
                if result is None:
                    continue
                kept, dropped, share_cut = result
                for group in dropped:
                    obj.vertex_groups[names[group]].remove([index])
                for group, weight in kept:
                    obj.vertex_groups[names[group]].add([index], weight, "REPLACE")
                cut, largest = cut + 1, max(largest, share_cut)
            influences_report[obj.name] = {"verts_checked": len(touched_all), "most_before": most, "verts_cut": cut,
                                           "largest_share_cut": round(largest, 4)}

    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    added = []
    for entry in settings["bones"]:
        added.append({"name": entry["name"], "kind": "swing", "copy_of": entry["bone"], "aims_at": entry["aim"]})
        if entry.get("roll"):
            roll = entry["roll"]
            added.append({"name": roll["name"], "kind": "roll", "part_of": entry["bone"], "from": roll["from"], "start": roll["start"],
                          "euler_order": roll["euler_order"]})
    report = {"schema_version": "model3d-twist-bones/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "armature": armature.name, "bones": added, "weights": weights_report, "influences": influences_report,
              "meshes": [obj.name for obj in meshes], "separated_from": separated, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "bones": len(added)}))


if __name__ == "__main__":
    main()
