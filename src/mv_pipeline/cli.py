import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

from . import pipeline, validate
from .io import PipelineError, atomic_json, read_json
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
    _inspect_parser(commands)
    _workflow_parser(commands)
    _export_parser(commands)
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
    legacy.add_argument("--units", choices=["seconds", "frames"], default="seconds",
                        help="how seg START END is passed: seconds (default) or frame numbers with END excluded (needs --fps)")
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
    demo = sub.add_parser("demo", help="write synthetic test material with known answers (vocals stand-in, lyrics, timeline, beats, expected.json)")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--duration", type=float, default=10.0)
    demo.add_argument("--fps", type=int, default=30)
    demo.add_argument("--bpm", type=float, default=120.0)
    demo.add_argument("--beats-per-bar", type=int, default=4)
    demo.add_argument("--no-downbeats", action="store_true", help="omit the downbeat grid")
    demo.add_argument("--shifts", help="comma-separated seconds added to each declared line time (default 0,-0.333,0,0.15)")
    demo.add_argument("--flashes", help="comma-separated flash times in seconds (default 5.5,6,6.5,7,7.5)")
    demo.add_argument("--flash-align", choices=["beat", "downbeat"], default="beat", help="what the flashes should be compared with")
    demo.add_argument("--attack-ms", type=float, default=30.0, help="rise time of each line's tone")
    demo.add_argument("--floor-db", type=float, default=-40.0, help="background noise relative to the tone")
    tl = sub.add_parser("timeline", help="measure a transparent PNG sequence (alpha) into a dance-timeline/0.1 for gate check")
    tl.add_argument("--frames", action="append", default=[], required=True,
                    help="PNG sequence folder; 'DIR:lyric-001' binds the folder to one line. Repeatable")
    tl.add_argument("--lyrics", type=Path, required=True, help="props.json from mv export remotion-props (remotion-props/0.1)")
    tl.add_argument("--layer", required=True, help="layer name written into the timeline (e.g. band)")
    tl.add_argument("--hints", type=Path, help="JSON {line id: normalized box} when one sequence holds several lines")
    tl.add_argument("--alpha-threshold", type=int, default=64, help="a pixel is visible when alpha is above this (0-254)")
    tl.add_argument("--flashes", help="comma-separated flash times in seconds to write into the timeline (props have none)")
    tl.add_argument("--out", type=Path, required=True, help="timeline.json to write (plus <stem>_measured.json)")


def _inspect_parser(commands):
    """生成動画の検品（docs/inspect.md）。`project` 引数を持たず、project_lock も通らない。合否は出さない。"""
    inspect = commands.add_parser("inspect", help="inspect generated video: locate, classify and link anomalies to fixes (no pass/fail)")
    sub = inspect.add_subparsers(dest="inspect_command", required=True)
    check = sub.add_parser("check", help="measure every frame and write the report, stills and fix plan")
    check.add_argument("config", type=Path, help="inspect.json (inspect/0.1)")
    check.add_argument("--out", type=Path, help="default: <config dir>/inspect_report")
    check.add_argument("--vlm", type=Path, help="VLM adapter config (tool, version, argv, sends_data). Runs only when given here")
    check.add_argument("--allow-send", action="store_true", help="allow a VLM adapter with sends_data: true to send frames and sheets")
    check.add_argument("--vlm-packet", action="store_true", help="write the VLM request packet only (nothing is sent)")
    check.add_argument("--vlm-response", type=Path, help="import a VLM response written for this run's request packet")
    check.add_argument("--vlm-legacy-response", action="store_true",
                       help="accept a VLM response without request_sha256 (old format; not bound to this request). Recorded in the report")
    check.add_argument("--quiet", action="store_true", help="do not report progress on stderr")
    demo = sub.add_parser("demo", help="write a synthetic clip with known anomalies, a design sheet and inspect.json")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--width", type=int, default=216)
    demo.add_argument("--height", type=int, default=384)
    demo.add_argument("--fps", type=int, default=24)
    demo.add_argument("--duration", type=float, default=4.0)


