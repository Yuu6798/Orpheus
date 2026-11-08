"""メッシュの操作: 読み込み、押し出し、結合、変形、面領域の切り出し、法線のならし、OBJ 書き出し。"""
from ..io import ToolError, atomic_json, atomic_npz, read_json
from ..mesh import (UNIT_FACTORS, Mesh, extrude_profile, face_edges, fill_holes, list_parts, read_all_parts, read_parts,
                    remap_rig_weights, smooth_normals, submesh, transform, weld)
from . import StepResult, number, register, require, selected

SMOOTH_PARAMETERS = {"iterations", "weld"}
FILL_PARAMETERS = {"max_sides", "cap", "orient", "part"}


def _save(mesh, outputs, name="mesh"):
    if name not in outputs:
        raise ToolError(f"outputs に {name} を宣言してください")
    mesh.save(outputs[name])


@register("mesh.import", inputs=("file",), outputs=("mesh",), summary="JSON か OBJ のメッシュを読み、model3d-mesh/0.1 で書く")
def mesh_import(inputs, parameters, outputs, context):
    require(inputs, ("file",), "mesh.import")
    mesh = Mesh.load(inputs["file"])
    if parameters.get("name"):
        mesh.name = str(parameters["name"])
    if parameters.get("units"):
        mesh.units = parameters["units"]
        mesh.validate()
    _save(mesh, outputs)
    return StepResult(observations=[{"kind": "mesh", **mesh.summary()}])


@register("mesh.import_parts", inputs=("parts", "normals"), outputs=("mesh",),
          summary="部品ごとの npz（names と co{i}・f{i}・uv{i}。法線は別の npz の n{i}）から part の部品を読み、model3d-mesh/0.1 で書く")
def mesh_import_parts(inputs, parameters, outputs, context):
    require(inputs, ("parts",), "mesh.import_parts")
    part = parameters.get("part")
    if not isinstance(part, str) or not part:
        raise ToolError("parameters.part に部品の名前を書いてください")
    mesh = read_parts(inputs["parts"], part, inputs.get("normals"))
    _save(mesh, outputs)
    return StepResult(observations=[{"kind": "mesh", **mesh.summary(), "part": part, "uv": mesh.uv is not None,
                                     "normals": "x_normals" in mesh.extra}])


def _iterations(value, where="iterations"):
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ToolError(f"{where} は 0 以上の整数にしてください（部品ごとの回数は、リポジトリ外の設定に書く）: {value!r}")
    return value


def _check_smooth(parameters, operation):
    unknown = set(parameters) - SMOOTH_PARAMETERS
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(SMOOTH_PARAMETERS)}）")
    if "iterations" not in parameters:
        raise ToolError(f"{operation} には iterations（ならす回数。0 以上の整数）が要ります")


@register("mesh.smooth_normals", inputs=("mesh",), outputs=("mesh", "report"),
          summary="頂点の法線をならして x_normals に書く（頂点と面は変えない）。格子 weld（既定は 1e-5 m をメッシュの単位に換算）で同じ位置の"
                  "頂点をまとめ（UV の継ぎ目で分かれた頂点も同じ法線になる）、面積の重みの法線から、辺でつながった隣の法線の和を足して長さ 1 に"
                  "戻すことを iterations 回くり返す。法線が 0 のままの頂点（向かい合う面が重なる所・面の無い頂点）は数を報告する")
