"""mv dance（実験的なダンス合成。docs/dance.md）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError
from . import _progress, _require_fx


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
    demo.add_argument("--schema", choices=["0.1", "0.2", "0.3"], default="0.3", help="spec version of the demo")
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
    passes = sub.add_parser("passes", help="composite element passes from model3d render.passes into dance.mp4 as a run")
    passes.add_argument("spec", type=Path)
    passes.add_argument("--out", type=Path, help="default: <spec dir>/dance_runs")
    passes.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    passes.add_argument("--quiet", action="store_true", help="do not report progress on stderr")
    finish = sub.add_parser("finish", help="finish rendered frames (a model3d frames list) with the flash, fade and look into dance.mp4 as a run")
    finish.add_argument("spec", type=Path)
    finish.add_argument("--out", type=Path, help="default: <spec dir>/dance_runs")
    finish.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    finish.add_argument("--quiet", action="store_true", help="do not report progress on stderr")
    accept = sub.add_parser("accept", help="append a human decision to acceptance.jsonl")
    accept.add_argument("run_dir", type=Path)
    accept.add_argument("--decision", choices=["accepted", "rejected", "hold"], required=True)
    accept.add_argument("--by", required=True)
    accept.add_argument("--note")


def dance_command(args):
    """dance サブコマンドを実行して結果の辞書を返す。import はここで遅延させる。"""
    command = args.dance_command
    if command == "accept":
        from ..dance import VARIANT
        from ..fx import runs as fxruns
        return fxruns.accept(args.run_dir, VARIANT, args.decision, args.by, args.note)
    if command == "plan":
        from ..dance import plan
        return plan.text(args.spec)
    _require_fx()
    if command == "demo":
        from ..dance import demo
        return demo.create(args.dir, args.width, args.height, args.duration, args.fps, args.schema)
    if command == "passes":
        from ..dance import passes
        return passes.render_run(args.spec, args.out, args.force, _progress, args.quiet)
    if command == "finish":
        from ..dance import finish_frames
        return finish_frames.render_run(args.spec, args.out, args.force, _progress, args.quiet)
    from ..dance import runs
    if command == "render":
        return runs.render_run(args.spec, args.start, args.duration, args.out, args.scale, args.force, _progress, args.quiet, args.hooks)
    if command == "stills":
        return runs.stills(args.spec, args.times, args.out, args.scale, args.guides, layers=args.layers, hooks=args.hooks)
    raise PipelineError(f"Unsupported dance command: {command}")
