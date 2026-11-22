"""Blender の中で動かす: 1 つの部品の中の選んだ面（顔の面など）を、材質の写しの新しい枠に分ける（model.split_material）。.blend は別の名前で書く。

    blender --background --factory-startup --python split_material.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

4 作目の 3D のダンスで、頭と上半身が 1 つの部品の、画像から作ったモデルの顔の面だけを別の材質にした（look.lit で、顔だけ段をやわらかくするため）。
面の選び方（面の中心の世界の座標と、色の絵の色）:
  選ぶ面 = 箱 region の中で、(a) 色の絵の、面の uv の中心の画素の色が color の決まりに入る面 か (b) boxes のどれかの箱の中の面。
  色は Blender の画像の画素の値（image.pixels）。uv の中心の画素は、u × 幅・v × 高さを切り捨てた画素（台本と同じ）。
選んだ面のうち、材質の枠 source_slot（既定 0）を使う面だけを、その材質の写し（名前 material）の新しい枠へ移す。ほかの面は変えない。
メッシュをほかの物体と分け合っている（リンクの複製）ときは止める（材質の枠と面の番号はメッシュの物で、ほかの物体まで変わるため）。
**検証**: 本物の Blender 5.2.4 で、台本と面ごとに同じ材質の枠になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="split_material.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def in_box(points, box):
    """点 (N, 3) が箱 {"x": [下, 上], "y": [下, 上], "z": [下, 上]}（省いた軸と null の端は限らない。端は含まない）に入るか (N,)。"""
    inside = np.ones(len(points), dtype=bool)
    for axis, key in enumerate("xyz"):
        span = box.get(key)
        if span is None:
            continue
        if span[0] is not None:
            inside &= points[:, axis] > span[0]
        if span[1] is not None:
            inside &= points[:, axis] < span[1]
    return inside


def color_match(rgb, rule):
    """色 (N, 3) が決まり {"rgb_min": [R, G, B]（どれもこれより大きい）, "red_at_least_blue": bool, "red_at_least_green": bool} に入るか (N,)。"""
    low = rule["rgb_min"]
    ok = (rgb[:, 0] > low[0]) & (rgb[:, 1] > low[1]) & (rgb[:, 2] > low[2])
    if rule.get("red_at_least_blue"):
        ok &= rgb[:, 0] >= rgb[:, 2]
    if rule.get("red_at_least_green"):
        ok &= rgb[:, 0] >= rgb[:, 1]
    return ok


def face_pixels(uv_centres, pixels):
    """面の uv の中心 (N, 2) の画素の色 (N, 3)（u × 幅・v × 高さを切り捨て。pixels は Blender の並び（下の行から）の (H, W, 4)）。"""
    height, width = pixels.shape[:2]
    x = np.clip((uv_centres[:, 0] * width).astype(int), 0, width - 1)
    y = np.clip((uv_centres[:, 1] * height).astype(int), 0, height - 1)
    return pixels[y, x, :3]


def choose(centres, colors, settings):
    """選ぶ面 (N,) と、内訳（色で選んだ面・箱だけで選んだ面）。"""
    region = in_box(centres, settings["region"])
    by_color = region & color_match(colors, settings["color"]) if settings.get("color") else np.zeros(len(centres), dtype=bool)
    by_box = np.zeros(len(centres), dtype=bool)
    for box in settings.get("boxes") or []:
        by_box |= in_box(centres, box)
    by_box &= region
    return by_color | by_box, by_color, by_box & ~by_color


def shared_with(body, objects):
    """body のメッシュを使う、ほかの物体の名前の並び（リンクの複製など）。材質の枠と面の番号はメッシュの物なので、分け合うと
    ほかの物体まで変わる。"""
    return sorted(obj.name for obj in objects if obj is not body and getattr(obj, "data", None) is not None and obj.data == body.data)


def color_image(material, pattern=None):
    """材質の色の絵: pattern（名前の一部）があれば、それを含む画像ノードの絵（1 つだけ）。無ければ、Principled BSDF の Base Color へ
    つながる画像ノードの絵。見つからなければ ValueError。"""
    nodes = [node for node in material.node_tree.nodes if node.type == "TEX_IMAGE" and node.image] if material and material.node_tree else []
    if pattern is not None:
        found = {node.image.name: node.image for node in nodes if pattern in node.image.name}
        if len(found) != 1:
            raise ValueError(f"材質 {material.name if material else None} に、名前に {pattern!r} を含む絵が 1 つではありません: {sorted(found)}")
        return next(iter(found.values()))
    for node in nodes:
        for link in node.outputs["Color"].links:
            if link.to_node.type == "BSDF_PRINCIPLED" and link.to_socket.identifier == "Base Color":
                return node.image
    raise ValueError(f"材質 {material.name if material else None} に、Base Color へつながる色の絵がありません（image で名前の一部を渡してください）")


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    body = bpy.data.objects.get(settings["object"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    others = shared_with(body, bpy.data.objects)
    if others:
        raise SystemExit(f"{body.name} のメッシュ {body.data.name} を、ほかの物体（{', '.join(others)}）も使っています。材質の枠と面の番号は"
                         "メッシュの物なので、分けるとそれらも変わります（先に、その物体のメッシュを単独の写しにしてください）")
    if bpy.data.materials.get(settings["material"]) is not None:
        raise SystemExit(f"材質 {settings['material']} はもうあります（別の名前にしてください）")
    slot = settings.get("source_slot", 0)
    if slot >= len(body.material_slots) or body.material_slots[slot].material is None:
        raise SystemExit(f"{body.name} に材質の枠 {slot} がありません（枠は {len(body.material_slots)} 個）")
    source = body.material_slots[slot].material
    mesh = body.data
    count = len(mesh.polygons)
    world = np.array(body.matrix_world)
    local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    positions = local.reshape(-1, 3).astype(np.float64) @ world[:3, :3].T + world[:3, 3]
    start = np.empty(count, dtype=np.int64)
    total = np.empty(count, dtype=np.int64)
    mesh.polygons.foreach_get("loop_start", start)
    mesh.polygons.foreach_get("loop_total", total)
    loop_vertex = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    owner = np.repeat(np.arange(count), total)                   # 面の角ごとの面の番号
    centres = np.zeros((count, 3))
    np.add.at(centres, owner, positions[loop_vertex])
    centres /= total[:, None]
    colors = np.zeros((count, 3))
    image_name = None
    if settings.get("color"):
        if mesh.uv_layers.active is None:
            raise SystemExit(f"{body.name} に uv がありません（color で選ぶには uv が要ります）")
        try:
            image = color_image(source, settings.get("image"))
        except ValueError as error:
            raise SystemExit(str(error))
        image_name = image.name
        uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        mesh.uv_layers.active.data.foreach_get("uv", uv)
        uv_centres = np.zeros((count, 2), dtype=np.float32)       # 単精度で平均する（台本と同じ。画素の切り捨ての境で揃える）
        np.add.at(uv_centres, owner, uv.reshape(-1, 2))
        uv_centres /= total[:, None].astype(np.float32)
        width, height = image.size
        pixels = np.empty(width * height * 4, dtype=np.float32)
        image.pixels.foreach_get(pixels)
        colors = face_pixels(uv_centres, pixels.reshape(height, width, 4))
    chosen, by_color, by_box_only = choose(centres, colors, settings)
    index = np.empty(count, dtype=np.int64)
    mesh.polygons.foreach_get("material_index", index)
    moving = chosen & (index == slot)
    copy = source.copy()
    copy.name = settings["material"]
    mesh.materials.append(copy)
    new_slot = len(body.material_slots) - 1
    index[moving] = new_slot
    mesh.polygons.foreach_set("material_index", index.astype(np.int32))
    mesh.update()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True)
    report = {"schema_version": "model3d-split-material/0.1", "blender": bpy.app.version_string, "object": body.name, "faces": int(count),
              "chosen_faces": int(chosen.sum()), "by_color": int(by_color.sum()), "by_box_only": int(by_box_only.sum()),
              "moved_faces": int(moving.sum()), "source_material": source.name, "material": copy.name, "slot": int(new_slot),
              "image": image_name, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"moved": report["moved_faces"]}))


if __name__ == "__main__":
    main()
