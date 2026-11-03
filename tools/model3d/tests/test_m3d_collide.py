import numpy as np
import pytest

from m3d_helpers import box
from model3d import collide
from model3d.io import ToolError
from model3d.mesh import Mesh, extrude_profile


def test_inside_by_ray_parity_on_a_cube():
    cube = box(2.0)
    points = [[0, 0, 0], [0.9, 0.9, 0.9], [1.1, 0, 0], [0, 0, -1.5], [5, 5, 5], [0.999, 0.0, 0.0]]
    assert collide.inside(points, cube).tolist() == [True, True, False, False, False, True]


def test_inside_does_not_depend_on_face_orientation():
    cube = box(2.0)
    flipped = Mesh(cube.vertices, [list(reversed(f)) for f in cube.faces])
    assert flipped.signed_volume() < 0
    assert collide.inside([[0, 0, 0], [3, 0, 0]], flipped).tolist() == [True, False]


def test_inside_on_an_open_tube_is_not_trusted_by_checks():
    tube = extrude_profile([[-1, -1], [1, -1], [1, 1], [-1, 1]], 2.0)
    tube.faces = [f for k, f in enumerate(tube.faces) if k not in tube.parts["cap_top"]]
    tube.parts = {}
    assert not tube.edge_report()["closed"]
    from model3d import checks
    with pytest.raises(ToolError, match="閉じていません"):
        checks.penetration(box(0.1), tube)


def test_closest_points_on_faces_edges_and_corners():
    cube = box(2.0)
    points = np.array([[0.0, 0.0, 3.0], [3.0, 3.0, 0.0], [3.0, 3.0, 3.0], [0.2, 0.1, 0.0]])
    closest, distance, _ = collide.closest_points(points, cube)
    assert np.allclose(closest[0], [0, 0, 1]) and abs(distance[0] - 2.0) < 1e-12
    assert np.allclose(closest[1], [1, 1, 0]) and abs(distance[1] - np.sqrt(8)) < 1e-12
    assert np.allclose(closest[2], [1, 1, 1]) and abs(distance[2] - np.sqrt(12)) < 1e-12
    assert abs(distance[3] - 0.8) < 1e-12          # 中の点: 一番近い面 x=1 まで 0.8


def test_push_out_moves_inside_points_to_the_clearance_and_pads_near_points():
    cube = box(2.0)
    points = np.array([[0.9, 0.0, 0.0], [0.0, -0.95, 0.0], [1.002, 0.0, 0.0], [0.0, 0.0, 2.0], [0.0, 0.0, 0.0]])
    moved, report = collide.push_out(points, cube, 0.01)
    assert report["inside"] == 3 and report["padded"] == 1 and report["moved"] == 4 and report["residual_inside"] == 0
    assert np.allclose(moved[0], [1.01, 0, 0]) and np.allclose(moved[1], [0, -1.01, 0]) and np.allclose(moved[2], [1.01, 0, 0])
    assert np.allclose(moved[3], [0, 0, 2.0])
    assert abs(report["max_depth"] - 1.0) < 1e-12       # 中心の点は面まで 1.0
    assert collide.inside(moved, cube).sum() == 0
    same, report = collide.push_out(points, cube, 0.01, pad_outside=False)
    assert report["padded"] == 0 and np.allclose(same[2], points[2])


def test_push_out_with_zero_clearance_lands_on_the_surface():
    moved, report = collide.push_out([[0.5, 0.2, 0.1]], box(2.0), 0.0)
    assert np.allclose(moved[0], [1.0, 0.2, 0.1]) and report["moved"] == 1


@pytest.mark.parametrize("clearance", [-0.1, float("nan"), True])
def test_push_out_rejects_bad_clearance(clearance):
    with pytest.raises(ToolError, match="clearance"):
        collide.push_out([[0, 0, 0]], box(), clearance)


def test_empty_inputs():
    assert collide.inside(np.zeros((0, 3)), box()).shape == (0,)
    with pytest.raises(ToolError, match="面がありません"):
        collide.inside([[0, 0, 0]], Mesh([[0, 0, 0]], []))
