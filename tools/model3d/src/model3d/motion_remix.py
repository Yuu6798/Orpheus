"""3D 骨格モーションの非線形リミックス・速度彫刻・上乗せ振付・分身フォーメーション軌道（numpy のみ）。

実制作の 3D ダンスアレンジで実証した以下を、Blender に依存しない純 numpy 計算部品として提供する:
1. `remix_pose_arrays`: 元モーション配列（location / quaternion / scale）を非線形フレーズ表に従って再構成し、
   拍同期の速度マイクロランプ、バレットタイム減速、逆再生、非連続つなぎ目の全骨四元数 Slerp クロスフェード、
   連続フレーム間の四元数符号整合を一括で行う。
2. `apply_additive_bone_euler`: 既存骨格や布の押し出し補正を壊さずに、覚醒の伏せ→上げ・拍グルーヴ・
   無重力アーチなどの局所回転を非破壊で上乗せする。
3. `compute_zero_g_trajectory`: 無重力バレットタイム浮遊（上昇→滞空→加速着地）と空中スピン軌道を計算する。
4. `compute_clone_formation_tracks`: 3D アストラル分身リグの V 字展開・呼吸・収束スパイラル軌道を生成する。
"""
import math

import numpy as np

from .io import ToolError


def _smoothstep(e0, e1, x):
    if e1 <= e0:
        return 1.0 if x >= e1 else 0.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def euler_xyz_to_quat(rx, ry, rz):
    """XYZ オイラー角（ラジアン）を正規化四元数 `(w, x, y, z)` へ変換する（Blender Euler('XYZ') と一致）。"""
    cx, sx = math.cos(rx * 0.5), math.sin(rx * 0.5)
    cy, sy = math.cos(ry * 0.5), math.sin(ry * 0.5)
    cz, sz = math.cos(rz * 0.5), math.sin(rz * 0.5)
    w = cx * cy * cz + sx * sy * sz
    x = sx * cy * cz - cx * sy * sz
    y = cx * sy * cz + sx * cy * sz
    z = cx * cy * sz - sx * sy * cz
    q = np.array([w, x, y, z], dtype=np.float64)
    n = float(np.linalg.norm(q))
    return q / n if n > 1e-12 else np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float64)


def mul_quat(q_a, q_b):
    """四元数 `(w, x, y, z)` の積 `q_a * q_b`（単一またはバッチ `(..., 4)` 対応）。"""
    a = np.asarray(q_a, dtype=np.float64)
    b = np.asarray(q_b, dtype=np.float64)
    w1, x1, y1, z1 = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    w2, x2, y2, z2 = b[..., 0], b[..., 1], b[..., 2], b[..., 3]
    out = np.stack([
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    ], axis=-1)
    n = np.linalg.norm(out, axis=-1, keepdims=True)
    return np.where(n > 1e-12, out / np.where(n > 1e-12, n, 1.0), np.array([1.0, 0.0, 0.0, 0.0]))


def slerp_batch(q0, q1, alpha):
    """バッチ四元数 `(B, 4)` 間の厳密球面線形補間（最短経路・微小角線形フォールバック付き）。"""
    a = float(max(0.0, min(1.0, alpha)))
    qa = np.asarray(q0, dtype=np.float64)
    qb = np.asarray(q1, dtype=np.float64)
    dot = np.sum(qa * qb, axis=-1, keepdims=True)
    qb_adj = np.where(dot < 0.0, -qb, qb)
    dot = np.clip(np.abs(dot), 0.0, 1.0)

    # 微小角は線形補間で数値安定性を保つ
    linear = qa * (1.0 - a) + qb_adj * a
    theta = np.arccos(np.clip(dot, 0.0, 1.0))
    sin_t = np.sin(theta)
    safe_sin = np.where(sin_t > 1e-6, sin_t, 1.0)
    w0 = np.sin((1.0 - a) * theta) / safe_sin
    w1 = np.sin(a * theta) / safe_sin
    sph = w0 * qa + w1 * qb_adj
    out = np.where(dot > 0.9995, linear, sph)
    n = np.linalg.norm(out, axis=-1, keepdims=True)
    return out / np.where(n > 1e-12, n, 1.0)


