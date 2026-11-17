"""Blender の中で動かす: VRM のキャラの揺れもの（VRM 1.0 のばね。VRM Add-on の計算）を、動きに合わせて骨のキーに焼く。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_bake_springs.py -- --blend dance.blend --out-blend out.blend --report report.json \
        [--overrides overrides.json] [--settle 2.0] [--rate 60] [--no-spring skirt] [--addon-module bl_ext.user_default.vrm] \
        [--cuts 120,240 [--cut-settle 2.0]] [--carry 300:308 [--carry-bone hips] [--carry-skip skirt_08]]

- VRM Add-on の揺れものの計算を 1/rate 秒（既定 1/60 秒）刻みで進め、コマごとに揺れものの骨の回転をキーとして打つ。
  最初のコマの姿勢で settle 秒ぶん進めて落ち着かせてから始める。刻みはシーンの fps（fps / fps_base）とコマの範囲から決める。
- --cuts に挙げたコマ（別々に骨へ載せた動きを組んだ踊りの、新しい本が始まるコマ。motion.join_clips の cuts）では、揺れものの状態を
  初期に戻し、そのコマの姿勢で --cut-settle 秒（既定は --settle と同じ）ぶん進めて落ち着かせ直してから、キーを打つ。
  切り替えのコマでは姿勢が飛ぶので、そのまま続けて計算すると、切り替えの直後に裾と髪が大きく跳ね上がる。刻みの数は、
  切り替えのコマから数え直す。
- --carry に挙げた区間（a:b。b を含む。カンマ区切りで何区間でも）のコマでは、計算を進める前に、揺れものの節が覚えている位置
  （VRM Add-on は、節ごとに animation_state の previous_world_translation と current_world_translation に世界の座標で持つ）を、
  --carry-bone（既定は VRM の人型の hips の骨）の前のコマからこのコマへの動きで写す。揺れものから見ると腰が止まっていたことになり、
  体が急に落ちる・上がる勢いが揺れものに伝わらない（脚の当たりや硬さはそのまま効く）。--carry-skip に挙げた文字を名前に含む揺れものは
  運ばない（勢いをそのまま伝える）。切り替えのコマでは運ばない（落ち着かせ直すので）。
- 動きの側に揺れものの骨のキーがあれば、先に消す（残すとコマを進めるたびに計算の結果が打ち消される）。
- アドオン自身のコマ送りの計算は、焼く間だけ止める（自分で刻みを進めるので、二重に進まないように）。
- --overrides の JSON で、揺れものの設定をこの .blend の中だけ上書きする（元の VRM は変えない）:
  - `{"<揺れものの名前に含まれる文字>": {"<関節の設定の名前>": 値, …}}`（例 `{"sleeve": {"stiffness": 0.2, "gravity_power": 0.9}}`）
  - `"_capsule_radius": {"<骨の名前に含まれる文字>": 半径}`（今あるカプセルの当たり判定の太さ）
  - `"_colliders": [{"bone", "offset", "tail", "radius", "groups"}]`（カプセルの当たり判定を足す。位置は骨から見た座標）
  - `"_release_walls": [{"springs", "group", "bones" か "frames", "on_deg", "off_deg", "radius_to", …}]`（姿勢に応じて、揺れものの
    「壁」の球を小さくする。下の「壁をゆるめる」）
  鍵が `_` で始まるものは、揺れものの名前としては読まない。
- 壁をゆるめる（_release_walls）: springs の文字を名前に含む揺れものが使う、名前に group を含む当たりの組の球（壁）を、姿勢に応じて
  小さくする。bones の骨（付け根から先の向き）が、reference の骨（既定は VRM の人型の hips）から見て、静止の姿勢の向きから何度離れたかを
  測り、全部が on_deg 以上になったら小さくし始め、どれかが off_deg 以下になったら戻し始める（間の角度では今の状態を続ける）。
  bones の代わりに frames [最初, 最後] と書くと、コマの区間で決める。小さくするのは ease_in_s 秒（既定 0.2）、戻すのは ease_out_s 秒
  （既定 0.3）かけて radius_to（m）まで（なだらかに）。guard_bone を書くと、小さくしている間だけ「見張りの当たり」を立てる: 同じ球を、
  静止の姿勢の位置のまま guard_bone に付けたもので、ふだんは半径 guard_radius_from（既定 0.01 m。効かない）、小さくしている間は
  元の半径 × guard_radius_scale（既定 1）。壁の代わりに、鎖が逆の側へ巻き込まれるのを止める。焼き終えたら、球は元の大きさに戻し、
  見張りの当たりは外して保存する（当たりの数は元のまま）。VRM の当たりの大きさは姿勢で変えられないので、この手当ては VRM に入らない。
- --no-spring に挙げた文字を名前に含む揺れものの骨は、動かさず初期の向きのままにする（布の計算など、別の方法で動かす部位）。

**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、2 作目の VRM のダンスでリポジトリの外の台本と同じキーになることを
確かめた。切り替えのコマ（--cuts）と運ぶ区間（--carry）は、3 作目の VRM のダンスで、リポジトリの外の台本と同じキーになることを
確かめた（docs/model3d.md）。
CI には Blender が無い。
"""
import argparse
import importlib
import json
import math
import os
import sys
import time

