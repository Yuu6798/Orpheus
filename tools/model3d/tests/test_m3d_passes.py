"""render.passes: 設定の重ね方・カメラの換算・ファイルの名前・踊り手の点のならし・要素ごとの切り替え（Blender なしで動く部分）と、
台本の引数と工程の受け渡し・要素ごとのコマの一覧・カメラの控え（代役の Blender）を確かめる。本物の Blender で描く所は CI では
確かめない（docs/model3d.md の「要素別に描く」の手元の確認）。"""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import SCHEMAS, contracts
from model3d.blender_adapter import pass_render
from model3d.io import ToolError, atomic_json
from model3d.ops import render_ops


def test_merged_fills_defaults_and_keeps_each_section_key():
    s = pass_render.merged({"passes": ["char"], "camera": {"sensor_height": 36.0}, "disc": {"radius": 0.3}})
    assert s["passes"] == ["char"] and s["resolution"] == [1080, 1920] and s["format"] == "png" and s["antialiasing"] == "5"
    assert s["camera"] == {"sensor_height": 36.0, "lens": 35.0, "clip": [0.1, 1000.0]}
    assert s["disc"] == {"radius": 0.3, "vertices": 48, "height": 0.004}
    assert s["room"] == {"collection": None, "world": [0.0, 0.0, 0.0]} and s["shade"]["studio_light"] == "Default"
    assert pass_render.DEFAULTS["disc"]["radius"] == 0.42                  # 既定の値は変えない


def test_the_camera_conversions_match_the_production_values():
    # リポジトリ外の試作の 1 コマ（受光 36 mm、画面 1080×1920）。焦点距離と主点からレンズとずらしを戻すと、Blender が持った値と同じ
    lens = pass_render.lens_from_focal(5559.300130208333, 1920, 36.0)
    assert lens == 104.23687744140625
    shift = pass_render.shift_from_principal([484.1958796977997, 1500.044116973877], 1080, 1920)
    assert shift == (0.029064645990729332, 0.2812729775905609)
    assert pass_render.focal_from_lens(lens, 36.0, 1920) == 5559.300130208333
    assert pass_render.principal_from_shift(shift, 1080, 1920) == [484.1958796977997, 1500.044116973877]
    assert pass_render.shift_from_principal([540.0, 960.0], 1080, 1920) == (0.0, 0.0)     # 画面の中心はずらし 0
    assert pass_render.shift_from_principal([0.0, 0.0], 1080, 1920) == (0.28125, -0.5)    # 主点が左上なら、右と下へずらす


def test_pass_files_are_named_by_kind_and_T():
    assert pass_render.pass_file("char", 7, "jpeg") == "char/f0007.png"         # 人物はいつも透けの PNG
    assert pass_render.pass_file("shade", 7, "jpeg") == "shade/f0007.jpg"
    assert pass_render.pass_file("room", 1234, "png") == "room/f1234.png"
    assert pass_render.extension("disc", "png") == "png" and pass_render.extension("head", "jpeg") == "jpg"


def test_cameras_come_from_the_record_or_the_track():
    record = {"frames": [{"T": 2, "matrix": np.eye(4).tolist(), "focal_px": 2000.0, "principal_px": [540, 960]},
                         {"T": 3, "matrix": np.eye(4).tolist(), "focal_px": 1000.0, "principal_px": [500, 900], "dancer": [0, 1, 2]}]}
    cams = pass_render.record_cameras(record)
    assert sorted(cams) == [2, 3] and cams[2][3] is None and cams[3][3] == [0, 1, 2] and cams[3][2] == [500.0, 900.0]
    assert pass_render.record_cameras(None) == {}
    plan = [(1, 10, {"camera": {"location": [0, -3, 1], "target": [0, 0, 1]}}), (2, 11, {}), (3, 12, {})]
    assert pass_render.cameras_problem(plan, cams) is None
    problem = pass_render.cameras_problem(plan, {})
    assert "T 2, 3" in problem and "camera" in problem


def test_the_dancer_path_is_smoothed_over_the_sorted_frames():
    raw = {f: np.array([float(f), 0.0, 1.0]) for f in range(1, 21)}
    path = pass_render.dancer_path(raw, [20, 1, 5, 5, *range(2, 21)], 2.0)
    assert sorted(path) == list(range(1, 21))
    assert np.allclose([path[f][0] for f in range(6, 15)], np.arange(6.0, 15.0))      # まっすぐな動きはそのまま
    assert path[1][0] > 1.0 and np.allclose([p[2] for p in path.values()], 1.0)         # 端は端の値で延ばす


