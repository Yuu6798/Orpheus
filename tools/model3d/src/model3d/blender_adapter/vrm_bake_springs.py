"""Blender の中で動かす: VRM のキャラの揺れもの（VRM 1.0 のばね。VRM Add-on の計算）を、動きに合わせて骨のキーに焼く。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_bake_springs.py -- --blend dance.blend --out-blend out.blend --report report.json \
        [--overrides overrides.json] [--settle 2.0] [--rate 60] [--no-spring skirt] [--addon-module bl_ext.user_default.vrm] \
        [--cuts 120,240 [--cut-settle 2.0]]

- VRM Add-on の揺れものの計算を 1/rate 秒（既定 1/60 秒）刻みで進め、コマごとに揺れものの骨の回転をキーとして打つ。
  最初のコマの姿勢で settle 秒ぶん進めて落ち着かせてから始める。刻みはシーンの fps（fps / fps_base）とコマの範囲から決める。
- --cuts に挙げたコマ（別々に骨へ載せた動きを組んだ踊りの、新しい本が始まるコマ。motion.join_clips の cuts）では、揺れものの状態を
  初期に戻し、そのコマの姿勢で --cut-settle 秒（既定は --settle と同じ）ぶん進めて落ち着かせ直してから、キーを打つ。
  切り替えのコマでは姿勢が飛ぶので、そのまま続けて計算すると、切り替えの直後に裾と髪が大きく跳ね上がる。刻みの数は、
  切り替えのコマから数え直す。
- 動きの側に揺れものの骨のキーがあれば、先に消す（残すとコマを進めるたびに計算の結果が打ち消される）。
- アドオン自身のコマ送りの計算は、焼く間だけ止める（自分で刻みを進めるので、二重に進まないように）。
- --overrides の JSON で、揺れものの設定をこの .blend の中だけ上書きする（元の VRM は変えない）:
  - `{"<揺れものの名前に含まれる文字>": {"<関節の設定の名前>": 値, …}}`（例 `{"sleeve": {"stiffness": 0.2, "gravity_power": 0.9}}`）
  - `"_capsule_radius": {"<骨の名前に含まれる文字>": 半径}`（今あるカプセルの当たり判定の太さ）
  - `"_colliders": [{"bone", "offset", "tail", "radius", "groups"}]`（カプセルの当たり判定を足す。位置は骨から見た座標）
  鍵が `_` で始まるものは、揺れものの名前としては読まない。
- --no-spring に挙げた文字を名前に含む揺れものの骨は、動かさず初期の向きのままにする（布の計算など、別の方法で動かす部位）。

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスでリポジトリの外の台本と同じキーになることを
確かめた。切り替えのコマ（--cuts）は、3 作目の VRM のダンスで、リポジトリの外の台本と同じキーになることを確かめた（docs/model3d.md）。
CI には Blender が無い。
"""
import argparse
import importlib
import json
import os
import sys
import time

IDENTITY = (1.0, 0.0, 0.0, 0.0)


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vrm_bake_springs.py")
    ap.add_argument("--blend", required=True, help="動きを付けた VRM のキャラの .blend")
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    ap.add_argument("--overrides", default="", help="揺れものの設定の上書きの JSON")
    ap.add_argument("--settle", type=float, default=2.0, help="最初のコマで揺れものを落ち着かせる秒数")
    ap.add_argument("--rate", type=float, default=60.0, help="揺れものの計算を 1 秒に何回進めるか")
    ap.add_argument("--no-spring", default="", help="動かさない揺れもの（名前に含まれる文字。カンマ区切り）")
    ap.add_argument("--addon-module", default="bl_ext.user_default.vrm", help="VRM Add-on のモジュール名")
    ap.add_argument("--armature", default="", help="アーマチュアの名前（揺れもののあるものが 1 つだけなら省ける）")
    ap.add_argument("--cuts", default="", help="揺れものを落ち着かせ直す切り替えのコマ（Blender のコマ番号。カンマ区切り）")
    ap.add_argument("--cut-settle", type=float, default=None, help="切り替えのコマで落ち着かせる秒数（既定は --settle と同じ）")
    ap.add_argument("--joined-frames", default="", help="切り替えのコマを読んだ、組んだ動きのコマの範囲（a:b。シーンの範囲と同じかを確かめる）")
    return ap.parse_args(argv)


def cut_list(text):
    """--cuts の「299,596」を、小さい順の整数の並びにする。整数でない・重なる値は ValueError。"""
    values = [part.strip() for part in text.split(",") if part.strip()]
    if not all(value.lstrip("-").isdigit() for value in values):
        raise ValueError(f"--cuts はコマの番号（整数）のカンマ区切りにしてください: {text!r}")
    cuts = [int(value) for value in values]
    if len(set(cuts)) != len(cuts):
        raise ValueError(f"--cuts に同じコマが 2 回あります: {text!r}")
    return sorted(cuts)


