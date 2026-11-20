"""骨格の 3D ワールド軌跡からカメラ正対ボリューメトリック光リボンを生成する（numpy のみ）。

実制作の 3D ダンス演出（手首・足首の軌跡に光の帯を描く表現）から切り出した計算部品。
Blender に依存せず、`bone_tracks.npz` 等で得たワールド座標 `(N, 3)` とカメラ位置から、
以下を毎フレーム高速に算出する:
- 移動速度ゲート（静止姿勢では幅 0、速い振りのみ太く発光させる smoothstep 係数）
- 接線 × カメラ視線ベクトルの外積によるカメラ正対ビルボード幅ベクトル
- 根元（手首側）から尾端にかけて滑らかに細くなるテーパー幅
- Quad トポロジーおよび `(u_along, v_across)` UV 座標
"""
import math

import numpy as np

from .io import ToolError


def _smoothstep(e0, e1, x):
    if e1 <= e0:
        return 1.0 if x >= e1 else 0.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def build_ribbon_topology(n_segs):
    """`n_segs` 区間の帯メッシュ用 `(faces, uvs)` を返す。

    頂点数は `(n_segs + 1) * 2`（各節 i に左端 `2*i`・右端 `2*i + 1`）。
    `faces` は 4 角形の頂点番号タプル、`uvs` は各頂点の `(u_along, v_across)`（`u=0` が先頭・`u=1` が尾端）。
    """
    n_segs = int(n_segs)
    if n_segs < 1:
        raise ToolError(f"n_segs は 1 以上にしてください: {n_segs}")
    faces = []
    for i in range(n_segs):
        a = i * 2
        b = a + 1
        c = a + 3
        d = a + 2
        faces.append((a, b, c, d))
    uvs = []
    inv_n = 1.0 / float(n_segs)
    for i in range(n_segs + 1):
        u = i * inv_n
        uvs.append((u, 0.0))
        uvs.append((u, 1.0))
    return faces, uvs


def compute_ribbon_vertices(
    track_pts,
    frame_idx,
    camera_pos,
    n_segs=14,
    base_width=0.056,
    speed_lo=0.06,
    speed_hi=0.32,
    speed_window=5,
):
    """時刻 `frame_idx`（0 始まり）における 3D 光リボンの頂点座標 `((n_segs+1)*2, 3)` と速度ゲート係数 `[0..1]` を返す。

    `track_pts`: 骨格の 3D ワールド座標列 `(N, 3)`（単位 m）。
    `camera_pos`: そのフレームのカメラ位置 `(3,)`。
    """
    pts_all = np.asarray(track_pts, dtype=np.float64)
    cam = np.asarray(camera_pos, dtype=np.float64).reshape(-1)
    if pts_all.ndim != 2 or pts_all.shape[1] != 3 or len(pts_all) == 0:
        raise ToolError(f"track_pts の形は空でない (N, 3) にしてください: {pts_all.shape}")
    if len(cam) != 3:
        raise ToolError(f"camera_pos は 3 要素にしてください: {cam.shape}")
    if not (np.isfinite(pts_all).all() and np.isfinite(cam).all()):
        raise ToolError("track_pts または camera_pos に非有限の値があります")
    n_segs = int(n_segs)
    if n_segs < 1:
        raise ToolError(f"n_segs は 1 以上にしてください: {n_segs}")
    if not (math.isfinite(base_width) and base_width >= 0.0):
        raise ToolError(f"base_width は 0 以上の有限値にしてください: {base_width}")
    if not (math.isfinite(speed_lo) and math.isfinite(speed_hi) and 0.0 <= speed_lo <= speed_hi):
        raise ToolError(f"0 <= speed_lo <= speed_hi にしてください: {speed_lo}, {speed_hi}")
    if int(speed_window) < 1:
        raise ToolError(f"speed_window は 1 以上にしてください: {speed_window}")
    fi = int(frame_idx)
    if not (0 <= fi < len(pts_all)):
        raise ToolError(f"frame_idx ({fi}) が軌跡長 ({len(pts_all)}) の範囲外です")

    # 過去 n_segs + 1 フレームの点を抽出（0 以前はその先頭点でクランプ）
    indices = np.maximum(0, fi - np.arange(n_segs + 1))
    trail = pts_all[indices]

    # 直近 speed_window 区間の移動距離から速度ゲート係数を算出
    win = max(1, min(int(speed_window), n_segs))
    recent_len = float(np.linalg.norm(trail[:win] - trail[1:win + 1], axis=1).sum())
    speed_fac = _smoothstep(float(speed_lo), float(speed_hi), recent_len)

    verts = np.empty(((n_segs + 1) * 2, 3), dtype=np.float64)
    fallback_up = np.array([0.0, 0.0, 1.0], dtype=np.float64)
    fallback_side = np.array([1.0, 0.0, 0.0], dtype=np.float64)
    prev_side = None

    for i in range(n_segs + 1):
        p = trail[i]
        tang = trail[i] - trail[i + 1] if i < n_segs else trail[i - 1] - trail[i]
        t_len = float(np.linalg.norm(tang))
        tang = tang / t_len if t_len > 1e-6 else fallback_up

        view = cam - p
        v_len = float(np.linalg.norm(view))
        view = view / v_len if v_len > 1e-6 else np.array([0.0, -1.0, 0.0], dtype=np.float64)

        side = np.cross(tang, view)
        s_len = float(np.linalg.norm(side))
        if s_len <= 1e-6:
            # 接線と視線が平行な退化ケースは視線と Z 軸の外積へ退避
            side = np.cross(view, fallback_up)
            s_len = float(np.linalg.norm(side))
            side = side / s_len if s_len > 1e-6 else fallback_side
        else:
            side = side / s_len

        # 急反転（ヘアピンカーブ）でリボンが蝶ネクタイ状にねじれるのを防ぐ
        if prev_side is not None and float(np.dot(side, prev_side)) < 0.0:
            side = -side
        prev_side = side

        u = i / float(n_segs)
        width = float(base_width) * speed_fac * math.sin((1.0 - u) * math.pi * 0.85) * (1.0 - u * 0.65)
        verts[i * 2] = p - side * width
        verts[i * 2 + 1] = p + side * width

    return verts, float(speed_fac)
