"""check.vrm の作りの一覧（structure）: 合成の GLB で、骨・骨の拘束・揺れもの・当たり判定・材質・画像・骨の重み・範囲の外の参照を確かめる。"""
import io
import json
import math
import struct

import numpy as np
import pytest

from model3d import vrm


def _glb(document, blob=b""):
    """JSON と BIN のチャンクを持つ GLB のバイト列（4 バイトにそろえる）。"""
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + (struct.pack("<I4s", len(blob), b"BIN\x00") + blob if blob else b"")
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _image(kind, size):
    from PIL import Image
    stream = io.BytesIO()
    Image.new("RGB", size, (200, 120, 90)).save(stream, kind)
    return stream.getvalue()


class Blob:
    """BIN チャンクを 4 バイトそろえで積む。"""

    def __init__(self):
        self.data = bytearray()

    def add(self, raw):
        self.data += b"\x00" * (-len(self.data) % 4)
        start = len(self.data)
        self.data += raw
        return {"buffer": 0, "byteOffset": start, "byteLength": len(raw)}


def _document():
    """骨（回転・拡大・行列の親子）・skin・重み（交互に並んだ bufferView）・骨の拘束・揺れもの・当たり判定・材質・画像のそろった VRM。"""
    blob = Blob()
    positions = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 0]], np.float32)
    joints = np.array([[0, 1, 0, 0], [0, 1, 2, 0], [1, 2, 0, 0], [2, 0, 0, 0]], np.uint8)
    weights = np.array([[255, 0, 0, 0], [128, 127, 0, 0], [100, 100, 55, 0], [255, 0, 0, 0]], np.uint8)
    interleaved = np.concatenate([joints, weights], axis=1).tobytes()          # 1 頂点 8 バイト（関節 4・重み 4）
    views = [blob.add(positions.tobytes()), blob.add(np.array([0, 1, 2, 2, 1, 3], np.uint16).tobytes()),
             {**blob.add(interleaved), "byteStride": 8}, blob.add(_image("PNG", (3, 2))), blob.add(_image("JPEG", (5, 7)))]
    half = math.sqrt(0.5)
    nodes = [{"name": "root", "translation": [0, 1, 0], "rotation": [0, 0, half, half], "children": [1, 4]},   # z のまわりに 90°
             {"name": "hips", "translation": [1, 0, 0], "scale": [2, 2, 2], "children": [2]},
             {"name": "spine", "translation": [0, 0, 2]},
             {"name": "Body", "mesh": 0, "skin": 0},
             {"name": "head", "matrix": [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0.5, 0, 0, 1]}]
    nodes += [{"name": f"n{i}"} for i in range(5, 17)]
    nodes += [{"name": "hair1"}, {"name": "hair2"}, {"name": "col"}]
    nodes[5]["extensions"] = {"VRMC_node_constraint": {"specVersion": "1.0", "constraint": {"roll": {"source": 4, "rollAxis": "Y", "weight": 0.5}}}}
    nodes[6]["extensions"] = {"VRMC_node_constraint": {"constraint": {"aim": {"source": 1, "aimAxis": "PositiveX"}}}}
    nodes[7]["name"] = "upper_arm_twist.L"
    human = {name: {"node": 5 + k} for k, name in enumerate(vrm.REQUIRED_HUMAN_BONES)}
    human.update(hips={"node": 1}, spine={"node": 2}, head={"node": 4})
    document = {
        "asset": {"version": "2.0"}, "extensionsUsed": ["VRMC_vrm", "VRMC_springBone"], "nodes": nodes,
        "skins": [{"joints": [1, 2, 4, 7]}],
        "meshes": [{"name": "BodyMesh", "primitives": [
            {"attributes": {"POSITION": 0, "JOINTS_0": 2, "WEIGHTS_0": 3}, "indices": 1, "material": 0},
            {"attributes": {"POSITION": 0}, "mode": 1, "material": 1}]}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 4, "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5123, "count": 6, "type": "SCALAR"},
                      {"bufferView": 2, "byteOffset": 0, "componentType": 5121, "count": 4, "type": "VEC4"},
                      {"bufferView": 2, "byteOffset": 4, "componentType": 5121, "normalized": True, "count": 4, "type": "VEC4"}],
        "bufferViews": views, "buffers": [{"byteLength": len(blob.data)}],
        "images": [{"name": "skin.png", "mimeType": "image/png", "bufferView": 3}, {"name": "face.jpg", "mimeType": "image/jpeg", "bufferView": 4}],
        "textures": [{"source": 0}, {"source": 1}],
        "materials": [{"name": "skin", "pbrMetallicRoughness": {"baseColorFactor": [0.8, 0.8, 0.8, 1], "baseColorTexture": {"index": 0}},
                       "extensions": {"VRMC_materials_mtoon": {"shadeColorFactor": [0.512, 0.512, 0.512], "shadingToonyFactor": 0.9,
                                                               "shadeMultiplyTexture": {"index": 1}}}},
                      {"name": "plain", "alphaMode": "MASK"}],
        "extensions": {
            "VRMC_vrm": {"specVersion": "1.0", "humanoid": {"humanBones": human},
                         "meta": {"name": "合成", "authors": ["誰か"], "licenseUrl": "https://vrm.dev/licenses/1.0/", "otherLicenseUrl": ""},
                         "lookAt": {"type": "bone", "offsetFromHeadBone": [0, 0.06, 0],
                                    "rangeMapHorizontalInner": {"inputMaxValue": 90, "outputScale": 10}},
                         "firstPerson": {"meshAnnotations": [{"node": 3, "type": "auto"}]}},
            "VRMC_springBone": {
                "specVersion": "1.0",
                "colliders": [{"node": 19, "shape": {"sphere": {"radius": 0.05, "offset": [0, 0, 0]}}},
                              {"node": 1, "shape": {"capsule": {"radius": 0.03, "offset": [0, 0, 0], "tail": [0, 0.1, 0]}}}],
                "colliderGroups": [{"name": "g", "colliders": [0, 1]}],
                "springs": [{"name": "hair", "center": 1, "colliderGroups": [0],
                             "joints": [{"node": 17, "hitRadius": 0.01, "stiffness": 0.9, "gravityPower": 0.1, "dragForce": 0.4},
                                        {"node": 18, "hitRadius": 0.02, "stiffness": 0.5}]}]}}}
    return document, blob


