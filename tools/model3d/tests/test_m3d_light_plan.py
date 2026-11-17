"""plan.lights（光の台本）: 小さな式の読み方と求め方、区間・let・set・閃光、拍の hit と音の帯、契約と工程の受け渡し。
値はどれも作り物（Blender なし）。3 作目の VRM のダンスの台本との突き合わせ（909 コマで同じ値）は docs/model3d.md
（リポジトリ外の台本なので CI には無い）。"""
import math

import pytest

from model3d import contracts, light_plan
from model3d.io import ToolError, atomic_json

GRID = {"period": 0.5, "k0_time": 0.25}


def _plan(values=None, segments=None, **extra):
    return {"schema_version": "model3d-light-plan/0.1", "fps": 10, "frames": 30, "grid": GRID,
            "colors": {"WHITE": [1.0, 1.0, 1.0], "RED": [1.0, 0.0, 0.0]},
            "values": values or {"key": 0.0, "key_color": "WHITE"}, "segments": segments or [{}], **extra}


def _keys(frames=20):
    return {"schema_version": "model3d-light-keys/0.1", "fps": 10, "frames": frames, "grid": GRID,
            "low": [round(0.05 * k, 3) for k in range(frames)], "mid": [0.5] * frames, "high": [0.25] * frames}


def _at(plan, T, keys=None):
    return light_plan.Plan(plan, keys).at(T)[0]


# ---- 式 ----

def test_expressions_follow_the_prototype_formulas():
    plan = _plan({"a": "ramp(t, 1.0, 2.0)", "b": "ease((t - 1.0) / 2.0, 'out')", "c": "ease(0.5, 'in')", "d": "ease(-1.0, 'linear')",
                  "e": "mix(0.2, 1.2, 0.25)", "f": "max(0.1, t, 0.3)", "g": "min(t, 0.2)", "h": "exp(-t / 0.5)", "i": "beat(2)",
                  "j": "1 - ramp(t, 1.0, 1.0)", "k": "0.8 * exp(-(t - 1.2) / 0.1) if t - 1.2 < 0.6 else 0.0",
                  "m": "1.0 if 0 <= t - 1.0 < 0.5 else 2.0", "n": "-t + +0.5"})
    v = _at(plan, 16)                                     # t = 1.5
    assert v["a"] == 0.5 * 0.5 * (3 - 2 * 0.5) and v["b"] == 1 - (1 - 0.25) * (1 - 0.25) and v["c"] == 0.25 and v["d"] == 0.0
    assert v["e"] == 0.2 + (1.2 - 0.2) * 0.25 and v["f"] == 1.5 and v["g"] == 0.2 and v["h"] == math.exp(-1.5 / 0.5)
    assert v["i"] == 0.25 + 0.5 * 2 and v["j"] == 0.0                                  # 幅 0 の ramp は 1e-6 で割る
    assert v["k"] == 0.8 * math.exp(-(1.5 - 1.2) / 0.1) and v["m"] == 2.0 and v["n"] == -1.0   # 0 <= 0.5 < 0.5 は成り立たない
    assert _at(plan, 13)["m"] == 1.0                      # t = 1.2
    later = _at(plan, 22)                                 # t = 2.1
    assert later["k"] == 0.0 and later["m"] == 2.0


def test_vectors_are_lists_mixes_and_scaled_names():
    plan = _plan({"key_color": "WHITE", "warm": "mix(WHITE, RED, 0.25)", "dim": "RED * 0.5", "half": "0.5 * RED",
                  "pos": [0.25, "-3.0 + 0.5 * t", 0.25], "sum": "WHITE - RED", "div": "WHITE / 4"})
    v = _at(plan, 11)                                     # t = 1.0
    assert v["key_color"] == (1.0, 1.0, 1.0) and v["warm"] == (1.0, 0.75, 0.75)
    assert v["dim"] == v["half"] == (0.5, 0.0, 0.0) and v["pos"] == (0.25, -2.5, 0.25)
    assert v["sum"] == (0.0, 1.0, 1.0) and v["div"] == (0.25, 0.25, 0.25)


