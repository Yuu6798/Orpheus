"""Blender の中で目の板と瞳を入れ、目の形の値を出す。"""

import argparse
import json
from pathlib import Path
import sys

import numpy as np

def add_material(bpy, mesh, image_path, name):
    image = bpy.data.images.load(str(image_path), check_existing=False)
    image.pack()
    material = bpy.data.materials.new(name)
    material.use_nodes = True
    tree = material.node_tree
    node = tree.nodes.new("ShaderNodeTexImage")
    node.image = image
    tree.nodes.active = node
    shader = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
    tree.links.new(node.outputs["Color"], shader.inputs["Base Color"])
    mesh.materials.append(material)
    return material.name


def add_surface(bm, attribute, marker, material, data, matrix, shapes, basis, uv, deform, group, followers):
    points, uvs, *parts = data
    local = points @ matrix[:3, :3].T + matrix[:3, 3]
    verts = []
    for index, point in enumerate(local):
        v = bm.verts.new(point)
        v[attribute] = marker
        v[deform][group] = 1.0
        for layer in shapes:
            value = v.co.copy()
            for source, weight in followers.get(index, ()):
                value += (source[layer] - source[basis]) * weight
            v[layer] = value
        verts.append(v)
    for faces in parts:
        for indices in faces:
            corners = points[indices]
            if np.cross(corners[1] - corners[0], corners[2] - corners[0])[1] > 0:
                indices = indices[::-1]
            face = bm.faces.new([verts[i] for i in indices])
            face.material_index = material
            face.smooth = True
            for loop, i in zip(face.loops, indices):
                loop[uv].uv = uvs[i]