def test_structure_lists_the_bones_springs_materials_images_and_weights():
    document, blob = _document()
    report = vrm.inspect(_glb(document, bytes(blob.data)))
    assert report["pass"] is True, report["failures"]
    assert report["meta"]["otherLicenseUrl"] == ""                 # VRM 1.0 のメタの鍵は全部写す（サムネイルの番号を除く）
    s = report["structure"]
    assert s["extensions_used"] == ["VRMC_vrm", "VRMC_springBone"]
    assert s["meshes"] == [{"mesh": 0, "name": "BodyMesh", "nodes": ["Body"], "primitives": [
        {"vertices": 4, "triangles": 2, "mode": 4, "material": "skin", "targets": 0, "attributes": ["JOINTS_0", "POSITION", "WEIGHTS_0"]},
        {"vertices": 4, "triangles": None, "mode": 1, "material": "plain", "targets": 0, "attributes": ["POSITION"]}]}]
    k = s["skeleton"]
    assert k["skin_joints"] == 4 and k["spring_joint_nodes"] == 2
    assert k["human_bones"]["hips"] == "hips" and k["human_bones"]["head"] == "head" and len(k["human_bones"]) == 15
    assert k["other_joints"] == ["upper_arm_twist.L"] and k["twist_like"] == ["upper_arm_twist.L"]   # 人型でも揺れものでもない関節
    assert s["node_constraints"] == [{"node": "n5", "kind": "roll", "source": "head", "axis": "Y", "weight": 0.5},
                                     {"node": "n6", "kind": "aim", "source": "hips", "axis": "PositiveX", "weight": None}]
    assert s["springs"] == [{"name": "hair", "joints": 2, "first": "hair1", "last": "hair2", "stiffness": [0.5, 0.9],
                             "gravityPower": [0.1, 0.1], "dragForce": [0.4, 0.4], "hitRadius": [0.01, 0.02],
                             "collider_groups": ["g"], "center": "hips"}]
    assert s["colliders"] == [{"node": "col", "shape": "sphere", "radius": 0.05, "offset": [0, 0, 0], "tail": None},
                              {"node": "hips", "shape": "capsule", "radius": 0.03, "offset": [0, 0, 0], "tail": [0, 0.1, 0]}]
    assert s["collider_shapes"] == {"sphere": 1, "capsule": 1} and s["collider_groups"] == [{"name": "g", "colliders": 2}]
    skin, plain = s["materials"]
    assert skin["baseColorFactor"] == [0.8, 0.8, 0.8, 1] and skin["baseColorImage"] == "skin.png" and skin["alphaMode"] == "OPAQUE"
    assert skin["mtoon"]["shadeColorFactor"] == [0.512, 0.512, 0.512] and skin["mtoon"]["shadeImage"] == "face.jpg"
    assert skin["mtoon"]["shadingToonyFactor"] == 0.9 and skin["mtoon"]["outlineWidthMode"] is None   # 無い値は null（既定を入れない）
    assert plain == {"name": "plain", "alphaMode": "MASK", "doubleSided": False, "baseColorFactor": None, "baseColorImage": None,
                     "mtoon": None}
    assert [(i["name"], i["format"], i["size"]) for i in s["images"]] == [("skin.png", "png", [3, 2]), ("face.jpg", "jpeg", [5, 7])]
    assert s["look_at"]["type"] == "bone" and s["look_at"]["rangeMapVerticalUp"] is None
    assert s["first_person"] == [{"node": "Body", "type": "auto"}]
    # 重みは (頂点, 枠) ごとに 1e-4 を超えるものを数える: hips 3（v0・v1・v2 の 3 つ目の枠）、spine 2、head 2
    assert s["weights"] == [{"node": "Body", "mesh": "BodyMesh", "skin": 0, "vertices": 4, "bones_used": 3,
                             "most_bones_on_one_vertex": 3, "second_joint_set": False,
                             "verts_per_bone": {"hips": 3, "head": 2, "spine": 2}}]


