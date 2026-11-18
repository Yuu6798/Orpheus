"""Blender の中で動かす: 目の動きの台本の値を、.blend の動きのキーにする（motion.eye_plan）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python eye_motion.py -- \
        --blend in.blend --settings settings.json --out out.blend --report report.json

settings.json（motion.eye_plan が書く）: object（頭のメッシュ）、radius（目の球の半径 m）、bones（{"R", "L"}。省くと VRM の人型の骨の
割り当て）、armature、plan_fps（台本の fps）、fps_mode（"check"・"set"・"keep"）、frames（[{"frame", "gaze": [横 m, たて m],
"keys": {名前: 値}}]。eye_plan.timeline の frames）。
すること:
- アーマチュアのあるシーンの fps が台本の fps と違うとき（キーは台本の fps でコマに置くので、違う速さで再生される）: check なら止める、
  set ならシーンを台本の fps にしてから打つ（シーンのほかの動きの速さも変わる）、keep ならシーンのまま打つ（ユウの答え 2026-11-18）。
- コマごとに、頭の形状キー（名前 + "_L"・"_R" があれば両方、無ければその名前）の値と、目の骨の回転（XYZ。たて ÷ 半径, 0, 横 ÷ 半径）に
  キーを打つ（形状キーは頭の形状キーの動き、目の骨はアーマチュアの動きに入る。アーマチュアにもう動きがあれば、そのコマの目の骨のキーを
  上書きする）。
- 台本の値が形状キーの範囲（slider_min・slider_max）を出るなら、範囲を広げる（行きすぎ・瞳の伸びで 1 を超えたり負になったりする。広げないと
  Blender が値を範囲に収めてしまう）。
- シーンのコマの範囲を、打つコマが入るまで広げる（もとの範囲は残す。再生や描画が範囲で止まらないように）。
- キーを打っても動かない .blend は書かない: 絶対の形状キー・打つキーを上書きするドライバー・目の骨の拘束・打つキーのミュート・ピン・静止の
  姿勢（REST）は止める（保存するので、この工程では外さない）。
入力の .blend は変えない（別のファイルに書く）。
"""
import argparse
import json
import os
import sys

try:   # パッケージとして読むとき（テスト）
    from .animation_sample import humanoid_map, pick_armature
    from .eye_keys import absolute_keys_problem
    from .eye_rig import eye_bones
    from .eye_sheet import gaze_rotation, key_targets
    from .vrm_apply_motion import use_scene_of
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import humanoid_map, pick_armature
    from eye_keys import absolute_keys_problem
    from eye_rig import eye_bones
    from eye_sheet import gaze_rotation, key_targets
    from vrm_apply_motion import use_scene_of


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="eye_motion.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def key_ranges(frames, names):
    """台本の値が入る形状キーごとの [いちばん小さい値, いちばん大きい値]。"""
    ranges = {}
    for row in frames:
        for name, value in key_targets(row["keys"], names).items():
            low, high = ranges.get(name, (value, value))
            ranges[name] = (min(low, value), max(high, value))
    return ranges


def overridden(driver_paths, keyed_paths):
    """キーを打つ値（keyed_paths。data_path）のうち、ドライバー（driver_paths）が上書きする物（ドライバーは F カーブのキーより強い）。"""
    return sorted(set(driver_paths) & set(keyed_paths))


def fps_action(scene_fps, plan_fps, mode):
    """シーンの fps と台本の fps と、違うときの扱い（mode: "check"・"set"・"keep"）から (すること, 止めるときの説明)。すること は
    "same"（同じ）・"stop"・"set"・"keep"。キーは台本の fps でコマに置くので、違う fps のシーンでは scene ÷ plan 倍の速さで再生される。"""
    if abs(scene_fps - plan_fps) < 1e-6:
        return "same", None
    if mode in ("set", "keep"):
        return mode, None
    return "stop", (f"シーンの fps {scene_fps:g} と台本の fps {plan_fps:g} が違います（このまま打つと、目の動きが {scene_fps / plan_fps:.3g} 倍の"
                    f"速さで再生されます）。fps: \"set\" ならシーンを台本の fps {plan_fps:g} にしてから打ちます（シーンのほかの動きの速さも"
                    "変わる）。fps: \"keep\" ならシーンの fps のまま打ちます（コマを 1 枚ずつ描いて、台本の fps でまとめるとき）")


def fps_setting(plan_fps):
    """台本の fps を Blender のシーンの (fps, fps_base) に（整数ならそのまま、そうでなければ四捨五入した fps と、それで割る数）。"""
    whole = round(plan_fps)
    if abs(whole - plan_fps) < 1e-9:
        return int(whole), 1.0
    return int(whole), whole / plan_fps


def rest_problem(armature_name, pose_position):
    """アーマチュアが静止の姿勢（REST）なら、目の骨の回りのキーが形に出ないので、止めるときの説明。POSE なら None。"""
    if pose_position == "POSE":
        return None
    return (f"{armature_name} は静止の姿勢（REST）になっていて、目の骨の回りのキーが形に出ません。姿勢の位置（POSE）にしてください"
            "（.blend は保存するので、この工程では変えない）")


def frame_range(start, end, first, last):
    """シーンのコマの範囲 [start, end] を、打つコマ [first, last] が入るまで広げる（もとの範囲は残す。狭めない）。"""
    return min(start, first), max(end, last)


