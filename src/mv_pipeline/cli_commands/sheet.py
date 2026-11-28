"""mv sheet（見るためのシートと並べた動画。docs/sheet.md）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError


def _sheet_parser(commands):
    """見るためのシート（docs/sheet.md）。検査ではなく閲覧の道具で、合否は出さない。`project` 引数を持たず、project_lock も通らない。"""
    sheet = commands.add_parser("sheet", help="contact sheet of frames at given times, even spacing or timeline events (viewing aid, no pass/fail)")
    sheet.add_argument("source", type=Path, help="video file or PNG sequence folder")
    sheet.add_argument("--out", type=Path, required=True,
                       help="sheet image to write; the extension picks the format (.png, or .jpg/.jpeg at quality 90). "
                            "A .json with the cells is written beside it")
    sheet.add_argument("--at", type=float, nargs="+", help="times in seconds (not together with --events)")
    sheet.add_argument("--events", type=Path, help="timeline.json; frames at line starts, flashes and seams (not together with --at)")
    sheet.add_argument("--every", type=float, metavar="S", help="add a frame every S seconds from 0 (alone, or with --at / --events / --count)")
    sheet.add_argument("--count", type=int, metavar="N",
                       help="add the middle frame of each of N equal parts of the whole length (alone, or with --at / --events / --every)")
    sheet.add_argument("--crop", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"),
                       help="region of each frame to show (in --units), at its native size unless --cell-width is given")
    sheet.add_argument("--units", choices=["norm", "px"], default="norm", help="units of --crop. norm: 0..1 of the frame (default), px: frame pixels")
    sheet.add_argument("--kinds", default="lyrics,flashes,seams", help="comma-separated event kinds to take from --events")
    sheet.add_argument("--fps", type=float, help="fps of a PNG sequence (default: the timeline's fps)")
    sheet.add_argument("--backgrounds", default="white,black,gray", help="comma-separated backgrounds for transparent frames")
    sheet.add_argument("--cell-width", type=int, help="cell width in pixels (default 320; with --crop, the region's own width)")
    sheet.add_argument("--columns", type=int, default=4, help="columns for opaque sources (transparent ones use one column per background)")
    sheet.add_argument("--font", type=Path, help="TTF/TTC for the labels (default: a common Japanese font if found, else ASCII labels)")
    sheet.add_argument("--with", dest="also", type=Path, action="append", default=[],
                       help="another video or opaque PNG sequence to compare (repeatable). With a sheet --out: one row per source at the same "
                            "times (--at/--every/--count). With an .mp4 --out: the videos side by side")
    sheet.add_argument("--labels", help="comma-separated names of the source and each --with (default: file names)")
    sheet.add_argument("--height", type=int, help="side-by-side video: panel height (default: the lowest video)")
    sheet.add_argument("--audio", type=int, default=1, help="side-by-side video: take the audio of this video (1 = the first, 0 = none)")
    sheet.add_argument("--crf", type=int, default=20, help="side-by-side video: H.264 CRF")
    sheet.add_argument("--brief", action="store_true", help="print one line instead of the JSON summary")


def sheet_command(args):
    result = _sheet(args)
    if getattr(args, "brief", False) and isinstance(result, dict):
        made = result.get("sheet") or result.get("video") or result.get("path")
        size = result.get("size")
        return f"{made}" + (f"（{result['cells']} コマ・{size[0]}x{size[1]}）" if "cells" in result and size else "")
    return result


def _sheet(args):
    from .. import sheet
    split = lambda value: tuple(v.strip() for v in value.split(",") if v.strip())  # noqa: E731
    labels = split(args.labels) if args.labels else None
    if args.out.suffix.lower() == ".mp4":
        if args.at or args.events or args.every is not None or args.count is not None or args.crop:
            raise PipelineError("並べた動画（.mp4 の --out）には --at・--events・--every・--count・--crop は使えません")
        return sheet.side_by_side([args.source, *args.also], args.out, args.height, args.audio, labels, args.crf, args.fps, args.font)
    if args.height is not None or args.audio != 1 or args.crf != 20:
        raise PipelineError("--height・--audio・--crf は並べた動画（.mp4 の --out）だけで使えます")
    if args.also:
        if args.events or args.crop:
            raise PipelineError("段で並べるシート（--with）には --events・--crop は使えません")
        return sheet.build_rows([args.source, *args.also], args.out, args.at, args.every, args.count, args.fps, labels,
                                args.cell_width, args.font)
    if labels:
        raise PipelineError("--labels は --with と一緒に使ってください")
    return sheet.build(args.source, args.out, args.at, args.events, args.fps, split(args.backgrounds), args.cell_width, args.columns,
                       split(args.kinds), args.font, every=args.every, count=args.count, crop=args.crop, units=args.units)