def _interp_pose(src_f, locs, quats, scales):
    n_src = locs.shape[0]
    x = max(0.0, min(float(n_src - 1), float(src_f) - 1.0))
    i0 = int(math.floor(x))
    i1 = min(n_src - 1, i0 + 1)
    a = x - i0
    loc = locs[i0] * (1.0 - a) + locs[i1] * a
    scl = scales[i0] * (1.0 - a) + scales[i1] * a
    q = slerp_batch(quats[i0], quats[i1], a)
    return loc, q, scl


def _eval_segment_src_frame(f, seg, fps, beat0, period):
    f0 = float(seg["out_start"])
    f1 = float(seg["out_end"])
    s0 = float(seg["src_start"])
    s1 = float(seg["src_end"])
    ramp = float(seg.get("ramp", 0.0))
    span = max(1.0, f1 - f0)
    u = max(0.0, min(1.0, (f - f0) / span))
    if ramp > 0.0 and period > 0.0:
        t = (f - 1.0) / fps
        bn = (t - beat0) / period
        beats_in_seg = max(1.0, span / (period * fps))
        wobble = -math.sin(2.0 * math.pi * bn) * (ramp / (2.0 * math.pi * beats_in_seg))
        u = max(0.0, min(1.0, u + wobble))
    return s0 + (s1 - s0) * u


def remix_pose_arrays(
    src_locs,
    src_quats,
    src_scales,
    segments,
    n_out_frames,
    fps=30.0,
    beat0=0.0,
    period=0.5,
    crossfade_frames=8,
):
    """元モーション `(N_src, B, ...)` を非線形フレーズ表 `segments` で再構成する。

    segments の各要素:
      `{"out_start": 1, "out_end": 120, "src_start": 10.0, "src_end": 130.0, "ramp": 0.2}`
      （`out_start`・`out_end` は 1 始まり半開区間 `[out_start, out_end)`）。
    非連続のつなぎ目は最大 `crossfade_frames` コマで全骨 Slerp 補間し、出力全体の四元数符号を連続化する。
    """
    locs = np.asarray(src_locs, dtype=np.float64)
    quats = np.asarray(src_quats, dtype=np.float64)
    scales = np.asarray(src_scales, dtype=np.float64)
    if locs.ndim != 3 or locs.shape[2] != 3:
        raise ToolError(f"src_locs の形は (N, B, 3) にしてください: {locs.shape}")
    if quats.ndim != 3 or quats.shape[2] != 4 or quats.shape[:2] != locs.shape[:2]:
        raise ToolError(f"src_quats の形は (N, B, 4) で src_locs と揃えてください: {quats.shape}")
    if scales.shape != locs.shape:
        raise ToolError(f"src_scales の形は src_locs と揃えてください: {scales.shape}")
    if not (np.isfinite(locs).all() and np.isfinite(quats).all() and np.isfinite(scales).all()):
        raise ToolError("元モーション配列に非有限の値があります")
    if n_out_frames < 1 or fps <= 0:
        raise ToolError(f"n_out_frames ({n_out_frames}) と fps ({fps}) は正の数にしてください")
    if not segments:
        raise ToolError("segments が空です")
    if crossfade_frames < 0:
        raise ToolError(f"crossfade_frames は 0 以上にしてください: {crossfade_frames}")

    # 元四元数を正規化
    qn = np.linalg.norm(quats, axis=-1, keepdims=True)
    quats = quats / np.where(qn > 1e-12, qn, 1.0)

    n_src, n_bones, _ = locs.shape
    for seg in segments:
        if float(seg["out_end"]) <= float(seg["out_start"]):
            raise ToolError(f"セグメントの区間が不正です: {seg}")
        if not (1.0 <= float(seg["src_start"]) <= n_src and 1.0 <= float(seg["src_end"]) <= n_src):
            raise ToolError(f"セグメントの src 範囲が元コマ数 1..{n_src} の外です: {seg}")
        if float(seg.get("ramp", 0.0)) < 0.0:
            raise ToolError(f"セグメントの ramp は 0 以上にしてください: {seg}")

    out_locs = np.zeros((n_out_frames, n_bones, 3), dtype=np.float64)
    out_quats = np.zeros((n_out_frames, n_bones, 4), dtype=np.float64)
    out_scales = np.ones((n_out_frames, n_bones, 3), dtype=np.float64)

    for fi in range(n_out_frames):
        f = fi + 1
        si = len(segments) - 1
        for idx, s in enumerate(segments):
            if float(s["out_start"]) <= f < float(s["out_end"]):
                si = idx
                break
        seg = segments[si]
        sf = _eval_segment_src_frame(f, seg, fps, beat0, period)
        loc, q, scl = _interp_pose(sf, locs, quats, scales)

        if crossfade_frames > 0 and si > 0 and (f - float(seg["out_start"])) < crossfade_frames:
            prev_seg = segments[si - 1]
            if abs(float(seg["src_start"]) - float(prev_seg["src_end"])) > 2.0:
                df = f - float(seg["out_start"])
                alpha = _smoothstep(0.0, float(crossfade_frames), df)
                prev_dir = 1.0 if float(prev_seg["src_end"]) >= float(prev_seg["src_start"]) else -1.0
                sf_prev = max(1.0, min(float(n_src), float(prev_seg["src_end"]) + prev_dir * df * 0.35))
                lp, qp, sp = _interp_pose(sf_prev, locs, quats, scales)
                loc = lp * (1.0 - alpha) + loc * alpha
                scl = sp * (1.0 - alpha) + scl * alpha
                q = slerp_batch(qp, q, alpha)

        out_locs[fi] = loc
        out_quats[fi] = q
        out_scales[fi] = scl

    # 全骨の連続フレーム間で四元数符号を揃える
    for fi in range(1, n_out_frames):
        dots = np.sum(out_quats[fi - 1] * out_quats[fi], axis=-1)
        flip = dots < 0.0
        if np.any(flip):
            out_quats[fi, flip] = -out_quats[fi, flip]

    return out_locs, out_quats, out_scales


