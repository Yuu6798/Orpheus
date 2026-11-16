"""小数のコマ: 動きと違う fps で描くコマの並び（plan.retime）、トラックの小数の f の読み方、前後のコマの間の時刻への進め方、
plan.stage・motion.bone_path・render.passes が小数のコマを受け取ること（Blender なし。本物の Blender の確認は docs/model3d.md）。"""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import stage_render
from model3d.io import ToolError, atomic_json
from model3d.ops import plan_ops
from model3d.stage_plan import build_track
from test_m3d_plan import bones, keys, plan


def test_motion_frames_stay_integers_only_when_they_are_whole():
    assert stage_render.motion_frame(25) == 25 and isinstance(stage_render.motion_frame(25.0), int)
    assert stage_render.motion_frame(25.8) == 25.8 and isinstance(stage_render.motion_frame(25.8), float)
    assert isinstance(stage_render.motion_frame(29.000000000000004), float)        # 丸めの残りも小数のまま（そのまま進める）
    assert stage_render.split_frame(25) == (25, 0.0) and stage_render.split_frame(25.75) == (25, 0.75)
    assert stage_render.split_frame(28.999999999999996)[0] == 28                   # 前のコマと、ほぼ 1 の割合
    assert stage_render.split_frame(-0.5) == (-1, 0.5)


def test_set_frame_passes_the_subframe():
    calls = []
    scene = SimpleNamespace(frame_set=lambda frame, subframe=0.0: calls.append((frame, subframe)))
    stage_render.set_frame(scene, 25.8)
    stage_render.set_frame(scene, 26)
    assert calls[0][0] == 25 and calls[0][1] == pytest.approx(0.8) and calls[1] == (26, 0.0)


def test_the_frame_plan_keeps_fractional_frames():
    track = {"frames": [{"T": 1, "f": 25}, {"T": 2, "f": 25.8}, {"T": 3, "f": 26.0}]}
    plan_rows = stage_render.frame_plan(track, 1, 1)
    assert [(T, f) for T, f, _ in plan_rows] == [(1, 25), (2, 25.8), (3, 26)] and isinstance(plan_rows[2][1], int)


def test_retime_maps_output_frames_onto_the_motion():
    track = plan_ops.retime_track(30, 24, 25, 11)
    assert contracts.validate("stage-track", track) and track["fps"] == 30
    fs = [fr["f"] for fr in track["frames"]]
    assert fs[0] == 25 and fs[5] == 29 and fs[10] == 33 and all(isinstance(f, int) for f in fs[::5])
    assert fs[1] == pytest.approx(25.8) and fs[4] == pytest.approx(28.2) and isinstance(fs[1], float)
    assert [fr["T"] for fr in track["frames"]] == list(range(1, 12))
    assert [fr["f"] for fr in plan_ops.retime_track(24, 24, 1, 3)["frames"]] == [1, 2, 3]       # 同じ fps なら 1 コマずつ


def test_the_retime_operation_writes_a_cut(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "retime", "inputs": {},
                                           "steps": [{"id": "cut", "operation": "plan.retime",
                                                      "parameters": {"fps": 30, "motion_fps": 24, "start": 25, "frames": 906},
                                                      "outputs": {"track": "cut.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    step = result["steps"]["cut"]
    assert result["status"] == "succeeded", step.get("error")
    cut = contracts.load("stage-track", step["artifacts"]["track"])
    assert len(cut["frames"]) == 906 and cut["frames"][-1]["f"] == pytest.approx(25 + 905 * 0.8)
    obs = step["observations"][0]
    assert obs["kind"] == "retime" and obs["fractional"] == 906 - 182 and obs["f"][0] == 25
    for bad, word in (({"fps": 0, "motion_fps": 24, "frames": 3}, "fps"), ({"fps": 30, "motion_fps": 24, "frames": 0}, "frames"),
                      ({"fps": 30, "motion_fps": 24, "frames": 2.5}, "frames"), ({"fps": 30, "frames": 3}, "motion_fps"),
                      ({"fps": 30, "motion_fps": 24, "frames": 3, "speed": 2}, "知らない")):
        atomic_json(tmp_path / "bad.json", {"schema_version": "model3d-recipe/0.1", "name": "bad", "inputs": {},
                                            "steps": [{"id": "cut", "operation": "plan.retime", "parameters": bad,
                                                       "outputs": {"track": "cut.json"}}]})
        failed = executor.run_recipe(tmp_path / "bad.json", tmp_path / "runs")
        assert failed["status"] == "failed" and word in failed["steps"]["cut"]["error"], (bad, failed["steps"]["cut"]["error"])


def test_plan_stage_follows_a_fractional_cut():
    cut = plan_ops.retime_track(10, 8, 1, 30)                    # 10 fps で描き、動きは 8 fps
    path = bones(sorted({fr["f"] for fr in cut["frames"]}))       # 骨の通り道も同じ小数のコマで測ったもの
    track, report = build_track(plan(), keys(30), path, cut)
    assert contracts.validate("stage-track", track) and [fr["f"] for fr in track["frames"]] == [fr["f"] for fr in cut["frames"]]
    assert report["frames"] == 30
    with pytest.raises(ToolError, match="動きのコマがありません"):
        build_track(plan(), keys(30), bones(range(1, 41)), cut)    # 整数のコマだけの通り道では、小数のコマが無い


def test_bone_path_measures_the_fractional_frames_of_a_track():
    from model3d.ops.motion_ops import _bone_path_settings
    track = {"frames": [{"T": 1, "f": 25.8}, {"T": 2, "f": 25}, {"T": 3, "f": 25.8}, {"T": 4, "f": 26.6}]}
    assert _bone_path_settings({}, track)["frames"] == [25, 25.8, 26.6]


STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
value = lambda flag: args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
out = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": settings["frames"],
       "bones": {b: [[0.0, 0.0, 1.0]] * len(settings["frames"]) for b in settings["bones"]}}
json.dump(out, open(value("--out"), "w"))
'''


def test_the_bone_path_operation_keeps_fractional_frames(tmp_path):
    from model3d import executor
    (tmp_path / "dance.blend").write_bytes(b"moved")
    atomic_json(tmp_path / "cut.json", plan_ops.retime_track(30, 24, 1, 6))
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "bones",
                                           "inputs": {"dance": {"path": "dance.blend"}, "cut": {"path": "cut.json"}},
                                           "steps": [{"id": "bones", "operation": "motion.bone_path",
                                                      "inputs": {"blend": "$inputs.dance", "track": "$inputs.cut"},
                                                      "parameters": {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN))},
                                                      "outputs": {"path": "bones.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["steps"]["bones"].get("error")
    path = json.loads(Path(result["steps"]["bones"]["artifacts"]["path"]).read_text(encoding="utf-8"))
    assert path["frames"][0] == 1 and path["frames"][1] == pytest.approx(1.8) and path["frames"][5] == 5


def test_the_contracts_take_fractional_frames():
    contracts.validate("frames", {"schema_version": "model3d-frames/0.1", "directory": ".", "fps": 30, "resolution": [8, 8],
                                  "frames": [{"T": 2, "f": 25.8, "file": "f0002.png", "sha256": "0" * 64, "bytes": 1}], "missing": []})
    contracts.validate("bone-path", {**bones([1, 1.8]), "bones": {"Hips": [[0, 0, 1], [0, 0, 1]]}})
    with pytest.raises(ToolError, match="stage-track"):
        contracts.validate("stage-track", {"schema_version": "model3d-stage-track/0.1", "frames": [{"T": 1, "f": "25.8"}]})