def test_bone_positions_follow_rotation_scale_and_matrix_of_the_parents():
    document, blob = _document()
    positions = vrm.inspect(_glb(document, bytes(blob.data)))["structure"]["skeleton"]["human_bone_positions"]
    # root は (0, 1, 0) にあり z のまわりに 90° 回る。hips の (1, 0, 0) は (0, 1, 0) へ回って (0, 2, 0)。
    # spine の (0, 0, 2) は hips の 2 倍の拡大で (0, 0, 4) 先。head は行列（列の順）の (0.5, 0, 0) が回って (0, 0.5, 0) 先
    for bone, want in (("hips", [0, 2, 0]), ("spine", [0, 2, 4]), ("head", [0, 1.5, 0]), ("leftUpperLeg", [0, 0, 0])):
        assert positions[bone] == pytest.approx(want, abs=1e-12), bone


def test_weights_and_images_that_cannot_be_read_are_listed_but_do_not_fail():
    document, blob = _document()
    document["accessors"][3]["sparse"] = {"count": 1}                                     # sparse は読まない
    document["images"][1] = {"name": "outside.jpg", "uri": "face.jpg"}                     # GLB の外のファイル
    document["bufferViews"].append({"buffer": 1, "byteOffset": 0, "byteLength": 4})
    document["images"].append({"name": "other buffer", "bufferView": 5})
    report = vrm.inspect(_glb(document, bytes(blob.data)))
    assert report["pass"] is True, report["failures"]
    weights = report["structure"]["weights"][0]
    assert weights["vertices"] == 0 and weights["unread"] == ["primitive 0: accessor 3 は sparse です（読みません）"]
    images = report["structure"]["images"]
    assert "外のファイル" in images[1]["unread"] and images[1]["format"] is None
    assert images[2]["unread"] == "bufferView 5 は GLB の外のバッファです"
    del document["meshes"][0]["primitives"][0]["attributes"]["WEIGHTS_0"]
    del document["accessors"][3]["sparse"]
    report = vrm.inspect(_glb(document, bytes(blob.data)))
    assert report["structure"]["weights"][0]["unread"] == ["primitive 0: WEIGHTS_0 がありません"]


def test_weights_read_float_weights_and_note_a_second_joint_set():
    document, blob = _document()
    raw = np.array([[1.0, 0, 0, 0], [0.00005, 0.99995, 0, 0], [0.5, 0.25, 0.25, 0], [1.0, 0, 0, 0]], np.float32)
    document["bufferViews"].append(blob.add(raw.tobytes()))
    document["accessors"].append({"bufferView": 5, "componentType": 5126, "count": 4, "type": "VEC4"})
    attributes = document["meshes"][0]["primitives"][0]["attributes"]
    attributes.update(WEIGHTS_0=4, JOINTS_1=2)
    weights = vrm.inspect(_glb(document, bytes(blob.data)))["structure"]["weights"][0]
    # v1 の 1 つ目の枠（5e-5）は数えない
    assert weights["verts_per_bone"] == {"hips": 2, "spine": 2, "head": 2} and weights["most_bones_on_one_vertex"] == 3
    assert weights["second_joint_set"] is True


