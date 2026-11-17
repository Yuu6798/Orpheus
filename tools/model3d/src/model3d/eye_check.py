"""頭の候補の目の作りを、数と絵で調べる（check.eyes の中身。Blender の外。numpy と Pillow）。合否は決めない。

docs/model3d.md の「頭の候補の目の作りを調べる」。リポジトリ外の 3 作目の VRM のダンスのあとの試し（目の作り直し）で、頭の目の作りを
調べた台本（部品の一覧・扇・絵の中の場所・正面の絵・断面）を、決め打ちの箱と島の番号を外して移した。頭は −Y を向き、長さは m
（model.export_mesh の npz）。

- 部品: 辺でつながった頂点の島（大きい順に番号。0 がいちばん大きい島＝肌）。窓（window）に中心がある島を、頂点と三角形の数・中心・範囲・
  色（色の絵があれば、三角形の uv の重心の色の平均）・絵の中の場所（uv の範囲と画素の大きさ）で並べる。
- 扇: 目ごとに（右目 x < 0、左目 x > 0）、肌の頂点のうち探す箱（eyes.find）の中で辺がいちばん多い頂点を中心にして、それを使う三角形
  （白目の扇かもしれない所）。中心・三角形の数・輪の頂点の数と範囲・絵の中の場所。扇が無い頭（白目が別の部品など）では、辺の数が少ない。
- 絵: 正面（色の絵か部品の色）、部品の色分け（番号つき）、断面（縦と横。肌は緑、扇は白、ほかの部品は部品の色）、上から見た断面（目の高さ。
  目の骨を渡せば軸を描く）。
"""
import numpy as np

from .face_shapes import Head

SCHEMA = "model3d-eye-check/0.1"
DEFAULTS = {"resolution": 0.0001, "section_pixel": 0.00005, "top_pixel": 0.00025, "margin": 0.005}
SIDES = (("R", -1.0), ("L", 1.0))
SKIN, FAN, BACKGROUND = (0, 220, 0), (255, 255, 255), (20, 20, 24)


def palette(count):
    """部品の色（番号ごと。0 の肌は灰色。試作の台本と同じ乱数の並び）。"""
    rng = np.random.default_rng(3)
    colors = rng.integers(60, 255, size=(count + 1, 3)).astype(np.uint8)
    colors[0] = (70, 70, 70)
    return colors


def texture_at(image, u, v):
    """uv の色（最も近い画素。試作と同じ読み方: x = u × 幅、y = (1 − v) × 高さ を切り捨て）。"""
    height, width = image.shape[:2]
    x = np.clip((np.asarray(u) * width).astype(int), 0, width - 1)
    y = np.clip(((1 - np.asarray(v)) * height).astype(int), 0, height - 1)
    return image[y, x]