def apply_additive_bone_euler(quats, bone_idx, frame_weights, rx, ry, rz):
    """特定ボーン `bone_idx` へフレーム重み `frame_weights (N,)` に応じた局所オイラー回転を非破壊上乗せする。"""
    out = np.array(quats, dtype=np.float64, copy=True)
    w = np.asarray(frame_weights, dtype=np.float64).reshape(-1)
    if out.ndim != 3 or out.shape[2] != 4:
        raise ToolError(f"quats の形は (N, B, 4) にしてください: {out.shape}")
    if len(w) != out.shape[0]:
        raise ToolError(f"frame_weights ({len(w)}) がフレーム数 ({out.shape[0]}) と一致しません")
    if not (0 <= bone_idx < out.shape[1]):
        raise ToolError(f"bone_idx ({bone_idx}) がボーン数 ({out.shape[1]}) の範囲外です")
    if not np.isfinite(w).all():
        raise ToolError("frame_weights に非有限の値があります")

    for fi in range(out.shape[0]):
        wi = float(w[fi])
        if abs(wi) <= 1e-12:
            continue
        dq = euler_xyz_to_quat(rx * wi, ry * wi, rz * wi)
        out[fi, bone_idx] = mul_quat(out[fi, bone_idx], dq)

    for fi in range(1, out.shape[0]):
        if float(np.dot(out[fi - 1, bone_idx], out[fi, bone_idx])) < 0.0:
            out[fi, bone_idx] = -out[fi, bone_idx]
    return out


