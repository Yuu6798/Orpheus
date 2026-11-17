"""Blender の中で動かす: 3D の候補（FBX・glTF・OBJ）を読み込み、全部のメッシュの頂点（世界の座標）と三角形を npz に書き出し、
粘土の見た目（簡易描画・一色・くぼみの陰）で数方向から描く（check.candidate）。元のファイルは読むだけ。

    blender --background --factory-startup --python candidate_mesh.py -- --model candidate.fbx --mesh mesh.npz \
        [--views DIR --kind body --size 520x760]

- 空のシーンに読み込む。頂点はメッシュの基本の形（形状キー・モディファイアは使わない）を、物体の置き場所で世界の座標にする（m、Z が上）。
- 描く向き: 正面（−Y から）・横（+X から）・後ろ（+Y から）と、体（body）は下から見上げた正面（裾の中と太ももの分かれ方を見る。腰から
  下を大きく）、頭（head）は斜め前。正射影。絵の幅と高さは --size。縦長の絵では、横に広い候補（T ポーズの腕）が切れないよう、横の
  範囲にも合わせる（fit_ortho。下から見上げた絵は腰から下を大きく写すので、はみ出してよい）。
**検証**: Windows の Blender 5.2.4 で、リポジトリ外の 3 作目の VRM のダンスの台本と、同じ数になり、絵は、体の正面と後ろ（台本は腕の先が
切れていた。こちらは横の範囲にも合わせる）と簡易描画の揺れ（数画素）のほかは同じになることを確かめた（docs/model3d.md）。
"""
import argparse
import os
import sys

import numpy as np

SUFFIXES = (".fbx", ".glb", ".gltf", ".obj")
CLAY = (0.78, 0.78, 0.8)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="candidate_mesh.py")
    ap.add_argument("--model", required=True)
    ap.add_argument("--mesh", required=True)
    ap.add_argument("--views", default="")
    ap.add_argument("--kind", default="body", choices=("body", "head"))
    ap.add_argument("--size", default="520x760")
    return ap.parse_args(argv)


def view_list(kind):
    """描く向き [(名前, カメラの向き（狙う点からカメラへ）)]。"""
    views = [("front", (0, -1, 0)), ("side", (1, 0, 0)), ("back", (0, 1, 0))]
    return views + ([("oblique", (0.6, -0.8, 0))] if kind == "head" else [("below", (0, -0.9, -0.55))])


def import_model(bpy, path):
    suffix = os.path.splitext(path)[1].lower()
    if suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=path)
    elif suffix in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=path)
    elif suffix == ".obj":
        bpy.ops.wm.obj_import(filepath=path)
    else:
        raise SystemExit(f"候補は {'・'.join(SUFFIXES)} のどれかにしてください: {path}")


def world_mesh(meshes):
    """全部のメッシュの頂点（世界の座標、float64）と三角形（頂点の番号）。"""
    verts, tris, offset = [], [], 0
    for ob in meshes:
        me = ob.data
        co = np.empty(len(me.vertices) * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        m = np.array(ob.matrix_world, dtype=np.float64)
        verts.append(co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3])
        me.calc_loop_triangles()
        tri = np.empty(len(me.loop_triangles) * 3, dtype=np.int64)
        me.loop_triangles.foreach_get("vertices", tri)
        tris.append(tri.reshape(-1, 3) + offset)
        offset += len(me.vertices)
    return np.concatenate(verts), np.concatenate(tris)


def fit_ortho(points, centre, right, scale, aspect, margin=1.06):
    """正射影の ortho_scale。台本と同じく scale × max(1, 横 ÷ 縦)。縦長の絵（aspect = 横 ÷ 縦 < 1）では、Blender は ortho_scale を縦の
    範囲にし、横の範囲は ortho_scale × aspect になるので、カメラの右向き right に写した点の広がり（中心 centre から左右の遠いほうの 2 倍
    × margin）が入らなければ、入るまで広げる（T ポーズの腕の先が切れないように）。"""
    ortho = scale * max(1.0, aspect)
    if aspect < 1:
        across = 2 * float(np.abs((np.asarray(points, float) - np.asarray(centre, float)) @ np.asarray(right, float)).max()) * margin
        if across > ortho * aspect:
            ortho = across / aspect
    return ortho


def render_views(bpy, meshes, folder, kind, width, height):
    """粘土の見た目で数方向から描き、folder に <向き>.png を書く。描いた向きの名前の並びを返す。"""
    from mathutils import Vector
    pts = np.concatenate([np.array([o.matrix_world @ v.co for v in o.data.vertices]) for o in meshes])
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    centre, size = (lo + hi) / 2, hi - lo
    diag = float(np.linalg.norm(size))
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    shading = scene.display.shading
    shading.light, shading.color_type, shading.single_color = "STUDIO", "SINGLE", CLAY
    shading.show_cavity, shading.cavity_type = True, "BOTH"
    shading.show_shadows, shading.show_backface_culling = False, False
    scene.render.resolution_x, scene.render.resolution_y = width, height
    scene.render.film_transparent = False
    scene.world = bpy.data.worlds.new("model3d_candidate_world")
    scene.world.color = (1, 1, 1)
    scene.view_settings.view_transform = "Standard"
    camera_data = bpy.data.cameras.new("model3d_candidate_camera")
    camera_data.type = "ORTHO"
    camera = bpy.data.objects.new("model3d_candidate_camera", camera_data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    span = max(size[2], size[0]) * 1.06
    os.makedirs(folder, exist_ok=True)
    names = []
    for name, direction in view_list(kind):
        d = Vector(direction).normalized()
        target, scale = Vector(centre), span
        if name == "below":                                # 腰から下を大きく
            target, scale = Vector((centre[0], centre[1], lo[2] + 0.50 * size[2])), size[2] * 0.62
        camera.location = target + d * (diag * 3)
        turn = (-d).to_track_quat("-Z", "Y")
        camera.rotation_euler = turn.to_euler()
        if name == "below":                                # 腰から下を大きく写す絵は、はみ出してよい
            camera_data.ortho_scale = scale * max(1.0, width / height)
        else:
            camera_data.ortho_scale = fit_ortho(pts, centre, list(turn.to_matrix().col[0]), scale, width / height)
        camera_data.clip_end = diag * 10
        scene.render.filepath = os.path.join(os.path.abspath(folder), name + ".png")
        bpy.ops.render.render(write_still=True)
        names.append(name)
    return names


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    width, height = (int(v) for v in a.size.split("x"))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    import_model(bpy, os.path.abspath(a.model))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH" and len(o.data.vertices)]
    if not meshes:
        raise SystemExit(f"候補にメッシュがありません: {a.model}")
    V, T = world_mesh(meshes)
    os.makedirs(os.path.dirname(os.path.abspath(a.mesh)), exist_ok=True)
    with open(a.mesh, "wb") as stream:
        np.savez(stream, V=V, T=T, names=np.array([o.name for o in meshes]))
    views = render_views(bpy, meshes, a.views, a.kind, width, height) if a.views else []
    print("CANDIDATE_MESH", len(V), len(T), ",".join(views))


if __name__ == "__main__":
    main()
