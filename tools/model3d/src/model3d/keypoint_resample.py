"""等間隔の SAM 3D Body の点を、同じ尺で別のフレームレートにそろえる。"""
import math

import numpy as np


POINT_ARRAYS = {"pred_keypoints_3d": (70, 3), "pred_cam_t": (3,)}
CONTINUOUS = {**POINT_ARRAYS, "pred_keypoints_2d": (70, 2), "bbox": (4,)}
FLAGS = ("valid", "used_full_image_box")
STATIC = {"cam_int_fixed", "fps", "t", "frame_index"}


def _rate(value, label):
    value = np.asarray(value)
    if value.ndim != 0 or value.dtype.kind not in "fiu" or not math.isfinite(float(value)) or float(value) <= 0:
        raise ValueError(f"{label} は正の有限値にしてください")
    return float(value)


def _endpoint_slope(first, second):
    slope = (3 * first - second) / 2
    opposite = np.sign(first) != np.sign(second)
    slope = np.where(opposite & (np.abs(slope) > 3 * np.abs(first)), 3 * first, slope)
    return np.where(np.sign(slope) == np.sign(first), slope, 0)


def _pchip(values, left, right, fraction, linear):
    """入力の 1 コマを刻み幅 1 とする PCHIP（各成分の単調性を保つ三次エルミート補間）。"""
    if len(values) == 1:
        return values[left]
    values64 = values.astype(np.float64)
    delta = np.diff(values64, axis=0)
    slope = np.empty_like(values64)
    if len(values) == 2:
        slope[:] = delta[0]
    else:
        before, after = delta[:-1], delta[1:]
        same_direction = (before != 0) & (np.sign(before) == np.sign(after))
        inner = slope[1:-1]
        inner.fill(0)
        # 同符号の傾きだけ調和平均を使う。符号が変わる極値では傾きを 0 にする。
        small = np.minimum(np.abs(before), np.abs(after))
        large = np.maximum(np.abs(before), np.abs(after))
        ratio = np.divide(small, large, out=np.zeros_like(small), where=same_direction)
        inner[:] = np.where(same_direction, np.sign(before) * small * (2 / (1 + ratio)), 0)
        slope[0] = _endpoint_slope(delta[0], delta[1])
        slope[-1] = _endpoint_slope(delta[-1], delta[-2])
    u = fraction.reshape((-1,) + (1,) * (values.ndim - 1))
    result = ((1 + 2 * u) * (1 - u)**2 * values64[left]
              + u * (1 - u)**2 * slope[left]
              + u**2 * (3 - 2 * u) * values64[right]
              + u**2 * (u - 1) * slope[right])
    if linear.any():
        # 両端が有効でも、外側に無効な点があると三次補間の傾きに混ざる。その区間は両端だけを使う。
        weight = u[linear]
        result[linear] = (1 - weight) * values64[left[linear]] + weight * values64[right[linear]]
    if not np.isfinite(result).all():
        raise ValueError("補間した値が有限値の範囲を超えました")
    return result.astype(values.dtype) if values.dtype.kind == "f" else result


def resample(arrays, target_fps, source_fps=None):
    target = _rate(target_fps, "target_fps")
    source = _rate(arrays["fps"], "npz の fps") if "fps" in arrays else None
    if source_fps is not None:
        override = _rate(source_fps, "source_fps")
        if source is not None and not math.isclose(source, override, rel_tol=1e-6):
            raise ValueError("source_fps が npz の fps と違います")
        source = override
    if source is None:
        raise ValueError("npz に fps がありません。source_fps を指定してください")

    if missing := POINT_ARRAYS.keys() - arrays.keys():
        raise ValueError(f"npz に配列がありません: {sorted(missing)}")
    points = arrays["pred_keypoints_3d"]
    if not points.ndim or len(points) == 0:
        raise ValueError("pred_keypoints_3d に 1 コマ以上が要ります")
    frames = len(points)

    for name, shape in CONTINUOUS.items():
        if name in arrays:
            array = arrays[name]
            if array.shape != (frames, *shape) or array.dtype.kind not in "fiu" or not np.isfinite(array).all():
                raise ValueError(f"{name} は全コマ分の有限な数の配列 {(frames, *shape)} にしてください")
    for name in FLAGS:
        if name in arrays and (arrays[name].shape != (frames,) or arrays[name].dtype.kind != "b"):
            raise ValueError(f"{name} は全コマ分の bool 配列にしてください")

    start = 0.0
    if "t" in arrays:
        times = arrays["t"]
        if times.shape != (frames,) or times.dtype.kind not in "fiu" or not np.isfinite(times).all():
            raise ValueError("t は全コマ分の有限な数の配列にしてください")
        stamps = times.astype(np.float64)
        start = float(stamps[0])
        expected = start + np.arange(frames) / source
        precise = times if times.dtype.kind == "f" else stamps
        # float32 の時刻を差し引くと、後半ほど丸めの差が目立つ。等間隔の時刻そのものと比べる。
        tolerance = np.spacing(np.abs(precise)).astype(float) + 1e-8 / source
        if (not np.isfinite(expected).all() or np.any(np.diff(stamps) <= 0)
                or np.any(np.abs(stamps - expected) > tolerance + tolerance[0])):
            raise ValueError("t は fps と一致する等間隔の秒の配列にしてください")

    count = frames * (target / source)
    if not math.isfinite(count) or count >= np.iinfo(np.intp).max:
        raise ValueError("変換後のコマ数を扱えません")
    target_frames = max(1, math.floor(count + 0.5))
    index = np.arange(target_frames, dtype=np.int64)
    where = np.zeros(1) if target_frames == 1 else np.minimum(index * (source / target), frames - 1)
    # fps 比の丸めで、入力コマと同時刻のフラグを隣のコマから拾わない。
    rounded = np.rint(where)
    where = np.where(np.isclose(where, rounded, rtol=0, atol=1e-9), rounded, where)
    left = np.floor(where).astype(np.intp)
    right = np.ceil(where).astype(np.intp)
    fraction = where - left
    nearest = np.where(fraction < 0.5, left, right)
    linear = np.zeros(target_frames, dtype=bool)
    if "valid" in arrays:
        valid = arrays["valid"]
        neighbours_valid = valid[np.maximum(0, left - 1)] & valid[np.minimum(frames - 1, right + 1)]
        linear = (left != right) & valid[left] & valid[right] & ~neighbours_valid
    output = {}
    for name, array in arrays.items():
        if name in CONTINUOUS:
            output[name] = _pchip(array, left, right, fraction, linear)
        elif name == "valid":
            output[name] = array[left] & array[right]
        elif name == "used_full_image_box":
            output[name] = array[left] | array[right]
        elif name not in STATIC:
            output[name] = array[nearest] if array.ndim and len(array) == frames else array
    if "cam_int_fixed" in arrays:
        output["cam_int_fixed"] = arrays["cam_int_fixed"]
    timestamps = start + index.astype(np.float64) / target
    if not np.isfinite(timestamps).all():
        raise ValueError("変換後の時刻を扱えません")
    output.update(fps=np.float64(target), t=timestamps, frame_index=index)
    report = {"source_fps": source, "target_fps": target, "source_frames": frames, "target_frames": target_frames}
    if "valid" in arrays:
        report["invalid_frames"] = {"source": int((~arrays["valid"]).sum()), "target": int((~output["valid"]).sum())}
    if "used_full_image_box" in arrays:
        report["full_image_box_frames"] = {"source": int(arrays["used_full_image_box"].sum()),
                                           "target": int(output["used_full_image_box"].sum())}
    return output, report
