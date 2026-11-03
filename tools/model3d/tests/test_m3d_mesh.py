import json
import math
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import box
from model3d.io import ToolError
from model3d.mesh import Mesh, canonical_signature, check_profile, extrude_profile, read_obj, remap_rig_weights, transform, weld, write_obj

FIXTURE = Path(__file__).parent / "data" / "jscad" / "cases.json"


def _polygon_metrics(polygons):
    tri = np.asarray(polygons, dtype=np.float64)
    normals = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    area = float(np.linalg.norm(normals, axis=1).sum() / 2)
    volume = float(np.einsum("ij,ij->i", tri[:, 0], np.cross(tri[:, 1], tri[:, 2])).sum() / 6)
    return tri, area, volume


def _vertex_set_distance(a, b):
    """a の各点から b の最も近い点までの距離の最大（両方向は呼び出し側で）。"""
    diff = a[:, None, :] - b[None, :, :]
    return float(np.sqrt((diff ** 2).sum(axis=2)).min(axis=1).max())


@pytest.mark.parametrize("case", json.loads(FIXTURE.read_text(encoding="utf-8"))["cases"], ids=lambda c: c["id"])
def test_extrude_matches_the_jscad_original_parts(case):
    """無変更の JSCAD が作った合成部品と、三角形の数・頂点集合・bounds・面積・体積（許容 1e-12）で一致する。"""
    options = case["options"]
    mesh = extrude_profile(case["points"], options["height"], twist_rad=options.get("twistAngle", 0.0), steps=options.get("twistSteps", 1))
    tris, _ = mesh.triangles()
    assert len(tris) == case["expected"]["triangle_count"]
    original, area, volume = _polygon_metrics(case["polygons"])
    assert abs(mesh.area() - case["expected"]["surface_area"]) <= 1e-12
    assert abs(mesh.area() - area) <= 1e-12
    assert mesh.signed_volume() > 0 and abs(mesh.signed_volume() - case["expected"]["signed_volume"]) <= 1e-12
    lo, hi = mesh.bounds()
    assert np.allclose(lo, case["expected"]["bounds"][0], atol=1e-12) and np.allclose(hi, case["expected"]["bounds"][1], atol=1e-12)
    mine = mesh.vertices
    theirs = np.unique(original.reshape(-1, 3), axis=0)
    assert max(_vertex_set_distance(mine, theirs), _vertex_set_distance(theirs, mine)) <= 1e-12
    assert mesh.edge_report()["closed"]


def test_extrude_rejects_invalid_steps_like_the_original():
    rejection = json.loads(FIXTURE.read_text(encoding="utf-8"))["rejections"][0]
    with pytest.raises(ToolError, match="steps"):
        extrude_profile(rejection["points"], rejection["options"]["height"], twist_rad=rejection["options"]["twistAngle"],
                        steps=rejection["options"]["twistSteps"])


def test_extrude_zero_twist_uses_a_single_step_and_degrees_match_radians():
    straight = extrude_profile([[0, 0], [1, 0], [0, 1]], 2.0, twist_deg=0.0, steps=9)
    assert straight.vertex_count == 6 and straight.face_count == 2 + 6
    a = extrude_profile([[0, 0], [1, 0], [0, 1]], 2.0, twist_deg=30.0, steps=3)
    b = extrude_profile([[0, 0], [1, 0], [0, 1]], 2.0, twist_rad=math.radians(30.0), steps=3)
    assert np.allclose(a.vertices, b.vertices)
    with pytest.raises(ToolError, match="同時"):
        extrude_profile([[0, 0], [1, 0], [0, 1]], 2.0, twist_deg=1.0, twist_rad=0.1)


def test_extrude_offset_moves_the_top_and_keeps_the_solid_closed():
    mesh = extrude_profile([[0, 0], [1, 0], [1, 1], [0, 1]], 1.0, offset=(0.5, -0.25))
    top = mesh.vertices[mesh.vertices[:, 2] > 0.5]
    assert np.allclose(top[:, :2].mean(axis=0), [1.0, 0.25])
    assert mesh.edge_report()["closed"] and mesh.signed_volume() > 0
    assert set(mesh.parts) == {"cap_bottom", "cap_top", "wall"}


@pytest.mark.parametrize("profile, message", [
    ([[0, 0], [0, 1], [1, 0]], "反時計回り"),             # 時計回り
    ([[0, 0], [1, 0], [2, 0], [0, 1]], "厳密に凸"),      # 一直線の 3 点
    ([[0, 0], [1, 0]], "3 点以上"),
])
def test_profile_validation(profile, message):
    with pytest.raises(ToolError, match=message):
        check_profile(profile)


