"""render.show（演出版を EEVEE で 1 回で描く）: カメラのキーの決め方（止めるコマ・動きのコマ・四元数の符号・レンズとずらし・
被写界深度）、光の強さの単位、版の確かめ、設定の確かめ、台本の引数と工程の受け渡し（代役の Blender）を確かめる。描く所は本物の
Blender が要るので、CI では確かめない（docs/model3d.md の手元の確認）。"""
import json
from pathlib import Path
from types import SimpleNamespace as M

import pytest

from m3d_helpers import stand_in_executable
from model3d import contracts
from model3d.blender_adapter import show_render
from model3d.io import ToolError, atomic_json


def test_merged_fills_defaults_and_keeps_each_section_key():
    s = show_render.merged({"samples": 24, "motion_blur": {"shutter": 0.4}, "camera": {"clip": [0.05, 300.0]}})
    assert s["samples"] == 24 and s["resolution"] == [1080, 1920] and s["view_transform"] == "Standard"
    assert s["motion_blur"] == {"shutter": 0.4, "steps": 1, "off": []} and s["camera"] == {"sensor_height": 36.0, "clip": [0.05, 300.0]}
    assert show_render.DEFAULTS["motion_blur"]["shutter"] == 0.0                   # 既定は書き換えない（ぶれは無し）


def test_the_last_frame_of_each_cut_holds_its_camera_key():
    assert show_render.held_frames([1, 2, 3, 4, 5, 6], [3, 5]) == {2, 4, 6}        # カットの 1 つ前と、最後のコマ
    assert show_render.held_frames([10, 11, 12], [10, 20]) == {12}                  # 並びの外のカットは数えない
    assert show_render.held_frames([], [2]) == set()


def test_camera_keys_need_whole_and_rising_motion_frames():
    assert show_render.motion_frames_problem([(1, 1), (2, 2), (3, 5)]) is None
    assert "整数" in show_render.motion_frames_problem([(1, 1), (2, 1.5)])
    assert "増えて" in show_render.motion_frames_problem([(1, 3), (2, 3)])
    assert "増えて" in show_render.motion_frames_problem([(1, 3), (2, 2)])


def test_motion_frames_within_a_hair_of_an_integer_are_whole():
    """時刻から求めた f の丸めの端数（plan.orbit の T 63 は 63.00000000000001）は整数にする。本当の小数のコマは止める。"""
    assert show_render.motion_frame(63.00000000000001) == 63 and isinstance(show_render.motion_frame(63.00000000000001), int)
    assert show_render.motion_frame(61.99999999999999) == 62 and show_render.motion_frame("7") == 7
    assert show_render.motion_frame(62.5) == 62.5 and show_render.motion_frame(62.00001) == 62.00001
    rows = [(T, show_render.motion_frame((T - 1) * (1 / 30) * 30 + 1)) for T in range(1, 200)]
    assert show_render.motion_frames_problem(rows) is None and all(isinstance(f, int) for _, f in rows)
    assert "整数" in show_render.motion_frames_problem([(1, show_render.motion_frame(1)), (2, show_render.motion_frame(2.25))])


def test_shift_keys_go_on_every_frame_once_one_frame_is_shifted():
    """ずらしのあるコマが 1 つでもあれば全部のコマに打つ（0 のコマにキーが無いと、最初のずらしが前のコマへ延びる）。"""
    assert show_render.shift_keys([(0.0, 0.0), (0.0, 0.0), (0.2, 0.0)]) is True
    assert show_render.shift_keys([(0.0, 0.0), (0.0, 0.0)]) is False and show_render.shift_keys([]) is False


def test_lens_and_shift_come_from_the_record():
    frame = {"T": 1, "focal_px": 1920.0, "principal_px": [540.0, 960.0]}
    assert show_render.lens_and_shift(frame, [1080, 1920], 36.0) == (36.0, (0.0, 0.0))        # 焦点距離の画素から
    assert show_render.lens_and_shift({**frame, "lens_mm": 50.0}, [1080, 1920], 36.0)[0] == 50.0   # lens_mm があればそれ
    lens, shift = show_render.lens_and_shift({**frame, "principal_px": [440.0, 1060.0]}, [1080, 1920], 36.0)
    assert shift == pytest.approx((100 / 1920, 100 / 1920))
    assert show_render.lens_and_shift({**frame, "shift": [0.1, -0.2]}, [1080, 1920], 36.0)[1] == (0.1, -0.2)


