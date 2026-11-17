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
from model3d.blender_adapter import animation_evidence as evidence
from model3d.blender_adapter import animation_sample as sampler
from model3d.blender_adapter import fault_inject
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
    assert report["events"] == [] and report["overall"] == "clean_with_skips"                      # 布のメッシュが無いので布は見ない
    assert [s["check"] for s in report["skipped"]] == ["cloth"]
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
    assert [s["check"] for s in report["skipped"]] == ["floor_right", "cloth"]
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
    assert report["head_triangles"] == 0 and [s["check"] for s in report["skipped"]] == ["head", "cloth"]


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


def _cloth_rows(rows):
    """布の当たりのまとめ（コマの位置, 骨, 布の番号, 数, 深さ, 位置, 外向き）の並びを、Sample の cloth_hits にする。"""
    return {"frame": np.array([r[0] for r in rows], int), "bone": np.array([NAMES.index(r[1]) for r in rows], int),
            "object": np.array([r[2] for r in rows], int), "count": np.array([r[3] for r in rows], int),
            "depth": np.array([r[4] for r in rows], float), "location": np.array([r[5] for r in rows], float).reshape(-1, 3),
            "normal": np.array([r[6] for r in rows], float).reshape(-1, 3)}


def test_body_through_cloth_needs_enough_vertices():
    """体が布を突き抜ける所は、(骨, 布) ごとに 22 頂点以上のコマだけ挙げ、続いたコマを 1 件にまとめる（頂点の数はいちばん多いコマ）。"""
    sample = make_sample(_clean, frames=40)
    out = [0.0, -1.0, 0.0]
    rows = [(9, "upper_leg.R", 0, 30, 0.059, [-0.1, -0.1, 0.7], out), (10, "upper_leg.R", 0, 25, 0.041, [-0.1, -0.1, 0.71], out),
            (12, "upper_leg.R", 0, 40, 0.02, [-0.1, -0.1, 0.72], [np.nan] * 3),     # すき間 1 コマ（コマ 12 は挙がる）はつなぐ
            (20, "upper_leg.L", 1, 21, 0.09, [0.1, -0.1, 0.7], out),                   # 21 頂点（小さい所）は挙げない
            (30, "hips", 1, 22, 0.012, [0.0, 0.1, 0.95], out)]
    sample.cloth = ["Skirt", "Sash"]
    sample.cloth_hits = _cloth_rows(rows)
    events = [e for e in ac.check(sample, ac.settings({}))["events"] if e["signal"] == "penetration.body_cloth"]
    assert [(e["part"], e["frames"]) for e in events] == [("右の太もも → Skirt", [10, 13]), ("腰 → Sash", [31, 31])]
    first = events[0]
    assert first["peak_frame"] == 10 and first["peak_value"] == 5.9 and first["vertices"] == 40 and first["normal"] == out
    assert first["location"] == [-0.1, -0.1, 0.7] and first["title"] == "体が布を突き抜ける" and first["kind"] == "静的"
    assert ac.check(sample, ac.settings({"cloth_min_vertices": 50}))["events"] == []
    # 布の当たりの番号が範囲の外なら止める
    sample.cloth_hits["object"][0] = 5
    with pytest.raises(ac.SettingsError, match="布の当たり"):
        ac.Sample(sample.humanoid, sample.bones, sample.parents, sample.rest, sample.frames, sample.fps, sample.pose, sample.quat,
                  sample.heads, sample.foot_object, sample.foot_world, sample.deform, sample.body, cloth=sample.cloth,
                  cloth_hits=sample.cloth_hits)