def mesh_smooth_normals(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.smooth_normals")
    _check_smooth(parameters, "mesh.smooth_normals")
    mesh = Mesh.load(inputs["mesh"])
    iterations = _iterations(parameters["iterations"])
    step = float(number(parameters, "weld", 1e-5 / UNIT_FACTORS[mesh.units], minimum=0, exclusive=True))
    triangles, _ = mesh.triangles()
    normals, report = smooth_normals(mesh.vertices, triangles, face_edges(mesh.faces), iterations, step)
    mesh.extra["x_normals"] = normals.tolist()
    _save(mesh, outputs)
    if "report" in outputs:
        atomic_json(outputs["report"], {"schema_version": "model3d-normals/0.1", "units": mesh.units, "parts": [{"name": mesh.name, **report}]})
    return StepResult(observations=[{"kind": "smooth_normals", **report}])


@register("mesh.smooth_part_normals", inputs=("parts",), outputs=("normals", "report"),
          summary="部品ごとの npz（names と co{i}・f{i}）の全部の部品の法線を、mesh.smooth_normals と同じ計算でならし、n{i}（float32、"
                  "頂点ごと）の npz に書く（mesh.import_parts の入力 normals になる）。iterations は全部の部品に同じ整数か、"
                  "{部品の名前: 整数}（全部の部品を書く）。weld は npz の単位（m）で、既定 1e-5")
def mesh_smooth_part_normals(inputs, parameters, outputs, context):
    import numpy as np
    require(inputs, ("parts",), "mesh.smooth_part_normals")
    _check_smooth(parameters, "mesh.smooth_part_normals")
    if "normals" not in outputs:
        raise ToolError("mesh.smooth_part_normals には outputs の normals（法線の npz）が要ります")
    parts = read_all_parts(inputs["parts"])
    names = [name for name, _vertices, _faces in parts]
    spec = parameters["iterations"]
    if isinstance(spec, dict):
        missing, extra = [n for n in names if n not in spec], sorted(set(spec) - set(names))
        if missing or extra:
            raise ToolError(f"iterations の部品が npz と合いません（書いていない部品: {missing}、npz に無い部品: {extra}）")
        counts = {name: _iterations(spec[name], f"iterations.{name}") for name in names}
    else:
        counts = dict.fromkeys(names, _iterations(spec))
    step = float(number(parameters, "weld", 1e-5, minimum=0, exclusive=True))
    arrays, rows = {}, []
    for i, (name, vertices, faces) in enumerate(parts):
        if faces.shape[1] == 3:
            triangles, edges = faces, face_edges(faces)
        else:                                            # 四角形以上は、面の平面で三角形に分けて面積を取る（隣は面の縁の辺）
            polygon = Mesh(vertices, faces.tolist())
            triangles, edges = polygon.triangles()[0], face_edges(polygon.faces)
        normals, report = smooth_normals(vertices, triangles, edges, counts[name], step)
        arrays[f"n{i}"] = normals.astype(np.float32)
        rows.append({"index": i, "name": name, **report})
    atomic_npz(outputs["normals"], **arrays)
    if "report" in outputs:
        atomic_json(outputs["report"], {"schema_version": "model3d-normals/0.1", "units": "m", "parts": rows})
    return StepResult(observations=[{"kind": "smooth_part_normals", "parts": len(rows), "vertices": sum(r["vertices"] for r in rows),
                                     "zero_normals": sum(r["zero_normals"] for r in rows), "weld": step}])


@register("mesh.fill_holes", inputs=("mesh",), outputs=("mesh", "report"),
          summary="穴（1 つの面だけが使う辺の輪）を面でふさぎ、閉じたメッシュにする（collision.push_out の当たりなど）。今の面と頂点の番号は"
                  "変えず、ふさぐ面を後ろに足す。cap: polygon（輪ごとに 1 つの多角形）か fan（輪の平均に頂点を足して三角形の扇）。"
                  "orient で、体積が負なら全部の面を裏返して外向きに。max_sides より多い辺の輪は残す（要確認）。多様体でない辺・"
                  "向きのそろわない面・2 つの輪が通る頂点・つぶれた辺は止める。x_normals は外す")
def mesh_fill_holes(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.fill_holes")
    unknown = set(parameters) - FILL_PARAMETERS
    if unknown:
        raise ToolError(f"mesh.fill_holes の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(FILL_PARAMETERS)}）")
    orient = parameters.get("orient", True)
    if not isinstance(orient, bool):
        raise ToolError(f"orient は true か false にしてください: {orient!r}")
    part = parameters.get("part")
    if part is not None and (not isinstance(part, str) or not part):
        raise ToolError(f"part はふさいだ面を入れる parts の名前（空でない文字列）にしてください: {part!r}")
    mesh = Mesh.load(inputs["mesh"])
    filled, report = fill_holes(mesh, parameters.get("max_sides", 0), parameters.get("cap", "polygon"), orient, part)
    _save(filled, outputs)
    if "report" in outputs:
        atomic_json(outputs["report"], {"schema_version": "model3d-fill-holes/0.1", "name": mesh.name, **report})
    unresolved = [f"ふさがなかった穴が {sum(1 for r in report['loops'] if not r['filled'])} つ（辺 {report['left_open_edges']} 本。"
                  "max_sides より大きい）。メッシュは閉じていない"] if report["left_open_edges"] else []
    observation = {"kind": "fill_holes", **{k: v for k, v in report.items() if k != "loops"},
                   "loop_sizes": [r["edges"] for r in report["loops"]][:50]}
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))


@register("mesh.list_parts", inputs=("parts", "normals"), outputs=("report",),
          summary="部品ごとの npz（names と co{i}・f{i}・uv{i}）の部品の一覧: 名前・頂点と面と三角形の数・面の角の数・範囲・uv の範囲。"
                  "normals（n{i} の npz）を渡すと、部品ごとに法線があるか・形が合うかも見る。見つけたこと（非有限の頂点・無い頂点を"
                  "指す面・形の合わない uv や法線・どの部品にも当たらない法線）があれば要確認。pickle は読まない")
def mesh_list_parts(inputs, parameters, outputs, context):
    require(inputs, ("parts",), "mesh.list_parts")
    if parameters:
        raise ToolError(f"mesh.list_parts にパラメーターはありません: {sorted(parameters)}")
    if "report" not in outputs:
        raise ToolError("mesh.list_parts には outputs の report が要ります")
    report = list_parts(inputs["parts"], inputs.get("normals"))
    atomic_json(outputs["report"], report)
    observation = {"kind": "list_parts", **report["totals"], "normals": inputs.get("normals") is not None,
                   "problems": len(report["problems"])}
    return StepResult(observations=[observation], unresolved=list(report["problems"]), review=bool(report["problems"]))


