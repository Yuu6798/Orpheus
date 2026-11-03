"""model3d の CLI。出力は JSON（mv と同じ）。失敗は Error: を stderr に出して終了コード 1。"""
import argparse
import json
import sys
from pathlib import Path

from . import VERSION, contracts
from .io import ToolError, atomic_json, read_json

INPUT_KINDS = ("mesh", "image", "blend", "glb", "fbx", "obj", "json", "recipe", "other")


def parser():
    p = argparse.ArgumentParser(prog="model3d", description="self-made 3D modeling tool: recipes, semantic parts, checks, run records")
    p.add_argument("--version", action="version", version=f"model3d {VERSION}")
    commands = p.add_subparsers(dest="command", required=True)

    demo = commands.add_parser("demo", help="write synthetic inputs, preset, semantic map and a recipe")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--clearance", type=float, default=0.004)
    demo.add_argument("--resolution", type=int, default=64, help="grid resolution passed to the stand-in generator")

    dance = commands.add_parser("dance-recipe", help="connect a private recovered dance worker bundle to RecipeGraph")
    dance.add_argument("dir", type=Path)
    dance.add_argument("--bundle", type=Path, required=True)
    dance.add_argument("--preset", type=Path, required=True)
    dance.add_argument("--face-config", type=Path, required=True)
    dance.add_argument("--python", type=Path, required=True, help="isolated legacy worker Python; venv symlink is preserved")
    dance.add_argument("--blender", type=Path, required=True)
    dance.add_argument("--frames", default="1:147:3")
    dance.add_argument("--profile", default="preview")
    dance.add_argument("--threads", type=int, default=4)

    run = commands.add_parser("run", help="execute a recipe and write a run record")
    run.add_argument("recipe", type=Path)
    run.add_argument("--out", type=Path, help="default: <recipe dir>/model3d_runs")
    run.add_argument("--force", action="store_true", help="run every step again even if an identical step succeeded before")
    run.add_argument("--preset", type=Path, help="override the recipe's preset")
    run.add_argument("--tool", action="append", default=[], metavar="NAME=PATH", help="external tool path (e.g. blender=/path/to/blender)")
    run.add_argument("--quiet", action="store_true")

    accept = commands.add_parser("accept", help="record the human decision for a run")
    accept.add_argument("run_dir", type=Path)
    accept.add_argument("--decision", choices=["accepted", "rejected", "hold"], required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--note")

    edit = commands.add_parser("edit", help="record a manual edit (what, by whom, minutes) on a run")
    edit.add_argument("run_dir", type=Path)
    edit.add_argument("--by", required=True)
    edit.add_argument("--what", required=True)
    edit.add_argument("--minutes", type=float)

    manifest = commands.add_parser("manifest", help="write an AssetManifest for input files")
    manifest.add_argument("out", type=Path)
    manifest.add_argument("--asset-id", required=True)
    manifest.add_argument("--input", action="append", default=[], metavar="NAME=PATH[:KIND]", required=True,
                          help=f"KIND: {', '.join(INPUT_KINDS)}（省略時は拡張子と中身から決める）")
    manifest.add_argument("--units", default="m", choices=["m", "cm", "mm"])
    manifest.add_argument("--up", default="+Z")
    manifest.add_argument("--forward", default="-Y")
    manifest.add_argument("--asset-version", default="1")
    manifest.add_argument("--part", action="append", default=[], help="part id")
    manifest.add_argument("--notes")

    sem = commands.add_parser("semantic", help="build or confirm a SemanticMap from a preset")
    sem_sub = sem.add_subparsers(dest="semantic_command", required=True)
    build = sem_sub.add_parser("build", help="map part ids and bone names to roles with a preset")
    build.add_argument("preset", type=Path)
    build.add_argument("out", type=Path)
    build.add_argument("--asset-id", required=True)
    build.add_argument("--parts", default="", help="comma-separated part ids")
    build.add_argument("--bones", default="", help="comma-separated bone names (NAME or NAME:PARENT)")
    build.add_argument("--rig", type=Path, help="take bone names and parents from a model3d-rig/0.1 file")
    confirm = sem_sub.add_parser("confirm", help="record a human confirmation of one mapping")
    confirm.add_argument("semantic_map", type=Path)
    confirm.add_argument("--kind", choices=["parts", "bones", "regions"], required=True)
    confirm.add_argument("--key", required=True)
    confirm.add_argument("--state", choices=["confirmed", "rejected", "unconfirmed"], required=True)
    confirm.add_argument("--by", required=True)
    confirm.add_argument("--note")

    validate = commands.add_parser("validate", help="validate a file against one of the data contracts")
    validate.add_argument("kind", choices=list(contracts.KINDS))
    validate.add_argument("file", type=Path)

    check = commands.add_parser("check", help="run one check on files")
    check_sub = check.add_subparsers(dest="check_command", required=True)
    structure = check_sub.add_parser("structure")
    structure.add_argument("rig", type=Path)
    structure.add_argument("--mesh", type=Path, help="also verify vertex count and recorded topology against this mesh")
    structure.add_argument("--semantic-map", type=Path)
    structure.add_argument("--required", help="comma-separated bone roles, or 'humanoid'")
    structure.add_argument("--tolerance", type=float, default=1e-3)
    penetration = check_sub.add_parser("penetration")
    penetration.add_argument("mesh", type=Path)
    penetration.add_argument("collider", type=Path)
    continuity = check_sub.add_parser("continuity")
    continuity.add_argument("track", type=Path)
    continuity.add_argument("--max-jump", type=float, required=True)

    obj = commands.add_parser("mesh", help="convert between model3d-mesh JSON and OBJ, or summarize a mesh")
    obj_sub = obj.add_subparsers(dest="mesh_command", required=True)
    convert = obj_sub.add_parser("convert")
    convert.add_argument("src", type=Path)
    convert.add_argument("dst", type=Path)
    info = obj_sub.add_parser("info")
    info.add_argument("file", type=Path)
    compare = obj_sub.add_parser("compare", help="same geometry up to vertex/face enumeration order?")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.add_argument("--no-colors", action="store_true")

    gen = commands.add_parser("generate", help="initial model from a setting sheet: prepare the image or inspect a generated mesh")
    gen_sub = gen.add_subparsers(dest="generate_command", required=True)
    prep = gen_sub.add_parser("prepare", help="write the prepared RGB PNG and the preparation metadata")
    prep.add_argument("image", type=Path)
    prep.add_argument("out", type=Path)
    prep.add_argument("--foreground-ratio", type=float, default=0.85)
    inspect = gen_sub.add_parser("inspect", help="geometry and structure checks of an initial model (OBJ or JSON)")
    inspect.add_argument("mesh", type=Path)
    inspect.add_argument("--out", type=Path)

    hand = commands.add_parser("handoff", help="write a handoff record (run, recipe and artifact sha256) for Orpheus")
    hand.add_argument("run_dir", type=Path)
    hand.add_argument("artifact", help="<step id>/<output name> or the artifact file name")
    hand.add_argument("out", type=Path)
    hand.add_argument("--notes")

    rea = commands.add_parser("rea", help="turn REA analysis outputs into recipe evidence")
    rea_sub = rea.add_subparsers(dest="rea_command", required=True)
    report = rea_sub.add_parser("report", help="from an analysis_report.json")
    report.add_argument("file", type=Path)
    report.add_argument("out", type=Path)
    summary = rea_sub.add_parser("summary", help="from a rea-real-3d-tools-experiment/0.1 summary")
    summary.add_argument("file", type=Path)
    summary.add_argument("out", type=Path)
    pyc = rea_sub.add_parser("pyc", help="from a parsePythonPyc evidence-summary.json (REA 6.8)")
    pyc.add_argument("file", type=Path)
    pyc.add_argument("out", type=Path)

    blender = commands.add_parser("blender", help="run Blender in the background (unverified on this machine)")
    blender_sub = blender.add_subparsers(dest="blender_command", required=True)
    observe = blender_sub.add_parser("observe", help="write an Observation3D of a .blend scene")
    observe.add_argument("--blender", type=Path, required=True)
    observe.add_argument("--blend", type=Path, required=True)
    observe.add_argument("--out", type=Path, required=True)
    observe.add_argument("--objects", nargs="*")
    observe.add_argument("--units", default="m", choices=["m", "cm", "mm"])
    observe.add_argument("--timeout", type=float)
    script = blender_sub.add_parser("script", help="run a Python script inside Blender")
    script.add_argument("--blender", type=Path, required=True)
    script.add_argument("--script", type=Path, required=True)
    script.add_argument("--blend", type=Path)
    script.add_argument("--timeout", type=float)
    script.add_argument("args", nargs="*")

    commands.add_parser("ops", help="list the registered operations")
    return p


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def _pairs(values, label):
    result = {}
    for item in values:
        if "=" not in item:
            raise ToolError(f"{label} は NAME=PATH の形にしてください: {item!r}")
        name, value = item.split("=", 1)
        result[name] = value
    return result


def command(args):
    if args.command == "demo":
        from . import demo
        return demo.create(args.dir, args.clearance, args.resolution)
    if args.command == "dance-recipe":
        from . import dance_recipe
        return dance_recipe.build(args.dir, args.bundle, args.preset, args.face_config, args.python, args.blender,
                                  args.frames, args.profile, args.threads)
    if args.command == "run":
        from . import executor
        result = executor.run_recipe(args.recipe, args.out, args.force, args.preset, _pairs(args.tool, "--tool"),
                                     None if args.quiet else _progress)
        args.failed = 1 if result["status"] == "failed" else 0
        return result
    if args.command == "accept":
        from . import executor
        return executor.accept(args.run_dir, args.decision, args.by, args.note)
    if args.command == "edit":
        from . import executor
        return executor.note_edit(args.run_dir, args.by, args.what, args.minutes)
    if args.command == "manifest":
        from . import manifest
        inputs = []
        for item in args.input:
            if "=" not in item:
                raise ToolError(f"--input は NAME=PATH[:KIND] の形にしてください: {item!r}")
            name, rest = item.split("=", 1)
            path, kind = rest, None
            if ":" in rest:
                head, tail = rest.rsplit(":", 1)
                if tail in INPUT_KINDS:   # 末尾が種類のときだけ切る（Windows の C: やパスの中のコロンは残す）
                    path, kind = head, tail
            inputs.append((name, path, kind))
        data = manifest.build(args.asset_id, inputs, args.units, {"up": args.up, "forward": args.forward}, args.asset_version,
                              args.part or None, notes=args.notes)
        atomic_json(args.out, data)
        return {"out": str(args.out.resolve()), "inputs": len(data["inputs"])}
    if args.command == "semantic":
        from . import semantic
        if args.semantic_command == "build":
            preset = semantic.load_preset(args.preset)
            parts = [p for p in args.parts.split(",") if p]
            bones = []
            if args.rig:
                rig = contracts.load("rig", args.rig)
                bones = [{"name": b["name"], "parent": b["parent"]} for b in rig["bones"]]
            for item in [b for b in args.bones.split(",") if b]:
                name, _, parent = item.partition(":")
                bones.append({"name": name, "parent": parent or None})
            data = semantic.build(args.asset_id, preset, parts, bones, preset_path=args.preset.resolve())
            atomic_json(args.out, data)
            return {"out": str(args.out.resolve()), "parts": len(data["parts"]), "bones": len(data["bones"]),
                    "unmapped": data["unmapped"], "required_missing": data["required_missing"]}
        data = contracts.load("semantic-map", args.semantic_map)
        semantic.confirm(data, args.kind, args.key, args.state, args.by, args.note)
        contracts.validate("semantic-map", data)
        atomic_json(args.semantic_map, data)
        return {"out": str(args.semantic_map.resolve()), "kind": args.kind, "key": args.key, "state": args.state}
    if args.command == "validate":
        contracts.load(args.kind, args.file)
        return {"valid": True, "kind": args.kind, "file": str(args.file)}
    if args.command == "check":
        from . import checks, semantic
        from .mesh import Mesh
        if args.check_command == "structure":
            rig = contracts.load("rig", args.rig)
            semantic_map = contracts.load("semantic-map", args.semantic_map) if args.semantic_map else None
            required = None
            if args.required == "humanoid":
                required = list(semantic.HUMANOID_REQUIRED_BONES)
            elif args.required:
                required = [r for r in args.required.split(",") if r]
            report = checks.structure(rig, semantic_map, required, args.tolerance,
                                      mesh=Mesh.load(args.mesh) if args.mesh else None)
        elif args.check_command == "penetration":
            report = checks.penetration(Mesh.load(args.mesh), Mesh.load(args.collider))
        else:
            report = checks.continuity(read_json(args.track), args.max_jump)
        args.failed = 0 if report.get("pass") else 1
        return report
    if args.command == "mesh":
        from .mesh import Mesh, compare_geometry
        if args.mesh_command == "compare":
            return compare_geometry(Mesh.load(args.left), Mesh.load(args.right), colors=not args.no_colors)
        mesh = Mesh.load(args.src if args.mesh_command == "convert" else args.file)
        if args.mesh_command == "convert":
            mesh.save(args.dst)
            return {"out": str(args.dst.resolve()), **mesh.summary()}
        return {**mesh.summary(), "units": mesh.units, "axes": mesh.axes, "parts": {k: len(v) for k, v in mesh.parts.items()},
                "edges": mesh.edge_report(), "area": mesh.area(), "signed_volume": mesh.signed_volume()}
    if args.command == "generate":
        from . import generate
        from .mesh import Mesh
        if args.generate_command == "prepare":
            return generate.prepare_file(args.image, args.out, args.foreground_ratio)
        report = {"check": "initial_model", **generate.inspect_mesh(Mesh.load(args.mesh))}
        if args.out:
            atomic_json(args.out, report)
        return report
    if args.command == "handoff":
        from . import handoff
        return handoff.write(args.run_dir, args.artifact, args.out, args.notes)
    if args.command == "rea":
        from .rea_bridge import evidence
        reader = {"report": evidence.from_analysis_report, "summary": evidence.from_summary, "pyc": evidence.from_pyc_evidence}[args.rea_command]
        data = reader(args.file)
        atomic_json(args.out, data)
        count = len(data.get("evidence", [])) or sum(len(t["evidence"]) for t in data.get("tools", {}).values())
        return {"out": str(args.out.resolve()), "evidence": count, "limitations": len(data.get("limitations", []))}
    if args.command == "blender":
        from .blender_adapter import launcher
        if args.blender_command == "observe":
            extra = ["--out", str(args.out.resolve()), "--units", args.units]
            if args.objects:
                extra += ["--objects", *args.objects]
            record = launcher.run_blender(args.blender.resolve(), launcher.OBSERVE_SCRIPT, args.blend.resolve(), extra,
                                          log=args.out.resolve().with_suffix(".log"), timeout=args.timeout)
            contracts.load("observation", args.out)
            return {"out": str(args.out.resolve()), **record}
        return launcher.run_blender(args.blender.resolve(), args.script.resolve(), args.blend.resolve() if args.blend else None,
                                    args.args, timeout=args.timeout)
    if args.command == "ops":
        from . import ops
        return ops.describe()
    raise ToolError(f"知らないコマンドです: {args.command}")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = command(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ToolError, OSError, ValueError, KeyError, TypeError, IndexError) as error:   # 形の違う入力は Error: で止める
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return getattr(args, "failed", 0)


if __name__ == "__main__":
    raise SystemExit(main())
