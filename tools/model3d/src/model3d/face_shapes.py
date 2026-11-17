"""表情の形（形状キー）の頂点の動きを、頭のメッシュから計算する（Blender の外。numpy だけ）。model.face_shapes の中身。

docs/model3d.md の「表情を形で作る」。リポジトリ外の 3 作目の VRM のダンスの台本と同じ計算の順（同じ頭と同じ設定から、同じ値になる）。

考え方（その台本で分かったこと）:
- 画像から作った頭は、目のまわりの面に細かい穴が多く、左右の並びも同じでない。面のつながりではなく、正面から見た位置（横 x・高さ z）で
  動かす量を決める。頭は −Y を向き、長さは m（model.export_mesh の npz）。
- 目: 白目の面は、辺のいちばん多い頂点を中心にした扇（eyes.find の箱の肌の頂点から探す）。瞳・眉は別の部品（eyes.iris・eyes.brow の条件の島）。
  正面の z バッファ（resolution の画素）で、白目の扇と瞳が見えている所を目の開きとし、列ごとの上の縁 U(x)・下の縁 L(x) と、縁のすぐ外の
  いちばん手前の奥行きを測る（smooth_px の画素でならす）。上まぶた（U より上）は下へ、下まぶた（L より下）は上へ、閉じる線 C(x) まで寄せる。
  上まぶたは縁から upper.keep をそのまま動かし（まつ毛の線を伸ばさない）、さらに upper.fade で 0 へ。下まぶたは lower.fade で 0 へ。
  白目と瞳は動かさない（まぶたがその前を通る）。
  閉じるとき、上下のまぶたの奥行きを合わせ（上まつ毛が手前に張り出していると、斜めから見て下に白目がのぞく）、瞳を iris_back だけ奥へ
  下げ、瞳の前ではまぶたを瞳より手前に保つ。
  まばたき（blink）の閉じる線は、下の縁から close_at の割合の高さ。笑顔の閉じ目（blink_smile。eyes.smile があるとき）は、目頭と目尻を
  結ぶ線から上へ smile.arc ふくらむ弧。
- 眉（brows）: 眉の部品をそのまま上下に動かす（外側 outer から内側 inner へ量を直線で）。前へ forward。
- 口角（mouth_smile。mouth があるとき）: 唇の合わせ目（正面から見える面のすぐ裏の帯）の高さと口の端を測り、口の端のまわりだけに掛かる
  重み（半径 corner.radius のガウス）で、外へ・上へ・奥へ動かす。口は開けない。
"""
import numpy as np

from .blender_adapter.model_edit import islands as vertex_roots

DEFAULTS = {"resolution": 0.0001,
            "eyes": {"min_rows": 3, "gap_px": 5, "smooth_px": 15, "edge_rows": 3, "corner_margin": 0.004, "iris_margin": 0.004,
                     "iris_gap": 0.0012, "cover_gap": 0.0010, "cover_above": 0.001, "lower_depth_gain": 4.0},
            "smile": {"power": 0.8, "clip": [0.05, 0.10]},
            "mouth": {"visible": 0.0008, "bins": 15, "corner_percentile": 98.0, "min_points": 3}}


def merged(settings):
    """既定の値に settings を重ねる（eyes・eyes.smile・mouth は中の鍵ごと）。"""
    out = {"resolution": settings.get("resolution", DEFAULTS["resolution"]), "front_y": settings["front_y"]}
    if settings.get("eyes") is not None:
        eyes = {**DEFAULTS["eyes"], **settings["eyes"]}
        if settings["eyes"].get("smile") is not None:
            eyes["smile"] = {**DEFAULTS["smile"], **settings["eyes"]["smile"]}
        out["eyes"] = eyes
    if settings.get("brows") is not None:
        out["brows"] = settings["brows"]
    if settings.get("mouth") is not None:
        out["mouth"] = {**DEFAULTS["mouth"], **settings["mouth"]}
    return out


def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


