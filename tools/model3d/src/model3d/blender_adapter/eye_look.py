"""Blender の中で動かす: VRM の視線を、目の骨を回す作り（bone）にし、回す角度の範囲を、目の板の上で瞳が滑る量から決める（vrm.look_at）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> --python eye_look.py -- \
        --blend in.blend --settings settings.json --out out.blend --report report.json

目の骨の根もとが目の球の中心にあると（model.eye_rig）、骨を θ 回すと瞳は板の上を 半径 × θ 滑る。視線の入力がいちばん端のときに瞳が
滑る量を決めれば、骨を回す角度の最大は degrees(滑る量 ÷ 半径)。settings.json（vrm.look_at が書く）:
- radius（目の球の半径 m）、slide（{"horizontal", "vertical"}。視線がいちばん端のときに瞳が滑る量 m）、input_max（視線の入力の
  いちばん大きい角度 °）、armature（省くと VRM の人型の骨の割り当てのあるアーマチュアが 1 つのときそれ）
すること（試しと同じ手順）: VRM 1.0 の視線（lookAt）の type を bone にし、4 つの範囲（横の内・外、たての下・上）の入力の最大を input_max、
出力の倍率を degrees(滑る量 ÷ 半径)（横は horizontal、たては vertical）にする。VRM Add-on の設定の値を書くだけ。
入力の .blend は変えない（別のファイルに書く）。
"""
import argparse
import json
import os
import sys

import numpy as np

try:   # パッケージとして読むとき（テスト）
    from .animation_sample import pick_armature
except ImportError:   # Blender が台本として動かすとき
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from animation_sample import pick_armature

RANGE_MAPS = (("range_map_horizontal_inner", "horizontal"), ("range_map_horizontal_outer", "horizontal"),
              ("range_map_vertical_down", "vertical"), ("range_map_vertical_up", "vertical"))


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="eye_look.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def output_scale(slide, radius):
    """視線がいちばん端のときに目の骨を回す角度（°）: 瞳が板の上を slide 滑る分（試しと同じく numpy の degrees）。"""
    return float(np.degrees(slide / radius))


def version_problem(name, spec_version):
    """VRM 1.0 の視線（lookAt）だけを書く。VRM 0.x のアーマチュアは視線の設定が別の所にあり、書いても書き出しに入らないので、
    そのときの説明（1.0 なら None）。"""
    if spec_version == "1.0":
        return None
    return f"{name} は VRM {spec_version} です。vrm.look_at は VRM 1.0 の視線（lookAt）だけを書きます（VRM 0.x の視線は未対応）"


def look_at_values(look_at):
    """VRM Add-on の視線の設定の、type と 4 つの範囲 [入力の最大, 出力の倍率]（報告用）。"""
    return {"type": look_at.type, "range": {name: [round(float(getattr(look_at, name).input_max_value), 6),
                                                    round(float(getattr(look_at, name).output_scale), 6)] for name, _ in RANGE_MAPS}}


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    armature = pick_armature(bpy, settings.get("armature"))
    problem = version_problem(armature.name, armature.data.vrm_addon_extension.spec_version)
    if problem:
        raise SystemExit(problem)
    look_at = armature.data.vrm_addon_extension.vrm1.look_at
    before = look_at_values(look_at)
    look_at.type = "bone"
    for name, axis in RANGE_MAPS:
        range_map = getattr(look_at, name)
        range_map.input_max_value = settings["input_max"]
        range_map.output_scale = output_scale(settings["slide"][axis], settings["radius"])
    after = look_at_values(look_at)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out), copy=True)
    report = {"blender": bpy.app.version_string, "armature": armature.name, "radius": settings["radius"],
              "degrees": {axis: round(output_scale(settings["slide"][axis], settings["radius"]), 4) for axis in ("horizontal", "vertical")},
              "before": before, "after": after}
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"look_at": after["type"], "degrees": report["degrees"]}))


if __name__ == "__main__":
    main()
