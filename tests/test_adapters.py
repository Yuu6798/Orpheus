import sys

from mv_pipeline.adapters.external import invoke
from mv_pipeline.io import read_json


def test_external_adapter_uses_literal_argv_and_declared_output(tmp_path):
    script = tmp_path / "adapter.py"
    script.write_text('import json,sys\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps(sys.argv[2:]))\n')
    output = tmp_path / "output.json"
    literal = "$(should-not-run); & echo secret"
    invoke({"tool": "fixture", "version": "1", "argv": [sys.executable, str(script), "{output}", "{audio}", literal]},
           audio="a b.wav", lyrics="lyrics.txt", output=output, log=tmp_path / "log")
    assert read_json(output) == ["a b.wav", literal]