def compute_zero_g_trajectory(n_frames, start_f, peak_start_f, peak_end_f, land_f, height_m=0.64, spin_deg=360.0):
    """無重力浮遊（上昇→滞空→加速着地）の Z 高さ `(N,)` と空中スピン角ラジアン `(N,)` を返す。"""
    if n_frames < 1:
        raise ToolError(f"n_frames は 1 以上にしてください: {n_frames}")
    if not (start_f < peak_start_f <= peak_end_f < land_f):
        raise ToolError("start_f < peak_start_f <= peak_end_f < land_f の順にしてください")
    if not (math.isfinite(height_m) and math.isfinite(spin_deg)):
        raise ToolError("height_m と spin_deg は有限値にしてください")
    z = np.zeros(n_frames, dtype=np.float64)
    yaw = np.zeros(n_frames, dtype=np.float64)
    spin_rad = math.radians(float(spin_deg))
    for fi in range(n_frames):
        f = float(fi + 1)
        if start_f <= f <= land_f:
            if f < peak_start_f:
                z[fi] = height_m * _smoothstep(start_f, peak_start_f, f)
            elif f < peak_end_f:
                z[fi] = height_m
            else:
                u = _smoothstep(peak_end_f, land_f, f)
                z[fi] = height_m * (1.0 - (u ** 1.8))
            yaw[fi] = spin_rad * _smoothstep(start_f, land_f, f)
    return z, yaw


def compute_clone_formation_tracks(
    n_frames,
    windows,
    target_xyz,
    yaw_rad=0.0,
    mirror_x=False,
    breath_amp=0.08,
    hidden_scale=1e-4,
):
    """3D 分身リグの配置軌道 `(locations (N,3), eulers (N,3), scales (N,3))` を生成する。

    windows の各要素:
      `{"start": 534, "hold_start": 550, "hold_end": 834, "end": 878, "spread": 1.0, "spiral_exit": True}`
      （1 始まりフレーム番号）。区間外は `hidden_scale` に縮小して非表示化する。
    """
    if n_frames < 1:
        raise ToolError(f"n_frames は 1 以上にしてください: {n_frames}")
    for w in windows:
        fs, fhs, fhe, fe = float(w["start"]), float(w["hold_start"]), float(w["hold_end"]), float(w["end"])
        if not (fs <= fhs <= fhe <= fe):
            raise ToolError(f"window の時刻順が不正です（start <= hold_start <= hold_end <= end）: {w}")
    tx, ty, tz = (float(v) for v in target_xyz)
    sx = -1.0 if mirror_x else 1.0
    locs = np.zeros((n_frames, 3), dtype=np.float64)
    eulers = np.zeros((n_frames, 3), dtype=np.float64)
    scales = np.full((n_frames, 3), float(hidden_scale), dtype=np.float64)
    scales[:, 0] *= sx

    for fi in range(n_frames):
        f = float(fi + 1)
        spread = 0.0
        vis = 0.0
        yaw = 0.0
        for w in windows:
            fs, fhs, fhe, fe = float(w["start"]), float(w["hold_start"]), float(w["hold_end"]), float(w["end"])
            if not (fs <= f <= fe):
                continue
            max_sp = float(w.get("spread", 1.0))
            spiral_yaw = 0.0
            if f < fhs:
                u = _smoothstep(fs, fhs, f)
                sp = max_sp * u
                v = min(1.0, u * 1.4)
            elif f <= fhe:
                breath = float(breath_amp) * math.sin((f - fhs) * 0.07)
                sp = max_sp * (1.0 + breath)
                v = 1.0
            else:
                u = 1.0 - _smoothstep(fhe, fe, f)
                sp = max_sp * u
                v = u
                if w.get("spiral_exit"):
                    spiral_yaw = (1.0 - u) * (math.pi if sx > 0 else -math.pi)
            if v > vis:
                spread = sp
                vis = v
                yaw = float(yaw_rad) * min(1.0, sp) + spiral_yaw

        s_val = float(hidden_scale) if vis < 0.01 else (0.96 + 0.04 * vis)
        locs[fi] = (tx * spread, ty * spread, tz * vis)
        eulers[fi] = (0.0, 0.0, yaw)
        scales[fi] = (sx * s_val, s_val, s_val)

    return locs, eulers, scales
