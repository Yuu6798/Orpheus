import hashlib
import os
from pathlib import Path

import pytest

from model3d import executor
from model3d.blender_adapter import launcher
from model3d.io import ToolError, atomic_json, read_json
from model3d.ops.vrm_ops import mtoon_material_settings


@pytest.mark.parametrize(("settings", "error"), [
    ({}, "変換"),
    ({"materials": ["Fabric", "Fabric"]}, "materials"),
    ({"alpha_modes": {"Fabric": "clip"}}, "alpha_modes"),
    ({"merge_images": {"a": "a"}}, "統合元"),
    ({"merge_materials": {"a": "b", "b": "c"}}, "連鎖"),
    ({"merge_images": {"a": []}}, "merge_images"),
    ({"materials": ["Fabric"], "shade_from_base": 1}, "shade_from_base"),
    ({"merge_images": {"a": "b"}, "reference_material": "Fabric"}, "reference_material"),
    ({"materials": ["Fabric"], "shade_color_factors": {"Other": [1, 1, 1]}}, "shade_color_factors"),
    ({"materials": ["Fabric"], "base_color_factors": {"Fabric": [1, True, 0, 0]}}, "base_color_factors"),
])
def test_invalid_material_settings(settings, error):
    with pytest.raises(ToolError, match=error):
        mtoon_material_settings(settings)


def test_alpha_and_reference_are_opt_in():
    settings = mtoon_material_settings({"materials": ["Fabric"]})
    assert settings["alpha_modes"] == {}
    assert settings["base_color_factors"] == settings["shade_color_factors"] == {}
    assert settings["reference_material"] is None and settings["shade_from_base"] is False


FIXTURE = r"""
import sys
from pathlib import Path
import bpy

root = Path(sys.argv[-1])
for name, color in (("base", (0.2, 0.4, 0.6, 1)), ("other", (0.6, 0.3, 0.1, 1))):
    image = bpy.data.images.new(name, width=4, height=4, alpha=True)
    image.pixels = color * 16
    image.filepath_raw = str(root / (name + ".png"))
    image.save()
    bpy.data.images.remove(image)

def packed(filename, name):
    image = bpy.data.images.load(str(root / filename), check_existing=False)
    image.name = name
    image.pack()
    return image

base = packed("base.png", "Atlas")
copy = packed("base.png", "Atlas copy")
other = packed("other.png", "Other atlas")
for name, image in (("Solid", base), ("Solid copy", copy), ("Variant", base),
                    ("Convert", base), ("Translucent", other), ("Unselected", copy)):
    bpy.ops.mesh.primitive_plane_add()
    ob = bpy.context.object
    ob.name = name
    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    ob.data.materials.append(mat)
    if name.startswith("Solid") or name == "Variant":
        gltf = mat.vrm_addon_extension.mtoon1
        gltf.enabled = True
        gltf.alpha_mode = "BLEND"
        toon = gltf.extensions.vrmc_materials_mtoon
        gltf.pbr_metallic_roughness.base_color_texture.index.source = image
        toon.shade_multiply_texture.index.source = image
        toon.shade_color_factor = (0.2, 0.4, 0.6)
        toon.shading_shift_factor = 0.125
        if name == "Variant":
            mat.node_tree.nodes["Mtoon1BaseColorTexture.Image"].image = other
        continue
    tree = mat.node_tree
    texture = tree.nodes.new("ShaderNodeTexImage")
    texture.image = image
    shader = next(node for node in tree.nodes if node.type == "BSDF_PRINCIPLED")
    tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
    if name == "Translucent":
        shader.inputs["Alpha"].default_value = 0.375
        texture.interpolation = "Closest"
        texture.extension = "EXTEND"

bpy.ops.wm.save_as_mainfile(filepath=str(root / "input.blend"))
"""

