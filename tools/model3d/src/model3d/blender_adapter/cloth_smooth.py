"""Blender の中で動かす: 布で動かした衣装の動きを、骨（腰）から見た座標で時間方向にならし、頂点の動きのファイル（.pc2）に置き換える
（cloth.smooth）。ちらつき（1 コマごとの震え）を消す。

    blender --background --factory-startup --python cloth_smooth.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json --proxy-cache proxy.pc2 [--cache detail.pc2]

なぜ要るか（リポジトリ外の 2 作目の VRM のダンスで分かったこと）: 布の計算は当たり判定のあたりで細かく震え、細かいひだがそれを
増幅する。動きの元のコマで測っても同じなので、コマの間の補間のせいではない。筒だけならしても、細かい裾は筒より大きく揺れたままだった
（貼り付けが筒の小さな向きの変化を遠いひだで増幅するのと、体の外へ押し戻す補正がコマごとに効いたり切れたりするため）。

やること:
  1. 布の筒（proxy。布の計算を焼いたメッシュ）の頂点を、布の計算のコマの範囲（point cache の最初〜最後）の全コマぶん読む。
  2. 骨（bone）から見た座標に直し、1 コマだけの飛びを前後 median コマの中央値で消してから、時間方向に σ sigma コマのガウスで
     ならす（骨に付いて動く所は動かないので、そのまま残る）。端のコマは端の値で延ばす。
  3. 世界の座標に戻して、頂点の動きのファイル（PC2）に書き、筒の変形をすべて外して「このファイルを読む」（Mesh Cache）だけにする。
  4. detail（細かい裾）も、ならした筒に付いて行く動きを読み、σ detail_sigma で同じようにならして、ファイルに置き換える
     （detail_sigma 0 なら細かい裾はそのまま）。読むときは、読む物と、その変形が使う物（貼り付けの相手・押し戻しの相手）だけを残し、
     ほかのメッシュは評価しない（速くするため。保存の前に戻す）。
ファイルは .blend と同じフォルダか、その下に置く（Mesh Cache は .blend からの相対パス //… で読む）。

報告は model3d-cloth-smooth/0.1（ならす前後のずれ。前後のコマの中間からのずれの平均と 99 百分位、動かした量）。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じファイルになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import struct
import sys
import time

import numpy as np

PC2_MAGIC = b"POINTCACHE2" + bytes(1)          # 12 バイト（末尾は 0）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="cloth_smooth.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--proxy-cache", required=True)
    ap.add_argument("--cache", default="")
    return ap.parse_args(argv)


# ---- ならし方とファイルの形（Blender なし。リポジトリ外の台本と同じ式と順。テストする） ----

def jitter(track):
    """前後のコマの中間からのずれ（m）。形は（コマ - 2, 頂点）。"""
    middle = (track[:-2] + track[2:]) / 2
    return np.linalg.norm(track[1:-1] - middle, axis=2)


def gaussian_kernel(sigma):
    """σ sigma コマのガウスの重み（±ceil(3σ) コマ、和 1）と、片側の幅。"""
    reach = int(math.ceil(sigma * 3))
    kernel = np.exp(-0.5 * (np.arange(-reach, reach + 1) / sigma) ** 2)
    return kernel / kernel.sum(), reach


def smooth_in_bone_space(points, bones, sigma, median):
    """頂点（コマ, 頂点, 3。世界の座標）を、骨の行列（コマ, 4, 4）から見た座標に直し、1 コマだけの飛びを median コマの中央値で
    消してから（median が 1 以下なら消さない）、σ sigma コマのガウスでならし、世界の座標に戻す。端は端のコマの値で延ばす。
    戻り: (ならした頂点, 要約)。要約は骨から見たずれ（ならす前・後）と、動かした量（mm）。"""
    inverse = np.linalg.inv(bones)
    local = np.einsum("fij,fvj->fvi", inverse[:, :3, :3], points) + inverse[:, None, :3, 3]
    work = local
    if median > 1:
        half = median // 2
        padded = np.concatenate([np.repeat(local[:1], half, axis=0), local, np.repeat(local[-1:], half, axis=0)], axis=0)
        work = np.median(np.stack([padded[k:k + len(local)] for k in range(median)]), axis=0)
    kernel, reach = gaussian_kernel(sigma)
    padded = np.concatenate([np.repeat(work[:1], reach, axis=0), work, np.repeat(work[-1:], reach, axis=0)], axis=0)
    smoothed = sum(kernel[k] * padded[k:k + len(work)] for k in range(len(kernel)))
    result = np.einsum("fij,fvj->fvi", bones[:, :3, :3], smoothed) + bones[:, None, :3, 3]
    before, after, moved = jitter(local), jitter(smoothed), np.linalg.norm(result - points, axis=2)
    summary = {
        "jitter_mm_before": {"mean": round(float(before.mean() * 1000), 2), "p99": round(float(np.percentile(before, 99) * 1000), 2)},
        "jitter_mm_after": {"mean": round(float(after.mean() * 1000), 2), "p99": round(float(np.percentile(after, 99) * 1000), 2)},
        "moved_mm": {"mean": round(float(moved.mean() * 1000), 2), "p99": round(float(np.percentile(moved, 99) * 1000), 2),
                     "max": round(float(moved.max() * 1000), 1)},
    }
    return result, summary


def pc2_bytes(points, start):
    """PC2（頂点の動きのファイル）のバイト列: 見出し（POINTCACHE2・版 1・頂点の数・最初のコマ・刻み 1・コマの数）と、
    コマごとの頂点（float32、物体の中の座標）。"""
    points = np.asarray(points)
    head = struct.pack("<12siiffi", PC2_MAGIC, 1, points.shape[1], float(start), 1.0, points.shape[0])
    return head + points.astype("<f4").tobytes()


def read_pc2(data):
    """pc2_bytes の逆（テストと確かめのため）。戻り: (頂点（コマ, 頂点, 3）, 最初のコマ, 刻み)。"""
    magic, version, count, start, step, frames = struct.unpack("<12siiffi", data[:32])
    if magic != PC2_MAGIC or version != 1:
        raise ValueError("PC2 のファイルではありません")
    points = np.frombuffer(data[32:], dtype="<f4")
    if points.size != frames * count * 3:
        raise ValueError(f"PC2 の大きさが見出しと合いません（{points.size} と {frames} × {count} × 3）")
    return points.reshape(frames, count, 3), start, step


def relative_to_blend(path, blend):
    """Mesh Cache が読むパス（.blend のフォルダからの相対パス //…。区切りは /）。.blend のフォルダの外なら None。"""
    relative = os.path.relpath(os.path.abspath(path), os.path.dirname(os.path.abspath(blend)))
    if relative == ".." or relative.startswith(".." + os.sep) or os.path.isabs(relative):
        return None
    return "//" + relative.replace(os.sep, "/")