def check_cuts(cuts, first, last, joined=""):
    """切り替えのコマが、最初のコマより後で最後のコマまでにあるか（最初のコマは、もともと落ち着かせてから始める）。
    joined（「a:b」）を渡すと、切り替えのコマを読んだ組んだ動きのコマの範囲が、シーンの範囲と同じかも確かめる（別の動きの報告を止める）。"""
    if joined:
        bounds = joined.split(":")
        if len(bounds) != 2 or not all(bound.strip().lstrip("-").isdigit() for bound in bounds):
            raise ValueError(f"--joined-frames は a:b にしてください: {joined!r}")
        if [int(bound) for bound in bounds] != [first, last]:
            raise ValueError(f"切り替えのコマを読んだ組んだ動きのコマの範囲（{joined.replace(':', '〜')}）が、シーンのコマの範囲（{first}〜{last}）と"
                             "違います（同じ動きを組んだときの report を渡してください）")
    outside = [cut for cut in cuts if not first < cut <= last]
    if outside:
        raise ValueError(f"切り替えのコマ {outside} が、最初のコマ（{first}）より後で最後のコマ（{last}）までの範囲にありません")
    return cuts


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _vector(value):
    return isinstance(value, list) and len(value) == 3 and all(_number(v) for v in value)


def check_overrides(overrides):
    """上書きの JSON の形を確かめる。おかしければ ValueError（Blender を起動する前に、工程からも呼ぶ）。"""
    if not isinstance(overrides, dict):
        raise ValueError("上書きは JSON のオブジェクトにしてください")
    for key, values in overrides.items():
        if key == "_capsule_radius":
            if not isinstance(values, dict) or not all(_number(v) and v > 0 for v in values.values()):
                raise ValueError("_capsule_radius は {骨の名前に含まれる文字: 半径（正の数）} にしてください")
        elif key == "_colliders":
            if not isinstance(values, list):
                raise ValueError("_colliders は当たり判定の並びにしてください")
            for spec in values:
                if not isinstance(spec, dict) or set(spec) != {"bone", "offset", "tail", "radius", "groups"} \
                        or not isinstance(spec["bone"], str) or not _vector(spec["offset"]) or not _vector(spec["tail"]) \
                        or not _number(spec["radius"]) or spec["radius"] <= 0 or not isinstance(spec["groups"], list) \
                        or not spec["groups"] or not all(isinstance(group, str) for group in spec["groups"]):
                    raise ValueError(f"_colliders の当たり判定は bone（骨の名前）・offset と tail（数 3 つ）・radius（正の数）・"
                                     f"groups（揺れものの当たりの組の名前の並び）にしてください: {spec!r}")
        elif key.startswith("_"):
            continue                                            # 書き手のメモなど
        elif not isinstance(values, dict) or not all(isinstance(name, str) and (_number(value) or _vector(value))
                                                     for name, value in values.items()):
            raise ValueError(f"揺れもの {key!r} の上書きは {{関節の設定の名前: 数（向きは数 3 つ）}} にしてください")
    return overrides


def bone_span(data_path):
    """`pose.bones["名前"].…` の骨の名前（\\ の逃がしを戻したもの）と、名前の後ろの `]` の位置。骨の F カーブでなければ None。
    Blender は名前の中の `"` と `\\` を `\\"`・`\\\\` にして書く。vrm_apply_motion.py に同じ写しがある。"""
    prefix = 'pose.bones["'
    if not data_path.startswith(prefix):
        return None
    name, index = [], len(prefix)
    while index < len(data_path):
        char = data_path[index]
        if char == "\\" and index + 1 < len(data_path):
            name.append(data_path[index + 1])
            index += 2
        elif char == '"':
            return ("".join(name), index + 1) if data_path[index + 1:index + 2] == "]" else None
        else:
            name.append(char)
            index += 1
    return None


def use_scene_of(bpy, armature):
    """アーマチュアのあるシーンを今のシーンにして返す（作業用のシーンを開いたまま保存されたファイルがある。vrm_apply_motion.py に
    同じ写しがある）。裏の起動でも、読み込んだファイルの窓のシーンを替えれば bpy.context.scene が替わる（Blender 5.2.4 で確かめた）。"""
    scenes = [scene for scene in bpy.data.scenes if armature.name in scene.objects]
    if not scenes:
        raise SystemExit(f"アーマチュア {armature.name} の入ったシーンがありません")
    if bpy.context.scene not in scenes:
        window = bpy.context.window or next(iter(bpy.context.window_manager.windows), None)
        if window is None:
            raise SystemExit(f"アーマチュア {armature.name} のシーン {scenes[0].name} に切り替えられません（窓がありません）")
        window.scene = scenes[0]
    return bpy.context.scene


