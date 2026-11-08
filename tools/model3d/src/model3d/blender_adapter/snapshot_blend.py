"""Blender の中で動かす: .blend の中身（メッシュの形・面のつなぎ・uv・属性・置き場所、骨の静止と姿勢、変形した形）を npz と JSON に書き出す。
check.blend_match が 2 つの .blend を比べるのに使う（比べるのは Blender の外の numpy）。

    blender --background --factory-startup --python snapshot_blend.py -- --blend in.blend --out snapshot.npz \
        --meta snapshot.json --settings settings.json

settings.json（check.blend_match が書く）:
- frames: 変形した形を測るコマ（evaluate のとき）。pose_frames: 骨の姿勢を測るコマ（"all" はシーンの全部のコマ）。
- evaluate: 変形した世界の形の sha256 と、等間隔の samples 個の頂点を残すか。
- attributes: 残す属性の名前の並びか "all"（. で始まる内部の属性と position は除く）。
- objects: 比べるメッシュの名前の並び（null は全部）。armature: 骨を読むアーマチュアの名前（null は 1 つだけあるとき）。
リポジトリ外の試作（作り直したキャラと採用版の比べ）と同じ読み方: 頂点は物体の中の座標（float32）、面のつなぎと uv は面の角ごと、
変形した形は float32 の行列で世界へ移した座標。evaluate でないときは、コマを動かす前にモディファイアのビューポートを切る（速くするため）。
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

ATTRIBUTE_KEYS = {"FLOAT": ("value", 1), "INT": ("value", 1), "BOOLEAN": ("value", 1), "INT8": ("value", 1),
                  "FLOAT_VECTOR": ("vector", 3), "FLOAT2": ("vector", 2), "FLOAT_COLOR": ("color", 4), "BYTE_COLOR": ("color", 4),
                  "INT32_2D": ("value", 2), "QUATERNION": ("value", 4)}
DTYPES = {"INT": np.int32, "BOOLEAN": np.bool_, "INT8": np.int32, "INT32_2D": np.int32}


def digest(array):
    return hashlib.sha256(np.ascontiguousarray(array).tobytes()).hexdigest()


def attribute_array(attribute):
    key, width = ATTRIBUTE_KEYS[attribute.data_type]
    dtype = DTYPES.get(attribute.data_type, np.float32)
    values = np.empty(len(attribute.data) * width, dtype=dtype)
    attribute.data.foreach_get(key, values)
    return values.reshape(len(attribute.data), width) if width > 1 else values


def parse_args(argv):
    parser = argparse.ArgumentParser(description="write a snapshot of a .blend for check.blend_match")
    for flag in ("--blend", "--out", "--meta", "--settings"):
        parser.add_argument(flag, required=True)
    return parser.parse_args(argv)


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    scene = bpy.context.scene
    # 物体の置き場所の行列（matrix_world）は、保存したときのコマで変わる（動く物体や親）。どちらのファイルでも、
    # シーンの最初のコマに合わせてから読む（頂点・面・uv・属性は、コマによらないメッシュのデータ）
    scene.frame_set(scene.frame_start)
    bpy.context.view_layer.update()
    arrays, meta = {}, {"schema_version": "model3d-blend-snapshot/0.1", "blender": bpy.app.version_string,
                        "scene": {"start": scene.frame_start, "end": scene.frame_end, "fps": scene.render.fps,
                                  "fps_base": scene.render.fps_base},     # 再生の速さは fps / fps_base
                        "objects": [], "evaluated": {}, "matrix_frame": scene.frame_start}
    wanted = settings.get("objects")
    meshes = [ob for ob in bpy.data.objects if ob.type == "MESH" and (wanted is None or ob.name in wanted)]
    for k, ob in enumerate(meshes):
        me = ob.data
        co = np.empty((len(me.vertices), 3), dtype=np.float32)
        me.vertices.foreach_get("co", co.ravel())
        loops = np.empty(len(me.loops), dtype=np.int32)
        me.loops.foreach_get("vertex_index", loops)
        sizes = np.empty(len(me.polygons), dtype=np.int32)
        me.polygons.foreach_get("loop_total", sizes)
        arrays.update({f"o{k}_co": co, f"o{k}_loops": loops, f"o{k}_sizes": sizes,
                       f"o{k}_matrix": np.asarray(ob.matrix_world, dtype=np.float64)})
        entry = {"name": ob.name, "key": f"o{k}", "vertices": len(co), "polygons": len(sizes), "uv": None, "attributes": {}}
        if me.uv_layers.active:
            uv = np.empty((len(me.loops), 2), dtype=np.float32)
            me.uv_layers.active.data.foreach_get("uv", uv.ravel())
            arrays[f"o{k}_uv"] = uv
            entry["uv"] = me.uv_layers.active.name
        names = settings.get("attributes", "all")
        for i, attribute in enumerate(me.attributes):
            if attribute.name.startswith(".") or attribute.name == "position" or attribute.data_type not in ATTRIBUTE_KEYS:
                continue
            if names != "all" and attribute.name not in names:
                continue
            arrays[f"o{k}_a{i}"] = attribute_array(attribute)
            entry["attributes"][attribute.name] = {"key": f"o{k}_a{i}", "domain": attribute.domain, "type": attribute.data_type}
        meta["objects"].append(entry)
        if not settings.get("evaluate"):
            for modifier in ob.modifiers:
                modifier.show_viewport = False
    armatures = [ob for ob in bpy.data.objects if ob.type == "ARMATURE"]
    if settings.get("armature"):
        armatures = [ob for ob in armatures if ob.name == settings["armature"]]
        if not armatures:
            raise SystemExit(f"アーマチュアがありません: {settings['armature']}")
    if len(armatures) > 1:
        raise SystemExit(f"アーマチュアが {len(armatures)} つあります。armature で名前を選んでください: {[ob.name for ob in armatures]}")
    frames = [int(f) for f in settings.get("frames") or []]
    pose_frames = settings.get("pose_frames")
    pose_frames = (list(range(scene.frame_start, scene.frame_end + 1)) if pose_frames == "all"
                   else [int(f) for f in (pose_frames if pose_frames is not None else frames)])
    arm = armatures[0] if armatures else None
    meta["armature"] = arm.name if arm else None
    meta["bones"] = sorted(b.name for b in arm.pose.bones) if arm else []
    if arm:
        arrays["rest"] = np.array([arm.data.bones[name].matrix_local for name in meta["bones"]], dtype=np.float64)
    meta["pose_frames"] = pose_frames
    poses = []
    for frame in sorted(set(pose_frames) | (set(frames) if settings.get("evaluate") else set())):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        if arm and frame in pose_frames:
            poses.append((frame, np.array([arm.pose.bones[name].matrix for name in meta["bones"]], dtype=np.float64)))
        if settings.get("evaluate") and frame in frames:
            graph = bpy.context.evaluated_depsgraph_get()
            evaluated = {}
            for k, ob in enumerate(meshes):
                ev = ob.evaluated_get(graph)
                mesh = ev.to_mesh()
                co = np.empty((len(mesh.vertices), 3), dtype=np.float32)
                mesh.vertices.foreach_get("co", co.ravel())
                matrix = np.asarray(ev.matrix_world, dtype=np.float32)
                world = co @ matrix[:3, :3].T + matrix[:3, 3]
                count = min(int(settings.get("samples", 512)), len(world))
                picks = np.linspace(0, len(world) - 1, count, dtype=int) if count else np.zeros(0, dtype=int)
                arrays[f"e{frame}_o{k}"] = world[picks]
                evaluated[ob.name] = {"key": f"e{frame}_o{k}", "vertices": len(world), "sha256": digest(world)}
                ev.to_mesh_clear()
            meta["evaluated"][str(frame)] = evaluated
    if arm:
        order = [frame for frame, _ in sorted(poses, key=lambda item: pose_frames.index(item[0]))]
        arrays["poses"] = (np.array([pose for _, pose in sorted(poses, key=lambda item: pose_frames.index(item[0]))])
                           if poses else np.zeros((0, len(meta["bones"]), 4, 4)))
        meta["pose_frames"] = order
    with open(a.out, "wb") as stream:
        np.savez(stream, **arrays)
    with open(a.meta, "w", encoding="utf-8") as stream:
        json.dump(meta, stream, ensure_ascii=False, indent=1)
    print(json.dumps({"objects": len(meshes), "bones": len(meta["bones"]), "pose_frames": len(meta["pose_frames"])}))


if __name__ == "__main__":
    main()
