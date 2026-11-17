"""口のまわりを調べて塗る（Blender の外。numpy だけ）。texture.mouth_inside の中身。

docs/model3d.md の「表情を形で作る」。リポジトリ外の 3 作目の VRM のダンスの台本と同じ計算の順（同じ頭と絵と設定から、同じ絵になる）。

なぜ（3 作目で分かったこと）: 画像から作った頭は、口の中の面（唇の裏の袋と、中の歯や舌の部品）が使う色の絵の所にも肌の色が入っていることがある
（外から見えない所に、まわりの色がそのまま回っている）。口を開ける形を作ると、肌色のかたまりが見える。ただし、肌色の板が見えたら色の絵のせいとは
限らない（伸びた面かもしれない。見えている面が何かを先に確かめる）。
"""
import numpy as np

from .face_shapes import Head, raster_front

DEFAULTS = {"resolution": 0.0001, "visible": 0.0008, "uv_margin": 0.02, "grow": 2, "inside_color": [92, 26, 30], "tongue_color": [168, 72, 74]}


def merged(settings):
    return {**DEFAULTS, **settings}


def dilate(mask, iterations):
    """縦横の 4 近傍で iterations 回ふくらませる（絵の外は無いものとして。scipy.ndimage.binary_dilation の既定の形と同じ）。"""
    out = np.asarray(mask, dtype=bool).copy()
    for _ in range(iterations):
        grown = out.copy()
        grown[1:, :] |= out[:-1, :]
        grown[:-1, :] |= out[1:, :]
        grown[:, 1:] |= out[:, :-1]
        grown[:, :-1] |= out[:, 1:]
        out = grown
    return out


