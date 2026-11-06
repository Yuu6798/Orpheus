"""推定した 3D の点（SAM 3D Body の MHR70、70 点）を、キャラに載せる前に整える。numpy だけを使う。

座標は x 右・y 奥（カメラから遠ざかる向き）・z 上、単位はメートル。入力は SAM 3D Body の出力と同じく、
カメラ座標（x 右・y 下・z 前）の点（pred_keypoints_3d）と、コマごとのカメラの並進（pred_cam_t）。

手順（リポジトリ外の 32 秒の試作で使った手順を、汎用の部分だけ移したもの）:
1. 1 コマだけ飛び出した点を、前後 3 コマの中央値で置き換える（腰の中心からの相対位置で見る）
2. Savitzky–Golay でならす。1 台のカメラの推定は奥行き（y）の揺れが横の 2〜3 倍大きいので、奥行きだけ窓を広げる
3. 足の最下点から床の平面を求め、床が水平になるよう全体を回し、床を z = 0 に置く。前後の傾き（pitch）は上書きできる
4. 腰より上（頭・肩・腕・手）を腰の中心のまわりに、カメラから遠ざかる向きへ起こせる（torso_back）
5. 接地の区間で足を横に動かさない（区間の中央値の位置に止める）。区間の前後はなめらかにつなぎ、膝は解き直す

推定の癖（どの量を直すべきか）はクリップごとに違うので、値は recipe の側で決める。ここで行うのは決めた量の処理だけ。
"""
import math

import numpy as np

N_POINTS = 70
HIPS = (9, 10)
# (股関節, 膝, 足首, 足の点) 左・右。足の点は足首・つま先・かかとなど MHR70 の 4 点
LEGS = ((9, 11, 13, (13, 15, 16, 17)), (10, 12, 14, (14, 18, 19, 20)))
FEET = tuple(foot for *_, foot in LEGS)
UPPER = tuple(range(0, 9)) + tuple(range(21, 70))   # 腰・脚・足（9〜20）以外
SIDES = ("left", "right")
CONTACT_MODES = ("absolute", "relative")


def from_camera(keypoints, translation):
    """カメラ座標（x 右・y 下・z 前）の点 + 並進 → x 右・y 奥・z 上。"""
    points = np.asarray(keypoints, dtype=float) + np.asarray(translation, dtype=float)[:, None, :]
    return np.stack([points[..., 0], points[..., 2], -points[..., 1]], axis=-1)


def median3(values):
    """時間方向（axis 0）の 3 コマの中央値。端は同じコマを繰り返して数える。"""
    values = np.asarray(values, dtype=float)
    padded = np.concatenate([values[:1], values, values[-1:]], axis=0)
    return np.median(np.stack([padded[:-2], padded[1:-1], padded[2:]]), axis=0)


def savgol(values, window, order=2):
    """Savitzky–Golay の平滑化（時間方向、axis 0）。

    中ほどは窓の多項式の中央の値。端の window // 2 コマは、端の window コマに当てた多項式の値（scipy の
    savgol_filter の mode="interp" と同じ考え方）。コマ数が窓より少なければ、全体に 1 つの多項式を当てる。
    """
    values = np.asarray(values, dtype=float)
    if window < 3 or window % 2 == 0 or window <= order:
        raise ValueError(f"window は order より大きい 3 以上の奇数にしてください: window={window}, order={order}")
    n = len(values)
    flat = values.reshape(n, -1)
    if n <= order:
        return values.copy()
    if n < window:
        t = np.arange(n, dtype=float)
        fit = np.linalg.lstsq(np.vander(t, order + 1), flat, rcond=None)[0]
        return (np.vander(t, order + 1) @ fit).reshape(values.shape)
    half = window // 2
    offsets = np.arange(-half, half + 1, dtype=float)
    centre = np.linalg.pinv(np.vander(offsets, order + 1, increasing=True))[0]
    out = np.empty_like(flat)
    windows = np.lib.stride_tricks.sliding_window_view(flat, window, axis=0)   # (n - window + 1, 列, window)
    out[half:n - half] = windows @ centre
    t = np.arange(window, dtype=float)
    basis = np.vander(t, order + 1)
    for rows, evaluate in ((slice(0, window), np.arange(half)), (slice(n - window, n), np.arange(window - half, window))):
        fit = np.linalg.lstsq(basis, flat[rows], rcond=None)[0]
        target = range(evaluate[0] + rows.start, evaluate[-1] + rows.start + 1)
        out[target.start:target.stop] = np.vander(evaluate.astype(float), order + 1) @ fit
    return out.reshape(values.shape)


