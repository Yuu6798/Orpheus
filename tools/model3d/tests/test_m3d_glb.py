"""GLB のテクスチャの取り出し（texture.extract_glb と model3d.glb）: GLB の頭とチャンクの確かめ、材質と種類（slot）の選び方、
埋め込まれた画像の場所（bufferView・data URI）、そのまま書くか Pillow で書き直すか、壊れた GLB と悪い設定を止めること。"""
import base64
import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import glb
from model3d.io import ToolError, atomic_json


def _image(fmt, color=(200, 30, 40), size=(4, 3), mode="RGB"):
    stream = io.BytesIO()
    Image.new(mode, size, color if mode == "RGB" else color + (128,)).save(stream, fmt)
    return stream.getvalue()


def _pack(document, blob=b""):
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text
    if blob is not None:
        padded = blob + b"\x00" * (-len(blob) % 4)
        body += struct.pack("<I4s", len(padded), b"BIN\x00") + padded
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _document(images, materials):
    """images: [(バイト列, mimeType)]。材質は glTF の materials の並び。"""
    blob, views, entries = b"", [], []
    for k, (data, mime) in enumerate(images):
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(data)})
        entries.append({"name": f"image{k}", "mimeType": mime, "bufferView": k})
        blob += data + b"\x00" * (-len(data) % 4)
    document = {"asset": {"version": "2.0"}, "images": entries, "bufferViews": views, "buffers": [{"byteLength": len(blob)}],
                "textures": [{"source": k} for k in range(len(images))], "materials": materials}
    return document, blob


def _material(name, base=None, normal=None):
    material = {"name": name, "pbrMetallicRoughness": {}}
    if base is not None:
        material["pbrMetallicRoughness"]["baseColorTexture"] = {"index": base}
    if normal is not None:
        material["normalTexture"] = {"index": normal}
    return material


def test_the_header_and_chunks_are_checked():
    document, blob = _document([(_image("PNG"), "image/png")], [_material("skin", base=0)])
    data = _pack(document, blob)
    parsed, found = glb.read_glb(data)
    assert parsed["materials"][0]["name"] == "skin" and found.startswith(_image("PNG"))
    for broken, message in [(data[:8], "短すぎ"), (b"glTX" + data[4:], "GLB ではありません"),
                            (data[:4] + struct.pack("<I", 1) + data[8:], "glTF 1"),
                            (data + b"\x00" * 4, "長さが合いません"),
                            (data[:12] + struct.pack("<I4s", 10 ** 6, b"JSON") + data[20:], "途中で切れています"),
                            (struct.pack("<4sII", b"glTF", 2, 16) + b"\x00" * 4, "チャンクの頭が途中で切れて"),
                            (struct.pack("<4sII", b"glTF", 2, 12), "JSON チャンクがありません")]:
        with pytest.raises(ToolError, match=message):
            glb.read_glb(broken)
    bad_json = struct.pack("<4sII", b"glTF", 2, 24) + struct.pack("<I4s", 4, b"JSON") + b"{{{{"
    with pytest.raises(ToolError, match="JSON チャンクを読めません"):
        glb.read_glb(bad_json)


def test_the_image_is_chosen_by_slot_and_material():
    images = [(_image("PNG"), "image/png"), (_image("PNG", (10, 20, 30)), "image/png"), (_image("PNG", (0, 0, 255)), "image/png")]
    shared, _ = _document(images, [_material("a", base=0, normal=2), _material("b", base=0)])
    use, users = glb.choose(shared, "baseColor")
    assert use["image"] == 0 and [u["material"] for u in users] == [0, 1]           # 全部の材質が同じ画像
    assert glb.choose(shared, "normal")[0]["image"] == 2
    split, _ = _document(images, [_material("a", base=0), _material("b", base=1)])
    with pytest.raises(ToolError, match="material で材質を選んで"):
        glb.choose(split, "baseColor")
    assert glb.choose(split, "baseColor", 1)[0]["image"] == 1 and glb.choose(split, "baseColor", "a")[0]["image"] == 0
    with pytest.raises(ToolError, match="材質 'c'"):
        glb.choose(split, "baseColor", "c")
    with pytest.raises(ToolError, match="使う材質がありません"):
        glb.choose(split, "emissive")
    with pytest.raises(ToolError, match="slot は"):
        glb.choose(split, "albedo")
    webp, _ = _document(images, [_material("a", base=0)])
    webp["textures"][0] = {"extensions": {"EXT_texture_webp": {"source": 0}}}
    with pytest.raises(ToolError, match="EXT_texture_webp"):
        glb.choose(webp, "baseColor")
    missing, _ = _document(images, [_material("a", base=7)])
    with pytest.raises(ToolError, match="番号 7"):
        glb.choose(missing, "baseColor")


