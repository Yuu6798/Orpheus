"""目の動きの台本（motion.eye_plan）: 台本の確かめ、視線・まばたき・驚き・笑顔・まぶた・瞳の伸びの計算、見せ直しの区間とコマの数（Blender なし）、
工程の受け渡し（代役の Blender）と悪い設定を止めること。本物の Blender 5.2.4 での照合は docs/model3d.md。例の台本は、どの演技とも関係の無い値。"""
import copy
import json
import math
from pathlib import Path

import pytest

from m3d_helpers import stand_in_executable
from model3d import eye_plan
from model3d.blender_adapter import eye_math, eye_motion
from model3d.io import atomic_json

PLAN = {
    "schema_version": "model3d-eye-plan/0.1", "fps": 24,
    "gaze": {"events": [[0.5, 0.004, 0.0], [1.2, -0.004, 0.001]],
             "saccade": {"base_s": 0.05, "per_m_s": 5.0, "overshoot": 0.1, "settle_s": 0.1, "settle_decay": 3.0}},
    "blink": {"starts": [2.0], "close_s": 0.05, "hold_s": 0.04, "open_s": 0.13, "squash_open_s": 0.1, "length_s": 0.5,
              "stretch": {"start_s": 0.17, "length_s": 0.2, "amount": 0.3, "taper": 0.4}, "overopen": {"start_s": 0.2, "length_s": 0.2, "amount": 0.08}},
    "surprise": {"start": 3.0, "release": 3.6, "end": 4.0, "squint": {"amount": 0.3, "length_s": 0.08},
                 "wide": [0.06, 0.08, 0.2, 0.05], "small": [0.07, 0.08, 0.1, 0.05], "brow": [0.05, 0.11]},
    "smile": {"start": 4.5, "ramp_s": 0.25, "brow": 0.4},
    "lids": {"up_per_m": 100.0, "down_per_m": 60.0},
    "iris_wide": {"amount": 0.8, "full_m": 0.005},
    "sections": [[0.0, 2.5, 1.0, "front", "look"], [1.9, 2.6, 0.5, "close", "blink slow"], [2.5, 5.0, 1.0, "front", "surprise and smile"]],
}
P = eye_plan.validate(PLAN)


def test_gaze_jumps_overshoots_and_settles():
    assert eye_plan.gaze(0.2, P) == (0.0, 0.0)                                        # 最初の飛びの前は正面
    dur = 0.05 + 5.0 * 0.004                                                          # かかる時間 = base_s + per_m_s × 距離
    assert eye_plan.gaze(0.5 + dur, P)[0] == pytest.approx(0.004 * 1.1)               # 着いた時に 1 割行きすぎる
    assert eye_plan.gaze(0.5 + dur + 0.05, P)[0] < 0.004 * 1.1                        # 戻りながら
    assert eye_plan.gaze(1.19, P)[0] == pytest.approx(0.004, abs=1e-6)                # 止まる
    x, z = eye_plan.gaze(2.4, P)
    assert x == pytest.approx(-0.004, abs=1e-6) and z == pytest.approx(0.001, abs=1e-6)


def test_blink_closes_holds_opens_and_stretches_the_iris():
    assert eye_plan.blink_parts(1.9, P) == (0.0, 0.0, 0.0)
    b, sq, _ = eye_plan.blink_parts(2.05, P)
    assert b == pytest.approx(1.0) and sq == pytest.approx(1.0)                       # close_s で閉じる
    assert eye_plan.blink_parts(2.07, P)[0] == 1.0                                    # hold_s の間は閉じたまま
    b, sq, wide = eye_plan.blink_parts(2.25, P)                                       # 開いたあと: 瞳はたてに伸び、まぶたは少し開きすぎる
    assert b == pytest.approx(0.0) and sq < 0 and wide > 0


