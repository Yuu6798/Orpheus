"""Connect a private, recovered dance CLI to RecipeGraph without importing it.

Only the reviewed legacy directory layout is supported. Asset names, part and
bone conventions remain in that private worker; this is not a general rigger.
The stage entry point uses only stdlib so worker dependencies stay isolated.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

STAGES = ("rebuild", "project_reference", "apply_texture", "render", "assemble")
REBUILD_STAGES = ("import_mesh", "import_segmentation", "label_parts", "split_parts", "dump_geometry",
                  "binding_validation", "extract_texture", "normalize_rig", "label_rig",
                  "add_secondary_bones", "bind_character", "outline")
REUSED = ("parts_w.npz", "parts_n.npz", "chains.npz", "torso_cut.npy", "basecolor_fix3.png", "e919/e919_p.blend")
WORKERS = ("rebuild_recipe.py", "run_upstream_blender.py", "face_project.py", "apply_texture.py",
           "dance_tool.py", "render_stage.py")
OUTPUTS = {
    "rebuild": {"blend": "character.blend", "geometry": "parts_geo.npz", "normals": "parts_n.npz",
                "provenance": "provenance.json", "execution": "execution.json"},
    "project_reference": {"texture": "integrated_basecolor.png", "report": "report.json", "integration": "integration_report.json"},
    "apply_texture": {"blend": "character.blend", "report": "character.texture_application.json"},
    "render": {"manifest": "render_manifest.json"},
    "assemble": {"video": "dance.mp4", "report": "dance.json"},
}


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def absolute_executable(path):
    # Preserve venv symlinks; Path.resolve() would select the base environment.
    path = Path(os.path.abspath(Path(path).expanduser()))
    if not path.is_file():
        raise ValueError(f"Executable does not exist: {path}")
    return path


def runtime_versions(python, blender):
    code = ("import json,platform,importlib.metadata as m; "
            "print(json.dumps({'python':platform.python_version(),'packages':"
            "{n:m.version(n) for n in ['numpy','scipy','Pillow','opencv-python-headless']}}))")
    py = subprocess.run([str(python), "-c", code], capture_output=True, text=True, check=True, timeout=60)
    bl = subprocess.run([str(blender), "--version"], capture_output=True, text=True, check=True, timeout=60)
    result = {**json.loads(py.stdout), "blender": bl.stdout.strip()}
    for tool in ("ffmpeg", "ffprobe"):
        executable = shutil.which(tool)
        if not executable:
            raise ValueError(f"Required encoder tool missing: {tool}")
        version = subprocess.run([executable, "-version"], capture_output=True, text=True, check=True, timeout=60)
        result[tool] = {"path": executable, "sha256": digest(executable), "version": version.stdout.splitlines()[0]}
    return result


def validate_frames(spec, total):
    if spec == "all":
        frames = list(range(1, total + 1))
    elif ":" in spec:
        values = [int(v) for v in spec.split(":")]
        if len(values) not in (2, 3):
            raise ValueError("Frames must use start:end[:step]")
        step = values[2] if len(values) == 3 else 1
        if step <= 0:
            raise ValueError("Frame step must be positive")
        frames = list(range(values[0], values[1] + 1, step))
    else:
        frames = [int(v) for v in spec.split(",")]
    if len(frames) < 2 or frames != sorted(set(frames)) or min(frames) < 1 or max(frames) > total:
        raise ValueError("Video needs at least two unique ascending frames within the animation")
    step = frames[1] - frames[0]
    if any(b - a != step for a, b in zip(frames, frames[1:])) or frames[-1] + step - 1 > total:
        raise ValueError("Video sampling must be evenly spaced and stay inside the source interval")
    return frames


def build(out, bundle, preset, face_config, python, blender, frames="1:147:3", profile="preview", threads=4):
    from . import contracts

    root, preset, face_config = (Path(p).resolve(strict=True) for p in (bundle, preset, face_config))
    python, blender = absolute_executable(python), absolute_executable(blender)
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError("Use a new empty recipe directory")
    if threads < 1:
        raise ValueError("Threads must be positive")
    setting, face = read(preset), read(face_config)
    source = (root / setting["source_assets"]).resolve(strict=True)
    upstream = (root / setting["recipe"]).resolve(strict=True)
    if upstream != (root / "upstream").resolve():
        raise ValueError("Recovered render workers require the bundle/upstream recipe layout")
    keys = (root / setting["light_keys"]).resolve(strict=True)
    animation = read(keys)
    selected = validate_frames(frames, int(animation["frames"]))
    if profile not in setting["render_profiles"]:
        raise ValueError(f"Unknown render profile: {profile}")
    files = {preset, face_config, keys, source / "manifest.json", Path(__file__).resolve(), python, blender}
    files.update(root / name for name in WORKERS)
    files.update(upstream / "models/mv" / name for name in REUSED)
    files.update(upstream.joinpath("scripts").glob("*.py"))
    for item in read(source / "manifest.json"):
        path = (source / item["name"]).resolve(strict=True)
        if not path.is_relative_to(source) or digest(path) != item["sha256"]:
            raise ValueError(f"Source manifest mismatch: {item['name']}")
        files.add(path)
    for name, path in face["inputs"].items():
        if name not in ("geometry_npz", "normals_npz", "part_name"):
            files.add((root / path).resolve(strict=True))
    inventory = {"schema": "model3d-dance-inputs/0.1", "files": [
        {"path": str(p), "sha256": digest(p), "bytes": p.stat().st_size} for p in sorted(files)],
        "runtime": runtime_versions(python, blender), "starts_from_completed_character": False}
    config = {"schema": "model3d-dance-connector/0.1", "bundle": str(root), "preset": str(preset),
              "face_config": str(face_config), "python": str(python), "blender": str(blender),
              "frames": frames, "selected_frames": selected, "profile": profile, "threads": threads,
              "source_fps": animation["fps"], "source_frames": animation["frames"]}
    out.mkdir(parents=True, exist_ok=True)
    config_path, inventory_path = out / "connector.json", out / "input_inventory.json"
    write(config_path, config)
    write(inventory_path, inventory)
    inputs = {name: {"path": str(path), "sha256": digest(path), "kind": "json"}
              for name, path in (("config", config_path), ("inventory", inventory_path))}
    inputs.update({f"source_{i:04d}": {"path": item["path"], "sha256": item["sha256"], "kind": "other"}
                   for i, item in enumerate(inventory["files"])})
    dependencies = {
        "rebuild": {},
        "project_reference": {"source_geometry": "$steps.rebuild.geometry", "source_normals": "$steps.rebuild.normals"},
        "apply_texture": {"source_blend": "$steps.rebuild.blend", "source_texture": "$steps.project_reference.texture"},
        "render": {"source_blend": "$steps.apply_texture.blend"},
        "assemble": {"render_manifest": "$steps.render.manifest"},
    }
    steps = []
    for stage in STAGES:
        argv = [sys.executable, str(Path(__file__).resolve()), "--stage", stage, "--config", "{config}",
                "--config-sha256", digest(config_path), "--inventory", "{inventory}",
                "--inventory-sha256", digest(inventory_path), "--work", "{work}"]
        for name in dependencies[stage]:
            argv.extend(["--input", name + "={" + name + "}"])
        parameters = {"config": {"tool": "private-dance-connector", "version": "0.1", "argv": argv}}
        if stage == "project_reference":
            parameters["review_note"] = "Reference projection uses a recovered heuristic; visual review and human acceptance remain required."
        steps.append({"id": stage, "operation": "external.run", "inputs": {"config": "$inputs.config", "inventory": "$inputs.inventory", **dependencies[stage]},
                      "parameters": parameters, "outputs": {**OUTPUTS[stage], "receipt": "stage_receipt.json"},
                      "notes": "Private recovered workers; original assets and accepted motion/corrections are reused."})
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "Private recovered dance reconstruction",
              "scope": "Raw asset rebuild, reference texture, existing choreography and preview rendering; not new image-to-3D generation.",
              "inputs": inputs, "steps": steps}
    contracts.validate("recipe", recipe)
    write(out / "recipe.json", recipe)
    return {"recipe": str(out / "recipe.json"), "inventory": str(inventory_path), "input_files": len(inventory["files"]),
            "stages": list(STAGES), "frames": len(selected), "status": "ready_for_execution"}


def check_inventory(inventory):
    for item in inventory["files"]:
        path = Path(item["path"])
        if not path.is_file() or digest(path) != item["sha256"]:
            raise ValueError(f"Pinned input changed or missing: {path}")


def copy_artifact(source, destination):
    source, destination = Path(source), Path(destination)
    if not source.is_file() or source.stat().st_size == 0:
        raise ValueError(f"Required worker output missing or empty: {source}")
    if source != destination:
        shutil.copyfile(source, destination)


def run_stage(stage, config, inventory, work, inputs):
    work = Path(work).resolve()
    work.mkdir(parents=True, exist_ok=True)
    job = work / "job"
    job.mkdir()  # Never consume stale worker output, including after a failed run.
    check_inventory(inventory)
    runtime = runtime_versions(config["python"], config["blender"])
    if runtime != inventory["runtime"]:
        raise ValueError("Worker runtime versions changed since recipe preparation")
    input_hashes = {name: digest(path) for name, path in inputs.items()}
    root = Path(config["bundle"])
    setting = read(config["preset"])
    python, blender = config["python"], config["blender"]
    started, commands = time.monotonic(), []

    def execute(argv):
        argv = [str(v) for v in argv]
        commands.append(argv)
        with (work / "worker.log").open("a", encoding="utf-8") as log:
            completed = subprocess.run(argv, cwd=root, stdout=log, stderr=subprocess.STDOUT,
                                       stdin=subprocess.DEVNULL, env={**os.environ, "PYTHONUNBUFFERED": "1"})
        if completed.returncode:
            raise RuntimeError(f"Worker exited {completed.returncode}; see {work / 'worker.log'}")

    cli = [python, root / "dance_tool.py", "--preset", config["preset"], "--python", python,
           "--blender", blender, "--threads", str(config["threads"])]
    if stage == "rebuild":
        execute([python, root / "rebuild_recipe.py", "--blender", blender, "--recipe", root / setting["recipe"],
                 "--source", root / setting["source_assets"], "--job", job, "--threads", config["threads"]])
        execution, provenance = read(job / "execution.json"), read(job / "provenance.json")
        if [v["step"] for v in execution] != list(REBUILD_STAGES) or any(v["status"] != "passed" for v in execution):
            raise ValueError("Worker did not complete all twelve rebuild stages")
        if provenance.get("starts_from_completed_character") is not False:
            raise ValueError("Rebuild did not attest raw-asset input")
        sources = {"blend": job / "models/mv/char_o.blend", "geometry": job / "models/mv/parts_geo.npz",
                   "normals": job / "models/mv/parts_n.npz", "provenance": job / "provenance.json", "execution": job / "execution.json"}
    elif stage == "project_reference":
        face = read(config["face_config"])
        for name, value in face["inputs"].items():
            if name != "part_name":
                face["inputs"][name] = str((root / value).resolve())
        face["inputs"].update(geometry_npz=str(inputs["source_geometry"]), normals_npz=str(inputs["source_normals"]))
        face["output_dir"] = str(job)
        write(work / "face_config.json", face)
        execute([python, root / "face_project.py", "--config", work / "face_config.json"])
        report = read(job / "integration_report.json")
        if not all(report.get(k) is True for k in ("outside_exactly_accepted_fix3", "inside_exactly_new_projection", "source_files_unchanged")):
            raise ValueError("Reference texture integration invariants failed")
        sources = {name: job / filename for name, filename in OUTPUTS[stage].items()}
    elif stage == "apply_texture":
        execute([blender, "--background", "--factory-startup", "--threads", config["threads"], "--disable-autoexec",
                 "--python-exit-code", "1", "--python", root / "apply_texture.py", "--", "--blend", inputs["source_blend"],
                 "--texture", inputs["source_texture"], "--out", work / "character.blend"])
        report = read(work / "character.texture_application.json")
        if not report.get("replacement_texture_packed") or not report.get("input_files_unchanged") or report.get("replaced_node_count", 0) < 1:
            raise ValueError("Texture application did not meet its output contract")
        sources = {name: work / filename for name, filename in OUTPUTS[stage].items()}
    elif stage == "render":
        execute([*cli, "render", "--blend", inputs["source_blend"], "--job", work,
                 "--frames", config["frames"], "--profile", config["profile"]])
        manifest = read(work / "render_manifest.json")
        if manifest.get("status") != "passed" or manifest["config"]["frames"] != config["selected_frames"]:
            raise ValueError("Render did not validate every requested frame")
        if [v["frame"] for v in manifest["outputs"]] != config["selected_frames"]:
            raise ValueError("Render output list differs from requested frames")
        for item in manifest["outputs"]:
            if digest(work / item["file"]) != item["sha256"]:
                raise ValueError("Rendered frame hash mismatch")
        sources = {"manifest": work / "render_manifest.json"}
    elif stage == "assemble":
        # The legacy assembler writes logs/sequence into its job directory.
        # Give it an isolated copy so a completed render step remains intact.
        manifest_path = Path(inputs["render_manifest"])
        manifest = read(manifest_path)
        if manifest.get("status") != "passed" or manifest["config"]["frames"] != config["selected_frames"]:
            raise ValueError("Assembly requires the validated render for this recipe")
        if [v["frame"] for v in manifest["outputs"]] != config["selected_frames"]:
            raise ValueError("Assembly frame list differs from requested frames")
        shutil.copyfile(manifest_path, job / "render_manifest.json")
        for item in manifest["outputs"]:
            source = (manifest_path.parent / item["file"]).resolve(strict=True)
            if not source.is_relative_to(manifest_path.parent) or digest(source) != item["sha256"]:
                raise ValueError("Assembly source frame changed or escaped the render directory")
            destination = job / item["file"]
            destination.symlink_to(source)
        execute([*cli, "assemble", "--job", job, "--output", work / "dance.mp4"])
        report = read(work / "dance.json")
        if report["frames"] != len(config["selected_frames"]) or report.get("audio") is not False:
            raise ValueError("Encoded video metadata differs from this recipe")
        sources = {name: work / filename for name, filename in OUTPUTS[stage].items()}
    else:
        raise ValueError(f"Unknown stage: {stage}")
    for name, source in sources.items():
        copy_artifact(source, work / OUTPUTS[stage][name])
    check_inventory(inventory)
    if any(digest(inputs[name]) != expected for name, expected in input_hashes.items()):
        raise ValueError("A stage input changed during execution")
    receipt = {"schema": "model3d-dance-stage/0.1", "stage": stage, "status": "executed",
               "visual_review_required": True, "runtime": runtime, "commands": commands,
               "input_sha256": input_hashes, "inventory_checked_before_and_after": True,
               "seconds": round(time.monotonic() - started, 3), "outputs": {
                   name: {"file": filename, "sha256": digest(work / filename), "bytes": (work / filename).stat().st_size}
                   for name, filename in OUTPUTS[stage].items()}}
    write(work / "stage_receipt.json", receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=STAGES, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--inventory-sha256", required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--input", action="append", default=[])
    args = parser.parse_args()
    for path, expected in ((args.config, args.config_sha256), (args.inventory, args.inventory_sha256)):
        if digest(path) != expected:
            raise ValueError(f"Connector configuration changed: {path}")
    inputs = {name: Path(path).resolve(strict=True) for name, path in (v.split("=", 1) for v in args.input)}
    result = run_stage(args.stage, read(args.config), read(args.inventory), args.work, inputs)
    print(json.dumps({"stage": args.stage, "seconds": result["seconds"], "outputs": list(result["outputs"])}))


if __name__ == "__main__":
    main()
