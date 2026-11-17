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
- 破綻を入れる前に、触る骨の元の値（回り・位置・大きさ）を読んでおき、入れるコマではその値から始める（前のコマで打ったキーが混ざらない。
  キーの少ない動きや、元はキーの無い位置でも、量が積み重ならない）。区間の外は、キーを打つ値ごとに元の値のキーで留める（区間の外へ破綻が
  伸びない）: 元のキーがあれば、区間の前は前の元のキーから、後ろは次の元のキーまで、全部のコマ（間引いたキーの間の形を変えない）。元のキーが
  無ければ前後 1 コマ。同じ値にキーを打つほかの破綻の区間の中のコマには打たない。
- world は骨の行列を入れるので、位置と大きさに小さなずれが出る。キーを打たない位置と大きさは、終わりに元の値へ戻す（キーが無いと全部の
  コマに効くため。試作はずれを残していた）。
- 区間の外の留めは、破綻を入れる前に打つ（親の破綻の区間の外で子の破綻を計算するとき、親が元の姿勢になっている）。
- world の軸と shift の向きは世界の座標。そのコマのアーマチュアの物体の向きでアーマチュアの中へ直し（物体が動いていても）、shift は親の
  今の姿勢を通して骨の位置へ直す（アーマチュアが回っていない・親の無い骨では、試作と同じ式）。
- 同じコマで先に入れた破綻（local など）は姿勢の行列に入れてから、world を入れる（先の破綻を入れた素材に world を入れたのと同じ）。
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


def keyed_props(fault):
    """破綻がキーを打つ値（回りはいつも。shift は位置も）。"""
    return ("rotation_quaternion", "location") if fault["kind"] == "shift" else ("rotation_quaternion",)


def pin_range(first, last, keys, start, end):
    """区間 first〜last の外で、元の値のキーで留めるコマ。keys はその値の元のキーのコマ（無ければ空）。元のキーがあれば、区間の前は前の
    元のキーから、後ろは次の元のキーまで全部のコマ（間引いたキーの間の形を変えない）。前や後ろに元のキーが無いか、元のキーが無ければ、
    そちらは区間の前後 1 コマだけ（元の値はそこから先ずっと同じ）。シーンの範囲の中だけ。"""
    before = [k for k in keys if k <= first - 1]
    after = [k for k in keys if k >= last + 1]
    lo = max(before) if before else first - 1
    hi = min(after) if after else last + 1
    return [f for f in list(range(lo, first)) + list(range(last + 1, hi + 1)) if start <= f <= end]


def fault_pins(faults, names, keys, start, end):
    """区間の外を留める (破綻の番号, 値, コマ) の並び。keys は {(骨, 値): 元のキーのコマ}。同じ骨の同じ値にキーを打つほかの破綻の区間の中の
    コマと、シーンに重ならない破綻は除く（回りだけの破綻の区間の中でも、位置は留める）。"""
    inside = {}
    for fault, name in zip(faults, names):
        for prop in keyed_props(fault):
            inside.setdefault((name, prop), set()).update(range(fault["frames"][0], fault["frames"][1] + 1))
    out = []
    for k, (fault, name) in enumerate(zip(faults, names)):
        if fault["frames"][1] < start or fault["frames"][0] > end:        # シーンに重ならない破綻は入らないので、留めない
            continue
        for prop in keyed_props(fault):
            for f in pin_range(fault["frames"][0], fault["frames"][1], keys.get((name, prop), []), start, end):
                if f not in inside[(name, prop)] and (k, prop, f) not in out:
                    out.append((k, prop, f))
    return out


def first_frames(wanted):
    """骨ごとに、元の値を読んだいちばん前のコマ。読んだコマが無い骨（シーンの範囲の外に置いた破綻だけの骨）は除く。"""
    return {name: min(frames) for name, frames in wanted.items() if frames}


