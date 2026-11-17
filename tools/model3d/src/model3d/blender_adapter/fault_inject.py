"""Blender の中で動かす: きれいな動きの .blend に、決まったコマでわざと破綻を入れて、別の .blend に書く（motion.inject_faults）。
動きの検品（check.animation）が、入れた破綻を入れたコマで見つけるかを確かめる、正解の分かっている試験の素材を作るためのもの。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python fault_inject.py -- \
        --blend clean.blend --settings settings.json --out faulty.blend --report report.json

settings.json（motion.inject_faults が書く）: armature（省くと VRM 1.0 の人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）、
faults（並び。上から順に入れる）。破綻ごとに:
- bone（骨の名前）か role（人型の骨の役割。left_hand など）、frames [最初, 最後]
- kind: local（骨の今の回りに、骨の中の軸 axis（[x, y, z]）のまわりの回りを足す）・world（骨を付け根のまわりに、世界の軸 axis（"X"・"Y"・"Z"）で
  回す。子も一緒に動く）・shift（骨の付け根を、世界の向きの delta（[x, y, z] m）だけ動かす）
- local・world は degrees（度）。shape: ramp（既定。最初と最後の ramp コマ（既定 3）でなめらかに 0 から入って 0 へ戻る）・constant（ずっと同じ）・
  alternate（1 コマおきに + と −。奇数のコマが +）
- 入れたコマごとに、触った骨の回り（rotation_quaternion）のキーを打つ（shift は位置 location も）。骨の回りの持ち方は QUATERNION のこと。
リポジトリ外の試作（試験の素材を作る台本）と同じ手順と計算（mathutils）。入力の .blend は変えない。報告は、入れた破綻の数とキーを打ったコマ。
"""
import argparse
import json
import math
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .animation_sample import humanoid_map, pick_armature
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import humanoid_map, pick_armature
    from vrm_apply_motion import use_scene_of

KINDS = ("local", "world", "shift")
SHAPES = ("ramp", "constant", "alternate")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="fault_inject.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def envelope(f, first, last, ramp=3):
    """コマ first〜last の間 1、前後 ramp コマでなめらかに 0 へ（試作と同じ式）。"""
    if f < first or f > last:
        return 0.0
    u = min(1.0, (f - first + 1) / ramp, (last - f + 1) / ramp)
    return u * u * (3 - 2 * u)


def amount(fault, f):
    """コマ f で入れる量の倍率（degrees や delta に掛ける）。"""
    first, last = fault["frames"]
    shape = fault.get("shape", "ramp")
    if shape == "constant":
        return 1.0
    if shape == "alternate":
        return 1.0 if f % 2 else -1.0
    return envelope(f, first, last, fault.get("ramp", 3))


def resolve_bones(faults, humanoid, bones):
    """破綻ごとの骨の名前（role は人型の骨の割り当てで名前にする）。無い骨は SystemExit。"""
    names = []
    for k, fault in enumerate(faults):
        name = fault.get("bone") or humanoid.get(fault.get("role", ""))
        if not name or name not in bones:
            raise SystemExit(f"faults[{k}] の骨がありません: bone={fault.get('bone')!r} role={fault.get('role')!r}")
        names.append(name)
    return names


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Quaternion, Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armature = pick_armature(bpy, settings.get("armature"))
    scene = use_scene_of(bpy, armature)
    faults = settings["faults"]
    pose = armature.pose.bones
    names = resolve_bones(faults, humanoid_map(armature), set(pose.keys()))
    for name in set(names):
        if pose[name].rotation_mode != "QUATERNION":
            raise SystemExit(f"骨 {name} の回りの持ち方が {pose[name].rotation_mode} です（QUATERNION の骨だけに入れられます）")
    rest3 = {name: armature.data.bones[name].matrix_local.to_3x3() for name in set(names)}

    def apply(fault, name, f):
        k = amount(fault, f)
        pose_bone = pose[name]
        if fault["kind"] == "local":
            pose_bone.rotation_quaternion = pose_bone.rotation_quaternion @ Quaternion(Vector(fault["axis"]), math.radians(fault["degrees"] * k))
        elif fault["kind"] == "world":
            head = pose_bone.matrix.translation.copy()
            pose_bone.matrix = (Matrix.Translation(head) @ Matrix.Rotation(math.radians(fault["degrees"] * k), 4, fault["axis"])
                                @ Matrix.Translation(-head) @ pose_bone.matrix)
            bpy.context.view_layer.update()
        else:
            pose_bone.location = pose_bone.location + rest3[name].inverted() @ Vector([v * k for v in fault["delta"]])

    hidden = [obj for obj in bpy.data.objects if obj.type == "MESH" and not obj.hide_viewport]
    for obj in hidden:                                                   # メッシュを評価しない（試作と同じ。速い）
        obj.hide_viewport = True
    keyed = {}
    try:
        for f in range(scene.frame_start, scene.frame_end + 1):
            active = [(fault, name) for fault, name in zip(faults, names) if fault["frames"][0] <= f <= fault["frames"][1]]
            if not active:
                continue
            scene.frame_set(f)
            for fault, name in active:
                apply(fault, name, f)
                pose[name].keyframe_insert("rotation_quaternion", frame=f)
                if fault["kind"] == "shift":
                    pose[name].keyframe_insert("location", frame=f)
                keyed.setdefault(fault["name"], []).append(f)
    finally:
        for obj in hidden:
            obj.hide_viewport = False
    scene.frame_set(scene.frame_start)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    report = {"blender": bpy.app.version_string, "armature": armature.name, "scene": scene.name,
              "frames": [scene.frame_start, scene.frame_end],
              "faults": [{"name": fault["name"], "bone": name, "keyed_frames": keyed.get(fault["name"], [])} for fault, name in zip(faults, names)]}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"faults": len(faults), "keyed": sum(len(v) for v in keyed.values())}))


if __name__ == "__main__":
    main()