class Head:
    """頭のメッシュ（co・edges・tris）と、島（辺でつながった頂点）の番号（大きい順。0 がいちばん大きい島＝肌）。"""

    def __init__(self, co, edges, tris):
        self.co, self.edges, self.tris = np.asarray(co, dtype=float), np.asarray(edges), np.asarray(tris)
        self.n = len(self.co)
        _, island_of = np.unique(vertex_roots(self.n, self.edges), return_inverse=True)
        sizes = np.bincount(island_of)
        rank = np.empty(len(sizes), dtype=int)
        rank[np.argsort(-sizes)] = np.arange(len(sizes))
        self.island_of = rank[island_of]
        self.islands = len(sizes)
        self.valence = np.bincount(self.edges.reshape(-1), minlength=self.n)
        self.skin = self.island_of == 0
        self.tri_island = self.island_of[self.tris[:, 0]]
        self._shape = {}

    def neighbours(self, vertex):
        """頂点の隣（辺の並びの順。台本と同じ並びで、輪の平均の足し方も同じになる）。"""
        touching = self.edges[(self.edges[:, 0] == vertex) | (self.edges[:, 1] == vertex)]
        return np.where(touching[:, 0] == vertex, touching[:, 1], touching[:, 0]).astype(int)

    def island_shape(self, k):
        """島 k の中心（頂点の平均）と大きさ（いちばん長い辺の箱の長さ）。"""
        if k not in self._shape:
            c = self.co[self.island_of == k]
            self._shape[k] = (c.mean(axis=0), (c.max(axis=0) - c.min(axis=0)).max())
        return self._shape[k]

    def islands_in(self, x0, x1, y1, z0, z1, min_size=0.0, max_size=1.0):
        """中心が箱（x0 < x < x1・y < y1・z0 < z < z1）に入り、大きさが min_size 以上 max_size 以下の島の番号（肌を除く）。"""
        found = []
        for k in range(1, self.islands):
            centre, size = self.island_shape(k)
            if x0 < centre[0] < x1 and centre[1] < y1 and z0 < centre[2] < z1 and min_size <= size <= max_size:
                found.append(k)
        return found


def raster_front(co, tris, x0, x1, z0, z1, res, front_y):
    """正面（−y の側）から見た、いちばん手前の三角形の番号と奥行き y。画素は res m、行は上が z1。いちばん手前の頂点の y が front_y より
    手前の三角形だけを描く（頭の後ろ側を除く）。三角形の番号の順に描き、同じ奥行きなら先の三角形。"""
    width, height = int(round((x1 - x0) / res)), int(round((z1 - z0) / res))
    depth = np.full((height, width), np.inf)
    owner = np.full((height, width), -1)
    cand = np.where((co[tris, 0].max(axis=1) > x0) & (co[tris, 0].min(axis=1) < x1) & (co[tris, 2].max(axis=1) > z0) & (co[tris, 2].min(axis=1) < z1)
                    & (co[tris, 1].min(axis=1) < front_y))[0]
    for t in cand:
        p = co[tris[t]]
        px, pz, py = (p[:, 0] - x0) / res, (z1 - p[:, 2]) / res, p[:, 1]
        c0, c1 = max(int(np.floor(px.min())), 0), min(int(np.ceil(px.max())) + 1, width)
        r0, r1 = max(int(np.floor(pz.min())), 0), min(int(np.ceil(pz.max())) + 1, height)
        if c0 >= c1 or r0 >= r1:
            continue
        gx, gz = np.meshgrid(np.arange(c0, c1) + 0.5, np.arange(r0, r1) + 0.5)
        det = (pz[1] - pz[2]) * (px[0] - px[2]) + (px[2] - px[1]) * (pz[0] - pz[2])
        if abs(det) < 1e-12:
            continue
        w0 = ((pz[1] - pz[2]) * (gx - px[2]) + (px[2] - px[1]) * (gz - pz[2])) / det
        w1 = ((pz[2] - pz[0]) * (gx - px[2]) + (px[0] - px[2]) * (gz - pz[2])) / det
        w2 = 1 - w0 - w1
        inside = (w0 >= -1e-6) & (w1 >= -1e-6) & (w2 >= -1e-6)
        y = w0 * py[0] + w1 * py[1] + w2 * py[2]
        block_d, block_o = depth[r0:r1, c0:c1], owner[r0:r1, c0:c1]
        better = inside & (y < block_d)
        block_d[better] = y[better]
        block_o[better] = t
    return owner, depth


def _pad(values, kernel):
    """端の値を足してから、長さ len(kernel) の平均でならす（長さは同じ）。"""
    half = len(kernel) // 2
    return np.convolve(np.concatenate([np.repeat(values[0], half), values, np.repeat(values[-1], half)]), kernel, mode="valid")


