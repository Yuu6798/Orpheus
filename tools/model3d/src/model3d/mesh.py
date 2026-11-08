"""JSON メッシュ（model3d-mesh/0.1）と OBJ の読み書き、位相のハッシュ、基本の幾何と 2 つの形状処理。

- `weld`: 距離指定の頂点結合（継ぎ目の二重頂点を 1 つにする）。一般的な「距離で結合」の独自実装で、
  Blockbench のコードは使っていない（Blockbench の結合は選択順に依存する非推移的なもので、ここは推移的に結合する）。
- `extrude_profile`: 凸な断面の押し出しとねじり。高さが負のときは**先に断面の有向辺を反転してから**押し出す
  （面の向きだけを反転する方法では面と体積が合わない、という再構成の実行結果 2026-11-03 を踏まえる）。
どちらも独自実装で、原本ツールとの一致は主張しない。
"""
import copy
import hashlib
import math
from pathlib import Path
from urllib.parse import quote, unquote

import numpy as np

from . import SCHEMAS, contracts
from .io import ToolError, atomic_json, canonical_json, read_json, sha256_text

SCHEMA = SCHEMAS["mesh"]
DEFAULT_AXES = {"up": "+Z", "forward": "-Y"}


class Mesh:
    """頂点 (N,3)・面（頂点番号の列）・任意の頂点 uv・面の集合 parts・頂点の selection。"""

    def __init__(self, vertices, faces, uv=None, name=None, units="m", axes=None, parts=None, selection=None, extra=None):
        self.vertices = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
        self.faces = [[int(i) for i in face] for face in faces]
        self.uv = None if uv is None else np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        self.name = name
        self.units = units
        self.axes = dict(axes) if axes else dict(DEFAULT_AXES)
        self.parts = {str(k): [int(i) for i in v] for k, v in (parts or {}).items()}
        self.selection = None if selection is None else [int(i) for i in selection]
        self.extra = {k: v for k, v in (extra or {}).items() if k.startswith("x_")}
        self.validate()

    # ---- 形式 ----------------------------------------------------------------

    def validate(self):
        n = len(self.vertices)
        if n and not np.isfinite(self.vertices).all():
            raise ToolError("頂点に非有限の値があります")
        if self.uv is not None and len(self.uv) != n:
            raise ToolError(f"uv の数 {len(self.uv)} が頂点数 {n} と違います")
        if self.uv is not None and len(self.uv) and not np.isfinite(self.uv).all():
            raise ToolError("uv に非有限の値があります")
        for k, face in enumerate(self.faces):
            if len(face) < 3:
                raise ToolError(f"面 {k} の頂点が 3 つ未満です")
            if any(i < 0 or i >= n for i in face):
                raise ToolError(f"面 {k} が無い頂点を指しています")
        nf = len(self.faces)
        for name, indices in self.parts.items():
            if any(i < 0 or i >= nf for i in indices):
                raise ToolError(f"parts {name!r} が無い面を指しています")
        if self.selection is not None and any(i < 0 or i >= n for i in self.selection):
            raise ToolError("selection が無い頂点を指しています")
        if self.units not in ("m", "cm", "mm"):
            raise ToolError(f"units は m / cm / mm から選んでください: {self.units!r}")

    @property
    def vertex_count(self):
        return int(len(self.vertices))

    @property
    def face_count(self):
        return len(self.faces)

    def copy(self):
        return Mesh(self.vertices.copy(), [list(f) for f in self.faces], None if self.uv is None else self.uv.copy(),
                    self.name, self.units, self.axes, {k: list(v) for k, v in self.parts.items()},
                    None if self.selection is None else list(self.selection), self.extra)

    def to_dict(self):
        data = {"schema_version": SCHEMA, "units": self.units, "axes": dict(self.axes),
                "vertices": [[float(x) for x in v] for v in self.vertices.tolist()],
                "faces": [list(f) for f in self.faces]}
        if self.name:
            data["name"] = self.name
        if self.uv is not None:
            data["uv"] = [[float(x) for x in v] for v in self.uv.tolist()]
        if self.parts:
            data["parts"] = {k: list(v) for k, v in self.parts.items()}
        if self.selection is not None:
            data["selection"] = list(self.selection)
        data.update(self.extra)
        return data

    @classmethod
    def from_dict(cls, data, where=None):
        contracts.validate("mesh", data, where)
        return cls(data["vertices"], data["faces"], data.get("uv"), data.get("name"), data.get("units", "m"),
                   data.get("axes"), data.get("parts"), data.get("selection"), data)

    @classmethod
    def load(cls, path):
        path = Path(path)
        if path.suffix.lower() == ".obj":
            return read_obj(path)
        return cls.from_dict(read_json(path), path)

    def save(self, path):
        path = Path(path)
        if path.suffix.lower() == ".obj":
            write_obj(self, path)
        else:
            atomic_json(path, self.to_dict())
        return path

    # ---- 幾何 ----------------------------------------------------------------

    def triangles(self):
        """三角形化した (T,3) の頂点番号と、各三角形が属する面の番号 (T,)。面の向き（巻き方）は保つ。

        三角形はそのまま、四角形は凹んだ頂点を通る対角線で、5 角形以上は面の平面に投影して耳切りで分ける
        （扇形は凸な面にしか正しくない。凹んだ面では面積・体積と衝突面がずれる）。退化した面だけ扇形に戻す。
        """
        quads = [k for k, face in enumerate(self.faces) if len(face) == 4]
        other_diagonal = {}
        if quads:
            corners = self.vertices[np.asarray([self.faces[k] for k in quads], dtype=np.int64)]      # (Q,4,3)
            normal = np.cross(corners[:, 2] - corners[:, 0], corners[:, 3] - corners[:, 1])           # 対角線の外積 ∝ 面の法線
            edges = np.roll(corners, -1, axis=1) - corners
            turn = np.einsum("qij,qj->qi", np.cross(edges, np.roll(edges, -1, axis=1)), normal)      # 頂点 i+1 の曲がり
            flagged = (turn[:, 0] < 0) | (turn[:, 2] < 0)   # 頂点 1 か 3 が凹 → 対角線 0-2 は面の外。1-3 で分ける
            other_diagonal = dict(zip(quads, flagged.tolist()))
        tris, owner = [], []
        for k, face in enumerate(self.faces):
            if len(face) == 3:
                tris.append(tuple(face))
                owner.append(k)
            elif len(face) == 4:
                if other_diagonal.get(k):
                    tris.extend([(face[1], face[2], face[3]), (face[1], face[3], face[0])])
                else:
                    tris.extend([(face[0], face[1], face[2]), (face[0], face[2], face[3])])
                owner.extend([k, k])
            else:
                for i, j, m in triangulate_polygon(self.vertices[face]):
                    tris.append((face[i], face[j], face[m]))
                    owner.append(k)
        if not tris:
            return np.zeros((0, 3), dtype=np.int64), np.zeros((0,), dtype=np.int64)
        return np.asarray(tris, dtype=np.int64), np.asarray(owner, dtype=np.int64)

    def triangle_points(self):
        tris, _ = self.triangles()
        return self.vertices[tris] if len(tris) else np.zeros((0, 3, 3))

    def triangle_normals(self, unit=True):
        points = self.triangle_points()
        if not len(points):
            return np.zeros((0, 3))
        normals = np.cross(points[:, 1] - points[:, 0], points[:, 2] - points[:, 0])
        if unit:
            length = np.linalg.norm(normals, axis=1)
            normals = normals / np.where(length > 0, length, 1.0)[:, None]
        return normals

    def area(self):
        return float(np.linalg.norm(self.triangle_normals(unit=False), axis=1).sum() / 2.0) if self.faces else 0.0

    def signed_volume(self):
        """閉じたメッシュの符号付き体積（外向きの面なら正）。"""
        points = self.triangle_points()
        if not len(points):
            return 0.0
        return float(np.einsum("ij,ij->i", points[:, 0], np.cross(points[:, 1], points[:, 2])).sum() / 6.0)

    def bounds(self):
        if not len(self.vertices):
            return [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]
        return self.vertices.min(axis=0).tolist(), self.vertices.max(axis=0).tolist()

    def edge_report(self):
        """閉じているか（全ての辺が向きの逆な 2 つの面に共有される）と、境界辺・非多様体辺の数。"""
        directed = {}
        for face in self.faces:
            for j in range(len(face)):
                a, b = face[j], face[(j + 1) % len(face)]
                directed[(a, b)] = directed.get((a, b), 0) + 1
        boundary = nonmanifold = 0
        seen = set()
        for (a, b), count in directed.items():
            key = (min(a, b), max(a, b))
            if key in seen:
                continue
            seen.add(key)
            total = count + directed.get((b, a), 0)
            if total != 2 or count != 1:
                if total == 1:
                    boundary += 1
                else:
                    nonmanifold += 1
        return {"closed": boundary == 0 and nonmanifold == 0 and bool(self.faces), "boundary_edges": boundary,
                "nonmanifold_edges": nonmanifold, "edges": len(seen),
                "consistent_winding": bool(self.faces) and all(count == 1 for count in directed.values())}

    def components(self):
        """面でつながった頂点のまとまりの数（面に使われていない頂点は数えない）。"""
        parent = {}

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for face in self.faces:
            for i in face:
                parent.setdefault(i, i)
            first = face[0]
            for i in face[1:]:
                a, b = find(first), find(i)
                if a != b:
                    parent[b] = a
        return len({find(i) for i in parent})

    def topology_hash(self):
        """頂点構成（頂点数と面の頂点番号）だけのハッシュ。頂点番号で領域を指すときは、この値に結び付ける。"""
        return sha256_text(canonical_json({"vertex_count": self.vertex_count, "faces": self.faces}))

    def geometry_hash(self):
        return sha256_text(canonical_json({"vertices": self.vertices.tolist(), "faces": self.faces}))

    def summary(self):
        lo, hi = self.bounds()
        return {"vertices": self.vertex_count, "faces": self.face_count, "bounds": {"min": lo, "max": hi},
                "topology_sha256": self.topology_hash(), "geometry_sha256": self.geometry_hash()}