def clean(points, depth_window=11, spike=0.03):
    """1 コマの跳ねを除き、ならす。腰の中心と、腰からの相対位置を別々にならす（体の形と全体の動きを混ぜない）。"""
    hip = points[:, list(HIPS)].mean(axis=1)
    relative = points - hip[:, None]
    middle = median3(relative)
    jumped = np.linalg.norm(relative - middle, axis=-1) > spike
    fixed = np.where(jumped[..., None], middle, relative)
    smooth = savgol(fixed, 5)
    smooth[..., 1] = savgol(fixed[..., 1], depth_window)
    centre = savgol(hip, 5)
    centre[:, 1] = savgol(hip[:, 1], depth_window)
    return smooth + centre[:, None], int(jumped.sum())


def _lowest_foot_points(points):
    rows = np.arange(len(points))
    picked = []
    for foot in FEET:
        foot = np.array(foot)
        picked.append(points[rows, foot[points[:, foot, 2].argmin(axis=1)]])
    return np.vstack(picked)


def floor_plane(points):
    """足の最下点に平面 z = a x + b y + c を当てる（高い方の点を外しながら 8 回）。(a, b, c) を返す。"""
    feet = _lowest_foot_points(points)
    keep = np.ones(len(feet), bool)
    for _ in range(8):
        design = np.c_[feet[keep, 0], feet[keep, 1], np.ones(keep.sum())]
        plane = np.linalg.lstsq(design, feet[keep, 2], rcond=None)[0]
        residual = feet[:, 2] - (plane[0] * feet[:, 0] + plane[1] * feet[:, 1] + plane[2])
        keep = residual < np.percentile(residual, 45)
    return plane


def level(points, pitch_deg=None):
    """床が水平になるよう全体を回し、床を z = 0 に置く。

    返り値: 回した点、測った傾き（roll, pitch の度）、使った傾き、床に合わせて下げた量（m）。
    pitch_deg を渡すと、前後の傾きは平面の値ではなくその値を使う（奥行きにあまり動かない踊りでは平面の pitch が定まらない）。
    """
    plane = floor_plane(points)
    measured = np.degrees(np.arctan(plane[:2]))
    if pitch_deg is not None:
        plane = plane.copy()
        plane[1] = math.tan(math.radians(pitch_deg))
    applied = np.degrees(np.arctan(plane[:2]))
    normal = np.array([-plane[0], -plane[1], 1.0])
    normal /= np.linalg.norm(normal)
    axis = np.cross(normal, [0.0, 0.0, 1.0])
    sine = np.linalg.norm(axis)
    if sine < 1e-12:
        rotated = points.copy()
    else:
        axis /= sine
        cross = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
        rotation = np.eye(3) + sine * cross + (1 - normal[2]) * cross @ cross
        rotated = points @ rotation.T
    lowest = np.minimum(*(rotated[:, list(foot), 2].min(axis=1) for foot in FEET))
    floor = float(np.percentile(lowest, 15))
    rotated[..., 2] -= floor
    return rotated, measured, applied, floor


