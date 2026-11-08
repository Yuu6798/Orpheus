"""検査（validation）: 構造・貫通・連続性。合否は数値の基準で決め、見た目の採否は人が決める。

未実施は合格ではない。材料が無い検査は `skipped` として理由を残す。
"""
import math

import numpy as np

from . import collide, semantic
from .io import ToolError


def _finite_matrix(matrix):
    try:
        array = np.asarray(matrix, dtype=np.float64)
    except (TypeError, ValueError):
        return False
    return array.shape == (4, 4) and bool(np.isfinite(array).all())


def structure(rig, semantic_map=None, required_bone_roles=None, tolerance=1e-3, mesh=None):
    """骨とウェイトを検査する。mesh を渡した場合は頂点数と記録した topology との整合も必須。"""
    failures, stats = [], {}
    bones = rig.get("bones", [])
    names = [b["name"] for b in bones]
    if len(set(names)) != len(names):
        failures.append("骨の名前が重なっています")
    by_name = {b["name"]: b for b in bones}
    for bone in bones:
        parent = bone.get("parent")
        if parent is not None and parent not in by_name:
            failures.append(f"骨 {bone['name']} の親 {parent} がありません")
        if "rest_matrix" in bone and not _finite_matrix(bone["rest_matrix"]):
            failures.append(f"骨 {bone['name']} のレスト行列が 4x4 の有限値ではありません")
        for key in ("head", "tail"):
            if key in bone and not all(isinstance(x, (int, float)) and math.isfinite(x) for x in bone[key]):
                failures.append(f"骨 {bone['name']} の {key} に非有限の値があります")
    for bone in bones:
        seen, current = set(), bone["name"]
        while current is not None:
            if current in seen:
                failures.append(f"骨の親子が循環しています: {bone['name']}")
                break
            seen.add(current)
            current = by_name.get(current, {}).get("parent")
    stats["bones"] = len(bones)
    roots = [b["name"] for b in bones if b.get("parent") is None]
    stats["roots"] = len(roots)
    if bones and not roots:
        failures.append("根の骨がありません")
    if required_bone_roles:
        if semantic_map is None:
            failures.append("必須骨の役割を確かめるには骨の対応表（SemanticMap）が要ります")
        else:
            present = semantic.roles_present(semantic_map)["bones"]
            mapped_names = set(semantic_map["bones"])
            missing_roles = [r for r in required_bone_roles if r not in present]
            if missing_roles:
                failures.append(f"必須の骨の役割がありません: {', '.join(missing_roles)}")
            absent = [n for n in mapped_names if n not in by_name]
            if absent:
                failures.append(f"対応表にある骨がリグにありません: {', '.join(sorted(absent))}")
            stats["required_roles"] = list(required_bone_roles)
    weights = rig.get("weights")
    if mesh is not None:
        recorded_topology = weights.get("topology_sha256") if weights else None
        stats["mesh_binding"] = {"vertex_count": mesh.vertex_count, "topology_sha256": mesh.topology_hash(),
                                 "rig_topology_sha256": recorded_topology}
        if weights is None:
            failures.append("メッシュとの整合を確かめるウェイトがありません")
        else:
            if weights["vertex_count"] != mesh.vertex_count:
                failures.append(f"ウェイトの頂点数 {weights['vertex_count']} とメッシュの頂点数 {mesh.vertex_count} が違います")
            if not recorded_topology:
                failures.append("ウェイトに対象メッシュの topology_sha256 が記録されていません")
            elif recorded_topology != mesh.topology_hash():
                failures.append("ウェイトの topology_sha256 とメッシュが違います")
    if weights is not None:
        count = int(weights["vertex_count"])
        total = np.zeros(count)
        touched = np.zeros(count, dtype=bool)
        bad_index = negative = non_finite = unknown_group = 0
        for group, pairs in weights["groups"].items():
            if group not in by_name:
                unknown_group += 1
            for vi, w in pairs:
                if isinstance(vi, bool) or not isinstance(vi, (int, float)) or not math.isfinite(vi) or vi != int(vi):
                    bad_index += 1          # 小数や非有限の頂点番号は、丸めて通さない
                    continue
                vi = int(vi)
                if vi < 0 or vi >= count:
                    bad_index += 1
                    continue
                if not math.isfinite(w):
                    non_finite += 1
                    continue
                if w < 0:
                    negative += 1
                total[vi] += w
                touched[vi] = True
        off = np.abs(total[touched] - 1.0) > tolerance
        untouched = int((~touched).sum())
        stats.update(weights={"vertex_count": count, "groups": len(weights["groups"]), "unweighted_vertices": untouched,
                              "unnormalized_vertices": int(off.sum()), "negative": negative, "non_finite": non_finite,
                              "bad_index": bad_index, "unknown_groups": unknown_group, "tolerance": tolerance})
        if bad_index:
            failures.append(f"ウェイトが無い頂点か整数でない頂点番号を指しています（{bad_index} 件）")
        if non_finite:
            failures.append(f"ウェイトに非有限の値があります（{non_finite} 件）")
        if negative:
            failures.append(f"ウェイトに負の値があります（{negative} 件）")
        if unknown_group:
            failures.append(f"リグに無い骨のウェイト群があります（{unknown_group} 件）")
        if untouched:
            failures.append(f"ウェイトの無い頂点があります（{untouched} 件）")
        if off.any():
            failures.append(f"ウェイトの合計が 1 でない頂点があります（{int(off.sum())} 件、許容 {tolerance}）")
    return {"check": "structure", "pass": not failures, "failures": failures, "stats": stats}


