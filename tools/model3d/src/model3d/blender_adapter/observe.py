"""Blender の中で動かして、シーンの事実を Observation3D（rea-3d-observation/0.1）として書く。

    blender --background scene.blend --python observe.py -- --out observation.json [--objects NAME …] [--units m]

取るもの: オブジェクトの種類・寸法・modifier、メッシュの頂点数と面数、アーマチュアの骨（名前・親・head/tail・レスト行列）、
頂点グループごとのウェイトの数と範囲、シェイプキーの名前。姿勢のコマは取らない（coverage.unmeasured に残す）。
長さはシーンの unit_settings.scale_length（1 Blender 単位 = 何 m）で換算してから --units の単位で書く。
**未検証**: この環境に Blender が無い。bpy に触れない整形（build_observation）だけをテストしている。
"""
import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

METHOD = {"name": "model3d.blender_adapter.observe", "version": "0.1.0"}
UNIT_FACTORS = {"m": 1.0, "cm": 0.01, "mm": 0.001}   # 1 単位 = 何 m


def length_factor(scale_length, units):
    """Blender 単位 → 出力の単位 の係数。scale_length は 1 Blender 単位 = 何 m（scene.unit_settings.scale_length）。"""
    if units not in UNIT_FACTORS:
        raise ValueError(f"units は m / cm / mm から選んでください: {units!r}")
    if not isinstance(scale_length, (int, float)) or isinstance(scale_length, bool) or not math.isfinite(scale_length) or scale_length <= 0:
        raise ValueError(f"scale_length は 0 より大きい有限の数にしてください: {scale_length!r}")
    return float(scale_length) / UNIT_FACTORS[units]


def _scaled(values, factor):
    return None if values is None else [None if v is None else v * factor for v in values]


def _scaled_matrix(matrix, factor):
    """4x4 行列の平行移動成分だけを換算する（回転の成分は触らない）。"""
    return [[(value * factor if (column == 3 and row < 3) else value) for column, value in enumerate(line)]
            for row, line in enumerate(matrix)]


def convert_object(info, factor):
    """describe_object の生の値（Blender 単位）を出力の単位に換算した写し。"""
    out = dict(info)
    if "dimensions" in info:
        out["dimensions"] = _scaled(info["dimensions"], factor)
    if "matrix_world" in info:
        out["matrix_world"] = _scaled_matrix(info["matrix_world"], factor)
    if "bones" in info:
        out["bones"] = [{**bone, "head": _scaled(bone.get("head"), factor), "tail": _scaled(bone.get("tail"), factor),
                         **({"rest_matrix": _scaled_matrix(bone["rest_matrix"], factor)} if bone.get("rest_matrix") else {})}
                        for bone in info["bones"]]
    return out


def parse_args(argv):
    parser = argparse.ArgumentParser(prog="observe.py")
    parser.add_argument("--out", required=True)
    parser.add_argument("--objects", nargs="*", default=None)
    parser.add_argument("--units", default="m", choices=["m", "cm", "mm"])
    parser.add_argument("--space", default="rest", choices=["local", "world", "rest", "pose"])
    return parser.parse_args(argv)


def _finite(values):
    return [float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None for v in values]


def describe_object(obj):
    """bpy のオブジェクトから、JSON にできる事実だけを取る（bpy に依存。未検証）。"""
    info = {"name": obj.name, "type": obj.type, "dimensions": _finite(list(obj.dimensions)),
            "modifiers": [{"name": m.name, "type": m.type} for m in getattr(obj, "modifiers", [])],
            "matrix_world": [[float(x) for x in row] for row in obj.matrix_world]}
    data = getattr(obj, "data", None)
    if obj.type == "MESH" and data is not None:
        info["vertices"] = len(data.vertices)
        info["faces"] = len(data.polygons)
        groups = {g.index: g.name for g in obj.vertex_groups}
        stats = {}
        for vertex in data.vertices:
            for element in vertex.groups:
                name = groups.get(element.group)
                if name is None:
                    continue
                entry = stats.setdefault(name, {"count": 0, "min": 1.0, "max": 0.0})
                entry["count"] += 1
                entry["min"] = min(entry["min"], float(element.weight))
                entry["max"] = max(entry["max"], float(element.weight))
        info["vertex_groups"] = stats
        keys = getattr(data, "shape_keys", None)
        info["shape_keys"] = [k.name for k in keys.key_blocks] if keys else []
    if obj.type == "ARMATURE" and data is not None:
        info["bones"] = [{"name": b.name, "parent": b.parent.name if b.parent else None,
                          "head": _finite(list(b.head_local)), "tail": _finite(list(b.tail_local)),
                          "rest_matrix": [[float(x) for x in row] for row in b.matrix_local]} for b in data.bones]
    return info


