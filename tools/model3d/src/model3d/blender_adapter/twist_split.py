"""Blender の中で動かす: 前腕のねじれを、肘と手首に半分ずつ持たせ直す。

    blender --background --factory-startup --python twist_split.py -- --blend in.blend --out-blend out.blend --report report.json \
        [--bone-prefix mixamorig:] [--frames 1:101]

動きを骨へ移すと、前腕が自分の軸のまわりに半回転し、手がそれを逆に回して打ち消すことがある（肘と手首で ±180° 近く）。
手の向きと前腕の向きは変えずに、前腕を軸のまわりに回し直し、肘と手首のねじれの合計（−180〜180° に戻したもの）を半分ずつ
持たせる。手首と肘の皮膚がつぶれなくなる。ねじれは、骨の回転を「曲がり × 骨の軸（Y）のまわりのねじれ」に分けた角度。
手の骨の親が前腕の骨で、両方の回転が四元数のリグだけを扱う（違えば止まる）。
直した後に、手の向き・前腕の向き・指先の位置が変わっていないことを確かめて報告に残す（指の骨が無いリグは手の骨の先）。
コマは --frames（a:b は b を含む）か、既定でシーンの開始〜終了。範囲の外のコマは元のままなので、範囲の端でねじれの分け方が
切り替わる（動き全体に当てるのが基本）。骨の名前は --bone-prefix + Mixamo の名前。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じ入力から同じ骨の行列になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="twist_split.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--bone-prefix", default="mixamorig:")
    ap.add_argument("--frames", default="", help="a:b（b を含む）。既定はシーンの開始〜終了")
    return ap.parse_args(argv)


def frame_range(spec, start, end):
    if not spec:
        return list(range(start, end + 1))
    try:
        first, last = (int(x) for x in spec.split(":"))
    except ValueError:
        raise SystemExit(f"--frames は a:b（b を含む）にしてください: {spec}") from None
    if last < first:
        raise SystemExit(f"--frames は a:b（a ≤ b）にしてください: {spec}")
    return list(range(first, last + 1))


def wrapped(angles):
    return (np.asarray(angles) + np.pi) % (2 * np.pi) - np.pi


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Quaternion

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    pb = arm.pose.bones
    frames = frame_range(a.frames, sc.frame_start, sc.frame_end)
    original_view = {(o.name, m.name): m.show_viewport for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers}
    for o in bpy.data.objects:                       # 骨だけを読むので、変形は止める（保存の前に戻す）
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = False

    def bone(name):
        full = a.bone_prefix + name
        if full not in pb:
            raise SystemExit(f"骨がありません（--bone-prefix を確かめてください）: {full}")
        return pb[full]

    def twist_y(q):
        """q を「曲がり × 軸まわりのねじれ」に分けたときのねじれの角度（ラジアン）"""
        return 2 * math.atan2(q.y, q.w)

    def ty(phi):
        return Quaternion((0.0, 1.0, 0.0), phi)

    def fingertip(side):
        index = a.bone_prefix + side + "HandIndex3"
        return pb[index].matrix.translation.copy() if index in pb else bone(side + "Hand").tail.copy()

    before, sides = {}, {}
    for side in ("Left", "Right"):
        fo, ha = bone(side + "ForeArm"), bone(side + "Hand")
        if ha.parent != fo:                              # 前腕のねじれを手で打ち消す式は、手が前腕の子のときだけ成り立つ
            raise SystemExit(f"{ha.name} の親が {fo.name} ではありません（{ha.parent.name if ha.parent else 'なし'}）")
        for b in (fo, ha):
            if b.rotation_mode != "QUATERNION":
                raise SystemExit(f"{b.name} の回転が四元数ではありません（{b.rotation_mode}）")
        relative = (arm.data.bones[fo.name].matrix_local.to_3x3().inverted() @ arm.data.bones[ha.name].matrix_local.to_3x3()).to_quaternion()
        qf, qh = [], []
        for f in frames:
            sc.frame_set(f)
            qf.append(fo.rotation_quaternion.copy())
            qh.append(ha.rotation_quaternion.copy())
            before[side, f] = (ha.matrix.to_quaternion(), fo.matrix.to_3x3().col[1].normalized(), fingertip(side))
        e = np.unwrap([twist_y(q) for q in qf])
        w = np.unwrap([twist_y(q) for q in qh])
        total = wrapped(e + w)                       # 合計は小さい（打ち消し合っている）ので、−180〜180° に戻してから半分にする
        phi = total / 2 - e
        pf = ph = None
        after_e, after_w = [], []
        for k, f in enumerate(frames):
            nf = qf[k] @ ty(float(phi[k]))
            nh = relative.inverted() @ ty(-float(phi[k])) @ relative @ qh[k]
            if pf is not None and nf.dot(pf) < 0:
                nf = -nf
            if ph is not None and nh.dot(ph) < 0:
                nh = -nh
            pf, ph = nf.copy(), nh.copy()
            fo.rotation_quaternion, ha.rotation_quaternion = nf, nh
            fo.keyframe_insert("rotation_quaternion", frame=f)
            ha.keyframe_insert("rotation_quaternion", frame=f)
            after_e.append(twist_y(nf))
            after_w.append(twist_y(nh))
        sides[side] = {
            "elbow_twist_median_deg": {"before": round(float(np.degrees(np.median(np.abs(wrapped(e))))), 2),
                                       "after": round(float(np.degrees(np.median(np.abs(wrapped(after_e))))), 2)},
            "wrist_twist_median_deg": {"before": round(float(np.degrees(np.median(np.abs(wrapped(w))))), 2),
                                       "after": round(float(np.degrees(np.median(np.abs(wrapped(after_w))))), 2)},
            "half_sum_max_deg": round(float(np.degrees(np.abs(total / 2).max())), 2)}
    # 確かめ: 手の向き・前腕の向き・指先の位置が変わっていないこと。角度は単精度の acos が 1 の近くで粗く、四元数の符号も
    # 反転しうるので、差の回転のベクトル部の長さ（sin(θ/2)）と、外積と内積の atan2 で測る
    worst = [0.0, 0.0, 0.0]
    for f in frames:
        sc.frame_set(f)
        bpy.context.view_layer.update()
        for side in ("Left", "Right"):
            hq, fy, tip = before[side, f]
            turn = hq.rotation_difference(bone(side + "Hand").matrix.to_quaternion())
            worst[0] = max(worst[0], math.degrees(2 * math.asin(min(1.0, math.hypot(turn.x, turn.y, turn.z)))))
            now = bone(side + "ForeArm").matrix.to_3x3().col[1].normalized()
            worst[1] = max(worst[1], math.degrees(math.atan2(fy.cross(now).length, fy.dot(now))))
            worst[2] = max(worst[2], (tip - fingertip(side)).length * 1000)
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = original_view[(o.name, m.name)]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    unchanged = {"hand_turn_deg": worst[0], "forearm_direction_deg": worst[1], "fingertip_mm": worst[2]}   # 丸めない（許容と比べる）
    report = {"schema_version": "model3d-twist-split/0.1", "blender": bpy.app.version_string, "frames": [frames[0], frames[-1]],
              "bone_prefix": a.bone_prefix, "sides": sides, "unchanged": unchanged}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"frames": len(frames), "unchanged": {k: round(v, 4) for k, v in unchanged.items()}}))


if __name__ == "__main__":
    main()
