"""Blender の中で動かす: 顔の形を参照の絵の比率へ寄せる（model.face_shape）。目より下の頂点を、高さに応じて上へ寄せ、あごを細くする。

    blender --background --factory-startup --python face_shape.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 2 作目の VRM のダンスで分かったこと）: 顔の絵は立ち絵から 2 段階でずれる（頭の絵にするとき、3D にするとき）。
目の間隔をそろえて比べると、3D は目から口・あごまでが長かった（面長）。形 → 色 → 絵の順で詰める（形を先に決めると、絵を描く人が
3D の顔の配置に合わせて描ける）。

やり方: 高さは正面の正射影の視点（view。texture.project_reference と同じ書き方で、from は -Y だけ）の画素で書く。目印は、同じ視点で
texture.render_view に描いた絵で読む。
  - lift: 「今の高さ vy（視点の画素。下へ増える）→ 上へ動かす量 up（画素）」の表。表を 1 画素おきに直線で補間し、σ smooth 画素の
    ガウスで角を丸める（上下が入れ替わる所があれば止める）。
  - 奥行き depth: 顔の正面（y が front 以下）で 1、耳のあたり（y が back 以上）で 0 の段で効かせる（頭の後ろ側と首のつなぎ目は動かさない）。
  - narrow: 上へ動かす量に比例して、横（x）を顔の中心へ詰める（あご先を細く。0 なら何もしない）。
色の絵は頂点に付いて動くので、描かれた鼻や口も一緒に上がる。形状キーのあるメッシュは、どのキーにも同じ動きを足す。元のファイルは変えない。
計算はリポジトリ外の台本と同じ順。**検証**: 本物の Blender 5.2.4 で、その台本と同じ形になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

import numpy as np


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="face_shape.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def lift_curve(anchors, smooth):
    """1 画素おきの高さの格子と、上へ動かす量（角を丸めたもの）。上下が入れ替わる所があれば ValueError。"""
    anchors = sorted(anchors, key=lambda item: item["vy"])
    grid = np.arange(anchors[0]["vy"] - 120.0, anchors[-1]["vy"] + 200.0, 1.0)
    lift = np.interp(grid, [item["vy"] for item in anchors], [item["up"] for item in anchors])
    sigma = smooth
    kernel = np.exp(-0.5 * (np.arange(-4 * sigma, 4 * sigma + 1) / sigma) ** 2)
    kernel /= kernel.sum()
    lift = np.convolve(np.pad(lift, (len(kernel) // 2, len(kernel) // 2), mode="edge"), kernel, mode="valid")
    if np.any(np.diff(grid - lift) <= 0):
        raise ValueError("上へ動かす量が大きすぎて、上下が入れ替わる所があります（lift を小さくするか smooth を大きくしてください）")
    return grid, lift


def shaped(world, settings):
    """頭の頂点（世界の座標 (N, 3)）を寄せた位置と、上へ動かした量（画素）。"""
    view = settings["view"]
    size, span, center_z = view["size"][1], view["span"], view["center"][1]
    pixel = span / size                                  # 視点の 1 画素の長さ（m）
    grid, lift = lift_curve(settings["lift"], settings["smooth"])
    vy = size / 2 - (world[:, 2] - center_z) / pixel
    up_pixels = np.interp(vy, grid, lift)
    front, back = settings["depth"]["front"], settings["depth"]["back"]
    t = np.clip((back - world[:, 1]) / (back - front), 0.0, 1.0)
    weight = t * t * (3 - 2 * t)
    moved = world.copy()
    moved[:, 2] += up_pixels * pixel * weight
    narrow = settings.get("narrow", 0.0)
    if narrow:                                           # 視点の中心の x（顔の中心）へ詰める
        amount = np.clip(up_pixels / max(lift.max(), 1e-6), 0.0, 1.0) * narrow * weight
        center_x = view["center"][0]
        moved[:, 0] = center_x + (moved[:, 0] - center_x) * (1.0 - amount)
    return moved


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = {"smooth": 10.0, "narrow": 0.0, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    head = bpy.data.objects.get(settings["head"])
    if head is None or head.type != "MESH":
        raise SystemExit(f"head のメッシュがありません: {settings['head']}")
    mesh = head.data
    matrix = np.array(head.matrix_world)
    rotation, translation = matrix[:3, :3], matrix[:3, 3]
    local = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get("co", local)
    local = local.reshape(-1, 3).astype(np.float64)
    world = local @ rotation.T + translation
    try:
        moved = shaped(world, settings)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    new_local = ((moved - translation) @ np.linalg.inv(rotation).T).astype(np.float32)      # 台本と同じ戻し方
    if mesh.shape_keys is not None:                     # 形状キーには、基本の形で求めた動きを足す
        delta = new_local.astype(np.float64).reshape(-1) - local.reshape(-1)
        for block in mesh.shape_keys.key_blocks:
            data = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
            block.data.foreach_get("co", data)
            block.data.foreach_set("co", (data + delta).astype(np.float32))
    mesh.vertices.foreach_set("co", new_local.reshape(-1))
    mesh.update()
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    shift = moved[:, 2] - world[:, 2]
    report = {"schema_version": "model3d-face-shape/0.1", "blender": bpy.app.version_string, "head": head.name,
              "vertices": len(world), "moved_over_1mm": int((np.abs(shift) > 0.001).sum()), "max_up_mm": float(shift.max() * 1000),
              "lowest_z_before": float(world[:, 2].min()), "lowest_z_after": float(moved[:, 2].min()),
              "narrowed": bool(settings.get("narrow")), "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "moved": report["moved_over_1mm"]}))


if __name__ == "__main__":
    main()