def test_profile_rejects_self_intersecting_star():
    """連続する曲がりが全部正でも、五芒星は自己交差している（全ての辺の左側に他の頂点があるかで拒否する）。"""
    star = [[math.cos(4 * math.pi * i / 5), math.sin(4 * math.pi * i / 5)] for i in range(5)]
    with pytest.raises(ToolError, match="自己交差"):
        check_profile(star)
    with pytest.raises(ToolError):
        extrude_profile(star, 1.0)
    pentagon = [[math.cos(2 * math.pi * i / 5), math.sin(2 * math.pi * i / 5)] for i in range(5)]
    assert len(check_profile(pentagon)) == 5


@pytest.mark.parametrize("height", [0, float("nan"), float("inf"), True, "1"])
def test_extrude_rejects_bad_height(height):
    with pytest.raises(ToolError):
        extrude_profile([[0, 0], [1, 0], [0, 1]], height)


def test_weld_repairs_a_cube_seam_and_keeps_six_quads():
    """立方体の近い継ぎ目（9 頂点）を 8 頂点にし、6 面と閉じた辺を保つ。"""
    vertices = [[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 0, 1], [1, 0, 1], [1, 1, 1], [0, 1, 1], [1.0000001, 1, 1]]
    faces = [[0, 3, 2, 1], [4, 5, 6, 7], [0, 1, 5, 4], [1, 2, 8, 5], [2, 3, 7, 8], [3, 0, 4, 7]]
    welded, report = weld(Mesh(vertices, faces), 1e-3)
    assert (report["vertices_before"], report["vertices_after"], report["merged_groups"]) == (9, 8, 1)
    assert welded.face_count == 6 and all(len(f) == 4 for f in welded.faces)
    assert welded.edge_report()["closed"]
    assert np.allclose(welded.vertices[6], [1.00000005, 1, 1])   # mean


def test_weld_is_transitive_and_respects_selection_center_and_uv():
    vertices = [[0, 0, 0], [0.5, 0, 0], [1.0, 0, 0], [0, 2, 0], [2, 2, 0]]
    mesh = Mesh(vertices, [[0, 1, 3], [1, 2, 4, 3]], uv=[[0, 0], [1, 0], [2, 0], [0, 1], [2, 1]], parts={"left": [0], "right": [1]})
    welded, report = weld(mesh, 0.6)                      # 0-1 と 1-2 が近い → 3 つが 1 つに（推移的）
    assert report["merged_groups"] == 1 and welded.vertex_count == 3
    assert report["faces_removed_degenerate"] == 1 and welded.face_count == 1     # 三角形 0-1-3 は頂点が 2 つになり消える
    assert welded.parts == {"left": [], "right": [0]}
    assert np.allclose(welded.vertices[0], [0.5, 0, 0]) and np.allclose(welded.uv[0], [1, 0])
    first, _ = weld(mesh, 0.6, center="first")
    assert np.allclose(first.vertices[0], [0, 0, 0]) and np.allclose(first.uv[0], [0, 0])
    partial, report = weld(mesh, 0.6, selection=[0, 1])    # 選択の外の 2 は結合しない
    assert report["merged_groups"] == 1 and partial.vertex_count == 4
    _, none = weld(mesh, 0.4)                              # 距離 0.5 > 0.4
    assert none["merged_groups"] == 0


def test_weld_remaps_vertex_colors(tmp_path):
    """結合した頂点の色は平均（first なら先頭）になり、長さは新しい頂点数に合う。書き出しても色が残る。"""
    vertices = [[0, 0, 0], [1, 0, 0], [0, 1, 0], [1.0000001, 0, 0], [1, 1, 0]]
    colors = [[1, 0, 0], [0, 1, 0], [0, 0, 1], [0, 0, 0], [1, 1, 1]]
    mesh = Mesh(vertices, [[0, 1, 2], [3, 4, 2]], extra={"x_vertex_colors": colors, "x_scale_calibrated": False})
    welded, report = weld(mesh, 1e-3)
    assert report["merged_groups"] == 1 and welded.vertex_count == 4
    assert len(welded.extra["x_vertex_colors"]) == 4 and welded.extra["x_scale_calibrated"] is False
    assert np.allclose(welded.extra["x_vertex_colors"][1], [0, 0.5, 0])       # 頂点 1 と 3 の平均
    first, _ = weld(mesh, 1e-3, center="first")
    assert np.allclose(first.extra["x_vertex_colors"][1], [0, 1, 0])
    write_obj(welded, tmp_path / "welded.obj")
    assert np.allclose(read_obj(tmp_path / "welded.obj").extra["x_vertex_colors"], welded.extra["x_vertex_colors"])
    assert canonical_signature(welded)["colors"]


