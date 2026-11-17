"""mv fx（実験的な演出エンジン。docs/fx.md）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError
from . import _progress, _require_fx


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
    render.add_argument("--no-overlays", action="store_true", help="ignore spec.overlays (render without lyrics and cards)")
    stills = sub.add_parser("stills", help="still sheet: rows = times, columns = variants")
    stills.add_argument("spec", type=Path)
    stills.add_argument("--times", type=float, nargs="+", required=True)
    stills.add_argument("--variants")
    stills.add_argument("--out", type=Path)
    stills.add_argument("--scale", type=float, default=1.0)
    stills.add_argument("--no-look", action="store_true", help="ignore spec.look (render without the look stage)")
    stills.add_argument("--no-overlays", action="store_true", help="ignore spec.overlays (render without lyrics and cards)")
    stills.add_argument("--guides", nargs="?", const="grid,lights",
                        help="draw guides on each cell: comma-separated grid,lights,masks (default when given: grid,lights)")
    stills.add_argument("--grid-step", type=float, help="grid spacing in source-normalized units (default: from the visible span)")
    stills.add_argument("--reference", type=Path, help="video whose nearest frames become the first column")
    stills.add_argument("--reference-offset", type=float, default=0.0, help="time in the reference video at shot time 0 (seconds)")
    stills.add_argument("--reference-label", default="reference", help="ASCII label of the reference column")
    guides = sub.add_parser("guides", help="draw a coordinate grid on a source image (to read positions for a spec)")
    guides.add_argument("image", type=Path)
    guides.add_argument("--out", type=Path, required=True, help="image to write; the extension picks the format (.png, or .jpg/.jpeg at quality 90)")
    guides.add_argument("--crop", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"), help="region to enlarge (in --units)")
    guides.add_argument("--units", choices=["norm", "px"], default="norm", help="norm: 0..1 of the image (default), px: image pixels")
    guides.add_argument("--step", type=float, help="grid spacing in --units (default: 0.05/0.02/0.01 from the crop, or 100 px)")
    guides.add_argument("--width", type=int, default=1600, help="output width in pixels (default 1600)")
    compare = sub.add_parser("compare", help="one video: a reference next to the variants of a finished render run")
    compare.add_argument("run_dir", type=Path)
    compare.add_argument("--reference", type=Path, required=True, help="reference video (same fps as the run)")
    compare.add_argument("--reference-offset", type=float, default=0.0, help="time in the reference video at shot time 0 (seconds)")
    compare.add_argument("--reference-label", default="reference", help="ASCII label of the reference tile")
    compare.add_argument("--out", type=Path, help="default: <run parent>/compare-<run_id>-<key8>.mp4")
    masks = sub.add_parser("masks", help="make masks from a plan (external alpha, polygons, GrabCut, islands, cuts)")
    masks.add_argument("plan", type=Path, help="fx-masks/0.1 plan (JSON)")
    masks.add_argument("--out", type=Path, help="output folder (default: the plan's folder)")
    masks.add_argument("--force", action="store_true", help="overwrite existing mask files whose content differs")
    points = sub.add_parser("points", help="find repeated small objects from a few seed examples (template matching; a table of positions)")
    points.add_argument("plan", type=Path, help="fx-points/0.1 plan (JSON)")
    points.add_argument("--out", type=Path, help="output folder (default: the plan's folder)")
    clips = sub.add_parser("clips", help="prepare generated clips from a plan (stabilise to the first frame, match colour to a still)")
    clips.add_argument("plan", type=Path, help="fx-clips/0.1 plan (JSON)")
    clips.add_argument("--out", type=Path, help="output folder (default: the plan's folder)")
    clips.add_argument("--only", help="comma-separated clip ids to prepare (default: all)")
    clips.add_argument("--force", action="store_true", help="overwrite existing outputs that are not in the record")
    legacy = sub.add_parser("legacy", help="run an external production renderer and record it as a run")
    legacy.add_argument("--renderer", type=Path, required=True)
    legacy.add_argument("--config", type=Path, required=True)
    legacy.add_argument("--start", type=float, help="seconds (default 0; not with --cuts)")
    legacy.add_argument("--duration", type=float, help="seconds (required unless --cuts is given)")
    legacy.add_argument("--cuts", type=int, nargs="+", metavar="FRAME",
                        help="frame numbers N0 N1 ... Nk: render [N0, N1), [N1, N2), ... as seg_<first>_<end>.mp4 in one run, "
                             "several at once (needs --units frames and --fps); the run folder can be passed to mv deliver join")
    legacy.add_argument("--jobs", type=int, help="with --cuts: how many segments run at once (default: all, up to the CPU count)")
    legacy.add_argument("--python", help="interpreter for the renderer (default: this one)")
    legacy.add_argument("--python-flags", help='arguments for the interpreter before the renderer, e.g. --python-flags="-I -X utf8"')
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
    timeline = sub.add_parser("timeline", help="fx-timeline: several fx shots joined by cut, xfade, black, flash and burn")
    steps = timeline.add_subparsers(dest="timeline_command", required=True)
    demo = steps.add_parser("demo", help="write three demo shot specs and an fx-timeline/0.1 timeline that uses black, xfade and flash")
    demo.add_argument("dir", type=Path)
    demo.add_argument("--width", type=int, default=320)
    demo.add_argument("--height", type=int, default=180)
    demo.add_argument("--duration", type=float, default=6.0)
    demo.add_argument("--fps", type=int, default=24)
    render = steps.add_parser("render", help="render timeline.mp4 and timeline.json (for mv gate check) as a run")
    render.add_argument("timeline", type=Path)
    render.add_argument("--start", type=float, default=0.0)
    render.add_argument("--duration", type=float)
    render.add_argument("--out", type=Path, help="default: <timeline dir>/fx_runs")
    render.add_argument("--scale", type=float, default=1.0)
    render.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    render.add_argument("--quiet", action="store_true", help="do not report progress on stderr")
    render.add_argument("--hooks", type=Path, help="effects hooks (Python: prepare, shot, final). Runs only when given here")
    plan = steps.add_parser("plan", help="print the shots and transitions with their times and nearest beats")
    plan.add_argument("timeline", type=Path)


def fx_command(args):
    """fx サブコマンドを実行して結果の辞書を返す。fx の import はここで遅延させる（既存コマンドは cv2 なしで動く）。"""
    command = args.fx_command
    if command == "accept":
        from ..fx import runs
        return runs.accept(args.run_dir, args.variant, args.decision, args.by, args.note)
    if command == "legacy":
        from ..fx import legacy
        if args.cuts is not None:
            if args.start is not None or args.duration is not None:
                raise PipelineError("--cuts は区間を区切りのフレーム番号で決めるので、--start・--duration と一緒には使えません")
            if args.units != "frames":
                raise PipelineError("--cuts はフレーム番号の区切りです。レンダラがフレーム番号で受け取る形（--units frames）にしてください")
            return legacy.run_segments(args.renderer, args.config, args.cuts, args.fps, args.python, args.width, args.height,
                                       args.out, args.timeout, args.cwd, _progress, args.python_flags, args.jobs)
        if args.jobs is not None:
            raise PipelineError("--jobs は --cuts（区間を並べて描く）のときだけ使えます")
        if args.duration is None:
            raise PipelineError("--duration が要ります（区間を並べて描くなら --cuts）")
        return legacy.run_legacy(args.renderer, args.config, 0.0 if args.start is None else args.start, args.duration,
                                 args.python, args.width, args.height, args.fps, args.out, args.timeout, args.cwd, _progress,
                                 args.units, args.python_flags)
    if command == "timeline":
        from ..fx import sequence
        if args.timeline_command == "plan":                 # 描かないので cv2 は要らない
            return sequence.plan_text(args.timeline)
        _require_fx()
        if args.timeline_command == "demo":
            return sequence.demo(args.dir, args.width, args.height, args.duration, args.fps)
        return sequence.render_run(args.timeline, args.out, args.start, args.duration, args.scale, args.force, _progress, args.quiet,
                                   args.hooks)
    _require_fx()
    from ..fx import demo, runs
    if command == "demo":
        return demo.create(args.dir, args.width, args.height, args.duration, args.fps, look=args.look)
    if command == "render":
        return runs.render_run(args.spec, args.variants, args.start, args.duration, args.out, args.scale,
                               not args.no_grid, args.force, _progress, no_look=args.no_look, no_overlays=args.no_overlays)
    if command == "stills":
        return runs.stills(args.spec, args.times, args.variants, args.out, args.scale, no_look=args.no_look, guides=args.guides,
                           grid_step=args.grid_step, reference=args.reference, reference_offset=args.reference_offset,
                           reference_label=args.reference_label, no_overlays=args.no_overlays)
    if command == "guides":
        from ..fx import guides
        return guides.write_image_grid(args.image, args.out, args.crop, args.units, args.step, args.width)
    if command == "compare":
        return runs.compare_run(args.run_dir, args.reference, args.reference_offset, args.reference_label, args.out, _progress)
    if command == "masks":
        from ..fx import masks
        return masks.make_masks(args.plan, args.out, args.force)
    if command == "points":
        from ..fx import points
        return points.find_points(args.plan, args.out)
    if command == "clips":
        from ..fx import clipprep
        only = [value.strip() for value in args.only.split(",") if value.strip()] if args.only else None
        return clipprep.make_clips(args.plan, args.out, args.force, only, _progress)
    raise PipelineError(f"Unsupported fx command: {command}")
