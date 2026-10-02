"""Connect an already-installed local separator/aligner using an argv JSON file.

The configuration is an explicit local execution request, never storyboard data.
The wrapper writes model-neutral output to {output}; the importer validates it.
"""
from pathlib import Path

from ..io import PipelineError, run


def invoke(config, *, audio, lyrics, output, log, analysis=None):
    argv = config.get("argv")
    if not isinstance(argv, list) or not argv or not all(isinstance(x, str) for x in argv):
        raise PipelineError("Adapter configuration requires an argv string array")
    executable = Path(argv[0])
    if not executable.is_absolute() or not executable.is_file():
        raise PipelineError("Adapter executable must be an existing absolute path")
    if not config.get("tool") or not config.get("version"):
        raise PipelineError("Adapter requires tool and version")
    values = {"{audio}": str(audio), "{lyrics}": str(lyrics), "{output}": str(output), "{analysis}": str(analysis)}
    args = []
    for part in argv:
        for key, value in values.items():
            part = part.replace(key, value)
        args.append(part)
    run(args, log=log)
    if not Path(output).is_file():
        raise PipelineError("Adapter did not create its declared output")
