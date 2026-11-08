"""穴ふさぎ（mesh.fill_holes）: 穴の縁の輪、面の向きのそろえ方、多角形と扇のふさぎ方、外向きへの裏返し、残す穴、
止める形（多様体でない辺・蝶ネクタイの頂点・裏表のない面・つぶれた辺）、uv・法線・parts、閉じた当たりとしての押し出し。"""
import json
import math
from pathlib import Path

import numpy as np
import pytest

from model3d import mesh as m3
from model3d.io import ToolError


def _tube(segments=8, radius=1.0, height=2.0, inward=False, uv=False):
    """上と下が開いた筒（側面だけ、四角形。外向き）。inward なら全部の面を裏返す。"""
    angles = [2 * math.pi * i / segments for i in range(segments)]
    vertices = [[radius * math.cos(a), radius * math.sin(a), z] for z in (0.0, height) for a in angles]
    faces = []
    for i in range(segments):
        j = (i + 1) % segments
        faces.append([i, j, segments + j, segments + i])
    if inward:
        faces = [face[::-1] for face in faces]
    coords = [[i / segments, z / height] for z in (0.0, height) for i in range(segments)] if uv else None
    return m3.Mesh(vertices, faces, coords, name="tube", parts={"side": list(range(segments))})


def _prism_volume(segments=8, radius=1.0, height=2.0):
    return segments / 2 * radius ** 2 * math.sin(2 * math.pi / segments) * height


def test_the_open_tube_has_two_boundary_loops_that_follow_the_face_direction():
    loops, counts = m3.boundary_loops(_tube())
    assert counts == {"edges": 24, "boundary_edges": 16}
    assert loops == [[0, 1, 2, 3, 4, 5, 6, 7], [8, 15, 14, 13, 12, 11, 10, 9]]     # 小さい頂点の順、面の向きにたどる


def test_polygon_caps_close_the_tube_without_new_vertices_and_keep_the_faces():
    tube = _tube()
    out, report = m3.fill_holes(tube)
    assert out.faces[:8] == tube.faces and out.vertex_count == 16                       # 今の面と頂点の番号は変えない
    assert report["filled_loops"] == 2 and report["filled_edges"] == 16 and report["faces_added"] == 2
    assert report["vertices_added"] == 0 and report["closed"] and out.edge_report()["consistent_winding"]
    assert report["volume"] == pytest.approx(_prism_volume()) and not report["flipped"] and report["faces_reoriented"] == 0
    assert report["volume_l"] == pytest.approx(_prism_volume() * 1000)
    assert out.parts == tube.parts                                                       # part を渡さなければ parts は同じ


def test_fan_caps_add_a_centre_vertex_per_hole():
    out, report = m3.fill_holes(_tube(uv=True), cap="fan", part="caps")
    assert report["vertices_added"] == 2 and report["faces_added"] == 16 and report["closed"]
    assert out.vertex_count == 18 and np.allclose(out.vertices[16], [0, 0, 0]) and np.allclose(out.vertices[17], [0, 0, 2])
    assert report["volume"] == pytest.approx(_prism_volume())                            # 平らな穴なら、多角形と同じ体積
    assert len(out.uv) == 18 and np.allclose(out.uv[16], [0.4375, 0.0])                   # 足した頂点には輪の uv の平均
    assert out.parts["caps"] == list(range(8, 24)) and out.parts["side"] == list(range(8))


def test_an_inward_tube_is_turned_outward_and_a_stray_face_is_reoriented():
    out, report = m3.fill_holes(_tube(inward=True))
    assert report["flipped"] and report["flipped_components"] == 1 and report["volume"] == pytest.approx(_prism_volume())
    stray = _tube()
    stray.faces[3] = stray.faces[3][::-1]                                                 # 1 枚だけ向きが逆
    out, report = m3.fill_holes(stray)
    assert report["faces_reoriented"] == 1 and report["closed"] and report["volume"] == pytest.approx(_prism_volume())
    assert out.edge_report()["consistent_winding"]
    _, report = m3.fill_holes(_tube(inward=True), orient=False)
    assert report["volume"] == pytest.approx(-_prism_volume()) and not report["flipped"]   # orient なしは内向きのまま


