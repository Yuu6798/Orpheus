"""Blender の中で動かす: 別々に骨へ載せた何本かの動き（motion.retarget などの .blend）を、切り替えのコマの表つきで 1 本の並びに組む。

    blender --background --factory-startup --python join_clips.py -- --settings settings.json --out-blend out.blend \
        --report report.json

- 設定（settings）の segments は、出力のコマを区間に分け、区間ごとに使う本（clip）と、その本の 0 秒目を出力の何秒目に置くか
  （offset_s）を決める。出力のコマ T（1 始まり。区間の out は [最初, 最後] で両端を含む）の元のコマは
      その本の最初のキー ＋ round(((T − 1) ÷ fps − offset_s) × fps) を、その本のキーの範囲に収めた値
  （範囲の外のコマは、最初か最後の姿勢で止まる。2D の合成で本を並べる式と同じ）。
- 本ごとに、動き（F カーブのいちばん多いアクション）の F カーブを、元のコマで読む。F カーブの組がそろっていない本・fps が違う本は止める。
  キーの無い F カーブは読まず、組んだ動きでも書き直さない（そのまま残す。数は報告の clips の empty_curves）。
- align が mean のとき、本ごとに、使うコマの腰（hips の骨）の位置の平均が原点（左右・前後。高さは変えない）に来るように、
  腰の位置をずらす（本ごとに、踊り手を画面の中央へ置き直すのと同じ）。
- 最初の区間の本の .blend の動きに、組んだ値を全部のコマのキー（補間は直線）として打ち直し、シーンのコマの範囲を 1〜最後、fps を
  出力の fps にして、別名で保存する（元のファイルは変えない。ほかの本のアクションは持ち込まない）。
  - 読んだ値には F カーブの修飾子（ノイズなど）が掛かっているので、打ち直した F カーブの修飾子は外す（二重に掛けない）。
  - 打ち直した動きを、アーマチュアが再生する動きにし（付いていなければ付け、層つきなら最初のスロットに）、NLA の層は外す
    （vrm.apply_motion と同じ。残すと組んだ動きに混ざる）。
  - motion.retarget の model3d_points_frames（各コマが 1 本の points の何コマ目か）は、組んだ動きには合わないので外す。
  - シーンの model3d_join_clip・model3d_join_frame に、コマごとの本の番号（報告の clips の順）と、その本の元のコマを残す。
- 本ごとに骨へ載せてから組むので、切り替えをまたいで動きがならされることはない（点をつないでから載せると、腕のならしなどが
  切り替えをまたいで、切り替えがぼける）。切り替えのコマ（区間の 2 つ目からの最初のコマ）は、報告の cuts に出す。

**検証**: Windows の Blender 4.5.15 と 5.2.4 で、3 作目の VRM のダンスでリポジトリの外の台本と同じ動きになることを確かめた
（docs/model3d.md）。CI には Blender が無い。
"""
import argparse
import json
import math
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .vrm_apply_motion import fps_settings, use_scene_of
    from .vrm_bake_springs import bone_span, fcurve_bags
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from vrm_apply_motion import fps_settings, use_scene_of
    from vrm_bake_springs import bone_span, fcurve_bags

ALIGNS = ("mean", "none")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="join_clips.py")
    ap.add_argument("--settings", required=True, help="clips（名前 → .blend）・segments・fps・align・hips・armature の JSON")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _frame(value):
    return isinstance(value, int) and not isinstance(value, bool)


