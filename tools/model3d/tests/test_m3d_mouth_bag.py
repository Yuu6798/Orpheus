"""Synthetic open-mouth geometry, shape-key transfer, and UV sampling."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from model3d import executor
from model3d.blender_adapter.mouth_bag import rim_cycle
from model3d.io import ToolError, atomic_json
from model3d.ops.face_ops import mouth_bag_settings


SETTINGS = {
    "object": "Fixture Face", "material": "Fixture Atlas", "uv_inside": [.7, .25], "uv_tongue": [.6, .3],
    "front_y": -.12, "window": {"half_width": .21, "z": [-.07, .07]}, "zone": {"half_width": .17},
    "depth": .18, "rings": 5, "half_height": .05,
}


@pytest.mark.parametrize("overrides", [
    {"uv_inside": None}, {"uv_inside": [.2, 1.1]}, {"uv_tongue": [.6, False]}, {"depth": 0},
    {"half_height": -1}, {"rings": True}, {"rings": 1.5}, {"last_ring_scale": 2},
    {"clearance": {"object": "Fixture Face", "half_width": .17, "z": [-.07, .07], "gap": .02}},
])
def test_mouth_bag_rejects_invalid_parameters(overrides):
    with pytest.raises(ToolError):
        mouth_bag_settings({**SETTINGS, **overrides})


def test_rim_selects_only_the_open_edges_on_the_largest_island():
    points = np.array([[-.1, -.2, 0], [0, -.2, .03], [.1, -.2, 0], [0, -.2, -.03], [0, -.4, .3],
                       [-.05, -.4, 0], [0, -.4, .03], [.05, -.4, 0], [0, -.4, -.03]])
    edges = np.array([[0, 1], [1, 2], [2, 3], [3, 0], [1, 4], [5, 6], [6, 7], [7, 8], [8, 5]])
    boundary = np.array([1, 1, 1, 1, 0, 1, 1, 1, 1], dtype=bool)
    assert rim_cycle(points, edges, boundary, SETTINGS) == [0, 1, 2, 3]
    boundary[1] = False
    with pytest.raises(ValueError, match="輪"):
        rim_cycle(points, edges, boundary, SETTINGS)


BLENDER_CHECK = r'''
import math
import sys

import bmesh
import bpy
from mathutils import Vector

N = 10

def make(path):
    bpy.ops.wm.read_factory_settings(use_empty=True)
    rim = [(.14 * math.cos(math.pi - 2*math.pi*i/N), -.2, .043 * math.sin(math.pi - 2*math.pi*i/N))
           for i in range(N)]
    outer = [(x * 1.9, -.32, z * 3.5) for x, _, z in rim]
    faces = [(N+(i+1)%N, N+i, i, (i+1)%N) for i in range(N)]
    mesh = bpy.data.meshes.new("Fixture Face")
    mesh.from_pydata(rim + outer, [], faces)
    face = bpy.data.objects.new("Fixture Face", mesh)
    bpy.context.scene.collection.objects.link(face)
    mesh.materials.append(bpy.data.materials.new("Fixture Atlas"))
    mesh.uv_layers.new(name="Fixture UV")
    for polygon in mesh.polygons:
        polygon.use_smooth = True
    mesh.normals_split_custom_set([(0, -1, 0)] * len(mesh.loops))
    face.shape_key_add(name="Basis")
    opened = face.shape_key_add(name="Open")
    open_wide = face.shape_key_add(name="Open Wide")
    open_wide.relative_key = opened
    for i, point in enumerate(opened.data):
        dz = math.sin(math.pi - 2*math.pi*(i % N)/N)
        point.co.z += .042 * dz
        open_wide.data[i].co.z += .085 * dz
    for name, weights in [("One", [.22 + .02*(i%N) for i in range(2*N)]),
                          ("Two", [.78 - .02*(i%N) for i in range(2*N)])]:
        group = face.vertex_groups.new(name=name)
        for i, weight in enumerate(weights):
            group.add([i], weight, "REPLACE")
    clone = face.copy()
    clone.name = "Fixture Clone"
    bpy.context.scene.collection.objects.link(clone)
    obstacle = bpy.data.meshes.new("Fixture Obstacle")
    obstacle.from_pydata([(-.06, -.05, -.05), (.06, -.05, -.05), (0, -.05, .05)], [], [(0, 1, 2)])
    bpy.context.scene.collection.objects.link(bpy.data.objects.new("Fixture Obstacle", obstacle))
    bpy.ops.wm.save_as_mainfile(filepath=path)

def check(source, built):
    bpy.ops.wm.open_mainfile(filepath=source, use_scripts=False)
    original = bpy.data.objects["Fixture Face"].data
    normals = [c.vector.copy() for c in original.corner_normals]
    originals = [[point.co.copy() for point in block.data] for block in original.shape_keys.key_blocks]
    bpy.ops.wm.open_mainfile(filepath=built, use_scripts=False)
    mesh = bpy.data.objects["Fixture Face"].data
    clone = bpy.data.objects["Fixture Clone"].data
    assert mesh != clone and len(clone.vertices) == 20 and len(mesh.vertices) == 71
    assert len(mesh.polygons) == 70 and len(clone.polygons) == 10
    assert len(mesh.shape_keys.key_blocks) == 3 and len(clone.shape_keys.key_blocks[0].data) == 20
    assert mesh.shape_keys.key_blocks["Open Wide"].relative_key.name == "Open"
    for before, block in zip(originals, mesh.shape_keys.key_blocks):
        assert all((a - b.co).length < 1e-7 for a, b in zip(before, block.data))
    assert all((before - mesh.corner_normals[i].vector).length < .001 for i, before in enumerate(normals))
    basis = mesh.shape_keys.key_blocks["Basis"].data
    opened = mesh.shape_keys.key_blocks["Open"].data
    for r in range(5):
        follow = (1 - (r+1)/5)**1.5
        for i in range(N):
            j = 2*N + r*N + i
            assert (opened[j].co - basis[j].co - (opened[i].co - basis[i].co) * follow).length < 1e-7
            assert [(g.group, g.weight) for g in mesh.vertices[j].groups] == [
                (g.group, g.weight) for g in mesh.vertices[i].groups]
    assert (opened[70].co - basis[70].co).length < 1e-7
    assert abs(basis[70].co.y - (-.074)) < 1e-6
    uv = mesh.uv_layers["Fixture UV"].data
    assert {tuple(round(x, 2) for x in uv[i].uv) for i in range(40, len(uv))} == {(.7, .25), (.6, .3)}
    bm = bmesh.new()
    bm.from_mesh(mesh)
    assert sum(edge.is_boundary for edge in bm.edges) == 10  # Outer edge of the fixture only.
    assert all(edge.is_boundary or edge.is_contiguous for edge in bm.edges)
    bm.free()

mode, *args = sys.argv[sys.argv.index("--") + 1:]
if mode == "make":
    make(*args)
else:
    check(*args)
'''


def test_mouth_bag_on_a_blend_with_shape_keys_and_shared_mesh(tmp_path):
    blender = os.environ.get("MODEL3D_TEST_BLENDER") or shutil.which("blender")
    if not blender:
        pytest.skip("Blender is needed for the synthetic mouth geometry")
    script = tmp_path / "fixture.py"
    script.write_text(BLENDER_CHECK, encoding="utf-8")

    def run_blender(*args):
        result = subprocess.run(
            [blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
             "--python", str(script), "--", *map(str, args)], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    source = tmp_path / "fixture.blend"
    run_blender("make", source)
    before = source.read_bytes()
    settings = {**SETTINGS, "blender": blender, "cap_extra": .004,
                "clearance": {"object": "Fixture Obstacle", "half_width": .13, "z": [-.07, .07], "gap": .024}}
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "mouth-fixture",
                         "inputs": {"face": {"path": source.name}},
                         "steps": [{"id": "bag", "operation": "model.mouth_bag", "inputs": {"blend": "$inputs.face"},
                                    "parameters": settings, "outputs": {"blend": "out.blend", "report": "mouth.json"}}]})
    run = executor.run_recipe(recipe, tmp_path / "runs")
    assert run["status"] == "succeeded", run
    artifacts = run["steps"]["bag"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    assert report["rim_vertices"] == 10 and report["new_vertices"] == 51 and report["new_faces"] == 60
    assert report["separated_mesh"] and report["shape_keys"] == 3 and report["tongue_faces"] > 0
    assert report["depth"] == pytest.approx(.122)
    assert source.read_bytes() == before
    run_blender("check", source, artifacts["blend"])
