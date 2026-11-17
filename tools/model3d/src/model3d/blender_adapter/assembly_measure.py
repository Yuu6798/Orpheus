"""組み立てたモデル（体・頭・髪が別のメッシュ）の頭身を測り、頭の大きさと位置の提案を出す（numpy だけ。Blender の中の台本
assembly.py からも、外のテストからも読む。check.assembly）。数を出すだけで、どう置くかは決めない。
3 作目の VRM のダンスで、リポジトリの外の台本（Blender の中の numpy）でしていた測り方を移した（数は台本と同じ）。

物差しは U = 足の裏からくびれ（胴がいちばん狭い高さ）まで（check.body_image・check.candidate と同じ決め方）。座標は世界の座標
（m、Z が上、正面は −Y）。部品はどれも (V, T)（V は (N, 3) の頂点、T は (M, 3) の三角形の頂点の番号）。

- 体: 床・いちばん上・腕の先の高さ（T ポーズ）・くびれと、いちばん上から BODY_NECK の深さの首の切り口（中心を含むかたまり）の幅・厚み・中心。
- 頭: 顔の真ん中（頭のメッシュのいちばん下の 4 % の頂点の横の範囲の真ん中）の縦の切り口で、鼻先（下 2/3 でいちばん前）とあご
  （鼻先より頭の高さの 0.1 倍より下の点で、前下 45° にいちばん出ている点）。鼻先の高さの顔の幅。前の輪郭の並び（profile）。
- 髪: 顔の真ん中の縦の切り口でいちばん高い点（横にある角や飾りは入らない）。
"""
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト・check.assembly）
    from . import slices
except ImportError:   # Blender が台本として動かすとき（ほかの部品と同じく、自分のフォルダを sys.path に足してから読む）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import slices

BODY_NECK = (0.003, 0.01, 0.02, 0.03, 0.04, 0.06, 0.08)   # 首の切り口を測る深さ（体のいちばん上から、m）
GAP = 0.002                    # 横に重なる輪をまとめるすき間（S に対する割合。check.candidate と同じ）
FACE_LOW = 0.04                # 顔の真ん中を決める、頭のメッシュのいちばん下からの高さ（頭の高さに対する割合）
NOSE_BELOW = 0.67              # 鼻先を探す高さ（頭のメッシュの下から、頭の高さに対する割合）
CHIN_BELOW_NOSE = 0.1          # あごは、鼻先よりこれだけ（頭の高さに対する割合）下の点から探す
PROFILE_BINS = 300             # 前の輪郭を、頭の高さをこの数に分けた高さごとに取る
MARGIN = 1.06                  # 全身の絵の、高さに対する写す範囲
WHOLE = (1000, 1500)           # 全身の絵の画素（幅, 高さ）
BUST = 1100                    # 胸から上の絵の画素（正方形）
DIRECTIONS = {"front": (0, -1, 0), "side": (1, 0, 0), "back": (0, 1, 0), "oblique": (0.6, -0.8, 0)}


def section(V, T, axis, value):
    """平面（座標 axis = value）の切り口の線分（(n, 2, 3)）。"""
    side = V[T][:, :, axis] > value
    segs = []
    for tri in T[(side.sum(axis=1) % 3) != 0]:
        pts = []
        for i, j in ((0, 1), (1, 2), (2, 0)):
            a, b = V[tri[i]], V[tri[j]]
            if (a[axis] > value) != (b[axis] > value):
                pts.append(a + (b - a) * ((value - a[axis]) / (b[axis] - a[axis])))
        if len(pts) == 2:
            segs.append(pts)
    return np.array(segs) if segs else np.zeros((0, 2, 3))


def loops_at(V, T, z):
    """高さ z の切り口の、つながった輪ごとの (x0, x1, y0, y1, 点の数)（小さい順）。"""
    return sorted((float(p[:, 0].min()), float(p[:, 0].max()), float(p[:, 1].min()), float(p[:, 1].max()), len(p))
                  for p in slices.section_loops(V, T, z))


def _body(V, T):
    if len(V) == 0 or len(T) == 0:
        raise ValueError("体にメッシュがありません")
    z0, zt, cx, zs, S = slices.frame(V)
    if not S > 0:
        raise ValueError("体の腕の先（中心からいちばん遠い頂点たち）が床より上にありません（T ポーズの体か確かめてください）")
    best = slices.waist(V, T, z0, S, cx, GAP * S)
    if best is None:
        raise ValueError("体のくびれが見つかりません（腕の高さの 0.80〜0.92 倍に、体の中心を含む切り口がありません）")
    waist_z = z0 + best[0] * S
    body = {"floor_z": z0, "top_z": zt, "centre_x": cx, "arm_z": zs, "waist_z": waist_z, "waist_width": best[1],
            "waist_depth": best[2], "U": waist_z - z0}
    neck = []
    for dz in BODY_NECK:
        box = slices.torso_box(V, T, zt - dz, cx, GAP * S)
        if box:
            neck.append({"below_top": dz, "width": box[1] - box[0], "depth": box[3] - box[2], "cx": (box[0] + box[1]) / 2,
                         "cy": (box[2] + box[3]) / 2})
    body["neck"] = neck
    return body


