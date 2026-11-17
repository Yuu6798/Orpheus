"""メッシュを水平な面で切った切り口を、面のつながりでたどって輪にし、体の太さを測る（numpy だけ。docs/model3d.md の
「キャラを作る段の確認地点」）。骨の無い 3D の候補（頭の無い T ポーズの体）を、選ぶ前に同じ物差しで測るため。数を出すだけで、
良し悪しは決めない。3 作目の VRM のダンスで、リポジトリの外の台本（Blender の中の numpy）でしていた測り方を移した（数は台本と同じ）。

座標は世界の座標（m、Z が上）。V は (N, 3) の頂点、T は (M, 3) の三角形の頂点の番号。
"""
import numpy as np

from .blender_adapter import slices
from .blender_adapter.slices import clusters, section_loops
from .io import ToolError

KNEE = (0.34, 0.42)                  # ひざ（いちばん細い所）を探す高さ（S に対する割合）
CALF = (0.26, 0.33)                  # ふくらはぎ（いちばん太い所）を探す高さ（S に対する割合）
LEG_MIN = 0.02                       # 脚とみなす切り口の幅の下限（S に対する割合）
PROFILE = (0.45, 0.96, 0.05)         # 胴の幅の並びを出す高さ（S に対する割合）
# くびれを探す高さ（WAIST）と腕の先の決め方（ARM_REACH）は blender_adapter/slices.py（Blender の中の台本からも読む部品）


def frame(V):
    """床の高さ z0・いちばん上 z1・体の中心 cx（下半分の頂点の x の中央値）・腕の先の高さ zs・物差し S = zs − z0。"""
    try:
        return slices.frame(V)
    except ValueError as error:
        raise ToolError(str(error)) from None


def measure_candidate(V, T, gap=0.002, thigh_at=None):
    """骨の無い体の候補（T ポーズ）の太さ。物差し S は床から腕の先までの高さ。胴は体の中心を含むかたまり、脚は中心を含まない
    左右のかたまり（幅が S の 0.02 倍を超える広い 2 つ）。値は S に対する割合（by_U は、足の裏からくびれまでの高さ U に対する割合で、
    元絵や体の絵の measure_legs と同じ決め方）。太ももは thigh_at（S に対する割合）の高さで測る（既定は無く、無ければ測らない）。"""
    if len(V) == 0 or len(T) == 0:
        raise ToolError("候補にメッシュがありません")
    z0, z1, cx, zs, S = frame(V)
    if not S > 0:
        raise ToolError("腕の先（体の中心からいちばん遠い頂点たち）が床より上にありません（T ポーズの体か確かめてください）")

    def torso(z):
        box = slices.torso_box(V, T, z, cx, gap * S)
        return None if box is None else (box[1] - box[0], box[3] - box[2])

    def legs(z):
        side = [(x1 - x0, y1 - y0) for x0, x1, y0, y1 in clusters(V, T, z, gap * S) if not (x0 <= cx <= x1) and (x1 - x0) > LEG_MIN * S]
        if len(side) < 2:
            return None
        side = sorted(side, reverse=True)[:2]
        return float(np.mean([w for w, _ in side])), float(np.mean([d for _, d in side]))

    result = {"verts": int(len(V)), "triangles": int(len(T)), "floor_z": z0, "top_z": z1, "centre_x": cx, "arm_z": zs, "S": S,
              "note": "値は S（床から腕までの高さ）に対する割合", "rows": {}}
    best = slices.waist(V, T, z0, S, cx, gap * S)
    if best:
        result["rows"]["waist"] = {"at": best[0], "width": best[1] / S, "depth": best[2] / S}
    t = legs(z0 + thigh_at * S) if thigh_at is not None else None
    if t:
        result["rows"]["thigh"] = {"at": thigh_at, "width": t[0] / S, "depth": t[1] / S}
    for name, (lo, hi), pick in (("knee", KNEE, min), ("calf", CALF, max)):
        found = [(legs(z0 + f * S), float(f)) for f in np.arange(lo, hi + 0.001, 0.005)]
        found = [(v[0], v[1], f) for v, f in found if v]
        if found:
            w, d, f = pick(found)
            result["rows"][name] = {"at": f, "width": w / S, "depth": d / S}
    if best:
        U = best[0] * S
        profile = []
        for h in np.arange(0.04, 0.80, 0.005):
            v = legs(z0 + h * U)
            if v:
                profile.append((float(h), v[0] / U, v[1] / U))
        continuous = []
        for i, row in enumerate(profile):                  # 足の裏から続けて脚が見えている高さだけ
            if i and row[0] - profile[i - 1][0] > 0.02:
                break
            continuous.append(row)
        P = np.array(continuous)
        by_U = {"U": U, "waist": {"width": best[1] / U, "depth": best[2] / U}}
        if len(P) > 10:
            by_U["hem_h"] = float(P[-1, 0])
        if len(P) > 10 and ((P[:, 0] >= 0.10) & (P[:, 0] <= 0.30)).any():     # 足首を探す高さまで脚が見えているときだけ
            def pick_row(lo, hi, choose):
                sel = np.where((P[:, 0] >= lo) & (P[:, 0] <= hi))[0]
                return P[sel[choose(P[sel, 1])]]
            ankle = pick_row(0.10, 0.30, np.argmin)
            calf = pick_row(ankle[0], 0.50, np.argmax)
            sel = np.where((P[:, 0] > calf[0]) & (P[:, 0] <= 0.62))[0]
            if len(sel):
                kmin = P[sel, 1].min()
                ksel = sel[P[sel, 1] <= kmin * 1.03]
                knee_h = float(P[ksel, 0].mean())
                knee = P[ksel[np.argmin(np.abs(P[ksel, 0] - knee_h))]]
                rows = (("ankle", ankle), ("calf", calf), ("knee", knee))
            else:
                rows = (("ankle", ankle), ("calf", calf))
            for name, row in rows:
                by_U[name] = {"at": float(row[0]), "width": float(row[1]), "depth": float(row[2])}
            if P[-1, 0] >= 0.65:
                i = int(np.argmin(np.abs(P[:, 0] - 0.65)))
                by_U["thigh"] = {"at": 0.65, "width": float(P[i, 1]), "depth": float(P[i, 2])}
        result["by_U"] = by_U
    result["profile"] = [{"at": float(f), "torso_width": (None if torso(z0 + f * S) is None else torso(z0 + f * S)[0] / S)}
                         for f in np.arange(*PROFILE)]
    return result