def test_cloth_skips_are_reported():
    sample = make_sample(_clean, frames=5)
    sample.cloth = ["Skirt"]
    sample.meta = {"cloth_measured": False}
    assert [s["reason"] for s in ac.check(sample, ac.settings({}))["skipped"]] == ["cloth: false（体が布を突き抜けるは見ない）"]
    sample.meta = {"cloth_measured": True, "cloth_watched": 0, "cloth_tucked": 3}
    report = ac.check(sample, ac.settings({}))
    assert "近くにある体の頂点がありません" in report["skipped"][0]["reason"] and report["cloth"]["tucked_vertices"] == 3
    sample.meta = {"cloth_measured": True, "cloth_watched": 120, "cloth_tucked": 3}
    report = ac.check(sample, ac.settings({}))
    assert report["skipped"] == [] and report["overall"] == "clean" and report["cloth"]["watched_vertices"] == 120
    sample.meta = {"cloth_measured": True, "cloth_watched": 120, "cloth_tucked": 3, "cloth_unwatched": {"Body": [2, 3, 5]}}
    report = ac.check(sample, ac.settings({}))                               # 頂点の数が変わるコマは見ない（番号が合わない）
    assert report["skipped"] == [{"check": "cloth", "reason": report["skipped"][0]["reason"], "objects": ["Body"], "frames": [[2, 3], [5, 5]]}]
    assert "頂点の数が変わるコマ" in report["skipped"][0]["reason"] and report["overall"] == "clean_with_skips"


def test_evidence_picks_the_largest_events_but_not_cuts():
    events = [{"id": 1, "signal": "motion.cut", "severity": 1e-7}, {"id": 2, "signal": "joint.neck", "severity": 2.5},
              {"id": 3, "signal": "penetration.floor", "severity": 4.0}, {"id": 4, "signal": "motion.pop", "severity": 2.5}]
    assert [e["id"] for e in ac.evidence_events(events, 30)] == [3, 2, 4]          # 同じ severity は報告の順
    assert [e["id"] for e in ac.evidence_events(events, 2)] == [3, 2]


def test_table_lists_the_summary_and_the_events():
    report = ac.check(make_sample(_faulty), ac.settings({}))
    report["blend"] = "dance.blend"
    report["events"][0]["images"] = ["e001_f0006.jpg", "e001_f0007.jpg"]
    lines = ac.table_markdown(report).splitlines()
    assert lines[0] == "# 動きの検品の報告" and lines[2] == "- 調べた物: `dance.blend`" and lines[3] == "- コマ: 1〜120（30 fps、4.00 秒）"
    assert lines[6].startswith("- 浅い接触（1〜3 cm。出来事には挙げていない）のコマ数: ")
    assert "| 番号 | 秒 | コマ | 種類 | 見つけた物 | 場所 | いちばん大きい所 | 絵 |" in lines
    first = next(line for line in lines if line.startswith("| 1 | "))
    assert first.endswith("| `img/e001_f0006.jpg` `img/e001_f0007.jpg` |")
    assert lines[-3:-1] == ["## 見なかった検査", ""] and lines[-1].startswith("- cloth: ")     # 布が無いので見なかった
    summary = lines[lines.index("## まとめ（見つけた物と場所ごと）") + 4]
    biggest = max(report["events"], key=lambda e: e["severity"])
    assert summary.startswith(f"| {biggest['title']} | {biggest['part']} | ")


def test_contact_sheets_put_six_cells_in_a_row(tmp_path):
    from PIL import Image
    events = []
    for k in range(1, 13):
        names = [evidence.image_name(k, 10 * k + d) for d in (-1, 0, 1)] if k % 2 else [evidence.image_name(k, 10 * k)]
        for name in names:
            Image.new("RGB", (48, 48), (10 * k, 100, 200)).save(tmp_path / name)
        events.append({"id": k, "images": names, "peak_frame": 10 * k, "signal": "motion.pop", "peak_value": 31.0, "unit": "度",
                       "kind": "動的" if k % 2 else "静的", "frames": [10 * k, 10 * k + 1]})
    report = {"events": events, "frames": [1, 200], "fps": 30.0}
    sheets = ac.contact_sheets(report, tmp_path, columns=6, rows=5, cell=100)
    assert len(sheets) == 1 and sheets[0].size == (600, 4 * 160)                    # 24 枚 = 6 列 × 4 段
    sheets = ac.contact_sheets(report, tmp_path, columns=4, rows=2, cell=100)
    assert [sheet.size for sheet in sheets] == [(400, 320)] * 3                       # 8 枚ずつ 3 枚
    assert np.allclose(sheets[0].getpixel((50, 50)), (10, 100, 200), atol=4)                    # 1 枚目の絵（JPEG の揺れを許す）


