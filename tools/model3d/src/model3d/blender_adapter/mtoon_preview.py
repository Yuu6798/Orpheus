"""Blender 内で MToon の直接光だけを描く。設定・材質・形状キーは保存しない。"""

import json
import math
import sys
from pathlib import Path

if __package__:
    from . import face_sheet
    from .color_factor import mtoon_of
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import face_sheet
    from color_factor import mtoon_of


def shading_bounds(shift, toony):
    """法線と光の内積を 0〜1 に写す区間。"""
    return -1.0 + toony - shift, 1.0 - toony - shift


def _material_inputs(material):
    gltf = mtoon_of(material)
    if gltf is None or not gltf.enabled or not material.node_tree:
        return None
    pbr = gltf.pbr_metallic_roughness
    mtoon = gltf.extensions.vrmc_materials_mtoon
    node = material.node_tree.nodes.get("Mtoon1BaseColorTexture.Image")
    image = getattr(node, "image", None) or pbr.base_color_texture.index.source
    return image, {"base": list(pbr.base_color_factor)[:3], "shade": list(mtoon.shade_color_factor),
                   "shift": float(mtoon.shading_shift_factor), "toony": float(mtoon.shading_toony_factor),
                   "texture": image.name if image is not None else None}


def _preview_shader(material, image, values):
    """白い方向光 1 つの明暗を、シーンの照明に依存しない発光色で描く。"""
    tree = material.node_tree
    nodes, links = tree.nodes, tree.links
    nodes.clear()
    texture = None
    if image is not None:
        texture = nodes.new("ShaderNodeTexImage")
        texture.image = image
    normal = nodes.new("ShaderNodeNewGeometry")
    light = nodes.new("ShaderNodeCombineXYZ")
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    links.new(normal.outputs["Normal"], dot.inputs[0])
    links.new(light.outputs[0], dot.inputs[1])
    lower, upper = shading_bounds(values["shift"], values["toony"])
    if lower == upper:  # 幅 0 の場合は境目で明るい側に切り替える
        dark = nodes.new("ShaderNodeMath")
        dark.operation = "LESS_THAN"
        dark.inputs[1].default_value = lower
        links.new(dot.outputs["Value"], dark.inputs[0])
        step = nodes.new("ShaderNodeMath")
        step.operation = "SUBTRACT"
        step.inputs[0].default_value = 1.0
        links.new(dark.outputs[0], step.inputs[1])
        shading = step.outputs[0]
    else:
        ramp = nodes.new("ShaderNodeMapRange")
        ramp.clamp = True
        ramp.inputs["From Min"].default_value = lower
        ramp.inputs["From Max"].default_value = upper
        ramp.inputs["To Min"].default_value = 0.0
        ramp.inputs["To Max"].default_value = 1.0
        links.new(dot.outputs["Value"], ramp.inputs["Value"])
        shading = ramp.outputs["Result"]
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    links.new(shading, mix.inputs[0])
    for socket, label in ((6, "shade"), (7, "base")):
        color = (*values[label], 1.0)
        if texture is None:
            mix.inputs[socket].default_value = color
            continue
        tint = nodes.new("ShaderNodeMix")
        tint.data_type, tint.blend_type = "RGBA", "MULTIPLY"
        tint.inputs[0].default_value = 1.0
        links.new(texture.outputs["Color"], tint.inputs[6])
        tint.inputs[7].default_value = color
        links.new(tint.outputs[2], mix.inputs[socket])
    emission = nodes.new("ShaderNodeEmission")
    links.new(mix.outputs[2], emission.inputs["Color"])
    output = nodes.new("ShaderNodeOutputMaterial")
    links.new(emission.outputs[0], output.inputs["Surface"])
    material.use_backface_culling = False
    return light


def _pose(bpy, settings):
    if not settings["pose"]:
        return
    from mathutils import Matrix

    armature = bpy.context.scene.objects.get(settings["armature"])
    if armature is None or armature.type != "ARMATURE":
        raise ValueError(f"アーマチュアがありません: {settings['armature']}")
    bones = armature.pose.bones
    for item in settings["pose"]:
        if item["bone"] not in bones:
            raise ValueError(f"ポーズを付ける骨がありません: {item['bone']}")
    for bone in bones:
        for constraint in bone.constraints:
            constraint.mute = True
    for item in settings["pose"]:
        bpy.context.view_layer.update()
        bone = bones[item["bone"]]
        head = bone.matrix.translation
        turn = Matrix.Rotation(math.radians(item["degrees"]), 4, item["axis"])
        bone.matrix = Matrix.Translation(head) @ turn @ Matrix.Translation(-head) @ bone.matrix
    bpy.context.view_layer.update()