IDENTITY = (1.0, 0.0, 0.0, 0.0)
RELEASE_KEYS = {"springs", "group", "bones", "frames", "reference", "on_deg", "off_deg", "radius_to", "ease_in_s", "ease_out_s",
                "guard_bone", "guard_radius_from", "guard_radius_scale"}
# 壁をゆるめるときの既定（3 作目の VRM のダンスで使った値。小さくする・戻す秒と、見張りの当たりのふだんの半径と倍率）
RELEASE_DEFAULTS = {"ease_in_s": 0.2, "ease_out_s": 0.3, "guard_radius_from": 0.01, "guard_radius_scale": 1.0}


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
    ap.add_argument("--carry", default="", help="揺れものの節を --carry-bone の動きで運ぶ区間（a:b。b を含む。カンマ区切り）")
    ap.add_argument("--carry-bone", default="", help="運ぶ骨（既定は VRM の人型の hips の骨）")
    ap.add_argument("--carry-skip", default="", help="運ばない揺れもの（名前に含まれる文字。カンマ区切り）")
    return ap.parse_args(argv)


def carry_frames(text):
    """--carry の「300:308,410:412」を、運ぶコマ（両端を含む）の小さい順の並びにする。おかしければ ValueError。"""
    frames = set()
    for part in (part.strip() for part in text.split(",")):
        if not part:
            continue
        bounds = part.split(":")
        if len(bounds) != 2 or not all(bound.strip().lstrip("-").isdigit() for bound in bounds):
            raise ValueError(f"--carry は a:b（コマの番号。b を含む）のカンマ区切りにしてください: {text!r}")
        first, last = (int(bound) for bound in bounds)
        if first > last:
            raise ValueError(f"--carry の区間は a ≤ b にしてください: {part!r}")
        frames.update(range(first, last + 1))
    return sorted(frames)


def check_carry(frames, first, last):
    """運ぶコマが、シーンのコマの範囲の中にあるか。"""
    outside = [frame for frame in frames if not first <= frame <= last]
    if outside:
        raise ValueError(f"運ぶコマ {outside[:4]}{'…' if len(outside) > 4 else ''} が、シーンのコマの範囲（{first}〜{last}）の外にあります")
    return frames


def humanoid_bone(armature, role):
    """VRM Add-on の人型の骨の割り当て（VRM 1.0）で、役割 role（hips など）に割り当てた骨の名前。無ければ ""。"""
    extension = getattr(armature.data, "vrm_addon_extension", None)
    human_bones = getattr(getattr(getattr(extension, "vrm1", None), "humanoid", None), "human_bones", None)
    node = getattr(getattr(human_bones, role, None), "node", None)
    return getattr(node, "bone_name", "") or ""


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


def _finite(value):
    return _number(value) and math.isfinite(value)


def _names(value):
    return isinstance(value, list) and bool(value) and all(isinstance(name, str) and name for name in value)


