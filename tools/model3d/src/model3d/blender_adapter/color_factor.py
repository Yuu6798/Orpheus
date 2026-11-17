"""Blender の中で動かす: VRM の材質（MToon）の色の掛け率（baseColorFactor）を 1 に戻し、影の色（shadeColorFactor）も同じ割合で戻す
（vrm.color_factor）。

    blender --background --factory-startup --python-expr <VRM Add-on を有効にする前置き> \
        --python color_factor.py -- --blend in.blend --settings settings.json --out-blend out.blend --report report.json

- settings の materials（材質の名前）ごとに、VRM Add-on の MToon の設定（vrm_addon_extension.mtoon1）の baseColorFactor の RGB を 1 にし
  （A はそのまま）、shadeColorFactor の RGB に「1 ÷ 元の掛け率」を掛ける（1 を超えない。元の掛け率が FLOOR 以下の色はそのまま）。
  VRM Add-on が、材質の中の MToon のノードの値も合わせて書き換える。
- 色の絵（テクスチャ）は変えない。テクスチャの色をそのまま描く描き方（Workbench など）は掛け率を使わないので、見た目は変わらない。
- 挙げた材質が無い・MToon でない（mtoon1.enabled でない）なら、何も変えずに止める。元のファイルは変えない。
- 報告に、MToon の全部の材質の、変える前と後の baseColorFactor と shadeColorFactor を残す。
**検証**: Windows の Blender 5.2.4 と VRM Add-on 4.10.5 で、リポジトリ外の 3 作目の VRM のダンスの台本と同じ値になることを確かめた（docs/model3d.md）。
"""
import argparse
import json
import os
import sys

FLOOR = 1e-6       # 掛け率がこれ以下の色は、影の色を割らない（台本と同じ）


def parse_args(argv):
    ap = argparse.ArgumentParser(prog="color_factor.py")
    ap.add_argument("--blend", required=True)
    ap.add_argument("--settings", required=True)
    ap.add_argument("--out-blend", required=True)
    ap.add_argument("--report", required=True)
    return ap.parse_args(argv)


def restored(base, shade):
    """(1 に戻した baseColorFactor の RGBA, 同じ割合で戻した shadeColorFactor の RGB)。影の色は 1 を超えない。"""
    return [1.0, 1.0, 1.0, base[3]], [min(1.0, s / b) if b > FLOOR else s for s, b in zip(shade, base[:3])]


def mtoon_of(material):
    """材質の VRM Add-on の MToon の設定（VRM Add-on が無ければ None）。"""
    extension = getattr(material, "vrm_addon_extension", None)
    return getattr(extension, "mtoon1", None) if extension is not None else None


def read_factors(materials):
    """MToon の材質ごとの {"base": baseColorFactor の RGBA, "shade": shadeColorFactor の RGB}。"""
    out = {}
    for material in materials:
        mtoon = mtoon_of(material)
        if mtoon is None or not mtoon.enabled:
            continue
        out[material.name] = {"base": [float(v) for v in mtoon.pbr_metallic_roughness.base_color_factor],
                              "shade": [float(v) for v in mtoon.extensions.vrmc_materials_mtoon.shade_color_factor]}
    return out


def main(argv=None):
    import bpy  # Blender の中でだけ使える

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    a = parse_args(argv)
    with open(a.settings, encoding="utf-8") as stream:
        settings = json.load(stream)
    bpy.ops.wm.open_mainfile(filepath=os.path.abspath(a.blend))
    chosen = []
    for name in settings["materials"]:
        material = bpy.data.materials.get(name)
        if material is None:
            raise SystemExit(f"材質がありません: {name}")
        mtoon = mtoon_of(material)
        if mtoon is None:
            raise SystemExit("VRM Add-on の設定がありません（VRM Add-on を有効にして起動しているか、VRM のキャラの .blend かを確かめてください）")
        if not mtoon.enabled:
            raise SystemExit(f"{name} は MToon の材質ではありません（VRM Add-on の mtoon1 が有効でない）")
        chosen.append((name, mtoon))
    before = read_factors(bpy.data.materials)
    for name, mtoon in chosen:
        base, shade = restored(list(mtoon.pbr_metallic_roughness.base_color_factor),
                               list(mtoon.extensions.vrmc_materials_mtoon.shade_color_factor))
        mtoon.pbr_metallic_roughness.base_color_factor = base
        mtoon.extensions.vrmc_materials_mtoon.shade_color_factor = shade
    after = read_factors(bpy.data.materials)
    bpy.ops.wm.save_as_mainfile(filepath=os.path.abspath(a.out_blend), copy=True)
    report = {"schema_version": "model3d-color-factor/0.1", "blender": bpy.app.version_string, "materials": settings["materials"],
              "changed": sorted(name for name in after if after[name] != before.get(name)), "before": before, "after": after,
              "settings": settings}
    os.makedirs(os.path.dirname(os.path.abspath(a.report)), exist_ok=True)
    with open(a.report, "w", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=1)
        stream.write("\n")
    print(json.dumps({"saved": a.out_blend, "changed": len(report["changed"])}))


if __name__ == "__main__":
    main()
