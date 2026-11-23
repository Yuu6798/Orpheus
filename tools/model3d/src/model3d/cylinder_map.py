"""メッシュの面を、軸に沿った距離と軸まわりの角度の地図にする。"""
import numpy as np

from .io import ToolError
from .texture import zbuffer


def coordinate_frame(axis, zero, quarter):
    def direction(value, name):
        longest = max(abs(component) for component in value)
        if longest == 0:
            raise ToolError(f"{name} は長さが 0 でない向きにしてください")
        return np.asarray(value, dtype=np.float64) / longest

    axis = direction(axis, "axis")
    axis /= np.linalg.norm(axis)
    zero = direction(zero, "zero_direction")
    zero = zero - axis * (zero @ axis)
    if np.linalg.norm(zero) < 1e-12:
        raise ToolError("zero_direction は axis と平行にできません")
    zero /= np.linalg.norm(zero)
    tangent = np.cross(axis, zero)
    side = float(tangent @ direction(quarter, "quarter_direction"))
    if abs(side) < 1e-12:
        raise ToolError("quarter_direction は 90 度側を決める、axis と zero_direction が作る面の外の向きにしてください")
    return axis, zero, tangent * np.sign(side)


def project(arrays, image, origin, frame, interval, radius, resolution, stride):
    xyz, triangles, loops, uv = (arrays[key] for key in ("co", "tris", "tri_loops", "uv"))
    if len(loops) != len(triangles):
        raise ToolError("tris と tri_loops の三角形の数が違います")
    axis, zero, tangent = frame
    relative = xyz - origin
    along, front, side = relative @ axis, relative @ zero, relative @ tangent
    radii = np.hypot(front, side)
    angle = np.degrees(np.arctan2(side, front)) % 360
    start, end = interval
    width, height = resolution
    step_m, step_deg = (end - start) / width, 360 / height
    included = (along > start) & (along < end) & (radii < radius)
    chosen = np.flatnonzero(included[triangles].all(axis=1))
    corners = triangles[chosen]
    angle_corners = angle[corners]
    seam = np.ptp(angle_corners, axis=1) > 180
    angle_corners += 360 * (seam[:, None] & (angle_corners < 180))
    grid_x = (along[corners] - start) / step_m
    grid_y = angle_corners / step_deg
    pixels_2d = np.concatenate([np.stack((grid_x, grid_y), axis=-1),
                                np.stack((grid_x[seam], grid_y[seam] - height), axis=-1)])
    copy_of = np.concatenate((chosen, chosen[seam]))
    points = pixels_2d.reshape(-1, 2)
    flattened_tris = np.arange(len(points)).reshape(-1, 3)
    depth, (winner, x, y, bary) = zbuffer(points, -radii[triangles[copy_of]].reshape(-1), flattened_tris, width, height)
    winning_tri = copy_of[winner]
    tex_coords = np.einsum("ij,ijk->ik", bary, uv[loops[winning_tri]])
    tex_h, tex_w = image.shape[:2]
    tx = np.minimum((np.clip(tex_coords[:, 0], 0, 1) * tex_w).astype(np.int32), tex_w - 1)
    ty = np.minimum((np.clip(1 - tex_coords[:, 1], 0, 1) * tex_h).astype(np.int32), tex_h - 1)
    mapped = np.zeros((height, width, 3), dtype=np.uint8)
    mapped[y, x] = image[ty, tx]
    u_map = np.full((height, width), -1, np.int32)
    v_map = np.full_like(u_map, -1)
    triangle_map = np.full_like(u_map, -1)
    u_map[y, x], v_map[y, x], triangle_map[y, x] = tx, ty, winning_tri
    radius_map = np.zeros_like(u_map, dtype=np.float32)
    radius_map[y, x] = -depth[y, x]
    covered = triangle_map >= 0
    profile = []
    for left in range(0, width, stride):
        right = min(left + stride, width)
        block = np.s_[:, left:right]
        present = covered[block]
        if not present.any():
            continue
        both = present[:, 1:] & present[:, :-1]
        jumps = np.hypot(np.diff(u_map[block], axis=1), np.diff(v_map[block], axis=1))[both]
        median_jump = float(np.median(jumps)) if len(jumps) else 0
        profile.append({"along_m": start + (left + right) / 2 * step_m, "radius_mm": float(radius_map[block][present].mean() * 1000),
                        "covered_pixels": int(present.sum()), "mm_per_texel": step_m * 1000 / median_jump if median_jump > 0 else None})
    lookup = {"px": u_map, "py": v_map, "r": radius_map, "tri": triangle_map, "start_m": start, "step_m": step_m, "step_deg": step_deg}
    report = {"schema_version": "model3d-cylinder-map/0.1", "resolution": [width, height], "triangles": int(len(chosen)),
              "covered_pixels": int(covered.sum()), "interval_m": list(interval), "step_m": step_m, "step_deg": step_deg, "profile": profile}
    return mapped, lookup, report