def test_evidence_camera_and_shots():
    assert evidence.shots({"peak_frame": 10, "kind": "動的"}, 1, 100) == [9, 10, 11]
    assert evidence.shots({"peak_frame": 1, "kind": "動的"}, 1, 100) == [1, 2]       # 範囲の外のコマは描かない
    assert evidence.shots({"peak_frame": 10, "kind": "静的"}, 1, 100) == [10]
    assert evidence.image_name(7, 12) == "e007_f0012.jpg"
    for frame in (12, 10000, 123456, -53):                                       # コマの番号は桁の数によらず読み戻せる
        assert ac.evidence_frame(evidence.image_name(7, frame)) == frame
    # 場所の外向き（無ければ腰から場所へ）と、体の前と少し上から。静的 0.95 m 離れる。床すれすれより下からは撮らない
    place = evidence.camera_place([1.0, 0.0, 1.0], None, P(0.0, 0.0, 1.0), P(0.0, -1.0, 0.0), "静的", P)
    toward = np.array([0.9, -0.5, 0.25])
    assert np.allclose(place.v, np.array([1.0, 0.0, 1.0]) + toward / np.linalg.norm(toward) * 0.95)
    place = evidence.camera_place([0.0, 0.0, 0.05], [0.0, 0.0, -1.0], P(0.0, 0.0, 1.0), P(0.0, -1.0, 0.0), "動的", P)
    assert place.v[2] == pytest.approx(evidence.CAMERA_LOWEST_M)


EXPECTED = {"faults": [
    {"name": "左手首を 95° 曲げる", "frames": [6, 16], "signals": ["joint.wrist_bend"], "part": ["左"]},
    {"name": "垂らした左腕を体の中へ振る", "frames": [20, 34], "signals": ["penetration.arm_body"], "part": ["左"]},
    {"name": "右手を 1 コマだけ 120° ねじる", "frames": [40, 40], "signals": ["motion.pop"], "part": ["右の手"]},
    {"name": "腰を 5 cm 下げる", "frames": [50, 56], "signals": ["penetration.floor"], "part": ["足"]},
    {"name": "頭を 1 コマおきに ±5° 揺らす", "frames": [62, 75], "signals": ["motion.jitter"], "part": ["頭"]},
    {"name": "左脚を内へ振って右脚に重ねる", "frames": [82, 94], "signals": ["penetration.leg_leg"], "part": ["左"]},
    {"name": "左の前腕を 1 コマだけ 40° 振る", "frames": [100, 100], "signals": ["motion.position_pop", "motion.pop"], "part": ["左"]},
    {"name": "入れていない破綻", "frames": [110, 112], "signals": ["joint.neck"], "part": []}]}