TETRA_OUT = [[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]]                                 # 外向き（頂点 0 を角にした四面体）


def test_shells_that_share_only_a_vertex_are_turned_outward_separately():
    big = [[0, 0, 0], [2, 0, 0], [0, 2, 0], [0, 0, 2]]                                    # 体積 8/6、外向き
    small = [[1, 0, 2], [0, 1, 2], [0, 0, 3]]                                             # 頂点 3 (0, 0, 2) だけを共有する体積 1/6 の殻
    local = {0: 3, 1: 4, 2: 5, 3: 6}
    inward = [[local[i] for i in face[::-1]] for face in TETRA_OUT]                      # 小さい方は内向き
    mesh = m3.Mesh(big + small, TETRA_OUT + inward)
    assert len(m3.face_components(mesh)) == 1 and len(m3.edge_components(mesh.faces)) == 2   # 頂点ではつながり、辺ではつながらない
    out, report = m3.fill_holes(mesh)
    assert report["components"] == 2 and report["flipped_components"] == 1 and report["closed"]
    assert report["volume"] == pytest.approx(8 / 6 + 1 / 6)                               # どちらも外向き（まとめて測ると 7/6 で裏返らない）
    assert out.faces[:4] == TETRA_OUT and out.faces[4:] == [[local[i] for i in face] for face in TETRA_OUT]


def test_fan_caps_give_the_added_vertices_the_mean_colour_of_their_loop(tmp_path):
    tube = _tube()
    palette = [[i / 16, 0.5, 1.0 - i / 16] for i in range(16)]
    tube.extra["x_vertex_colors"] = palette
    out, _ = m3.fill_holes(tube, cap="fan")
    colors = out.extra["x_vertex_colors"]
    assert len(colors) == out.vertex_count == 18 and colors[:16] == palette
    assert np.allclose(colors[16], np.mean(palette[:8], axis=0)) and np.allclose(colors[17], np.mean(palette[8:], axis=0))
    assert len(tube.extra["x_vertex_colors"]) == 16                                       # 入力の色の並びは変えない
    out.save(tmp_path / "tube.obj")                                                       # 数が合うので、OBJ にも色が残る
    assert len(m3.Mesh.load(tmp_path / "tube.obj").extra["x_vertex_colors"]) == 18
    polygon, _ = m3.fill_holes(tube)
    assert polygon.extra["x_vertex_colors"] == palette                                    # 多角形は頂点を足さないので、色もそのまま


def test_holes_larger_than_max_sides_are_left_open_and_reported():
    out, report = m3.fill_holes(_tube(), max_sides=6)
    assert report["filled_loops"] == 0 and report["left_open_edges"] == 16 and not report["closed"] and report["orient_skipped"]
    assert [loop["filled"] for loop in report["loops"]] == [False, False] and out.faces == _tube().faces


def test_shapes_that_cannot_be_filled_unambiguously_are_refused():
    sheet = m3.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 1], [1, 1, 1]], [[0, 1, 2], [0, 1, 3], [1, 0, 4]])
    with pytest.raises(ToolError, match="3 つ以上の面"):
        m3.fill_holes(sheet)
    bowtie = m3.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [-1, 0, 0], [0, -1, 0]], [[0, 1, 2], [0, 3, 4]])
    with pytest.raises(ToolError, match="2 つ以上の穴の縁"):
        m3.fill_holes(bowtie)
    strip = []                                                                           # メビウスの帯（裏表がない）
    n = 12
    for i in range(n):
        t = 2 * math.pi * i / n
        for s in (-0.3, 0.3):
            strip.append([(1 + s * math.cos(t / 2)) * math.cos(t), (1 + s * math.cos(t / 2)) * math.sin(t), s * math.sin(t / 2)])
    faces = [[2 * i, 2 * i + 1, 2 * (i + 1) + 1, 2 * (i + 1)] for i in range(n - 1)] + [[2 * (n - 1), 2 * (n - 1) + 1, 0, 1]]
    with pytest.raises(ToolError, match="裏表"):
        m3.fill_holes(m3.Mesh(strip, faces))
    with pytest.raises(ToolError, match="つぶれた辺"):
        m3.fill_holes(m3.Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 1, 2]]))
    with pytest.raises(ToolError, match="cap"):
        m3.fill_holes(_tube(), cap="dome")
    with pytest.raises(ToolError, match="max_sides"):
        m3.fill_holes(_tube(), max_sides=-1)


