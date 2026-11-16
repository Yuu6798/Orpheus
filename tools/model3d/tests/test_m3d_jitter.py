"""ちらつきの検査（check.jitter）: ずれの式と要約（Blender なし）、骨から見た座標での読み方（代役の bpy）、工程の受け渡しと
合否（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path
from types import SimpleNamespace as N

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d import checks
from model3d.blender_adapter import jitter_sample
from model3d.io import ToolError, atomic_json


def _plan(count, first=1):
    return [(k + 1, first + k) for k in range(count)]


def test_offsets_are_half_the_second_difference():
    t = np.arange(6, dtype=float)
    steady = np.stack([np.stack([t * 0.01, t * 0.02, np.zeros(6)], axis=1)] * 2, axis=1)   # 一定の速さで動く 2 頂点
    assert np.allclose(jitter_sample.offsets(steady), 0.0)
    spiked = steady.copy()
    spiked[3, 1, 2] += 0.010                                       # 頂点 1 がコマ 3 だけ 10 mm 跳ぶ
    offset = jitter_sample.offsets(spiked)
    assert offset.shape == (4, 2)
    assert np.allclose(offset[:, 0], 0.0)
    assert np.allclose(offset[:, 1], [0.0, 0.005, 0.010, 0.005])   # 跳んだコマは 10 mm、前後のコマは中間の半分


def test_summary_reports_mm_and_where_it_jumps():
    track = np.zeros((7, 3, 3))
    track[2, 0, 0] = 0.008                                         # 頂点 0 がコマ 3（T 3）で 8 mm
    track[5, 2, 1] = -0.002                                        # 頂点 2 がコマ 6（T 6）で 2 mm
    plan = _plan(7, first=10)
    report = jitter_sample.summary(track, 0.004, plan, top=2)
    offset = jitter_sample.offsets(track)
    assert report["vertices"] == 3
    assert report["mean_mm"] == float(offset.mean() * 1000) and report["peak_mm"] == pytest.approx(8.0)
    assert report["p99_mm"] == float(np.percentile(offset, 99) * 1000)
    assert report["over_percent"] == pytest.approx(100 / 15)       # 15 個の（コマ, 頂点）のうち、4 mm を超えたのは 8 mm の 1 個
    assert [(w["T"], w["f"]) for w in report["worst_frames"]] == [(3, 12), (2, 11)]
    assert report["worst_frames"][0]["peak_mm"] == pytest.approx(8.0)
    assert report["worst_frames"][0]["mean_mm"] == pytest.approx(8.0 / 3)
    assert report["worst_vertices"][0][0] == 0 and report["worst_vertices"][1][0] == 2


class _Matrix:
    """mathutils.Matrix の代役（@ と inverted だけ）。"""

    def __init__(self, values):
        self.values = np.asarray(values, float)

    def __matmul__(self, other):
        return _Matrix(self.values @ other.values)

    def inverted(self):
        return _Matrix(np.linalg.inv(self.values))

    def __array__(self, dtype=None, copy=None):
        return self.values if dtype is None else self.values.astype(dtype)


def _pose(f):
    """コマ f の骨の行列: 骨が x へ 0.3 m/コマで進み、z のまわりに回る（速さが変わる）。"""
    angle = 0.1 * f * f
    c, s = np.cos(angle), np.sin(angle)
    return np.array([[c, -s, 0, 0.3 * f], [s, c, 0, 0], [0, 0, 1, 1.0], [0, 0, 0, 1]])


def _fake_bpy(rest, wobble):
    """骨に付いて回る頂点（rest、骨から見た座標）に、コマ 3 だけ wobble を足すメッシュを持つ代役の bpy。"""
    state = {"f": None}

    class Mesh:
        def __init__(self, points):
            self.vertices = _Vertices(points)

    class Evaluated:
        matrix_world = _Matrix(np.eye(4))

        def to_mesh(self):
            pose = _pose(state["f"])
            points = rest @ pose[:3, :3].T + pose[:3, 3]
            if state["f"] == 3:
                points = points + wobble
            return Mesh(points)

        def to_mesh_clear(self):
            pass

    obj = N(name="skirt", evaluated_get=lambda depsgraph: Evaluated())
    armature = N(matrix_world=_Matrix(np.eye(4)), pose=N(bones={"hips": N(matrix=None)}))

    def frame_set(whole, subframe=0.0):
        state["f"] = whole + subframe
        armature.pose.bones["hips"].matrix = _Matrix(_pose(state["f"]))

    scene = N(frame_set=frame_set)
    bpy = N(context=N(evaluated_depsgraph_get=lambda: None))
    return bpy, scene, armature, obj


class _Vertices:
    def __init__(self, points):
        self.points = np.asarray(points, float)

    def __len__(self):
        return len(self.points)

    def foreach_get(self, name, out):
        assert name == "co"
        out[:] = self.points.reshape(-1)


def test_sample_reads_vertices_in_the_bone_space():
    rest = np.array([[0.0, 0.1, -0.5], [0.2, 0.0, -0.6]])
    wobble = np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.012]])
    bpy, scene, armature, obj = _fake_bpy(rest, wobble)
    tracks = jitter_sample.sample(bpy, scene, armature, "hips", [obj], [1, 2, 3, 4, 5], 1.0)
    track = tracks["skirt"]
    assert track.shape == (5, 2, 3)
    assert np.allclose(track[[0, 1, 3, 4]], rest)                   # 骨に付いて動く所は、骨から見ると動かない
    offset = jitter_sample.offsets(track)
    assert np.allclose(offset[:, 0], 0.0) and offset[1, 1] == pytest.approx(0.012, abs=1e-9)
    # scale_length（1 Blender 単位が何 m か）で m に直す
    assert np.allclose(jitter_sample.sample(bpy, scene, armature, "hips", [obj], [1, 2], 0.01)["skirt"][0], rest * 0.01)
    # 世界の座標で見ると、骨の動きそのもの（速さの変わる回転）が大きなずれになる
    world = np.array([rest @ _pose(f)[:3, :3].T + _pose(f)[:3, 3] for f in (1, 2, 3, 4, 5)])
    assert jitter_sample.offsets(world).max() > 0.05


def test_sample_stops_on_meshes_it_cannot_measure():
    class Counted:
        matrix_world = _Matrix(np.eye(4))

        def __init__(self, counts):
            self.counts, self.calls = counts, 0

        def to_mesh(self):
            count = self.counts[min(self.calls, len(self.counts) - 1)]
            self.calls += 1
            return N(vertices=_Vertices(np.zeros((count, 3))))

        def to_mesh_clear(self):
            pass

    armature = N(matrix_world=_Matrix(np.eye(4)), pose=N(bones={"hips": N(matrix=_Matrix(np.eye(4)))}))
    scene = N(frame_set=lambda whole, subframe=0.0: None)
    bpy = N(context=N(evaluated_depsgraph_get=lambda: None))
    for counts, message in (([4, 4, 5], "頂点の数がコマによって違います"), ([0], "頂点がありません")):
        evaluated = Counted(counts)
        obj = N(name="skirt", evaluated_get=lambda depsgraph, e=evaluated: e)
        with pytest.raises(SystemExit, match=message):
            jitter_sample.sample(bpy, scene, armature, "hips", [obj], [1, 2, 3], 1.0)


def test_verdict_compares_each_mesh_with_the_thresholds():
    raw = {"status": "done", "measured": {"skirt": {"mean_mm": 3.0, "p99_mm": 40.0, "peak_mm": 180.0, "over_percent": 12.5},
                                          "sash": {"mean_mm": 1.0, "p99_mm": 6.0, "peak_mm": 20.0, "over_percent": 0.5}}}
    measured = checks.jitter_verdict(raw)
    assert measured["pass"] is None and "測っただけ" in measured["reason"] and measured["thresholds"] == {}
    report = checks.jitter_verdict(raw, {"max_mean_mm": 2.0, "max_peak_mm": 100.0, "max_p99_mm": None})
    assert report["pass"] is False and report["thresholds"] == {"max_mean_mm": 2.0, "max_peak_mm": 100.0}
    assert len(report["failures"]) == 2 and all(f.startswith("skirt: ") for f in report["failures"])
    assert checks.jitter_verdict(raw, {"max_mean_mm": 5.0})["pass"] is True
    skipped = checks.jitter_verdict({"status": "skipped", "reason": "3 コマ以上が要ります（2 コマ）", "measured": {}},
                                    {"max_mean_mm": 1.0})
    assert skipped["pass"] is None and "3 コマ" in skipped["reason"]
    with pytest.raises(ToolError, match="知らないしきい値"):
        checks.jitter_verdict(raw, {"max_wobble": 1})


# 代役の Blender: 受け取った設定を写し、決めた値の報告を書く
STAND_IN = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
frames = settings["frames"] or [[1, 1], [2, 2], [3, 3], [4, 4]]
measured = {name: {"vertices": 10, "mean_mm": 3.0, "p99_mm": 30.0, "over_percent": 8.0, "peak_mm": 90.0,
                   "worst_frames": [{"T": frames[1][0], "f": frames[1][1], "peak_mm": 90.0, "mean_mm": 3.0}],
                   "worst_vertices": [[4, 90.0]]} for name in settings["objects"]}
toggled = [{"object": settings["objects"][0], "modifier": "cloth", "type": "CLOTH", "measured": measured}] if settings["toggle"] else []
report = {"schema_version": "model3d-jitter/0.1", "check": "jitter", "status": "done", "armature": "Rig", "bone": settings["bone"],
          "objects": settings["objects"], "frames": {"source": settings["frames_source"], "count": len(frames)},
          "jump_mm": settings["jump_m"] * 1000, "scale_length": 1.0, "measured": measured, "toggled": toggled,
          "x_settings": settings, "x_argv": sys.argv[1:]}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''


def _recipe(tmp_path, parameters, track=None):
    (tmp_path / "dance.blend").write_bytes(b"blend")
    inputs = {"dance": {"path": "dance.blend"}}
    step_inputs = {"blend": "$inputs.dance"}
    if track is not None:
        atomic_json(tmp_path / "track.json", track)
        inputs["track"] = {"path": "track.json"}
        step_inputs["track"] = "$inputs.track"
    step = {"id": "jitter", "operation": "check.jitter", "inputs": step_inputs, "parameters": parameters,
            "outputs": {"report": "jitter.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "jitter", "inputs": inputs,
                                           "steps": [step]})
    return tmp_path / "recipe.json"


def test_check_jitter_passes_the_frames_and_the_bone_to_blender(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    track = {"schema_version": "model3d-stage-track/0.1", "fps": 30,
             "frames": [{"T": T, "f": 25 + (T - 1) * 0.8} for T in range(1, 6)]}
    recipe = _recipe(tmp_path, {"blender": blender, "objects": ["skirt", "sash"], "bone_prefix": "", "bone": "hips",
                                "jump_mm": 2.5, "toggle": True, "top": 3, "max_mean_mm": 5.0}, track)
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    report = json.loads(Path(result["steps"]["jitter"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    settings = report["x_settings"]
    assert settings["frames"] == [[1, 25], [2, 25.8], [3, 26.6], [4, 27.4], [5, 28.2]] and settings["frames_source"] == "track"
    assert settings["bone"] == "hips" and settings["jump_m"] == 0.0025 and settings["toggle"] is True and settings["top"] == 3
    assert settings["toggle_types"] == ["SHRINKWRAP", "SURFACE_DEFORM", "CLOTH", "SMOOTH"]
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "jitter_sample.py" and "--factory-startup" in argv
    assert "--python-expr" not in argv                                       # アドオンは既定で有効にしない
    assert report["pass"] is True and report["thresholds"] == {"max_mean_mm": 5.0} and report["blender_version"] == "5.2.4"
    assert report["toggled"][0]["type"] == "CLOTH"
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["jitter"]["reused_from"] is None     # 毎回測る
    # しきい値を超えると needs_review、しきい値が無ければ測っただけ（needs_review）、report_only なら測るだけで成功
    for parameters, status in (({"max_peak_mm": 50.0}, "needs_review"), ({}, "needs_review"), ({"report_only": True}, "succeeded")):
        recipe = _recipe(tmp_path, {"blender": blender, "objects": ["skirt"], "frames": [10, 14], **parameters})
        result = executor.run_recipe(recipe, tmp_path / "runs")
        assert result["status"] == status, (parameters, result)
        report = json.loads(Path(result["steps"]["jitter"]["artifacts"]["report"]).read_text(encoding="utf-8"))
        assert report["x_settings"]["frames"] == [[1, 10], [2, 11], [3, 12], [4, 13], [5, 14]]
        assert report["x_settings"]["frames_source"] == "range" and report["x_settings"]["bone"] == "mixamorig:Hips"
    recipe = _recipe(tmp_path, {"blender": blender, "objects": ["skirt"], "report_only": True})
    report = json.loads(Path(executor.run_recipe(recipe, tmp_path / "runs")["steps"]["jitter"]["artifacts"]["report"])
                        .read_text(encoding="utf-8"))
    assert report["x_settings"]["frames"] is None and report["x_settings"]["frames_source"] == "scene"   # シーンの全コマ


@pytest.mark.parametrize("parameters, track, message", [
    ({"objects": ["skirt"], "wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "objects"),
    ({"objects": "skirt"}, None, "objects"),
    ({"objects": ["skirt", "skirt"]}, None, "objects"),
    ({"objects": ["skirt"], "bone": ""}, None, "bone"),
    ({"objects": ["skirt"], "armature": 3}, None, "armature"),
    ({"objects": ["skirt"], "toggle": "yes"}, None, "toggle"),
    ({"objects": ["skirt"], "toggle_types": "CLOTH"}, None, "toggle_types"),
    ({"objects": ["skirt"], "frames": [5, 3]}, None, "frames"),
    ({"objects": ["skirt"], "frames": [1, 2]}, None, "3 コマ以上"),
    ({"objects": ["skirt"], "frames": [1, 9]}, [1, 2, 3], "一緒に使えません"),
    ({"objects": ["skirt"]}, [1, 2], "3 コマ以上"),
    ({"objects": ["skirt"]}, [1, 2, 4], "等間隔"),
    ({"objects": ["skirt"]}, [3, 2, 1], "等間隔"),
    ({"objects": ["skirt"], "jump_mm": 0}, None, "jump_mm"),
    ({"objects": ["skirt"], "top": 0}, None, "top"),
    ({"objects": ["skirt"], "max_mean_mm": -1}, None, "max_mean_mm"),
    ({"objects": ["skirt"], "no_blender": True}, None, "Blender"),
])
def test_check_jitter_rejects_bad_settings(tmp_path, parameters, track, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"blend")
    inputs = {"blend": tmp_path / "dance.blend"}
    if track is not None:
        atomic_json(tmp_path / "track.json", {"schema_version": "model3d-stage-track/0.1",
                                              "frames": [{"T": k + 1, "f": f} for k, f in enumerate(track)]})
        inputs["track"] = tmp_path / "track.json"
    if not parameters.pop("no_blender", False):
        parameters = {"blender": str(blender), **parameters}

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("check.jitter").function(inputs, parameters, {"report": tmp_path / "r.json"}, Context())
