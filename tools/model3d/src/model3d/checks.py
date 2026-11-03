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


def penetration(mesh, collider, directions=None, limit=50):
    """mesh の頂点のうち collider（閉じたメッシュ）の中にあるものの数・番号・深さ。"""
    require_same_frame(mesh, collider)
    report = collider.edge_report()
    if not report["closed"]:
        raise ToolError(f"衝突相手が閉じていません（境界辺 {report['boundary_edges']}・非多様体辺 {report['nonmanifold_edges']}）。"
                        "口の開いた筒では内外を決められないので、ふさいでから渡してください")
    is_inside = collide.inside(mesh.vertices, collider, directions)
    indices = np.flatnonzero(is_inside)
    depth = np.zeros(0)
    if len(indices):
        _, depth, _ = collide.closest_points(mesh.vertices[indices], collider)
    return {"check": "penetration", "pass": not len(indices), "vertices": mesh.vertex_count, "inside": int(len(indices)),
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
