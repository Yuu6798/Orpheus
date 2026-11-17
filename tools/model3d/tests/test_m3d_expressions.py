"""表情: 閉じ目の形状キー（model.closed_eye_key）の形の計算（Blender なし）と、render.passes の expressions（時刻の表・設定の確かめ・
工程の受け渡し。代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import closed_eye_key as ce
from model3d.blender_adapter import pass_render
from model3d.io import ToolError, atomic_json

EYE = {"eye_x": [0.008, 0.062], "eye_z": [1.47, 1.525], "front_y": -0.05}


def ring(cx, cz, radius, y, count, phase=0.0):
    angles = np.linspace(0, 2 * np.pi, count, endpoint=False) + phase
    return np.stack([cx + radius * np.cos(angles), np.full(count, y), cz + radius * np.sin(angles)], axis=1)


def synthetic_head():
    """顔の面（いちばん大きい島。左右の目の穴の縁の輪を持つ）・眼球の面（120 頂点、高さ 24 mm）・まつ毛の板（10 頂点）を左右に。"""
    parts, roots, boundary, loops = [], [], [], []
    face = np.concatenate([ring(0.0, 1.45, 0.09, -0.08, 300), ring(0.035, 1.5, 0.012, -0.07, 24), ring(-0.035, 1.5, 0.012, -0.07, 24)])
    parts.append(face)
    roots += [0] * len(face)
    boundary += [False] * 300 + [True] * 48
    loops += [0] * 300 + [1] * 24 + [2] * 24
    start = len(face)
    for sign in (1, -1):
        eyeball = ring(0.035 * sign, 1.5, 0.012, -0.06, 120, 0.1)
        lashes = ring(0.035 * sign, 1.505, 0.013, -0.075, 10)
        for piece in (eyeball, lashes):
            parts.append(piece)
            roots += [start] * len(piece)
            boundary += [False] * len(piece)
            loops += list(range(start, start + len(piece)))
            start += len(piece)
    return np.concatenate(parts), np.array(roots), np.array(boundary), np.array(loops)


def test_closed_shape_covers_the_hole_and_tucks_the_lashes():
    points, roots, boundary, loops = synthetic_head()
    settings = ce.merged({"head": "Head", **EYE})
    moved, report, face_verts = ce.closed_shape(points, roots, boundary, loops, settings)
    assert face_verts == 348 and set(report) == {"L", "R"}
    left = report["L"]
    assert left["rim_verts"] == 24 and [p["kind"] for p in left["parts"]] == ["eyeball", "lash_or_small"]
    eyeball = slice(348, 468)
    assert moved[eyeball, 1] == pytest.approx(-0.07 + 0.0002)                  # 眼球の面は穴の縁の膜の位置へ（縁の y は −0.07）
    assert moved[eyeball, [0]] == pytest.approx(points[eyeball, [0]]) and (moved[eyeball, 2] == points[eyeball, 2]).all()   # 左右と高さはそのまま
    lashes = slice(468, 478)
    assert moved[lashes, 1] == pytest.approx(points[lashes, 1] + 0.03)        # まつ毛は頭の中へ
    assert (moved[:348] == points[:348]).all()                                 # 顔の面は動かさない


def test_closed_shape_reads_lengths_in_meters_whatever_the_scene_unit():
    points, roots, boundary, loops = synthetic_head()
    settings = ce.merged({"head": "Head", **EYE})
    meters, report, _ = ce.closed_shape(points, roots, boundary, loops, settings)
    same, _, _ = ce.closed_shape_in_units(points, 1.0, roots, boundary, loops, settings)
    assert (same == meters).all()                                              # scale_length 1 なら値はそのまま
    centimeters, scaled, _ = ce.closed_shape_in_units(points * 100, 0.01, roots, boundary, loops, settings)   # 1 単位 = 1 cm のシーン
    assert centimeters == pytest.approx(meters * 100) and scaled["L"]["rim_verts"] == report["L"]["rim_verts"]


def test_absolute_shape_keys_are_refused():
    assert ce.absolute_keys_problem("Head", True) is None                       # 相対の形状キー（いつもの）はそのまま
    assert "絶対" in ce.absolute_keys_problem("Head", False)                     # 時間で形を選ぶキーには足さない


def test_shared_shape_keys_stay_on_for_every_expression_that_uses_them():
    shape_keys = {"closed_smile": "eyes_closed", "closed_sad": "eyes_closed", "wink": "wink_L"}
    assert pass_render.shape_key_values(shape_keys, "closed_smile") == {"eyes_closed": 1.0, "wink_L": 0.0}
    assert pass_render.shape_key_values(shape_keys, "closed_sad") == {"eyes_closed": 1.0, "wink_L": 0.0}
    assert pass_render.shape_key_values(shape_keys, "smile") == {"eyes_closed": 0.0, "wink_L": 0.0}


def test_closed_shape_stops_without_an_eyeball():
    points, roots, boundary, loops = synthetic_head()
    with pytest.raises(ValueError, match="眼球"):
        ce.closed_shape(points, roots, boundary, loops, ce.merged({"head": "Head", **EYE, "eyeball": {"min_verts": 500}}))


def test_boundary_loops_find_the_open_edges_of_the_face():
    # 2×2 の四角形の格子（9 頂点）。外周の 8 頂点が縁で、1 つの輪
    quads = [[0, 1, 4, 3], [1, 2, 5, 4], [3, 4, 7, 6], [4, 5, 8, 7]]
    keys = [[tuple(sorted((q[i], q[(i + 1) % 4]))) for i in range(4)] for q in quads]
    boundary, loops = ce.boundary_loops(9, keys, np.zeros(9, int), 0)
    assert boundary.sum() == 8 and not boundary[4] and len(set(loops[boundary])) == 1


def test_expression_at_follows_the_table():
    times = [[0.0, "smile"], [2.8, "closed"], [4.1, "smile"]]
    assert [pass_render.expression_at(times, t) for t in (0.0, 2.79, 2.8, 4.0, 4.1, 30.0)] == ["smile", "smile", "closed", "closed",
                                                                                                "smile", "smile"]
    assert pass_render.expression_at([[1.0, "closed"]], 0.0) == "closed"     # 最初の秒より前は最初の名前（試作と同じ）


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
plan = [(fr["T"], fr["f"]) for fr in track["frames"]]
rendered = {k: [] for k in pass_render.all_kinds(settings)}
shown = {}
for T, f in plan:
    shown[str(T)] = pass_render.expression_at(settings["expressions"]["times"], (T - 1) / track["fps"])
    for kind in pass_render.all_kinds(settings):
        path = os.path.join(value("--passes-dir"), pass_render.pass_file(kind, T, settings["format"]))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").write(b"x")
        rendered[kind].append(T)
frames = [pass_render.camera_entry(T, f, [[1, 0, 0, 0], [0, 1, 0, -3], [0, 0, 1, 1], [0, 0, 0, 1]], 35.0, (0.0, 0.0), 24.0,
                                   settings["resolution"], 1.0, [0.0, 0.0, 1.0]) for T, f in plan]
json.dump({"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": settings["resolution"], "fps": track["fps"],
           "frames": frames}, open(value("--cameras-out"), "w"))
counts = {name: list(shown.values()).count(name) for name in set(shown.values())}
json.dump({"schema_version": "model3d-pass-render/0.1", "planned": [{"T": T, "f": f} for T, f in plan], "rendered": rendered,
           "skipped_existing": {k: [] for k in rendered}, "fps": track["fps"], "resolution": settings["resolution"],
           "camera": {"from_record": 0, "from_track": len(plan)}, "objects": {"character": ["Body", "Head"]},
           "settings": settings, "expressions": {"shown": shown, "counts": counts}}, open(value("--report"), "w"))
'''


def write_recipe(tmp_path, expressions):
    for name in ("smile", "closed"):
        Image.new("RGB", (8, 8), (200, 150, 140)).save(tmp_path / f"{name}.png")
    (tmp_path / "dance.blend").write_bytes(b"dance")
    frames = [{"T": T, "f": T, "camera": {"location": [0, -3, 1], "target": [0, 0, 1]}} for T in (1, 85, 124)]
    atomic_json(tmp_path / "track.json", {"schema_version": "model3d-stage-track/0.1", "fps": 30, "frames": frames})
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "faces",
        "inputs": {"dance": {"path": "dance.blend"}, "track": {"path": "track.json"}},
        "steps": [{"id": "passes", "operation": "render.passes", "inputs": {"blend": "$inputs.dance", "track": "$inputs.track"},
                   "parameters": {"blender": blender, "passes": ["char"], "expressions": expressions},
                   "outputs": {"passes": "passes.json", "cameras": "cameras.json", "report": "report.json"}}]})
    return tmp_path / "recipe.json"


def test_passes_with_expressions(tmp_path):
    from model3d import executor
    expressions = {"objects": ["Head"], "images": {"smile": "smile.png", "closed": "closed.png"},
                   "times": [[0.0, "smile"], [2.8, "closed"], [4.1, "smile"]], "shape_keys": {"closed": "eyes_closed"}}
    result = executor.run_recipe(write_recipe(tmp_path, expressions), tmp_path / "runs")
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["passes"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    settings = report["settings"]["expressions"]
    assert Path(settings["images"]["smile"]).is_absolute() and settings["shape_keys"] == {"closed": "eyes_closed"}
    assert report["expressions"]["shown"] == {"1": "smile", "85": "closed", "124": "smile"}   # T 85 は 2.8 秒、T 124 は 4.1 秒
    observed = result["steps"]["passes"]["observations"][0]["expressions"]
    assert set(observed["images"]) == {"smile", "closed"} and len(observed["images"]["smile"]["sha256"]) == 64


@pytest.mark.parametrize("expressions, message", [
    ({"objects": ["Head"], "images": {"smile": "nothing.png"}, "times": [[0.0, "smile"]]}, "絵がありません"),
    ({"objects": ["Head"], "images": {"smile": "smile.png"}, "times": [[2.0, "smile"], [1.0, "closed"]]}, "小さい順"),
    ({"objects": ["Head"], "images": {"wink": "smile.png"}, "times": [[0.0, "smile"]]}, "出てこない"),
    ({"objects": ["Head"], "times": [[0.0, "smile"]]}, "images"),
    ({"objects": [], "images": {"smile": "smile.png"}, "times": [[0.0, "smile"]]}, "objects"),
    ({"objects": ["Head"], "images": {"smile": "smile.png"}, "times": [[0.0, "smile"]], "fade": 1}, "知らない鍵"),
])
def test_bad_expressions_stop_before_blender(tmp_path, expressions, message):
    from model3d import executor
    result = executor.run_recipe(write_recipe(tmp_path, expressions), tmp_path / "runs")
    assert result["status"] == "failed" and message in result["steps"]["passes"]["error"]


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({"eye_x": [0.008, 0.062], "eye_z": [1.47, 1.525]}, "front_y"),
    ({"eye_x": [0.06, 0.008], "eye_z": [1.47, 1.525], "front_y": -0.05}, "eye_x"),
    ({"eye_x": [0.008, 0.062], "eye_z": [1.47, 1.525], "front_y": -0.05, "rim_min_verts": 2}, "rim_min_verts"),
    ({"eye_x": [0.008, 0.062], "eye_z": [1.47, 1.525], "front_y": -0.05, "eyeball": {"size": 3}}, "知らない設定"),
    ({"eye_x": [0.008, 0.062], "eye_z": [1.47, 1.525], "front_y": -0.05, "tuck": -1}, "tuck"),
])
def test_closed_eye_key_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "import sys\nsys.exit(3)\n")
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("model.closed_eye_key").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), "head": "Head", **parameters},
                                             {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))