def _edge_depth(depth, rows, cols):
    """列ごとに、rows の行の範囲のいちばん手前の奥行き（範囲が絵の外なら inf）。"""
    out = []
    for (r0, r1), c in zip(rows, cols):
        block = depth[max(r0, 0):max(r1, 0), c]
        out.append(np.min(block) if block.size else np.inf)
    return np.array(out)


def _fill(values, where):
    """測れなかった列（非有限）を、となりの列の値で埋める（直線の補間）。全部が測れなければ ValueError。"""
    bad = ~np.isfinite(values)
    if bad.all():
        raise ValueError(f"{where}を測れません（目の開きの縁の外に、手前の面がありません）")
    if bad.any():
        values[bad] = np.interp(np.where(bad)[0], np.where(~bad)[0], values[~bad])
    return values


def _parts(head, found):
    """島の番号の並びを、報告用の頂点の数の並びに。"""
    return [int((head.island_of == k).sum()) for k in found]


def eye_shapes(head, settings, side, sign):
    """片目（side は R か L。sign は x の向き）の形 {名前: (N, 3)} と報告と確かめ用の配列。"""
    co, n, eyes = head.co, head.n, settings["eyes"]
    res = settings["resolution"]
    find = eyes["find"]
    box = head.skin & (co[:, 0] * sign > find["x"][0]) & (co[:, 0] * sign < find["x"][1]) & (co[:, 2] > find["z"][0]) \
        & (co[:, 2] < find["z"][1]) & (co[:, 1] < find["front_y"])
    idx = np.where(box)[0]
    if not len(idx):
        raise ValueError(f"白目の扇の中心を探す箱（eyes.find）に、肌の頂点がありません（{side}）")
    centre = int(idx[np.argmax(head.valence[idx])])      # 白目の扇の中心（つながりがいちばん多い頂点）
    ring = head.neighbours(centre)
    socket = np.zeros(n, dtype=bool)
    socket[centre] = True
    socket[ring] = True
    cx = co[ring, 0].mean()
    xs = (cx - eyes["window"]["half_width"], cx + eyes["window"]["half_width"])
    zs = tuple(eyes["window"]["z"])
    lo, hi = min(xs), max(xs)
    iris_rule = eyes["iris"]                              # 瞳の部品: 扇の輪の範囲に中心がある、小さく薄い島
    iris = head.islands_in(co[ring, 0].min(), co[ring, 0].max(), iris_rule["front_y"], co[ring, 2].min(), co[ring, 2].max(),
                           max_size=iris_rule["max_size"])
    iris = [k for k in iris if (co[head.island_of == k][:, 1].max() - co[head.island_of == k][:, 1].min()) < iris_rule["max_depth"]
            and co[head.island_of == k][:, 1].mean() > iris_rule["back_y"]]
    if not iris:
        raise ValueError(f"瞳の部品が見つかりません（{side}。eyes.iris を確かめてください）")
    iris_mask = np.isin(head.island_of, iris)
    brow = []
    if eyes.get("brow") is not None:                      # 眉の部品（まぶたと一緒に動かさない）
        rule = eyes["brow"]
        brow = [k for k in head.islands_in(lo, hi, rule["front_y"], rule["z"][0], rule["z"][1], min_size=rule["min_size"])
                if co[head.island_of == k][:, 2].min() > rule["min_z"]]
    brow_mask = np.isin(head.island_of, brow)
    owner, depth = raster_front(co, head.tris, lo, hi, zs[0], zs[1], res, settings["front_y"])
    fan_tri = np.any(head.tris == centre, axis=1)
    open_tri = fan_tri | np.isin(head.tri_island, iris)
    opening = (owner >= 0) & open_tri[np.clip(owner, 0, None)]
    cols = np.where(opening.sum(axis=0) >= eyes["min_rows"])[0]
    if not len(cols):
        raise ValueError(f"目の開きが見つかりません（{side}。eyes.window を確かめてください）")
    # いちばん長い連続区間を目の開きとする（まつ毛のすき間などの小さな飛び地を除く）
    runs = np.split(cols, np.where(np.diff(cols) > eyes["gap_px"])[0] + 1)
    cols = max(runs, key=len)
    xa, xb = lo + (cols[0] + 0.5) * res, lo + (cols[-1] + 0.5) * res
    if xb <= xa:
        raise ValueError(f"目の開きの幅が 1 画素しかありません（{side}）")
    U = np.array([zs[1] - (np.where(opening[:, c])[0].min()) * res for c in cols])
    L = np.array([zs[1] - (np.where(opening[:, c])[0].max() + 1) * res for c in cols])
    kernel = np.ones(eyes["smooth_px"]) / eyes["smooth_px"]
    # 縁の奥行き: 開きのすぐ上（上まつ毛の下の端）と、すぐ下（下まぶたの縁）の、いちばん手前の y
    tops = [int(np.where(opening[:, c])[0].min()) for c in cols]
    bots = [int(np.where(opening[:, c])[0].max()) for c in cols]
    rows = eyes["edge_rows"]
    yU = _fill(_edge_depth(depth, [(r - rows, r) for r in tops], cols), "上の縁の奥行き")
    yL = _fill(_edge_depth(depth, [(r + 1, r + 1 + rows) for r in bots], cols), "下の縁の奥行き")
    U, L, yU, yL = _pad(U, kernel), _pad(L, kernel), _pad(yU, kernel), _pad(yL, kernel)
    xcol = lo + (cols + 0.5) * res
    iris_x0, iris_x1, iris_front = co[iris_mask, 0].min(), co[iris_mask, 0].max(), co[iris_mask, 1].min()
    report = {"fan_centre": centre, "ring": len(ring), "iris_parts": _parts(head, iris), "brow_parts": _parts(head, brow),
              "x": [round(xa, 4), round(xb, 4)], "opening_height_mm": round(float((U - L).max() * 1000), 2),
              "upper_z": [round(float(U.min()), 4), round(float(U.max()), 4)], "lower_z": [round(float(L.min()), 4), round(float(L.max()), 4)],
              "upper_edge_y": [round(float(yU.min()), 4), round(float(yU.max()), 4)],
              "lower_edge_y": [round(float(yL.min()), 4), round(float(yL.max()), 4)], "iris_front_y": round(float(iris_front), 4)}
    # まぶたとして動かす頂点: 窓の中の肌（扇・瞳・眉は除く）と、窓に中心がある小さな部品（下まつ毛・二重の線など）
    region_rule = eyes["region"]
    region = (co[:, 0] > lo) & (co[:, 0] < hi) & (co[:, 2] > zs[0]) & (co[:, 2] < zs[1]) & (co[:, 1] < region_rule["front_y"]) & ~socket \
        & ~iris_mask & ~brow_mask
    region &= np.isin(head.island_of, [0] + head.islands_in(lo, hi, region_rule["front_y"], zs[0], zs[1], max_size=region_rule["max_part_size"]))
    v = np.where(region)[0]
    report["moved_vertices"] = int(len(v))
    x, z = co[v, 0], co[v, 2]
    Ux, Lx = np.interp(x, xcol, U), np.interp(x, xcol, L)
    yUx, yLx = np.interp(x, xcol, yU), np.interp(x, xcol, yL)
    iris_back = eyes["iris_back"]                         # 閉じるとき、瞳を奥へ下げる量
    outside = np.maximum(np.maximum(iris_x0 - x, x - iris_x1), 0.0)
    over_iris = smoothstep(1 - outside / eyes["iris_margin"])
    # 閉じた線の奥行き: 上の縁と下の縁の中ほど。瞳の前では、下げた瞳より iris_gap 手前まで出す
    yCx = 0.5 * (yUx + yLx)
    yCx = yCx + (np.minimum(yCx, iris_front + iris_back - eyes["iris_gap"]) - yCx) * over_iris
    s = np.clip((x - xa) / (xb - xa), 0, 1)               # 0 = 画面で左の端、1 = 右の端
    margin = eyes["corner_margin"]
    wx = np.where(x < xa, smoothstep(1 - (xa - x) / margin), np.where(x > xb, smoothstep(1 - (x - xb) / margin), 1.0))
    corner_z = np.interp(x, [xa, xb], [0.5 * (U[0] + L[0]), 0.5 * (U[-1] + L[-1])])
    keep, fade_up, fade_low = eyes["upper"]["keep"], eyes["upper"]["fade"], eyes["lower"]["fade"]

    def close_shape(C):
        """閉じる線 C（頂点ごとの高さ）へ、上下のまぶたを寄せる動き。"""
        delta = np.zeros((n, 3))
        up = z >= 0.5 * (Ux + Lx)                         # 開きの中にある肌の頂点は、近い方の縁と同じに動かす
        move_up = -(Ux - C)                               # 上まぶたの縁が動く量（下向きで負）
        move_low = C - Lx                                 # 下まぶたの縁が動く量（上向きで正）
        fall_up = np.where(z >= Ux, 1 - smoothstep((z - Ux - keep) / fade_up), 1.0)
        fall_low = np.where(z <= Lx, 1 - smoothstep((Lx - z) / fade_low), 1.0)
        weight = np.where(up, fall_up, fall_low) * wx
        dz = np.where(up, move_up, move_low) * weight
        delta[v, 2] = dz
        closing = np.clip((Ux - C) / np.maximum(Ux - Lx, 1e-6), 0, 1)   # この形で、上の縁がどこまで下りるか（1 = 下の縁まで）
        dy = np.where(up, (yCx - yUx) * closing,
                      (yCx - yLx) * np.clip((C - Lx) / np.maximum(Ux - Lx, 1e-6) * eyes["lower_depth_gain"], 0, 1)) * weight
        # 下りてきたまぶたが、瞳より手前にあるようにする
        z_new, y_new = z + dz, co[v, 1] + dy
        limit = iris_front + iris_back - eyes["cover_gap"]
        cover = up & (z_new < Ux + eyes["cover_above"]) & (over_iris > 0)
        y_new = np.where(cover, np.minimum(y_new, limit + (y_new - limit) * (1 - over_iris)), y_new)
        delta[v, 1] = y_new - co[v, 1]
        delta[iris_mask, 1] = iris_back
        return delta

    shapes = {"blink_" + side: close_shape(Lx + eyes["close_at"] * (Ux - Lx))}
    smile = eyes.get("smile")
    if smile is not None:                                 # 笑顔の閉じ目: 目頭と目尻を結ぶ線から、上へふくらむ弧
        arc = corner_z + smile["arc"] * np.sin(np.pi * s) ** smile["power"]
        low, high = smile["clip"]
        shapes["blink_smile_" + side] = close_shape(np.clip(arc, Lx + low * (Ux - Lx), Ux - high * (Ux - Lx)))
    brows = settings.get("brows")
    if brows is not None:                                 # 眉（別の部品）。t は外側 0 → 内側 1
        bv = np.where(brow_mask)[0]
        if not len(bv):
            raise ValueError(f"眉の部品が見つかりません（{side}。eyes.brow を確かめてください）")
        report["brow_vertices"] = int(len(bv))
        t = (np.abs(co[bv, 0]).max() - np.abs(co[bv, 0])) / max(np.ptp(np.abs(co[bv, 0])), 1e-6)
        for name, amount in brows["shapes"].items():
            inner, outer = amount["inner"], amount["outer"]
            dz = inner * t + outer * (1 - t) if inner != outer else inner + 0.0 * t   # 同じ量なら一定（丸めの揺れを足さない）
            delta = np.zeros((n, 3))
            delta[bv, 2] = dz
            delta[bv, 1] = -brows["forward"]
            shapes[f"{name}_{side}"] = delta
    debug = {f"{side}_opening": opening, f"{side}_xcol": xcol, f"{side}_U": U, f"{side}_L": L, f"{side}_lo": np.float64(lo),
             f"{side}_hi": np.float64(hi), f"{side}_z0": np.float64(zs[0]), f"{side}_z1": np.float64(zs[1])}
    return shapes, report, debug


