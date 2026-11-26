"""顔の遮りの工程の入力と、合成の動く袖のレイを確かめる。"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from m3d_helpers import stand_in_executable
from model3d import executor
from model3d.blender_adapter.face_occlusion import pixel_from_plan, sync_occluders_for_render
from model3d.io import ToolError, atomic_json
from model3d.ops.check_ops import face_occlusion_settings


PARAMETERS = {"landmarks": {"nose": {"bone": "Head"}, "edge": {"bone": "Head", "offset": [20, 0, 0]}},
              "occluders": ["Sleeve", "Behind", "Hidden"]}


def run_recipe(tmp_path, blender, **parameters):
    inputs = {"source": {"path": "input.blend"}}
    bound = {"blend": "$inputs.source"}
    if (tmp_path / "cameras.json").exists():
        inputs["cameras"] = {"path": "cameras.json"}
        bound["cameras"] = "$inputs.cameras"
    path = tmp_path / "recipe.json"
    atomic_json(path, {"schema_version": "model3d-recipe/0.1", "name": "face",
                       "inputs": inputs,
                       "steps": [{"id": "check", "operation": "check.face_occlusion",
                                  "inputs": bound, "outputs": {"report": "face.json"},
                                  "parameters": {**PARAMETERS, "blender": str(blender), **parameters}}]})
    run = executor.run_recipe(path, tmp_path / "runs")
    assert run["status"] in {"succeeded", "needs_review"}, run
    report = json.loads(Path(run["steps"]["check"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    return run["status"], report


def cameras():
    def frame(T, f, x):
        return {"T": T, "f": f, "matrix": [[1, 0, 0, x], [0, 1, 0, 0], [0, 0, 1, 5], [0, 0, 0, 1]],
                "focal_px": 500, "principal_px": [500, 500]}
    return {"schema_version": "model3d-cameras/0.1", "unit": "m", "resolution": [1000, 1000],
            "clip_m": [0.1, 10], "frames": [frame(8, 1, 0), frame(9, 1.5, 0), frame(10, 1, 3)]}


def test_planned_camera_pixel_coordinates_are_top_left():
    assert pixel_from_plan((1, 2, -10), 50, (6, 8)) == [11.0, -2.0]
    assert pixel_from_plan((0, 0, 10), 50, (6, 8)) is None


def test_sync_uses_render_subdivision_levels_and_simplify_cap():
    render = SimpleNamespace(use_simplify=True, simplify_subdivision=1, simplify_subdivision_render=3)
    subdiv = SimpleNamespace(name="Subd", type="SUBSURF", show_viewport=True, show_render=True, levels=0, render_levels=2)
    multires = SimpleNamespace(name="Multi", type="MULTIRES", show_viewport=False, show_render=True,
                              levels=1, render_levels=4, total_levels=2)
    obj = SimpleNamespace(name="Sleeve", modifiers=[subdiv, multires])
    scene = SimpleNamespace(render=render)

    assert sync_occluders_for_render(scene, [obj]) == {"Sleeve/Subd", "Sleeve/Multi"}
    assert render.simplify_subdivision == 3
    assert subdiv.levels == 2 and multires.levels == 2 and multires.show_viewport
    assert sync_occluders_for_render(scene, [obj]) == set()


@pytest.mark.parametrize("params", [{"occluders": []}, {"occluders": ["Sleeve", "Sleeve"]},
                                    {"landmarks": {"eyes": {"bone": "Head", "offset": [1, 2]}}},
                                    {"frames": [3, 1]}, {"margin_m": -1}, {"report_only": 1}])
def test_invalid_face_occlusion_settings(params):
    with pytest.raises(ToolError):
        face_occlusion_settings({**PARAMETERS, **params}, None)


def test_camera_plan_settings_reject_ambiguity_and_duplicate_frames():
    with pytest.raises(ToolError, match="camera"):
        face_occlusion_settings({**PARAMETERS, "camera": "Cam"}, cameras())
    repeated = cameras()
    repeated["frames"].append(repeated["frames"][0])
    with pytest.raises(ToolError, match="T"):
        face_occlusion_settings(PARAMETERS, repeated)


def test_face_occlusion_recipe_calls_blender_and_records_review(tmp_path):
    (tmp_path / "input.blend").write_bytes(b"base")
    atomic_json(tmp_path / "cameras.json", cameras())
    blender = stand_in_executable(tmp_path, "blender", """
import json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4")
    raise SystemExit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def path(flag):
    return Path(args[args.index(flag) + 1])
settings = json.loads(path("--settings").read_text(encoding="utf-8"))
report = {"frames": [{"T": 8}], "counts": {"nose": {"clear": 0, "occluded": 1}}, "occluded_T": [8],
          "settings": settings, "script": Path(sys.argv[sys.argv.index("--python") + 1]).name}
