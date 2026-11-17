"""目の作り直しの計算（numpy だけ。Blender の中の台本と、Blender の外の工程の両方で使う）。

目の形の値（model3d-eye-geometry/0.1）: 片目ごとに、目の板を乗せた球（中心・半径・板の向き）、瞳の中心（正面から見た x・z）と楕円の半径、
目の開きの上下の縁（正面から見た x ごとの上の縁と下の縁の z）、目のまわりの窓、瞳の頂点と、見開きで動かすまぶたの頂点と、目の板の頂点の番号
（目の板はあれば。確かめの絵で塗り分ける）。
位置は世界の座標（m。x はキャラの左が +、y は手前が −、z は上が +）。目の板と瞳を入れる工程（これから持ち帰る）がこの値を出し、
目の骨・まばたきのキー・新しいキー・VRM の視線の工程が読む。リポジトリ外の 3 作目の VRM のダンスのあとの試しの値の形と同じ中身。
"""
import math

import numpy as np

SCHEMA = "model3d-eye-geometry/0.1"
SIDES = ("R", "L")


class GeometryError(ValueError):
    """目の形の値の誤り。"""


def _vector(value, where, length=3):
    if (not isinstance(value, list) or len(value) != length
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in value)):
        raise GeometryError(f"{where} は数 {length} つの並びにしてください: {value!r}")
    return [float(v) for v in value]


def _indices(value, where, count=None):
    if not isinstance(value, list) or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in value):
        raise GeometryError(f"{where} は頂点の番号（0 以上の整数）の並びにしてください")
    if len(set(value)) != len(value):
        raise GeometryError(f"{where} に同じ頂点が 2 回あります")
    if count is not None and value and max(value) >= count:
        raise GeometryError(f"{where} の番号が頂点の数（{count}）を超えています")
    return list(value)


def validate(geometry, sides=SIDES, vertex_count=None):
    """目の形の値を確かめて返す（誤りは GeometryError）。sides の目だけを見る。"""
    if not isinstance(geometry, dict) or geometry.get("schema_version") != SCHEMA:
        raise GeometryError(f"目の形の値は schema_version: {SCHEMA} の JSON にしてください")
    if not isinstance(geometry.get("object"), str) or not geometry["object"]:
        raise GeometryError("目の形の値の object（頭のメッシュの物体の名前）が要ります")
    eyes = geometry.get("eyes")
    if not isinstance(eyes, dict) or not all(side in eyes for side in sides):
        raise GeometryError(f"目の形の値の eyes に {list(sides)} が要ります")
    for side in sides:
        eye, where = eyes[side], f"eyes.{side}"
        if not isinstance(eye, dict):
            raise GeometryError(f"{where} は表にしてください")
        sphere = eye.get("sphere") or {}
        _vector(sphere.get("centre"), f"{where}.sphere.centre")
        normal = _vector(sphere.get("normal"), f"{where}.sphere.normal")
        if abs(math.sqrt(sum(v * v for v in normal)) - 1.0) > 1e-6:
            raise GeometryError(f"{where}.sphere.normal は長さ 1 の向きにしてください")
        radius = sphere.get("radius")
        if isinstance(radius, bool) or not isinstance(radius, (int, float)) or not radius > 0:
            raise GeometryError(f"{where}.sphere.radius は正の数（m）にしてください: {radius!r}")
        _vector(eye.get("pupil"), f"{where}.pupil", 2)
        iris = eye.get("iris") or {}
        for key in ("a", "b"):
            if isinstance(iris.get(key), bool) or not isinstance(iris.get(key), (int, float)) or not iris[key] > 0:
                raise GeometryError(f"{where}.iris.{key}（瞳の楕円の半径 m）が要ります")
        rim = eye.get("rim") or {}
        xs, upper, lower = (rim.get(key) for key in ("x", "upper", "lower"))
        if not all(isinstance(v, list) and len(v) >= 2 for v in (xs, upper, lower)) or not len(xs) == len(upper) == len(lower):
            raise GeometryError(f"{where}.rim は x・upper・lower（同じ長さの数の並び）にしてください")
        if any(b <= a for a, b in zip(xs, xs[1:])):
            raise GeometryError(f"{where}.rim.x は小さい順にしてください")
        window = _vector(eye.get("window"), f"{where}.window", 4)
        if window[0] >= window[1] or window[2] >= window[3]:
            raise GeometryError(f"{where}.window は [x0, x1, z0, z1]（x0 < x1、z0 < z1）にしてください")
        _indices(eye.get("iris_vertices"), f"{where}.iris_vertices", vertex_count)
        for key in ("lid_vertices", "plate_vertices"):
            if eye.get(key) is not None:
                _indices(eye[key], f"{where}.{key}", vertex_count)
    return geometry