def require_same_frame(mesh, collider):
    """単位と軸の約束が同じときだけ座標を比べる。違えば止める（換算は mesh.transform の to_units / to_axes）。"""
    if mesh.units != collider.units:
        raise ToolError(f"単位が違います（mesh: {mesh.units}、collider: {collider.units}）。"
                        "mesh.transform の to_units でそろえてから渡してください（座標をそのまま比べない）")
    if mesh.axes != collider.axes:
        raise ToolError(f"軸の約束が違います（mesh: {mesh.axes}、collider: {collider.axes}）。"
                        "mesh.transform の to_axes でそろえてから渡してください（座標をそのまま比べない）")


require_same_units = require_same_frame


def penetration(mesh, collider, directions=None, limit=50, vertices=None):
    """mesh の頂点（vertices があればその番号の頂点だけ）のうち collider（閉じたメッシュ）の中にあるものの数・番号・深さ。"""
    require_same_frame(mesh, collider)
    report = collider.edge_report()
    if not report["closed"]:
        raise ToolError(f"衝突相手が閉じていません（境界辺 {report['boundary_edges']}・非多様体辺 {report['nonmanifold_edges']}）。"
                        "口の開いた筒では内外を決められないので、ふさいでから渡してください")
    checked = np.arange(mesh.vertex_count) if vertices is None else np.asarray(sorted(set(vertices)), dtype=np.int64)
    if len(checked) and (checked.min() < 0 or checked.max() >= mesh.vertex_count):
        raise ToolError("検査する頂点の番号がメッシュの範囲外です")
    indices = checked[collide.inside(mesh.vertices[checked], collider, directions)]
    depth = np.zeros(0)
    if len(indices):
        _, depth, _ = collide.closest_points(mesh.vertices[indices], collider)
    return {"check": "penetration", "pass": not len(indices), "vertices": mesh.vertex_count,
            **({"checked": int(len(checked))} if vertices is not None else {}), "inside": int(len(indices)),
            "inside_indices": indices[:limit].tolist(), "truncated": bool(len(indices) > limit),
            "max_depth": float(depth.max()) if len(depth) else 0.0, "mean_depth": float(depth.mean()) if len(depth) else 0.0,
            "topology_sha256": mesh.topology_hash(), "collider_topology_sha256": collider.topology_hash()}