@pytest.mark.parametrize("text, message", [
    ("t.real", "書き方"), ("WHITE[0]", "書き方"), ("T", "名前 'T'"), ("__import__('os')", "呼び出し"), ("abs(t)", "呼び出し"),
    ("eval('1')", "呼び出し"), ("max(t)", "引数の数"), ("ease(t, kind='in')", "呼び出し"), ("ease(t, 'up')", "つなぎ方"),
    ("ease(t, k)", "つなぎ方"), ("'text'", "定数"), ("True", "定数"), ("t ** 2", "演算"), ("t % 2", "演算"), ("t // 2", "演算"),
    ("t and 1", "書き方"), ("not t", "演算"), ("t < 1", "比べる式"), ("1 + (t < 1)", "比べる式"), ("1 if t else 0", "if の条件"),
    ("lambda: 1", "書き方"), ("[t for t in WHITE]", "書き方"), ("1 if t == 1 else 0", "比べ方"), ("max(*WHITE)", "引数"),
    ("(t := 1)", "書き方"), ("1 +", "読めません"),
])
def test_only_the_listed_words_and_forms_are_read(text, message):
    with pytest.raises(ToolError, match=message):
        light_plan.Plan(_plan({"key": text}))


@pytest.mark.parametrize("value, message", [
    ("WHITE + 1", "掛けるか割る"), ("1 - WHITE", "掛ける"), ("mix(WHITE, 1, 0.5)", "数どうし"), ("ramp(WHITE, 0, 1)", "数が要ります"),
    ("1 / (t - t)", "求められません"), ("exp(1000)", "求められません"), ("1 if WHITE < 1 else 0", "数どうし"),
])
def test_values_that_cannot_be_computed_stop_with_the_frame(value, message):
    with pytest.raises(ToolError, match=message):
        _at(_plan({"key": value}), 1)


# ---- 区間・let・set・閃光 ----

def test_segments_let_and_set_override_the_defaults():
    plan = _plan({"key": 0.0, "key_color": "WHITE", "spin": "t * 2.0"},
                 [{"until": {"beat": 1}, "let": {"up": "ramp(t, 0.0, 0.5)", "twice": "2 * up"}, "set": {"key": "twice", "key_color": "RED"}},
                  {"until": "drop", "set": {"key": 0.5}}, {}], marks={"drop": 2.0})
    first, n = light_plan.Plan(plan).at(3)                # t = 0.2（拍 1 は 0.75 秒）
    assert n == 0 and first["key"] == 2 * (0.4 * 0.4 * (3 - 2 * 0.4)) and first["key_color"] == (1.0, 0.0, 0.0)
    assert first["spin"] == 0.2 * 2.0                    # 上書きしない値は既定のまま
    second, n = light_plan.Plan(plan).at(9)               # t = 0.8
    assert n == 1 and second == {**second, "key": 0.5, "key_color": (1.0, 1.0, 1.0)}
    third, n = light_plan.Plan(plan).at(21)               # t = 2.0 はちょうど印の時刻なので、次の区間
    assert n == 2 and third["key"] == 0.0


@pytest.mark.parametrize("segments, message", [
    ([{"set": {"wobble": 1.0}}], "values にありません"),
    ([{"let": {"b": "a", "a": "1.0"}}], "名前 'a'"),
    ([{"let": {"t": "1.0"}}], "重なります"), ([{"let": {"WHITE": "1.0"}}], "重なります"), ([{"let": {"max": "1.0"}}], "重なります"),
    ([{"let": {"x y": "1.0"}}], "英字"),
    ([{"until": 1.0}, {"until": 0.5}], "until"), ([{"until": 1.0}, {}, {}], "until が要ります"),
    ([{"until": "nope"}], "印"),
])
def test_bad_segments_are_refused(segments, message):
    with pytest.raises(ToolError, match=message):
        light_plan.Plan(_plan(segments=segments))


def test_a_set_must_keep_the_shape_of_the_value():
    with pytest.raises(ToolError, match="同じ形"):
        _at(_plan(segments=[{"set": {"key": "WHITE"}}]), 1)


def test_the_strongest_flash_wins_and_keeps_its_colour():
    plan = _plan({"key": 0.0, "flash_color": "WHITE"},
                 [{"flashes": [{"at": 1.0, "strength": 0.5}, {"at": 1.1, "strength": 0.9, "color": "RED", "decay": 0.2},
                               {"at": 1.1, "strength": 0.9, "color": "WHITE", "decay": 0.2}, {"at": 0.0, "strength": 5.0, "length": 0.3}]}])
    v = _at(plan, 12)                                     # t = 1.1: 0.5 × e^-1 と 0.9 × e^0
    assert v["flash"] == 0.9 and v["flash_color"] == (1.0, 0.0, 0.0)               # 同じ強さなら先の閃光（強い方だけ置き換える）
    early = _at(plan, 11)                                 # t = 1.0: 色を省いた閃光は values の flash_color
    assert early["flash"] == 0.5 and early["flash_color"] == (1.0, 1.0, 1.0)
    assert _at(plan, 4)["flash"] == 0.0                   # t = 0.3 は at 0 の長さ 0.3 の外（at ≤ t < at + length）
    assert _at(plan, 3)["flash"] == 5.0 * math.exp(-0.2 / 0.10)
    assert _at(plan, 19)["flash"] == 0.0                  # t = 1.8: どの閃光も長さ 0.6 秒を過ぎた