@pytest.mark.parametrize("center, expected", [("mean", [0.5, 0.5]), ("first", [1.0, 0.0])])
def test_weld_remaps_bound_rig_weights_with_the_geometry_rule(center, expected):
    import copy
    from model3d import checks
    mesh = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [0, 0, 0]], [[0, 1, 2], [3, 1, 2]])
    rig = {"schema_version": "model3d-rig/0.1", "x_note": "preserve me",
           "bones": [{"name": "a", "parent": None}, {"name": "b", "parent": "a"}],
           "weights": {"vertex_count": 4, "topology_sha256": mesh.topology_hash(), "x_note": "weights",
                       "groups": {"a": [[0, 1.0], [1, 1.0], [2, 0.25]], "b": [[2, 0.75], [3, 1.0]]}}}
    before = copy.deepcopy(rig)
    welded, report = weld(mesh, 1e-6, center=center)
    mapping = report["vertex_mapping"]
    assert mapping["old_to_new"] == [0, 1, 2, 0]
    out = remap_rig_weights(rig, mesh, welded, mapping)
    assert rig == before and out["bones"] == before["bones"] and out["x_note"] == "preserve me"
    assert out["weights"]["x_note"] == "weights" and out["weights"]["vertex_count"] == 3
    assert [dict(out["weights"]["groups"][b]).get(0, 0.0) for b in ("a", "b")] == expected
    assert dict(out["weights"]["groups"]["a"])[2] == 0.25
    assert dict(out["weights"]["groups"]["b"])[2] == 0.75
    assert checks.structure(out, mesh=welded)["pass"]


def test_weight_remapping_rejects_unbound_wrong_or_invalid_source_and_mapping():
    import copy
    from model3d import demo
    mesh = demo.sash_mesh()
    welded, report = weld(mesh, 1e-6)
    mapping = report["vertex_mapping"]
    rig = demo.rig_json(mesh.vertex_count, mesh.topology_hash())
    unbound = demo.rig_json(mesh.vertex_count)
    with pytest.raises(ToolError, match="topology_sha256"):
        remap_rig_weights(unbound, mesh, welded, mapping)
    stale = copy.deepcopy(rig)
    stale["weights"]["topology_sha256"] = "0" * 64
    with pytest.raises(ToolError, match="topology_sha256"):
        remap_rig_weights(stale, mesh, welded, mapping)
    invalid = copy.deepcopy(rig)
    invalid["weights"]["groups"]["mixamorig:LeftUpLeg"][0][1] = -1
    with pytest.raises(ToolError, match="負の値"):
        remap_rig_weights(invalid, mesh, welded, mapping)
    for indices in ([0.5] + mapping["old_to_new"][1:], [True] + mapping["old_to_new"][1:], mapping["old_to_new"][:-1]):
        with pytest.raises(ToolError, match="頂点対応"):
            remap_rig_weights(rig, mesh, welded, {**mapping, "old_to_new": indices})
    with pytest.raises(ToolError, match="source / target"):
        remap_rig_weights(rig, mesh, welded, {**mapping, "target_topology_sha256": "0" * 64})


def test_weld_requires_rig_input_and_output_together(tmp_path):
    from model3d import demo
    from model3d.ops import mesh_ops
    mesh = demo.sash_mesh()
    mesh.save(tmp_path / "mesh.json")
    for inputs, outputs in [({"mesh": tmp_path / "mesh.json", "rig": tmp_path / "rig.json"},
                             {"mesh": tmp_path / "out.json"}),
                            ({"mesh": tmp_path / "mesh.json"},
                             {"mesh": tmp_path / "out.json", "rig": tmp_path / "out_rig.json"})]:
        with pytest.raises(ToolError, match="入力 rig と出力 rig"):
            mesh_ops.mesh_weld(inputs, {"distance": 1e-6}, outputs, None)


def test_weld_removes_duplicate_and_contained_faces():
    mesh = Mesh([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [0, 1, 1e-9]], [[0, 1, 2, 3], [0, 1, 2, 4], [0, 1, 2]])
    welded, report = weld(mesh, 1e-6)
    assert welded.face_count == 1 and report["faces_removed_contained"] == 2
    # 結合に関わらない、元から重なっている面（頂点 0-1-2 の 2 枚）は触らない。結合した側の重なりだけを除く
    far = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0], [5, 5, 5], [6, 5, 5], [5, 6, 5], [5, 5, 5.0000001]],
               [[0, 1, 2], [0, 1, 2], [3, 4, 5], [6, 4, 5]])
    welded, report = weld(far, 1e-3)
    assert report["merged_groups"] == 1 and report["faces_removed_contained"] == 1 and welded.face_count == 3
    assert welded.faces[:2] == [[0, 1, 2], [0, 1, 2]]


