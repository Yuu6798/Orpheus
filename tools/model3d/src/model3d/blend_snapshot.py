"""2 つの .blend の書き出し（blender_adapter/snapshot_blend.py の npz と JSON）を、numpy で比べる（Blender は要らない）。

リポジトリ外の試作（作り直したキャラと採用版の比べ）と同じ項目: 物体の有無・シーン・メッシュごとの頂点（物体の中の座標）・面のつなぎ・
面の角の数・uv・属性・置き場所の行列、骨の名前・静止の行列・姿勢の行列、変形した世界の形（sha256 と等間隔の頂点）。
全部が同じかを exact に、違いの大きさを max_* に出す。tolerance を渡すと、座標と行列の違いがその中かで合否を出す
（渡していない項目は同じであること）。"""
import json
from pathlib import Path

import numpy as np

from .io import ToolError

TOLERANCE_KEYS = ("coordinate", "matrix", "value")


def load(npz, meta):
    """書き出しを読む: (JSON の辞書, npz の配列の辞書)。pickle は読まない。"""
    with open(meta, encoding="utf-8") as stream:
        info = json.load(stream)
    if info.get("schema_version") != "model3d-blend-snapshot/0.1":
        raise ToolError(f".blend の書き出しの形式が違います: {meta}")
    with np.load(npz, allow_pickle=False) as data:
        arrays = {key: data[key] for key in data.files}
    return info, arrays


def _max_error(a, b):
    """違いの最大。形が違えば None。非有限の値（NaN・無限大）があっても None（数で測れず、報告の JSON にも書けない。
    compare が non_finite に並べて不合格にする。NaN のままだと、tolerance の比べ NaN > 上限 が偽で通ってしまう）。"""
    if a.shape != b.shape:
        return None
    if not a.size:
        return 0.0
    diff = np.abs(a.astype(np.float64) - b.astype(np.float64))
    return float(diff.max()) if np.isfinite(diff).all() else None


def _non_finite(*arrays):
    return any(x is not None and x.size and not np.isfinite(np.asarray(x, dtype=np.float64)).all() for x in arrays)


def _same(a, b):
    return a is not None and b is not None and a.shape == b.shape and a.dtype == b.dtype and bool(np.array_equal(a, b))


