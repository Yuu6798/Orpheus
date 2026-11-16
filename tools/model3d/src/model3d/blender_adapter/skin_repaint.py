"""Blender の中で動かす: 肌の島の色の絵を、頂点の色をならして塗り直す（model.smooth_skin・model.clean_skin）。モードは --mode。

    blender --background --factory-startup --python skin_repaint.py -- --mode smooth|clean --blend in.blend --settings settings.json \
        --out-blend out.blend --texture out.png --report report.json

画像から作ったモデルの癖（リポジトリ外の 2 作目の VRM のダンスで分かったこと）:
  - smooth: 脇から腰の肌に、形の溝と、色の絵に焼き込まれた明暗の帯の両方があった。肌の島（つながり）の、高さ・横の距離・横を向いた面の
    なだらかな重みの範囲だけ、形は縮まないならし方（Taubin。縮める → 戻す）で、色は頂点の色を隣どうしでならして塗り直す。
    色だけで肌を選ぶと、色の近い服まで崩す。ただの平均でならすと肌が縮んで服との間にすき間が出る。
  - clean: 部品（袖など）を外すと、その下の肌に部品の色がにじんだしみが出る。手を含む島の、選んだ骨（上腕・前腕など）の頂点のうち、
    肌色でないもの（しみ）を、まわりのきれいな肌の色で埋め（外側から順に広げる）、軽くならして塗り直す。手は触らない。
塗り直しは、3 頂点とも対象の三角形を、色の絵の上で頂点の色の重心補間で塗る（三角形の外へ paint_margin_px 画素塗り足す。先に全部の
塗り足しを塗り、あとで内側を塗る。逆にすると、隣の三角形の内側を塗り足しが上書きして、辺に沿った細い線が出る）。
塗り直した絵は --texture の PNG に書いて詰め込み、元の絵を使っていた所を付け替える。元のファイルは変えない。
計算は台本と同じ順（頂点の色の読み方・HSV は colorsys・島は union-find）。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスの台本と同じ形と絵になることを確かめた（docs/model3d.md）。
"""
import argparse
import colorsys
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import deform_names, islands, mesh_edges, ramp, top_groups, write_points
    from .recolor_texture import effective_materials, replace_image, slots_using
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import deform_names, islands, mesh_edges, ramp, top_groups, write_points
    from recolor_texture import effective_materials, replace_image, slots_using

SMOOTH = {"z_range": None, "z_soft": 0.03, "x_min": None, "x_max": None, "x_soft": 0.03, "facing": None,
          "seed_z": None, "shape": None, "color_iterations": 60, "paint_margin_px": 4.0}
SHAPE = {"iterations": 10, "factor": 0.5, "inflate": -0.53, "z_max": None, "z_soft": 0.02}
CLEAN = {"armature": None, "island_groups": None, "island_min": 50, "region_groups": None, "fill_iterations": 300, "smooth_iterations": 25,
         "smooth_factor": 0.5, "paint_margin_px": 4.0}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="skin_repaint.py")
    ap.add_argument("--mode", required=True, choices=("smooth", "clean"))
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--texture", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def sample(texture, uvs):
    """色の絵 (H, W, 4) を uv (N, 2) で双線形に引いた RGB（台本と同じ。端は 1.001 画素手前で切る）。"""
    height, width = texture.shape[:2]
    x = np.clip(uvs[:, 0] * width - 0.5, 0, width - 1.001)
    y = np.clip(uvs[:, 1] * height - 0.5, 0, height - 1.001)
    ix, iy = x.astype(int), y.astype(int)
    tx, ty = (x - ix)[:, None], (y - iy)[:, None]
    return (texture[iy, ix, :3] * (1 - tx) * (1 - ty) + texture[iy, ix + 1, :3] * tx * (1 - ty)
            + texture[iy + 1, ix, :3] * (1 - tx) * ty + texture[iy + 1, ix + 1, :3] * tx * ty)


def vertex_colors(texture, uv, loop_vertex, count):
    """頂点の色（その頂点を使うすべての角の色の平均）(count, 3)。"""
    color = np.zeros((count, 3))
    hits = np.zeros(count)
    np.add.at(color, loop_vertex, sample(texture, uv))
    np.add.at(hits, loop_vertex, 1)
    return color / np.maximum(hits, 1)[:, None]