def torso_back(points, degrees):
    """腰より上を腰の中心のまわりに x 軸で回し、上の端をカメラから遠ざける（y が増える向き）。"""
    if not degrees:
        return points
    angle = math.radians(-degrees)
    rotation = np.array([[1, 0, 0], [0, math.cos(angle), -math.sin(angle)], [0, math.sin(angle), math.cos(angle)]])
    out = points.copy()
    hip = points[:, list(HIPS)].mean(axis=1)[:, None]
    out[:, list(UPPER)] = (points[:, list(UPPER)] - hip) @ rotation.T + hip
    return out


def leg_ik(hip, ankle, upper, lower, pole_hint):
    """股関節と足首から膝の位置を解く（脚の長さは upper・lower、曲がる向きは pole_hint）。"""
    v = ankle - hip
    length = min(max(np.linalg.norm(v), abs(upper - lower) + 1e-4), upper + lower - 1e-4)
    direction = v / np.linalg.norm(v)
    pole = pole_hint - direction * pole_hint.dot(direction)
    if np.linalg.norm(pole) < 1e-4:
        pole = np.array([0.0, -1.0, 0.0]) - direction * (-direction[1])
    pole /= np.linalg.norm(pole)
    along = (upper * upper - lower * lower + length * length) / (2 * length)
    return hip + direction * along + pole * math.sqrt(max(upper * upper - along * along, 0.0))


def _contact(points, foot, lowest, mode, height):
    low = points[:, list(foot), 2].min(axis=1)
    contact = (low - lowest) < height if mode == "relative" else low < height
    for i in range(1, len(points) - 1):   # 1 コマの切れ目はつなぐ
        if contact[i - 1] and contact[i + 1]:
            contact[i] = True
    return contact


def _runs(contact, centre, step_split):
    """接地の連続区間。step_split があれば、足が止めた位置からその距離（奥行きは半分に数える）より動いた所で分ける。"""
    runs, t, n = [], 0, len(contact)
    while t < n:
        if not contact[t]:
            t += 1
            continue
        u = t
        while u < n and contact[u]:
            u += 1
        if step_split is None:
            runs.append((t, u))
        else:
            start, i = t, t
            while i < u:
                d = centre[i] - np.median(centre[start:i + 1], axis=0)
                if i - start >= 2 and math.hypot(d[0], 0.5 * d[1]) > step_split:
                    runs.append((start, i - 1))
                    start = i + 1   # 2 コマ（i-1, i）を踏み替えのつなぎに空ける
                    i = start
                    continue
                i += 1
            runs.append((start, u))
        t = u
    return runs


def stance_lock(points, mode="absolute", height=0.05, step_split=None, blend=4, min_span=4):
    """接地の区間で足を横に止める。返り値: 点、足ごとの区間 [[開始, 終了), …]、足ごとの接地の重み (n, 2)。

    absolute: 足の最下点が床から height 未満を接地とする。relative: 低い方の足より height 未満を接地とする
    （立っているだけの所で推定が体ごと上下しても、低い方の足を着いたままにする）。
    重みは、step_split を使うときは床に着いている（上下の判定）コマが 1、使わないときは止めた区間のコマが 1。
    """
    if mode not in CONTACT_MODES:
        raise ValueError(f"接地の決め方は {CONTACT_MODES} のどれか: {mode!r}")
    out = points.copy()
    n = len(points)
    frames = np.arange(n)
    lowest = np.minimum(*(points[:, list(foot), 2].min(axis=1) for foot in FEET))
    spans, weights = {}, []
    for side, (hip_i, knee_i, ankle_i, foot) in zip(SIDES, LEGS):
        ground = _contact(points, foot, lowest, mode, height)
        centre = points[:, list(foot), :2].mean(axis=1)
        target = np.full((n, 2), np.nan)
        kept = []
        for t, u in _runs(ground, centre, step_split):
            if u - t >= min_span:
                kept.append([int(t), int(u)])
                target[t:u] = np.median(centre[t:u], axis=0)
        spans[side] = kept
        in_span = np.zeros(n)
        for t, u in kept:
            in_span[t:u] = 1.0
        weights.append(ground.astype(float) if step_split is not None else in_span)
        offset = target - centre
        have = ~np.isnan(offset[:, 0])
        if not have.any():
            continue
        whole = np.stack([np.interp(frames, frames[have], offset[have, k]) for k in (0, 1)], axis=1)
        weight = np.zeros(n)
        for t, u in kept:
            for i in range(n):
                gap = t - i if i < t else (i - u + 1 if i >= u else 0)
                x = max(0.0, 1.0 - gap / (blend + 1))
                weight[i] = max(weight[i], x * x * (3 - 2 * x))
        out[:, list(foot), :2] = points[:, list(foot), :2] + (whole * weight[:, None])[:, None, :]
        for i in np.flatnonzero(weight > 0):
            hip, knee, ankle = points[i, hip_i], points[i, knee_i], points[i, ankle_i]
            out[i, knee_i] = leg_ik(out[i, hip_i], out[i, ankle_i], np.linalg.norm(knee - hip), np.linalg.norm(ankle - knee),
                                    knee - (hip + ankle) / 2)
    return out, spans, np.stack(weights, axis=1)