def compare(left, right, tolerance=None, requested=None, requested_attributes=None):
    """2 つの書き出し（load の戻り）を比べた報告。tolerance（{"coordinate", "matrix", "value"}）を渡せば、その中で合否を出す
    （渡していない項目は同じであること）。渡さなければ、全部が同じ（exact）かで合否を出す。requested（名前を挙げて比べる
    メッシュの並び）と requested_attributes（名前を挙げて比べる属性の並び）を渡せば、その名前が両方の書き出しにあるかも確かめる
    （無い名前は黙って外さず、形のつくりの違いにする。属性は、選んだメッシュのどれかにあればよい）。非有限の値（NaN・無限大）が
    あれば、違いを測らずに不合格にする。"""
    (lmeta, larr), (rmeta, rarr) = left, right
    lobjects = {o["name"]: o for o in lmeta["objects"]}
    robjects = {o["name"]: o for o in rmeta["objects"]}
    report = {"check": "blend_match", "missing_objects": sorted(set(lobjects) - set(robjects)),
              "additional_objects": sorted(set(robjects) - set(lobjects)),
              "scene_identical": lmeta["scene"] == rmeta["scene"], "scene": {"left": lmeta["scene"], "right": rmeta["scene"]},
              "objects": []}
    unfound = None
    if requested is not None:
        unfound = {"left": sorted(set(requested) - set(lobjects)), "right": sorted(set(requested) - set(robjects))}
        report["requested_objects_missing"] = unfound
    attributes_unfound = None
    if requested_attributes is not None:                   # 打ち間違いや、書き出せない型の属性（どのメッシュにも無い名前）
        seen = {side: {a for o in meta["objects"] for a in o["attributes"]} for side, meta in (("left", lmeta), ("right", rmeta))}
        attributes_unfound = {side: sorted(set(requested_attributes) - names) for side, names in seen.items()}
        report["requested_attributes_missing"] = attributes_unfound
    non_finite = []
    for name in sorted(set(lobjects) & set(robjects)):
        lo, ro = lobjects[name], robjects[name]
        lk, rk = lo["key"], ro["key"]
        co_l, co_r = larr[f"{lk}_co"], rarr[f"{rk}_co"]
        uv_l, uv_r = larr.get(f"{lk}_uv"), rarr.get(f"{rk}_uv")
        if _non_finite(co_l, co_r, uv_l, uv_r, larr[f"{lk}_matrix"], rarr[f"{rk}_matrix"],
                       *(larr[i["key"]] for i in lo["attributes"].values()), *(rarr[i["key"]] for i in ro["attributes"].values())):
            non_finite.append(name)
        attributes = {}
        for attr in sorted(set(lo["attributes"]) | set(ro["attributes"])):
            if attr not in lo["attributes"] or attr not in ro["attributes"]:
                attributes[attr] = {"identical": False, "missing": "left" if attr not in lo["attributes"] else "right", "max_error": None}
                continue
            la, ra = lo["attributes"][attr], ro["attributes"][attr]
            a, b = larr[la["key"]], rarr[ra["key"]]
            # 置き場所（domain）と型（type）も比べる。違えば値の幅では許さない（形のつくりの違い。頂点と面の数が同じなら、
            # 置き場所が変わっても配列の形は同じになる）
            same_kind = la.get("domain") == ra.get("domain") and la.get("type") == ra.get("type")
            attributes[attr] = {"identical": same_kind and _same(a, b), "missing": None,
                                "max_error": _max_error(a, b) if same_kind else None, "kind_identical": same_kind}
            if not same_kind:
                attributes[attr].update(domain=[la.get("domain"), ra.get("domain")], type=[la.get("type"), ra.get("type")])
        matrix_error = _max_error(larr[f"{lk}_matrix"], rarr[f"{rk}_matrix"])
        row = {"name": name, "left_vertices": int(len(co_l)), "right_vertices": int(len(co_r)), "vertices_exact": _same(co_l, co_r),
               "max_coordinate_error": _max_error(co_l, co_r),
               "polygon_count_identical": lo["polygons"] == ro["polygons"],
               "loop_vertices_identical": _same(larr[f"{lk}_loops"], rarr[f"{rk}_loops"]),
               "polygon_sizes_identical": _same(larr[f"{lk}_sizes"], rarr[f"{rk}_sizes"]),
               "uv_identical": (uv_l is None and uv_r is None) or _same(uv_l, uv_r),
               "uv_max_error": _max_error(uv_l, uv_r) if uv_l is not None and uv_r is not None else None,
               "attributes": attributes, "attributes_identical": all(v["identical"] for v in attributes.values()),
               "matrix_world_identical": matrix_error == 0.0, "matrix_world_max_error": matrix_error}
        report["objects"].append(row)
    report["bone_names_identical"] = lmeta["bones"] == rmeta["bones"]
    report["bones"] = len(rmeta["bones"])
    report["pose_frames_identical"] = lmeta["pose_frames"] == rmeta["pose_frames"]
    report["pose_frames"] = rmeta["pose_frames"]
    if report["bone_names_identical"] and lmeta["bones"]:
        report["rest_matrix_max_abs_error"] = _max_error(larr["rest"], rarr["rest"])
        report["pose_matrix_max_abs_error"] = (_max_error(larr["poses"], rarr["poses"]) if report["pose_frames_identical"] else None)
        if _non_finite(larr.get("rest"), rarr.get("rest"), larr.get("poses"), rarr.get("poses")):
            non_finite.append("bones")
    else:
        report["rest_matrix_max_abs_error"] = report["pose_matrix_max_abs_error"] = None
    evaluations = []
    for frame in sorted(set(lmeta["evaluated"]) & set(rmeta["evaluated"]), key=int):
        le, re_ = lmeta["evaluated"][frame], rmeta["evaluated"][frame]
        for name in sorted(set(le) & set(re_)):
            a, b = larr[le[name]["key"]], rarr[re_[name]["key"]]
            if _non_finite(a, b):
                non_finite.append(f"{name}（コマ {frame}）")
            evaluations.append({"frame": int(frame), "object": name, "vertex_count_identical": le[name]["vertices"] == re_[name]["vertices"],
                                "all_evaluated_vertices_exact": le[name]["sha256"] == re_[name]["sha256"],
                                "sampled_max_coordinate_error": _max_error(a, b)})
    report["evaluated_meshes"] = evaluations
    report["non_finite"] = non_finite
    structure = (not report["missing_objects"] and not report["additional_objects"] and report["scene_identical"]
                 and report["bone_names_identical"] and report["pose_frames_identical"]
                 and all(o["left_vertices"] == o["right_vertices"] and o["polygon_count_identical"] and o["loop_vertices_identical"]
                         and o["polygon_sizes_identical"] for o in report["objects"])
                 and all(info.get("kind_identical", True) for o in report["objects"] for info in o["attributes"].values())
                 and not (unfound and (unfound["left"] or unfound["right"]))
                 and not (attributes_unfound and (attributes_unfound["left"] or attributes_unfound["right"]))
                 and all(e["vertex_count_identical"] for e in evaluations))
    geometry = all(o["vertices_exact"] and o["uv_identical"] and o["attributes_identical"] and o["matrix_world_identical"]
                   for o in report["objects"])
    report["exact"] = bool(structure and geometry and not non_finite and report["rest_matrix_max_abs_error"] in (0.0, None)
                           and report["pose_matrix_max_abs_error"] in (0.0, None)
                           and all(e["all_evaluated_vertices_exact"] for e in evaluations))
    report["tolerance"] = tolerance
    failures = []
    if not structure:
        failures.append("形のつくり（物体・シーン・頂点と面の数とつなぎ・属性の置き場所と型・骨の名前・姿勢のコマ）が同じではありません")
    if unfound and (unfound["left"] or unfound["right"]):
        failures.append(f"名前を挙げたメッシュがありません（左に無い: {unfound['left'][:10]}、右に無い: {unfound['right'][:10]}）")
    if attributes_unfound and (attributes_unfound["left"] or attributes_unfound["right"]):
        failures.append(f"名前を挙げた属性が、選んだメッシュのどれにもありません（左: {attributes_unfound['left'][:10]}、"
                        f"右: {attributes_unfound['right'][:10]}。打ち間違いか、書き出せない型）")
    if non_finite:                                         # tolerance でも許さない（違いの大きさを測れない）
        failures.append(f"非有限の値（NaN・無限大）があり、違いの大きさを測れません: {non_finite[:10]}")
    if tolerance is None:
        if structure and not report["exact"]:
            failures.append("全部が同じではありません（違いの大きさは max_* を見る。許す幅は tolerance で渡す）")
    else:
        def beyond(value, limit):
            return value is not None and limit is not None and value > limit
        # 渡していない項目は、同じであることを求める（uv と属性の value と同じ扱い。渡し忘れた項目を上限なしで通さない）。
        # 違いの大きさが None（形が違う・非有限）は、形のつくりか non_finite で不合格にしてあるので、ここでは重ねない
        coordinate, matrix, value = (tolerance.get(k) for k in TOLERANCE_KEYS)
        omitted = "を渡していないので、同じであることを求めます"
        for o in report["objects"]:
            if beyond(o["max_coordinate_error"], coordinate):
                failures.append(f"{o['name']}: 頂点の違い {o['max_coordinate_error']:.3g} が coordinate を超えます")
            elif coordinate is None and o["max_coordinate_error"] is not None and not o["vertices_exact"]:
                failures.append(f"{o['name']}: 頂点が同じではありません（違い {o['max_coordinate_error']:.3g}。coordinate {omitted}）")
            if beyond(o["matrix_world_max_error"], matrix):
                failures.append(f"{o['name']}: 置き場所の行列の違い {o['matrix_world_max_error']:.3g} が matrix を超えます")
            elif matrix is None and o["matrix_world_max_error"] is not None and not o["matrix_world_identical"]:
                failures.append(f"{o['name']}: 置き場所の行列が同じではありません（違い {o['matrix_world_max_error']:.3g}。matrix {omitted}）")
            if not o["uv_identical"] and (value is None or beyond(o["uv_max_error"], value) or o["uv_max_error"] is None):
                failures.append(f"{o['name']}: uv が違います")
            for attr, info in o["attributes"].items():
                if not info.get("kind_identical", True):
                    failures.append(f"{o['name']}: 属性 {attr} の置き場所か型が違います（置き場所 {info['domain']}、型 {info['type']}）")
                elif not info["identical"] and (value is None or info["max_error"] is None or beyond(info["max_error"], value)):
                    failures.append(f"{o['name']}: 属性 {attr} が違います")
        for key in ("rest_matrix_max_abs_error", "pose_matrix_max_abs_error"):
            if beyond(report[key], matrix):
                failures.append(f"骨の {key} {report[key]:.3g} が matrix を超えます")
            elif matrix is None and report[key] not in (0.0, None):
                failures.append(f"骨の {key} {report[key]:.3g} が 0 ではありません（matrix {omitted}）")
        for e in evaluations:
            if beyond(e["sampled_max_coordinate_error"], coordinate):
                failures.append(f"コマ {e['frame']} の {e['object']}: 変形した形の違い {e['sampled_max_coordinate_error']:.3g} が coordinate を超えます")
            elif (coordinate is None and e["vertex_count_identical"] and e["sampled_max_coordinate_error"] is not None
                  and not e["all_evaluated_vertices_exact"]):                 # exact と同じく、頂点の全部（sha256）で比べる
                failures.append(f"コマ {e['frame']} の {e['object']}: 変形した形が同じではありません（coordinate {omitted}）")
    report.update({"pass": not failures, "failures": failures})
    return report


def check_tolerance(value):
    """tolerance の形を確かめる: {"coordinate": 数, "matrix": 数, "value": 数}（どれも任意、0 以上。渡していない項目は、
    compare が同じであることを求める）。"""
    if value is None:
        return None
    if not isinstance(value, dict) or not value or set(value) - set(TOLERANCE_KEYS):
        raise ToolError(f"tolerance は {{{', '.join(TOLERANCE_KEYS)}}} のうちの数にしてください: {value!r}")
    for key, item in value.items():
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item < 0:
            raise ToolError(f"tolerance.{key} は 0 以上の数にしてください: {item!r}")
    return {key: float(item) for key, item in value.items()}


def snapshot_paths(directory, side):
    directory = Path(directory)
    return directory / f"{side}_snapshot.npz", directory / f"{side}_snapshot.json"