def continuity(track, max_jump):
    """コマごとの点の列（{"frames": [[t, [[x,y,z], …]], …]}）で、前のコマからの最大移動が max_jump を超えるコマ。"""
    if isinstance(max_jump, bool) or not isinstance(max_jump, (int, float)) or not math.isfinite(max_jump) or max_jump <= 0:
        raise ToolError(f"max_jump は 0 より大きい有限の数にしてください: {max_jump!r}")
    frames = track.get("frames") if isinstance(track, dict) else None
    if not isinstance(frames, list) or len(frames) < 2:
        return {"check": "continuity", "pass": None, "status": "skipped", "reason": "2 コマ以上の点の列が要ります", "frames": 0,
                "jumps": [], "max_jump": max_jump}
    empty = [index for index, frame in enumerate(frames)
             if isinstance(frame, (list, tuple)) and len(frame) == 2 and isinstance(frame[1], (list, tuple)) and len(frame[1]) == 0]
    if empty:   # 点が無いコマは「動いていない」ではなく「見ていない」
        return {"check": "continuity", "pass": None, "status": "skipped",
                "reason": f"点の無いコマがあります（コマ {', '.join(str(i) for i in empty[:10])}）", "frames": len(frames),
                "jumps": [], "max_jump": max_jump}
    previous, jumps, worst, non_finite = None, [], 0.0, 0
    for index, frame in enumerate(frames):
        if not isinstance(frame, (list, tuple)) or len(frame) != 2 or isinstance(frame[0], bool) \
                or not isinstance(frame[0], (int, float)) or not isinstance(frame[1], (list, tuple)):
            raise ToolError(f"コマ {index} の形が違います（[t, [[x, y, z], …]] にしてください）")
        try:
            points = np.asarray(frame[1], dtype=np.float64).reshape(-1, 3)
        except (TypeError, ValueError) as error:
            raise ToolError(f"コマ {index} の点の列を読めません（{error}）") from error
        t = frame[0]
        if not np.isfinite(points).all():
            non_finite += 1
        if previous is not None:
            if len(previous[1]) != len(points):
                raise ToolError(f"コマ {index} の点の数が前のコマと違います（{len(points)} と {len(previous[1])}）")
            move = float(np.linalg.norm(points - previous[1], axis=1).max()) if len(points) else 0.0
            worst = max(worst, move)
            if move > max_jump:
                jumps.append({"frame": index, "t": t, "max_move": move})
        previous = (t, points)
    passed = not jumps and not non_finite
    return {"check": "continuity", "pass": passed, "status": "done", "frames": len(frames), "max_jump": max_jump,
            "worst_move": worst, "jumps": jumps[:50], "non_finite_frames": non_finite}


MOTION_THRESHOLDS = ("max_swivel_deg", "max_skate_fraction", "max_floor_depth_m", "max_step_m", "max_length_spread")


def _worst(values, frames, top):
    order = np.argsort(-values)[:top]
    return [[int(frames[i]), round(float(values[i]), 4)] for i in order]


def leg_swivel(points, hip, knee, ankle, toes, heel):
    """股関節→足首の軸のまわりで、膝の出ている向きと足の向き（かかと→つま先）の角度（度）と、膝の曲がり（度）。

    向きが決まらないコマ（膝が軸の上・足が軸に沿う）は角度を NaN にする。
    """
    h, k, a = points[:, hip], points[:, knee], points[:, ankle]
    axis = a - h
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)
    knee_out = (k - h) - ((k - h) * axis).sum(1, keepdims=True) * axis
    foot = points[:, list(toes)].mean(1) - points[:, heel]
    foot = foot - (foot * axis).sum(1, keepdims=True) * axis
    lengths = np.linalg.norm(knee_out, axis=1) * np.linalg.norm(foot, axis=1)
    cosine = np.where(lengths > 1e-12, (knee_out * foot).sum(1) / np.maximum(lengths, 1e-12), np.nan)
    angle = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
    u, v = h - k, a - k
    bend = 180 - np.degrees(np.arccos(np.clip((u * v).sum(1) / (np.linalg.norm(u, axis=1) * np.linalg.norm(v, axis=1)), -1, 1)))
    return angle, bend


