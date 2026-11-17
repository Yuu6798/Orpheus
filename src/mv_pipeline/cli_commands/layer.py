"""mv layer（外の描画を台帳に載せる。docs/remotion-props.md）の組み立てと処理。"""
from pathlib import Path

from . import _progress


def _layer_parser(commands):
    """外の描画（Remotion の透過 PNG 連番）を台帳に載せる（docs/remotion-props.md）。`project` 引数を持たず、project_lock も通らない。"""
    layer = commands.add_parser("layer", help="experimental: render a transparent layer with Remotion and record it as a run")
    sub = layer.add_subparsers(dest="layer_command", required=True)
    render = sub.add_parser("render", help="render a composition to a PNG sequence (frames/) and record versions, hashes and timing")
    render.add_argument("--props", type=Path, required=True, help="props.json (remotion-props/0.1 from mv export remotion-props)")
    render.add_argument("--entry", type=Path, help="Remotion entry (default: renderer/src/layer-index.tsx)")
    render.add_argument("--composition", default="LyricLayer")
    render.add_argument("--public", type=Path, help="public folder (fonts etc.); every file's sha256 is recorded")
    render.add_argument("--out", type=Path, help="runs folder (default: <props dir>/layer_runs)")
    render.add_argument("--twice", action="store_true", help="render twice and record whether every frame hash matches")
    render.add_argument("--force", action="store_true", help="render again even if an identical run exists")
    render.add_argument("--renderer", type=Path, help="render script (default: renderer/render-frames.mjs)")
    render.add_argument("--concurrency", type=int, default=2)


def layer_command(args):
    from .. import layer
    return layer.run_render(args.props, args.entry, args.composition, args.public, args.out, args.twice, args.force, args.renderer,
                            args.concurrency, _progress)
