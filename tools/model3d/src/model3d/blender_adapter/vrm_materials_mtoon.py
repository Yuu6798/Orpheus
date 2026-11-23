"""Blender と VRM Add-on で、指定材質を MToon に変換し、同値の材質と画像を統合する。"""

import argparse
from array import array
import hashlib
import json
from pathlib import Path
import sys


def mtoon_of(material):
    extension = getattr(material, "vrm_addon_extension", None)
    mtoon = getattr(extension, "mtoon1", None)
    if mtoon is None:
        raise SystemExit("VRM Add-on の MToon 設定を読めません")
    return mtoon


def image_node_on(socket, output="Color"):
    if socket.is_linked:
        link = socket.links[0]
        if link.from_node.type != "TEX_IMAGE" or link.from_socket.name != output or link.from_node.image is None:
            raise SystemExit(f"{socket.name!r} は画像ノードの {output} から直接つないでください")
        node = link.from_node
        if node.inputs["Vector"].is_linked or node.projection != "FLAT":
            raise SystemExit(f"画像ノード {node.name!r} はメッシュの既定の UV を直接使ってください")
        return node
    return None


def image_sampling(node):
    if node is None:
        return {}
    filtering = {"Closest": "NEAREST", "Linear": "LINEAR"}.get(node.interpolation)
    wrapping = {"REPEAT": "REPEAT", "EXTEND": "CLAMP_TO_EDGE"}.get(node.extension)
    if filtering is None or wrapping is None:
        raise SystemExit(f"画像ノード {node.name!r} の補間か繰り返し方法を MToon に写せません")
    return {"mag_filter": filtering, "min_filter": filtering, "wrap_s": wrapping, "wrap_t": wrapping}


def set_sampling(texture, settings):
    for key, value in settings.items():
        setattr(texture.sampler, key, value)


def read_reference(material):
    gltf = mtoon_of(material)
    if not gltf.enabled:
        raise SystemExit(f"手本の材質 {material.name!r} の MToon を有効にしてください")
    toon = gltf.extensions.vrmc_materials_mtoon
    return {
        "double_sided": gltf.double_sided,
        "shade_color_factor": tuple(toon.shade_color_factor),
        "shading_toony_factor": toon.shading_toony_factor,
        "shading_shift_factor": toon.shading_shift_factor,
        "gi_equalization_factor": toon.gi_equalization_factor,
    }


def conversion_shader(material):
    tree = material.node_tree
    outputs = [node for node in tree.nodes if node.type == "OUTPUT_MATERIAL" and node.is_active_output] if tree else []
    if len(outputs) != 1 or not outputs[0].inputs["Surface"].is_linked:
        raise SystemExit(f"材質 {material.name!r} に出力へつながった Principled BSDF が要ります")
    output = outputs[0]
    shader = output.inputs["Surface"].links[0].from_node
    if shader.type != "BSDF_PRINCIPLED" or any(output.inputs[name].is_linked for name in ("Volume", "Displacement")):
        raise SystemExit(f"材質 {material.name!r} は MToon へ写せる単独の Principled BSDF ではありません")
    if material.animation_data or tree.animation_data:
        raise SystemExit(f"材質 {material.name!r} に変換で写せないアニメーションがあります")
    if any(socket.is_linked and socket.name not in {"Base Color", "Alpha", "Emission Color", "Normal"}
           for socket in shader.inputs):
        raise SystemExit(f"材質 {material.name!r} の Principled BSDF に変換で写せない接続があります")
    return shader


def has_pixel_transparency(image):
    if image.source not in {"FILE", "GENERATED"} or not all(image.size) or not len(image.pixels):
        raise SystemExit(f"画像 {image.name!r} の透過を確かめられません")
    channels = image.channels
    if channels not in (2, 4):
        return False
    pixels = array("f", [0]) * len(image.pixels)
    image.pixels.foreach_get(pixels)
    return any(a < 1 for a in memoryview(pixels)[channels - 1::channels])