def test_the_camera_entry_is_written_in_metres():
    M = np.eye(4)
    M[:3, 3] = [100.0, -300.0, 150.0]                     # 1 単位 = 1 cm のシーンで、カメラは (1, -3, 1.5) m
    entry = pass_render.camera_entry(5, 12, M.tolist(), 50.0, (0.1, -0.05), 24.0, [1080, 1920], 0.01, [1.0, 0.0, 1.5])
    assert entry["matrix"][0][3] == 1.0 and entry["matrix"][1][3] == -3.0 and entry["matrix"][2][3] == 1.5
    assert entry["focal_px"] == 50.0 / 24.0 * 1920 and entry["principal_px"] == [540 - 0.1 * 1920, 960 - 0.05 * 1920]
    assert entry["distance_m"] == pytest.approx(3.0) and entry["dancer"] == [1.0, 0.0, 1.5]
    assert "dancer" not in pass_render.camera_entry(1, 1, np.eye(4).tolist(), 35.0, (0, 0), 24.0, [100, 100], 1.0, None)


class _Shading(SimpleNamespace):
    pass


def _fake_bpy():
    shading = _Shading(light="STUDIO", color_type="MATERIAL", single_color=(0.8, 0.8, 0.8), show_specular_highlight=True,
                       use_world_space_lighting=False, studio_light="Default", studiolight_rotate_z=0.0)
    image = SimpleNamespace(file_format="PNG", color_mode="RGB", color_depth="8", compression=15, quality=90)
    render = SimpleNamespace(film_transparent=False, image_settings=image)
    scene = SimpleNamespace(display=SimpleNamespace(shading=shading), render=render)
    return SimpleNamespace(context=SimpleNamespace(scene=scene))


def test_each_pass_shows_its_objects_and_switches_the_look():
    bpy = _fake_bpy()
    body, head = SimpleNamespace(name="Body", hide_render=False, color=None), SimpleNamespace(name="Head", hide_render=False, color=None)
    floor, disc = SimpleNamespace(name="floor", hide_render=False), SimpleNamespace(name="disc", hide_render=True)
    world = SimpleNamespace(color=None)
    handles = {"character": [body, head], "room": [floor], "disc": disc, "world": world}
    settings = pass_render.merged({"passes": list(pass_render.PASSES), "head": ["Head"], "format": "jpeg", "quality": 92,
                                   "room": {"collection": "room", "world": [0.02, 0.015, 0.012]}, "shade": {"rotate_z": 90.0}})
    sc = bpy.context.scene
    seen = {}
    for kind in ("shade", "room", "char", "disc", "head"):        # どの順でも、要素ごとに同じ切り替えになる
        pass_render.set_pass(bpy, kind, handles, settings)
        sh, image = sc.display.shading, sc.render.image_settings
        seen[kind] = {"body": not body.hide_render, "floor": not floor.hide_render, "disc": not disc.hide_render,
                      "light": sh.light, "color": sh.color_type, "transparent": sc.render.film_transparent,
                      "world": world.color, "format": (image.file_format, image.color_mode)}
        if kind == "shade":
            assert sh.use_world_space_lighting is True and sh.show_specular_highlight is False
            assert sh.studiolight_rotate_z == pytest.approx(np.pi / 2) and sh.single_color == (1.0, 1.0, 1.0)
        if kind == "head":
            assert head.color == (1.0, 1.0, 1.0, 1.0) and body.color == (0.0, 0.0, 0.0, 1.0)
    assert seen["room"] == {"body": False, "floor": True, "disc": False, "light": "FLAT", "color": "TEXTURE", "transparent": False,
                            "world": (0.02, 0.015, 0.012), "format": ("JPEG", "RGB")}
    assert seen["char"] == {"body": True, "floor": False, "disc": False, "light": "FLAT", "color": "TEXTURE", "transparent": True,
                            "world": (0.0, 0.0, 0.0), "format": ("PNG", "RGBA")}
    assert seen["shade"]["light"] == "STUDIO" and seen["shade"]["color"] == "SINGLE" and seen["shade"]["body"]
    assert seen["disc"] == {"body": False, "floor": False, "disc": True, "light": "FLAT", "color": "SINGLE", "transparent": False,
                            "world": (0.0, 0.0, 0.0), "format": ("JPEG", "RGB")}
    assert seen["head"]["color"] == "OBJECT" and seen["head"]["body"] and not seen["head"]["floor"]
    pass_render.set_pass(bpy, "disc", handles, {**settings, "format": "png"})
    assert (sc.render.image_settings.file_format, sc.render.image_settings.color_mode) == ("PNG", "RGB")


