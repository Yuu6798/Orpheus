"""整えた 70 点（motion.prepare_keypoints の points）を区間ごとに手直しする: 顔の向き、握りこぶし、肘から先の回し。

推定が届かない動き（真横まで振り向く顔、画面の端で開いた指、速い拳の回し）を、点の上で作り直す。値は recipe で決め、
ここでは決めた量の処理だけを行う。区間は [開始, 終了)（終了のコマは含まない）で、その前後の fade コマでなめらかに出入りする
（クリップの端では出入りを詰める）。座標は x 右・y 奥・z 上、m。
"""
import math

import numpy as np

HEAD = (0, 1, 2, 3, 4)                                # 鼻・両目・両耳
EARS = (3, 4)
SIDES = ("left", "right")
HANDS = {"right": {"shoulder": 6, "elbow": 8, "wrist": 41, "fingers": {"thumb": 21, "index": 25, "middle": 29, "ring": 33, "pinky": 37}},
         "left": {"shoulder": 5, "elbow": 7, "wrist": 62, "fingers": {"thumb": 42, "index": 46, "middle": 50, "ring": 54, "pinky": 58}}}

# 握りの形。手の座標: x 手の先（手首 → 中指の付け根）、y 人差し指の側、z 手のひら側。単位は手首〜中指の付け根の長さ。
# 指ごとに [先, 第 1 関節, 第 2 関節, 付け根]（MHR70 の 指の番号 + 0, 1, 2, 3 と同じ並び）
_KNUCKLES = {"index": (0.95, 0.27, 0.0), "middle": (1.0, 0.07, 0.0), "ring": (0.94, -0.13, 0.0), "pinky": (0.86, -0.32, 0.0)}
_SEGMENTS = {"index": (0.45, 0.28, 0.22), "middle": (0.48, 0.30, 0.23), "ring": (0.45, 0.28, 0.22), "pinky": (0.36, 0.22, 0.19)}
_FLEX = (math.radians(88), math.radians(188), math.radians(245))     # 根元から先へ、曲げの累計
_THUMB = ((0.80, 0.08, 0.62), (0.72, 0.30, 0.55), (0.50, 0.42, 0.30), (0.25, 0.30, 0.10))   # 先、IP、MCP、CMC


def _fist_template():
    shape = {}
    for finger, knuckle in _KNUCKLES.items():
        base = np.array(knuckle)
        bends = [np.array((math.cos(angle), 0.0, math.sin(angle))) for angle in _FLEX]
        joint2 = base + _SEGMENTS[finger][0] * bends[0]
        joint1 = joint2 + _SEGMENTS[finger][1] * bends[1]
        shape[finger] = [joint1 + _SEGMENTS[finger][2] * bends[2], joint1, joint2, base]
    shape["thumb"] = [np.array(p) for p in _THUMB]
    return shape


FIST = _fist_template()


