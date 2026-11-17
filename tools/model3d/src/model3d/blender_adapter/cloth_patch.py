"""Blender の中で動かす: 布（裾など）の内側から見て、裏地の色でなく見える所に、裏地の色の当て布（面）を足す（cloth.patch_lining）。
モデルの .blend にも踊りの .blend にも使える。

    blender --background --factory-startup --python cloth_patch.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: 裾を下から見上げると、赤い裏地に黒い筋・三角・しみが見えた。調べると、裏地そのものに
切れ目やすき間があり、そこから表の布の裏側が見えている所（大半）と、裏地の面なのに色の絵が黒い所があった。前者は色の絵を塗っても直らない
（塗ると表の色が変わる）ので、黒く見える面のすぐ内側に、裏地の色の面を足して隠す。

やり方（初期姿勢で決める）:
  - 布の内側の何か所か（縦の軸の上の 6 つの高さと、3 つの高さで軸から少し外した 4 か所ずつ。軸から外す量は、その高さの布の内側までの距離の
    view_spread 倍）から見て、ほかの布の面に隠れずに見える面を「内側から見える面」とする。布より下から見上げる位置は入れない（布のふちの、外からも
    見える面まで選んでしまうため）。
  - その面の色の絵を何点か（samples。決まった乱数）読み、裏地の色（lining_color: 1 つの色の成分 channel が min 以上で、ほかの成分がその ratio 倍以下）
    でない点が min_dark 以上ある面を選ぶ。裏地の色だけの内向きの面のうち、いちばん広い面の uv を「裏地の色の 1 点」にする。
  - 選んだ面の頂点を写し、内側（面の向きを内向きにそろえた法線の向き）へ offset だけ寄せて、新しい面を張る。骨の重みは元の頂点と同じ（一緒に動く）。
    向きは内向き。uv は全部「裏地の色の 1 点」（色の絵は書き換えない。材質も同じ）。
  - 張る前に、当て布の面の上の点から外（水平〜斜め上、左右に 50° まで）へ線を飛ばし、cover_reach 以内で元の布の面にも cover_objects（体など。
    揺れる布は入れない）にも当たらずに抜ける面は張らない（ひだの折り目で、内側へ寄せた当て布が隣の面を突き抜けて、表に裏地の色の細い線が出たため）。
  - 当て布の頂点に頂点グループ group（既定 lining_patch。骨の名前ではないので、動きにも VRM の重みにも関わらない）を付ける
    （cloth.pushout・check.cloth_layers・check.cloth_over が、当て布の面を見分ける）。
  布のふちの下向きの面（外からも見える面）は、この見つけ方では選ばれないので、そのまま残る。

報告は model3d-cloth-patch/0.1。**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ形・uv・重みになることを
確かめた（docs/model3d.md）。CI には Blender が無い（色の決め方はテストする）。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .cloth_layers import mesh_problem
    from .toon_look import bound_modifiers
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cloth_layers import mesh_problem
    from toon_look import bound_modifiers
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"object": None, "armature": None, "axis_bone": None, "lining_color": None, "image": None, "offset": 0.002, "min_dark": 0.10,
            "samples": 30, "view_spread": 0.4, "keep_outside_visible": False, "cover_reach": 0.25, "cover_objects": [], "group": "lining_patch"}
CHANNELS = {"r": 0, "g": 1, "b": 2}
SEED = 5                                                # 色の絵を読む点の乱数（決まった種。台本と同じ）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_patch.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def is_similarity(m, tol=1e-6):
    """3×3 の行列 m が、回転（と鏡）とそろった拡大だけか（列の長さが同じで、互いに直角）。そうなら面の向き（法線）をその行列で
    移しても、逆転置で移したのと同じ向きになる（台本と同じ計算のままにして、3 作目の照合のバイトを保つ）。"""
    m = np.asarray(m, dtype=np.float64)
    lengths = np.linalg.norm(m, axis=0)
    if lengths.max() <= 0 or lengths.max() - lengths.min() > tol * lengths.max():
        return False
    gram = (m.T @ m) / lengths.max() ** 2
    return bool(np.all(np.abs(gram - np.diag(np.diag(gram))) <= tol))


def is_lining(colours, rule):
    """色 (..., 3)（0〜1）が裏地の色か: 成分 channel が min 以上で、ほかの 2 つの成分がその ratio 倍以下（台本の赤の決め方を、成分と値で選べる形にした）。"""
    k = CHANNELS[rule["channel"]]
    main = colours[..., k]
    others = [colours[..., j] for j in range(3) if j != k]
    return (main >= rule["min"]) & (others[0] <= rule["ratio"] * main) & (others[1] <= rule["ratio"] * main)


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
    cloth = bpy.data.objects.get(s["object"])
    if cloth is None or cloth.type != "MESH":
        raise SystemExit(f"布のメッシュがありません: {s['object']}")
    if s["axis_bone"] not in armature.data.bones:
        raise SystemExit(f"{armature.name} に縦の軸の骨 {s['axis_bone']} がありません（axis_bone）")
    if mesh_problem(cloth, shape_keys=True):              # 面と頂点を足す（共有・形状キーは合わなくなる）
        raise SystemExit(mesh_problem(cloth, shape_keys=True))
    bound = bound_modifiers(cloth)                        # 元の頂点の並びに結び付いた変形（焼いた動きを読む・覚えた頂点）は、頂点を足すと働かなくなる
    if bound:
        raise SystemExit(f"{cloth.name} に、元の頂点の並びに結び付いたモディファイアがあります: {bound}。頂点を足すと働かなくなるので、"
                         "それらを足す前（動きを焼く前・結び付ける前）の .blend にかけてください")
    if s["group"] in cloth.vertex_groups:
        raise SystemExit(f"{cloth.name} に頂点グループ {s['group']} がもうあります（cloth.patch_lining を 2 回かけない）")
    missing = [name for name in s["cover_objects"] if bpy.data.objects.get(name) is None or bpy.data.objects[name].type != "MESH"]
    if missing:
        raise SystemExit(f"cover_objects のメッシュがありません: {missing}")
    saved = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    mesh = cloth.data
    mw = cloth.matrix_world
    axis = armature.matrix_world @ armature.data.bones[s["axis_bone"]].head_local

    image = None
    for slot in cloth.material_slots:
        if slot.material and slot.material.use_nodes:
            for node in slot.material.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image and (s["image"] is None or node.image.name == s["image"]):
                    if image is None or node.image.size[0] * node.image.size[1] > image.size[0] * image.size[1]:
                        image = node.image
    if image is None or image.size[0] * image.size[1] == 0:
        raise SystemExit(f"{cloth.name} の材質に、色の絵（画像テクスチャ）がありません" + (f"（image {s['image']}）" if s["image"] else ""))
    iw, ih = image.size
    pixels = np.empty(iw * ih * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    pixels = pixels.reshape(ih, iw, 4)[:, :, :3]

    def texel(uv):
        x = np.clip((uv[..., 0] % 1.0) * iw, 0, iw - 1).astype(int)
        y = np.clip((uv[..., 1] % 1.0) * ih, 0, ih - 1).astype(int)
        return pixels[y, x]

    patch_group = cloth.vertex_groups.new(name=s["group"])
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    uv_layer = bm.loops.layers.uv.active
    if uv_layer is None:
        raise SystemExit(f"{cloth.name} に uv がありません")
    deform = bm.verts.layers.deform.verify()
    world = [mw @ v.co for v in bm.verts]
    tree = BVHTree.FromPolygons(world, [tuple(v.index for v in f.verts) for f in bm.faces])
    other_points, other_faces = [], []                   # 布を外から隠している、揺れない物（体など）
    for o in bpy.data.objects:
        if o.type == "MESH" and o is not cloth and o.name in s["cover_objects"]:
            base = len(other_points)
            other_points += [o.matrix_world @ v.co for v in o.data.vertices]
            other_faces += [tuple(base + i for i in poly.vertices) for poly in o.data.polygons]
    others = BVHTree.FromPolygons(other_points, other_faces) if other_faces else None
    rng = np.random.default_rng(SEED)
    if abs(mw.to_3x3().determinant()) < 1e-12:
        raise SystemExit(f"{cloth.name} の物体の変換に、拡大が 0 の軸があります")
    # 面の向き（法線）を世界へ移す行列: 軸ごとに違う拡大やずれがあれば逆転置（面に直角のまま）。回転とそろった拡大だけなら、
    # 向きは同じなのでその行列のまま（台本と同じ計算）
    similar = is_similarity(np.array(mw.to_3x3()))
    normal_matrix = mw.to_3x3() if similar else mw.to_3x3().inverted().transposed()
    chosen, clean = [], []
    counts = {"innermost": 0, "outward_dark": 0, "inward_dark": 0, "other_material": 0}
    image_slots = {i for i, slot in enumerate(cloth.material_slots)       # 読む絵を使う材質（ほかの材質の面は、色を読まない）
                   if slot.material and slot.material.use_nodes
                   and any(node.type == "TEX_IMAGE" and node.image == image for node in slot.material.node_tree.nodes)}
    z_low, z_high = min(p.z for p in world), max(p.z for p in world)

    def inner_radius(z):
        near = [Vector((p.x - axis.x, p.y - axis.y)).length for p in world if abs(p.z - z) < 0.015]
        return min(near) if near else 0.0

    # 見る位置は、布の内側だけ（布より下からは見ない）
    inside_views = [Vector((axis.x, axis.y, z_low + 0.005 + k * (z_high - z_low - 0.03) / 5)) for k in range(6)]
    for share in (0.08, 0.4, 0.7):
        z = z_low + (z_high - z_low) * share
        r = inner_radius(z) * s["view_spread"]
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            inside_views.append(Vector((axis.x + dx * r, axis.y + dy * r, z)))

    def seen_from_inside(f):
        """面の中の何点かが、見る位置のどれかから、ほかの面に隠れずに見えるか。見えた位置を返す（見えなければ None）。"""
        verts = [mw @ v.co for v in f.verts]
        centre = sum(verts, Vector((0.0, 0.0, 0.0))) / len(verts)
        samples = [centre] + [centre.lerp(v, 0.6) for v in verts]
        for eye in [Vector((axis.x, axis.y, centre.z))] + inside_views:
            for point in samples:
                ray = point - eye
                distance = ray.length
                if distance < 1e-4:
                    continue
                hit = tree.ray_cast(eye, ray / distance)
                if hit[0] is None or hit[3] >= distance - 0.0005 or hit[2] == f.index:
                    return eye
        return None

    for f in bm.faces:
        centre = mw @ f.calc_center_median()
        eye = seen_from_inside(f)
        if eye is None:
            continue
        d = (centre - eye).normalized()                  # 見る位置から面へ向かう向き（面から見て外）
        counts["innermost"] += 1
        if f.material_index not in image_slots:          # ほかの材質の面は、この絵の色で決められない
            counts["other_material"] += 1
            continue
        loops = list(f.loops)
        uvs = np.array([loop[uv_layer].uv[:] for loop in loops])
        points = []
        for k in range(1, len(loops) - 1):              # 扇の形に三角に分けて、中の点を読む
            w = rng.dirichlet((1.0, 1.0, 1.0), size=max(4, s["samples"] // (len(loops) - 2)))
            points.append(w @ uvs[[0, k, k + 1]])
        colours = texel(np.concatenate(points))
        dark = float((~is_lining(colours, s["lining_color"])).mean())
        normal = (normal_matrix @ f.normal).normalized()
        inward = normal.dot(d) < 0
        if dark >= s["min_dark"]:
            chosen.append((f, inward))
            counts["inward_dark" if inward else "outward_dark"] += 1
        elif inward and dark == 0.0:
            clean.append((f.calc_area(), uvs.mean(axis=0)))
    if not clean:
        raise SystemExit("内側から見える面に、裏地の色だけの内向きの面がありません（lining_color を確かめてください）")
    lining_uv = max(clean, key=lambda r: r[0])[1]
    lining_colour = [round(float(v), 3) for v in texel(np.array(lining_uv))]

    to_local = mw.inverted().to_3x3()
    direction = {}
    for f, inward in chosen:
        n = normal_matrix @ f.normal
        if not similar:                                  # 逆転置は面の向きで長さが変わるので、長さ 1 にして面を同じ重みで混ぜる
            n.normalize()                                # （そろった拡大では長さが全部同じなので、台本と同じ計算のまま）
        n = n if inward else -n                          # 内側を向く向き
        for v in f.verts:
            direction[v] = direction.get(v, Vector((0.0, 0.0, 0.0))) + n
    position = {}
    for v, n in direction.items():
        n = n.normalized() if n.length > 1e-9 else Vector((0.0, 0.0, 0.0))
        position[v] = v.co + to_local @ (n * s["offset"])

    def shows_outside(f):
        """当て布の面が、自分の側の外（水平〜斜め上）から、元の布の面にも隠す物にも隠れずに見えるか。"""
        corners = [mw @ position[v] for v in f.verts]
        centre = sum(corners, Vector((0.0, 0.0, 0.0))) / len(corners)
        for point in [centre] + [centre.lerp(c, 0.6) for c in corners] + [centre.lerp(c, 0.97) for c in corners]:
            radial = Vector((point.x - axis.x, point.y - axis.y, 0.0))
            if radial.length < 1e-4:
                continue
            radial.normalize()
            for turn in (-50.0, -25.0, 0.0, 25.0, 50.0):
                c, s_ = math.cos(math.radians(turn)), math.sin(math.radians(turn))
                flat = Vector((radial.x * c - radial.y * s_, radial.x * s_ + radial.y * c, 0.0))
                for rise in (0.0, 25.0, 50.0):
                    ray = (flat * math.cos(math.radians(rise)) + Vector((0.0, 0.0, math.sin(math.radians(rise))))).normalized()
                    start = point + ray * 1e-4
                    if tree.ray_cast(start, ray, s["cover_reach"])[0] is None and (others is None or others.ray_cast(start, ray, s["cover_reach"])[0] is None):
                        return True
        return False

    dropped, kept = 0, []
    for f, inward in chosen:
        if s["keep_outside_visible"] or not shows_outside(f):
            kept.append((f, inward))
        else:
            dropped += 1
    new_vert = {}
    for f, inward in kept:
        for v in f.verts:
            if v not in new_vert:
                nv = bm.verts.new(position[v])
                for group, weight in v[deform].items():
                    if group != patch_group.index:
                        nv[deform][group] = weight
                nv[deform][patch_group.index] = 1.0
                new_vert[v] = nv
    made = 0
    for f, inward in kept:
        order = [new_vert[v] for v in f.verts]
        if not inward:
            order.reverse()                              # 内向きの面にする
        try:
            nf = bm.faces.new(order)
        except ValueError:
            continue
        nf.material_index = f.material_index
        nf.smooth = f.smooth
        for loop in nf.loops:
            loop[uv_layer].uv = lining_uv
        made += 1
    before = (len(mesh.vertices), len(mesh.polygons))
    bm.normal_update()
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    armature.data.pose_position = saved
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report = {"schema_version": "model3d-cloth-patch/0.1", "blender": bpy.app.version_string, "scene": scene.name, "object": cloth.name,
              "image": image.name, "faces_seen_first_from_inside": counts["innermost"], "faces_other_material": counts["other_material"], "patched_back_of_outer_cloth": counts["outward_dark"],
              "patched_dark_lining": counts["inward_dark"], "dropped_because_seen_from_outside": dropped, "faces_added": made,
              "verts_added": len(new_vert), "verts_faces_before": list(before), "verts_faces_after": [len(mesh.vertices), len(mesh.polygons)],
              "lining_uv": [round(float(v), 4) for v in lining_uv], "lining_colour": lining_colour, "settings": s}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "faces_added": made}))


if __name__ == "__main__":
    main()
