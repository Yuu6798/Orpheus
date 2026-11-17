"""VRM 1.0 のファイル（GLB）の中身を読んで、構造と数を報告する（標準ライブラリだけ。Blender は要らない）。

見た目や使い勝手は判定しない。読めるか・VRM 1.0 の決まりで要るもの（人型の必須の骨・メタの名前と作者とライセンスの URL）が
あるか・参照の番号が範囲の中か、だけを合否にする（docs/model3d.md の「VRM を書き出す」）。
"""
from pathlib import Path

from .glb import read_glb
from .io import ToolError

# VRM 1.0 の人型の骨で必須のもの（VRM 1.0 の humanoid の仕様。VRM Add-on 4.10.5 の requirement とも同じ）
REQUIRED_HUMAN_BONES = ("hips", "spine", "head", "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg",
                        "rightFoot", "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm", "rightLowerArm", "rightHand")
REQUIRED_META = ("name", "authors", "licenseUrl")
META_KEYS = ("name", "version", "authors", "licenseUrl", "avatarPermission", "commercialUsage", "creditNotation",
             "allowRedistribution", "modification", "allowExcessivelyViolentUsage", "allowExcessivelySexualUsage",
             "allowPoliticalOrReligiousUsage", "allowAntisocialOrHateUsage")


def _node(index, nodes):
    return isinstance(index, int) and not isinstance(index, bool) and 0 <= index < nodes


def morph_targets(document):
    """メッシュごとの形（モーフ）の数と名前: [{"mesh": 番号, "name", "targets", "names"}]。形の無いメッシュは載せない。名前は glTF の
    mesh.extras.targetNames（VRM Add-on と Blender の glTF の書き出しが書く。無ければ None）。数は primitives の targets のいちばん多いもの
    （glTF では、どの primitive も同じ数）。"""
    out = []
    for index, mesh in enumerate(document.get("meshes") or []):
        mesh = mesh if isinstance(mesh, dict) else {}
        count = max([len(p.get("targets") or []) for p in mesh.get("primitives") or [] if isinstance(p, dict)], default=0)
        if not count:
            continue
        names = (mesh.get("extras") or {}).get("targetNames") if isinstance(mesh.get("extras"), dict) else None
        if not (isinstance(names, list) and all(isinstance(name, str) for name in names)):
            names = None
        out.append({"mesh": index, "name": mesh.get("name"), "targets": count, "names": names})
    return out


def morph_bind(bind, document, morphs):
    """表情の形の割り当て 1 つの中身 {"node", "mesh", "index", "target", "weight"} と、問題の説明（無ければ None）。mesh はメッシュの名前、
    target は形の名前（名前が無ければ None）。"""
    bind = bind if isinstance(bind, dict) else {}
    nodes = document.get("nodes") or []
    node, index = bind.get("node"), bind.get("index")
    entry = {"node": node, "mesh": None, "index": index, "target": None, "weight": bind.get("weight")}
    if not _node(node, len(nodes)):
        return entry, None                               # node の範囲の外は、呼ぶ側が node の問題としてまとめて書く
    mesh_index = (nodes[node] or {}).get("mesh") if isinstance(nodes[node], dict) else None
    meshes = document.get("meshes") or []
    if not _node(mesh_index, len(meshes)):
        return entry, f"node {node} にメッシュがありません"
    entry["mesh"] = (meshes[mesh_index] or {}).get("name") if isinstance(meshes[mesh_index], dict) else None
    found = next((m for m in morphs if m["mesh"] == mesh_index), None)
    count = found["targets"] if found else 0
    if not _node(index, count):
        return entry, f"index {index!r} が node {node} のメッシュの形の数（{count}）の外です"
    if found["names"] is not None and index < len(found["names"]):
        entry["target"] = found["names"][index]
    return entry, None