def test_embedded_images_are_read_from_the_bin_chunk_or_a_data_uri():
    png = _image("PNG")
    document, blob = _document([(png, "image/png")], [_material("a", base=0)])
    raw, record = glb.image_bytes(document, blob, 0)
    assert raw == png and record["source"] == "bufferView" and record["mimeType"] == "image/png"
    document["images"].append({"uri": "data:image/png;base64," + base64.b64encode(png).decode()})
    raw, record = glb.image_bytes(document, blob, 1)
    assert raw == png and record["source"] == "data-uri" and record["mimeType"] == "image/png"
    document["images"].append({"uri": "textures/skin.png"})
    with pytest.raises(ToolError, match="外のファイル"):
        glb.image_bytes(document, blob, 2)
    document["images"].append({"uri": "data:image/png," + "plain"})
    with pytest.raises(ToolError, match="base64"):
        glb.image_bytes(document, blob, 3)
    document["bufferViews"].append({"buffer": 1, "byteLength": 4})
    document["images"].append({"bufferView": 1, "mimeType": "image/png"})
    with pytest.raises(ToolError, match="外のバッファ"):
        glb.image_bytes(document, blob, 4)
    document["bufferViews"].append({"buffer": 0, "byteOffset": len(blob) - 2, "byteLength": 8})
    document["images"].append({"bufferView": 2, "mimeType": "image/png"})
    with pytest.raises(ToolError, match="超えます"):
        glb.image_bytes(document, blob, 5)
    with pytest.raises(ToolError, match="BIN チャンクがありません"):
        glb.image_bytes(document, None, 0)


def _recipe(tmp_path, parameters, image="out.png", name="model.glb"):
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "glb", "inputs": {"glb": {"path": name}},
                                           "steps": [{"id": "extract", "operation": "texture.extract_glb",
                                                      "inputs": {"glb": "$inputs.glb"}, "parameters": parameters,
                                                      "outputs": {"image": image, "report": "extract.json"}}]})
    return tmp_path / "recipe.json"


def test_the_operation_writes_the_embedded_bytes_and_records_where_they_came_from(tmp_path):
    from model3d import executor
    png = _image("PNG")
    document, blob = _document([(png, "image/png"), (_image("PNG", (1, 2, 3)), "image/png")],
                               [_material("skin", base=0, normal=1)])
    (tmp_path / "model.glb").write_bytes(_pack(document, blob))
    result = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert result["status"] == "succeeded"
    step = result["steps"]["extract"]
    assert Path(step["artifacts"]["image"]).read_bytes() == png                     # 埋め込まれたバイトそのまま
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["schema_version"] == "model3d-glb-image/0.1" and report["slot"] == "baseColor"
    assert report["image"]["sha256"] == report["written"]["sha256"] and report["size"] == [4, 3] and report["mode"] == "RGB"
    assert report["materials"] == [{"index": 0, "name": "skin"}] and report["material"] is None and not report["written"]["decoded"]
    assert step["observations"][0]["kind"] == "extract_glb"
    again = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert again["steps"]["extract"]["reused_from"] is not None                     # 入力の GLB だけで決まるので再利用する
    normal = executor.run_recipe(_recipe(tmp_path, {"slot": "normal"}, "normal.png"), tmp_path / "runs")
    with Image.open(normal["steps"]["extract"]["artifacts"]["image"]) as image:
        assert image.getpixel((0, 0)) == (1, 2, 3)


def test_a_jpeg_keeps_its_bytes_or_is_decoded_into_the_output_format(tmp_path):
    from model3d import executor
    jpeg = _image("JPEG", (90, 140, 200), size=(16, 8))
    document, blob = _document([(jpeg, "image/jpeg")], [_material("skin", base=0)])
    (tmp_path / "model.glb").write_bytes(_pack(document, blob))
    refused = executor.run_recipe(_recipe(tmp_path, {}), tmp_path / "runs")
    assert refused["status"] == "failed" and "decode: true" in refused["steps"]["extract"]["error"]
    raw = executor.run_recipe(_recipe(tmp_path, {}, "out.jpg"), tmp_path / "runs")
    assert Path(raw["steps"]["extract"]["artifacts"]["image"]).read_bytes() == jpeg
    decoded = executor.run_recipe(_recipe(tmp_path, {"decode": True}), tmp_path / "runs")
    out = Path(decoded["steps"]["extract"]["artifacts"]["image"])
    with Image.open(out) as image, Image.open(io.BytesIO(jpeg)) as source:
        assert image.format == "PNG" and np.array_equal(np.asarray(image), np.asarray(source))   # 画素は JPEG を開いたまま


def test_writing_a_translucent_image_as_jpeg_is_refused(tmp_path):
    from model3d import executor
    document, blob = _document([(_image("PNG", mode="RGBA"), "image/png")], [_material("glass", base=0)])
    (tmp_path / "model.glb").write_bytes(_pack(document, blob))
    result = executor.run_recipe(_recipe(tmp_path, {"decode": True}, "out.jpg"), tmp_path / "runs")
    assert result["status"] == "failed" and "書けません" in result["steps"]["extract"]["error"]


class Context:
    tools = {}

    def __init__(self, step_dir):
        self.step_dir = step_dir
        self.recipe_dir = step_dir


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"decode": "yes"}, "decode"),
    ({"material": -1}, "material"),
    ({"material": True}, "material"),
    ({"slot": "albedo"}, "slot"),
])
def test_the_operation_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    document, blob = _document([(_image("PNG"), "image/png")], [_material("skin", base=0)])
    (tmp_path / "model.glb").write_bytes(_pack(document, blob))
    with pytest.raises(ToolError, match=message):
        get("texture.extract_glb").function({"glb": tmp_path / "model.glb"}, parameters, {"image": tmp_path / "o.png"},
                                            Context(tmp_path))
