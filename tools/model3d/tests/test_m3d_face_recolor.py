"""面だけを塗り、別の面も使う画素を探す。"""
import json
import os
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from model3d.blender_adapter.face_recolor import paint_pixels
from model3d.io import ToolError, atomic_json
from model3d.ops.model_ops import recolor_paint, recolor_region


def test_paint_keeps_alpha_and_does_not_wrap_the_atlas():
    pixels = np.full((5, 7, 4), 0.5, dtype=np.float32)
    mask = np.zeros(pixels.shape[:2], dtype=bool)
    mask[2, 0] = True
    result = paint_pixels(pixels, mask, recolor_paint({"rgb": [1, 0, 0], "pad": 1}))
    assert np.all(result[2, :2, :3] == [1, 0, 0])
    assert np.array_equal(result[:, -1], pixels[:, -1])
    assert np.array_equal(result[..., 3], pixels[..., 3])
    assert np.count_nonzero(np.any(result != pixels, axis=2)) == 4


@pytest.mark.parametrize("api, config, message", [
    (recolor_region, {"island_vertices": [4, 2]}, "island_vertices"),
    (recolor_region, {"island_box": {"x": [0, 1]}}, "island_vertices"),
    (recolor_paint, {"rgb": [1, 0, 0], "select": {"mode": "dark", "value": 0.3, "softness": 0}}, "softness"),
    (recolor_paint, {"rgb": [2, 0, 0]}, "paint.rgb"),
])
def test_recolor_rejects_invalid_region_parameters(api, config, message):
    with pytest.raises(ToolError, match=message):
        api(config)


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_blender_recolors_one_region_and_reports_shared_atlas_face(tmp_path):
    from model3d import executor

    image = np.full((16, 16, 4), [160, 128, 96, 255], dtype=np.uint8)
    texture, blend = tmp_path / "colors.png", tmp_path / "rig.blend"
    Image.fromarray(image).save(texture)
    script = tmp_path / "fixture.py"
    script.write_text("""
import sys
import bpy

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
texture, out = sys.argv[-2:]
image = bpy.data.images.load(texture)
image.pack()
material = bpy.data.materials.new("Cloth")
material.use_nodes = True
material.node_tree.nodes.new("ShaderNodeTexImage").image = image
secondary = bpy.data.scenes.new("Secondary")

def make(name, faces, atlas):
    mesh = bpy.data.meshes.new(name)
    coordinates = [(3 * j + x, y, 0) for j in range(faces) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
    mesh.from_pydata(coordinates, [], [tuple(range(i, i + 4)) for i in range(0, len(coordinates), 4)])
    uv = mesh.uv_layers.new(name="UV")
    for j, (left, right) in enumerate(atlas):
        for k, (u, v) in enumerate(((left, 0.016), (right, 0.016), (right, 0.984), (left, 0.984))):
            uv.data[j * 4 + k].uv = (u, v)
    obj = bpy.data.objects.new(name, mesh)
    if name == "Sharing":
        obj.location.x = 7
    obj.data.materials.append(material)
    (secondary if name == "Sharing" else bpy.context.scene).collection.objects.link(obj)

make("Target", 2, [(0.016, 0.481), (0.52, 0.984)])
make("Sharing", 1, [(0.016, 0.481)])
bpy.ops.wm.save_as_mainfile(filepath=out, check_existing=False)
""", encoding="utf-8")
    blender = os.environ["MODEL3D_BLENDER"]
    subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                    "--python", str(script), "--", str(texture), str(blend)], check=True, capture_output=True, timeout=60)
    frozen = blend.read_bytes()
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "recolor",
                         "inputs": {"model": {"path": blend.name}},
                         "steps": [{"id": "recolor", "operation": "model.recolor_part", "inputs": {"blend": "$inputs.model"},
                                    "parameters": {"blender": blender, "object": "Target", "region": {"box": {"x": [-0.1, 1.1]},
                                                   "material": "Cloth", "island_vertices": [4, 4]},
                                                   "paint": {"rgb": [24 / 255, 56 / 255, 220 / 255]}},
                                    "outputs": {"blend": "edited.blend", "texture": "edited.png", "report": "recolor.json"}}]})
    run = executor.run_recipe(recipe, tmp_path / "runs")
    step = run["steps"]["recolor"]
    assert step["status"] == "needs_review", run
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["selected_triangles"] == 2 and report["texels_changed"] == 128
    assert report["other_faces_using_changed_texels"] == {"Sharing": {"faces": 1, "texels": 128}}
    with Image.open(step["artifacts"]["texture"]) as changed:
        observed = np.asarray(changed.convert("RGBA"))
        assert np.all(observed[:, :8] == [24, 56, 220, 255])
        assert np.array_equal(observed[:, 8:], image[:, 8:])
    assert blend.read_bytes() == frozen
