"""Blender の中で動かす: 鎖の骨（髪など）を、ばねで体の動きに遅れて揺らし、回転のキーに焼き付ける。

    blender --background --factory-startup --python spring_bones.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（motion.spring_bones が書く）:
- chains: 鎖の根元の骨の名前の並び。根元から、子が 1 本の間を毛先までたどる（枝分かれは止める）。根元より下の骨は
  親につながっている（head が親の tail）こと。どの鎖も同じ骨の数。
- colliders: 当たりのカプセル [{"from": 骨, "to": 骨, "radius_mm": 半径}]（2 本の骨の head を結ぶ。同じ骨か、head が重なる
  骨にすると、その head を中心にした球）。
- links: 離れすぎないようにする鎖の組 [[i, j], ...]（chains の番号）。
- f_root_hz・f_tip_hz（根元・毛先のばねの固有振動数）、damping（減衰比）、bend（曲がりのならし）、substeps（1 コマを分ける数）、
  push（当たりで 1 刻みに押し出す割合）、settle_s（踊りの前に最初の形で落ち着かせる秒数）。
- target: rest（既定。鎖が親の骨に固く付いて動いたときの形へ引く）か animation（鎖の骨にすでにある動きへ引く）。
- follow: rest のときの付いて行き方を、骨と静止の高さの並び [{"bone": 骨, "height_m": 高さ}] にする。各節は、静止の高さで
  前後の骨の動きを線形に混ぜて付いて行く（いちばん上より上・いちばん下より下は端の骨に固く付く）。背中に沿う長い髪に使う。

ばねの計算（リポジトリ外の試作の手順）:
- 節（骨の head と、毛先の骨の tail）を、狙いの位置へばねで引く。根元ほどばねが強く、毛先ほど弱い（固有振動数を根元から
  毛先へ等比で変える）。減衰は狙いの速さとの差に掛ける。根元の節は狙いの位置に止める。
- 1 刻みごとに、狙いからのずれを隣の節とならし（bend）、節の間の長さを根元から毛先へ 2 回戻し、links の鎖が静止の 1.25 倍
  より離れないように寄せ、当たりのカプセルの内側の節を push の割合だけ外へ出す（静止で近い節は、その近さまで許す）。
骨にする: 狙いの骨の向きに、毛の向き（節から次の節）への最小の回転を足し、親からの回転にしてキーを打つ。根元の骨には、
head をばねの根元の節に置く位置のキーも打つ。焼き付けた後にコマごとに骨を測り直し、骨の向きがばねの節の向きと、根元の
head が根元の節と合うかを報告に残す。節の間の長さは、鎖のつなぎと当たりが節を動かすぶん伸び縮みするが、骨は長さを保つ。
その伸び縮みと、その先の節の位置の差も報告に残す。
コマは .blend のシーンの開始〜終了、1 秒あたりのコマはシーンの値。
**検証**: ばねの計算は、リポジトリ外の試作と同じ入力から同じ節の位置になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

DEFAULTS = {"f_root_hz": 3.2, "f_tip_hz": 1.1, "damping": 0.30, "bend": 0.12, "substeps": 8, "push": 0.3, "settle_s": 1.0,
            "target": "rest", "follow": [], "colliders": [], "links": []}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="spring_bones.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--settings", required=True)
    return ap.parse_args(argv)


def along_axis(points, a, ab):
    """点を、a から a + ab への軸に投げた位置（0〜1）。軸の長さが 0（両端が同じ骨・重なる head）なら、a を中心にした球として 0。"""
    length2 = ab @ ab
    if length2 <= 1e-12:
        return np.zeros(np.shape(points)[:-1])
    return np.clip(((points - a) @ ab) / length2, 0, 1)


def perpendicular(v, w=None):
    """v に垂直な単位ベクトル。w があれば v と w の両方に垂直にする（v と w が平行なら、v に垂直なだけ）。"""
    v = np.asarray(v, float)
    if w is not None:
        c = np.cross(v, w)
        if np.linalg.norm(c) > 1e-9:
            return c / np.linalg.norm(c)
    c = np.cross(v, np.eye(3)[int(np.argmin(np.abs(v)))])    # v といちばん平行でない座標軸
    return c / np.linalg.norm(c)


def escape(offsets, ab, segments):
    """当たりの軸の上に乗った節を押し出す向き（n, 3 の単位ベクトル）。1 刻み前の位置への向き（offsets）を使い、それも
    軸の上なら、軸と、親の節からの向き（segments）の両方に垂直な向きにする（鎖に沿って押すと、すぐ後の長さの戻しで
    軸の上に戻される）。球なら、親の節からの向きに垂直にする。"""
    out = np.zeros((len(offsets), 3))
    axis = ab / np.linalg.norm(ab) if np.linalg.norm(ab) > 1e-6 else None
    for i, (offset, segment) in enumerate(zip(np.asarray(offsets, float), np.asarray(segments, float))):
        if np.linalg.norm(offset) >= 1e-6:
            out[i] = offset / np.linalg.norm(offset)
        elif axis is not None:
            out[i] = perpendicular(axis, segment if np.linalg.norm(segment) > 1e-9 else None)
        else:
            out[i] = perpendicular(segment) if np.linalg.norm(segment) > 1e-9 else (0.0, 0.0, 1.0)
    return out


def rest_limit(nodes, collider):
    """静止で当たりに近い節は、その近さまでを許す（最初から押し出さないように）。"""
    ab = collider["qb"] - collider["qa"]
    t = along_axis(nodes, collider["qa"], ab)
    return np.linalg.norm(nodes - (collider["qa"] + t[..., None] * ab), axis=-1)


def simulate(nodes, targets, colliders=(), links=(), fps=30, substeps=8, f_root=3.2, f_tip=1.1, damping=0.30, bend=0.12,
             push=0.3, settle=1.0):
    """節（鎖, 節, 3）の静止の位置と、コマごとの狙いの位置（コマ, 鎖, 節, 3）から、ばねで動かした節の位置と、当たりで
    押した回数を返す。colliders は {pa, pb: コマごとの両端（コマ, 3）、qa, qb: 静止の両端、ra, rb: 両端の半径}。"""
    nodes, targets = np.asarray(nodes, float), np.asarray(targets, float)
    frames = len(targets)
    chains, count = nodes.shape[:2]
    dt = 1.0 / (fps * substeps)
    lengths = np.linalg.norm(np.diff(nodes, axis=1), axis=2)
    rest_cap = [rest_limit(nodes, c) for c in colliders]
    s = np.linspace(0, 1, count)[None, :]
    w0 = 2 * np.pi * f_root * (f_tip / f_root) ** s     # 根元から毛先へ弱く
    kk = (w0 ** 2)[..., None]
    cc = (2 * damping * w0)[..., None]

    def step(x, v, tar, tar_v):
        acc = kk * (tar - x) + cc * (tar_v - v)
        v = v + dt * acc
        x_new = x + dt * v
        x_new[:, 0] = tar[:, 0]
        off = x_new - tar                                # 狙いからのずれをならす（もとの反りは残る）
        x_new[:, 1:-1] += bend * (0.5 * (off[:, :-2] + off[:, 2:]) - off[:, 1:-1])
        x_new[:, -1] += bend * (off[:, -2] - off[:, -1])
        for _ in range(2):                               # 節の間の長さを保つ（根元から毛先へ 2 回）
            for i in range(count - 1):
                d = x_new[:, i + 1] - x_new[:, i]
                ln = np.linalg.norm(d, axis=1, keepdims=True)
                x_new[:, i + 1] = x_new[:, i] + d * (lengths[:, i:i + 1] / np.maximum(ln, 1e-6))
        for c0, c1 in links:                             # 隣の鎖と離れすぎないように
            d0 = np.linalg.norm(nodes[c1] - nodes[c0], axis=1)
            d = x_new[c1] - x_new[c0]
            ln = np.linalg.norm(d, axis=1)
            over = np.maximum(ln - d0 * 1.25, 0) / np.maximum(ln, 1e-6)
            x_new[c0, 1:] += (0.5 * over[:, None] * d)[1:]
            x_new[c1, 1:] -= (0.5 * over[:, None] * d)[1:]
        return x_new, (x_new - x) / dt

    def collide(x, f, before):
        hits = 0
        for c, dcap in zip(colliders, rest_cap):
            a0, ab = c["pa"][f], c["pb"][f] - c["pa"][f]
            t = along_axis(x, a0, ab)
            q = a0 + t[..., None] * ab
            rc = 0.98 * np.minimum(c["ra"] + t * (c["rb"] - c["ra"]), dcap)
            d = x - q
            ln = np.linalg.norm(d, axis=2)
            m = ln < rc
            m[:, 0] = False
            if m.any():                                  # 1 刻みで押し切らず、少しずつ押す（毛先が急に飛ばないように）
                out = d / np.maximum(ln[..., None], 1e-6)
                on_axis = m & (ln < 1e-6)                # 軸の上の節は向きが決まらないので、逃げる向きを別に決める
                if on_axis.any():
                    segments = np.zeros_like(x)
                    segments[:, 1:] = x[:, 1:] - x[:, :-1]
                    out[on_axis] = escape(before[on_axis] - q[on_axis], ab, segments[on_axis])
                x[m] += push * (q[m] + out[m] * rc[m, None] - x[m])
                hits += int(m.sum())
        return x, hits

    x = targets[0].copy()                                # 踊りの前に、最初の形で落ち着かせる
    v = np.zeros_like(x)
    for _ in range(int(round(settle * fps * substeps))):
        x, v = step(x, v, targets[0], np.zeros_like(x))
    sim = np.zeros((frames, chains, count, 3))
    sim[0] = x
    hits = 0
    for f in range(1, frames):
        tv = (targets[f] - targets[f - 1]) * fps
        for k in range(substeps):
            tar = targets[f - 1] + (targets[f] - targets[f - 1]) * (k + 1) / substeps
            before = x                                   # 1 刻み前の位置（軸の上に乗った節の逃げる向き）
            x, v = step(x, v, tar, tv)
            x, h = collide(x, f, before)
            hits += h
        sim[f] = x
    return sim, hits


def stable(fps, settings):
    """1 刻みの間に、いちばん速いばねが回る角度と、減衰の効きが 1 を超えないこと（超えると計算が発散しうる）。"""
    dt = 1.0 / (fps * settings["substeps"])
    fastest = 2 * math.pi * max(settings["f_root_hz"], settings["f_tip_hz"])
    return fastest * dt <= 1 and 2 * settings["damping"] * fastest * dt <= 1


def height_weights(heights, anchors):
    """静止の高さ（n）ごとの、付いて行く骨の重み（n, 骨）。骨の高さの間を線形に混ぜ、端より外は端の骨だけ。"""
    anchors = np.asarray(anchors, float)
    order = np.argsort(anchors)
    return np.stack([np.interp(heights, anchors[order], (np.arange(len(anchors)) == j).astype(float)[order])
                     for j in range(len(anchors))], -1)


def blend_follow(moves, weights, points):
    """骨ごとの静止から今への変換 moves（コマ, 骨, 4, 4）を重み（n, 骨）で混ぜて、点（n, 3）の今の位置（コマ, n, 3）と
    回り（混ぜた回転を直交に戻したもの。コマ, n, 3, 3）を返す。"""
    m = np.einsum("fbij,nb->fnij", moves, weights)
    x = np.einsum("fnij,nj->fni", m, np.c_[points, np.ones(len(points))])[..., :3]
    u, _, vt = np.linalg.svd(m[..., :3, :3])
    return x, u @ vt


def root_location(parent_follow, rest_root, heads):
    """根元の骨の head を heads（コマ, 3）に置く位置のキー（コマ, 3）。parent_follow は親の骨の静止から今への変換（コマ, 4, 4）。"""
    local = np.linalg.inv(rest_root) @ np.linalg.inv(parent_follow)
    return np.einsum("fij,fj->fi", local, np.c_[heads, np.ones(len(heads))])[:, :3]


def motion_stats(sim, targets):
    """狙いからのずれ（節ごとの中央値・99% 点、cm）、1 コマの動き・その変化（99% 点・最大、cm）。"""
    off = np.linalg.norm(sim - targets, axis=3)
    step = np.linalg.norm(np.diff(sim, axis=0), axis=3)
    change = np.linalg.norm(np.diff(sim, n=2, axis=0), axis=3)
    return {"offset_cm_median_by_node": np.round(np.median(off, axis=(0, 1)) * 100, 1).tolist(),
            "offset_cm_p99_by_node": np.round(np.percentile(off, 99, axis=(0, 1)) * 100, 1).tolist(),
            "step_cm": {"p99": round(float(np.percentile(step, 99)) * 100, 1), "max": round(float(step.max()) * 100, 1)}
            if len(step) else None,
            "change_of_step_cm": {"p99": round(float(np.percentile(change, 99)) * 100, 1), "max": round(float(change.max()) * 100, 1)}
            if len(change) else None}


def rotation_part(m):
    """行列（…, 3, 3 か 4, 4）の回転（拡大を除く。極分解）。"""
    u, _, vt = np.linalg.svd(np.asarray(m, float)[..., :3, :3])
    sign = np.where(np.linalg.det(u @ vt) < 0, -1.0, 1.0)   # 鏡映にならないように
    u = u.copy()
    u[..., :, 2] *= sign[..., None]
    return u @ vt


def minimal_rotation(a, b):
    """単位ベクトル a を b へ向ける最小の回転（…, 3, 3）。向きが真逆のときは回さない（試作と同じ）。"""
    a = a / np.linalg.norm(a, axis=-1, keepdims=True)
    b = b / np.linalg.norm(b, axis=-1, keepdims=True)
    axis = np.cross(a, b)
    sn = np.linalg.norm(axis, axis=-1, keepdims=True)
    cs = np.sum(a * b, axis=-1, keepdims=True)
    k = axis / np.maximum(sn, 1e-12)
    angle = np.arctan2(sn, cs)[..., 0]
    K = np.zeros(k.shape[:-1] + (3, 3))
    K[..., 0, 1], K[..., 0, 2], K[..., 1, 2] = -k[..., 2], k[..., 1], -k[..., 0]
    K[..., 1, 0], K[..., 2, 0], K[..., 2, 1] = k[..., 2], -k[..., 1], k[..., 0]
    eye = np.broadcast_to(np.eye(3), K.shape)
    return eye + np.sin(angle)[..., None, None] * K + (1 - np.cos(angle))[..., None, None] * (K @ K)


def chain_basis(parent_rotation, rest_relation, target_rotation, directions):
    """鎖の骨の、親からの回転（コマ, 骨, 3, 3）。parent_rotation: 鎖の親の骨の回転（コマ, 3, 3）、rest_relation: 静止での
    親から見た回転（骨, 3, 3）、target_rotation: 狙いの骨の回転（コマ, 骨, 3, 3）、directions: 毛の向き（コマ, 骨, 3）。
    狙いの骨の Y 軸（head → tail）から毛の向きへの最小の回転を足す。"""
    frames, bones = directions.shape[:2]
    out = np.zeros((frames, bones, 3, 3))
    above = parent_rotation
    for j in range(bones):
        new = minimal_rotation(target_rotation[:, j, :, 1], directions[:, j]) @ target_rotation[:, j]
        out[:, j] = np.swapaxes(rest_relation[j], -1, -2) @ np.swapaxes(above, -1, -2) @ new
        above = new
    return out


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Quaternion

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
                         "substeps を増やすか、f_root_hz・f_tip_hz・damping を下げてください")

    # 鎖をたどる
    chains, seen = [], set()
    for root in settings["chains"]:
        if root not in bones:
            raise SystemExit(f"鎖の根元の骨がありません: {root}")
        if bones[root].parent is None:
            raise SystemExit(f"鎖の根元の骨に親がありません: {root}")
        chain = [root]
        while True:
            children = bones[chain[-1]].children
            if len(children) > 1:
                raise SystemExit(f"鎖が枝分かれしています: {chain[-1]}（{', '.join(c.name for c in children)}）")
            if not children:
                break
            child = children[0]
            if (child.head_local - bones[chain[-1]].tail_local).length > 1e-6:
                raise SystemExit(f"鎖の骨が親につながっていません: {child.name}")
            chain.append(child.name)
        for name in chain:
            if name in seen:
                raise SystemExit(f"骨が 2 つの鎖に入っています: {name}")
            seen.add(name)
            if pb[name].rotation_mode != "QUATERNION":
                raise SystemExit(f"{name} の回転が四元数ではありません（{pb[name].rotation_mode}）")
            if not bones[name].use_inherit_rotation or bones[name].inherit_scale != "FULL":
                raise SystemExit(f"{name} は親の回転と拡大をそのまま受け継ぐ骨にしてください")
        chains.append(chain)
    if len({len(c) for c in chains}) != 1:
        raise SystemExit("どの鎖も同じ骨の数にしてください（違う長さの鎖は別の工程に分ける）: "
                         + ", ".join(f"{c[0]} {len(c)}" for c in chains))
    for c0, c1 in settings["links"]:
        if not (0 <= c0 < len(chains) and 0 <= c1 < len(chains)) or c0 == c1:
            raise SystemExit(f"links の鎖の番号が違います: {[c0, c1]}")
    for c in settings["colliders"]:
        for key in ("from", "to"):
            if c[key] not in bones:
                raise SystemExit(f"当たりの骨がありません: {c[key]}")
    for anchor in settings["follow"]:
        if anchor["bone"] not in bones:
            raise SystemExit(f"付いて行く骨がありません: {anchor['bone']}")
    parents = [bones[c[0]].parent.name for c in chains]

    def rest(name):
        return np.array(bones[name].matrix_local)

    # 静止の節と、骨どうしの静止での回転
    rest_nodes = np.array([[np.array(bones[n].head_local) for n in c] + [np.array(bones[c[-1]].tail_local)] for c in chains])
    relation = np.array([[rotation_part(np.linalg.inv(rest(p if j == 0 else c[j - 1])) @ rest(n)) for j, n in enumerate(c)]
                         for c, p in zip(chains, parents)])

    # コマごとに読む（変形は止めて速くする。保存の前に戻す）
    original_view = {(o.name, m.name): m.show_viewport for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers}
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = False
    world0 = np.array(arm.matrix_world)
    parent_pose = np.zeros((len(frames), len(chains), 4, 4))
    chain_pose = np.zeros((len(frames), len(chains), len(chains[0]), 4, 4))
    chain_nodes = np.zeros((len(frames),) + rest_nodes.shape)
    ends = {}
    follow_pose = np.zeros((len(frames), len(settings["follow"]), 4, 4))
    for k, f in enumerate(frames):
        sc.frame_set(f)
        if not np.allclose(np.array(arm.matrix_world), world0):
            raise SystemExit("アーマチュアの物体が動いている .blend はまだ扱えません（骨の動きにしてください）")
        for i, (c, p) in enumerate(zip(chains, parents)):
            parent_pose[k, i] = np.array(pb[p].matrix)
            for j, n in enumerate(c):
                chain_pose[k, i, j] = np.array(pb[n].matrix)
                chain_nodes[k, i, j] = np.array(pb[n].head)
            chain_nodes[k, i, -1] = np.array(pb[c[-1]].tail)
        for c in settings["colliders"]:
            for key in ("from", "to"):
                ends.setdefault(c[key], np.zeros((len(frames), 3)))[k] = np.array(pb[c[key]].head)
        for j, anchor in enumerate(settings["follow"]):
            follow_pose[k, j] = np.array(pb[anchor["bone"]].matrix)
    chain_rest = np.array([[rest(n) for n in c] for c in chains])
    if settings["target"] == "rest" and settings["follow"]:   # 骨と高さの並びで混ぜて付いて行く（リポジトリ外の試作と同じ）
        moves = np.stack([follow_pose[:, j] @ np.linalg.inv(rest(anchor["bone"])) for j, anchor in enumerate(settings["follow"])], 1)
        flat = rest_nodes.reshape(-1, 3)
        x, turn = blend_follow(moves, height_weights(flat[:, 2], [anchor["height_m"] for anchor in settings["follow"]]), flat)
        targets = x.reshape((len(frames),) + rest_nodes.shape)
        turn = turn.reshape((len(frames),) + rest_nodes.shape[:2] + (3, 3))
        target_rot = rotation_part(0.5 * (turn[:, :, :-1] + turn[:, :, 1:])) @ rotation_part(chain_rest)   # 骨の両端の回りの平均
    elif settings["target"] == "rest":                   # 鎖が親に固く付いて動いたとき（鎖の骨の回転と根元の位置は 0）
        follow = np.array([parent_pose[:, i] @ np.linalg.inv(rest(p)) for i, p in enumerate(parents)]).swapaxes(0, 1)
        homogeneous = np.concatenate([rest_nodes, np.ones(rest_nodes.shape[:2] + (1,))], -1)
        targets = np.einsum("fcij,cnj->fcni", follow, homogeneous)[..., :3]
        target_rot = rotation_part(np.einsum("fcij,cnjk->fcnik", follow, chain_rest))
    elif settings["target"] == "animation":
        targets, target_rot = chain_nodes, rotation_part(chain_pose)
    else:
        raise SystemExit(f"target は rest か animation: {settings['target']!r}")

    def to_world(points):
        return points @ world0[:3, :3].T + world0[:3, 3]

    colliders = [{"pa": to_world(ends[c["from"]]), "pb": to_world(ends[c["to"]]), "ra": c["radius_mm"] / 1000,
                  "rb": c["radius_mm"] / 1000, "qa": to_world(np.array(bones[c["from"]].head_local)),
                  "qb": to_world(np.array(bones[c["to"]].head_local))} for c in settings["colliders"]]
    sim, hits = simulate(to_world(rest_nodes), to_world(targets), colliders, [tuple(x) for x in settings["links"]], fps,
                         int(settings["substeps"]), settings["f_root_hz"], settings["f_tip_hz"], settings["damping"],
                         settings["bend"], settings["push"], settings["settle_s"])

    # 骨にする
    to_armature = np.linalg.inv(world0)
    sim_armature = sim @ to_armature[:3, :3].T + to_armature[:3, 3]
    basis = np.array([chain_basis(rotation_part(parent_pose[:, i]), relation[i], target_rot[:, i],
                                  np.diff(sim_armature[:, i], axis=1)) for i in range(len(chains))])   # 鎖, コマ, 骨, 3, 3
    root_keys = [root_location(parent_pose[:, i] @ np.linalg.inv(rest(p)), rest(c[0]), sim_armature[:, i, 0])
                 for i, (c, p) in enumerate(zip(chains, parents))]
    for i, c in enumerate(chains):
        for j, n in enumerate(c):
            prev = None
            for k, f in enumerate(frames):
                q = Matrix(basis[i, k, j].tolist()).to_quaternion()
                if prev is not None and q.dot(prev) < 0:
                    q = -q
                prev = q
                pb[n].rotation_quaternion = q
                pb[n].keyframe_insert("rotation_quaternion", frame=f)
                if j == 0:                               # 根元の head を、ばねの根元の節に置く
                    pb[n].location = root_keys[i][k].tolist()
                    pb[n].keyframe_insert("location", frame=f)
    # 確かめ: 焼き付けた骨の向きがばねの節の向きと、根元の head がばねの根元の節と合うこと。節の間の長さは、ばねの計算で
    # 鎖のつなぎと当たりが節を動かすぶん伸び縮みするが、骨は長さを保つので、その先の節の位置の差は別に数える
    worst = {"direction_deg": 0.0, "root_mm": 0.0, "nodes_mm": 0.0}
    for k, f in enumerate(frames):
        sc.frame_set(f)
        vl.update()
        for i, c in enumerate(chains):
            got = to_world(np.array([np.array(pb[n].head) for n in c] + [np.array(pb[c[-1]].tail)]))
            have, want = np.diff(got, axis=0), np.diff(sim[k, i], axis=0)
            angle = np.arctan2(np.linalg.norm(np.cross(have, want), axis=1), np.sum(have * want, axis=1))
            worst["direction_deg"] = max(worst["direction_deg"], float(np.degrees(angle.max())))
            worst["root_mm"] = max(worst["root_mm"], float(np.linalg.norm(got[0] - sim[k, i, 0])) * 1000)
            worst["nodes_mm"] = max(worst["nodes_mm"], float(np.linalg.norm(got - sim[k, i], axis=1).max()) * 1000)
    stretch = (np.linalg.norm(np.diff(sim, axis=2), axis=3) - np.linalg.norm(np.diff(to_world(rest_nodes), axis=1), axis=2)[None]) * 1000
    for o in bpy.data.objects:
        if o.type == "MESH":
            for m in o.modifiers:
                m.show_viewport = original_view[(o.name, m.name)]
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-spring-bones/0.1", "blender": bpy.app.version_string,
              "frames": [frames[0], frames[-1]], "fps": fps,
              "chains": [{"root": c[0], "bones": len(c), "parent": p} for c, p in zip(chains, parents)],
              "settings": {k: settings[k] for k in ("target", "follow", "f_root_hz", "f_tip_hz", "damping", "bend", "substeps",
                                                   "push", "settle_s", "links", "colliders")},
              "collision_pushes": hits, "motion": motion_stats(sim, to_world(targets)),
              "spring_stretch_mm": {"max": round(float(stretch.max()), 2), "min": round(float(stretch.min()), 2)},
              "bake_check": {"direction_deg": worst["direction_deg"], "root_mm": worst["root_mm"]},   # 丸めない（許容と比べる）
              "bones_vs_springs_mm": round(worst["nodes_mm"], 2)}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"chains": len(chains), "frames": len(frames), "collision_pushes": hits,
                      "bake_check": {k: round(v, 5) for k, v in report["bake_check"].items()}}))


if __name__ == "__main__":
    main()