def convert_material(material, settings, reference):
    shader = conversion_shader(material)
    base_node = image_node_on(shader.inputs["Base Color"])
    base_image = base_node.image if base_node else None
    base_sampling = image_sampling(base_node)
    emission_node = image_node_on(shader.inputs["Emission Color"])
    emission_sampling = image_sampling(emission_node)
    normal = shader.inputs["Normal"]
    normal_sampling = {}
    if normal.is_linked:
        node = normal.links[0].from_node
        if node.type != "NORMAL_MAP" or node.space != "TANGENT" or node.uv_map or node.inputs["Strength"].is_linked:
            raise SystemExit(f"材質 {material.name!r} のノーマルは既定の UV の接空間ノーマルマップにしてください")
        normal_sampling = image_sampling(image_node_on(node.inputs["Color"]))
    alpha = shader.inputs["Alpha"]
    alpha_node = image_node_on(alpha, "Alpha")
    if alpha_node is not None and alpha_node != base_node:
        raise SystemExit(f"材質 {material.name!r} は色と透過で同じ画像ノードを使ってください")
    old_mode = getattr(material, "blend_method", None)
    cutoff = getattr(material, "alpha_threshold", 0.5)
    double_sided = not material.use_backface_culling
    opacity = 1.0 if alpha_node else alpha.default_value
    rgb = (1.0, 1.0, 1.0) if base_node else shader.inputs["Base Color"].default_value[:3]
    color = (*rgb, opacity)
    color = settings["base_color_factors"].get(material.name, color)
    transparent = alpha_node is not None or color[3] < 1.0 or old_mode == "BLEND" or (
        getattr(material, "surface_render_method", None) == "BLENDED")
    alpha_mode = "MASK" if old_mode == "CLIP" else ("BLEND" if transparent else "OPAQUE")
    effective_mode = settings["alpha_modes"].get(material.name, alpha_mode)
    if effective_mode != "OPAQUE" and base_image is not None and alpha_node is None and has_pixel_transparency(base_image):
        raise SystemExit(f"材質 {material.name!r} は色の画像の透過を使っていませんが、MToon では画像の透過も使われます")

    gltf = mtoon_of(material)
    gltf.enabled = True
    if not gltf.enabled:
        raise SystemExit(f"材質 {material.name!r} を MToon に変換できませんでした")
    base = gltf.pbr_metallic_roughness.base_color_texture.index
    if base.source != base_image:
        base.source = base_image
    set_sampling(base, base_sampling)
    if emission_node is not None:
        gltf.emissive_factor = (1.0, 1.0, 1.0)
    set_sampling(gltf.emissive_texture.index, emission_sampling)
    set_sampling(gltf.normal_texture.index, normal_sampling)
    toon = gltf.extensions.vrmc_materials_mtoon
    if settings["shade_from_base"] and base_image is not None:
        shade = toon.shade_multiply_texture.index
        shade.source = base_image
        set_sampling(shade, base_sampling)
    gltf.pbr_metallic_roughness.base_color_factor = color
    gltf.double_sided = reference.get("double_sided", double_sided)
    for key in ("shade_color_factor", "shading_toony_factor", "shading_shift_factor", "gi_equalization_factor"):
        if key in reference:
            setattr(toon, key, reference[key])
    if material.name in settings["shade_color_factors"]:
        toon.shade_color_factor = settings["shade_color_factors"][material.name]
    gltf.alpha_mode = alpha_mode
    if alpha_mode == "MASK":
        gltf.alpha_cutoff = cutoff


def image_signature(image, cache):
    key = image.as_pointer()
    if key not in cache:
        if image.source != "FILE" or not all(image.size) or not len(image.pixels):
            raise SystemExit(f"画像 {image.name!r} は画素を読める単一ファイル画像ではありません")
        pixels = array("f", [0]) * len(image.pixels)
        image.pixels.foreach_get(pixels)
        digest = hashlib.sha256(memoryview(pixels).cast("B")).hexdigest()
        cache[key] = (tuple(image.size), image.channels, image.depth, image.is_float, image.alpha_mode,
                      image.colorspace_settings.name, image.use_view_as_render, digest)
    return cache[key]