def _workflow_parser(commands):
    """用途別の工程案内と完了確認。既存の解析・描画を自動実行しない。"""
    workflow = commands.add_parser("workflow", help="purpose-based production plans and completion checks")
    sub = workflow.add_subparsers(dest="workflow_command", required=True)
    plan = sub.add_parser("plan", help="save the required inputs, commands and handoffs for a production")
    plan.add_argument("target", type=Path, help="MV project directory or dance spec JSON")
    plan.add_argument("--purpose", choices=["lyric-mv", "dance-edit"], required=True)
    plan.add_argument("--out", type=Path, help="workflow JSON path (default: beside the target)")
    plan.add_argument("--lyrics", action=argparse.BooleanOptionalAction, default=None,
                      help="declare sung lyrics for a dance edit (otherwise inferred from an existing spec)")
    plan.add_argument("--inspection", choices=["optional", "required"],
                      help="add the mv inspect stage (required: the plan is incomplete without a matching inspect report)")
    check = sub.add_parser("check", help="check current evidence; exit 1 for missing or failed requirements")
    check.add_argument("plan", type=Path)
    check.add_argument("--run", type=Path, help="render run directory (required for dance edits)")
    check.add_argument("--gate-config", type=Path, help="gate config for this dance run; checks are rerun")
    check.add_argument("--out", type=Path, help="write the completion report JSON and Markdown here")
    check.add_argument("--inspect-report", type=Path, action="append", default=[],
                       help="mv inspect report (inspect_report.json) to record; repeatable. Matched to the final video, spec or assets by SHA-256")
    omit = sub.add_parser("omit", help="record an intentional omission; never turns a failed check into a pass")
    omit.add_argument("plan", type=Path)
    omit.add_argument("stage", choices=["beats", "lyrics"])
    omit.add_argument("--reason", required=True)
    restore = sub.add_parser("restore", help="remove an omission and require the stage again")
    restore.add_argument("plan", type=Path)
    restore.add_argument("stage", choices=["beats", "lyrics"])


# ---- 実行 ------------------------------------------------------------------
#
# どのコマンドも「引数 → 結果（dict / list / str / Path / None）」の関数で、_run が stdout への出力と
# 例外の 1 行化を引き受ける。検査の不合格など「結果は出すが終了コードは 1」は args.failed で伝える。

CLI_ERRORS = (PipelineError, OSError, ValueError, KeyError, ImportError, wave.Error, subprocess.CalledProcessError)


def _progress(message):
    print(message, file=sys.stderr, flush=True)


def _require_fx():
    try:
        import cv2  # noqa: F401
    except ModuleNotFoundError as error:
        if error.name != "cv2":
            raise
        raise PipelineError("fx には opencv-python-headless が必要です。`uv sync --locked --extra fx` を実行してください") from error


def _print_result(result):
    """str はそのまま、dict / list / None は JSON、それ以外（Path など）は str で 1 つ出す。"""
    if isinstance(result, str):
        print(result)
    elif result is None or isinstance(result, (dict, list)):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(str(result))


def _run(function, args):
    """function(args) の結果を stdout に出して終了コードを返す。例外（出力の途中の OSError も）は stderr に 1 行で出して 1。"""
    try:
        _print_result(function(args))
    except CLI_ERRORS as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return getattr(args, "failed", 0)


# ---- 実験的なサブコマンド群（project_lock を通らない） -------------------------

def fx_command(args):
    """fx サブコマンドを実行して結果の辞書を返す。fx の import はここで遅延させる（既存コマンドは cv2 なしで動く）。"""
    command = args.fx_command
    if command == "accept":
        from .fx import runs
        return runs.accept(args.run_dir, args.variant, args.decision, args.by, args.note)
    if command == "legacy":
        from .fx import legacy
        return legacy.run_legacy(args.renderer, args.config, args.start, args.duration, args.python, args.width,
                                 args.height, args.fps, args.out, args.timeout, args.cwd, _progress, args.units)
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
    """gate サブコマンドを実行して結果の辞書を返す。check の不合格は args.failed=1。"""
    if args.gate_command == "lines":
        from .gate import lines
        return lines.make_table(args.lines, args.out, args.vocals, args.window, args.offset)
    if args.gate_command == "demo":
        from .gate import demo
        return demo.create(args.dir, args.duration, args.fps, args.bpm, args.beats_per_bar, not args.no_downbeats, args.shifts,
                           args.flashes, args.flash_align, args.attack_ms, args.floor_db)
    if args.gate_command == "timeline":
        from .gate import frames_timeline
        flashes = [v for v in (args.flashes or "").split(",") if v.strip()]
        try:
            flashes = [float(v) for v in flashes]
        except ValueError as error:
            raise PipelineError(f"--flashes は秒をコンマで区切って指定してください: {args.flashes!r}") from error
        return frames_timeline.write(args.frames, args.lyrics, args.layer, args.out, args.hints, args.alpha_threshold, flashes)
    from .gate import checks
    report, out = checks.run(args.config, args.out)
    args.failed = 1 if report["overall"] == "fail" else 0
    return {"overall": report["overall"], "out": str(out),
            "checks": [{k: c[k] for k in ("check", "status", "summary")} for c in report["checks"]]}


def inspect_command(args):
    """inspect サブコマンド。cv2 を使うので fx extra が要る。"""
    _require_fx()
    if args.inspect_command == "demo":
        from .inspection import demo
        return demo.create(args.dir, args.width, args.height, args.fps, args.duration)
    from .inspection import run
    result, out = run.run(args.config, args.out, args.vlm, args.allow_send, args.vlm_packet, args.vlm_response,
                          None if args.quiet else _progress, vlm_legacy=args.vlm_legacy_response)
    return run.summarize(result, out)