def test_the_settings_are_checked_before_blender_runs():
    s = render_ops._pass_settings({"passes": ["char", "head", "room"], "head": ["Head"], "room": {"collection": "set"},
                                   "bone_prefix": "", "dancer": {"bones": ["upper_leg.L", "upper_leg.R"], "sigma": 3},
                                   "camera": {"sensor_height": 36, "clip": [0.05, 200]}, "format": "jpeg", "quality": 92})
    assert s["dancer"] == {"bones": ["upper_leg.L", "upper_leg.R"], "sigma": 3.0} and s["camera"]["clip"] == [0.05, 200.0]
    assert render_ops._pass_settings({})["dancer"] == {"bones": ["mixamorig:Hips"]}          # 既定は Mixamo 式の腰
    bad = [({"passes": ["char", "char"]}, "passes"), ({"passes": ["shadow"]}, "passes"), ({"passes": []}, "passes"),
           ({"passes": ["head"]}, "head"), ({"passes": ["room"]}, "room.collection"), ({"format": "tiff"}, "format"),
           ({"quality": 101}, "quality"), ({"antialiasing": "4"}, "antialiasing"), ({"camera": {"clip": [1, 0.5]}}, "clip"),
           ({"camera": {"fov": 1}}, "camera"), ({"disc": {"vertices": 2}}, "vertices"), ({"room": {"world": [1, 1]}}, "room.world"),
           ({"head": []}, "head"), ({"dancer": {"bones": "hips"}}, "dancer.bones"), ({"only": [0]}, "only")]
    for params, word in bad:
        with pytest.raises(ToolError, match=word):
            render_ops._pass_settings(params)


STAND_IN = r'''
import json, os, sys
from model3d.blender_adapter import pass_render
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1] if flag in args else None
settings = pass_render.merged(json.load(open(value("--settings"), encoding="utf-8")))
track = json.load(open(value("--track"), encoding="utf-8"))
record = json.load(open(value("--cameras"), encoding="utf-8")) if value("--cameras") else None
plan = [(fr["T"], fr["f"]) for fr in track["frames"]]
only = set(settings["only"] or [T for T, _ in plan])
rendered = {k: [] for k in settings["passes"]}
existing = {k: [] for k in settings["passes"]}
for T, f in plan:
    if T not in only:
        continue
    for kind in settings["passes"]:
        path = os.path.join(value("--passes-dir"), pass_render.pass_file(kind, T, settings["format"]))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.exists(path):
            existing[kind].append(T)
            continue
        open(path, "wb").write(b"%s %d" % (kind.encode(), T))
        rendered[kind].append(T)
by_record = {fr["T"] for fr in (record or {}).get("frames", [])}
frames = [pass_render.camera_entry(T, f, [[1, 0, 0, 0], [0, 1, 0, -3], [0, 0, 1, 1], [0, 0, 0, 1]], 35.0, (0.0, 0.0), 24.0,
                                   settings["resolution"], 1.0, [0.0, 0.0, 1.0]) for T, f in plan]
json.dump({"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": settings["resolution"], "fps": track.get("fps", 24),
           "frames": frames}, open(value("--cameras-out"), "w"))
json.dump({"schema_version": "model3d-pass-render/0.1", "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered,
           "skipped_existing": existing, "fps": track.get("fps", 24), "resolution": settings["resolution"],
           "camera": {"from_record": sum(1 for T, _ in plan if T in by_record), "from_track": sum(1 for T, _ in plan if T not in by_record)},
           "objects": {"character": ["Body", "Head"], "room": [], "head": settings["head"]}, "argv": args, "settings": settings},
          open(value("--report"), "w"))
'''


def _track(frames):
    return {"schema_version": "model3d-stage-track/0.1", "fps": 30, "frames": frames}


def _cameras(Ts, resolution=(1080, 1920)):
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": list(resolution),
            "frames": [{"T": T, "matrix": np.eye(4).tolist(), "focal_px": 2000.0, "principal_px": [540.0, 960.0]} for T in Ts]}


