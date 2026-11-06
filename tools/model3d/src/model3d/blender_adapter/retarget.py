"""Blender の中で動かす: 整えた 70 点（motion.prepare_keypoints の points）を、Mixamo 式の骨を持つキャラへ移す。

    blender --background --factory-startup --python retarget.py -- --points points.npz --character rig.blend \
        --out-blend out.blend --pose pose.npz --report report.json [--pitch0 35] [--foot-yaw heel] [--resolve] [--smooth-arms]

骨の向きの決め方（リポジトリ外の 32 秒の試作で使った手順。骨の名前は --bone-prefix + Mixamo の名前）:
- 腰: 股関節の左右と、腰の中心 → 肩の中心の上向きで向きを決める。位置は、踊り手の脚の長さとキャラの脚の長さの比で縮めた腰の中心
- 背骨 3 本: 上向きに沿わせ、左右は腰と肩の左右を 2:1・1:2・0:1 で混ぜる
- 首と頭: 両耳と鼻から顔の上向きと左右を取る。頭の上下の基準角 pitch0（既定はクリップの中央値）を引く
- 腕: 上腕を肘へ、前腕を手首へ向ける。ひねりは、肘の曲がり具合で「前腕の向き」と「手の左右（人差し指→小指の付け根）」を混ぜる
- 指: 各指の 3 本の骨を、点の並び（付け根 → 第 2 → 第 1 → 先）に沿わせる
- 脚: キャラの太もも・脛の長さで、縮めた足首の位置へ IK で届かせる（膝の向きは点の膝）。接地のコマ（stance）は足を床に平らに置く
- 足の向き: toes は親指 → 小指の線、heel はかかと → つま先の向きで決める
接地の重み（stance）があれば、接地中の足首の高さの中央値をキャラの足首の高さに合わせて床を決める。
--resolve は、手・前腕が胴の面の内側に入ったコマで、肘を外へ振るか手首を外へ押して直す（BVH の光線で内外を見る）。
--smooth-arms は、直したコマの前後で、肩から見た肘と手首の通り道をならす（直しはコマごとに入るので腕が跳ねる）。
--skip-vertices の npz の真偽の配列は、すべて合わせて「胴の面として数えない頂点」にする（髪の前に手があるだけで内側と判定しない）。
頂点の数が合うメッシュにだけ当てる。

出力: --out-blend（骨に動きを打った .blend）、--pose（コマごとの骨の行列。names・rest・tail・pose・world の npz）、--report（JSON）。
**検証**: 本物の Blender 4.5.15 で、試作と同じ入力から同じ骨の行列になることを確かめた（docs/model3d.md）。CI には Blender が無い。
"""
import argparse
import json
import math
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="retarget.py")
    ap.add_argument("--points", required=True, help="motion.prepare_keypoints の points（npz の points・stance）")
    ap.add_argument("--character", required=True, help="Mixamo 式の骨と重みの入った .blend")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--pose", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--frames", default="", help="a:b[:step] か 0,5,9（既定は全部）")
    ap.add_argument("--bone-prefix", default="mixamorig:")
    ap.add_argument("--pitch0", type=float, default=None, help="頭の上下の基準角（度）。既定はクリップの中央値")
    ap.add_argument("--head-dpitch", default="", help="コマごとの頭の上下の追加（npz の dpitch_deg、度）")
    ap.add_argument("--foot-yaw", choices=("toes", "heel"), default="toes")
    ap.add_argument("--skip-vertices", default="", help="胴の面として数えない頂点の真偽の配列の npz")
    ap.add_argument("--resolve", action="store_true")
    ap.add_argument("--smooth-arms", action="store_true")
    ap.add_argument("--check", action="store_true", help="コマごとの刺さり・頭と胸の角度・腕のひねりの跳びを報告に残す")
    ap.add_argument("--fps", type=int, default=30)
    return ap.parse_args(argv)


