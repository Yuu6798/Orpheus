"""Blender の中で動かす: 物体のメッシュ（頭など）を、Blender の外で計算するための npz に書き出す（model.export_mesh）。.blend は変えない。

    blender --background --factory-startup --python mesh_export.py -- --blend in.blend --settings settings.json \
        --out-mesh mesh.npz --report report.json [--out-texture color.png]

npz の中身（docs/model3d.md の「表情を形で作る」。リポジトリ外の 3 作目の VRM のダンスの台本と同じ形と型）:
- co (N, 3) float64: 頂点の基本の形（形状キーがあれば基準のキー）を、物体の置き場所の行列（ファイルを開いたときのまま）で世界の座標へ移したもの。
  長さは m（シーンの scale_length で直す。1 なら値はそのまま）
- edges (E, 2)・tris (T, 3)・tri_loops (T, 3) int64: 辺、三角形（Blender の loop_triangles）の頂点と面の角の番号
- uv (L, 2) float64: 今の uv の面の角ごとの座標（uv が無ければ書かない）
- normals (N, 3) float64: 頂点の法線を、置き場所の行列の 3×3 で移したもの（長さはそろえない。台本と同じ）
--out-texture を渡すと、その物体の材質（実際に描く材質。material_slots）が使う色の絵を PNG で書く（settings の image は絵の名前の一部。
省くと、使う絵が 1 つのときだけ）。
**検証**: 本物の Blender 5.2.4 で、リポジトリ外の台本の書き出しと同じ配列（バイトまで）になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import mesh_edges
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import mesh_edges


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="mesh_export.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-mesh", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--out-texture")
    return ap.parse_args(argv)


def choose_image(names, pattern=None):
    """材質が使う絵の名前（重ならない並び）から、書き出す 1 つ。pattern（名前の一部）があればそれを含むもの。1 つに決まらなければ ValueError。"""
    found = [name for name in names if pattern is None or pattern in name]
    if len(found) == 1:
        return found[0]
    where = f"名前に {pattern!r} を含む絵" if pattern is not None else "材質が使う絵"
    if not found:
        raise ValueError(f"{where}がありません（使う絵: {list(names)}）")
    raise ValueError(f"{where}が 2 つ以上あります（image で名前の一部を渡してください）: {found}")


def to_world(local, matrix, unit=1.0):
    """物体の中の座標 (N, 3) を世界の座標（m）へ。matrix は Blender から読んだ 4×4 の配列のまま（5.2.4 は単精度。台本と同じ型で掛ける）。"""
    world = local @ matrix[:3, :3].T + matrix[:3, 3]
    return world * unit if unit != 1.0 else world


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    obj = bpy.data.objects.get(settings["object"])
    if obj is None or obj.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    mesh = obj.data
    count = len(mesh.vertices)
    local = np.empty(count * 3)
    if mesh.shape_keys is not None:                      # 形状キーがあれば、基準のキーの形（台本と同じ）
        mesh.shape_keys.reference_key.data.foreach_get("co", local)
    else:
        mesh.vertices.foreach_get("co", local)
    local = local.reshape(-1, 3)
    matrix = np.array(obj.matrix_world)
    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
    unit = float(scene.unit_settings.scale_length)       # 1 Blender 単位が何 m か
    mesh.calc_loop_triangles()
    tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("vertices", tris)
    tri_loops = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("loops", tri_loops)
    normals = np.empty(count * 3)
    mesh.vertices.foreach_get("normal", normals)
    arrays = {"co": to_world(local, matrix, unit), "edges": mesh_edges(mesh), "tris": tris.reshape(-1, 3),
              "tri_loops": tri_loops.reshape(-1, 3)}
    layer = mesh.uv_layers.active
    if layer is not None:
        uv = np.empty(len(layer.data) * 2)
        layer.data.foreach_get("uv", uv)
        arrays["uv"] = uv.reshape(-1, 2)
    arrays["normals"] = normals.reshape(-1, 3) @ matrix[:3, :3].T
    os.makedirs(os.path.dirname(os.path.abspath(a.out_mesh)), exist_ok=True)
    np.savez_compressed(a.out_mesh, **arrays)
    report = {"schema_version": "model3d-export-mesh/0.1", "blender": bpy.app.version_string, "object": obj.name, "vertices": count,
              "edges": int(len(mesh.edges)), "triangles": int(len(mesh.loop_triangles)), "loops": int(len(mesh.loops)),
              "uv_layer": layer.name if layer is not None else None, "shape_keys": mesh.shape_keys is not None,
              "scale_length": unit, "matrix_world": [[float(v) for v in row] for row in matrix], "texture": None,
              "settings": settings}
    if a.out_texture:
        images = []
        for slot in obj.material_slots:                  # 実際に描く材質（枠が物体の側の上書きなら、その材質）
            material = slot.material
            if material is None or material.node_tree is None:
                continue
            for node in material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image is not None and node.image.name not in images:
                    images.append(node.image.name)
        try:
            name = choose_image(images, settings.get("image"))
        except ValueError as error:
            raise SystemExit(str(error)) from error
        copy = bpy.data.images[name].copy()              # 元の絵の設定を変えずに書く（台本と同じ）
        copy.filepath_raw = os.path.abspath(a.out_texture)
        copy.file_format = "PNG"
        copy.save()
        report["texture"] = {"image": name, "size": [int(v) for v in bpy.data.images[name].size]}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_mesh, "vertices": count}))


if __name__ == "__main__":
    main()
