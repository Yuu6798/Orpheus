"""model3d.spatial のカメラ視線コリドー遮蔽ガードの単体テスト。"""
import numpy as np
import pytest

from model3d.io import ToolError
from model3d.spatial import camera_corridor_attenuation, camera_corridor_mask


def test_camera_corridor_mask_3d_and_xy_only():
    cam = [0.0, -4.0, 1.0]
    tgt = [0.0, 0.0, 1.0]
    pts = np.array([
        [0.0, -2.0, 1.0],    # 視線のど真ん中 → 遮蔽
        [0.5, -2.0, 1.0],    # 半径 1.0 以内 → 遮蔽
        [1.5, -2.0, 1.0],    # 横に十分離れている → 非遮蔽
        [0.0, +2.0, 1.0],    # 注視点の奥側 → 非遮蔽
        [0.0, -5.0, 1.0],    # カメラの背後 → 非遮蔽
        [0.0, -2.0, 3.0],    # 3D なら上へ外れるが XY 投影なら直上コリドー内
    ])

    mask3d = camera_corridor_mask(pts, cam, tgt, radius=1.0, xy_only=False)
    assert mask3d.tolist() == [True, True, False, False, False, False]

    mask_xy = camera_corridor_mask(pts, cam, tgt, radius=1.0, xy_only=True)
    assert mask_xy.tolist() == [True, True, False, False, False, True]


def test_camera_corridor_attenuation_monotonic_smoothstep():
    cam = [0.0, -4.0, 0.0]
    tgt = [0.0, 0.0, 0.0]
    pts = np.array([
        [0.0, -2.0, 0.0],   # d=0.0 <= inner=1.0 -> 0.0
        [1.0, -2.0, 0.0],   # d=1.0 == inner     -> 0.0
        [1.5, -2.0, 0.0],   # 中点               -> 0.5
        [2.0, -2.0, 0.0],   # d=2.0 == outer     -> 1.0
        [3.0, -2.0, 0.0],   # d=3.0 > outer      -> 1.0
        [0.0, +2.0, 0.0],   # 奥側（軸外）       -> 1.0
    ])
    att = camera_corridor_attenuation(pts, cam, tgt, inner_radius=1.0, outer_radius=2.0)
    assert np.allclose(att, [0.0, 0.0, 0.5, 1.0, 1.0, 1.0])


def test_camera_corridor_rejects_degenerate_and_bad_inputs():
    pts = np.array([[0.0, 0.0, 0.0]])
    with pytest.raises(ToolError):
        camera_corridor_mask(pts, [1, 2, 3], [1, 2, 3], radius=1.0)
    with pytest.raises(ToolError):
        camera_corridor_mask(pts, [0, -1, 0], [0, 0, 0], radius=-0.1)
    with pytest.raises(ToolError):
        camera_corridor_attenuation(pts, [0, -1, 0], [0, 0, 0], inner_radius=2.0, outer_radius=1.0)
    with pytest.raises(ToolError):
        camera_corridor_mask(np.array([[np.nan, 0.0, 0.0]]), [0, -1, 0], [0, 0, 0], radius=1.0)
