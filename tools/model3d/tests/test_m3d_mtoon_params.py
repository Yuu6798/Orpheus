import json
from pathlib import Path
from types import SimpleNamespace as Namespace

import pytest

from m3d_helpers import stand_in_executable
from model3d import executor
from model3d.blender_adapter import mtoon_params
from model3d.io import atomic_json


def _material(enabled=True):
    mtoon = Namespace(**{name: [1.0, 1.0, 1.0] for name in mtoon_params.COLORS},
                      **{name: 0.0 for name in mtoon_params.SCALARS},
                      matcap_texture=Namespace(index=Namespace(source=None)))
    gltf = Namespace(enabled=enabled, extensions=Namespace(vrmc_materials_mtoon=mtoon))
    return Namespace(vrm_addon_extension=Namespace(mtoon1=gltf)), mtoon


@pytest.mark.parametrize(("values", "error"), [
    (None, "materials"),
    ({"": {"shading_toony_factor": 0.125}}, "材質名"),
    ({"Sample": {"optional": True}}, "1 つ以上"),
    ({"Sample": {"optional": 1}}, "optional"),
    ({"Sample": {"shade_color_factor": "#12345678"}}, "RGB"),
    ({"Sample": {"matcap_factor": [1.0, 0.5, True]}}, "matcap_factor"),
    ({"Sample": {"parametric_rim_color_factor": [1.01, 0.5, 0.25]}}, "parametric_rim_color_factor"),
    ({"Sample": {"shading_toony_factor": -0.25}}, "shading_toony_factor"),
    ({"Sample": {"parametric_rim_fresnel_power_factor": -0.25}}, "parametric_rim_fresnel_power_factor"),
    ({"Sample": {"parametric_rim_lift_factor": float("inf")}}, "parametric_rim_lift_factor"),
    ({"Sample": {"shading_shift_factor": float("nan")}}, "shading_shift_factor"),
    ({"Sample": {"shading_shift_factor": 1e39}}, "shading_shift_factor"),
    ({"Sample": {"rim_lighting_mix_factor": 1.125}}, "rim_lighting_mix_factor"),
    ({"Sample": {"matcap_texture": "blend"}}, "入力名"),
    ({"Sample": {"matcap_texture": "unlinked.png"}}, "入力名"),
    ({"Sample": {"matcap_texture": "reflection"}}, "画像ファイル"),
    ({"Sample": {"look_at": []}}, "知らない"),
])
def test_invalid_material_settings_stop_before_blender(tmp_path, values, error):
    with pytest.raises(ValueError, match=error):
        mtoon_params.compile_settings(values, {"blend": tmp_path / "input.blend", "reflection": tmp_path / "missing.png"})


def test_mtoon_params_sets_only_requested_fields_and_shares_packed_images(tmp_path):
    image_path = tmp_path / "tiny.png"
    image_path.write_bytes(b"synthetic image")
    first, first_mtoon = _material()
    second, second_mtoon = _material()
    control, control_mtoon = _material()
    image = Namespace(name="tiny.png", source="FILE", size=(2, 3), packed_file=None)
    loaded = []

    def load(path, *, check_existing):
        loaded.append((path, check_existing))
        return image

    def pack():
        image.packed_file = object()

    image.pack = pack
    data = Namespace(materials={"First": first, "Second": second, "Control": control}, images=Namespace(load=load))
    params = {
        "First": {"shade_color_factor": "#0080ff", "shading_toony_factor": 0.25, "shading_shift_factor": -0.25,
                  "parametric_rim_color_factor": [0.125, 0.25, 0.375], "parametric_rim_fresnel_power_factor": 2.125,
                  "parametric_rim_lift_factor": -0.125, "rim_lighting_mix_factor": 0.375,
                  "matcap_factor": [0.625, 0.75, 0.875], "matcap_texture": "reflection"},
        "Second": {"matcap_texture": "reflection"},
        "Spare": {"optional": True, "shading_shift_factor": 0.25},
    }
    control_before = mtoon_params.snapshot(control_mtoon)
    second_before = mtoon_params.snapshot(second_mtoon)
    report = mtoon_params.change_materials(data, mtoon_params.compile_settings(params, {"reflection": image_path}))
    assert report["changed"] == ["First", "Second"]
    assert report["skipped"] == ["Spare"]
    assert loaded == [(str(image_path), False)]
    assert first_mtoon.matcap_texture.index.source is second_mtoon.matcap_texture.index.source is image
    assert report["materials"]["First"]["after"] == {
        **params["First"], "shade_color_factor": pytest.approx([0.0, 0.2158605001, 1.0]),
        "matcap_texture": {"image": "tiny.png", "packed": True},
    }
    assert report["materials"]["Second"] == {
        "before": second_before,
        "after": {**second_before, "matcap_texture": {"image": "tiny.png", "packed": True}},
    }
    assert mtoon_params.snapshot(control_mtoon) == control_before