def inspect(data, where="VRM"):
    """VRM のバイト列の中身の報告（model3d-vrm-check/0.1）。GLB として読めなければ ToolError。"""
    document, blob = read_glb(data, where)
    failures = []
    extensions = document.get("extensions") or {}
    vrm = extensions.get("VRMC_vrm")
    nodes = len(document.get("nodes") or [])
    report = {"schema_version": "model3d-vrm-check/0.1", "bytes": len(data), "generator": (document.get("asset") or {}).get("generator"),
              "spec_version": None, "meta": {}, "human_bones": 0, "missing_human_bones": [], "nodes": nodes,
              "meshes": [mesh.get("name") for mesh in document.get("meshes") or []],
              "materials": len(document.get("materials") or []),
              "mtoon_materials": sum(1 for material in document.get("materials") or []
                                     if "VRMC_materials_mtoon" in (material.get("extensions") or {})),
              "images": [], "springs": [], "spring_joints": 0, "colliders": 0, "collider_groups": 0, "expressions_bound": [],
              "morph_targets": morph_targets(document), "expressions": {"preset": {}, "custom": {}}}
    views = document.get("bufferViews") or []
    for image in document.get("images") or []:
        view = image.get("bufferView")
        size = views[view].get("byteLength") if isinstance(view, int) and 0 <= view < len(views) else None
        report["images"].append({"name": image.get("name"), "mime_type": image.get("mimeType"), "bytes": size})
    if not isinstance(vrm, dict):
        failures.append("VRMC_vrm（VRM 1.0 の拡張）がありません")
    else:
        report["spec_version"] = vrm.get("specVersion")
        if vrm.get("specVersion") != "1.0":
            failures.append(f"VRMC_vrm の specVersion が 1.0 ではありません: {vrm.get('specVersion')!r}")
        meta = vrm.get("meta") or {}
        report["meta"] = {key: meta[key] for key in META_KEYS if key in meta}
        for key in REQUIRED_META:
            if not meta.get(key):
                failures.append(f"メタの {key} がありません（VRM 1.0 で必須）")
        bones = (vrm.get("humanoid") or {}).get("humanBones") or {}
        report["human_bones"] = len(bones)
        report["missing_human_bones"] = [name for name in REQUIRED_HUMAN_BONES if name not in bones]
        if report["missing_human_bones"]:
            failures.append(f"人型の必須の骨がありません: {report['missing_human_bones']}")
        used = {}
        for name, bone in bones.items():
            index = (bone or {}).get("node")
            if not _node(index, nodes):
                failures.append(f"人型の骨 {name} の node が範囲の外です: {index!r}")
            elif index in used:
                failures.append(f"人型の骨 {name} と {used[index]} が同じ node {index} です")
            else:
                used[index] = name
        if "thumbnailImage" in meta and not _node(meta["thumbnailImage"], len(document.get("images") or [])):
            failures.append(f"メタの thumbnailImage が画像の範囲の外です: {meta['thumbnailImage']!r}")
        for number, annotation in enumerate((vrm.get("firstPerson") or {}).get("meshAnnotations") or []):
            if not _node((annotation or {}).get("node"), nodes):
                failures.append(f"firstPerson の meshAnnotations {number} の node が範囲の外です: {(annotation or {}).get('node')!r}")
        materials = len(document.get("materials") or [])
        expressions = vrm.get("expressions") or {}
        for group in ("preset", "custom"):
            for name, expression in (expressions.get(group) or {}).items():
                expression = expression or {}
                if any(expression.get(kind) for kind in ("morphTargetBinds", "materialColorBinds", "textureTransformBinds")):
                    report["expressions_bound"].append(name)
                binds = []
                for bind in expression.get("morphTargetBinds") or []:
                    entry, problem = morph_bind(bind, document, report["morph_targets"])
                    binds.append(entry)
                    if problem:
                        failures.append(f"表情 {name} の形の割り当ての {problem}")
                report["expressions"][group][name] = binds
                bad = [bind.get("node") for bind in expression.get("morphTargetBinds") or [] if not _node((bind or {}).get("node"), nodes)]
                bad += [bind.get("material") for kind in ("materialColorBinds", "textureTransformBinds")
                        for bind in expression.get(kind) or [] if not _node((bind or {}).get("material"), materials)]
                if bad:
                    failures.append(f"表情 {name} の割り当ての node か material が範囲の外です: {bad}")
    springs = extensions.get("VRMC_springBone")
    if isinstance(springs, dict):
        colliders = springs.get("colliders") or []
        report["colliders"] = len(colliders)
        report["collider_groups"] = len(springs.get("colliderGroups") or [])
        for number, collider in enumerate(colliders):
            if not _node((collider or {}).get("node"), nodes):
                failures.append(f"当たり判定 {number} の node が範囲の外です: {(collider or {}).get('node')!r}")
        groups = springs.get("colliderGroups") or []
        for number, group in enumerate(groups):          # 当たりの組が指す当たり判定の番号
            bad = [index for index in (group or {}).get("colliders") or [] if not _node(index, len(colliders))]
            if bad:
                failures.append(f"当たりの組 {number} の colliders が範囲の外です: {bad}")
        for spring in springs.get("springs") or []:
            joints = spring.get("joints") or []
            report["springs"].append(spring.get("name"))
            report["spring_joints"] += len(joints)
            bad = [joint.get("node") for joint in joints if not _node((joint or {}).get("node"), nodes)]
            if bad:
                failures.append(f"揺れもの {spring.get('name')!r} の関節の node が範囲の外です: {bad}")
            bad = [index for index in spring.get("colliderGroups") or [] if not _node(index, len(groups))]
            if bad:
                failures.append(f"揺れもの {spring.get('name')!r} の colliderGroups が範囲の外です: {bad}")
            if "center" in spring and not _node(spring["center"], nodes):    # 揺れものの中心の空間（あれば node の番号）
                failures.append(f"揺れもの {spring.get('name')!r} の center が範囲の外です: {spring['center']!r}")
    report["bin_bytes"] = len(blob) if blob is not None else None
    report["failures"] = failures
    report["pass"] = not failures
    return report


def inspect_file(path):
    try:
        data = Path(path).read_bytes()
    except OSError as error:
        raise ToolError(f"VRM を読めません: {path}: {error}") from error
    return inspect(data, str(path))


def meta_of(data, where="VRM"):
    """VRM 1.0 のメタ（名前・版・作者）。vrm.export の meta_from で写す元。無ければ ToolError。"""
    document, _ = read_glb(data, where)
    meta = ((document.get("extensions") or {}).get("VRMC_vrm") or {}).get("meta")
    if not isinstance(meta, dict):
        raise ToolError(f"{where} に VRM 1.0 のメタがありません（VRM 0.x は未対応）")
    return {"name": meta.get("name", ""), "version": meta.get("version", ""), "authors": list(meta.get("authors") or [])}