def front_raster(co, tris, window, scale, front_y, colors=None, tri_uv=None, image=None):
    """正面（−y の側）から見た、窓の中のいちばん手前の三角形の番号（行は上が z1）と、色（三角形の色 colors か、色の絵の uv の色）。
    試作の台本と同じ描き方（三角形の番号の順、重心の座標が 0 以上の画素、同じ奥行きなら先の三角形）。"""
    (x0, x1), (z0, z1) = window["x"], window["z"]
    width, height = int((x1 - x0) * scale), int((z1 - z0) * scale)
    depth = np.full((height, width), 9.0)
    ids = np.full((height, width), -1, dtype=int)
    color = np.zeros((height, width, 3), dtype=np.uint8)
    p3 = co[tris]
    sel = np.nonzero((p3[:, :, 0].max(axis=1) > x0) & (p3[:, :, 0].min(axis=1) < x1) & (p3[:, :, 2].max(axis=1) > z0)
                     & (p3[:, :, 2].min(axis=1) < z1) & (p3[:, :, 1].min(axis=1) < front_y))[0]
    for t in sel:
        p = p3[t]
        px, pz = (p[:, 0] - x0) * scale, (z1 - p[:, 2]) * scale
        c0, c1 = max(int(px.min()), 0), min(int(px.max()) + 1, width - 1)
        r0, r1 = max(int(pz.min()), 0), min(int(pz.max()) + 1, height - 1)
        if c1 < c0 or r1 < r0:
            continue
        gx, gz = np.meshgrid(np.arange(c0, c1 + 1) + 0.5, np.arange(r0, r1 + 1) + 0.5)
        den = (pz[1] - pz[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (pz[0] - pz[2])
        if abs(den) < 1e-9:
            continue
        w0 = ((pz[1] - pz[2]) * (gx - px[2]) + (px[2] - px[1]) * (gz - pz[2])) / den
        w1 = ((pz[2] - pz[0]) * (gx - px[2]) + (px[0] - px[2]) * (gz - pz[2])) / den
        w2 = 1 - w0 - w1
        inside = (w0 >= 0) & (w1 >= 0) & (w2 >= 0)
        if not inside.any():
            continue
        y = w0 * p[0, 1] + w1 * p[1, 1] + w2 * p[2, 1]
        block = depth[r0:r1 + 1, c0:c1 + 1]
        win = inside & (y < block)
        if not win.any():
            continue
        block[win] = y[win]
        ids[r0:r1 + 1, c0:c1 + 1][win] = t
        if image is not None:
            u = w0 * tri_uv[t, 0, 0] + w1 * tri_uv[t, 1, 0] + w2 * tri_uv[t, 2, 0]
            v = w0 * tri_uv[t, 0, 1] + w1 * tri_uv[t, 1, 1] + w2 * tri_uv[t, 2, 1]
            color[r0:r1 + 1, c0:c1 + 1][win] = texture_at(image, u, v)[win]
        elif colors is not None:
            color[r0:r1 + 1, c0:c1 + 1][win] = colors[t]
    return ids, color


def cut(co, tris, axis, value):
    """三角形を axis = value の面で切った線分（両端の 3D の点）と、その三角形の番号。"""
    p = co[tris]
    s = p[:, :, axis] - value
    segments, owners = [], []
    for t in np.where((s.min(axis=1) < 0) & (s.max(axis=1) > 0))[0]:
        points = []
        for i in range(3):
            a, b = s[t, i], s[t, (i + 1) % 3]
            if (a < 0) != (b < 0):
                points.append(p[t, i] + a / (a - b) * (p[t, (i + 1) % 3] - p[t, i]))
        if len(points) == 2:
            segments.append(points)
            owners.append(int(t))
    return segments, owners


def survey(co, edges, tris, settings, uv=None, tri_loops=None, image=None):
    """報告（dict）と、絵を描くための材料を返す。"""
    head = Head(co, edges, tris)
    co, tris = head.co, head.tris
    tri_uv = uv[tri_loops] if uv is not None and tri_loops is not None else None
    tri_color = None
    if image is not None and tri_uv is not None:
        tri_color = texture_at(image, tri_uv[:, :, 0].mean(axis=1), tri_uv[:, :, 1].mean(axis=1)).astype(float)
    window = settings["window"]
    (x0, x1), (z0, z1), y_max = window["x"], window["z"], window["y"]
    parts = []
    for k in range(head.islands):
        mask = head.island_of == k
        c = co[mask]
        centre = c.mean(axis=0)
        if not (x0 < centre[0] < x1 and z0 < centre[2] < z1 and centre[1] < y_max) and k != 0:
            continue
        row = {"island": int(k), "vertices": int(mask.sum()), "triangles": int((head.tri_island == k).sum()),
               "centre": [round(float(v), 4) for v in centre], "min": [round(float(v), 4) for v in c.min(axis=0)],
               "max": [round(float(v), 4) for v in c.max(axis=0)]}
        if tri_color is not None:
            colors = tri_color[head.tri_island == k]
            row["colour"] = [int(v) for v in colors.mean(axis=0)] if len(colors) else None
        if tri_uv is not None and k != 0:
            kuv = tri_uv[head.tri_island == k].reshape(-1, 2)
            if len(kuv):
                (u0, v0), (u1, v1) = kuv.min(axis=0), kuv.max(axis=0)
                row["uv_min"], row["uv_max"] = [round(float(u0), 4), round(float(v0), 4)], [round(float(u1), 4), round(float(v1), 4)]
                if image is not None:
                    row["px"] = [int((u1 - u0) * image.shape[1]), int((v1 - v0) * image.shape[0])]
        parts.append(row)
    find = settings["eyes"]["find"]
    eyes, fan_tri = {}, np.zeros(len(tris), dtype=bool)
    for side, sign in SIDES:
        box = (head.island_of == 0) & (co[:, 0] * sign > find["x"][0]) & (co[:, 0] * sign < find["x"][1]) \
            & (co[:, 2] > find["z"][0]) & (co[:, 2] < find["z"][1]) & (co[:, 1] < find["front_y"])
        idx = np.where(box)[0]
        if not len(idx):
            eyes[side] = {"found": False, "reason": "探す箱（eyes.find）に肌の頂点がありません"}
            continue
        centre = int(idx[np.argmax(head.valence[idx])])                  # 扇の中心かもしれない頂点（辺がいちばん多い頂点）
        fan = np.any(tris == centre, axis=1)
        fan_tri |= fan
        ring = np.unique(tris[fan])
        ring = ring[ring != centre]
        entry = {"found": True, "fan_centre": centre, "valence": int(head.valence[centre]), "centre_co": [round(float(v), 4) for v in co[centre]],
                 "fan_tris": int(fan.sum()), "ring": int(len(ring)),
                 "ring_x": [round(float(co[ring, 0].min()), 4), round(float(co[ring, 0].max()), 4)],
                 "ring_y": [round(float(co[ring, 1].min()), 4), round(float(co[ring, 1].max()), 4)],
                 "ring_z": [round(float(co[ring, 2].min()), 4), round(float(co[ring, 2].max()), 4)],
                 "cone_depth_mm": round(float((co[centre, 1] - co[ring, 1].mean()) * 1000), 2)}
        if tri_uv is not None:
            fuv = tri_uv[fan].reshape(-1, 2)
            entry["fan_uv_min"] = [round(float(v), 4) for v in fuv.min(axis=0)]
            entry["fan_uv_max"] = [round(float(v), 4) for v in fuv.max(axis=0)]
        # 扇の輪の範囲（正面から見て）に中心がある肌でない部品（瞳・光・まつ毛などの候補）
        entry["parts_in_ring"] = [row["island"] for row in parts
                                  if row["island"] != 0 and co[ring, 0].min() < row["centre"][0] < co[ring, 0].max()
                                  and co[ring, 2].min() < row["centre"][2] < co[ring, 2].max()]
        eyes[side] = entry
    report = {"schema_version": SCHEMA, "vertices": int(head.n), "triangles": int(len(tris)), "islands": int(head.islands),
              "parts": parts, "eyes": eyes}
    if image is not None:
        report["texture"] = {"size": [int(image.shape[1]), int(image.shape[0])]}
    return report, {"head": head, "tri_uv": tri_uv, "fan_tri": fan_tri}


def class_colors(head, fan_tri):
    """三角形ごとの線の色: 扇は白、肌は緑、ほかの部品は部品の色。"""
    colors = palette(head.islands)[head.tri_island].astype(int)
    colors[head.tri_island == 0] = SKIN
    colors[fan_tri] = FAN
    return colors


def section_sheet(material, settings, report, font):
    """断面の絵（1 枚）。上の段は縦の断面（x を決めて、横軸 y（左が手前）・縦軸 z）を横に並べ、その下に横の断面（z を決めて、横軸 x・
    縦軸 y（下が手前））を、高さごとに右目と左目を並べる。切る位置は settings の sections（無ければ目ごとの扇の輪の真ん中）。"""
    from PIL import Image, ImageDraw
    head, fan_tri = material["head"], material["fan_tri"]
    co, tris = head.co, head.tris
    colors = class_colors(head, fan_tri)
    scale = 1.0 / settings["section_pixel"]
    sections, margin = settings.get("sections") or {}, settings["margin"]
    rings = {side: e for side, e in report["eyes"].items() if e.get("found")}
    if not rings:
        return None
    xs = sections.get("x") or [round(float(np.mean(e["ring_x"])), 4) for e in rings.values()]
    zs = sections.get("z") or [round(float(np.mean(next(iter(rings.values()))["ring_z"])), 4)]
    y0, y1 = sections.get("y") or [min(e["ring_y"][0] for e in rings.values()) - margin, max(e["ring_y"][1] for e in rings.values()) + margin]
    z0, z1 = min(e["ring_z"][0] for e in rings.values()) - margin, max(e["ring_z"][1] for e in rings.values()) + margin

    def panel(width, height):
        image = Image.new("RGB", (max(1, int(width * scale)), max(1, int(height * scale))), BACKGROUND)
        return image, ImageDraw.Draw(image)

    rows = [[]]
    for x in xs:
        image, draw = panel(y1 - y0, z1 - z0)
        segments, owners = cut(co, tris, 0, x)
        for (a, b), t in zip(segments, owners):
            draw.line([((a[1] - y0) * scale, (z1 - a[2]) * scale), ((b[1] - y0) * scale, (z1 - b[2]) * scale)], fill=tuple(colors[t]), width=2)
        draw.text((6, image.height - 22), "x = %.1f mm (left is the front)" % (x * 1000), fill=(255, 255, 0), font=font)
        rows[0].append(image)
    for z in zs:
        row = []
        segments, owners = cut(co, tris, 2, z)
        for side, entry in rings.items():
            lo, hi = entry["ring_x"][0] - margin, entry["ring_x"][1] + margin
            image, draw = panel(hi - lo, y1 - y0)
            for (a, b), t in zip(segments, owners):
                if lo <= a[0] <= hi or lo <= b[0] <= hi:
                    draw.line([((a[0] - lo) * scale, (y1 - a[1]) * scale), ((b[0] - lo) * scale, (y1 - b[1]) * scale)],
                              fill=tuple(colors[t]), width=2)
            draw.text((6, 4), "%s eye, z = %.1f mm (bottom is the front)" % (side, z * 1000), fill=(255, 255, 0), font=font)
            row.append(image)
        rows.append(row)
    gap = 10
    width = max(sum(p.width for p in row) + gap * (len(row) - 1) for row in rows)
    height = sum(max(p.height for p in row) for row in rows) + gap * (len(rows) - 1)
    sheet = Image.new("RGB", (width, height), (60, 60, 60))
    y = 0
    for row in rows:
        x = 0
        for p in row:
            sheet.paste(p, (x, y))
            x += p.width + gap
        y += max(p.height for p in row) + gap
    return sheet


def top_image(material, settings, report, font):
    """上から見た断面（目の高さ。下が顔の前）。肌は緑、扇は白、ほかの部品は部品の色。目の骨（bones）を渡せば軸を描く。"""
    from PIL import Image, ImageDraw
    head, fan_tri = material["head"], material["fan_tri"]
    co, tris = head.co, head.tris
    colors = class_colors(head, fan_tri)
    rings = [e for e in report["eyes"].values() if e.get("found")]
    top = settings.get("top") or {}
    z = top.get("z", float(np.mean([e["centre_co"][2] for e in rings])) if rings else float(co[:, 2].mean()))
    scale, margin = 1.0 / settings["top_pixel"], 0.01
    x0, x1 = top.get("x", [float(co[:, 0].min()) - margin, float(co[:, 0].max()) + margin])
    y0, y1 = top.get("y", [float(co[:, 1].min()) - margin, float(co[:, 1].max()) + margin])
    image = Image.new("RGB", (int((x1 - x0) * scale), int((y1 - y0) * scale)), BACKGROUND)
    draw = ImageDraw.Draw(image)

    def at(p):                                      # 上から見るので、キャラの左（+x）は絵の左。前（−y）は下
        return ((x1 - p[0]) * scale, (y1 - p[1]) * scale)

    segments, owners = cut(co, tris, 2, z)
    for (a, b), t in zip(segments, owners):
        draw.line([at(a), at(b)], fill=tuple(colors[t]), width=2)
    for bone in settings.get("bones") or []:
        h, tl = np.asarray(bone["head"], float), np.asarray(bone["tail"], float)
        draw.line([at(h), at(tl)], fill=(80, 220, 255), width=2)
        x, y = at(h)
        draw.ellipse([x - 4, y - 4, x + 4, y + 4], fill=(80, 220, 255))
        draw.text((x + 6, y - 16), bone.get("name", ""), fill=(80, 220, 255), font=font)
    draw.text((6, 4), "top section at z = %.1f mm (bottom is the front)" % (z * 1000), fill=(255, 255, 0), font=font)
    return image


def images(material, settings, report, image=None):
    """絵 {front, parts, sections, top}（PIL の画像）。"""
    from PIL import Image, ImageDraw, ImageFont
    font = ImageFont.load_default(size=14)
    head, tri_uv = material["head"], material["tri_uv"]
    scale = 1.0 / settings["resolution"]
    window = settings["window"]
    out = {}
    ids, color = front_raster(head.co, head.tris, window, scale, settings["front_y"], tri_uv=tri_uv, image=image)
    out["front"] = Image.fromarray(color)
    owner_island = np.where(ids >= 0, head.tri_island[np.clip(ids, 0, None)], -1)
    colors = palette(head.islands)
    parts = Image.fromarray(np.where(owner_island[..., None] >= 0, colors[np.clip(owner_island, 0, None)], 0).astype(np.uint8))
    draw = ImageDraw.Draw(parts)
    for row in report["parts"]:
        if row["island"] == 0:
            continue
        x, z = (row["centre"][0] - window["x"][0]) * scale, (window["z"][1] - row["centre"][2]) * scale
        draw.text((x, z), str(row["island"]), fill=(255, 255, 255), font=font)
    out["parts"] = parts
    sheet = section_sheet(material, settings, report, font)
    if sheet is not None:
        out["sections"] = sheet
    out["top"] = top_image(material, settings, report, font)
    return out