def keys_of(text):
    return [key.strip() for key in text.split(",") if key.strip()]


def target_steps(frame, first, fps, step):
    """コマ frame までに、最初のコマから計算を何回進めておくか（step 秒刻み）。

    24 fps と 1/60 秒では奇数コマでちょうど .5 になり、割り算の順で丸めの向きが変わる。2 作目の VRM のダンスで使った台本と同じ
    式（経過秒 ÷ step）にしてある。"""
    return int(round((frame - first) / fps / step))


def fcurve_bags(action):
    """層つきのアクションの F カーブの入れ物（channelbag）。古い形式（4.3 まで）のアクションはアクションそのもの。"""
    bags = [bag for layer in getattr(action, "layers", []) for strip in layer.strips for bag in getattr(strip, "channelbags", [])]
    if not bags and hasattr(action, "fcurves"):
        bags = [action]
    return bags


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    if a.settle < 0 or a.rate <= 0:
        raise SystemExit(f"--settle は 0 以上、--rate は正の数にしてください: {a.settle}, {a.rate}")
    cut_settle = a.settle if a.cut_settle is None else a.cut_settle
    if cut_settle < 0:
        raise SystemExit(f"--cut-settle は 0 以上にしてください: {cut_settle}")
    try:
        cuts = cut_list(a.cuts)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    overrides = {}
    if a.overrides:
        with open(a.overrides, encoding="utf-8") as stream:
            try:
                overrides = check_overrides(json.load(stream))
            except ValueError as error:
                raise SystemExit(str(error)) from error
    try:
        handler = importlib.import_module(f"{a.addon_module}.editor.spring_bone1.handler")
    except ImportError as error:
        raise SystemExit(f"VRM Add-on の揺れものの計算を読めません（{a.addon_module}。アドオンを有効にして起動してください）: "
                         f"{error}") from error

    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armatures = [obj for obj in bpy.data.objects if obj.type == "ARMATURE" and (not a.armature or obj.name == a.armature)
                 and len(obj.data.vrm_addon_extension.spring_bone1.springs)]
    if len(armatures) != 1:
        raise SystemExit(f"揺れもののあるアーマチュアが {len(armatures)} 個あります（--armature で選んでください）")
    armature = armatures[0]
    scene = use_scene_of(bpy, armature)
    try:
        check_cuts(cuts, scene.frame_start, scene.frame_end, a.joined_frames)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    action = armature.animation_data.action if armature.animation_data else None
    if action is None:
        raise SystemExit(f"{armature.name} に動き（アクション）がありません")
    spring = armature.data.vrm_addon_extension.spring_bone1

    # 揺れものの設定の上書き（この .blend の中だけ。元の VRM は変えない）
    overridden = {}
    names = [item.vrm_name for item in spring.springs]
    for key, values in overrides.items():
        if key.startswith("_"):
            continue
        matched = [item for item in spring.springs if key in item.vrm_name]
        if not matched:
            raise SystemExit(f"上書きの {key!r} を名前に含む揺れものがありません（あるもの: {names}）")
        for item in matched:
            for joint in item.joints:
                for name, value in values.items():
                    if not hasattr(joint, name):
                        raise SystemExit(f"揺れものの関節に {name!r} という設定はありません（{item.vrm_name}）")
                    setattr(joint, name, value)
            overridden[item.vrm_name] = values
    for key, radius in overrides.get("_capsule_radius", {}).items():
        hit = []
        for collider in spring.colliders:
            if key in collider.node.bone_name and collider.shape_type == "Capsule":
                collider.shape.capsule.radius = radius
                hit.append(f"{collider.node.bone_name}={radius}")
        if not hit:
            raise SystemExit(f"_capsule_radius の {key!r} を骨の名前に含むカプセルの当たり判定がありません")
        overridden.setdefault("_capsule_radius", []).extend(hit)
        bpy.context.view_layer.update()
    for spec in overrides.get("_colliders", []):
        if spec["bone"] not in armature.data.bones:
            raise SystemExit(f"_colliders の骨がありません: {spec['bone']}")
        groups = []
        for group_name in spec["groups"]:
            group = next((candidate for candidate in spring.collider_groups if candidate.vrm_name == group_name), None)
            if group is None:
                raise SystemExit(f"_colliders の当たりの組がありません: {group_name}")
            groups.append(group)
        collider = spring.add_collider(bpy.context, armature)
        collider.node.bone_name = spec["bone"]
        collider.shape_type = "Capsule"
        bpy.context.view_layer.update()
        collider.shape.capsule.offset = spec["offset"]
        collider.shape.capsule.tail = spec["tail"]
        collider.shape.capsule.radius = spec["radius"]
        bpy.context.view_layer.update()
        for group in groups:
            group.add_collider().collider_uuid = collider.uuid
        overridden.setdefault("_colliders", []).append(f'{spec["bone"]} → {", ".join(spec["groups"])}')

    joint_names, still_names = [], []
    skip_keys = keys_of(a.no_spring)
    for item in spring.springs:
        skipped = any(key in item.vrm_name for key in skip_keys)
        for joint in item.joints:
            name = joint.node.bone_name
            if name and name in armature.pose.bones and name not in joint_names:
                joint_names.append(name)
                if skipped:
                    still_names.append(name)
    unused = [key for key in skip_keys if not any(key in item.vrm_name for item in spring.springs)]
    if unused:
        raise SystemExit(f"--no-spring の {unused} を名前に含む揺れものがありません（あるもの: {names}）")
    joints = [armature.pose.bones[name] for name in joint_names]

    # 動きの側の揺れものの骨のキーを消す（残すと、コマを進めるたびに計算の結果が打ち消される）
    removed_curves = 0
    for bag in fcurve_bags(action):
        for curve in list(bag.fcurves):
            span = bone_span(curve.data_path)
            if span is not None and span[0] in joint_names:
                bag.fcurves.remove(curve)
                removed_curves += 1
    for bone in joints:
        bone.rotation_mode = "QUATERNION"
        bone.rotation_quaternion = IDENTITY

    # アドオン自身のコマ送りの計算を止める（自分で刻みを進めるので、二重に進まないように）
    removed_handlers = []
    for handlers in (bpy.app.handlers.frame_change_pre, bpy.app.handlers.depsgraph_update_pre):
        for function in list(handlers):
            if getattr(function, "__module__", "") == handler.__name__:
                handlers.remove(function)
                removed_handlers.append((handlers, function))

    first, last = scene.frame_start, scene.frame_end
    fps = scene.render.fps / scene.render.fps_base
    step = 1.0 / a.rate
    spring.enable_animation = True
    bpy.context.view_layer.objects.active = armature
    scene.frame_set(first)
    bpy.ops.vrm.reset_spring_bone1_animation_state(armature_object_name=armature.name)
    started = time.time()
    # 刻みの間は画面の更新をしない（アドオンがコマ送りのときに進めるのと同じ。メッシュの計算を避けて速くする）
    for _ in range(int(round(a.settle / step))):
        handler.update_pose_bone_rotations(bpy.context, step)

    def settle_again(frame):
        """切り替えのコマで、揺れものを初期の向きと状態に戻し、そのコマの姿勢で落ち着かせ直す。"""
        scene.frame_set(frame)
        for bone in joints:                               # 前のコマまでに打ったキーの向きを捨てる
            bone.rotation_quaternion = IDENTITY
        bpy.context.view_layer.update()
        bpy.ops.vrm.reset_spring_bone1_animation_state(armature_object_name=armature.name)
        for _ in range(int(round(cut_settle / step))):
            handler.update_pose_bone_rotations(bpy.context, step)

    steps_done, base_frame, total_steps = 0, first, 0
    for frame in range(first, last + 1):
        if frame in cuts:                                 # 新しい本が始まるコマ: 落ち着かせ直し、刻みはここから数え直す
            settle_again(frame)
            steps_done, base_frame = 0, frame
        else:
            scene.frame_set(frame)
            goal = target_steps(frame, base_frame, fps, step)
            while steps_done < goal:
                handler.update_pose_bone_rotations(bpy.context, step)
                steps_done += 1
                total_steps += 1
        for bone in joints:
            if bone.name not in still_names:
                bone.keyframe_insert("rotation_quaternion", frame=frame)
    spring.enable_animation = False                       # 以後は焼いたキーで動く
    for name in still_names:                              # 動かさない部位は、骨を初期の向きのままにする
        armature.pose.bones[name].rotation_quaternion = IDENTITY
    for handlers, function in removed_handlers:           # 止めていたアドオンの計算を元に戻す
        handlers.append(function)
    scene.frame_set(first)

    out = os.path.abspath(a.out_blend)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=out, copy=True, compress=True)
    report = {"schema_version": "model3d-vrm-bake-springs/0.1", "blender": bpy.app.version_string, "armature": armature.name,
              "action": action.name, "frames": [first, last], "fps": fps, "rate": a.rate, "settle_s": a.settle,
              "springs": names, "joints": len(joints), "still_joints": len(still_names), "steps": total_steps,
              "removed_curves": removed_curves, "overridden": overridden, "cuts": cuts,
              "cut_settle_s": cut_settle if cuts else None, "bake_seconds": round(time.time() - started, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"joints": len(joints), "steps": total_steps, "out_blend": out}))


if __name__ == "__main__":
    main()
