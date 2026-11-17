"""動きの検品（check.animation）: 合成の人形（骨と体のメッシュ）に、決まったコマで破綻を入れて、入れたコマと場所で見つけること、きれいな
動きでは何も挙げないこと（Blender なし）。Blender の中の台本の、測る所の小さな関数（代役の値）と、工程の受け渡し（代役の Blender）。
本物の Blender 5.2.4 での照合は docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import animation_check as ac
from model3d.blender_adapter import animation_sample as sampler
from model3d.io import atomic_json

FPS = 30
SKIP = ()


# ---------------------------------------------------------------- 合成の人形
def _frame(head, direction, hint=(0.0, 0.0, 1.0)):
    """付け根 head から direction を向く骨の行列（Y が骨の向き）。"""
    y = np.asarray(direction, float) / np.linalg.norm(direction)
    x = np.cross(y, hint)
    if np.linalg.norm(x) < 1e-6:
        x = np.cross(y, (1.0, 0.0, 0.0))
    x /= np.linalg.norm(x)
    z = np.cross(x, y)
    m = np.eye(4)
    m[:3, 0], m[:3, 1], m[:3, 2], m[:3, 3] = x, y, z, head
    return m


def _rotation(axis, degrees):
    axis = np.asarray(axis, float) / np.linalg.norm(axis)
    x, y, z = axis
    k = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
    a = math.radians(degrees)
    return np.eye(3) + math.sin(a) * k + (1 - math.cos(a)) * (k @ k)


# 骨: 名前 → (親, 付け根, 向き, 人型の役割)
BONES = {
    "hips": (None, (0, 0, 1.0), (0, 0, 1), "hips"),
    "spine": ("hips", (0, 0, 1.1), (0, 0, 1), "spine"),
    "chest": ("spine", (0, 0, 1.25), (0, 0, 1), "chest"),
    "neck": ("chest", (0, 0, 1.45), (0, 0, 1), "neck"),
    "head": ("neck", (0, 0, 1.52), (0, 0, 1), "head"),
    "upper_arm.L": ("chest", (0.18, 0, 1.40), (1, 0, 0), "left_upper_arm"),
    "lower_arm.L": ("upper_arm.L", (0.46, 0, 1.40), (1, 0, 0), "left_lower_arm"),
    "hand.L": ("lower_arm.L", (0.70, 0, 1.40), (1, 0, 0), "left_hand"),
    "index.L": ("hand.L", (0.78, 0, 1.40), (1, 0, 0), "left_index_proximal"),
    "upper_arm.R": ("chest", (-0.18, 0, 1.40), (-1, 0, 0), "right_upper_arm"),
    "lower_arm.R": ("upper_arm.R", (-0.46, 0, 1.40), (-1, 0, 0), "right_lower_arm"),
    "hand.R": ("lower_arm.R", (-0.70, 0, 1.40), (-1, 0, 0), "right_hand"),
    "index.R": ("hand.R", (-0.78, 0, 1.40), (-1, 0, 0), "right_index_proximal"),
    "upper_leg.L": ("hips", (0.09, 0, 0.95), (0, 0, -1), "left_upper_leg"),
    "lower_leg.L": ("upper_leg.L", (0.09, 0, 0.52), (0, 0, -1), "left_lower_leg"),
    "foot.L": ("lower_leg.L", (0.09, 0, 0.08), (0, -1, -0.5), "left_foot"),
    "toes.L": ("foot.L", (0.09, -0.12, 0.02), (0, -1, 0), "left_toes"),
    "upper_leg.R": ("hips", (-0.09, 0, 0.95), (0, 0, -1), "right_upper_leg"),
    "lower_leg.R": ("upper_leg.R", (-0.09, 0, 0.52), (0, 0, -1), "right_lower_leg"),
    "foot.R": ("lower_leg.R", (-0.09, 0, 0.08), (0, -1, -0.5), "right_foot"),
    "toes.R": ("foot.R", (-0.09, -0.12, 0.02), (0, -1, 0), "right_toes"),
    "skirt": ("hips", (0.0, -0.12, 0.95), (0, -0.2, -1), None),       # 揺れものの骨（人型でない）
}
NAMES = list(BONES)
REST = np.array([_frame(np.array(h, float), d, (0.0, -1.0, 0.0) if abs(d[2]) > 0.9 else (0.0, 0.0, 1.0))
                 for _p, h, d, _r in BONES.values()])
CHILDREN = {}
for _name, (_parent, *_rest) in BONES.items():
    if _parent:
        CHILDREN.setdefault(_parent, []).append(_name)


def _subtree(name):
    out, stack = [], [name]
    while stack:
        bone = stack.pop()
        out.append(bone)
        stack.extend(CHILDREN.get(bone, []))
    return out


def _turn(pose, bone, axis, degrees):
    """骨 bone を、その付け根のまわりに世界の軸 axis で回す（子も一緒に）。"""
    i = NAMES.index(bone)
    head = pose[i, :3, 3].copy()
    m = np.eye(4)
    m[:3, :3] = _rotation(axis, degrees)
    m[:3, 3] = head - m[:3, :3] @ head
    for name in _subtree(bone):
        j = NAMES.index(name)
        pose[j] = m @ pose[j]


def _move(pose, bone, delta):
    for name in _subtree(bone):
        pose[NAMES.index(name), :3, 3] += delta


def _mesh():
    """体のメッシュ: 胴の筒（半径 0.15）、頭の球（半径 0.1。三角形）、脚の筒、足の箱、腕・手・指の点、揺れものの布の点。"""
    co, names, faces = [], [], []
    for z in np.arange(0.95, 1.4501, 0.01):
        bone = "hips" if z < 1.1 else ("spine" if z < 1.25 else "chest")
        for k in range(48):
            a = 2 * math.pi * k / 48
            co.append((0.15 * math.cos(a), 0.11 * math.sin(a), z))
            names.append(bone)
    first = len(co)
    rings, around = 8, 12
    center = np.array([0.0, 0.0, 1.64])
    for i in range(rings + 1):
        theta = math.pi * i / rings
        for j in range(around):
            phi = 2 * math.pi * j / around
            co.append(tuple(center + 0.1 * np.array([math.sin(theta) * math.cos(phi), math.sin(theta) * math.sin(phi), math.cos(theta)])))
            names.append("head")
    for i in range(rings):
        for j in range(around):
            a, b = first + i * around + j, first + i * around + (j + 1) % around
            c, d = a + around, b + around
            faces += [[a, b, d], [a, d, c]]
    for side, x in (("L", 0.09), ("R", -0.09)):
        for bone, z0, z1 in ((f"upper_leg.{side}", 0.55, 0.95), (f"lower_leg.{side}", 0.12, 0.52)):
            for z in np.arange(z0, z1, 0.02):
                for k in range(16):
                    a = 2 * math.pi * k / 16
                    co.append((x + 0.06 * math.cos(a), 0.06 * math.sin(a), z))
                    names.append(bone)
        for dx in (-0.04, 0.04):
            for y in (-0.10, 0.05):
                for z in (0.0, 0.08):
                    co.append((x + dx, y, z))
                    names.append(f"foot.{side}")
            co.append((x + dx, -0.16, 0.0))
            names.append(f"toes.{side}")
        for bone, x0 in ((f"upper_arm.{side}", 0.25), (f"lower_arm.{side}", 0.55), (f"hand.{side}", 0.74), (f"index.{side}", 0.8)):
            for k in range(4):
                co.append(((x0 if side == "L" else -x0), 0.03 * math.cos(k), 1.40 + 0.03 * math.sin(k)))
                names.append(bone)
    for k in range(10):
        co.append((0.03 * k - 0.15, -0.16, 0.9))
        names.append("skirt")
    co = np.array(co, float)
    torso = np.array([[1.0, 1.0] if n in ("hips", "spine", "chest") else [0.0, 1.0] for n in names])
    return co, names, torso, faces


CO, DOMINANT, TORSO, FACES = _mesh()


FOOT_POINTS = {side: [i for i, n in enumerate(DOMINANT) if n in (f"foot.{side}", f"toes.{side}")] for side in ("L", "R")}


def _lowest(pose, picked):
    """足の頂点を、いちばん重い骨に固く付けて動かし（試験のための単純な変形）、いちばん低い点を返す。"""
    points = []
    for i in picked:
        j = NAMES.index(DOMINANT[i])
        m = pose[j] @ np.linalg.inv(REST[j])
        points.append(m[:3, :3] @ CO[i] + m[:3, 3])
    points = np.array(points)
    return points[int(np.argmin(points[:, 2]))]


def make_sample(edit, frames=120):
    """コマごとに edit(pose, f) で姿勢を変えた動きの Sample。f は 1 から。腕は下ろした姿勢を基本にする。"""
    poses = []
    for f in range(1, frames + 1):
        pose = REST.copy()
        _turn(pose, "upper_arm.L", (0, 1, 0), 75)                     # 腕を下ろす
        _turn(pose, "upper_arm.R", (0, 1, 0), -75)
        _move(pose, "hips", np.array([0.02 * math.sin(2 * math.pi * f / 90), 0.0, 0.0]))       # ゆっくり横へ揺れる
        edit(pose, f)
        poses.append(pose)
    pose = np.array(poses).astype(np.float32).astype(np.float64)        # Blender の姿勢の行列は単精度
    quat = np.array([[ac.quaternion(m[:3, :3]) for m in frame] for frame in pose])
    heads = pose[:, :, :3, 3].copy()
    feet = np.array([[_lowest(p, FOOT_POINTS["L"]), _lowest(p, FOOT_POINTS["R"])] for p in pose])
    humanoid = {role: name for name, (_p, _h, _d, role) in BONES.items() if role}
    body = [{"name": "Body", "co": CO, "dominant": DOMINANT, "torso": TORSO, "faces": [np.array(f) for f in FACES]}]
    return ac.Sample(humanoid, NAMES, [BONES[n][0] for n in NAMES], REST, list(range(1, frames + 1)), FPS, pose, quat, heads,
                     feet, feet, {name: 1.0 for name in NAMES}, body)


def _clean(pose, f):
    pass


def _events(sample, **kwargs):
    return ac.check(sample, ac.settings(kwargs), skip_bones=SKIP)["events"]


def _found(events, signal, first, last, part=""):
    return [e for e in events if e["signal"] == signal and e["frames"][0] <= last + 1 and e["frames"][1] >= first - 1 and part in e["part"]]


# ---------------------------------------------------------------- 数える所（Blender なし）
def test_clean_motion_has_no_events():
    report = ac.check(make_sample(_clean), ac.settings({}))
    assert report["events"] == [] and report["overall"] == "clean"
    assert report["schema_version"] == "model3d-animation-check/0.1" and report["frames"] == [1, 120] and report["fps"] == FPS
    assert report["torso"]["slices"] >= 20 and report["head_triangles"] == 8 * 12 * 2
    assert report["arm_torso_baseline_m"] == {"left": [0.0, 0.0, 0.0], "right": [0.0, 0.0, 0.0]}     # 細い胴では、下ろした腕は胴に触れない
    assert report["leg_radius_m"]["left_upper_leg"] == pytest.approx(0.06, abs=1e-3)
    assert report["bones"] == len(NAMES) - 1                           # 親の無い腰のほかは全部（見た目を動かす骨）


def _env(f, a, b, ramp=3):
    if f < a or f > b:
        return 0.0
    u = min(1.0, (f - a + 1) / ramp, (b - f + 1) / ramp)
    return u * u * (3 - 2 * u)


def _faulty(pose, f):
    _turn(pose, "hand.L", (0, 1, 0), 95 * _env(f, 6, 16))                                  # 左手首を 95° 曲げる
    _turn(pose, "upper_arm.L", (0, 1, 0), 22 * _env(f, 20, 34))                            # 垂らした左腕を体の中へ振る
    if f == 40:
        _turn(pose, "hand.R", (1, 0, 0), 120)                                              # 右手を 1 コマだけ 120° ねじる
    _move(pose, "hips", np.array([0.0, 0.0, -0.05 * _env(f, 50, 56, 2)]))                 # 腰を 5 cm 下げる（足が床より下）
    if 62 <= f <= 75:
        _turn(pose, "head", (1, 0, 0), 5 if f % 2 else -5)                                # 頭を 1 コマおきに ±5° 揺らす
    _turn(pose, "upper_leg.L", (0, 1, 0), 25 * _env(f, 82, 94))                            # 左脚を内へ振って右脚に重ねる
    if f == 100:
        _turn(pose, "lower_arm.L", (0, 1, 0), 40)                                          # 左の前腕を 1 コマだけ 40° 振る


def test_faults_are_found_where_they_were_put():
    events = _events(make_sample(_faulty))
    assert _found(events, "joint.wrist_bend", 6, 16, "左の手首")
    assert _found(events, "penetration.arm_body", 20, 34, "左の")
    pop = _found(events, "motion.pop", 40, 40, "右の手")
    assert pop and pop[0]["frames"] == [40, 40] and pop[0]["peak_value"] == pytest.approx(120.0, abs=0.5) and "1 コマで戻る" in pop[0]["part"]
    floor = _found(events, "penetration.floor", 50, 56)
    assert {e["part"] for e in floor} == {"左の足", "右の足"} and max(e["peak_value"] for e in floor) == pytest.approx(5.0, abs=0.2)
    assert _found(events, "motion.jitter", 62, 75, "頭")
    assert _found(events, "penetration.leg_leg", 82, 94)
    assert _found(events, "motion.position_pop", 100, 100, "左手") and _found(events, "motion.pop", 100, 100, "左の前腕")
    for event in events:                                                   # どれも要確認・測った値（採点しない）
        assert event["status"] == "review" and event["evidence"] == "measured"
        assert event["seconds"][0] == round((event["frames"][0] - 1) / FPS, 2)
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))
    assert events == sorted(events, key=lambda e: (e["frames"][0], e["signal"]))


def test_events_merge_runs_and_mark_momentary_ones():
    hits = [(5, "joint.neck", "首", 72.0, np.zeros(3), None), (6, "joint.neck", "首", 75.0, np.ones(3), None),
            (9, "joint.neck", "首", 71.0, np.zeros(3), None),                    # すき間 2 コマ（3 コマ目）はつなぐ
            (20, "joint.neck", "首", 80.0, np.zeros(3), None),                   # 離れた 1 コマ
            (30, "penetration.floor", "左の足", 0.021, np.array([0.1, 0.2, -0.021]), None)]
    events = ac.events_from(hits, list(range(1, 41)), 30.0, merge_gap=2)
    neck = [e for e in events if e["signal"] == "joint.neck"]
    assert [e["frames"] for e in neck] == [[5, 9], [20, 20]]
    assert neck[0]["peak_frame"] == 6 and neck[0]["peak_value"] == 75.0 and neck[0]["location"] == [1.0, 1.0, 1.0]
    assert neck[0]["momentary"] is False and neck[1]["momentary"] is True and neck[0]["kind"] == "静的"
    floor = next(e for e in events if e["signal"] == "penetration.floor")
    assert floor["peak_value"] == 2.1 and floor["unit"] == "cm" and floor["severity"] == round(2.1 / 1.0 * 1.0, 2)
    assert floor["seconds"] == [round(29 / 30, 2)] * 2 and floor["normal"] is None


def test_arm_points_below_the_shoulder_are_not_counted_when_the_body_is_that_wide():
    """肩幅より胸まわりが広い体では、腕を下ろしただけで二の腕が胴に重なる。その分（その体で避けられない重なり）を差し引く。"""
    wide = make_sample(_clean)
    for mesh in wide.body:
        torso = np.array([n in ("hips", "spine", "chest") for n in mesh["dominant"]])
        mesh["co"] = mesh["co"].copy()
        mesh["co"][torso, 0] *= 1.45                                        # 胴を横へ 1.45 倍（半径 0.2175 m）
    body = ac.Body(wide, ac.settings({}))
    assert body.baseline["left"][0] > 0.01 and body.baseline["right"][0] > 0.01
    assert _events(wide) == []


def test_thighs_need_a_deeper_overlap_than_the_other_leg_pairs():
    """太ももは柔らかく、閉じると重なるので、太ももどうしは片方の軸が相手の中ほどまで入った時だけ挙げる（ほかの組は leg_overlap_min_m）。"""
    def parallel(gap):
        def edit(pose, f):
            if 10 <= f <= 20:
                _move(pose, "upper_leg.L", np.array([-(0.18 - gap), 0.0, 0.0]))  # 左脚をまるごと右脚へ寄せる（脚の軸の間 gap）
        return [e for e in _events(make_sample(edit, frames=30)) if e["signal"] == "penetration.leg_leg"]
    shallow = parallel(0.035)                  # 重なり 8.5 cm: 太ももどうしの下限 9 cm（0.06 + 0.06 − 0.03）に届かない
    assert shallow and all(e["part"] != "左の太もも ↔ 右の太もも" for e in shallow)
    assert shallow[0]["peak_value"] == pytest.approx(8.5, abs=0.3)
    deep = parallel(0.02)                      # 重なり 10 cm
    assert deep and deep[0]["part"] == "左の太もも ↔ 右の太もも"


def test_unmeasured_legs_are_estimates():
    """脚の太さを測れない（その骨にいちばん重い頂点が無い）と、推定の太さで見て、その脚を使った重なりを evidence: estimated にする。"""
    sample = make_sample(_faulty)
    for mesh in sample.body:
        mesh["dominant"] = ["hips" if name == "upper_leg.L" else name for name in mesh["dominant"]]   # 左の太ももの重みが別の骨に
    report = ac.check(sample, ac.settings({}))
    assert report["leg_radius_estimated"] == ["left_upper_leg"] and report["leg_radius_m"]["left_upper_leg"] == ac.LEG_RADIUS_GUESS_M
    legs = [e for e in report["events"] if e["signal"] in ("penetration.leg_leg", "penetration.arm_leg")]
    assert {e["signal"] for e in legs if "左の太もも" in e["part"]} == {"penetration.leg_leg", "penetration.arm_leg"}
    for event in report["events"]:
        assert event["evidence"] == ("estimated" if event in legs and "左の太もも" in event["part"] else "measured")
    assert ac.check(make_sample(_faulty), ac.settings({}))["leg_radius_estimated"] == []


def test_wrist_twist_is_measured_from_the_untwisted_forearm():
    """前腕の骨が軸まわりに裏返り、手が逆に回って打ち消している時（見た目は変わらない）は、手首のねじれに数えない。"""
    def flip(pose, f):
        if f >= 30:
            _turn(pose, "lower_arm.L", pose[NAMES.index("lower_arm.L"), :3, 1], 172)
            _turn(pose, "hand.L", pose[NAMES.index("hand.L"), :3, 1], -172)
    events = _events(make_sample(flip))
    assert not [e for e in events if e["signal"] == "joint.wrist_twist"]
    assert not [e for e in events if e["signal"] == "motion.pop" and "前腕" in e["part"]]   # 筒の部位は軸まわりの回りを見ない

    def twist(pose, f):
        if f >= 30:
            _turn(pose, "hand.L", pose[NAMES.index("hand.L"), :3, 1], 150 * min(1.0, (f - 29) / 10))
    events = _events(make_sample(twist))
    twisted = [e for e in events if e["signal"] == "joint.wrist_twist"]
    assert twisted and twisted[0]["peak_value"] == pytest.approx(150.0, abs=0.5)


def test_knee_back_neck_and_cut():
    def edit(pose, f):
        if 10 <= f <= 15:
            _turn(pose, "lower_leg.R", (1, 0, 0), -30)                        # すねを前へ振る（ひざが股と足首の線より後ろ）
        if 30 <= f <= 33:
            _turn(pose, "head", (1, 0, 0), 80)                                # 首を 80° 曲げる
        if f >= 60:
            for bone in ("upper_arm.L", "upper_arm.R", "upper_leg.L", "upper_leg.R", "spine", "hand.L", "hand.R"):
                _turn(pose, bone, (1, 0, 0), 40)                              # 全身が同じコマで切り替わる
    report = ac.check(make_sample(edit), ac.settings({}))
    events = report["events"]
    assert _found(events, "joint.knee_back", 10, 15, "右のひざ")
    neck = _found(events, "joint.neck", 30, 33)
    assert neck and neck[0]["peak_value"] == pytest.approx(80.0, abs=0.5)
    assert report["cuts"] == [60] and _found(events, "motion.cut", 60, 60, "全身")
    assert not [e for e in events if e["signal"] == "motion.pop" and 59 <= e["frames"][0] <= 61]   # 切り替えの前後のコマの飛びは挙げない


def test_foot_slide_counts_only_planted_slow_steps():
    def slide(pose, f):
        _move(pose, "hips", np.array([0.015 * max(0, min(f, 50) - 40), 0.0, 0.0]))           # 腰ごと 1 コマ 1.5 cm、10 コマ滑らせる
        if 80 <= f <= 82:
            _move(pose, "hips", np.array([0.1 * (f - 79), 0.0, 0.0]))                      # 1 コマ 10 cm の踏み出しは滑りではない
    events = _events(make_sample(slide))
    slides = [e for e in events if e["signal"] == "motion.foot_slide"]
    assert {e["part"] for e in slides} == {"左の足", "右の足"}
    assert all(40 <= e["frames"][0] and e["frames"][1] <= 51 for e in slides)
    sway = 0.02 * (math.sin(2 * math.pi * 50 / 90) - math.sin(2 * math.pi * 40 / 90))     # 基本の動きの横の揺れの分
    assert slides[0]["peak_value"] == pytest.approx((0.15 + sway) * 100, abs=0.05)         # コマ 40 → 50 で動いた長さ


def test_skip_bones_and_the_visual_parent():
    events = _events(make_sample(lambda pose, f: _turn(pose, "index.L", (0, 0, 1), 60) if f == 50 else None))
    fingers = _found(events, "motion.pop", 50, 50, "左手の指")
    assert fingers and fingers[0]["severity"] == round(60.0 / 30 * 0.02, 2)        # 指は手ごとにまとめ、絵の順番では後ろ
    report = ac.check(make_sample(lambda pose, f: _turn(pose, "index.L", (0, 0, 1), 60) if f == 50 else None), ac.settings({}),
                      skip_bones=["index.*"])
    assert not _found(report["events"], "motion.pop", 50, 50, "指") and report["bones"] == len(NAMES) - 3
    # 親（手）が同じコマで飛んだ時の子（指）は挙げない
    events = _events(make_sample(lambda pose, f: _turn(pose, "hand.L", (0, 0, 1), 60) if f == 50 else None))
    assert _found(events, "motion.pop", 50, 50, "左の手") and not _found(events, "motion.pop", 50, 50, "指")


def test_spring_bones_need_a_bigger_jump():
    events = _events(make_sample(lambda pose, f: _turn(pose, "skirt", (1, 0, 0), 40) if f == 50 else None))
    assert not _found(events, "motion.pop", 50, 50, "skirt")                  # 揺れものの骨は 45° かつ 4 倍から
    events = _events(make_sample(lambda pose, f: _turn(pose, "skirt", (1, 0, 0), 50) if f == 50 else None))
    assert _found(events, "motion.pop", 50, 50, "skirt（1 コマで戻る）")


def test_a_moved_armature_keeps_lengths_and_the_floor_in_the_world():
    """アーマチュアの物体が世界で動いていても（コマごとの置き場所）、形は骨の中で比べ、床と位置は世界で見る。"""
    def slide(pose, f):
        _move(pose, "hips", np.array([0.015 * max(0, min(f, 50) - 40), 0.0, 0.0]))
    base = make_sample(slide, frames=60)
    lifted = make_sample(slide, frames=60)
    lifted.world = np.array([np.eye(4)] * 60)
    lifted.world[:, 2, 3] = 0.3                                              # アーマチュアごと 30 cm 上
    lifted.heads = lifted.heads + [0.0, 0.0, 0.3]
    lifted.foot_world = lifted.foot_world + [0.0, 0.0, 0.3]
    a, b = _events(base), _events(lifted)
    assert [(e["signal"], e["part"], e["frames"]) for e in a] == [(e["signal"], e["part"], e["frames"]) for e in b]
    assert all(ev_b["location"][2] == pytest.approx(ev_a["location"][2] + 0.3, abs=1e-4) for ev_a, ev_b in zip(a, b))
    sunk = make_sample(lambda pose, f: _move(pose, "hips", np.array([0.0, 0.0, -0.05])) if 10 <= f <= 12 else None, frames=20)
    sunk.foot_world = sunk.foot_world + [0.0, 0.0, 0.3]
    sunk.world = np.array([np.eye(4)] * 20)
    sunk.world[:, 2, 3] = 0.3
    assert not _found(_events(sunk), "penetration.floor", 10, 12)           # 世界の床（z = 0）より上


def test_a_turned_armature_reports_normals_in_the_world():
    """アーマチュアの物体が回っていると、位置と同じく、向き（normal）も世界で書く。"""
    turn = np.eye(4)
    turn[:3, :3] = _rotation((0, 0, 1), 90)
    base, turned = make_sample(_faulty), make_sample(_faulty)
    turned.world = np.array([turn] * len(turned.frames))
    turned.heads = turned.heads @ turn[:3, :3].T
    turned.foot_world = turned.foot_world @ turn[:3, :3].T

    def pressed(events):
        return [e for e in events if e["signal"].startswith("penetration.arm_") and e["normal"]]
    a, b = pressed(_events(base)), pressed(_events(turned))
    assert a and [(e["signal"], e["frames"]) for e in a] == [(e["signal"], e["frames"]) for e in b]
    for ea, eb in zip(a, b):
        assert np.allclose(eb["normal"], turn[:3, :3] @ np.array(ea["normal"]), atol=2e-3)
        assert np.allclose(eb["location"], turn[:3, :3] @ np.array(ea["location"]), atol=2e-3)


def test_foot_lowest_choice_and_missing_feet():
    sample = make_sample(lambda pose, f: _move(pose, "hips", np.array([0.0, 0.0, -0.05])) if 10 <= f <= 12 else None)
    sample.foot_object = sample.foot_object.copy()
    sample.foot_object[:, :, 2] = 0.0                                         # 物体の中の z で選んだ点は床の上（試作の選び方）
    assert _found(ac.check(sample, ac.settings({}))["events"], "penetration.floor", 10, 12)          # 既定は世界のいちばん低い点
    assert not _found(ac.check(sample, ac.settings({}), foot_lowest="object")["events"], "penetration.floor", 10, 12)
    sample.foot_world[:, 1] = np.nan
    report = ac.check(sample, ac.settings({}))
    assert [s["check"] for s in report["skipped"]] == ["floor_right"]
    with pytest.raises(ac.SettingsError, match="foot_lowest"):
        ac.check(sample, ac.settings({}), foot_lowest="lowest")
    partial = make_sample(lambda pose, f: None, frames=20)                   # 一部のコマだけ足の点が無い（頂点を足し引きする変形など）
    partial.foot_world = partial.foot_world.copy()
    partial.foot_world[[4, 5, 6, 12], 0] = np.nan
    report = ac.check(partial, ac.settings({}))
    gaps = [s for s in report["skipped"] if s["check"] == "floor_left"]
    frames = partial.frames
    assert gaps and gaps[0]["frames"] == [[frames[4], frames[6]], [frames[12], frames[12]]] and report["overall"] != "clean"


def test_head_contact_uses_the_head_surface():
    def touch(pose, f):
        if 20 <= f <= 24:
            _turn(pose, "upper_arm.L", (0, 1, 0), -135)                        # 腕を上げて手を頭へ
            _turn(pose, "lower_arm.L", (0, 1, 0), -117)
    report = ac.check(make_sample(touch), ac.settings({}))
    head = [e for e in report["events"] if e["signal"] == "penetration.arm_body" and "頭" in e["part"]]
    assert head and 20 <= head[0]["peak_frame"] <= 24
    no_head = make_sample(touch)
    no_head.body[0]["faces"] = []
    report = ac.check(no_head, ac.settings({}))
    assert report["head_triangles"] == 0 and [s["check"] for s in report["skipped"]] == ["head"]


def test_closest_on_triangles_matches_brute_force():
    rng = np.random.default_rng(3)
    tris = rng.normal(size=(40, 3, 3))
    for point in rng.normal(size=(30, 3)) * 2:
        dense = []
        for a, b, c in tris:                                                  # 三角形の上の点を細かく並べた近似
            u, v = np.meshgrid(np.linspace(0, 1, 60), np.linspace(0, 1, 60))
            keep = u + v <= 1
            pts = a + u[keep, None] * (b - a) + v[keep, None] * (c - a)
            dense.append(np.linalg.norm(pts - point, axis=1).min())
        exact = ac.closest_on_triangles(point, tris)
        assert exact <= min(dense) + 1e-9 and min(dense) - exact < 0.05
    flat = np.array([[[0, 0, 0], [1, 0, 0], [2, 0, 0]]], float)                # つぶれた三角形
    assert ac.closest_on_triangles(np.array([1.0, 1.0, 0.0]), flat) == pytest.approx(1.0, abs=1e-12)


def test_quaternion_and_rotation_between():
    for axis, degrees in (((1, 0, 0), 30), ((0, 1, 1), 170), ((1, 2, 3), -100), ((0, 0, 1), 180)):
        m = _rotation(axis, degrees)
        q = ac.quaternion(m)
        assert q[0] >= 0 and 2 * math.degrees(math.acos(min(1.0, q[0]))) == pytest.approx(abs(degrees), abs=1e-6)
    for a, b in (((1, 0, 0), (0, 1, 0)), ((1, 0, 0), (-1, 0, 0)), ((0, 0, 1), (0, 0, 1)), ((1, 2, 3), (-3, 1, 0.5))):
        r = ac.rotation_between(a, b)
        assert np.allclose(r @ (np.array(a) / np.linalg.norm(a)), np.array(b) / np.linalg.norm(b), atol=1e-9)
        assert np.allclose(r @ r.T, np.eye(3), atol=1e-9)


def test_settings_check_the_table_and_numbers():
    cfg = ac.settings({"arm_body_min_m": {"wrist": 0.05}, "pop_window": 4, "unrelated": 1})
    assert cfg["arm_body_min_m"]["wrist"] == 0.05 and cfg["arm_body_min_m"]["fist"] == 0.03 and cfg["pop_window"] == 4
    assert ac.settings({}) == ac.DEFAULTS
    for bad, message in (({"arm_upper": [[0.5, 0.04], [1.0, 0.04]]}, "3 点"), ({"arm_upper": [[0.5, 0.04], [0.4, 0.04], [1.0, 0.04]]}, "小さい順"),
                         ({"arm_lower": [[0.3, 0.03], [0.6, 0.03], [0.9, 0.03]]}, "最後の点を 1"),
                         ({"arm_body_min_m": {"shoulder": 0.1}}, "arm_body_min_m"), ({"pop_window": 2.5}, "整数"),
                         ({"floor_min_m": -1}, "0 以上"), ({"torso_weight_min": 1.5}, "0〜1"), ({"fist": [0.07]}, "fist"),
                         ({"slide_min_m_per_frame": 0.05}, "slide_min"), ({"jitter_window": 1}, "jitter_window"),
                         ({"neck_max_deg": True}, "数")):
        with pytest.raises(ac.SettingsError, match=message):
            ac.settings(bad)


def test_missing_roles_and_bad_samples_stop():
    sample = make_sample(_clean, frames=5)
    del sample.humanoid["left_foot"]
    with pytest.raises(ac.SettingsError, match="left_foot"):
        ac.check(sample, ac.settings({}))
    sample = make_sample(_clean, frames=5)
    sample.humanoid["left_toes"] = "nothing"
    with pytest.raises(ac.SettingsError, match="nothing"):
        ac.check(sample, ac.settings({}))
    with pytest.raises(ac.SettingsError, match="形"):
        ac.Sample({}, NAMES, [BONES[n][0] for n in NAMES], REST[:3], [1], FPS, np.zeros((1, len(NAMES), 4, 4)), np.zeros((1, len(NAMES), 4)),
                  np.zeros((1, len(NAMES), 3)), np.zeros((1, 2, 3)), np.zeros((1, 2, 3)), {}, [])
    no_torso = make_sample(_clean, frames=5)
    no_torso.body[0]["torso"] = np.zeros_like(no_torso.body[0]["torso"])
    with pytest.raises(ac.SettingsError, match="胴"):
        ac.check(no_torso, ac.settings({}))


# ---------------------------------------------------------------- 測る所（Blender の中の台本の小さな関数）
def test_sampler_helpers():
    names = {0: "hips", 1: "skirt_01", 2: "head"}
    assert sampler.dominant([[(0, 0.4), (1, 0.6)], [(0, 0.5), (1, 0.5)], [], [(2, 0.0)]], names) == ["skirt_01", "hips", None, None]
    children = {"hips": ["spine", "skirt_01"], "spine": ["head"], "head": ["hair_01"], "hair_01": ["hair_02"]}
    tree = sampler.descendants(children, "head")
    assert tree == {"head", "hair_01", "hair_02"}
    human = {"hips", "spine", "head"}
    assert sampler.classify(["skirt_01"] * 7 + ["hips"] * 3, human, tree) == "cloth"
    assert sampler.classify(["hair_01"] * 7 + ["head"] * 3, human, tree) == "hair"
    assert sampler.classify(["skirt_01"] * 5 + ["hips"] * 5, human, tree) == "body"
    assert sampler.classify([None] * 4, human, tree) == "body"

    class Node:
        def __init__(self, name):
            self.node = type("N", (), {"bone_name": name})()

    class HumanBones:
        hips = Node("Hips")
        spine = Node("Spine")
        left_eye = Node("")                                                   # 割り当ての無い役割は入れない
        last_bone_names = []

    extension = type("E", (), {"vrm1": type("V", (), {"humanoid": type("H", (), {"human_bones": HumanBones()})()})()})()
    armature = type("A", (), {"data": type("D", (), {"vrm_addon_extension": extension})()})()
    assert sampler.humanoid_map(armature) == {"hips": "Hips", "spine": "Spine"}
    assert sampler.humanoid_map(type("A", (), {"data": object()})()) == {}


def test_lowest_points_by_object_axis_and_by_world():
    """物体の中の z で選ぶ（試作）と、世界の z で選ぶ。物体が X のまわりに 90° 回っていると、物体の中の z は前後になる。"""
    co = np.array([[0.0, 0.10, -0.05], [0.0, -0.02, 0.03], [0.0, 0.00, 0.00]])          # 物体の中の座標
    matrix = np.eye(4)
    matrix[:3, :3] = _rotation((1, 0, 0), 90) * 1.45                          # 世界の z = 1.45 × 物体の中の y
    matrix[:3, 3] = (0.0, 0.0, 0.3)
    assert sampler.lowest_indices(co, matrix) == (0, 1)                       # 物体の中の z では頂点 0、世界の z では頂点 1
    assert sampler.lowest_indices(co, np.eye(4)) == (0, 0)                    # 回っていなければ同じ


# ---------------------------------------------------------------- 工程の受け渡し（代役の Blender）
STAND_IN = r'''
import json, os, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
source = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixture.json"), encoding="utf-8"))   # 代役の名前は OS で違う
shutil.copyfile(source["npz"], value("--out"))
meta = json.load(open(source["meta"], encoding="utf-8"))
meta["x_settings"], meta["x_argv"] = settings, sys.argv[1:]
json.dump(meta, open(value("--meta"), "w", encoding="utf-8"))
'''


def _write_sample(sample, folder):
    """Sample を、animation_sample.py が書く形（npz と JSON）にする。"""
    groups = sorted({n for mesh in sample.body for n in mesh["dominant"] if n})
    arrays = {"rest": sample.rest, "pose": sample.pose.astype(np.float32), "quat": sample.quat, "heads": sample.heads,
              "foot_object": sample.foot_object, "foot_world": sample.foot_world}
    for k, mesh in enumerate(sample.body):
        arrays[f"body{k}_co"] = mesh["co"]
        arrays[f"body{k}_dominant"] = np.array([groups.index(n) if n else -1 for n in mesh["dominant"]], dtype=np.int32)
        arrays[f"body{k}_torso"] = mesh["torso"]
        arrays[f"body{k}_face_start"] = np.cumsum([0] + [len(f) for f in mesh["faces"][:-1]]).astype(np.int32)
        arrays[f"body{k}_face_verts"] = np.concatenate([np.asarray(f) for f in mesh["faces"]]).astype(np.int32)
    np.savez_compressed(folder / "sample.npz", **arrays)
    meta = {"schema_version": "model3d-animation-sample/0.1", "blender": "5.2.4 LTS", "scene": "Scene", "armature": "Rig",
            "armature_world": np.eye(4).tolist(), "fps": sample.fps, "frames": sample.frames, "humanoid": sample.humanoid,
            "bones": sample.bones, "parents": sample.parents, "groups": groups, "deform": sample.deform,
            "body": [{"name": m["name"]} for m in sample.body], "cloth_objects": ["Skirt"], "hair_objects": []}
    atomic_json(folder / "sample.json", meta)
    atomic_json(folder / "fixture.json", {"npz": str(folder / "sample.npz"), "meta": str(folder / "sample.json")})


def test_sample_files_round_trip(tmp_path):
    sample = make_sample(_faulty)
    _write_sample(sample, tmp_path)
    loaded = ac.load_sample(tmp_path / "sample.npz", json.loads((tmp_path / "sample.json").read_text(encoding="utf-8")))
    assert ac.check(loaded, ac.settings({}))["events"] == ac.check(sample, ac.settings({}))["events"]
    with pytest.raises(ac.SettingsError, match="形が違います"):
        ac.load_sample(tmp_path / "sample.npz", {"schema_version": "other"})


def _recipe(tmp_path, parameters):
    (tmp_path / "dance.blend").write_bytes(b"blend")
    step = {"id": "check", "operation": "check.animation", "inputs": {"blend": "$inputs.dance"}, "parameters": parameters,
            "outputs": {"report": "animation.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "animation",
                                           "inputs": {"dance": {"path": "dance.blend"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_check_animation_runs_blender_and_reports_events(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    _write_sample(make_sample(_faulty), tmp_path)
    recipe = _recipe(tmp_path, {"blender": blender, "frames": [1, 120], "skip_bones": ["*twist*"], "wrist_bend_max_deg": 70})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "needs_review", result                         # 出来事があれば要確認（関門ではない）
    step = result["steps"]["check"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-animation-check/0.1" and report["blender_version"] == "5.2.4"
    assert report["thresholds"]["wrist_bend_max_deg"] == 70 and report["skip_bones"] == ["*twist*"] and report["foot_lowest"] == "world"
    assert report["armature"] == "Rig" and report["cloth_objects"] == ["Skirt"] and report["overall"] == "review"
    assert any(e["signal"] == "joint.wrist_bend" for e in report["events"])
    meta = json.loads((Path(step["artifacts"]["report"]).parent / "sample.json").read_text(encoding="utf-8"))
    assert meta["x_settings"] == {"armature": None, "frames": [1, 120]}
    argv = meta["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "animation_sample.py" and "--factory-startup" in argv
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]          # VRM Add-on を既定で有効にする
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["check"]["reused_from"] is None     # 毎回測る
    quiet = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "report_only": True}), tmp_path / "runs")
    assert quiet["status"] == "succeeded"


@pytest.mark.parametrize("parameters, message", [
    ({"strict": True}, "知らないパラメーター"), ({"frames": [10, 5]}, "frames"), ({"skip_bones": "twist"}, "skip_bones"),
    ({"foot_lowest": "lowest"}, "foot_lowest"), ({"arm_upper": [[1.0, 0.04]]}, "3 点"), ({"armature": ""}, "armature"),
    ({"report_only": "yes"}, "report_only")])
def test_check_animation_refuses_bad_parameters(tmp_path, parameters, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    _write_sample(make_sample(_clean, frames=5), tmp_path)
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["check"]["error"]
