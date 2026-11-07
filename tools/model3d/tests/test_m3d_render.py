"""render.stage: 設定の重ね方・コマの並び・腰のならし・カメラの位置・輪郭線の太さ・光の値の重ね方（Blender なしで動く部分）と、
台本の引数と工程の受け渡し・描いたコマの一覧（代役の Blender）を確かめる。舞台を作って描く所は本物の Blender が要るので、
CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import stage_render
from model3d.io import ToolError, atomic_json


def test_merged_fills_defaults_and_keeps_each_section_key():
    settings = stage_render.merged({"samples": 8, "camera": {"lens": 50.0}, "outline": {"scale": {"hair": 0.7}}})
    assert settings["samples"] == 8 and settings["resolution"] == [1080, 1920]
    assert settings["camera"]["lens"] == 50.0 and settings["camera"]["dist"] == 3.6 and settings["camera"]["follow_sigma"] == 6.0
    assert settings["outline"] == {"line_px": 2.4, "reference_height": 1920, "scale": {"hair": 0.7}}
    assert settings["stage"]["enabled"] is True and stage_render.DEFAULTS["camera"]["lens"] == 35.0


def test_frame_plan_follows_the_track_or_the_scene():
    track = {"frames": [{"T": 5, "f": 12}, {"T": 6, "f": 12, "stage": "void"}, {"T": 7, "f": 10}]}
    plan = stage_render.frame_plan(track, 1, 100)
    assert [(T, f) for T, f, _ in plan] == [(5, 12), (6, 12), (7, 10)] and plan[1][2]["stage"] == "void"
    assert [(T, f) for T, f, _ in stage_render.frame_plan(None, 3, 5)] == [(1, 3), (2, 4), (3, 5)]
    assert stage_render.frame_name(843) == "f0843.png"


def test_gauss_smooth_pads_the_ends_and_keeps_straight_lines():
    x = np.stack([np.full(20, 2.0), np.arange(20.0)], axis=1)
    out = stage_render.gauss_smooth(x, 2.0)
    assert out.shape == (20, 2) and np.allclose(out[:, 0], 2.0)
    assert np.allclose(out[6:14, 1], np.arange(6.0, 14.0))         # 端から 2σ より中は、まっすぐな線のまま
    assert out[0, 1] > 0.0                                           # 端は端の値で延ばすので、0 より少し上に寄る


def test_orbit_places_the_camera_around_the_target():
    assert stage_render.orbit((0.1, 0.2, 0.95), 0.0, 3.6, 1.1) == pytest.approx((0.1, 0.2 - 3.6, 1.1))   # 0 は -Y 側の正面
    assert stage_render.orbit((0.0, 0.0, 0.95), 90.0, 2.0, 1.2) == pytest.approx((2.0, 0.0, 1.2))       # 正は +X 側
    assert stage_render.orbit((0.0, 0.0, 0.95), -90.0, 2.0, 1.2) == pytest.approx((-2.0, 0.0, 1.2))     # キャラの右の真横


def test_outline_width_keeps_the_line_the_same_on_screen():
    w = stage_render.outline_width(3.6, 35.0, 24.0, 1.6, 1920, 1920)
    assert w == pytest.approx(1.6 * 24.0 * 3.6 / (1920 * 35.0)) and w == pytest.approx(0.0020571, abs=1e-6)
    assert stage_render.outline_width(3.6, 35.0, 24.0, 1.6, 1920, 1080) == pytest.approx(w)      # 解像度によらない
    assert stage_render.outline_width(1.8, 50.0, 24.0, 1.6, 1920, 1920) == pytest.approx(w * 0.5 * 35.0 / 50.0)


def test_hull_strength_converts_blender_units_to_the_hull_units():
    width = stage_render.outline_width(360.0, 35.0, 24.0, 2.4, 1920, 192)     # 1 単位 = 1 cm のシーンで 3.6 m 先
    assert width == pytest.approx(0.30857, abs=1e-5)                           # Blender 単位（cm）で 0.31 = 3.1 mm
    assert stage_render.hull_strength(width, 0.01, 0.01, 1.0) == pytest.approx(-width)          # 拡大 1 の写しの中の単位も cm
    assert stage_render.hull_strength(width, 0.01, 0.005, 1.0) == pytest.approx(-2 * width)     # 0.5 倍の写しは中の単位で 2 倍
    assert stage_render.hull_strength(0.002, 1.0, 1.0, 0.7) == pytest.approx(-0.0014)           # m のシーン、拡大 1、髪の 0.7 倍


def test_light_energy_keeps_the_brightness_in_any_scene_unit():
    assert stage_render.light_energy(2600, 1.0) == 2600
    assert stage_render.light_energy(2600, 0.01) == pytest.approx(2600 * 1e4)   # 1 単位 = 1 cm: 距離が 100 倍なので 1 万倍


def test_tone_values_fall_back_to_the_first_values_not_the_previous_frame():
    initial = {"a": [0.3, 0.09, 1.0], "b": [1.0, 0.05, 0.45], "gain": 0.35}
    assert stage_render.tone_values({"tone": {"gain": 2.0}}, initial) == {**initial, "gain": 2.0}
    assert stage_render.tone_values({}, initial) == initial                    # 省いたコマは最初の値
    assert stage_render.tone_values({"tone": {"a": [1, 1, 1]}}, initial)["gain"] == 0.35


def test_frame_values_layer_the_track_over_the_static_lights():
    static = stage_render.static_values(((0.3, 0.09, 1.0), (1.0, 0.05, 0.45)), 0.02)
    assert static["wall"]["dark"] == pytest.approx([0.006, 0.0018, 0.02]) and static["rims"][1]["color"] == [1.0, 0.05, 0.45]
    assert static["beams"]["colors"][0] == [1.0, 0.05, 0.45] and static["beams"]["colors"][1] == [0.3, 0.09, 1.0]
    assert static["beams"]["rotations"][0][1] == pytest.approx(math.radians(-12)) and static["beams"]["rotations"][7][1] > 0
    v = stage_render.frame_values({"stage": "led_diag", "wall": {"strength": 2.0, "color": [0.05, 0.6, 1.0]},
                                   "rims": [{"energy": 900.0}, {}], "beams": {"gain": 1.5}}, static, 0.02)
    assert v["stage"] == "led_diag" and v["wall"]["strength"] == 2.0 and v["wall"]["dark"] == pytest.approx([0.001, 0.012, 0.02])
    assert v["rims"][0] == {"energy": 900.0, "color": [0.3, 0.09, 1.0]} and v["rims"][1]["energy"] == 1500.0
    assert v["beams"]["gain"] == 1.5 and v["beams"]["rotations"] == static["beams"]["rotations"]
    kept = stage_render.frame_values({"wall": {"color": [1, 1, 1], "dark": [0.5, 0.5, 0.5]}}, static, 0.02)
    assert kept["wall"]["dark"] == [0.5, 0.5, 0.5] and kept["stage"] == "led"         # 暗い帯の色を渡せばそのまま


def test_parse_args():
    args = stage_render.parse_args(["--blend", "i.blend", "--settings", "s.json", "--frames-dir", "f", "--report", "r.json"])
    assert args.track is None and args.frames_dir == "f"


def _track(frames):
    return {"schema_version": "model3d-stage-track/0.1", "fps": 30, "frames": frames}


def test_the_track_and_frames_contracts():
    beams = {"gain": 1.0, "rotations": [[0.1, 0.2, 0.0]] * 8, "colors": [[1.0, 0.0, 0.0]] * 8}
    frame = {"T": 1, "f": 3, "camera": {"location": [0, -3.6, 1.1], "target": [0, 0, 0.95], "lens": 35},
             "stage": "led", "tone": {"a": [0.3, 0.09, 1.0], "b": [1, 0.05, 0.45], "gain": 0.5},
             "wall": {"strength": 1.0, "color": [1, 1, 1], "dark": [0.02, 0.02, 0.02], "scroll": -0.04},
             "rims": [{"energy": 900, "color": [1, 0, 0]}, {"energy": 900, "color": [0, 0, 1]}], "beams": beams}
    assert contracts.validate("stage-track", _track([frame]))
    assert contracts.kind_of(_track([frame])) == "stage-track"
    for bad in ({**frame, "stage": "disco"}, {**frame, "beams": {**beams, "colors": [[1, 0, 0]] * 7}},
                {**frame, "camera": {"location": [0, 0, 0]}}, {**frame, "T": 0}, {**frame, "rims": [frame["rims"][0]]}):
        with pytest.raises(ToolError, match="stage-track"):
            contracts.validate("stage-track", _track([bad]))
    manifest = {"schema_version": "model3d-frames/0.1", "directory": "x", "fps": 30, "resolution": [1080, 1920],
                "frames": [{"T": 1, "f": 3, "file": "f0001.png", "sha256": "0" * 64, "bytes": 10}], "missing": [2]}
    assert contracts.validate("frames", manifest) and contracts.kind_of(manifest) == "frames"


# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読み、並びのコマ（トラックか 1〜3）のうち only のコマに
# 印の PNG を書き（すでにあるファイルはとばす）、受け取った引数と設定を報告に残す
STAND_IN = r'''
import json, os, sys
if "--version" in sys.argv:
    print("Blender 4.5.15")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = json.load(open(value("--settings"), encoding="utf-8"))
track = json.load(open(value("--track"), encoding="utf-8")) if value("--track") else None
plan = [(fr["T"], fr["f"]) for fr in track["frames"]] if track else [(1, 1), (2, 2), (3, 3)]
only = set(settings.get("only") or [T for T, _ in plan])
rendered, existing = [], []
for T, f in plan:
    path = os.path.join(value("--frames-dir"), "f%04d.png" % T)
    if T not in only:
        continue
    if os.path.exists(path):
        existing.append(T)
        continue
    open(path, "wb").write(b"png %d" % T)
    rendered.append(T)
json.dump({"schema_version": "model3d-stage-render/0.1", "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered,
           "skipped_existing": existing, "fps": (track or {}).get("fps", 24), "resolution": settings.get("resolution", [1080, 1920]),
           "argv": args, "script": script, "settings": settings}, open(value("--report"), "w"))
'''


def _recipe(tmp_path, params, track=None):
    (tmp_path / "dance.blend").write_bytes(b"looked")
    inputs, step_inputs = {"dance": {"path": "dance.blend"}}, {"blend": "$inputs.dance"}
    if track is not None:
        atomic_json(tmp_path / "track.json", track)
        inputs["track"] = {"path": "track.json"}
        step_inputs["track"] = "$inputs.track"
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "render",
                                           "inputs": inputs,
                                           "steps": [{"id": "render", "operation": "render.stage", "inputs": step_inputs,
                                                      "parameters": params,
                                                      "outputs": {"frames": "frames.json", "report": "render.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_renders_the_track_and_lists_the_frames(tmp_path):
    from model3d import executor
    frames_dir = tmp_path / "out_frames"
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "samples": 16, "resolution": [540, 960],
              "camera": {"follow_sigma": 24}, "outline": {"line_px": 1.6, "scale": {"hair": 0.7}},
              "frames_dir": str(frames_dir), "only": [843, 845]}
    track = _track([{"T": 843, "f": 1}, {"T": 844, "f": 2}, {"T": 845, "f": 2}])
    result = executor.run_recipe(_recipe(tmp_path, params, track), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["render"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert Path(report["script"]).name == "stage_render.py" and "--track" in report["argv"]
    assert report["settings"] == {"samples": 16, "resolution": [540, 960], "only": [843, 845], "camera": {"follow_sigma": 24.0},
                                  "outline": {"line_px": 1.6, "scale": {"hair": 0.7}}, "stage": {}}
    manifest = json.loads(Path(step["artifacts"]["frames"]).read_text(encoding="utf-8"))
    assert manifest["directory"] == str(frames_dir.resolve()) and manifest["fps"] == 30 and manifest["missing"] == [844]
    assert [(fr["T"], fr["f"], fr["file"], fr["bytes"]) for fr in manifest["frames"]] == [(843, 1, "f0843.png", 7), (845, 2, "f0845.png", 7)]
    assert contracts.validate("frames", manifest)
    observation = step["observations"][0]
    assert observation["kind"] == "stage_render" and observation["planned"] == 3 and observation["rendered"] == 2
    assert observation["frames"] == 2 and observation["missing"] == 1
    # もう一度流すと、描いたコマはとばし、一覧には前に描いたコマも載る
    params["only"] = None
    params.pop("only")
    again = executor.run_recipe(_recipe(tmp_path, params, track), tmp_path / "runs")
    step = again["steps"]["render"]
    assert step["reused_from"] is None and step["observations"][0]["rendered"] == 1
    assert step["observations"][0]["skipped_existing"] == 2 and step["observations"][0]["frames"] == 3


def test_without_a_track_the_frames_go_in_the_step_folder(tmp_path):
    from model3d import executor
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN))}
    result = executor.run_recipe(_recipe(tmp_path, params), tmp_path / "runs")
    step = result["steps"]["render"]
    manifest = json.loads(Path(step["artifacts"]["frames"]).read_text(encoding="utf-8"))
    assert Path(manifest["directory"]) == Path(step["artifacts"]["frames"]).parent / "frames"
    assert [fr["T"] for fr in manifest["frames"]] == [1, 2, 3] and manifest["missing"] == []


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, outputs=None, track=None):
    from model3d.ops import get
    (tmp_path / "dance.blend").write_bytes(b"looked")
    inputs = {"blend": tmp_path / "dance.blend"}
    if track is not None:
        atomic_json(tmp_path / "track.json", track)
        inputs["track"] = tmp_path / "track.json"
    declared = {k: tmp_path / v for k, v in (outputs or {"frames": "frames.json", "report": "r.json"}).items()}
    return get("render.stage").function(inputs, parameters, declared, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"resolution": [1080]}, "resolution"),
    ({"resolution": [8, 8]}, "resolution"),
    ({"samples": 0}, "samples"),
    ({"raytracing": "yes"}, "raytracing"),
    ({"bone_prefix": 3}, "bone_prefix"),
    ({"fps": 0}, "fps"),
    ({"camera": {"zoom": 2}}, "知らない鍵"),
    ({"camera": {"lens": 0}}, "lens"),
    ({"camera": {"follow": 1.5}}, "follow"),
    ({"outline": {"line_px": -1}}, "line_px"),
    ({"outline": {"scale": {"hair": 0}}}, "outline.scale"),
    ({"stage": {"enabled": 1}}, "stage.enabled"),
    ({"stage": {"stripe_soft": 0.1}}, "stripe_soft"),
    ({"only": [1, 1]}, "only"),
    ({"only": []}, "only"),
    ({"camera": "front"}, "camera"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **parameters})


def test_the_operation_checks_the_track(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    with pytest.raises(ToolError, match="重なっています"):
        call(tmp_path, {"blender": blender}, track=_track([{"T": 1, "f": 1}, {"T": 1, "f": 2}]))
    with pytest.raises(ToolError, match="stage-track"):
        call(tmp_path, {"blender": blender}, track=_track([{"T": 1, "f": 1, "stage": "disco"}]))


def test_the_operation_needs_blender_and_both_outputs(tmp_path):
    with pytest.raises(ToolError, match="Blender"):
        call(tmp_path, {})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN))}, {"frames": "frames.json"})
