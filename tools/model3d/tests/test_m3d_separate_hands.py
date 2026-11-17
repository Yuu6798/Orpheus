"""腕どうしを離す（motion.separate_hands）: 棒の距離・重なり・ならし・向き・腕を回す計算・すれ違いの手当て（Blender なし）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import separate_hands as sh
from model3d.io import ToolError, atomic_json

BONES = {"sides": ["L", "R"], "upper": "upper_arm.{side}", "forearm": "lower_arm.{side}", "hand": "hand.{side}",
         "finger": "{finger}_{part}.{side}", "torso": ["hips", "spine", "chest"], "head": "head"}


def test_closest_points_between_segments():
    p1, q1 = np.array([[0.0, 0, 0], [0, 0, 0]]), np.array([[1.0, 0, 0], [1, 0, 0]])
    p2, q2 = np.array([[0.5, -1, 0.2], [2, 1, 0]]), np.array([[0.5, 1, 0.2], [3, 1, 0]])
    a, b = sh.closest(p1, q1, p2, q2)
    assert a[0] == pytest.approx([0.5, 0, 0]) and b[0] == pytest.approx([0.5, 0, 0.2])     # 交差する 2 本
    assert a[1] == pytest.approx([1, 0, 0]) and b[1] == pytest.approx([2, 1, 0])           # 端どうし


# 体の太さ（合成の腕の値。工程では必須）
SIZES = {"forearm_radius": 0.025, "soft_from": 0.035, "palm_radius": 0.012, "finger_radius": 0.007, "head_radius": 0.1, "torso_radius": 0.09}


def separator(profile=None, **overrides):
    bones = overrides.pop("bones", BONES)
    s = {**sh.DEFAULTS, **SIZES, "bones": bones, **overrides}
    profile = profile or {side: [(k / 5, (k + 1) / 5, 0.03, k < 3) for k in range(5)] for side in ("L", "R")}
    plan = sh.Separator(s, sh.Names(bones), list(range(1, 21)), profile, bones["torso"], "head")
    plan.fps = 30.0
    names = plan.names
    plan.arm_bones = {side: {names.upper(side), names.forearm(side), names.hand(side)}
                      | {names.finger(side, f, p) for f in names.fingers for p in names.parts} for side in names.sides}
    return plan


def arms(n=20, gap=0.0):
    """左右の腕の点の通り道: 前腕どうしが体の前で X に交差する（左の前腕は y = gap）。"""
    pos = {}
    for side, x, sign in (("L", 0.2, 1.0), ("R", -0.2, -1.0)):
        shoulder = np.array([x, 0.0, 1.4])
        elbow = np.array([x, -0.25, 1.15])
        wrist = np.array([-sign * 0.05, -0.3 + (gap if side == "L" else 0.0), 1.2])
        for bone, head, tail in ((f"upper_arm.{side}", shoulder, elbow), (f"lower_arm.{side}", elbow, wrist), (f"hand.{side}", wrist, wrist + [-sign * 0.08, 0, 0])):
            pos[(bone, "head")], pos[(bone, "tail")] = np.tile(head, (n, 1)), np.tile(tail, (n, 1))
        for f in sh.FINGERS:
            for k, p in enumerate(sh.PARTS):
                start = wrist + [-sign * (0.08 + 0.02 * k), 0.01 * sh.FINGERS.index(f), 0]
                pos[(f"{f}_{p}.{side}", "head")], pos[(f"{f}_{p}.{side}", "tail")] = np.tile(start, (n, 1)), np.tile(start + [-sign * 0.02, 0, 0], (n, 1))
    for b, z in (("hips", 1.0), ("spine", 1.1), ("chest", 1.25), ("head", 1.55)):
        pos[(b, "head")], pos[(b, "tail")] = np.tile([0, 0.05, z], (n, 1)), np.tile([0, 0.05, z + 0.1], (n, 1))
    return pos


def test_crossed_forearms_overlap_and_measure_finds_the_pair():
    plan = separator()
    pos = plan.add_forearm(arms())
    overlap, pl, pr, need, which = plan.measure(pos)
    assert overlap.min() > 0.03 and "forearm" in which[0]                   # 太さ 3 cm の前腕が同じ所で交差
    far = plan.add_forearm(arms(gap=0.2))
    assert plan.measure(far)[0].max() == 0.0


def test_soft_sleeves_may_come_closer():
    plan = separator(soft_factor=0.5)
    profile_soft = {side: [(k / 5, (k + 1) / 5, 0.03, True) for k in range(5)] for side in ("L", "R")}
    soft = separator(profile_soft, soft_factor=0.5)
    pos = plan.add_forearm(arms(gap=0.05))
    assert soft.measure(pos)[3][0] < plan.measure(pos)[3][0]               # 袖どうしは要る離れが小さい


def test_hard_only_leaves_out_sleeve_pairs():
    profile_soft = {side: [(k / 5, (k + 1) / 5, 0.03, True) for k in range(5)] for side in ("L", "R")}
    plan = separator(profile_soft)
    pos = plan.add_forearm(arms())
    full, _, _, _, which = plan.measure(pos)
    hard, _, _, _, hard_which = plan.measure(pos, hard_only=True)
    assert all(str(w).count("(袖)") == 2 for w in which)                    # 交差した前腕どうし（どちらも袖）がいちばん大きい
    assert np.all(hard <= full) and hard.max() < full.max()
    assert not any(str(w).count("(袖)") == 2 for w in hard_which if w is not None)   # 袖どうしの組は数えない


def test_smoothing_spreads_the_peak_but_never_across_a_cut():
    plan = separator(cuts=[11], dilate=1, sigma=1.0)
    x = np.zeros(20)
    x[9] = 1.0
    out = plan.smooth_amount(x)
    assert out[9] > 0 and out[8] > 0 and out[10:].max() == 0.0             # 11 コマ目（番号 10）からは別の区間


def test_rotating_the_arm_about_the_shoulder_keeps_its_shape():
    plan = separator()
    pos = plan.add_forearm(arms())
    omega = np.zeros((20, 3))
    omega[:, 2] = 0.3                                                       # 縦の軸のまわりに 0.3 ラジアン
    turned = plan.rotate_arm(pos, "L", omega)
    shoulder = pos[("upper_arm.L", "head")]
    for key in (("hand.L", "head"), ("lower_arm.L", "head"), ("forearm", "L", 0.4)):
        assert np.linalg.norm(turned[key] - shoulder, axis=1) == pytest.approx(np.linalg.norm(pos[key] - shoulder, axis=1))
    assert np.array_equal(turned[("hand.R", "head")], pos[("hand.R", "head")])   # 反対の腕は動かない
    assert not np.allclose(turned[("hand.L", "head")], pos[("hand.L", "head")])


def test_directions_take_one_side_for_each_run():
    plan = separator()
    pos = plan.add_forearm(arms())
    overlap, pl, pr, need, which = plan.measure(pos)
    direction, sides = plan.directions(pos, overlap, pl - pr)
    assert len(sides) == 1 and np.linalg.norm(direction, axis=1) == pytest.approx(np.ones(20))


def test_detour_finds_a_way_past_a_short_crossing():
    # 指を減らした骨組み（棒の組が少ないので速い。すれ違いの手当ての計算は同じ）
    plan = separator(bones={**BONES, "fingers": ["thumb", "index"], "parts": ["proximal"]}, detour_max=0.2, threshold=0.003)
    base = arms(gap=0.15)
    pos = plan.add_forearm({k: v.copy() for k, v in base.items()})
    for key in [k for k in pos if k[0] != "forearm" and k[0].endswith(".L") and not k[0].startswith("upper_arm")]:
        pos[key][9:11, 1] -= 0.15                                           # 2 コマだけ、左の手が右の前腕を通り抜ける
    pos = plan.add_forearm(pos)
    assert plan.measure(pos)[0][9] > 0.005
    planned, todo, records = plan.plan_detours(pos)
    assert records and records[0]["solved"] and todo
    assert plan.measure(planned)[0][8:12].max() <= plan.s["gap"] + 1e-9


def test_wrist_acceleration_skips_short_clips_and_cuts_outside_the_range():
    assert sh.wrist_acceleration(np.zeros((2, 3)), 30.0, [1, 2], []) is None          # 2 コマでは 2 階の差が取れない（止めない）
    w = np.zeros((20, 3))
    w[10, 0] = 0.01                                                                   # 11 コマ目だけ 1 cm 跳ぶ
    plain = sh.wrist_acceleration(w, 30.0, list(range(1, 21)), [])
    assert plain["max"] == round(0.02 * 900, 1) and plain["max_at_frame"] == 11
    assert sh.wrist_acceleration(w, 30.0, list(range(1, 21)), [12])["max"] == 0.0     # 切り替えの前後 3 コマは数えない
    assert sh.wrist_acceleration(w, 30.0, list(range(1, 21)), [-5, 40]) == plain       # 範囲の外の切り替えは何もしない


def test_body_loss_compares_each_hand_point():
    # 1 つの点はもとから 2 cm 体に入っていて、もう 1 つの点が外から 1.5 cm 入る: コマごとの最小では変わらないが、点ごとに比べると 3 cm 近づいた
    before = np.array([[-0.02, 0.01]])
    after = np.array([[-0.02, -0.015]])
    assert sh.body_loss(before.min(axis=1)[:, None], after.min(axis=1)[:, None]) == 0.0
    assert sh.body_loss(before, after) == pytest.approx(0.025)
    assert sh.body_loss(np.array([[0.2]]), np.array([[0.05]])) == 0.0              # 3 cm より遠い所で近づくのは数えない
    plan = separator()
    pos = plan.add_forearm(arms())
    per_point = plan.body_clearance(pos, per_point=True)
    assert per_point.shape[0] == 20 and np.array_equal(per_point.min(axis=1), plan.body_clearance(pos))


def test_upper_arm_rotations_must_be_keyed_on_every_frame():
    path = 'pose.bones["upper_arm.L"].rotation_quaternion'
    def curve(index, keys):
        return NS(data_path=path, array_index=index, keyframe_points=[NS(co=(float(f), 0.0)) for f in keys])
    every = [curve(i, range(1, 11)) for i in range(4)]
    assert sh.unkeyed_frames(every, path, list(range(1, 11))) == []
    sparse = every[:3] + [curve(3, [1, 5, 10])]                                     # 1 つの成分だけキーがまばら
    assert sh.unkeyed_frames(sparse, path, list(range(1, 11))) == [2, 3, 4, 6, 7, 8, 9]
    assert sh.unkeyed_frames([], path, [1, 2]) == [1, 2]                              # 動きが無い
    assert sh.action_curves(NS(layers=[NS(strips=[NS(channelbags=[NS(fcurves=every)])])])) == every


def test_detour_runs_are_split_at_cuts():
    # 切り替えのコマ（11）をまたいで手が通り抜ける区間は、切り替えの前と後で別の区間として手当てする（後ろを捨てない）
    plan = separator(bones={**BONES, "fingers": ["thumb", "index"], "parts": ["proximal"]}, detour_max=0.2, threshold=0.003, cuts=[11])
    base = arms(gap=0.15)
    pos = plan.add_forearm({k: v.copy() for k, v in base.items()})
    for key in [k for k in pos if k[0] != "forearm" and k[0].endswith(".L") and not k[0].startswith("upper_arm")]:
        pos[key][9:11, 1] -= 0.15
    pos = plan.add_forearm(pos)
    _, _, records = plan.plan_detours(pos)
    assert sorted(record["frames"][0] for record in records) == [10, 11]


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out-blend"))
summary = {"frames_over": 0, "threshold_mm": 5.0, "max_overlap_mm": 0.0, "at_frame": 1, "runs": []}
report = {"schema_version": "model3d-separate-hands/0.1", "armature": "Rig", "frames": [1, 3], "before": {**summary, "frames_over": 4},
          "after": summary, "passes": [], "detours": [], "wrist_moved": {"L": {"max_mm": 12.0}}, "settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def run_recipe(tmp_path, parameters, joined=None):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    inputs, step_inputs = {"dance": {"path": "dance.blend"}}, {"blend": "$inputs.dance"}
    if joined is not None:
        atomic_json(tmp_path / "join.json", joined)
        inputs["joined"], step_inputs["joined"] = {"path": "join.json"}, "$inputs.joined"
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "separate", "inputs": inputs,
                                           "steps": [{"id": "separate", "operation": "motion.separate_hands", "inputs": step_inputs,
                                                      "parameters": {"blender": blender, **parameters},
                                                      "outputs": {"blend": "separated.blend", "report": "separate.json"}}]})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_separate_hands_step_passes_settings(tmp_path):
    result = run_recipe(tmp_path, {"body": "Body", "bones": BONES, "cuts": [596, 299], "passes": 6, "detour": 3, "up_share": 0.65,
                                   "aliases": {"lower_arm_twist.L": "lower_arm.L"}, **SIZES})
    assert result["status"] == "succeeded", result
    step = result["steps"]["separate"]
    sent = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert sent["cuts"] == [299, 596] and sent["passes"] == 6 and sent["detour"] == 3 and sent["bones"]["fingers"] == list(sh.FINGERS)
    assert {key: sent[key] for key in SIZES} == SIZES
    assert step["observations"][0]["before"] == 4 and step["observations"][0]["after"] == 0


def test_cuts_can_come_from_the_joined_report(tmp_path):
    sizes = {key: value for key, value in SIZES.items() if key not in ("head_radius", "torso_radius")}   # detour が無ければ頭と胴は要らない
    result = run_recipe(tmp_path, {"body": "Body", "bones": BONES, **sizes},
                        joined={"schema_version": "model3d-motion-join/0.1", "cuts": [299, 596], "frames": [1, 954]})
    assert result["status"] == "succeeded", result
    sent = json.loads(Path(result["steps"]["separate"]["artifacts"]["report"]).read_text(encoding="utf-8"))["settings"]
    assert sent["cuts"] == [299, 596]


def test_overlaps_hidden_behind_a_sleeve_pair_still_need_review(tmp_path):
    # 区間のいちばん大きい組が袖どうしでも、袖どうしを除いて測った after_hard に重なりがあれば要確認
    from model3d.ops import get
    sleeve_run = {"frames": [5, 9], "seconds": [0.13, 0.27], "max_overlap_mm": 30.0, "at": 7, "pair": "forearm_0(袖) × forearm_0(袖)"}
    hand_run = {"frames": [6, 6], "seconds": [0.17, 0.17], "max_overlap_mm": 8.0, "at": 6, "pair": "palm_index × forearm_80"}
    for after_hard, review in (({"frames_over": 1, "runs": [hand_run]}, True), ({"frames_over": 0, "runs": []}, False)):
        report = {"armature": "Rig", "after": {"frames_over": 5, "runs": [sleeve_run]}, "after_hard": after_hard}
        source = tmp_path / "report.json"
        source.write_text(json.dumps(report), encoding="utf-8")
        blender = stand_in_executable(tmp_path, "blender", f"SOURCE = {str(source)!r}\n" + STAND_IN_REPORT)
        (tmp_path / "dance.blend").write_bytes(b"dance")
        result = get("motion.separate_hands").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), "body": "Body", "bones": BONES, **SIZES},
                                                       {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
        assert result.review is review and len(result.unresolved) == (1 if review else 0)
        assert result.observations[0]["after_hard"] == after_hard["frames_over"]


STAND_IN_REPORT = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
shutil.copyfile(value("--blend"), value("--out-blend"))
shutil.copyfile(SOURCE, value("--report"))
'''


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"bones": BONES}, "body"),
    ({"body": "Body"}, "bones"),
    ({"body": "Body", "bones": {**BONES, "sides": ["L"]}}, "sides"),
    ({"body": "Body", "bones": {k: v for k, v in BONES.items() if k != "hand"}}, "hand"),
    ({"body": "Body", "bones": {**BONES, "upper": "upper_arm"}}, "{side}"),
    ({"body": "Body", "bones": {**BONES, "finger": "{finger}.{side}"}}, "{part}"),
    ({"body": "Body", "bones": BONES, "passes": 0}, "passes"),
    ({"body": "Body", "bones": BONES, "up_share": 1.5}, "up_share"),
    ({"body": "Body", "bones": BONES, "cuts": [0]}, "cuts"),
    ({"body": "Body", "bones": BONES, "keep_side": True}, "知らない"),
    ({"body": "Body", "bones": BONES}, "forearm_radius"),                         # 体の太さは体ごとに違うので既定が無い
    ({"body": "Body", "bones": BONES, **{k: v for k, v in SIZES.items() if k != "finger_radius"}}, "finger_radius"),
    ({"body": "Body", "bones": BONES, "detour": 1, **{k: v for k, v in SIZES.items() if k != "torso_radius"}}, "torso_radius"),
])
def test_separate_hands_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")
    with pytest.raises(ToolError, match=message):
        get("motion.separate_hands").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                              {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