# ---- 面の集まり（面領域の材料）------------------------------------------------------------------

def face_components(mesh):
    """頂点を共有する面でつながった面の集まり（連結成分）。最小の面番号の順に並べ、各成分の面番号は昇順。"""
    parent = list(range(mesh.vertex_count))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for face in mesh.faces:
        root = find(face[0])
        for i in face[1:]:
            other = find(i)
            if other != root:
                parent[other] = root
    groups = {}
    for k, face in enumerate(mesh.faces):
        groups.setdefault(find(face[0]), []).append(k)
    return sorted(groups.values(), key=lambda faces: faces[0])


def face_vertices(mesh, faces):
    """面の集まりが使う頂点の番号（昇順）。"""
    return sorted({i for k in faces for i in mesh.faces[k]})


def face_centers(mesh):
    """面ごとの重心（頂点の平均）(F,3)。"""
    if not mesh.faces:
        return np.zeros((0, 3))
    return np.asarray([mesh.vertices[face].mean(axis=0) for face in mesh.faces])


def submesh(mesh, faces):
    """面の集まりだけのメッシュと、新→旧の頂点番号・面番号。使わない頂点は落とし、uv・頂点色・単位・軸は引き継ぐ。"""
    faces = sorted({int(k) for k in faces})
    vertices = face_vertices(mesh, faces)
    new_index = {old: new for new, old in enumerate(vertices)}
    palette = mesh.extra.get("x_vertex_colors")
    extra = {"x_vertex_colors": [palette[i] for i in vertices]} if isinstance(palette, list) and len(palette) == mesh.vertex_count else {}
    normals = mesh.extra.get("x_normals")
    if isinstance(normals, list) and len(normals) == mesh.vertex_count:
        extra["x_normals"] = [normals[i] for i in vertices]
    part = Mesh(mesh.vertices[vertices], [[new_index[i] for i in mesh.faces[k]] for k in faces],
                None if mesh.uv is None else mesh.uv[vertices], mesh.name, mesh.units, mesh.axes, extra=extra)
    return part, {"vertices": vertices, "faces": faces}


def describe_faces(mesh, faces):
    """面の集まりの要約（人が役割を決める材料）: 面と頂点の数、面積、bounds、面積で重みを付けた重心、閉じているか、頂点色の平均。"""
    part, _ = submesh(mesh, faces)
    lo, hi = part.bounds()
    points = part.triangle_points()
    areas = np.linalg.norm(part.triangle_normals(unit=False), axis=1) / 2.0 if len(points) else np.zeros(0)
    if areas.sum() > 0:
        centroid = (points.mean(axis=1) * areas[:, None]).sum(axis=0) / areas.sum()
    else:
        centroid = part.vertices.mean(axis=0) if part.vertex_count else np.zeros(3)
    edges = part.edge_report()
    palette = part.extra.get("x_vertex_colors")
    summary = {"faces": part.face_count, "vertices": part.vertex_count, "area": float(areas.sum()), "bounds": {"min": lo, "max": hi},
               "centroid": [float(x) for x in centroid], "closed": edges["closed"], "boundary_edges": edges["boundary_edges"],
               "nonmanifold_edges": edges["nonmanifold_edges"]}
    if isinstance(palette, list) and len(palette) == part.vertex_count and part.vertex_count:
        summary["mean_color"] = [float(x) for x in np.asarray(palette, dtype=np.float64).mean(axis=0)]
    return summary


