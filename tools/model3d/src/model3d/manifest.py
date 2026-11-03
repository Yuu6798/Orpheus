"""AssetManifest: 入力の参照とハッシュ、部品 ID、形状とトポロジーの識別値、単位、軸、資産の版。"""
from pathlib import Path

from . import contracts
from .io import ToolError, now, read_json, sha256

KIND_BY_SUFFIX = {".json": "json", ".obj": "obj", ".blend": "blend", ".glb": "glb", ".gltf": "glb", ".fbx": "fbx",
                  ".png": "image", ".jpg": "image", ".jpeg": "image", ".webp": "image"}


def describe_input(name, path, kind=None):
    path = Path(path)
    if not path.is_file():
        raise ToolError(f"入力が見つかりません: {path}")
    kind = kind or KIND_BY_SUFFIX.get(path.suffix.lower(), "other")
    entry = {"name": name, "path": str(path.resolve()), "sha256": sha256(path), "bytes": path.stat().st_size, "kind": kind}
    mesh = None
    if kind == "mesh" or kind == "obj":
        from .mesh import Mesh
        mesh = Mesh.load(path)
    elif kind == "json":
        data = read_json(path)
        if contracts.kind_of(data) == "mesh":
            from .mesh import Mesh
            mesh = Mesh.from_dict(data, path)
            entry["kind"] = "mesh"
        elif contracts.kind_of(data) == "recipe":
            entry["kind"] = "recipe"
    if mesh is not None:
        entry.update(topology_sha256=mesh.topology_hash(), geometry_sha256=mesh.geometry_hash(),
                     vertex_count=mesh.vertex_count, face_count=mesh.face_count)
    return entry


def build(asset_id, inputs, units="m", axes=None, version="1", parts=None, transform=None, notes=None):
    """inputs は (name, path[, kind]) の列。検証済みの AssetManifest を返す。"""
    if not asset_id:
        raise ToolError("asset_id が要ります")
    entries = []
    for item in inputs:
        name, path = item[0], item[1]
        kind = item[2] if len(item) > 2 else None
        entries.append(describe_input(name, path, kind))
    names = [e["name"] for e in entries]
    if len(set(names)) != len(names):
        raise ToolError("入力の名前が重なっています")
    data = {"schema_version": "model3d-asset-manifest/0.1", "asset_id": asset_id, "version": str(version),
            "created_at": now(), "inputs": entries, "units": units, "axes": dict(axes or {"up": "+Z", "forward": "-Y"})}
    if parts:
        data["parts"] = [{"id": p} if isinstance(p, str) else dict(p) for p in parts]
    if transform is not None:
        data["transform"] = transform
    if notes:
        data["notes"] = notes
    return contracts.validate("asset-manifest", data)