def _head(V, T):
    if len(V) == 0 or len(T) == 0:
        raise ValueError("頭にメッシュがありません")
    hz0, hz1 = float(V[:, 2].min()), float(V[:, 2].max())
    if not hz1 > hz0:
        raise ValueError("頭の高さが 0 です")
    low = V[V[:, 2] < hz0 + FACE_LOW * (hz1 - hz0)]
    fx = float((low[:, 0].min() + low[:, 0].max()) / 2)
    segs = section(V, T, 0, fx)
    if not len(segs):
        raise ValueError(f"頭の顔の真ん中（x = {fx:.4f}）の縦の切り口がありません")
    pts = np.concatenate([segs[:, 0], segs[:, 1], (segs[:, 0] + segs[:, 1]) / 2])[:, 1:]        # (y, z)
    step = (hz1 - hz0) / PROFILE_BINS
    zi = np.floor((pts[:, 1] - hz0) / step).astype(int)
    prof = []
    for k in np.unique(zi):
        sel = pts[zi == k]
        prof.append((float(sel[:, 1].mean()), float(sel[:, 0].min())))
    prof = np.array(prof)                                                                        # (z, いちばん前の y)
    face = prof[prof[:, 0] < hz0 + NOSE_BELOW * (hz1 - hz0)]
    if not len(face):
        raise ValueError("頭の下 2/3 に、顔の真ん中の切り口がありません（鼻先を探せません）")
    nose = face[np.argmin(face[:, 1])]
    below = pts[pts[:, 1] < nose[0] - CHIN_BELOW_NOSE * (hz1 - hz0)]
    if not len(below):
        raise ValueError("鼻先より下に、顔の真ん中の切り口がありません（あごを探せません）")
    chin = below[np.argmax(-below[:, 0] - below[:, 1])]
    head = {"bottom_z": hz0, "top_z": hz1, "face_x": fx, "nose": {"z": float(nose[0]), "y": float(nose[1])},
            "chin": {"z": float(chin[1]), "y": float(chin[0])},
            "x": [float(V[:, 0].min()), float(V[:, 0].max())], "y": [float(V[:, 1].min()), float(V[:, 1].max())],
            "profile": [[round(float(z), 5), round(float(y), 5)] for z, y in prof[::3]]}
    rings = loops_at(V, T, float(nose[0]))
    if rings:
        widest = max(rings, key=lambda r: r[1] - r[0])
        head["width_at_nose"] = widest[1] - widest[0]
    return head


def _hair(V, T, fx):
    if len(V) == 0 or len(T) == 0:
        raise ValueError("髪にメッシュがありません")
    segs = section(V, T, 0, fx)
    if not len(segs):
        raise ValueError(f"髪の、顔の真ん中（x = {fx:.4f}）の縦の切り口がありません")
    hp = np.concatenate([segs[:, 0], segs[:, 1]])
    top = hp[np.argmax(hp[:, 2])]
    return {"top_centre_z": float(top[2]), "top_centre_y": float(top[1]), "max_z": float(V[:, 2].max()), "min_z": float(V[:, 2].min()),
            "x": [float(V[:, 0].min()), float(V[:, 0].max())], "y": [float(V[:, 1].min()), float(V[:, 1].max())]}


def measure(body, head, hair):
    """体・頭・髪（どれも (V, T)）を測る。頭身は（髪のてっぺん − 床）÷（髪のてっぺん − あご）。測れなければ ValueError。"""
    m = {"body": _body(*body), "head": _head(*head)}
    m["hair"] = _hair(*hair, m["head"]["face_x"])
    z0, zt, U = m["body"]["floor_z"], m["body"]["top_z"], m["body"]["U"]
    top_z, chin_z = m["hair"]["top_centre_z"], m["head"]["chin"]["z"]
    if not top_z > chin_z:
        raise ValueError(f"髪のてっぺん（{top_z:.4f}）があご（{chin_z:.4f}）より上にありません")
    m["ratio"] = {"height_to_hair_top": top_z - z0, "head_length": top_z - chin_z, "heads": (top_z - z0) / (top_z - chin_z),
                  "top_U": (top_z - z0) / U, "chin_U": (chin_z - z0) / U, "chin_above_body_top": chin_z - zt,
                  "head_bottom_below_body_top": zt - m["head"]["bottom_z"]}
    m["reach"] = {"x": float(max(np.abs(V[:, 0]).max() for V, _ in (body, head, hair))),       # 原点からいちばん遠い横（x）と前後（y）
                  "y": float(max(np.abs(V[:, 1]).max() for V, _ in (body, head, hair)))}
    return m


