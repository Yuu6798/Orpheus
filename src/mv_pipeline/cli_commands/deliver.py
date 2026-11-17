"""mv deliver（受け渡しの書き出し。docs/deliver.md）の組み立てと処理。"""
from pathlib import Path

from ..io import PipelineError


def _deliver_parser(commands):
    """受け渡しの書き出し（docs/deliver.md）。cv2 を使わず、`project` 引数も project_lock も持たない。"""
    deliver = commands.add_parser("deliver", help="experimental: join rendered segments and add the audio into a master MP4")
    sub = deliver.add_subparsers(dest="deliver_command", required=True)
    join = sub.add_parser("join", help="concatenate segment videos without re-encoding, cut the audio with fades and mux a master")
    join.add_argument("segments", type=Path, nargs="+", help="segment videos in order, or one folder of seg_<first>_<end>.mp4")
    join.add_argument("--out", type=Path, required=True, help="master .mp4 to write (a <name>.deliver.json record is written beside it)")
    join.add_argument("--audio", type=Path, help="audio file to cut from (none: video only)")
    join.add_argument("--audio-start", type=float, default=0.0, help="seconds into the audio where the video starts (default 0)")
    join.add_argument("--fade-in", type=float, default=0.02, help="audio fade-in seconds (default 0.02)")
    join.add_argument("--fade-out", type=float, default=0.25, help="audio fade-out seconds (default 0.25)")
    join.add_argument("--force", action="store_true", help="overwrite a master whose contents differ")
    review = sub.add_parser("review-copy", help="small review copy: the master itself under the limit, else a 2-pass H.264 near the target")
    review.add_argument("master", type=Path)
    review.add_argument("--out", type=Path, required=True, help="review .mp4 to write (a <name>.review.json record is written beside it)")
    review.add_argument("--limit-bytes", type=int, default=28_000_000, help="copy the master as is below this size (default 28,000,000)")
    review.add_argument("--target-bytes", type=int, default=27_000_000, help="2-pass size target in bytes (default 27,000,000)")
    review.add_argument("--maxrate", default="11M")
    review.add_argument("--bufsize", default="22M")
    review.add_argument("--preset", default="slow")
    review.add_argument("--threads", type=int, default=1, help="x264 threads; 1 repeats byte for byte (default), 0 lets ffmpeg choose (faster)")
    review.add_argument("--force", action="store_true", help="overwrite a review copy whose contents differ")
    split = sub.add_parser("split", help="cut a master into parts with a Windows join script (join_master.bat) and SHA256SUMS")
    split.add_argument("master", type=Path)
    split.add_argument("--out", type=Path, required=True, help="folder for the parts (a <folder>.split.json record is written beside it)")
    split.add_argument("--part-bytes", type=int, default=25_000_000, help="bytes per part (default 25,000,000)")
    split.add_argument("--name", help="file name of the joined master (ASCII letters, digits, . _ -; default: the master's name)")
    split.add_argument("--title", help="title shown in the join script (default: the name)")
    split.add_argument("--force", action="store_true", help="overwrite parts and files of the same names")


def deliver_command(args):
    from .. import deliver
    if args.deliver_command == "join":
        return deliver.join(args.segments, args.out, args.audio, args.audio_start, args.fade_in, args.fade_out, args.force)
    if args.deliver_command == "review-copy":
        return deliver.review_copy(args.master, args.out, args.limit_bytes, args.target_bytes, args.maxrate, args.bufsize, args.preset,
                                   args.threads, args.force)
    if args.deliver_command == "split":
        return deliver.split(args.master, args.out, args.part_bytes, args.name, args.title, args.force)
    raise PipelineError(f"Unsupported deliver command: {args.deliver_command}")
