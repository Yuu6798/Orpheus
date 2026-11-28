"""mv gate（書き出し前の機械的な検査と行・秒の確認表。docs/gate.md）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError


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
    faces = sub.add_parser("faces", help="project the face boxes of an fx timeline onto every frame without rendering, and count "
                                         "frames where a face box is cut by the frame edge (numbers only; faces.json for gate check)")
    faces.add_argument("timeline", type=Path, help="fx timeline (fx-timeline/0.2)")
    faces.add_argument("--out", type=Path, required=True, help="folder for faces.json (gate check faces + visible) and faces_report.json")
    faces.add_argument("--start", type=float, default=0.0, help="start of the range in timeline seconds (default 0)")
    faces.add_argument("--duration", type=float, help="length of the range in seconds (default: to the end)")
    faces.add_argument("--extra", type=Path, help="gate-boxes/0.1 JSON with more boxes per shot in source coordinates (hands, props)")
    faces.add_argument("--min-visible", type=float, default=1.0,
                       help="count a frame when a main box has less than this share of its area inside the frame (default 1.0: any cut)")
    tl = sub.add_parser("timeline", help="measure a transparent PNG sequence (alpha) into a dance-timeline/0.1 for gate check")
    tl.add_argument("--frames", action="append", default=[], required=True,
                    help="PNG sequence folder; 'DIR:lyric-001' binds the folder to one line. Repeatable")
    tl.add_argument("--lyrics", type=Path, required=True, help="props.json from mv export remotion-props (remotion-props/0.1)")
    tl.add_argument("--layer", required=True, help="layer name written into the timeline (e.g. band)")
    tl.add_argument("--hints", type=Path, help="JSON {line id: normalized box} when one sequence holds several lines")
    tl.add_argument("--alpha-threshold", type=int, default=64, help="a pixel is visible when alpha is above this (0-254)")
    tl.add_argument("--flashes", help="comma-separated flash times in seconds to write into the timeline (props have none)")
    tl.add_argument("--out", type=Path, required=True, help="timeline.json to write (plus <stem>_measured.json)")
    flashes = sub.add_parser("flashes", help="count whole-frame brightness flashes in a finished video (no timeline); "
                                             "exit code 1 above --max-per-s. Not a compliance judgment")
    flashes.add_argument("video", type=Path)
    flashes.add_argument("--out", type=Path, help="default: <video stem>_flash_report next to the video")
    flashes.add_argument("--delta", type=float, default=0.10, help="a change is a rise or fall of at least this from the last peak or valley (linear relative luminance 0-1)")
    flashes.add_argument("--max-per-s", type=float, default=3, help="most flashes allowed in any one-second window")
    flashes.add_argument("--brief", action="store_true", help="print one line instead of the JSON summary")
    tail = sub.add_parser("tail", help="when the sound of a finished video ends and how long the picture goes on after it (no "
                                       "timeline); warns above --max-tail-s or when the sound is still on at the end. Numbers only")
    tail.add_argument("video", type=Path)
    tail.add_argument("--out", type=Path, help="default: <video stem>_tail_report next to the video")
    tail.add_argument("--max-tail-s", type=float, help="warn when the picture goes on longer than this after the sound ends (default 1.0)")
    tail.add_argument("--window", type=float, help="loudness window in seconds (default 0.1)")
    tail.add_argument("--floor-db", type=float, help="a window sounds when within this many dB of the loudest (default -60)")
    final = sub.add_parser("final", help="one table of export checks for a video drawn in 3D: structure (fx probe.check_video), "
                                         "model3d check.video_frames, last frame brightness, render.show shadow overflow lines and "
                                         "render seconds; exit code 1 when a row fails. Numbers only")
    final.add_argument("video", type=Path)
    final.add_argument("--video-frames", type=Path, help="report of model3d check.video_frames for this video")
    final.add_argument("--show", type=Path, action="append", default=[], help="report of model3d render.show (repeatable: one per chunk)")
    final.add_argument("--width", type=int, help="expected width (default: from the render.show reports)")
    final.add_argument("--height", type=int, help="expected height (default: from the render.show reports)")
    final.add_argument("--fps", type=float, help="expected fps (default: from the render.show reports)")
    final.add_argument("--frames", type=int, help="expected frame count (with fps, also the sound length)")
    final.add_argument("--no-audio", action="store_true", help="the video is meant to be silent")
    final.add_argument("--min-last-mean", type=float, default=0.02,
                       help="the last frame fails below this mean brightness 0-1 (default 0.02; 0 for videos that end in black)")
    final.add_argument("--out", type=Path, help="default: <video stem>_final_report next to the video")
    voice = sub.add_parser("voice", help="numbers only: where the vocal stem has voice (segments and the gaps between them), "
                                         "to find line starts, ends and breaths")
    voice.add_argument("vocals", type=Path)
    voice.add_argument("--from", dest="start", type=float, default=0.0, help="start of the range in seconds (default 0)")
    voice.add_argument("--to", dest="stop", type=float, help="end of the range in seconds (default: the end)")
    voice.add_argument("--threshold-db", type=float, default=-32.0, help="voice when the level is above this, relative to the 95th percentile")
    voice.add_argument("--gap-s", type=float, default=0.14, help="join segments closer than this")
    voice.add_argument("--min-s", type=float, default=0.08, help="drop segments shorter than this")
    voice.add_argument("--out", type=Path, help="also write the record (JSON) here")
    voice.add_argument("--force", action="store_true", help="overwrite an --out whose contents differ")
    notes = sub.add_parser("notes", help="numbers only: sung notes (pYIN, semitones), onsets and level dips in a range")
    notes.add_argument("vocals", type=Path)
    notes.add_argument("--from", dest="start", type=float, required=True)
    notes.add_argument("--to", dest="stop", type=float, required=True)
    notes.add_argument("--out", type=Path, help="also write the record (JSON) here")
    notes.add_argument("--force", action="store_true", help="overwrite an --out whose contents differ")
    repeat = sub.add_parser("repeat", help="numbers only: where the melody of --ref repeats inside --search (offset in seconds, match rate)")
    repeat.add_argument("vocals", type=Path)
    repeat.add_argument("--ref", type=float, nargs=2, required=True, metavar=("FROM", "TO"))
    repeat.add_argument("--search", type=float, nargs=2, required=True, metavar=("FROM", "TO"))
    repeat.add_argument("--top", type=int, default=3, help="how many best offsets to list (default 3)")
    repeat.add_argument("--include-end", action="store_true",
                        help="also compare the alignment that ends exactly at the end of --search (the production script skips it)")
    repeat.add_argument("--out", type=Path, help="also write the record (JSON) here")
    repeat.add_argument("--force", action="store_true", help="overwrite an --out whose contents differ")


def gate_command(args):
    """gate サブコマンドを実行して結果の辞書を返す。check の不合格と、flashes の上限の超過と、final の不合格は args.failed=1。"""
    if args.gate_command == "lines":
        from ..gate import lines
        return lines.make_table(args.lines, args.out, args.vocals, args.window, args.offset)
    if args.gate_command == "demo":
        from ..gate import demo
        return demo.create(args.dir, args.duration, args.fps, args.bpm, args.beats_per_bar, not args.no_downbeats, args.shifts,
                           args.flashes, args.flash_align, args.attack_ms, args.floor_db)
    if args.gate_command == "faces":
        from . import _require_fx
        _require_fx()       # 枠を写すのに fx の timeline の素材を読む（cv2 を使う）
        from ..gate import faces
        return faces.write(args.timeline, args.out, args.start, args.duration, args.extra, args.min_visible)
    if args.gate_command == "timeline":
        from ..gate import frames_timeline
        flashes = [v for v in (args.flashes or "").split(",") if v.strip()]
        try:
            flashes = [float(v) for v in flashes]
        except ValueError as error:
            raise PipelineError(f"--flashes は秒をコンマで区切って指定してください: {args.flashes!r}") from error
        return frames_timeline.write(args.frames, args.lyrics, args.layer, args.out, args.hints, args.alpha_threshold, flashes)
    if args.gate_command == "flashes":
        from ..gate import flashes
        report, out = flashes.run(args.video, args.out, args.delta, args.max_per_s)
        args.failed = 1 if report["status"] == "fail" else 0
        return flashes.brief(report, out) if args.brief else flashes.summary(report, out)
    if args.gate_command == "tail":
        from ..gate import tail
        report, out = tail.run(args.video, args.out, args.max_tail_s, args.window, args.floor_db)
        return tail.summary(report, out)
    if args.gate_command == "final":
        from ..gate import final
        report, out = final.run(args.video, args.out, args.video_frames, args.show, args.width, args.height, args.fps, args.frames,
                                not args.no_audio, args.min_last_mean)
        args.failed = 1 if report["status"] == "fail" else 0
        return final.summary(report, out)
    if args.gate_command in ("voice", "notes", "repeat"):
        from ..gate import vocals
        if args.gate_command == "voice":
            record = vocals.voice(args.vocals, args.start, args.stop, args.threshold_db, args.gap_s, args.min_s)
        elif args.gate_command == "notes":
            record = vocals.notes(args.vocals, args.start, args.stop)
        else:
            record = vocals.repeat(args.vocals, tuple(args.ref), tuple(args.search), args.top, args.include_end)
        return vocals.summary(record, vocals.save(record, args.out, args.vocals, args.force), args.out)
    from ..gate import checks
    report, out = checks.run(args.config, args.out)
    args.failed = 1 if report["overall"] == "fail" else 0
    return {"overall": report["overall"], "out": str(out),
            "checks": [{k: c[k] for k in ("check", "status", "summary")} for c in report["checks"]]}