@pytest.mark.parametrize("distance", [0, -1, float("nan"), True])
def test_weld_rejects_bad_distance(distance):
    with pytest.raises(ToolError, match="distance"):
        weld(box(), distance)


def test_concave_faces_are_triangulated_inside_the_face():
    """扇形では凹んだ面の外に三角形がはみ出す（指摘の 8 角形で面積 11）。耳切りと対角線の選択で正しい面積になる。"""
    from model3d.mesh import triangulate_polygon
    notch = [[0, 0, 0], [3, 0, 0], [3, 3, 0], [2, 3, 0], [2, 1, 0], [1, 1, 0], [1, 3, 0], [0, 3, 0]]
    mesh = Mesh(notch, [list(range(8))])
    assert abs(mesh.area() - 7.0) < 1e-12
    tris, owner = mesh.triangles()
    assert len(tris) == 6 and owner.tolist() == [0] * 6
    assert np.allclose(mesh.triangle_normals(), [[0, 0, 1]] * 6)        # 向きは面の巻き方のまま
    dart = Mesh([[0, 0, 0], [2, 1, 0], [4, 0, 0], [2, 3, 0]], [[0, 1, 2, 3]])   # 頂点 1 が凹んだ四角形
    assert abs(dart.area() - 4.0) < 1e-12 and sorted(map(sorted, dart.triangles()[0].tolist())) == [[0, 1, 3], [1, 2, 3]]
    dart_other = Mesh([[2, 1, 0], [4, 0, 0], [2, 3, 0], [0, 0, 0]], [[0, 1, 2, 3]])   # 凹んだ頂点が 0 なら扇形のまま
    assert abs(dart_other.area() - 4.0) < 1e-12
    # 凹みの上の点は、はみ出した偽の面に当たらない（実際の境界までの距離になる）
    from model3d import collide
    _, distance, _ = collide.closest_points([[1.5, 2.0, 0.5]], mesh)
    assert abs(distance[0] - math.sqrt(0.5 ** 2 + 0.5 ** 2)) < 1e-12
    convex = Mesh([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0], [-0.5, 0.5, 0]], [[0, 1, 2, 3, 4]])
    assert triangulate_polygon(convex.vertices) == [(0, 1, 2), (0, 2, 3), (0, 3, 4)]   # 凸な面は扇形のまま
    tilted = Mesh((np.asarray(notch) @ np.array([[0.6, 0, 0.8], [0, 1, 0], [-0.8, 0, 0.6]]).T + [1, 2, 3]).tolist(), [list(range(8))])
    assert abs(tilted.area() - 7.0) < 1e-9                                            # 傾いた面でも投影して分ける
    assert len(triangulate_polygon([[0, 0, 0], [1, 0, 0], [2, 0, 0], [3, 0, 0]])) == 2   # 退化した面は扇形