def prepare(keypoints, translation, depth_window=11, spike=0.03, pitch_deg=None, torso_back_deg=0.0,
            contact="absolute", contact_height=0.05, step_split=None, blend=4, min_span=4):
    """SAM 3D Body の点から、整えた点・接地の重み・報告を作る。"""
    keypoints, translation = np.asarray(keypoints, dtype=float), np.asarray(translation, dtype=float)
    if keypoints.ndim != 3 or keypoints.shape[1:] != (N_POINTS, 3):
        raise ValueError(f"点は (コマ, {N_POINTS}, 3) にしてください: {keypoints.shape}")
    if translation.shape != (len(keypoints), 3):
        raise ValueError(f"並進は (コマ, 3) にしてください: {translation.shape}")
    if len(keypoints) < 5:
        raise ValueError("コマが 5 より少ないと整えられません")
    if not np.isfinite(keypoints).all() or not np.isfinite(translation).all():
        raise ValueError("点か並進に NaN・無限大があります")
    raw = from_camera(keypoints, translation)
    smooth, replaced = clean(raw, depth_window, spike)
    levelled, measured, applied, floor = level(smooth, pitch_deg)
    upright = torso_back(levelled, torso_back_deg)
    locked, spans, stance = stance_lock(upright, contact, contact_height, step_split, blend, min_span)
    relative = locked - locked[:, list(HIPS)].mean(axis=1)[:, None]
    steps = {side: float(np.linalg.norm(np.diff(relative[:, ankle], axis=0), axis=1).max())
             for side, (_, _, ankle, _) in zip(SIDES, LEGS)}
    report = {
        "frames": int(len(keypoints)), "points": N_POINTS, "frame": "x right, y away from the camera, z up; metres; floor z = 0",
        "smoothing": {"spike_m": spike, "replaced_points": replaced, "window": 5, "depth_window": depth_window},
        "floor": {"measured_roll_deg": round(float(measured[0]), 3), "measured_pitch_deg": round(float(measured[1]), 3),
                  "applied_pitch_deg": round(float(applied[1]), 3), "pitch_overridden": pitch_deg is not None,
                  "lowered_m": round(floor, 5)},
        "torso_back_deg": float(torso_back_deg),
        "contact": {"mode": contact, "height_m": contact_height, "step_split_m": step_split, "blend_frames": blend,
                    "min_span_frames": min_span, "spans": spans,
                    "locked_frames": {side: int(sum(u - t for t, u in spans[side])) for side in SIDES}},
        "max_ankle_step_m": {side: round(value, 5) for side, value in steps.items()},
    }
    return locked, stance, report