def mouth_smile(head, settings):
    """口角を上げる形 (N, 3) と報告。唇の合わせ目の高さと口の端を測り、口の端のまわりだけに掛かる重みで動かす（口は開けない）。"""
    co, n, mouth = head.co, head.n, settings["mouth"]
    res = settings["resolution"]
    half, (z0, z1) = mouth["window"]["half_width"], mouth["window"]["z"]
    owner, depth = raster_front(co, head.tris, -half, half, z0, z1, res, settings["front_y"])

    def front_depth(x, z):
        c = np.clip(((x - (-half)) / res).astype(int), 0, depth.shape[1] - 1)
        r = np.clip(((z1 - z) / res).astype(int), 0, depth.shape[0] - 1)
        return depth[r, c]

    zone = (np.abs(co[:, 0]) < mouth["zone"]["half_width"]) & (co[:, 2] > z0) & (co[:, 2] < z1) & (co[:, 1] < settings["front_y"])
    mv = np.where(zone & head.skin)[0]
    if not len(mv):
        raise ValueError("口のまわり（mouth.zone）に、肌の頂点がありません")
    fd = front_depth(co[mv, 0], co[mv, 2])
    seam_rule = mouth["seam"]
    outer = co[mv, 1] <= fd + mouth["visible"]            # 正面から見える面にある頂点
    band = ~outer & (co[mv, 1] < fd + seam_rule["depth"])   # 唇の合わせ目（外の面のすぐ裏）
    centre_band = band & (np.abs(co[mv, 0]) < seam_rule["centre"])
    if not centre_band.any():
        raise ValueError("唇の合わせ目が見つかりません（mouth.seam を確かめてください）")
    seam0 = float(np.median(co[mv[centre_band], 2]))
    inner = ~outer & (np.abs(co[mv, 2] - seam0) < seam_rule["band"])
    if not inner.any():
        raise ValueError("口の端が見つかりません（合わせ目の高さの裏の頂点がありません。mouth.seam を確かめてください）")
    xc = float(np.percentile(np.abs(co[mv[inner], 0]), mouth["corner_percentile"]))   # 口の端（唇の裏の袋の幅）
    bins = np.linspace(-xc, xc, mouth["bins"])
    seam_x, seam_z = [], []
    for a, b in zip(bins[:-1], bins[1:]):
        pick = band & (co[mv, 0] >= a) & (co[mv, 0] < b) & (np.abs(co[mv, 2] - seam0) < seam_rule["band"])
        if pick.sum() >= mouth["min_points"]:
            seam_x.append(0.5 * (a + b))
            seam_z.append(float(np.median(co[mv[pick], 2])))
    if not seam_x:
        raise ValueError("唇の合わせ目の高さを、口の幅の中で測れません（mouth.seam・min_points を確かめてください）")
    seam_x, seam_z = np.array(seam_x), np.array(seam_z)
    report = {"seam_z_centre": round(seam0, 4), "corner_x": round(xc, 4), "seam_z_range": [round(float(seam_z.min()), 4), round(float(seam_z.max()), 4)],
              "outer_vertices": int(outer.sum()), "inner_vertices": int((~outer).sum())}
    x, y, z = co[mv, 0], co[mv, 1], co[mv, 2]
    radius, reach = mouth["corner"]["radius"], mouth["corner"]["depth"]
    corner = {sign: np.exp(-(((x - sign * xc) ** 2 + (z - np.interp(sign * xc, seam_x, seam_z)) ** 2) / radius ** 2)) * (y < fd + reach)
              for sign in (-1.0, 1.0)}
    out, up, back = mouth["smile"]["out"], mouth["smile"]["up"], mouth["smile"]["back"]
    delta = np.zeros((n, 3))
    delta[mv, 0] = (-out) * corner[-1.0] + out * corner[1.0]   # 口の端ごとに外へ（左右の重みの順に足す。台本と同じ順）
    delta[mv, 2] = up * corner[-1.0] + up * corner[1.0]
    delta[mv, 1] = back * corner[-1.0] + back * corner[1.0]
    report["moved_vertices"] = int((np.linalg.norm(delta, axis=1) > 1e-6).sum())
    return delta, report