def test_depth_of_field_needs_both_values():
    assert show_render.depth_of_field({"T": 1}) is None
    assert show_render.depth_of_field({"T": 1, "fstop": 2.0, "focus_m": 2.5}) == (2.0, 2.5)
    with pytest.raises(SystemExit, match="組"):
        show_render.depth_of_field({"T": 3, "fstop": 2.0})


def test_quaternions_keep_the_sign_of_the_previous_key():
    assert show_render.same_sign((1.0, 0.0, 0.0, 0.0), None) == (1.0, 0.0, 0.0, 0.0)
    assert show_render.same_sign((-0.9, 0.1, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)) == (0.9, -0.1, -0.0, -0.0)
    assert show_render.same_sign((0.9, 0.1, 0.0, 0.0), (1.0, 0.0, 0.0, 0.0)) == (0.9, 0.1, 0.0, 0.0)


def test_light_energy_is_scaled_by_the_scene_unit():
    assert show_render.light_energy(1500.0, 1.0) == 1500.0
    assert show_render.light_energy(1.5, 0.01) == pytest.approx(15000.0)


def test_only_blender_5_is_accepted():
    assert show_render.version_problem((5, 2, 4)) is None
    assert "render.stage" in show_render.version_problem((4, 5, 15))


def test_action_curves_read_layers_and_the_older_list():
    curves = [M(name="loc"), M(name="rot")]
    layered = M(layers=[M(strips=[M(channelbags=[M(fcurves=curves[:1]), M(fcurves=curves[1:])])])])
    assert show_render.action_curves(layered) == curves
    assert show_render.action_curves(M(layers=[], fcurves=curves)) == curves


# 代役の Blender: --version に答え、ふだんは -- の後ろの引数を読んで、カメラの控えのコマを描いたことにする（灰色の小さな PNG）
STAND_IN = r'''
import json, os, sys, zlib, struct
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
def png(path):
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * 2 for _ in range(2))
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))
cameras = json.load(open(value("--cameras"), encoding="utf-8"))
settings = json.load(open(value("--settings"), encoding="utf-8"))
frames_dir = value("--frames-dir")
os.makedirs(frames_dir, exist_ok=True)
planned = [{"T": fr["T"], "f": fr.get("f", fr["T"])} for fr in cameras["frames"] if not settings.get("only") or fr["T"] in settings["only"]]
for item in planned:
    png(os.path.join(frames_dir, "f%04d.png" % item["T"]))
json.dump({"schema_version": "model3d-show-render/0.1", "planned": planned, "rendered": [p["T"] for p in planned], "fps": 30,
           "resolution": [2, 2], "camera": {"held": [2]}, "motion_blur": {"frames": len(planned)}, "argv": args, "settings": settings},
          open(value("--report"), "w"))
'''


def _cameras(frames=3):
    rows = [{"T": T, "f": T, "matrix": [[1, 0, 0, 0], [0, 0, -1, -3.0], [0, 1, 0, 1.2], [0, 0, 0, 1]], "focal_px": 1920.0,
             "principal_px": [540.0, 960.0], "lens_mm": 36.0} for T in range(1, frames + 1)]
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [1080, 1920], "fps": 30, "sensor_height_mm": 36.0,
            "frames": rows}


def _track(frames=3):
    return {"schema_version": "model3d-light-track/0.1", "fps": 30,
            "frames": [{"T": T, "values": {"flash": 0.0, "flash_color": [1, 1, 1], "fade": 1.0, "key": 1.0, "amb": [0.2, 0.2, 0.2]}}
                       for T in range(1, frames + 1)]}