def test_a_flash_on_a_beat_starts_on_the_frame_of_the_beat():
    """拍の時刻の丸め（0.1 + 0.1 × 2 = 0.30000000000000004）でも、ちょうど拍のコマ（t = 0.3）から光る（区間と同じ境目のゆとり）。"""
    plan = {**_plan({"key": 0.0}, [{"flashes": [{"at": {"beat": 2}, "strength": 1.0, "length": 0.3}]}]),
            "grid": {"period": 0.1, "k0_time": 0.1}}
    p = light_plan.Plan(plan)
    assert p.clock({"beat": 2}, "at") > 0.3                                   # 拍の時刻は 0.3 より少し後
    assert [p.at(T)[0]["flash"] for T in (3, 4, 5, 6, 7)] == [0.0, 1.0, math.exp(-(0.4 - p.clock({"beat": 2}, "at")) / 0.10),
                                                           math.exp(-(0.5 - p.clock({"beat": 2}, "at")) / 0.10), 0.0]   # 長さ 0.3 秒は 3 コマ


def test_flash_and_fade_are_always_there_and_must_not_be_negative():
    v = _at(_plan({"key": 0.0}), 1)
    assert v["flash"] == 0.0 and v["flash_color"] == (1.0, 1.0, 1.0) and v["fade"] == 1.0
    with pytest.raises(ToolError, match="fade は 0 以上"):
        _at(_plan({"fade": "0.5 - t"}), 11)
    with pytest.raises(ToolError, match="flash_color は 3 つの並び"):
        _at(_plan({"flash_color": 1.0}), 1)


# ---- 拍・音の帯・格子 ----

def test_hit_and_the_bands_follow_the_grid_and_the_keys():
    keys = _keys()
    v = _at(_plan({"h": "hit", "l": "low", "m": "mid", "x": "high"}, hit={"tau": 0.2}), 11, keys)   # t = 1.0
    assert v["h"] == math.exp(-((1.0 - 0.25) % 0.5) / 0.2) and v["l"] == keys["low"][10] and v["m"] == 0.5 and v["x"] == 0.25
    beyond = _at(_plan({"l": "low"}), 30, keys)          # 光のキーより後のコマは最後の値
    assert beyond["l"] == keys["low"][-1]
    before = _at(_plan({"h": "hit"}), 1)                  # 拍 0 より前は、前の拍（の格子）から
    assert before["h"] == math.exp(-((0.0 - 0.25) % 0.5) / 0.13)
    assert _at(_plan({"l": "low"}), 1)["l"] == 0.0        # 光のキーが無ければ帯は 0


def test_hit_is_one_on_the_frame_of_the_beat():
    """ちょうど拍のコマ（t = 0.7。拍 1 は 0.2 + 0.5）で、(0.7 − 0.2) mod 0.5 が丸めでほぼ 0.5 になっても、hit は 1（区間と同じ境目のゆとり）。"""
    plan = {**_plan({"pulse": "hit"}), "grid": {"period": 0.5, "k0_time": 0.2}}
    assert 0.5 - (0.7 - 0.2) % 0.5 < 1e-9                                      # 丸めの罠（そのままなら hit は 0.02 ほど）
    p = light_plan.Plan(plan)
    assert [p.at(T)[0]["pulse"] for T in (8, 9)] == [1.0, math.exp(-((0.8 - 0.2) % 0.5) / 0.13)]   # 次のコマは拍の頭から減る
    assert p.at(7)[0]["pulse"] == math.exp(-((0.6 - 0.2) % 0.5) / 0.13)                          # 拍の前のコマは前の拍から


@pytest.mark.parametrize("band, values", [("low", [0.0]), ("mid", [0.1, 0.5]), ("high", [0.1, 0.5, 0.9, 0.2])])
def test_light_keys_with_bands_of_another_length_are_refused(band, values):
    """帯の数が frames と違う光のキー（契約は数のそろいまでは見ない）は、黙って値を止めたり読めなくなったりせず、止める。"""
    keys = {**_keys(3), band: values}
    with pytest.raises(ToolError, match=f"光のキーの {band} の数（{len(values)}）が frames（3）と違います"):
        light_plan.Plan(_plan(), keys)