def motion(points, stance=None, fps=30.0, bend_min_deg=25.0, contact_height=0.03, skate_speed=0.2, floor_tolerance=0.005,
           thresholds=None, frames=None, top=8):
    """整えた 70 点（x 右・y 奥・z 上、m、床 z = 0）の動きを数で測る。好みは判定しない。

    測るもの: 膝とつま先の向きの食い違い（膝を bend_min_deg 以上曲げたコマ）、接地中の足の滑り（水平の速さ > skate_speed）、
    接地中の床へのめり込み、腰から見た足首・手首・鼻の 1 コマの移動、骨の長さのぶれ（(p95 − p5) / 中央値）。
    接地は stance（motion.prepare_keypoints の重み、> 0.5）、無ければ足の最下点が contact_height 未満。
    thresholds（MOTION_THRESHOLDS の一部）を渡したときだけ合否を出し、無ければ pass は None（測っただけ）。
    """
    from . import motion as mo
    points = np.asarray(points, dtype=np.float64)
    if points.ndim != 3 or points.shape[1:] != (mo.N_POINTS, 3):
        raise ToolError(f"点は (コマ, {mo.N_POINTS}, 3) にしてください: {points.shape}")
    if not np.isfinite(points).all():
        raise ToolError("点に NaN・無限大があります")
    n_all = len(points)
    start, end = (0, n_all) if frames is None else frames
    if not 0 <= start < end <= n_all:
        raise ToolError(f"frames は 0 以上 {n_all} 以下の [開始, 終了) にしてください: {frames}")
    if stance is not None:
        stance = np.asarray(stance, dtype=np.float64)
        if stance.shape != (n_all, 2):
            raise ToolError(f"stance は (コマ, 2) にしてください: {stance.shape}")
        stance = stance[start:end]
    if end - start < 2:
        raise ToolError("2 コマ以上が要ります")
    whole_hip = points[:, list(mo.HIPS)].mean(axis=1)

    def per_frame(values):
        """1 つ前のコマからの移動（区間で切る前に数え、区間の最初のコマも前のコマと比べる）。クリップの最初のコマは NaN。"""
        return np.r_[np.nan, np.linalg.norm(np.diff(values, axis=0), axis=-1)][start:end]

    whole = points
    points = points[start:end]
    index = np.arange(start, end)
    report = {"check": "motion", "status": "done", "frames": [int(start), int(end)], "fps": fps,
              "settings": {"bend_min_deg": bend_min_deg, "contact_height_m": contact_height, "skate_speed_mps": skate_speed,
                           "floor_tolerance_m": floor_tolerance, "contact_from": "stance" if stance is not None else "height"},
              "swivel": {}, "skate": {}, "floor": {}, "steps": {}, "segments": {}}
    # しきい値と比べるのは丸める前の値。報告に書く数だけを丸める（丸めた値で比べると、少しだけ超えた値を合格にしてしまう）
    raw = {"swivel": {}, "skate": {}, "floor": {}, "steps": {}, "spread": {}}
    for side, (h, k, a, toes, heel), (_, _, _, foot) in zip(mo.SIDES, mo.SWIVEL_LEGS, mo.LEGS):
        angle, bend = leg_swivel(points, h, k, a, toes, heel)
        bent = (bend >= bend_min_deg) & np.isfinite(angle)
        chosen = angle[bent]
        raw["swivel"][side] = (chosen, index[bent])
        report["swivel"][side] = {
            "bent_frames": int(bent.sum()), "undefined_frames": int(((bend >= bend_min_deg) & ~np.isfinite(angle)).sum()),
            "median_deg": round(float(np.median(chosen)), 2) if len(chosen) else None,
            "p90_deg": round(float(np.percentile(chosen, 90)), 2) if len(chosen) else None,
            "max_deg": round(float(chosen.max()), 2) if len(chosen) else None,
            "worst": _worst(chosen, index[bent], top) if len(chosen) else []}
        lowest = points[:, list(foot), 2].min(axis=1)
        contact = stance[:, mo.SIDES.index(side)] > 0.5 if stance is not None else lowest < contact_height
        speed = per_frame(whole[:, list(foot), :2].mean(axis=1)) * fps
        measured = contact & np.isfinite(speed)      # 前のコマが無いコマ（クリップの最初）は速さを測っていない
        skating = measured & (speed > skate_speed)
        raw["skate"][side] = float(skating.sum() / max(measured.sum(), 1))
        raw["floor"][side] = float(max(0.0, -lowest[contact].min())) if contact.any() else 0.0
        report["skate"][side] = {
            "contact_frames": int(contact.sum()), "measured_contact_frames": int(measured.sum()),
            "skating_frames": int(skating.sum()), "fraction": round(raw["skate"][side], 4),
            "max_contact_speed_mps": round(float(speed[measured].max()), 4) if measured.any() else None,
            "worst": _worst(speed[measured], index[measured], top) if measured.any() else []}
        below = contact & (lowest < -floor_tolerance)
        report["floor"][side] = {
            "depth_m": round(float(max(0.0, -lowest[contact].min())), 4) if contact.any() else 0.0,
            "frames_below": int(below.sum()),
            "max_height_in_contact_m": round(float(lowest[contact].max()), 4) if contact.any() else None,
            "worst": _worst(-lowest[below], index[below], top)}
    for name, joint in mo.TRACKED.items():
        step = per_frame(whole[:, joint] - whole_hip)
        raw["steps"][name] = float(np.nanmax(step))
        report["steps"][name] = {"max_m": round(raw["steps"][name], 4), "frame": int(index[int(np.nanargmax(step))])}
    centre_step = per_frame(whole_hip)
    report["steps"]["hip_centre"] = {"max_m": round(float(np.nanmax(centre_step)), 4),
                                     "frame": int(index[int(np.nanargmax(centre_step))])}
    for name, (a, b) in mo.SEGMENTS.items():
        length = np.linalg.norm(points[:, b] - points[:, a], axis=1)
        middle = float(np.median(length))
        raw["spread"][name] = float((np.percentile(length, 95) - np.percentile(length, 5)) / middle) if middle > 0 else None
        report["segments"][name] = {
            "median_m": round(middle, 4), "min_m": round(float(length.min()), 4), "max_m": round(float(length.max()), 4),
            "spread": None if raw["spread"][name] is None else round(raw["spread"][name], 4)}
    thresholds = {k: v for k, v in (thresholds or {}).items() if v is not None}
    unknown = set(thresholds) - set(MOTION_THRESHOLDS)
    if unknown:
        raise ToolError(f"知らないしきい値: {sorted(unknown)}（使えるのは {list(MOTION_THRESHOLDS)}）")
    report["thresholds"] = thresholds
    if not thresholds:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
        return report
    failures = []
    for side in mo.SIDES:
        limit = thresholds.get("max_swivel_deg")
        angles, at = raw["swivel"][side]
        if limit is not None and len(angles) and angles.max() > limit:
            over = angles > limit
            examples = _worst(angles[over], at[over], 3)
            failures.append(f"{side}: 膝とつま先の食い違いが {limit}° を超えるコマが {int(over.sum())} あります"
                            f"（最大 {angles.max():.6g}°、例 {examples}）")
        limit = thresholds.get("max_skate_fraction")
        if limit is not None and raw["skate"][side] > limit:
            failures.append(f"{side}: 接地中の足の滑りが {raw['skate'][side]:.6g}（しきい値 {limit}）")
        limit = thresholds.get("max_floor_depth_m")
        if limit is not None and raw["floor"][side] > limit:
            failures.append(f"{side}: 接地中に床へ {raw['floor'][side]:.6g} m めり込んでいます（しきい値 {limit} m）")
    limit = thresholds.get("max_step_m")
    if limit is not None:
        for name, value in raw["steps"].items():
            if value > limit:
                failures.append(f"{name}: コマ {report['steps'][name]['frame']} で 1 コマに {value:.6g} m 動いています（しきい値 {limit} m）")
    limit = thresholds.get("max_length_spread")
    if limit is not None:
        for name, value in raw["spread"].items():
            if value is not None and value > limit:
                failures.append(f"{name}: 骨の長さのぶれが {value:.6g}（しきい値 {limit}）")
    report.update({"pass": not failures, "failures": failures})
    return report