def scalar_properties(owner, *, skip=()):
    fields = []
    for prop in owner.bl_rna.properties:
        if prop.identifier not in skip and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}:
            value = getattr(owner, prop.identifier)
            fields.append((prop.identifier, tuple(value) if getattr(prop, "is_array", False) else value))
    return tuple(fields)


def addon_signature(group, bpy, cache):
    values = []
    for prop in group.bl_rna.properties:
        if prop.identifier == "rna_type":
            continue
        value = getattr(group, prop.identifier)
        if isinstance(value, bpy.types.Image):
            value = image_signature(value, cache)
        elif isinstance(value, bpy.types.PropertyGroup):
            value = addon_signature(value, bpy, cache)
        elif isinstance(value, bpy.types.ID):
            value = value.as_pointer()
        elif prop.type == "COLLECTION":
            raise SystemExit(f"MToon の設定 {prop.identifier!r} は同値か確かめられません")
        elif getattr(prop, "is_array", False):
            value = tuple(value)
        values.append((prop.identifier, value))
    return tuple(values)


def driver_signature(tree, material):
    anim = tree.animation_data
    if anim is None:
        return ()
    if anim.action or anim.nla_tracks:
        raise SystemExit(f"材質 {material.name!r} のノードにアニメーションがあります")
    records = []
    for curve in anim.drivers:
        driver = curve.driver
        variables = []
        if curve.modifiers or driver.type != "SUM":
            raise SystemExit(f"材質 {material.name!r} に同値か確かめられないドライバーがあります")
        for var in driver.variables:
            if var.type != "SINGLE_PROP" or len(var.targets) != 1 or var.targets[0].id != material:
                raise SystemExit(f"材質 {material.name!r} のドライバーが別のデータを参照しています")
            target = var.targets[0]
            try:
                value = material.path_resolve(target.data_path)
            except ValueError:
                # アドオンは、画像をまだつないでいない枠にも画像の大きさのドライバーを置く。
                value = None
            if value is not None and not isinstance(value, (int, float, str)):
                raise SystemExit(f"材質 {material.name!r} のドライバーで数や文字を読めません")
            variables.append((var.name, scalar_properties(target), value))
        records.append((curve.data_path, curve.array_index, curve.mute, tuple(variables)))
    return tuple(sorted(records))


NODE_LAYOUT = {"location", "location_absolute", "dimensions", "width", "width_hidden", "height", "select",
               "show_options", "show_preview", "show_texture", "hide", "label", "color", "use_custom_color"}


def shader_signature(material, cache):
    tree = material.node_tree
    if material.animation_data or tree is None:
        raise SystemExit(f"材質 {material.name!r} のノードの同値を確認できません")
    supported = {"BSDF_PRINCIPLED", "EMISSION", "FRAME", "GROUP", "MATH", "MIX_SHADER", "NORMAL_MAP",
                 "OUTPUT_MATERIAL", "REROUTE", "TEX_IMAGE", "UVMAP"}
    nodes = []
    for node in tree.nodes:
        if node.type not in supported:
            raise SystemExit(f"材質 {material.name!r} のノード {node.type!r} は同値を確認できません")
        image = getattr(node, "image", None)
        group = getattr(node, "node_tree", None)
        image_user = getattr(node, "image_user", None)
        mapping = ()
        if node.type == "TEX_IMAGE":
            color = node.color_mapping
            ramp = color.color_ramp if color.use_color_ramp else None
            colors = (scalar_properties(ramp), tuple(scalar_properties(element) for element in ramp.elements)) if ramp else ()
            mapping = (scalar_properties(node.texture_mapping), scalar_properties(color), colors)
        sockets = tuple(scalar_properties(socket) for socket in (*node.inputs, *node.outputs))
        nodes.append((node.name, scalar_properties(node, skip=NODE_LAYOUT), sockets,
                      image_signature(image, cache) if image else None,
                      group.as_pointer() if group else None,
                      scalar_properties(image_user) if image_user else None, mapping))
    links = sorted((link.from_node.name, link.from_socket.identifier, link.to_node.name,
                    link.to_socket.identifier, link.is_muted) for link in tree.links)
    render_fields = ("use_backface_culling", "use_backface_culling_shadow", "use_backface_culling_lightprobe_volume",
                     "surface_render_method", "blend_method", "shadow_method", "thickness_mode", "displacement_method",
                     "use_transparency_overlap", "show_transparent_back", "use_screen_refraction", "use_raytrace_refraction",
                     "use_thickness_from_shadow", "refraction_depth", "volume_intersection_method")
    mode = (material.diffuse_color[:], *(getattr(material, field, None) for field in render_fields))
    return mode, tuple(sorted(nodes)), tuple(links), driver_signature(tree, material)