CHECK = r"""
import bpy

mats = bpy.data.materials
assert "Solid copy" not in mats and "Atlas copy" not in bpy.data.images
assert bpy.data.objects["Solid copy"].data.materials[0] == mats["Solid"]
solid = mats["Solid"].vrm_addon_extension.mtoon1
converted = mats["Convert"].vrm_addon_extension.mtoon1
translucent = mats["Translucent"].vrm_addon_extension.mtoon1
unselected = mats["Unselected"]
assert next(node.image.name for node in unselected.node_tree.nodes if node.type == "TEX_IMAGE") == "Atlas"
assert not unselected.vrm_addon_extension.mtoon1.enabled
assert converted.enabled and solid.alpha_mode == converted.alpha_mode == "OPAQUE"
assert mats["Variant"].vrm_addon_extension.mtoon1.alpha_mode == "BLEND"
assert tuple(converted.pbr_metallic_roughness.base_color_factor) == (1, 0.5, 0.25, 1)
assert converted.pbr_metallic_roughness.base_color_texture.index.source.name == "Atlas"
toon = converted.extensions.vrmc_materials_mtoon
assert tuple(toon.shade_color_factor) == (0.25, 0.5, 0.75)
assert toon.shade_multiply_texture.index.source.name == "Atlas"
assert toon.shading_shift_factor == 0.125
assert translucent.enabled and translucent.alpha_mode == "BLEND"
assert tuple(translucent.pbr_metallic_roughness.base_color_factor) == (1, 1, 1, 0.375)
sampler = translucent.pbr_metallic_roughness.base_color_texture.index.sampler
assert sampler.mag_filter == "NEAREST" and sampler.wrap_s == sampler.wrap_t == "CLAMP_TO_EDGE"
"""


def recipe(tmp_path, parameters):
    path = tmp_path / "recipe.json"
    atomic_json(path, {"schema_version": "model3d-recipe/0.1", "name": "materials",
                       "inputs": {"blend": {"path": "input.blend"}},
                       "steps": [{"id": "finish", "operation": "vrm.materials_mtoon",
                                  "inputs": {"blend": "$inputs.blend"}, "parameters": parameters,
                                  "outputs": {"blend": "finished.blend", "report": "report.json"}}]})
    return path


@pytest.mark.skipif(not (os.getenv("MODEL3D_BLENDER") and os.getenv("BLENDER_USER_EXTENSIONS")),
                    reason="set MODEL3D_BLENDER and BLENDER_USER_EXTENSIONS for installed VRM Add-on")
def test_synthetic_blend_conversion_and_deduplication(tmp_path):
    blender = os.environ["MODEL3D_BLENDER"]
    extensions = os.environ["BLENDER_USER_EXTENSIONS"]
    addons = ["bl_ext.user_default.vrm"]
    script = tmp_path / "fixture.py"
    script.write_text(FIXTURE, encoding="utf-8")
    launcher.run_blender(blender, script, args=[tmp_path], addons=addons, extensions=extensions)
    source = tmp_path / "input.blend"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    settings = {"blender": blender, "blender_extensions": extensions}
    run = executor.run_recipe(recipe(tmp_path, settings | {
        "materials": ["Convert", "Translucent"], "shade_from_base": True, "reference_material": "Solid",
        "base_color_factors": {"Convert": [1, 0.5, 0.25, 1]},
        "shade_color_factors": {"Convert": [0.25, 0.5, 0.75]},
        "alpha_modes": {"Solid": "OPAQUE", "Solid copy": "OPAQUE"},
        "merge_materials": {"Solid copy": "Solid"}, "merge_images": {"Atlas copy": "Atlas"},
    }), tmp_path / "runs")
    step = run["steps"]["finish"]
    assert run["status"] == "succeeded", step.get("error")
    report = read_json(step["artifacts"]["report"])
    assert report["input_blend_sha256"] == hashlib.sha256(source.read_bytes()).hexdigest() == digest
    assert report["converted"] == ["Convert", "Translucent"]
    assert len(report["alpha_changed"]) == 2
    assert report["materials_merged"] == {"Solid copy": "Solid"}
    assert report["images_merged"] == {"Atlas copy": "Atlas"}
    script.write_text(CHECK, encoding="utf-8")
    launcher.run_blender(blender, script, blend=Path(step["artifacts"]["blend"]), addons=addons, extensions=extensions)
    for invalid in ({"merge_materials": {"Variant": "Solid"}}, {"merge_images": {"Other atlas": "Atlas"}}):
        run = executor.run_recipe(recipe(tmp_path, settings | invalid), tmp_path / "runs")
        assert run["status"] == "failed"
        log = Path(run["run_dir"]) / "finish" / "blender.log"
        assert "同じ内容と確認できません" in log.read_text(encoding="utf-8")
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
