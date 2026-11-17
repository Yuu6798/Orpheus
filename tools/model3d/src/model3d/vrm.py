"""VRM 1.0 のファイル（GLB）の中身を読んで、構造と数を報告する（Blender は要らない。骨の重みを読むのに numpy を使う）。

見た目や使い勝手は判定しない。読めるか・VRM 1.0 の決まりで要るもの（人型の必須の骨・メタの名前と作者とライセンスの URL）が
あるか・参照の番号が範囲の中か、だけを合否にする（docs/model3d.md の「VRM を書き出す」）。
作りの一覧（structure）は、キャラを作る道具から受け取った VRM を、Blender に持ち込む前に知るためのもの（揺れものの鎖ごとの値・
当たり判定・骨の拘束・ねじれを受け持つ骨・材質の値・画像の大きさ・メッシュごとの骨の重み）。数を並べるだけで、良し悪しは決めない。
"""
import math
import struct
from collections import Counter
from pathlib import Path

from .glb import image_bytes, read_glb
from .io import ToolError

# VRM 1.0 の人型の骨で必須のもの（VRM 1.0 の humanoid の仕様。VRM Add-on 4.10.5 の requirement とも同じ）
REQUIRED_HUMAN_BONES = ("hips", "spine", "head", "leftUpperLeg", "leftLowerLeg", "leftFoot", "rightUpperLeg", "rightLowerLeg",
                        "rightFoot", "leftUpperArm", "leftLowerArm", "leftHand", "rightUpperArm", "rightLowerArm", "rightHand")
REQUIRED_META = ("name", "authors", "licenseUrl")
META_KEYS = ("name", "version", "authors", "licenseUrl", "avatarPermission", "commercialUsage", "creditNotation",
             "allowRedistribution", "modification", "allowExcessivelyViolentUsage", "allowExcessivelySexualUsage",
             "allowPoliticalOrReligiousUsage", "allowAntisocialOrHateUsage", "copyrightInformation", "contactInformation",
             "references", "thirdPartyLicenses", "otherLicenseUrl")
# 作りの一覧（structure）
TWIST_WORDS = ("twist", "roll", "nejire")      # 名前（小文字）にこれを含む骨を「ねじれらしい骨」に挙げる（名前で見るだけ）
WEIGHT_USED = 1e-4                             # これより大きい重みを「その骨に付いている」と数える
SPRING_VALUES = ("stiffness", "gravityPower", "dragForce", "hitRadius")
MTOON_VALUES = ("shadeColorFactor", "shadingShiftFactor", "shadingToonyFactor", "giEqualizationFactor", "outlineWidthMode",
                "outlineWidthFactor", "outlineColorFactor", "outlineLightingMixFactor", "rimLightingMixFactor",
                "parametricRimColorFactor", "parametricRimFresnelPowerFactor", "parametricRimLiftFactor", "matcapFactor",
                "transparentWithZWrite", "renderQueueOffsetNumber")
LOOK_AT_VALUES = ("type", "offsetFromHeadBone", "rangeMapHorizontalInner", "rangeMapHorizontalOuter", "rangeMapVerticalDown",
                  "rangeMapVerticalUp")
COMPONENTS = {5120: "i1", 5121: "u1", 5122: "<i2", 5123: "<u2", 5125: "<u4", 5126: "<f4"}   # glTF の componentType → numpy
WIDTHS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
JPEG_SOF = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}   # 大きさを持つ JPEG の印


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


def _dict(value):
    return value if isinstance(value, dict) else {}


def _items(document, key):
    """document[key] の並び（並びでなければ空）。辞書でない要素は空の辞書にする（番号はそのまま）。"""
    items = document.get(key)
    return [item if isinstance(item, dict) else {} for item in items] if isinstance(items, list) else []


def _list(value):
    return value if isinstance(value, list) else []