def key_state_problem(head_name, muted, pinned):
    """打つ形状キーのうちミュートされている物（muted）や、形状キーを 1 つだけ見せる設定（ピン。pinned）があれば、キーを打っても形が
    変わらないので、止めるときの説明。無ければ None（.blend は保存するので、この工程では外さない）。"""
    if muted:
        return f"打つ形状キーがミュートされていて、キーを打っても形が変わりません: {sorted(muted)}。ミュートを外してください"
    if pinned:
        return f"{head_name} は形状キーを 1 つだけ見せる設定（ピン）になっていて、打った値が形に出ません。ピンを外してください"
    return None


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    head = bpy.data.objects.get(settings["object"])
    if head is None or head.type != "MESH" or head.data.shape_keys is None:
        raise SystemExit(f"形状キーのある頭のメッシュがありません: {settings['object']}")
    problem = absolute_keys_problem(head.name, head.data.shape_keys.use_relative)   # 絶対の形状キーは値のキーで動かせない
    if problem:
        raise SystemExit(problem)
    armature = pick_armature(bpy, settings.get("armature"))
    bones = eye_bones(settings.get("bones"), humanoid_map(armature))
    missing = [name for name in bones.values() if name not in armature.pose.bones]
    if missing:
        raise SystemExit(f"{armature.name} に目の骨がありません: {missing}")
    blocks = head.data.shape_keys.key_blocks
    names = set(blocks.keys()) - {blocks[0].name}
    frames = settings["frames"]
    # 打つキーを、ドライバーや目の骨の拘束が上書きすると、キーは入っても動かない（黙って効かない .blend を書かない）
    key_paths = {f'key_blocks["{name}"].value' for name in key_ranges(frames, names)}
    bone_paths = {f'pose.bones["{bones[side]}"].rotation_euler' for side in ("L", "R")}
    key_motion, bone_motion = head.data.shape_keys.animation_data, armature.animation_data
    clash = (overridden([d.data_path for d in key_motion.drivers] if key_motion else [], key_paths)
             + overridden([d.data_path for d in bone_motion.drivers] if bone_motion else [], bone_paths))
    if clash:
        raise SystemExit(f"打つキーをドライバーが上書きします（ドライバーは F カーブのキーより強い）: {clash}。ドライバーを外してください")
    constrained = [f"{bones[side]}: {c.name}" for side in ("L", "R") for c in armature.pose.bones[bones[side]].constraints if not c.mute]
    if constrained:
        raise SystemExit(f"目の骨の拘束が、打った回りのキーを上書きします: {constrained}。拘束を外すか止めてください")
    problem = key_state_problem(head.name, [name for name in key_ranges(frames, names) if blocks[name].mute], head.show_only_shape_key)
    if problem:
        raise SystemExit(problem)
    problem = rest_problem(armature.name, armature.data.pose_position)   # 静止の姿勢のままだと、目の骨のキーが形に出ない
    if problem:
        raise SystemExit(problem)
    scene = use_scene_of(bpy, armature)                                 # 描くのはアーマチュアのあるシーン
    scene_fps = scene.render.fps / scene.render.fps_base
    action, message = fps_action(scene_fps, settings["plan_fps"], settings.get("fps_mode", "check"))
    if action == "stop":
        raise SystemExit(message)
    fps_report = {"scene": round(scene_fps, 6), "plan": settings["plan_fps"], "action": action}
    if action == "set":                                                 # シーンを台本の fps に（保存する。シーンのほかの動きの速さも変わる）
        scene.render.fps, scene.render.fps_base = fps_setting(settings["plan_fps"])
        fps_report["scene_after"] = round(scene.render.fps / scene.render.fps_base, 6)
    widened = {}
    for name, (low, high) in key_ranges(frames, names).items():
        block = blocks[name]
        if low < block.slider_min or high > block.slider_max:
            widened[name] = {"before": [round(block.slider_min, 4), round(block.slider_max, 4)]}
            block.slider_min, block.slider_max = min(block.slider_min, low), max(block.slider_max, high)
            widened[name]["after"] = [round(block.slider_min, 4), round(block.slider_max, 4)]
    pose_bones = [armature.pose.bones[bones[side]] for side in ("L", "R")]
    for bone in pose_bones:
        bone.rotation_mode = "XYZ"
    keyed, unknown = set(), set()
    for row in frames:
        frame = row["frame"]
        targets = key_targets(row["keys"], names)
        unknown |= {name for name in row["keys"] if not any(t == name or t.startswith(name + "_") for t in targets)}
        for name, value in targets.items():
            blocks[name].value = value
            blocks[name].keyframe_insert("value", frame=frame)
            keyed.add(name)
        rotation = gaze_rotation(row["gaze"], settings["radius"])
        for bone in pose_bones:
            bone.rotation_euler = rotation
            bone.keyframe_insert("rotation_euler", frame=frame, group=bone.name)
    range_report = None
    if frames:                                                          # 再生や描画がシーンの範囲で止まらないように（もとの範囲は残す）
        before = [scene.frame_start, scene.frame_end]
        after = list(frame_range(scene.frame_start, scene.frame_end, frames[0]["frame"], frames[-1]["frame"]))
        scene.frame_start, scene.frame_end = after                      # 広げるだけなので、先に始めを変えても終わりを越えない
        range_report = {"before": before, "after": after}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    report = {"blender": bpy.app.version_string, "object": head.name, "armature": armature.name, "bones": bones,
              "frames": [frames[0]["frame"], frames[-1]["frame"]] if frames else [], "count": len(frames), "keyed": sorted(keyed),
              "unknown_keys": sorted(unknown), "widened": widened, "scene_fps": fps_report, "scene_frames": range_report}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"count": len(frames), "keyed": len(keyed), "unknown_keys": sorted(unknown)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
