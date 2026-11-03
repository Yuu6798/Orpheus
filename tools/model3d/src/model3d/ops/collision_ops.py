"""衝突の補正: 閉じた衝突相手の中にある頂点を、余裕を取って外へ出す。"""
import numpy as np

from .. import checks, collide
from ..io import ToolError, atomic_json
from ..mesh import Mesh
from . import StepResult, number, register, require


@register("collision.push_out", inputs=("mesh", "collider"), outputs=("mesh", "deltas", "report"),
          summary="collider の中にある頂点を面の外へ clearance だけ離す。deltas は頂点ごとの移動量（骨で動かす前の形に戻すのは別工程）")
def push_out(inputs, parameters, outputs, context):
    require(inputs, ("mesh", "collider"), "collision.push_out")
    mesh, collider = Mesh.load(inputs["mesh"]), Mesh.load(inputs["collider"])
    checks.require_same_units(mesh, collider)   # clearance も mesh の単位で読む
    closed = collider.edge_report()
    if not closed["closed"]:
        raise ToolError(f"衝突相手が閉じていません（境界辺 {closed['boundary_edges']}・非多様体辺 {closed['nonmanifold_edges']}）")
    clearance = number(parameters, "clearance", minimum=0)
    selection = parameters.get("selection")
    indices = np.arange(mesh.vertex_count) if selection is None else np.asarray(selection, dtype=np.int64)
    if len(indices) and (indices.min() < 0 or indices.max() >= mesh.vertex_count):
        raise ToolError("selection が無い頂点を指しています")
    moved, report = collide.push_out(mesh.vertices[indices], collider, clearance, parameters.get("directions"),
                                     parameters.get("pad_outside", True))
    fixed = mesh.copy()
    fixed.vertices[indices] = moved
    deltas = (moved - mesh.vertices[indices])
    changed = np.flatnonzero(np.linalg.norm(deltas, axis=1) > 0)
    if "mesh" in outputs:
        fixed.save(outputs["mesh"])
    if "deltas" in outputs:
        atomic_json(outputs["deltas"], {"schema_version": "model3d-deltas/0.1", "topology_sha256": mesh.topology_hash(),
                                        "space": parameters.get("space", "pose"), "units": mesh.units,
                                        "deltas": [[int(indices[i]), [float(x) for x in deltas[i]]] for i in changed]})
    observation = {"kind": "push_out", **report, "changed": int(len(changed)), "topology_sha256": mesh.topology_hash(),
                   "collider_topology_sha256": collider.topology_hash()}
    if "report" in outputs:
        atomic_json(outputs["report"], observation)
    unresolved = []
    if report["residual_inside"]:
        unresolved.append(f"補正の後も {report['residual_inside']} 頂点が中に残っています（凹んだ衝突相手では 1 回の補正で出ないことがある）")
    return StepResult(observations=[observation], unresolved=unresolved, review=bool(unresolved))