def bone_key_frames(armature, names):
    """{(骨, 値): 元のキーのコマ（整数に丸めて並べた物）}（回りと位置。Blender 4.4 からの層つきの動きでも読む）。"""
    anim = armature.animation_data
    if anim is None or anim.action is None:
        return {}
    try:
        from bpy_extras import anim_utils
        bag = anim_utils.action_get_channelbag_for_slot(anim.action, anim.action_slot)
        curves = list(bag.fcurves) if bag else []
    except (ImportError, AttributeError):
        curves = list(anim.action.fcurves)
    out = {}
    for name in set(names):
        for prop in ("rotation_quaternion", "location"):
            path = f'pose.bones["{name}"].{prop}'
            frames = sorted({int(round(point.co.x)) for curve in curves if curve.data_path == path for point in curve.keyframe_points})
            if frames:
                out[(name, prop)] = frames
    return out


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
    axes = {"X": Vector((1.0, 0.0, 0.0)), "Y": Vector((0.0, 1.0, 0.0)), "Z": Vector((0.0, 0.0, 1.0))}

    def apply(fault, name, f, frame_world):
        """frame_world はそのコマのアーマチュアの置き場所（アーマチュアの物体が動いていても、そのコマの向きで直す）。"""
        k = amount(fault, f)
        pose_bone = pose[name]
        world3 = frame_world.to_3x3()
        turned = any(world3[i][j] != (1.0 if i == j else 0.0) for i in range(3) for j in range(3))   # アーマチュアが回っている
        world_turn = frame_world.to_quaternion().inverted()
        if fault["kind"] == "local":
            pose_bone.rotation_quaternion = pose_bone.rotation_quaternion @ Quaternion(Vector(fault["axis"]), math.radians(fault["degrees"] * k))
        elif fault["kind"] == "world":
            bpy.context.view_layer.update()                                  # 同じコマで先に入れた破綻（local など）を、姿勢の行列に入れてから読む
            head = pose_bone.matrix.translation.copy()
            axis = (world_turn @ axes[fault["axis"]]) if turned else fault["axis"]   # 世界の軸をアーマチュアの中へ（回っていなければ試作と同じ）
            pose_bone.matrix = (Matrix.Translation(head) @ Matrix.Rotation(math.radians(fault["degrees"] * k), 4, axis)
                                @ Matrix.Translation(-head) @ pose_bone.matrix)
            bpy.context.view_layer.update()
        else:
            delta = Vector([v * k for v in fault["delta"]])
            if turned:
                delta = world3.inverted() @ delta                            # 世界の向き → アーマチュアの中
            parent = armature.data.bones[name].parent
            if parent is None:
                pose_bone.location = pose_bone.location + rest3[name].inverted() @ delta          # 親の無い骨（試作と同じ式）
            else:                                                            # 親の今の姿勢を通して、骨の位置へ
                bpy.context.view_layer.update()
                basis = pose[parent.name].matrix.to_3x3() @ parent.matrix_local.to_3x3().inverted() @ rest3[name]
                pose_bone.location = pose_bone.location + basis.inverted() @ delta

    hidden = [obj for obj in bpy.data.objects if obj.type == "MESH" and not obj.hide_viewport]
    for obj in hidden:                                                   # メッシュを評価しない（試作と同じ。速い）
        obj.hide_viewport = True
    keyed, boundary = {}, {}
    try:
        clean = {}                                                       # 元の値（回り, 位置, 大きさ）。キーを打つ前に読んでおく
        pins = fault_pins(faults, names, bone_key_frames(armature, names), scene.frame_start, scene.frame_end)
        wanted = {}
        for fault, name in zip(faults, names):
            wanted.setdefault(name, set()).update(range(max(fault["frames"][0], scene.frame_start), min(fault["frames"][1], scene.frame_end) + 1))
        for k, _, f in pins:
            wanted[names[k]].add(f)
        for f in sorted(set().union(*wanted.values())) if wanted else []:
            scene.frame_set(f)
            for name, frames in wanted.items():
                if f in frames:
                    clean[(name, f)] = (pose[name].rotation_quaternion.copy(), pose[name].location.copy(), pose[name].scale.copy())
        # キーの無い位置と大きさは、どのコマでも同じ元の値（world で行列を入れると小さなずれが出るので、コマごとと終わりに戻す）
        static = {name: clean[(name, f)][1:] for name, f in first_frames(wanted).items()}

        def restore_static():
            for name, (location, scale) in static.items():
                pose[name].location, pose[name].scale = location, scale

        # 区間の外は、破綻を入れる前に元の値で留める（親の区間の外で子の破綻を計算するとき、親が元の姿勢になっている）
        for k, prop, f in pins:
            fault, name = faults[k], names[k]
            pose[name].rotation_quaternion, pose[name].location, pose[name].scale = clean[(name, f)]
            pose[name].keyframe_insert(prop, frame=f)
            if f not in boundary.setdefault(fault["name"], []):
                boundary[fault["name"]].append(f)
        for f in range(scene.frame_start, scene.frame_end + 1):
            active = [(fault, name) for fault, name in zip(faults, names) if fault["frames"][0] <= f <= fault["frames"][1]]
            if not active:
                continue
            restore_static()
            scene.frame_set(f)
            for name in {name for _, name in active}:                    # 元の値から始める（前のコマで打ったキーが混ざらない）
                pose[name].rotation_quaternion, pose[name].location, pose[name].scale = clean[(name, f)]
            bpy.context.view_layer.update()
            frame_world = armature.matrix_world.copy()                   # このコマのアーマチュアの置き場所
            for fault, name in active:
                apply(fault, name, f, frame_world)
                pose[name].keyframe_insert("rotation_quaternion", frame=f)
                if fault["kind"] == "shift":
                    pose[name].keyframe_insert("location", frame=f)
                keyed.setdefault(fault["name"], []).append(f)
        restore_static()
    finally:
        for obj in hidden:
            obj.hide_viewport = False
    scene.frame_set(scene.frame_start)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    report = {"blender": bpy.app.version_string, "armature": armature.name, "scene": scene.name,
              "frames": [scene.frame_start, scene.frame_end],
              "faults": [{"name": fault["name"], "bone": name, "keyed_frames": keyed.get(fault["name"], []),
                          "boundary_frames": boundary.get(fault["name"], [])} for fault, name in zip(faults, names)]}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"faults": len(faults), "keyed": sum(len(v) for v in keyed.values())}))


if __name__ == "__main__":
    main()