def test_expected_faults_are_matched_with_events():
    report = ac.check(make_sample(_faulty), ac.settings({}))
    matched = ac.match_expected(report["events"], ac.expected_faults(EXPECTED))
    assert matched["total"] == 8 and matched["found"] == 7
    assert [bool(row["found"]) for row in matched["faults"]] == [True] * 7 + [False]
    ids = {i for row in matched["faults"] for i in row["found"]}
    assert sorted(ids | set(matched["others"])) == [e["id"] for e in report["events"]] and not ids & set(matched["others"])
    report["expected"] = matched
    lines = ac.expected_markdown(report).splitlines()
    assert lines[0] == "# わざと入れた破綻と、見つけた出来事の照らし合わせ" and lines[2].startswith("| 入れた破綻 | 入れたコマ |")
    assert lines[4].startswith("| 左手首を 95° 曲げる | 6〜16 | joint.wrist_bend | 見つけた | #")
    assert lines[11] == "| 入れていない破綻 | 110〜112 | joint.neck | **見落とし** | — |"
    assert "入れた 8 件のうち、見つけたのは 7 件。" in lines and f"## 入れていない所で挙がった出来事（{len(matched['others'])} 件）" in lines
    for bad in ({}, {"faults": []}, {"faults": [{"name": "x", "frames": [3, 1], "signals": ["joint.neck"]}]},
                {"faults": [{"name": "x", "frames": [1, 3], "signals": ["joint.elbow"]}]},
                {"faults": [{"name": "x", "frames": [1, 3], "signals": ["joint.neck"], "part": "左"}]}):
        with pytest.raises(ac.SettingsError, match="expected"):
            ac.expected_faults(bad)


def test_fault_amounts():
    assert fault_inject.envelope(5, 6, 16) == 0.0 and fault_inject.envelope(17, 6, 16) == 0.0
    assert fault_inject.envelope(6, 6, 16) == pytest.approx((1 / 3) ** 2 * (3 - 2 / 3))     # 3 コマでなめらかに入る
    assert fault_inject.envelope(8, 6, 16) == 1.0 and fault_inject.envelope(16, 6, 16) == fault_inject.envelope(6, 6, 16)
    assert fault_inject.envelope(270, 270, 282, 6) == pytest.approx((1 / 6) ** 2 * (3 - 2 / 6))
    ramp = {"frames": [6, 16]}
    assert fault_inject.amount(ramp, 11) == 1.0 and fault_inject.amount({**ramp, "ramp": 6}, 7) < 1.0
    assert fault_inject.amount({"frames": [160, 160], "shape": "constant"}, 160) == 1.0
    assert [fault_inject.amount({"frames": [1, 4], "shape": "alternate"}, f) for f in (1, 2, 3, 4)] == [1.0, -1.0, 1.0, -1.0]
    assert fault_inject.resolve_bones([{"role": "left_hand"}, {"bone": "hips"}], {"left_hand": "hand.L"}, {"hand.L", "hips"}) == ["hand.L", "hips"]
    with pytest.raises(SystemExit, match="faults\\[0\\]"):
        fault_inject.resolve_bones([{"role": "left_toes"}], {}, {"hips"})


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


class V:
    """mathutils の Vector の代役（布の光線の計算に使う所だけ）。"""

    def __init__(self, *values):
        self.v = np.array(values[0] if len(values) == 1 else values, dtype=float)

    def __add__(self, other):
        return V(self.v + other.v)

    def __sub__(self, other):
        return V(self.v - other.v)

    def __mul__(self, k):
        return V(self.v * k)

    def __truediv__(self, k):
        return V(self.v / k)

    def __iadd__(self, other):
        self.v = self.v + other.v
        return self

    def __isub__(self, other):
        self.v = self.v - other.v
        return self

    def dot(self, other):
        return float(self.v @ other.v)

    @property
    def length(self):
        return float(np.linalg.norm(self.v))

    @property
    def length_squared(self):
        return float(self.v @ self.v)


class P(V):
    """mathutils の Vector の代役（x・y・z と normalized も。証拠の絵のカメラの計算に使う所）。"""

    def __add__(self, other):
        return P(self.v + other.v)

    def __sub__(self, other):
        return P(self.v - other.v)

    def __mul__(self, k):
        return P(self.v * k)

    def normalized(self):
        return P(self.v / np.linalg.norm(self.v))

    x = property(lambda self: self.v[0])
    y = property(lambda self: self.v[1])

    @property
    def z(self):
        return self.v[2]

    @z.setter
    def z(self, value):
        self.v[2] = value