def build(bpy, bmesh, ob, atlas, config):
    from model3d.blender_adapter.eye_math import GeometryError, SCHEMA, SIDES, validate
    from model3d.blender_adapter.model_edit import world_points
    from model3d.eye_build import make_eye
    from model3d.face_shapes import Head

    mesh = ob.data
    if ob.library or mesh.library or mesh.users != 1 + int(mesh.use_fake_user) or ob.mode != "OBJECT":
        raise GeometryError("書き換えられる単独のメッシュをオブジェクトモードで使ってください")
    group = ob.vertex_groups.get(config["head_group"])
    if group is None:
        raise GeometryError(f"頭の頂点グループ {config['head_group']!r} がありません")
    mesh.calc_loop_triangles()
    if not len(mesh.loop_triangles):
        raise GeometryError("頭のメッシュに面がありません")
    co = world_points(ob)
    edges = np.empty(len(mesh.edges) * 2, np.int32)
    mesh.edges.foreach_get("vertices", edges)
    tris = np.empty(len(mesh.loop_triangles) * 3, np.int32)
    mesh.loop_triangles.foreach_get("vertices", tris)
    polygons = np.empty(len(mesh.loop_triangles), np.int32)
    mesh.loop_triangles.foreach_get("polygon_index", polygons)
    head = Head(co, edges.reshape(-1, 2), tris.reshape(-1, 3))
    skin_vertex = config.get("skin_vertex")
    if skin_vertex is not None and skin_vertex >= head.n:
        raise GeometryError("skin_vertex が頂点の数を超えています")
    skin = head.skin if skin_vertex is None else head.island_of == head.island_of[skin_vertex]
    regions, removed, lids = {}, set(), set()
    for side in SIDES:
        regions[side] = area = make_eye(head, config["eyes"][side], config, skin, removed, polygons)
        if lids.intersection(area["lids"]):
            raise GeometryError("見開きで動かすまぶたの頂点が左右で重なっています")
        lids.update(area["lids"])
        removed.update(area["remove"])
    if lids.intersection(removed):
        raise GeometryError("見開きで動かすまぶたに、取り除く頂点が含まれています")

    matrix = np.array(ob.matrix_world.inverted())
    material_slot = len(mesh.materials)
    material = add_material(bpy, mesh, atlas, config["material"])
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        tag = bm.verts.layers.int.new("_model3d_eye_build_marker")
        tag_name = tag.name
        deform = bm.verts.layers.deform.verify()
        render_uv = next((layer for layer in mesh.uv_layers if layer.active_render), None)
        uv = bm.loops.layers.uv.get(render_uv.name) if render_uv else bm.loops.layers.uv.verify()
        wants_shapes = mesh.shape_keys is not None
        shapes = [bm.verts.layers.shape[key.name] for key in mesh.shape_keys.key_blocks] if wants_shapes else []
        basis = bm.verts.layers.shape[mesh.shape_keys.reference_key.name] if wants_shapes else None
        old = list(bm.verts)
        for index, side in enumerate(SIDES, 1):
            data = regions[side]
            for vertex in data["lids"]:
                old[vertex][tag] = index + 4
            points, uvs, faces, bent = data["plate"]
            followers = {}
            if shapes:
                # 形状キーごとに、正面で近い肌 3 頂点の動きを距離の二乗の逆数で写す。
                candidates = data["skin"][~np.isin(data["skin"], list(removed))]
                if bent.any() and len(candidates) < 3:
                    raise GeometryError("板の端に形状キーを写す肌の頂点が 3 つありません")
                skin_xz = co[candidates][:, [0, 2]]
                for idx in np.flatnonzero(bent):
                    distances = np.sum((skin_xz - points[idx, [0, 2]]) ** 2, axis=1)
                    near = np.argsort(distances, kind="stable")[:3]
                    weights = 1 / np.maximum(distances[near], np.finfo(float).eps)
                    followers[idx] = [(old[v], w) for v, w in zip(candidates[near], weights / weights.sum())]
            add_surface(bm, tag, index, material_slot, (points, uvs, faces),
                        matrix, shapes, basis, uv, deform, group.index, followers)
            add_surface(bm, tag, index + 2, material_slot, data["iris"],
                        matrix, shapes, basis, uv, deform, group.index, {})
        bmesh.ops.delete(bm, geom=[old[i] for i in sorted(removed)], context="VERTS")
        bm.to_mesh(mesh)
    finally:
        bm.free()
    mesh.update()
    flags = np.empty(len(mesh.vertices), np.int32)
    attribute = mesh.attributes[tag_name]
    attribute.data.foreach_get("value", flags)
    mesh.attributes.remove(attribute)

    geometry = {"schema_version": SCHEMA, "object": ob.name, "eyes": {}}
    report = {"schema_version": "model3d-eye-build/0.1", "vertices_before": head.n, "vertices_after": len(flags),
              "removed_vertices": len(removed), "material": material, "eyes": {}}
    for index, side in enumerate(SIDES, 1):
        eye, area = config["eyes"][side], regions[side]
        geometry["eyes"][side] = {"sphere": area["sphere"], "pupil": eye["pupil"],
                                 "iris": dict(zip(("a", "b"), eye["iris"])), "rim": area["rim"],
                                 "window": eye["window"], "iris_vertices": np.flatnonzero(flags == index + 2).tolist(),
                                 "lid_vertices": np.flatnonzero(flags == index + 4).tolist(),
                                 "plate_vertices": np.flatnonzero(flags == index).tolist()}
        report["eyes"][side] = {"plate_vertices": len(area["plate"][0]), "plate_faces": len(area["plate"][2]),
                                "bent_vertices": int(area["plate"][3].sum()), "iris_vertices": len(area["iris"][0]),
                                "iris_faces": len(area["iris"][2]) + len(area["iris"][3]),
                                "removed_vertices": len(area["remove"]), "opening_pixels": area["opening_pixels"]}
    return validate(geometry, vertex_count=len(mesh.vertices)), report


def main():
    import bmesh
    import bpy

    if not __package__:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from model3d.blender_adapter.eye_math import GeometryError
    from model3d.eye_build import validate_settings

    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("blend", "atlas", "settings", "out", "geometry", "report"):
        parser.add_argument("--" + flag, required=True, type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:])
    paths = {key: path.resolve() for key, path in vars(args).items()}
    if len(set(paths.values())) != len(paths) or paths["out"].suffix.lower() != ".blend":
        raise SystemExit("入力と出力は別のファイルにし、out を .blend にしてください")
    settings = validate_settings(json.loads(paths["settings"].read_text(encoding="utf-8")))
    bpy.ops.wm.open_mainfile(filepath=str(paths["blend"]), use_scripts=False)
    ob = bpy.data.objects.get(settings["object"])
    if ob is None or ob.type != "MESH":
        raise SystemExit(f"頭のメッシュ {settings['object']!r} がありません")
    try:
        geometry, report = build(bpy, bmesh, ob, paths["atlas"], settings)
    except GeometryError as exc:
        raise SystemExit(str(exc)) from exc
    for key in ("out", "report", "geometry"):
        paths[key].parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(paths["out"]), check_existing=False, copy=True, compress=True)
    for key, data in (("report", report), ("geometry", geometry)):
        paths[key].write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