def test_the_grid_fps_and_frames_come_from_the_keys_or_the_plan():
    keys = _keys(25)
    p = light_plan.Plan({**_plan(), "frames": None, "fps": None, "grid": None}, keys)
    assert p.frames == 25 and p.fps == 10 and p.grid == GRID
    with pytest.raises(ToolError, match="grid"):
        light_plan.Plan({**_plan(), "grid": {"period": 0.4, "k0_time": 0.25}}, keys)
    with pytest.raises(ToolError, match="fps"):
        light_plan.Plan({**_plan(), "fps": 24}, keys)
    with pytest.raises(ToolError, match="grid"):
        light_plan.Plan({**_plan(), "grid": None})
    with pytest.raises(ToolError, match="frames"):
        light_plan.Plan({**_plan(), "frames": None})


# ---- 契約と工程 ----

def test_build_writes_a_track_and_a_report_that_fit_the_contracts():
    plan = _plan({"key": 0.0, "key_color": "WHITE"}, [{"until": 1.0, "set": {"key": 1.0}, "flashes": [{"at": 0.5, "strength": 0.4}]},
                                                     {"set": {"fade": "1 - ramp(t, 2.0, 2.9)"}}])
    contracts.validate("light-plan", plan)
    track, report = light_plan.build(plan, _keys())
    contracts.validate("light-track", track)
    assert [fr["T"] for fr in track["frames"]] == list(range(1, 31)) and track["fps"] == 10 and track["grid"] == GRID
    assert track["frames"][0]["values"] == {"key": 1.0, "key_color": [1.0, 1.0, 1.0], "flash": 0.0, "flash_color": [1.0, 1.0, 1.0],
                                            "fade": 1.0}
    assert report["segments"][0]["T"] == [1, 10] and report["segments"][1]["T"] == [11, 30]
    assert report["flash_frames"] == 5 and report["fade_frames"] == 9 and report["keys"] == {"frames": 20, "beyond": 10}
    assert report["ranges"]["key"] == {"min": 0.0, "max": 1.0}


@pytest.mark.parametrize("change, message", [
    ({"values": {}}, "values"), ({"segments": []}, "segments"), ({"wobble": 1}, "知らない鍵"),
    ({"values": {"key": True}}, "values"), ({"values": {"key": [1, 2]}}, "values"), ({"colors": {"WHITE": [1, 1]}}, "colors"),
    ({"segments": [{"flashes": [{"at": 1.0}]}]}, "strength"), ({"segments": [{"flashes": [{"at": 1.0, "strength": 1, "size": 2}]}]}, "知らない鍵"),
])
def test_the_light_plan_contract_refuses_bad_shapes(change, message):
    with pytest.raises(ToolError, match=message):
        contracts.validate("light-plan", {**_plan(), **change})


def test_the_plan_lights_operation_writes_its_outputs(tmp_path):
    from model3d import executor
    atomic_json(tmp_path / "plan.json", _plan({"key": "0.5 + 0.5 * hit", "key_color": "WHITE"}))
    atomic_json(tmp_path / "keys.json", _keys())
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "lights",
                                           "inputs": {"plan": {"path": "plan.json"}, "keys": {"path": "keys.json"}},
                                           "steps": [{"id": "lights", "operation": "plan.lights",
                                                      "inputs": {"plan": "$inputs.plan", "keys": "$inputs.keys"},
                                                      "outputs": {"track": "track.json", "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    step = result["steps"]["lights"]
    track = contracts.load("light-track", step["artifacts"]["track"])
    assert len(track["frames"]) == 30 and track["frames"][5]["values"]["key"] == 0.5 + 0.5 * math.exp(-((0.5 - 0.25) % 0.5) / 0.13)
    observation = step["observations"][0]
    assert observation["kind"] == "light_plan" and observation["frames"] == 30 and observation["beyond_keys"] == 10
    again = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert again["steps"]["lights"]["reused_from"] is not None                    # 計算だけなので、同じ入力なら使い回す


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def test_the_plan_lights_operation_checks_its_inputs(tmp_path):
    from model3d.ops import get
    op = get("plan.lights").function
    atomic_json(tmp_path / "plan.json", _plan())
    outputs = {"track": tmp_path / "track.json"}
    with pytest.raises(ToolError, match="入力 plan"):
        op({}, {}, outputs, Context(tmp_path))
    with pytest.raises(ToolError, match="知らないパラメーター"):
        op({"plan": tmp_path / "plan.json"}, {"frames": 3}, outputs, Context(tmp_path))
    with pytest.raises(ToolError, match="track"):
        op({"plan": tmp_path / "plan.json"}, {}, {"report": tmp_path / "r.json"}, Context(tmp_path))
    op({"plan": tmp_path / "plan.json"}, {}, outputs, Context(tmp_path))                 # 光のキーは省ける
    assert len(contracts.load("light-track", outputs["track"])["frames"]) == 30
