"""Blender の中で動かす: 目を閉じたとき用の形（形状キー。既定 eyes_closed）を頭に足す（model.closed_eye_key）。

    blender --background --factory-startup --python closed_eye_key.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 2 作目の VRM のダンスで分かったこと）: 画像から作った頭は、顔の面の目の所が穴で、5〜15 mm 奥に眼球の面があり、
まつ毛は顔より手前に出た別の板（両面）だった。目を閉じた絵（閉じた目の線）を写すと、正面からは合うが、斜めから見ると穴の縁と奥の面で線が
ずれて、欠けたり段になったりした。
形（左右の目ごと。左は x > 0、右は x < 0）:
  - 目のまわりの箱（|x| が eye_x、高さ eye_z、y が front_y より手前）に全部収まる島のうち、eyeball（頂点 min_verts 以上・高さの幅 min_height
    以上）の島を眼球の面とし、その中心にいちばん近い「顔の面の縁の輪」（顔の島＝いちばん大きい島の、面が 1 枚しか付いていない辺の輪。
    rim_min_verts 頂点以上）を目の穴の縁とする。
  - 眼球の面は、穴の縁に張った膜の位置まで手前に出す（縁の頂点の y を、x-z の距離の 2 乗の逆数で混ぜた値 + membrane）。左右と高さは
    変えないので、写した絵はずれない。
  - ほかの、箱に全部収まる島（まつ毛の板・目頭の小さな部品）は、頭の中へ tuck（m）引っ込める（閉じた目の線は絵で描く）。
値は 0（開いた目）。render.passes の expressions の shape_keys で、閉じた目の絵の間だけ 1 にする。元のファイルは変えない。
計算はリポジトリ外の台本と同じ順。**検証**: 本物の Blender 5.2.4 で、その台本と同じ形状キーになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import islands, mesh_edges
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import islands, mesh_edges

DEFAULTS = {"head": None, "key": "eyes_closed", "eye_x": None, "eye_z": None, "front_y": None,
            "eyeball": {"min_verts": 100, "min_height": 0.02}, "rim_min_verts": 6, "membrane": 0.0002, "tuck": 0.03}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="closed_eye_key.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    out = {**DEFAULTS, **settings}
    out["eyeball"] = {**DEFAULTS["eyeball"], **settings.get("eyeball", {})}
    return out


def boundary_loops(count, polygons_edge_keys, roots, face_root):
    """顔の島の、面が 1 枚しか付いていない辺の頂点 (count,) と、縁の輪ごとの番号 (count,)（台本と同じ union-find）。"""
    edge_faces = {}
    for keys in polygons_edge_keys:
        for key in keys:
            edge_faces[key] = edge_faces.get(key, 0) + 1
    boundary = np.zeros(count, dtype=bool)
    loop_parent = np.arange(count)

    def loop_find(a):
        while loop_parent[a] != a:
            loop_parent[a] = loop_parent[loop_parent[a]]
            a = loop_parent[a]
        return a

    for (a, b), number in edge_faces.items():
        if number == 1 and roots[a] == face_root:
            boundary[a] = boundary[b] = True
            ra, rb = loop_find(a), loop_find(b)
            if ra != rb:
                loop_parent[ra] = rb
    return boundary, np.array([loop_find(i) for i in range(count)])


def closed_shape(points, roots, boundary, loops, settings):
    """目を閉じた形の頂点 (N, 3)（世界の座標）と、左右ごとの報告。目の箱に眼球の面か穴の縁が無ければ ValueError。"""
    sizes = {root: int((roots == root).sum()) for root in np.unique(roots)}
    face_root = max(sizes, key=sizes.get)
    moved = points.copy()
    report = {}
    (x_low, x_high), (z_low, z_high) = settings["eye_x"], settings["eye_z"]
    for side, sign in (("L", 1.0), ("R", -1.0)):
        x = points[:, 0] * sign
        near_eye = (x > x_low) & (x < x_high) & (points[:, 2] > z_low) & (points[:, 2] < z_high) & (points[:, 1] < settings["front_y"])
        eyeball = None
        for root, size in sizes.items():
            members = (roots == root) & near_eye
            if (root != face_root and size >= settings["eyeball"]["min_verts"] and members.sum() == size
                    and points[members, 2].max() - points[members, 2].min() > settings["eyeball"]["min_height"]):
                eyeball = members
        if eyeball is None:
            raise ValueError(f"眼球の面が見つかりません（{side}。eye_x・eye_z・front_y・eyeball を確かめてください）")
        centre = points[eyeball][:, [0, 2]].mean(axis=0)
        best, best_distance = None, 1e9
        for loop in np.unique(loops[boundary & near_eye]):
            members = boundary & (loops == loop)
            if members.sum() < settings["rim_min_verts"]:
                continue
            distance = float(np.linalg.norm(points[members][:, [0, 2]].mean(axis=0) - centre))
            if distance < best_distance:
                best, best_distance = members, distance
        if best is None:
            raise ValueError(f"目の穴の縁が見つかりません（{side}）")
        rim = best
        entry = {"rim_verts": int(rim.sum()), "rim_x": [float(points[rim, 0].min()), float(points[rim, 0].max())],
                 "rim_z": [float(points[rim, 2].min()), float(points[rim, 2].max())], "parts": []}
        for root, size in sizes.items():
            if root == face_root:
                continue
            members = (roots == root) & near_eye
            if members.sum() < size:                     # 目のまわりに全部収まっている島だけ
                continue
            thickness_z = points[members, 2].max() - points[members, 2].min()
            if size >= settings["eyeball"]["min_verts"] and thickness_z > settings["eyeball"]["min_height"]:
                difference = points[members][:, None, [0, 2]] - points[rim][None, :, [0, 2]]
                weight = 1.0 / (np.square(difference).sum(axis=2) + 1e-8)
                moved[members, 1] = (weight * points[rim, 1][None, :]).sum(axis=1) / weight.sum(axis=1) + settings["membrane"]
                entry["parts"].append({"kind": "eyeball", "verts": size,
                                       "moved_mm_max": float(np.abs(moved[members, 1] - points[members, 1]).max() * 1000)})
            else:                                        # まつ毛の板・小さな部品: 頭の中へ
                moved[members, 1] = points[members, 1] + settings["tuck"]
                entry["parts"].append({"kind": "lash_or_small", "verts": size})
        report[side] = entry
    return moved, report, sizes[face_root]


def closed_shape_in_units(points, unit, roots, boundary, loops, settings):
    """Blender 単位の点 (N, 3) で closed_shape を呼ぶ。設定の長さは m なので、シーンの scale_length（1 Blender 単位が何 m か）で m に直して
    から形を作り、Blender 単位へ戻す（scale_length が 1 なら値はそのまま）。"""
    moved, sides, face_verts = closed_shape(points * unit, roots, boundary, loops, settings)
    return moved / unit, sides, face_verts


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    head = bpy.data.objects.get(settings["head"])
    if head is None or head.type != "MESH":
        raise SystemExit(f"head のメッシュがありません: {settings['head']}")
    mesh = head.data
    keys = mesh.shape_keys
    if keys is not None and settings["key"] in keys.key_blocks:
        raise SystemExit(f"{head.name} に形状キー {settings['key']} がもうあります（model.closed_eye_key を 2 回かけない）")
    count = len(mesh.vertices)
    world = np.array(head.matrix_world)
    inverse = np.array(head.matrix_world.inverted())
    local = np.empty(count * 3)
    mesh.vertices.foreach_get("co", local)
    local = local.reshape(-1, 3)
    points = local @ world[:3, :3].T + world[:3, 3]        # 初期姿勢での位置（顔は −Y を向く。Blender 単位）
    scene = head.users_scene[0] if head.users_scene else bpy.context.scene
    unit = float(scene.unit_settings.scale_length)       # 1 Blender 単位が何 m か（設定の長さは m）
    roots = islands(count, mesh_edges(mesh))
    sizes = {root: int((roots == root).sum()) for root in np.unique(roots)}
    face_root = max(sizes, key=sizes.get)
    boundary, loops = boundary_loops(count, [polygon.edge_keys for polygon in mesh.polygons], roots, face_root)
    try:
        moved, sides, face_verts = closed_shape_in_units(points, unit, roots, boundary, loops, settings)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if mesh.shape_keys is None:
        head.shape_key_add(name="Basis", from_mix=False)
    key = head.shape_key_add(name=settings["key"], from_mix=False)
    key.data.foreach_set("co", (moved @ inverse[:3, :3].T + inverse[:3, 3]).reshape(-1))
    key.value = 0.0
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report = {"schema_version": "model3d-closed-eye-key/0.1", "blender": bpy.app.version_string, "head": head.name, "key": settings["key"],
              "face_island_verts": face_verts, "sides": sides, "scale_length": unit, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "key": settings["key"]}))


if __name__ == "__main__":
    main()