def smoothstep(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _unit(v):
    length = np.linalg.norm(v)
    return v / length if length > 1e-9 else v


def _across(v, axis):
    return v - axis * v.dot(axis)


def window_weight(t, start, end, fade_in, fade_out, frames):
    """全部効かせる [start, end) と、前後 fade_in・fade_out コマの出入り（smoothstep）の重み。"""
    low, high = max(0, start - fade_in), min(frames - 1, end - 1 + fade_out)
    if t < low or t > high:
        return 0.0
    if t < start:
        return smoothstep((t - low) / max(1, start - low))
    if t >= end:
        return smoothstep((high - t) / max(1, high - (end - 1)))
    return 1.0


def _span(start, end, fade_in, fade_out, frames):
    return range(max(0, start - fade_in), min(frames - 1, end - 1 + fade_out) + 1)


def face_azimuth(frame):
    """顔の向き（度）: 両耳の中点 → 鼻を上から見た向き。カメラの方（−y）が 0、+x へ回ると増える。"""
    forward = frame[0] - frame[list(EARS)].mean(axis=0)
    return math.degrees(math.atan2(forward[0], -forward[1]))


def head_turn(points, degrees, start, end, fade_in=0, fade_out=0):
    """頭の点（鼻・両目・両耳）を、両耳の中点を通る鉛直の軸のまわりに degrees × 重みだけ回す（+ は上から見て反時計回り、
    顔が +x の方へ向く）。移し替えは頭の向きを鼻と両耳から決めるので、点を回せば首と頭が付いてくる。"""
    out = points.copy()
    changed, samples = 0, []
    for t in _span(start, end, fade_in, fade_out, len(points)):
        weight = window_weight(t, start, end, fade_in, fade_out, len(points))
        if weight <= 0:
            continue
        frame = points[t]
        centre = frame[list(EARS)].mean(axis=0)
        angle = math.radians(degrees * weight)
        rotation = np.array([[math.cos(angle), -math.sin(angle), 0.0], [math.sin(angle), math.cos(angle), 0.0], [0.0, 0.0, 1.0]])
        for i in HEAD:
            out[t, i] = centre + rotation @ (frame[i] - centre)
        changed += 1
        if t in (start, (start + end - 1) // 2, end - 1):
            samples.append([int(t), round(face_azimuth(frame), 2), round(face_azimuth(out[t]), 2)])
    return out, {"changed_frames": changed, "face_azimuth_before_after_deg": samples}


def _fist_points(side, wrist, along, index_side, palm, hand_length):
    placed = {}
    for finger, first in HANDS[side]["fingers"].items():
        for j, p in enumerate(FIST[finger]):
            placed[first + j] = wrist + hand_length * (along * p[0] + index_side * p[1] + palm * p[2])
    return placed


def _hand_length(points, side):
    hand = HANDS[side]
    return float(np.median(np.linalg.norm(points[:, hand["fingers"]["middle"] + 3] - points[:, hand["wrist"]], axis=1)))


def fist(points, side, start, end, fade=3):
    """片手の指を握りにする（手首の位置と手の向きは推定のまま）。画面の端や体の陰で推定した指は当てにならない。"""
    out = points.copy()
    hand = HANDS[side]
    fingers = hand["fingers"]
    length = _hand_length(points, side)
    changed = 0
    for t in _span(start, end, fade, fade, len(points)):
        weight = window_weight(t, start, end, fade, fade, len(points))
        if weight <= 0:
            continue
        frame = points[t]
        wrist = frame[hand["wrist"]]
        along = _unit(frame[fingers["middle"] + 3] - wrist)
        index_side = _unit(_across(frame[fingers["index"] + 3] - frame[fingers["pinky"] + 3], along))
        palm = -np.cross(along, index_side) if side == "right" else np.cross(along, index_side)
        for i, p in _fist_points(side, wrist, along, index_side, palm, length).items():
            out[t, i] = frame[i] * (1 - weight) + p * weight
        changed += 1
    return out, {"changed_frames": changed, "hand_length_m": round(length, 4)}


def _chest(frame):
    """胸の座標: 肩の中点、前 f・体の左 l・上 u。"""
    shoulders = (frame[5] + frame[6]) / 2
    up = _unit(shoulders - (frame[9] + frame[10]) / 2)
    left = _unit(_across(frame[5] - frame[6], up))
    return shoulders, np.cross(left, up), left, up


def forearm_roll(points, start, end, stack, half=7.1, fade=4, radius=0.06, radius_lat=0.035, tilt_deg=25.0, lateral=-0.06,
                 forward_add=0.0, up_add=0.0, sigma=5.0, direction=1, palm="down", palm_down_deg=20.0, elbows="ik"):
    """両の拳が胸の前で回り合う動きを作り直す。

    回る中心は、推定の両手首の中点の前と上（胸の座標）を sigma コマでならしたもの。横は lateral に置く。両の拳は、
    中心のまわりの縦長の輪（上下 radius・横 radius_lat、前へ tilt_deg 傾ける）に反対向きに置き、半周を half コマで回す
    （stack のコマで右が下）。
    - elbows "ik": 肩から拳までを腕の長さで毎コマ解く（腕全体が動く）。
    - elbows "fixed": 肘を、手が回る中心にあるときの位置に止め（胸に対して動かない、中心は胸の座標で一定）、前腕だけを肘のまわりに回す。
    - palm "down": 手のひらを下と内向き。"in": 内向きで親指が上の縦の拳（palm_down_deg だけ下へ傾ける）。
    腕と手の長さはクリップ全体の中央値。
    """
    out = points.copy()
    frames = len(points)
    upper = {s: float(np.median(np.linalg.norm(points[:, h["elbow"]] - points[:, h["shoulder"]], axis=1))) for s, h in HANDS.items()}
    lower = {s: float(np.median(np.linalg.norm(points[:, h["wrist"]] - points[:, h["elbow"]], axis=1))) for s, h in HANDS.items()}
    hand = float(np.median([np.linalg.norm(points[:, h["fingers"]["middle"] + 3] - points[:, h["wrist"]], axis=1) for h in HANDS.values()]))
    span = list(_span(start, end, fade, fade, frames))
    middle = []
    for t in span:
        shoulders, forward, left, up = _chest(points[t])
        m = (points[t, 41] + points[t, 62]) / 2 - shoulders
        middle.append((m.dot(forward), m.dot(left), m.dot(up)))
    middle = np.array(middle)
    reach = int(math.ceil(3 * sigma))
    kernel = np.exp(-0.5 * (np.arange(-reach, reach + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    smooth = np.stack([np.convolve(np.pad(middle[:, k], reach, mode="edge"), kernel, mode="valid") for k in range(3)], axis=1)
    full = [n for n, t in enumerate(span) if start <= t < end]
    fixed_forward = float(np.mean(smooth[full, 0])) + forward_add
    fixed_up = float(np.mean(smooth[full, 2])) + up_add

    def solve_arm(side, shoulder, target, up, outward):
        """肩から target へ、腕の長さで肘を解く（肘は下と外へ）。(肘, 届く範囲に収めた手首)"""
        d = target - shoulder
        length = min(max(np.linalg.norm(d), abs(upper[side] - lower[side]) + 1e-4), upper[side] + lower[side] - 1e-4)
        direction_ = _unit(d)
        pole = _unit(_across(-0.8 * up + 0.6 * outward, direction_))
        along = (upper[side] ** 2 - lower[side] ** 2 + length * length) / (2 * length)
        return shoulder + direction_ * along + pole * math.sqrt(max(upper[side] ** 2 - along * along, 0.0)), shoulder + direction_ * length

    elbow_track = {s: [] for s in SIDES}
    swing = {s: [] for s in SIDES}
    changed = 0
    for n, t in enumerate(span):
        weight = window_weight(t, start, end, fade, fade, frames)
        if weight <= 0:
            continue
        frame = points[t]
        shoulders, forward, left, up = _chest(frame)
        if elbows == "fixed":
            centre = shoulders + forward * fixed_forward + left * lateral + up * fixed_up
        else:
            centre = shoulders + forward * (smooth[n, 0] + forward_add) + left * lateral + up * (smooth[n, 2] + up_add)
        phase = -math.pi / 2 + direction * math.pi * (t - stack) / half
        tilt = math.radians(tilt_deg)
        ring = left * (radius_lat * math.cos(phase)) + (up * math.cos(tilt) + forward * math.sin(tilt)) * (radius * math.sin(phase))
        placed = {}
        for side, h in HANDS.items():
            target = centre + (1 if side == "right" else -1) * ring
            outward = -left if side == "right" else left
            if elbows == "fixed":
                elbow, _ = solve_arm(side, frame[h["shoulder"]], centre, up, outward)
                wrist = elbow + lower[side] * _unit(target - elbow)
                if start <= t < end:
                    e = elbow - shoulders
                    elbow_track[side].append((e.dot(forward), e.dot(left), e.dot(up)))
                    swing[side].append(math.degrees(math.acos(np.clip(_unit(wrist - elbow).dot(_unit(centre - elbow)), -1, 1))))
            else:
                elbow, wrist = solve_arm(side, frame[h["shoulder"]], target, up, outward)
            along = _unit(wrist - elbow)
            inward = -outward
            if palm == "in":
                down = math.radians(palm_down_deg)
                palm_dir = _unit(_across(math.cos(down) * inward - math.sin(down) * up, along))
            else:
                palm_dir = _unit(_across(-0.85 * up + 0.5 * inward, along))
            index_side = np.cross(along, palm_dir) if side == "right" else np.cross(palm_dir, along)
            placed[side] = {h["elbow"]: elbow, h["wrist"]: wrist, **_fist_points(side, wrist, along, index_side, palm_dir, hand)}
        for side in placed:
            for i, p in placed[side].items():
                out[t, i] = points[t, i] * (1 - weight) + p * weight
        changed += 1
    report = {"changed_frames": changed, "arm_lengths_m": {s: [round(upper[s], 4), round(lower[s], 4)] for s in SIDES},
              "hand_length_m": round(hand, 4), "elbows": elbows}
    if elbows == "fixed":
        report["centre_m"] = {"forward": round(fixed_forward, 4), "lateral": lateral, "up": round(fixed_up, 4)}
        report["elbow_spread_m"] = {s: round(float(np.array(elbow_track[s]).std(axis=0).max()), 5) if elbow_track[s] else None for s in SIDES}
        report["forearm_swing_max_deg"] = {s: round(max(swing[s]), 2) if swing[s] else None for s in SIDES}
    return out, report
