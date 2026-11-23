import hashlib
import json
from pathlib import Path
from types import SimpleNamespace as Namespace

import pytest
from m3d_helpers import stand_in_executable

from model3d import executor
from model3d.blender_adapter.mtoon_preview import _shape_keys, shading_bounds
from model3d.io import ToolError, atomic_json
from model3d.ops.face_ops import mtoon_preview_settings


def _settings():
    return {
        "lights": [{"name": "key", "direction": [0, -3, 4]}],
        "views": [{"name": "detail", "direction": [0, -6, 0], "centre": [0, 0, 1], "scale": 2.25, "size": [64, 96]}],
    }


def test_shading_interval_has_expected_shift_width_and_exact_threshold():
    assert shading_bounds(0.125, 0.375) == (-0.75, 0.5)
    assert shading_bounds(-0.25, 1.0) == (0.25, 0.25)


def test_settings_uses_explicit_directions_and_supports_ordered_pose_and_multiple_shapes():
    settings = mtoon_preview_settings({
        **_settings(),
        "armature": "Rig", "pose": [{"bone": "joint", "axis": "Y", "degrees": -20}],
        "sets": [{"name": "rest"}, {"name": "change", "keys": {"Sample": {"Lift": 0.5}}}],
    })
    assert settings["lights"][0]["direction"] == pytest.approx([0, -0.6, 0.8])
    assert settings["views"][0] == {"name": "detail", "direction": [0, -1, 0], "centre": [0, 0, 1], "scale": 2.25, "size": [64, 96]}
    assert settings["pose"][0]["degrees"] == -20 and settings["reset_keys"]
    assert settings["sets"][1]["keys"] == {"Sample": {"Lift": 0.5}}


@pytest.mark.parametrize(("overrides", "message"), [
    ({"lights": []}, "lights"),
    ({"lights": [{"name": "key", "direction": [0, 0, 0]}]}, "ゼロ"),
    ({"views": [{"name": "a", "direction": [0, 0, 1], "centre": [0, 0, 0], "scale": 0}]}, "scale"),
    ({"views": [{"name": "a", "size": [16, 5000]}]}, "size"),
    ({"sets": [{"name": "rest"}, {"name": "rest"}]}, "重なっています"),
    ({"sets": [{"name": "rest"}], "keys": {}}, "どちらか一方"),
    ({"keys": {"Sample": {"Lift": float("nan")}}}, "有限"),
    ({"pose": [{"bone": "joint", "axis": "Y", "degrees": 20}]}, "armature"),
    ({"pose": [{"bone": "joint", "axis": "W", "degrees": 20}]}, "axis"),
    ({"background": [0.125, 0.25, 1.125]}, "background"),
])
def test_preview_invalid_settings(overrides, message):
    with pytest.raises(ToolError, match=message):
        mtoon_preview_settings({**_settings(), **overrides})


def test_shared_key_conflicts_and_basis_fail_before_changes():
    key = Namespace(name="Basis")
    data = Namespace(as_pointer=lambda: 42, use_relative=True, key_blocks={"Basis": key, "Lift": object()}, reference_key=key)
    obj = Namespace(type="MESH", data=Namespace(shape_keys=data))
    scene = Namespace(objects={"a": obj, "b": obj})
    with pytest.raises(ValueError, match="異なる値"):
        _shape_keys(scene, [{"name": "test", "keys": {"a": {"Lift": 0.125}, "b": {"Lift": 0.25}}}], False)
    with pytest.raises(ValueError, match="Basis"):
        _shape_keys(scene, [{"name": "test", "keys": {"a": {"Basis": 0.125}}}], False)


STAND_IN = r'''
import json, sys
from itertools import product
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def arg(name):
    return Path(args[args.index(name) + 1])
settings = json.loads(arg("--settings").read_text(encoding="utf-8"))
images = []
out = arg("--out-dir")
out.mkdir(parents=True)
for i, (pose, light, view) in enumerate(product(settings["sets"], settings["lights"], settings["views"]), 1):
    file = f"{i:04d}.png"
    (out / file).write_bytes(b"preview fixture")
    images.append({"file": file, "set": pose["name"], "light": light["name"], "view": view["name"], "size": view["size"]})
arg("--report").write_text(json.dumps({"schema_version": "model3d-mtoon-preview/0.1", "images": images, "materials": {"Sample": {}},
                                     "argv": sys.argv[1:], "settings": settings}), encoding="utf-8")
'''


def test_recipe_records_files_and_hashes_without_editing_input(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    blend = tmp_path / "fixture.blend"
    blend.write_bytes(b"synthetic scene")
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "preview", "inputs": {"scene": {"path": blend.name}},
              "steps": [{"operation": "render.mtoon_preview", "id": "preview", "inputs": {"blend": "$inputs.scene"},
                         "parameters": {**_settings(), "blender": str(blender), "sets": [{"name": "neutral"}, {"name": "lift"}]},
                         "outputs": {"report": "preview.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["preview"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert [image["file"] for image in report["images"]] == ["0001.png", "0002.png"]
    for image in report["images"]:
        assert (Path(report["directory"]) / image["file"]).is_file()
        assert image["sha256"] == hashlib.sha256(b"preview fixture").hexdigest()
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "mtoon_preview.py"
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]
    assert step["observations"][0]["images"] == 2
    assert blend.read_bytes() == b"synthetic scene"
