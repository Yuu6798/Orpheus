"""Blender の中で動かす: 衣装（裾・腰の垂れなど）が体に入った分を、体の外へ押し出す補正（Shrinkwrap の Outside）を付ける（cloth.pushout）。
当たりの相手は、体のメッシュを輪切りにして作った骨盤と左右の太ももの閉じた筒。重なった布は層に分けて、層ごとの距離へ押し出す。
--only-lining-gap（cloth.lining_gap）なら、当たりも補正も付けず、裏地を表から離す形の手直しだけをする。

    blender --background --factory-startup --python cloth_pushout.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 揺れものの骨は数本しかないので、骨が体の外にあっても、その間の布の面は体に
食い込む（脚を上げたときに、黒いパンツと太ももが裾を突き抜けて斑に見えた）。
当たり（筒）: レストの姿勢の体を、骨盤は水平に（股から布のいちばん上の少し上まで 2 cm おき）、太ももは骨に垂直に（付け根の少し下から膝まで 8 つ）
輪切りにし、輪ごとに中心と 16 方向の太さ（その方向の頂点の距離の 95% 点）を測って、両端を丸めた閉じた筒にする。骨で動く。描かない。
体のメッシュそのものを相手にすると、部品の穴のふちや鋭い角のそばで外の頂点が「裏側」と判定され、遠くの頂点を吸い寄せて面を破った（docs の罠）。
層: 同じメッシュの布が重なっている所（裾の表・ひだ・裏地）は、いちばん外は「外」の距離、いちばん内（裏地）は「内」の距離、その間は真ん中、
当て布（頂点グループ patch_group の頂点）は内より patch_inset だけ内側へ押し出す。同じ距離へ押し出すと、体に近い所で表と裏地が同じ所にそろい、
裏地が表より外へ出た。初期姿勢で筒の内側に attached_depth より深くもぐっている頂点（付け根）は動かさない。

報告は model3d-cloth-pushout/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ筒・頂点グループ・
モディファイアになることを確かめた（docs/model3d.md）。CI には Blender が無い（輪の太さの測り方と、層の距離の決め方はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from . import cloth_layers as cl
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cloth_layers as cl
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"body": None, "cloths": [], "armature": None, "pelvis": None, "thighs": [], "report_bones": None, "axis_bone": None,
            "layer_gap": 0.015, "patch_group": "lining_patch", "patch_inset": 0.0015, "attached_depth": 0.003, "lining_gap": 0.0,
            "only_lining_gap": False, "proxy_prefix": "LowerProxy.", "group": "pushout", "modifier": "pushout_body"}
LAYER_SUFFIXES = (("", ""), ("_mid", "_mid"), ("_in", "_in"), ("_patch", "_patch"))   # 層 0〜3 の頂点グループとモディファイアの名前の後ろ


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_pushout.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def layer_offsets(offsets, patch_inset):
    """布の押し出す距離（m。[外] か [内, 外]）から、層 0〜3（外・間・内・当て布）の距離。層に分けないときは外だけ。台本と同じ式。"""
    if len(offsets) < 2:
        return [offsets[-1]]
    return [offsets[1], (offsets[0] + offsets[1]) / 2, offsets[0], max(offsets[0] - patch_inset, 0.0005)]


def follow_bone(z, follow, heads):
    """骨盤の輪の高さ z を動かす骨: follow の並びで、次の骨の付け根より低ければその骨、どれより高ければ最後の骨（台本の hips・spine・chest）。"""
    for bone, upper in zip(follow[:-1], follow[1:]):
        if z < heads[upper]:
            return bone
    return follow[-1]


def main(argv=None):
    import bmesh  # Blender の中でだけ使える
    import bpy
    from mathutils.kdtree import KDTree

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
    bones = armature.data.bones
    pairs = []
    for item in s["cloths"]:
        cloth = bpy.data.objects.get(item["mesh"])
        if cloth is None or cloth.type != "MESH":
            raise SystemExit(f"布のメッシュがありません: {item['mesh']}")
        problem = cl.mesh_problem(cloth, shape_keys=bool(s["only_lining_gap"] or s["lining_gap"] > 0))   # 頂点グループを足す・裏地を寄せる
        if problem:
            raise SystemExit(problem)
        pairs.append((cloth, [float(value) for value in item["offsets"]]))
    axis_bone = bones.get(s["axis_bone"])
    if axis_bone is None:
        raise SystemExit(f"{armature.name} に縦の軸の骨 {s['axis_bone']} がありません（axis_bone）")
    saved_position = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    axis_xy = (armature.matrix_world @ axis_bone.head_local)[:2]
    report = {"schema_version": "model3d-cloth-pushout/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name}

    if s["only_lining_gap"]:
        # モデルの .blend 用: 当たりの形も補正も付けず、裏地を表から離す形の手直しだけを入れる（層に分ける布だけ）
        report["objects"] = {}
        for cloth, offsets in pairs:
            if len(offsets) < 2:
                report["objects"][cloth.name] = "層に分けない指定なので、何もしていない"
                continue
            _, gaps, inner = cl.layer_classes(cloth, axis_xy, s["layer_gap"], s["patch_group"])
            report["objects"][cloth.name] = cl.widen_lining(cloth, gaps, inner, axis_xy, s["lining_gap"])
        armature.data.pose_position = saved_position
        save(bpy, a, report, s)
        return

    body = bpy.data.objects.get(s["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"体のメッシュがありません: {s['body']}")
    pelvis, thighs = s["pelvis"], s["thighs"]
    needed = set(pelvis["bones"]) | {pelvis["centre"]} | {t[k] for t in thighs for k in ("upper", "lower")} | {b for t in thighs for b in t["bones"]}
    missing = sorted(name for name in needed if name not in bones)
    if missing:
        raise SystemExit(f"{armature.name} に骨がありません: {missing}")
    names = [s["proxy_prefix"] + "pelvis"] + [s["proxy_prefix"] + "thigh." + t["name"] for t in thighs]
    for name in names:
        if name in bpy.data.objects:
            raise SystemExit(f"物体 {name} がもうあります（cloth.pushout を 2 回かけない）")
    for cloth, offsets in pairs:
        for k in range(4 if len(offsets) >= 2 else 1):
            group, modifier = s["group"] + LAYER_SUFFIXES[k][0], s["modifier"] + LAYER_SUFFIXES[k][1]
            if group in cloth.vertex_groups or any(m.name.startswith(modifier) for m in cloth.modifiers):
                raise SystemExit(f"{cloth.name} に {group} の頂点グループか {modifier} のモディファイアがもうあります")

    # 1. 当たりの筒（台本の build_tubes）。体の基本の形を、いちばん重い頂点グループ（変形に使う骨のグループだけ）で分ける
    deform = {bone.name for bone in bones if bone.use_deform}
    groups = {g.index: g.name for g in body.vertex_groups if g.name in deform}
    mw = np.array(body.matrix_world)
    co = np.array([v.co[:] for v in body.data.vertices]) @ mw[:3, :3].T + mw[:3, 3]
    top = []
    for v in body.data.vertices:
        items = [g for g in v.groups if g.group in groups]
        top.append(groups[max(items, key=lambda g: g.weight).group] if items else "")
    top = np.array(top)
    aw = armature.matrix_world

    def head(name):
        return np.array(aw @ bones[name].head_local)

    hips = head(pelvis["centre"])
    info, proxies = {}, []
    lower = np.isin(top, pelvis["bones"])
    centre_line = (top == pelvis["centre"]) & (np.abs(co[:, 0] - hips[0]) < 0.02)
    if not centre_line.any():
        raise SystemExit(f"{body.name} に、{pelvis['centre']} がいちばん重い真ん中の頂点がありません（股の高さを決められない）")
    z_crotch = float(co[centre_line, 2].min())
    z_top = max(float((c.matrix_world @ v.co).z) for c, _ in pairs for v in c.data.vertices) + 0.05
    follow = [name for name in pelvis["follow"] if name in bones]
    heads = {name: head(name)[2] for name in follow}
    x_axis, y_axis, z_axis = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])
    rings = []
    for z in np.arange(z_crotch + 0.01, z_top + 1e-6, 0.02):               # 骨盤（股から、布のいちばん上の少し上まで）。水平に輪切り
        sel = lower & (np.abs(co[:, 2] - z) < 0.015)
        if sel.sum() < 12:
            continue
        x, y = co[sel, 0], co[sel, 1]
        cx = (np.percentile(x, 5) + np.percentile(x, 95)) / 2
        cy = (np.percentile(y, 5) + np.percentile(y, 95)) / 2
        radii = cl.sector_radii(x - cx, y - cy)
        if radii is None:
            continue
        rings.append({"centre": np.array([cx, cy, z]), "axis": z_axis, "side": x_axis, "up": y_axis, "radii": radii,
                      "group": follow_bone(z, follow, heads)})
    if len(rings) < 2:
        raise SystemExit(f"骨盤の輪が {len(rings)} しか測れません（体と布の高さ・pelvis.bones を確かめてください）")
    cl.smooth_along(rings)
    proxy, volume = cl.star_tube(bpy, bmesh, scene, armature, names[0], rings, 0.3, 0.3)
    proxies.append(proxy)
    middle = rings[len(rings) // 2]["radii"]
    info[proxy.name] = {"rings": len(rings), "z_from_to": [round(z_crotch + 0.01, 3), round(float(rings[-1]["centre"][2]), 3)],
                        "volume_litre": round(volume * 1000, 2),
                        "width_depth_mm_at_middle": [round(float(middle[[0, cl.SECTORS // 2]].sum()) * 1000, 1),
                                                     round(float(middle[[cl.SECTORS // 4, 3 * cl.SECTORS // 4]].sum()) * 1000, 1)]}
    for thigh, name in zip(thighs, names[1:]):                             # 太もも（付け根の少し下から膝まで）。骨に垂直に輪切り
        hip, knee = head(thigh["upper"]), head(thigh["lower"])
        axis = cl.unit(knee - hip)
        length = float(np.linalg.norm(knee - hip))
        side = cl.unit(np.cross(axis, y_axis))
        up = np.cross(axis, side)
        same_side = (co[:, 0] - hips[0]) * (1.0 if hip[0] > hips[0] else -1.0) > -0.01
        sel0 = np.isin(top, thigh["bones"]) & same_side
        rings, previous = [], None
        for t in np.linspace(0.15, 1.0, 8):
            rel = co - (hip + axis * t * length)
            along = rel @ axis
            u, v = rel @ side, rel @ up
            sel = sel0 & (np.abs(along) < 0.075 * length) & (np.hypot(u, v) < 0.13)
            radii, cu, cv = None, 0.0, 0.0
            if sel.sum() >= 12:
                cu = (np.percentile(u[sel], 5) + np.percentile(u[sel], 95)) / 2
                cv = (np.percentile(v[sel], 5) + np.percentile(v[sel], 95)) / 2
                radii = cl.sector_radii(u[sel] - cu, v[sel] - cv)
            if radii is None:
                if previous is None:
                    continue
                radii, cu, cv = previous
            previous = (radii, cu, cv)
            rings.append({"centre": hip + axis * t * length + side * cu + up * cv, "axis": axis, "side": side, "up": up, "radii": radii,
                          "group": thigh["upper"]})
        if len(rings) < 2:
            raise SystemExit(f"太もも {thigh['name']} の輪が {len(rings)} しか測れません（thighs の骨を確かめてください）")
        cl.smooth_along(rings)
        proxy, volume = cl.star_tube(bpy, bmesh, scene, armature, name, rings, 1.0, 0.6)
        proxies.append(proxy)
        info[proxy.name] = {"rings": len(rings), "volume_litre": round(volume * 1000, 2),
                            "mean_radius_mm_top_middle_bottom": [round(float(np.mean(rings[i]["radii"])) * 1000, 1) for i in (0, len(rings) // 2, -1)]}
    bpy.context.view_layer.update()
    trees = [cl.rest_tree(p) for p in proxies]
    for p, tree in zip(proxies, trees):
        wrong, total, inside = cl.far_test(p, tree)
        info[p.name].update({"sign_wrong_points": wrong, "sign_tested_points": total, "of_which_truly_inside": int(inside)})
    region = np.isin(top, s["report_bones"] or pelvis["bones"]) & (co[:, 2] > z_crotch - 0.30) & (co[:, 2] < z_top - 0.05)
    if region.any():                                                       # 体が筒からどれだけはみ出しているか（レスト。正＝はみ出し）
        out = np.array([min(cl.signed_distance(tree, p) for tree in trees) for p in co[region]])
        info["body_outside_tubes_mm"] = {"verts": int(region.sum()), "p50": round(float(np.percentile(out, 50)) * 1000, 1),
                                         "p90": round(float(np.percentile(out, 90)) * 1000, 1), "p99": round(float(np.percentile(out, 99)) * 1000, 1),
                                         "max": round(float(out.max()) * 1000, 1), "share_over_3mm": round(float((out > 0.003).mean()), 4),
                                         "share_over_6mm": round(float((out > 0.006).mean()), 4)}
    report["tubes"] = info
    labels = [p.name[len(s["proxy_prefix"]):] for p in proxies]
    trees = [cl.rest_tree(t) for t in proxies]

    # 2. 布ごとに、付け根を除き、層に分けて、層ごとの頂点グループと Shrinkwrap を付ける
    report["objects"] = {}
    for cloth, offsets in pairs:
        free, fixed = [], []
        for vertex in cloth.data.vertices:
            point = cloth.matrix_world @ vertex.co
            inside = any(-0.12 < cl.signed_distance(tree, point) < -s["attached_depth"] for tree in trees)
            (fixed if inside else free).append(vertex.index)
        distances = layer_offsets(offsets, s["patch_inset"])
        lining_report = None
        if len(offsets) >= 2:
            classes, gaps, inner = cl.layer_classes(cloth, axis_xy, s["layer_gap"], s["patch_group"])
            if s["lining_gap"] > 0:
                lining_report = cl.widen_lining(cloth, gaps, inner, axis_xy, s["lining_gap"])
            patch = [i for i, c in enumerate(classes) if c == 3]
            if patch:                                   # 当て布の頂点は、もとの頂点（いちばん近い当て布でない頂点）が付け根なら付け根に
                originals = [v.index for v in cloth.data.vertices if classes[v.index] != 3]
                tree_of_originals = KDTree(len(originals))
                for k, i in enumerate(originals):
                    tree_of_originals.insert(cloth.data.vertices[i].co, k)
                tree_of_originals.balance()
                is_free = set(free)
                for i in patch:
                    source = originals[tree_of_originals.find(cloth.data.vertices[i].co)[1]]
                    is_free.discard(i)
                    if source in is_free:
                        is_free.add(i)
                free = sorted(is_free)
                fixed = [i for i in range(len(cloth.data.vertices)) if i not in is_free]
        else:
            classes = [0] * len(cloth.data.vertices)
        everything = list(range(len(cloth.data.vertices)))
        made = {}
        for wanted, distance in enumerate(distances):
            group_name, modifier_name = s["group"] + LAYER_SUFFIXES[wanted][0], s["modifier"] + LAYER_SUFFIXES[wanted][1]
            group = cloth.vertex_groups.new(name=group_name)
            group.remove(everything)
            members = [i for i in free if classes[i] == wanted]
            group.add(members, 1.0, "REPLACE")
            if wanted == 0:
                group.add(fixed, 0.0, "REPLACE")
            if not members:
                continue
            for target, label in zip(proxies, labels):
                modifier = cloth.modifiers.new(modifier_name + "@" + label, "SHRINKWRAP")
                modifier.target = target
                modifier.wrap_method = "NEAREST_SURFACEPOINT"
                modifier.wrap_mode = "OUTSIDE"
                modifier.offset = distance
                modifier.vertex_group = group_name
            made[modifier_name] = {"offset_m": round(distance, 4), "verts": len(members)}
        report["objects"][cloth.name] = {"layers": made, "moved_if_inside": len(free), "kept_as_attached": len(fixed),
                                         "modifiers": [m.name for m in cloth.modifiers]}
        if lining_report:
            report["objects"][cloth.name]["lining_moved_inward"] = lining_report
    report["targets"] = [p.name for p in proxies]
    armature.data.pose_position = saved_position
    save(bpy, a, report, s)


def save(bpy, a, report, settings):
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report["settings"] = settings
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend}))


if __name__ == "__main__":
    main()
