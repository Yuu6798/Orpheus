"""表情の形の計算（model.face_shapes）: 合成の頭で、目の開きの測り方・まばたきと笑顔の閉じ目・眉・口角と、工程の受け渡しと悪い設定
（Blender なし）。3 作目の VRM のダンスの頭との突き合わせ（同じ値になる）は docs/model3d.md（リポジトリ外の素材なので CI には無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from model3d import face_shapes
from model3d.io import ToolError, atomic_json

STEP = 0.002
EYE_X, EYE_Z, HOLE = 0.036, 1.50, (0.012, 0.0035)          # 目の中心（|x|・高さ）と、顔の面の穴の楕円の半径（横・縦）


def synthetic_head():
    """合成の頭: 顔の面（−y = −0.06 の格子。目の所は楕円の穴）・その奥の白目の扇（中心に 26 本の辺。格子と 1 本の辺でつながる）・
    扇の手前の瞳（小さな円板の別の島）・目の上の眉（細い帯の別の島）・口の所の、顔の面のすぐ裏の唇の合わせ目（3 列の頂点）。
    戻り: (co, edges, tris, 目印の頂点の番号の辞書)。"""
    xs = np.round(np.arange(-0.08, 0.08 + 1e-9, STEP), 6)
    zs = np.round(np.arange(1.42, 1.56 + 1e-9, STEP), 6)
    co = [[x, -0.06, z] for z in zs for x in xs]
    index = {(i, j): j * len(xs) + i for j in range(len(zs)) for i in range(len(xs))}
    tris = []

    def in_hole(x, z):
        return any(((x - sign * EYE_X) / HOLE[0]) ** 2 + ((z - EYE_Z) / HOLE[1]) ** 2 < 1 for sign in (-1, 1))

    for j in range(len(zs) - 1):
        for i in range(len(xs) - 1):
            if in_hole(xs[i] + STEP / 2, zs[j] + STEP / 2):
                continue
            a, b, c, d = index[i, j], index[i + 1, j], index[i + 1, j + 1], index[i, j + 1]
            tris += [(a, b, c), (a, c, d)]
    edges = set()
    for a, b, c in tris:
        edges |= {tuple(sorted(pair)) for pair in ((a, b), (b, c), (c, a))}
    used = {v for tri in tris for v in tri}
    for v in range(len(co)):                                                  # 穴の中の使われない格子の頂点は、頭の後ろへ（1 頂点の島）
        if v not in used:
            co[v][1] = 0.1
    marks = {}

    def add(points):
        start = len(co)
        co.extend(points)
        return start

    for side, sign in (("R", -1.0), ("L", 1.0)):
        centre = add([[sign * EYE_X, -0.0575, EYE_Z]])                       # 白目の扇
        angles = np.linspace(0, 2 * np.pi, 26, endpoint=False)
        ring = add([[sign * EYE_X + 0.014 * np.cos(t), -0.0575, EYE_Z + 0.005 * np.sin(t)] for t in angles])
        for k in range(26):
            tris.append((centre, ring + k, ring + (k + 1) % 26))
            edges |= {(centre, ring + k), tuple(sorted((ring + k, ring + (k + 1) % 26)))}
        edges.add(tuple(sorted((ring, index[int(round((sign * EYE_X + 0.014 + 0.08) / STEP)), int(round((EYE_Z - 1.42) / STEP))]))))
        iris = add([[sign * EYE_X, -0.059, EYE_Z]] + [[sign * EYE_X + 0.004 * np.cos(t), -0.059, EYE_Z + 0.004 * np.sin(t)]
                                                    for t in np.linspace(0, 2 * np.pi, 12, endpoint=False)])
        for k in range(12):
            tris.append((iris, iris + 1 + k, iris + 1 + (k + 1) % 12))
            edges |= {(iris, iris + 1 + k), tuple(sorted((iris + 1 + k, iris + 1 + (k + 1) % 12)))}
        bar_x = np.round(sign * np.arange(0.020, 0.0561, 0.004), 6)
        brow = add([[x, -0.066, z] for z in (1.521, 1.523) for x in bar_x])      # 眉: 2 列の帯
        count = len(bar_x)
        for k in range(count - 1):
            a, b, c, d = brow + k, brow + k + 1, brow + count + k + 1, brow + count + k
            tris += [(a, b, c), (a, c, d)]
            edges |= {tuple(sorted(p)) for p in ((a, b), (b, c), (c, a), (c, d), (d, a))}
        marks[side] = {"centre": centre, "ring": ring, "iris": range(iris, iris + 13), "brow": range(brow, brow + 2 * count)}
    seam = add([[x, -0.057, z] for z in (1.449, 1.450, 1.451) for x in np.round(np.arange(-0.02, 0.0201, STEP), 6)])
    seam_count = len(co) - seam
    for k in range(seam_count - 1):
        edges.add((seam + k, seam + k + 1))
    edges.add((seam, index[30, 15]))                                          # 合わせ目は顔の面（肌の島）とつながる
    marks["seam"] = range(seam, seam + seam_count)
    marks["grid"] = index
    marks["xs"], marks["zs"] = xs, zs
    return np.array(co, dtype=float), np.array(sorted(edges), dtype=np.int64), np.array(tris, dtype=np.int64), marks


SETTINGS = {
    "front_y": -0.02, "resolution": 0.0001,
    "eyes": {"find": {"x": [0.005, 0.075], "z": [1.485, 1.515], "front_y": -0.03},
             "window": {"half_width": 0.034, "z": [1.482, 1.524]},
             "iris": {"front_y": -0.045, "max_size": 0.02, "max_depth": 0.005, "back_y": -0.07},
             "brow": {"front_y": -0.03, "z": [1.512, 1.53], "min_size": 0.03, "min_z": 1.511},
             "region": {"front_y": -0.03, "max_part_size": 0.05},
             "upper": {"keep": 0.002, "fade": 0.008}, "lower": {"fade": 0.007}, "close_at": 0.2, "iris_back": 0.002,
             "smile": {"arc": 0.0025}},
    "brows": {"forward": 0.0004, "shapes": {"brow_up": {"inner": 0.0015, "outer": 0.0015}, "brow_angry": {"inner": -0.002, "outer": 0.0005}}},
    "mouth": {"window": {"half_width": 0.03, "z": [1.43, 1.47]}, "zone": {"half_width": 0.028},
              "seam": {"depth": 0.006, "centre": 0.012, "band": 0.003}, "corner": {"radius": 0.006, "depth": 0.012},
              "smile": {"out": 0.0015, "up": 0.0025, "back": 0.0005}},
}


@pytest.fixture(scope="module")
def built():
    co, edges, tris, marks = synthetic_head()
    shapes, report, debug = face_shapes.build(co, edges, tris, json.loads(json.dumps(SETTINGS)))
    return co, marks, shapes, report, debug


def grid_vertex(marks, x, z):
    i = int(round((x - marks["xs"][0]) / STEP))
    j = int(round((z - marks["zs"][0]) / STEP))
    return marks["grid"][i, j]


def test_the_order_of_the_shapes_is_right_eye_left_eye_then_the_mouth(built):
    _, _, shapes, report, _ = built
    assert list(shapes) == ["blink_R", "blink_smile_R", "brow_up_R", "brow_angry_R", "blink_L", "blink_smile_L", "brow_up_L", "brow_angry_L",
                            "mouth_smile"]
    assert list(report["shapes"]) == list(shapes) and all(v["moved_vertices"] > 0 for v in report["shapes"].values())


def test_the_eye_parts_are_found(built):
    _, marks, _, report, _ = built
    for side in ("R", "L"):
        eye = report["eyes"][side]
        assert eye["fan_centre"] == marks[side]["centre"] and eye["ring"] == 26          # 辺のいちばん多い頂点
        assert eye["iris_parts"] == [13] and eye["brow_parts"] == [len(marks[side]["brow"])]
        assert eye["opening_height_mm"] == pytest.approx(8.0, abs=0.2)                    # 穴の縦は真ん中で 4 段（格子の 2 mm の段）
        assert eye["upper_edge_y"] == [-0.06, -0.06] and eye["lower_edge_y"] == [-0.06, -0.06]   # 縁のすぐ外は顔の面


def _edges_at(debug, side, x):
    xcol, U, L = debug[f"{side}_xcol"], debug[f"{side}_U"], debug[f"{side}_L"]
    return float(np.interp(x, xcol, U)), float(np.interp(x, xcol, L))


def test_blink_brings_both_lids_to_the_closing_line(built):
    co, marks, shapes, _, debug = built
    for side, sign in (("R", -1.0), ("L", 1.0)):
        blink = shapes["blink_" + side]
        Ux, Lx = _edges_at(debug, side, sign * EYE_X)
        close = Lx + SETTINGS["eyes"]["close_at"] * (Ux - Lx)
        top, bottom = grid_vertex(marks, sign * EYE_X, 1.504), grid_vertex(marks, sign * EYE_X, 1.496)   # 穴の上と下の縁の頂点
        assert blink[top, 2] == pytest.approx(close - Ux, abs=1e-12) and blink[top, 2] < -0.005   # 上まぶたは閉じる線まで下りる
        assert blink[bottom, 2] == pytest.approx(close - Lx, abs=1e-12) and 0 < blink[bottom, 2] < 0.002
        assert (blink[grid_vertex(marks, sign * EYE_X, 1.516)] == 0).all()             # 縁から 12 mm 上（keep + fade の外）は動かない
        assert (blink[grid_vertex(marks, sign * EYE_X, 1.488)] == 0).all()             # 下は fade の外
        assert (blink[list(marks[side]["iris"]), 1] == SETTINGS["eyes"]["iris_back"]).all()   # 瞳は奥へ下げるだけ
        assert (blink[list(marks[side]["iris"])][:, [0, 2]] == 0).all()
        socket = [marks[side]["centre"]] + list(range(marks[side]["ring"], marks[side]["ring"] + 26))
        assert (blink[socket] == 0).all() and (blink[list(marks[side]["brow"])] == 0).all()   # 白目と眉は動かさない
        assert blink[top, 1] == pytest.approx(0.0, abs=1e-12)                           # 平らな顔は上下の縁の奥行きが同じで、寄せる量は 0
        assert blink[list(marks[side]["iris"])][:, 1].max() == SETTINGS["eyes"]["iris_back"]   # 奥行きの手当ては瞳を下げる分だけ


def test_the_two_eyes_mirror_each_other(built):
    _, marks, shapes, _, _ = built
    for x, z in ((0.036, 1.504), (0.030, 1.502), (0.042, 1.496), (0.026, 1.500)):
        left, right = grid_vertex(marks, x, z), grid_vertex(marks, -x, z)
        assert shapes["blink_L"][left, 2] == pytest.approx(shapes["blink_R"][right, 2], abs=2e-5)
        assert shapes["blink_smile_L"][left, 2] == pytest.approx(shapes["blink_smile_R"][right, 2], abs=2e-5)


def test_the_smile_closes_on_an_arc_above_the_corner_line(built):
    co, marks, shapes, report, debug = built
    top = grid_vertex(marks, EYE_X, 1.504)
    assert shapes["blink_smile_L"][top, 2] > shapes["blink_L"][top, 2]                 # 弧は閉じる線より上（上まぶたは少し下りる）
    bottom = grid_vertex(marks, EYE_X, 1.496)
    assert shapes["blink_smile_L"][bottom, 2] > shapes["blink_L"][bottom, 2]           # 下まぶたは多く上がる
    xa, xb = report["eyes"]["L"]["x"]
    Ux, Lx = _edges_at(debug, "L", EYE_X)
    U, L = debug["L_U"], debug["L_L"]
    corner = float(np.interp(EYE_X, [xa, xb], [0.5 * (U[0] + L[0]), 0.5 * (U[-1] + L[-1])]))
    s = np.clip((EYE_X - xa) / (xb - xa), 0, 1)
    arc = np.clip(corner + SETTINGS["eyes"]["smile"]["arc"] * np.sin(np.pi * s) ** 0.8, Lx + 0.05 * (Ux - Lx), Ux - 0.10 * (Ux - Lx))
    assert shapes["blink_smile_L"][bottom, 2] == pytest.approx(arc - Lx, rel=2e-3)


def test_brows_move_as_a_whole(built):
    co, marks, shapes, _, _ = built
    for side in ("R", "L"):
        brow = list(marks[side]["brow"])
        up, angry = shapes["brow_up_" + side][brow], shapes["brow_angry_" + side][brow]
        assert (up[:, 2] == 0.0015).all() and (up[:, 1] == -0.0004).all() and (up[:, 0] == 0).all()   # 同じ量なら一定
        x = np.abs(co[brow, 0])
        assert angry[np.argmin(x), 2] == pytest.approx(-0.002) and angry[np.argmax(x), 2] == pytest.approx(0.0005)   # 内側が下がり外側が上がる
        others = np.ones(len(co), dtype=bool)
        others[brow] = False
        assert (shapes["brow_up_" + side][others] == 0).all()


def test_the_mouth_corners_move_out_up_and_back(built):
    co, marks, shapes, report, _ = built
    assert report["mouth"]["seam_z_centre"] == pytest.approx(1.45) and report["mouth"]["corner_x"] == pytest.approx(0.02, abs=1e-6)
    smile = shapes["mouth_smile"]
    out, up, back = (SETTINGS["mouth"]["smile"][k] for k in ("out", "up", "back"))
    left, right = grid_vertex(marks, 0.02, 1.45), grid_vertex(marks, -0.02, 1.45)
    assert smile[left] == pytest.approx([out, back, up], rel=1e-3) and smile[right] == pytest.approx([-out, back, up], rel=1e-3)
    assert np.abs(smile[grid_vertex(marks, 0.0, 1.45)]).max() < 1e-6                    # 口の真ん中はほとんど動かない
    assert (smile[grid_vertex(marks, 0.02, 1.43)] == pytest.approx([0, 0, 0], abs=1e-6))   # 20 mm 下
    eyes = np.zeros(len(co), dtype=bool)
    for side in ("R", "L"):
        eyes[list(marks[side]["brow"])] = True
    assert (smile[eyes] == 0).all()


def test_raster_front_keeps_the_nearest_triangle_in_front_of_the_head():
    co = np.array([[0, -0.05, 0], [1, -0.05, 0], [0, -0.05, 1], [0, -0.06, 0], [1, -0.06, 0], [0, -0.06, 1], [0, 0.05, 0], [1, 0.05, 0],
                   [0, 0.05, 1]], dtype=float) * [1, 1, 1]
    co[:, [0, 2]] *= 0.01
    tris = np.array([[0, 1, 2], [3, 4, 5], [6, 7, 8]])
    owner, depth = face_shapes.raster_front(co, tris, 0.0, 0.01, 0.0, 0.01, 0.001, -0.02)
    inside = owner >= 0
    assert inside.sum() > 0 and set(owner[inside].tolist()) == {1}                     # いちばん手前（−y）の三角形。頭の後ろ側は描かない
    assert (depth[inside] == pytest.approx(-0.06))


def test_build_stops_when_a_part_is_missing():
    co, edges, tris, marks = synthetic_head()
    settings = json.loads(json.dumps(SETTINGS))
    settings["eyes"]["iris"]["max_size"] = 0.001                                       # 瞳が小さすぎる箱
    with pytest.raises(ValueError, match="瞳の部品"):
        face_shapes.build(co, edges, tris, settings)
    settings = json.loads(json.dumps(SETTINGS))
    settings["eyes"]["find"]["z"] = [1.40, 1.41]
    with pytest.raises(ValueError, match="扇の中心"):
        face_shapes.build(co, edges, tris, settings)
    settings = json.loads(json.dumps(SETTINGS))
    settings["eyes"]["brow"]["min_size"] = 0.5
    with pytest.raises(ValueError, match="眉の部品"):
        face_shapes.build(co, edges, tris, settings)
    settings = json.loads(json.dumps(SETTINGS))
    settings["mouth"]["seam"]["depth"] = 0.001                                         # 合わせ目（3 mm 裏）が帯の外
    with pytest.raises(ValueError, match="合わせ目"):
        face_shapes.build(co, edges, tris, settings)


def test_without_brows_and_smile_only_the_blinks_are_made():
    co, edges, tris, _ = synthetic_head()
    settings = json.loads(json.dumps(SETTINGS))
    del settings["brows"], settings["mouth"], settings["eyes"]["smile"]
    shapes, report, _ = face_shapes.build(co, edges, tris, settings)
    assert list(shapes) == ["blink_R", "blink_L"] and "mouth" not in report


# ---------- 工程 ----------

def _recipe(tmp_path, parameters):
    co, edges, tris, _ = synthetic_head()
    np.savez_compressed(tmp_path / "head_mesh.npz", co=co, edges=edges, tris=tris)
    atomic_json(tmp_path / "recipe.json", {
        "schema_version": "model3d-recipe/0.1", "name": "face",
        "inputs": {"mesh": {"path": "head_mesh.npz"}},
        "steps": [{"id": "shapes", "operation": "model.face_shapes", "inputs": {"mesh": "$inputs.mesh"}, "parameters": parameters,
                   "outputs": {"shapes": "shapes.npz", "report": "report.json", "debug": "debug.npz"}}]})
    return tmp_path / "recipe.json"


def test_face_shapes_keeps_an_upper_case_output_name(tmp_path):
    from model3d import executor
    path = _recipe(tmp_path, SETTINGS)
    recipe = json.loads(path.read_text(encoding="utf-8"))
    recipe["steps"][0]["outputs"] = {"shapes": "SHAPES.NPZ", "report": "report.json"}
    atomic_json(path, recipe)
    result = executor.run_recipe(path, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    assert Path(result["steps"]["shapes"]["artifacts"]["shapes"]).name == "SHAPES.NPZ"


def test_face_shapes_operation_writes_the_shapes_and_is_reused(tmp_path):
    from model3d import executor
    path = _recipe(tmp_path, SETTINGS)
    result = executor.run_recipe(path, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["shapes"]
    with np.load(step["artifacts"]["shapes"], allow_pickle=False) as data:
        assert data.files[0] == "blink_R" and data.files[-1] == "mouth_smile" and data["blink_L"].shape[1] == 3
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-face-shapes/0.1" and report["settings"]["eyes"]["close_at"] == 0.2
    assert step["observations"][0]["eyes"]["L"]["iris_parts"] == [13]
    again = executor.run_recipe(path, tmp_path / "runs")                                 # 計算だけなので、同じ入力なら再利用する
    assert again["steps"]["shapes"].get("reused_from")


def test_a_shape_that_moves_nothing_needs_review(tmp_path):
    from model3d import executor
    parameters = json.loads(json.dumps(SETTINGS))
    parameters["brows"]["shapes"]["brow_flat"] = {"inner": 0.0, "outer": 0.0}
    parameters["brows"]["forward"] = 0.0
    result = executor.run_recipe(_recipe(tmp_path, parameters), tmp_path / "runs")
    assert result["status"] == "needs_review" and "brow_flat_R" in " ".join(result["steps"]["shapes"]["unresolved"])


def _with(change):
    parameters = json.loads(json.dumps(SETTINGS))
    change(parameters)
    return parameters


@pytest.mark.parametrize("parameters, message", [
    (_with(lambda p: p.pop("front_y")), "front_y"),
    (_with(lambda p: (p.pop("eyes"), p.pop("brows"), p.pop("mouth"))), "eyes"),
    (_with(lambda p: p["eyes"].pop("find")), "eyes に find"),
    (_with(lambda p: p["eyes"]["find"].update(x=[0.07, 0.01])), "eyes.find.x"),
    (_with(lambda p: p["eyes"].update(smooth_px=14)), "奇数"),
    (_with(lambda p: p["eyes"].update(gap_px=0)), "gap_px"),                         # 0 だと隣り合う列まで切ってしまう
    (_with(lambda p: p["eyes"].update(close_at=1.5)), "close_at"),
    (_with(lambda p: p["eyes"]["smile"].update(clip=[0.6, 0.5])), "和"),
    (_with(lambda p: p["eyes"].pop("brow")), "eyes.brow"),
    (_with(lambda p: p["brows"]["shapes"].update(blink={"inner": 0.001, "outer": 0.001})), "blink"),
    (_with(lambda p: p["mouth"].pop("smile")), "mouth に smile"),
    (_with(lambda p: p["mouth"]["zone"].update(half_width=0.04)), r"mouth\.window\.half_width（0\.03）以下"),   # 窓の外は比べられない
    (_with(lambda p: p.update(resolution=0.000001)), "画素"),
    (_with(lambda p: p["eyes"].update(wink=1)), "知らない設定"),
    (_with(lambda p: p.update(nose=1)), "知らない設定"),
])
def test_face_shapes_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import face_ops
    with pytest.raises(ToolError, match=message):
        face_ops.face_shapes_settings(parameters)


def test_face_shapes_rejects_a_mesh_that_is_not_a_head_mesh(tmp_path):
    from model3d.ops import get

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    np.savez(tmp_path / "bad.npz", co=np.zeros((3, 3)), edges=np.array([[0, 5]]), tris=np.array([[0, 1, 2]]))
    run = get("model.face_shapes").function
    outputs = {"shapes": tmp_path / "s.npz", "report": tmp_path / "r.json"}
    with pytest.raises(ToolError, match="無い頂点"):
        run({"mesh": tmp_path / "bad.npz"}, SETTINGS, outputs, Context())
    np.savez(tmp_path / "partial.npz", co=np.zeros((3, 3)))
    with pytest.raises(ToolError, match="edges"):
        run({"mesh": tmp_path / "partial.npz"}, SETTINGS, outputs, Context())
    with pytest.raises(ToolError, match=".npz"):
        run({"mesh": tmp_path / "partial.npz"}, SETTINGS, {"shapes": tmp_path / "s.json", "report": tmp_path / "r.json"}, Context())
