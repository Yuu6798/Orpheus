"""motion.ground_feet: 腰に足す高さの計算と、.blend のコマと points のコマの対応（Blender なしで動く部分）、
台本の引数と工程の受け渡し（代役の Blender）を確かめる。足の点を測る所は本物の Blender が要るので、CI では確かめない
（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import ground_feet
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
open(value("--out-blend"), "wb").write(b"grounded")
lowest = {"median_mm": -11.7, "min_mm": -79.2, "min_frame": 5, "max_mm": 8.1, "max_frame": 30, "below_10mm_frames": 56,
          "over_30mm_frames": 0, "over_60mm_frames": 0}
json.dump({"schema_version": "model3d-ground-feet/0.1", "frames": [1, 3], "points_frames": {"first": 0, "last": 2, "from": "blend"},
           "feet": {"from": "bone_weights", "vertices": {"body": 12}, "modifiers_off": []}, "stance_frames": 3,
           "lift_mm": {"min": -1.2, "max": 76.5, "largest_change_per_frame": 15.5},
           "lowest_point": {"before": lowest, "after": dict(lowest, median_mm=0.0)}, "lift_check_mm": LIFT_CHECK_MM,
           "argv": args, "script": script}, open(value("--report"), "w"))
'''


def blender_stand_in(directory, lift_check_mm=0.00201234):
    return stand_in_executable(directory, "blender", STAND_IN.replace("LIFT_CHECK_MM", repr(lift_check_mm)))


def prototype_lift(lowest, stance):
    """リポジトリ外の試作の式（9 コマ・σ 1.5 のガウス、沈み 3 mm、1.5 倍）をそのまま写したもの"""
    x = np.arange(len(lowest))
    want = np.interp(x, x[stance], -lowest[stance])
    k = np.exp(-0.5 * (np.arange(-4, 5) / 1.5) ** 2)
    k /= k.sum()
    dz = np.convolve(np.pad(want, 4, mode="edge"), k, mode="valid")
    under = np.maximum(0.0, -(lowest + dz) - 0.003)
    return dz + np.convolve(np.pad(under, 4, mode="edge"), k, mode="valid") * 1.5


def test_lift_matches_the_prototype_formula():
    rng = np.random.default_rng(7)
    lowest = rng.normal(-0.01, 0.02, 101)
    stance = rng.random(101) > 0.3
    np.testing.assert_allclose(ground_feet.lift(lowest, stance), prototype_lift(lowest, stance), atol=1e-15)
    assert len(ground_feet.gaussian(1.5)) == 9   # ±2.5σ で打ち切ると、試作と同じ 9 コマ


def test_lift_brings_a_sunken_foot_up_to_the_floor():
    np.testing.assert_allclose(ground_feet.lift(np.full(20, -0.01), np.ones(20, bool)), 0.01, atol=1e-12)


def test_lift_joins_frames_without_contact_from_their_neighbours():
    lowest = np.array([-0.01, 0.2, 0.3, 0.2, -0.03])            # 1〜3 コマは跳んでいる
    stance = np.array([True, False, False, False, True])
    np.testing.assert_allclose(ground_feet.lift(lowest, stance, sigma=0.1), [0.01, 0.015, 0.02, 0.025, 0.03], atol=1e-9)


def test_without_contact_only_a_sinking_foot_is_lifted():
    np.testing.assert_allclose(ground_feet.lift(np.full(10, 0.05), np.zeros(10, bool)), 0.0)
    # 沈みが許す 3 mm を超えた分の 1.5 倍を足す: (20 − 3) × 1.5 = 25.5 mm
    np.testing.assert_allclose(ground_feet.lift(np.full(10, -0.02), np.zeros(10, bool)), 0.0255, atol=1e-12)


def test_points_frames_prefers_the_parameter_then_the_retarget_record_then_equal_counts():
    assert ground_feet.points_frames(3, 20, stored=[4, 6, 8], first=2) == ([2, 3, 4], "parameter")
    assert ground_feet.points_frames(3, 20, stored=[4, 6, 8]) == ([4, 6, 8], "blend")
    assert ground_feet.points_frames(4, 4) == ([0, 1, 2, 3], "same_count")


@pytest.mark.parametrize("blend_frames, points_count, stored, first", [
    (5, 9, None, None),          # どこか分からない
    (3, 20, [4, 6], None),       # 記録とシーンのコマ数が違う
    (5, 6, None, 3),             # points の外まで行く
    (2, 20, [-1, 0], None),
])
def test_points_frames_refuses_what_it_cannot_line_up(blend_frames, points_count, stored, first):
    with pytest.raises(SystemExit):
        ground_feet.points_frames(blend_frames, points_count, stored, first)


def test_parse_args_defaults():
    args = ground_feet.parse_args(["--blend", "in.blend", "--points", "p.npz", "--out-blend", "o.blend", "--report", "r.json"])
    assert args.bone_prefix == "mixamorig:" and args.foot_object == [] and args.first_points_frame is None
    assert args.sigma == 1.5 and args.allow_sink_mm == 3.0


def test_stats_count_sinking_and_floating_frames():
    report = ground_feet.stats([-0.02, 0.0, 0.04, 0.07], [1, 2, 3, 4])
    assert report["min_mm"] == -20.0 and report["min_frame"] == 1 and report["max_frame"] == 4
    assert (report["below_10mm_frames"], report["over_30mm_frames"], report["over_60mm_frames"]) == (1, 2, 1)


def make_recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "dance.blend").write_bytes(b"retargeted")
    np.savez(tmp_path / "points.npz", points=np.zeros((3, 70, 3)), stance=np.ones((3, 2)))
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "ground",
                                           "inputs": {"dance": {"path": "dance.blend"}, "points": {"path": "points.npz"}},
                                           "steps": [{"id": "ground", "operation": "motion.ground_feet",
                                                      "inputs": {"blend": "$inputs.dance", "points": "$inputs.points"},
                                                      "parameters": parameters,
                                                      "outputs": outputs or {"blend": "grounded.blend", "report": "ground.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_passes_the_blend_points_and_options_to_blender(tmp_path):
    from model3d import executor
    recipe = make_recipe(tmp_path, {"blender": str(blender_stand_in(tmp_path)), "foot_objects": ["boot L", "boot,R"],
                                    "first_points_frame": 7, "smooth_sigma_frames": 2, "allow_sink_mm": 1.5})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["ground"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    argv = report["argv"]
    assert Path(report["script"]).name == "ground_feet.py"
    assert Path(argv[argv.index("--blend") + 1]).name == "dance.blend" and Path(argv[argv.index("--points") + 1]).name == "points.npz"
    assert [argv[i + 1] for i, flag in enumerate(argv) if flag == "--foot-object"] == ["boot L", "boot,R"]   # 名前はそのまま 1 つずつ
    assert argv[argv.index("--first-points-frame") + 1] == "7"
    assert argv[argv.index("--sigma") + 1] == "2.0" and argv[argv.index("--allow-sink-mm") + 1] == "1.5"
    assert Path(step["artifacts"]["blend"]).read_bytes() == b"grounded"
    observation = step["observations"][0]
    assert observation["kind"] == "ground_feet" and observation["blender_version"] == "4.5.15"
    assert observation["stance_frames"] == 3 and observation["lift_mm"]["max"] == 76.5 and observation["feet_from"] == "bone_weights"
    assert observation["lift_check_mm"] == 0.002   # 報告は丸めない、観察は丸める
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["ground"]["reused_from"] is None   # 毎回実行する


def test_without_options_the_script_picks_feet_by_weight_and_reads_the_frames_from_the_blend(tmp_path):
    from model3d import executor
    result = executor.run_recipe(make_recipe(tmp_path, {"blender": str(blender_stand_in(tmp_path))}), tmp_path / "runs")
    argv = json.loads(Path(result["steps"]["ground"]["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert "--foot-object" not in argv and "--first-points-frame" not in argv
    assert argv[argv.index("--sigma") + 1] == "1.5" and argv[argv.index("--allow-sink-mm") + 1] == "3.0"


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir


def call(tmp_path, parameters, outputs=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"retargeted")
    np.savez(tmp_path / "points.npz", points=np.zeros((3, 70, 3)), stance=np.ones((3, 2)))
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "report": "r.json"}).items()}
    return get("motion.ground_feet").function({"blend": tmp_path / "dance.blend", "points": tmp_path / "points.npz"}, parameters,
                                              declared, Context(tmp_path))


def test_the_operation_stops_when_the_feet_did_not_follow_the_hips(tmp_path):
    with pytest.raises(ToolError, match="足が動きませんでした"):
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, lift_check_mm=12.5))})


def test_the_tolerance_is_compared_before_rounding(tmp_path):
    with pytest.raises(ToolError, match="足が動きませんでした"):   # 0.10004 は 4 桁に丸めると 0.1 で通ってしまう
        call(tmp_path, {"blender": str(blender_stand_in(tmp_path, lift_check_mm=0.10004))})


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "Blender"),
    ({"foot_objects": "boot"}, None, "foot_objects"),
    ({"foot_objects": []}, None, "foot_objects"),
    ({"foot_objects": ["boot", ""]}, None, "foot_objects"),
    ({"foot_objects": [3]}, None, "foot_objects"),
    ({"first_points_frame": -1}, None, "first_points_frame"),
    ({"first_points_frame": 1.5}, None, "first_points_frame"),
    ({"first_points_frame": True}, None, "first_points_frame"),
    ({"smooth_sigma_frames": 0}, None, "smooth_sigma_frames"),
    ({"allow_sink_mm": -1}, None, "allow_sink_mm"),
    ({"bone_prefix": 3}, None, "bone_prefix"),
    ({}, {"blend": "o.blend"}, "report"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, outputs, message):
    if message != "Blender":
        parameters = {"blender": str(blender_stand_in(tmp_path)), **parameters}
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters, outputs)
