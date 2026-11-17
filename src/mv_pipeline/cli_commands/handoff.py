"""mv handoff（届いた絵の機械的な検査。docs/handoff.md）の組み立てと処理。"""
from pathlib import Path


def _handoff_parser(commands):
    """届いた絵の機械的な検査（docs/handoff.md）。`project` 引数を持たず、project_lock も通らない。"""
    handoff = commands.add_parser("handoff", help="mechanical checks of work delivered by someone else (no taste judgement)")
    sub = handoff.add_subparsers(dest="handoff_command", required=True)
    check = sub.add_parser("check", help="compare a delivered image with the base: outside the allowed regions it must not change, and the "
                                          "size must match; exit code 1 on fail. Optionally write the image with the outside restored")
    check.add_argument("base", type=Path, help="the image you sent (the base)")
    check.add_argument("delivered", type=Path, help="the image you received")
    check.add_argument("--region", action="append", default=[], metavar="X0,Y0,X1,Y1",
                       help="allowed region in base pixels (right and bottom excluded); repeat for more regions")
    check.add_argument("--regions-width", type=float, help="the regions are written for an image this wide (scaled to the base width)")
    check.add_argument("--tolerance", type=float, default=8.0, help="a pixel counts as changed above this difference (0-255, default 8)")
    check.add_argument("--max-changed", type=int, default=0, help="fail when more changed pixels than this lie outside (default 0)")
    check.add_argument("--allow-resize", action="store_true", help="compare after shrinking a delivered image of another size (warn)")
    check.add_argument("--restore", type=Path, metavar="OUT.png",
                       help="write the delivered image with everything outside the regions put back to the base (PNG only: lossless)")
    check.add_argument("--feather", type=float, default=12.0, help="soft edge of the restored regions in region pixels (default 12)")
    check.add_argument("--out", type=Path, help="default: <delivered stem>_handoff_report next to the delivered image")


def handoff_command(args):
    """handoff サブコマンドを実行して結果の辞書を返す。check の fail は args.failed=1。"""
    from .. import handoff
    report, out = handoff.run(args.base, args.delivered, args.region, args.regions_width, args.tolerance, args.max_changed,
                              args.allow_resize, args.restore, args.feather, args.out)
    args.failed = 1 if report["status"] == "fail" else 0
    return handoff.summary(report, out)