path("--report").write_text(json.dumps(report), encoding="utf-8")
""")
    status, report = run_recipe(tmp_path, blender, frames=[8, 8])
    assert status == "needs_review"
    assert report["script"] == "face_occlusion.py"
    assert report["settings"]["cameras"] == cameras()
    assert report["settings"]["frames"] == [8, 8]
    assert report["settings"]["occluders"] == PARAMETERS["occluders"]
    status, _ = run_recipe(tmp_path, blender, report_only=True)
    assert status == "succeeded"
    assert (tmp_path / "input.blend").read_bytes() == b"base"


def test_real_blender_with_scene_camera_and_camera_plan(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("実 Blender がありません（MODEL3D_TEST_BLENDER）")
    source_path = tmp_path / "input.blend"
    script = tmp_path / "make.py"
    script.write_text(f"""
import bpy

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
data = bpy.data.armatures.new("Rig")
rig = bpy.data.objects.new("Rig", data)
bpy.context.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
bone = data.edit_bones.new("Head")
bone.head = (0, 0, 0)
bone.tail = (0, 1, 0)
bpy.ops.object.mode_set(mode="OBJECT")

camera_data = bpy.data.cameras.new("View")
camera_data.type = "ORTHO"
camera_data.ortho_scale = 6
camera = bpy.data.objects.new("View", camera_data)
bpy.context.collection.objects.link(camera)
camera.location = (0, 0, 5)
scene = bpy.context.scene
scene.camera = camera
scene.frame_start, scene.frame_end = 1, 2

for name, z in [("Sleeve", 2), ("Behind", -1), ("Hidden", 3)]:
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(-0.25, -0.25, 0), (0.25, -0.25, 0), (0.25, 0.25, 0), (-0.25, 0.25, 0)], [], [(0, 1, 2, 3)])
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    obj.location.z = z
    if name == "Sleeve":
        obj.keyframe_insert("location", frame=1)
        obj.location.x = 2
        obj.keyframe_insert("location", frame=2)
    if name == "Hidden":
        obj.hide_render = True

for name, kind in [("Subd edge", "SUBSURF"), ("Multires edge", "MULTIRES")]:
    bpy.ops.mesh.primitive_cube_add(size=1.5, location=(0.7, 0.7, 2))
    obj = bpy.context.object
    obj.name = name
    modifier = obj.modifiers.new("Round", kind)
    if kind == "MULTIRES":
        bpy.ops.object.multires_subdivide(modifier=modifier.name, mode="CATMULL_CLARK")
        bpy.ops.object.multires_subdivide(modifier=modifier.name, mode="CATMULL_CLARK")
    modifier.levels = 0
    modifier.render_levels = 2
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    assert evaluated.ray_cast((-0.7, -0.7, 3), (0, 0, -1), distance=5)[0]
scene.render.use_simplify = True
scene.render.simplify_subdivision = 0
scene.render.simplify_subdivision_render = 2
bpy.ops.wm.save_as_mainfile(filepath={str(source_path)!r})
""", encoding="utf-8")
    run = subprocess.run([blender, "--background", "--factory-startup", "--python-exit-code", "1", "--python", str(script)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    source = source_path.read_bytes()
    status, report = run_recipe(tmp_path, blender)
    assert status == "needs_review"
    assert report["occluded_T"] == [1]
    assert report["counts"]["nose"] == {"clear": 1, "occluded": 1, "out_of_view": 0, "too_near": 0}
    assert report["counts"]["edge"]["out_of_view"] == 2
    nose = report["frames"][0]["landmarks"]["nose"]
    assert nose["hit"]["object"] == "Sleeve" and nose["hit"]["gap_to_landmark_m"] == pytest.approx(2)

    status, report = run_recipe(tmp_path, blender, occluders=["Subd edge", "Multires edge"])
    assert status == "succeeded"
    assert report["occluded_T"] == [] and report["counts"]["nose"]["clear"] == 2
    assert set(report["synced_modifiers"]) == {"Subd edge/Round", "Multires edge/Round"}

    atomic_json(tmp_path / "cameras.json", cameras())
    status, report = run_recipe(tmp_path, blender, report_only=True)
    assert status == "succeeded"
    assert report["occluded_T"] == [8]
    assert [frame["f"] for frame in report["frames"]] == [1, 1.5, 1]
    assert report["counts"]["nose"]["clear"] == 2
    assert report["frames"][2]["landmarks"]["nose"]["uv"] == pytest.approx([0.2, 0.5])
    assert source_path.read_bytes() == source