def check_segments(segments, clips=None):
    """並びの区間を確かめる。おかしければ ValueError（Blender を起動する前に、工程からも呼ぶ）。

    区間は {clip: 本の名前, out: [最初, 最後]（出力のコマ。1 始まり・両端を含む）, offset_s: 秒}。最初の区間は 1 から始め、
    区間は前の区間の次のコマから隙間なく続ける。clips（本の名前の集まり）を渡すと、無い本を指す区間と、使わない本も止める。"""
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments は区間の並び（1 つ以上）にしてください")
    following = 1
    for position, segment in enumerate(segments):
        label = f"segments[{position}]"
        if not isinstance(segment, dict) or set(segment) != {"clip", "out", "offset_s"}:
            raise ValueError(f"{label} は {{clip: 本の名前, out: [最初, 最後], offset_s: 秒}} にしてください: {segment!r}")
        if not isinstance(segment["clip"], str) or not segment["clip"]:
            raise ValueError(f"{label} の clip は本の名前（入力の名前）にしてください: {segment['clip']!r}")
        out = segment["out"]
        if not (isinstance(out, list) and len(out) == 2 and all(_frame(v) for v in out) and out[0] <= out[1]):
            raise ValueError(f"{label} の out は出力のコマ [最初, 最後]（整数、最初 ≤ 最後）にしてください: {out!r}")
        if out[0] != following:
            raise ValueError(f"{label} の out は {following} コマ目から始めてください（最初の区間は 1 から、ほかは前の区間の次のコマから。"
                             f"隙間も重なりも作らない）: {out!r}")
        if not _number(segment["offset_s"]):
            raise ValueError(f"{label} の offset_s は秒（有限の数）にしてください: {segment['offset_s']!r}")
        following = out[1] + 1
    if clips is not None:
        unknown = sorted({segment["clip"] for segment in segments} - set(clips))
        if unknown:
            raise ValueError(f"segments の clip に無い本があります: {unknown}（あるのは {sorted(clips)}）")
        unused = sorted(set(clips) - {segment["clip"] for segment in segments})
        if unused:
            raise ValueError(f"どの区間にも使わない本があります: {unused}")
    return segments


def clip_order(segments):
    """区間の順に、初めて出る本の名前の並び（最初が、組んだ動きを打ち直す .blend の本）。"""
    order = []
    for segment in segments:
        if segment["clip"] not in order:
            order.append(segment["clip"])
    return order


def cut_frames(segments):
    """切り替えのコマ（区間の 2 つ目からの、最初のコマ。Blender のコマ番号）。"""
    return [segment["out"][0] for segment in segments[1:]]


def source_table(segments, ranges, fps):
    """出力のコマ T（1 から最後まで）ごとの (区間の番号, 本の元のコマ) と、区間ごとの報告。ranges は本の名前 → キーの範囲 (最初, 最後)。

    元のコマ ＝ 最初のキー ＋ round(((T − 1) ÷ fps − offset_s) × fps) をキーの範囲に収めた値（試作の台本と同じ式。0 始まりの
    出力のコマ j ＝ T − 1 で (j ÷ fps − offset_s) × fps を丸める。round は Python の round）。"""
    table, report = [], []
    for index, segment in enumerate(segments):
        first, last = ranges[segment["clip"]]
        count = last - first + 1
        held_start = held_end = 0
        frames = []
        for frame in range(segment["out"][0], segment["out"][1] + 1):
            j = frame - 1
            step = int(round((j / fps - segment["offset_s"]) * fps))
            if step < 0:
                held_start += 1
            elif step > count - 1:
                held_end += 1
            source = first + min(max(step, 0), count - 1)
            frames.append(source)
            table.append((index, source))
        report.append({"clip": segment["clip"], "out": list(segment["out"]), "offset_s": segment["offset_s"],
                       "source": [min(frames), max(frames)], "held_start": held_start, "held_end": held_end})
    return table, report


def bone_channel(data_path):
    """`pose.bones["名前"].location` の (骨の名前, `.location`)。骨の F カーブでなければ None。"""
    span = bone_span(data_path)
    if span is None or data_path[span[1]:span[1] + 1] != "]":
        return None
    return span[0], data_path[span[1] + 1:]


def fcurves_of(action):
    return [curve for bag in fcurve_bags(action) for curve in bag.fcurves]


def main_action(actions):
    """F カーブのいちばん多いアクション（vrm.apply_motion が取り込むのと同じ選び方）。無ければ None。"""
    actions = [action for action in actions if fcurves_of(action)]
    return max(actions, key=lambda action: len(fcurves_of(action))) if actions else None


def keyed_curves(curves):
    """キーのある F カーブだけ（キーの無い F カーブは、値を読まず、組んだ動きでも書き直さずにそのまま残す）。"""
    return [curve for curve in curves if len(curve.keyframe_points)]