def test_mesh_validation_and_hashes():
    with pytest.raises(ToolError, match="無い頂点"):
        Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 5]])
    with pytest.raises(ToolError, match="3 つ未満"):
        Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1]])
    with pytest.raises(ToolError, match="非有限"):
        Mesh([[0, 0, float("nan")], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    a = Mesh([[0, 0, 0], [1, 0, 0], [0, 1, 0]], [[0, 1, 2]])
    b = Mesh([[0, 0, 0], [2, 0, 0], [0, 2, 0]], [[0, 1, 2]])
    assert a.topology_hash() == b.topology_hash() and a.geometry_hash() != b.geometry_hash()


def test_json_and_obj_round_trip(tmp_path):
    mesh = box(2.0, (0.5, 0.0, 1.0))
    mesh.uv = np.linspace(0, 1, mesh.vertex_count * 2).reshape(-1, 2)
    mesh.selection = [0, 3]
    mesh.extra = {"x_note": "kept"}
    path = mesh.save(tmp_path / "box.json")
    again = Mesh.load(path)
    assert np.allclose(again.vertices, mesh.vertices) and again.faces == mesh.faces and again.parts == mesh.parts
    assert np.allclose(again.uv, mesh.uv) and again.selection == [0, 3] and again.extra == {"x_note": "kept"}
    obj = tmp_path / "box.obj"
    write_obj(mesh, obj)
    text = obj.read_text(encoding="utf-8")
    assert text.count("\nv ") == mesh.vertex_count and "g wall" in text and "f 1/1" in text
    back = read_obj(obj)
    assert np.allclose(back.vertices, mesh.vertices) and back.faces == mesh.faces and np.allclose(back.uv, mesh.uv)
    assert set(back.parts) == {"cap_bottom", "cap_top", "wall"} and sorted(back.parts["wall"]) == sorted(mesh.parts["wall"])
    assert back.edge_report()["closed"] and abs(back.signed_volume() - 8.0) < 1e-9


def test_obj_round_trip_keeps_units_axes_and_exact_coordinates(tmp_path):
    mesh = extrude_profile([[0.0003509780730004703, -0.04230702128362548], [0.05417201544554691, -0.02895345867957695], [0.01, 0.07]],
                           0.123456789012345, twist_deg=17.5, steps=3, units="mm")
    mesh.axes = {"up": "+Y", "forward": "+Z"}
    write_obj(mesh, tmp_path / "part.obj")
    back = read_obj(tmp_path / "part.obj")
    assert back.units == "mm" and back.axes == {"up": "+Y", "forward": "+Z"}
    assert back.geometry_hash() == mesh.geometry_hash()      # 数値が往復で変わらない
    plain = tmp_path / "plain.obj"
    plain.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n", encoding="utf-8")
    assert read_obj(plain).units == "m" and read_obj(plain).axes == {"up": "+Z", "forward": "-Y"}


def test_obj_negative_indices_and_inconsistent_uv(tmp_path):
    obj = tmp_path / "t.obj"
    obj.write_text("v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nf -3/1 -2/2 -1/1\nf 1/2 2/1 3/1\n", encoding="utf-8")
    mesh = read_obj(obj)
    assert mesh.faces == [[0, 1, 2], [0, 1, 2]] and mesh.uv is None   # 頂点 0 の vt が面ごとに違う → uv は持たない
    bad = tmp_path / "bad.obj"
    bad.write_text("v 0 0 x\n", encoding="utf-8")
    with pytest.raises(ToolError, match="1 行目"):
        read_obj(bad)


def test_transform_order_scale_rotate_translate():
    mesh = Mesh([[1, 0, 0], [0, 1, 0], [0, 0, 1]], [[0, 1, 2]])
    moved = transform(mesh, translate=[1, 0, 0], scale=2, rotate_z_deg=90)
    assert np.allclose(moved.vertices[0], [1, 2, 0])      # (2,0,0) → 回転 (0,2,0) → 平行移動 (1,2,0)
    with pytest.raises(ToolError, match="scale"):
        transform(mesh, scale=0)
    with pytest.raises(ToolError, match="rotate_z_deg"):
        transform(mesh, rotate_z_deg=float("inf"))
    mm = transform(Mesh([[1000, 0, 0], [0, 1000, 0], [0, 0, 1000]], [[0, 1, 2]], units="mm"), translate=[1, 0, 0], to_units="m")
    assert mm.units == "m" and np.allclose(mm.vertices[0], [2, 0, 0])    # 換算（×0.001）の後に m で平行移動
    with pytest.raises(ToolError, match="to_units"):
        transform(mesh, to_units="inch")
    from model3d.mesh import axis_basis, convert_axes
    for source, target in (({"up": "+Y", "forward": "-Z"}, {"up": "+Z", "forward": "-Y"}), ({"up": "+Z", "forward": "+X"}, {"up": "-Y", "forward": "+Z"})):
        rotation = axis_basis(target) @ axis_basis(source).T
        assert abs(np.linalg.det(rotation) - 1.0) < 1e-12                     # 鏡映にならない回転
        up_s, fwd_s = np.asarray(axis_basis(source)[:, 0]), np.asarray(axis_basis(source)[:, 1])
        assert np.allclose(convert_axes([up_s * 5], source, target)[0], axis_basis(target)[:, 0] * 5)   # up は up へ
        assert np.allclose(convert_axes([fwd_s * 3], source, target)[0], axis_basis(target)[:, 1] * 3)  # forward は forward へ
    assert np.allclose(convert_axes([[1, 2, 3]], {"up": "+Z", "forward": "-Y"}, {"up": "+Z", "forward": "-Y"}), [[1, 2, 3]])
    with pytest.raises(ToolError, match="直交"):
        axis_basis({"up": "+Z", "forward": "-Z"})
    with pytest.raises(ToolError, match="axes"):
        axis_basis({"up": "+W", "forward": "-Y"})
