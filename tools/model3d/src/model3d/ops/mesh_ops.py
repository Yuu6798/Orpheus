"""メッシュの操作: 読み込み、押し出し、結合、変形、面領域の切り出し、OBJ 書き出し。"""
from ..io import ToolError, atomic_json, read_json
from ..mesh import Mesh, extrude_profile, remap_rig_weights, submesh, transform, weld
from . import StepResult, number, register, require, selected


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
