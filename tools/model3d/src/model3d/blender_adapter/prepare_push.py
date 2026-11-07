"""Blender の中で動かす: render.stage の push（描く直前の押し出し）の準備として、当たりの写しと、押し出す頂点の組を足す。

    blender --background --factory-startup --python prepare_push.py -- --blend in.blend --out-blend out.blend \
        --report report.json --settings settings.json

settings.json（render.prepare_push が書く。長さは m で、シーンの scale_length で割って Blender の単位にする）:
- armature（任意）: Armature モディファイアの無いメッシュの骨を読むアーマチュア。省くと、ファイルにアーマチュアが 1 つならそれ。
- colliders: [{"from": 写す元のメッシュ, "name": 写しの名前, "weld": 頂点をまとめる距離, "decimate": 面を残す割合}]
- groups: [{"mesh": 頂点の組を足すメッシュ, "bones": 種の骨（その子孫も）, "top": 上の端の高さ, "fade": 重みが 0 → 1 になる幅,
  "weld": つながりを見るときに同じ点とみなす格子, "front": 前を向く頂点の組の名前, "back": 後ろの組の名前}]

当たりの写し（リポジトリ外の試作の手順）:
- 元のメッシュを写し（モディファイアは外す）、世界で weld 以内の頂点をまとめ（物体の拡大で直した中の距離。軸ごとに拡大が違えば
  いちばん大きい拡大で割る）、穴（付け根や口）をふさぎ、面の向きをそろえる。
  体積が負（内向き）なら裏返して外向きにする。筒のままだと、穴のふちの面の向きで中か外かを読み違えるため。
- 面を decimate の割合に減らし、すべての面をなめらかにする（ふさいだ口の平らな面のふちで、向きが混ざるように）。
  写しに付いてきた形状キーは先に外す（キーのあるメッシュは面を減らせない。元のメッシュは変えない。写しは骨でだけ動く）。
- 体積は、物体の中で測った値に、変換の行列式の大きさと scale_length³ を掛けて、世界の L で報告する。
- 元の Armature モディファイアと同じアーマチュア・設定（頂点の組の絞りと反転、ビューポートと描画で効くかも）で動かす（親と変換は元のまま）。カメラ・影・反射などには写さない
  （透明な材質も付ける）。model3d_collider の印を付け、look.toon の既定のメッシュ選びから外す（hide_render は付けない）。
押し出す頂点の組（試作の帯の印と同じ手順）:
- 種は、bones の骨とその子孫の骨に重みのある頂点。高さ top より下で、種とつながった部分（同じ位置の頂点はつながる）を選ぶ。
- 重みは、高さ z で smoothstep((top - z) / fade)（上の端で 0、fade 下で 1）を 1/64 に丸めたもの（0 の頂点は入れない）。
- 頂点の法線がキャラの前（-Y）を向けば front、ほかは back の組に入れる。
- メッシュをほかの物体と分け合う（リンク複製）なら、先にこの物体だけのメッシュにする（重みはメッシュに付くので、分け合ったまま
  だと、高さの違うほかの物体の重みまで変わる）。分け合っていた物体を報告に載せる。
高さと法線は世界の座標（メッシュの静止の形）。
**検証**: 本物の Blender 4.5.15 で、リポジトリ外の試作と同じ写しと頂点の組になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np

LEVELS = 64                                              # 重みの刻み（試作と同じ）
COLLIDER_MARK = "model3d_collider"                      # 当たりの写しの印（look.toon の既定のメッシュ選びから外す）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="prepare_push.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--settings", required=True)
    return ap.parse_args(argv)


def subtree(parents, roots):
    """roots の骨とその子孫の名前。parents は {骨の名前: 親の名前か None}。"""
    children = {}
    for name, parent in parents.items():
        children.setdefault(parent, []).append(name)
    names, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name in names or name not in parents:
            continue
        names.add(name)
        todo.extend(children.get(name, []))
    return names


def fade_weights(z, top, fade):
    """高さ z の重み: smoothstep((top - z) / fade)。上の端で 0、fade 下から 1。"""
    w = np.clip((top - np.asarray(z, float)) / fade, 0, 1)
    return w * w * (3 - 2 * w)


def weld_index(points, step):
    """位置を step の格子に丸め、同じ格子の頂点に同じ番号を振る（戻り: 頂点ごとの番号と番号の数）。"""
    _, inverse = np.unique(np.round(np.asarray(points, float) / step).astype(np.int64), axis=0, return_inverse=True)
    inverse = inverse.ravel()
    return inverse, int(inverse.max()) + 1 if len(inverse) else 0


def components(count, edges):
    """count 個の点を edges（(辺, 2)）でつないだときの、点ごとの成分の代表の番号。"""
    parent = np.arange(count)

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for a, b in np.asarray(edges, dtype=np.int64).reshape(-1, 2):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb
    return np.array([find(i) for i in range(count)], dtype=np.int64)


def hanging_part(points, edges, seeds, top, step):
    """高さ top より下で、種（seeds。頂点ごとの真偽）とつながった頂点の番号（小さい順）と、つながった部分の数と、
    top より下の種の数。位置が step の格子で同じ頂点は 1 つとみなす。"""
    points = np.asarray(points, float)
    weld, count = weld_index(points, step)
    height = np.zeros(count)
    height[weld] = points[:, 2]                          # 同じ格子の頂点は、後の頂点の高さ（試作と同じ）
    low = height < top
    we = weld[np.asarray(edges, dtype=np.int64).reshape(-1, 2)] if len(edges) else np.zeros((0, 2), np.int64)
    root = components(count, we[low[we[:, 0]] & low[we[:, 1]]])
    seeded = np.asarray(seeds, bool) & low[weld]
    pieces = np.unique(root[weld[seeded]])
    chosen = np.where(np.isin(root[weld], pieces) & low[weld])[0]
    return chosen, int(len(pieces)), int(seeded.sum())


def group_levels(z, normals_y, top, fade):
    """選んだ頂点の、重みの刻み（0〜LEVELS の整数）と、前（法線が -Y）を向くか。"""
    levels = np.round(fade_weights(z, top, fade) * LEVELS)
    return levels, np.asarray(normals_y) < 0


def local_distance(world, distance):
    """世界の距離 distance を、変換 world（4×4）の物体の中の距離に直す。軸ごとに拡大が違えば、いちばん大きい拡大で割る
    （どの向きでも、世界で distance を超えない）。"""
    scale = float(np.linalg.norm(np.asarray(world, float)[:3, :3], axis=0).max())
    return distance / scale if scale > 0 else distance


def world_litres(local_volume, world, unit):
    """物体の中で測った体積を、世界の体積（L）に直す: |体積| × |det(world の 3×3)| × scale_length³ × 1000。"""
    return abs(local_volume) * abs(float(np.linalg.det(np.asarray(world, float)[:3, :3]))) * unit ** 3 * 1000


ARMATURE_KEYS = ("use_deform_preserve_volume", "use_vertex_groups", "use_bone_envelopes", "vertex_group", "invert_vertex_group",
                 "show_viewport", "show_render")       # 効く・効かない（ビューポートと描画）も元と同じにする


def signed_volume_bmesh(bm):
    """bmesh の面の並びの向きでの体積（外向きなら正）。"""
    return sum(t[0].vert.co.dot(t[1].vert.co.cross(t[2].vert.co)) for t in bm.calc_loop_triangles()) / 6


def armature_of(ob):
    """メッシュを動かす最初の Armature モディファイア（無ければ None）。"""
    return next((m for m in ob.modifiers if m.type == "ARMATURE" and m.object is not None), None)


def hidden_material(bpy):
    """写しに付ける、透明な材質（描かない）。"""
    mat = bpy.data.materials.new("proxy_hidden")
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    clear = nodes.new("ShaderNodeBsdfTransparent")
    mat.node_tree.links.new(clear.outputs[0], out.inputs["Surface"])
    if hasattr(mat, "surface_render_method"):
        mat.surface_render_method = "BLENDED"
    return mat


def make_collider(bpy, bmesh, spec, material, unit=1.0):
    """当たりの写しを 1 つ作る。戻り: 報告の中身。"""
    src = bpy.data.objects.get(spec["from"])
    if src is None or src.type != "MESH":
        raise SystemExit(f"写す元のメッシュがありません: {spec['from']}")
    if spec["name"] in bpy.data.objects:
        raise SystemExit(f"写しの名前の物体がもうあります: {spec['name']}")
    source_armature = armature_of(src)
    px = src.copy()                                      # 親と変換は元のまま
    px.data = src.data.copy()
    px.name = spec["name"]
    px.modifiers.clear()
    bpy.context.scene.collection.objects.link(px)
    dropped = 0
    if px.data.shape_keys:                               # 写しに付いてきた形状キーは外す（面を減らせないため。元のメッシュは変えない）
        dropped = len(px.data.shape_keys.key_blocks)
        px.shape_key_clear()
    bm = bmesh.new()
    bm.from_mesh(px.data)
    weld = local_distance(np.array(src.matrix_world), spec["weld"])   # bmesh は物体の中の座標なので、物体の拡大で直す
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=weld)
    holes = [e for e in bm.edges if e.is_boundary]
    bmesh.ops.holes_fill(bm, edges=holes, sides=0)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    volume = signed_volume_bmesh(bm)
    if volume < 0:                                       # 内向きになることがある（体積が負）。外向きにそろえる
        bmesh.ops.reverse_faces(bm, faces=bm.faces)
    open_left = sum(1 for e in bm.edges if e.is_boundary)
    bm.to_mesh(px.data)
    bm.free()
    if "custom_normal" in px.data.attributes:            # 元の法線は、ふさいだ形に合わない
        px.data.attributes.remove(px.data.attributes["custom_normal"])
    px.data.materials.clear()
    px.data.materials.append(material)
    bpy.context.view_layer.objects.active = px
    decimate = px.modifiers.new("dec", "DECIMATE")
    decimate.ratio = spec["decimate"]
    bpy.ops.object.modifier_apply(modifier=decimate.name)
    px.data.shade_smooth()
    if source_armature is not None:
        m = px.modifiers.new("armature", "ARMATURE")
        m.object = source_armature.object
        for key in ARMATURE_KEYS:                       # 頂点の組の絞り（反転も）と、効く・効かないまで元と同じにして、元と同じに動かす
            setattr(m, key, getattr(source_armature, key))
    for key in ("visible_camera", "visible_shadow", "visible_diffuse", "visible_glossy", "visible_transmission",
                "visible_volume_scatter"):
        setattr(px, key, False)
    px[COLLIDER_MARK] = True                             # look.toon などの既定のメッシュ選びから外す印（hide_render は付けない）
    return {"name": px.name, "from": src.name, "armature": source_armature.object.name if source_armature else None,
            "filled_edges": len(holes), "open_edges": open_left,
            "volume_l": round(world_litres(volume, np.array(src.matrix_world), unit), 3), "shape_keys_dropped": dropped,
            "flipped": bool(volume < 0), "vertices": len(px.data.vertices), "faces": len(px.data.polygons), "weld_local": weld}


def make_groups(bpy, spec, fallback_armature):
    """押し出す頂点の組（front と back）を 1 つのメッシュに足す。戻り: 報告の中身。"""
    ob = bpy.data.objects.get(spec["mesh"])
    if ob is None or ob.type != "MESH":
        raise SystemExit(f"頂点の組を足すメッシュがありません: {spec['mesh']}")
    for name in (spec["front"], spec["back"]):
        if name in ob.vertex_groups:
            raise SystemExit(f"{ob.name} に頂点の組 {name} がもうあります")
    sharing = sorted(o.name for o in bpy.data.objects if o.data == ob.data and o.name != ob.name)
    if sharing:                                          # リンク複製は、この物体だけのメッシュにしてから組を足す（重みは
        ob.data = ob.data.copy()                         # メッシュに付くので、ほかの物体の重みまで変わるため）
    modifier = armature_of(ob)
    arm = modifier.object if modifier is not None else fallback_armature
    if arm is None:
        raise SystemExit(f"{ob.name} の骨（Armature モディファイアかアーマチュア）がありません")
    parents = {b.name: (b.parent.name if b.parent else None) for b in arm.data.bones}
    missing = [b for b in spec["bones"] if b not in parents]
    if missing:
        raise SystemExit(f"{arm.name} に骨がありません: {missing}")
    bones = subtree(parents, spec["bones"])
    me = ob.data
    n = len(me.vertices)
    world = np.array(ob.matrix_world)
    co = np.zeros(n * 3)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
    nr = np.zeros(n * 3)
    if hasattr(me, "vertex_normals"):
        me.vertex_normals.foreach_get("vector", nr)
    else:                                                # Blender 4.0 まで
        me.vertices.foreach_get("normal", nr)
    nr = nr.reshape(-1, 3) @ np.linalg.inv(world[:3, :3])   # 法線は逆行列の転置で世界へ
    edges = np.zeros(len(me.edges) * 2, dtype=np.int64)
    me.edges.foreach_get("vertices", edges)
    names = {g.index: g.name for g in ob.vertex_groups}
    seeds = np.zeros(n, bool)
    for v in me.vertices:
        for g in v.groups:
            if g.weight > 0 and names.get(g.group) in bones:
                seeds[v.index] = True
    chosen, pieces, low_seeds = hanging_part(co, edges.reshape(-1, 2), seeds, spec["top"], spec["weld"])
    if not low_seeds:
        raise SystemExit(f"{ob.name} に、骨 {sorted(bones)} に重みがあって高さ top より下の頂点がありません")
    levels, front = group_levels(co[chosen, 2], nr[chosen, 1], spec["top"], spec["fade"])
    counts = {}
    for name, sel in ((spec["back"], ~front), (spec["front"], front)):   # 試作と同じく後ろの組から
        g = ob.vertex_groups.new(name=name)
        for level in np.unique(levels[sel]):
            if level > 0:
                g.add(chosen[sel][levels[sel] == level].tolist(), float(level) / LEVELS, "REPLACE")
        counts[name] = int((levels[sel] > 0).sum())
    return {"mesh": ob.name, "armature": arm.name, "bones": sorted(bones), "seeds": int(seeds.sum()), "pieces": pieces,
            "chosen": int(len(chosen)), "front": {"name": spec["front"], "vertices": counts[spec["front"]]},
            "back": {"name": spec["back"], "vertices": counts[spec["back"]]},
            "left_out": int((levels == 0).sum()), "z": [round(float(co[chosen, 2].min()), 4), round(float(co[chosen, 2].max()), 4)],
            "separated_from": sharing}


def main(argv=None):
    import bmesh  # Blender の中でだけ使える
    import bpy

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    unit = bpy.context.scene.unit_settings.scale_length or 1.0
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    named = settings.get("armature")
    if named is not None:
        fallback = bpy.data.objects.get(named)
        if fallback is None or fallback.type != "ARMATURE":
            raise SystemExit(f"アーマチュアがありません: {named}")
    else:
        fallback = armatures[0] if len(armatures) == 1 else None
    colliders, groups = [], []
    material = hidden_material(bpy) if settings.get("colliders") else None
    for spec in settings.get("colliders", []):
        colliders.append(make_collider(bpy, bmesh, {**spec, "weld": spec["weld"] / unit}, material, unit))
    for spec in settings.get("groups", []):
        groups.append(make_groups(bpy, {**spec, "top": spec["top"] / unit, "fade": spec["fade"] / unit,
                                        "weld": spec["weld"] / unit}, fallback))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend))
    report = {"schema_version": "model3d-prepare-push/0.1", "blender": bpy.app.version_string, "scale_length": unit,
              "colliders": colliders, "groups": groups, "settings": settings}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"colliders": len(colliders), "groups": len(groups)}))


if __name__ == "__main__":
    main()
