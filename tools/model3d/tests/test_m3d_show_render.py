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
skipped = []
for item in planned:
    path = os.path.join(frames_dir, "f%04d.png" % item["T"])
    if "--skip-existing" in args and os.path.exists(path):
        skipped.append(item["T"])
        continue
    png(path)
if open(value("--blend"), "rb").read() in (b"overflow", b"overflow_crash"):   # 影の領域があふれた場面のまね（EEVEE は標準エラーに書き、描くのは続ける）
    for need in (3494, 6252):
        sys.stderr.write("Error: Shadow buffer full, may result in missing shadows and lower performance. (%d / 4096)\n" % need)
if open(value("--blend"), "rb").read() == b"overflow_crash":                    # あふれたあと、報告を書く前に落ちる
    sys.exit(3)
json.dump({"schema_version": "model3d-show-render/0.1", "planned": planned, "rendered": [p["T"] for p in planned if p["T"] not in skipped],
           "skipped_existing": skipped, "fps": 30,
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


def call(tmp_path, parameters, inputs=None, outputs=None, overflow=False):
    from model3d.ops import get
    (tmp_path / "show.blend").write_bytes(b"overflow" if overflow else b"lit")   # overflow: 代役が影の領域のあふれを記録に書く
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


# ---- 光と影のつなぎ分け（links） ----

@pytest.mark.parametrize("links, message", [
    ({"lights": ["a"]}, "links は"), ([{"lights": ["a"], "receivers": ["s"], "glow": 1}], "links\\[0\\] は"),
    ([{"lights": [], "receivers": ["s"]}], "lights"), ([{"lights": ["a", "a"], "receivers": ["s"]}], "lights"),
    ([{"lights": ["a"], "receivers": ["s"], "exclude": ["s"]}], "どちらか"), ([{"lights": ["a"]}], "要ります"),
    ([{"lights": ["a"], "receivers": "everyone"}], "receivers"), ([{"lights": ["a"], "blockers": []}], "blockers"),
    ([{"lights": ["a"], "exclude": ["s", "s"]}], "exclude"),
    ([{"lights": ["a"], "receivers": ["s"]}, {"lights": ["a"], "exclude": ["s"]}], "links\\[0\\] にも"),
    ([{"lights": ["a"], "blockers": "character"}, {"lights": ["b", "a"], "blockers": ["s"]}], "blockers は links\\[0\\]"),
])
def test_links_problems_are_named(links, message):
    import re
    problem = show_render.links_problem(links)
    assert problem is not None and re.search(message, problem), problem


def test_links_may_split_receivers_and_blockers_between_entries():
    assert show_render.links_problem([]) is None
    assert show_render.links_problem([{"lights": ["shadow"], "receivers": ["screen"], "blockers": "character"},
                                      {"lights": ["key", "fill"], "exclude": ["screen"]},
                                      {"lights": ["key"], "blockers": ["prop"]}]) is None


class FakeCollection:
    """bpy.data.collections の 1 つのまね: objects.link と、物体ごとのつなぎ分けの設定（collection_objects）。"""

    def __init__(self, name):
        self.name, self.objects, self.collection_objects = name, FakeObjects(self), []


class FakeObjects(list):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def link(self, ob):
        self.append(ob)
        self.owner.collection_objects.append(M(light_linking=M(link_state="INCLUDE")))


def _fake_bpy(objects):
    made = []

    def new(name):
        made.append(FakeCollection(name))
        return made[-1]
    by_name = {o.name: o for o in objects}
    return M(data=M(objects=M(get=by_name.get), collections=M(new=new))), made


def test_apply_links_makes_one_collection_per_role():
    light = lambda name: M(name=name, type="LIGHT", light_linking=M(receiver_collection=None, blocker_collection=None))
    shadow, key, fill = light("shadow"), light("key"), light("fill")
    screen, body, hair = M(name="screen", type="MESH"), M(name="body", type="MESH"), M(name="hair", type="MESH")
    bpy, made = _fake_bpy([shadow, key, fill, screen, body, hair])
    report = show_render.apply_links(bpy, [{"lights": ["shadow"], "receivers": ["screen"], "blockers": "character"},
                                           {"lights": ["key", "fill"], "exclude": ["screen"]}], [body, hair])
    assert report == [{"lights": ["shadow"], "receivers": ["screen"], "blockers": ["body", "hair"]},
                      {"lights": ["key", "fill"], "exclude": ["screen"]}]
    receivers, blockers, excluded = made
    assert shadow.light_linking.receiver_collection is receivers and shadow.light_linking.blocker_collection is blockers
    assert key.light_linking.receiver_collection is fill.light_linking.receiver_collection is excluded
    assert key.light_linking.blocker_collection is None                         # 影は、ほかの光の設定のまま（みな落とす）
    assert [m.light_linking.link_state for m in excluded.collection_objects] == ["EXCLUDE"]   # 照らさない物
    assert [m.light_linking.link_state for m in receivers.collection_objects] == ["INCLUDE"]
    with pytest.raises(SystemExit, match="光の物体がありません: screen"):
        show_render.apply_links(bpy, [{"lights": ["screen"], "receivers": ["body"]}], [])
    with pytest.raises(SystemExit, match="receivers の物体がありません: wall"):
        show_render.apply_links(bpy, [{"lights": ["key"], "receivers": ["wall"]}], [])


def test_the_operation_checks_and_passes_the_links(tmp_path):
    links = [{"lights": ["stage_shadow"], "receivers": ["stage_disc"], "blockers": "character"}]
    with pytest.raises(ToolError, match="どちらか"):
        call(tmp_path, {"links": [{"lights": ["a"], "receivers": ["s"], "exclude": ["s"]}]})
    call(tmp_path, {"links": links})
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["settings"]["links"] == links



# ---- 影の領域（shadow）とあふれの知らせ ----

FULL = "Error: Shadow buffer full, may result in missing shadows and lower performance. ({} / {})"


def test_shadow_overflow_counts_the_lines_and_keeps_the_largest_need():
    assert show_render.shadow_overflow("") == {"lines": 0, "worst": None}
    assert show_render.shadow_overflow("T 1 f 1 2.0s\nT 2 f 2 4.1s\n") == {"lines": 0, "worst": None}
    text = "\n".join(["T 1 f 1 2.0s", FULL.format(3494, 2048), FULL.format(6252, 4096), FULL.format(4100, 4096), "Error: Shadow buffer full"])
    assert show_render.shadow_overflow(text) == {"lines": 4, "worst": [6252, 4096]}   # 数の無い行も 1 行に数える


def test_shadow_settings_default_to_the_blend_and_to_failing():
    s = show_render.merged({"shadow": {"resolution_scale": 0.5}})
    assert s["shadow"] == {"pool_size": None, "resolution_scale": 0.5, "overflow": "fail"}
    assert show_render.merged({})["shadow"] == {"pool_size": None, "resolution_scale": None, "overflow": "fail"}


@pytest.mark.parametrize("shadow, message", [
    ({"pool_size": 1000}, "shadow.pool_size"), ({"pool_size": "1024"}, "shadow.pool_size"), ({"pool_size": True}, "shadow.pool_size"),
    ({"resolution_scale": 0}, "resolution_scale"), ({"resolution_scale": 1.5}, "resolution_scale"),
    ({"overflow": "ignore"}, "shadow.overflow"), ({"tiles": 4}, "知らない鍵"),
])
def test_bad_shadow_settings_are_named(tmp_path, shadow, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, {"shadow": shadow})


def test_the_settings_pass_the_shadow_section(tmp_path):
    call(tmp_path, {"shadow": {"pool_size": 1024, "resolution_scale": 0.5, "overflow": "warn"}})
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["settings"]["shadow"] == {"pool_size": 1024, "resolution_scale": 0.5, "overflow": "warn"}
    assert report["shadow_overflow"] == {"lines": 0, "worst": None, "action": "warn"}


def test_an_overflowing_render_fails_by_default_and_keeps_its_frames(tmp_path):
    with pytest.raises(ToolError, match=r"Shadow buffer full）が 2 行.*6252 / 4096.*shadow.overflow"):
        call(tmp_path, {}, overflow=True)
    frames = contracts.load("frames", tmp_path / "frames.json")                    # 描いたコマの一覧は、失敗でも残す
    assert [fr["T"] for fr in frames["frames"]] == [1, 2, 3]
    report = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
    assert report["shadow_overflow"] == {"lines": 2, "worst": [6252, 4096], "action": "fail"}


def test_an_overflowing_render_only_warns_when_asked(tmp_path):
    result = call(tmp_path, {"shadow": {"overflow": "warn"}}, overflow=True)
    assert result.observations[0]["shadow_overflow"] == {"lines": 2, "worst": [6252, 4096]}


# ---- 露出（exposure）と霧（volumetric） ----

def test_exposure_and_fog_default_to_fixed_and_to_the_blend():
    s = show_render.merged({"volumetric": {"tile_size": 8}})
    assert s["exposure"] is None and s["volumetric"]["tile_size"] == 8 and s["volumetric"]["samples"] is None
    assert show_render.DEFAULTS["volumetric"]["tile_size"] is None                # 既定は書き換えない


def test_report_values_turn_number_enums_into_numbers():
    assert show_render.report_value("1024") == 1024 and show_render.report_value(0.30000001192092896) == 0.3
    assert show_render.report_value(True) is True and show_render.report_value("Standard") == "Standard"


@pytest.mark.parametrize("parameters, message", [
    ({"exposure": "dark"}, "exposure"), ({"exposure": 40}, "exposure"),
    ({"volumetric": {"tile_size": 3}}, "volumetric.tile_size"), ({"volumetric": {"tile_size": True}}, "volumetric.tile_size"),
    ({"volumetric": {"samples": 0}}, "samples"), ({"volumetric": {"samples": 300}}, "volumetric.samples"),
    ({"volumetric": {"shadow_samples": 129}}, "volumetric.shadow_samples"), ({"volumetric": {"end": 0}}, "end"),
    ({"volumetric": {"start": 5.0, "end": 2.0}}, "volumetric.start"), ({"volumetric": {"shadows": 1}}, "volumetric.shadows"),
    ({"volumetric": {"density": 1}}, "知らない鍵"),
])
def test_bad_exposure_and_fog_settings_are_named(tmp_path, parameters, message):
    with pytest.raises(ToolError, match=message):
        call(tmp_path, parameters)


def test_the_settings_pass_exposure_and_fog(tmp_path):
    call(tmp_path, {"exposure": -0.5, "volumetric": {"tile_size": 2, "samples": 40, "end": 25, "shadows": False}})
    settings = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["settings"]
    assert settings["exposure"] == -0.5 and settings["volumetric"] == {"tile_size": 2, "samples": 40, "end": 25.0, "shadows": False}


# ---- フックの値（hook_config） ----

def test_the_hook_config_is_passed_and_its_sha256_is_kept(tmp_path):
    (tmp_path / "stage.py").write_text("def build(ctx):\n    pass\n", encoding="utf-8")
    atomic_json(tmp_path / "values.json", {"glow": 2.0, "anything": [1, 2]})
    inputs = {"blend": tmp_path / "show.blend", "cameras": tmp_path / "cameras.json", "hooks": tmp_path / "stage.py",
              "hook_config": tmp_path / "values.json"}
    result = call(tmp_path, {}, inputs=inputs)
    args = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["argv"]
    assert Path(args[args.index("--hook-config") + 1]).name == "values.json"
    from model3d.io import sha256
    assert result.observations[0]["hooks"]["config"] == {"path": str(tmp_path / "values.json"), "sha256": sha256(tmp_path / "values.json")}


def test_without_a_hook_config_the_hooks_record_has_none(tmp_path):
    (tmp_path / "stage.py").write_text("", encoding="utf-8")
    result = call(tmp_path, {}, inputs={"blend": tmp_path / "show.blend", "cameras": tmp_path / "cameras.json", "hooks": tmp_path / "stage.py"})
    args = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["argv"]
    assert "--hook-config" not in args and result.observations[0]["hooks"]["config"] is None


def test_a_hook_config_needs_hooks_and_a_dictionary(tmp_path):
    atomic_json(tmp_path / "values.json", {"glow": 2.0})
    with pytest.raises(ToolError, match="組で"):
        call(tmp_path, {}, inputs={"blend": tmp_path / "show.blend", "cameras": tmp_path / "cameras.json", "hook_config": tmp_path / "values.json"})
    (tmp_path / "stage.py").write_text("", encoding="utf-8")
    atomic_json(tmp_path / "list.json", [1, 2])
    with pytest.raises(ToolError, match="辞書"):
        call(tmp_path, {}, inputs={"blend": tmp_path / "show.blend", "cameras": tmp_path / "cameras.json", "hooks": tmp_path / "stage.py",
                                    "hook_config": tmp_path / "list.json"})


def test_the_blender_side_reads_a_hook_config_with_a_bom(tmp_path):
    """工程の側（read_json）は BOM つきの JSON を読むので、Blender の側も同じに読む（BOM で止まると、確かめを通った値で描く前に落ちる）。"""
    import codecs
    import hashlib
    from model3d.io import read_json
    raw = codecs.BOM_UTF8 + json.dumps({"glow": 2.0}).encode("utf-8")
    (tmp_path / "values.json").write_bytes(raw)
    assert read_json(tmp_path / "values.json") == {"glow": 2.0}
    assert show_render.read_hook_config(tmp_path / "values.json") == ({"glow": 2.0}, hashlib.sha256(raw).hexdigest())


# ---- 区切って描いて集める・続きから描く（frames_dir） ----

HOOKS = "def build(ctx):\n    pass\n"


def _recipe(tmp_path, parameters, hooks=HOOKS):
    (tmp_path / "show.blend").write_bytes(b"lit")
    atomic_json(tmp_path / "cameras.json", _cameras(4))
    (tmp_path / "stage.py").write_text(hooks, encoding="utf-8")
    params = {"blender": str(stand_in_executable(tmp_path, "blender", STAND_IN)), "samples": 8, "frames_dir": "frames_all", **parameters}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "show",
                                           "inputs": {"blend": {"path": "show.blend"}, "cameras": {"path": "cameras.json"},
                                                      "hooks": {"path": "stage.py"}},
                                           "steps": [{"id": "show", "operation": "render.show",
                                                      "inputs": {"blend": "$inputs.blend", "cameras": "$inputs.cameras", "hooks": "$inputs.hooks"},
                                                      "parameters": params, "outputs": {"frames": "frames.json", "report": "report.json"}}]})
    return tmp_path / "recipe.json"