def test_surprise_smile_lids_and_iris_stretch():
    squint, wide, small, brow = eye_plan.surprise_parts(3.04, P)
    assert squint > 0 and wide == 0.0 and small == 0.0                                # 一瞬すくんでから
    _, wide, small, brow = eye_plan.surprise_parts(3.5, P)
    assert wide == pytest.approx(1.0, abs=1e-3) and small == pytest.approx(1.0, abs=1e-3) and brow == pytest.approx(1.0)
    assert eye_plan.surprise_parts(4.0, P)[1] == pytest.approx(0.0, abs=1e-9)         # release から end でゆるめる
    smiled = eye_plan.state(4.9, P)["keys"]
    assert smiled["blink_smile"] == 1.0 and smiled["mouth_smile"] == 1.0 and smiled["blink"] == 0.0 and smiled["brow_up"] == pytest.approx(0.4)
    up = eye_plan.state(2.4, P)                                                       # 上を見ると、まぶたが少し開く
    assert up["keys"]["eye_wide"] == pytest.approx(100.0 * up["gaze"][1], abs=1e-9)
    side = eye_plan.state(1.19, P)["keys"]                                            # 横を見ると、瞳が横へ伸びる
    assert side["iris_wide"] == pytest.approx(0.8 * eye_plan.smooth(0.004 / 0.005), abs=1e-3)
    assert set(side) == set(eye_plan.KEY_NAMES)


def test_sections_replay_slowly_and_number_the_frames():
    frames = eye_plan.frames(P)
    counts = [sum(1 for _, s in frames if s == k) for k in range(3)]
    assert counts == [60, round(0.7 / 0.5 * 24), 60]                                   # 半分の速さの区間は 2 倍のコマ
    assert frames[60] == (1.9, 1) and frames[61][0] == pytest.approx(1.9 + 0.7 / 34)
    timeline = eye_plan.timeline(P, start_frame=101)
    assert timeline["count"] == len(frames) and timeline["frames"][0]["frame"] == 101
    assert [(s["frames"], s["caption"], s["speed"]) for s in timeline["sections"]] == [
        ([101, 160], "look", 1.0), ([161, 194], "blink slow", 0.5), ([195, 254], "surprise and smile", 1.0)]


@pytest.mark.parametrize("change, message", [
    ({"schema_version": "other"}, "schema_version"), ({"wiggle": 1}, "知らない項目"), ({"fps": 0}, "fps"),
    ({"gaze": {"events": [[1.0, 0, 0], [0.5, 0, 0]], "saccade": PLAN["gaze"]["saccade"]}}, "τ の順"),
    ({"gaze": {"events": [], "saccade": {"base_s": 0.05}}}, "gaze.saccade"),
    ({"gaze": {**PLAN["gaze"], "circle": {"start": 2, "end": 1, "h_m": 0.001, "v_m": 0.001, "ramp_s": 0.2}}}, "circle"),
    ({"blink": {**PLAN["blink"], "close_s": 0}}, "blink.close_s"), ({"blink": {**PLAN["blink"], "stretch": None}}, "blink.stretch"),
    ({"surprise": {**PLAN["surprise"], "release": 5.0}}, "release"), ({"surprise": {**PLAN["surprise"], "wide": [0.1, 0.1]}}, "surprise.wide"),
    ({"lids": {"up_per_m": 1}}, "lids"), ({"sections": [[1.0, 0.5, 1.0, "front", "x"]]}, "終わり"), ({"sections": []}, "sections")])
def test_plan_is_checked(change, message):
    with pytest.raises(eye_plan.PlanError, match=message):
        eye_plan.validate({**copy.deepcopy(PLAN), **change})


def test_key_ranges_widen_the_sliders_only_where_needed():
    rows = [{"keys": {"blink": 0.5, "iris_squash": -0.3, "eye_wide": 1.2}}, {"keys": {"blink": 1.0, "iris_squash": 0.8, "eye_wide": 0.0}}]
    names = {"blink_L", "blink_R", "iris_squash_L", "iris_squash_R", "eye_wide_L", "eye_wide_R"}
    ranges = eye_motion.key_ranges(rows, names)
    assert ranges["blink_L"] == (0.5, 1.0) and ranges["iris_squash_R"] == (-0.3, 0.8) and ranges["eye_wide_L"] == (0.0, 1.2)
    from model3d.blender_adapter import eye_keys
    assert eye_motion.absolute_keys_problem is eye_keys.absolute_keys_problem  # 絶対の形状キーは値のキーで動かせないので止める（目のキーと同じ）
    # 打つキーをドライバーが上書きするなら止める（ドライバーは F カーブのキーより強い）
    keyed = {'key_blocks["blink_L"].value', 'pose.bones["eye.L"].rotation_euler'}
    assert eye_motion.overridden(['key_blocks["blink_L"].value', 'key_blocks["mouth"].value'], keyed) == ['key_blocks["blink_L"].value']
    assert eye_motion.overridden([], keyed) == []