def check_release(spec, position):
    """_release_walls の 1 つ（壁をゆるめる指定）を確かめる。おかしければ ValueError。"""
    label = f"_release_walls[{position}]"
    if not isinstance(spec, dict):
        raise ValueError(f"{label} は辞書にしてください: {spec!r}")
    unknown = sorted(set(spec) - RELEASE_KEYS)
    if unknown:
        raise ValueError(f"{label} の知らない鍵: {unknown}（使えるのは {sorted(RELEASE_KEYS)}）")
    if not _names(spec.get("springs")):
        raise ValueError(f"{label} の springs は、ゆるめる揺れものの名前に含まれる文字（空でない）の並びにしてください: {spec.get('springs')!r}")
    if not isinstance(spec.get("group"), str) or not spec["group"]:
        raise ValueError(f"{label} の group は、当たりの組の名前に含まれる文字（空でない）にしてください: {spec.get('group')!r}")
    if ("bones" in spec) == ("frames" in spec):
        raise ValueError(f"{label} には bones（見張る骨）か frames（コマの区間）のどちらか 1 つを書いてください")
    if "bones" in spec:
        if not _names(spec["bones"]):
            raise ValueError(f"{label} の bones は、見張る骨の名前（空でない）の並びにしてください: {spec['bones']!r}")
        on, off = spec.get("on_deg"), spec.get("off_deg")
        if not (_finite(on) and _finite(off) and 0 <= off <= on <= 180):
            raise ValueError(f"{label} の on_deg と off_deg は 0 ≤ off_deg ≤ on_deg ≤ 180 の角度にしてください: {on!r}, {off!r}")
        if "reference" in spec and not (isinstance(spec["reference"], str) and spec["reference"]):
            raise ValueError(f"{label} の reference は基準の骨の名前にしてください: {spec['reference']!r}")
    else:
        frames = spec["frames"]
        if not (isinstance(frames, list) and len(frames) == 2 and all(isinstance(v, int) and not isinstance(v, bool) for v in frames)
                and frames[0] <= frames[1]):
            raise ValueError(f"{label} の frames は [最初, 最後]（Blender のコマ番号。最初 ≤ 最後）にしてください: {frames!r}")
        extra = sorted({"on_deg", "off_deg", "reference"} & set(spec))
        if extra:
            raise ValueError(f"{label} の {extra} は bones（見張る骨）と一緒のときだけ使えます")
    if not (_finite(spec.get("radius_to")) and spec["radius_to"] > 0):
        raise ValueError(f"{label} の radius_to は、小さくした球の半径（m。正の数）にしてください: {spec.get('radius_to')!r}")
    for key in ("ease_in_s", "ease_out_s"):
        if key in spec and not (_finite(spec[key]) and spec[key] >= 0):
            raise ValueError(f"{label} の {key} は秒（0 以上）にしてください: {spec[key]!r}")
    if "guard_bone" in spec and not (isinstance(spec["guard_bone"], str) and spec["guard_bone"]):
        raise ValueError(f"{label} の guard_bone は見張りの当たりを付ける骨の名前にしてください: {spec['guard_bone']!r}")
    for key in ("guard_radius_from", "guard_radius_scale"):
        if key in spec:
            if "guard_bone" not in spec:
                raise ValueError(f"{label} の {key} は guard_bone（見張りの当たり）と一緒に使ってください")
            if not (_finite(spec[key]) and spec[key] > 0):
                raise ValueError(f"{label} の {key} は正の数にしてください: {spec[key]!r}")
    return spec


def release_switch(on, angle, on_deg, off_deg):
    """壁をゆるめるか: 角度が on_deg 以上ならゆるめる、off_deg 以下なら戻す、間なら今のまま（行ったり来たりしないように）。"""
    if angle >= on_deg:
        return True
    if angle <= off_deg:
        return False
    return on


def release_amount(amount, target, dt, ease_in, ease_out):
    """ゆるめ具合（0〜1）を target（1 か 0）へ dt 秒ぶん近づける（0 → 1 は ease_in 秒、1 → 0 は ease_out 秒で動き切る速さ）。
    dt が None なら一度に合わせる（落ち着かせ直すとき）。試作の台本と同じ式。"""
    if dt is None:
        return target
    ease = ease_in if target > amount else ease_out
    step = dt / max(ease, 1e-6)
    return amount + max(-step, min(step, target - amount))


def smoothstep(amount):
    """ゆるめ具合を、なだらかな割合にする（0 と 1 で傾きが 0）。"""
    return amount * amount * (3.0 - 2.0 * amount)