# ---- 列挙順に依らない指紋 ------------------------------------------------------------------

def canonical_signature(mesh, colors=True):
    """頂点番号と面の列挙順だけが違うメッシュが同じ指紋になるように正規化した sha256。

    座標（頂点色 x_vertex_colors があれば座標と色の組）で頂点を並べ替え、三角形は巡回の最小の並びにして辞書順に並べる
    （向きは保つ）。丸め・許容差・位置合わせは使わない。座標と色が同じ頂点は 1 つに寄せて数え、座標だけ同じ頂点
    （継ぎ目で分かれた、色の違う頂点）は別のまま残す。座標が重なる頂点があれば duplicate_positions に残す。
    """
    tris, _ = mesh.triangles()
    positions = mesh.vertices
    palette = mesh.extra.get("x_vertex_colors") if colors else None
    colored = bool(isinstance(palette, list) and len(palette) == len(positions) and len(positions))
    keyed = np.hstack([positions, np.asarray(palette, dtype=np.float64).reshape(-1, 3)]) if colored else positions
    unique, inverse = np.unique(keyed, axis=0, return_inverse=True) if len(keyed) else (keyed, np.zeros(0, dtype=np.int64))
    inverse = np.asarray(inverse).reshape(-1)
    duplicates = bool(len(np.unique(positions, axis=0)) != len(positions)) if len(positions) else False
    faces = inverse[tris] if len(tris) else np.zeros((0, 3), dtype=np.int64)
    rotated = [min((tuple(row), (row[1], row[2], row[0]), (row[2], row[0], row[1]))) for row in faces.tolist()]
    rotated.sort()
    digest = hashlib.sha256()
    digest.update(np.ascontiguousarray(unique, dtype="<f8").tobytes())   # 色付きなら座標と色の組
    digest.update(np.asarray(rotated, dtype="<i8").tobytes())
    return {"sha256": digest.hexdigest(), "vertices": int(len(unique)), "triangles": int(len(rotated)),
            "duplicate_positions": duplicates, "colors": colored}


def compare_geometry(a, b, colors=True):
    """2 つのメッシュが、頂点番号と面の列挙順の違いを除いて同じか。"""
    left, right = canonical_signature(a, colors), canonical_signature(b, colors)
    return {"same": left["sha256"] == right["sha256"], "left": left, "right": right}


# ---- 多角形の三角形化 ------------------------------------------------------------------

def _cross2(a, b):
    """2D ベクトルの列どうしの外積の z 成分（numpy 2 は 2D の np.cross を非推奨にした）。"""
    return a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]


def _project_polygon(points):
    """面の法線（Newell 法）に投影した 2D 座標 (n,2)。向きは元の面の巻き方と同じ（反時計回り）。退化していれば None。"""
    normal = np.cross(points, np.roll(points, -1, axis=0)).sum(axis=0)
    length = np.linalg.norm(normal)
    if not np.isfinite(length) or length < 1e-18:
        return None
    normal = normal / length
    helper = np.array([1.0, 0.0, 0.0]) if abs(normal[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
    u = np.cross(normal, helper)
    u /= np.linalg.norm(u)
    v = np.cross(normal, u)
    flat = (points - points[0]) @ np.stack([u, v]).T
    if _cross2(flat, np.roll(flat, -1, axis=0)).sum() < 0:   # 投影の向きが逆なら軸を反転して反時計回りにする
        flat[:, 1] *= -1.0
    return flat


def _fan(indices):
    return [(indices[0], indices[j], indices[j + 1]) for j in range(1, len(indices) - 1)]


def triangulate_polygon(points):
    """多角形（面の頂点の 3D 座標の列）を、面の中にある三角形に分ける。返すのは面の中での番号の組の列。

    凸な面は扇形（JSCAD の照合と同じ分割）、凹んだ面は耳切り（ear clipping）。耳が見つからない退化した面は扇形に戻す。
    """
    points = np.asarray(points, dtype=np.float64)
    n = len(points)
    if n < 3:
        return []
    if n == 3:
        return [(0, 1, 2)]
    flat = _project_polygon(points)
    if flat is None:
        return _fan(list(range(n)))
    scale = float(np.abs(flat).max()) or 1.0
    eps = 1e-12 * scale * scale
    edges = np.roll(flat, -1, axis=0) - flat
    turns = _cross2(edges, np.roll(edges, -1, axis=0))   # 頂点 i+1 の曲がり（反時計回りなら正）
    if (turns >= -eps).all():
        return _fan(list(range(n)))

    def convex(a, b, c):
        return (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0]) > eps

    def contains(a, b, c, p):   # 三角形 abc（反時計回り）の中か辺の上に p があるか
        return ((b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0]) >= -eps and
                (c[0] - b[0]) * (p[1] - b[1]) - (c[1] - b[1]) * (p[0] - b[0]) >= -eps and
                (a[0] - c[0]) * (p[1] - c[1]) - (a[1] - c[1]) * (p[0] - c[0]) >= -eps)

    remaining = list(range(n))
    result = []
    while len(remaining) > 3:
        found = False
        for k in range(len(remaining)):
            i0, i1, i2 = remaining[k - 1], remaining[k], remaining[(k + 1) % len(remaining)]
            a, b, c = flat[i0], flat[i1], flat[i2]
            if not convex(a, b, c):
                continue
            if any(contains(a, b, c, flat[j]) for j in remaining if j not in (i0, i1, i2)):
                continue
            result.append((i0, i1, i2))
            del remaining[k]
            found = True
            break
        if not found:   # 自己交差などで耳が無い: 残りは扇形
            result.extend(_fan(remaining))
            return result
    result.append(tuple(remaining))
    return result


# ---- 部品ごとの npz ---------------------------------------------------------------

