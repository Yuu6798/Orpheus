import hashlib
import inspect
import json
import os
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from model3d import executor
from model3d.blender_adapter import launcher
from model3d.blender_adapter.eye_math import GeometryError, validate
from model3d.eye_build import iris_mesh, removal_triangles, validate_settings
from model3d.io import atomic_json, read_json


def eye_config(layout, eyes):
    return {"object": "Synthetic face", "head_group": "Surface", "material": "Eye atlas",
            "front_y": -0.02, "resolution": 0.001, "grid": 0.003, "clearance": 0.0008,
            "behind_skin": 0.002, "plate_margin": 0.008, "wall_margin": 0.002, "rim_search": 0.009,
            "shade_depth": 0.006, "iris_lift": 0.0005, "plate_uv": [0.92, 0.2, 0.7],
            "iris_uv": [0.12, 0.2, 0.7, 0.8], "iris_rings": [0.4, 0.75, 1], "iris_segments": 12,
            "layout": layout, "eyes": eyes}


def mesh_fixture(kind):
    points, faces, eyes = [], [], {}
    x_grid = np.arange(-28, 29, dtype=float) / 200
    z_grid = np.arange(-14, 15, dtype=float) / 200
    nz, nx = len(z_grid), len(x_grid)
    for z in z_grid:
        for x in x_grid:
            points.append((x, -0.09 + 0.07 * x + 0.12 * abs(z), z))
    for row in range(nz - 1):
        for col in range(nx - 1):
            x, z = x_grid[col] + 0.0025, z_grid[row] + 0.0025
            if abs(abs(x) - 0.065) < 0.019 and abs(z) < 0.014:
                continue
            i = row * nx + col
            faces.append((i, i + 1, i + nx + 1, i + nx))
    for side, x in (("R", -0.065), ("L", 0.065)):
        eye = {"window": [x - 0.033, x + 0.033, -0.034, 0.034], "pupil": [x, 0.001],
               "iris": [0.009, 0.011], "radius": 0.12}
        ids = []
        corners = [(x - 0.02, -0.015), (x + 0.02, -0.015), (x + 0.02, 0.015), (x - 0.02, 0.015)]
        center = len(points)
        points.append((x, -0.073, 0.0))
        for a, b in corners:
            if kind == "connected":
                ids.append(int(np.argmin(abs(z_grid - b))) * nx + int(np.argmin(abs(x_grid - a))))
            else:
                ids.append(len(points))
                points.append((a, -0.073, b))
        for i in range(4):
            faces.append((center, ids[i], ids[(i + 1) % 4]))
        if kind == "connected":
            offset = len(points)
            for dx, dz in ((-0.007, -0.006), (0.007, -0.006), (0.007, 0.008), (-0.007, 0.008)):
                points.append((x + dx, -0.076 + 0.06 * dz, dz))
            iris_ids = list(range(offset, offset + 4))
            faces.append(tuple(iris_ids))
            eye.update({"remove": {"vertices": [center, *iris_ids]}, "fit_iris_vertices": iris_ids,
                        "pitch_limit": 15, "plate_removed_margin": 0.004})
        else:
            eye["remove"] = {"component_size": 5}
        eyes[side] = eye
    return points, faces, eyes


@pytest.mark.parametrize("mode", ["projected", "tangent"])
def test_iris_mesh_has_exact_expected_topology_and_radius(mode):
    *_, eyes = mesh_fixture("connected")
    cfg = validate_settings(eye_config(mode, eyes))
    eye = eyes["R"]
    centre = [-0.065, 0.03, 0]
    points, uv, triangles, quads = iris_mesh(eye, cfg, centre, np.array([[1, 0, 0], [0, 0, 1]]))
    assert len(points) == 37 and len(triangles) == 12 and len(quads) == 24
    assert set(np.r_[triangles.ravel(), quads.ravel()]) == set(range(len(points)))
    assert np.max(np.abs(np.linalg.norm(points - centre, axis=1) - (eye["radius"] + cfg["iris_lift"]))) < 1e-12
    assert uv.min() >= 0.12 and uv.max() <= 0.8
    np.testing.assert_allclose(points[0, [0, 2]], eye["pupil"])


def test_configuration_rejects_ambiguous_removal_and_absurd_resolution():
    *_, eyes = mesh_fixture("connected")
    cfg = eye_config("projected", eyes)
    with pytest.raises(GeometryError, match="resolution"):
        validate_settings({**cfg, "resolution": 1e-10})
    eyes["R"]["remove"]["component_size"] = 5
    with pytest.raises(GeometryError, match="component_size"):
        validate_settings(cfg)


def test_removal_raster_includes_all_triangles_of_a_polygon():
    triangles = np.array([[0, 1, 2], [0, 2, 3], [0, 3, 4], [3, 5, 4]])
    polygons = np.array([0, 0, 0, 1])
    assert removal_triangles(triangles, polygons, [1]).tolist() == [True, True, True, False]