def _count(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _vector(value, size):
    return isinstance(value, list) and len(value) == size and all(_number(v) for v in value)


def _name_at(items, index):
    """items[index] の名前（番号が範囲の外なら None）。"""
    return items[index].get("name") if _node(index, len(items)) else None


def _span(values):
    """数の並びの [最小, 最大]（数が 1 つも無ければ None）。"""
    numbers = [v for v in values if _number(v)]
    return [min(numbers), max(numbers)] if numbers else None


def _mesh_list(document, failures):
    """メッシュごとの primitive（頂点・三角形・描き方・材質・形の数・属性）と、そのメッシュを使う node の名前。"""
    nodes, meshes = _items(document, "nodes"), _items(document, "meshes")
    accessors, materials = _items(document, "accessors"), _items(document, "materials")
    users = {}
    for node in nodes:
        if _node(node.get("mesh"), len(meshes)):
            users.setdefault(node["mesh"], []).append(node.get("name"))
    out = []
    for m, mesh in enumerate(meshes):
        primitives = []
        for p, primitive in enumerate(_list(mesh.get("primitives"))):
            primitive = _dict(primitive)
            attributes = _dict(primitive.get("attributes"))
            where = f"メッシュ {m}（{mesh.get('name')!r}）の primitive {p}"
            entry = {"vertices": None, "triangles": None, "mode": primitive.get("mode", 4), "material": None,
                     "targets": len(_list(primitive.get("targets"))), "attributes": sorted(k for k in attributes if isinstance(k, str))}
            position, indices = attributes.get("POSITION"), primitive.get("indices")
            for label, index in (("POSITION", position), ("indices", indices)):
                if index is not None and not _node(index, len(accessors)):
                    failures.append(f"{where} の {label} の accessor が範囲の外です: {index!r}")
            if _node(position, len(accessors)) and _count(accessors[position].get("count")):
                entry["vertices"] = accessors[position]["count"]
            if entry["mode"] == 4:                  # 三角形の並び（glTF の既定）。索引が無ければ頂点を 3 つずつ
                if indices is None:
                    entry["triangles"] = None if entry["vertices"] is None else entry["vertices"] // 3
                elif _node(indices, len(accessors)) and _count(accessors[indices].get("count")):
                    entry["triangles"] = accessors[indices]["count"] // 3
            material = primitive.get("material")
            if material is not None:
                if _node(material, len(materials)):
                    entry["material"] = materials[material].get("name")
                else:
                    failures.append(f"{where} の material が範囲の外です: {material!r}")
            primitives.append(entry)
        out.append({"mesh": m, "name": mesh.get("name"), "nodes": users.get(m, []), "primitives": primitives})
    return out


def _local_matrix(node):
    """node の親から見た 4×4 の行列（matrix か、translation・rotation・scale）。"""
    import numpy as np
    if _vector(node.get("matrix"), 16):
        return np.array(node["matrix"], dtype=np.float64).reshape(4, 4).T      # glTF の matrix は列の順
    t = node["translation"] if _vector(node.get("translation"), 3) else [0.0, 0.0, 0.0]
    x, y, z, w = node["rotation"] if _vector(node.get("rotation"), 4) else [0.0, 0.0, 0.0, 1.0]
    s = node["scale"] if _vector(node.get("scale"), 3) else [1.0, 1.0, 1.0]
    length = math.sqrt(x * x + y * y + z * z + w * w) or 1.0
    x, y, z, w = x / length, y / length, z / length, w / length
    rotation = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                         [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                         [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    out = np.eye(4)
    out[:3, :3] = rotation * np.asarray(s, dtype=np.float64)                    # R · S（列ごとに拡大）
    out[:3, 3] = t
    return out


def _world_positions(nodes, wanted, failures):
    """wanted（node の番号）の世界の位置（glTF の座標。Y が上、m）。親子は children でたどる。範囲の外の children と、
    親子の循環（どの node から親をたどっても）は failures に足し、循環にかかる位置は None。"""
    import numpy as np
    parent = {}
    for i, node in enumerate(nodes):
        children = _list(node.get("children"))
        bad = [c for c in children if not _node(c, len(nodes))]
        if bad:
            failures.append(f"node {i} の children が範囲の外です: {bad}")
        for child in children:
            if _node(child, len(nodes)):
                parent.setdefault(child, i)

    def chain(index):
        """index から根までの node の番号（循環すれば None）。"""
        seen, k = [], index
        while k is not None:
            if k in seen:
                return None
            seen.append(k)
            k = parent.get(k)
        return seen

    if any(chain(i) is None for i in parent):
        failures.append("node の親子（children）が循環しています")
    out = {}
    for index in wanted:
        path = chain(index)
        if path is None:
            out[index] = None
            continue
        matrix = np.eye(4)
        for k in path:                                  # 自分 → 親 → … → 根の順に左から掛ける
            matrix = _local_matrix(nodes[k]) @ matrix
        out[index] = [float(v) for v in matrix[:3, 3]]
    return out


def _springs(document):
    return _dict(_dict(document.get("extensions")).get("VRMC_springBone"))


def _skeleton(document, failures):
    """骨: skin の関節の数・人型の骨の node の名前と位置・揺れものの関節の node の数・人型でも揺れものでもない関節・名前がねじれらしい関節。"""
    nodes = _items(document, "nodes")
    joints = set()
    for s, skin in enumerate(_items(document, "skins")):
        listed = _list(skin.get("joints"))
        bad = [j for j in listed if not _node(j, len(nodes))]
        if bad:
            failures.append(f"skin {s} の joints が範囲の外です: {bad}")
        joints.update(j for j in listed if _node(j, len(nodes)))
    vrm = _dict(_dict(document.get("extensions")).get("VRMC_vrm"))
    bones = _dict(_dict(vrm.get("humanoid")).get("humanBones"))
    human = {name: _dict(bone).get("node") for name, bone in bones.items() if _node(_dict(bone).get("node"), len(nodes))}
    spring_nodes = {_dict(joint).get("node") for spring in _list(_springs(document).get("springs"))
                    for joint in _list(_dict(spring).get("joints"))}
    spring_nodes = {index for index in spring_nodes if _node(index, len(nodes))}
    positions = _world_positions(nodes, sorted(set(human.values())), failures)
    human_nodes = set(human.values())
    return {"skin_joints": len(joints),
            "human_bones": {name: nodes[index].get("name") for name, index in sorted(human.items())},
            "human_bone_positions": {name: positions[index] for name, index in sorted(human.items())},
            "spring_joint_nodes": len(spring_nodes),
            "other_joints": [nodes[j].get("name") for j in sorted(joints) if j not in human_nodes and j not in spring_nodes],
            "twist_like": [nodes[j].get("name") for j in sorted(joints)
                           if any(word in str(nodes[j].get("name") or "").lower() for word in TWIST_WORDS)]}


def _constraint_list(document, failures):
    """骨の拘束（VRMC_node_constraint）の node ごとの種類・source・軸・重み。"""
    nodes = _items(document, "nodes")
    out = []
    for i, node in enumerate(nodes):
        extensions = _dict(node.get("extensions"))
        if "VRMC_node_constraint" not in extensions:
            continue
        constraint = _dict(_dict(extensions["VRMC_node_constraint"]).get("constraint"))
        kind = next((k for k in ("roll", "aim", "rotation") if isinstance(constraint.get(k), dict)), None)
        body = constraint[kind] if kind else {}
        if kind is not None and not _node(body.get("source"), len(nodes)):
            failures.append(f"骨の拘束（node {i} {node.get('name')!r}）の source が範囲の外です: {body.get('source')!r}")
        out.append({"node": node.get("name"), "kind": kind, "source": _name_at(nodes, body.get("source")),
                    "axis": body.get("rollAxis", body.get("aimAxis")), "weight": body.get("weight")})
    return out


def _spring_parts(document):
    """揺れものの鎖ごとの値（関節の数・最初と最後の node・固さ・重力・引きずり・当たりの半径の [最小, 最大]・当たりの組・center）、
    当たり判定（node・形・半径・位置）と形ごとの数、当たりの組。値はファイルにあるまま（無い値は数えない）。"""
    nodes, spring = _items(document, "nodes"), _springs(document)
    groups = [_dict(group) for group in _list(spring.get("colliderGroups"))]
    chains = []
    for chain in _list(spring.get("springs")):
        chain = _dict(chain)
        joints = [_dict(joint) for joint in _list(chain.get("joints"))]
        entry = {"name": chain.get("name"), "joints": len(joints),
                 "first": _name_at(nodes, joints[0].get("node")) if joints else None,
                 "last": _name_at(nodes, joints[-1].get("node")) if joints else None}
        for key in SPRING_VALUES:
            entry[key] = _span(joint.get(key) for joint in joints)
        entry["collider_groups"] = [groups[g].get("name") if _node(g, len(groups)) else None for g in _list(chain.get("colliderGroups"))]
        entry["center"] = _name_at(nodes, chain.get("center")) if "center" in chain else None
        chains.append(entry)
    colliders, shapes = [], Counter()
    for collider in _list(spring.get("colliders")):
        collider = _dict(collider)
        shape = _dict(collider.get("shape"))
        kind = next(iter(shape), "?")
        body = _dict(shape.get(kind))
        shapes[kind] += 1
        colliders.append({"node": _name_at(nodes, collider.get("node")), "shape": kind, "radius": body.get("radius"),
                          "offset": body.get("offset"), "tail": body.get("tail")})
    return {"springs": chains, "colliders": colliders, "collider_shapes": dict(shapes),
            "collider_groups": [{"name": group.get("name"), "colliders": len(_list(group.get("colliders")))} for group in groups]}


def _material_list(document, failures):
    """材質ごとの透け方・両面・色の掛け率と色の絵、MToon の値（MToon でなければ None）と影の色の絵。値はファイルにあるまま
    （alphaMode と doubleSided だけ glTF の既定 OPAQUE・false を入れる）。"""
    materials, textures, images = _items(document, "materials"), _items(document, "textures"), _items(document, "images")

    def image_of(info, where):
        info = _dict(info)
        if "index" not in info:
            return None
        if not _node(info["index"], len(textures)):
            failures.append(f"{where} のテクスチャが範囲の外です: {info['index']!r}")
            return None
        source = textures[info["index"]].get("source")
        if source is None:                       # 画像が拡張（KHR_texture_basisu など）にしか無いテクスチャ
            return None
        if not _node(source, len(images)):
            failures.append(f"{where} のテクスチャ {info['index']} の source が範囲の外です: {source!r}")
            return None
        return images[source].get("name")

    out = []
    for m, material in enumerate(materials):
        pbr = _dict(material.get("pbrMetallicRoughness"))
        mtoon = _dict(material.get("extensions")).get("VRMC_materials_mtoon")
        where = f"材質 {m}（{material.get('name')!r}）"
        row = {"name": material.get("name"), "alphaMode": material.get("alphaMode", "OPAQUE"),
               "doubleSided": material.get("doubleSided", False), "baseColorFactor": pbr.get("baseColorFactor"),
               "baseColorImage": image_of(pbr.get("baseColorTexture"), where + " の baseColorTexture"), "mtoon": None}
        if isinstance(mtoon, dict):
            row["mtoon"] = {key: mtoon.get(key) for key in MTOON_VALUES}
            row["mtoon"]["shadeImage"] = image_of(mtoon.get("shadeMultiplyTexture"), where + " の shadeMultiplyTexture")
        out.append(row)
    return out


def _jpeg_size(data):
    """JPEG の [幅, 高さ]（大きさを持つ印 SOF から）。見つからなければ None。"""
    position = 2
    while position + 4 <= len(data):
        if data[position] != 0xFF:
            return None
        marker = data[position + 1]
        if marker == 0xFF:                                  # 詰め物
            position += 1
            continue
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:        # 長さを持たない印
            position += 2
            continue
        if marker in (0xD9, 0xDA):                          # 終わりか画像のデータ（その前に SOF が無かった）
            return None
        if marker in JPEG_SOF:
            if position + 9 > len(data):
                return None
            height, width = struct.unpack_from(">HH", data, position + 5)
            return [width, height]
        position += 2 + struct.unpack_from(">H", data, position + 2)[0]
    return None


def _image_format(data):
    """画像のバイト列の形式と [幅, 高さ]（PNG は IHDR、JPEG は SOF から。読めなければ None）。"""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        if len(data) >= 24 and data[12:16] == b"IHDR":
            width, height = struct.unpack_from(">II", data, 16)
            return "png", [width, height]
        return "png", None
    if data[:2] == b"\xff\xd8":
        return "jpeg", _jpeg_size(data)
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", None
    return None, None


def _image_list(document, blob, failures):
    """画像ごとの名前・mimeType・形式・[幅, 高さ]・バイト数。bufferView の番号が範囲の外なら failures に足す。読めない画像
    （GLB の外のバッファやファイル・BIN チャンクに収まらない）は unread に理由を書く。"""
    views = _items(document, "bufferViews")
    out = []
    for i, image in enumerate(_items(document, "images")):
        entry = {"name": image.get("name"), "mime_type": image.get("mimeType"), "format": None, "size": None, "bytes": None}
        view = image.get("bufferView")
        if view is not None and not _node(view, len(views)):
            failures.append(f"画像 {i} の bufferView が範囲の外です: {view!r}")
        elif view is not None and views[view].get("buffer", 0) != 0:
            entry["unread"] = f"bufferView {view} は GLB の外のバッファです"
        else:
            try:
                data, _ = image_bytes(document, blob, i)
            except ToolError as error:
                entry["unread"] = str(error)
            else:
                entry["bytes"] = len(data)
                entry["format"], entry["size"] = _image_format(data)
        out.append(entry)
    return out


def _accessor(document, blob, index, where, failures):
    """accessor index の値（(count, 幅) の numpy の配列）と、読めない理由（読めたら None）。accessor と bufferView の番号が範囲の外なら
    failures に足す。sparse・GLB の外のバッファ・BIN チャンクに収まらない値は読まない（理由を返す）。bufferView の無い accessor は
    glTF の決まりで全部 0。"""
    import numpy as np
    accessors, views = _items(document, "accessors"), _items(document, "bufferViews")
    if not _node(index, len(accessors)):
        failures.append(f"{where} の accessor が範囲の外です: {index!r}")
        return None, "accessor が範囲の外"
    accessor = accessors[index]
    kind, width, count = COMPONENTS.get(accessor.get("componentType")), WIDTHS.get(accessor.get("type")), accessor.get("count")
    if kind is None or width is None or not _count(count):
        return None, f"accessor {index} の componentType・type・count を読めません"
    if "sparse" in accessor:
        return None, f"accessor {index} は sparse です（読みません）"
    dtype = np.dtype(kind)
    if "bufferView" not in accessor:
        values = np.zeros((count, width), dtype)
    else:
        view_index = accessor["bufferView"]
        if not _node(view_index, len(views)):
            failures.append(f"{where} の accessor {index} の bufferView が範囲の外です: {view_index!r}")
            return None, "bufferView が範囲の外"
        view = views[view_index]
        if view.get("buffer", 0) != 0:
            return None, f"bufferView {view_index} は GLB の外のバッファです（読みません）"
        element = dtype.itemsize * width
        start, length = view.get("byteOffset", 0), view.get("byteLength")
        offset, stride = accessor.get("byteOffset", 0), view.get("byteStride", element)
        if not all(_count(v) for v in (start, length, offset, stride)) or stride < element:
            return None, f"accessor {index} か bufferView {view_index} の byteOffset・byteLength・byteStride を読めません"
        if blob is None or start + length > len(blob) or (count and offset + (count - 1) * stride + element > length):
            return None, f"accessor {index} の値が bufferView {view_index}（BIN チャンク）に収まりません"
        values = np.ndarray((count, width), dtype=dtype, buffer=blob, offset=start + offset,
                            strides=(stride, dtype.itemsize)).copy()
    if accessor.get("normalized") and dtype.kind in "iu":
        values = np.maximum(values.astype(np.float64) / np.iinfo(dtype).max, -1.0)
    return values, None


def _weight_list(document, blob, failures):
    """skin のある node ごとの、骨の重み（JOINTS_0・WEIGHTS_0）: 頂点の数・使う骨の数・1 頂点に付く骨のいちばん多い数・JOINTS_1 があるか・
    骨ごとの頂点の数（重みが WEIGHT_USED を超える (頂点, 枠) を数える）。"""
    import numpy as np
    nodes, meshes, skins = _items(document, "nodes"), _items(document, "meshes"), _items(document, "skins")
    out = []
    for n, node in enumerate(nodes):
        if "mesh" not in node or "skin" not in node:
            continue
        where = f"node {n}（{node.get('name')!r}）"
        if not _node(node["mesh"], len(meshes)) or not _node(node["skin"], len(skins)):
            failures.append(f"{where}の mesh か skin が範囲の外です: mesh {node['mesh']!r}・skin {node['skin']!r}")
            continue
        mesh, joints = meshes[node["mesh"]], _list(skins[node["skin"]].get("joints"))
        counts, vertices, most, second, unread, outside = Counter(), 0, 0, False, [], set()
        for p, primitive in enumerate(_list(mesh.get("primitives"))):
            attributes = _dict(_dict(primitive).get("attributes"))
            if "JOINTS_0" not in attributes:
                continue
            label = f"{where}の primitive {p}"
            bones, why = _accessor(document, blob, attributes["JOINTS_0"], label + " の JOINTS_0", failures)
            weights, why_weights = ((None, "WEIGHTS_0 がありません") if "WEIGHTS_0" not in attributes else
                                    _accessor(document, blob, attributes["WEIGHTS_0"], label + " の WEIGHTS_0", failures))
            if bones is None or weights is None:
                unread.append(f"primitive {p}: {why or why_weights}")
                continue
            if bones.shape != weights.shape or bones.dtype.kind != "u":
                unread.append(f"primitive {p}: JOINTS_0（{bones.dtype}・{bones.shape}）と WEIGHTS_0（{weights.shape}）の形が合いません")
                continue
            second = second or "JOINTS_1" in attributes
            vertices += len(bones)
            used = weights.astype(np.float64) > WEIGHT_USED
            if len(bones):
                most = max(most, int(used.sum(axis=1).max()))
            for slot in range(bones.shape[1]):
                values, frequency = np.unique(bones[used[:, slot], slot], return_counts=True)
                for joint, many in zip(values.tolist(), frequency.tolist()):
                    if joint >= len(joints):
                        outside.add(joint)
                        continue
                    if not _node(joints[joint], len(nodes)):       # skin の joints の範囲の外（_skeleton が不合格にしている）
                        continue
                    name = nodes[joints[joint]].get("name")
                    counts[name if name is not None else f"node {joints[joint]}"] += many
        if outside:
            failures.append(f"{where}の JOINTS_0 の番号が skin の joints の外です: {sorted(outside)}")
        entry = {"node": node.get("name"), "mesh": mesh.get("name"), "skin": node["skin"], "vertices": vertices,
                 "bones_used": len(counts), "most_bones_on_one_vertex": most, "second_joint_set": second,
                 "verts_per_bone": dict(sorted(counts.items(), key=lambda item: (-item[1], str(item[0]))))}
        if unread:
            entry["unread"] = unread
        out.append(entry)
    return out


def structure(document, blob, failures):
    """VRM の作りの一覧（数と名前だけ。良し悪しは決めない）。たどった参照が範囲の外なら failures に足す。"""
    nodes = _items(document, "nodes")
    vrm = _dict(_dict(document.get("extensions")).get("VRMC_vrm"))
    look = vrm.get("lookAt")
    report = {"extensions_used": _list(document.get("extensionsUsed")), "meshes": _mesh_list(document, failures),
              "skeleton": _skeleton(document, failures), "node_constraints": _constraint_list(document, failures),
              **_spring_parts(document), "materials": _material_list(document, failures),
              "images": _image_list(document, blob, failures),
              "look_at": {key: look.get(key) for key in LOOK_AT_VALUES} if isinstance(look, dict) else None,
              "first_person": [{"node": _name_at(nodes, _dict(a).get("node")), "type": _dict(a).get("type")}
                               for a in _list(_dict(vrm.get("firstPerson")).get("meshAnnotations"))],
              "weights": _weight_list(document, blob, failures)}
    return report


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
    report["structure"] = structure(document, blob, failures)
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
