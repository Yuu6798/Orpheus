"""mv export（外のレンダラへ渡すファイルの書き出し）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError


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
    subs = sub.add_parser("subtitles", help="subtitle files (SRT / WebVTT / LRC) and description lyrics from lyric line times")
    subs.add_argument("source", type=Path, help="project folder or analysis.json, a lines JSON ([{start, end, text}]) or an LRC")
    subs.add_argument("--out", type=Path, required=True, help="folder to write into")
    subs.add_argument("--name", help="file name stem (default: the project folder or the source file stem)")
    subs.add_argument("--formats", default="srt,vtt,lrc", help="comma-separated: srt, vtt, lrc, description (default srt,vtt,lrc)")
    subs.add_argument("--field", default="text", help="text field: analysis text|alignment_text; lines JSON any field such as ja")
    subs.add_argument("--lead", type=float, default=0.15, help="seconds a cue appears before the line is sung (default 0.15)")
    subs.add_argument("--tail", type=float, default=0.40, help="seconds a cue stays after the line ends, never past the next cue (default 0.40)")
    subs.add_argument("--duration", type=float, help="video length in seconds: cues are cut there (and an LRC's last line ends there)")
    subs.add_argument("--vtt-settings", default="", help='WebVTT cue settings, e.g. "line:8%% position:50%% align:center" (top of the screen)')
    subs.add_argument("--description-heading", default="■ Lyrics", help="first line of the description lyrics (empty for none)")
    subs.add_argument("--description-body", type=Path, help="description text: check that it plus the lyrics fit in 5,000 bytes")
    subs.add_argument("--force", action="store_true", help="overwrite files whose contents differ")


def export_command(args):
    """export サブコマンド。プロジェクトのファイルを読むだけで、書くのは --out だけ。"""
    from .. import remotion_props
    if args.export_command == "remotion-props":
        return remotion_props.export(args.project.resolve(), args.out, args.beats, args.beats_per_bar, args.downbeat_phase,
                                     storyboard=not args.no_storyboard)
    if args.export_command == "subtitles":
        from .. import subtitles
        formats = tuple(f.strip() for f in args.formats.split(",") if f.strip())
        return subtitles.export(args.source, args.out, args.name, formats, args.field, args.lead, args.tail, args.duration,
                                args.vtt_settings, args.description_heading, args.description_body, args.force)
    raise PipelineError(f"Unsupported export command: {args.export_command}")
