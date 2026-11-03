"""メッシュの操作: 読み込み、押し出し、結合、変形、OBJ 書き出し。"""
from ..io import ToolError, read_json
from ..mesh import Mesh, extrude_profile, transform, weld
from . import StepResult, number, register, require


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


@register("mesh.weld", inputs=("mesh",), outputs=("mesh",), summary="distance 以内の頂点を結合する（selection は任意・center は mean か first）")
def mesh_weld(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.weld")
    mesh = Mesh.load(inputs["mesh"])
    distance = number(parameters, "distance", minimum=0, exclusive=True)
    selection = parameters.get("selection", mesh.selection if parameters.get("use_selection") else None)
    welded, report = weld(mesh, distance, selection, parameters.get("center", "mean"))
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


@register("mesh.export_obj", inputs=("mesh",), outputs=("obj",), summary="OBJ に書き出す（v / vt / f / g）")
def mesh_export_obj(inputs, parameters, outputs, context):
    require(inputs, ("mesh",), "mesh.export_obj")
    if "obj" not in outputs:
        raise ToolError("outputs に obj を宣言してください")
    mesh = Mesh.load(inputs["mesh"])
    mesh.save(outputs["obj"])
    return StepResult(observations=[{"kind": "export", "format": "obj", **mesh.summary()}])
