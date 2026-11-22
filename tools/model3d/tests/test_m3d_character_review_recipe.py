"""Follow a measured GLB finding through the recipe graph into the camera evidence."""
import os
from pathlib import Path

from PIL import Image
import pytest

from m3d_helpers import run_cli
from model3d.blender_adapter.launcher import run_blender
from model3d.io import atomic_json, read_json


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_roundtrip_measurement_drives_review_frames_and_links(tmp_path):
    blender = os.environ["MODEL3D_BLENDER"]
    run_blender(blender, Path(__file__).parent / "data" / "make_roundtrip_tube.py", args=[tmp_path])
    # Both scenes contain the rig, but only Performance contains the measured mesh.
    link_stage = tmp_path / "link_stage.py"
    link_stage.write_text("import bpy\n"
                          "bpy.data.scenes['Stage'].collection.objects.link(bpy.data.objects['Rig'])\n"
                          "bpy.ops.wm.save_as_mainfile(filepath=bpy.data.filepath)\n", encoding="utf-8")
    run_blender(blender, link_stage, tmp_path / "native.blend")
    Image.new("RGB", (32, 32), "#394e42").save(tmp_path / "guide.png")
    section = {"mesh": "Sleeve", "bone": "Forearm", "tip": "Wrist"}
    atomic_json(tmp_path / "measure.json", {
        "native": "native.blend", "delivered": "delivered.glb", "frames": [1, 7, 13],
        "sections": [{"label": "arm", "native": section, "delivered": {**section, "mesh": "Tube"}}],
    })
    model = {"path": "native.blend", "armature": "Rig", "left": "Wrist", "right": "Forearm",
             "anchor": "Forearm", "meshes": ["Sleeve"]}
    atomic_json(tmp_path / "views.json", {
        "before": model, "after": model, "references": {"left": "guide.png"}, "scale": 2, "size": 32,
        "events": [{"id": "thin", "label": "Twisted forearm", "region": "arm", "focus_view": "left",
                    "finding": {"kind": "sections", "label": "arm"}}],
    })
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {
        "schema_version": "model3d-recipe/0.1", "name": "review measured export",
        "inputs": {"views": {"path": "views.json"}, "measure": {"path": "measure.json"}},
        "steps": [
            {"id": "review", "operation": "review.character_views",
             "inputs": {"spec": "$inputs.views", "roundtrip": "$steps.measure.report"},
             "outputs": {"report": "review.json"}},
            {"id": "measure", "operation": "check.character_roundtrip",
             "inputs": {"spec": "$inputs.measure"}, "outputs": {"report": "measurement.json"}},
        ],
    })

    exit_code, run, stderr = run_cli("run", recipe, "--out", tmp_path / "runs", "--quiet", "--tool", f"blender={blender}")
    assert exit_code == 0, (run, stderr)
    assert run["steps"]["measure"]["status"] == "succeeded"
    assert run["steps"]["review"]["status"] == run["status"] == "needs_review"
    run_dir = Path(run["run_dir"])
    measured = read_json(run_dir / "measure" / "measurement.json")
    assert measured["native_scene"] == "Performance"
    worst = measured["worst"]["sections"]["arm"]
    assert worst["ratio"] == pytest.approx(.5, abs=.01)
    assert (worst["frame"], worst["seconds"]) == (13, .5)
    index = Path(read_json(run_dir / "review" / "review.json")["index"])
    package = index.parent
    assert package.is_relative_to(run_dir / "review")
    assert read_json(package / "roundtrip.json") == measured
    event, = read_json(package / "events.json")
    assert event["frame"] == worst["frame"] and event["focus_view"] == "left"
    cameras = read_json(package / "views" / "cameras.json")["cameras"]
    assert len(cameras) == 12 and all(c["seconds"] == worst["seconds"] for c in cameras)
    html = index.read_text(encoding="utf-8")
    assert f'ratio = {worst["ratio"]:.6g}' in html
    assert 'href="./roundtrip.json"' in html and 'href="#event-1-left">0.500' in html
    assert 'id="event-1-left"' in html and (package / "views" / "thin" / "left" / "after.png").is_file()