def _shape_keys(scene, sets, reset):
    """共有された Key データも、各セットにつき一度だけ設定する。"""
    key_data = {}
    assignments = {}
    if reset:
        for obj in scene.objects:
            if obj.type == "MESH" and (keys := obj.data.shape_keys) is not None and keys.use_relative:
                key_data[keys.as_pointer()] = keys
    for preset in sets:
        grouped = {}
        for name, entries in preset["keys"].items():
            obj = scene.objects.get(name)
            keys = obj.data.shape_keys if obj is not None and obj.type == "MESH" else None
            if keys is None or not keys.use_relative:
                raise ValueError(f"相対の形状キーを持つ物体がありません: {name}")
            pointer = keys.as_pointer()
            key_data[pointer] = keys
            requested = grouped.setdefault(pointer, {})
            for key, value in entries.items():
                if key not in keys.key_blocks or key == keys.reference_key.name:
                    raise ValueError(f"{name} に指定できる形状キーがありません: {key}")
                if key in requested and requested[key] != value:
                    raise ValueError(f"共有されている形状キー {key} に異なる値が指定されています")
                requested[key] = value
        assignments[preset["name"]] = grouped
    for pointer, keys in key_data.items():
        states = {block.name: (block.mute, block.slider_min, block.slider_max)
                  for block in keys.key_blocks if block.name != keys.reference_key.name}
        unmute, widen = face_sheet.key_changes([(name, grouped.get(pointer, {})) for name, grouped in assignments.items()], states)
        for name in unmute:
            keys.key_blocks[name].mute = False
        for name, (low, high) in widen.items():
            keys.key_blocks[name].slider_min, keys.key_blocks[name].slider_max = low, high
        if keys.animation_data is not None:
            keys.animation_data_clear()
    for obj in scene.objects:
        if obj.type == "MESH" and (keys := obj.data.shape_keys) is not None and keys.as_pointer() in key_data:
            obj.show_only_shape_key = False
    return key_data, assignments


def _configure_scene(bpy, settings):
    scene = bpy.context.scene
    for obj in scene.objects:
        if obj.type in ("EMPTY", "LIGHT"):
            obj.hide_render = True
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = settings["samples"]
    for target, fixed in ((scene.render, face_sheet.RENDER_FIXED),
                          (scene.render.image_settings, face_sheet.IMAGE_FIXED),
                          (scene.display_settings, face_sheet.DISPLAY_FIXED)):
        for name, value in fixed.items():
            setattr(target, name, value)
    scene.view_settings.view_transform = "Standard"
    for name, value in face_sheet.VIEW_FIXED.items():
        setattr(scene.view_settings, name, value)
    scene.render.resolution_percentage = 100
    scene.render.use_stamp = False
    for marker in scene.timeline_markers:
        marker.camera = None
    world = bpy.data.worlds.new("model3d_mtoon_preview")
    world.use_nodes = True
    world.node_tree.nodes["Background"].inputs[0].default_value = (*settings["background"], 1.0)
    scene.world = world
    data = bpy.data.cameras.new("model3d_mtoon_preview")
    data.type = "ORTHO"
    camera = bpy.data.objects.new("model3d_mtoon_preview", data)
    scene.collection.objects.link(camera)
    scene.camera = camera
    return camera


def main(argv=None):
    import bpy
    from mathutils import Vector

    if argv is None:
        argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    args = face_sheet.parse_args(argv)
    settings = json.loads(Path(args.settings).read_text(encoding="utf-8"))
    bpy.ops.wm.open_mainfile(filepath=str(Path(args.blend).resolve()), use_scripts=False)
    scene = bpy.context.scene
    _pose(bpy, settings)
    key_data, assignments = _shape_keys(scene, settings["sets"], settings["reset_keys"])
    lights, materials = [], {}
    for material in bpy.data.materials:
        values = _material_inputs(material) if material.users else None
        if values is not None:
            image, factors = values
            lights.append(_preview_shader(material, image, factors))
            materials[material.name] = factors
    if not materials:
        raise ValueError("MToon の材質がありません。VRM Add-on と入力 .blend を確認してください")
    camera = _configure_scene(bpy, settings)
    unit = scene.unit_settings.scale_length
    camera.data.clip_start, camera.data.clip_end = camera.data.clip_start / unit, 50 / unit
    output = Path(args.out_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    images = []
    for preset in settings["sets"]:
        for pointer, keys in key_data.items():
            if settings["reset_keys"]:
                for block in keys.key_blocks:
                    if block != keys.reference_key:
                        block.value = 0.0
            for key, value in assignments[preset["name"]].get(pointer, {}).items():
                keys.key_blocks[key].value = value
        bpy.context.view_layer.update()
        for light in settings["lights"]:
            for node in lights:
                for index, component in enumerate(light["direction"]):
                    node.inputs[index].default_value = component
            for view in settings["views"]:
                centre = Vector(view["centre"]) / unit
                direction = Vector(view["direction"])
                camera.location = centre + direction * (10 / unit)
                camera.rotation_euler = (-direction).to_track_quat("-Z", "Y").to_euler()
                camera.data.ortho_scale = view["scale"] / unit
                scene.render.resolution_x, scene.render.resolution_y = view["size"]
                file = f"{len(images) + 1:04d}.png"
                scene.render.filepath = str(output / file)
                bpy.ops.render.render(write_still=True)
                images.append({"set": preset["name"], "light": light["name"], "view": view["name"],
                               "size": view["size"], "file": file})
    report = {"schema_version": "model3d-mtoon-preview/0.1", "blender": bpy.app.version_string,
              "materials": materials, "images": images, "settings": settings}
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
