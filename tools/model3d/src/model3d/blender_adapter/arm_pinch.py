"""Blender の中で動かす: 腕の関節のまわり（肩の付け根・肘・手首など）のメッシュが、ねじれで絞ったように細くなっていないかを、コマごとに
測る（check.arm_pinch。測るだけで、.blend は変えない）。

    blender --background --factory-startup --python arm_pinch.py -- --blend dance.blend --settings settings.json --report raw.json

なぜ（リポジトリ外の 3 作目の VRM のダンスで分かったこと）: ねじれを受け持つ骨が無いと、骨が軸のまわりに回ったとき、関節のまわりのメッシュが
飴の包み紙のようにねじれて細くなる（前腕と手が逆向きに ±180° 近く回り、手首と肘がくびれて見えた）。骨の角度（check.twist）だけでなく、
描かれる形そのものが細くなっていないかを確かめる。

測り方（rings の輪ごと）:
  - 初期姿勢で、輪の中心を「骨 at の付け根 + 骨 axis の向き × offset × axis の長さ」に置き、骨 axis に沿った位置が中心の ±band の中にあり、
    骨の軸からの距離が radius より小さく、groups の頂点グループの重みの和が min_weight を超える頂点を、輪の頂点にする（body のメッシュの基本の形）。
  - コマごとに、輪の頂点の「頂点の重心からの距離」の平均（そのコマの骨 axis の向きに垂直な分だけ）を出し、初期姿勢の値で割る。
    1.0 なら初期姿勢と同じ太さ。ねじれて絞られると小さくなる（半回転でほぼ 0）。曲げただけでは大きくは変わらない。
  - body の Armature 以外のモディファイア（押し出しの補正など）は止め、骨の変形だけを見る。ほかのメッシュは評価しない。
  輪の頂点が 6 に満たない輪は、どのコマも 1.0 とし、報告に頂点の数を残す。

報告（raw）は model3d-arm-pinch/0.1: 輪ごとの頂点の数・初期姿勢の太さ・コマごとの比（丸めない）。要約は check.arm_pinch がする。
**検証**: 本物の Blender 5.2.4 で、3 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
CI には Blender が無い（輪の頂点の選び方と太さの式はテストする）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .model_edit import bone_world, world_points
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from model_edit import bone_world, world_points
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"body": None, "rings": [], "frames": None, "armature": None, "min_weight": 0.9}
MIN_RING_VERTS = 6                                     # これより少ない輪は測らない（台本と同じ）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="arm_pinch.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def ring_vertices(rest, centre, axis, band, radius, weight, min_weight):
    """初期姿勢の頂点 rest (N, 3) のうち、輪に入る頂点の番号（小さい順）。axis は単位の向き。台本と同じ式。"""
    along = (rest - centre) @ axis
    radial = np.linalg.norm((rest - centre) - np.outer(along, axis), axis=1)
    return np.where((np.abs(along) < band) & (weight > min_weight) & (radial < radius))[0]


def spread(points, axis):
    """点の重心からの距離の平均（axis に垂直な分だけ）。台本と同じ式。"""
    centre = points.mean(axis=0)
    rel = points - centre
    rel = rel - np.outer(rel @ axis, axis)
    return float(np.linalg.norm(rel, axis=1).mean())


def group_weight(obj, names):
    """頂点ごとの、names の頂点グループの重みの和 (N,)（頂点の中のグループの並びの順に足す。台本と同じ）。"""
    index = {group.index for group in obj.vertex_groups if group.name in names}
    return np.array([sum(item.weight for item in vertex.groups if item.group in index) for vertex in obj.data.vertices])


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = {**DEFAULTS, **json.load(stream)}
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not settings["armature"] or obj.name == settings["armature"])]
    if not armatures:
        raise SystemExit(f"アーマチュアがありません{'（' + settings['armature'] + '）' if settings['armature'] else ''}")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    body = bpy.data.objects.get(settings["body"])
    if body is None or body.type != "MESH":
        raise SystemExit(f"体のメッシュがありません: {settings['body']}")
    for ring in settings["rings"]:
        for key in ("at", "axis"):
            if ring[key] not in armature.data.bones:
                raise SystemExit(f"{armature.name} に骨 {ring[key]} がありません（rings の {ring['name']} の {key}）")
    for obj in bpy.data.objects:
        if obj.type == "MESH" and obj is not body:
            obj.hide_viewport = True                     # 体のほかのメッシュは評価しない（台本と同じ）
    muted = []
    for modifier in body.modifiers:                     # 押し出しの補正などは止めて、骨の変形だけを見る
        if modifier.type != "ARMATURE" and modifier.show_viewport:
            modifier.show_viewport = False
            muted.append(modifier.name)
    world_armature = np.array(armature.matrix_world)
    rest = world_points(body)
    rings, out = [], {}
    for ring in settings["rings"]:
        head, tail = bone_world(armature, ring["axis"])
        axis = tail - head
        length = np.linalg.norm(axis)
        axis = axis / length
        centre = bone_world(armature, ring["at"])[0] + axis * ring["offset"] * length
        chosen = ring_vertices(rest, centre, axis, ring["band"], ring["radius"], group_weight(body, set(ring["groups"])),
                               settings["min_weight"])
        base = spread(rest[chosen], axis) if len(chosen) >= MIN_RING_VERTS else None
        rings.append((ring, chosen, base))
        out[ring["name"]] = {"verts": int(len(chosen)), "rest_spread_mm": None if base is None else round(base * 1000, 3), "ratio": []}
    first, last = settings["frames"] or (scene.frame_start, scene.frame_end)
    frames = list(range(int(first), int(last) + 1))
    for frame in frames:
        scene.frame_set(frame)
        evaluated = body.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        evaluated.to_mesh_clear()
        matrix = np.array(evaluated.matrix_world)
        points = co.reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        for ring, chosen, base in rings:
            if base is None:
                out[ring["name"]]["ratio"].append(1.0)
                continue
            m = world_armature @ np.array(armature.pose.bones[ring["axis"]].matrix)
            axis = m[:3, 1] / np.linalg.norm(m[:3, 1])
            out[ring["name"]]["ratio"].append(spread(points[chosen], axis) / base)
    report = {"schema_version": "model3d-arm-pinch/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "body": body.name, "modifiers_off": muted, "frames": frames, "rings": out}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(frames), "rings": len(out)}))


if __name__ == "__main__":
    main()
