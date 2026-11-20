"""model3d.bone_ribbon（骨格軌跡連動 3D 光リボン生成）の単体テスト。"""
import numpy as np
import pytest

from model3d.bone_ribbon import build_ribbon_topology, compute_ribbon_vertices
from model3d.io import ToolError


def test_build_ribbon_topology_counts_and_uv_range():
    faces, uvs = build_ribbon_topology(10)
    assert len(faces) == 10
    assert len(uvs) == 22
    assert uvs[0] == (0.0, 0.0) and uvs[1] == (0.0, 1.0)
    assert uvs[-2] == (1.0, 0.0) and uvs[-1] == (1.0, 1.0)
    # 全頂点番号が範囲内
    for quad in faces:
        assert len(quad) == 4 and all(0 <= vi < len(uvs) for vi in quad)


def test_compute_ribbon_vertices_speed_gate_and_orthogonality():
    # 1) 静止軌跡 → 速度ゲート 0 → 全幅 0（左右頂点が一致）
    static = np.tile(np.array([[0.2, 0.0, 1.2]]), (30, 1))
    verts_s, spd_s = compute_ribbon_vertices(static, frame_idx=20, camera_pos=[0.0, -3.0, 1.2], n_segs=8)
    assert spd_s == 0.0
    assert np.allclose(verts_s[0::2], verts_s[1::2])

    # 2) 高速スイープ軌跡 → 速度ゲート 1、カメラ視線・接線の双方に直交、尾端へ向かい先細り
    t = np.linspace(0.0, 1.0, 30)
    fast = np.stack([t * 2.5, np.zeros_like(t), np.full_like(t, 1.2)], axis=1)
    cam = np.array([0.0, -3.0, 1.2])
    verts_f, spd_f = compute_ribbon_vertices(fast, frame_idx=25, camera_pos=cam, n_segs=8, base_width=0.10)
    assert spd_f == 1.0
    assert verts_f.shape == (18, 3)

    widths = np.linalg.norm(verts_f[1::2] - verts_f[0::2], axis=1)
    # 尾端は完全に 0、根元付近は明確な幅を持つ
    assert widths[-1] < 1e-9
    assert widths[1] > widths[5] > widths[-1]

    # 先頭付近の横ベクトルがカメラ視線と接線に直交する
    side = verts_f[3] - verts_f[2]
    side /= np.linalg.norm(side)
    view = cam - fast[24]
    view /= np.linalg.norm(view)
    tang = np.array([1.0, 0.0, 0.0])
    assert abs(float(np.dot(side, view))) < 1e-5
    assert abs(float(np.dot(side, tang))) < 1e-5


def test_compute_ribbon_vertices_handles_parallel_view_and_rejects_bad_inputs():
    # 軌跡がカメラへ向かってまっすぐ進む退化ケースでも NaN を出さない
    t = np.linspace(0.0, 2.0, 20)
    toward_cam = np.stack([np.zeros_like(t), -t, np.ones_like(t)], axis=1)
    verts, spd = compute_ribbon_vertices(toward_cam, frame_idx=15, camera_pos=[0.0, -5.0, 1.0], n_segs=6)
    assert np.isfinite(verts).all() and spd > 0.0

    # ヘアピン急反転（+X 進行 → -X 反転）で横ベクトルが隣接節間で裏返らない（蝶ネクタイねじれ防止）
    xs = np.concatenate([np.linspace(0.0, 1.0, 8), np.linspace(0.9, 0.0, 8)])
    hairpin = np.stack([xs, np.zeros_like(xs), np.full_like(xs, 1.2)], axis=1)
    vh, _ = compute_ribbon_vertices(hairpin, frame_idx=14, camera_pos=[0.0, -3.0, 1.2], n_segs=10, base_width=0.08)
    sides = vh[1::2] - vh[0::2]
    for i in range(1, len(sides) - 1):
        if np.linalg.norm(sides[i - 1]) > 1e-6 and np.linalg.norm(sides[i]) > 1e-6:
            assert float(np.dot(sides[i - 1], sides[i])) >= 0.0

    with pytest.raises(ToolError):
        build_ribbon_topology(0)
    with pytest.raises(ToolError):
        compute_ribbon_vertices(toward_cam, frame_idx=15, camera_pos=[0.0, -5.0, 1.0], speed_window=0)
    with pytest.raises(ToolError):
        compute_ribbon_vertices(toward_cam, frame_idx=99, camera_pos=[0.0, -5.0, 1.0])
    with pytest.raises(ToolError):
        compute_ribbon_vertices(toward_cam, frame_idx=5, camera_pos=[0.0, np.nan, 1.0])