def test_the_operation_passes_the_inputs_and_lists_the_frames(tmp_path):
    from model3d import executor
    (tmp_path / "show.blend").write_bytes(b"lit")
    atomic_json(tmp_path / "cameras.json", _cameras())
    atomic_json(tmp_path / "lights.json", _track())
    (tmp_path / "stage.py").write_text("def build(ctx):\n    pass\n", encoding="utf-8")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "samples": 8, "motion_blur": {"shutter": 0.4, "off": [2]},
              "cuts": [3], "lights": {"stage_key": {"energy": "key", "base": 1500}}, "ambient": "amb", "only": [1, 3]}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "show",
                                           "inputs": {"blend": {"path": "show.blend"}, "cameras": {"path": "cameras.json"},
                                                      "lights": {"path": "lights.json"}, "hooks": {"path": "stage.py"}},
                                           "steps": [{"id": "show", "operation": "render.show",
                                                      "inputs": {"blend": "$inputs.blend", "cameras": "$inputs.cameras",
                                                                 "lights": "$inputs.lights", "hooks": "$inputs.hooks"},
                                                      "parameters": params, "outputs": {"frames": "frames.json", "report": "report.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result["error"]
    step = result["steps"]["show"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    args = report["argv"]
    assert Path(args[args.index("--lights") + 1]).name == "lights.json" and Path(args[args.index("--hooks") + 1]).name == "stage.py"
    assert Path(args[args.index("--frames-dir") + 1]).name == "frames"                       # いつも工程のフォルダの frames
    assert report["settings"]["lights"] == {"stage_key": {"energy": "key", "base": 1500.0}} and report["settings"]["cuts"] == [3]
    frames = contracts.load("frames", step["artifacts"]["frames"])
    assert [fr["T"] for fr in frames["frames"]] == [1, 3] and frames["missing"] == []
    observation = step["observations"][0]
    assert observation["kind"] == "show_render" and observation["rendered"] == 2 and observation["hooks"]["sha256"]
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["steps"]["show"]["reused_from"] is None   # 毎回描く


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


def call(tmp_path, parameters, inputs=None, outputs=None):
    from model3d.ops import get
    (tmp_path / "show.blend").write_bytes(b"lit")
    atomic_json(tmp_path / "cameras.json", _cameras())
    atomic_json(tmp_path / "lights.json", _track())
    given = {"blend": tmp_path / "show.blend", "cameras": tmp_path / "cameras.json"} if inputs is None else inputs
    declared = {k: tmp_path / v for k, v in (outputs or {"frames": "frames.json", "report": "r.json"}).items()}
    return get("render.show").function(given, {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), **parameters},
                                       declared, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"), ({"resolution": [1080]}, "resolution"), ({"samples": 0}, "samples"),
    ({"raytracing": "yes"}, "raytracing"), ({"view_transform": ""}, "view_transform"),
    ({"camera": {"zoom": 1}}, "知らない鍵"), ({"camera": {"clip": [1.0, 0.5]}}, "camera.clip"), ({"camera": {"sensor_height": 0}}, "sensor_height"),
    ({"motion_blur": {"shutter": -0.1}}, "shutter"), ({"motion_blur": {"steps": 0}}, "steps"), ({"motion_blur": {"off": [0]}}, "motion_blur.off"),
    ({"cuts": [1]}, "cuts"), ({"cuts": [3, 3]}, "cuts"),
    ({"lights": {"stage_key": {}}}, "lights.stage_key"), ({"lights": {"stage_key": {"energy": 3}}}, "lights.stage_key.energy"),
    ({"lights": {"stage_key": {"energy": "key", "base": -1}}}, "base"), ({"lights": {"stage_key": {"energy": "key", "glow": 1}}}, "知らない鍵"),
    ({"ambient": 3}, "ambient"), ({"world": [0, 0]}, "world"), ({"world": [0, -1, 0]}, "world"),
    ({"bloom": {"quality": "Ultra"}}, "bloom.quality"), ({"bloom": {"size": 2}}, "bloom.size"), ({"bloom": {"glow": 1}}, "知らない鍵"),
    ({"expressions": {"objects": ["Head"]}}, "expressions"), ({"expressions": {"objects": ["Head"], "values": {"blink": [[1.0, 0.5], [0.5, 0.0]]}}}, "小さい順"),
    ({"only": [0]}, "only"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_the_operation_needs_its_inputs(tmp_path):
    with pytest.raises(ToolError, match="入力 cameras"):
        call(tmp_path, {}, inputs={"blend": tmp_path / "show.blend"})
    with pytest.raises(ToolError, match="入力 lights"):
        call(tmp_path, {"ambient": "amb"})
    with pytest.raises(ToolError, match="report"):
        call(tmp_path, {}, outputs={"frames": "frames.json"})
    doubled = _cameras()
    doubled["frames"].append(doubled["frames"][0])
    atomic_json(tmp_path / "doubled.json", doubled)
    with pytest.raises(ToolError, match="重なって"):
        call(tmp_path, {}, inputs={"blend": tmp_path / "show.blend", "cameras": tmp_path / "doubled.json"})