# ---- Blender の中 ----

def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    for path in filter(None, (a.proxy_cache, a.cache)):
        if relative_to_blend(path, a.out_blend) is None:
            raise SystemExit(f"頂点の動きのファイルは、出力の .blend と同じフォルダかその下に置いてください: {path}")
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE"
                 and (not settings.get("armature") or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit("アーマチュアがありません")
    armature = armatures[0]
    if settings["bone"] not in armature.pose.bones:
        raise SystemExit(f"{armature.name} に骨 {settings['bone']} がありません")
    proxy = bpy.data.objects.get(settings["proxy"])
    if proxy is None or proxy.type != "MESH":
        raise SystemExit(f"布の筒のメッシュがありません: {settings['proxy']}")
    cloth = next((modifier for modifier in proxy.modifiers if modifier.type == "CLOTH"), None)
    if cloth is None:
        raise SystemExit(f"{proxy.name} に布の計算（Cloth）がありません")
    if not cloth.point_cache.is_baked:
        raise SystemExit(f"{proxy.name} の布の計算が焼いてありません")
    detail = None
    if settings.get("detail_sigma", 0) > 0:
        detail = bpy.data.objects.get(settings.get("detail") or "")
        if detail is None or detail.type != "MESH":
            raise SystemExit(f"細かい裾のメッシュがありません: {settings.get('detail')}")
        if not a.cache:
            raise SystemExit("細かい裾をならすには --cache が要ります")
    start, end = cloth.point_cache.frame_start, cloth.point_cache.frame_end
    bone = armature.pose.bones[settings["bone"]]
    started = time.time()
    readers = []

    def read_frames(target):
        """全コマの頂点（世界の座標）と骨の行列。"""
        points, bones = [], []
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = target.evaluated_get(depsgraph)
            mesh = evaluated.to_mesh()
            world = np.array(evaluated.matrix_world)
            local = np.empty(len(mesh.vertices) * 3)
            mesh.vertices.foreach_get("co", local)
            points.append(local.reshape(-1, 3) @ world[:3, :3].T + world[:3, 3])
            evaluated.to_mesh_clear()
            bones.append(np.array(armature.matrix_world @ bone.matrix))
        return np.array(points), np.array(bones)

    def replace_with_cache(target, world_points, path):
        """頂点の動きをファイル（PC2）に書き、target の変形を「このファイルを読む」だけに置き換える。外した変形の名前を返す。"""
        inverse_object = np.array(target.matrix_world.inverted())
        object_space = world_points @ inverse_object[:3, :3].T + inverse_object[:3, 3]
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "wb") as handle:
            handle.write(pc2_bytes(object_space, start))
        removed = [modifier.name for modifier in target.modifiers]
        for modifier in list(target.modifiers):
            target.modifiers.remove(modifier)
        reader = target.modifiers.new("baked_motion", "MESH_CACHE")
        reader.cache_format = "PC2"
        # 読む間は絶対パス（開いているのは入力の .blend で、//… は入力のフォルダから解かれる）。保存の前に相対パスへ戻す
        reader.filepath = os.path.abspath(path)
        readers.append((reader, path))
        reader.frame_start = start
        reader.interpolation = "LINEAR"
        reader.deform_mode = "OVERWRITE"
        reader.play_mode = "SCENE"
        reader.time_mode = "FRAME"
        return removed

    def needed_by(target):
        """target の変形が使う物（貼り付けの相手・押し戻しの相手など、モディファイアが指す物）。"""
        found = set()
        for modifier in target.modifiers:
            for name in ("target", "object", "auxiliary_target"):
                value = getattr(modifier, name, None)
                if isinstance(value, bpy.types.Object):
                    found.add(value)
        return found

    # 筒: ほかのメッシュを隠して読む（筒と骨だけ評価すれば足りる）→ ならす → ファイルに置き換える
    hidden = []
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj is not proxy and not obj.hide_viewport:
            obj.hide_viewport = True
            hidden.append(obj)
    points, bones = read_frames(proxy)
    proxy_result, proxy_summary = smooth_in_bone_space(points, bones, settings["sigma"], settings["median"])
    removed = replace_with_cache(proxy, proxy_result, a.proxy_cache)
    report = {"schema_version": "model3d-cloth-smooth/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "bone": settings["bone"], "frames": [int(start), int(end)], "median_window": settings["median"],
              "proxy": {"object": proxy.name, "verts": int(points.shape[1]), "sigma_frames": settings["sigma"],
                        "cache": os.path.basename(a.proxy_cache), "removed_modifiers": removed, **proxy_summary}}

    # 細かい裾: ならした筒に付いて行く動きを読む → もう一度ならす → ファイルに置き換える
    if detail is not None:
        for obj in {detail} | needed_by(detail):
            obj.hide_viewport = False
        points, bones = read_frames(detail)
        detail_result, detail_summary = smooth_in_bone_space(points, bones, settings["detail_sigma"], settings["median"])
        removed = replace_with_cache(detail, detail_result, a.cache)
        report["detail"] = {"object": detail.name, "verts": int(points.shape[1]), "sigma_frames": settings["detail_sigma"],
                            "cache": os.path.basename(a.cache), "removed_modifiers": removed, **detail_summary}
    for obj in hidden:
        obj.hide_viewport = False
    scene.frame_set(scene.frame_start)
    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    # Mesh Cache のパスを、出力の .blend からの相対パス（//…）にして保存する。保存は、開いている（入力の）.blend のフォルダから
    # 見た相対パスを、出力のフォルダから見た相対パスに直す（relative_remap）ので、入力のフォルダから見た相対パスを入れておく
    for reader, path in readers:
        reader.filepath = bpy.path.relpath(os.path.abspath(path))
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True, relative_remap=True)
    report["cache_paths"] = {reader.id_data.name: relative_to_blend(path, out) for reader, path in readers}
    report["seconds"] = round(time.time() - started, 1)
    report["settings"] = settings
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"frames": [int(start), int(end)], "detail": detail is not None, "out_blend": out}))


if __name__ == "__main__":
    main()