def slice_rows(V, T, fractions, min_points=6):
    """高さの割合（いちばん下 0 〜 いちばん上 1）ごとの切り口の輪: [[x0, x1, 前後の幅, 点の数], …]（x は箱の中心から、長さは
    全高で割った値。min_points より点の少ない輪は除く。x0 の小さい順）。"""
    lo, hi = V.min(axis=0), V.max(axis=0)
    centre, size = (lo + hi) / 2, hi - lo
    out = {}
    for frac in fractions:
        rows = []
        for points in section_loops(V, T, lo[2] + frac * size[2]):
            if len(points) < min_points:
                continue
            rows.append([float((points[:, 0].min() - centre[0]) / size[2]), float((points[:, 0].max() - centre[0]) / size[2]),
                         float(np.ptp(points[:, 1]) / size[2]), int(len(points))])
        out["%.3f" % frac] = sorted(rows)
    return out


def check_picture(V, result, path, width=700):
    """正面から見た頂点（灰色の点）と、測った高さの線（赤。腕の高さは青）の確認用の絵（足が下）。"""
    from PIL import Image
    z0, z1, cx, S = result["floor_z"], result["top_z"], result["centre_x"], result["S"]
    scale = (width - 40) / max(V[:, 0].max() - V[:, 0].min(), z1 - z0)
    height = int((z1 - z0) * scale) + 40
    image = np.full((height, width, 3), 255, dtype=np.uint8)
    px = np.clip(((V[:, 0] - cx) * scale + width / 2).astype(int), 0, width - 1)
    py = np.clip(((V[:, 2] - z0) * scale + 20).astype(int), 0, height - 1)
    image[height - 1 - py, px] = 64
    marks = []
    rows, by_U = result["rows"], result.get("by_U") or {}
    for name in ("waist", "thigh", "knee", "calf"):
        if name in rows:
            marks.append(rows[name]["at"])
    waist_at = rows.get("waist", {}).get("at")
    for name in ("ankle", "calf", "knee", "thigh"):
        if name in by_U and waist_at is not None:
            marks.append(by_U[name]["at"] * waist_at)
    for at, colour in [(at, (255, 26, 26)) for at in marks] + [(1.0, (26, 77, 255))]:
        y = int(at * S * scale + 20)
        for row in (y, y + 1):
            if 0 <= row < height:
                image[height - 1 - row] = colour
    Image.fromarray(image).save(path, format="PNG")