def _recipe(tmp_path, params, track, cameras=None):
    (tmp_path / "dance.blend").write_bytes(b"dance")
    atomic_json(tmp_path / "track.json", track)
    inputs = {"dance": {"path": "dance.blend"}, "track": {"path": "track.json"}}
    step_inputs = {"blend": "$inputs.dance", "track": "$inputs.track"}
    if cameras is not None:
        atomic_json(tmp_path / "cameras_in.json", cameras)
        inputs["cameras"] = {"path": "cameras_in.json"}
        step_inputs["cameras"] = "$inputs.cameras"
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "passes", "inputs": inputs,
                                           "steps": [{"id": "passes", "operation": "render.passes", "inputs": step_inputs,
                                                      "parameters": params,
                                                      "outputs": {"passes": "passes.json", "cameras": "cameras.json",
                                                                  "report": "report.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_renders_each_pass_and_lists_the_frames(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    track = _track([{"T": 1, "f": 25, "camera": {"location": [0, -3, 1], "target": [0, 0, 1]}}, {"T": 2, "f": 29}, {"T": 3, "f": 33}])
    params = {"blender": blender, "passes": ["char", "shade", "disc", "head"], "head": ["Head"], "format": "jpeg", "quality": 92,
              "camera": {"sensor_height": 36.0}, "passes_dir": "out_passes", "only": [1, 2]}
    result = executor.run_recipe(_recipe(tmp_path, params, track, _cameras([2, 3])), tmp_path / "runs")
    step = result["steps"]["passes"]
    assert result["status"] == "succeeded", step.get("error")
    manifest = contracts.load("passes", step["artifacts"]["passes"])
    assert manifest["schema_version"] == SCHEMAS["passes"] and manifest["fps"] == 30 and manifest["resolution"] == [1080, 1920]
    assert set(manifest["passes"]) == {"char", "shade", "disc", "head"}
    char, shade = manifest["passes"]["char"], manifest["passes"]["shade"]
    assert (char["format"], char["alpha"], shade["format"], shade["alpha"]) == ("png", True, "jpeg", False)
    assert [(fr["T"], fr["f"], fr["file"]) for fr in shade["frames"]] == [(1, 25, "shade/f0001.jpg"), (2, 29, "shade/f0002.jpg")]
    assert shade["missing"] == [3] and char["frames"][0]["file"] == "char/f0001.png"
    assert (tmp_path / "out_passes" / "head" / "f0002.jpg").read_bytes() == b"head 2"
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert "--cameras" in report["argv"] and report["settings"]["dancer"] == {"bones": ["mixamorig:Hips"], "sigma": 6.0}
    cameras = contracts.load("cameras", step["artifacts"]["cameras"])
    assert [fr["T"] for fr in cameras["frames"]] == [1, 2, 3]                       # 控えは描いたコマに関係なく全部
    obs = step["observations"][0]
    assert obs["kind"] == "pass_render" and obs["planned"] == 3 and obs["camera"] == {"from_record": 2, "from_track": 1}
    assert obs["passes"]["char"] == {"rendered": 2, "skipped_existing": 0, "frames": 2, "missing": 1}
    # 続きから描く: 描いてあるコマはとばし、残りだけ描く
    params2 = {**params, "only": [1, 2, 3]}
    result2 = executor.run_recipe(_recipe(tmp_path, params2, track, _cameras([2, 3])), tmp_path / "runs")
    obs2 = result2["steps"]["passes"]["observations"][0]
    assert obs2["passes"]["disc"] == {"rendered": 1, "skipped_existing": 2, "frames": 3, "missing": 0}


def test_the_operation_stops_before_blender_on_bad_inputs(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n"))
    track = _track([{"T": 1, "f": 1}, {"T": 2, "f": 2}])
    cases = [(dict(cameras=_cameras([1])), {}, "カメラの無いコマ"),
             (dict(cameras=_cameras([1, 2])), {"only": [5]}, "only"),
             (dict(cameras=_cameras([1, 2], (1920, 1080))), {}, "縦横比"),
             (dict(cameras={**_cameras([1, 2]), "frames": _cameras([1, 1])["frames"]}), {}, "重なって")]
    for index, (extra, params, word) in enumerate(cases):
        folder = tmp_path / f"case{index}"
        folder.mkdir()
        result = executor.run_recipe(_recipe(folder, {"blender": blender, **params}, track, **extra), folder / "runs")
        assert result["status"] == "failed" and word in result["steps"]["passes"]["error"], (word, result["steps"]["passes"]["error"])


def test_the_new_contracts_are_known():
    assert SCHEMAS["passes"] == "model3d-passes/0.1" and SCHEMAS["cameras"] == "model3d-cameras/0.1"
    assert {"passes", "cameras"} <= set(contracts.KINDS)
    with pytest.raises(ToolError, match="cameras"):
        contracts.validate("cameras", {**_cameras([1]), "frames": [{"T": 1, "matrix": [[1, 0, 0]], "focal_px": 1, "principal_px": [0, 0]}]})
    with pytest.raises(ToolError, match="passes"):
        contracts.validate("passes", {"schema_version": "model3d-passes/0.1", "directory": ".", "fps": 30, "resolution": [8, 8],
                                      "passes": {"shadow": {"format": "png", "alpha": False, "frames": [], "missing": []}}})
