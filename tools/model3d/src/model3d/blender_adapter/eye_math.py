"""目の作り直しの計算（numpy だけ。Blender の中の台本と、Blender の外の工程の両方で使う）。

目の形の値（model3d-eye-geometry/0.1）: 片目ごとに、目の板を乗せた球（中心・半径・板の向き）、瞳の中心（正面から見た x・z）と楕円の半径、
目の開きの上下の縁（正面から見た x ごとの上の縁と下の縁の z）、目のまわりの窓、瞳の頂点と、見開きで動かすまぶたの頂点の番号。
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
        if eye.get("lid_vertices") is not None:
            _indices(eye["lid_vertices"], f"{where}.lid_vertices", vertex_count)
    return geometry


def bone_ends(centre, normal, radius):
    """目の骨の根もと（球の中心）と先（中心 + 板の向き × 半径。ちょうど目の板に届く）。世界の座標。"""
    centre, normal = np.asarray(centre, dtype=np.float64), np.asarray(normal, dtype=np.float64)
    return centre, centre + normal * radius


def to_armature(point, armature_matrix):
    """世界の座標の点を、アーマチュアの中の座標へ（試しと同じ解き方: 行列の 3×3 で解く）。"""
    a = np.asarray(armature_matrix, dtype=np.float64)
    return np.linalg.solve(a[:3, :3], np.asarray(point, dtype=np.float64) - a[:3, 3])
