import argparse
import json
import sys
import wave
from pathlib import Path

from . import pipeline, validate
from .io import PipelineError, atomic_json, read_json, safe_path
from .runner import project_lock


def parser():
    p = argparse.ArgumentParser(prog="mv", description="Orpheus: local reproducible music videos")
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project", type=Path)
    init.add_argument("--audio", type=Path, required=True)
    init.add_argument("--lyrics", type=Path, required=True)
    init.add_argument("--start", type=float, default=0)
    init.add_argument("--duration", type=float)
    init.add_argument("--fps", type=int, default=30)
    init.add_argument("--width", type=int, default=1920)
    init.add_argument("--height", type=int, default=1080)
    for name in ["analyze", "export-brief", "verify", "resume", "status", "confirm-assets"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
    for name in ["import-storyboard", "import-assets", "import-alignment", "apply-edits", "update-lyrics"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        sub.add_argument("file", type=Path)
    for name in ["render", "preview", "validate"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        sub.add_argument("--profile", choices=["final", "preview"], default="preview" if name == "preview" else "final")
        if name == "preview":
            sub.add_argument("--studio", action="store_true", help="Open the same composition in Remotion Studio")
    demo = commands.add_parser("demo")
    demo.add_argument("project", type=Path)
    demo.add_argument("--width", type=int, default=1920)
    demo.add_argument("--height", type=int, default=1080)
    adapter = commands.add_parser("run-adapter")
    adapter.add_argument("project", type=Path)
    adapter.add_argument("config", type=Path)
    adapter.add_argument("--kind", choices=["alignment", "separation"], required=True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    root = args.project.resolve()
    try:
        with project_lock(root):
            command = args.command
            if command == "init":
                result = pipeline.initialize(root, args.audio, args.lyrics, args.start, args.duration, args.fps, args.width, args.height)
            elif command == "demo":
                from .demo import create
                result = create(root, args.width, args.height)
            elif command == "analyze":
                result = pipeline.analyze(root)
            elif command == "export-brief":
                result = pipeline.export_brief(root)
            elif command.startswith("import-") or command in {"apply-edits", "update-lyrics"}:
                function = {"import-storyboard": pipeline.import_storyboard, "import-assets": pipeline.import_assets,
                            "import-alignment": pipeline.import_alignment, "apply-edits": pipeline.import_edits,
                            "update-lyrics": pipeline.update_lyrics}[command]
                result = function(root, args.file)
            elif command == "render" or command == "preview":
                result = pipeline.render(root, args.profile, getattr(args, "studio", False))
            elif command == "validate":
                *_, warnings = validate.bundle(root, args.profile)
                result = {"valid": True, "warnings": warnings}
            elif command == "verify":
                result = pipeline.verify(root)
            elif command == "resume":
                result = pipeline.resume(root)
            elif command == "status":
                result = read_json(root / "job.json")
            elif command == "confirm-assets":
                result = {"changed": [], "review_required": []}
                atomic_json(root / "work/asset-review.json", result)
            elif command == "run-adapter":
                from .adapters.external import invoke
                from .io import sha256
                from .runner import Runner, cache_key
                config = read_json(args.config)
                a = read_json(root / "analysis.json")
                validate.analysis(a, root)
                adapter_audio = safe_path(root, config.get("input_audio", a["audio"]["path"]))
                validate.require(adapter_audio.is_file(), "Adapter audio is missing")
                if config.get("input_audio"):
                    validate.require(sha256(adapter_audio) == config.get("input_sha256"), "Adapter input audio hash mismatch")
                output = root / "work" / ("alignment.adapter.json" if args.kind == "alignment" else "vocals.wav")
                def action():
                    temporary = output.with_name("pending-" + output.name)
                    # A stale temporary file must never pass as a new adapter result.
                    temporary.unlink(missing_ok=True)
                    invoke(config, audio=adapter_audio, lyrics=root / "inputs/lyrics.txt", output=temporary,
                           log=root / "work/adapter.log", analysis=root / "work/analysis.raw.json")
                    if args.kind == "alignment":
                        from .normalize import alignment
                        validate.analysis(alignment(read_json(root / "work/analysis.raw.json"), read_json(temporary)), root)
                    else:
                        from .media import probe
                        validate.require(any(s["codec_type"] == "audio" for s in probe(temporary)["streams"]), "Separator produced no audio")
                        validate.require(isinstance(config.get("offset_s"), (int, float)), "Separator must declare offset_s")
                    temporary.replace(output)
                    atomic_json(root / "work" / f"{args.kind}.provenance.json", {"audio_sha256": a["audio"]["sha256"], "config": config})
                    return [output.relative_to(root).as_posix(), f"work/{args.kind}.provenance.json"]
                Runner(root).execute(args.kind, cache_key(args.kind, [a["audio"]["sha256"], sha256(adapter_audio), sha256(root / "inputs/lyrics.txt")], config), action)
                if args.kind == "alignment":
                    pipeline.import_alignment(root, output)
                result = output
            else:
                raise PipelineError(f"Unsupported command: {command}")
        print(json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, (dict, list)) else str(result or "OK"))
        return 0
    except (PipelineError, OSError, ValueError, KeyError, ImportError, wave.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
