"""Blender の中で動かす: 骨が自分の軸のまわりにどれだけねじれているかを、コマごとに測る（check.twist。測るだけで、.blend は変えない）。

    blender --background --factory-startup --python twist_measure.py -- --blend dance.blend --settings settings.json --report raw.json

骨の回転（初期姿勢からの、骨自身の回転 matrix_basis）を「向きを変える分（振り）」と「骨の軸（Y）のまわりに回る分（ねじれ）」に分け、
ねじれの角（−180〜180°）を出す。ねじれが大きいと、ねじれを受け持つ骨の無いモデルでは、付け根のメッシュが絞ったように見える
（model.twist_bones で分ける）。メッシュは評価しない（骨だけを動かして速く測る）。
**検証**: 本物の Blender 5.2.4 で、2 作目の VRM のダンスで、リポジトリの外の台本と同じ値になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import math
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import use_scene_of

DEFAULTS = {"bones": [], "frames": None, "armature": None}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="twist_measure.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def twist_deg(w, y):
    """クォータニオン（w, x, y, z）の、骨の軸 Y のまわりのねじれの角（度。−180〜180）。台本と同じ式。"""
    twist = math.degrees(2 * math.atan2(y, w))
    return (twist + 180) % 360 - 180


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
    absent = [name for name in settings["bones"] if name not in armature.pose.bones]
    if absent:
        raise SystemExit(f"{armature.name} に無い骨があります（bones）: {absent}")
    for obj in bpy.data.objects:
        if obj.type == "MESH":
            obj.hide_viewport = True                    # 骨だけ評価すれば足りる（台本と同じ）
    first, last = settings["frames"] or (scene.frame_start, scene.frame_end)
    frames = list(range(int(first), int(last) + 1))
    series = {name: [] for name in settings["bones"]}
    for frame in frames:
        scene.frame_set(frame)
        for name in settings["bones"]:
            q = armature.pose.bones[name].matrix_basis.to_quaternion()
            series[name].append(twist_deg(q.w, q.y))
    report = {"schema_version": "model3d-twist/0.1", "blender": bpy.app.version_string, "scene": scene.name, "armature": armature.name,
              "frames": frames, "twist_deg": series}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False)
        stream.write("\n")
    print(json.dumps({"frames": len(frames), "bones": len(series)}))


if __name__ == "__main__":
    main()
