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
    _fx_parser(commands)
    _dance_parser(commands)
    _gate_parser(commands)
    return p


def _fx_parser(commands):
    """実験的な演出エンジン fx（docs/fx.md）。`project` 引数を持たず、project_lock も通らない。"""
    fx = commands.add_parser("fx", help="experimental effects engine: camera, layers, local light")
    sub = fx.add_subparsers(dest="fx_command", required=True)
    demo = sub.add_parser("demo", help="write synthetic demo assets and a spec")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--width", type=int, default=960)
    demo.add_argument("--height", type=int, default=540)
    demo.add_argument("--duration", type=float, default=10.0)
    demo.add_argument("--fps", type=int, default=24)
    demo.add_argument("--look", action="store_true", help="add a night look (grade, vignette, grain) to the demo spec")
    render = sub.add_parser("render", help="render variants (base, layers, light, both) and a 2x2 grid")
    render.add_argument("spec", type=Path)
    render.add_argument("--variants", help="comma-separated: base,layers,light,both (default: all)")
    render.add_argument("--start", type=float, default=0.0)
    render.add_argument("--duration", type=float)
    render.add_argument("--out", type=Path, help="default: <spec dir>/fx_runs")
    render.add_argument("--scale", type=float, default=1.0)
    render.add_argument("--no-grid", action="store_true")
    render.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    render.add_argument("--no-look", action="store_true", help="ignore spec.look (render without the look stage)")
    stills = sub.add_parser("stills", help="still sheet: rows = times, columns = variants")
    stills.add_argument("spec", type=Path)
    stills.add_argument("--times", type=float, nargs="+", required=True)
    stills.add_argument("--variants")
    stills.add_argument("--out", type=Path)
    stills.add_argument("--scale", type=float, default=1.0)
    stills.add_argument("--no-look", action="store_true", help="ignore spec.look (render without the look stage)")
    legacy = sub.add_parser("legacy", help="run an external production renderer and record it as a run")
    legacy.add_argument("--renderer", type=Path, required=True)
    legacy.add_argument("--config", type=Path, required=True)
    legacy.add_argument("--start", type=float, default=0.0)
    legacy.add_argument("--duration", type=float, required=True)
    legacy.add_argument("--python", help="interpreter for the renderer (default: this one)")
    legacy.add_argument("--width", type=int)
    legacy.add_argument("--height", type=int)
    legacy.add_argument("--fps", type=float)
    legacy.add_argument("--out", type=Path, help="default: <config dir>/fx_runs")
    legacy.add_argument("--timeout", type=float, help="seconds (default: max(600, 120 x duration))")
    legacy.add_argument("--cwd", type=Path, help="working directory for the renderer (default: its own directory)")
    accept = sub.add_parser("accept", help="append a human decision to acceptance.jsonl")
    accept.add_argument("run_dir", type=Path)
    accept.add_argument("--variant", required=True)
    accept.add_argument("--decision", choices=["accepted", "rejected", "hold"], required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--note")


def _dance_parser(commands):
    """実験的なダンス合成（docs/dance.md）。fx と同じく `project` 引数を持たず、project_lock も通らない。"""
    dance = commands.add_parser("dance", help="experimental dance compositor: keyed performer on a stage with vertical lyrics")
    sub = dance.add_subparsers(dest="dance_command", required=True)
    demo = sub.add_parser("demo", help="write synthetic demo assets and a spec")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--width", type=int, default=540)
    demo.add_argument("--height", type=int, default=960)
    demo.add_argument("--duration", type=float, default=6.0)
    demo.add_argument("--fps", type=int, default=24)
    demo.add_argument("--schema", choices=["0.1", "0.2"], default="0.2", help="spec version of the demo")
    render = sub.add_parser("render", help="render dance.mp4 and timeline.json as a run")
    render.add_argument("spec", type=Path)
    render.add_argument("--start", type=float, default=0.0)
    render.add_argument("--duration", type=float)
    render.add_argument("--out", type=Path, help="default: <spec dir>/dance_runs")
    render.add_argument("--scale", type=float, default=1.0)
    render.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    render.add_argument("--quiet", action="store_true", help="do not report progress on stderr")
    render.add_argument("--hooks", type=Path, help="finishing hooks (Python). Runs only when given here")
    plan = sub.add_parser("plan", help="print the resolved times as tables")
    plan.add_argument("spec", type=Path)
    stills = sub.add_parser("stills", help="still sheet of the given times")
    stills.add_argument("spec", type=Path)
    stills.add_argument("--times", type=float, nargs="+", required=True)
    stills.add_argument("--out", type=Path)
    stills.add_argument("--scale", type=float, default=1.0)
    stills.add_argument("--guides", nargs="?", const="visible", choices=["visible", "all"],
                        help="draw the UI regions and the boxes of the visible lines (all: every line)")
    stills.add_argument("--layers", action="store_true", help="one row per time: final, stage, performer alpha, behind text, front text")
    stills.add_argument("--hooks", type=Path, help="finishing hooks (Python). Runs only when given here")
    accept = sub.add_parser("accept", help="append a human decision to acceptance.jsonl")
    accept.add_argument("run_dir", type=Path)
    accept.add_argument("--decision", choices=["accepted", "rejected", "hold"], required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--note")


def _gate_parser(commands):
    """書き出し前の機械的な検査と行・秒の確認表（docs/gate.md）。`project` 引数を持たず、project_lock も通らない。"""
    gate = commands.add_parser("gate", help="pre-export mechanical checks and the lyric line table")
    sub = gate.add_subparsers(dest="gate_command", required=True)
    check = sub.add_parser("check", help="run the checks; exit code 1 when any check fails")
    check.add_argument("config", type=Path)
    check.add_argument("--out", type=Path, help="default: <config dir>/gate_report")
    lines = sub.add_parser("lines", help="write the line/second table and lines_measured.json")
    lines.add_argument("lines", type=Path, help=".lrc or .json")
    lines.add_argument("out", type=Path)
    lines.add_argument("--vocals", type=Path, help="vocal stem; measures each line start near the estimate")
    lines.add_argument("--window", type=float, default=0.8)
    lines.add_argument("--offset", type=float, default=0.0, help="seconds added to every estimate")


def _require_fx():
    try:
        import cv2  # noqa: F401
    except ModuleNotFoundError as error:
        if error.name != "cv2":
            raise
        raise PipelineError("fx には opencv-python-headless が必要です。`uv sync --locked --extra fx` を実行してください") from error


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def fx_command(args):
    """fx サブコマンドを実行して結果の辞書を返す。fx の import はここで遅延させる（既存コマンドは cv2 なしで動く）。"""
    command = args.fx_command
    if command == "accept":
        from .fx import runs
        return runs.accept(args.run_dir, args.variant, args.decision, args.by, args.note)
    if command == "legacy":
        from .fx import legacy
        return legacy.run_legacy(args.renderer, args.config, args.start, args.duration, args.python, args.width,
                                 args.height, args.fps, args.out, args.timeout, args.cwd, _progress)
    _require_fx()
    from .fx import demo, runs
    if command == "demo":
        return demo.create(args.dir, args.width, args.height, args.duration, args.fps, look=args.look)
    if command == "render":
        return runs.render_run(args.spec, args.variants, args.start, args.duration, args.out, args.scale,
                               not args.no_grid, args.force, _progress, no_look=args.no_look)
    if command == "stills":
        return runs.stills(args.spec, args.times, args.variants, args.out, args.scale, no_look=args.no_look)
    raise PipelineError(f"Unsupported fx command: {command}")


def dance_command(args):
    """dance サブコマンドを実行して結果の辞書を返す。import はここで遅延させる。"""
    command = args.dance_command
    if command == "accept":
        from .dance import VARIANT
        from .fx import runs as fxruns
        return fxruns.accept(args.run_dir, VARIANT, args.decision, args.by, args.note)
    if command == "plan":
        from .dance import plan
        return plan.text(args.spec)
    _require_fx()
    if command == "demo":
        from .dance import demo
        return demo.create(args.dir, args.width, args.height, args.duration, args.fps, args.schema)
    from .dance import runs
    if command == "render":
        return runs.render_run(args.spec, args.start, args.duration, args.out, args.scale, args.force, _progress, args.quiet, args.hooks)
    if command == "stills":
        return runs.stills(args.spec, args.times, args.out, args.scale, args.guides, layers=args.layers, hooks=args.hooks)
    raise PipelineError(f"Unsupported dance command: {command}")


def gate_command(args):
    """gate サブコマンドを実行して結果の辞書を返す。"""
    if args.gate_command == "lines":
        from .gate import lines
        return lines.make_table(args.lines, args.out, args.vocals, args.window, args.offset)
    from .gate import checks
    report, out = checks.run(args.config, args.out)
    args.failed = 1 if report["overall"] == "fail" else 0
    return {"overall": report["overall"], "out": str(out),
            "checks": [{k: c[k] for k in ("check", "status", "summary")} for c in report["checks"]]}


def _run_gate(args):
    code = _run_json(gate_command, args)
    return code if code else getattr(args, "failed", 0)


def _run_json(function, args):
    try:
        result = function(args)
        print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, OSError, ValueError, KeyError, ImportError, wave.Error) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


def main(argv=None):
    args = parser().parse_args(argv)
    if args.command == "fx":
        return _run_json(fx_command, args)
    if args.command == "dance":
        return _run_json(dance_command, args)
    if args.command == "gate":
        return _run_gate(args)
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
