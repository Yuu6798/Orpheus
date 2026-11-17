"""Blender の中で動かす: VRM の表情（VRM 1.0 の決まった表情。happy・blink など）に、メッシュの形状キーを割り当てる（vrm.expressions）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python vrm_expressions.py -- --blend in.blend --settings settings.json --out-blend out.blend --report report.json

- settings の binds（{VRM の表情の名前: {形状キー: 重み}}）の表情ごとに、今の割り当てを外して、object の形状キーを挙げた順に割り当てる。
  空の {} は割り当てを外すだけ（口の形が無いので aa などを空にする、など）。挙げない表情は変えない。
- 表情の名前は VRM 1.0 の名前（blinkLeft・lookUp など。check.vrm の名前と同じ）。VRM Add-on の中の名前（blink_left など）へ直して書く。
- 形状キーが object に無ければ止める。重みは 0〜1。元のファイルは変えない。
- 作ったキャラの道具の初期値には、形の名前だけでメッシュの無い割り当てがあることがある（VRM には書き出されない）。報告に、変える前の割り当ても残す。
**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、リポジトリ外の 3 作目の VRM のダンスの台本と同じ割り当てになることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

# VRM 1.0 の決まった表情の名前 → VRM Add-on 4.10.5 の vrm1.expressions.preset の名前
PRESETS = {"happy": "happy", "angry": "angry", "sad": "sad", "relaxed": "relaxed", "surprised": "surprised", "aa": "aa", "ih": "ih",
           "ou": "ou", "ee": "ee", "oh": "oh", "blink": "blink", "blinkLeft": "blink_left", "blinkRight": "blink_right", "lookUp": "look_up",
           "lookDown": "look_down", "lookLeft": "look_left", "lookRight": "look_right", "neutral": "neutral"}


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="vrm_expressions.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def missing_keys(binds, keys):
    """binds で挙げたのに object に無い形状キー {表情: [名前]}（無ければ空）。"""
    return {name: [key for key in values if key not in keys] for name, values in binds.items() if any(key not in keys for key in values)}


def bind_rows(expression):
    """VRM Add-on の表情の、形の割り当ての並び [[物体の名前, 形状キー, 重み]]（重みは 3 桁に丸める。報告用）。"""
    return [[bind.node.mesh_object_name, bind.index, round(float(bind.weight), 3)] for bind in expression.morph_target_binds]


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    obj = bpy.data.objects.get(settings["object"])
    if obj is None or obj.type != "MESH":
        raise SystemExit(f"object のメッシュがありません: {settings['object']}")
    armatures = [o for o in bpy.data.objects if o.type == "ARMATURE"]
    if settings.get("armature"):
        armature = bpy.data.objects.get(settings["armature"])
        if armature is None or armature.type != "ARMATURE":
            raise SystemExit(f"armature のアーマチュアがありません: {settings['armature']}")
    elif len(armatures) == 1:
        armature = armatures[0]
    else:
        raise SystemExit(f"アーマチュアが {len(armatures)} あります（armature で名前を渡してください）")
    extension = getattr(armature.data, "vrm_addon_extension", None)
    if extension is None:
        raise SystemExit("VRM Add-on の設定がありません（VRM Add-on を有効にして起動しているか、VRM のキャラの .blend かを確かめてください）")
    keys = {k.name for k in obj.data.shape_keys.key_blocks} if obj.data.shape_keys else set()
    missing = missing_keys(settings["binds"], keys)
    if missing:
        raise SystemExit(f"{obj.name} に無い形状キーを割り当てようとしています: {missing}")
    preset = extension.vrm1.expressions.preset
    before = {name: bind_rows(getattr(preset, attribute)) for name, attribute in PRESETS.items()}
    for name, values in settings["binds"].items():
        expression = getattr(preset, PRESETS[name])
        expression.morph_target_binds.clear()
        for key, weight in values.items():
            bind = expression.morph_target_binds.add()
            bind.node.mesh_object_name = obj.name
            bind.index = key
            bind.weight = weight
    after = {name: bind_rows(getattr(preset, attribute)) for name, attribute in PRESETS.items()}
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True)
    report = {"schema_version": "model3d-vrm-expressions/0.1", "blender": bpy.app.version_string, "object": obj.name, "armature": armature.name,
              "changed": sorted(settings["binds"]), "before": before, "after": after,
              "bound": sorted(name for name, rows in after.items() if any(row[0] for row in rows)),
              "name_only": sorted(name for name, rows in after.items() if rows and not all(row[0] for row in rows)), "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "changed": len(settings["binds"])}))


if __name__ == "__main__":
    main()
