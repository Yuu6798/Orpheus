"""Blender の中で動かす: 上に重なる布（腰の垂れなど）が、下の布（裾など）の内側へ入っているコマを数える（check.cloth_over。測るだけで、
.blend は変えない）。

    blender --background --factory-startup --python cloth_over.py -- --blend dance.blend --settings settings.json --report raw.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 別々に揺れる布どうしは、初期姿勢で組にした面の平面と比べる数え方（層の順番の検査）
では、ずれが大きく、数えすぎた（954 コマ中 953 コマ・深さ 10 cm と出た）。コマごとに、上の布の頂点から外向きに線を伸ばし、下の布の面に
当たるか（＝下の布の内側にいるか）を直接見る。

測り方:
  - 外向きは、縦の軸の骨（axis_bone）の軸から離れる向き（そのコマの姿勢で）。
  - 上の布（over）の頂点（変形した後）から外向きに depth（m）まで線を伸ばし、下の布（under）の面（変形した後。当て布 patch_group の面は数えない）に
    当たったら「入り込み」。深さは、当たるまでの距離。初期姿勢でも同じ判定をして、もともと下の布の内側にある頂点（付け根など）は数から除く。
  - 初期姿勢は、アーマチュアを REST にして評価する。ファイルから動きを読む変形（MESH_CACHE・MESH_SEQUENCE_CACHE。cloth.smooth が置き換えた
    変形など）は、REST にしても開いたコマの形を読むので、初期姿勢の判定の間だけ外す（外した物は報告の rest_caches_off）。
  - tol より浅い入り込みは数えない。
  - 上の布・下の布と、それらのモディファイアの相手のほかのメッシュは評価しない（速くする）。

報告（raw）は model3d-cloth-over/0.1: コマごとの入り込んだ頂点の数・いちばん深い量・平均（mm）、初期姿勢で除いた頂点の数。要約は check.cloth_over がする。
**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
CI には Blender が無い（要約はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .stage_render import sync_to_render
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from stage_render import sync_to_render
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"over": None, "under": None, "armature": None, "axis_bone": None, "patch_group": "lining_patch", "depth": 0.06, "tol": 0.001,
            "frames": None}
# ファイルから動きを読む変形。アーマチュアを REST にしても、開いたコマの形を読む（初期姿勢の形にならない）
CACHE_READERS = {"MESH_CACHE", "MESH_SEQUENCE_CACHE"}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_over.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector
    from mathutils.bvhtree import BVHTree

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        s = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not s["armature"] or obj.name == s["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + s['armature'] + '）' if s['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:          # 距離の設定（m）と Blender 単位を合わせる
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    over, under = bpy.data.objects.get(s["over"]), bpy.data.objects.get(s["under"])
    for label, obj, name in (("over", over, s["over"]), ("under", under, s["under"])):
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"{label} のメッシュがありません: {name}")
    synced = sync_to_render(over) + sync_to_render(under)   # （描く形で測る: 表示と描画でモディファイアの効き方が違えば描画にそろえる。保存しない）
    if s["axis_bone"] not in armature.pose.bones:
        raise SystemExit(f"{armature.name} に縦の軸の骨 {s['axis_bone']} がありません（axis_bone）")
    patch_group = under.vertex_groups.get(s["patch_group"])
    under_patch = set() if patch_group is None else {v.index for v in under.data.vertices
                                                      if any(g.group == patch_group.index and g.weight > 0.5 for g in v.groups)}
    needed = {over.name, under.name}
    for obj in (over, under):
        for m in obj.modifiers:
            target = getattr(m, "target", None)
            if target is not None:
                needed.add(target.name)
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj.name not in needed:
            obj.hide_viewport = True

    def world_mesh(obj):
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        if len(mesh.vertices) != len(obj.data.vertices):   # 頂点の番号で元のメッシュと結ぶ所があるので、頂点の数を変えるモディファイアは止める
            verts = len(mesh.vertices)
            evaluated.to_mesh_clear()
            raise SystemExit(f"{obj.name} のモディファイアが頂点の数を変えています（{len(obj.data.vertices)} → 変形した後 {verts}）。"
                             "細分化などを止めた .blend で測ってください")
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        mesh.calc_loop_triangles()
        tris = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
        mesh.loop_triangles.foreach_get("vertices", tris)
        evaluated.to_mesh_clear()
        m = np.array(evaluated.matrix_world)
        return co.reshape(-1, 3) @ m[:3, :3].T + m[:3, 3], tris.reshape(-1, 3)

    def covered():
        """上の布の頂点ごとの、下の布の内側への深さ（入っていなければ 0）。"""
        pb = armature.pose.bones[s["axis_bone"]]
        matrix = armature.matrix_world @ pb.matrix
        head = np.array(matrix.translation)
        axis = np.array(matrix.to_3x3().col[1])
        axis = axis / np.linalg.norm(axis)
        uco, utris = world_mesh(under)
        tree = BVHTree.FromPolygons([Vector(p) for p in uco], [tuple(int(v) for v in t) for t in utris if not any(int(v) in under_patch for v in t)])
        oco, _ = world_mesh(over)
        depth = np.zeros(len(oco))
        for i, p in enumerate(oco):
            rel = p - head
            radial = rel - axis * float(rel @ axis)
            length = np.linalg.norm(radial)
            if length < 1e-4:
                continue
            d = Vector(radial / length)
            hit = tree.ray_cast(Vector(p) + d * 1e-4, d, s["depth"])
            if hit[0] is not None:
                depth[i] = hit[3]
        return depth

    # 初期姿勢の形: アーマチュアを REST にして評価する。ファイルから動きを読む変形（cloth.smooth が置き換えた MESH_CACHE など）は、REST にしても
    # 開いたコマの形を読む（その形の入り込みを全部のコマから除いてしまう）ので、初期姿勢の判定の間だけ外す
    caches = [m for name in sorted(needed) if bpy.data.objects[name].type == "MESH"
              for m in bpy.data.objects[name].modifiers if m.type in CACHE_READERS and m.show_viewport]
    for m in caches:
        m.show_viewport = False
    saved = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    rest = covered() > s["tol"]
    armature.data.pose_position = saved
    for m in caches:
        m.show_viewport = True
    bpy.context.view_layer.update()
    first, last = s["frames"] or (scene.frame_start, scene.frame_end)
    rows = []
    for frame in range(int(first), int(last) + 1):
        scene.frame_set(frame)
        depth = covered()
        depth[rest] = 0.0
        hit = depth > s["tol"]
        rows.append({"frame": frame, "verts": int(hit.sum()), "max_mm": round(float(depth.max()) * 1000, 1),
                     "mean_mm": round(float(depth[hit].mean()) * 1000, 1) if hit.any() else 0.0})
    report = {"schema_version": "model3d-cloth-over/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "over": over.name, "under": under.name, "over_verts": int(len(rest)), "ignored_at_rest": int(rest.sum()),
              "under_patch_verts": len(under_patch), "modifiers_synced": synced,
              "rest_caches_off": [f"{m.id_data.name}:{m.name}" for m in caches], "rows": rows}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(rows)}))


if __name__ == "__main__":
    main()