def test_chunks_are_drawn_into_one_folder_and_drawn_frames_are_skipped(tmp_path):
    from model3d import executor
    first = executor.run_recipe(_recipe(tmp_path, {"only": [1, 2]}), tmp_path / "runs")
    assert first["status"] == "succeeded", first["error"]
    assert first["steps"]["show"]["observations"][0]["rendered"] == 2
    second = executor.run_recipe(_recipe(tmp_path, {"only": [2, 3, 4]}), tmp_path / "runs")   # 区切りが重なっても、描いたコマはとばす
    observation = second["steps"]["show"]["observations"][0]
    assert second["status"] == "succeeded" and observation["rendered"] == 2 and observation["skipped_existing"] == 1
    assert observation["inputs"]["kept"] == 1
    assert sorted(p.name for p in (tmp_path / "frames_all").glob("f*.png")) == ["f0001.png", "f0002.png", "f0003.png", "f0004.png"]
    frames = contracts.load("frames", second["steps"]["show"]["artifacts"]["frames"])
    assert [fr["T"] for fr in frames["frames"]] == [2, 3, 4] and frames["missing"] == []


def test_changed_inputs_stop_before_drawing_into_the_same_folder(tmp_path):
    from model3d import executor
    assert executor.run_recipe(_recipe(tmp_path, {"only": [1, 2]}), tmp_path / "runs")["status"] == "succeeded"
    changed = executor.run_recipe(_recipe(tmp_path, {"only": [3, 4]}, hooks=HOOKS + "# 書き換えた\n"), tmp_path / "runs")
    assert changed["status"] == "failed" and "フック" in changed["error"]     # 描いてないコマでも、別の入力で描いたフォルダには描かない
    other = executor.run_recipe(_recipe(tmp_path, {"only": [3, 4], "samples": 16}), tmp_path / "runs")
    assert other["status"] == "failed" and "設定（samples）" in other["error"]
    ok = executor.run_recipe(_recipe(tmp_path, {"only": [3, 4], "shadow": {"overflow": "warn"}}), tmp_path / "runs")
    assert ok["status"] == "succeeded", ok["error"]                         # あふれの扱いは、描く絵を変えない