def _npz_arrays(path, what, keys, optional=()):
    """npz の keys の配列（optional は無ければ None）。読めなければ、どのファイルかを名指す ToolError。pickle は読まない。"""
    try:
        data = np.load(path, allow_pickle=False)
        if isinstance(data, np.ndarray):
            raise ToolError(f"{what} は npz（名前つきの配列の束）にしてください: {path}")
        with data:
            missing = [k for k in keys if k not in data.files]
            if missing:
                raise ToolError(f"{what} に {', '.join(missing)} がありません: {path}")
            arrays = {k: np.asarray(data[k]) for k in keys}
            arrays.update({k: np.asarray(data[k]) if k in data.files else None for k in optional})
        return arrays
    except ToolError:
        raise
    except Exception as error:   # 壊れ方で OSError・ValueError・EOFError・BadZipFile・zlib.error などが出る
        raise ToolError(f"{what} を読めません: {path}（{type(error).__name__}: {error}）") from error


def _part_names(path):
    names = _npz_arrays(path, "部品の npz", ["names"])["names"]
    if names.ndim != 1:
        raise ToolError(f"names は部品の名前の 1 次元の列にしてください（形 {names.shape}）: {path}")
    return [n.decode("utf-8") if isinstance(n, bytes) else str(n) for n in names.tolist()]


def _check_part_geometry(vertices, faces, i, path):
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not np.issubdtype(vertices.dtype, np.number):
        raise ToolError(f"co{i} は頂点 (N,3) の数にしてください（形 {vertices.shape}、{vertices.dtype}）: {path}")
    if faces.ndim != 2 or faces.shape[1] < 3 or not np.issubdtype(faces.dtype, np.integer):
        raise ToolError(f"f{i} は面の頂点番号 (F,k) の整数にしてください（形 {faces.shape}、{faces.dtype}）: {path}")


def read_all_parts(path):
    """部品ごとの npz の全部の部品を、並びの順に (名前, 頂点 (N,3) float64, 面 (F,k) int64) で読む（uv は読まない）。
    頂点が有限でないか、面が無い頂点を指していれば止める。pickle の入った配列は読まない。"""
    names = _part_names(path)
    arrays = _npz_arrays(path, "部品の npz", [key for i in range(len(names)) for key in (f"co{i}", f"f{i}")])
    out = []
    for i, name in enumerate(names):
        vertices, faces = arrays[f"co{i}"], arrays[f"f{i}"]
        _check_part_geometry(vertices, faces, i, path)
        if not np.isfinite(vertices).all():
            raise ToolError(f"co{i}（{name}）に非有限の値があります: {path}")
        if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
            raise ToolError(f"f{i}（{name}）が無い頂点を指しています: {path}")
        out.append((name, vertices.astype(np.float64), faces.astype(np.int64)))
    return out


def read_parts(path, part, normals=None):
    """部品ごとの配列を持つ npz から、名前で 1 つの部品を読む。

    npz は names（部品の名前の 1 次元の列）と、i 番目の部品の co{i}（頂点 (N,3)）・f{i}（面の頂点番号 (F,k) の整数）・
    uv{i}（頂点の uv (N,2)、任意）。normals（別の npz）があれば、その n{i}（頂点の法線 (N,3)。部品の順で対応させる）を
    x_normals に入れる。メッシュの名前は部品の名前（面領域は名前で部品を区別する）。pickle の入った配列は読まない。
    """
    names = _part_names(path)
    if part not in names:
        raise ToolError(f"部品 {part!r} がありません（{', '.join(names)}）: {path}")
    i = names.index(part)
    arrays = _npz_arrays(path, "部品の npz", [f"co{i}", f"f{i}"], optional=[f"uv{i}"])
    vertices, faces, uv = arrays[f"co{i}"], arrays[f"f{i}"], arrays[f"uv{i}"]
    _check_part_geometry(vertices, faces, i, path)
    if uv is not None and (uv.shape != (len(vertices), 2) or not np.issubdtype(uv.dtype, np.number)):
        raise ToolError(f"uv{i} は頂点ごとの uv (N,2) にしてください（形 {uv.shape}）: {path}")
    extra = {}
    if normals is not None:
        given = _npz_arrays(normals, "法線の npz", [f"n{i}"])[f"n{i}"]
        if given.shape != vertices.shape or not np.issubdtype(given.dtype, np.number) or not np.isfinite(given).all():
            raise ToolError(f"n{i} は頂点と同じ (N,3) の有限の数にしてください（形 {given.shape}）: {normals}")
        extra["x_normals"] = given.astype(np.float64).tolist()
    return Mesh(vertices.astype(np.float64), faces.astype(np.int64).tolist(),
                None if uv is None else uv.astype(np.float64), name=part, extra=extra)


# ---- OBJ ------------------------------------------------------------------------

def read_obj(path):
    """OBJ を読む（v / vt / f / o / g）。頂点ごとの uv は、各頂点が 1 つの vt にだけ対応するときに持つ。

    名前は write_obj が見出しに書いた name、無ければファイル名（面領域は名前で部品を区別するので、往復で変えない）。
    """
    vertices, uvs, faces, face_uvs, parts, colors = [], [], [], [], {}, []
    group, units, axes, name = None, "m", None, None
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.startswith(f"# {SCHEMA} "):   # write_obj が書く見出し: units・軸・名前を戻す
            fields = dict(token.split("=", 1) for token in raw.split()[2:] if "=" in token)
            units = fields.get("units", units)
            if fields.get("up") and fields.get("forward"):
                axes = {"up": fields["up"], "forward": fields["forward"]}
            if fields.get("name"):
                name = unquote(fields["name"])
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head, *rest = line.split()
        try:
            if head == "v" and len(rest) >= 3:
                vertices.append([float(rest[0]), float(rest[1]), float(rest[2])])
                colors.append([float(rest[3]), float(rest[4]), float(rest[5])] if len(rest) >= 6 else None)
            elif head == "vt" and len(rest) >= 2:
                uvs.append([float(rest[0]), float(rest[1])])
            elif head in ("o", "g"):
                group = " ".join(rest) or None
            elif head == "f":
                corner, corner_uv = [], []
                for item in rest:
                    fields = item.split("/")
                    vi = int(fields[0])
                    corner.append(vi - 1 if vi > 0 else len(vertices) + vi)
                    if len(fields) > 1 and fields[1]:
                        ti = int(fields[1])
                        corner_uv.append(ti - 1 if ti > 0 else len(uvs) + ti)
                    else:
                        corner_uv.append(None)
                faces.append(corner)
                face_uvs.append(corner_uv)
                if group:
                    parts.setdefault(group, []).append(len(faces) - 1)
        except ValueError as error:
            raise ToolError(f"OBJ の {number} 行目を読めません: {raw.strip()!r}（{error}）") from error
    uv = None
    if uvs and faces:
        per_vertex, consistent = {}, True
        for corner, corner_uv in zip(faces, face_uvs):
            for vi, ti in zip(corner, corner_uv):
                if ti is None or ti < 0 or ti >= len(uvs):
                    consistent = False
                    break
                if per_vertex.setdefault(vi, ti) != ti:
                    consistent = False
                    break
            if not consistent:
                break
        if consistent and len(per_vertex) == len(vertices):
            uv = [uvs[per_vertex[i]] for i in range(len(vertices))]
    extra = {}
    if vertices and all(c is not None for c in colors):   # 頂点色（0..1 の RGB。trimesh などの書き方）
        extra["x_vertex_colors"] = colors
    return Mesh(vertices, faces, uv, name=name or Path(path).stem, units=units, axes=axes, parts=parts, extra=extra)


