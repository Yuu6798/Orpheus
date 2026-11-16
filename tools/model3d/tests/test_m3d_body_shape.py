"""体つきを広げる（model.body_shape）と測る（check.body_size）: 形の計算（Blender なし）、形状キーへの書き込み（代役の bpy の物体）、
工程の受け渡しと悪い設定（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import body_shape as bs
from model3d.blender_adapter import body_size as bz
from model3d.blender_adapter import model_edit as me
from model3d.io import ToolError, atomic_json

HEAD, TAIL = np.array([0.2, 0.0, 1.4]), np.array([0.5, 0.0, 1.4])   # +X 向きの長さ 0.3 m の腕の骨


def ring(x, radius, count=12, z=1.4):
    """骨の軸（+X）のまわりの輪（x の位置、半径 radius）の頂点。"""
    angles = np.linspace(0, 2 * np.pi, count, endpoint=False)
    return np.stack([np.full(count, x), radius * np.cos(angles), z + radius * np.sin(angles)], axis=1)


def tube(xs, radius, count=12):
    """輪を xs の位置に並べた筒の頂点と辺（輪の中と、隣の輪どうし）。"""
    points = np.concatenate([ring(x, radius, count) for x in xs])
    edges = []
    for r in range(len(xs)):
        for i in range(count):
            edges.append([r * count + i, r * count + (i + 1) % count])
            if r:
                edges.append([(r - 1) * count + i, r * count + i])
    return points, np.array(edges)


def test_islands_follow_the_edges():
    roots = me.islands(6, [[0, 1], [1, 2], [4, 5]])
    assert roots[0] == roots[1] == roots[2] and roots[4] == roots[5]
    assert len({roots[0], roots[3], roots[4]}) == 3


def test_tables_are_checked():
    assert me.check_table([[0, 1.1], [1, 1.2]], "p", 0, 1) == [[0, 1.1], [1, 1.2]]
    for bad in ([], [[0, 1], [0, 2]], [[0, 0]], [[0, 1, 2]], [[2, 1]], [[0, float("nan")]]):
        with pytest.raises(ValueError):
            me.check_table(bad, "p", 0, 1)


def test_limbs_widen_around_the_bone_axis():
    points = np.concatenate([ring(0.2 + 0.3 * t, 0.04) for t in (0.0, 0.5, 1.0)])
    moved = bs.limb_moved(points, HEAD, TAIL, [[0.0, 1.0], [0.5, 1.5], [1.0, 2.0]], np.zeros(len(points), bool), 1.0)
    radius = np.hypot(moved[:, 1], moved[:, 2] - 1.4)
    assert radius[:12] == pytest.approx(0.04) and radius[12:24] == pytest.approx(0.06) and radius[24:] == pytest.approx(0.08)
    assert moved[:, 0] == pytest.approx(points[:, 0])                        # 骨に沿った位置は変わらない
    uniform = bs.limb_moved(points, HEAD, TAIL, [[0.0, 2.0], [1.0, 2.0]], np.ones(len(points), bool), 1.02)
    assert np.hypot(uniform[:, 1], uniform[:, 2] - 1.4) == pytest.approx(0.0408)   # 腕輪は決まった倍率


def test_torso_widens_width_and_depth_separately_around_the_centre_line():
    levels = np.arange(0.9, 1.3, 0.02)
    # 胴の断面: 中心 y = 0.05 の、幅 0.2・厚み 0.1 の四角
    rows = [[x, y, z] for z in levels for x in (-0.1, 0.1) for y in (0.0, 0.1)]
    points = np.array(rows, dtype=float)
    centre = bs.centre_line(points, np.ones(len(points), bool), levels, 0.015, 0.25, 4)
    assert centre == pytest.approx(0.05)
    moved = bs.torso_moved(points, {"width": [[0.0, 1.5]], "depth": [[0.0, 2.0]]}, levels, centre)
    assert np.ptp(moved[:4, 0]) == pytest.approx(0.3) and np.ptp(moved[:4, 1]) == pytest.approx(0.2)
    assert moved[:4, 1].mean() == pytest.approx(0.05)                        # 中心線のまわりに広がる
    only_width = bs.torso_moved(points, {"width": [[0.0, 1.5]]}, levels, centre)
    assert (only_width[:, 1] == points[:, 1]).all()                           # 表の無い向きは動かさない
    with pytest.raises(ValueError, match="中心線"):
        bs.centre_line(points, np.zeros(len(points), bool), levels, 0.015, 0.25, 4)


def test_bands_are_small_islands_on_one_bone():
    top = np.array(["upper_arm.L"] * 10 + ["upper_arm.L"] * 3 + ["chest"] * 3)
    roots = np.array([0] * 10 + [10] * 3 + [13] * 3)
    is_band, found = bs.find_bands(roots, top, {"bones": ["upper_arm.L"], "factor": 1.0, "max_verts": 5, "share": 0.95})
    assert found == [["upper_arm.L", 3]] and is_band.sum() == 3 and is_band[10:13].all()   # 大きな島（10 頂点）は腕輪ではない


def arm_with_band():
    """付け根側の肌の筒（t 0〜0.5）・先側の肌の筒（t 0.55〜1）・腕輪（t 0.48〜0.6、太い）の 3 つの島。"""
    shoulder, e1 = tube([0.2 + 0.3 * t for t in (0.0, 0.25, 0.5)], 0.04)
    hand, e2 = tube([0.2 + 0.3 * t for t in (0.55, 0.75, 1.0)], 0.04)
    band, e3 = tube([0.2 + 0.3 * t for t in (0.48, 0.6)], 0.05)
    points = np.concatenate([shoulder, hand, band])
    edges = np.concatenate([e1, e2 + len(shoulder), e3 + len(shoulder) + len(hand)])
    count = len(points)
    top = np.array(["upper_arm.L"] * count)
    groups = [("upper_arm.L", np.arange(count), np.ones(count))]
    return points, edges, top, groups, (len(shoulder), len(hand), len(band))


def test_band_shift_moves_the_band_and_the_seam_under_it():
    points, edges, top, groups, (n1, n2, n3) = arm_with_band()
    roots = me.islands(len(points), edges)
    is_band, found = bs.find_bands(roots, top, {"bones": ["upper_arm.L"], "factor": 1.0, "max_verts": 30, "share": 0.95})
    assert found == [["upper_arm.L", n3]]
    shift = {**bs.SHIFT, "pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.1}
    moved, report = bs.shift_bands(points, roots, top, is_band, groups, {"upper_arm.L": (HEAD, TAIL)}, shift)
    t_before, t_after = (points[:, 0] - 0.2) / 0.3, (moved[:, 0] - 0.2) / 0.3
    band = slice(n1 + n2, None)
    assert t_after[band] == pytest.approx(t_before[band] + 0.1)                 # 腕輪は骨に沿って 0.1（3 cm）先へ
    entry = report["upper_arm.L"]
    assert entry["band_t"] == pytest.approx([0.48, 0.6]) and entry["moved_cm"] == pytest.approx(3.0)
    new_end = 0.48 + 0.1 + 0.07                                                 # 付け根側の筒の端は腕輪の下（内側 0.07）
    assert t_after[:n1].max() == pytest.approx(new_end) and entry["seam_t_after"] == pytest.approx([new_end, new_end + 0.02])
    assert t_after[n1:n1 + n2].min() == pytest.approx(new_end + 0.02)           # 先側の筒の端はすき間 0.02 をあけて縮む
    assert t_after[:n1].min() == pytest.approx(0.0) and t_after[n1:n1 + n2].max() == pytest.approx(1.0)   # 両端はそのまま
    assert np.hypot(moved[:, 1], moved[:, 2] - 1.4) == pytest.approx(np.hypot(points[:, 1], points[:, 2] - 1.4))   # 太さは変えない


def test_band_shift_on_one_skin_tube_moves_only_the_band():
    points, edges, top, groups, (n1, n2, n3) = arm_with_band()
    edges = np.concatenate([edges, [[0, n1]]])                                  # 肌を 1 つの筒にする
    roots = me.islands(len(points), edges)
    is_band, _ = bs.find_bands(roots, top, {"bones": ["upper_arm.L"], "factor": 1.0, "max_verts": 30, "share": 0.95})
    moved, report = bs.shift_bands(points, roots, top, is_band, groups, {"upper_arm.L": (HEAD, TAIL)},
                                   {**bs.SHIFT, "pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.1})
    assert (moved[:n1 + n2] == points[:n1 + n2]).all() and report["upper_arm.L"]["seam_t"] is None


def test_two_bands_on_one_bone_are_refused():
    points, edges, top, groups, (n1, n2, n3) = arm_with_band()
    extra, e4 = tube([0.2 + 0.3 * t for t in (0.1, 0.2)], 0.05)
    points = np.concatenate([points, extra])
    edges = np.concatenate([edges, e4 + len(points) - len(extra)])
    top = np.array(["upper_arm.L"] * len(points))
    roots = me.islands(len(points), edges)
    is_band, _ = bs.find_bands(roots, top, {"bones": ["upper_arm.L"], "factor": 1.0, "max_verts": 30, "share": 0.95})
    with pytest.raises(ValueError, match="2 つ以上"):
        bs.shift_bands(points, roots, top, is_band, [("upper_arm.L", np.arange(len(points)), np.ones(len(points)))],
                       {"upper_arm.L": (HEAD, TAIL)}, {**bs.SHIFT, "pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.1})


def test_weights_mix_the_moves_and_other_bones_stay():
    points = np.concatenate([ring(0.35, 0.04), ring(0.35, 0.04)])
    count = len(points)
    groups = [("upper_arm.L", np.arange(count), np.r_[np.ones(12), np.full(12, 0.5)]),
              ("hand.L", np.arange(12, 24), np.full(12, 0.5))]
    top = np.array(["upper_arm.L"] * count)
    settings = bs.merged({"body": "Body", "limbs": [{"bones": ["upper_arm.L"], "profile": [[0.0, 2.0], [1.0, 2.0]]}]})
    new, info = bs.reshape_body(points, groups, top, np.arange(count), {"upper_arm.L": (HEAD, TAIL)}, settings)
    radius = np.hypot(new[:, 1], new[:, 2] - 1.4)
    assert radius[:12] == pytest.approx(0.08) and radius[12:] == pytest.approx(0.06)   # 半分だけ腕の骨: 2 倍と 1 倍の平均
    assert info["bands"] == [] and info["centre"] is None


class FakeVector(tuple):
    pass


class FakeMatrix:
    def __init__(self, matrix):
        self.matrix = np.asarray(matrix, dtype=float)

    def __array__(self, dtype=None, copy=None):
        return self.matrix

    def inverted(self):
        return FakeMatrix(np.linalg.inv(self.matrix))


class FakeData:
    """foreach_get / foreach_set の代役（頂点の co・形状キーの co）。"""

    def __init__(self, co):
        self.co = np.asarray(co, dtype=float).reshape(-1)

    def __len__(self):
        return len(self.co) // 3

    def foreach_get(self, key, out):
        out[:] = self.co

    def foreach_set(self, key, values):
        self.co = np.asarray(values, dtype=float).copy()


class FakeKey:
    def __init__(self, co):
        self.data = FakeData(co)


class FakeMesh:
    def __init__(self, co, keys=None):
        self.vertices = FakeData(co)
        self.shape_keys = None if keys is None else type("Keys", (), {"key_blocks": [FakeKey(k) for k in keys]})()

    def update(self):
        pass


class FakeObject:
    def __init__(self, co, matrix, keys=None):
        self.data = FakeMesh(co, keys)
        self.matrix_world = FakeMatrix(matrix)


def test_points_are_written_to_every_shape_key():
    matrix = np.eye(4)
    matrix[:3, 3] = [1.0, 0.0, 0.0]                                         # 物体は x に 1 m ずれている
    co = np.zeros((2, 3))
    obj = FakeObject(co, matrix, keys=[co, co + [[0, 0, 0.1], [0, 0, 0]]])
    assert me.world_points(obj) == pytest.approx(np.array([[1.0, 0, 0], [1.0, 0, 0]]))
    me.write_points(obj, np.array([[1.0, 0.2, 0], [1.0, 0, 0]]))
    assert obj.data.vertices.co.reshape(-1, 3) == pytest.approx(np.array([[0, 0.2, 0], [0, 0, 0]]))
    basis, closed = (block.data.co.reshape(-1, 3) for block in obj.data.shape_keys.key_blocks)
    assert basis == pytest.approx(np.array([[0, 0.2, 0], [0, 0, 0]]))
    assert closed == pytest.approx(np.array([[0, 0.2, 0.1], [0, 0, 0]]))


def test_limb_diameters_and_torso_sizes():
    points = np.concatenate([ring(0.2 + 0.3 * t, 0.04, 24) for t in (0.5, 0.55, 0.6, 0.65, 0.7)])
    top = np.array(["upper_arm.L"] * len(points))
    diameters, count = bz.limb_diameters(points, top, HEAD, TAIL, ["upper_arm.L"], 0.6, 0.06, 6)
    assert count == 72 and diameters == pytest.approx([0.08, 0.08], abs=1e-3)
    assert bz.limb_diameters(points, top, HEAD, TAIL, ["lower_arm.L"], 0.6, 0.06, 6) == (None, 0)
    one = np.array([[0.38, 0.04, 1.4]])                                 # 帯に頂点が 1 つだけ: 2 つ目の向きが無いので測らない（落ちない）
    assert bz.limb_diameters(one, np.array(["upper_arm.L"]), HEAD, TAIL, ["upper_arm.L"], 0.6, 0.06, 1) == (None, 1)
    rows = [[x, y, 1.0] for x in (-0.1, 0.1) for y in (0.0, 0.12)] * 2
    torso = bz.torso_sizes(np.array(rows, dtype=float), np.array(["hips"] * 8),
                           {"groups": ["hips"], "heights": [1.0, 1.5], "band": 0.012, "half_width": 0.25, "min_points": 7})
    assert torso[0]["width_cm"] == pytest.approx(20.0) and torso[0]["depth_cm"] == pytest.approx(12.0) and torso[1]["width_cm"] is None


# 代役の Blender: 入力の .blend を写し、決めた報告を書く
STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
script = sys.argv[sys.argv.index("--python") + 1]
if script.endswith("body_shape.py"):
    shutil.copyfile(value("--blend"), value("--out-blend"))
    report = {"schema_version": "model3d-body-shape/0.1", "armature": "Rig",
              "objects": {settings["body"]: {"verts": 10, "moved_mm_max": 5.0, "bands": [["upper_arm.L", 3]]}},
              "x_settings": settings, "x_argv": sys.argv[1:]}
else:
    report = {"schema_version": "model3d-body-size/0.1", "height_m": 1.6, "limbs": [], "torso": [], "x_settings": settings}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''

SHAPE = {"body": "Body", "limbs": [{"bones": ["upper_arm.L", "upper_arm.R"], "profile": [[0.0, 1.1], [1.0, 1.2]]}],
         "torso": {"bones": ["hips", "spine"], "width": [[0.8, 1.0], [1.1, 1.3]], "depth": [[0.8, 1.0], [1.1, 1.2]],
                   "levels": [0.78, 1.36, 0.02]},
         "bands": {"bones": ["upper_arm.L", "upper_arm.R"], "factor": 1.02},
         "band_shift": {"pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.16}, "follow_torso": ["Skirt"]}


def run_recipe(tmp_path, steps):
    from model3d import executor
    (tmp_path / "model.blend").write_bytes(b"model")
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "body",
                                           "inputs": {"model": {"path": "model.blend"}}, "steps": steps})
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_body_shape_and_size_steps(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    steps = [{"id": "shape", "operation": "model.body_shape", "inputs": {"blend": "$inputs.model"},
              "parameters": {"blender": blender, **SHAPE}, "outputs": {"blend": "model_wide.blend", "report": "shape.json"}},
             {"id": "size", "operation": "check.body_size", "inputs": {"blend": "$steps.shape.blend"},
              "parameters": {"blender": blender, "body": "Body", "report_only": True,
                             "limbs": [{"bone": "upper_arm.L", "at": [0.6]}],
                             "torso": {"groups": ["hips", "upper_leg.L"], "heights": [1.04, 1.1]}},
              "outputs": {"report": "size.json"}}]
    result = run_recipe(tmp_path, steps)
    assert result["status"] == "succeeded", result
    shape = json.loads(Path(result["steps"]["shape"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert shape["x_settings"]["limbs"] == SHAPE["limbs"] and shape["x_settings"]["band_shift"]["pairs"] == [["upper_arm.L", "lower_arm.L"]]
    assert Path(shape["x_argv"][shape["x_argv"].index("--python") + 1]).name == "body_shape.py"
    assert result["steps"]["shape"]["observations"][0]["objects"]["Body"]["bands"] == [["upper_arm.L", 3]]
    size = json.loads(Path(result["steps"]["size"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert size["pass"] is None and size["x_settings"]["torso"]["heights"] == [1.04, 1.1]


def test_body_size_without_report_only_needs_review(tmp_path):
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    steps = [{"id": "size", "operation": "check.body_size", "inputs": {"blend": "$inputs.model"},
              "parameters": {"blender": blender, "body": "Body", "limbs": [{"bone": "upper_arm.L", "at": [0.6]}]},
              "outputs": {"report": "size.json"}}]
    assert run_recipe(tmp_path, steps)["status"] == "needs_review"   # 測っただけは合格ではない


class Context:
    tools = {}

    def __init__(self, path):
        self.step_dir = self.recipe_dir = path


@pytest.mark.parametrize("parameters, message", [
    ({}, "body"),
    ({"body": "Body"}, "どれか"),
    ({"body": "Body", "wobble": 1, "limbs": SHAPE["limbs"]}, "知らない設定"),
    ({"body": "Body", "limbs": [{"bones": ["a"], "profile": [[0, 1], [0, 2]]}]}, "増える順"),
    ({"body": "Body", "limbs": [{"bones": ["a"], "profile": [[0, 1], [1.5, 2]]}]}, "0.0〜1.0"),
    ({"body": "Body", "limbs": [{"bones": ["a"], "profile": [[0, 1]]}, {"bones": ["a"], "profile": [[0, 1]]}]}, "2 回"),
    ({"body": "Body", "limbs": [{"bones": ["a"], "profile": [[0, 0]]}]}, "0 より大きく"),
    ({"body": "Body", "torso": {"bones": ["hips"], "width": [[1.0, 1.1]], "levels": [1, 0.5, 0.02]}}, "levels"),
    ({"body": "Body", "torso": {"bones": ["a"], "width": [[1.0, 1.1]]}, "limbs": [{"bones": ["a"], "profile": [[0, 1]]}]}, "limbs にも"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "bands": {"bones": ["hand.L"]}}, "limbs の骨"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "band_shift": {"pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.1}}, "bands が要ります"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "bands": {"bones": ["upper_arm.L"]},
      "band_shift": {"pairs": [["upper_arm.R", "lower_arm.R"]], "shift": 0.1}}, "bands.bones"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "bands": {"bones": ["upper_arm.L"]},
      "band_shift": {"pairs": [["upper_arm.L", "lower_arm.L"]], "shift": 0.1, "stretch_from": 0.9}}, "keep_from"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "follow_torso": ["Skirt"]}, "follow_torso"),
    ({"body": "Body", "limbs": SHAPE["limbs"], "follow_torso": ["Body"]}, "follow_torso に body"),
])
def test_body_shape_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("model.body_shape").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                         {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context(tmp_path))


@pytest.mark.parametrize("parameters, message", [
    ({"body": "Body"}, "limbs か torso"),
    ({"body": "Body", "limbs": [{"bone": "a", "at": [1.2]}]}, "1.0 以下"),
    ({"body": "Body", "limbs": [{"bone": "a", "at": []}]}, "at"),
    ({"body": "Body", "torso": {"groups": ["hips"], "heights": []}}, "heights"),
    ({"body": "Body", "torso": {"groups": ["hips"], "heights": [1.0], "band": 0}}, "band"),
    ({"body": "Body", "limbs": [{"bone": "a", "at": [0.5]}], "window": -1}, "window"),
    ({"body": "Body", "limbs": [{"bone": "a", "at": [0.5]}], "min_points": 1}, "min_points"),
])
def test_body_size_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "model.blend").write_bytes(b"model")
    with pytest.raises(ToolError, match=message):
        get("check.body_size").function({"blend": tmp_path / "model.blend"}, {"blender": str(blender), **parameters},
                                        {"report": tmp_path / "r.json"}, Context(tmp_path))
