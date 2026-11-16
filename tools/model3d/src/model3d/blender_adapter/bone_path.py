"""Blender の中で動かす: 動きのコマごとに、骨の付け根の位置（世界の座標）と、骨から決めた点（顔・胸・手の先など）を測る。

    blender --background --factory-startup --python bone_path.py -- --blend in.blend --settings settings.json --out path.json

settings.json（motion.bone_path が書く）: bones（付け根を測る骨の名前の並び）、points（点の名前 → 項の並び。下）、frames（測るコマ。
null ならシーンの frame_start〜frame_end）、armature（アーマチュアの物体の名前。null なら最初のアーマチュア。render.stage と同じ）。
出力（model3d-bone-path/0.1）: 位置は m（シーンの scale_length を掛ける）。骨だけを動かして測る（メッシュの変形は止めて速く
する。後で戻す。.blend は保存しない）。

点（points）: 項の重みつきの平均 Σ weight × 項の値 ÷ Σ weight。項は次のどちらか。
- 骨の項 {"bone", "at", "offset", "weight"}: 骨の付け根から先へ at の割合の所（0 で付け根、1 で先。ちょうど 0 と 1 は端の値そのもの。
  1 より大きい値は先の向こう）を、骨の向きの軸（x・y・z。長さ 1）に沿って offset（m）ずらした点。
- 点の項 {"point", "weight"}: 前に決めた別の点（参照される点を先に計算する。輪は motion.bone_path が止める）。
計算はコマごとに Blender の Vector（単精度）で、リポジトリ外の試作（2 作目の VRM のダンスの演出版）の台本と同じ順に行う
（顔 = 頭の付け根 + 1.2 × (先 − 付け根) − 0.07 × 骨の z 軸、胸 = 両肩の平均 × 0.7 + 両腰の平均 × 0.3、手の先 = 手の骨の先）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from . import stage_render
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import stage_render


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="bone_path.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    return ap.parse_args(argv)


def point_order(points):
    """点の名前を、参照する点が先に来る順に並べる（motion.bone_path が輪を止めてあるので、ここでは並べるだけ）。"""
    done, order = set(), []

    def visit(name):
        if name in done:
            return
        for term in points[name]:
            if "point" in term:
                visit(term["point"])
        done.add(name)
        order.append(name)

    for name in points:
        visit(name)
    return order


def unit_axis(axis):
    """長さ 1 の向き（Blender の Vector なら normalized()、numpy の配列なら長さで割る）。"""
    if hasattr(axis, "normalized"):
        return axis.normalized()
    import numpy as np
    return axis / np.linalg.norm(axis)


def bone_term(head, tail, axes, at, offset, unit):
    """骨の項の値（Blender 単位）。head・tail は世界の座標の付け根と先、axes(k) は骨の k 軸（0: x、1: y、2: z）の世界の向き、
    offset は m（unit で Blender 単位に直す）。ちょうど at 0・1 は付け根と先そのもの（補間の丸めを入れない）。"""
    if at == 0:
        value = head.copy()
    elif at == 1:
        value = tail.copy()
    else:
        value = head + at * (tail - head)
    for k in range(3):
        if offset[k]:
            value = value + (offset[k] / unit) * unit_axis(axes(k))
    return value


def combine(terms, value_of):
    """Σ weight × 項の値 ÷ Σ weight（項の値に重みを掛けて足し、重みの和で割る。試作の (左 + 右) / 2 や 肩 × 0.7 + 腰 × 0.3 と同じ丸め）。"""
    total, weights = None, 0.0
    for term in terms:
        value = value_of(term) * term["weight"]
        total = value if total is None else total + value
        weights += term["weight"]
    return total / weights


def measure_points(points, bone_of, unit):
    """このコマの点（Blender 単位）。bone_of(名前) → (付け根, 先, 軸の関数)。"""
    out = {}
    for name in point_order(points):
        def value_of(term):
            if "point" in term:
                return out[term["point"]]
            head, tail, axes = bone_of(term["bone"])
            return bone_term(head, tail, axes, term["at"], term["offset"], unit)
        out[name] = combine(points[name], value_of)
    return out


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    sc = bpy.context.scene
    if settings.get("armature"):
        arm = bpy.data.objects.get(settings["armature"])
        if arm is None or arm.type != "ARMATURE":
            raise SystemExit(f"アーマチュアの物体がありません: {settings['armature']}")
    else:
        armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
        if not armatures:
            raise SystemExit("アーマチュアがありません")
        arm = armatures[0]
    points_spec = settings.get("points") or {}
    wanted = list(settings["bones"]) + [t["bone"] for terms in points_spec.values() for t in terms if "bone" in t]
    missing = sorted({b for b in wanted if b not in arm.pose.bones})
    if missing:
        raise SystemExit(f"{arm.name} に無い骨: {missing}（bone_prefix を確かめてください）")
    frames = settings.get("frames") or list(range(sc.frame_start, sc.frame_end + 1))
    unit = float(sc.unit_settings.scale_length)
    mods = [(m, m.show_viewport) for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers]
    for m, _ in mods:
        m.show_viewport = False
    positions = {b: [] for b in settings["bones"]}
    measured = {name: [] for name in points_spec}

    def bone_of(name):
        pose = arm.pose.bones[name]
        world = arm.matrix_world
        rotation = world.to_3x3() @ pose.matrix.to_3x3()
        return world @ pose.head, world @ pose.tail, lambda k: rotation.col[k]

    try:
        for f in frames:
            stage_render.set_frame(sc, f)             # 小数のコマは、前のコマとそこからの割合（subframe。render.stage と同じ）
            for b in settings["bones"]:
                p = arm.matrix_world @ arm.pose.bones[b].head
                positions[b].append([p.x * unit, p.y * unit, p.z * unit])
            for name, p in measure_points(points_spec, bone_of, unit).items():
                measured[name].append([p.x * unit, p.y * unit, p.z * unit])
    finally:
        for m, shown in mods:
            m.show_viewport = shown
    out = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "scale_length": unit,
           "fps": sc.render.fps / sc.render.fps_base, "armature": arm.name, "frames": [stage_render.motion_frame(f) for f in frames],
           "bones": positions}
    if points_spec:
        out["points"] = measured
    with open(a.out, "w", encoding="utf-8") as stream:
        json.dump(out, stream)
    print(f"BONE_PATH {len(frames)} frames, {len(positions)} bones, {len(measured)} points", flush=True)


if __name__ == "__main__":
    main()
