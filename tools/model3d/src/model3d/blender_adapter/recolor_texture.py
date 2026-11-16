"""Blender の中で動かす: .blend の中の色の絵（テクスチャ）を塗り替える工程の台本（model.recolor・model.recolor_part）と、部品（物体）を外す
台本（model.remove_parts）。モードは --mode で選ぶ。

    blender --background --factory-startup --python recolor_texture.py -- --mode recolor|part|remove --blend in.blend \
        --settings settings.json --out-blend out.blend --report report.json [--texture out.png]

- recolor（色相の範囲の補正）: rules の 1 つごとに、名前に image を含む絵の、色相の範囲 hue_range_deg（度。範囲の縁は soft 度でなめらかに）
  で彩度が min_saturation（から 0.1 の幅でなめらかに）を超える画素の、色相・彩度・明るさを補正する（hue_shift・sat_scale・value_gamma・
  value_scale）。範囲の外（髪の絵の中の金の飾りなど）は変えない。補正した絵を <元の名前>_recolor として詰め込み、元の絵を使っていた所を
  付け替える。同じ絵を指す規則は、前の規則の結果に順に重ねる（絵は塗り替える前に規則ごとに決める）。
- part（部品の uv の範囲だけの色替え）: 物体 object のメッシュが使う uv の範囲（三角形と、その外の margin 画素）の、明るさ（RGB の最大）が
  dark_below より暗い画素に、色ごとの倍率 gain を掛ける（dark_below から +feather でなめらかに 0 へ。dark_below を省くと範囲の全部）。
  ほかの部品と分け合う色の絵でも、その部品の所だけが変わる。塗り替えた絵を --texture の PNG に書いて詰め込み、付け替える。
- remove（部品を外す）: 物体 objects を消す（使う物の無くなったメッシュも）。骨と揺れものの設定は残す。
どれも元のファイルは変えない（別名で保存する）。計算はリポジトリ外の 2 作目の VRM のダンスの台本と同じ順。
**検証**: 本物の Blender 5.2.4 で、その台本と同じ絵と .blend になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="recolor_texture.py")
    ap.add_argument("--mode", required=True, choices=("recolor", "part", "remove"))
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--texture")
    return ap.parse_args(argv)


# ---- 色相の範囲の補正（台本と同じ式） ----------------------------------------------------------------

def to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maximum, minimum = rgb.max(-1), rgb.min(-1)
    delta = maximum - minimum
    hue = np.zeros_like(maximum)
    mask = delta > 1e-6
    red = mask & (maximum == r)
    green = mask & (maximum == g) & ~red
    blue = mask & ~red & ~green
    hue[red] = ((g - b)[red] / delta[red]) % 6
    hue[green] = (b - r)[green] / delta[green] + 2
    hue[blue] = (r - g)[blue] / delta[blue] + 4
    saturation = np.where(maximum > 1e-6, delta / np.maximum(maximum, 1e-6), 0)
    return hue * 60.0, saturation, maximum


def to_rgb(hue, saturation, value):
    h = (hue % 360.0) / 60.0
    i = np.floor(h).astype(np.int32) % 6
    f = h - np.floor(h)
    p, q, t = value * (1 - saturation), value * (1 - saturation * f), value * (1 - saturation * (1 - f))
    r = np.choose(i, [value, q, p, p, t, value])
    g = np.choose(i, [t, value, value, q, p, p])
    b = np.choose(i, [p, p, t, value, value, q])
    return np.stack([r, g, b], -1)


def recolor_pixels(pixels, rule):
    """(N, 4) の画素（0〜1）に rule の補正をかけた画素と、補正の重み (N,)。"""
    hue, saturation, value = to_hsv(pixels[:, :3])
    low, high = rule["hue_range_deg"]
    soft = rule.get("soft", 6.0)
    signed = ((hue - low + 180.0) % 360.0) - 180.0 + low            # 範囲の下端を基準に、-180〜180 度に直した色相
    weight = np.clip((signed - (low - soft)) / soft, 0, 1) * np.clip(((high + soft) - signed) / soft, 0, 1)
    weight *= np.clip((saturation - rule.get("min_saturation", 0.15)) / 0.1, 0, 1)
    new_hue = hue + rule.get("hue_shift", 0.0)
    new_saturation = np.clip(saturation * rule.get("sat_scale", 1.0), 0, 1)
    new_value = np.clip(np.power(value, rule.get("value_gamma", 1.0)) * rule.get("value_scale", 1.0), 0, 1)
    changed = to_rgb(new_hue, new_saturation, new_value)
    result = pixels.copy()
    result[:, :3] = pixels[:, :3] * (1 - weight[:, None]) + changed * weight[:, None]
    return result, weight


def recolor_all(rules, names, read):
    """規則を順にかける。names は規則ごとの絵の名前、read(名前) は元の画素 (N, 4)。同じ絵の規則は、前の規則の結果に重ねる（1 つずつ
    付け替えると、2 つ目の規則が付け替え済みの元の絵を塗り直して、結果が使われない）。戻り: ({絵の名前: 画素}（初めて出た順）, 規則ごとの報告)。"""
    current, reports = {}, []
    for rule, name in zip(rules, names):
        pixels = current[name] if name in current else read(name)
        result, weight = recolor_pixels(pixels, rule)
        current[name] = result
        selected = weight > 0.5
        reports.append({"image": name, "pixels_changed_share": float(selected.mean()),
                        "median_before": [float(v) for v in np.median(pixels[selected, :3], 0)] if selected.any() else None,
                        "median_after": [float(v) for v in np.median(result[selected, :3], 0)] if selected.any() else None})
    return current, reports


# ---- 部品の uv の範囲 -------------------------------------------------------------------------------

def uv_mask(uv_triangles, width, height, margin):
    """uv の三角形 (T, 3, 2)（画素の座標）が覆う画素と、その外の margin 画素（三角形の大きさで割った重心座標の許し）。台本と同じ。"""
    mask = np.zeros((height, width), dtype=bool)
    reach = int(np.ceil(margin)) + 1
    for t in uv_triangles:
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
        tolerance = margin / max(np.sqrt(abs(denominator)), 1.0)
        inside = (w0 >= -tolerance) & (w1 >= -tolerance) & (w2 >= -tolerance)
        mask[y0:y1 + 1, x0:x1 + 1] |= inside
    return mask


def gain_pixels(texture, mask, gain, dark_below, feather):
    """範囲の中の暗い画素に、色ごとの倍率を掛ける（dark_below から +feather でなめらかに 0 へ。dark_below が None なら範囲の全部）。"""
    if dark_below is None:
        amount = mask.astype(np.float32)
    else:
        value = texture[..., :3].max(axis=2)
        amount = np.clip((dark_below + feather - value) / feather, 0.0, 1.0) * mask
    result = texture.copy()
    result[..., :3] = texture[..., :3] * (1 + (gain - 1) * amount[..., None])
    return result, amount


# ---- Blender の部分 -----------------------------------------------------------------------------------

def image_of(obj, key):
    """物体の材質の画像ノードのうち、名前に key を含む画像（最後に見つかったもの。台本と同じ）。key が None なら、使う画像が 1 つのときだけ。"""
    found = []
    for material in obj.data.materials:
        if material is None or material.node_tree is None:
            continue
        for node in material.node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image and (key is None or key in node.image.name):
                found.append(node.image)
    if key is None and len({image.name for image in found}) > 1:
        raise SystemExit(f"{obj.name} は色の絵を 2 つ以上使っています（image で名前の一部を挙げてください）: "
                         f"{sorted({image.name for image in found})}")
    if not found:
        raise SystemExit(f"{obj.name} の材質に色の絵がありません{'（' + key + '）' if key else ''}")
    return found[-1]


def read_pixels(image):
    width, height = image.size
    pixels = np.empty(width * height * 4, dtype=np.float32)
    image.pixels.foreach_get(pixels)
    return pixels.reshape(height, width, 4)


def replace_image(bpy, image, pixels, name, path=None):
    """塗り替えた画素の新しい絵を作って詰め込み、元の絵を使っていた所を付け替える（台本と同じ）。"""
    height, width = pixels.shape[:2]
    new_image = bpy.data.images.new(name, width, height, alpha=True)
    new_image.colorspace_settings.name = image.colorspace_settings.name
    new_image.pixels.foreach_set(pixels.reshape(-1))
    if path:
        new_image.filepath_raw = path
        new_image.file_format = "PNG"
        new_image.save()
    new_image.pack()
    image.user_remap(new_image)
    return new_image


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from pathlib import Path

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    report = {"schema_version": f"model3d-{'recolor' if a.mode == 'recolor' else 'recolor-part' if a.mode == 'part' else 'remove-parts'}/0.1",
              "blender": bpy.app.version_string, "settings": settings}
    if a.mode == "recolor":
        names = []
        for rule in settings["rules"]:                   # 絵は塗り替える前に決める（付け替えた後の絵を探し直さない）
            image = next((candidate for candidate in bpy.data.images if rule["image"] in candidate.name), None)
            if image is None:
                raise SystemExit(f"絵が見つかりません: {rule['image']}")
            names.append(image.name)
        current, report["rules"] = recolor_all(settings["rules"], names, lambda name: read_pixels(bpy.data.images[name]).reshape(-1, 4))
        for name, pixels in current.items():
            image = bpy.data.images[name]
            width, height = image.size
            replace_image(bpy, image, pixels.reshape(height, width, 4), name + "_recolor")
    elif a.mode == "part":
        obj = bpy.data.objects.get(settings["object"])
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"object のメッシュがありません: {settings['object']}")
        mesh = obj.data
        mesh.calc_loop_triangles()
        image = image_of(obj, settings.get("image"))
        texture = read_pixels(image)
        height, width = texture.shape[:2]
        uv = np.empty(len(mesh.loops) * 2, dtype=np.float32)
        mesh.uv_layers.active.data.foreach_get("uv", uv)
        uv = uv.reshape(-1, 2) * np.array([width, height], dtype=np.float32)
        triangles = np.array([uv[list(triangle.loops)] for triangle in mesh.loop_triangles])
        mask = uv_mask(triangles, width, height, settings.get("margin", 3.0))
        gain = np.array(settings["gain"], dtype=np.float32)
        result, amount = gain_pixels(texture, mask, gain, settings.get("dark_below", 0.45), settings.get("feather", 0.1))
        replace_image(bpy, image, result, Path(a.texture).stem, a.texture)
        report.update(object=obj.name, image=image.name, texture=a.texture, texels_in_part=int(mask.sum()),
                      texels_changed=int((amount > 0.01).sum()), gain=[float(v) for v in gain])
    else:
        removed, missing = [], []
        for name in settings["objects"]:
            obj = bpy.data.objects.get(name)
            if obj is None:
                missing.append(name)
                continue
            mesh = obj.data if obj.type == "MESH" else None
            removed.append({"name": name, "type": obj.type, "verts": len(mesh.vertices) if mesh else 0})
            bpy.data.objects.remove(obj, do_unlink=True)
            if mesh is not None and mesh.users == 0:
                bpy.data.meshes.remove(mesh)
        report.update(removed=removed, missing=missing,
                      motion_files_still_used=sorted({m.filepath for obj in bpy.data.objects for m in obj.modifiers if m.type == "MESH_CACHE"}),
                      meshes_left=sorted(obj.name for obj in bpy.data.objects if obj.type == "MESH" and not obj.hide_render))
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"mode": a.mode, "saved": a.out_blend}))


if __name__ == "__main__":
    main()