def proposal(now, pivot, reference=None, anchor=None, place=None):
    """頭と髪を pivot を中心に scale 倍し、pivot を to へ動かす置き方。

    reference（元絵の髪のてっぺんとあごの高さの、U に対する値 top_U・chin_U）からは、元絵の比率になる頭の長さとあごの高さ。
    anchor（体の側の目印の高さ z、元絵でのあごから目印までの間隔 gap（頭の長さを 1 として）、目標の頭身 heads）からは、あごを
    目印の gap 上に置いて頭身を守る頭の長さとあごの高さ（髪のてっぺん − 床 = heads × L、あご = 目印 + gap × L から
    L =（目印 − 床）÷（heads − 1 − gap））。両方あれば anchor を使う。place（scale・z・y）は手で決めた値: scale は倍率、z は
    pivot の新しい高さ（どちらも提案の代わり）、y は前後に動かす量（前がマイナス）。"""
    place = place or {}
    z0, U = now["body"]["floor_z"], now["body"]["U"]
    target_len = target_chin = how = None
    if reference:
        target_len = (reference["top_U"] - reference["chin_U"]) * U
        target_chin = z0 + reference["chin_U"] * U
        how = "reference ratio (U)"
    if anchor:
        target_len = (anchor["z"] - z0) / (anchor["heads"] - 1 - anchor["gap"])
        target_chin = anchor["z"] + anchor["gap"] * target_len
        how = "anchor on the body + heads"
    if target_len is not None and not target_len > 0:
        raise ValueError(f"目標の頭の長さが正になりません（{target_len}）")
    if target_len is None and not ("scale" in place and "z" in place):
        raise ValueError("reference か anchor が要ります（place に scale と z の両方があるときは要りません）")
    k = place["scale"] if "scale" in place else target_len / now["ratio"]["head_length"]
    z_new = place["z"] if "z" in place else target_chin - k * (now["head"]["chin"]["z"] - pivot[2])
    if "scale" in place:
        how = "manual"
    return {"from": how, "scale": k, "pivot": [float(v) for v in pivot], "to": [float(pivot[0]), float(pivot[1] + place.get("y", 0.0)), z_new],
            "target_head_length": target_len, "target_chin_z": target_chin}


def view_plan(m, tag, bust_size, bust_drop):
    """描く絵の並び（名前, 向き, 狙う点, 写す幅, 画素, 描き方）。全身は床から上のいちばん高い所までの高さ H の 1.06 倍（縦長の絵なので、
    写す幅は縦の範囲。横の範囲はその 横 ÷ 縦 倍なので、原点から横（正面・後ろは x、横からは y）にいちばん遠い所の 2 倍の 1.06 倍が入らなければ、
    入るまで広げる。T ポーズの腕の先が切れないように）。胸から上は頭の真ん中（髪のてっぺんとあごの間）から bust_drop 下を中心に bust_size の幅
    （m）。描き方 tex は色の絵そのまま、clay は粘土。"""
    z0, top = m["body"]["floor_z"], max(m["hair"]["max_z"], m["head"]["top_z"])
    H = top - z0
    hc = (m["hair"]["top_centre_z"] + m["head"]["chin"]["z"]) / 2
    whole, bust = (0.0, 0.0, z0 + H / 2), (0.0, 0.0, hc - bust_drop)
    aspect = WHOLE[0] / WHOLE[1]
    across = {axis: max(H * MARGIN, 2 * m["reach"][axis] * MARGIN / aspect) for axis in ("x", "y")}
    plan = []
    for mode in ("tex", "clay"):
        plan += [(f"{tag}_front_{mode}", DIRECTIONS["front"], whole, across["x"], WHOLE, mode),
                 (f"{tag}_side_{mode}", DIRECTIONS["side"], whole, across["y"], WHOLE, mode),
                 (f"{tag}_bust_front_{mode}", DIRECTIONS["front"], bust, bust_size, (BUST, BUST), mode),
                 (f"{tag}_bust_side_{mode}", DIRECTIONS["side"], bust, bust_size, (BUST, BUST), mode)]
    return plan + [(f"{tag}_back_tex", DIRECTIONS["back"], whole, across["x"], WHOLE, "tex"),
                   (f"{tag}_bust_oblique_tex", DIRECTIONS["oblique"], bust, bust_size, (BUST, BUST), "tex")]


def landmarks(m):
    """絵に線を引く目印の高さ（名前 → z）。"""
    return {"hair top": m["hair"]["top_centre_z"], "chin": m["head"]["chin"]["z"], "nose": m["head"]["nose"]["z"],
            "body top": m["body"]["top_z"], "head bottom": m["head"]["bottom_z"], "waist": m["body"]["waist_z"],
            "floor": m["body"]["floor_z"]}


def view_row(view, z):
    """描いた絵（view: centre・res・ppm）の、高さ z の行（画素。上が 0）。"""
    return view["res"][1] / 2 - (z - view["centre"][2]) * view["ppm"]
