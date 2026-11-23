"""Blender の中で動かす: .blend の材質の画像ノードのうち、名前の合う画像だけを新しいテクスチャに差し替えて詰め込み、別の .blend に書く。

    blender --background --factory-startup --python apply_texture.py -- --blend in.blend --texture new.png \
        --out-blend out.blend --report report.json --settings settings.json

settings.json（texture.apply_to_blend が書く）:
- match: 差し替える画像の名前（画像の名前かファイルのパスの、ファイル名だけを比べる。大文字と小文字、Blender の重複の印 .001 は見ない）。
- min_replacements: 差し替えるノードの最少の数（足りなければ止め、今ある画像を並べる）。
- image_name: 新しい画像に付ける名前。
- preserve_image_datablocks: 画像データブロックを残し、中身を差し替える。MToon の設定などの参照も同じ画像へ向く。

リポジトリ外の試作の手順と同じ:
- 材質のノードの木を、ノードグループの中までたどって、画像のある画像ノードを集める（同じ木は 1 度だけ）。
- 合うノードの画像は、色空間が 1 つにそろい、新しいテクスチャと同じ大きさであること。新しい画像はその色空間で読む。
- 合うノードだけを新しい画像につなぎ替えて詰め込み、詰め込んだバイトの sha256 が元の PNG と同じことを確かめる。
- 合わないノードは、前と同じ画像のままであることを確かめる。形・骨・材質のつなぎ方は変えない。
- 圧縮して別の .blend に書き、入力の .blend とテクスチャが変わっていないことを確かめる。
**検証**: 本物の Blender 4.5.15 で、試作と同じ差し替えになることを確かめた（docs/model3d.md）。
"""
import argparse
import hashlib
import json
import ntpath
import os
import re
import sys


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_name(name):
    """画像の名前かパスの、比べる形: ファイル名だけ、Blender の重複の印（.001）を外し、小文字に。Blender の相対パスの頭の // は
    先に外す（そのままだと Windows の共有フォルダの名前と読まれ、ファイル名が空になる）。"""
    name = name or ""
    if name.startswith("//"):
        name = name[2:]
    return re.sub(r"\.\d{3}$", "", ntpath.basename(name)).lower()


def material_image_nodes(bpy):
    """材質ごとの、画像のある画像ノード（材質の名前, ノードの木, ノード）。ノードグループの中までたどり、同じ木は 1 度だけ。"""
    seen = set()

    def visit(tree, owner):
        if tree is None or tree.as_pointer() in seen:
            return
        seen.add(tree.as_pointer())
        for node in tree.nodes:
            if node.type == "TEX_IMAGE" and node.image is not None:
                yield owner, tree, node
            elif node.type == "GROUP" and node.node_tree is not None:
                yield from visit(node.node_tree, owner)

    for material in bpy.data.materials:
        if material.use_nodes:
            yield from visit(material.node_tree, material.name)


def pack_verified(image, expected_hash):
    image.pack()
    if image.packed_file is None:
        raise SystemExit("新しいテクスチャを詰め込めませんでした")
    if hashlib.sha256(image.packed_file.data).hexdigest() != expected_hash:
        raise SystemExit("詰め込んだバイトが、渡したテクスチャと違います")


def replace_image_contents(image, texture, expected_size, expected_hash):
    colorspace = image.colorspace_settings.name
    if image.packed_file is not None:
        image.unpack(method="REMOVE")  # .blend からだけ外し、元の画像ファイルへ書き戻さない。
    image.filepath = texture
    image.reload()
    image.colorspace_settings.name = colorspace
    if tuple(image.size) != expected_size:
        raise SystemExit(f"{image.name!r} の画像の読み直しで大きさが変わりました")
    pack_verified(image, expected_hash)


