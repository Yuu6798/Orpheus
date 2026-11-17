"""Blender の中で動かす: 計算した形（頂点の動きの npz。model.face_shapes の shapes）を、メッシュの形状キーとして入れる（model.shape_keys）。

    blender --background --factory-startup --python shape_keys.py -- --blend in.blend --shapes shapes.npz --settings settings.json \
        --out-blend out.blend --report report.json

- 形の名前ごとの動き (N, 3) は世界の座標（m）。物体の置き場所の行列の 3×3 の逆で物体の中の座標へ移し（シーンの scale_length で Blender の単位に直す）、
  基準のキーの形に足した形状キーにする。並びは npz の並び。値は 0、範囲は 0〜1。
- 形状キーが無ければ Basis を足す。同じ名前のキーがあれば止める（settings の replace なら、消してから足し直す。並びの最後になる）。
  形状キーが絶対（Relative のチェックなし。時間で形を選ぶ）なら止める（値で入り切りできないため。model.closed_eye_key と同じ）。
- 元のファイルは変えない。
計算はリポジトリ外の 3 作目の VRM のダンスの台本と同じ順。**検証**: 本物の Blender 5.2.4 で、その台本と同じ形状キーになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="shape_keys.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--shapes", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def read_shapes(path, count):
    """形の npz を、並びの順に [(名前, 動き (count, 3) float64)] で読む。形・有限・頂点の数が合わなければ ValueError。pickle は読まない。"""
    out = []
    with np.load(path, allow_pickle=False) as data:
        if not data.files:
            raise ValueError("形の npz に形がありません")
        for name in data.files:
            delta = np.asarray(data[name])
            if delta.ndim != 2 or delta.shape[1] != 3 or not np.issubdtype(delta.dtype, np.floating):
                raise ValueError(f"形 {name} は頂点の動き (N, 3) の数にしてください（形 {delta.shape}、{delta.dtype}）")
            if delta.shape[0] != count:
                raise ValueError(f"形 {name} の頂点の数 {delta.shape[0]} が、メッシュの頂点の数 {count} と違います")
            if not np.isfinite(delta).all():
                raise ValueError(f"形 {name} に非有限の値があります")
            out.append((name, delta.astype(np.float64)))
    return out


def reference_problem(names, reference):
    """形の名前に、基準のキー（Basis など）と同じ名前があればその説明（無ければ None）。入れ直すと基準のキーを消してしまうため。"""
    if reference in names:
        return f"形 {reference} は基準のキーと同じ名前です（形の名前を変えてください）"
    return None


def shape_coordinates(base, delta, matrix, unit=1.0):
    """基準のキーの形 base（物体の中の座標）に、世界の座標の動き delta（m）を足した形状キーの座標。matrix は Blender から読んだ置き場所の
    行列のまま（5.2.4 は単精度。台本と同じ型で逆にする）。"""
    to_local = np.linalg.inv(matrix[:3, :3])
    return base + (delta / unit if unit != 1.0 else delta) @ to_local.T


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    obj = bpy.data.objects.get(settings["object"])
    if obj is None or obj.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    mesh = obj.data
    count = len(mesh.vertices)
    try:
        shapes = read_shapes(a.shapes, count)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    keys = mesh.shape_keys
    if keys is not None and not keys.use_relative:       # 絶対の形状キーは値でなく時間（eval_time）で形を選ぶ
        raise SystemExit(f"{obj.name} の形状キーは絶対（Relative のチェックなし）で、時間で形を選びます。表情の形状キーは値で入り切りするので、"
                         "相対の形状キーのメッシュ（または形状キーの無いメッシュ）にだけ足せます")
    reference_name = keys.reference_key.name if keys is not None else "Basis"
    clash = reference_problem([name for name, _ in shapes], reference_name)
    if clash:
        raise SystemExit(clash)
    existing = [name for name, _ in shapes if keys is not None and name in keys.key_blocks]
    if existing and not settings.get("replace"):
        raise SystemExit(f"{obj.name} に同じ名前の形状キーがもうあります（replace で入れ直せます）: {existing}")
    if mesh.shape_keys is None:
        obj.shape_key_add(name="Basis", from_mix=False)
    reference = mesh.shape_keys.reference_key
    base = np.empty(count * 3)
    reference.data.foreach_get("co", base)
    base = base.reshape(-1, 3)
    matrix = np.array(obj.matrix_world)
    scene = obj.users_scene[0] if obj.users_scene else bpy.context.scene
    unit = float(scene.unit_settings.scale_length)       # 1 Blender 単位が何 m か（動きは m）
    added = []
    for name, delta in shapes:
        old = mesh.shape_keys.key_blocks.get(name)
        if old is not None:
            obj.shape_key_remove(old)
        key = obj.shape_key_add(name=name, from_mix=False)
        key.data.foreach_set("co", shape_coordinates(base, delta, matrix, unit).reshape(-1))
        key.value = 0.0
        key.slider_min, key.slider_max = 0.0, 1.0
        moved = np.linalg.norm(delta, axis=1)
        added.append({"name": name, "moved_vertices": int((moved > 1e-6).sum()), "max_mm": round(float(moved.max() * 1000), 2),
                      "replaced": old is not None})
    mesh.update()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True)
    report = {"schema_version": "model3d-shape-keys/0.1", "blender": bpy.app.version_string, "object": obj.name, "vertices": count,
              "reference_key": reference.name, "keys": added, "all_keys": [k.name for k in mesh.shape_keys.key_blocks], "scale_length": unit,
              "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "keys": len(added)}))


if __name__ == "__main__":
    main()