def write_obj(mesh, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = {}
    for name in sorted(mesh.parts):
        for k in mesh.parts[name]:
            owner.setdefault(k, name)
    lines = [f"# {SCHEMA} units={mesh.units} up={mesh.axes.get('up')} forward={mesh.axes.get('forward')}"
             + (f" name={quote(mesh.name, safe='')}" if mesh.name else "")]
    if mesh.name:
        lines.append(f"o {mesh.name}")
    colors = mesh.extra.get("x_vertex_colors")
    if isinstance(colors, list) and len(colors) == mesh.vertex_count:
        lines.extend(f"v {x!r} {y!r} {z!r} {float(c[0])!r} {float(c[1])!r} {float(c[2])!r}"
                     for (x, y, z), c in zip(mesh.vertices.tolist(), colors))
    else:
        lines.extend(f"v {x!r} {y!r} {z!r}" for x, y, z in mesh.vertices.tolist())   # repr: 往復で値が変わらない最短表記
    if mesh.uv is not None:
        lines.extend(f"vt {u!r} {v!r}" for u, v in mesh.uv.tolist())
    current = object()
    for k, face in enumerate(mesh.faces):
        group = owner.get(k)
        if group != current:
            lines.append(f"g {group if group is not None else 'default'}")
            current = group
        if mesh.uv is not None:
            lines.append("f " + " ".join(f"{i + 1}/{i + 1}" for i in face))
        else:
            lines.append("f " + " ".join(str(i + 1) for i in face))
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    tmp.replace(path)
    return path


# ---- 変形 --------------------------------------------------------------------------

UNIT_FACTORS = {"m": 1.0, "cm": 0.01, "mm": 0.001}
AXIS_VECTORS = {"+X": (1.0, 0.0, 0.0), "-X": (-1.0, 0.0, 0.0), "+Y": (0.0, 1.0, 0.0), "-Y": (0.0, -1.0, 0.0),
                "+Z": (0.0, 0.0, 1.0), "-Z": (0.0, 0.0, -1.0)}


def axis_basis(axes):
    """軸の約束 {"up", "forward"} から、列が [up, forward, up×forward] の正規直交基底（右手系）を作る。"""
    try:
        up, forward = np.asarray(AXIS_VECTORS[axes["up"]]), np.asarray(AXIS_VECTORS[axes["forward"]])
    except (KeyError, TypeError) as error:
        raise ToolError(f"axes は {{\"up\": \"+Z\", \"forward\": \"-Y\"}} の形で、±X / ±Y / ±Z から選んでください: {axes!r}") from error
    if abs(float(up @ forward)) > 0:
        raise ToolError(f"axes の up と forward は直交する軸にしてください: {axes!r}")
    return np.column_stack([up, forward, np.cross(up, forward)])


def convert_axes(points, source_axes, target_axes):
    """source の約束で書かれた座標を target の約束へ回す（up は up へ、forward は forward へ）。"""
    rotation = axis_basis(target_axes) @ axis_basis(source_axes).T
    return np.asarray(points, dtype=np.float64) @ rotation.T


def transform(mesh, translate=None, scale=None, rotate_z_deg=0.0, to_units=None, to_axes=None):
    """単位の換算（to_units）→ 軸の換算（to_axes）→ 拡大縮小 → Z 軸まわりの回転 → 平行移動の順に当てた写し。
    translate は換算後の単位と軸。頂点の法線（x_normals）も同じ向きへ回す（拡大縮小はその逆を掛けて長さを 1 に戻す）。"""
    out = mesh.copy()
    points = out.vertices
    normals = out.extra.get("x_normals")
    normals = (np.asarray(normals, dtype=np.float64).reshape(-1, 3)
               if isinstance(normals, list) and len(normals) == mesh.vertex_count else None)
    if to_units is not None:
        if to_units not in UNIT_FACTORS:
            raise ToolError(f"to_units は m / cm / mm から選んでください: {to_units!r}")
        points = points * (UNIT_FACTORS[mesh.units] / UNIT_FACTORS[to_units])
        out.units = to_units
    if to_axes is not None:
        target = {"up": to_axes.get("up"), "forward": to_axes.get("forward")} if isinstance(to_axes, dict) else None
        points = convert_axes(points, mesh.axes, target or to_axes)
        if normals is not None:
            normals = convert_axes(normals, mesh.axes, target or to_axes)
        out.axes = dict(target)
    if scale is not None:
        factor = np.asarray(scale, dtype=np.float64)
        if factor.ndim == 0:
            factor = np.repeat(factor, 3)
        if factor.shape != (3,) or not np.isfinite(factor).all() or (factor == 0).any():
            raise ToolError(f"scale は 0 でない数か 3 要素の列にしてください: {scale!r}")
        points = points * factor
        if normals is not None:
            normals = normals / factor   # 法線は拡大縮小の逆転置（鏡映なら向きも反転）
    if isinstance(rotate_z_deg, bool) or not isinstance(rotate_z_deg, (int, float)) or not math.isfinite(rotate_z_deg):
        raise ToolError(f"rotate_z_deg は有限の数にしてください: {rotate_z_deg!r}")
    if rotate_z_deg:
        angle = math.radians(float(rotate_z_deg))
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        points = points @ rotation.T
        if normals is not None:
            normals = normals @ rotation.T
    if translate is not None:
        offset = np.asarray(translate, dtype=np.float64)
        if offset.shape != (3,) or not np.isfinite(offset).all():
            raise ToolError(f"translate は 3 要素の列にしてください: {translate!r}")
        points = points + offset
    if normals is not None:
        length = np.linalg.norm(normals, axis=1, keepdims=True)
        out.extra["x_normals"] = (normals / np.where(length > 0, length, 1.0)).tolist()
    out.vertices = np.ascontiguousarray(points)
    out.validate()
    return out


# ---- 頂点結合 ------------------------------------------------------------------------

def _candidate_pairs(points, distance):
    """距離 distance 以内にある可能性のある頂点の組（格子で絞る）。"""
    cells = np.floor(points / distance).astype(np.int64)
    buckets = {}
    for index, cell in enumerate(map(tuple, cells.tolist())):
        buckets.setdefault(cell, []).append(index)
    pairs = []
    offsets = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)]
    for cell, members in buckets.items():
        for offset in offsets:
            other = (cell[0] + offset[0], cell[1] + offset[1], cell[2] + offset[2])
            if other < cell or other not in buckets:
                continue
            targets = buckets[other]
            for i in members:
                for j in targets:
                    if other != cell or j > i:
                        pairs.append((i, j))
    return pairs