class Wall:
    """布の代役: x = at の平らな布（面の番号は 0）。光線の当たりと、いちばん近い所までの距離だけ（mathutils の BVHTree の代わり）。"""

    def __init__(self, at):
        self.at = at

    def ray_cast(self, origin, direction, distance):
        if abs(direction.v[0]) < 1e-12:
            return None, None, None, None
        t = (self.at - origin.v[0]) / direction.v[0]
        if 0 <= t <= distance:
            return origin + direction * t, V(1.0, 0.0, 0.0), 0, t
        return None, None, None, None

    def find_nearest(self, point):
        return V(self.at, point.v[1], point.v[2]), V(1.0, 0.0, 0.0), 0, abs(point.v[0] - self.at)


def test_cloth_rays_from_the_body_toward_the_bone():
    """体の頂点から骨の軸へ向けた光線が、布に深さ cloth_depth_min_m 以上で当たる（体が布の外へ出ている）所を数える。"""
    segment = lambda name: (V(0.09, 0.0, 1.0), V(0.09, 0.0, 0.5))                 # 太ももの骨（x = 0.09 の縦の線）
    assert sampler.toward_bone(V(0.095, 0.0, 0.7), *segment("t")) is None          # 骨の軸から 1 cm 未満は見ない
    origin, direction, length = sampler.toward_bone(V(0.15, 0.0, 0.7), *segment("t"))
    assert np.allclose(direction.v, [-1.0, 0.0, 0.0]) and origin.v[0] == pytest.approx(0.1495) and length == pytest.approx(0.054)
    wall = Wall(0.13)                                                              # 布は、頂点と骨の軸の間（体が布の外へ出ている）
    points = [V(0.15, 0.0, 0.7), V(0.16, 0.0, 0.7), V(0.135, 0.0, 0.7), V(0.12, 0.0, 0.7)]
    found = sampler.through_cloth(points, ["thigh.R"] * 4, segment, wall, ["Skirt"], 0.008, lambda: V(0.0, 0.0, 0.0))
    (key, (count, depth, position, outward)), = found.items()
    assert key == ("thigh.R", "Skirt") and count == 2 and depth == pytest.approx(0.0295)  # 5 mm と布より内の頂点は数えない
    assert np.allclose(position.v / count, [0.155, 0.0, 0.7]) and np.allclose(outward.v, [2.0, 0.0, 0.0])
    more = sampler.through_cloth([V(0.17, 0.0, 0.7)], ["thigh.R"], segment, wall, ["Skirt"], 0.008, lambda: V(0.0, 0.0, 0.0), found)
    assert more is found and found[("thigh.R", "Skirt")][0] == 3                  # 同じコマのほかのメッシュの頂点も同じ所へ足す
    # 初期姿勢: 布の近くの頂点だけを見る。もう布の外にある頂点（作りとしてそうなっている所）は見ない
    names = ["thigh.R", "thigh.R", "thigh.R", "head", "skirt_01"]
    rest = [V(0.12, 0.0, 0.7), V(0.15, 0.0, 0.7), V(0.25, 0.0, 0.7), V(0.12, 0.0, 0.7), V(0.12, 0.0, 0.7)]
    picked, tucked = sampler.watched(rest, names, {"thigh.R", "head"}, {"head"}, segment, wall, 0.06, 0.008)
    assert picked == [0] and tucked == 1                                           # 0.15 は布の外（深さ 2 cm）、0.25 は遠い


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
script = sys.argv[sys.argv.index("--python") + 1]
if script.endswith("fault_inject.py"):                           # わざと入れる破綻: 入力を写し、入れたコマを報告に書く
    shutil.copyfile(value("--blend"), value("--out"))
    faults = [{"name": f["name"], "bone": f.get("bone") or "hand.L",
               "keyed_frames": [k for k in range(f["frames"][0], f["frames"][1] + 1) if k <= 120]} for f in settings["faults"]]
    json.dump({"blender": "5.2.4", "armature": "Rig", "faults": faults, "x_settings": settings},
              open(value("--report"), "w", encoding="utf-8"))
    sys.exit(0)
