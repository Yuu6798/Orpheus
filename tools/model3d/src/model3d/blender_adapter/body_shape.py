"""Blender の中で動かす: 体つきを部位ごとに広げる（model.body_shape）。初期姿勢で測って、腕・脚は骨の軸のまわりに、胴は高さごとの
中心線のまわりに広げる。

    blender --background --factory-startup --python body_shape.py -- --blend in.blend --settings settings.json \
        --out-blend out.blend --report report.json

なぜ（リポジトリ外の 2 作目の VRM のダンスで分かったこと）: 画像から作ったモデルは体が細いことがある（ウエスト幅 16 cm など）。
全体を大きくするのではなく、部位ごとに測ってから（check.body_size）、部位ごとの倍率で広げる。

やり方（体のメッシュ body の頂点ごと。頂点グループの重みで混ぜる）:
  - limbs の骨（腕・脚）: 骨の軸からの距離を、骨に沿った位置（0 = 付け根、1 = 先）ごとの倍率 profile で広げる。
  - torso の骨（腰・背骨・胸）: 高さごとの体の中心線（左右は x = 0、前後は胴の頂点の前と後ろの中央）から、幅 width と厚み depth を
    高さ（床から m）ごとの別々の倍率で広げる。中心線は、胴の骨がいちばん重い頂点の、高さ levels ごとの帯（±band、|x| < half_width）
    から決め、[0.25, 0.5, 0.25] でならす。
  - それ以外の骨（頭・首・手・指・足など）: 動かさない。重みで混ぜるので、境目はなだらかにつながる。
  - bands: limbs の骨 1 本だけにほぼ付いた小さな島（腕輪など。max_verts 頂点以下、share を超える割合）は、島ごとに 1 つの倍率 factor
    （模様が歪まないように。腕と同じ倍率で広げると、腕輪のほうが腕より太いままになる）。
  - band_shift: 腕輪を骨に沿って shift（骨の長さに対する割合）だけ先へ動かす。腕の肌は腕輪の下で 2 つの筒に分かれていて（付け根側と
    先側）、腕輪がその継ぎ目を隠している。腕輪だけ動かすと継ぎ目が見えるので、付け根側の筒を伸ばし、先側の筒の端を縮めて、継ぎ目も
    腕輪の下へ動かす。肌が 1 つの筒なら腕輪だけを動かす。
  - follow_torso の物体（裾・帯など）: 胴と同じ広げ方。
骨と色の絵は変えない。形状キーのあるメッシュは、どのキーにも同じ動きを足す。元のファイルは変えない（別名で保存）。

報告は model3d-body-shape/0.1。**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ入力から
同じ形になることを確かめた（docs/model3d.md）。CI には Blender が無い（形の計算はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import (bone_axis, bone_world, group_weights, islands, mesh_edges, profile, top_groups, world_points,
                             write_points)
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import (bone_axis, bone_world, group_weights, islands, mesh_edges, profile, top_groups, world_points,
                            write_points)
    from vrm_apply_motion import use_scene_of

TORSO = {"bones": [], "band": 0.015, "half_width": 0.25, "min_points": 7, "step": 0.02}
BANDS = {"bones": [], "factor": 1.0, "max_verts": 600, "share": 0.95}
SHIFT = {"pairs": [], "shift": 0.0, "seam_inside": 0.07, "seam_gap": 0.02, "keep_from": 0.88, "stretch_from": 0.20,
         "weight_from": 0.3, "weight_span": 0.5}
DEFAULTS = {"body": None, "armature": None, "limbs": [], "torso": None, "bands": None, "band_shift": None, "follow_torso": []}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="body_shape.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def merged(settings):
    out = {**DEFAULTS, **settings}
    for key, defaults in (("torso", TORSO), ("bands", BANDS), ("band_shift", SHIFT)):
        if out.get(key) is not None:
            out[key] = {**defaults, **out[key]}
    return out


def centre_line(points, is_torso, levels, band, half_width, min_points):
    """高さ levels ごとの胴の前後の中央（y）。帯に min_points 未満しか頂点の無い高さは、ほかの高さから直線で補い、
    [0.25, 0.5, 0.25] でならす（台本と同じ）。"""
    centre = []
    for z in levels:
        sel = is_torso & (np.abs(points[:, 2] - z) < band) & (np.abs(points[:, 0]) < half_width)
        centre.append((points[sel, 1].max() + points[sel, 1].min()) / 2 if sel.sum() >= min_points else np.nan)
    centre = np.array(centre)
    good = ~np.isnan(centre)
    if not good.any():
        raise ValueError("胴の中心線を決められません（どの高さの帯にも胴の頂点が足りません。torso の bones・levels・band を確かめてください）")
    centre = np.interp(levels, levels[good], centre[good])
    kernel = np.array([0.25, 0.5, 0.25])
    return np.convolve(np.pad(centre, 1, mode="edge"), kernel, mode="valid")


def torso_moved(points, torso, levels, centre):
    """胴の広げ方: 高さごとの中心線（左右は x = 0）から、幅と厚みを別々の倍率で広げる。表の無い向きは動かさない。"""
    z = points[:, 2]
    moved = points.copy()
    if torso.get("width"):
        moved[:, 0] = points[:, 0] * profile(torso["width"], z)
    if torso.get("depth"):
        middle = np.interp(z, levels, centre)
        moved[:, 1] = middle + (points[:, 1] - middle) * profile(torso["depth"], z)
    return moved


def limb_moved(points, head, tail, table, uniform, factor_uniform):
    """腕・脚の広げ方: 骨の軸からの距離を、骨に沿った位置ごとの倍率で広げる。uniform の頂点（腕輪）は決まった倍率。"""
    axis = np.asarray(tail) - np.asarray(head)
    length = np.linalg.norm(axis)
    axis = axis / length
    relative = points - head
    along = relative @ axis
    radial = relative - np.outer(along, axis)
    factor = profile(table, np.clip(along / length, 0.0, 1.0))
    factor = np.where(uniform, factor_uniform, factor)
    return head + np.outer(along, axis) + radial * factor[:, None]


def find_bands(roots, top, bands):
    """bands の骨 1 本だけにほぼ付いた小さな島（腕輪など）。戻り: 頂点ごとの真偽 (N,)・[[骨, 頂点の数], …]。"""
    is_band = np.zeros(len(roots), dtype=bool)
    found = []
    if not bands or not bands["bones"]:
        return is_band, found
    for root in np.unique(roots):
        members = roots == root
        if members.sum() > bands["max_verts"]:
            continue
        values, numbers = np.unique(top[members], return_counts=True)
        name = values[np.argmax(numbers)]
        if str(name) in bands["bones"] and numbers.max() / members.sum() > bands["share"]:
            is_band |= members
            found.append([str(name), int(members.sum())])
    return is_band, found


def weight_array(groups, name, count):
    """頂点グループ name の頂点ごとの重み (count,)（入っていない頂点は 0）。"""
    out = np.zeros(count)
    for group, index, weight in groups:
        if group == name:
            out[index] = weight
    return out


def shift_bands(points, roots, top, is_band, groups, bones, shift):
    """腕輪を骨に沿って動かし、腕の肌の継ぎ目も腕輪の下へ動かす（台本と同じ計算の順）。戻り: 動かした点・{骨: 報告}。"""
    report = {}
    count = len(points)
    for upper, lower in shift["pairs"]:
        head, tail = bones[upper]
        axis, length = bone_axis(head, tail)
        t = (points - head) @ axis / length
        dominant = top == upper
        band = is_band & dominant
        if not band.any():
            report[upper] = {"band": None}
            continue
        band_root = roots[band][0]
        if len(np.unique(roots[band])) > 1:
            raise ValueError(f"{upper} に腕輪の島が 2 つ以上あります（動かす島を決められません）")
        band = roots == band_root
        skin_roots = [root for root in np.unique(roots[dominant]) if root != band_root]
        band_from, band_to = float(t[band].min()), float(t[band].max())
        moved_t = t.copy()
        amount = np.zeros(count)
        entry = {"band_t": [band_from, band_to], "band_t_after": [band_from + shift["shift"], band_to + shift["shift"]],
                 "moved_cm": shift["shift"] * length * 100}
        shoulder_root = min(skin_roots, key=lambda root: t[(roots == root) & dominant].min()) if skin_roots else None
        hand_root = max(skin_roots, key=lambda root: t[(roots == root) & dominant].max()) if skin_roots else None
        if shoulder_root is not None and shoulder_root != hand_root:
            seam_end = float(t[(roots == shoulder_root) & dominant].max())
            seam_start = float(t[(roots == hand_root) & dominant].min())
            new_end = band_from + shift["shift"] + shift["seam_inside"]
            new_start = new_end + shift["seam_gap"]
            keep, stretch_from = shift["keep_from"], shift["stretch_from"]
            both = weight_array(groups, upper, count) + weight_array(groups, lower, count)
            amount = np.clip((both - shift["weight_from"]) / shift["weight_span"], 0.0, 1.0)   # 腕に付いている頂点だけ動かす
            in_shoulder = (roots == shoulder_root) & (t > stretch_from)
            moved_t[in_shoulder] = stretch_from + (t[in_shoulder] - stretch_from) * (new_end - stretch_from) / (seam_end - stretch_from)
            in_hand = (roots == hand_root) & (t < keep)
            moved_t[in_hand] = keep - (keep - t[in_hand]) * (keep - new_start) / (keep - seam_start)
            entry.update(seam_t=[seam_end, seam_start], seam_t_after=[new_end, new_start])
        else:
            entry.update(seam_t=None, seam_t_after=None)       # 肌が 1 つの筒（継ぎ目が無い）: 腕輪だけ動かす
        moved_t[band] = t[band] + shift["shift"]
        amount = np.where(band, 1.0, amount)
        points = points + np.outer((moved_t - t) * amount * length, axis)
        report[upper] = entry
    return points, report


def reshape_body(points, groups, top, roots, bones, settings):
    """体の頂点 points（世界の座標）を広げた位置 (N, 3) と報告。groups は model_edit.group_weights の並び、bones は
    {骨: (付け根, 先)}（世界の座標）。胴の中心線（levels と centre）も返す（follow_torso の物体に使う）。"""
    count = len(points)
    torso = settings.get("torso")
    levels = centre = None
    is_torso = np.isin(top, torso["bones"]) if torso else np.zeros(count, dtype=bool)
    if torso and (torso.get("width") or torso.get("depth")):
        if torso.get("levels"):
            start, stop, step = torso["levels"]
            levels = np.arange(start, stop, step)
        else:
            z = points[is_torso, 2]
            if not len(z):
                raise ValueError("torso の bones がいちばん重い頂点がありません")
            levels = np.arange(z.min(), z.max(), torso["step"])
        centre = centre_line(points, is_torso, levels, torso["band"], torso["half_width"], torso["min_points"])
    is_band, bands = find_bands(roots, top, settings.get("bands"))
    band_report = {}
    if settings.get("band_shift") and settings["band_shift"]["shift"] != 0:
        points, band_report = shift_bands(points, roots, top, is_band, groups, bones, settings["band_shift"])
    torso_position = torso_moved(points, torso, levels, centre) if centre is not None else points
    limbs = {}
    for entry in settings.get("limbs") or []:
        for bone in entry["bones"]:
            limbs[bone] = entry["profile"]
    torso_bones = set(torso["bones"]) if torso else set()
    factor = settings["bands"]["factor"] if settings.get("bands") else 1.0
    total = np.zeros((count, 3))
    weight_sum = np.zeros(count)
    for name, index, weight in groups:
        if name in limbs:
            target = limb_moved(points[index], *bones[name], limbs[name], is_band[index], factor)
        elif name in torso_bones:
            target = torso_position[index]
        else:
            target = points[index]
        np.add.at(total, index, target * weight[:, None])
        np.add.at(weight_sum, index, weight)
    new_points = np.where(weight_sum[:, None] > 1e-6, total / np.maximum(weight_sum, 1e-6)[:, None], points)
    return new_points, {"bands": bands, "band_moved": band_report, "levels": levels, "centre": centre}


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = merged(json.load(stream))
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not settings["armature"] or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + settings['armature'] + '）' if settings['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    if abs(scene.unit_settings.scale_length - 1.0) > 1e-9:
        raise SystemExit(f"1 Blender 単位 = 1 m のシーンだけを扱います（scale_length {scene.unit_settings.scale_length}）")
    body = bpy.data.objects.get(settings["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"body のメッシュがありません: {settings['body']}")
    follow = []
    for name in settings["follow_torso"]:
        obj = bpy.data.objects.get(name)
        if obj is None or obj.type != "MESH":
            raise SystemExit(f"follow_torso のメッシュがありません: {name}")
        follow.append(obj)
    wanted = {bone for entry in settings["limbs"] for bone in entry["bones"]}
    if settings["band_shift"]:
        wanted |= {upper for upper, _ in settings["band_shift"]["pairs"]}
    absent = sorted(bone for bone in wanted if bone not in armature.data.bones)
    if absent:
        raise SystemExit(f"{armature.name} に無い骨があります（limbs・band_shift）: {absent}")
    group_names = {group.name for group in body.vertex_groups}
    if settings["band_shift"]:
        missing = sorted({name for pair in settings["band_shift"]["pairs"] for name in pair} - group_names)
        if missing:      # 打ち間違いを黙って通さない（重みが 0 のまま、動かす頂点が変わる）
            raise SystemExit(f"band_shift.pairs の骨の頂点グループが {body.name} にありません: {missing}")
    listed = {bone for entry in settings["limbs"] for bone in entry["bones"]} | set((settings["torso"] or {}).get("bones", []))
    unused = sorted(listed - group_names)
    position = armature.data.pose_position
    armature.data.pose_position = "REST"
    bpy.context.view_layer.update()
    points = world_points(body)
    groups = group_weights(body)
    top = top_groups(body)
    roots = islands(len(points), mesh_edges(body.data))
    bones = {name: bone_world(armature, name) for name in sorted(wanted)}
    try:
        new_points, info = reshape_body(points, groups, top, roots, bones, settings)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    shift = np.linalg.norm(new_points - points, axis=1)
    write_points(body, new_points)
    objects = {body.name: {"verts": len(points), "bands": info["bands"], "band_moved": info["band_moved"],
                           "moved_mm_mean": float(shift.mean() * 1000), "moved_mm_max": float(shift.max() * 1000),
                           "moved_verts_over_0_1mm": int((shift > 1e-4).sum())}}
    for obj in follow:
        if info["centre"] is None:
            raise SystemExit("follow_torso には torso の width か depth が要ります（胴の広げ方に付いて行かせるため）")
        before = world_points(obj)
        moved = torso_moved(before, settings["torso"], info["levels"], info["centre"])
        write_points(obj, moved)
        objects[obj.name] = {"verts": len(before), "moved_mm_max": float(np.linalg.norm(moved - before, axis=1).max() * 1000)}
    armature.data.pose_position = position
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True, compress=True)
    report = {"schema_version": "model3d-body-shape/0.1", "blender": bpy.app.version_string, "scene": scene.name,
              "armature": armature.name, "objects": objects, "groups_not_on_body": unused,
              "centre_line": None if info["centre"] is None else {"levels_m": [float(v) for v in info["levels"]],
                                                                  "y_m": [float(v) for v in info["centre"]]},
              "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "objects": len(objects)}))


if __name__ == "__main__":
    main()
