"""Blender の中で動かす: 袖口の骨を、関節の曲げに少し遅れて、一部だけ付いて行かせる。

    blender --background --factory-startup --python sleeve_bones.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（motion.sleeve_bones が書く）:
- sleeves: [{"bone": 袖口の骨, "follow": 曲げに付いて行く骨（前腕など）}]。袖口の骨は親を持つこと（ふつうは follow の骨の親、
  二の腕。静止は follow の骨と同じ所に置くと、肘で前腕と一緒に曲がる）。回転は四元数。
- ratio（曲げのうち付いて行く割合、既定 0.75）、f_hz（遅れのばねの固有振動数、既定 4.0）、damping（減衰比、既定 0.45）、
  substeps（1 コマを分ける数、既定 8）。

付いて行き方（リポジトリ外の試作の手順）:
- follow の骨が、親に固く付いて動いたとき（曲げなし）の向きから、今の向きへの曲げ（骨の Y 軸どうしの最小の回転。
  前腕のねじれは 1 コマで大きく飛ぶことがあるので入れない）を、その向きの中の回転として求める。
- その ratio 倍へ、減衰つきのばねで遅れて付いて行く（止まる所で軽く行きすぎる）。
- 袖口の骨は、曲げなしの向きにその回転を掛けた向き（静止での follow の骨との向きの差は保つ）にして、回転のキーを打つ。
焼き付けた後に袖口の骨の向きを測り直し、狙いと合うかを報告に残す。
コマは .blend のシーンの開始〜終了、1 秒あたりのコマはシーンの値。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じ骨の動きから同じ袖口の向きになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

DEFAULTS = {"ratio": 0.75, "f_hz": 4.0, "damping": 0.45, "substeps": 8}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="sleeve_bones.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--settings", required=True)
    return ap.parse_args(argv)


def rotation_part(m):
    """行列（…, 3, 3 か 4, 4）の回転（拡大を除く。極分解）。"""
    u, _, vt = np.linalg.svd(np.asarray(m, float)[..., :3, :3])
    sign = np.where(np.linalg.det(u @ vt) < 0, -1.0, 1.0)
    u = u.copy()
    u[..., :, 2] *= sign[..., None]
    return u @ vt


def bend(rigid, actual):
    """曲げなしの向き rigid（コマ, 3, 3）から今の向き actual（コマ, 3, 3）への、Y 軸どうしの最小の回転を、rigid の向きの中の
    回転ベクトル（コマ, 3）で返す（ねじれは入れない）。Y 軸がちょうど真逆（180° の曲げ）だと外積の向きが決まらないので、
    rigid の X 軸（前腕なら肘の蝶番の向きに近い）のまわりの半回転にする。"""
    ya, yb = rigid[:, :, 1], actual[:, :, 1]
    axis = np.cross(ya, yb)
    sn = np.linalg.norm(axis, axis=1, keepdims=True)
    cs = np.sum(ya * yb, axis=1, keepdims=True)
    folded = (sn[:, 0] < 1e-9) & (cs[:, 0] < 0)
    axis = np.where(folded[:, None], rigid[:, :, 0], axis / np.maximum(sn, 1e-9))
    world = axis * np.arctan2(sn, cs)
    world[folded] = rigid[folded, :, 0] * np.pi
    return np.einsum("fji,fj->fi", rigid, world)


def sleeve_stats(raw, want, rv):
    """袖口ごとの報告の数: 曲げ（割合を掛ける前）の最大と、狙い（割合を掛けた後）からの遅れの中央値・最大（°）。"""
    lag = np.degrees(np.linalg.norm(rv - want, axis=1))
    return {"bend_max_deg": round(float(np.degrees(np.linalg.norm(raw, axis=1).max())), 1),
            "lag_deg": {"median": round(float(np.median(lag)), 2), "max": round(float(lag.max()), 2)}}


def lagged(want, fps, substeps=8, f_hz=4.0, damping=0.45):
    """回転ベクトルの並び want（コマ, 3）へ、減衰つきのばねで遅れて付いて行った並び。最初のコマは want のまま。"""
    w = 2 * np.pi * f_hz
    h = 1.0 / (fps * substeps)
    want = np.asarray(want, float)
    th, vel = want[0].copy(), np.zeros(3)
    out = np.zeros_like(want)
    out[0] = th
    for f in range(1, len(want)):
        for k in range(substeps):
            tar = want[f - 1] + (want[f] - want[f - 1]) * (k + 1) / substeps
            vel = vel + h * (w * w * (tar - th) - 2 * damping * w * vel)
            th = th + h * vel
        out[f] = th
    return out


def rotvec_matrix(rv):
    """回転ベクトル（…, 3）から回転行列（…, 3, 3）。"""
    rv = np.asarray(rv, float)
    angle = np.linalg.norm(rv, axis=-1)
    k = rv / np.maximum(angle, 1e-15)[..., None]
    K = np.zeros(rv.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2], K[..., 1, 2] = -k[..., 2], k[..., 1], -k[..., 0]
    K[..., 1, 0], K[..., 2, 0], K[..., 2, 1] = k[..., 2], -k[..., 1], k[..., 0]
    eye = np.broadcast_to(np.eye(3), K.shape)
    return eye + np.sin(angle)[..., None, None] * K + (1 - np.cos(angle))[..., None, None] * (K @ K)


def stable(fps, settings):
    """1 刻みの間にばねが回る角度と、減衰の効きが 1 を超えないこと（超えると計算が発散しうる）。"""
    h = 1.0 / (fps * settings["substeps"])
    w = 2 * math.pi * settings["f_hz"]
    return w * h <= 1 and 2 * settings["damping"] * w * h <= 1


def angle_between(a, b):
    """回転行列どうしの角度（°。…）。単精度の行列でも 0 の近くで粗くならないよう、差の回転の sin と cos の atan2 で測る。"""
    d = np.swapaxes(a, -1, -2) @ b
    sin = 0.5 * np.linalg.norm(np.stack([d[..., 2, 1] - d[..., 1, 2], d[..., 0, 2] - d[..., 2, 0], d[..., 1, 0] - d[..., 0, 1]], -1),
                               axis=-1)
    cos = (np.trace(d, axis1=-2, axis2=-1) - 1) / 2
    return np.degrees(np.arctan2(sin, cos))


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc, vl = bpy.context.scene, bpy.context.view_layer
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    arm = armatures[0]
    bones, pb = arm.data.bones, arm.pose.bones
    frames = list(range(sc.frame_start, sc.frame_end + 1))
    fps = sc.render.fps / sc.render.fps_base
    if not stable(fps, settings):
        raise SystemExit(f"ばねが 1 刻みに対して速すぎます（{fps} コマ/秒、substeps {settings['substeps']}）。"
                         "substeps を増やすか、f_hz・damping を下げてください")
    seen = set()
    for s in settings["sleeves"]:
        for key in ("bone", "follow"):
            if s[key] not in bones:
                raise SystemExit(f"骨がありません: {s[key]}")
        if s["bone"] in seen or s["bone"] == s["follow"]:
            raise SystemExit(f"袖口の骨が重なっているか、付いて行く骨と同じです: {s['bone']}")
        seen.add(s["bone"])
        for key in ("bone", "follow"):
            if bones[s[key]].parent is None:
                raise SystemExit(f"{s[key]} に親の骨がありません")
        if pb[s["bone"]].rotation_mode != "QUATERNION":
            raise SystemExit(f"{s['bone']} の回転が四元数ではありません（{pb[s['bone']].rotation_mode}）")
        if not bones[s["bone"]].use_inherit_rotation or bones[s["bone"]].inherit_scale != "FULL":
            raise SystemExit(f"{s['bone']} は親の回転と拡大をそのまま受け継ぐ骨にしてください")

    def rest(name):
        return np.array(bones[name].matrix_local)

    original_view = {(o.name, m.name): m.show_viewport for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers}
    for o in bpy.data.objects:                       # 骨だけを読むので、変形は止める（保存の前に戻す）
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = False
    names = sorted({n for s in settings["sleeves"] for n in (s["follow"], bones[s["follow"]].parent.name,
                                                              bones[s["bone"]].parent.name)})
    pose = {n: np.zeros((len(frames), 4, 4)) for n in names}
    for k, f in enumerate(frames):
        sc.frame_set(f)
        for n in names:
            pose[n][k] = np.array(pb[n].matrix)

    results, targets = [], {}
    for s in settings["sleeves"]:
        follow, parent = s["follow"], bones[s["follow"]].parent.name
        rigid = rotation_part(pose[parent] @ np.linalg.inv(rest(parent)) @ rest(follow))   # 曲げなしの向き
        actual = rotation_part(pose[follow])
        raw = bend(rigid, actual)                    # 割合を掛ける前の曲げ（報告にも使う）
        want = raw * settings["ratio"]
        rv = lagged(want, fps, int(settings["substeps"]), settings["f_hz"], settings["damping"])
        offset = rotation_part(rest(follow)).T @ rotation_part(rest(s["bone"]))   # 静止での follow の骨との向きの差
        target = rigid @ rotvec_matrix(rv) @ offset
        own_parent = bones[s["bone"]].parent.name
        relation = rotation_part(np.linalg.inv(rest(own_parent)) @ rest(s["bone"]))
        basis = np.swapaxes(relation, -1, -2) @ np.swapaxes(rotation_part(pose[own_parent]), -1, -2) @ target
        prev = None
        for k, f in enumerate(frames):
            q = Matrix(basis[k].tolist()).to_quaternion()
            if prev is not None and q.dot(prev) < 0:
                q = -q
            prev = q
            pb[s["bone"]].rotation_quaternion = q
            pb[s["bone"]].keyframe_insert("rotation_quaternion", frame=f)
        targets[s["bone"]] = target
        results.append({"bone": s["bone"], "follow": follow, "parent": own_parent, **sleeve_stats(raw, want, rv)})
    # 確かめ: 焼き付けた袖口の骨の向きが、狙いの向きと合うこと
    worst = 0.0
    for k, f in enumerate(frames):
        sc.frame_set(f)
        vl.update()
        for s in settings["sleeves"]:
            got = rotation_part(np.array(pb[s["bone"]].matrix))
            worst = max(worst, float(angle_between(got, targets[s["bone"]][k])))
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = original_view[(o.name, m.name)]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-sleeve-bones/0.1", "blender": bpy.app.version_string,
              "frames": [frames[0], frames[-1]], "fps": fps, "sleeves": results,
              "settings": {k: settings[k] for k in ("ratio", "f_hz", "damping", "substeps")},
              "bake_check_deg": worst}                                         # 丸めない（許容と比べる）
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"sleeves": len(results), "frames": len(frames), "bake_check_deg": round(worst, 5)}))


if __name__ == "__main__":
    main()
