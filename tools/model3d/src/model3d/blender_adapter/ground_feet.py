"""Blender の中で動かす: 接地のコマで、足の一番低い点が床（z=0）に着くように腰の高さを足す。

    blender --background --factory-startup --python ground_feet.py -- --blend in.blend --points points.npz \
        --out-blend out.blend --report report.json [--bone-prefix mixamorig:] [--foot-object NAME ...] \
        [--first-points-frame N] [--sigma 1.5] [--allow-sink-mm 3]

骨へ移した動きでは、足首の高さで床に合わせても、靴の底やかかとの厚みのぶんだけ足が床に沈む・浮くことがある。
- 足の点: --foot-object で選んだメッシュの全頂点。無ければ、足の骨（Foot・ToeBase・Toe_End）の重みがいちばん大きい頂点と、
  足の骨に親子付けしたメッシュの全頂点（レンダーに出すメッシュだけ。物体とコレクションの hide_render、ビューレイヤーでの除外を見る）。
- 接地のコマ（points の stance でどちらかの足が 0.5 より大きいコマ）では、足の一番低い点を床へ合わせる高さを求める。
  接地の無いコマ（跳んでいる所）は、前後の接地のコマからつなぐ。
- 急に上下しないように、±2.5σ で打ち切ったガウスでならす。ならした後も allow_sink_mm より深く沈むコマは、
  その分の 1.5 倍をならして足す。
- 腰の骨の位置を世界の上向きに動かしてキーを打つ（腰の回転と他の骨は変えない）。
- 直した後に足の一番低い点を測り直し、腰を動かした量だけ動いたかを報告に残す（足が腰の子でない・脚に IK の拘束がある、などで外れる）。
コマは .blend のシーンの開始〜終了。.blend の各コマが points の何コマ目かは、--first-points-frame（続いたコマとみなす）、
motion.retarget がシーンに残した model3d_points_frames、コマの数が同じなら 0 から、の順で決める。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じ入力から同じ腰の高さになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

FEET = ("LeftFoot", "LeftToeBase", "LeftToe_End", "RightFoot", "RightToeBase", "RightToe_End")
CONTACT = 0.5          # stance がこれより大きい足があるコマを接地とみなす
UNDER_GAIN = 1.5       # ならした後に残る沈みへ足す倍率（試作の値）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="ground_feet.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--points", required=True, help="motion.prepare_keypoints の points（stance を使う）")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--bone-prefix", default="mixamorig:")
    ap.add_argument("--foot-object", action="append", default=[], help="足のメッシュの名前（何度でも）。既定は足の骨の重みで選ぶ")
    ap.add_argument("--first-points-frame", type=int, default=None, help=".blend の最初のコマが points の何コマ目か")
    ap.add_argument("--sigma", type=float, default=1.5, help="ならしのガウスの幅（コマ）")
    ap.add_argument("--allow-sink-mm", type=float, default=3.0, help="ならした後に許す沈み（mm）")
    return ap.parse_args(argv)


def gaussian(sigma):
    radius = max(1, math.ceil(2.5 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return kernel / kernel.sum()


def smooth(values, kernel):
    radius = len(kernel) // 2
    return np.convolve(np.pad(np.asarray(values, float), radius, mode="edge"), kernel, mode="valid")


def lift(lowest, stance, sigma=1.5, allow_sink=0.003):
    """足の一番低い点の高さ（コマごと、m）と接地の印（真偽）から、腰に足す高さ（m）を求める。"""
    lowest, stance = np.asarray(lowest, float), np.asarray(stance, bool)
    x = np.arange(len(lowest))
    want = np.interp(x, x[stance], -lowest[stance]) if stance.any() else np.zeros(len(lowest))
    kernel = gaussian(sigma)
    dz = smooth(want, kernel)
    under = np.maximum(0.0, -(lowest + dz) - allow_sink)    # ならした後も沈むコマは底上げする
    return dz + smooth(under, kernel) * UNDER_GAIN


def points_frames(blend_frames, points_count, stored=None, first=None):
    """.blend の各コマが points の何コマ目か（と、その決め方）。"""
    if first is not None:
        frames, source = list(range(first, first + blend_frames)), "parameter"
    elif stored is not None:
        frames, source = [int(f) for f in stored], "blend"
        if len(frames) != blend_frames:
            raise SystemExit(f".blend の model3d_points_frames（{len(frames)} コマ）とシーンのコマ数（{blend_frames}）が違います。"
                             "--first-points-frame を渡してください")
    elif points_count == blend_frames:
        frames, source = list(range(blend_frames)), "same_count"
    else:
        raise SystemExit(f".blend の {blend_frames} コマが points（{points_count} コマ）のどこか分かりません。"
                         "--first-points-frame を渡してください")
    if frames and (min(frames) < 0 or max(frames) >= points_count):
        raise SystemExit(f"points の範囲（0〜{points_count - 1}）の外のコマを指しています: {min(frames)}〜{max(frames)}")
    return frames, source


def stats(lowest, frames):
    lowest = np.asarray(lowest, float)
    return {"median_mm": round(float(np.median(lowest)) * 1000, 2),
            "min_mm": round(float(lowest.min()) * 1000, 2), "min_frame": int(frames[int(lowest.argmin())]),
            "max_mm": round(float(lowest.max()) * 1000, 2), "max_frame": int(frames[int(lowest.argmax())]),
            "below_10mm_frames": int((lowest < -0.010).sum()), "over_30mm_frames": int((lowest > 0.030).sum()),
            "over_60mm_frames": int((lowest > 0.060).sum())}


def rendered(ob, view_layers):
    """ob がレンダーに出るか。物体の hide_render と、ob の入ったコレクションまでたどる道（どれか 1 つ）の、コレクションの
    hide_render とビューレイヤーでの除外を見る。使うビューレイヤーのどれかで出れば出る"""
    if ob.hide_render:
        return False

    def walk(layer):
        if layer.exclude or layer.collection.hide_render:
            return False
        return ob.name in layer.collection.objects or any(walk(child) for child in layer.children)

    return any(walk(vl.layer_collection) for vl in view_layers if vl.use)


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    if not a.sigma > 0 or not a.allow_sink_mm >= 0:
        raise SystemExit("--sigma は 0 より大きく、--allow-sink-mm は 0 以上にしてください")
    with np.load(a.points, allow_pickle=False) as data:
        if "stance" not in data.files:
            raise SystemExit("points に stance がありません（motion.prepare_keypoints の出力を渡してください）")
        stance_all = np.asarray(data["stance"], float)
    stance_all = stance_all.reshape(len(stance_all), -1)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc, vl = bpy.context.scene, bpy.context.view_layer
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    pb = arm.pose.bones
    hips = a.bone_prefix + "Hips"
    if hips not in pb:
        raise SystemExit(f"骨がありません（--bone-prefix を確かめてください）: {hips}")
    blend_frames = list(range(sc.frame_start, sc.frame_end + 1))
    stored = sc.get("model3d_points_frames")
    mapping, source = points_frames(len(blend_frames), len(stance_all), None if stored is None else list(stored),
                                    a.first_points_frame)
    stance = stance_all[mapping].max(1) > CONTACT

    # 足の点を選ぶ（名前 → 頂点の番号。None は全頂点）
    picks, modifiers_off = {}, []
    if a.foot_object:
        for name in a.foot_object:
            o = bpy.data.objects.get(name)
            if o is None or o.type != "MESH":
                raise SystemExit(f"足のメッシュがありません: {name}")
            picks[name] = None
        chosen = "foot_objects"
    else:
        feet, bones = {a.bone_prefix + n for n in FEET}, set(arm.data.bones.keys())
        for o in bpy.data.objects:
            if o.type != "MESH" or not rendered(o, sc.view_layers):   # レンダーに出さないメッシュ（当たりの写し、コレクションで隠した衣装など）は選ばない
                continue
            if o.parent == arm and o.parent_type == "BONE" and o.parent_bone in feet:
                picks[o.name] = None
                continue
            groups = {g.index: g.name for g in o.vertex_groups if g.name in bones}   # 骨の名前の重みだけを見る
            foot_groups = {i for i, n in groups.items() if n in feet}
            if not foot_groups:
                continue
            chosen_vertices = []
            for v in o.data.vertices:
                best, which = 0.0, -1
                for g in v.groups:
                    if g.group in groups and g.weight > best:
                        best, which = g.weight, g.group
                if which in foot_groups:
                    chosen_vertices.append(v.index)
            if chosen_vertices:
                picks[o.name] = np.array(chosen_vertices)
        if not picks:
            raise SystemExit("足の骨（" + "・".join(FEET) + "）の重みがいちばん大きい頂点がありません。"
                             "--foot-object で足のメッシュを選んでください")
        chosen = "bone_weights"
    original_view = {(o.name, m.name): m.show_viewport for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers}
    for o in bpy.data.objects:                       # 測らないメッシュは変形を止めて速くする（保存の前に戻す）
        if o.type == "MESH" and o.name not in picks:
            for m in o.modifiers:
                m.show_viewport = False
    sc.frame_set(blend_frames[0])
    dg = bpy.context.evaluated_depsgraph_get()
    for name, index in picks.items():                # 番号で選んだ頂点は、頂点の数を変えるモディファイアを止めて測る
        o = bpy.data.objects[name]
        if index is None:
            continue
        ev = o.evaluated_get(dg)
        count = len(ev.to_mesh().vertices)
        ev.to_mesh_clear()
        if count != len(o.data.vertices):
            for m in o.modifiers:
                if m.type != "ARMATURE" and m.show_viewport:
                    m.show_viewport = False
                    modifiers_off.append([name, m.name])

    def lowest_now():
        dg = bpy.context.evaluated_depsgraph_get()
        low = math.inf
        for name, index in picks.items():
            ev = bpy.data.objects[name].evaluated_get(dg)
            me = ev.to_mesh()
            co = np.empty(len(me.vertices) * 3, np.float32)
            me.vertices.foreach_get("co", co)
            ev.to_mesh_clear()
            co = co.reshape(-1, 3) if index is None else co.reshape(-1, 3)[index]
            world = np.array(ev.matrix_world)
            low = min(low, float((co @ world[2, :3] + world[2, 3]).min()))
        return low

    before = []
    for f in blend_frames:
        sc.frame_set(f)
        before.append(lowest_now())
    dz = lift(before, stance, a.sigma, a.allow_sink_mm / 1000)
    AW = arm.matrix_world.copy()
    AWi = AW.inverted()
    hp = pb[hips]
    for k, f in enumerate(blend_frames):
        sc.frame_set(f)
        m = hp.matrix.copy()
        m.translation = AWi @ ((AW @ m.translation) + Vector((0.0, 0.0, float(dz[k]))))
        hp.matrix = m
        vl.update()
        hp.keyframe_insert("location", frame=f)
    after = []
    for f in blend_frames:
        sc.frame_set(f)
        after.append(lowest_now())
    check = float(np.abs(np.asarray(after) - np.asarray(before) - dz).max()) * 1000   # 丸めない（許容と比べる）
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = original_view[(o.name, m.name)]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-ground-feet/0.1", "blender": bpy.app.version_string,
              "frames": [blend_frames[0], blend_frames[-1]], "bone_prefix": a.bone_prefix,
              "points_frames": {"first": mapping[0], "last": mapping[-1], "from": source},
              "feet": {"from": chosen, "vertices": {n: (None if i is None else int(len(i))) for n, i in picks.items()},
                       "modifiers_off": modifiers_off},
              "stance_frames": int(stance.sum()), "sigma_frames": a.sigma, "allow_sink_mm": a.allow_sink_mm,
              "lift_mm": {"min": round(float(dz.min()) * 1000, 2), "max": round(float(dz.max()) * 1000, 2),
                          "largest_change_per_frame": round(float(np.abs(np.diff(dz)).max()) * 1000, 2) if len(dz) > 1 else 0.0},
              "lowest_point": {"before": stats(before, blend_frames), "after": stats(after, blend_frames)},
              "lift_check_mm": check}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"frames": len(blend_frames), "stance_frames": report["stance_frames"], "lift_mm": report["lift_mm"],
                      "lift_check_mm": round(check, 4)}))


if __name__ == "__main__":
    main()
