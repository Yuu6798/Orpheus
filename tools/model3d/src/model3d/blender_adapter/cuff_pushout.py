"""Blender の中で動かす: 袖と袖口のフリルが、相手の腕に入り込まないようにする（当たった所で、相手の腕の閉じた筒の外へ押し出す＝つぶれる）
（collision.cuff_pushout）。腕は動かさない。

    blender --background --factory-startup --python cuff_pushout.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 袖口のフリルは前腕の骨から半径 50〜60 mm あり、手首を交差させる振りでは、
腕を手首の太さぶん離しても、フリルが相手の腕や袖に 25〜30 mm 入り込んだ。フリルの太さぶんまで腕を離すと、手首が最大 17 cm 動いて
振りが変わった。やわらかいフリルは、相手の腕に当たったらつぶれるのが自然なので、腕は離さず、フリルの頂点だけを相手の腕の外へ押し出す。

やり方（arms の 2 本の腕ごと）:
  - 当たり用の見えない形（proxy の名前）を作る: 肘 → 手首 → 手のひらの先（palm_end の骨の付け根）を通る 1 本の閉じた筒。前腕を 5 つに
    区切った輪のうち、肘から sleeve_until までは芯の太さ core_radius、手首側は体のメッシュの前腕がいちばん重い頂点から測った太さ
    （輪の ±0.12 の帯の 90% 点。頂点が 5 に満たなければ肘の側の隣の輪と同じ）、手のひらは palm_radius。両端は半球のように丸める（輪を 60°・30° で縮め、
    先に点を置く）。輪は segments 角。骨に付けて一緒に動かす（Armature）。描画はしない（hide_render）。
  - 体のメッシュの袖（前腕がいちばん重い頂点のうち、肘から sleeve_until までの頂点）に、相手の腕の筒を相手にした Shrinkwrap（Outside、
    offset）を付ける。前に「頂点ウェイトの近接」（Vertex Weight Proximity）を置き、相手の筒から gate_near 以内は重み 1、gate_far より
    遠くは重み 0 にする（判定が外れても、遠くの頂点は動かない）。骨の変形の後ろに付くので、当たっていないコマでは何も変わらない。
  罠（台本の最初の版。必ず読む）: Shrinkwrap の Outside は「いちばん近い面の裏側にある頂点」を内側とみなして、距離に関係なく面まで
  動かす。当たり用の形を前腕と手のひらの別の筒にして、端を平らなふたでふさいだら、ふたと側面の角（90° より鋭い）のそばの外の点が
  ふたの裏側と判定され、相手の腕から 20〜30 cm 離れた袖の頂点が相手の手首まで吸い寄せられ、とがった面が伸びた（954 コマのうち約 170 コマ）。
  鋭い角の無い 1 本の閉じた筒にし、近接の重みで遠くの頂点を止めた。筒から 5〜30 cm 離れた点を置いて、内側と判定される点が無いかを
  確かめ、報告する（far_points_judged_inside）。付けたあとは check.pushout_moves で全コマの動いた量を調べる。

報告は model3d-cuff-pushout/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ形・重み・
モディファイアになることを確かめた（docs/model3d.md）。CI には Blender が無い（輪の向きと太さの決め方はテストする）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of

# 体と衣装の大きさ（core_radius・sleeve_until・palm_radius・gate_near・gate_far）は既定を置かない（工程が必ず渡す）。
# ほかの既定は 3 作目で使った値
DEFAULTS = {"body": None, "arms": [], "aliases": {}, "armature": None,
            "offset": 0.002, "segments": 20, "proxy": "ArmProxy.{arm}", "group": "cuff_{arm}",
            "near_group": "cuff_near_{arm}", "near_modifier": "cuff_near_{arm}", "modifier": "cuff_out_{arm}"}
RINGS = 5                                               # 前腕を区切る数（輪は 6 つ。台本と同じ）
FAR_SEED, FAR_SAMPLES = 7, 12                           # 筒から離れた点の確かめ（決まった乱数・頂点ごとの点の数）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cuff_pushout.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def unit(v):
    return v / np.linalg.norm(v)


def frame(axis, hint=None):
    """筒の輪を置く向き（軸・横・上）。hint を渡すと、前の輪とねじれないように横の向きを引き継ぐ（台本と同じ）。"""
    axis = unit(axis)
    if hint is None:
        side = np.cross(axis, [0.0, 0.0, 1.0])
        if np.linalg.norm(side) < 1e-3:
            side = np.cross(axis, [1.0, 0.0, 0.0])
    else:
        side = hint - axis * float(hint @ axis)
    side = unit(side)
    return axis, side, np.cross(axis, side)


def ring_radii(t, radial, core, sleeve_until):
    """前腕の 6 つの輪の太さ: 肘から sleeve_until までは芯の太さ core、手首側は、輪の ±0.12 の帯で sleeve_until より先の頂点の、
    軸からの距離の 90% 点（5 頂点に満たなければ肘の側の隣の輪と同じ。いちばん肘の輪はいつも芯）。t は頂点の骨に沿った位置、radial は軸からの距離（台本と同じ）。"""
    radii = []
    for k in range(RINGS + 1):
        tk = k / RINGS
        if tk < sleeve_until - 1e-6:
            radii.append(core)
            continue
        near = radial[(t >= tk - 0.12) & (t < tk + 0.12) & (t >= sleeve_until)]
        radii.append(float(np.percentile(near, 90)) if len(near) >= 5 else radii[-1])
    return radii


def heaviest(items, aliases):
    """1 頂点の [(頂点グループの名前, 重み)] の、いちばん重い名前（aliases は仲間の骨に足してから比べる。同じ重みは先）。"""
    totals = {}
    for name, weight in items:
        name = aliases.get(name, name)
        totals[name] = totals.get(name, 0.0) + weight
    return max(totals, key=totals.get) if totals else ""


def main(argv=None):
    import bmesh  # Blender の中でだけ使える
    import bpy
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
    body = bpy.data.objects.get(s["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"体のメッシュがありません: {s['body']}")
    arms = s["arms"]
    for arm in arms:
        label = arm["name"]
        for key in ("forearm", "hand", "palm_end"):
            if arm[key] not in armature.data.bones:
                raise SystemExit(f"{armature.name} に骨 {arm[key]} がありません（arms の {label} の {key}）")
        if s["proxy"].format(arm=label) in bpy.data.objects:
            raise SystemExit(f"物体 {s['proxy'].format(arm=label)} がもうあります（collision.cuff_pushout を 2 回かけない）")
        for key in ("group", "near_group"):
            if s[key].format(arm=label) in body.vertex_groups:
                raise SystemExit(f"{body.name} に頂点グループ {s[key].format(arm=label)} がもうあります")
        for key in ("near_modifier", "modifier"):
            if s[key].format(arm=label) in body.modifiers:
                raise SystemExit(f"{body.name} にモディファイア {s[key].format(arm=label)} がもうあります")
    saved = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    aw = armature.matrix_world
    inv = aw.inverted()                                  # 筒はアーマチュアの子なので、頂点はアーマチュアの空間で作る（原点になくても合う）
    deform = {bone.name for bone in armature.data.bones if bone.use_deform}
    names = {group.index: group.name for group in body.vertex_groups if group.name in deform}
    top = [heaviest([(names[g.group], g.weight) for g in v.groups if g.group in names], s["aliases"]) for v in body.data.vertices]
    bw = np.array(body.matrix_world)
    world = np.array([bw[:3, :3] @ np.array(v.co) + bw[:3, 3] for v in body.data.vertices])
    segments = s["segments"]

    def bone_point(name):
        return np.array(aw @ armature.data.bones[name].head_local)

    def build_proxy(name, rings, start_pole, end_pole, groups):
        """輪の並び [(中心, 半径, 横, 上, グループ)] と両端の点から、閉じた 1 本の筒を作る（台本と同じ頂点と面の並び）。"""
        mesh = bpy.data.meshes.new(name)
        proxy = bpy.data.objects.new(name, mesh)
        scene.collection.objects.link(proxy)
        index = {group: proxy.vertex_groups.new(name=group).index for group in groups}
        bm = bmesh.new()
        layer = bm.verts.layers.deform.verify()
        made = []
        for centre, radius, side, up, group in rings:
            ring = []
            for k in range(segments):
                angle = 2 * math.pi * k / segments
                v = bm.verts.new(inv @ Vector(centre + (side * math.cos(angle) + up * math.sin(angle)) * radius))
                v[layer][index[group]] = 1.0
                ring.append(v)
            made.append(ring)
        for first, second in zip(made[:-1], made[1:]):
            for k in range(segments):
                k2 = (k + 1) % segments
                bm.faces.new((first[k], first[k2], second[k2], second[k]))
        for ring, (point, group), flip in ((made[0], start_pole, True), (made[-1], end_pole, False)):
            c = bm.verts.new(inv @ Vector(point))
            c[layer][index[group]] = 1.0
            for k in range(segments):
                k2 = (k + 1) % segments
                bm.faces.new((ring[k2], ring[k], c) if flip else (ring[k], ring[k2], c))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        volume = bm.calc_volume(signed=True)
        if volume < 0:                                   # 面が外を向いているか（体積が正か）。逆なら全部裏返す
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
            volume = bm.calc_volume(signed=True)
        bm.to_mesh(mesh)
        bm.free()
        proxy.parent = armature
        modifier = proxy.modifiers.new("Armature", "ARMATURE")
        modifier.object = armature
        proxy.hide_render = True
        proxy.display_type = "WIRE"
        return proxy, volume

    def far_test(proxy):
        """筒から 5〜30 cm 離れた点が「内側」と判定されないか（Shrinkwrap の Outside と同じ見方: いちばん近い面の裏側か）。"""
        mesh = proxy.data
        points = [v.co.copy() for v in mesh.vertices]
        tree = BVHTree.FromPolygons(points, [tuple(p.vertices) for p in mesh.polygons])
        rng = np.random.default_rng(FAR_SEED)
        wrong = total = 0
        for v in mesh.vertices:
            for _ in range(FAR_SAMPLES):
                d = unit(rng.normal(size=3))
                p = v.co + Vector(d * rng.uniform(0.05, 0.30))
                location, normal, _, distance = tree.find_nearest(p)
                if distance < 0.045:                     # 別の所の面に近い点は、内側かもしれないので数えない
                    continue
                total += 1
                if (p - location).dot(normal) < 0:
                    wrong += 1
        return wrong, total

    report, proxies, cuff_index = {}, {}, {}
    for arm in arms:
        label = arm["name"]
        elbow, wrist, palm_end = bone_point(arm["forearm"]), bone_point(arm["hand"]), bone_point(arm["palm_end"])
        axis = wrist - elbow
        length = float(np.linalg.norm(axis))
        sel = np.array([n == arm["forearm"] for n in top])
        rel = world[sel] - elbow
        f_axis, f_side, f_up = frame(axis)
        t = rel @ f_axis / length
        radial = np.linalg.norm(rel - np.outer(rel @ f_axis, f_axis), axis=1)
        radii = ring_radii(t, radial, s["core_radius"], s["sleeve_until"])
        fore, hand = arm["forearm"], arm["hand"]
        rings = []
        r0 = radii[0]
        for deg in (60, 30):                             # 肘の側の丸い端
            angle = math.radians(deg)
            rings.append((elbow - f_axis * r0 * math.sin(angle), r0 * math.cos(angle), f_side, f_up, fore))
        for k in range(RINGS + 1):
            rings.append((elbow + axis * (k / RINGS), radii[k], f_side, f_up, fore))
        p_axis, p_side, p_up = frame(palm_end - wrist, f_side)
        rp = s["palm_radius"] * 0.9
        rings.append(((wrist + palm_end) / 2, s["palm_radius"], p_side, p_up, hand))
        rings.append((palm_end, rp, p_side, p_up, hand))
        for deg in (30, 60):                             # 手のひらの先の丸い端
            angle = math.radians(deg)
            rings.append((palm_end + p_axis * rp * math.sin(angle), rp * math.cos(angle), p_side, p_up, hand))
        proxy, volume = build_proxy(s["proxy"].format(arm=label), rings, (elbow - f_axis * r0, fore), (palm_end + p_axis * rp, hand), (fore, hand))
        proxies[label] = proxy
        wrong, total = far_test(proxy)
        index = np.where(sel)[0][t < s["sleeve_until"]]
        cuff_index[label] = [int(i) for i in index]
        report[label] = {"proxy": proxy.name, "sleeve_verts": int(len(index)), "forearm_length_mm": round(length * 1000, 1),
                         "forearm_rings_mm": [round(r * 1000, 1) for r in radii], "proxy_verts": len(proxy.data.vertices),
                         "proxy_volume_cm3": round(volume * 1e6, 1), "far_points_judged_inside": wrong, "far_points_tested": total}
    for arm, other in ((arms[0], arms[1]), (arms[1], arms[0])):
        label = arm["name"]
        for key in ("group", "near_group"):
            body.vertex_groups.new(name=s[key].format(arm=label)).add(cuff_index[label], 1.0, "REPLACE")
        gate = body.modifiers.new(s["near_modifier"].format(arm=label), "VERTEX_WEIGHT_PROXIMITY")
        gate.vertex_group = s["near_group"].format(arm=label)
        gate.target = proxies[other["name"]]
        gate.proximity_mode = "GEOMETRY"
        gate.proximity_geometry = {"FACE"}
        gate.min_dist = s["gate_far"]                    # この距離で重み 0
        gate.max_dist = s["gate_near"]                   # この距離以内は重み 1
        gate.falloff_type = "LINEAR"
        modifier = body.modifiers.new(s["modifier"].format(arm=label), "SHRINKWRAP")
        modifier.target = proxies[other["name"]]
        modifier.wrap_method = "NEAREST_SURFACEPOINT"
        modifier.wrap_mode = "OUTSIDE"
        modifier.offset = s["offset"]
        modifier.vertex_group = s["near_group"].format(arm=label)
    armature.data.pose_position = saved
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    out = {"schema_version": "model3d-cuff-pushout/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
           "body": body.name, "arms": report, "body_modifiers": [m.name + ":" + m.type for m in body.modifiers], "settings": s}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(out, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "arms": list(report)}))


if __name__ == "__main__":
    main()