UV_STRETCH_THRESHOLDS = ("max_over", "max_degenerate")


def uv_stretch(mesh, width, height, threshold=8.0, thresholds=None):
    """uv の伸び（三角形ごとの、テクスチャの画素での周長 / 3D の周長を、その中央値で割った値）を測る。uv が島をまたいで
    引き伸ばされた三角形ほど大きい（texture.project_reference の max_stretch と同じ値）。3D で潰れた三角形は数えて外す。
    しきい値（max_over: threshold を超える三角形の数、max_degenerate: 潰れた三角形の数）を渡したときだけ合否を出す。"""
    from . import texture
    if mesh.uv is None:
        return {"check": "uv_stretch", "pass": None, "status": "skipped", "reason": "メッシュに uv がありません",
                "triangles": 0}
    triangles, _ = mesh.triangles()
    corners = texture.texel_coordinates(mesh.uv[triangles], width, height)
    ratio = texture.stretch(corners, mesh.vertices[triangles])
    finite = np.isfinite(ratio)
    extent = np.ptp(corners, axis=1).max(axis=1)
    usable = ratio[finite]
    report = {"check": "uv_stretch", "status": "done", "texture_size": [int(width), int(height)], "triangles": int(len(ratio)),
              "degenerate": int((~finite).sum()), "threshold": float(threshold), "over": int((usable > threshold).sum()),
              "ratio_percentiles": (dict(zip(("p50", "p99", "p99_9", "max"),
                                             [round(float(v), 3) for v in np.percentile(usable, [50, 99, 99.9, 100])]))
                                    if len(usable) else None),
              "uv_extent_px": (dict(zip(("p99", "max"), [round(float(v), 2) for v in np.percentile(extent, [99, 100])]))
                               if len(extent) else None)}
    thresholds = thresholds or {}
    if not thresholds:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
        return report
    failures = []
    if "max_over" in thresholds and report["over"] > thresholds["max_over"]:
        failures.append(f"伸びが {threshold:g} を超える三角形が {report['over']} 個（上限 {thresholds['max_over']:g}）")
    if "max_degenerate" in thresholds and report["degenerate"] > thresholds["max_degenerate"]:
        failures.append(f"3D で潰れた三角形が {report['degenerate']} 個（上限 {thresholds['max_degenerate']:g}）")
    report.update({"pass": not failures, "failures": failures})
    return report