if script.endswith("animation_evidence.py"):                     # 証拠の絵: 出来事ごとに、描くコマの絵を書く
    import os
    from PIL import Image
    os.makedirs(settings["directory"], exist_ok=True)
    images = {}
    for event in settings["events"]:
        peak = event["peak_frame"]
        frames = [peak - 1, peak, peak + 1] if event["kind"] == "動的" else [peak]
        names = ["e%03d_f%04d.jpg" % (event["id"], f) for f in frames if settings["frames"][0] <= f <= settings["frames"][1]]
        for name in names:
            Image.new("RGB", (settings["resolution"],) * 2, (90, 90, 90)).save(os.path.join(settings["directory"], name))
        images[str(event["id"])] = names
    json.dump({"images": images, "render_changes": {"x_settings": settings}}, open(value("--report"), "w", encoding="utf-8"))
    sys.exit(0)
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
    arrays.update({f"cloth_{key}": value for key, value in sample.cloth_hits.items()})
    np.savez_compressed(folder / "sample.npz", **arrays)
    meta = {"schema_version": "model3d-animation-sample/0.1", "blender": "5.2.4 LTS", "scene": "Scene", "armature": "Rig",
            "armature_world": np.eye(4).tolist(), "fps": sample.fps, "frames": sample.frames, "humanoid": sample.humanoid,
            "bones": sample.bones, "parents": sample.parents, "groups": groups, "deform": sample.deform,
            "body": [{"name": m["name"]} for m in sample.body], "cloth_objects": sample.cloth, "hair_objects": [],
            "cloth_measured": bool(sample.cloth), "cloth_watched": 50 if sample.cloth else 0, "cloth_tucked": 0}
    atomic_json(folder / "sample.json", meta)
    atomic_json(folder / "fixture.json", {"npz": str(folder / "sample.npz"), "meta": str(folder / "sample.json")})


def test_sample_files_round_trip(tmp_path):
    sample = make_sample(_faulty)
    sample.cloth = ["Skirt"]
    sample.cloth_hits = _cloth_rows([(40, "upper_leg.R", 0, 30, 0.03, [-0.1, -0.1, 0.7], [0.0, -1.0, 0.0])])
    _write_sample(sample, tmp_path)
    loaded = ac.load_sample(tmp_path / "sample.npz", json.loads((tmp_path / "sample.json").read_text(encoding="utf-8")))
    assert ac.check(loaded, ac.settings({}))["events"] == ac.check(sample, ac.settings({}))["events"]
    with pytest.raises(ac.SettingsError, match="形が違います"):
        ac.load_sample(tmp_path / "sample.npz", {"schema_version": "other"})


