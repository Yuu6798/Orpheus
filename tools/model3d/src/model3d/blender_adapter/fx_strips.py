"""光る帯と玉の四角形を作る（カメラを向いた帯・床に寝た帯・カメラを向いた四角形）。numpy だけを使う（render.show のフックが
Blender の中で `import fx_strips` で読む。CI でも試せる）。

リポジトリ外の 4 作目の 3D のダンスの演出の台本で、出来事の形（線・輪・粒・床の枝分かれ・光の玉）を毎コマ 1 つのメッシュにした
帯の作り方を、同じ式・同じ頂点の並びで移した。台本は Blender の mathutils（単精度）で計算していたので、ここ（倍精度）とは
1e-6 m ほど違う。

    quads = Quads(camera_position)
    quads.strip(points, widths, values)        点の列 → 四角形の列（幅の向きは 接線 × 視線。flat=True なら 接線 × 上）
    quads.dot(centre, size, value)             カメラを向いた四角形 1 つ（光の玉・止まった粒）
    co, uv, value = quads.arrays()             (N, 4, 3)・(N, 4, 2)・(N, 4)。Blender の側はメッシュにするだけ

四角形の頂点は [p − s, p + s, q + t, q − t]（s・t は点 p・q の幅の半分の向き）。UV の u は幅の向き（0 = −s の縁、1 = +s の縁）、
v は帯の向き（0 = p、1 = q）。足し算で光る材質は u で、真ん中を白く、縁を色つきにする。value は頂点ごとの強さ（材質が掛ける）。
帯の幅の向きは、点ごとに 接線（前後の点の差）× 視線（点からカメラへ）。どちらかが 0 か平行なら 接線 × x。
"""
import numpy as np

UP = np.array([0.0, 0.0, 1.0])
X = np.array([1.0, 0.0, 0.0])
Y = np.array([0.0, 1.0, 0.0])
QUAD_UV = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
TINY = 1e-9
ZERO = 1e-12        # これより短い向きは 0 とみなす（長さ 0 の帯は、幅も 0 の四角形になる）


def unit(v):
    """長さ 1 にした向き（長さがほぼ 0 なら 0 のまま）。"""
    v = np.asarray(v, float)
    length = float(np.linalg.norm(v))
    return v / length if length > ZERO else np.zeros(3)


def perpendicular(v):
    """v に垂直な、長さ 1 の向き（v × 上。v が上と平行なら v × y。v が 0 なら x）。"""
    for other in (UP, Y):
        side = unit(np.cross(v, other))
        if float(np.linalg.norm(side)) > 0.0:
            return side
    return X.copy()


def sides(points, widths, camera, flat=False, untwist=False):
    """点ごとの幅の半分の向き (n, 3)。flat=True は面に寝た帯（幅の向き = 接線 × 上）。untwist=True は、向きが前の点と逆を向いたら
    返す（急に折り返す線で、帯が蝶ネクタイの形にねじれない。model3d.bone_ribbon の帯と同じ）。
    同じ点が続いて接線が 0 の所は、前（無ければ後ろ）の点の接線を使う。どの点の接線も 0（長さ 0 の帯）なら、幅も 0。
    接線 × 視線も 接線 × x も 0 なら（線がカメラと x の向きに並ぶ）、視線 × 上、それも 0 なら 接線に垂直な向き。"""
    points = np.asarray(points, float)
    n = len(points)
    out = np.zeros((n, 3))
    camera = np.asarray(camera, float)
    tangents = [points[min(i + 1, n - 1)] - points[max(i - 1, 0)] for i in range(n)]
    known = [i for i in range(n) if float(np.linalg.norm(tangents[i])) > ZERO]
    if not known:
        return out
    previous = None
    for i in range(n):
        if i not in known:
            before = [j for j in known if j < i]
            tangents[i] = tangents[before[-1] if before else known[0]]
        tangent = tangents[i]
        normal = UP if flat else camera - points[i]
        side = np.cross(tangent, normal)
        if float(np.linalg.norm(side)) < TINY:
            side = np.cross(tangent, X)
        if float(np.linalg.norm(side)) < TINY:
            side = np.cross(normal, UP)
        side = unit(side) if float(np.linalg.norm(side)) >= TINY else perpendicular(tangent)
        if untwist and previous is not None and float(side @ previous) < 0.0:
            side = -side
        if float(np.linalg.norm(side)) > 0.0:
            previous = side
        out[i] = side * (float(widths[i]) * 0.5)
    return out


def path_length(points):
    """折れ線の道のり（区間ごとの長さの和）。"""
    points = np.asarray(points, float)
    return float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()) if len(points) > 1 else 0.0


def cut(points, fraction):
    """折れ線を、根もとから道のりの fraction（0〜1）までで切った点の列（1 以上なら元のまま）。"""
    points = [np.asarray(p, float) for p in points]
    if fraction >= 1.0:
        return points
    lengths = [float(np.linalg.norm(q - p)) for p, q in zip(points[:-1], points[1:])]
    target, walked, out = sum(lengths) * max(0.0, fraction), 0.0, [points[0]]
    for p, q, length in zip(points[:-1], points[1:], lengths):
        if walked + length >= target:
            out.append(p + (q - p) * ((target - walked) / max(length, TINY)))
            break
        out.append(q)
        walked += length
    return out


class Quads:
    """四角形をためる。camera はカメラの位置（m）。"""

    def __init__(self, camera):
        self.camera = np.asarray(camera, float)
        self._co, self._value = [], []

    def __len__(self):
        return len(self._co)

    def strip(self, points, widths, values, flat=False, untwist=False):
        """点の列（2 つ以上）を帯にする。widths・values は点ごと（幅は m、強さは材質が掛ける数）。"""
        points = np.asarray(points, float)
        n = len(points)
        if n < 2:
            return 0
        s = sides(points, widths, self.camera, flat, untwist)
        for i in range(n - 1):
            p, q = points[i], points[i + 1]
            self._co.append(np.array([p - s[i], p + s[i], q + s[i + 1], q - s[i + 1]]))
            self._value.append((float(values[i]), float(values[i]), float(values[i + 1]), float(values[i + 1])))
        return n - 1

    def dot(self, centre, size, value, right=None, up=None):
        """カメラを向いた四角形（一辺 size）。right・up を渡せば、その向き（長さは問わない）。カメラが点の真上・真下なら、
        右は 視線 × y（fx_strips.perpendicular）。"""
        p = np.asarray(centre, float)
        to_camera = unit(self.camera - p)
        right = (perpendicular(to_camera) if right is None else unit(right)) * (size * 0.5)
        up = unit(np.cross(right, to_camera) if up is None else up)
        if float(np.linalg.norm(up)) == 0.0:                               # カメラが点と同じ所
            up = perpendicular(right)
        up = up * (size * 0.5)
        self._co.append(np.array([p - right - up, p + right - up, p + right + up, p - right + up]))
        self._value.append((float(value),) * 4)
        return 1

    def arrays(self):
        """(co (N, 4, 3), uv (N, 4, 2), value (N, 4))。"""
        n = len(self._co)
        if not n:
            return np.zeros((0, 4, 3)), np.zeros((0, 4, 2)), np.zeros((0, 4))
        return np.array(self._co), np.tile(QUAD_UV, (n, 1, 1)), np.array(self._value)
