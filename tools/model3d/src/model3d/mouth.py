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
    out[paint_tongue, :3] = settings["tongue_color"]
    raised = 0
    if out.shape[2] == 4:                                # 透けのある絵は、塗る画素を不透明にする
        painted = paint | paint_tongue
        raised = int((painted & (out[..., 3] < 255)).sum())
        out[painted, 3] = 255
    corners = uv[tri_loops[pocket | inner | tongue]]     # 塗る三角形の角の uv
    outside = int(((corners < 0) | (corners > 1)).any(axis=(1, 2)).sum())
    report = {"pocket_triangles": int(pocket.sum()), "part_triangles": int(inner.sum()), "tongue_triangles": int(tongue.sum()),
              "parts": [int((head.island_of == k).sum()) for k in parts], "tongue_parts": [int((head.island_of == k).sum()) for k in tongue_parts],
              "painted_pixels": int(paint.sum() + paint_tongue.sum()), "pixels_shared_with_visible_faces_left_untouched": overlap,
              "alpha_raised_pixels": raised, "uv_outside_triangles": outside,
              "inside_color": list(settings["inside_color"]), "tongue_color": list(settings["tongue_color"])}
    return out, report