def build_observation(scene_name, objects, source_path, source_sha256, units="m", space="rest", created_at=None,
                      blender_version=None, scale_length=1.0):
    """オブジェクトの事実の列から Observation3D を組み立てる（bpy に依存しない）。

    objects は describe_object の生の値（Blender 単位）。scale_length（1 Blender 単位 = 何 m）と units から換算してから単位を付ける。
    """
    created_at = created_at or datetime.now(timezone.utc).isoformat()
    evidence_id = "ev-bpy-data"
    factor = length_factor(scale_length, units)
    facts = [{"id": "scene:scale_length", "name": "scale_length", "value": float(scale_length), "type": "number", "units": "m",
              "evidence_ref": evidence_id},
             {"id": "scene:length_factor", "name": "length_factor", "value": factor, "type": "number", "evidence_ref": evidence_id}]
    objects = [convert_object(info, factor) for info in objects]
    for info in objects:
        base = f"object:{info['name']}"
        facts.append({"id": f"{base}:type", "name": "type", "value": info["type"], "type": "string", "evidence_ref": evidence_id})
        facts.append({"id": f"{base}:dimensions", "name": "dimensions", "value": info.get("dimensions"), "type": "vec3",
                      "units": units, "evidence_ref": evidence_id})
        if "vertices" in info:
            facts.append({"id": f"{base}:vertices", "name": "vertex_count", "value": info["vertices"], "type": "integer",
                          "evidence_ref": evidence_id})
            facts.append({"id": f"{base}:faces", "name": "face_count", "value": info["faces"], "type": "integer",
                          "evidence_ref": evidence_id})
            facts.append({"id": f"{base}:vertex_groups", "name": "vertex_groups", "value": info.get("vertex_groups", {}),
                          "type": "object", "evidence_ref": evidence_id})
            facts.append({"id": f"{base}:shape_keys", "name": "shape_keys", "value": info.get("shape_keys", []),
                          "type": "array", "evidence_ref": evidence_id})
        if "bones" in info:
            facts.append({"id": f"{base}:bones", "name": "bones", "value": info["bones"], "type": "array",
                          "evidence_ref": evidence_id})
        facts.append({"id": f"{base}:modifiers", "name": "modifiers", "value": info.get("modifiers", []), "type": "array",
                      "evidence_ref": evidence_id})
    method = dict(METHOD)
    if blender_version:
        method["version"] = f"{METHOD['version']} (Blender {blender_version})"
    return {"schema_version": "rea-3d-observation/0.1",
            "observation_id": "obs-" + hashlib.sha256(f"{source_sha256}:{scene_name}:{created_at}".encode()).hexdigest()[:16],
            "created_at": created_at,
            "source_refs": [{"path": str(source_path), "sha256": source_sha256, "kind": "blend"}],
            "subject": {"kind": "scene", "id": scene_name},
            "coordinate": {"units": units, "axes": {"up": "+Z", "forward": "-Y"}, "space": space},
            "facts": facts,
            "evidence": [{"id": evidence_id, "source": {"path": str(source_path), "data_path": "bpy.data.objects"},
                          "method": method, "state": "observed"}],
            "coverage": {"objects": [o["name"] for o in objects],
                         "items": ["scale_length", "length_factor", "type", "dimensions", "modifiers", "vertex_count", "face_count",
                                   "vertex_groups", "shape_keys", "bones"],
                         "frames": [], "unmeasured": ["pose frames", "materials", "constraints", "physics caches"]},
            "limitations": ["static scene data only; animation frames are not sampled", "weights are summarized per group, not per vertex",
                            "lengths converted with scene.unit_settings.scale_length; per-object scale is kept inside matrix_world"],
            "unknowns": []}


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = parse_args(argv)
    chosen = [o for o in bpy.data.objects if args.objects is None or o.name in args.objects]
    source = bpy.data.filepath or ""
    digest = hashlib.sha256(Path(source).read_bytes()).hexdigest() if source and Path(source).is_file() else "0" * 64
    scale_length = float(bpy.context.scene.unit_settings.scale_length)   # 1 Blender 単位 = 何 m
    observation = build_observation(bpy.context.scene.name, [describe_object(o) for o in chosen], source, digest,
                                    args.units, args.space, blender_version=bpy.app.version_string, scale_length=scale_length)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(observation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "objects": len(chosen)}))


if __name__ == "__main__":
    main()
