"""光る帯と玉の四角形（blender_adapter/fx_strips.py）と、bone_ribbon の任意の折れ線の帯: 幅の向きが接線と視線に垂直なこと、
床に寝た帯、頂点の並びと UV、強さ、ねじれを返すこと、道のりで切ること。値は作り物（Blender なし）。"""
import numpy as np
import pytest

from model3d.blender_adapter import fx_strips
from model3d.bone_ribbon import build_ribbon_topology, polyline_ribbon_vertices
from model3d.io import ToolError

CAMERA = np.array([0.3, -4.0, 1.5])


def _wave(n=9):
    s = np.linspace(0.0, 1.0, n)
    return np.stack([s * 2.0 - 1.0, 0.4 * np.sin(s * 5.0), 1.0 + 0.3 * np.cos(s * 3.0)], axis=1)


def test_strip_width_is_perpendicular_to_tangent_and_view_and_has_the_given_width():
    points = _wave()
    widths = np.linspace(0.05, 0.01, len(points))
    quads = fx_strips.Quads(CAMERA)
    assert quads.strip(points, widths, np.linspace(1.0, 0.5, len(points))) == len(points) - 1
    co, uv, value = quads.arrays()
    assert co.shape == (len(points) - 1, 4, 3) and uv.shape == (len(points) - 1, 4, 2) and value.shape == (len(points) - 1, 4)
    for i in range(len(points) - 1):
        side = (co[i, 1] - co[i, 0]) / 2                       # 点 i の幅の半分の向き
        tangent = points[min(i + 1, len(points) - 1)] - points[max(i - 1, 0)]
        view = CAMERA - points[i]
        assert abs(side @ tangent) < 1e-12 and abs(side @ view) < 1e-12
        assert np.linalg.norm(side) * 2 == pytest.approx(widths[i])
        assert np.allclose((co[i, 0] + co[i, 1]) / 2, points[i]) and np.allclose((co[i, 2] + co[i, 3]) / 2, points[i + 1])
        if i + 1 < len(points) - 1:                             # 隣の四角形と、縁の頂点が同じ所
            assert np.allclose(co[i, 2], co[i + 1, 1]) and np.allclose(co[i, 3], co[i + 1, 0])
    assert np.array_equal(uv[0], [[0, 0], [1, 0], [1, 1], [0, 1]])           # u = 幅の向き、v = 帯の向き
    assert value[0].tolist() == [1.0, 1.0, value[1, 0], value[1, 0]]


def test_flat_strip_lies_in_the_horizontal_plane():
    points = np.array([[0.0, 0.0, 0.01], [0.5, 0.2, 0.01], [1.0, -0.1, 0.01]])
    quads = fx_strips.Quads(CAMERA)
    quads.strip(points, [0.1, 0.1, 0.1], [1, 1, 1], flat=True)
    co, _, _ = quads.arrays()
    assert np.allclose(co[..., 2], 0.01)


def test_degenerate_view_falls_back_and_short_input_is_skipped():
    quads = fx_strips.Quads([0.0, -5.0, 0.0])
    assert quads.strip([[0, 0, 0]], [0.1], [1]) == 0
    quads.strip([[0, -1, 0], [0, 1, 0]], [0.2, 0.2], [1, 1])           # 線がカメラの方を向く: 接線 × x
    co, _, _ = quads.arrays()
    side = (co[0, 1] - co[0, 0]) / 2
    assert np.allclose(np.abs(side), [0, 0, 0.1])


def test_dot_faces_the_camera_with_the_given_size():
    quads = fx_strips.Quads(CAMERA)
    centre = np.array([0.2, 0.5, 1.4])
    quads.dot(centre, 0.3, 2.0)
    co, _, value = quads.arrays()
    normal = np.cross(co[0, 1] - co[0, 0], co[0, 3] - co[0, 0])
    assert abs(abs(fx_strips.unit(normal) @ fx_strips.unit(CAMERA - centre)) - 1) < 1e-12
    assert np.linalg.norm(co[0, 1] - co[0, 0]) == pytest.approx(0.3) and np.allclose(co[0].mean(axis=0), centre)
    assert value[0].tolist() == [2.0] * 4


def test_untwist_keeps_the_side_on_one_side_across_a_hairpin():
    points = np.array([[0, 0, 1.0], [1, 0, 1.0], [1.0, 0, 1.2], [0, 0, 1.2]])      # カメラの正面で折り返す
    plain = fx_strips.sides(points, [0.1] * 4, CAMERA)
    kept = fx_strips.sides(points, [0.1] * 4, CAMERA, untwist=True)
    assert (np.einsum("ij,ij->i", plain[1:], plain[:-1]) < 0).any()
    assert (np.einsum("ij,ij->i", kept[1:], kept[:-1]) >= 0).all()


def test_cut_follows_the_path_length():
    points = [[0, 0, 0], [1, 0, 0], [1, 2, 0]]
    assert fx_strips.path_length(points) == pytest.approx(3.0)
    half = fx_strips.cut(points, 0.5)
    assert fx_strips.path_length(half) == pytest.approx(1.5) and np.allclose(half[-1], [1, 0.5, 0])
    assert len(fx_strips.cut(points, 1.0)) == 3 and len(fx_strips.cut(points, 0.0)) == 2


def test_bone_ribbon_takes_any_polyline():
    points = _wave(6)
    verts = polyline_ribbon_vertices(points, 0.04, CAMERA)
    faces, uvs = build_ribbon_topology(len(points) - 1)
    assert verts.shape == (len(uvs), 3) and max(max(f) for f in faces) == len(verts) - 1
    assert np.allclose((verts[0::2] + verts[1::2]) / 2, points)
    assert np.allclose(np.linalg.norm(verts[1::2] - verts[0::2], axis=1), 0.04)
    with pytest.raises(ToolError, match="points の形"):
        polyline_ribbon_vertices([[0, 0, 0]], 0.1, CAMERA)
    with pytest.raises(ToolError, match="widths"):
        polyline_ribbon_vertices(points, [0.1, 0.2], CAMERA)
    with pytest.raises(ToolError, match="有限"):
        polyline_ribbon_vertices(points, -0.1, CAMERA)