def key_range(curves):
    """F カーブの最初と最後のキーのコマ（キーのある F カーブの、最初のキーの最小と最後のキーの最大）。"""
    frames = [point.co[0] for curve in keyed_curves(curves) for point in (curve.keyframe_points[0], curve.keyframe_points[-1])]
    if not frames:
        raise SystemExit("動きのアクションにキーがありません")
    return int(round(min(frames))), int(round(max(frames)))


def strip_modifiers(curves):
    """打ち直す F カーブの修飾子（ノイズなど）を外す。読んだ値には、もう掛かっているので、残すと二重に掛かる。→ 外した数"""
    removed = 0
    for curve in curves:
        for modifier in list(curve.modifiers):
            curve.modifiers.remove(modifier)
            removed += 1
    return removed


def bind_action(armature, action):
    """action を armature が再生する動きにし（付いていなければ付け、層つきのアクションなら最初のスロットに）、NLA の層を外す
    （vrm.apply_motion と同じ。残すと組んだ動きに混ざる）。→ (付け替えたか, 外した層の数)"""
    animation = armature.animation_data_create()
    bound = animation.action is None or animation.action != action
    if bound:
        animation.action = action
        if getattr(action, "slots", None) and len(action.slots):
            animation.action_slot = action.slots[0]
    tracks = list(animation.nla_tracks)
    for track in tracks:
        animation.nla_tracks.remove(track)
    return bound, len(tracks)


def _read_clip(bpy, path, armature_name, hips):
    """本の .blend を開き、動きの F カーブの値（キーの範囲の全部のコマ）と、腰の位置の F カーブ、fps を読む。"""
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(path))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not armature_name or obj.name == armature_name)]
    if len(armatures) != 1:
        raise SystemExit(f"{path}: アーマチュアが {len(armatures)} 個あります（armature で選んでください）")
    scene = use_scene_of(bpy, armatures[0])
    action = main_action(bpy.data.actions)
    if action is None:
        raise SystemExit(f"{path}: 動き（F カーブのあるアクション）がありません")
    every = fcurves_of(action)
    curves = keyed_curves(every)
    first, last = key_range(curves)
    keys = [(curve.data_path, curve.array_index) for curve in curves]
    if len(set(keys)) != len(keys):
        raise SystemExit(f"{path}: 同じ data_path と番号の F カーブが 2 本あります")
    values = {key: [curve.evaluate(frame) for frame in range(first, last + 1)] for key, curve in zip(keys, curves)}
    hips_keys = [key for key in keys if bone_channel(key[0]) == (hips, ".location")]
    return {"keys": keys, "values": values, "range": (first, last), "fps": scene.render.fps / scene.render.fps_base,
            "hips_keys": hips_keys, "action": action.name, "armature": armatures[0].name, "empty_curves": len(every) - len(curves)}