def _export_parser(commands):
    """外のレンダラへ渡すファイルの書き出し。読むだけなので project_lock を通らない。"""
    export = commands.add_parser("export", help="write files for external renderers (read-only)")
    sub = export.add_subparsers(dest="export_command", required=True)
    props = sub.add_parser("remotion-props", help="frame-based props.json (remotion-props/0.1) from analysis.json and storyboard.json")
    props.add_argument("project", type=Path)
    props.add_argument("--out", type=Path, required=True, help="props.json to write")
    props.add_argument("--beats", type=Path, help='beat grid JSON with "beats" and "downbeats" (gate / dance format)')
    props.add_argument("--beats-per-bar", type=int, help="derive downbeats from analysis beats_s every N beats")
    props.add_argument("--downbeat-phase", type=int, default=0, help="index of the first downbeat within beats_s (with --beats-per-bar)")
    props.add_argument("--no-storyboard", action="store_true", help="do not include storyboard scenes even if storyboard.json exists")


def export_command(args):
    """export サブコマンド。プロジェクトのファイルを読むだけで、書くのは --out だけ。"""
    from . import remotion_props
    if args.export_command == "remotion-props":
        return remotion_props.export(args.project.resolve(), args.out, args.beats, args.beats_per_bar, args.downbeat_phase,
                                     storyboard=not args.no_storyboard)
    raise PipelineError(f"Unsupported export command: {args.export_command}")


def workflow_command(args):
    """workflow サブコマンド。check で必須工程が未実施・未確認なら args.failed=1。"""
    from . import workflow

    if args.workflow_command == "plan":
        return workflow.create_plan(args.target, args.purpose, args.out, args.lyrics, args.inspection)
    if args.workflow_command == "omit":
        return workflow.omit(args.plan, args.stage, args.reason)
    if args.workflow_command == "restore":
        return workflow.restore(args.plan, args.stage)
    report = workflow.check(args.plan, args.run, args.gate_config, args.out, args.inspect_report)
    args.failed = 0 if report["overall"] in {"complete", "complete_with_omissions"} else 1
    return report


GROUP_COMMANDS = {"fx": fx_command, "dance": dance_command, "gate": gate_command,
                  "inspect": inspect_command, "workflow": workflow_command, "export": export_command}


# ---- MV プロジェクトのコマンド（project_lock の中で動く） -----------------------

def _demo(root, args):
    from .demo import create
    return create(root, args.width, args.height)


def _validate(root, args):
    *_, warnings = validate.bundle(root, args.profile)
    return {"valid": True, "warnings": warnings}


def _confirm_assets(root, args):
    result = {"changed": [], "review_required": []}
    atomic_json(root / "work/asset-review.json", result)
    return result


PROJECT_COMMANDS = {
    "init": lambda root, a: pipeline.initialize(root, a.audio, a.lyrics, a.start, a.duration, a.fps, a.width, a.height),
    "demo": _demo,
    "analyze": lambda root, a: pipeline.analyze(root),
    "export-brief": lambda root, a: pipeline.export_brief(root),
    "import-storyboard": lambda root, a: pipeline.import_storyboard(root, a.file),
    "import-assets": lambda root, a: pipeline.import_assets(root, a.file),
    "import-alignment": lambda root, a: pipeline.import_alignment(root, a.file),
    "apply-edits": lambda root, a: pipeline.import_edits(root, a.file),
    "update-lyrics": lambda root, a: pipeline.update_lyrics(root, a.file),
    "render": lambda root, a: pipeline.render(root, a.profile),
    "preview": lambda root, a: pipeline.render(root, a.profile, a.studio),
    "validate": _validate,
    "verify": lambda root, a: pipeline.verify(root),
    "resume": lambda root, a: pipeline.resume(root),
    "status": lambda root, a: read_json(root / "job.json"),
    "confirm-assets": _confirm_assets,
    "run-adapter": lambda root, a: pipeline.run_adapter(root, a.config, a.kind),
}


def project_command(args):
    """`mv <command> PROJECT` を project_lock の中で実行し、結果を返す。結果の無いコマンドは "OK"。"""
    function = PROJECT_COMMANDS.get(args.command)
    if function is None:
        raise PipelineError(f"Unsupported command: {args.command}")
    root = args.project.resolve()
    with project_lock(root):
        result = function(root, args)
    return "OK" if result is None else result


def main(argv=None):
    args = parser().parse_args(argv)
    return _run(GROUP_COMMANDS.get(args.command, project_command), args)


if __name__ == "__main__":
    raise SystemExit(main())