def test_frames_drawn_outside_the_control_record_stop_the_resume(tmp_path):
    from model3d import executor
    (tmp_path / "frames_all").mkdir()
    (tmp_path / "frames_all" / "f0001.png").write_bytes(b"old")
    result = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert result["status"] == "failed" and "入力の控え" in result["error"]


def _overflow_recipe(tmp_path, parameters):
    recipe = _recipe(tmp_path, parameters)
    (tmp_path / "show.blend").write_bytes(b"overflow")            # 代役が影の領域のあふれを記録に書く
    return recipe


def test_frames_from_an_overflowing_render_are_not_silently_reused(tmp_path):
    """あふれて失敗した描画のコマは、同じ入力で続きから描いても、とばして通さない（あふれの知らせは描いた回の記録にしか出ない）。"""
    from model3d import executor
    first = executor.run_recipe(_overflow_recipe(tmp_path, {"only": [1, 2]}), tmp_path / "runs")
    assert first["status"] == "failed" and "Shadow buffer full" in first["error"]
    marks = json.loads((tmp_path / "frames_all" / "model3d_show_overflow.json").read_text(encoding="utf-8"))["frames"]
    assert sorted(marks) == ["f0001.png", "f0002.png"] and marks["f0001.png"]["worst"] == [6252, 4096]
    again = executor.run_recipe(_overflow_recipe(tmp_path, {"only": [1, 2]}), tmp_path / "runs")
    assert again["status"] == "failed" and "前の描画で影の領域があふれた" in again["error"] and "2 個のコマ" in again["error"]
    warned = executor.run_recipe(_overflow_recipe(tmp_path, {"only": [1, 2], "shadow": {"overflow": "warn"}}), tmp_path / "runs")
    assert warned["status"] == "succeeded", warned["error"]
    report = json.loads(Path(warned["steps"]["show"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["shadow_overflow"]["reused"] == 2 and report["shadow_overflow"]["lines"] == 2


def test_switching_from_warn_to_fail_still_finds_the_overflowing_frames(tmp_path):
    from model3d import executor
    warned = executor.run_recipe(_overflow_recipe(tmp_path, {"only": [1], "shadow": {"overflow": "warn"}}), tmp_path / "runs")
    assert warned["status"] == "succeeded", warned["error"]
    failing = executor.run_recipe(_overflow_recipe(tmp_path, {"only": [1, 2]}), tmp_path / "runs")
    assert failing["status"] == "failed" and "1 個のコマ（f0001.png）" in failing["error"]


def test_a_clean_redraw_clears_the_overflow_record(tmp_path):
    from model3d import executor
    from model3d.ops import show_ops
    (tmp_path / "frames_all").mkdir()
    show_ops._write_overflow_marks(tmp_path / "frames_all", {"f0003.png": {"lines": 1, "worst": None}})
    result = executor.run_recipe(_recipe(tmp_path, {"only": [3]}), tmp_path / "runs")          # まだ無いコマなので描き直す
    assert result["status"] == "succeeded", result["error"]
    assert not (tmp_path / "frames_all" / "model3d_show_overflow.json").exists()


def test_frames_left_by_an_interrupted_overflowing_render_are_marked_too(tmp_path):
    """あふれたあと Blender が落ちても（報告を書く前）、今回描けたコマを控えるので、同じ入力の続きの描画でとばして通さない。"""
    from model3d import executor
    recipe = _recipe(tmp_path, {"only": [1, 2]})
    (tmp_path / "show.blend").write_bytes(b"overflow_crash")
    crashed = executor.run_recipe(recipe, tmp_path / "runs")
    assert crashed["status"] == "failed" and "終了コード 3" in crashed["error"]
    marks = json.loads((tmp_path / "frames_all" / "model3d_show_overflow.json").read_text(encoding="utf-8"))["frames"]
    assert sorted(marks) == ["f0001.png", "f0002.png"]
    again = executor.run_recipe(recipe, tmp_path / "runs")              # 同じ入力: 描く前に、あふれたコマで止まる
    assert again["status"] == "failed" and "前の描画で影の領域があふれた" in again["error"]


def test_a_hook_with_a_bom_can_be_fingerprinted_for_resuming(tmp_path):
    """Python は BOM つきの台本を読むので、続きから描く入力のまとまりの台本の sha256 でも読む（隣の台本の import もたどる）。"""
    import codecs
    from model3d import render_inputs
    (tmp_path / "helper.py").write_bytes(codecs.BOM_UTF8 + "X = 1\n".encode("utf-8"))
    (tmp_path / "stage.py").write_bytes(codecs.BOM_UTF8 + "# 舞台\nimport helper\n\ndef build(ctx):\n    pass\n".encode("utf-8"))
    assert sorted(render_inputs.scripts(tmp_path / "stage.py")) == ["helper.py", "stage.py"]


def test_the_overflow_record_is_updated_inside_the_folder_lock(tmp_path):
    """同じフォルダへ同時に描く区切りどうしで、ほかの区切りの控えを消さない（読んで書き換えて書くまでを待ち合わせの中で）。"""
    import threading
    from model3d import render_inputs
    from model3d.ops import show_ops
    folder = tmp_path / "frames_all"
    show_ops._write_overflow_marks(folder.mkdir() or folder, {"f0001.png": {"lines": 1, "worst": None}})
    lock = render_inputs._locked(folder)
    worker = threading.Thread(target=show_ops._update_overflow_marks,
                              args=(folder, lambda marks: marks.update({"f0005.png": {"lines": 2, "worst": [9, 8]}})))
    worker.start()
    worker.join(0.3)
    assert worker.is_alive()                                       # 待ち合わせを持っている間は書かない
    lock.__exit__(None, None, None)
    worker.join(10)
    assert sorted(show_ops._overflow_marks(folder)) == ["f0001.png", "f0005.png"]
