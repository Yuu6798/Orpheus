"""motion.push_hands: 骨の選び方・手首を動かす量・ならし（Blender なしで動く部分）と、台本の引数と工程の受け渡し
（代役の Blender）を確かめる。変形した頂点を測る所と骨を回す所は本物の Blender が要るので、CI では確かめない
（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import push_hands
from model3d.io import ToolError, atomic_json

# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、決まった出力を書き、受け取った引数を報告に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"pushed")
side = {"frames_crowded": 3, "points_inside_max": 80, "deepest_mm": 30.0, "deepest_frame": 12}
json.dump({"schema_version": "model3d-push-hands/0.1", "frames": [1, 3],
           "selection": {"body_from": "bone_weights", "body": {"body": 10}, "hands": {"Left": {"body": 4}, "Right": {"body": 4}},
                         "modifiers_off": []},
           "passes": [{"Left": {"frames_moved": 2, "largest_move_mm": 40.0, "deepest_lack_mm": 46.0},
                       "Right": {"frames_moved": 1, "largest_move_mm": 12.0, "deepest_lack_mm": 20.0}}],
           "inside": {"before": {"Left": side, "Right": side}, "after": {"Left": dict(side, frames_crowded=0), "Right": side}},
           "argv": args, "script": script}, open(value("--report"), "w"))
'''


def test_subtree_follows_children_and_stops_at_the_given_bones():
    parents = {"Hips": None, "Spine": "Hips", "Neck": "Spine", "Head": "Neck", "hair": "Head", "LeftShoulder": "Spine",
               "LeftArm": "LeftShoulder", "LeftUpLeg": "Hips", "LeftLeg": "LeftUpLeg", "LeftFoot": "LeftLeg", "skirt": "Hips",
               "skirt_tip": "skirt"}
    assert push_hands.subtree(parents, "Hips", stop={"Neck", "LeftShoulder", "LeftFoot"}) == {
        "Hips", "Spine", "LeftUpLeg", "LeftLeg", "skirt", "skirt_tip"}
    assert push_hands.subtree(parents, "LeftArm") == {"LeftArm"}
    assert push_hands.subtree(parents, "LeftHand") == set()


def prototype_spread_and_smooth(v):
    """リポジトリ外の試作の式（前後 3 コマの最大を広げ、11 コマ・σ 1.5 のガウス）をそのまま写したもの"""
    k = np.exp(-0.5 * (np.arange(-5, 6) / 1.5) ** 2)
    k /= k.sum()
    raw = np.linalg.norm(v, axis=1)
    wide = np.zeros_like(v)
    for f in range(len(v)):
        lo, hi = max(0, f - 3), min(len(v), f + 4)
        wide[f] = v[lo + int(raw[lo:hi].argmax())]
    return np.stack([np.convolve(np.pad(wide[:, j], 5, mode="edge"), k, mode="valid") for j in range(3)], 1)


def prototype_need(p, bc, bn, i, d, gap=0.006, reach=0.04, cap=0.04):
    """リポジトリ外の試作の、1 コマ・片手分の式をそのまま写したもの"""
    s = np.einsum("ij,ij->i", p - bc[i], bn[i])
    lack = np.where((s < gap) & (d < reach), gap - s, 0.0)
    if (lack > 0.002).sum() < 15:
        return None, 0.0
    m = lack > 0
    nbar = (bn[i[m]] * lack[m, None]).sum(0)
    nbar /= max(np.linalg.norm(nbar), 1e-9)
    cosv = np.maximum(bn[i[m]] @ nbar, 0.3)
    req = float(np.percentile(lack[m] / cosv, 98))
    return nbar * min(req, cap), float((gap - s[m]).max())


def test_smoothing_matches_the_prototype():
    moves = np.random.default_rng(3).normal(0, 0.01, (60, 3)) * (np.random.default_rng(4).random((60, 1)) > 0.6)
    np.testing.assert_allclose(push_hands.spread_and_smooth(moves), prototype_spread_and_smooth(moves), atol=1e-15)
    assert len(push_hands.gaussian(1.5)) == 11   # ±3σ で打ち切ると、試作と同じ 11 コマ


def test_push_vector_matches_the_prototype_on_random_points():
    rng = np.random.default_rng(11)
    moved = 0
    for _ in range(20):
        bc = rng.normal(0, 0.05, (200, 3)).astype(np.float32)
        bn = rng.normal(0, 1, (200, 3)).astype(np.float32)
        bn /= np.linalg.norm(bn, axis=1, keepdims=True)
        p = (bc[:80] + rng.normal(0, 0.01, (80, 3))).astype(np.float32)
        i = rng.integers(0, 200, 80)
        d = rng.random(80) * 0.06
        expected = prototype_need(p, bc, bn, i, d)
        vector, depth = push_hands.push_vector(push_hands.signed_gap(p, bc, bn, i), bn, i, d, 0.006, 0.04, 0.04)
        if expected[0] is None:
            assert vector is None and depth == 0.0
        else:
            np.testing.assert_array_equal(vector, expected[0])
            assert depth == expected[1]
            moved += 1
    assert moved > 0


def flat_floor(depth_m, count=30):
    """z=0 の面（法線は上）と、その内側 depth_m に並んだ手の点"""
    bc = np.zeros((count, 3), np.float32)
    bc[:, 0] = np.arange(count) * 0.001
    bn = np.tile(np.float32([0, 0, 1]), (count, 1))
    p = bc.copy()
    p[:, 2] = -depth_m
    return p, bc, bn, np.arange(count), np.full(count, depth_m)


def test_a_hand_sunk_into_a_flat_surface_is_pushed_out_along_its_normal_by_the_gap():
    p, bc, bn, i, d = flat_floor(0.01)
    vector, depth = push_hands.push_vector(push_hands.signed_gap(p, bc, bn, i), bn, i, d, 0.006, 0.04, 0.04)
    np.testing.assert_allclose(vector, [0, 0, 0.016], atol=1e-7)   # 10 mm の沈み + 6 mm の隙間
    assert depth == pytest.approx(0.016, abs=1e-7)


def test_one_push_is_capped():
    p, bc, bn, i, d = flat_floor(0.1)
    d[:] = 0.01                                                      # 近さの判定は通す
    vector, _ = push_hands.push_vector(push_hands.signed_gap(p, bc, bn, i), bn, i, d, 0.006, 0.4, 0.04)
    np.testing.assert_allclose(vector, [0, 0, 0.04], atol=1e-7)


def test_a_few_points_or_far_points_do_not_move_the_arm():
    p, bc, bn, i, d = flat_floor(0.01, count=14)                    # 15 点に満たない
    assert push_hands.push_vector(push_hands.signed_gap(p, bc, bn, i), bn, i, d, 0.006, 0.04, 0.04) == (None, 0.0)
    p, bc, bn, i, d = flat_floor(0.01)
    d[:] = 0.05                                                      # reach より遠い
    assert push_hands.push_vector(push_hands.signed_gap(p, bc, bn, i), bn, i, d, 0.006, 0.04, 0.04) == (None, 0.0)


def test_the_search_box_keeps_every_body_point_within_reach():
    hand = np.zeros((1, 3))
    body = np.array([[0.03, 0, 0], [0.055, 0, 0], [0.07, 0, 0], [0.09, 0, 0]])
    assert push_hands.around(body, hand, 0.04).tolist() == [True, False, False, False]   # 既定は試作と同じ 5 cm
    assert push_hands.around(body, hand, 0.08).tolist() == [True, True, True, False]     # reach が 5 cm を超えたら広げる
    rng = np.random.default_rng(5)
    for reach in (0.02, 0.04, 0.08, 0.15):
        hand, body = rng.normal(0, 0.03, (40, 3)), rng.normal(0, 0.1, (400, 3))
        distance = np.linalg.norm(body[:, None] - hand[None], axis=2).min(1)
        assert (distance < reach).any() and push_hands.around(body, hand, reach)[distance < reach].all()


def test_inside_counts_points_deeper_than_two_millimetres():
    count, deepest = push_hands.inside(np.array([-0.01, -0.001, 0.003, -0.02]), np.array([0.01, 0.01, 0.01, 0.05]), 0.04)
    assert count == 1 and deepest == pytest.approx(0.01)
    assert push_hands.inside(np.array([0.01]), np.array([0.01]), 0.04) == (0, 0.0)


def test_summary_counts_crowded_frames():
    report = push_hands.summary([1, 2, 3], {"Left": np.array([10, 60, 70])}, {"Left": np.array([0.001, 0.02, 0.01])})
    assert report["Left"] == {"frames_crowded": 2, "points_inside_max": 70, "deepest_mm": 20.0, "deepest_frame": 2}


def test_parse_args_defaults():
    args = push_hands.parse_args(["--blend", "in.blend", "--out-blend", "o.blend", "--report", "r.json"])
    assert args.bone_prefix == "mixamorig:" and args.body_object == [] and args.passes == 2
    assert (args.gap_mm, args.reach_mm, args.cap_mm) == (6.0, 40.0, 40.0)


def make_recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "dance.blend").write_bytes(b"grounded")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "push",
                                           "inputs": {"dance": {"path": "dance.blend"}},
                                           "steps": [{"id": "push", "operation": "motion.push_hands",
                                                      "inputs": {"blend": "$inputs.dance"}, "parameters": parameters,
                                                      "outputs": outputs or {"blend": "pushed.blend", "report": "push.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_passes_the_blend_and_options_to_blender(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    recipe = make_recipe(tmp_path, {"blender": str(blender), "body_objects": ["torso", "skirt, front"], "passes": 1,
                                    "gap_mm": 4, "reach_mm": 30, "cap_mm": 20})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["push"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    argv = report["argv"]
    assert Path(report["script"]).name == "push_hands.py"
    assert Path(argv[argv.index("--blend") + 1]).name == "dance.blend"
    assert [argv[i + 1] for i, flag in enumerate(argv) if flag == "--body-object"] == ["torso", "skirt, front"]
    assert argv[argv.index("--passes") + 1] == "1"
    assert [argv[argv.index(f) + 1] for f in ("--gap-mm", "--reach-mm", "--cap-mm")] == ["4.0", "30.0", "20.0"]
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"pushed"
    observation = step["observations"][0]
    assert observation["kind"] == "push_hands" and observation["blender_version"] == "4.5.15"
    assert observation["passes"][0]["Left"]["largest_move_mm"] == 40.0
    assert observation["inside"]["after"]["Left"]["frames_crowded"] == 0
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["push"]["reused_from"] is None   # 毎回実行する


def test_without_options_the_script_uses_bone_weights_and_two_passes(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    result = executor.run_recipe(make_recipe(tmp_path, {"blender": str(blender)}), tmp_path / "runs")
    argv = json.loads(Path(result["steps"]["push"]["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert "--body-object" not in argv and argv[argv.index("--passes") + 1] == "2"
    assert [argv[argv.index(f) + 1] for f in ("--gap-mm", "--reach-mm", "--cap-mm")] == ["6.0", "40.0", "40.0"]


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "Blender"),
    ({"body_objects": "torso"}, None, "body_objects"),
    ({"body_objects": []}, None, "body_objects"),
    ({"body_objects": ["torso", ""]}, None, "body_objects"),
    ({"body_objects": [3]}, None, "body_objects"),
    ({"passes": 0}, None, "passes"),
    ({"passes": 1.5}, None, "passes"),
    ({"passes": True}, None, "passes"),
    ({"gap_mm": -1}, None, "gap_mm"),
    ({"reach_mm": 0}, None, "reach_mm"),
    ({"cap_mm": 0}, None, "cap_mm"),
    ({"bone_prefix": 3}, None, "bone_prefix"),
    ({}, {"blend": "o.blend"}, "report"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    if message != "Blender":
        parameters = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **parameters}
    (tmp_path / "dance.blend").write_bytes(b"grounded")
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    with pytest.raises(ToolError, match=message):
        get("motion.push_hands").function({"blend": tmp_path / "dance.blend"}, parameters, declared, Context(tmp_path))