def build(co, edges, tris, settings):
    """頭のメッシュから形を作る。戻り: ({名前: (N, 3) の動き（世界の座標、m）}、報告、確かめ用の配列)。形の並びは、右の目（R）の
    まばたき・笑顔の閉じ目・眉、左の目（L）の同じもの、口角の順（形状キーの並びになる）。作れなければ ValueError。"""
    settings = merged(settings)
    head = Head(co, edges, tris)
    shapes, debug = {}, {}
    report = {"schema_version": "model3d-face-shapes/0.1", "vertices": head.n, "islands": head.islands}
    if "eyes" in settings:
        report["eyes"] = {}
        for side, sign in (("R", -1.0), ("L", 1.0)):     # L はキャラの左（+x）
            made, entry, arrays = eye_shapes(head, settings, side, sign)
            shapes.update(made)
            report["eyes"][side] = entry
            debug.update(arrays)
    elif "brows" in settings:
        raise ValueError("brows は eyes（と eyes.brow）といっしょに使ってください（眉の部品は目の窓から探す）")
    if "mouth" in settings:
        shapes["mouth_smile"], report["mouth"] = mouth_smile(head, settings)
    report["shapes"] = {}
    for name, delta in shapes.items():
        moved = np.linalg.norm(delta, axis=1)
        report["shapes"][name] = {"moved_vertices": int((moved > 1e-6).sum()), "max_mm": round(float(moved.max() * 1000), 2)}
    return shapes, report, debug