def parse_args(argv):
    parser = argparse.ArgumentParser(description="replace matched image nodes with a packed texture")
    for flag in ("--blend", "--texture", "--out-blend", "--report", "--settings"):
        parser.add_argument(flag, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    source, texture, output = (os.path.abspath(p) for p in (a.blend, a.texture, a.out_blend))
    if output in (source, texture) or not output.lower().endswith(".blend"):
        raise SystemExit("出力は入力と別の .blend にしてください")
    hashes = {"blend": sha256(source), "texture": sha256(texture)}
    bpy.ops.wm.open_mainfile(filepath=source)
    nodes = list(material_image_nodes(bpy))
    wanted = canonical_name(settings["match"])
    matches = [(owner, tree, node) for owner, tree, node in nodes
               if wanted in {canonical_name(node.image.name), canonical_name(node.image.filepath)}]
    if len(matches) < settings["min_replacements"]:
        existing = sorted({node.image.name + " | " + node.image.filepath for _, _, node in nodes})
        raise SystemExit(f"{settings['match']!r} の画像ノードが {len(matches)} 個しかありません"
                         f"（{settings['min_replacements']} 個以上のはず）。今ある画像: {existing}")
    spaces = {node.image.colorspace_settings.name for _, _, node in matches}
    if len(spaces) != 1:
        raise SystemExit(f"合う画像ノードの色空間がそろっていません: {sorted(spaces)}")
    before = {node.as_pointer(): node.image.as_pointer() for _, _, node in nodes}
    matched = {node.as_pointer() for _, _, node in matches}
    preserve = settings.get("preserve_image_datablocks", False)
    replacement = bpy.data.images.load(texture, check_existing=False)
    if not preserve:
        replacement.name = settings["image_name"]
    replacement.colorspace_settings.name = spaces.pop()
    if replacement.size[0] < 1 or replacement.size[1] < 1:
        raise SystemExit("新しいテクスチャを画像として読めません")
    size = tuple(replacement.size)
    colorspace = replacement.colorspace_settings.name
    old_sizes = {tuple(node.image.size) for _, _, node in matches}
    if old_sizes != {size}:
        raise SystemExit(f"新しいテクスチャの大きさ {size} が、今の画像 {sorted(old_sizes)} と違います")
    replacements = [{"material": owner, "node_tree": tree.name, "node": node.name, "old_image": node.image.name,
                     "old_image_path": node.image.filepath, "new_image": node.image.name if preserve else replacement.name}
                    for owner, tree, node in matches]
    preserved_names = []
    if preserve:
        originals = {node.image.as_pointer(): node.image for _, _, node in matches}
        unsupported = [image.name for image in originals.values() if image.source != "FILE"]
        if unsupported:
            raise SystemExit(f"画像をそのまま使う差し替えには単一ファイルの画像が要ります: {unsupported}")
        preserved_names = sorted(image.name for image in originals.values())
        bpy.data.images.remove(replacement)
        for image in originals.values():
            replace_image_contents(image, texture, size, hashes["texture"])
        image_name = preserved_names[0] if len(preserved_names) == 1 else None
    else:
        for _, _, node in matches:
            node.image = replacement
        pack_verified(replacement, hashes["texture"])
        if any(node.image != replacement for _, _, node in matches):
            raise SystemExit("つなぎ替えていない画像ノードがあります")
        image_name = replacement.name
    unrelated = 0
    for _, _, node in nodes:
        pointer = node.as_pointer()
        if pointer not in matched:
            unrelated += 1
        if (preserve or pointer not in matched) and node.image.as_pointer() != before[pointer]:
            raise SystemExit("同じ画像を指すべきノードの画像が変わりました")
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=output, check_existing=False, compress=True)
    if not os.path.isfile(output) or os.path.getsize(output) == 0:
        raise SystemExit("別の .blend を書けませんでした")
    unchanged = sha256(source) == hashes["blend"] and sha256(texture) == hashes["texture"]
    if not unchanged:
        raise SystemExit("入力の .blend かテクスチャが、途中で変わりました")
    report = {"schema_version": "model3d-apply-texture/0.1", "blender": bpy.app.version_string,
              "input_blend_sha256": hashes["blend"], "input_texture_sha256": hashes["texture"],
              "output_blend_sha256": sha256(output), "match": settings["match"], "image_name": image_name,
              "colorspace": colorspace, "replaced_node_count": len(matches),
              "unrelated_image_nodes_verified_unchanged": unrelated, "replacement_texture_packed": True,
              "packed_texture_sha256": hashes["texture"], "atlas_size_wh": list(size), "input_files_unchanged": True,
              "replacements": replacements, "settings": settings}
    if preserve:
        report["preserved_image_names"] = preserved_names
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"replaced": len(matches), "unrelated": unrelated}))


if __name__ == "__main__":
    main()
