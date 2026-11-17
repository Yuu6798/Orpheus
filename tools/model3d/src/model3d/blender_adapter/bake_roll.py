"""Blender の中で動かす: 手の回転の一部を写す骨（model.twist_bones の roll）の回転を、拘束に頼らず正確な値でコマごとのキーに焼く
（motion.bake_roll）。焼いた骨の Copy Rotation は止める（mute）。

    blender --background --factory-startup --python bake_roll.py -- --blend dance.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: roll の骨は、Copy Rotation（Y だけ・Add・ローカルどうし）で、写す骨（前腕なら手）の
軸のまわりの回転を足す。VRM Add-on はこれを VRM 1.0 の roll の拘束として書き出し、VRM の roll は回転を「曲がり × 軸のまわりのねじれ」に分けた
ねじれを使う。Blender の Copy Rotation は回転を 3 つの角度（オイラー角）に分けた Y を使うので、手を大きく曲げたコマでは VRM と合わない
（3 作目で中央値 3°、いちばん大きいコマで 30〜55°。どの分け方の順でも 24〜55° 残る）。踊りの動画は Blender で描くので、そのままだと、
VRM を動かすアプリとは手首のまわりの形が少し変わる。

やり方（bones の roll の骨ごと。骨の順に）:
  1. シーンの全コマで、写す骨（拘束の subtarget）の回転（初期姿勢からの、骨自身の回転 matrix_basis の四元数）の、骨の軸 Y のまわりのねじれ
     2·atan2(y, w) を出し、−π〜π に戻して、拘束の影響（influence）を掛ける（VRM の roll の重み）。
  2. 比べる用に、止める前の拘束が出していた角度（親から見た骨の回転の Y のまわりのねじれ）をコマごとに測る。
  3. 拘束を止め、骨の回転を「軸 Y のまわりにその角度」にして、コマごとにキーを打つ（前のコマの四元数と向きをそろえる）。骨自身の回転は使わない。
  「向くだけの骨」（Damped Track）は、VRM の aim と同じ決め方（いちばん小さい回転で向く）なので、そのまま。
  拘束を止めると VRM には書き出されないので、焼くのは踊りの .blend だけ（モデルは拘束のまま）。

報告は model3d-bake-roll/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じキーになることを
確かめた（docs/model3d.md）。CI には Blender が無い（角度の戻し方と、拘束の形の見分け方はテストする）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"bones": [], "armature": None}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="bake_roll.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def wrapped(angles):
    """角度（ラジアン）の並びを −π〜π に戻す（台本と同じ式）。"""
    return (np.asarray(angles, dtype=np.float64) + np.pi) % (2 * np.pi) - np.pi


def roll_problem(constraint):
    """Copy Rotation が roll の形（Y だけ・Add・ローカルどうし・相手が骨）でなければ、その理由。roll の形なら ""。"""
    axes = (bool(constraint.use_x), bool(constraint.use_y), bool(constraint.use_z))
    if axes != (False, True, False):
        return f"軸が Y だけではありません（X {axes[0]}・Y {axes[1]}・Z {axes[2]}）"
    if constraint.mix_mode != "ADD":
        return f"混ぜ方が Add ではありません（{constraint.mix_mode}）"
    if constraint.target_space != "LOCAL" or constraint.owner_space != "LOCAL":
        return f"空間がローカルどうしではありません（{constraint.target_space}・{constraint.owner_space}）"
    if not constraint.subtarget:
        return "写す骨（subtarget）がありません"
    return ""


def off_by_deg(before, after):
    """止める前の拘束の角度と、焼いた角度の差（度。0〜180）の中央値・95% 点・最大。"""
    difference = np.degrees(np.abs((np.asarray(before) - np.asarray(after) + np.pi) % (2 * np.pi) - np.pi))
    return {"median": round(float(np.median(difference)), 1), "p95": round(float(np.percentile(difference, 95)), 1),
            "max": round(float(difference.max()), 1)}


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Quaternion

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not settings["armature"] or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + settings['armature'] + '）' if settings['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    names = settings["bones"]
    if not names:                                       # 省くと、止めていない roll の形の Copy Rotation がある骨を全部
        names = [pose_bone.name for pose_bone in armature.pose.bones
                 if any(c.type == "COPY_ROTATION" and not c.mute and not roll_problem(c) for c in pose_bone.constraints)]
        if not names:
            raise SystemExit(f"{armature.name} に、止めていない roll の形の Copy Rotation のある骨がありません（bones で選んでください）")
    plans = []
    for name in names:
        pose_bone = armature.pose.bones.get(name)
        if pose_bone is None:
            raise SystemExit(f"{armature.name} に骨 {name} がありません（bones）")
        live = [c for c in pose_bone.constraints if c.type == "COPY_ROTATION" and not c.mute]
        if len(live) != 1:
            raise SystemExit(f"{name} に、止めていない Copy Rotation が {len(live)} 個あります（1 個の骨だけを焼ける）")
        problem = roll_problem(live[0])
        if problem:
            raise SystemExit(f"{name} の Copy Rotation が roll の形ではありません: {problem}")
        if live[0].target != armature or live[0].subtarget not in armature.pose.bones:
            raise SystemExit(f"{name} の Copy Rotation の相手が、このアーマチュアの骨ではありません")
        if pose_bone.parent is None:
            raise SystemExit(f"{name} に親の骨がありません（親から見た回転を測れない）")
        plans.append((pose_bone, live[0]))

    hidden = [(obj, obj.hide_viewport) for obj in bpy.data.objects if obj.type == "MESH"]
    for obj, _ in hidden:
        obj.hide_viewport = True                        # 骨だけ評価すれば足りる（台本と同じ）
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    angles = {pose_bone.name: [] for pose_bone, _ in plans}
    for frame in frames:
        scene.frame_set(frame)
        for pose_bone, constraint in plans:
            q = armature.pose.bones[constraint.subtarget].matrix_basis.to_quaternion()
            angles[pose_bone.name].append(2 * math.atan2(q.y, q.w))
    report = {}
    for pose_bone, constraint in plans:
        pose_bone.rotation_mode = "QUATERNION"
        before = []
        for frame in frames:                            # 止める前の、拘束が出していた角度（比べる用）
            scene.frame_set(frame)
            rest = pose_bone.bone.parent.matrix_local.inverted() @ pose_bone.bone.matrix_local
            now = (rest.inverted() @ (pose_bone.parent.matrix.inverted() @ pose_bone.matrix)).to_quaternion()
            before.append(2 * math.atan2(now.y, now.w))
        constraint.mute = True
        values = wrapped(angles[pose_bone.name]) * constraint.influence
        previous = None
        for frame, angle in zip(frames, values):
            q = Quaternion((0.0, 1.0, 0.0), float(angle))
            if previous is not None and q.dot(previous) < 0:
                q = -q
            previous = q.copy()
            pose_bone.rotation_quaternion = q
            pose_bone.keyframe_insert("rotation_quaternion", frame=frame)
        report[pose_bone.name] = {"from": constraint.subtarget, "constraint": constraint.name, "influence": round(float(constraint.influence), 6),
                                  "frames": len(frames), "largest_twist_deg": round(float(np.degrees(np.abs(values)).max()), 1),
                                  "constraint_was_off_by_deg": off_by_deg(before, values)}
    for obj, state in hidden:
        obj.hide_viewport = state
    scene.frame_set(frames[0])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    out = {"schema_version": "model3d-bake-roll/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
           "frames": [frames[0], frames[-1]], "bones": report, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(out, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "bones": list(report)}))


if __name__ == "__main__":
    main()
