"""Clock, paired measurements and an optional physical Blender → glTF → Blender replay."""
import json
import os
import struct
from pathlib import Path

import pytest

from model3d.blender_adapter.launcher import run_blender
from model3d.character_roundtrip import _delivery_info, _settings, compare, run
from model3d.io import ToolError


def spec():
    section = {"mesh": "Sleeve", "bone": "Forearm", "tip": "Wrist"}
    key = {"mesh": "Sleeve", "key": "Blink"}
    return {"native": "native.blend", "delivered": "delivered.glb", "frames": [1, 7, 13],
            "sections": [{"label": "arm", "native": section, "delivered": {**section, "mesh": "Tube"}}],
            "expressions": [{"label": "blink", "native": key, "delivered": key}]}


@pytest.mark.parametrize("change", [{"frames": []}, {"frames": [True]}, {"frames": [1, 1]},
                                    {"sections": [{}]}, {"native_start": 2}, {"framse": [1]}])
def test_bad_mapping_fails_before_launch(change):
    with pytest.raises(ToolError):
        _settings({**spec(), **change})


def write_glb(path, **data):
    doc = json.dumps({"asset": {"version": "2.0"}, **data}).encode()
    doc += b" " * (-len(doc) % 4)
    path.write_bytes(struct.pack("<4sIII4s", b"glTF", 2, 20 + len(doc), len(doc), b"JSON") + doc)


@pytest.mark.parametrize("clips", [[], [{}, {}]])
def test_static_or_ambiguous_clips_cannot_produce_reports(tmp_path, clips):
    glb = tmp_path / "delivered.glb"
    write_glb(glb, animations=clips)
    (tmp_path / "native.blend").write_bytes(b"unchanged")
    request = tmp_path / "fields.json"
    request.write_text(json.dumps(spec()))
    output = tmp_path / "report.json"
    output.write_text("prior report")
    with pytest.raises(ToolError, match="one animation"):
        run(request, output, "unused")
    assert output.read_text() == "prior report"


def test_imported_mesh_aliases_require_one_node_per_unique_mesh(tmp_path):
    glb = tmp_path / "scene.glb"
    clip = {"name": "Dance", "samplers": [{"input": i} for i in range(3)], "channels": [{"sampler": 0}, {"sampler": 1}]}
    # Shorter channels hold endpoint values. Unused samplers cannot extend the clip.
    write_glb(glb, animations=[clip], accessors=[{"min": [.5], "max": [1]}, {"min": [.75], "max": [1.5]},
                                                  {"min": [0], "max": [2]}],
              nodes=[{"name": "Left", "mesh": 0}, {"name": "Right", "mesh": 0}, {"name": "Face", "mesh": 1}],
              meshes=[{"name": "Arm data"}, {"name": "Face data"}])
    clip, aliases = _delivery_info(glb)
    assert clip == {"name": "Dance", "start_seconds": .5, "end_seconds": 1.5}
    assert aliases == {"Face": "Face data"}


def test_worst_frame_and_undefined_native_radius():
    def samples(radii, values):
        return {"samples": [{"frame": f, "seconds": (f-1)/24, "sections": {"arm": r}, "expressions": {"blink": v}}
                            for f, (r, v) in enumerate(zip(radii, values), 1)]}
    data = compare(samples([0, .25, .25], [0, 1, 0]), samples([0, .10, .09], [0, .25, 0]))
    assert data["frames"][0]["sections"]["arm"]["ratio"] is None
    assert data["worst"]["sections"]["arm"]["ratio"] == pytest.approx(.36)
    assert data["worst"]["sections"]["arm"]["frame"] == 3
    assert data["worst"]["expressions"]["blink"]["delta"] == -.75
    assert data["worst"]["expressions"]["blink"]["frame"] == 2


@pytest.fixture(scope="module")
def real_assets(tmp_path_factory):
    blender = os.getenv("MODEL3D_BLENDER")
    if not blender:
        pytest.skip("set MODEL3D_BLENDER to the Blender executable")
    folder = tmp_path_factory.mktemp("roundtrip")
    run_blender(blender, Path(__file__).parent / "data" / "make_roundtrip_tube.py", args=[folder])
    return folder


def replay(tmp_path, folder, **overrides):
    cfg = {**spec(), **overrides}
    for k in ("native", "delivered"):
        cfg[k] = str(folder / cfg[k])
    request, output = tmp_path / "spec.json", tmp_path / "report.json"
    request.write_text(json.dumps(cfg))
    run(request, output, os.environ["MODEL3D_BLENDER"])
    return json.loads(output.read_text(encoding="utf-8"))


@pytest.mark.parametrize("delivery, origin", [("delivered.glb", 0), ("late.glb", 4.5)])
def test_imported_skin_and_blink_on_a_synced_clock(tmp_path, real_assets, delivery, origin):
    report = replay(tmp_path, real_assets, delivered=delivery, delivered_start=origin)
    assert report["native_scene"] == "Performance" and report["fps"] == 24
    assert report["clip"]["start_seconds"] == pytest.approx(origin/24)
    rest, middle, twisted = report["frames"]
    assert rest["sections"]["arm"]["native"] == pytest.approx(.1, abs=1e-4)
    assert rest["sections"]["arm"]["ratio"] == pytest.approx(1, abs=1e-4)
    assert twisted["sections"]["arm"]["ratio"] == pytest.approx(.5, abs=.01)
    assert twisted["seconds"] == .5 and twisted["delivered_frame"] == origin + 12
    assert middle["expressions"]["blink"] == pytest.approx({"native": 1, "delivered": 1, "delta": 0})
    assert report["worst"]["sections"]["arm"]["frame"] == 13


@pytest.mark.parametrize("origin", [0, 5])
def test_samples_outside_positive_clip_refuse_clamping(tmp_path, real_assets, origin):
    with pytest.raises(ToolError, match="outside GLB range"):
        replay(tmp_path, real_assets, delivered="late.glb", delivered_start=origin)
    assert not (tmp_path / "report.json").exists()


def test_native_saved_in_rest_position_still_samples_animation(tmp_path, real_assets):
    source = real_assets / "rest.blend"
    original = source.read_bytes()
    report = replay(tmp_path, real_assets, native=source.name)
    rest, _, twisted = report["frames"]
    arm_rest = rest["sections"]["arm"]["native"]
    arm_twisted = twisted["sections"]["arm"]
    assert arm_twisted["native"] == pytest.approx(arm_rest * .5, abs=.002)
    assert arm_twisted["ratio"] == pytest.approx(1, abs=.02)
    assert source.read_bytes() == original


def test_section_tracks_bind_vertices_through_evaluated_reordering(tmp_path, real_assets):
    baseline = replay(tmp_path, real_assets)
    reordered = replay(tmp_path, real_assets, native="reordered.blend")
    for expected, actual in zip(baseline["frames"], reordered["frames"], strict=True):
        assert actual["sections"]["arm"] == pytest.approx(expected["sections"]["arm"])


def test_muted_expression_and_unsupported_native_scenes(tmp_path, real_assets):
    report = replay(tmp_path, real_assets, native="muted.blend")
    assert report["frames"][1]["expressions"]["blink"] == pytest.approx({"native": 0, "delivered": 1, "delta": 1})
    with pytest.raises(ToolError, match="relative, non-basis"):
        replay(tmp_path, real_assets, native="absolute.blend")
    with pytest.raises(ToolError, match="expected one native scene"):
        replay(tmp_path, real_assets, native="ambiguous.blend")