FIXTURE = """
import bpy
import json
import numpy as np
from pathlib import Path
import sys

root, kind = Path(sys.argv[-2]), sys.argv[-1]
cfg_points, faces, eyes = mesh_fixture(kind)
mesh = bpy.data.meshes.new("Fixture mesh")
mesh.from_pydata(cfg_points, [], faces)
mesh.update()
ob = bpy.data.objects.new("Synthetic face", mesh)
bpy.context.scene.collection.objects.link(ob)
ob.vertex_groups.new(name="Surface").add(list(range(len(cfg_points))), 1, "REPLACE")
ob.shape_key_add(name="Basis")
key = ob.shape_key_add(name="Shift")
for v in key.data:
    v.co.z += 0.004 + 0.012 * v.co.x + 0.015 * v.co.z
old_uv = mesh.uv_layers.new(name="Old UV")
for item in old_uv.data:
    item.uv = (0.1, 0.15)
mesh.materials.append(bpy.data.materials.new("Base surface"))
(root / "eyes.json").write_text(json.dumps(eyes), encoding="utf-8")
bpy.ops.wm.save_as_mainfile(filepath=str(root / "fixture.blend"))
"""

CHECK = """
import bpy
import json
import numpy as np
from pathlib import Path
import sys

path = Path(sys.argv[-1])
data = json.loads(path.read_text(encoding="utf-8"))
mesh = bpy.data.objects[data["object"]].data
ob = bpy.data.objects[data["object"]]
assert len(mesh.materials) == 2
image = next(n.image for n in mesh.materials[1].node_tree.nodes if n.type == "TEX_IMAGE")
assert image.packed_file and list(image.size) == [8, 8]
assert "_model3d_eye_build_marker" not in mesh.attributes
assert mesh.uv_layers.active.name == "Old UV"
assert len(mesh.shape_keys.key_blocks) == 2
measurements = {}
for side, eye in data["eyes"].items():
    groups = {key: set(eye[key]) for key in ("iris_vertices", "plate_vertices", "lid_vertices")}
    assert all(groups.values()) and len(set.union(*groups.values())) == sum(map(len, groups.values()))
    assert all(mesh.vertices[idx].groups[0].weight == 1.0
               for idx in groups["iris_vertices"] | groups["plate_vertices"])
    iris = np.array([tuple(ob.matrix_world @ mesh.vertices[i].co) for i in eye["iris_vertices"]])
    errors = np.abs(np.linalg.norm(iris - eye["sphere"]["centre"], axis=1) - (eye["sphere"]["radius"] + 0.0005))
    assert errors.max() < 2e-8
    a, b = mesh.shape_keys.key_blocks[0], mesh.shape_keys.key_blocks[1]
    def shifts(indices):
        return [float((b.data[i].co - a.data[i].co).z) for i in sorted(indices)]
    assert max(map(abs, shifts(groups["iris_vertices"]))) < 1e-7
    lids = sorted(groups["lid_vertices"])
    lid_xz = np.array([tuple(a.data[i].co) for i in lids])[:, [0, 2]]
    lid_shifts = np.array(shifts(lids))
    transferred = 0
    for i, delta in zip(sorted(groups["plate_vertices"]), shifts(groups["plate_vertices"])):
        if abs(delta) < 1e-7:
            continue
        v = a.data[i].co
        distances = np.sum((lid_xz - [v.x, v.z]) ** 2, axis=1)
        near = np.argsort(distances, kind="stable")[:3]
        assert abs(delta - np.average(lid_shifts[near], weights=1 / np.maximum(distances[near], np.finfo(float).eps))) < 2e-6
        transferred += 1
    assert transferred > 0
    measurements[side] = {"iris_max_radial_error": float(errors.max()),
                          "transferred_shapes": transferred}
path.with_name("verification.json").write_text(json.dumps(measurements), encoding="utf-8")
"""


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to installed Blender executable")
@pytest.mark.parametrize(("kind", "mode"), [("connected", "projected"), ("shell", "tangent")])
def test_synthetic_eye_build_feeds_existing_geometry_schema(tmp_path, kind, mode):
    blender = os.environ["MODEL3D_BLENDER"]
    script = tmp_path / "fixture.py"
    script.write_text(inspect.getsource(mesh_fixture) + FIXTURE, encoding="utf-8")
    launcher.run_blender(blender, script, args=[tmp_path, kind])
    source = tmp_path / "fixture.blend"
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    atlas = tmp_path / "atlas.png"
    Image.new("RGB", (8, 8), (245, 170, 120)).save(atlas)
    cfg = eye_config(mode, read_json(tmp_path / "eyes.json"))
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "eye-build",
                         "inputs": {"head": {"path": source.name}, "color": {"path": atlas.name}},
                         "steps": [{"id": "eyes", "operation": "model.eye_build",
                                    "inputs": {"blend": "$inputs.head", "atlas": "$inputs.color"},
                                    "parameters": {**cfg, "blender": blender},
                                    "outputs": {"blend": "head.blend", "geometry": "geometry.json", "report": "report.json"}}]})
    run = executor.run_recipe(recipe, tmp_path / "runs")
    step = run["steps"]["eyes"]
    assert run["status"] == "succeeded", step.get("error")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    data = step["artifacts"]
    geometry, report = validate(read_json(data["geometry"])), read_json(data["report"])
    assert report["removed_vertices"] == 10
    for side, record in report["eyes"].items():
        assert 0 < record["bent_vertices"] < record["plate_vertices"]
        assert record["iris_vertices"] == len(geometry["eyes"][side]["iris_vertices"]) == 37
    script.write_text(CHECK, encoding="utf-8")
    launcher.run_blender(blender, script, blend=Path(data["blend"]), args=[data["geometry"]])