def _recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "dance.blend").write_bytes(b"blend")
    step = {"id": "check", "operation": "check.animation", "inputs": {"blend": "$inputs.dance"}, "parameters": parameters,
            "outputs": {"report": "animation.json", **(outputs or {})}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "animation",
                                           "inputs": {"dance": {"path": "dance.blend"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_check_animation_runs_blender_and_reports_events(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    faulty = make_sample(_faulty)
    faulty.cloth = ["Skirt"]
    _write_sample(faulty, tmp_path)
    recipe = _recipe(tmp_path, {"blender": blender, "frames": [1, 120], "skip_bones": ["*twist*"], "wrist_bend_max_deg": 70,
                                "cloth_near_m": 0.05})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "needs_review", result                         # 出来事があれば要確認（関門ではない）
    step = result["steps"]["check"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-animation-check/0.1" and report["blender_version"] == "5.2.4"
    assert report["thresholds"]["wrist_bend_max_deg"] == 70 and report["skip_bones"] == ["*twist*"] and report["foot_lowest"] == "world"
    assert report["armature"] == "Rig" and report["cloth_objects"] == ["Skirt"] and report["overall"] == "review"
    assert any(e["signal"] == "joint.wrist_bend" for e in report["events"])
    meta = json.loads((Path(step["artifacts"]["report"]).parent / "sample.json").read_text(encoding="utf-8"))
    assert meta["x_settings"] == {"armature": None, "frames": [1, 120], "cloth": True, "cloth_near_m": 0.05, "cloth_depth_min_m": 0.008}
    argv = meta["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "animation_sample.py" and "--factory-startup" in argv
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]          # VRM Add-on を既定で有効にする
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["check"]["reused_from"] is None     # 毎回測る
    quiet = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "report_only": True}), tmp_path / "runs")
    assert quiet["status"] == "succeeded"


def test_check_animation_draws_evidence_and_writes_the_table(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    _write_sample(make_sample(_faulty), tmp_path)
    recipe = _recipe(tmp_path, {"blender": blender, "max_images": 3}, outputs={"table": "report.md", "sheet": "events.jpg"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "needs_review", result
    artifacts = result["steps"]["check"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    drawn = [e for e in report["events"] if e.get("images")]
    assert len(drawn) == 3 and report["images_directory"] == "img" and report["sheets"] == ["events.jpg"]
    biggest = ac.evidence_events(report["events"], 3)
    assert [e["id"] for e in drawn] == sorted(e["id"] for e in biggest)
    settings = report["render_changes"]["x_settings"]
    assert settings["resolution"] == 480 and settings["quality"] == 88 and settings["hips"] == "hips" and settings["frames"] == [1, 120]
    step_dir = Path(artifacts["report"]).parent
    assert all((step_dir / "img" / name).exists() for e in drawn for name in e["images"])
    table = Path(artifacts["table"]).read_text(encoding="utf-8")
    assert "`img/" in table and table.startswith("# 動きの検品の報告")
    from PIL import Image
    with Image.open(artifacts["sheet"]) as sheet:
        assert sheet.size[0] == 6 * 300
    for outputs, message in (({"table": "report.txt"}, ".md"), ({"sheet": "events.png"}, ".jpg")):
        failed = executor.run_recipe(_recipe(tmp_path, {"blender": blender}, outputs=outputs), tmp_path / "runs")
        assert failed["status"] == "failed" and message in failed["steps"]["check"]["error"]


FAULTS = [{"name": "左手首を曲げる", "role": "left_hand", "frames": [6, 16], "kind": "local", "axis": [1, 0, 0], "degrees": 95,
           "expect": {"signals": ["joint.wrist_bend"], "part": ["左"]}},
          {"name": "腰を下げる", "bone": "hips", "frames": [130, 135], "kind": "shift", "delta": [0, 0, -0.05], "ramp": 2,
           "expect": {"signals": ["penetration.floor"], "part": ["足"]}}]


def _inject_recipe(tmp_path, parameters):
    (tmp_path / "clean.blend").write_bytes(b"blend")
    step = {"id": "inject", "operation": "motion.inject_faults", "inputs": {"blend": "$inputs.clean"}, "parameters": parameters,
            "outputs": {"blend": "faulty.blend", "expected": "expected.json", "report": "inject.json"}}
    atomic_json(tmp_path / "inject.json", {"schema_version": "model3d-recipe/0.1", "name": "inject",
                                           "inputs": {"clean": {"path": "clean.blend"}}, "steps": [step]})
    return tmp_path / "inject.json"


def test_inject_faults_writes_the_blend_and_the_expected_list(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_inject_recipe(tmp_path, {"blender": blender, "faults": FAULTS}), tmp_path / "runs")
    assert result["status"] == "needs_review", result                      # 2 つ目はシーンのコマの外で入らない（代役は 120 コマまで）
    artifacts = result["steps"]["inject"]["artifacts"]
    expected = json.loads(Path(artifacts["expected"]).read_text(encoding="utf-8"))
    assert expected["source"].endswith("clean.blend")
    assert expected["faults"] == [{"name": "左手首を曲げる", "frames": [6, 16], "signals": ["joint.wrist_bend"], "part": ["左"]},
                                  {"name": "腰を下げる", "frames": [130, 135], "signals": ["penetration.floor"], "part": ["足"]}]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["blender_version"] == "5.2.4" and "expect" not in report["x_settings"]["faults"][0]
    assert report["x_settings"]["faults"][0]["role"] == "left_hand" and Path(artifacts["blend"]).read_bytes() == b"blend"
    result = executor.run_recipe(_inject_recipe(tmp_path, {"blender": blender, "faults": FAULTS[:1]}), tmp_path / "runs")
    assert result["status"] == "succeeded"


@pytest.mark.parametrize("change, message", [
    ({"kind": "twist"}, "kind"), ({"bone": "hand.L"}, "どちらか 1 つ"), ({"frames": [16, 6]}, "frames"), ({"axis": "X"}, "local の axis"),
    ({"kind": "world", "axis": [1, 0, 0]}, "world の axis"), ({"degrees": None}, "degrees"), ({"shape": "wave"}, "shape"), ({"ramp": 0}, "ramp"),
    ({"expect": {"signals": ["joint.elbow"]}}, "expect"), ({"name": ""}, "name"), ({"spin": 1}, "知らない鍵"),
    ({"kind": "shift", "delta": [0, 0, 0.1]}, "shift は delta")])
def test_inject_faults_refuses_bad_faults(tmp_path, change, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    fault = {key: value for key, value in {**FAULTS[0], **change}.items() if value is not None}
    result = executor.run_recipe(_inject_recipe(tmp_path, {"blender": blender, "faults": [fault]}), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["inject"]["error"]
    twice = executor.run_recipe(_inject_recipe(tmp_path, {"blender": blender, "faults": [FAULTS[0], FAULTS[0]]}), tmp_path / "runs")
    assert twice["status"] == "failed" and "重なっています" in twice["steps"]["inject"]["error"]


def test_check_animation_matches_the_expected_faults(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    _write_sample(make_sample(_faulty), tmp_path)
    atomic_json(tmp_path / "expected.json", EXPECTED)
    recipe = _recipe(tmp_path, {"blender": blender}, outputs={"expected_table": "check.md"})
    data = json.loads(recipe.read_text(encoding="utf-8"))
    data["inputs"]["expected"] = {"path": "expected.json"}
    data["steps"][0]["inputs"]["expected"] = "$inputs.expected"
    atomic_json(recipe, data)
    result = executor.run_recipe(recipe, tmp_path / "runs")
    artifacts = result["steps"]["check"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["expected"]["found"] == 7 and report["expected"]["total"] == 8
    assert result["steps"]["check"]["observations"][0]["expected"] == {"found": 7, "total": 8}
    assert Path(artifacts["expected_table"]).read_text(encoding="utf-8").startswith("# わざと入れた破綻と、見つけた出来事の照らし合わせ")
    alone = executor.run_recipe(_recipe(tmp_path, {"blender": blender}, outputs={"expected_table": "check.md"}), tmp_path / "runs")
    assert alone["status"] == "failed" and "expected" in alone["steps"]["check"]["error"]


@pytest.mark.parametrize("parameters, message", [
    ({"strict": True}, "知らないパラメーター"), ({"frames": [10, 5]}, "frames"), ({"skip_bones": "twist"}, "skip_bones"),
    ({"foot_lowest": "lowest"}, "foot_lowest"), ({"arm_upper": [[1.0, 0.04]]}, "3 点"), ({"armature": ""}, "armature"),
    ({"report_only": "yes"}, "report_only"), ({"cloth": 1}, "cloth"), ({"cloth_min_vertices": 2.5}, "整数"),
    ({"max_images": 0}, "max_images")])
def test_check_animation_refuses_bad_parameters(tmp_path, parameters, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    _write_sample(make_sample(_clean, frames=5), tmp_path)
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["check"]["error"]
