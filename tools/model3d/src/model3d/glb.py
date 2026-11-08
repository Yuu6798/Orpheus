"""GLB（glTF 2.0 のバイナリ）から、材質のテクスチャの画像を取り出す（標準ライブラリだけで読む）。

リポジトリ外の試作で、生成器（Tripo）の GLB から色のテクスチャ（basecolor）を取り出した台本を移した。試作は材質 0 の色だけを、
Pillow で開いて書き直したが、ここは材質と種類（slot）を選べ、既定は埋め込まれたバイトをそのまま書く（ハッシュが変わらない）。"""
import base64
import binascii
import json
import struct

from .io import ToolError

MAGIC = b"glTF"
JSON_CHUNK, BIN_CHUNK = b"JSON", b"BIN\x00"
# 材質の中のテクスチャの場所（slot の名前 → 材質の辞書のたどり方）
SLOTS = {"baseColor": ("pbrMetallicRoughness", "baseColorTexture"),
         "metallicRoughness": ("pbrMetallicRoughness", "metallicRoughnessTexture"),
         "normal": ("normalTexture",), "occlusion": ("occlusionTexture",), "emissive": ("emissiveTexture",)}
SUFFIXES = {"image/png": (".png",), "image/jpeg": (".jpg", ".jpeg"), "image/webp": (".webp",), "image/ktx2": (".ktx2",)}


def read_glb(data, where="GLB"):
    """GLB のバイト列を確かめて、(JSON の辞書, BIN のバイト列か None) を返す。頭（magic・版 2・長さ）と、
    チャンクの長さが壊れていれば止める。JSON と BIN は最初の 1 つを使い、知らない種類のチャンクは飛ばす。"""
    if len(data) < 12:
        raise ToolError(f"{where} が短すぎます（{len(data)} バイト。GLB の頭は 12 バイト）")
    magic, version, length = struct.unpack_from("<4sII", data)
    if magic != MAGIC:
        raise ToolError(f"{where} は GLB ではありません（先頭が glTF でない）")
    if version != 2:
        raise ToolError(f"{where} は glTF {version} です（読めるのは 2 だけ）")
    if length != len(data):
        raise ToolError(f"{where} の長さが合いません（頭では {length} バイト、ファイルは {len(data)} バイト。途中で切れたか、後ろに余分がある）")
    document = blob = None
    offset = 12
    while offset < length:
        if offset + 8 > length:
            raise ToolError(f"{where} のチャンクの頭が途中で切れています（{offset} バイト目）")
        size, kind = struct.unpack_from("<I4s", data, offset)
        offset += 8
        if offset + size > length:
            raise ToolError(f"{where} のチャンク（{kind!r}）が途中で切れています（{size} バイトのはずが {length - offset} バイト）")
        chunk = data[offset:offset + size]
        offset += size
        if kind == JSON_CHUNK and document is None:
            try:
                document = json.loads(chunk.decode("utf-8").rstrip("\x00 "))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ToolError(f"{where} の JSON チャンクを読めません: {error}") from error
        elif kind == BIN_CHUNK and blob is None:
            blob = chunk
    if not isinstance(document, dict):
        raise ToolError(f"{where} に JSON チャンクがありません")
    return document, blob


def _get(container, key, index, what):
    items = container.get(key)
    if not isinstance(items, list) or not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(items):
        raise ToolError(f"{what} の番号 {index!r} がありません（{key} は {len(items) if isinstance(items, list) else 0} 個）")
    return items[index]


def slot_uses(document, slot):
    """slot のテクスチャを使う材質の並び: [{"material", "name", "texture", "image"}]。テクスチャの画像が拡張にしか無い
    （KHR_texture_basisu・EXT_texture_webp など）ときは image を None にし、拡張の名前を extensions に入れる（止めるのは、
    その材質を取り出すときだけ。choose）。"""
    if slot not in SLOTS:
        raise ToolError(f"slot は {', '.join(SLOTS)} のどれかにしてください: {slot!r}")
    uses = []
    for m, material in enumerate(document.get("materials") or []):
        node = material
        for key in SLOTS[slot]:
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict) or "index" not in node:
            continue
        texture = _get(document, "textures", node["index"], f"材質 {m} の {slot} のテクスチャ")
        use = {"material": m, "name": material.get("name"), "texture": node["index"], "image": texture.get("source")}
        if use["image"] is None:
            use["extensions"] = sorted((texture.get("extensions") or {}).keys())
        uses.append(use)
    return uses