class _Union:
    def __init__(self, items):
        self.parent = {i: i for i in items}

    def find(self, i):
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i, j):
        a, b = self.find(i), self.find(j)
        if a != b:
            if b < a:
                a, b = b, a
            self.parent[b] = a


def weld(mesh, distance, selection=None, center="mean"):
    """距離 distance 以内の頂点（selection があればその中だけ）を結合した写しと、結果の数。

    結合した頂点の位置・uv・頂点色（x_vertex_colors）・法線（x_normals。長さは 1 に戻す）は平均（center="mean"）か、
    番号が最小の頂点のもの（center="first"）。
    面は番号を付け替え、連続する同じ頂点を畳み、頂点が 3 つ未満になった面と、同じ頂点集合の面・他の面の
    頂点集合に含まれる面を除く（結合に関わった面と、それと頂点を共有する面だけを見る。元から重なっていた面は見ない）。
    結合は推移的（A-B と B-C が近ければ A-B-C が 1 つになる）。
    """
    if not isinstance(distance, (int, float)) or isinstance(distance, bool) or not math.isfinite(distance) or distance <= 0:
        raise ToolError(f"distance は 0 より大きい有限の数にしてください: {distance!r}")
    if center not in ("mean", "first"):
        raise ToolError(f"center は mean か first です: {center!r}")
    n = mesh.vertex_count
    chosen = sorted(set(range(n)) if selection is None else {int(i) for i in selection})
    if any(i < 0 or i >= n for i in chosen):
        raise ToolError("selection が無い頂点を指しています")
    union = _Union(chosen)
    if chosen:
        points = mesh.vertices[chosen]
        for a, b in _candidate_pairs(points, float(distance)):
            if np.linalg.norm(points[a] - points[b]) <= distance:
                union.union(chosen[a], chosen[b])
    groups = {}
    for i in chosen:
        groups.setdefault(union.find(i), []).append(i)
    groups = {root: members for root, members in groups.items() if len(members) > 1}
    representative = {i: i for i in range(n)}
    for root, members in groups.items():
        for i in members:
            representative[i] = root
    vertices = mesh.vertices.copy()
    uv = None if mesh.uv is None else mesh.uv.copy()
    palette = mesh.extra.get("x_vertex_colors")
    colors = np.asarray(palette, dtype=np.float64).reshape(-1, 3).copy() if isinstance(palette, list) and len(palette) == n and n else None
    given = mesh.extra.get("x_normals")
    normals = np.asarray(given, dtype=np.float64).reshape(-1, 3).copy() if isinstance(given, list) and len(given) == n and n else None
    if center == "mean":
        for root, members in groups.items():
            vertices[root] = mesh.vertices[members].mean(axis=0)
            if uv is not None:
                uv[root] = mesh.uv[members].mean(axis=0)
            if colors is not None:
                colors[root] = colors[members].mean(axis=0)   # 頂点色も uv と同じ規則（first なら番号が最小の頂点の色）
            if normals is not None:
                normals[root] = normals[members].mean(axis=0)
    keep = [i for i in range(n) if representative[i] == i]
    new_index = {old: new for new, old in enumerate(keep)}
    remap = {i: new_index[representative[i]] for i in range(n)}
    faces, kept_faces, touched, degenerate = [], [], [], 0
    for k, face in enumerate(mesh.faces):
        corners = [remap[i] for i in face]
        folded = [corners[j] for j in range(len(corners)) if corners[j] != corners[j - 1]]
        if len(set(folded)) < 3:
            degenerate += 1
            continue
        faces.append(folded)
        kept_faces.append(k)
        if any(representative[i] != i for i in face):
            touched.append(len(faces) - 1)
    sets = [frozenset(f) for f in faces]
    by_vertex = {}
    for k, face in enumerate(faces):
        for vertex in sets[k]:
            by_vertex.setdefault(vertex, []).append(k)
    pool = set(touched)
    for a in touched:   # 結合に関わった面と頂点を共有する面も見る（そちらが含まれる側のこともある）
        for vertex in sets[a]:
            pool.update(by_vertex[vertex])
    contained = set()
    for a in sorted(pool):
        for b in by_vertex[faces[a][0]]:   # a を含む面は a の全頂点、特に先頭の頂点を持つ
            if a != b and sets[a] <= sets[b] and (len(sets[a]) < len(sets[b]) or a > b):
                contained.add(a)
                break
    final_faces = [faces[k] for k in range(len(faces)) if k not in contained]
    final_old = [kept_faces[k] for k in range(len(faces)) if k not in contained]
    face_index = {old: new for new, old in enumerate(final_old)}
    parts = {name: sorted(face_index[k] for k in indices if k in face_index) for name, indices in mesh.parts.items()}
    selection_out = None if mesh.selection is None else sorted({remap[i] for i in mesh.selection})
    extra = dict(mesh.extra)
    if colors is not None:
        extra["x_vertex_colors"] = [[float(c) for c in row] for row in colors[keep].tolist()]
    extra.pop("x_normals", None)   # 頂点数の合わない古い法線は持ち越さない
    if normals is not None:
        kept = normals[keep]
        length = np.linalg.norm(kept, axis=1, keepdims=True)
        extra["x_normals"] = (kept / np.where(length > 0, length, 1.0)).tolist()
    out = Mesh(vertices[keep], final_faces, None if uv is None else uv[keep], mesh.name, mesh.units, mesh.axes,
               parts, selection_out, extra)
    vertex_mapping = {"source_vertex_count": n, "target_vertex_count": out.vertex_count,
                      "source_topology_sha256": mesh.topology_hash(), "target_topology_sha256": out.topology_hash(),
                      "old_to_new": [remap[i] for i in range(n)], "center": center,
                      "face_old_to_new": [face_index.get(k, -1) for k in range(mesh.face_count)],   # 消えた面は -1。面領域の付け替えに使う
                      "source_name": mesh.name, "target_name": out.name}   # 面領域の付け替えをこの部品だけに限る
    report = {"distance": float(distance), "center": center, "selected": len(chosen), "merged_groups": len(groups),
              "vertices_before": n, "vertices_after": out.vertex_count, "faces_before": mesh.face_count,
              "faces_after": out.face_count, "faces_removed_degenerate": degenerate, "faces_removed_contained": len(contained),
              "vertex_mapping": vertex_mapping}
    return out, report


