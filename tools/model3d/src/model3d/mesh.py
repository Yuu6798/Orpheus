"""JSON メッシュ（model3d-mesh/0.1）と OBJ の読み書き、位相のハッシュ、基本の幾何と 2 つの形状処理。

- `weld`: 距離指定の頂点結合（継ぎ目の二重頂点を 1 つにする）。一般的な「距離で結合」の独自実装で、
  Blockbench のコードは使っていない（Blockbench の結合は選択順に依存する非推移的なもので、ここは推移的に結合する）。
- `extrude_profile`: 凸な断面の押し出しとねじり。高さが負のときは**先に断面の有向辺を反転してから**押し出す
  （面の向きだけを反転する方法では面と体積が合わない、という再構成の実行結果 2026-11-03 を踏まえる）。
どちらも独自実装で、原本ツールとの一致は主張しない。
"""
import math
from pathlib import Path

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
        """扇形に三角形化した (T,3) の頂点番号と、各三角形が属する面の番号 (T,)。"""
        tris, owner = [], []
        for k, face in enumerate(self.faces):
            for j in range(1, len(face) - 1):
                tris.append((face[0], face[j], face[j + 1]))
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
                "nonmanifold_edges": nonmanifold, "edges": len(seen)}

    def topology_hash(self):
        """頂点構成（頂点数と面の頂点番号）だけのハッシュ。頂点番号で領域を指すときは、この値に結び付ける。"""
        return sha256_text(canonical_json({"vertex_count": self.vertex_count, "faces": self.faces}))

    def geometry_hash(self):
        return sha256_text(canonical_json({"vertices": self.vertices.tolist(), "faces": self.faces}))

    def summary(self):
        lo, hi = self.bounds()
        return {"vertices": self.vertex_count, "faces": self.face_count, "bounds": {"min": lo, "max": hi},
                "topology_sha256": self.topology_hash(), "geometry_sha256": self.geometry_hash()}


# ---- OBJ ------------------------------------------------------------------------

def read_obj(path):
    """OBJ を読む（v / vt / f / o / g）。頂点ごとの uv は、各頂点が 1 つの vt にだけ対応するときに持つ。"""
    vertices, uvs, faces, face_uvs, parts = [], [], [], [], {}
    group, units, axes = None, "m", None
    text = Path(path).read_text(encoding="utf-8", errors="replace")
    for number, raw in enumerate(text.splitlines(), 1):
        if raw.startswith(f"# {SCHEMA} "):   # write_obj が書く見出し: units と軸を戻す
            fields = dict(token.split("=", 1) for token in raw.split()[2:] if "=" in token)
            units = fields.get("units", units)
            if fields.get("up") and fields.get("forward"):
                axes = {"up": fields["up"], "forward": fields["forward"]}
            continue
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        head, *rest = line.split()
        try:
            if head == "v" and len(rest) >= 3:
                vertices.append([float(rest[0]), float(rest[1]), float(rest[2])])
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
    return Mesh(vertices, faces, uv, name=Path(path).stem, units=units, axes=axes, parts=parts)


def write_obj(mesh, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    owner = {}
    for name in sorted(mesh.parts):
        for k in mesh.parts[name]:
            owner.setdefault(k, name)
    lines = [f"# {SCHEMA} units={mesh.units} up={mesh.axes.get('up')} forward={mesh.axes.get('forward')}"]
    if mesh.name:
        lines.append(f"o {mesh.name}")
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


def transform(mesh, translate=None, scale=None, rotate_z_deg=0.0, to_units=None):
    """単位の換算（to_units）→ 拡大縮小 → Z 軸まわりの回転 → 平行移動の順に当てた写し。translate は換算後の単位。"""
    out = mesh.copy()
    points = out.vertices
    if to_units is not None:
        if to_units not in UNIT_FACTORS:
            raise ToolError(f"to_units は m / cm / mm から選んでください: {to_units!r}")
        points = points * (UNIT_FACTORS[mesh.units] / UNIT_FACTORS[to_units])
        out.units = to_units
    if scale is not None:
        factor = np.asarray(scale, dtype=np.float64)
        if factor.ndim == 0:
            factor = np.repeat(factor, 3)
        if factor.shape != (3,) or not np.isfinite(factor).all() or (factor == 0).any():
            raise ToolError(f"scale は 0 でない数か 3 要素の列にしてください: {scale!r}")
        points = points * factor
    if isinstance(rotate_z_deg, bool) or not isinstance(rotate_z_deg, (int, float)) or not math.isfinite(rotate_z_deg):
        raise ToolError(f"rotate_z_deg は有限の数にしてください: {rotate_z_deg!r}")
    if rotate_z_deg:
        angle = math.radians(float(rotate_z_deg))
        c, s = math.cos(angle), math.sin(angle)
        rotation = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
        points = points @ rotation.T
    if translate is not None:
        offset = np.asarray(translate, dtype=np.float64)
        if offset.shape != (3,) or not np.isfinite(offset).all():
            raise ToolError(f"translate は 3 要素の列にしてください: {translate!r}")
        points = points + offset
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

    結合した頂点の位置と uv は平均（center="mean"）か、番号が最小の頂点のもの（center="first"）。
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
    if center == "mean":
        for root, members in groups.items():
            vertices[root] = mesh.vertices[members].mean(axis=0)
            if uv is not None:
                uv[root] = mesh.uv[members].mean(axis=0)
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
    out = Mesh(vertices[keep], final_faces, None if uv is None else uv[keep], mesh.name, mesh.units, mesh.axes,
               parts, selection_out, mesh.extra)
    report = {"distance": float(distance), "center": center, "selected": len(chosen), "merged_groups": len(groups),
              "vertices_before": n, "vertices_after": out.vertex_count, "faces_before": mesh.face_count,
              "faces_after": out.face_count, "faces_removed_degenerate": degenerate, "faces_removed_contained": len(contained)}
    return out, report


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