@pytest.mark.parametrize(("name", "optional", "message"), [
    ("Missing", False, "材質がありません"),
    ("Plain", False, "MToon"),
    ("Plain", True, "MToon"),
])
def test_material_lookup_validates_all_targets_before_writing(name, optional, message):
    material, mtoon = _material()
    plain, _ = _material(enabled=False)
    data = Namespace(materials={"Sample": material, "Plain": plain})
    settings = mtoon_params.compile_settings({
        "Sample": {"shading_toony_factor": 0.25},
        name: {"shading_toony_factor": 0.75, "optional": optional},
    }, {})
    with pytest.raises(ValueError, match=message):
        mtoon_params.change_materials(data, settings)
    assert mtoon.shading_toony_factor == 0.0


def test_invalid_image_stops_before_writing(tmp_path):
    image_path = tmp_path / "broken.png"
    image_path.write_bytes(b"invalid")
    material, mtoon = _material()
    image = Namespace(source="FILE", size=(0, 0))
    data = Namespace(materials={"Sample": material}, images=Namespace(load=lambda *_args, **_kwargs: image))
    settings = mtoon_params.compile_settings({"Sample": {"matcap_texture": "reflection", "shading_toony_factor": 0.25}},
                                             {"reflection": image_path})
    with pytest.raises(ValueError, match="reflection.*静止画像"):
        mtoon_params.change_materials(data, settings)
    assert mtoon.shading_toony_factor == 0.0


STAND_IN = r'''
import json, sys
from pathlib import Path
if "--version" in sys.argv:
    print("Blender 5.2.4")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def flag(name):
    return Path(args[args.index(name) + 1])
settings = json.loads(flag("--settings").read_text(encoding="utf-8"))
flag("--out-blend").write_bytes(b"output blend")
flag("--report").write_text(json.dumps({"changed": ["Sample"], "skipped": [], "settings": settings,
                                      "argv": sys.argv[1:]}), encoding="utf-8")
'''


def test_recipe_passes_declared_materials_and_texture_to_the_blender_adapter(tmp_path):
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    blend = tmp_path / "sample.blend"
    blend.write_bytes(b"synthetic input blend")
    image = tmp_path / "tiny.png"
    image.write_bytes(b"synthetic input image")
    recipe = {
        "schema_version": "model3d-recipe/0.1", "name": "mtoon",
        "inputs": {"model": {"path": blend.name}, "picture": {"path": image.name}},
        "steps": [{
            "id": "mtoon", "operation": "vrm.mtoon_params",
            "inputs": {"blend": "$inputs.model", "reflection": "$inputs.picture"},
            "parameters": {"blender": str(blender), "materials": {"Sample": {"matcap_texture": "reflection"}}},
            "outputs": {"blend": "out.blend", "report": "report.json"},
        }],
    }
    atomic_json(tmp_path / "recipe.json", recipe)
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["mtoon"]
    report = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))
    assert report["settings"] == {"materials": {"Sample": {"optional": False, "fields": {"matcap_texture": "reflection"}}},
                                  "textures": {"reflection": str(image)}}
    argv = report["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "mtoon_params.py"
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]
    assert step["observations"][0]["changed"] == ["Sample"]
    assert blend.read_bytes() == b"synthetic input blend"