def test_vertex_normals_are_dropped_because_they_do_not_fit_the_filled_shape():
    tube = _tube()
    tube.extra["x_normals"] = [[1.0, 0.0, 0.0]] * 16
    out, report = m3.fill_holes(tube)
    assert report["normals_dropped"] and "x_normals" not in out.extra and "x_normals" in tube.extra   # 入力は変えない


def _recipe(tmp_path, steps, inputs):
    from model3d.io import atomic_json
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "fill", "inputs": inputs, "steps": steps})
    return tmp_path / "recipe.json"


def test_the_filled_tube_becomes_a_closed_collider_for_push_out(tmp_path):
    from model3d import executor
    _tube().save(tmp_path / "tube.json")
    m3.Mesh([[0.2, 0.1, 1.0], [5.0, 0.0, 1.0], [0.0, 5.0, 1.0]], [[0, 1, 2]], name="cloth").save(tmp_path / "cloth.json")
    push = {"id": "push", "operation": "collision.push_out", "inputs": {"mesh": "$inputs.cloth", "collider": "$steps.fill.mesh"},
            "parameters": {"clearance": 0.01}, "outputs": {"mesh": "cloth.json", "report": "push.json"}}
    fill = {"id": "fill", "operation": "mesh.fill_holes", "inputs": {"mesh": "$inputs.tube"}, "parameters": {"part": "caps"},
            "outputs": {"mesh": "tube.json", "report": "fill.json"}}
    result = executor.run_recipe(_recipe(tmp_path, [fill, push], {"tube": {"path": "tube.json"}, "cloth": {"path": "cloth.json"}}),
                                 tmp_path / "runs")
    assert result["status"] == "succeeded"
    observation = result["steps"]["fill"]["observations"][0]
    assert observation["kind"] == "fill_holes" and observation["closed"] and observation["loop_sizes"] == [8, 8]
    report = json.loads(Path(result["steps"]["fill"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-fill-holes/0.1" and report["name"] == "tube"
    moved = m3.Mesh.load(result["steps"]["push"]["artifacts"]["mesh"])
    assert np.hypot(*moved.vertices[0][:2]) > 0.9                                      # 筒の中の点が外へ出た
    open_push = dict(push, inputs={"mesh": "$inputs.cloth", "collider": "$inputs.tube"})
    refused = executor.run_recipe(_recipe(tmp_path, [open_push], {"tube": {"path": "tube.json"}, "cloth": {"path": "cloth.json"}}),
                                  tmp_path / "runs2")
    assert refused["status"] == "failed" and "閉じていません" in refused["steps"]["push"]["error"]   # ふさぐ前の筒は当たりにならない


def test_leaving_holes_open_marks_the_step_for_review(tmp_path):
    from model3d import executor
    _tube().save(tmp_path / "tube.json")
    fill = {"id": "fill", "operation": "mesh.fill_holes", "inputs": {"mesh": "$inputs.tube"}, "parameters": {"max_sides": 4},
            "outputs": {"mesh": "tube.json"}}
    result = executor.run_recipe(_recipe(tmp_path, [fill], {"tube": {"path": "tube.json"}}), tmp_path / "runs")
    step = result["steps"]["fill"]
    assert step["status"] == "needs_review" and "閉じていない" in step["unresolved"][0]


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"orient": "yes"}, "orient"),
    ({"part": ""}, "part"),
    ({"cap": "dome"}, "cap"),
    ({"max_sides": 2.5}, "max_sides"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    _tube().save(tmp_path / "tube.json")
    with pytest.raises(ToolError, match=message):
        get("mesh.fill_holes").function({"mesh": tmp_path / "tube.json"}, parameters, {"mesh": tmp_path / "o.json"}, Context(tmp_path))