def skin_colored(colors, skin):
    """頂点の色が肌色か (count,)。skin は {"hue_deg": [下, 上], "saturation": [下, 上], "value_min": 下}。HSV は colorsys（台本と同じ）。"""
    hsv = np.array([colorsys.rgb_to_hsv(*color) for color in colors])
    hue = hsv[:, 0] * 360
    return ((hue >= skin["hue_deg"][0]) & (hue <= skin["hue_deg"][1])
            & (hsv[:, 1] >= skin["saturation"][0]) & (hsv[:, 1] <= skin["saturation"][1]) & (hsv[:, 2] >= skin["value_min"]))


def neighbour_mean(values, edges, count, usable=None):
    """辺でつながった隣の値の平均と、隣があるか。usable を渡すと、usable な隣だけ（台本の 2 つの書き方と同じ足し合わせの順）。"""
    total = np.zeros_like(values)
    number = np.zeros(count)
    if usable is None:
        np.add.at(total, edges[:, 0], values[edges[:, 1]])
        np.add.at(total, edges[:, 1], values[edges[:, 0]])
        np.add.at(number, edges[:, 0], 1)
        np.add.at(number, edges[:, 1], 1)
    else:
        for first, second in ((0, 1), (1, 0)):
            ok = usable[edges[:, second]]
            np.add.at(total, edges[ok, first], values[edges[ok, second]])
            np.add.at(number, edges[ok, first], 1)
    return total / np.maximum(number, 1)[:, None], number > 0


def paint_jobs(triangles, colors, weights, width, height, margin_px):
    """三角形 [(頂点 3 つ, uv の角 (3, 2)), …] を塗る仕事（塗り足しか内側か・行・列・色・混ぜる重み）の並び（台本と同じ）。"""
    jobs = []
    reach = int(np.ceil(margin_px)) + 1
    for vertices, corners in triangles:
        t = corners * np.array([width, height], dtype=np.float32)
        x0, x1 = max(int(np.floor(t[:, 0].min())) - reach, 0), min(int(np.ceil(t[:, 0].max())) + reach, width - 1)
        y0, y1 = max(int(np.floor(t[:, 1].min())) - reach, 0), min(int(np.ceil(t[:, 1].max())) + reach, height - 1)
        if x1 < x0 or y1 < y0:
            continue
        grid_x, grid_y = np.meshgrid(np.arange(x0, x1 + 1) + 0.5, np.arange(y0, y1 + 1) + 0.5)
        a, b, c = t
        denominator = (b[1] - c[1]) * (a[0] - c[0]) + (c[0] - b[0]) * (a[1] - c[1])
        if abs(denominator) < 1e-9:
            continue
        w0 = ((b[1] - c[1]) * (grid_x - c[0]) + (c[0] - b[0]) * (grid_y - c[1])) / denominator
        w1 = ((c[1] - a[1]) * (grid_x - c[0]) + (a[0] - c[0]) * (grid_y - c[1])) / denominator
        w2 = 1.0 - w0 - w1
        tolerance = margin_px / max(np.sqrt(abs(denominator)), 1.0)
        near = (w0 >= -tolerance) & (w1 >= -tolerance) & (w2 >= -tolerance)
        inner = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        for mask, is_inner in ((near & ~inner, False), (inner, True)):
            if not mask.any():
                continue
            b0, b1, b2 = np.clip(w0[mask], 0, 1), np.clip(w1[mask], 0, 1), np.clip(w2[mask], 0, 1)
            total = b0 + b1 + b2
            b0, b1, b2 = b0 / total, b1 / total, b2 / total
            color = b0[:, None] * colors[vertices[0]] + b1[:, None] * colors[vertices[1]] + b2[:, None] * colors[vertices[2]]
            blend = (b0 * weights[vertices[0]] + b1 * weights[vertices[1]] + b2 * weights[vertices[2]])[:, None]
            jobs.append((is_inner, grid_y[mask].astype(int), grid_x[mask].astype(int), color, blend))
    return jobs


def paint(texture, jobs):
    """先に塗り足し、あとで内側を塗った色の絵と、内側を塗った画素の数。"""
    result = texture.copy()
    painted = 0
    for wanted in (False, True):
        for is_inner, rows, cols, color, blend in jobs:
            if is_inner != wanted:
                continue
            result[rows, cols, :3] = texture[rows, cols, :3] * (1 - blend) + color * blend
            painted += len(rows) if is_inner else 0
    return result, painted