def released_spans(frames):
    """ゆるめていたコマの並びを、続いた区間 [最初, 最後] の並びにする。"""
    spans = []
    for frame in frames:
        if spans and frame - spans[-1][1] <= 1:
            spans[-1][1] = frame
        else:
            spans.append([frame, frame])
    return spans


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
        elif key == "_release_walls":
            if not isinstance(values, list) or not values:
                raise ValueError("_release_walls は、壁をゆるめる指定（1 つ以上）の並びにしてください")
            for position, spec in enumerate(values):
                check_release(spec, position)
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
    from mathutils import Vector

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
        carried_frames = carry_frames(a.carry)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    if (a.carry_bone or a.carry_skip) and not carried_frames:
        raise SystemExit("--carry-bone・--carry-skip は --carry（運ぶ区間）と一緒に使ってください")
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
        check_carry(carried_frames, scene.frame_start, scene.frame_end)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    carry_bone = ""
    if carried_frames:
        carry_bone = a.carry_bone or humanoid_bone(armature, "hips")
        if not carry_bone:
            raise SystemExit("運ぶ骨が決まりません（VRM の人型の hips の割り当てがありません。--carry-bone で選んでください）")
        if carry_bone not in armature.pose.bones:
            raise SystemExit(f"運ぶ骨がありません: {carry_bone}")
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

    # 壁をゆるめる（_release_walls）: 当たりの形は一覧の中の番号で持つ（見張りの当たりを足すと、前に取った参照が使えなくなるため）
    colliders_at_start, groups_at_start = len(spring.colliders), len(spring.collider_groups)
    releases, taken = [], set()
    for position, spec in enumerate(overrides.get("_release_walls", [])):
        label = f"_release_walls[{position}]"
        if not any(any(key in item.vrm_name for key in spec["springs"]) for item in spring.springs):
            raise SystemExit(f"{label}: {spec['springs']} を名前に含む揺れものがありません（あるもの: {names}）")
        group_names = {group.uuid: group.vrm_name for group in spring.collider_groups}
        index_by_uuid = {collider.uuid: index for index, collider in enumerate(spring.colliders)}
        group_by_uuid = {group.uuid: group for group in spring.collider_groups}
        walls = []
        for item in spring.springs:
            if not any(key in item.vrm_name for key in spec["springs"]):
                continue
            for reference in item.collider_groups:
                if spec["group"] not in group_names.get(reference.collider_group_uuid, ""):
                    continue
                for member in group_by_uuid[reference.collider_group_uuid].colliders:
                    index = index_by_uuid.get(member.collider_uuid)
                    if index is not None and spring.colliders[index].shape_type == "Sphere" and all(index != w[0] for w in walls):
                        walls.append((index, spring.colliders[index].shape.sphere.radius))
        if not walls:
            raise SystemExit(f"{label}: {spec['springs']} の揺れものが使う、名前に {spec['group']!r} を含む当たりの組に、球の当たりがありません")
        shared = sorted(index for index, _ in walls if index in taken)
        if shared:
            raise SystemExit(f"{label}: ほかの指定と同じ球の当たり（番号 {shared}）をゆるめようとしています")
        taken.update(index for index, _ in walls)
        watched, reference_bone = [], ""
        if "bones" in spec:
            reference_bone = spec.get("reference") or humanoid_bone(armature, "hips")
            if not reference_bone:
                raise SystemExit(f"{label}: 基準の骨が決まりません（VRM の人型の hips の割り当てがありません。reference で選んでください）")
            missing = [name for name in [reference_bone, *spec["bones"]] if name not in armature.data.bones]
            if missing:
                raise SystemExit(f"{label}: 骨がありません: {missing}")
            reference_rest = armature.data.bones[reference_bone].matrix_local.to_3x3().inverted()
            for name in spec["bones"]:
                bone = armature.data.bones[name]
                watched.append((name, reference_rest @ (bone.tail_local - bone.head_local).normalized()))
        settings = {**RELEASE_DEFAULTS, **{key: spec[key] for key in RELEASE_DEFAULTS if key in spec}}
        # 見張りの当たり: ゆるめる球と同じ球を、静止の姿勢の位置のまま guard_bone に付けたもの。ゆるめている間だけ大きくする
        guards, guard_group = [], ""
        if spec.get("guard_bone"):
            guard_bone = spec["guard_bone"]
            if guard_bone not in armature.data.bones:
                raise SystemExit(f"{label}: 見張りの当たりを付ける骨がありません: {guard_bone}")
            places = []
            for index, base in walls:
                collider = spring.colliders[index]
                rest_place = armature.data.bones[collider.node.bone_name].matrix_local @ Vector(collider.shape.sphere.offset)
                places.append((tuple(armature.data.bones[guard_bone].matrix_local.inverted() @ rest_place), base))
            bpy.ops.vrm.add_spring_bone1_collider_group(armature_object_name=armature.name)
            group_index = len(spring.collider_groups) - 1
            guard_group = f"model3d_release_guard_{position}"
            spring.collider_groups[group_index].vrm_name = guard_group
            for place, base in places:
                bpy.ops.vrm.add_spring_bone1_collider(armature_object_name=armature.name)
                index = len(spring.colliders) - 1
                spring.colliders[index].node.bone_name = guard_bone
                spring.colliders[index].shape_type = "Sphere"
                bpy.context.view_layer.update()
                spring.colliders[index].shape.sphere.offset = place
                spring.colliders[index].shape.sphere.radius = settings["guard_radius_from"]
                bpy.ops.vrm.add_spring_bone1_collider_group_collider(armature_object_name=armature.name, collider_group_index=group_index)
                spring.collider_groups[group_index].colliders[-1].collider_uuid = spring.colliders[index].uuid
                guards.append((index, base * settings["guard_radius_scale"]))
            for spring_index, item in enumerate(spring.springs):
                if any(key in item.vrm_name for key in spec["springs"]):
                    bpy.ops.vrm.add_spring_bone1_spring_collider_group(armature_object_name=armature.name, spring_index=spring_index)
                    item.collider_groups[-1].collider_group_uuid = spring.collider_groups[group_index].uuid
            bpy.context.view_layer.update()
        releases.append({"spec": spec, "settings": settings, "walls": walls, "guards": guards, "guard_group": guard_group,
                         "watched": watched, "reference": reference_bone, "on": False, "amount": 0.0, "frames": [], "largest": 0.0})
        overridden.setdefault("_release_walls", []).append(
            f"{spec['springs']}: 球 {len(walls)} 個（半径 {sorted({round(w[1], 3) for w in walls})} → {spec['radius_to']}）、見張りの当たり {len(guards)} 個")

    def raised_angle(release):
        """見張る骨が、基準の骨から見て静止の姿勢の向きから何度離れているか。全部の骨のうちいちばん小さい角度（＝全部がそれ以上）。"""
        inverse = armature.pose.bones[release["reference"]].matrix.to_3x3().inverted()
        angles = []
        for name, rest_direction in release["watched"]:
            bone = armature.pose.bones[name]
            direction = (inverse @ (bone.tail - bone.head)).normalized()
            angles.append(math.degrees(math.acos(max(-1.0, min(1.0, direction.dot(rest_direction))))))
        return min(angles)

    def update_releases(dt, frame):
        """今の姿勢（かコマ）で壁をゆるめるかを決め、球の半径を少しずつ動かす。dt が None なら、その姿勢の値へ一度に合わせる。"""
        for release in releases:
            spec, settings = release["spec"], release["settings"]
            if "frames" in spec:                           # コマの区間で決める
                release["on"] = spec["frames"][0] <= frame <= spec["frames"][1]
            else:                                          # 姿勢で決める
                release["on"] = release_switch(release["on"], raised_angle(release), spec["on_deg"], spec["off_deg"])
            release["amount"] = release_amount(release["amount"], 1.0 if release["on"] else 0.0, dt, settings["ease_in_s"],
                                               settings["ease_out_s"])
            smooth = smoothstep(release["amount"])
            for index, base in release["walls"]:
                spring.colliders[index].shape.sphere.radius = base + (spec["radius_to"] - base) * smooth
            small = settings["guard_radius_from"]
            for index, full in release["guards"]:
                spring.colliders[index].shape.sphere.radius = small + (full - small) * smooth
            if release["amount"] > 0.0 and (not release["frames"] or release["frames"][-1] != frame):
                release["frames"].append(frame)
            release["largest"] = max(release["largest"], release["amount"])

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
    carry_skip = keys_of(a.carry_skip)
    unused = [key for key in carry_skip if not any(key in item.vrm_name for item in spring.springs)]
    if unused:
        raise SystemExit(f"--carry-skip の {unused} を名前に含む揺れものがありません（あるもの: {names}）")

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
    if releases:                                          # 最初のコマの姿勢で、壁の大きさを一度に合わせる
        update_releases(None, first)
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
        if releases:                                      # そのコマの姿勢で、壁の大きさを一度に合わせる
            update_releases(None, frame)
        bpy.ops.vrm.reset_spring_bone1_animation_state(armature_object_name=armature.name)
        for _ in range(int(round(cut_settle / step))):
            handler.update_pose_bone_rotations(bpy.context, step)

    carry_set, carried = set(carried_frames), []

    def carrier_matrix():
        """運ぶ骨の世界の行列（今のコマ）。"""
        return (armature.matrix_world @ armature.pose.bones[carry_bone].matrix).copy()

    def carry(move):
        """揺れものの節が覚えている位置（今と 1 つ前。世界の座標）を、move（運ぶ骨の、前のコマからこのコマへの動き）で写す。
        揺れものから見ると、運ぶ骨は止まっていたことになる。当たりの形や硬さの計算はそのまま。"""
        for item in spring.springs:
            if any(key in item.vrm_name for key in carry_skip):
                continue
            for joint in item.joints:
                state = joint.animation_state
                if state.initialized_as_tail:
                    state.previous_world_translation = list(move @ Vector(state.previous_world_translation))
                    state.current_world_translation = list(move @ Vector(state.current_world_translation))

    carrier_before = carrier_matrix() if carry_set else None
    steps_done, base_frame, total_steps = 0, first, 0
    for frame in range(first, last + 1):
        if frame in cuts:                                 # 新しい本が始まるコマ: 落ち着かせ直し、刻みはここから数え直す
            settle_again(frame)
            steps_done, base_frame = 0, frame
            if carry_set:
                carrier_before = carrier_matrix()
        else:
            scene.frame_set(frame)
            if carry_set:                                 # 運ぶ区間: 運ぶ骨の前のコマからの動きで、節の位置を写してから進める
                carrier_now = carrier_matrix()
                if frame in carry_set:
                    carry(carrier_now @ carrier_before.inverted())
                    carried.append(frame)
                carrier_before = carrier_now
            goal = target_steps(frame, base_frame, fps, step)
            while steps_done < goal:
                if releases:                              # 刻みごとに、姿勢を見て壁の大きさを少しずつ動かす
                    update_releases(step, frame)
                handler.update_pose_bone_rotations(bpy.context, step)
                steps_done += 1
                total_steps += 1
        for bone in joints:
            if bone.name not in still_names:
                bone.keyframe_insert("rotation_quaternion", frame=frame)
    spring.enable_animation = False                       # 以後は焼いたキーで動く
    release_report = []
    for release in releases:                              # 保存する .blend では、球を元の大きさに戻しておく
        for index, base in release["walls"]:
            spring.colliders[index].shape.sphere.radius = base
        release_report.append({"springs": release["spec"]["springs"], "walls": len(release["walls"]), "guards": len(release["guards"]),
                               "frames_released": len(release["frames"]), "spans": released_spans(release["frames"]),
                               "largest_amount": round(release["largest"], 3)})
    # 見張りの当たりは焼く間だけのもの。保存する前に外す（当たりの形と組の数を元に戻す）
    for release in releases:
        if not release["guard_group"]:
            continue
        group_index = next(index for index, group in enumerate(spring.collider_groups) if group.vrm_name == release["guard_group"])
        group_uuid = spring.collider_groups[group_index].uuid
        for spring_index, item in enumerate(spring.springs):
            for index in reversed(range(len(item.collider_groups))):
                if item.collider_groups[index].collider_group_uuid == group_uuid:
                    bpy.ops.vrm.remove_spring_bone1_spring_collider_group(armature_object_name=armature.name, spring_index=spring_index,
                                                                          collider_group_index=index)
        bpy.ops.vrm.remove_spring_bone1_collider_group(armature_object_name=armature.name, collider_group_index=group_index)
    for index in reversed(range(colliders_at_start, len(spring.colliders))):
        bpy.ops.vrm.remove_spring_bone1_collider(armature_object_name=armature.name, collider_index=index)
    if (len(spring.colliders), len(spring.collider_groups)) != (colliders_at_start, groups_at_start):
        raise SystemExit(f"見張りの当たりを外したあとの当たりの数が元と違います: 当たり {len(spring.colliders)}（元 {colliders_at_start}）・"
                         f"組 {len(spring.collider_groups)}（元 {groups_at_start}）")
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
              "cut_settle_s": cut_settle if cuts else None,
              "carried_frames": [carried[0], carried[-1], len(carried)] if carried else [], "carry_bone": carry_bone or None,
              "carry_skip": carry_skip, "released_walls": release_report,
              "colliders": [colliders_at_start, len(spring.colliders)], "bake_seconds": round(time.time() - started, 1)}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"joints": len(joints), "steps": total_steps, "out_blend": out}))


if __name__ == "__main__":
    main()
