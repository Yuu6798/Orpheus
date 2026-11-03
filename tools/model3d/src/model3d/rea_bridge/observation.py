"""Observation3D（rea-3d-observation/0.1）の組み立てと検証。推定した値は facts に混ぜない。"""
import hashlib

from .. import contracts
from ..io import now


def new(subject, facts, evidence, source_refs, units="m", space="local", axes=None, coverage=None, limitations=(),
        unknowns=(), frame=None):
    """facts は {"id", "name", "value", "type", "evidence_ref"} の列、evidence は {"id", "source", "method", "state"} の列。"""
    created = now()
    seed = f"{subject}:{created}:{[r.get('sha256') for r in source_refs]}"
    coordinate = {"units": units, "axes": dict(axes or {"up": "+Z", "forward": "-Y"}), "space": space}
    if frame is not None:
        coordinate["frame"] = frame
    data = {"schema_version": "rea-3d-observation/0.1",
            "observation_id": "obs-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
            "created_at": created, "source_refs": list(source_refs), "subject": dict(subject), "coordinate": coordinate,
            "facts": list(facts), "evidence": list(evidence),
            "coverage": {"items": sorted({f["name"] for f in facts}), "unmeasured": [], **(coverage or {})},
            "limitations": list(limitations), "unknowns": list(unknowns)}
    refs = {e["id"] for e in data["evidence"]}
    dangling = [f["id"] for f in data["facts"] if f["evidence_ref"] not in refs]
    if dangling:
        from ..io import ToolError
        raise ToolError(f"facts の evidence_ref が evidence にありません: {', '.join(dangling[:5])}")
    return contracts.validate("observation", data)


def from_mesh(mesh, path, sha256, space="local"):
    """メッシュファイルの実測（頂点数・面数・bounds・面積・閉じているか）を Observation3D にする。"""
    evidence = [{"id": "ev-mesh-file", "source": {"path": str(path)}, "method": {"name": "model3d.mesh", "version": "0.1.0"},
                 "state": "observed"}]
    lo, hi = mesh.bounds()
    report = mesh.edge_report()
    facts = [
        {"id": "vertex_count", "name": "vertex_count", "value": mesh.vertex_count, "type": "integer", "evidence_ref": "ev-mesh-file"},
        {"id": "face_count", "name": "face_count", "value": mesh.face_count, "type": "integer", "evidence_ref": "ev-mesh-file"},
        {"id": "bounds", "name": "bounds", "value": {"min": lo, "max": hi}, "type": "aabb", "units": mesh.units, "evidence_ref": "ev-mesh-file"},
        {"id": "area", "name": "area", "value": mesh.area(), "type": "number", "units": f"{mesh.units}^2", "evidence_ref": "ev-mesh-file"},
        {"id": "closed", "name": "closed", "value": report["closed"], "type": "boolean", "evidence_ref": "ev-mesh-file"},
    ]
    if report["closed"]:
        facts.append({"id": "signed_volume", "name": "signed_volume", "value": mesh.signed_volume(), "type": "number",
                      "units": f"{mesh.units}^3", "evidence_ref": "ev-mesh-file"})
    return new({"kind": "mesh", "id": mesh.name or str(path), "topology_sha256": mesh.topology_hash()}, facts, evidence,
               [{"path": str(path), "sha256": sha256, "kind": "mesh"}], units=mesh.units, space=space, axes=mesh.axes,
               limitations=["measured from the file as stored; no pose or animation"])
