"""mv inspect（生成動画の検品。docs/inspect.md）の組み立てと処理。"""
from pathlib import Path

from . import _progress, _require_fx


def _inspect_parser(commands):
    """生成動画の検品（docs/inspect.md）。`project` 引数を持たず、project_lock も通らない。合否は出さない。"""
    inspect = commands.add_parser("inspect", help="inspect generated video: locate, classify and link anomalies to fixes (no pass/fail)")
    sub = inspect.add_subparsers(dest="inspect_command", required=True)
    check = sub.add_parser("check", help="measure every frame and write the report, stills and fix plan")
    check.add_argument("config", type=Path, help="inspect.json (inspect/0.1)")
    check.add_argument("--out", type=Path, help="default: <config dir>/inspect_report")
    check.add_argument("--vlm", type=Path, help="VLM adapter config (tool, version, argv, sends_data). Runs only when given here")
    check.add_argument("--brief", action="store_true", help="print one or two lines instead of the JSON summary")
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


def inspect_command(args):
    """inspect サブコマンド。cv2 を使うので fx extra が要る。"""
    _require_fx()
    if args.inspect_command == "demo":
        from ..inspection import demo
        return demo.create(args.dir, args.width, args.height, args.fps, args.duration)
    from ..inspection import run
    result, out = run.run(args.config, args.out, args.vlm, args.allow_send, args.vlm_packet, args.vlm_response,
                          None if args.quiet else _progress, vlm_legacy=args.vlm_legacy_response)
    return run.brief(result, out) if getattr(args, "brief", False) else run.summarize(result, out)
