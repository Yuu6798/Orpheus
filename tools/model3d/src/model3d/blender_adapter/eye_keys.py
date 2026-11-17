"""Blender の中で動かす: 目の板と瞳を入れた頭の、まばたきのキーを直し、目の新しい形状キーを足す（model.eye_keys）。

    blender --background --factory-startup --python eye_keys.py -- --blend in.blend --settings settings.json --out out.blend --report report.json

settings.json（model.eye_keys が書く）:
- geometry: 目の形の値（eye_math.py の model3d-eye-geometry/0.1）。object・片目ごとの sphere・pupil・rim・window・iris_vertices・lid_vertices
- blink（{"R", "L"}。まばたきのキーの名前）・smile（{"R", "L"}。笑顔の閉じ目のキーの名前。省くと直さない）
- names: 新しいキーの名前の頭 {"wide", "small", "stretch", "squash"}（後ろに _R・_L を付ける）
- cfg: 量（lid_front・lid_margin・close_below・close_reach・close_max_scale・wide_up・wide_down・iris_small・iris_stretch・iris_squash・
  iris_lift）と形
  （side_fade・bump_power・upper_fall・lower_fall）
すること（試しと同じ手順と計算。eye_math.py）:
- まばたきのキー（右目・左目の順）: 下りてくる上まぶたを、下まぶたのふちより close_below 下まで届くように伸ばし（笑顔の閉じ目は伸ばさない）、
  下りてきたまぶた（もとは板より lid_front より手前）を板より lid_margin 手前の球の面へ押し出す。瞳の頂点はキーで動かさない（瞳を奥へ逃がさない）。
- 新しいキー（右目・左目の順に、瞳が縮む・横へ伸びる・つぶれる・見開き）: 瞳のキーは、瞳の頂点を瞳の中心のまわりに倍率で動かして、球（半径 +
  iris_lift）の面に乗せる。つぶれるキーの下限は −1（負の値でたてに伸びる）。見開きは、まぶたの頂点（lid_vertices）を上下へ。
入力の .blend は変えない（別のファイルに書く）。同じ名前の形状キーがもうあれば止める。形状キーが絶対（Relative のチェックなし）なら、
キーを変える前に止める（時間で形を選ぶので、値 0 で消せず、表情として入り切りできない。model.closed_eye_key と同じ）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .eye_math import Frame, fix_blink, iris_key, validate, wide_key
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from eye_math import Frame, fix_blink, iris_key, validate, wide_key

SIDES = ("R", "L")


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="eye_keys.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def absolute_keys_problem(name, use_relative):
    """まばたきと目のキーは、相対の形状キーの値で入り切りする。絶対の形状キー（Relative のチェックなし）は値でなく時間（eval_time）で
    形を選ぶので、値 0 で消せず、表情として入り切りできない（model.closed_eye_key と同じ考え）。そのときの説明（相対なら None）。"""
    if use_relative:
        return None
    return (f"{name} の形状キーは絶対（Relative のチェックなし）で、時間で形を選びます。まばたきと目のキーは値で入り切りするので、"
            "相対の形状キーのメッシュにだけ使えます")


def new_key_plan(names, cfg):
    """足すキーの並び [(名前の頭, 横の倍率, たての倍率, 下限)]（瞳が縮む・横へ伸びる・つぶれる。見開きは別）。"""
    return [(names["small"], cfg["iris_small"], cfg["iris_small"], 0.0), (names["stretch"], *cfg["iris_stretch"], 0.0),
            (names["squash"], *cfg["iris_squash"], -1.0)]


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    geometry, cfg, names = settings["geometry"], settings["cfg"], settings["names"]
    head = bpy.data.objects.get(geometry["object"])
    if head is None or head.type != "MESH":
        raise SystemExit(f"頭のメッシュの物体がありません: {geometry['object']}")
    mesh = head.data
    count = len(mesh.vertices)
    validate(geometry, vertex_count=count)
    if mesh.shape_keys is None:
        raise SystemExit(f"{head.name} に形状キーがありません（まばたきのキーが要ります）")
    problem = absolute_keys_problem(head.name, mesh.shape_keys.use_relative)   # 絶対の形状キーは値 0 で消せず、表情として入り切りできない
    if problem:
        raise SystemExit(problem)
    blocks = mesh.shape_keys.key_blocks
    wanted = [settings["blink"][side] for side in SIDES] + [settings["smile"][side] for side in SIDES if settings.get("smile")]
    absent = [name for name in wanted if name not in blocks]
    if absent:
        raise SystemExit(f"{head.name} に形状キーがありません: {absent}")
    adding = [prefix + side for side in SIDES for prefix in [row[0] for row in new_key_plan(names, cfg)] + [names["wide"]]]
    clash = [name for name in adding if name in blocks]
    if clash:
        raise SystemExit(f"{head.name} に同じ名前の形状キーがもうあります: {clash}")
    frame = Frame(np.array(head.matrix_world))
    base = np.zeros(count * 3, dtype=np.float32)                              # まばたきの直しは 32 ビットの頂点から（試しと同じ）
    mesh.vertices.foreach_get("co", base)
    base = base.reshape(-1, 3)
    report = {"blender": bpy.app.version_string, "object": head.name, "blink": {}, "new_keys": {}}
    for side in SIDES:
        eye = geometry["eyes"][side]
        iris = np.asarray(eye["iris_vertices"], dtype=np.int64)
        targets = [(settings["blink"][side], True)] + ([(settings["smile"][side], False)] if settings.get("smile") else [])
        for name, lower_close in targets:
            block = blocks[name]
            key = np.zeros(count * 3, dtype=np.float32)
            block.data.foreach_get("co", key)
            key = key.reshape(-1, 3)
            fixed, info = fix_blink(base, key, frame, eye, cfg, lower_close, skip=iris)
            info["iris_reset"] = int(np.any(np.abs(fixed[iris] - base[iris]) > 0, axis=1).sum())
            fixed[iris] = base[iris]                                     # 瞳はまばたきで動かさない
            block.data.foreach_set("co", fixed.ravel())
            report["blink"][name] = info
    base_w = frame.world(base.astype(np.float64))                             # 新しいキーは 64 ビットの頂点から（試しと同じ）

    def add_key(name, world_positions, low=0.0):
        block = head.shape_key_add(name=name, from_mix=False)
        block.data.foreach_set("co", frame.local(world_positions).ravel())
        block.slider_min = low
        block.value = 0.0
        moved = np.linalg.norm(world_positions - base_w, axis=1)
        report["new_keys"][name] = {"moved_vertices": int((moved > 1e-7).sum()), "max_mm": round(float(moved.max() * 1000), 2)}

    for side in SIDES:
        eye = geometry["eyes"][side]
        for prefix, sx, sz, low in new_key_plan(names, cfg):
            add_key(prefix + side, iris_key(base_w, eye, sx, sz, cfg["iris_lift"]), low)
        add_key(names["wide"] + side, wide_key(base_w, eye, cfg))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"blink": list(report["blink"]), "new_keys": list(report["new_keys"])}))


if __name__ == "__main__":
    main()
