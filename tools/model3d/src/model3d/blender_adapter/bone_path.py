"""Blender の中で動かす: 動きのコマごとに、骨の付け根の位置（世界の座標）を測る。

    blender --background --factory-startup --python bone_path.py -- --blend in.blend --settings settings.json --out path.json

settings.json（motion.bone_path が書く）: bones（骨の名前の並び）、frames（測るコマ。null ならシーンの frame_start〜frame_end）、
armature（アーマチュアの物体の名前。null なら最初のアーマチュア。render.stage と同じ）。
出力（model3d-bone-path/0.1）: 位置は m（シーンの scale_length を掛ける）。骨だけを動かして測る（メッシュの変形は止めて速く
する。後で戻す。.blend は保存しない）。
"""
import argparse
import json
import os
import sys


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="bone_path.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    return ap.parse_args(argv)


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
    missing = [b for b in settings["bones"] if b not in arm.pose.bones]
    if missing:
        raise SystemExit(f"{arm.name} に無い骨: {missing}（bone_prefix を確かめてください）")
    frames = settings.get("frames") or list(range(sc.frame_start, sc.frame_end + 1))
    unit = float(sc.unit_settings.scale_length)
    mods = [(m, m.show_viewport) for o in bpy.data.objects if o.type == "MESH" for m in o.modifiers]
    for m, _ in mods:
        m.show_viewport = False
    points = {b: [] for b in settings["bones"]}
    try:
        for f in frames:
            sc.frame_set(int(f))
            for b in settings["bones"]:
                p = arm.matrix_world @ arm.pose.bones[b].head
                points[b].append([p.x * unit, p.y * unit, p.z * unit])
    finally:
        for m, shown in mods:
            m.show_viewport = shown
    out = {"schema_version": "model3d-bone-path/0.1", "unit": "m", "scale_length": unit,
           "fps": sc.render.fps / sc.render.fps_base, "armature": arm.name, "frames": [int(f) for f in frames], "bones": points}
    with open(a.out, "w", encoding="utf-8") as stream:
        json.dump(out, stream)
    print(f"BONE_PATH {len(frames)} frames, {len(points)} bones", flush=True)


if __name__ == "__main__":
    main()