def bone_ends(centre, normal, radius):
    """目の骨の根もと（球の中心）と先（中心 + 板の向き × 半径。ちょうど目の板に届く）。世界の座標。"""
    centre, normal = np.asarray(centre, dtype=np.float64), np.asarray(normal, dtype=np.float64)
    return centre, centre + normal * radius


def to_armature(point, armature_matrix):
    """世界の座標の点を、アーマチュアの中の座標へ（試しと同じ解き方: 行列の 3×3 で解く）。"""
    a = np.asarray(armature_matrix, dtype=np.float64)
    return np.linalg.solve(a[:3, :3], np.asarray(point, dtype=np.float64) - a[:3, 3])


def up_in_armature(armature_matrix):
    """世界の上（+Z）を、アーマチュアの中の向きへ（骨のロールをそろえる向き。アーマチュアの物体が回っていても、世界の上にそろう）。
    アーマチュアの置き場所の 3×3 が単位行列なら (0, 0, 1) のまま（試しと同じ）。"""
    a = np.asarray(armature_matrix, dtype=np.float64)[:3, :3]
    if np.array_equal(a, np.eye(3)):
        return np.array([0.0, 0.0, 1.0])
    up = np.linalg.solve(a, np.array([0.0, 0.0, 1.0]))
    return up / np.linalg.norm(up)


def world_length(armature_matrix, head, tail):
    """アーマチュアの中の 2 点（骨の根もとと先）の、世界での長さ（m。アーマチュアの物体の大きさを入れる）。"""
    a = np.asarray(armature_matrix, dtype=np.float64)[:3, :3]
    return float(np.linalg.norm(a @ (np.asarray(tail, dtype=np.float64) - np.asarray(head, dtype=np.float64))))


# ---------------------------------------------------------------- まばたきのキーの直しと新しいキー（試しと同じ計算。64 ビット）
def smoothstep(t):
    t = np.clip(t, 0.0, 1.0)
    return t * t * (3 - 2 * t)


def sphere_y(centre, radius, x, z):
    """球の手前の面の y（正面から見た x, z での奥行き）。"""
    s = radius ** 2 - (x - centre[0]) ** 2 - (z - centre[2]) ** 2
    return centre[1] - np.sqrt(np.maximum(s, 0.0))


class Frame:
    """頭の物体の置き場所（4×4）。物体の中の座標と世界の座標の行き来（試しと同じ式）。行列は Blender が持つ 32 ビットのまま使い、
    逆の行列も 32 ビットで求める（試しと同じ。64 ビットで求めると、キーの値の最後の桁が試しとずれる）。32 ビットの点は 32 ビットで、
    64 ビットの点は 64 ビットで計算される。"""

    def __init__(self, matrix):
        m = np.asarray(matrix, dtype=np.float32)
        self.a, self.t = m[:3, :3], m[:3, 3]
        self.inverse = np.linalg.inv(self.a)

    def world(self, p):
        return np.asarray(p) @ self.a.T + self.t

    def local(self, p):
        return (np.asarray(p) - self.t) @ self.inverse.T


