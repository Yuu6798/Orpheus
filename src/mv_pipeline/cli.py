"""mv の入口（pyproject の `mv = "mv_pipeline.cli:main"` と `python -m mv_pipeline.cli`）。parser の組み立て（登録の順が help の
並び）・実行の共通部分（結果の出力と、例外の 1 行化）・MV のプロジェクトの命令を持つ。fx・dance などのまとまりの組み立てと処理は
mv_pipeline/cli_commands/ にあり、ここから以前の名前で読める。"""
import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

from . import pipeline, validate
from .cli_commands import _progress, _require_fx  # noqa: F401（以前の名前。各まとまりの処理が使う）
from .cli_commands.fx import _fx_parser, fx_command
from .cli_commands.dance import _dance_parser, dance_command
from .cli_commands.gate import _gate_parser, gate_command
from .cli_commands.handoff import _handoff_parser, handoff_command
from .cli_commands.inspection import _inspect_parser, inspect_command
from .cli_commands.workflow import _workflow_parser, workflow_command
from .cli_commands.export import _export_parser, export_command
from .cli_commands.sheet import _sheet_parser, sheet_command
from .cli_commands.layer import _layer_parser, layer_command
from .cli_commands.overlay import _overlay_parser, overlay_command
from .cli_commands.deliver import _deliver_parser, deliver_command
from .cli_commands.audio import _audio_parser, audio_command
from .io import PipelineError, atomic_json, read_json
from .runner import project_lock


def parser():
    p = argparse.ArgumentParser(prog="mv", description="Orpheus: local reproducible music videos")
    commands = p.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init")
    init.add_argument("project", type=Path)
    init.add_argument("--audio", type=Path, required=True)
    init.add_argument("--lyrics", type=Path, required=True)
    init.add_argument("--start", type=float, default=0)
    init.add_argument("--duration", type=float)
    init.add_argument("--fps", type=int, default=30)
    init.add_argument("--width", type=int, default=1920)
    init.add_argument("--height", type=int, default=1080)
    for name in ["analyze", "export-brief", "verify", "resume", "status", "confirm-assets"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
    for name in ["import-storyboard", "import-assets", "import-alignment", "apply-edits", "update-lyrics"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        sub.add_argument("file", type=Path)
    for name in ["render", "preview", "validate"]:
        sub = commands.add_parser(name)
        sub.add_argument("project", type=Path)
        sub.add_argument("--profile", choices=["final", "preview"], default="preview" if name == "preview" else "final")
        if name == "preview":
            sub.add_argument("--studio", action="store_true", help="Open the same composition in Remotion Studio")
    demo = commands.add_parser("demo")
    demo.add_argument("project", type=Path)
    demo.add_argument("--width", type=int, default=1920)
    demo.add_argument("--height", type=int, default=1080)
    adapter = commands.add_parser("run-adapter")
    adapter.add_argument("project", type=Path)
    adapter.add_argument("config", type=Path)
    adapter.add_argument("--kind", choices=["alignment", "separation"], required=True)
    _fx_parser(commands)
    _dance_parser(commands)
    _gate_parser(commands)
    _handoff_parser(commands)
    _inspect_parser(commands)
    _workflow_parser(commands)
    _export_parser(commands)
    _sheet_parser(commands)
    _layer_parser(commands)
    _overlay_parser(commands)
    _deliver_parser(commands)
    _audio_parser(commands)
    return p


# ---- 実行 ------------------------------------------------------------------
#
# どのコマンドも「引数 → 結果（dict / list / str / Path / None）」の関数で、_run が stdout への出力と
# 例外の 1 行化を引き受ける。検査の不合格など「結果は出すが終了コードは 1」は args.failed で伝える。

CLI_ERRORS = (PipelineError, OSError, ValueError, KeyError, ImportError, wave.Error, subprocess.CalledProcessError)


def _print_result(result):
    """str はそのまま、dict / list / None は JSON、それ以外（Path など）は str で 1 つ出す。"""
    if isinstance(result, str):
        print(result)
    elif result is None or isinstance(result, (dict, list)):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(str(result))


def _run(function, args):
    """function(args) の結果を stdout に出して終了コードを返す。例外（出力の途中の OSError も）は stderr に 1 行で出して 1。"""
    try:
        _print_result(function(args))
    except CLI_ERRORS as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    return getattr(args, "failed", 0)


# ---- 実験的なサブコマンド群（project_lock を通らない） -------------------------
#
# まとまりごとの組み立て（_X_parser）と処理（X_command）は mv_pipeline/cli_commands/ にある（上で読み、ここから出し直す）。

GROUP_COMMANDS = {"fx": fx_command, "dance": dance_command, "gate": gate_command, "handoff": handoff_command,
                  "inspect": inspect_command, "workflow": workflow_command, "export": export_command, "overlay": overlay_command,
                  "sheet": sheet_command, "layer": layer_command, "deliver": deliver_command, "audio": audio_command}


# ---- MV プロジェクトのコマンド（project_lock の中で動く） -----------------------

def _demo(root, args):
    from .demo import create
    return create(root, args.width, args.height)


def _validate(root, args):
    *_, warnings = validate.bundle(root, args.profile)
    return {"valid": True, "warnings": warnings}


def _confirm_assets(root, args):
    result = {"changed": [], "review_required": []}
    atomic_json(root / "work/asset-review.json", result)
    return result


PROJECT_COMMANDS = {
    "init": lambda root, a: pipeline.initialize(root, a.audio, a.lyrics, a.start, a.duration, a.fps, a.width, a.height),
    "demo": _demo,
    "analyze": lambda root, a: pipeline.analyze(root),
    "export-brief": lambda root, a: pipeline.export_brief(root),
    "import-storyboard": lambda root, a: pipeline.import_storyboard(root, a.file),
    "import-assets": lambda root, a: pipeline.import_assets(root, a.file),
    "import-alignment": lambda root, a: pipeline.import_alignment(root, a.file),
    "apply-edits": lambda root, a: pipeline.import_edits(root, a.file),
    "update-lyrics": lambda root, a: pipeline.update_lyrics(root, a.file),
    "render": lambda root, a: pipeline.render(root, a.profile),
    "preview": lambda root, a: pipeline.render(root, a.profile, a.studio),
    "validate": _validate,
    "verify": lambda root, a: pipeline.verify(root),
    "resume": lambda root, a: pipeline.resume(root),
    "status": lambda root, a: read_json(root / "job.json"),
    "confirm-assets": _confirm_assets,
    "run-adapter": lambda root, a: pipeline.run_adapter(root, a.config, a.kind),
}


def project_command(args):
    """`mv <command> PROJECT` を project_lock の中で実行し、結果を返す。結果の無いコマンドは "OK"。"""
    function = PROJECT_COMMANDS.get(args.command)
    if function is None:
        raise PipelineError(f"Unsupported command: {args.command}")
    root = args.project.resolve()
    with project_lock(root):
        result = function(root, args)
    return "OK" if result is None else result


def main(argv=None):
    args = parser().parse_args(argv)
    return _run(GROUP_COMMANDS.get(args.command, project_command), args)


if __name__ == "__main__":
    raise SystemExit(main())
