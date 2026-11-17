"""mv overlay（透過レイヤーの重ね合わせ。docs/fx.md の overlay）の組み立てと処理。"""
from pathlib import Path

from . import _progress, _require_fx


def _overlay_parser(commands):
    """透過レイヤーの重ね合わせ（docs/fx.md の overlay）。`project` 引数を持たず、project_lock も通らない。書き出しが fx の render にあるので fx extra が要る。"""
    overlay = commands.add_parser("overlay", help="experimental: composite transparent layers (PNG sequences / alpha video) over a base and encode")
    overlay.add_argument("--base", type=Path, required=True, help="base image or video")
    overlay.add_argument("--layer", type=Path, action="append", default=[],
                         help="PNG sequence folder or alpha video; repeatable, bottom to top")
    overlay.add_argument("--layers", type=Path,
                         help="fx-overlay-layers/0.1 JSON: layers with first_frame, timed still images and style (instead of --layer)")
    overlay.add_argument("--frames", type=int, help="frame count when the base is an image and no layer fixes it")
    overlay.add_argument("--audio", type=Path, help="audio file to mux from 0 s")
    overlay.add_argument("--out", type=Path, help="runs folder (default: <base dir>/overlay_runs)")
    overlay.add_argument("--fps", type=float, help="required when --base is an image; must match a video base")
    overlay.add_argument("--crf", type=int, default=18)
    overlay.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    overlay.add_argument("--quiet", action="store_true", help="do not report progress on stderr")


def overlay_command(args):
    _require_fx()      # 書き出しは fx の render（cv2 を import する）にある
    from ..fx import overlay
    return overlay.run_overlay(args.base, args.layer, args.audio, args.out, args.fps, args.crf, args.force, _progress, args.quiet,
                               layers_file=args.layers, frames_count=args.frames)
