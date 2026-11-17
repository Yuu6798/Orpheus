"""Blender の中で動かす: 首と頭のつなぎ目を直す（model.neck_junction）。頭の部品に首が無く（いちばん下があごの先）、体の首が上の開いた筒で
首の骨だけに付いているモデルでは、頭を上げる・かしげる・回すと、筒の上の縁や、あごと首のあいだの奥が見える。体のメッシュだけを直す
（頭・髪・骨・色の絵は変えない）。

    blender --background --factory-startup --python neck_junction.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

1. remove_groups（目の骨など）の重みを、体のメッシュの全部の頂点から外し、残りの骨で割り直す（残りが無ければ首の骨に 1）。
2. 首の上の端を、頭の骨へなだらかに付け替える。首の骨の付け根から頭の骨の付け根へ引いた軸から radius の内で、高さ ramp_from_z より上、
   首・頭・remove_groups の重みの和が 0.5 を超える頂点（首の上の端）の、ramp_from_z から首のいちばん上 z_top までで、0 →
   head_weight_top になめらか（smoothstep）に変わる値 s で、頭の重みを増やす。head_weight（既定 add）: add は、もとの重みを全部
   (1 − s) 倍して頭に s を足す（台本と同じ。もとから頭の重み h がある頂点は h(1 − s) + s）。set は頭の重みを s にし、ほかの骨の
   重みを合わせて 1 − s になるよう同じ割合で縮める（頭の骨だけだった頂点は、残りを首の骨に）。頭の重みが無い頂点では同じ。
3. 首の中から頭の中へ伸びる栓（ふた付きの筒）を足す。z_top より plug_from_below_top 下の高さの、首の上の端の頂点（±slab）の輪郭を
   plug_segments の向きに分けて半径を取り（無い向きは補い、[0.1, 0.2, 0.4, 0.2, 0.1] でならして plug_inset を掛ける）、上へ plug_height
   だけ、上ほど plug_top_scale へすぼめながら plug_rings 段の輪を積み、上をふさぐ（いちばん上の点は cap 上）。下の輪は首の上の端と同じ
   重み、上へ向けて頭の骨だけにする。面は外向き。
4. 栓と首の上の端（栓の下の高さより recolour_below 下から上で、頂点が全部首の上の端の面）の uv を 1 点にまとめ、首の肌の 1 色にする。
   色は、栓の下の高さから uv_band 下までの首の上の端の頂点の色（最初の頂点の面の材質のいちばん大きい色の絵の 5×5 画素の平均。
   頂点ごとに、その材質の面の角の uv で読む）の中央値にいちばん近い頂点の uv。1 色にする面の材質も、その材質にそろえる
   （首の上の端が別の色の絵の材質にまたがっても、同じ色になるように）。
首のまわりの形は入り組んでいる（画像から作ったメッシュ。縁の輪がきれいに取れない）ので、つながりには頼らず、位置だけで決める。
元のファイルは変えない。1 Blender 単位 = 1 m のシーンだけ。形状キーのある体・ほかの物体とメッシュを分け合う体は止める。
**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、リポジトリ外の 3 作目の VRM のダンスの台本と同じ .blend になることを確かめた
（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

DEFAULTS = {"armature": None, "remove_groups": [], "head_weight": "add", "plug_rings": 6, "plug_segments": 28, "plug_inset": 0.985,
            "plug_top_scale": 0.5, "uv_band": 0.008, "recolour_below": 0.006, "slab": 0.007, "cap": 0.004}
HEAD_WEIGHT = ("add", "set")     # 頭の重みの付け方（add: 台本と同じ足し方、set: 頭の重みを s にする）
REQUIRED = ("body", "neck_bone", "head_bone", "radius", "ramp_from_z", "head_weight_top", "plug_from_below_top", "plug_height")
SMOOTH = (0.1, 0.2, 0.4, 0.2, 0.1)     # 栓の半径をならす重み（向きの輪の上で）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="neck_junction.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    return {**DEFAULTS, **settings}


def ramp(z, ramp_from_z, z_top, head_weight_top):
    """首の上の端の高さ z での頭の重み（ramp_from_z で 0、z_top で head_weight_top。なめらかな段）。"""
    t = min(max((z - ramp_from_z) / (z_top - ramp_from_z), 0.0), 1.0)
    return t * t * (3 - 2 * t) * head_weight_top


def set_head_weight(weights, head, neck, s):
    """head_weight: set の重み: 頂点の重み（グループの番号 → 重み）の、頭の骨の重みを s にした重み。ほかの骨の重みは、合わせて
    1 − s になるよう同じ割合で縮める（もとの頭の重みを h として ×(1 − s) ÷ (1 − h)。h が 0 なら add と同じ ×(1 − s)）。頭の骨
    だけだった頂点は、残りを首の骨に。並びは、もとのグループの順のあとに頭（もとから頭があれば、その位置のまま書き換わる）。"""
    rest = 1.0 - weights.get(head, 0.0)
    out = {}
    if rest > 1e-9:
        for g, w in weights.items():
            if g != head:
                out[g] = w * (1 - s) / rest
    else:
        out[neck] = 1.0 - s
    out[head] = s
    return out


def loop_of(loops, material):
    """頂点の角（loop）のうち、面の材質の番号が material の最初の角（無ければ None）。"""
    for loop in loops:
        if loop.face.material_index == material:
            return loop
    return None


def plug_radius(points, centre, segments, inset):
    """栓の輪の向きごとの半径。points は輪の高さの頂点の (x, y)。向きごとのいちばん遠い頂点の距離を、頂点の無い向きは
    隣から補い（輪の上で直線）、SMOOTH でならして inset を掛ける。"""
    angle = np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0])
    radius = np.hypot(points[:, 0] - centre[0], points[:, 1] - centre[1])
    bins = np.floor((angle + math.pi) / (2 * math.pi) * segments).astype(int) % segments
    out = np.full(segments, np.nan)
    for b in range(segments):
        if (bins == b).any():
            out[b] = radius[bins == b].max()
    good = ~np.isnan(out)
    index = np.arange(segments)
    out = np.interp(index, np.concatenate([index[good] - segments, index[good], index[good] + segments]), np.tile(out[good], 3))
    return np.convolve(np.concatenate([out[-2:], out, out[:2]]), np.array(SMOOTH), mode="valid") * inset


def main(argv=None):
    import bmesh
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if settings["armature"]:
        armature = bpy.data.objects.get(settings["armature"])
        if armature is None or armature.type != "ARMATURE":
            raise SystemExit(f"armature のアーマチュアがありません: {settings['armature']}")
    elif len(armatures) == 1:
        armature = armatures[0]
    else:
        raise SystemExit(f"アーマチュアが {len(armatures)} あります（armature で名前を渡してください）")
    scene = bpy.context.scene
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    body = bpy.data.objects.get(settings["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"body のメッシュがありません: {settings['body']}")
    if body.data.shape_keys is not None:
        raise SystemExit(f"{body.name} に形状キーがあります（足す栓の頂点の形状キーを決められないので扱わない）")
    if body.data.users - (1 if body.data.use_fake_user else 0) > 1:
        raise SystemExit(f"{body.name} のメッシュはほかの物体と分け合っています（リンク複製。分けてから直してください）")
    if body.data.uv_layers.active is None:
        raise SystemExit(f"{body.name} に uv がありません（栓と首の上の端を肌の色にするのに要る）")
    for key in ("neck_bone", "head_bone"):
        if settings[key] not in armature.data.bones:
            raise SystemExit(f"{armature.name} に {key} の骨がありません: {settings[key]}")
    group = {g.name: g.index for g in body.vertex_groups}
    for key in ("neck_bone", "head_bone"):
        if settings[key] not in group:
            raise SystemExit(f"{body.name} に {key} の頂点グループがありません: {settings[key]}")
    position = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    aw = armature.matrix_world
    bw = body.matrix_world
    bw_inv = bw.inverted()
    neck_head = aw @ armature.data.bones[settings["neck_bone"]].head_local
    head_head = aw @ armature.data.bones[settings["head_bone"]].head_local
    if abs(head_head.z - neck_head.z) < 1e-9:
        raise SystemExit("首の骨と頭の骨の付け根が同じ高さです（首の軸を決められない）")

    bm = bmesh.new()
    bm.from_mesh(body.data)
    bm.verts.ensure_lookup_table()
    deform = bm.verts.layers.deform.verify()
    uv_layer = bm.loops.layers.uv.verify()
    gi_head, gi_neck = group[settings["head_bone"]], group[settings["neck_bone"]]
    remove = [group[n] for n in settings["remove_groups"] if n in group]
    missing = [n for n in settings["remove_groups"] if n not in group]       # 体に無い頂点グループ（報告して要確認）
    world = np.array([bw @ v.co for v in bm.verts])

    def axis_xy(z):
        """首の軸（首の骨の付け根 → 頭の骨の付け根）の、高さ z での (x, y)。"""
        t = (z - neck_head.z) / (head_head.z - neck_head.z)
        return neck_head.x + (head_head.x - neck_head.x) * t, neck_head.y + (head_head.y - neck_head.y) * t

    ax = np.array([axis_xy(z) for z in world[:, 2]])
    dist = np.hypot(world[:, 0] - ax[:, 0], world[:, 1] - ax[:, 1])
    neck_w = np.array([v[deform].get(gi_neck, 0.0) + v[deform].get(gi_head, 0.0) + sum(v[deform].get(g, 0.0) for g in remove)
                       for v in bm.verts])
    region = (world[:, 2] > settings["ramp_from_z"]) & (dist < settings["radius"]) & (neck_w > 0.5)
    if not region.any():
        raise SystemExit("首の上の端の頂点がありません（ramp_from_z・radius・首の骨の名前を確かめてください）")
    z_top = float(world[region, 2].max())
    if z_top <= settings["ramp_from_z"]:
        raise SystemExit("首の上の端の高さが ramp_from_z と同じです")
    report = {"region_verts": int(region.sum()), "z_top": z_top, "ramp_from_z": settings["ramp_from_z"]}

    def head_weight(z):
        return ramp(z, settings["ramp_from_z"], z_top, settings["head_weight_top"])

    # 1. remove_groups の重みを外す（体のメッシュ全体）
    removed = 0
    for v in bm.verts:
        d = v[deform]
        if sum(d.get(g, 0.0) for g in remove) > 1e-9:
            removed += 1
            for g in remove:
                if g in d:
                    del d[g]
            total = sum(d.values())
            if total > 1e-9:
                for g in list(d.keys()):
                    d[g] = d[g] / total
            else:
                d[gi_neck] = 1.0
    report["removed_weight_verts"] = removed
    report["remove_groups_missing"] = missing

    # 2. 首の上の端を頭の骨へ付け替える
    ramped = preweighted = 0
    for i in np.where(region)[0]:
        d = bm.verts[int(i)][deform]
        s = head_weight(world[i, 2])
        if s <= 0:
            continue
        preweighted += d.get(gi_head, 0.0) > 0
        if settings["head_weight"] == "set":
            weights = set_head_weight(dict(d.items()), gi_head, gi_neck, s)
            for g in [g for g in d.keys() if g not in weights]:
                del d[g]
            for g, w in weights.items():
                d[g] = w
        else:                                   # add（台本と同じ）: もとの重みを全部 (1 − s) 倍して、頭に s を足す
            for g in list(d.keys()):
                d[g] = d[g] * (1 - s)
            d[gi_head] = d.get(gi_head, 0.0) + s
        ramped += 1
    report["head_weight"] = settings["head_weight"]
    report["ramped_verts"] = ramped
    report["ramped_with_head_weight"] = int(preweighted)

    # 3. 栓の輪の大きさ
    z_p = z_top - settings["plug_from_below_top"]
    slab = region & (np.abs(world[:, 2] - z_p) < settings["slab"])
    if not slab.any():
        raise SystemExit("栓の高さに首の上の端の頂点がありません（plug_from_below_top・slab を確かめてください）")
    cx = (world[slab, 0].min() + world[slab, 0].max()) / 2
    cy = (world[slab, 1].min() + world[slab, 1].max()) / 2
    segs = settings["plug_segments"]
    radius = plug_radius(world[slab][:, :2], (cx, cy), segs, settings["plug_inset"])
    report["plug"] = {"z_from": z_p, "z_to": z_p + settings["plug_height"], "centre": [float(cx), float(cy)],
                      "radius_cm": [float(radius.min() * 100), float(radius.max() * 100)], "slab_verts": int(slab.sum())}

    # 4. 色を取る 1 点
    band = np.where(region & (world[:, 2] > z_p - settings["uv_band"]) & (world[:, 2] <= z_p))[0]
    band = [int(i) for i in band if bm.verts[int(i)].link_loops]
    if not band:
        raise SystemExit("栓の色を取る首の頂点がありません（uv_band を確かめてください）")
    first = bm.verts[band[0]].link_loops[0]
    src_mat, src_smooth = first.face.material_index, first.face.smooth
    material = body.material_slots[src_mat].material if src_mat < len(body.material_slots) else None
    image = None
    if material is not None and material.node_tree is not None:
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and (image is None or node.image.size[0] * node.image.size[1] > image.size[0] * image.size[1]):
                image = node.image
    if image is None or not image.size[0] or not image.size[1]:
        raise SystemExit(f"首の面の材質（{material.name if material else None}）に色の絵がありません")
    iw, ih = image.size
    pixels = np.empty(iw * ih * image.channels, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(ih, iw, image.channels)

    def colour_at(uv):
        x = min(max(int(uv.x % 1.0 * iw), 0), iw - 1)
        y = min(max(int(uv.y % 1.0 * ih), 0), ih - 1)
        return pixels[max(y - 2, 0):y + 3, max(x - 2, 0):x + 3, :3].reshape(-1, 3).mean(axis=0)

    loops = [loop for loop in (loop_of(bm.verts[i].link_loops, src_mat) for i in band) if loop is not None]
    cands = [(loop[uv_layer].uv.copy(), colour_at(loop[uv_layer].uv)) for loop in loops]
    cols = np.array([c for _, c in cands])
    median = np.median(cols, axis=0)
    pick = int(np.argmin(np.linalg.norm(cols - median, axis=1)))
    src_uv = cands[pick][0]
    report["plug_colour"] = {"candidates": len(cands), "median": [float(v) for v in median], "picked": [float(v) for v in cols[pick]],
                             "image": image.name}

    # 首の上の端（栓の下の高さより recolour_below 下から上）の面を、同じ材質の同じ 1 点の色にする
    recoloured = rematerialled = 0
    region_set = set(int(i) for i in np.where(region)[0])
    for f in bm.faces:
        if all(v.index in region_set and world[v.index, 2] > z_p - settings["recolour_below"] for v in f.verts):
            for loop in f.loops:
                loop[uv_layer].uv = src_uv
            if f.material_index != src_mat:
                f.material_index = src_mat
                rematerialled += 1
            recoloured += 1
    report["neck_top_faces_recoloured"] = recoloured
    report["neck_top_faces_material_changed"] = rematerialled

    # 5. 栓を足す
    rings = []
    n = settings["plug_rings"]
    base = head_weight(z_p)
    for k in range(n + 1):
        f = k / n
        z = z_p + settings["plug_height"] * f
        scale = 1.0 + (settings["plug_top_scale"] - 1.0) * (f * f)
        s = base + (1.0 - base) * min(1.0, f * 3.0)          # 下の輪は首と同じ重み、上へ向けて頭の骨だけに
        ring = []
        for b in range(segs):
            angle = -math.pi + (b + 0.5) * 2 * math.pi / segs
            v = bm.verts.new(bw_inv @ Vector((cx + math.cos(angle) * radius[b] * scale, cy + math.sin(angle) * radius[b] * scale, z)))
            v[deform][gi_head] = s
            if s < 1.0:
                v[deform][gi_neck] = 1.0 - s
            ring.append(v)
        rings.append(ring)
    top = bm.verts.new(bw_inv @ Vector((cx, cy, z_p + settings["plug_height"] + settings["cap"])))
    top[deform][gi_head] = 1.0
    faces = []
    for k in range(n):
        for b in range(segs):
            b2 = (b + 1) % segs
            faces.append(bm.faces.new((rings[k][b], rings[k][b2], rings[k + 1][b2], rings[k + 1][b])))   # 外向き（上から見て反時計回り）
    for b in range(segs):
        faces.append(bm.faces.new((rings[n][b], rings[n][(b + 1) % segs], top)))
    for f in faces:
        f.material_index = src_mat
        f.smooth = src_smooth
        for loop in f.loops:
            loop[uv_layer].uv = src_uv
    bm.normal_update()
    report["plug"].update(verts=(n + 1) * segs + 1, faces=len(faces), uv=[float(src_uv.x), float(src_uv.y)],
                          material=material.name if material else None)
    bm.to_mesh(body.data)
    bm.free()
    body.data.update()
    armature.data.pose_position = position
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True)
    report = {"schema_version": "model3d-neck-junction/0.1", "blender": bpy.app.version_string, "armature": armature.name, "body": body.name,
              **report, "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "region_verts": report["region_verts"], "plug_verts": report["plug"]["verts"]}))


if __name__ == "__main__":
    main()
