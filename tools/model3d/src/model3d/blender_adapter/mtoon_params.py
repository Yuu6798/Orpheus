"""MToon 1.0 の影・縁の光・マットキャップを Blender の VRM Add-on で設定する。"""

import argparse
import json
import re
import sys
from pathlib import Path

if __package__:
    from .color_factor import mtoon_of
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from color_factor import mtoon_of


COLORS = ("shade_color_factor", "parametric_rim_color_factor", "matcap_factor")
MAX_FLOAT = float.fromhex("0x1.fffffep127")  # Blender の単精度のプロパティに収まる有限の値
SCALARS = {
    "shading_toony_factor": (0.0, 1.0),
    "shading_shift_factor": (-MAX_FLOAT, MAX_FLOAT),
    "parametric_rim_fresnel_power_factor": (0.0, MAX_FLOAT),
    "parametric_rim_lift_factor": (-MAX_FLOAT, MAX_FLOAT),
    "rim_lighting_mix_factor": (0.0, 1.0),
}
FIELDS = (*COLORS, *SCALARS, "matcap_texture")


def number(value, minimum, maximum, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise ValueError(f"{label} は {minimum:g} 以上 {maximum:g} 以下の有限の数にしてください")
    return float(value)


def linear_rgb(value, label):
    if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        srgb = [int(value[i:i + 2], 16) / 255.0 for i in (1, 3, 5)]
        return [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055) ** 2.4 for v in srgb]
    if isinstance(value, list) and len(value) == 3:
        return [number(v, 0.0, 1.0, label) for v in value]
    raise ValueError(f"{label} は #RRGGBB の sRGB 色か、直線 RGB の数 3 つにしてください")


def compile_settings(materials, inputs):
    """パラメーターを直線色と入力画像の絶対パスにそろえる。"""
    if not isinstance(materials, dict) or not materials:
        raise ValueError("materials は材質名をキーにした空でないオブジェクトにしてください")

    normalized = {}
    textures = {}
    for name, values in materials.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(values, dict):
            raise ValueError("materials には空でない材質名と設定オブジェクトが要ります")
        unknown = values.keys() - {*FIELDS, "optional"}
        if unknown:
            raise ValueError(f"{name}: 知らない材質の設定: {', '.join(sorted(map(str, unknown)))}")
        optional = values.get("optional", False)
        if not isinstance(optional, bool):
            raise ValueError(f"{name}.optional は true / false にしてください")
        fields = {}
        for field, value in values.items():
            if field == "optional":
                continue
            label = f"{name}.{field}"
            if field in COLORS:
                value = linear_rgb(value, label)
            elif field in SCALARS:
                minimum, maximum = SCALARS[field]
                value = number(value, minimum, maximum, label)
            else:
                if not isinstance(value, str) or not value or value == "blend" or value not in inputs:
                    raise ValueError(f"{label} は画像を渡す工程の入力名にしてください")
                path = Path(inputs[value]).resolve()
                if not path.is_file():
                    raise ValueError(f"{label}: 入力 {value!r} に画像ファイルがありません")
                textures[value] = str(path)
            fields[field] = value
        if not fields:
            raise ValueError(f"{name} には 1 つ以上の材質の設定が要ります")
        normalized[name] = {"optional": optional, "fields": fields}

    return {"materials": normalized, "textures": textures}


def snapshot(mtoon):
    image = mtoon.matcap_texture.index.source
    return {**{key: list(getattr(mtoon, key)) for key in COLORS},
            **{key: getattr(mtoon, key) for key in SCALARS},
            "matcap_texture": {"image": image.name, "packed": bool(image.packed_file)} if image is not None else None}


def change_materials(data, settings):
    chosen = {}
    skipped = []
    for name, material_settings in settings["materials"].items():
        material = data.materials.get(name)
        if material is None:
            if material_settings["optional"]:
                skipped.append(name)
                continue
            raise ValueError(f"材質がありません: {name}")
        gltf = mtoon_of(material)
        if gltf is None or not gltf.enabled:
            raise ValueError(f"{name} は MToon の材質ではありません")
        chosen[name] = (gltf.extensions.vrmc_materials_mtoon, material_settings["fields"])

    images = {}
    for _, fields in chosen.values():
        alias = fields.get("matcap_texture")
        if alias is None or alias in images:
            continue
        try:
            image = data.images.load(settings["textures"][alias], check_existing=False)
            if image.source != "FILE" or not all(image.size):
                raise ValueError("静止画像を読み込めません")
            image.pack()
            if image.packed_file is None:
                raise ValueError("画像を .blend に格納できません")
        except (RuntimeError, ValueError) as error:
            raise ValueError(f"マットキャップの入力 {alias!r}: {error}") from error
        images[alias] = image

    materials = {}
    for name, (mtoon, fields) in chosen.items():
        before = snapshot(mtoon)
        for field, value in fields.items():
            if field == "matcap_texture":
                mtoon.matcap_texture.index.source = images[value]
            else:
                setattr(mtoon, field, value)
        materials[name] = {"before": before, "after": snapshot(mtoon)}
    return {"materials": materials, "skipped": sorted(skipped),
            "changed": sorted(name for name, values in materials.items() if values["before"] != values["after"])}


def main():
    import bpy

    parser = argparse.ArgumentParser()
    for argument in ("blend", "settings", "out-blend", "report"):
        parser.add_argument(f"--{argument}", type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else [])
    settings = json.loads(args.settings.read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(args.blend.resolve()), use_scripts=False)
    report = {"schema_version": "model3d-mtoon-params/0.1", **change_materials(bpy.data, settings)}
    bpy.ops.wm.save_as_mainfile(filepath=str(args.out_blend.resolve()), copy=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
