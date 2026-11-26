import copy
import hashlib
from pathlib import Path

import pytest
from PIL import Image

from model3d import executor
from model3d.io import atomic_json, read_json


def manifest(tmp_path, name, colors, *, face=False):
    directory = tmp_path / name
    directory.mkdir()
    rows = []
    for T, color in colors.items():
        file = directory / f"f{T:04d}.png"
        image = Image.new("RGB", (4, 2), color)
        if face:
            image.paste((255, 255, 255), (0, 0, 2, 2))
        image.save(file)
        data = file.read_bytes()
        rows.append({"T": T, "f": T + 10, "file": file.name, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()})
    header = {"directory": str(directory), "fps": 30, "resolution": [4, 2]}
    if face:
        return {**header, "schema_version": "model3d-passes/0.1", "passes": {
            "head": {"format": "png", "alpha": False, "frames": rows, "missing": []}}}
    return {**header, "schema_version": "model3d-frames/0.1", "frames": rows, "missing": []}


def run_composite(tmp_path, manifests, parameters):
    for name, value in manifests.items():
        atomic_json(tmp_path / f"{name}.json", value)
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "composite-fx",
              "inputs": {name: {"path": f"{name}.json"} for name in manifests},
              "steps": [{"id": "finish", "operation": "render.composite_fx", "parameters": parameters,
                         "inputs": {name: f"$inputs.{name}" for name in manifests},
                         "outputs": {"frames": "output.json", "report": "report.json"}}]}
    atomic_json(tmp_path / "recipe.json", recipe)
    return executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")


def test_interval_rerender_and_face_protection_through_recipe(tmp_path):
    base = manifest(tmp_path, "base", {1: "black", 2: "black", 3: "black"})
    replacement = manifest(tmp_path, "replacement", {2: (30, 30, 30)})
    replacement["missing"] = [1, 3]
    previous = manifest(tmp_path, "previous", {1: (8, 8, 8), 3: (12, 12, 12)})
    face = manifest(tmp_path, "matte", {2: "black"}, face=True)
    effect = tmp_path / "effect"
    effect.mkdir()
    Image.new("RGBA", (4, 2), (200, 150, 100, 255)).save(effect / "f0002.png")

    run = run_composite(tmp_path, {"frames": base, "replacement": replacement, "previous": previous, "face_passes": face},
                        {"effect_dir": "effect", "update_ranges": [[2, 2]]})
    assert run["status"] == "succeeded", run["error"]
    artifacts = run["steps"]["finish"]["artifacts"]
    result = read_json(artifacts["frames"])
    directory = Path(result["directory"])
    assert (directory / "f0001.png").read_bytes() == (tmp_path / "previous/f0001.png").read_bytes()
    assert (directory / "f0003.png").read_bytes() == (tmp_path / "previous/f0003.png").read_bytes()
    with Image.open(directory / "f0002.png") as image:
        assert image.getpixel((0, 0)) == (30, 30, 30)
        assert image.getpixel((3, 0)) == (200, 150, 100)
    assert [r["f"] for r in result["frames"]] == [11, 12, 13]
    report = read_json(artifacts["report"])
    assert report["updated"] == [2]
    assert [r["source"] for r in report["frames"]] == ["previous", "replacement", "previous"]
    assert report["frames"][1]["head_sha256"] == face["passes"]["head"]["frames"][0]["sha256"]


@pytest.mark.parametrize("fault", ["old_bytes", "matte_time", "replacement_outside"])
def test_bad_reuse_or_matte_leaves_no_manifest(tmp_path, fault):
    base = manifest(tmp_path, "base", {1: "black", 2: "black"})
    previous = manifest(tmp_path, "previous", {1: "white"})
    face = manifest(tmp_path, "matte", {2: "black"}, face=True)
    effect = tmp_path / "effect"
    effect.mkdir()
    Image.new("RGBA", (4, 2), (200, 150, 100, 255)).save(effect / "f0002.png")
    inputs = {"frames": base, "previous": previous, "face_passes": face}
    if fault == "old_bytes":
        (tmp_path / "previous/f0001.png").write_bytes(b"changed")
    elif fault == "matte_time":
        face["passes"]["head"]["frames"][0]["f"] += 1
    else:
        inputs["replacement"] = copy.deepcopy(base)

    run = run_composite(tmp_path, inputs, {"effect_dir": "effect", "update_ranges": [[2, 2]]})
    assert run["status"] == "failed"
    assert not list((tmp_path / "runs").rglob("output.json"))
