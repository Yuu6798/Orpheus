"""model3d.motion_remix（非線形モーションリミックス・速度彫刻・分身フォーメーション）の単体テスト。"""
import math

import numpy as np
import pytest

from model3d import motion_remix as mr
from model3d.io import ToolError


def _make_synthetic_motion(n_src=60, n_bones=3):
    locs = np.zeros((n_src, n_bones, 3), dtype=np.float64)
    quats = np.zeros((n_src, n_bones, 4), dtype=np.float64)
    scales = np.ones((n_src, n_bones, 3), dtype=np.float64)
    for fi in range(n_src):
        u = fi / max(1, n_src - 1)
        locs[fi, 0] = [u, 0.0, 1.0 + 0.2 * math.sin(u * math.pi)]
        for bi in range(n_bones):
            quats[fi, bi] = mr.euler_xyz_to_quat(0.0, 0.0, u * math.pi * (bi + 1) * 0.5)
    return locs, quats, scales


def test_remix_nonlinear_slerp_crossfade_and_bullet_time():
    locs, quats, scales = _make_synthetic_motion(60, 3)
    segments = [
        # 前半: 元フレーム 40..55 を 1..30 へ（0.5x スロー＋速度ランプ）
        {"out_start": 1, "out_end": 31, "src_start": 40.0, "src_end": 55.0, "ramp": 0.25},
        # 後半: 非連続に元フレーム 5..25 へジャンプ（クロスフェードで滑らか接続）
        {"out_start": 31, "out_end": 61, "src_start": 5.0, "src_end": 25.0, "ramp": 0.0},
    ]
    ol, oq, os_ = mr.remix_pose_arrays(locs, quats, scales, segments, n_out_frames=60, crossfade_frames=8)
    assert ol.shape == (60, 3, 3) and oq.shape == (60, 3, 4) and os_.shape == (60, 3, 3)

    # 全四元数が単位長かつ隣接フレーム符号反転ゼロ
    norms = np.linalg.norm(oq, axis=-1)
    assert np.allclose(norms, 1.0, atol=1e-6)
    for fi in range(1, 60):
        dots = np.sum(oq[fi - 1] * oq[fi], axis=-1)
        assert np.all(dots >= 0.0)

    # 非連続つなぎ目（f=30 -> f=31）が Slerp クロスフェードにより急跳びしないこと
    jump = float(np.linalg.norm(ol[30, 0] - ol[29, 0]))
    raw_gap = float(np.linalg.norm(locs[4, 0] - locs[54, 0]))
    assert jump < raw_gap * 0.35


def test_apply_additive_bone_euler_preserves_other_bones():
    _, quats, _ = _make_synthetic_motion(20, 3)
    weights = np.linspace(0.0, 1.0, 20)
    out = mr.apply_additive_bone_euler(quats, bone_idx=1, frame_weights=weights, rx=0.4, ry=0.0, rz=0.0)
    # 対象外ボーンは不変
    assert np.allclose(out[:, 0], quats[:, 0])
    assert np.allclose(out[:, 2], quats[:, 2])
    # 重み 0 の先頭フレームは不変、末尾フレームは回転が乗る
    assert np.allclose(out[0, 1], quats[0, 1])
    assert not np.allclose(out[-1, 1], quats[-1, 1])
    assert np.allclose(np.linalg.norm(out[:, 1], axis=-1), 1.0, atol=1e-6)


def test_zero_g_trajectory_lifts_and_lands_cleanly():
    z, yaw = mr.compute_zero_g_trajectory(100, start_f=20, peak_start_f=40, peak_end_f=50, land_f=65, height_m=0.64, spin_deg=360.0)
    assert z[0] == 0.0 and z[18] == 0.0
    assert abs(z[44] - 0.64) < 1e-6
    assert z[64] == 0.0 and z[99] == 0.0
    assert abs(yaw[64] - 2.0 * math.pi) < 1e-6


def test_clone_formation_tracks_mirror_spread_and_hide():
    windows = [{"start": 20, "hold_start": 30, "hold_end": 50, "end": 60, "spread": 1.0, "spiral_exit": True}]
    locs, eulers, scales = mr.compute_clone_formation_tracks(
        80, windows, target_xyz=(-1.2, 0.9, 0.0), yaw_rad=-0.25, mirror_x=True
    )
    # 区間外は極小スケールで隠れる
    assert abs(scales[0, 0]) < 1e-3 and np.allclose(locs[0], 0.0)
    assert abs(scales[75, 0]) < 1e-3
    # 展開中は X ミラー反転（負スケール）で目標座標付近に配置される
    assert scales[39, 0] < -0.95 and scales[39, 1] > 0.95
    assert locs[39, 0] < -1.0 and locs[39, 1] > 0.75
    # 収束スパイラル区間（f=55）でスピン角が変化している
    assert abs(eulers[54, 2]) > 0.5


def test_clone_formation_overlapping_windows_do_not_leak_spiral_yaw():
    windows = [
        {"start": 1, "hold_start": 5, "hold_end": 10, "end": 20, "spread": 0.5, "spiral_exit": True},
        {"start": 10, "hold_start": 12, "hold_end": 25, "end": 30, "spread": 1.0, "spiral_exit": False},
    ]
    _, eulers, _ = mr.compute_clone_formation_tracks(30, windows, target_xyz=(1.0, 1.0, 0.0), yaw_rad=0.2, mirror_x=False)
    # f=15 は window2（vis=1.0, spiral_exit=False）が勝つため、window1 の spiral_yaw が混入しない
    assert abs(eulers[14, 2] - 0.2) < 0.05


def test_remix_rejects_invalid_inputs():
    locs, quats, scales = _make_synthetic_motion(10, 2)
    with pytest.raises(ToolError):
        mr.remix_pose_arrays(locs, quats, scales, [], n_out_frames=10)
    with pytest.raises(ToolError):
        mr.remix_pose_arrays(locs, quats, scales, [{"out_start": 1, "out_end": 11, "src_start": 1.0, "src_end": 99.0}], n_out_frames=10)
    with pytest.raises(ToolError):
        mr.remix_pose_arrays(locs, quats, scales, [{"out_start": 1, "out_end": 11, "src_start": 1.0, "src_end": 5.0, "ramp": -0.1}], n_out_frames=10)
    with pytest.raises(ToolError):
        mr.compute_zero_g_trajectory(50, start_f=30, peak_start_f=20, peak_end_f=40, land_f=45)
    with pytest.raises(ToolError):
        mr.compute_clone_formation_tracks(30, [{"start": 10, "hold_start": 5, "hold_end": 20, "end": 25}], (1, 0, 0))