def fix_blink(base, key, frame, eye, cfg, lower_close, skip=()):
    """まばたきのキー 1 つ（物体の中の座標 (N, 3)）を直した値を返す（試しと同じ手順で、頂点ごとに）:
    - 窓の中で、目の球の中心より板の側（板の向き normal の側。顔の前）にあって、キーで動く頂点だけを見る（動かない頂点はそのまま）。
      skip（瞳の頂点）は見ない。試しは世界の y < 0 で前を選んでいた。頭が動いても同じ頂点を選ぶよう、目の球を基準にする。
    - lower_close（まばたき。笑顔の閉じ目は False）なら、下りてくる上まぶたを、下まぶたのふちより close_below 下まで届くように伸ばす
      （元のキーは開きの close_reach の所まで閉じる。伸ばす倍率は close_max_scale まで）。
    - 下りてきたまぶた（もとは板より lid_front より手前にある頂点）が板より lid_margin 手前に居るように、球の面へ押し出す。
    base と key は Blender が持つ 32 ビットの配列で渡す（試しと同じく、頂点ごとの計算を 32 ビットで始める）。
    返す: (直したキー, 数の記録)。"""
    centre, radius = np.asarray(eye["sphere"]["centre"], dtype=np.float64), eye["sphere"]["radius"]
    facing = np.asarray(eye["sphere"]["normal"], dtype=np.float64)          # 板の向き（球の中心から板へ。顔の前）
    r_out = radius + cfg["lid_margin"]
    x0, x1, z0, z1 = eye["window"]
    xcol, upper, lower = eye["rim"]["x"], eye["rim"]["upper"], eye["rim"]["lower"]
    out = np.array(key, dtype=np.float64)
    skip = set(int(i) for i in skip)
    count, worst, scaled = 0, 0.0, 0
    for v in range(len(base)):
        if v in skip:
            continue
        p0 = frame.world(base[v])
        if not (x0 < p0[0] < x1 and z0 < p0[2] < z1 and float(np.dot(p0 - centre, facing)) > 0):
            continue
        p1 = frame.world(key[v])
        if np.abs(p1 - p0).max() < 1e-7:
            continue
        if lower_close and p1[2] < p0[2] - 1e-6:
            h = float(np.interp(p0[0], xcol, upper) - np.interp(p0[0], xcol, lower))
            k = float(np.clip((h + cfg["close_below"]) / max(cfg["close_reach"] * h, 1e-6), 1.0, cfg["close_max_scale"]))
            p1 = p0 + (p1 - p0) * k
            scaled += 1
        d0, d1 = np.linalg.norm(p0 - centre), np.linalg.norm(p1 - centre)
        if d0 > radius + cfg["lid_front"] and d1 < r_out:
            worst = max(worst, r_out - d1)
            p1 = centre + (p1 - centre) / d1 * r_out
            count += 1
        out[v] = frame.local(p1)
    return out, {"scaled_vertices": scaled, "pushed_in_front": count, "pushed_max_mm": round(worst * 1000, 2)}


def iris_key(base_w, eye, sx, sz, lift):
    """瞳の変形のキー（世界の座標 (N, 3)）: 瞳の頂点を、瞳の中心のまわりに横 sx・たて sz 倍して、球（半径 + lift）の面に乗せる。"""
    centre, radius = np.asarray(eye["sphere"]["centre"], dtype=np.float64), eye["sphere"]["radius"]
    c = eye["pupil"]
    iris = np.asarray(eye["iris_vertices"], dtype=np.int64)
    p = base_w.copy()
    x = c[0] + (base_w[iris, 0] - c[0]) * sx
    z = c[1] + (base_w[iris, 2] - c[1]) * sz
    p[iris, 0], p[iris, 2] = x, z
    p[iris, 1] = sphere_y(centre, radius + lift, x, z)
    return p


def wide_key(base_w, eye, cfg):
    """見開きのキー（世界の座標 (N, 3)）: まぶたの頂点（lid_vertices）を、上まぶたは上へ wide_up、下まぶたは下へ wide_down（目の幅の真ん中で
    いちばん大きく、目頭と目尻へ sin の 0.7 乗で減らす）。縁から離れると減らし（上 upper_fall、下 lower_fall）、目の幅の外は side_fade で 0 へ。"""
    v = np.asarray(eye["lid_vertices"], dtype=np.int64)
    xcol, upper, lower = eye["rim"]["x"], eye["rim"]["upper"], eye["rim"]["lower"]
    xa, xb = xcol[0], xcol[-1]
    x, z = base_w[v, 0], base_w[v, 2]
    ux, lx = np.interp(x, xcol, upper), np.interp(x, xcol, lower)
    s = np.clip((x - xa) / (xb - xa), 0, 1)
    fade = cfg["side_fade"]
    wx = np.where(x < xa, smoothstep(1 - (xa - x) / fade), np.where(x > xb, smoothstep(1 - (x - xb) / fade), 1.0))
    bump = np.sin(np.pi * s) ** cfg["bump_power"]
    up = z >= 0.5 * (ux + lx)
    start, width = cfg["upper_fall"]
    fall_up = np.where(z >= ux, 1 - smoothstep((z - ux - start) / width), 1.0)
    fall_low = np.where(z <= lx, 1 - smoothstep((lx - z) / cfg["lower_fall"]), 1.0)
    dz = np.where(up, cfg["wide_up"] * bump * fall_up, -cfg["wide_down"] * bump * fall_low) * wx
    p = base_w.copy()
    p[v, 2] += dz
    return p
