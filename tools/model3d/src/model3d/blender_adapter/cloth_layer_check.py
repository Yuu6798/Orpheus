"""Blender の中で動かす: 重なった布（裾の表と裏地、その上の垂れなど）の順番が入れ替わっているコマを数える（check.cloth_layers。測るだけで、
.blend は変えない）。

    blender --background --factory-startup --python cloth_layer_check.py -- --blend dance.blend --settings settings.json --report raw.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 裾は表と裏地が 1 枚につながった薄い袋で、体の外へ押し出す補正が表も裏地も同じ距離へ
そろえたので、重なった所で裏地が表より外へ出て、裾の表に裏地の色の面が見えた。絵で 1 コマずつ探すと見落とすので、数で調べる。

測り方:
  - 初期姿勢（押し出しの補正 Shrinkwrap は止めて）で、縦の軸の骨（axis_bone）の軸から各頂点へ水平に線を引き、その線が頂点より内側で布を横切る回数を
    「層の番号」とする（いちばん内が 0）。各頂点について、すぐ外側の層を線が横切る点（面と、面の中の位置）と、そこまでの距離を覚えておく。
    当て布（patch_group の頂点）の面は層として数えない。
  - 初期姿勢はアーマチュアを REST にして評価する。ファイルから動きを読む変形（MESH_CACHE・MESH_SEQUENCE_CACHE。cloth.smooth が置き換えた変形など）と
    焼いた物理の計算（CLOTH・SOFT_BODY。cloth.skirt_proxy の筒など）は、REST にしても開いたコマの形を返すので、初期姿勢の間だけ外す（布と体と、
    そのモディファイアの相手。外した物は報告の rest_caches_off。焼いていない物理の計算は止める。check.cloth_over と同じ）。
  - コマごとに、その面の同じ位置と頂点を比べ、頂点が面の外向きの側へ tol より出ていたら「入れ替わり」。
  - 調べない頂点: 初期姿勢で、すぐ外の層までが max_gap より遠い頂点（ひだの別の山など、隣り合っていない層）。only_innermost ならいちばん内の層だけ。
    pair_from ならそのメッシュの頂点だけ、pair_to ならすぐ外の面がそのメッシュの頂点だけ。
  - patches が only なら、当て布の頂点だけを調べる（すぐ外の元の面＝当て布が隠している面より外へ出ていないか）。
  - without を渡すと、その名前で始まる布のモディファイアを止めて測る（押し出しの補正を外した形と比べるため）。
  - body を渡すと、初期姿勢での層ごとの体までの距離（10%・50%・90% 点）も報告する（押し出す距離を決める材料）。
  布と体のほかのメッシュは評価しない（台本と同じ）。

報告（raw）は model3d-cloth-layers/0.1: 調べた頂点の数、すぐ外の層までの距離、コマごとの入れ替わった頂点の数といちばん大きい量（mm）。
要約は check.cloth_layers がする。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた
（docs/model3d.md）。CI には Blender が無い（要約はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .cloth_over import cached_motion
    from .stage_render import sync_to_render
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cloth_over import cached_motion
    from stage_render import sync_to_render
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"objects": [], "armature": None, "axis_bone": None, "tol": 0.0005, "without": "", "max_gap": 0.015, "only_innermost": False,
            "pair_from": "", "pair_to": "", "patch_group": "lining_patch", "patches": "ignore", "body": None, "frames": None}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_layer_check.py")
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
    cloths = []
    for name in s["objects"]:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"布のメッシュがありません: {name}")
        cloths.append(obj)
    if s["axis_bone"] not in armature.data.bones:
        raise SystemExit(f"{armature.name} に縦の軸の骨 {s['axis_bone']} がありません（axis_bone）")
    body = bpy.data.objects.get(s["body"]) if s["body"] else None
    if s["body"] and (body is None or body.type != "MESH"):
        raise SystemExit(f"体のメッシュがありません: {s['body']}")
    for o in bpy.data.objects:
        if o.type == "MESH" and o not in cloths and o is not body:
            o.hide_viewport = True
    synced = []
    for o in cloths + ([body] if body is not None else []):   # （描く形で測る: 表示と描画でモディファイアの効き方が違えば描画にそろえる。保存しない）
        synced += sync_to_render(o)
    if s["without"]:
        for c in cloths:
            for m in c.modifiers:
                if m.name.startswith(s["without"]):
                    m.show_viewport = False

    def world_mesh(obj):
        """変形した後の頂点（世界の座標）と三角形。"""
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

    patches = {}
    for c in cloths:
        group = c.vertex_groups.get(s["patch_group"])
        patches[c.name] = set() if group is None else {v.index for v in c.data.vertices
                                                         if any(g.group == group.index and g.weight > 0.5 for g in v.groups)}

    caches_off = set()

    def rest_mods_off(objects):
        """初期姿勢の形を評価する間だけ外すモディファイア: 布の押し出しの補正（Shrinkwrap）と、ファイルから動きを読む変形・焼いた物理の計算
        （REST にしても開いたコマの形を返す。モディファイアの相手の筒なども。cloth_over.cached_motion）。戻り: 外したモディファイア（あとで戻す）。"""
        off = [m for o in objects if o in cloths for m in o.modifiers if m.type == "SHRINKWRAP" and m.show_viewport]
        cached = cached_motion(objects)
        caches_off.update(f"{m.id_data.name}:{m.name}" for m in cached)
        off += cached
        for m in off:
            m.show_viewport = False
        return off

    def cloth_layers(axis_xy):
        """初期姿勢での層の番号と、すぐ外側の層の点。戻り: {名前: {"layer", "pair": [(相手, 三角形, 重み 3 つ, 向きの符号, 距離) か None]}}, 三角形"""
        saved_mods = rest_mods_off(cloths)
        saved_pose = armature.data.pose_position
        armature.data.pose_position = "REST"
        bpy.context.view_layer.update()
        data = {c.name: world_mesh(c) for c in cloths}
        verts, polys, owner = [], [], []
        for name, (co, tris) in data.items():
            base = len(verts)
            verts.extend(Vector(p) for p in co)
            for k, t in enumerate(tris):                 # 当て布の面は、層としては数えない
                if any(int(v) in patches[name] for v in t):
                    continue
                polys.append((int(t[0]) + base, int(t[1]) + base, int(t[2]) + base))
                owner.append((name, k))
        tree = BVHTree.FromPolygons(verts, polys)
        out = {}
        for name, (co, tris) in data.items():
            layer = np.zeros(len(co), dtype=int)
            pair = [None] * len(co)
            for i, p in enumerate(co):
                if (i in patches[name]) != (s["patches"] == "only"):
                    continue
                origin = Vector((axis_xy[0], axis_xy[1], p[2]))
                d = Vector(p) - origin
                reach = d.length
                if reach < 1e-4:
                    continue
                d = d / reach
                hits, start, travelled = [], origin, 0.0
                for _ in range(40):
                    loc, normal, index, dist = tree.ray_cast(start, d)
                    if loc is None:
                        break
                    travelled += dist
                    hits.append((travelled, index, loc.copy(), normal.copy()))
                    start = loc + d * 2e-5
                    travelled += 2e-5
                inside = [h for h in hits if h[0] < reach - 0.0003]
                outside = [h for h in hits if h[0] > reach + 0.0003]
                merged, last = 0, -1.0
                for t, _, _, _ in inside:                # 0.2 mm より近い 2 つは同じ面のふち
                    if t - last > 0.0002:
                        merged += 1
                    last = t
                layer[i] = merged
                if outside and outside[0][0] - reach < 0.03:
                    gap = outside[0][0] - reach
                    t, index, loc, normal = outside[0]
                    oname, k = owner[index]
                    tri = data[oname][1][k]
                    a_, b_, c_ = (data[oname][0][int(v)] for v in tri)
                    v0, v1, v2 = b_ - a_, c_ - a_, np.array(loc) - a_
                    d00, d01, d11, d20, d21 = v0 @ v0, v0 @ v1, v1 @ v1, v2 @ v0, v2 @ v1
                    den = d00 * d11 - d01 * d01
                    if abs(den) > 1e-16:
                        w1 = (d11 * d20 - d01 * d21) / den
                        w2 = (d00 * d21 - d01 * d20) / den
                        n = np.cross(v0, v1)
                        sign = 1.0 if float(n @ np.array(d)) >= 0 else -1.0
                        pair[i] = (oname, int(k), (1.0 - w1 - w2, w1, w2), sign, float(gap))
            out[name] = {"layer": layer, "pair": pair}
        armature.data.pose_position = saved_pose
        for m in saved_mods:
            m.show_viewport = True
        bpy.context.view_layer.update()
        return out, {name: tris for name, (co, tris) in data.items()}

    bone = armature.data.bones[s["axis_bone"]]
    axis_xy = np.array(armature.matrix_world @ bone.head_local)[:2]
    layers, tri_of = cloth_layers(axis_xy)

    rest_stats = {}
    if body is not None:                                 # 初期姿勢での、層ごとの体までの距離（押し出す距離を決める材料）
        saved = armature.data.pose_position
        armature.data.pose_position = "REST"
        off = rest_mods_off(cloths + [body])
        bpy.context.view_layer.update()
        bco, btris = world_mesh(body)
        btree = BVHTree.FromPolygons([Vector(p) for p in bco], [tuple(int(v) for v in t) for t in btris])
        for c in cloths:
            co, _ = world_mesh(c)
            dist = np.array([btree.find_nearest(Vector(p))[3] for p in co])
            lay = layers[c.name]["layer"]
            rest_stats[c.name] = {str(k): {"verts": int((lay == k).sum()),
                                           "body_distance_mm_p10_p50_p90": [round(float(v) * 1000, 1) for v in np.percentile(dist[lay == k], [10, 50, 90])]}
                                  for k in sorted(set(lay.tolist()))}
        for m in off:
            m.show_viewport = True
        armature.data.pose_position = saved
        bpy.context.view_layer.update()

    def counted(c, i, pr):
        oname = pr[0]
        if pr[4] > s["max_gap"] or (s["only_innermost"] and layers[c.name]["layer"][i] != 0):
            return False
        return not ((s["pair_from"] and c.name != s["pair_from"]) or (s["pair_to"] and oname != s["pair_to"]))

    first, last = s["frames"] or (scene.frame_start, scene.frame_end)
    rows = []
    for frame in range(int(first), int(last) + 1):
        scene.frame_set(frame)
        co = {c.name: world_mesh(c)[0] for c in cloths}
        row = {"frame": frame}
        total, worst = 0, 0.0
        for c in cloths:
            count, deep = 0, 0.0
            for i, pr in enumerate(layers[c.name]["pair"]):
                if pr is None or not counted(c, i, pr):
                    continue
                oname, k, w, sign, gap = pr
                tri = tri_of[oname][k]
                a_, b_, c_ = (co[oname][int(v)] for v in tri)
                point = a_ * w[0] + b_ * w[1] + c_ * w[2]
                n = np.cross(b_ - a_, c_ - a_)
                length = np.linalg.norm(n)
                if length < 1e-12:
                    continue
                poke = float((co[c.name][i] - point) @ n) / length * sign
                if poke > s["tol"]:
                    count += 1
                    deep = max(deep, poke)
            row[c.name] = [count, round(deep * 1000, 1)]
            total += count
            worst = max(worst, deep)
        row["total"] = [total, round(worst * 1000, 1)]
        rows.append(row)
    checked = {c.name: [p[4] for i, p in enumerate(layers[c.name]["pair"]) if p is not None and counted(c, i, p)] for c in cloths}
    # 距離の分布は、台本と同じく「いちばん内だけ」の絞りを掛けずに出す（数えた組の数 pairs_checked は掛ける）
    near = {c.name: [p[4] for p in layers[c.name]["pair"] if p is not None and p[4] <= s["max_gap"]
                     and not ((s["pair_from"] and c.name != s["pair_from"]) or (s["pair_to"] and p[0] != s["pair_to"]))] for c in cloths}
    innermost = {c.name: [p[4] for i, p in enumerate(layers[c.name]["pair"]) if p is not None and layers[c.name]["layer"][i] == 0]
                 for c in cloths}
    report = {"schema_version": "model3d-cloth-layers/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "objects": [c.name for c in cloths], "without": s["without"], "patches": s["patches"], "modifiers_synced": synced,
              "rest_caches_off": sorted(caches_off),
              "patch_verts": {name: len(v) for name, v in patches.items()}, "layers_rest": rest_stats,
              "pairs_checked": {name: len(v) for name, v in checked.items()},
              "checked_gap_mm_p10_p50_p90": {name: [round(float(x) * 1000, 1) for x in np.percentile(v or [0.0], [10, 50, 90])] for name, v in near.items()},
              "innermost_gap_mm_p10_p50_p90": {name: [round(float(x) * 1000, 1) for x in np.percentile(v or [0.0], [10, 50, 90])]
                                               for name, v in innermost.items()},
              "rows": rows}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(rows)}))


if __name__ == "__main__":
    main()