def raster_uv(uv, tri_loops, selection, width, height, margin):
    """選んだ三角形が色の絵の上で掛かる画素の印 (H, W)。三角形の外 margin（重心の座標の割合）まで含める。"""
    mask = np.zeros((height, width), dtype=bool)
    for t in np.where(selection)[0]:
        p = uv[tri_loops[t]]
        px, py = p[:, 0] * width, (1 - p[:, 1]) * height
        c0, c1 = max(int(np.floor(px.min())) - 1, 0), min(int(np.ceil(px.max())) + 2, width)
        r0, r1 = max(int(np.floor(py.min())) - 1, 0), min(int(np.ceil(py.max())) + 2, height)
        if c0 >= c1 or r0 >= r1:
            continue
        gx, gy = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        det = (py[1] - py[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (py[0] - py[2])
        if abs(det) < 1e-12:
            continue
        w0 = ((py[1] - py[2]) * (gx - px[2]) + (px[2] - px[1]) * (gy - py[2])) / det
        w1 = ((py[2] - py[0]) * (gx - px[2]) + (px[0] - px[2]) * (gy - py[2])) / det
        mask[r0:r1, c0:c1] |= (w0 >= -margin) & (w1 >= -margin) & (1 - w0 - w1 >= -margin)
    return mask


def inside_parts(head, settings):
    """口の中の部品（歯・舌など）の島の番号と、そのうち舌の島（奥まで届き、真ん中にある）の番号。"""
    rule, tongue_rule = settings["parts"], settings["tongue"]
    co = head.co
    parts = []
    for k in range(1, head.islands):
        c = co[head.island_of == k]
        centre, size = c.mean(axis=0), (c.max(axis=0) - c.min(axis=0)).max()
        if (abs(centre[0]) < rule["half_width"] and centre[1] < rule["front_y"] and rule["z"][0] < centre[2] < rule["z"][1]
                and size < rule["max_size"] and c[:, 1].min() > rule["front_limit"]):
            parts.append(k)
    tongue = [k for k in parts if co[head.island_of == k][:, 1].max() > tongue_rule["back_y"]
              and abs(co[head.island_of == k][:, 0].mean()) < tongue_rule["centre"]]
    return parts, tongue


def hidden_vertices(head, settings):
    """口のまわり（mouth の窓・zone）の頂点のうち、正面から見えない頂点の印（いちばん手前の面より visible 以上奥）。"""
    co, window = head.co, settings["window"]
    half, (z0, z1), res = window["half_width"], window["z"], settings["resolution"]
    _, depth = raster_front(co, head.tris, -half, half, z0, z1, res, settings["front_y"])
    zone = (np.abs(co[:, 0]) < settings["zone"]["half_width"]) & (co[:, 2] > z0) & (co[:, 2] < z1) & (co[:, 1] < settings["front_y"])
    col = np.clip(((co[:, 0] - (-half)) / res).astype(int), 0, depth.shape[1] - 1)
    row = np.clip(((z1 - co[:, 2]) / res).astype(int), 0, depth.shape[0] - 1)
    return zone & (co[:, 1] > depth[row, col] + settings["visible"])


def paint_inside(co, edges, tris, tri_loops, uv, image, settings):
    """色の絵 image（uint8 の (H, W, 3) か (H, W, 4)）の、口の中の面と部品が使う所だけを塗った絵と報告。外から見える面が使う画素には触らない。
    口の中の面は、肌（いちばん大きい島）の三角形のうち 3 頂点とも正面から見えないもの（唇のふちをまたぐ面は塗らない）。
    透けのある絵は、塗る画素だけ不透明にする（透けたままだと塗った色が見えない）。uv が 0〜1 の外の所は絵の外として塗らない（数を報告する）。"""
    settings = merged(settings)
    head = Head(co, edges, tris)
    height, width = image.shape[:2]
    hidden = hidden_vertices(head, settings)
    parts, tongue_parts = inside_parts(head, settings)
    pocket = (head.tri_island == 0) & hidden[head.tris].all(axis=1)
    tongue = np.isin(head.tri_island, tongue_parts)
    inner = np.isin(head.tri_island, parts) & ~tongue
    margin = settings["uv_margin"]
    inside_mask = raster_uv(uv, tri_loops, pocket | inner, width, height, margin)
    tongue_mask = raster_uv(uv, tri_loops, tongue, width, height, margin)
    other = raster_uv(uv, tri_loops, ~(pocket | inner | tongue), width, height, margin)
    overlap = int(((inside_mask | tongue_mask) & other).sum())
    paint = dilate(inside_mask, settings["grow"]) & ~other & ~tongue_mask
    paint_tongue = dilate(tongue_mask, settings["grow"]) & ~other
    out = image.copy()
    out[paint, :3] = settings["inside_color"]
    out[paint_tongue, :3] = settings["tongue_color"]     # ふくらませた所が重なれば舌の色（重なりは 1 回だけ数える）
    painted = paint | paint_tongue
    raised = 0
    if out.shape[2] == 4:                                # 透けのある絵は、塗る画素を不透明にする
        raised = int((painted & (out[..., 3] < 255)).sum())
        out[painted, 3] = 255
    corners = uv[tri_loops[pocket | inner | tongue]]     # 塗る三角形の角の uv
    outside = int(((corners < 0) | (corners > 1)).any(axis=(1, 2)).sum())
    report = {"pocket_triangles": int(pocket.sum()), "part_triangles": int(inner.sum()), "tongue_triangles": int(tongue.sum()),
              "parts": [int((head.island_of == k).sum()) for k in parts], "tongue_parts": [int((head.island_of == k).sum()) for k in tongue_parts],
              "painted_pixels": int(painted.sum()), "pixels_shared_with_visible_faces_left_untouched": overlap,
              "alpha_raised_pixels": raised, "uv_outside_triangles": outside,
              "inside_color": list(settings["inside_color"]), "tongue_color": list(settings["tongue_color"])}
    return out, report


# ---------- 頭の候補の口の作りを調べる（check.mouth） ----------

CHECK_DEFAULTS = {"resolution": 0.0001, "visible": 0.0008, "bins": 15, "corner_percentile": 98.0, "min_points": 3, "seeds": [0.003, 0.008],
                  "section_x": 0.0, "uv_margin": 0.02}
COLORS = {"background": (0, 0, 0), "skin": (200, 200, 200), "upper": (235, 150, 150), "lower": (150, 170, 235), "inside": (110, 35, 40),
          "part": (150, 150, 150), "seam": (255, 230, 0), "glued": (255, 0, 0)}


def shortest_paths(count, edges, lengths, seeds):
    """辺 (E, 2)（長さ lengths）でつながった count 個の点の、seeds のどれかからの最短の道の長さ (count,)（届かなければ inf）。"""
    import heapq
    adjacency = [[] for _ in range(count)]
    for (a, b), w in zip(np.asarray(edges).tolist(), np.asarray(lengths).tolist()):
        adjacency[a].append((b, w))
        adjacency[b].append((a, w))
    distance = np.full(count, np.inf)
    heap = []
    for s in np.asarray(seeds).tolist():
        distance[s] = 0.0
        heap.append((0.0, s))
    heapq.heapify(heap)
    while heap:
        d, v = heapq.heappop(heap)
        if d > distance[v]:
            continue
        for u, w in adjacency[v]:
            if d + w < distance[u]:
                distance[u] = d + w
                heapq.heappush(heap, (d + w, u))
    return distance


def edge_face_counts(edges, tris, count):
    """メッシュの辺ごとの、その辺を持つ面の数 (E,)（三角形にした面で数える。面の中の対角線はメッシュの辺に無いので数えない）。"""
    edges = np.sort(np.asarray(edges, dtype=np.int64), axis=1)
    tris = np.asarray(tris, dtype=np.int64)
    tri_edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    keys, counts = np.unique(tri_edges[:, 0] * count + tri_edges[:, 1], return_counts=True)
    wanted = edges[:, 0] * count + edges[:, 1]
    at = np.clip(np.searchsorted(keys, wanted), 0, len(keys) - 1)
    return np.where(keys[at] == wanted, counts[at], 0)


def section_segments(co, tris, x0):
    """面 x = x0 で三角形を切った線分 [(三角形の番号, (y, z), (y, z))]。"""
    out = []
    side = co[:, 0] - x0
    for t, tri in enumerate(np.asarray(tris)):
        s = side[tri]
        if (s > 0).all() or (s < 0).all():
            continue
        points = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = tri[i], tri[j]
            if (side[a] > 0) != (side[b] > 0) and side[a] != side[b]:
                u = side[a] / (side[a] - side[b])
                p = co[a] + (co[b] - co[a]) * u
                points.append((float(p[1]), float(p[2])))
        if len(points) == 2:
            out.append((t, points[0], points[1]))
    return out


def check_mouth(co, edges, tris, settings, uv=None, tri_loops=None, image=None):
    """頭の候補の口の作りを数で調べる（合否は決めない）。戻り: (報告, 正面の色分けの絵 (H, W, 3) uint8, 真ん中の断面の絵 (H, W, 3) uint8)。
    (1) 上下の唇が別の面か: 口の幅を区間に分け、区間ごとに、その x の幅の中の浅い所（見える面から seam.depth まで）だけを通って、唇の外の面の
        合わせ目の上の帯から下の帯へ（帯は合わせ目から seeds の [近い, 遠い] の間）、面のつながりで行けるかを調べる。上下の唇が別の面なら、浅い所では
        つながらない（つながるのは口の奥）。くっついている区間は、合わせ目をまたいでつながる。つながる区間（くっついている所）・つながらない区間の数、
        くっついた所の頂点、口の中を回ってもよいときのいちばん短い道（deep_path_mm）。
    (2) 口のまわりの肌の辺のうち、3 枚以上の面が集まる辺と、1 枚の面しか無い辺（穴の縁）の数。
    (3) 口の中: 3 頂点とも正面から見えない肌の三角形（唇の裏の袋）の数と、見える面からの奥行き。parts・tongue があれば口の中の部品。
    (4) 色の絵（image と uv・tri_loops）があれば、口の中の面と、口のまわりの見える肌の面が使う画素の色の中央値と、その差。
    (5) 絵: 正面の色分け（上唇・下唇・口の中・ほかの部品、合わせ目の線、くっついた頂点）と、x = section_x の断面。"""
    from .face_shapes import mouth_seam
    settings = {**CHECK_DEFAULTS, **settings}
    head = Head(co, edges, tris)
    seam = mouth_seam(head, settings, settings)
    mv, fd, outer, xc = seam["mv"], seam["fd"], seam["outer"], seam["xc"]
    seam_x, seam_z = seam["seam_x"], seam["seam_z"]
    local = -np.ones(head.n, dtype=np.int64)
    local[mv] = np.arange(len(mv))
    ea, eb = local[head.edges[:, 0]], local[head.edges[:, 1]]
    keep = (ea >= 0) & (eb >= 0)
    graph_edges = np.stack([ea[keep], eb[keep]], axis=1)
    lengths = np.linalg.norm(head.co[mv[graph_edges[:, 0]]] - head.co[mv[graph_edges[:, 1]]], axis=1)
    x, y, z = head.co[mv, 0], head.co[mv, 1], head.co[mv, 2]
    seam_at = np.interp(x, seam_x, seam_z)
    near, far = settings["seeds"]
    seed_up = np.where(outer & (z > seam_at + near) & (z < seam_at + far))[0]
    seed_low = np.where(outer & (z < seam_at - near) & (z > seam_at - far))[0]
    if not len(seed_up) or not len(seed_low):
        raise ValueError("唇の外の面に、合わせ目の上か下の帯（seeds）の頂点がありません（seam・seeds を確かめてください）")
    up_mask, low_mask = np.zeros(len(mv), dtype=bool), np.zeros(len(mv), dtype=bool)
    up_mask[seed_up], low_mask[seed_low] = True, True
    whole = shortest_paths(len(mv), graph_edges, lengths, seed_up)
    deep = float(whole[low_mask].min()) if np.isfinite(whole[low_mask]).any() else None   # 口の中を回ってもよいときの、いちばん短い道
    shallow = y < fd + settings["seam"]["depth"]          # 見える面のすぐ裏まで（合わせ目の帯の深さ）
    around = np.abs(z - seam_at) < settings["seam"]["band"]
    edges_x = np.linspace(-xc, xc, settings["bins"])
    bins, glued_vertices = [], np.zeros(len(mv), dtype=bool)
    for a, b in zip(edges_x[:-1], edges_x[1:]):
        slab = shallow & (x >= a) & (x < b)               # この x の幅の中の、浅い所だけでつながるか（横へ回らせない）
        ups, lows = np.where(slab & up_mask)[0], np.where(slab & low_mask)[0]
        entry = {"x": round(0.5 * (a + b), 4), "seeds": [int(len(ups)), int(len(lows))], "connected": None, "path_mm": None}
        if len(ups) and len(lows):
            inside = slab[graph_edges[:, 0]] & slab[graph_edges[:, 1]]
            from_up = shortest_paths(len(mv), graph_edges[inside], lengths[inside], ups)
            from_low = shortest_paths(len(mv), graph_edges[inside], lengths[inside], lows)
            reach = float(from_up[lows].min())
            entry["connected"] = bool(np.isfinite(reach))
            if entry["connected"]:
                entry["path_mm"] = round(reach * 1000, 2)
                glued_vertices |= slab & around & np.isfinite(from_up) & np.isfinite(from_low)
        bins.append(entry)
    measured = [entry for entry in bins if entry["connected"] is not None]
    lips = {"seed_vertices": [int(len(seed_up)), int(len(seed_low))], "bins": bins, "bins_measured": len(measured),
            "glued_bins": sum(1 for entry in measured if entry["connected"]), "separate_bins": sum(1 for entry in measured if not entry["connected"]),
            "glued_vertices": int(glued_vertices.sum()), "deep_path_mm": round(deep * 1000, 2) if deep is not None else None}
    zone = np.zeros(head.n, dtype=bool)
    zone[mv] = True
    counts = edge_face_counts(head.edges, head.tris, head.n)
    in_zone = zone[head.edges[:, 0]] & zone[head.edges[:, 1]]
    structure = {"zone_edges": int(in_zone.sum()), "three_or_more_faces": int((in_zone & (counts >= 3)).sum()),
                 "open_edges": int((in_zone & (counts == 1)).sum())}
    hidden = np.zeros(head.n, dtype=bool)
    hidden[mv[~outer]] = True
    visible = np.zeros(head.n, dtype=bool)
    visible[mv[outer]] = True
    skin_tri = head.tri_island == 0
    pocket = skin_tri & hidden[head.tris].all(axis=1)
    front_skin = skin_tri & visible[head.tris].all(axis=1)
    depth_mm = float((y[~outer] - fd[~outer]).max() * 1000) if (~outer).any() else 0.0
    inside = {"pocket_triangles": int(pocket.sum()), "hidden_vertices": int((~outer).sum()), "depth_mm": round(depth_mm, 2)}
    if settings.get("parts") is not None and settings.get("tongue") is not None:
        parts, tongue_parts = inside_parts(head, settings)
        inside["parts"] = [int((head.island_of == k).sum()) for k in parts]
        inside["tongue_parts"] = [int((head.island_of == k).sum()) for k in tongue_parts]
    color = None
    if image is not None and uv is not None and tri_loops is not None:
        height, width = image.shape[:2]
        inside_mask = raster_uv(uv, tri_loops, pocket, width, height, settings["uv_margin"])
        skin_mask = raster_uv(uv, tri_loops, front_skin, width, height, settings["uv_margin"]) & ~inside_mask
        rgb = image[..., :3].astype(float)
        corners = uv[tri_loops[pocket | front_skin]]      # uv が 0〜1 の外の所は絵の外として数えない
        color = {"inside_pixels": int(inside_mask.sum()), "skin_pixels": int(skin_mask.sum()), "inside_median": None, "skin_median": None,
                 "distance": None, "uv_outside_triangles": int(((corners < 0) | (corners > 1)).any(axis=(1, 2)).sum())}
        if inside_mask.any() and skin_mask.any():
            a, b = np.median(rgb[inside_mask], axis=0), np.median(rgb[skin_mask], axis=0)
            color.update(inside_median=[round(float(v), 1) for v in a], skin_median=[round(float(v), 1) for v in b],
                         distance=round(float(np.linalg.norm(a - b)), 1))
    report = {"schema_version": "model3d-mouth-check/0.1", "seam": seam["report"], "lips": lips, "edges": structure, "inside": inside,
              "color": color}
    return report, front_view(head, seam, settings, mv[glued_vertices], hidden), section_view(head, settings, hidden)


def front_view(head, seam, settings, glued, hidden):
    """正面の色分けの絵: 窓の z バッファのいちばん手前の三角形を、上唇・下唇（合わせ目の上下の、口のまわりの見える肌）・口の中（見えない肌）・
    ほかの肌・ほかの部品で塗り、合わせ目の線とくっついた頂点を重ねる。"""
    owner, res = seam["owner"], settings["resolution"]
    half, (z0, z1) = settings["window"]["half_width"], settings["window"]["z"]
    height, width = owner.shape
    centre = head.co[head.tris].mean(axis=1)
    above = centre[:, 2] >= np.interp(centre[:, 0], seam["seam_x"], seam["seam_z"])
    zone = np.zeros(head.n, dtype=bool)
    zone[seam["mv"]] = True
    in_zone = zone[head.tris].all(axis=1)
    hidden_tri = hidden[head.tris].any(axis=1)
    skin = head.tri_island == 0
    colors = np.empty((len(head.tris), 3), dtype=np.uint8)
    colors[:] = COLORS["part"]
    colors[skin] = COLORS["skin"]
    colors[skin & in_zone & ~hidden_tri & above] = COLORS["upper"]
    colors[skin & in_zone & ~hidden_tri & ~above] = COLORS["lower"]
    colors[skin & hidden_tri] = COLORS["inside"]
    out = np.zeros((height, width, 3), dtype=np.uint8)
    out[:] = COLORS["background"]
    drawn = owner >= 0
    out[drawn] = colors[owner[drawn]]
    xs = np.linspace(-seam["xc"], seam["xc"], max(int(2 * seam["xc"] / res), 2))
    cols = np.clip(((xs + half) / res).astype(int), 0, width - 1)
    rows = np.clip(((z1 - np.interp(xs, seam["seam_x"], seam["seam_z"])) / res).astype(int), 0, height - 1)
    out[rows, cols] = COLORS["seam"]
    out[np.clip(rows + 1, 0, height - 1), cols] = COLORS["seam"]          # 線は 2 画素の太さ
    for v in glued:
        c, r = int((head.co[v, 0] + half) / res), int((z1 - head.co[v, 2]) / res)
        out[max(r - 2, 0):r + 3, max(c - 2, 0):c + 3] = COLORS["glued"]
    return out


def section_view(head, settings, hidden):
    """真ん中の断面の絵（x = section_x の面で切った線を横から見る。左が顔の前）: 肌は白、見えない肌（口の中）は赤、ほかの部品は灰色。"""
    from PIL import Image, ImageDraw
    res, (z0, z1), front_y = settings["resolution"], settings["window"]["z"], settings["front_y"]
    segments = section_segments(head.co, head.tris, settings["section_x"])
    keep = [(t, a, b) for t, a, b in segments if min(a[1], b[1]) < z1 and max(a[1], b[1]) > z0 and min(a[0], b[0]) < front_y]
    y_min = min([min(a[0], b[0]) for _, a, b in keep], default=front_y - 0.05) - 0.002
    width, height = max(int(round((front_y - y_min) / res)), 2), max(int(round((z1 - z0) / res)), 2)
    canvas = Image.new("RGB", (width, height), COLORS["background"])
    draw = ImageDraw.Draw(canvas)
    for t, a, b in keep:
        tri = head.tris[t]
        color = COLORS["part"] if head.tri_island[t] != 0 else (COLORS["inside"] if hidden[tri].any() else COLORS["skin"])
        draw.line([((a[0] - y_min) / res, (z1 - a[1]) / res), ((b[0] - y_min) / res, (z1 - b[1]) / res)], fill=color, width=2)
    return np.asarray(canvas)
