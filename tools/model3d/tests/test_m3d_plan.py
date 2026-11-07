"""plan.stage（段取り表 → トラック）と motion.bone_path（骨の通り道）の確かめ。値は作り物（どの曲の時刻とも関係ない）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import SCHEMAS, contracts
from model3d.io import ToolError, atomic_json
from model3d.stage_plan import Clock, build_track, ease, gauss, since, spans

FPS = 10


def keys(frames=40):
    return {"schema_version": "model3d-light-keys/0.1", "fps": FPS, "frames": frames, "grid": {"period": 0.5, "k0_time": 1.0},
            "low": [i / 100 for i in range(frames)], "mid": [0.4] * frames, "high": [0.6] * frames}


def bones(frames=range(1, 41), hips=(0.1, -0.2, 1.0), head=(0.1, -0.2, 1.6)):
    frames = list(frames)
    return {"schema_version": "model3d-bone-path/0.1", "unit": "m", "frames": frames,
            "bones": {"mixamorig:Hips": [list(hips)] * len(frames), "mixamorig:Head": [list(head)] * len(frames)}}


def plan(**parts):
    return {"schema_version": "model3d-stage-plan/0.1", **parts}


def frame(track, T):
    return next(f for f in track["frames"] if f["T"] == T)


def az_dist(fr):
    """カメラの位置と狙う点から、方位（度）と水平の距離を戻す。"""
    dx = fr["camera"]["location"][0] - fr["camera"]["target"][0]
    dy = fr["camera"]["location"][1] - fr["camera"]["target"][1]
    return math.degrees(math.atan2(dx, -dy)), math.hypot(dx, dy)


def test_the_three_contracts_are_registered_and_strict():
    assert SCHEMAS["stage-plan"] == "model3d-stage-plan/0.1" and SCHEMAS["light-keys"] == "model3d-light-keys/0.1"
    assert SCHEMAS["bone-path"] == "model3d-bone-path/0.1"
    for kind, doc in (("stage-plan", plan()), ("light-keys", keys()), ("bone-path", bones())):
        assert contracts.validate(kind, doc) and contracts.kind_of(doc) == kind
    for bad in (plan(camera={"shots": [{"target": {"hips": 0.6}}]}),                       # 腰の狙いには z が要る
                plan(camera={"shots": [{"target": {"head": 0, "point": [0, 0, 0]}}]}),
                plan(camera={"effects": [{"kind": "zoom", "at": 1}]}),
                plan(stage=[{"kind": "disco"}]), plan(lights={"wall": {"glow": 1}}), plan(lights={"rims": [{}]}),
                plan(lights={"dims": [{"from": 1, "until": 2, "rims": -1}]}), plan(wobble=1)):
        with pytest.raises(ToolError, match="stage-plan"):
            contracts.validate("stage-plan", bad)
    with pytest.raises(ToolError, match="light-keys"):
        contracts.validate("light-keys", {**keys(), "low": [1.5] * 40})
    with pytest.raises(ToolError, match="bone-path"):
        contracts.validate("bone-path", {**bones(), "unit": "cm"})


def test_the_clock_reads_seconds_marks_and_beats():
    clock = Clock({"drop": {"beat": 2}, "end": 3.5}, {"period": 0.5, "k0_time": 1.0})
    assert clock(1.25, "x") == 1.25 and clock("drop", "x") == 2.0 and clock("end", "x") == 3.5
    assert clock({"beat": -1}, "x") == 0.5 and clock.many(["drop", 1], "x") == [2.0, 1.0] and clock.many("end", "x") == [3.5]
    with pytest.raises(ToolError, match="marks にありません"):
        clock("nope", "x")


def test_parts_need_a_later_until_and_only_the_last_may_omit_it():
    clock = Clock({}, {"period": 0.5, "k0_time": 0.0})
    assert spans([{"until": 1}, {"until": 2}, {}], clock, "p") == [(0.0, 1.0), (1.0, 2.0), (2.0, math.inf)]
    for parts, message in (([{"until": 1}, {}, {"until": 3}], "until が要ります"), ([{"until": 2}, {"until": 1}], "より後"),
                           ([{"until": 0}], "より後")):
        with pytest.raises(ToolError, match=message):
            spans(parts, clock, "p")


def test_ease_gauss_and_since():
    assert ease(0.5) == 0.5 and ease(0.25) == 0.15625 and ease(2) == 1 and ease(-1) == 0 and ease(0.25, "linear") == 0.25
    flat = np.full((7, 3), 2.0)
    assert np.allclose(gauss(flat, 1.5), 2.0)
    step = np.array([[0.0]] * 6 + [[1.0]] * 6)
    w = np.exp(-0.5 * (np.arange(-3, 4) / 1.5) ** 2)                 # 既定の幅は ±round(2σ)
    assert gauss(step, 1.5)[6, 0] == pytest.approx(w[3:].sum() / w.sum())
    assert gauss(step, 1.5, radius=6)[6, 0] != pytest.approx(gauss(step, 1.5)[6, 0])
    assert since([1.0, 2.0], 2.5) == 0.5 and since([1.0], 0.5) == 99.0 and since([1.0], 1.0 - 5e-7) == pytest.approx(-5e-7)


def test_the_default_plan_follows_the_hips_from_the_front():
    track, report = build_track(plan(), keys(), bones())
    assert contracts.validate("stage-track", track) and track["fps"] == FPS and len(track["frames"]) == 40
    fr = frame(track, 1)
    assert fr["camera"]["target"] == pytest.approx([0.06, -0.12, 0.95])
    assert fr["camera"]["location"] == pytest.approx([0.06, -0.12 - 3.6, 1.1]) and fr["camera"]["lens"] == 35.0
    assert fr["stage"] == "led" and fr["tone"]["a"] == [0.30, 0.09, 1.0] and fr["tone"]["b"] == [1.0, 0.05, 0.45]
    assert report["T"] == [1, 40] and report["bones"] == {"hips": "mixamorig:Hips"} and report["shots"][0]["T"] == [1, 40]


def test_head_and_point_targets_and_heights():
    shots = [{"until": 1.0, "target": {"head": -0.1}, "height": {"head": 0.05}, "az": 90, "dist": 2.0},
             {"target": {"point": [1, 2, 3]}, "lens": 50}]
    track, report = build_track(plan(camera={"shots": shots}), keys(), bones())
    fr = frame(track, 1)
    assert fr["camera"]["target"] == pytest.approx([0.1, -0.2, 1.5])
    assert fr["camera"]["location"] == pytest.approx([2.1, -0.2, 1.65])
    fr = frame(track, 11)                                              # t = 1.0 秒から次の区間
    assert fr["camera"]["target"] == [1.0, 2.0, 3.0] and fr["camera"]["location"] == pytest.approx([1.0, 2.0 - 3.6, 1.1])
    assert fr["camera"]["lens"] == 50.0 and report["bones"] == {"head": "mixamorig:Head"}     # 腰はどの区間も使わない


def test_a_move_eases_from_the_shot_values_to_its_end_values():
    shots = [{"until": 2.0, "dist": 4.0, "to": {"az": 40, "dist": 2.0, "height": 2.0, "target": {"head": 0.0}}, "over": 1.0,
              "ease": "linear"}, {}]
    track, _ = build_track(plan(camera={"shots": shots}), keys(), bones())
    fr = frame(track, 6)                                               # t = 0.5 秒 → 半分
    assert az_dist(fr) == pytest.approx((20.0, 3.0))
    assert fr["camera"]["location"][2] == pytest.approx(1.1 + (2.0 - 1.1) * 0.5)
    assert fr["camera"]["target"] == pytest.approx([0.06 + (0.1 - 0.06) * 0.5, -0.12 + (-0.2 + 0.12) * 0.5, 0.95 + (1.6 - 0.95) * 0.5])
    fr = frame(track, 16)                                              # over の後は to のまま
    assert az_dist(fr) == pytest.approx((40.0, 2.0)) and fr["camera"]["target"] == pytest.approx([0.1, -0.2, 1.6])
    smooth, _ = build_track(plan(camera={"shots": [{"until": 2.0, "to": {"az": 40}, "over": 1.0}, {}]}), keys(), bones())
    assert az_dist(frame(smooth, 3))[0] == pytest.approx(40 * ease(0.2))     # 既定の ease は smooth


def test_whip_turns_and_pulls_in_then_returns():
    effects = [{"kind": "whip", "at": 2.0}]
    track, _ = build_track(plan(camera={"effects": effects}), keys(), bones())
    u = ease((1.9 - 1.7) / 0.3)
    assert az_dist(frame(track, 20)) == pytest.approx((6 * u, 3.6 * (1 - 0.35 * u)))      # 寄りながら回る
    assert az_dist(frame(track, 22)) == pytest.approx((6 + 4 * 0.1 / 0.15, 3.6 * 0.65))   # 寄ったまま回り続ける
    assert az_dist(frame(track, 25)) == pytest.approx((10 * 0.5, 3.6 * (0.65 + 0.35 * 0.5)))   # 戻る
    assert az_dist(frame(track, 30)) == pytest.approx((0.0, 3.6))


def test_push_shake_and_beat_push():
    effects = [{"kind": "push", "at": [1.0, 3.0], "amount": 0.1, "tau": 0.2, "length": 0.5}]
    track, _ = build_track(plan(camera={"effects": effects}), keys(), bones())
    assert az_dist(frame(track, 13))[1] == pytest.approx(3.6 * (1 - 0.1 * math.exp(-1)))
    assert az_dist(frame(track, 17))[1] == pytest.approx(3.6) and az_dist(frame(track, 31))[1] == pytest.approx(3.6 * 0.9)
    shake = [{"kind": "shake", "at": 1.0, "tau": 0.2, "length": 0.5, "x": [0.1, 10], "z": [0.05, 20]}]
    track, _ = build_track(plan(camera={"effects": shake}), keys(), bones())
    loc = frame(track, 12)["camera"]["location"]
    assert loc[0] == pytest.approx(0.06 + 0.1 * math.exp(-0.5) * math.sin(1.0))
    assert loc[2] == pytest.approx(1.1 + 0.05 * math.exp(-0.5) * math.cos(2.0))
    beats = [{"kind": "beat_push", "from": 0, "until": 3.0, "strong": 0.2, "weak": 0.1, "every": 2, "tau": 0.1}]
    track, _ = build_track(plan(camera={"effects": beats}), keys(), bones())
    assert az_dist(frame(track, 11))[1] == pytest.approx(3.6 * 0.8)          # 拍 0（every の倍数）
    assert az_dist(frame(track, 16))[1] == pytest.approx(3.6 * 0.9)          # 拍 1
    assert az_dist(frame(track, 17))[1] == pytest.approx(3.6 * (1 - 0.1 * math.exp(-1)))
    assert az_dist(frame(track, 31))[1] == pytest.approx(3.6)                # until の後
    quiet = plan(camera={"shots": [{"until": 2.5, "effects": False}, {}], "effects": effects})
    track, _ = build_track(quiet, keys(), bones())
    assert az_dist(frame(track, 13))[1] == pytest.approx(3.6) and az_dist(frame(track, 31))[1] == pytest.approx(3.6 * 0.9)


def test_a_frame_exactly_on_a_beat_gets_that_beat():
    # 30 fps・拍 0.4 秒・k0 0.1 秒では、T 52（1.7 秒）がちょうど拍 4。割り算は丸めで 4 に届かない
    assert math.floor((51 / 30 - 0.1) / 0.4) == 3
    on_beat = {**keys(90), "fps": 30, "grid": {"period": 0.4, "k0_time": 0.1}}
    beats = [{"kind": "beat_push", "from": 0, "until": 3.0}]
    track, _ = build_track(plan(camera={"effects": beats}), on_beat, bones(range(1, 91)))
    assert az_dist(frame(track, 52))[1] == pytest.approx(3.6 * (1 - 0.045))           # 拍 4（every 4 の倍数）の頭
    assert az_dist(frame(track, 53))[1] == pytest.approx(3.6 * (1 - 0.045 * math.exp(-(1 / 30) / 0.12)), rel=1e-6)
    marked = [{"kind": "push", "at": {"beat": 4}, "amount": 0.1}, {"kind": "shake", "at": {"beat": 4}, "x": [0.1, 10], "z": [0.05, 20]}]
    track, _ = build_track(plan(camera={"effects": marked}), on_beat, bones(range(1, 91)))
    fr = frame(track, 52)
    assert az_dist(fr)[1] == pytest.approx(3.6 * 0.9)                                  # 揺れの横は sin(0) = 0 から
    assert fr["camera"]["location"][2] == pytest.approx(1.1 + 0.05)                  # 揺れの上下は cos(0) から


def test_the_paths_are_smoothed_over_the_shown_action_frames():
    path = bones(range(1, 11))
    path["bones"]["mixamorig:Hips"] = [[float(f * f), 0.0, 1.0] for f in range(1, 11)]
    path["bones"]["mixamorig:Head"] = [[0.0, 0.0, float(f)] for f in range(1, 11)]
    cut = {"schema_version": "model3d-stage-track/0.1", "fps": FPS,
           "frames": [{"T": T, "f": f} for T, f in enumerate([3, 3, 5, 9, 2, 5], start=1)]}
    smooth = {"hips": {"sigma": 1.0}, "head": {"sigma": 1.0, "z": {"sigma": 2.0, "radius": 3}}}
    track, report = build_track(plan(camera={"smooth": smooth, "shots": [{"until": 0.2}, {"target": {"head": 0.0}}]}), keys(),
                                path, cut)
    shown = [2, 3, 5, 9]                                                # 重ねず小さい順
    hips = gauss(np.array([[f * f, 0.0, 1.0] for f in shown]), 1.0)
    assert frame(track, 1)["camera"]["target"][0] == pytest.approx(0.6 * hips[1, 0])     # f 3
    head_z = gauss(np.array([[float(f)] for f in shown]), 2.0, radius=3)[:, 0]
    assert frame(track, 3)["camera"]["target"][2] == pytest.approx(head_z[2])            # f 5
    assert [fr["f"] for fr in track["frames"]] == [3, 3, 5, 9, 2, 5] and report["action_frames"] == [2, 9]


def test_lights_follow_the_palette_level_signals_and_dims():
    lights = {"palette": [{"until": "m", "a": "red", "b": "blue"}, {"a": [0, 1, 0], "b": "red"}],
              "level": {"default": 1.0, "segments": [{"from": 0, "until": 1.0, "value": [0.5, 1.0], "ease": "linear"},
                                                     {"from": 3.0, "value": 0.25}]},
              "hit": {"frames": 5}, "punch": {"at": [1.0], "frames": 10},
              "dims": [{"from": 2.5, "until": 3.0, "rims": 0.5, "beams": {"level": 0.1}, "tone": 0.2}]}
    track, _ = build_track(plan(colors={"red": [1, 0, 0], "blue": [0, 0, 1]}, marks={"m": 2.0}, lights=lights), keys(), bones())
    fr = frame(track, 6)                     # t 0.5: 強さ 0.75、拍の上（hit 1）、印の前（punch 0）、low は 0.05
    assert fr["wall"]["strength"] == pytest.approx((0.12 + 0.9 * 0.05) * 0.75) and fr["wall"]["color"] == [1.0, 0.0, 0.0]
    assert [r["energy"] for r in fr["rims"]] == pytest.approx([(900 + 1800 * 0.4) * 0.75, (900 + 1800 * 0.6) * 0.75])
    assert [r["color"] for r in fr["rims"]] == [[1.0, 0.0, 0.0], [0.0, 0.0, 1.0]]
    assert fr["beams"]["gain"] == pytest.approx((0.9 + 2.0 + 1.2 * 0.6) * 0.75)
    assert fr["beams"]["colors"][:2] == [[0.0, 0.0, 1.0], [1.0, 0.0, 0.0]]
    assert fr["tone"] == {"a": [1.0, 0.0, 0.0], "b": [0.0, 0.0, 1.0], "gain": pytest.approx(0.35 + 0.5 * 0.4)}
    fr = frame(track, 13)                    # t 1.2: 印から 0.2 秒（punch 0.8）、拍から 0.2 秒（hit 0.6）、強さは default
    assert fr["wall"]["strength"] == pytest.approx(0.12 + 0.9 * 0.12 + 1.2 * 0.8)
    assert fr["beams"]["gain"] == pytest.approx(0.9 + 2.0 * 0.6 + 1.2 * 0.6)
    fr = frame(track, 28)                    # t 2.7: 落とす区間。色は次の区間
    assert fr["rims"][0]["energy"] == pytest.approx((900 + 1800 * 0.4) * 0.5) and fr["rims"][0]["color"] == [0.0, 1.0, 0.0]
    assert fr["beams"]["gain"] == pytest.approx((0.9 + 2.0 * 0.6 + 1.2 * 0.6) * 0.1)
    assert fr["tone"]["gain"] == pytest.approx((0.35 + 0.5 * 0.4) * 0.2) and fr["tone"]["b"] == [1.0, 0.0, 0.0]
    assert frame(track, 35)["rims"][1]["energy"] == pytest.approx((900 + 1800 * 0.6) * 0.25)


def test_the_stage_fade_closeup_scroll_and_beam_angles():
    stage = [{"until": 1.0, "kind": "void", "fade": [0, 1], "ease": "linear"}, {"until": 2.0, "kind": "led_diag"}, {"kind": "led"}]
    lights = {"wall": {"closeup": {"lens": 50, "scale": 0.5}, "scroll": 0.1, "low": 0.0}, "beams": {"phase": 1.0}}
    track, report = build_track(plan(stage=stage, lights=lights, camera={"shots": [{"until": 2.0}, {"lens": 60}]}), keys(), bones())
    assert frame(track, 6)["stage"] == "void" and frame(track, 6)["wall"]["strength"] == pytest.approx(0.12 * 0.5)
    assert frame(track, 15)["stage"] == "led_diag" and frame(track, 15)["wall"]["strength"] == pytest.approx(0.12)
    assert frame(track, 25)["stage"] == "led" and frame(track, 25)["wall"]["strength"] == pytest.approx(0.12 * 0.5)
    assert frame(track, 25)["wall"]["scroll"] == pytest.approx(2.5)
    rot = frame(track, 11)["beams"]["rotations"]                      # t 1.0 = phase → 拍 0
    for j in (0, 5):
        side = -1 if j < 4 else 1
        assert rot[j] == pytest.approx([math.radians(-(18 + 12 * math.sin(j))), math.radians(side * 12 + 24 * math.sin(j * 0.6)), 0])
    assert [s["kind"] for s in report["stage"]] == ["void", "led_diag", "led"] and report["stage"][1]["T"] == [11, 20]


@pytest.mark.parametrize("doc, message", [
    (plan(lights={"palette": [{"a": "red", "b": [0, 0, 1]}]}), "colors にありません"),
    (plan(camera={"shots": [{"until": "drop"}]}), "marks にありません"),
    (plan(camera={"shots": [{"until": 1.0}]}), "camera.shots"),
    (plan(lights={"wall": {"base": -5}}), "負"),
    (plan(camera={"shots": [{"to": {"az": 10}}]}), "over"),
    (plan(camera={"shots": [{"over": 1.0}]}), "to と一緒"),
    (plan(lights={"level": {"segments": [{"from": 1.0, "value": [0, 1]}]}}), "until が要ります"),
    (plan(stage=[{"kind": "led", "fade": [0, 1]}]), "fade"),
    (plan(lights={"dims": [{"from": 2.0, "until": 1.0}]}), "より後"),
])
def test_bad_plans_are_refused(doc, message):
    with pytest.raises(ToolError, match=message):
        build_track(contracts.validate("stage-plan", doc), keys(), bones())


def test_the_inputs_must_agree():
    with pytest.raises(ToolError, match="frames"):
        build_track(plan(), {**keys(), "low": [0.1] * 39}, bones())
    no_hips = bones()
    del no_hips["bones"]["mixamorig:Hips"]
    with pytest.raises(ToolError, match="mixamorig:Hips"):
        build_track(plan(), keys(), no_hips)
    assert build_track(plan(camera={"base": {"target": {"point": [0, 0, 1]}}}), keys(), no_hips)[1]["bones"] == {}
    cut = {"schema_version": "model3d-stage-track/0.1", "fps": FPS, "frames": [{"T": 1, "f": 99}]}
    with pytest.raises(ToolError, match="動きのコマがありません"):
        build_track(plan(), keys(), bones(), cut)
    with pytest.raises(ToolError, match="fps"):
        build_track(plan(), keys(), bones(), {**cut, "fps": 24, "frames": [{"T": 1, "f": 1}]})
    late = {**cut, "frames": [{"T": 45, "f": 1}]}                     # 光のキーより後のコマは最後の値を使い、数を報告する
    assert build_track(plan(), keys(), bones(), late)[1]["keys"]["beyond"] == 1


def _files(tmp_path, doc=None):
    atomic_json(tmp_path / "plan.json", doc or plan(camera={"shots": [{"name": "寄り", "until": 1.0, "dist": 2.0}, {"name": "引き"}]}))
    atomic_json(tmp_path / "keys.json", keys())
    atomic_json(tmp_path / "bones.json", bones())
    atomic_json(tmp_path / "cut.json", {"schema_version": "model3d-stage-track/0.1", "fps": FPS,
                                        "frames": [{"T": T, "f": T} for T in range(5, 16)]})


def _plan_recipe(tmp_path, outputs=None, parameters=None):
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "plan",
        "inputs": {"plan": {"path": "plan.json"}, "keys": {"path": "keys.json"}, "bones": {"path": "bones.json"},
                   "cut": {"path": "cut.json"}},
        "steps": [{"id": "plan", "operation": "plan.stage",
                   "inputs": {"plan": "$inputs.plan", "keys": "$inputs.keys", "bones": "$inputs.bones", "cut": "$inputs.cut"},
                   "parameters": parameters or {}, "outputs": outputs or {"track": "track.json", "report": "plan.json"}}]})
    return tmp_path / "recipe.json"


def test_the_plan_operation_writes_the_track_and_the_report(tmp_path):
    from model3d import executor
    _files(tmp_path)
    result = executor.run_recipe(_plan_recipe(tmp_path), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["plan"]
    track = contracts.load("stage-track", step["artifacts"]["track"])
    assert [fr["T"] for fr in track["frames"]] == list(range(5, 16))
    assert az_dist(frame(track, 5))[1] == pytest.approx(2.0) and az_dist(frame(track, 11))[1] == pytest.approx(3.6)
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert [(s["name"], s["T"], s["frames"]) for s in report["shots"]] == [("寄り", [5, 10], 6), ("引き", [11, 15], 5)]
    observation = step["observations"][0]
    assert observation["kind"] == "stage_plan" and observation["frames"] == 11 and observation["shots"] == 2
    again = executor.run_recipe(_plan_recipe(tmp_path), tmp_path / "runs")      # 計算だけなので、同じ入力なら再利用する
    assert again["steps"]["plan"]["reused_from"] is not None
    only = executor.run_recipe(_plan_recipe(tmp_path, {"track": "track.json"}), tmp_path / "runs2")
    assert only["status"] == "succeeded" and set(only["steps"]["plan"]["artifacts"]) == {"track"}


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def test_the_plan_operation_checks_its_parameters_and_inputs(tmp_path):
    from model3d.ops import get
    _files(tmp_path)
    op = get("plan.stage").function
    inputs = {name: tmp_path / f"{name}.json" for name in ("plan", "keys", "bones")}
    with pytest.raises(ToolError, match="知らないパラメーター"):
        op(inputs, {"wobble": 1}, {"track": tmp_path / "t.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="track"):
        op(inputs, {}, {"report": tmp_path / "r.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="bone_prefix"):
        op(inputs, {"bone_prefix": 1}, {"track": tmp_path / "t.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="mixamorig_x:Hips"):
        op(inputs, {"bone_prefix": "mixamorig_x:"}, {"track": tmp_path / "t.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="keys"):
        op({k: v for k, v in inputs.items() if k != "keys"}, {}, {"track": tmp_path / "t.json"}, Context(tmp_path))
    atomic_json(tmp_path / "plan.json", {"schema_version": "model3d-stage-plan/0.1", "camera": {"zoom": 2}})
    with pytest.raises(ToolError, match="stage-plan"):
        op(inputs, {}, {"track": tmp_path / "t.json"}, Context(tmp_path))


# 代役の Blender: --version に答え、ふだんは設定の bones と frames（null なら 1〜3）で、位置 (f, 0, 1) の通り道を書き、
# 受け取った引数と設定を notes に残す
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = json.load(open(value("--settings"), encoding="utf-8"))
frames = settings["frames"] or [1, 2, 3]
json.dump({"schema_version": "model3d-bone-path/0.1", "unit": "m", "scale_length": 1.0, "fps": 30, "armature": "rig",
           "frames": frames, "bones": {b: [[f, 0, 1] for f in frames] for b in settings["bones"]},
           "notes": json.dumps({"script": script, "argv": args, "settings": settings})}, open(value("--out"), "w"))
'''


def _bones_recipe(tmp_path, parameters, cut=False):
    (tmp_path / "dance.blend").write_bytes(b"moved")
    inputs, step_inputs = {"dance": {"path": "dance.blend"}}, {"blend": "$inputs.dance"}
    if cut:
        atomic_json(tmp_path / "cut.json", {"schema_version": "model3d-stage-track/0.1",
                                            "frames": [{"T": 1, "f": 7}, {"T": 2, "f": 4}, {"T": 3, "f": 7}]})
        inputs["cut"] = {"path": "cut.json"}
        step_inputs["track"] = "$inputs.cut"
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "bones", "inputs": inputs,
                                           "steps": [{"id": "bones", "operation": "motion.bone_path", "inputs": step_inputs,
                                                      "parameters": parameters, "outputs": {"path": "bones.json"}}]})
    return tmp_path / "recipe.json"


def test_the_bone_path_operation_measures_the_shown_frames(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    result = executor.run_recipe(_bones_recipe(tmp_path, {"blender": blender, "bone_prefix": "rig:"}, cut=True), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["bones"]
    path = contracts.load("bone-path", step["artifacts"]["path"])
    notes = json.loads(path["notes"])
    assert Path(notes["script"]).name == "bone_path.py"
    assert notes["settings"] == {"bones": ["rig:Hips", "rig:Head"], "frames": [4, 7], "armature": None}
    observation = step["observations"][0]
    assert observation["kind"] == "bone_path" and observation["frames"] == 2 and observation["bones"] == ["rig:Head", "rig:Hips"]
    span = executor.run_recipe(_bones_recipe(tmp_path, {"blender": blender, "frame_range": [3, 5], "bones": ["Spine"],
                                                        "armature": "rig"}), tmp_path / "runs2")
    notes = json.loads(contracts.load("bone-path", span["steps"]["bones"]["artifacts"]["path"])["notes"])
    assert notes["settings"] == {"bones": ["mixamorig:Spine"], "frames": [3, 4, 5], "armature": "rig"}
    scene = executor.run_recipe(_bones_recipe(tmp_path, {"blender": blender}), tmp_path / "runs3")
    assert json.loads(contracts.load("bone-path", scene["steps"]["bones"]["artifacts"]["path"])["notes"])["settings"]["frames"] is None


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"bones": []}, "bones"),
    ({"bones": ["Hips", "Hips"]}, "bones"),
    ({"armature": ""}, "armature"),
    ({"frame_range": [5, 3]}, "frame_range"),
    ({"frame_range": [1.5, 3]}, "frame_range"),
    ({"bone_prefix": 2}, "bone_prefix"),
])
def test_the_bone_path_operation_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"moved")
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    with pytest.raises(ToolError, match=message):
        get("motion.bone_path").function({"blend": tmp_path / "dance.blend"}, {"blender": blender, **parameters},
                                         {"path": tmp_path / "p.json"}, Context(tmp_path))


def test_the_bone_path_operation_needs_blender_an_output_and_one_frame_source(tmp_path):
    from model3d.ops import get
    op = get("motion.bone_path").function
    (tmp_path / "dance.blend").write_bytes(b"moved")
    atomic_json(tmp_path / "cut.json", {"schema_version": "model3d-stage-track/0.1", "frames": [{"T": 1, "f": 1}]})
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    with pytest.raises(ToolError, match="Blender"):
        op({"blend": tmp_path / "dance.blend"}, {}, {"path": tmp_path / "p.json"}, Context(tmp_path))
    with pytest.raises(ToolError, match="path"):
        op({"blend": tmp_path / "dance.blend"}, {"blender": blender}, {}, Context(tmp_path))
    with pytest.raises(ToolError, match="一緒に使えません"):
        op({"blend": tmp_path / "dance.blend", "track": tmp_path / "cut.json"}, {"blender": blender, "frame_range": [1, 2]},
           {"path": tmp_path / "p.json"}, Context(tmp_path))