def _refuse_extension_only(uses, slot):
    for use in uses:
        if use["image"] is None:
            raise ToolError(f"材質 {use['material']} の {slot} のテクスチャは、画像が拡張（{', '.join(use['extensions']) or '不明'}）"
                            "にしかありません。拡張の画像は取り出せません")


def choose(document, slot, material=None):
    """取り出す画像を決める。material（番号か名前）を選べば、その材質の画像（ほかの材質の、拡張にしか無い画像では止めない）。
    省くと、slot を使う全部の材質が同じ画像を指すときだけ、その画像（拡張にしか無い画像が 1 つでもあれば止める）。"""
    uses = slot_uses(document, slot)
    if material is not None:
        chosen = [u for u in uses if (u["material"] == material if isinstance(material, int) and not isinstance(material, bool)
                                      else u["name"] == material)]
        if not chosen:
            names = [(m, mat.get("name")) for m, mat in enumerate(document.get("materials") or [])]
            raise ToolError(f"材質 {material!r} に {slot} のテクスチャがありません（材質: {names}）")
        _refuse_extension_only(chosen[:1], slot)
        return chosen[0], chosen
    if not uses:
        raise ToolError(f"{slot} のテクスチャを使う材質がありません")
    _refuse_extension_only(uses, slot)
    images = sorted({u["image"] for u in uses})
    if len(images) > 1:
        detail = ", ".join(f"材質 {u['material']}（{u['name']}）→ 画像 {u['image']}" for u in uses[:12])
        raise ToolError(f"材質ごとに {slot} の画像が違います（{detail}）。material で材質を選んでください")
    return uses[0], uses


def image_bytes(document, blob, index):
    """画像 index の埋め込まれたバイト列と、その画像の記録（名前・mimeType・どこにあったか）。bufferView（BIN チャンクの中）か、
    data: の URI だけを読む。外のファイルを指す URI は止める。"""
    image = _get(document, "images", index, "画像")
    record = {"index": index, "name": image.get("name"), "mimeType": image.get("mimeType")}
    if "bufferView" in image:
        view = _get(document, "bufferViews", image["bufferView"], f"画像 {index} の bufferView")
        if view.get("buffer", 0) != 0:
            raise ToolError(f"画像 {index} は GLB の外のバッファ（{view.get('buffer')}）にあります。読めるのは GLB の中だけです")
        if blob is None:
            raise ToolError(f"画像 {index} は BIN チャンクを指していますが、GLB に BIN チャンクがありません")
        start, size = view.get("byteOffset", 0), view.get("byteLength")
        if not isinstance(start, int) or not isinstance(size, int) or start < 0 or size < 0 or start + size > len(blob):
            raise ToolError(f"画像 {index} の範囲（{start} から {size} バイト）が BIN チャンク（{len(blob)} バイト）を超えます")
        record.update(source="bufferView", bufferView=image["bufferView"], byteOffset=start)
        return bytes(blob[start:start + size]), record
    uri = image.get("uri")
    if isinstance(uri, str) and uri.startswith("data:"):
        header, _, payload = uri.partition(",")
        if ";base64" not in header:
            raise ToolError(f"画像 {index} の data URI は base64 でありません")
        try:
            raw = base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ToolError(f"画像 {index} の data URI の base64 を読めません: {error}") from error
        record.update(source="data-uri", mimeType=record["mimeType"] or header[5:].split(";")[0] or None)
        return raw, record
    if uri is not None:
        raise ToolError(f"画像 {index} は外のファイル（{uri}）を指しています。読めるのは GLB の中の画像だけです")
    raise ToolError(f"画像 {index} に bufferView も uri もありません")