FRAME_THRESHOLDS = ("max_differing_pixels", "max_channel_diff", "max_mean_abs")
FRAME_BANDS = (0, 2, 8, 16, 64, 128)


def frame_files(source):
    """コマのフォルダ（f<T>.png）か、描いたコマの一覧（model3d-frames/0.1 の JSON）から {T: ファイル}。"""
    import re
    from pathlib import Path
    source = Path(source)
    if source.is_dir():
        found = {}
        for path in source.iterdir():
            m = re.fullmatch(r"f(\d+)\.png", path.name)
            if m and path.is_file():
                found[int(m.group(1))] = path
        return found
    from . import contracts
    manifest = contracts.load("frames", source)
    directory = Path(manifest["directory"])
    return {fr["T"]: directory / fr["file"] for fr in manifest["frames"]}


def _frame_arrays(path):
    from PIL import Image
    with Image.open(path) as image:
        rgb = np.asarray(image.convert("RGB"), dtype=np.int16)       # 色はリポジトリ外の試作と同じ読み方（透けは別に比べる）
        alpha = np.asarray(image.convert("RGBA").getchannel("A"), dtype=np.int16)
    return rgb, alpha


def compare_frames(left, right, thresholds=None, top=5, bands=FRAME_BANDS):
    """2 つのコマの並び（{T: ファイル}）を、同じ T どうしで画素で比べる。ファイルが同じ（sha256）なら開かずに同じとする。

    コマごとに、違う画素の数（色か透けのどれかが違う）・いちばん大きい値の差（0〜255）・色の差の平均（R・G・B の 3 つ。
    リポジトリ外の試作の比べと同じ値）・透けの違う画素の数。片方にしか無いコマと、大きさの違うコマも並べる。
    左を基準に比べる: しきい値（コマごとの max_differing_pixels・max_channel_diff・max_mean_abs）を渡したときだけ合否を出し、
    そのときは右に無いコマと大きさの違うコマも不合格にする（右にしか無いコマは並べるだけ）。"""
    from .io import sha256
    from PIL import Image
    rows, missing_right, size_mismatch = [], [], []
    identical = pixels = differing_total = 0
    over = dict.fromkeys(bands, 0)
    for T in sorted(left):
        if T not in right:
            missing_right.append(T)
            continue
        if sha256(left[T]) == sha256(right[T]):
            with Image.open(left[T]) as image:
                width, height = image.size
            identical += 1
            pixels += width * height
            rows.append({"T": T, "identical": True, "same_file": True, "differing_pixels": 0, "max_channel_diff": 0,
                         "mean_abs_diff": 0.0, "alpha_differing_pixels": 0})
            continue
        (a, a_alpha), (b, b_alpha) = _frame_arrays(left[T]), _frame_arrays(right[T])
        if a.shape != b.shape:
            size_mismatch.append(T)
            rows.append({"T": T, "size_left": [a.shape[1], a.shape[0]], "size_right": [b.shape[1], b.shape[0]]})
            continue
        diff = np.abs(a - b)
        alpha = np.abs(a_alpha - b_alpha)
        worst = np.maximum(diff.max(axis=2), alpha)
        differing = int((worst > 0).sum())
        identical += differing == 0
        pixels += a.shape[0] * a.shape[1]
        differing_total += differing
        for band in bands:
            over[band] += int((worst > band).sum())
        rows.append({"T": T, "identical": differing == 0, "same_file": False, "differing_pixels": differing,
                     "max_channel_diff": int(max(diff.max(), alpha.max())), "mean_abs_diff": float(diff.mean()),
                     "alpha_differing_pixels": int((alpha > 0).sum())})
    missing_left = sorted(T for T in right if T not in left)
    report = {"check": "frames", "status": "done", "frames": len(left), "compared": sum(1 for r in rows if "differing_pixels" in r),
              "identical_frames": int(identical), "missing_on_right": missing_right, "missing_on_left": missing_left,
              "size_mismatch": size_mismatch, "differing_pixels": differing_total, "pixels": pixels,
              "max_channel_diff": max((r.get("max_channel_diff", 0) for r in rows), default=0),
              "pixels_over": {f">{band}": count for band, count in over.items()},
              "worst_frames": sorted((r for r in rows if "differing_pixels" in r), key=lambda r: -r["differing_pixels"])[:top],
              "rows": rows}
    thresholds = thresholds or {}
    if not thresholds:
        report.update({"pass": None, "reason": "しきい値が無いので測っただけ（report_only で測るだけにできる）", "failures": []})
        return report
    failures = []
    if missing_right:                                    # 左を基準に比べる（右にしか無いコマは並べるだけ。一部のコマと全部のコマを比べられる）
        failures.append(f"右に無いコマがあります（T: {missing_right[:10]}）")
    if size_mismatch:
        failures.append(f"大きさの違うコマがあります（T: {size_mismatch[:10]}）")
    limits = {"max_differing_pixels": "differing_pixels", "max_channel_diff": "max_channel_diff", "max_mean_abs": "mean_abs_diff"}
    for key, field in limits.items():
        if key in thresholds:
            beyond = [r["T"] for r in rows if field in r and r[field] > thresholds[key]]
            if beyond:
                failures.append(f"{field} が {thresholds[key]:g} を超えるコマが {len(beyond)} 枚（最初の T: {beyond[:10]}）")
    report.update({"pass": not failures, "failures": failures})
    return report


def frame_view(left_path, right_path, out, amplify=8):
    """1 組のコマを [左 | 右 | 差（いちばん大きい値の差 × amplify）] に並べた PNG（リポジトリ外の試作の見比べと同じ並び）。"""
    from PIL import Image
    a, a_alpha = _frame_arrays(left_path)
    b, b_alpha = _frame_arrays(right_path)
    if a.shape != b.shape:
        raise ToolError(f"大きさの違うコマは並べられません: {left_path} と {right_path}")
    worst = np.maximum(np.abs(a - b).max(axis=2), np.abs(a_alpha - b_alpha))
    heat = np.clip(worst * amplify, 0, 255).astype(np.uint8)
    view = np.concatenate([a.astype(np.uint8), b.astype(np.uint8), np.stack([heat] * 3, axis=2)], axis=1)
    Image.fromarray(view).save(out)
    return {"left": str(left_path), "right": str(right_path), "amplify": amplify,
            "fraction_over": {f">{t}": round(float((worst > t).mean()), 6) for t in FRAME_BANDS}}