def main(argv=None):
    import bpy  # Blender の中でだけ使える
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    paths = settings["clips"]
    try:
        segments = check_segments(settings["segments"], paths)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    align = settings.get("align", "mean")
    if align not in ALIGNS:
        raise SystemExit(f"align は {ALIGNS} のどれか: {align!r}")
    hips, armature_name = settings["hips"], settings.get("armature") or ""
    order = clip_order(segments)

    # 本を 1 本ずつ開いて読む。最初の区間の本（打ち直す .blend）は最後に開き、そのまま書き換える
    clips = {}
    for name in order[1:] + order[:1]:
        clips[name] = _read_clip(bpy, paths[name], armature_name, hips)
    base = clips[order[0]]
    for name in order[1:]:
        missing = [key for key in base["keys"] if key not in clips[name]["values"]]
        extra = [key for key in clips[name]["keys"] if key not in base["values"]]
        if missing or extra:
            raise SystemExit(f"本 {name} の F カーブが本 {order[0]} とそろっていません（無い {len(missing)} 本、余分 {len(extra)} 本）: "
                             f"{(missing + extra)[:4]}")
    fps = float(settings["fps"]) if settings.get("fps") is not None else base["fps"]
    different = {name: clip["fps"] for name, clip in clips.items() if abs(clip["fps"] - fps) > 1e-6}
    if different:
        raise SystemExit(f"fps が出力の fps（{fps}）と違う本があります: {different}（本ごとに同じ fps で骨へ載せてください）")
    table, segment_report = source_table(segments, {name: clip["range"] for name, clip in clips.items()}, fps)
    total = len(table)

    scene = bpy.context.scene
    armature = bpy.data.objects[base["armature"]]
    action = main_action(bpy.data.actions)
    curves = keyed_curves(fcurves_of(action))

    # 本ごとの腰の位置のずらし（使うコマの、世界の左右・前後の平均を原点へ。高さは変えない）
    offsets = {}
    if align == "mean":
        if len(base["hips_keys"]) != 3:
            raise SystemExit(f"腰の骨 {hips} の位置の F カーブが 3 本そろっていません（align を none にするか、bone_prefix を確かめてください）")
        if hips not in armature.data.bones:
            raise SystemExit(f"腰の骨がありません: {hips}")
        rest = armature.matrix_world @ armature.data.bones[hips].matrix_local
        hips_keys = sorted(base["hips_keys"], key=lambda key: key[1])
        if [key[1] for key in hips_keys] != [0, 1, 2]:
            raise SystemExit(f"腰の骨 {hips} の位置の F カーブの番号が 0・1・2 ではありません: {hips_keys}")
        for name in order:
            clip = clips[name]
            first = clip["range"][0]
            used = sorted({source for (index, source) in table if segments[index]["clip"] == name})
            location = np.array([[clip["values"][key][frame - first] for key in hips_keys] for frame in used])
            world = np.array([list(rest @ Vector(value)) for value in location])
            mean = world.mean(axis=0)
            delta_local = rest.to_3x3().inverted() @ Vector((-mean[0], -mean[1], 0.0))
            offsets[name] = {"mean_world_xy": [round(float(mean[0]), 4), round(float(mean[1]), 4)],
                             "delta_local": [float(v) for v in delta_local],
                             "world_z_range": [round(float(world[:, 2].min()), 3), round(float(world[:, 2].max()), 3)],
                             "keys": hips_keys}

    # 値を全部先に組んでから、打ち直す .blend の F カーブを書き換える
    columns = {}
    for key in base["keys"]:
        column = np.empty(total)
        for frame_index, (index, source) in enumerate(table):
            name = segments[index]["clip"]
            clip = clips[name]
            column[frame_index] = clip["values"][key][source - clip["range"][0]]
            if name in offsets and key in offsets[name]["keys"]:
                column[frame_index] += offsets[name]["delta_local"][key[1]]
        columns[key] = column
    frames = np.arange(1, total + 1, dtype=float)
    modifiers_removed = strip_modifiers(curves)
    for curve in curves:
        points = curve.keyframe_points
        points.clear()
        points.add(total)
        co = np.empty(total * 2)
        co[0::2] = frames
        co[1::2] = columns[(curve.data_path, curve.array_index)]
        points.foreach_set("co", co)
        for point in points:
            point.interpolation = "LINEAR"
        curve.update()
    action_bound, nla_tracks_removed = bind_action(armature, action)

    scene.frame_start, scene.frame_end = 1, total
    try:
        scene.render.fps, scene.render.fps_base = fps_settings(fps)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    removed_points_frames = "model3d_points_frames" in scene
    if removed_points_frames:
        del scene["model3d_points_frames"]
    clip_index = {name: position for position, name in enumerate(order)}
    scene["model3d_join_clip"] = [clip_index[segments[index]["clip"]] for index, _ in table]
    scene["model3d_join_frame"] = [int(source) for _, source in table]
    scene.frame_set(1)
    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)

    report = {"schema_version": "model3d-motion-join/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "action": action.name, "curves": len(curves), "frames": [1, total], "fps": scene.render.fps / scene.render.fps_base,
              "clips": [{"name": name, "keys": list(clips[name]["range"]), "fps": clips[name]["fps"], "action": clips[name]["action"],
                         "empty_curves": clips[name]["empty_curves"]} for name in order],
              "segments": segment_report, "cuts": cut_frames(segments), "align": align, "hips": hips,
              "offsets": {name: {k: v for k, v in item.items() if k != "keys"} for name, item in offsets.items()},
              "points_frames_removed": removed_points_frames, "action_bound": action_bound,
              "nla_tracks_removed": nla_tracks_removed, "modifiers_removed": modifiers_removed}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"frames": total, "cuts": report["cuts"], "out_blend": out}))


if __name__ == "__main__":
    main()