def test_structure_fails_on_references_out_of_range():
    document, blob = _document()
    document["skins"][0]["joints"].append(99)
    document["meshes"][0]["primitives"][0]["material"] = 9
    document["meshes"][0]["primitives"][1]["attributes"]["POSITION"] = 42
    document["nodes"][5]["extensions"]["VRMC_node_constraint"]["constraint"]["roll"]["source"] = 99
    document["materials"][1]["pbrMetallicRoughness"] = {"baseColorTexture": {"index": 7}}
    document["textures"][1]["source"] = 9
    document["images"][0]["bufferView"] = 99
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(blob.data) - 4, "byteLength": 400})
    document["images"][1]["bufferView"] = 5
    document["nodes"].append({"name": "stray", "mesh": 5, "skin": 0})
    document["nodes"][1]["children"] = [2, 50]
    raw = bytearray(blob.data)
    view = document["bufferViews"][2]["byteOffset"]
    raw[view + 3 * 8] = 7                                         # v3 の 1 つ目の枠の関節を 7 に（skin の joints は 5 つ）
    report = vrm.inspect(_glb(document, bytes(raw)))
    text = " / ".join(report["failures"])
    assert report["pass"] is False
    for part in ("skin 0 の joints が範囲の外です: [99]", "primitive 0 の material が範囲の外です: 9",
                 "primitive 1 の POSITION の accessor が範囲の外です: 42", "骨の拘束（node 5 'n5'）の source が範囲の外です: 99",
                 "の baseColorTexture のテクスチャが範囲の外です: 7", "shadeMultiplyTexture のテクスチャ 1 の source が範囲の外です: 9",
                 "画像 0 の bufferView が範囲の外です: 99", "node 20（'stray'）の mesh か skin が範囲の外です",
                 "node 1 の children が範囲の外です: [50]", "node 3（'Body'）の JOINTS_0 の番号が skin の joints の外です: [7]"):
        assert part in text, part
    assert len(report["failures"]) == 10
    assert report["structure"]["meshes"][0]["primitives"][1]["vertices"] is None
    assert "BIN チャンク" in report["structure"]["images"][1]["unread"]      # 番号は範囲の中で、バイトが足りない画像は読まないだけ


def test_values_past_their_buffer_view_are_not_read_and_parent_loops_fail():
    document, blob = _document()
    document["accessors"][3]["count"] = 5                          # 4 + 4 × 8 + 4 = 40 バイトが 32 バイトの bufferView を超える
    document["accessors"][2]["count"] = 5
    document["nodes"][2]["children"] = [0]                         # root → hips → spine → root
    report = vrm.inspect(_glb(document, bytes(blob.data)))
    assert report["failures"] == ["node の親子（children）が循環しています"]
    assert report["structure"]["weights"][0]["unread"] == ["primitive 0: accessor 2 の値が bufferView 2（BIN チャンク）に収まりません"]
    assert report["structure"]["skeleton"]["human_bone_positions"]["hips"] is None
    document, blob = _document()
    document["nodes"][5]["children"], document["nodes"][6]["children"] = [6], [5]   # 人型の骨でない node どうしの循環
    report = vrm.inspect(_glb(document, bytes(blob.data)))
    assert report["failures"] == ["node の親子（children）が循環しています"]
    assert report["structure"]["skeleton"]["human_bone_positions"]["hips"] == pytest.approx([0, 2, 0])


def test_jpeg_and_png_sizes_are_read_from_their_headers():
    sof = b"\xff\xc2" + struct.pack(">HBHH", 17, 8, 7, 5) + b"\x00" * 10           # 高さ 7・幅 5（プログレッシブ）
    data = b"\xff\xd8" + b"\xff\xe0" + struct.pack(">H", 6) + b"JFIF" + b"\xff\xff" + sof + b"\xff\xd9"
    assert vrm._image_format(data) == ("jpeg", [5, 7])
    assert vrm._image_format(b"\xff\xd8\xff\xda\x00\x02\xff\xd9") == ("jpeg", None)  # SOF の前に画像のデータ
    assert vrm._image_format(b"\xff\xd8\x00\x00") == ("jpeg", None)                  # 印でないバイト
    assert vrm._image_format(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) == ("png", None)   # IHDR が無い
    assert vrm._image_format(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == ("webp", None)
    assert vrm._image_format(b"GIF89a") == (None, None)