def remap_rig_weights(rig, source_mesh, target_mesh, mapping):
    """結合の正確な旧→新頂点対応でウェイトを移す。近傍推定や別メッシュへのリグ転送ではない。

    source の topology に結び付いた有効なリグだけを受け付ける。結合された頂点のウェイトは、
    位置と同じ mean / first 規則でまとめる。負値や未正規化の入力を黙って修復しない。
    """
    from . import checks
    contracts.validate("rig", rig)
    report = checks.structure(rig, mesh=source_mesh)
    if not report["pass"]:
        raise ToolError("結合前のリグが入力メッシュと整合しません: " + "; ".join(report["failures"]))
    expected = {"source_vertex_count": source_mesh.vertex_count, "target_vertex_count": target_mesh.vertex_count,
                "source_topology_sha256": source_mesh.topology_hash(), "target_topology_sha256": target_mesh.topology_hash()}
    if any(mapping.get(key) != value for key, value in expected.items()):
        raise ToolError("頂点対応の source / target が入力メッシュと違います")
    indices, center = mapping.get("old_to_new"), mapping.get("center")
    if center not in ("mean", "first") or not isinstance(indices, list) or len(indices) != source_mesh.vertex_count \
            or any(isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < target_mesh.vertex_count for i in indices):
        raise ToolError("頂点対応は source の全頂点を target の整数番号へ写す mean / first の対応にしてください")
    members = [[] for _ in range(target_mesh.vertex_count)]
    for old, new in enumerate(indices):
        members[new].append(old)
    if any(not group for group in members):
        raise ToolError("頂点対応に転送元のない target 頂点があります")
    groups = {}
    for name, pairs in rig["weights"]["groups"].items():
        old_weights = np.zeros(source_mesh.vertex_count, dtype=np.float64)
        for vi, weight in pairs:
            old_weights[int(vi)] += weight
        new_weights = [float(old_weights[group].mean()) if center == "mean" else float(old_weights[group[0]])
                       for group in members]
        groups[name] = [[vi, weight] for vi, weight in enumerate(new_weights) if weight != 0]
    out = copy.deepcopy(rig)
    out["weights"] = {**out["weights"], "vertex_count": target_mesh.vertex_count,
                      "topology_sha256": target_mesh.topology_hash(), "groups": groups}
    contracts.validate("rig", out)
    report = checks.structure(out, mesh=target_mesh)
    if not report["pass"]:
        raise ToolError("結合後のリグが出力メッシュと整合しません: " + "; ".join(report["failures"]))
    return out


# ---- 押し出し ------------------------------------------------------------------------