def frame_list(spec, total):
    if not spec:
        return list(range(total))
    if "," in spec:
        return [int(x) for x in spec.split(",")]
    parts = [int(x) for x in spec.split(":")]
    return list(range(parts[0], min(parts[1], total), parts[2] if len(parts) > 2 else 1))


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Quaternion, Vector
    from mathutils.bvhtree import BVHTree

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with np.load(a.points, allow_pickle=False) as data:
        B = data["points"].astype(float)
        STANCE = data["stance"].astype(float) if "stance" in data.files else None
        focal = float(data["focal"]) if "focal" in data.files else None
    T = len(B)
    fr = frame_list(a.frames, T)

    def V(t, i):
        return Vector(B[t, i])

    def nz(v):
        return v.normalized() if v.length > 1e-9 else Vector((0, 0, 0))

    def perp(v, axis):
        ax = axis.normalized()
        return v - ax * v.dot(ax)

    def frame2(p, q):
        x = p.normalized()
        y = perp(q, x).normalized()
        z = x.cross(y)
        return Matrix((x, y, z)).transposed()

    def signed_about(v, ref, axis):
        ax = axis.normalized()
        v1, r1 = perp(v, ax).normalized(), perp(ref, ax).normalized()
        return math.degrees(math.atan2(ax.dot(r1.cross(v1)), r1.dot(v1)))

    def head_up_raw(t):
        em = (V(t, 3) + V(t, 4)) / 2
        lat = V(t, 3) - V(t, 4)
        fwd = V(t, 0) - em
        return nz(fwd.cross(lat)), lat

    pitches = []
    for t in range(T):
        fu, lat = head_up_raw(t)
        up = nz((V(t, 5) + V(t, 6)) / 2 - (V(t, 9) + V(t, 10)) / 2)
        pitches.append(signed_about(fu, up, lat))
    PITCH0 = float(np.median(pitches)) if a.pitch0 is None else float(a.pitch0)
    HDP = np.load(a.head_dpitch, allow_pickle=False)["dpitch_deg"] if a.head_dpitch else None

    def head_up(t):
        fu, lat = head_up_raw(t)
        return Quaternion(lat.normalized(), math.radians(-PITCH0)) @ fu, lat

    # ---------- キャラ ----------
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.character))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if not armatures:
        raise SystemExit("キャラの .blend にアーマチュアがありません")
    arm = armatures[0]
    meshes = [o for o in bpy.data.objects if o.type == "MESH"]
    pb = arm.pose.bones
    for b in pb:
        b.rotation_mode = "QUATERNION"
    AW = arm.matrix_world
    AWi = AW.inverted()
    AR = AW.to_3x3().normalized()
    ARi = AR.inverted()
    vl, sc = bpy.context.view_layer, bpy.context.scene

    def N(n):
        return a.bone_prefix + n

    missing = [N(n) for n in ("Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftArm", "LeftForeArm", "LeftHand",
                              "LeftUpLeg", "LeftLeg", "LeftFoot", "RightArm", "RightForeArm", "RightHand", "RightUpLeg",
                              "RightLeg", "RightFoot") if N(n) not in pb]
    if missing:
        raise SystemExit(f"骨がありません（--bone-prefix を確かめてください）: {missing[:6]}")

    def reset():
        for b in pb:
            b.rotation_quaternion = Quaternion()
            b.location = Vector()

    reset()
    vl.update()
    REST = {b.name: (AR @ b.matrix.to_3x3()).normalized() for b in pb}

    def bw(n, end="head"):
        b = pb[N(n)]
        return AW @ (b.head if end == "head" else b.tail)

    def cur_rot(name):
        return (AR @ pb[N(name)].matrix.to_3x3()).normalized()

    rest_hips = bw("Hips")
    char_torso = ((bw("LeftArm") + bw("RightArm")) / 2 - (bw("LeftUpLeg") + bw("RightUpLeg")) / 2).length
    src_torso = float(np.median(np.linalg.norm((B[:, 5] + B[:, 6]) / 2 - (B[:, 9] + B[:, 10]) / 2, axis=1)))
    S = char_torso / src_torso
    HM_REF = Vector(np.median((B[:, 9] + B[:, 10]) / 2, axis=0))
    # 脚はキャラの長さに合わせて足首の目標へ IK で届かせる（踊り手と脚の比が違うので、向きを写すだけだと足が浮いて滑る）
    THIGH = {s_: (bw(s_ + "Leg") - bw(s_ + "UpLeg")).length for s_ in ("Left", "Right")}
    SHIN = {s_: (bw(s_ + "Foot") - bw(s_ + "Leg")).length for s_ in ("Left", "Right")}
    src_leg = float(np.median(np.linalg.norm(B[:, 11] - B[:, 9], axis=1) + np.linalg.norm(B[:, 13] - B[:, 11], axis=1)))
    SL = (THIGH["Left"] + SHIN["Left"]) / src_leg
    CHAR_ANKLE = float(bw("LeftFoot").z)            # 足首の床からの高さ
    # 接地中の足首の高さの差を引く（踊り手の靴のかかとなど）。stance があっても接地のコマが 1 つも無ければ、
    # 中央値が NaN になって全部の位置を壊すので、接地なしと同じく足の骨の高さで床を決める（下の dz）
    contact_z = (np.r_[B[STANCE[:, 0] > 0.5, 13, 2], B[STANCE[:, 1] > 0.5, 14, 2]] if STANCE is not None else np.zeros(0))
    HAS_CONTACT = len(contact_z) > 0
    ANKLE_Z0 = SL * float(np.median(contact_z)) - CHAR_ANKLE if HAS_CONTACT else 0.0

    def to_char(v):
        return Vector((rest_hips.x + SL * (v.x - HM_REF.x), rest_hips.y + SL * (v.y - HM_REF.y), SL * v.z - ANKLE_Z0))

    # ---------- 刺さりの道具（安静時に準備） ----------
    PARTS_B = ["Hips", "Spine", "Spine1", "Spine2", "Neck", "Head", "LeftUpLeg", "RightUpLeg"]

    def dom_groups(ob):
        names = {g.index: g.name.replace(a.bone_prefix, "") for g in ob.vertex_groups}
        out = []
        for v in ob.data.vertices:
            gs = [(g.weight, names[g.group]) for g in v.groups if g.weight > 0]
            out.append(max(gs)[1] if gs else "")
        return out

    OBS = meshes
    COLLISION = a.resolve or a.check                # 刺さりの準備は、直すか検査するときだけ（重いので）
    DOM, skipped, ARMV, SURF_FACES, REST_CO, YR = {}, {}, {}, {}, {}, {}
    if COLLISION:
        DOM = {ob.name: dom_groups(ob) for ob in OBS}
    if COLLISION and a.skip_vertices:
        with np.load(a.skip_vertices, allow_pickle=False) as data:
            masks = [data[k].astype(bool) for k in data.files if data[k].dtype == bool]
        if masks:
            skip = np.logical_or.reduce(masks)
            for ob in OBS:
                if len(skip) == len(DOM[ob.name]):
                    DOM[ob.name] = ["" if skip[i] else g for i, g in enumerate(DOM[ob.name])]
                    skipped[ob.name] = int(skip.sum())
    for ob in (OBS[:2] if COLLISION else []):
        lst = [(i, "L" if g.startswith("Left") else "R", "forearm" if "ForeArm" in g else "hand")
               for i, g in enumerate(DOM[ob.name]) if ("ForeArm" in g or "Hand" in g)]
        ARMV[ob.name] = dict(idx=np.array([x[0] for x in lst], dtype=int), side=np.array([x[1] for x in lst]),
                             kind=np.array([x[2] for x in lst]))
    if COLLISION:
        SURF_FACES = {ob.name: [list(p.vertices) for p in ob.data.polygons if all(DOM[ob.name][i] in PARTS_B for i in p.vertices)]
                      for ob in OBS}

    def hand_end(side):
        """手の先: 中指の 3 本目の骨の先。指の骨が無いリグでは手の骨の先"""
        return bw(side + "HandMiddle3", "tail") if N(side + "HandMiddle3") in pb else bw(side + "Hand", "tail")

    def eval_co(ob):
        dg = bpy.context.evaluated_depsgraph_get()
        ev = ob.evaluated_get(dg)
        me = ev.to_mesh()
        co = np.array([tuple(ob.matrix_world @ v.co) for v in me.vertices])
        ev.to_mesh_clear()
        return co

    def bone_frame(n):
        return np.array(cur_rot(n)), np.array(bw(n))

    if COLLISION:
        REST_CO = {ob.name: eval_co(ob) for ob in OBS}

    def _seg(p, s, e):
        s, e = np.array(s), np.array(e)
        ab = e - s
        u = np.clip(((p - s) @ ab) / (ab @ ab), 0, 1)
        return np.linalg.norm(p - (s + u[:, None] * ab), axis=1)

    for ob in (OBS[:2] if COLLISION else []):   # 袖の布は前腕の骨に付いていても腕ではないので、検査は腕そのものの頂点だけにする
        A_ = ARMV[ob.name]
        if not len(A_["idx"]):
            continue
        P_ = REST_CO[ob.name][A_["idx"]]
        keep_ = np.zeros(len(P_), bool)
        for s_, side in (("L", "Left"), ("R", "Right")):
            near_ = (_seg(P_, bw(side + "ForeArm"), bw(side + "Hand")) < 0.06) | \
                    (_seg(P_, bw(side + "Hand"), hand_end(side)) < 0.09)
            keep_ |= (A_["side"] == s_) & near_
        ARMV[ob.name] = {k_: v_[keep_] for k_, v_ in A_.items()}
    for n in (PARTS_B if COLLISION else []):
        R, o = bone_frame(n)
        pts = [REST_CO[ob.name][[i for i, g in enumerate(DOM[ob.name]) if g == n]] for ob in OBS]
        pts = [p_ for p_ in pts if len(p_)]
        if not pts:
            continue
        P = (np.vstack(pts) - o) @ R
        YR[n] = (float(P[:, 1].min()), float(P[:, 1].max()))

    def pen_scan():
        """各側・各種類（hand/forearm）について、内側に入った頂点の (深さ, 外向き) の一覧"""
        cos = {ob.name: eval_co(ob) for ob in OBS}
        allco, polys = [], []
        for ob in OBS:
            off = len(allco)
            allco += [Vector(c) for c in cos[ob.name]]
            polys += [[i + off for i in f] for f in SURF_FACES[ob.name]]
        bvh = BVHTree.FromPolygons(allco, polys)
        frames = {n: bone_frame(n) for n in PARTS_B if n in YR}
        out = {"L": {"hand": [], "forearm": []}, "R": {"hand": [], "forearm": []}}
        for obn, A in ARMV.items():
            pts = cos[obn][A["idx"]]
            best_rad = np.full(len(pts), np.inf)
            axis_pt = np.zeros_like(pts)
            for n, (R, o) in frames.items():
                L = (pts - o) @ R
                rad = np.hypot(L[:, 0], L[:, 2])
                ok = (L[:, 1] >= YR[n][0]) & (L[:, 1] <= YR[n][1]) & (rad < 0.30) & (rad < best_rad)
                best_rad[ok] = rad[ok]
                axis_pt[ok] = o + (L[ok, 1:2] * R[:, 1][None, :])
            for j in np.where(np.isfinite(best_rad) & (best_rad > 1e-4))[0]:
                p_ = Vector(pts[j])
                d = nz(p_ - Vector(axis_pt[j]))
                hit = bvh.ray_cast(p_ + d * 1e-4, d, 0.30)
                if hit[0] is not None:
                    out[A["side"][j]][A["kind"][j]].append((hit[3], d))
        return out

    for o in meshes:
        for m in o.modifiers:
            m.show_viewport = False

    def set_rot(name, Rw, pos=None):
        b = pb[N(name)]
        cur = b.matrix.copy()
        tr = cur.translation if pos is None else AWi @ pos
        b.matrix = Matrix.Translation(tr) @ (ARi @ Rw).to_4x4()
        vl.update()

    def aim(name, d, pole=None, pole_rest=None):
        if d.length < 1e-9:
            return
        y = d.normalized()
        if pole is None or perp(pole, y).length < 1e-6:
            Rc = cur_rot(name)
            q = (Rc @ Vector((0, 1, 0))).rotation_difference(y)
            set_rot(name, q.to_matrix() @ Rc)
            return
        pl = REST[N(name)].transposed() @ pole_rest
        yl = Vector((0, 1, 0))
        zl = perp(pl, yl).normalized()
        xl = yl.cross(zl)
        L = Matrix((xl, yl, zl)).transposed()
        zt = perp(pole, y).normalized()
        xt = y.cross(zt)
        set_rot(name, Matrix((xt, y, zt)).transposed() @ L.transposed())

    def full(name, ta, tb, ra, rb, pos=None):
        set_rot(name, frame2(ta, tb) @ frame2(ra, rb).transposed() @ REST[N(name)], pos)

    EX, EY, EZ = Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))
    FWD = Vector((0, -1, 0))
    SIN25 = math.sin(math.radians(25))

    def bendw(d1, d2):
        if d1.length < 1e-9 or d2.length < 1e-9:
            return 0.0
        return min(1.0, math.sin(d1.angle(d2)) / SIN25)

    ARMS = {"Left": dict(S=5, E=7, W=62, base={"Thumb": 42, "Index": 46, "Middle": 50, "Ring": 54, "Pinky": 58}),
            "Right": dict(S=6, E=8, W=41, base={"Thumb": 21, "Index": 25, "Middle": 29, "Ring": 33, "Pinky": 37})}
    LEGS = {"Left": dict(H=9, K=11, A=13, BT=15, ST=16, HL=17), "Right": dict(H=10, K=12, A=14, BT=18, ST=19, HL=20)}

    def pose_frame(t):
        reset()
        vl.update()
        lh, rh, ls, rs = V(t, 9), V(t, 10), V(t, 5), V(t, 6)
        hm, sm = (lh + rh) / 2, (ls + rs) / 2
        up = nz(sm - hm)
        lat_h, lat_s = lh - rh, ls - rs
        full("Hips", lat_h, up, EX, EZ, pos=to_char(hm))
        aim("Spine", up, nz(lat_h * (2 / 3) + lat_s * (1 / 3)), EX)
        aim("Spine1", up, nz(lat_h * (1 / 3) + lat_s * (2 / 3)), EX)
        full("Spine2", lat_s, up, EX, EZ)
        hu, lat_e = head_up(t)
        aim("Neck", nz(up + hu), nz(nz(lat_s) + nz(lat_e)), EX)
        full("Head", lat_e, hu, EX, EZ)
        if HDP is not None:
            hb_ = pb[N("Head")]
            hb_.rotation_quaternion = hb_.rotation_quaternion @ Quaternion((1, 0, 0), math.radians(float(HDP[t])))
            vl.update()
        for side, k in ARMS.items():
            sh, el, wr = V(t, k["S"]), V(t, k["E"]), V(t, k["W"])
            bs = k["base"]
            idx3, mid3, pnk3 = V(t, bs["Index"] + 3), V(t, bs["Middle"] + 3), V(t, bs["Pinky"] + 3)
            dU, dF = el - sh, wr - el
            handlat = idx3 - pnk3
            w = bendw(dU, dF)
            aim(side + "Arm", dU, nz(nz(perp(dF, dU)) * w + nz(perp(handlat, dU)) * (1 - w)), FWD)
            Rf = cur_rot(side + "ForeArm")
            pc = perp(Rf @ (REST[N(side + "ForeArm")].transposed() @ FWD), dF)
            ph = perp(handlat, dF)
            if pc.length < 1e-6:
                pf = ph
            elif ph.length < 1e-6:
                pf = pc
            else:
                pc, ph = pc.normalized(), ph.normalized()
                pf = pc + ph if pc.dot(ph) > -0.9 else pc
            aim(side + "ForeArm", dF, pf, FWD)
            aim(side + "Hand", mid3 - wr, handlat, FWD)
            for fn, b0 in bs.items():
                if N(side + "Hand" + fn + "1") not in pb:
                    continue
                tip, f1, f2, f3 = V(t, b0), V(t, b0 + 1), V(t, b0 + 2), V(t, b0 + 3)
                aim(side + "Hand" + fn + "1", f2 - f3)
                aim(side + "Hand" + fn + "2", f1 - f2)
                aim(side + "Hand" + fn + "3", tip - f1)
        for side, k in LEGS.items():
            hp_, kn, an = V(t, k["H"]), V(t, k["K"]), V(t, k["A"])
            bt, st, hl = V(t, k["BT"]), V(t, k["ST"]), V(t, k["HL"])
            ffwd = (bt + st) / 2 - hl
            H_ = bw(side + "UpLeg")
            A_ = to_char(an)
            l1, l2 = THIGH[side], SHIN[side]
            dv = A_ - H_
            L_ = min(max(dv.length, abs(l1 - l2) + 1e-4), l1 + l2 - 1e-4)
            dn = dv.normalized()
            pole = perp(kn - (hp_ + an) / 2, dn)
            pole = pole.normalized() if pole.length > 1e-4 else nz(perp(FWD, dn))
            x_ = (l1 * l1 - l2 * l2 + L_ * L_) / (2 * L_)
            h_ = math.sqrt(max(l1 * l1 - x_ * x_, 0.0))
            Kn_ = H_ + dn * x_ + pole * h_
            dT, dS = Kn_ - H_, H_ + dn * L_ - Kn_
            w = bendw(dT, dS)
            if STANCE is not None:                       # 接地中は足を床に平らに置く
                wf = float(STANCE[t, 0 if side == "Left" else 1])
                ffwd = Vector((ffwd.x, ffwd.y, ffwd.z * (1 - wf)))
                bt = Vector((bt.x, bt.y, bt.z * (1 - wf) + hl.z * wf))
                st = Vector((st.x, st.y, st.z * (1 - wf) + hl.z * wf))
            aim(side + "UpLeg", dT, nz(nz(perp(-dS, dT)) * w + nz(perp(ffwd, dT)) * (1 - w)), FWD)
            aim(side + "Leg", dS, perp(ffwd, dS), FWD)
            lat = (st - bt) if side == "Left" else (bt - st)    # 足の左右の軸（親指→小指。左足と右足で同じ向きになる）
            if a.foot_yaw == "heel":
                lat_h2 = EZ.cross(ffwd)                          # かかと→つま先と上向きから左右の軸（足は横に傾けない）
                if lat_h2.length > 1e-6:
                    lat = lat_h2
            full(side + "Foot", lat, ffwd, EX, FWD)

    sc.render.fps = a.fps
    sc.frame_start = 1
    sc.frame_end = len(fr)
    for k, t in enumerate(fr, start=1):
        pose_frame(t)
        for b in pb:
            b.keyframe_insert("rotation_quaternion", frame=k)
        pb[N("Hips")].keyframe_insert("location", frame=k)

    FLOOR_BONES = (("LeftToeBase", 0.010), ("RightToeBase", 0.010), ("LeftToe_End", 0.013), ("RightToe_End", 0.013),
                   ("LeftFoot", 0.085), ("RightFoot", 0.085))   # 骨の高さから床までの目安（人の足）。無い骨は飛ばす

    def foot_low():
        return min(bw(n).z - offset for n, offset in FLOOR_BONES if N(n) in pb)

    if HAS_CONTACT:
        dz = 0.0                                    # 接地があるときは足首の高さで床に合わせ済み
    else:
        lows = []
        for k in range(1, len(fr) + 1):
            sc.frame_set(k)
            lows.append(foot_low())
        dz = -float(np.percentile(lows, 5))
    if dz:
        for k in range(1, len(fr) + 1):
            sc.frame_set(k)
            hp = pb[N("Hips")]
            m = hp.matrix.copy()
            m.translation = AWi @ ((AW @ m.translation) + Vector((0, 0, dz)))
            hp.matrix = m
            vl.update()
            hp.keyframe_insert("location", frame=k)
    for o in meshes:
        for m in o.modifiers:
            m.show_viewport = True
    info = dict(frames=len(fr), first_frame=fr[0] if fr else None, focal=focal, pitch0_deg=PITCH0, scale=S, leg_scale=SL,
                floor_shift=dz, floor_from="stance" if HAS_CONTACT else "foot_bones", skipped_vertices=skipped)

    def counts(P):
        return {s_: {k_: int(sum(1 for d_, _ in P[s_][k_] if d_ > 0.02)) for k_ in ("hand", "forearm")} for s_ in ("L", "R")}

    # ---------- 刺さりの補正 ----------
    def side_pole(name):
        return cur_rot(name) @ (REST[N(name)].transposed() @ FWD)

    def resolve_frame(k):
        log = []
        for s_, side in (("L", "Left"), ("R", "Right")):
            for it in range(8):
                sc.frame_set(k)
                vl.update()
                P = pen_scan()
                hin = [x for x in P[s_]["hand"] if x[0] > 0.02]
                fin = [x for x in P[s_]["forearm"] if x[0] > 0.02]
                if len(hin) < 20 and len(fin) < 10:
                    break
                Sh, E, W = bw(side + "Arm"), bw(side + "ForeArm"), bw(side + "Hand")
                la, lb = (E - Sh).length, (W - E).length
                Rh = cur_rot(side + "Hand")
                pa, pfo = side_pole(side + "Arm"), side_pole(side + "ForeArm")
                if len(fin) >= 10 and len(fin) * 4 >= len(hin):
                    # 前腕が内側：肩〜手首の軸のまわりに肘を外へ振る（手首の位置はそのまま）
                    d = nz(sum((x[1] * x[0] for x in fin), Vector()))
                    ax = nz(W - Sh)
                    c = Sh + ax * (E - Sh).dot(ax)
                    r = E - c
                    ang = math.radians(12) * (1 if ax.dot(r.cross(d)) > 0 else -1)
                    E2 = c + Quaternion(ax, ang) @ r
                    W2 = W
                    mode = "swing_elbow"
                else:
                    # 手が内側：手首を外へ押し出し、肩〜肘を解き直す（肘は今の曲げの向きを保つ）
                    d = nz(sum((x[1] * x[0] for x in hin), Vector()))
                    push = float(np.percentile([x[0] for x in hin], 75)) + 0.01
                    W2 = W + d * push
                    dv = W2 - Sh
                    L = min(max(dv.length, abs(la - lb) + 1e-4), la + lb - 1e-4)
                    dn = dv.normalized()
                    pole = perp(E - Sh, dn)
                    pole = pole.normalized() if pole.length > 1e-6 else nz(perp(FWD, dn))
                    x_ = (la * la - lb * lb + L * L) / (2 * L)
                    h = math.sqrt(max(la * la - x_ * x_, 0.0))
                    E2 = Sh + dn * x_ + pole * h
                    W2 = Sh + dn * L
                    mode = "push_wrist"
                aim(side + "Arm", E2 - Sh, pa, FWD)
                aim(side + "ForeArm", W2 - bw(side + "ForeArm"), pfo, FWD)
                set_rot(side + "Hand", Rh)
                for nm in (side + "Arm", side + "ForeArm", side + "Hand"):
                    pb[N(nm)].keyframe_insert("rotation_quaternion", frame=k)
                log.append(dict(side=s_, it=it, mode=mode, hand_in=len(hin), fore_in=len(fin)))
        return log

    if a.resolve:
        before, rlog = {}, {}
        for k, t in enumerate(fr, start=1):
            sc.frame_set(k)
            vl.update()
            before[t] = counts(pen_scan())
            if max(before[t]["L"]["hand"], before[t]["R"]["hand"]) >= 20 or max(before[t]["L"]["forearm"], before[t]["R"]["forearm"]) >= 10:
                rlog[t] = resolve_frame(k)
            if k % 40 == 0:
                print("resolve", k, "/", len(fr), flush=True)
        info["resolve"] = dict(frames_fixed=len(rlog), log={str(t_): v for t_, v in rlog.items()})
        info["before"] = {str(t_): v for t_, v in before.items()}
        if a.smooth_arms and rlog:
            # 補正はコマごとに別々に入るので腕が跳ねる。補正した区間の前後だけ、肩から見た肘と手首の通り道をならして解き直す
            n_ = len(fr)
            near = np.zeros(n_)
            for t_ in rlog:
                near[fr.index(t_)] = 1.0
            kern = np.array([1, 2, 3, 2, 1]) / 3.0
            wgt = np.clip(np.convolve(near, kern, mode="same"), 0, 1)

            def blur(x):
                for _ in range(2):
                    pad = np.r_[x[:2][::-1], x, x[-2:][::-1]]
                    x = sum(w_ * pad[i_:i_ + len(x)] for i_, w_ in enumerate((1 / 16, 4 / 16, 6 / 16, 4 / 16, 1 / 16)))
                return x

            for s_, side in (("L", "Left"), ("R", "Right")):
                Sp, Ep, Wp = [], [], []
                for k in range(1, n_ + 1):
                    sc.frame_set(k)
                    vl.update()
                    Sp.append(list(bw(side + "Arm")))
                    Ep.append(list(bw(side + "ForeArm")))
                    Wp.append(list(bw(side + "Hand")))
                Sp, Ep, Wp = np.array(Sp), np.array(Ep), np.array(Wp)
                eN = (Ep - Sp) * (1 - wgt)[:, None] + blur(Ep - Sp) * wgt[:, None]
                wN = (Wp - Sp) * (1 - wgt)[:, None] + blur(Wp - Sp) * wgt[:, None]
                la = float(np.median(np.linalg.norm(Ep - Sp, axis=1)))
                lb = float(np.median(np.linalg.norm(Wp - Ep, axis=1)))
                for k in range(1, n_ + 1):
                    if wgt[k - 1] < 0.01:
                        continue
                    sc.frame_set(k)
                    vl.update()
                    Sh = bw(side + "Arm")
                    E_t, W_t = Sh + Vector(eN[k - 1]), Sh + Vector(wN[k - 1])
                    dv = W_t - Sh
                    L = min(max(dv.length, abs(la - lb) + 1e-4), la + lb - 1e-4)
                    dn = dv.normalized()
                    pole = perp(E_t - Sh, dn)
                    pole = pole.normalized() if pole.length > 1e-6 else nz(perp(FWD, dn))
                    x_ = (la * la - lb * lb + L * L) / (2 * L)
                    h = math.sqrt(max(la * la - x_ * x_, 0.0))
                    E2, W2 = Sh + dn * x_ + pole * h, Sh + dn * L
                    Rh = cur_rot(side + "Hand")
                    pa, pfo = side_pole(side + "Arm"), side_pole(side + "ForeArm")
                    aim(side + "Arm", E2 - Sh, pa, FWD)
                    aim(side + "ForeArm", W2 - bw(side + "ForeArm"), pfo, FWD)
                    set_rot(side + "Hand", Rh)
                    for nm in (side + "Arm", side + "ForeArm", side + "Hand"):
                        pb[N(nm)].keyframe_insert("rotation_quaternion", frame=k)
            info["smoothed_arm_frames"] = int((wgt >= 0.01).sum())

    # ---------- 検査 ----------
    summary, rows = None, []
    if a.check:
        prev = None
        for k, t in enumerate(fr, start=1):
            sc.frame_set(k)
            vl.update()
            P = pen_scan()
            c_ = counts(P)
            dmax = {s_: max([x[0] for kk in ("hand", "forearm") for x in P[s_][kk]] + [0.0]) for s_ in ("L", "R")}
            ch, hd = cur_rot("Spine2") @ EY, cur_rot("Head") @ EY
            hu, _ = head_up(t)
            up = nz((V(t, 5) + V(t, 6)) / 2 - (V(t, 9) + V(t, 10)) / 2)
            cur = {s_: (cur_rot(sd + "Arm") @ EY, side_pole(sd + "Arm")) for s_, sd in (("L", "Left"), ("R", "Right"))}
            tj = {}
            for s_ in ("L", "R"):
                if prev is None:
                    tj[s_] = 0.0
                    continue
                d0, p0 = prev[s_]
                d1, p1 = cur[s_]
                p0r = d0.rotation_difference(d1) @ p0
                tj[s_] = abs(signed_about(p1, p0r, d1))
            prev = cur
            rows.append(dict(frame=t, n2cm=c_, depth_max_cm={s_: round(v * 100, 1) for s_, v in dmax.items()},
                             head_vs_chest=round(math.degrees(ch.angle(hd)), 1), src_head_vs_torso=round(math.degrees(up.angle(hu)), 1),
                             twist_jump={s_: round(v, 1) for s_, v in tj.items()}))
        summary = dict(frames=len(rows),
                       hand_in_ge20=dict(L=sum(r["n2cm"]["L"]["hand"] >= 20 for r in rows), R=sum(r["n2cm"]["R"]["hand"] >= 20 for r in rows)),
                       forearm_in_ge10=dict(L=sum(r["n2cm"]["L"]["forearm"] >= 10 for r in rows), R=sum(r["n2cm"]["R"]["forearm"] >= 10 for r in rows)),
                       head_vs_chest_max=max(r["head_vs_chest"] for r in rows),
                       twist_jump_max=dict(L=max(r["twist_jump"]["L"] for r in rows), R=max(r["twist_jump"]["R"] for r in rows)),
                       twist_jump_over45=[r["frame"] for r in rows if max(r["twist_jump"].values()) > 45])

    # ---------- 書き出し ----------
    for o in meshes:                                # 骨の行列だけを読むので、変形は止める
        for m in o.modifiers:
            m.show_viewport = False
    names = [b.name for b in pb]
    rest = np.array([np.array(arm.data.bones[n].matrix_local) for n in names], np.float64)
    tail = np.array([np.array(arm.data.bones[n].tail_local) for n in names], np.float64)
    pose = np.zeros((len(fr), len(names), 4, 4), np.float64)
    for k in range(1, len(fr) + 1):
        sc.frame_set(k)
        vl.update()
        for j, b in enumerate(pb):
            pose[k - 1, j] = np.array(b.matrix)
    os.makedirs(os.path.dirname(os.path.abspath(a.pose)), exist_ok=True)
    with open(a.pose, "wb") as stream:
        np.savez(stream, names=np.array(names), rest=rest, tail=tail, pose=pose, world=np.array(arm.matrix_world),
                 frames=np.array(fr))
    for o in meshes:
        for m in o.modifiers:
            m.show_viewport = True
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-retarget/0.1", "blender": bpy.app.version_string, "bone_prefix": a.bone_prefix,
              "foot_yaw": a.foot_yaw, "resolve": a.resolve, "smooth_arms": a.smooth_arms, "info": info,
              "check": {"summary": summary, "rows": rows} if a.check else None}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"frames": len(fr), "bones": len(names), "out_blend": a.out_blend}))


if __name__ == "__main__":
    main()
