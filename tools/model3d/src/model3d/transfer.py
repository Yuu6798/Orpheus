"""部位のラベルを、別のメッシュの面へ移す（面の重心どうしの最近傍）。

リポジトリ外の試作で、部品に分けた生成器のメッシュ（分割版）から、分けていない元のメッシュの面に部品の番号を付けた台本を移した。
重心は試作と同じく、頂点の並びの型のまま（npz の float32 なら float32 で）平均し、最近傍は spatial.nearest（scipy なし）で探す。
2 つのメッシュは同じ座標（単位と軸）にあるものとし、合わせ直さない。"""
import hashlib
from pathlib import Path

import numpy as np

from . import checks, spatial
from .io import ToolError, sha256
from .mesh import UNIT_FACTORS, Mesh, _npz_arrays, _part_names, _check_part_geometry


def face_centroids(vertices, faces):
    """面ごとの重心（頂点の平均）。faces は (F,k) の配列か、角の数がまちまちな面のリスト。頂点の型のまま平均する。"""
    vertices = np.asarray(vertices)
    if isinstance(faces, np.ndarray):
        return vertices[faces].mean(axis=1)
    out = np.empty((len(faces), 3), dtype=vertices.dtype)
    sizes = np.array([len(face) for face in faces])
    for k in np.unique(sizes):
        rows = np.flatnonzero(sizes == k)
        out[rows] = vertices[np.array([faces[r] for r in rows], dtype=np.int64)].mean(axis=1)
    return out


def _is_npz(path):
    return Path(path).suffix.lower() == ".npz"


def read_source(path):
    """移し元: 部品ごとの npz（names と co{i}・f{i}）か、parts のある model3d のメッシュ（JSON・OBJ）。
    戻り: (部品の名前, 面の重心 (S,3), 面の部品の番号 (S,), 単位か None, メッシュか None)。"""
    if _is_npz(path):
        names = _part_names(path)
        arrays = _npz_arrays(path, "部品の npz", [key for i in range(len(names)) for key in (f"co{i}", f"f{i}")])
        centroids, labels = [], []
        for i, name in enumerate(names):
            vertices, faces = arrays[f"co{i}"], arrays[f"f{i}"]
            _check_part_geometry(vertices, faces, i, path)
            if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
                raise ToolError(f"f{i}（{name}）が無い頂点を指しています: {path}")
            centroids.append(face_centroids(vertices, faces))
            labels.append(np.full(len(faces), i, dtype=np.int64))
        if not names or not sum(len(c) for c in centroids):
            raise ToolError(f"移し元の部品に面がありません: {path}")
        return names, np.vstack(centroids), np.concatenate(labels), None, None
    mesh = Mesh.load(path)
    if not mesh.parts:
        raise ToolError(f"移し元のメッシュに parts（面の集合）がありません: {path}")
    owner = np.full(mesh.face_count, -1, dtype=np.int64)
    names = list(mesh.parts)
    for i, name in enumerate(names):
        faces = np.asarray(mesh.parts[name], dtype=np.int64)
        twice = faces[owner[faces] >= 0]
        if len(twice):
            raise ToolError(f"面 {twice[:5].tolist()} が 2 つの parts に入っています（{name} と {names[owner[twice[0]]]}）")
        owner[faces] = i
    chosen = np.flatnonzero(owner >= 0)
    centroids = face_centroids(mesh.vertices, [mesh.faces[f] for f in chosen])
    return names, centroids, owner[chosen], mesh.units, mesh


def read_target(path):
    """移し先: 1 つのメッシュの npz（co と faces）か、model3d のメッシュ（JSON・OBJ）。戻り: (面の重心 (F,3), 面の数, 単位か None, メッシュか None)。"""
    if _is_npz(path):
        arrays = _npz_arrays(path, "移し先のメッシュの npz", ["co", "faces"])
        vertices, faces = arrays["co"], arrays["faces"]
        _check_part_geometry(vertices, faces, "", path)
        if faces.size and (faces.min() < 0 or faces.max() >= len(vertices)):
            raise ToolError(f"faces が無い頂点を指しています: {path}")
        return face_centroids(vertices, faces), len(faces), None, None
    mesh = Mesh.load(path)
    return face_centroids(mesh.vertices, mesh.faces), mesh.face_count, mesh.units, mesh


def transfer_parts(source, target, max_distance=None, cell=None):
    """移し先の面ごとに、重心がいちばん近い移し元の面の部品を付ける。max_distance より遠い面は付けない（-1）。
    戻り: (部品の名前, 面ごとの番号 (F,) int32, 移し先のメッシュか None, 報告)。"""
    names, source_centroids, source_labels, source_units, source_mesh = read_source(source)
    target_centroids, faces, target_units, target_mesh = read_target(target)
    if source_mesh is not None and target_mesh is not None:
        checks.require_same_frame(target_mesh, source_mesh)
    if not faces:
        raise ToolError(f"移し先に面がありません: {target}")
    index, distance, search = spatial.nearest(source_centroids, target_centroids, cell)
    labels = source_labels[index].astype(np.int32)
    beyond = np.zeros(len(labels), dtype=bool) if max_distance is None else distance > max_distance
    labels[beyond] = -1
    units = target_units or source_units
    to_mm = UNIT_FACTORS[units] * 1000 if units else 1000.0              # npz は m とみなす
    matched = np.bincount(index[~beyond], minlength=len(source_centroids))
    per_source = np.bincount(source_labels, minlength=len(names))
    per_target = np.bincount(labels[labels >= 0], minlength=len(names))
    report = {"schema_version": "model3d-transfer-parts/0.1", "method": "centroid",
              "source": {"file": Path(source).name, "sha256": sha256(source), "parts": len(names), "faces": int(len(source_centroids))},
              "target": {"file": Path(target).name, "sha256": sha256(target), "faces": int(faces)},
              "units": units or "m（npz はメートルとみなす）",
              "distance": {"median": float(np.median(distance)), "p99": float(np.percentile(distance, 99)), "max": float(distance.max()),
                           "median_mm": round(float(np.median(distance)) * to_mm, 4), "p99_mm": round(float(np.percentile(distance, 99)) * to_mm, 4),
                           "max_mm": round(float(distance.max()) * to_mm, 4)},
              "max_distance": max_distance, "unresolved": int(beyond.sum()),
              "one_to_one": {"matched_once": int((matched == 1).sum()), "unmatched": int((matched == 0).sum()),
                             "matched_more": int((matched > 1).sum())},
              "parts": [{"index": i, "name": name, "source_faces": int(per_source[i]), "target_faces": int(per_target[i])}
                        for i, name in enumerate(names)],
              "search": search}
    return names, labels, target_mesh, report


def labels_digest(names, labels):
    """名前と番号の列の sha256（報告で同じ結果かを見比べるため）。"""
    digest = hashlib.sha256("\n".join(names).encode("utf-8"))
    digest.update(np.ascontiguousarray(labels, dtype=np.int32).tobytes())
    return digest.hexdigest()