@register("mesh.extrude_profile", inputs=("profile",), outputs=("mesh",),
          summary="凸断面（{\"points\": [[x, y], …]}）を height だけ押し出す。twist_deg / twist_rad・steps・offset")
def mesh_extrude(inputs, parameters, outputs, context):
    if "profile" in inputs:
        data = read_json(inputs["profile"])
        points = data.get("points") if isinstance(data, dict) else data
    else:
        points = parameters.get("profile")
    if points is None:
        raise ToolError("mesh.extrude_profile には入力 profile（{\"points\": …}）かパラメーター profile が要ります")
    height = number(parameters, "height")
    steps = number(parameters, "steps", 1, minimum=1, integer=True)
    mesh = extrude_profile(points, height, parameters.get("twist_deg", 0.0), steps, tuple(parameters.get("offset", (0.0, 0.0))),
                           parameters.get("name"), parameters.get("units", "m"), twist_rad=parameters.get("twist_rad"))
    _save(mesh, outputs)
    return StepResult(observations=[{"kind": "mesh", **mesh.summary(), "closed": mesh.edge_report()["closed"],
                                     "area": mesh.area(), "signed_volume": mesh.signed_volume()}])


@register("mesh.weld", inputs=("mesh",), outputs=("mesh", "mapping", "rig"), selectable=("mesh",),
          summary="distance 以内の頂点を結合する。任意の mapping 出力（頂点と面の旧→新）、rig 入出力でウェイトも移す（mean / first）")
def mesh_weld(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.weld")
    mesh = Mesh.load(inputs["mesh"])
    distance = number(parameters, "distance", minimum=0, exclusive=True)
    chosen = selected(context, "mesh", parameters, "mesh.weld")
    selection = chosen["vertices"] if chosen else parameters.get("selection", mesh.selection if parameters.get("use_selection") else None)
    welded, report = weld(mesh, distance, selection, parameters.get("center", "mean"))
    mapping = report.pop("vertex_mapping")
    if ("rig" in inputs) != ("rig" in outputs):
        raise ToolError("mesh.weld でリグを扱うときは入力 rig と出力 rig を両方宣言してください")
    if "rig" in inputs:
        rig = remap_rig_weights(read_json(inputs["rig"]), mesh, welded, mapping)
        atomic_json(outputs["rig"], rig)
    if "mapping" in outputs:
        atomic_json(outputs["mapping"], mapping)
    _save(welded, outputs)
    return StepResult(observations=[{"kind": "weld", **report, "topology_sha256": welded.topology_hash()}])


@register("mesh.transform", inputs=("mesh",), outputs=("mesh",),
          summary="to_units（単位の換算）→ to_axes（軸の換算）→ scale → rotate_z_deg → translate の順に当てる")
def mesh_transform(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.transform")
    mesh = Mesh.load(inputs["mesh"])
    moved = transform(mesh, parameters.get("translate"), parameters.get("scale"), parameters.get("rotate_z_deg", 0.0),
                      parameters.get("to_units"), parameters.get("to_axes"))
    _save(moved, outputs)
    return StepResult(observations=[{"kind": "mesh", **moved.summary()}])


@register("mesh.extract_region", inputs=("mesh",), outputs=("mesh", "mapping"), selectable=("mesh",),
          summary="工程の select で選んだ役割の面だけのメッシュを書く（使わない頂点は落とす）。mapping は新→旧の頂点・面番号")
def mesh_extract_region(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.extract_region")
    chosen = selected(context, "mesh", parameters, "mesh.extract_region")
    if chosen is None:
        raise ToolError("mesh.extract_region には工程の select（{\"mesh\": \"<役割>\"}）が要ります")
    mesh = Mesh.load(inputs["mesh"])
    part, index = submesh(mesh, chosen["faces"])
    part.name = chosen["role"]
    _save(part, outputs)
    if "mapping" in outputs:
        atomic_json(outputs["mapping"], {"role": chosen["role"], "source_vertex_count": mesh.vertex_count,
                                         "source_topology_sha256": mesh.topology_hash(), "target_topology_sha256": part.topology_hash(),
                                         "vertices_new_to_old": index["vertices"], "faces_new_to_old": index["faces"]})
    edges = part.edge_report()
    return StepResult(observations=[{"kind": "region", "role": chosen["role"], **part.summary(), "components": part.components(),
                                     "closed": edges["closed"], "boundary_edges": edges["boundary_edges"],
                                     "nonmanifold_edges": edges["nonmanifold_edges"]}])


@register("mesh.export_obj", inputs=("mesh",), outputs=("obj",), summary="OBJ に書き出す（v / vt / f / g）")
def mesh_export_obj(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.export_obj")
    if "obj" not in outputs:
        raise ToolError("outputs に obj を宣言してください")
    mesh = Mesh.load(inputs["mesh"])
    mesh.save(outputs["obj"])
    return StepResult(observations=[{"kind": "export", "format": "obj", **mesh.summary()}])