def smooth_weights(positions, normals, skin, settings):
    """smooth の範囲の重み (count,)（高さ・横の距離・横を向いた面のなだらかな段の積。肌でない頂点は 0）。"""
    z, x = positions[:, 2], np.abs(positions[:, 0])
    z_low, z_high = settings["z_range"]
    soft = settings["z_soft"]
    weight = (ramp(z, z_low - soft, z_low + soft) * (1 - ramp(z, z_high - soft, z_high + soft))
              * ramp(np.abs(normals[:, 0]), *settings["facing"])
              * ramp(x, settings["x_min"], settings["x_min"] + settings["x_soft"])
              * (1 - ramp(x, settings["x_max"] - settings["x_soft"], settings["x_max"])))
    return np.where(skin, weight, 0.0)


def taubin(positions, edges, count, weight, shape):
    """縮めて戻すならし（Taubin）。weight の頂点だけ動かす。"""
    smoothed = positions.copy()
    for _ in range(shape["iterations"]):
        for factor in (shape["factor"], shape["inflate"]):
            mean, has = neighbour_mean(smoothed, edges, count)
            step = factor * weight * has
            smoothed += (mean - smoothed) * step[:, None]
    return smoothed


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from pathlib import Path

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    defaults = SMOOTH if a.mode == "smooth" else CLEAN
    settings = {**defaults, **settings}
    if a.mode == "smooth" and settings.get("shape") is not None:
        settings["shape"] = {**SHAPE, **settings["shape"]}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    body = bpy.data.objects.get(settings["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"body のメッシュがありません: {settings['body']}")
    mesh = body.data
    mesh.calc_loop_triangles()
    count = len(mesh.vertices)
    found = {}                                           # 名前の一部に合う絵（2 つ以上なら止める）
    for material in effective_materials(body):          # 物体が実際に描く材質（枠が物体の側の上書きならその材質）
        if material is None or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and settings["image"] in node.image.name:
                found[node.image.name] = node.image
    if not found:
        raise SystemExit(f"{body.name} の材質に色の絵がありません（{settings['image']}）")
    if len(found) > 1:
        raise SystemExit(f"{body.name} の材質に、image（{settings['image']}）に合う色の絵が 2 つ以上あります。名前をはっきり書いてください: "
                         f"{sorted(found)}")
    (image,) = found.values()
    width, height = image.size
    texture = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(texture)
    texture = texture.reshape(height, width, 4)
    slots = slots_using(effective_materials(body), image)   # その絵を使う材質（全部）
    world = np.array(body.matrix_world)
    local = np.empty(count * 3)
    mesh.vertices.foreach_get("co", local)
    positions = local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3]
    uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    mesh.uv_layers.active.data.foreach_get("uv", uv)
    uv = uv.reshape(-1, 2)
    loop_vertex = np.empty(len(mesh.loops), dtype=np.int64)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    color = vertex_colors(texture, uv, loop_vertex, count)
    edges = mesh_edges(mesh)
    roots = islands(count, edges)
    report = {"schema_version": f"model3d-{a.mode}-skin/0.1", "blender": bpy.app.version_string, "body": body.name, "image": image.name,
              "texture": a.texture}
    if a.mode == "smooth":
        normals = np.empty(count * 3)
        mesh.vertices.foreach_get("normal", normals)
        normals = normals.reshape(-1, 3) @ world[:3, :3].T
        normals /= np.linalg.norm(normals, axis=1, keepdims=True) + 1e-12
        uses_image = np.zeros(count, dtype=bool)
        for polygon in mesh.polygons:
            if polygon.material_index in slots:
                uses_image[list(polygon.vertices)] = True
        skin = skin_colored(color, settings["skin"]) & uses_image
        seed_z = settings["seed_z"]
        seed = (skin & (positions[:, 2] >= seed_z[0]) & (positions[:, 2] <= seed_z[1])
                & (np.abs(positions[:, 0]) >= settings["x_min"]) & (np.abs(normals[:, 0]) >= settings["facing"][1]))
        seed_roots, seed_counts = np.unique(roots[seed], return_counts=True)
        if len(seed_roots) == 0:
            raise SystemExit("肌の島が見つかりません（seed_z の範囲に、横を向いた肌色の頂点がありません）")
        island = roots == seed_roots[np.argmax(seed_counts)]
        skin &= island
        weight = smooth_weights(positions, normals, skin, settings)
        skin_edges = edges[skin[edges[:, 0]] & skin[edges[:, 1]]]
        shift = np.zeros(count)
        if settings["shape"] is not None:
            shape = settings["shape"]
            z_max = shape["z_max"] if shape["z_max"] is not None else settings["z_range"][1] + settings["z_soft"]
            shape_weight = weight * (1 - ramp(positions[:, 2], z_max - shape["z_soft"], z_max))
            smoothed = taubin(positions, skin_edges, count, shape_weight, shape)
            shift = np.linalg.norm(smoothed - positions, axis=1)
            write_points(body, smoothed)
        smoothed_color = color.copy()
        for _ in range(settings["color_iterations"]):
            mean, has = neighbour_mean(smoothed_color, skin_edges, count)
            step = weight * has
            smoothed_color += (mean - smoothed_color) * step[:, None]
        triangles = [(list(t.vertices), uv[list(t.loops)]) for t in mesh.loop_triangles
                     if t.material_index in slots and skin[list(t.vertices)].all() and weight[list(t.vertices)].max() > 0.01]
        result, painted = paint(texture, paint_jobs(triangles, smoothed_color, weight, width, height, settings["paint_margin_px"]))
        selected = weight > 0.01
        report.update(skin_island_verts=int(island.sum()), skin_verts=int(skin.sum()), selected_verts=int(selected.sum()),
                      left_right=[int((selected & (positions[:, 0] > 0)).sum()), int((selected & (positions[:, 0] < 0)).sum())],
                      shape_moved_mm={"mean": float(shift[selected].mean() * 1000) if selected.any() else 0.0, "max": float(shift.max() * 1000)})
    else:
        armatures = [obj for obj in bpy.data.objects
                     if obj.type == "ARMATURE" and (not settings.get("armature") or obj.name == settings["armature"])]
        if settings.get("armature") and not armatures:
            raise SystemExit(f"アーマチュアがありません: {settings['armature']}")
        deform = deform_names(armatures[0]) if armatures else None     # いちばん重い骨は、変形に使う骨のグループの中で
        top = top_groups(body, deform)
        is_arm = np.array([name.startswith(tuple(settings["region_groups"])) for name in top])
        is_hand = np.array([name.startswith(tuple(settings["island_groups"])) for name in top])
        arm_roots = [root for root in np.unique(roots[is_hand]) if (is_hand & (roots == root)).sum() >= settings["island_min"]]
        in_island = np.isin(roots, arm_roots)
        region = in_island & is_arm
        island_edges = edges[in_island[edges[:, 0]] & in_island[edges[:, 1]]]
        clean = skin_colored(color, settings["skin"])
        dirty = region & ~clean
        filled = color.copy()
        known = ~dirty
        for _ in range(settings["fill_iterations"]):
            if known[region].all():
                break
            mean, has = neighbour_mean(filled, island_edges, count, known)
            fill = ~known & has
            filled[fill] = mean[fill]
            known = known | fill
        remaining = int((region & ~known).sum())
        everything = np.ones(count, dtype=bool)
        for _ in range(settings["smooth_iterations"]):
            mean, has = neighbour_mean(filled, island_edges, count, everything)
            move = region & has
            filled[move] += (mean[move] - filled[move]) * settings["smooth_factor"]
        weight = region.astype(float)
        triangles = [(list(t.vertices), uv[list(t.loops)]) for t in mesh.loop_triangles
                     if t.material_index in slots and in_island[list(t.vertices)].all() and weight[list(t.vertices)].max() > 0]
        result, painted = paint(texture, paint_jobs(triangles, filled, weight, width, height, settings["paint_margin_px"]))
        reference = np.median(color[region & clean], axis=0) if (region & clean).any() else np.zeros(3)
        report.update(islands=len(arm_roots), region_verts=int(region.sum()), dirty_verts=int(dirty.sum()), not_filled=remaining,
                      clean_skin_rgb=[float(v) for v in reference])
    replace_image(bpy, image, result, Path(a.texture).stem, a.texture)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report.update(texels_painted=painted, settings=settings)
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"mode": a.mode, "painted": painted}))


if __name__ == "__main__":
    main()
