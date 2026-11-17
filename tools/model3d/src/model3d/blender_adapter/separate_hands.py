"""Blender の中で動かす: 左右の腕・手・指が互いを通り抜けているコマを測り、上腕を肩のまわりに少し回して離す（motion.separate_hands）。
肘から先の曲げと手の形は変えない。

    blender --background --factory-startup --python separate_hands.py -- --blend dance.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 動画から読んだ関節の点は、手を重ねる振り（両手を体の前で合わせて回す、など）で
左右の手の位置が数 cm ずれ、骨へ載せると、手首や前腕、指が互いを通り抜けた。motion.push_hands は「手と体」を見る工程で、「手と手」は
見ない。同じ考え方（上腕を肩のまわりに少し回す）で、左右の腕どうしを離す。骨の名前は bones の型（{side}・{finger}・{part}）で決める。

測り方:
  片方の腕を、太さのある棒（カプセル）の集まりとみなす: 前腕（肘 → 手首を forearm_bins に区切る。太さは区切りごとに、初期姿勢の
  体のメッシュの前腕がいちばん重い頂点から測る。袖やフリルのある区切りは太い）、手のひら（手首 → 指の付け根。親指を除く 4 本）、
  指（5 本 × 骨 3 つ）。袖やフリルの区切り（太さが soft_from 以上）どうしは、やわらかい物どうしなので、太さの和の soft_factor 倍まで
  近づいてよいとする（soft_radius を渡すと、袖の区切りの太さをその値に決める。フリルは押し出しで直すので、腕は芯だけで離す）。
  左右の棒のすべての組で、いちばん近い 2 点の距離が「太さの和 ＋ gap」より小さい分を重なりとし、コマごとにいちばん大きい重なりを取る。
直し方:
  - 左右の前腕の両方に垂直な向き（前腕が X の形に交差しているとき、重ねる向き）に、左右の腕を分けて離す（上へ動くほうの腕に up_share を持たせる）。
    重なりの続く区間ごとに側を 1 つに決め（その区間で多いほうの側）、向きは時間方向にならす。腕は肩（上腕の付け根）を中心に丸ごと回す。
  - コマごとの量は、前後 ±dilate コマの最大を取ってから時間方向にならす（σ sigma コマ）。切り替えのコマ（cuts）はまたがない。passes 回くり返す。
  - くり返しのあとに残る短い重なり（手が相手の腕の中心を 1〜2 コマで通り抜ける所）は、上の向きでは解けない（通り抜ける前と後で向きの
    符号が逆になる）。detour 回まで、残った区間ごとに、向きの候補（体の左右・上下・前後とその斜め、通り抜ける向きに垂直な向き、区間の前後で
    2 点を結ぶ向き。両方の符号）と量（1 cm から 0.5 cm きざみで detour_max まで）を計算で試し、「その区間で重なりが無くなる・前後のコマで
    新しい重なりを作らない・手が頭や胴へ今より detour_body_loss より近づかない」を満たす、いちばん小さい量を採る（向きは区間の間ずっと同じ。
    量だけを前後へなめらかに立ち上げる）。袖どうしの重なりは動かさない（フリルは押し出しで直す）。
  上腕の骨の回転のキーだけを書き換える（回転は四元数）。
試して採らなかった向きの決め方（台本の記録）: いちばん近い 2 点を結ぶ向き（棒が真ん中を通り抜けるコマで決まらない）、それを前後のコマから
補う向き（向きが数コマで逆になり、手首の加速度が約 3 倍）、体の前後に固定した向き（手が体の前にあると肩を回しても前後へ動かせない）、
区間ごとに決めた側をくり返しの間変えない形（腕がほどける所で手首が 24 cm 動き、加速度が 2 倍）。

報告は model3d-separate-hands/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じキーになることを
確かめた（docs/model3d.md）。CI には Blender が無い（棒の距離・重なり・ならし・向き・腕を回す計算・すれ違いの手当ては、Blender なしでテストする）。
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

# 体の太さ（forearm_radius・soft_from・palm_radius・finger_radius、detour のときは head_radius・torso_radius。m）は既定を置かない
# （体ごとに違う。工程が必ず渡す）。ほかの既定は 3 作目で使った値
DEFAULTS = {"body": None, "armature": None, "aliases": {}, "cuts": [], "passes": 5, "forearm_bins": 5,
            "soft_factor": 0.6, "soft_percentile": 75.0, "soft_radius": 0.0,
            "gap": 0.002, "dilate": 3, "sigma": 3.0, "merge": 10, "relax": 1.0, "up_share": 0.85,
            "threshold": 0.003, "detour": 0, "detour_max": 0.10, "detour_body_loss": 0.005}
FINGERS = ("thumb", "index", "middle", "ring", "little")    # 既定の指の名前（bones の fingers）。手のひらの棒は 2 本目から
PARTS = ("proximal", "intermediate", "distal")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="separate_hands.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


class Names:
    """骨の名前の型から、左右（sides の 1 つ目と 2 つ目）の骨の名前を作る。"""

    def __init__(self, bones):
        self.sides = list(bones["sides"])
        self.fingers = list(bones.get("fingers", FINGERS))
        self.parts = list(bones.get("parts", PARTS))
        self.templates = bones
        self.torso_all = list(bones.get("torso", []))
        self.head = bones.get("head")

    def bone(self, role, side, finger=None, part=None):
        return self.templates[role].format(side=side, finger=finger, part=part)

    def upper(self, side):
        return self.bone("upper", side)

    def forearm(self, side):
        return self.bone("forearm", side)

    def hand(self, side):
        return self.bone("hand", side)

    def finger(self, side, finger, part):
        return self.bone("finger", side, finger, part)


def unit(v):
    return v / max(float(np.linalg.norm(v)), 1e-9)


def action_curves(action):
    """動き（action）の F カーブ（Blender 5 の層つきの動きでも、前の形でも）。"""
    found = []
    for layer in getattr(action, "layers", []):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", []):
                found.extend(bag.fcurves)
    if not found and hasattr(action, "fcurves"):
        found = list(action.fcurves)
    return found


def unkeyed_frames(curves, data_path, frames):
    """data_path の四元数の 4 つの成分のどれかに、キーの無いコマ（小さい順）。"""
    missing = set()
    for index in range(4):
        curve = next((c for c in curves if c.data_path == data_path and c.array_index == index), None)
        keyed = set() if curve is None else {int(round(point.co[0])) for point in curve.keyframe_points}
        missing |= set(frames) - keyed
    return sorted(missing)


def closest(p1, q1, p2, q2):
    """線分 p1-q1 と p2-q2 の、いちばん近い 2 点（全コマをまとめて計算する。台本と同じ式）。"""
    d1, d2, r = q1 - p1, q2 - p2, p1 - p2
    a = np.maximum((d1 * d1).sum(1), 1e-12)
    e = np.maximum((d2 * d2).sum(1), 1e-12)
    f = (d2 * r).sum(1)
    c = (d1 * r).sum(1)
    b = (d1 * d2).sum(1)
    den = a * e - b * b
    s = np.where(den > 1e-12, np.clip((b * f - c * e) / np.maximum(den, 1e-12), 0.0, 1.0), 0.0)
    t = (b * s + f) / e
    s = np.where(t < 0.0, np.clip(-c / a, 0.0, 1.0), np.where(t > 1.0, np.clip((b - c) / a, 0.0, 1.0), s))
    t = np.clip(t, 0.0, 1.0)
    return p1 + d1 * s[:, None], p2 + d2 * t[:, None]


def gaussian(sigma):
    """ならしの重み（±ceil(3σ)）と半径。台本と同じ。"""
    radius = int(math.ceil(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-radius, radius + 1) / sigma) ** 2)
    return kernel / kernel.sum(), radius


def wrist_acceleration(w, fps, frames, cuts):
    """手首の加速度（m/s²）の要約: 99% 点・最大・最大のコマ。w は手首の位置 (コマ, 3)。切り替えのコマ（範囲の中のもの）の前後 3 コマは 0 にする
    （つなぎ目の跳びを数えない。台本と同じ）。コマが 3 つ未満なら 2 階の差が取れないので None（測れない）。"""
    if len(w) < 3:
        return None
    values = np.linalg.norm(np.diff(w, 2, axis=0), axis=1) * fps * fps
    for c in cuts:
        if not frames[0] < c <= frames[-1]:
            continue
        k = c - frames[0] - 1
        values[max(0, k - 3):k + 3] = 0
    return {"p99": round(float(np.percentile(values, 99)), 1), "max": round(float(values.max()), 1),
            "max_at_frame": frames[int(np.argmax(values)) + 1]}


def body_loss(before, after):
    """手当てで手が頭や胴へ近づいた量の最大（m）。before・after は同じ形（コマ, 点）の離れ。もともと 3 cm より離れていた点は 3 cm から測る
    （遠くで少し近づくのは数えない）。点ごとに比べるので、別の点が新しく体に入るのを、ほかの点の小さい値で隠さない。"""
    return float(np.max(np.maximum(np.minimum(before, 0.03) - after, 0.0)))


class Separator:
    """腕どうしの重なりの測り方と直し方（Blender なしで計算できる所）。pos は {(骨, "head"|"tail") か ("forearm", 側, t): (コマ, 3)}。"""

    def __init__(self, s, names, frames, profile, torso, head):
        self.s, self.names, self.frames, self.n = s, names, list(frames), len(frames)
        self.profile, self.torso, self.head = profile, list(torso), head
        self.kernel, self.radius = gaussian(s["sigma"])
        first, last = self.frames[0], self.frames[-1]
        self.edges = [0] + [c - first for c in s["cuts"] if first < c <= last] + [self.n]
        self.segs = {side: self.segments_of(side) for side in names.sides}

    def segments_of(self, side):
        """片方の腕の棒の一覧: (名前, 始点, 終点, 太さ, やわらかいか)。始点・終点は (骨, 端) か、前腕の上の位置 ("forearm", 側, t)。"""
        names, s = self.names, self.s
        segs = []
        for t0, t1, r, soft in self.profile[side]:
            segs.append(("forearm_%d%s" % (round(t0 * 100), "(袖)" if soft else ""), ("forearm", side, t0), ("forearm", side, t1), r, soft))
        for finger in names.fingers[1:]:
            segs.append(("palm_" + finger, (names.hand(side), "head"), (names.finger(side, finger, names.parts[0]), "head"), s["palm_radius"],
                         False))
        for finger in names.fingers:
            for part in names.parts:
                bone = names.finger(side, finger, part)
                segs.append(("%s_%s" % (finger, part), (bone, "head"), (bone, "tail"), s["finger_radius"], False))
        return segs

    def bones(self):
        """位置を読む骨の名前（並びは名前の順）。"""
        names = self.names
        found = {key[0] for side in names.sides for seg in self.segs[side] for key in (seg[1], seg[2]) if key[0] != "forearm"}
        found |= {role(side) for side in names.sides for role in (names.upper, names.forearm, names.hand)}
        return sorted(found | set(self.torso) | ({self.head} if self.head else set()))

    def add_forearm(self, pos):
        """前腕の上の位置（肘 → 手首を forearm_bins に区切った点）を pos に足す。"""
        names, bins = self.names, self.s["forearm_bins"]
        for side in names.sides:
            elbow, wrist = pos[(names.forearm(side), "head")], pos[(names.hand(side), "head")]
            for k in range(bins + 1):
                t = k / bins
                pos[("forearm", side, t)] = elbow * (1 - t) + wrist * t
        return pos

    def measure(self, pos, hard_only=False):
        """コマごとの、いちばん大きい重なりと、その 2 点・要る離れ・組の名前（pos は全コマでも一部のコマでもよい）。
        hard_only なら、袖どうし（どちらもやわらかい区切り）の組を除いて測る（手・指・前腕の芯がからむ重なり）。"""
        s, left, right = self.s, self.names.sides[0], self.names.sides[1]
        m = len(pos[(self.names.hand(left), "head")])
        overlap = np.full(m, -1.0)
        pl, pr = np.zeros((m, 3)), np.zeros((m, 3))
        need = np.zeros(m)
        which = np.empty(m, dtype=object)
        for name_l, a0, a1, rl, soft_l in self.segs[left]:
            for name_r, b0, b1, rr, soft_r in self.segs[right]:
                if hard_only and soft_l and soft_r:
                    continue
                p, q = closest(pos[a0], pos[a1], pos[b0], pos[b1])
                wanted = (rl + rr) * (s["soft_factor"] if (soft_l and soft_r) else 1.0) + s["gap"]
                ov = wanted - np.linalg.norm(p - q, axis=1)
                better = ov > overlap
                overlap[better] = ov[better]
                pl[better], pr[better] = p[better], q[better]
                need[better] = wanted
                which[better] = name_l + " × " + name_r
        return np.maximum(overlap, 0.0), pl, pr, need, which

    def summary(self, overlap, which, fps):
        frames, n = self.frames, self.n
        limit = self.s["threshold"] + self.s["gap"]
        runs, start = [], None
        for i in range(n + 1):
            on = i < n and overlap[i] > limit
            if on and start is None:
                start = i
            if not on and start is not None:
                j = start + int(np.argmax(overlap[start:i]))
                runs.append({"frames": [frames[start], frames[i - 1]], "seconds": [round((frames[start] - 1) / fps, 2), round((frames[i - 1] - 1) / fps, 2)],
                             "max_overlap_mm": round(float(overlap[j]) * 1000, 1), "at": frames[j], "pair": which[j]})
                start = None
        return {"frames_over": int((overlap > limit).sum()), "threshold_mm": round(limit * 1000, 1),
                "max_overlap_mm": round(float(overlap.max()) * 1000, 1), "at_frame": frames[int(np.argmax(overlap))], "runs": runs}

    def smooth_amount(self, x):
        """量（長さ）を、前後の最大を取ってからならす。切り替えはまたがない。"""
        dilate = self.s["dilate"]
        out = np.zeros_like(x)
        for lo, hi in zip(self.edges[:-1], self.edges[1:]):
            part = x[lo:hi]
            if len(part) == 0:
                continue
            dil = np.array([part[max(0, i - dilate):i + dilate + 1].max() for i in range(len(part))])
            out[lo:hi] = np.convolve(np.pad(dil, self.radius, mode="edge"), self.kernel, mode="valid")
        return out

    def directions(self, pos, overlap, raw):
        """離す向き（コマごと）。左右の前腕の両方に垂直な向きを、区間の中で符号をそろえ、ならして、区間ごとに側を決める。"""
        names, n, kernel, radius = self.names, self.n, self.kernel, self.radius
        left, right = names.sides
        cross = np.cross(pos[(names.hand(left), "head")] - pos[(names.forearm(left), "head")],
                         pos[(names.hand(right), "head")] - pos[(names.forearm(right), "head")])
        norm = np.linalg.norm(cross, axis=1)
        valid = norm > 0.001                                              # 前腕がほぼ平行のコマは向きが決まらない
        cross = cross / np.maximum(norm, 1e-9)[:, None]
        direction = np.zeros((n, 3))
        spread = self.s["dilate"] + radius
        sides = []
        for lo, hi in zip(self.edges[:-1], self.edges[1:]):
            groups = []
            for i in range(lo, hi):
                if overlap[i] <= 0:
                    continue
                if groups and i - groups[-1][1] <= self.s["merge"]:         # 間が merge コマ以内の重なりは、1 つの区間にまとめる
                    groups[-1][1] = i
                else:
                    groups.append([i, i])
            for g0, g1 in groups:
                a0, a1 = max(lo, g0 - spread), min(hi, g1 + 1 + spread)
                ax, ok = cross[a0:a1].copy(), valid[a0:a1].copy()
                if not ok.any():
                    ax[:], ok[:] = np.array([0.0, 0.0, 1.0]), True
                last_good = None
                for k in range(len(ax)):                                   # 符号を前のコマにそろえる
                    if not ok[k]:
                        continue
                    if last_good is not None and float(ax[k] @ ax[last_good]) < 0:
                        ax[k] = -ax[k]
                    last_good = k
                good = np.where(ok)[0]
                for k in np.where(~ok)[0]:                                 # 決まらないコマは、いちばん近い決まるコマの向き
                    ax[k] = ax[good[np.argmin(np.abs(good - k))]]
                padded = np.pad(ax, ((radius, radius), (0, 0)), mode="edge")
                ax = np.stack([np.convolve(padded[:, c], kernel, mode="valid") for c in range(3)], axis=1)
                ax = ax / np.maximum(np.linalg.norm(ax, axis=1), 1e-9)[:, None]
                side = sum(float(raw[i] @ ax[i - a0]) for i in range(g0, g1 + 1) if overlap[i] > 0)
                direction[a0:a1] = ax * (1.0 if side >= 0 else -1.0)
                sides.append([self.frames[g0], self.frames[g1], round(side, 3)])
        return direction, sides

    def own(self, key, side):
        """pos の鍵 key が、その側の腕（肩から先）の点か。"""
        if key[0] == "forearm":
            return key[1] == side
        return key[0] in self.arm_bones[side]

    def rotate_arm(self, pos, side, omega):
        """片方の腕を、肩（上腕の付け根）のまわりに omega（回す軸 × 角度。コマごと）だけ回したときの位置。上腕を回すと、肘から先は形を
        変えずに丸ごと付いて回るので、Blender でコマを送らなくても計算だけで求まる。"""
        pivot_key = (self.names.upper(side), "head")
        pivot = pos[pivot_key]
        angle = np.linalg.norm(omega, axis=1)
        idx = np.where(angle >= 1e-5)[0]
        out = dict(pos)
        if len(idx) == 0:
            return out
        axis = omega[idx] / angle[idx][:, None]
        c, sn = np.cos(angle[idx])[:, None], np.sin(angle[idx])[:, None]
        for key in pos:
            if not self.own(key, side) or key == pivot_key:
                continue
            v = pos[key][idx] - pivot[idx]
            arr = pos[key].copy()
            arr[idx] = pivot[idx] + v * c + np.cross(axis, v) * sn + axis * (axis * v).sum(1)[:, None] * (1 - c)
            out[key] = arr
        return out

    def body_clearance(self, pos, per_point=False):
        """手の点（手首・指の付け根・指先）が、頭と胴にどれだけ近いか（小さいほど近い。負は中に入っている）。
        per_point なら点ごと (コマ, 点)（頭と胴のうち近いほう）。そうでなければコマごとのいちばん小さい値 (コマ,)。"""
        names, s = self.names, self.s
        m = len(pos[(names.hand(names.sides[0]), "head")])
        points = []
        for side in names.sides:
            points.append(pos[(names.hand(side), "head")])
            for finger in names.fingers:
                points.append(pos[(names.finger(side, finger, names.parts[0]), "head")])
                points.append(pos[(names.finger(side, finger, names.parts[-1]), "tail")])
        clear = np.full((m, len(points)), 9.0)
        if self.head and (self.head, "head") in pos:
            axis = pos[(self.head, "tail")] - pos[(self.head, "head")]
            axis = axis / np.maximum(np.linalg.norm(axis, axis=1), 1e-9)[:, None]
            centre = pos[(self.head, "head")] + axis * s["head_radius"]
            for i, p in enumerate(points):
                clear[:, i] = np.minimum(clear[:, i], np.linalg.norm(p - centre, axis=1) - s["head_radius"])
        for a, b in zip(self.torso[:-1], self.torso[1:]):
            for i, p in enumerate(points):
                _, q = closest(p, p, pos[(a, "head")], pos[(b, "head")])
                clear[:, i] = np.minimum(clear[:, i], np.linalg.norm(p - q, axis=1) - s["torso_radius"])
        return clear if per_point else clear.min(axis=1)

    def plan_detours(self, pos):
        """くり返しのあとに残った短い重なりごとに、すれ違う向きと量を探す（モジュールの説明）。
        戻り: (当てたあとの位置（計算）, [(側, 始まりのコマの番号, 回転)], 記録)"""
        s, names, n, frames = self.s, self.names, self.n, self.frames
        left_side, right_side = names.sides
        overlap = self.measure(pos)[0]
        limit, tol = s["threshold"] + s["gap"], s["gap"]
        runs, start = [], None
        for i in range(n + 1):
            on = i < n and overlap[i] > limit
            if on and start is None:
                start = i
            if not on and start is not None:
                runs.append((start, i - 1))
                start = None
        split = []                                       # 切り替えのコマをまたぐ区間は、切り替えごとに分ける（どの本の区間も手当てする）
        for g0, g1 in runs:
            inner = [e for e in self.edges if g0 < e <= g1]
            split += list(zip([g0] + inner, [e - 1 for e in inner] + [g1]))
        runs = split
        spread = s["dilate"] + self.radius
        amounts = np.arange(0.01, s["detour_max"] + 1e-9, 0.005)
        todo, records = [], []
        for g0, g1 in runs:
            seg_lo = max(e for e in self.edges if e <= g0)
            seg_hi = min(e for e in self.edges if e > g0)
            g1 = min(g1, seg_hi - 1)
            a0, a1 = max(seg_lo, g0 - spread), min(seg_hi, g1 + 1 + spread)
            ind = np.zeros(n)
            ind[g0:g1 + 1] = 1.0
            bump = self.smooth_amount(ind)[a0:a1]
            bump = bump / max(float(bump.max()), 1e-9)
            win = {k: v[a0:a1] for k, v in pos.items()}
            before_ov, pl_w, pr_w, _, which_w = self.measure(win)
            before_clear = self.body_clearance(win, per_point=True)        # 点ごとに比べる（別の点が体に入るのを、いちばん小さい値で隠さない）
            core = np.zeros(a1 - a0, dtype=bool)
            core[g0 - a0:g1 - a0 + 1] = True
            if float(before_ov[core].max()) <= limit:                       # 前の区間の手当てで、もう解けている
                continue
            j = int(np.argmax(np.where(core, before_ov, -1.0)))
            if which_w[j].count("(袖)") == 2:                                 # 袖どうしは動かさない（フリルは押し出しで直す）
                records.append({"frames": [frames[g0], frames[g1]], "pair": which_w[j], "overlap_before_mm": round(float(before_ov[core].max()) * 1000, 1),
                                "solved": False, "note": "袖どうし（やわらかい物どうし）なので手当てしない"})
                continue
            left = unit(win[(names.upper(left_side), "head")][j] - win[(names.upper(right_side), "head")][j])
            up = np.array([0.0, 0.0, 1.0])
            fwd = unit(np.cross(up, left))
            dirs = [left, up, fwd, unit(left + up), unit(left - up), unit(left + fwd), unit(left - fwd), unit(up + fwd), unit(up - fwd)]
            kb, ka = g0 - a0 - 1, g1 - a0 + 1
            if kb >= 0 and ka < a1 - a0:
                d_b, d_a = pl_w[kb] - pr_w[kb], pl_w[ka] - pr_w[ka]
                travel = d_a - d_b
                if np.linalg.norm(travel) > 0.01:                           # 通り抜ける向きに垂直で、もともと少しずれている側
                    u = unit(travel)
                    mean = (d_a + d_b) / 2
                    beside = mean - u * float(mean @ u)
                    if np.linalg.norm(beside) > 0.003:
                        dirs.append(unit(beside))
                for d in (d_b, d_a):
                    if np.linalg.norm(d) > 0.003:
                        dirs.append(unit(d))
            candidates = [v * sign for v in dirs for sign in (1.0, -1.0)]

            def trial(w, amount):
                share = 0.5 + (s["up_share"] - 0.5) * math.tanh(w[2] / 0.3)
                out, omegas = win, {}
                for side, sign, part in ((left_side, 1.0, share), (right_side, -1.0, 1.0 - share)):
                    r = win[(names.hand(side), "head")] - win[(names.upper(side), "head")]
                    delta = w[None, :] * (sign * part * amount * bump)[:, None]
                    omegas[side] = np.cross(r, delta) / np.maximum((r * r).sum(1), 1e-8)[:, None]
                    out = self.rotate_arm(out, side, omegas[side])
                ov = self.measure(out)[0]
                loss = body_loss(before_clear, self.body_clearance(out, per_point=True))
                worst = float(ov[core].max())
                outside = float(np.max(np.maximum(ov[~core] - np.maximum(before_ov[~core], tol), 0.0))) if (~core).any() else 0.0
                return worst, outside, loss, omegas

            before_worst = float(before_ov[core].max())
            chosen, fallback = None, (before_worst, None, 0.0, None, before_worst, 0.0)
            for amount in amounts:
                best = None
                for w in candidates:
                    worst, outside, loss, omegas = trial(w, float(amount))
                    score = worst + outside + loss
                    if score < fallback[0] - 1e-6:
                        fallback = (score, w, float(amount), omegas, worst, loss)
                    if worst <= tol and outside <= 0.0005 and loss <= s["detour_body_loss"] and (best is None or worst + loss < best[0]):
                        best = (worst + loss, w, float(amount), omegas, worst, loss)
                if best is not None:
                    chosen = best
                    break
            solved = chosen is not None
            if chosen is None:
                chosen = fallback
            _, w, amount, omegas, worst, loss = chosen
            record = {"frames": [frames[g0], frames[g1]], "seconds": [round((frames[g0] - 1) / self.fps, 2), round((frames[g1] - 1) / self.fps, 2)],
                      "pair": which_w[j], "overlap_before_mm": round(before_worst * 1000, 1), "solved": solved}
            if w is None:
                record["note"] = "どの候補でも良くならなかった（何もしていない）"
            else:
                record.update({"direction_left_up_forward": [round(float(w @ left), 2), round(float(w[2]), 2), round(float(w @ fwd), 2)],
                               "amount_mm": round(amount * 1000, 1), "overlap_after_mm": round(worst * 1000, 1),
                               "closer_to_body_mm": round(loss * 1000, 1)})
                for side in names.sides:
                    full = np.zeros((n, 3))
                    full[a0:a1] = omegas[side]
                    pos = self.rotate_arm(pos, side, full)
                    todo.append((side, a0, omegas[side]))
            records.append(record)
        return pos, todo, records


def forearm_profile(body, armature, names, side, s, aliases, deform):
    """初期姿勢で、前腕がいちばん重い頂点の、骨の軸からの距離を、骨に沿った区切りごとに測る: [(t0, t1, 太さ, やわらかいか)]（台本と同じ式。
    頂点の位置は単精度のまま計算する）。"""
    groups = {group.index: group.name for group in body.vertex_groups if group.name in deform}
    forearm = names.forearm(side)

    def heaviest(vertex):
        totals = {}
        for item in vertex.groups:
            if item.group not in groups:
                continue
            name = groups[item.group]
            name = aliases.get(name, name)
            totals[name] = totals.get(name, 0.0) + item.weight
        return max(totals, key=totals.get) if totals else ""

    m = np.array(armature.matrix_world)
    head = m[:3, :3] @ np.array(armature.data.bones[forearm].head_local) + m[:3, 3]
    wrist = m[:3, :3] @ np.array(armature.data.bones[names.hand(side)].head_local) + m[:3, 3]
    axis = wrist - head
    length = np.linalg.norm(axis)
    axis = axis / length
    bm = np.array(body.matrix_world)
    pts = np.array([bm[:3, :3] @ np.array(v.co) + bm[:3, 3] for v in body.data.vertices if v.groups and heaviest(v) == forearm])
    if len(pts) == 0:
        raise SystemExit(f"{body.name} に、{forearm} がいちばん重い頂点がありません（前腕の太さを測れない）")
    rel = pts - head
    t = rel @ axis / length
    radial = np.linalg.norm(rel - np.outer(rel @ axis, axis), axis=1)
    rows = []
    bins = s["forearm_bins"]
    for k in range(bins):
        t0, t1 = k / bins, (k + 1) / bins
        sel = radial[(t >= t0 - 0.02) & (t < t1 + 0.02)]
        if len(sel) < 5:
            rows.append((t0, t1, s["forearm_radius"], False))
            continue
        soft = float(np.percentile(sel, 95)) >= s["soft_from"]
        r = float(np.percentile(sel, s["soft_percentile"] if soft else 95))
        if soft and s["soft_radius"] > 0:
            rows.append((t0, t1, s["soft_radius"], soft))
            continue
        rows.append((t0, t1, max(r, s["forearm_radius"] if not soft else s["soft_from"]), soft))
    return rows


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Matrix, Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        s = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not s["armature"] or obj.name == s["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + s['armature'] + '）' if s['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:          # 距離の設定（m）と Blender 単位を合わせる
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    names = Names(s["bones"])
    body = bpy.data.objects.get(s["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"体のメッシュがありません: {s['body']}")
    pose = armature.pose.bones
    for side in names.sides:
        needed = [names.upper(side), names.forearm(side), names.hand(side)] + [names.finger(side, f, p) for f in names.fingers for p in names.parts]
        missing = [name for name in needed if name not in pose]
        if missing:
            raise SystemExit(f"{armature.name} に骨がありません（bones の型を確かめてください）: {missing}")
        if pose[names.upper(side)].rotation_mode != "QUATERNION":
            raise SystemExit(f"{names.upper(side)} の回転が四元数ではありません（{pose[names.upper(side)].rotation_mode}）")
    frames = list(range(scene.frame_start, scene.frame_end + 1))
    action = armature.animation_data.action if armature.animation_data else None
    curves = action_curves(action) if action is not None else []
    for side in names.sides:                            # 直した回転は、キーのあるコマだけに書く（キーの間を補う動きだと前後のコマへ広がる）
        bone = names.upper(side)
        missing = unkeyed_frames(curves, 'pose.bones["%s"].rotation_quaternion' % bone, frames)
        if missing:
            raise SystemExit(f"{bone} の回転（四元数）に、キーの無いコマがあります（{len(missing)} コマ。最初は {missing[0]}）。"
                             "キーの間を補う動きのままだと、直した回転が前後のコマや切り替えの向こうへ広がるので、先にコマごとのキーに焼いてください")
    fps = scene.render.fps / scene.render.fps_base
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    profile = {side: forearm_profile(body, armature, names, side, s, s["aliases"], deform) for side in names.sides}
    torso = [b for b in names.torso_all if b in pose]
    head = names.head if names.head and names.head in pose else None
    plan = Separator(s, names, frames, profile, torso, head)
    plan.fps = fps
    plan.arm_bones = {}
    for side in names.sides:                            # 腕の骨: 上腕とその子孫（肩から先。回すと付いて回る点）
        bone = armature.data.bones[names.upper(side)]
        plan.arm_bones[side] = {bone.name} | {child.name for child in bone.children_recursive}
    hidden = [(obj, obj.hide_viewport) for obj in bpy.data.objects if obj.type == "MESH"]
    for obj, _ in hidden:
        obj.hide_viewport = True                         # メッシュの変形は要らない（コマ送りを速くする）
    bone_list = plan.bones()
    n = len(frames)

    def sample():
        """全コマの、骨の付け根と先の位置（世界の座標）。"""
        pos = {(b, e): np.zeros((n, 3)) for b in bone_list for e in ("head", "tail")}
        for i, f in enumerate(frames):
            scene.frame_set(f)
            aw = armature.matrix_world.copy()           # そのコマのアーマチュアの向き（アーマチュアや親が動いても合う）
            for b in bone_list:
                pb = pose[b]
                pos[(b, "head")][i] = aw @ pb.head
                pos[(b, "tail")][i] = aw @ pb.tail
        return plan.add_forearm(pos)

    def rotate_keys(side, a0, omega):
        """rotate_arm と同じ回転を、Blender の上腕の骨のキーに書く。戻り: 書いたキーの数。"""
        pb = pose[names.upper(side)]
        count = 0
        for k in range(len(omega)):
            angle = float(np.linalg.norm(omega[k]))
            if angle < 1e-5:
                continue
            f = frames[a0 + k]
            scene.frame_set(f)
            aw = armature.matrix_world.copy()
            pivot = aw @ pb.head
            rotation = Matrix.Translation(pivot) @ Matrix.Rotation(angle, 4, Vector(omega[k] / angle)) @ Matrix.Translation(-pivot)
            pb.matrix = aw.inverted() @ rotation @ aw @ pb.matrix
            bpy.context.view_layer.update()
            pb.keyframe_insert("rotation_quaternion", frame=f)
            count += 1
        return count

    pos = sample()
    overlap, pl, pr, need, which = plan.measure(pos)
    report = {"schema_version": "model3d-separate-hands/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "armature": armature.name, "frames": [frames[0], frames[-1]], "gap": s["gap"],
              "radii": {"palm": s["palm_radius"], "finger": s["finger_radius"], "soft_factor": s["soft_factor"],
                        "forearm_profile_mm": {side: [[t0, t1, round(r * 1000, 1), soft] for t0, t1, r, soft in profile[side]] for side in names.sides}},
              "before": plan.summary(overlap, which, fps), "passes": []}
    start_wrist = {side: pos[(names.hand(side), "head")].copy() for side in names.sides}
    left_side, right_side = names.sides
    for number in range(s["passes"]):
        if overlap.max() <= s["gap"] + 0.0005:
            break
        raw = pl - pr
        direction, sides = plan.directions(pos, overlap, raw)
        move = np.zeros(n)
        active = overlap > 0
        move[active] = np.maximum(need[active] - (raw[active] * direction[active]).sum(1), 0.0)   # その向きでの今の離れを、要る離れまで広げる
        move = plan.smooth_amount(move) * s["relax"]
        changed = 0
        # 上へ動くほうの腕に多く持たせる（半分ずつにすると、下になる腕が裾へ押し込まれた。台本の記録）
        share_l = 0.5 + (s["up_share"] - 0.5) * np.tanh(direction[:, 2] / 0.3)
        for side, point, sign, share in ((left_side, pl, 1.0, share_l), (right_side, pr, -1.0, 1.0 - share_l)):
            r = point - pos[(names.upper(side), "head")]
            delta = direction * (sign * share * move)[:, None]
            omega = np.cross(r, delta) / np.maximum((r * r).sum(1), 1e-8)[:, None]
            changed += rotate_keys(side, 0, omega)
        pos = sample()
        overlap, pl, pr, need, which = plan.measure(pos)
        report["passes"].append({"pass": number + 1, "keys_changed": changed, "sides": sides,
                                 **{k: v for k, v in plan.summary(overlap, which, fps).items() if k != "runs"}})
    report["after_passes"] = plan.summary(overlap, which, fps)
    report["detours"] = []
    for number in range(s["detour"]):                    # 残った短い重なりに、すれ違いの手当て
        planned, todo, records = plan.plan_detours(pos)
        if not records:
            break
        changed = sum(rotate_keys(side, a0, omega) for side, a0, omega in todo)
        pos = sample()
        differ = max(float(np.linalg.norm(pos[(names.hand(side), "head")] - planned[(names.hand(side), "head")], axis=1).max())
                     for side in names.sides)
        overlap, pl, pr, need, which = plan.measure(pos)
        report["detours"].append({"round": number + 1, "keys_changed": changed, "events": records, "computed_vs_actual_mm": round(differ * 1000, 2),
                                  **{k: v for k, v in plan.summary(overlap, which, fps).items() if k != "runs"}})
        if not todo:
            break
    report["after"] = plan.summary(overlap, which, fps)
    hard, _, _, _, hard_which = plan.measure(pos, hard_only=True)
    report["after_hard"] = plan.summary(hard, hard_which, fps)   # 袖どうしでない組だけ（いちばん大きい組が袖どうしでも隠れない）
    moved = {side: np.linalg.norm(pos[(names.hand(side), "head")] - start_wrist[side], axis=1) for side in names.sides}
    report["wrist_moved"] = {side: {"frames_over_1mm": int((moved[side] > 0.001).sum()), "max_mm": round(float(moved[side].max()) * 1000, 1),
                                    "at_frame": frames[int(np.argmax(moved[side]))]} for side in names.sides}
    acc = {}
    for side in names.sides:                             # 手首の加速度（m/s²）。直す前と後。切り替えの前後 3 コマは除く
        row = {}
        for label, w in (("before", start_wrist[side]), ("after", pos[(names.hand(side), "head")])):
            row[label] = wrist_acceleration(w, fps, frames, s["cuts"])
        acc[side] = row
    report["wrist_acceleration"] = acc
    for obj, state in hidden:
        obj.hide_viewport = state
    scene.frame_set(frames[0])
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report["settings"] = s
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"before": report["before"]["frames_over"], "after": report["after"]["frames_over"]}))


if __name__ == "__main__":
    main()