def equal_materials(left, right, bpy, cache):
    a, b = mtoon_of(left), mtoon_of(right)
    return (a.enabled and b.enabled and addon_signature(a, bpy, cache) == addon_signature(b, bpy, cache)
            and shader_signature(left, cache) == shader_signature(right, cache))


def merge_datablocks(collection, pairs, equivalent):
    for source, target in pairs.items():
        item, keep = collection[source], collection[target]
        if item.library or keep.library or not equivalent(item, keep):
            raise SystemExit(f"同じ内容と確認できません: {source!r} と {target!r}")
        if item.use_fake_user:
            keep.use_fake_user = True
        item.user_remap(keep)
        collection.remove(item)


def sha256(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def main(argv=None):
    import bpy

    parser = argparse.ArgumentParser(description=__doc__)
    for flag in ("blend", "settings", "out-blend", "report"):
        parser.add_argument("--" + flag, required=True, type=Path)
    a = parser.parse_args(sys.argv[sys.argv.index("--") + 1:] if argv is None else argv)
    settings = json.loads(a.settings.read_text(encoding="utf-8"))
    source, output, report_path = a.blend.resolve(), a.out_blend.resolve(), a.report.resolve()
    if output.suffix.lower() != ".blend" or len({source, output, report_path}) != 3:
        raise SystemExit("出力は入力と別の .blend にしてください")
    digest = sha256(source)
    bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
    materials, images = bpy.data.materials, bpy.data.images
    names = set(settings["materials"]) | settings["alpha_modes"].keys() | settings["merge_materials"].keys()
    names.update(settings["merge_materials"].values())
    for name in sorted(names):
        if name not in materials or materials[name].library:
            raise SystemExit(f"書き換えられる材質 {name!r} がありません")
    for source_name, target_name in settings["merge_images"].items():
        if source_name not in images or target_name not in images:
            raise SystemExit(f"統合する画像 {source_name!r} または {target_name!r} がありません")
    reference_name = settings["reference_material"]
    if reference_name and reference_name not in materials:
        raise SystemExit(f"手本の材質 {reference_name!r} がありません")
    reference = read_reference(materials[reference_name]) if reference_name else {}
    converted, alpha_changed = [], []
    for name in settings["materials"]:
        if not mtoon_of(materials[name]).enabled:
            convert_material(materials[name], settings, reference)
            converted.append(name)
    for name, mode in settings["alpha_modes"].items():
        gltf = mtoon_of(materials[name])
        if not gltf.enabled:
            raise SystemExit(f"材質 {name!r} の MToon を有効にしてください")
        if gltf.alpha_mode != mode:
            alpha_changed.append({"material": name, "from": gltf.alpha_mode, "to": mode})
            gltf.alpha_mode = mode
    cache = {}
    merge_datablocks(materials, settings["merge_materials"], lambda left, right: equal_materials(left, right, bpy, cache))
    merge_datablocks(images, settings["merge_images"], lambda left, right: image_signature(left, cache) == image_signature(right, cache))
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.save_as_mainfile(filepath=str(output), check_existing=False, copy=True, compress=True)
    if not output.is_file() or not output.stat().st_size or sha256(source) != digest:
        raise SystemExit("元のファイルを保った .blend の書き出しを確かめられませんでした")
    report = {"schema_version": "model3d-materials-mtoon/0.1", "blender": bpy.app.version_string,
              "input_blend_sha256": digest, "output_blend_sha256": sha256(output),
              "converted": converted, "alpha_changed": alpha_changed,
              "materials_merged": settings["merge_materials"], "images_merged": settings["merge_images"]}
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