def _geometry():
    def eye(sign):
        return {"sphere": {"centre": [0.04 * sign, 0.1, 1.3], "radius": 0.1, "normal": [0.0, -1.0, 0.0]}, "pupil": [0.04 * sign, 1.3],
                "iris": {"a": 0.006, "b": 0.005}, "rim": {"x": [0.04 * sign - 0.01, 0.04 * sign + 0.01], "upper": [1.305, 1.305], "lower": [1.295, 1.295]},
                "window": [0.04 * sign - 0.02, 0.04 * sign + 0.02, 1.28, 1.32], "iris_vertices": [1, 2] if sign < 0 else [3, 4]}
    return {"schema_version": eye_math.SCHEMA, "object": "Head", "eyes": {"R": eye(-1.0), "L": eye(1.0)}}


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
shutil.copyfile(value("--blend"), value("--out"))
frames = settings["frames"]
json.dump({"blender": "5.2.4", "object": settings["object"], "armature": "Armature", "bones": {"R": "eye.R", "L": "eye.L"},
           "frames": [frames[0]["frame"], frames[-1]["frame"]], "count": len(frames), "keyed": ["blink_L", "blink_R"], "unknown_keys": [],
           "widened": {}, "x_settings": {k: v for k, v in settings.items() if k != "frames"}, "x_first": frames[0]},
          open(value("--report"), "w", encoding="utf-8"))
'''


def _recipe(tmp_path, parameters, plan=None, outputs=None):
    (tmp_path / "head.blend").write_bytes(b"blend")
    atomic_json(tmp_path / "eyes.json", _geometry())
    atomic_json(tmp_path / "plan.json", plan or PLAN)
    step = {"id": "plan", "operation": "motion.eye_plan", "inputs": {"blend": "$inputs.head", "geometry": "$inputs.eyes", "plan": "$inputs.plan"},
            "parameters": parameters, "outputs": outputs or {"blend": "motion.blend", "timeline": "timeline.json", "report": "motion.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "plan",
                                           "inputs": {"head": {"path": "head.blend"}, "eyes": {"path": "eyes.json"}, "plan": {"path": "plan.json"}},
                                           "steps": [step]})
    return tmp_path / "recipe.json"


def test_eye_plan_step_writes_the_timeline_and_keys(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, "start": 10}), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["plan"]
    timeline = json.loads(Path(step["artifacts"]["timeline"]).read_text(encoding="utf-8"))
    assert timeline["schema_version"] == "model3d-eye-timeline/0.1" and timeline["count"] == 154 and timeline["frames"][0]["frame"] == 10
    assert timeline["sections"][1]["caption"] == "blink slow" and timeline["sections"][1]["frames"] == [70, 103]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["x_settings"] == {"object": "Head", "radius": 0.1, "bones": None, "armature": None}
    assert report["x_first"] == {"frame": 10, "gaze": [0.0, 0.0], "keys": eye_plan.state(0.0, P)["keys"]}
    assert report["sections"][2] == {"frames": [104, 163], "speed": 1.0, "view": "front", "caption": "surprise and smile"}
    assert step["observations"][0]["count"] == 154 and step["observations"][0]["sections"] == 3


@pytest.mark.parametrize("parameters, plan, outputs, message", [
    ({"start": 1.5}, None, None, "start"), ({"bones": {"R": "eye"}}, None, None, "bones"), ({"spin": 1}, None, None, "知らないパラメーター"),
    ({}, {"schema_version": "other"}, None, "目の動きの台本"),
    ({}, {**PLAN, "sections": [[0.0, 0.01, 1.0, "front", "x"]]}, None, "コマが 1 つも"),
    ({}, None, {"blend": "m.blend", "report": "m.json"}, "timeline")])
def test_eye_plan_refuses_bad_settings(tmp_path, parameters, plan, outputs, message):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_recipe(tmp_path, {"blender": blender, **parameters}, plan, outputs), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["plan"]["error"]