def check_profile(profile):
    """断面は 3 点以上で、重複がなく、厳密に凸で反時計回り（+Z から見て）。違えば ToolError。"""
    points = np.asarray(profile, dtype=np.float64)
    if points.ndim != 2 or points.shape[1] != 2 or len(points) < 3:
        raise ToolError("profile は [[x, y], …]（3 点以上）にしてください")
    if not np.isfinite(points).all():
        raise ToolError("profile に非有限の値があります")
    n = len(points)
    for i in range(n):
        a, b, c = points[i], points[(i + 1) % n], points[(i + 2) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if not cross > 1e-12:
            raise ToolError("profile は厳密に凸で反時計回り（+Z から見て）の断面にしてください（時計回りなら点の順を逆にする。"
                            f"点 {i}-{(i + 1) % n}-{(i + 2) % n} の外積 {cross:.3g}）")
    for i in range(n):   # 曲がりが全部正でも星形は通るので、全ての辺の左側に他の全頂点があることも確かめる（自己交差の拒否）
        a, b = points[i], points[(i + 1) % n]
        others = np.delete(points, [i, (i + 1) % n], axis=0)
        side = (b[0] - a[0]) * (others[:, 1] - a[1]) - (b[1] - a[1]) * (others[:, 0] - a[0])
        if not (side > 1e-12).all():
            raise ToolError(f"profile が凸でないか自己交差しています（辺 {i}-{(i + 1) % n} の右側に頂点があります）")
    return points


def extrude_profile(profile, height, twist_deg=0.0, steps=1, offset=(0.0, 0.0), name=None, units="m", twist_rad=None):
    """凸断面を Z 方向に height だけ押し出す。twist_deg（または twist_rad）は上端までの Z 軸まわりの回転、offset は上端までの XY の移動。

    高さが負のときは先に断面の有向辺を反転してから -Z へ押し出す（外向きの面を保つ）。壁は三角形、蓋は扇形の三角形。
    ねじりが 0 のときは steps に関わらず 1 段で押し出す（JSCAD の extrudeLinear と同じ決まり。steps の検査は 0 でも行う）。
    parts: cap_bottom（z=0）・cap_top（z=height）・wall。
    """
    points = check_profile(profile)
    if isinstance(height, bool) or not isinstance(height, (int, float)) or not math.isfinite(height) or height == 0:
        raise ToolError(f"height は 0 でない有限の数にしてください: {height!r}")
    if isinstance(steps, bool) or not isinstance(steps, int) or steps < 1:
        raise ToolError(f"steps は 1 以上の整数にしてください: {steps!r}")
    if twist_rad is not None:
        if twist_deg:
            raise ToolError("twist_deg と twist_rad は同時に指定できません")
        if isinstance(twist_rad, bool) or not isinstance(twist_rad, (int, float)) or not math.isfinite(twist_rad):
            raise ToolError(f"twist_rad は有限の数にしてください: {twist_rad!r}")
        twist = float(twist_rad)
    else:
        if isinstance(twist_deg, bool) or not isinstance(twist_deg, (int, float)) or not math.isfinite(twist_deg):
            raise ToolError(f"twist_deg は有限の数にしてください: {twist_deg!r}")
        twist = math.radians(float(twist_deg))
    if twist == 0.0:
        steps = 1
    shift = np.asarray(offset, dtype=np.float64)
    if shift.shape != (2,) or not np.isfinite(shift).all():
        raise ToolError(f"offset は [dx, dy] にしてください: {offset!r}")
    if height < 0:
        points = points[::-1].copy()
    n = len(points)
    vertices = []
    for k in range(steps + 1):
        fraction = k / steps
        angle = twist * fraction
        c, s = math.cos(angle), math.sin(angle)
        for x, y in points:
            rx, ry = c * x - s * y, s * x + c * y
            vertices.append([rx + shift[0] * fraction, ry + shift[1] * fraction, height * fraction])
    faces, parts = [], {"cap_bottom": [], "cap_top": [], "wall": []}
    for j in range(1, n - 1):  # 底面: 断面の逆順（法線は押し出しの逆向き）
        faces.append([0, j + 1, j])
        parts["cap_bottom"].append(len(faces) - 1)
    top = steps * n
    for j in range(1, n - 1):
        faces.append([top, top + j, top + j + 1])
        parts["cap_top"].append(len(faces) - 1)
    for k in range(steps):
        lower, upper = k * n, (k + 1) * n
        for i in range(n):
            a, b = lower + i, lower + (i + 1) % n
            c2, d = upper + i, upper + (i + 1) % n
            faces.append([a, b, d])
            parts["wall"].append(len(faces) - 1)
            faces.append([a, d, c2])
            parts["wall"].append(len(faces) - 1)
    return Mesh(vertices, faces, None, name, units, None, parts)


# ---- 法線のならし ------------------------------------------------------------------

def grid_weld(points, step):
    """格子 step に丸めて同じになる点を 1 つにまとめる。戻りは点ごとのまとめた番号（まとめた点の並びは、丸めた座標の辞書順）。
    距離で結ぶ weld と違い、格子の境をまたぐ近い点は別のままになる（リポジトリ外の試作と同じ。UV の継ぎ目で分かれた頂点は
    同じ座標なのでまとまる）。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if not len(points):
        return np.zeros(0, dtype=np.int64)
    _, inverse = np.unique(np.round(points / step).astype(np.int64), axis=0, return_inverse=True)
    return inverse.ravel().astype(np.int64)


def face_edges(faces):
    """面の縁の辺 (E,2)（面の頂点を順に結ぶ。三角形は (0,1)・(1,2)・(2,0)）。faces は (F,k) の配列か、面の頂点番号のリスト。"""
    if isinstance(faces, np.ndarray):
        k = faces.shape[1] if faces.ndim == 2 else 0
        if not len(faces):
            return np.zeros((0, 2), dtype=np.int64)
        return np.concatenate([faces[:, [i, (i + 1) % k]] for i in range(k)]).astype(np.int64)
    pairs = [(face[i], face[(i + 1) % len(face)]) for face in faces for i in range(len(face))]
    return np.asarray(pairs, dtype=np.int64).reshape(-1, 2)


def smooth_normals(vertices, triangles, edges, iterations, step=1e-5):
    """頂点の法線をならす（リポジトリ外の試作の、部品の法線と同じ計算。セル調の影の境が面の細かな凹凸で荒れないように）。

    1. 格子 step で同じ位置の頂点をまとめる（UV の継ぎ目で分かれた頂点も、同じ法線になる）。
    2. まとめた頂点で、面積で重みを付けた法線（三角形の外積を頂点ごとに足し、長さ 1 に）。
    3. iterations 回、辺でつながった隣の法線の和に自分の法線を足し、長さ 1 に戻す。
    4. まとめた法線を、元の頂点へ配る。

    和は float64 で、試作（scipy の疎行列の積）と同じ順に足す（同じ値になる）。戻りは (法線 (N,3) float64, 報告)。
    向かい合う 2 枚の面が重なる所や、面の無い頂点の法線は 0 のまま返し、数を報告に載せる。"""
    if not isinstance(iterations, int) or isinstance(iterations, bool) or iterations < 0:
        raise ToolError(f"iterations は 0 以上の整数にしてください: {iterations!r}")
    if not (isinstance(step, (int, float)) and not isinstance(step, bool) and step > 0 and math.isfinite(step)):
        raise ToolError(f"weld（まとめる格子）は正の数にしてください: {step!r}")
    c = np.asarray(vertices, dtype=np.float64).reshape(-1, 3)
    triangles = np.asarray(triangles, dtype=np.int64).reshape(-1, 3)
    edges = np.asarray(edges, dtype=np.int64).reshape(-1, 2)
    inv = grid_weld(c, step)
    n = int(inv.max()) + 1 if len(inv) else 0
    report = {"vertices": int(len(c)), "welded": n, "iterations": iterations, "weld": float(step)}
    if not n:
        return np.zeros((0, 3)), dict(report, mean_turn_deg=0.0, max_turn_deg=0.0, zero_normals=0, isolated=0)
    welded = np.zeros((n, 3))
    welded[inv] = c                                     # まとめた頂点の位置（試作と同じく、同じ番号の最後の頂点）
    g = inv[triangles]
    face_normal = np.cross(welded[g[:, 1]] - welded[g[:, 0]], welded[g[:, 2]] - welded[g[:, 0]])   # 長さが面積の 2 倍
    area_weighted = np.zeros((n, 3))
    for k in range(3):
        np.add.at(area_weighted, g[:, k], face_normal)
    area_weighted /= np.maximum(np.linalg.norm(area_weighted, axis=1, keepdims=True), 1e-12)
    e = inv[edges]
    key = np.unique(np.concatenate([e, e[:, ::-1]])[:, 0] * n + np.concatenate([e, e[:, ::-1]])[:, 1])
    rows, cols = key // n, key % n                      # 隣どうし（向きを問わず 1 つずつ。行、列の順に並ぶ = 疎行列と同じ足し順）
    v = area_weighted.copy()
    for _ in range(iterations):
        neighbours = np.zeros_like(v)
        np.add.at(neighbours, rows, v[cols])
        v = neighbours + v
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-12)
    nonzero = (np.linalg.norm(v, axis=1) > 0.5) & (np.linalg.norm(area_weighted, axis=1) > 0.5)
    turn = np.degrees(np.arccos(np.clip((v[nonzero] * area_weighted[nonzero]).sum(axis=1), -1, 1)))
    used = np.zeros(n, dtype=bool)
    used[g.ravel()] = True
    report.update(mean_turn_deg=round(float(turn.mean()), 3) if len(turn) else 0.0,
                  max_turn_deg=round(float(turn.max()), 3) if len(turn) else 0.0,
                  zero_normals=int((np.linalg.norm(v, axis=1) <= 0.5).sum()), isolated=int((~used).sum()))
    return v[inv], report
