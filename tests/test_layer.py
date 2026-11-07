"""`mv layer render`: 外の描画（連番）を run と台帳に載せる。Remotion は呼ばず、代役の描画スクリプトで確かめる。"""
import json
from pathlib import Path

import pytest
from fx_synth import run_cli

from mv_pipeline import layer
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256

STANDIN = Path(__file__).with_name("data") / "layer_standin.py"


def _props(path, frames=6):
    atomic_json(path, {"schema_version": "remotion-props/0.1", "fps": 12, "width": 48, "height": 64, "duration_frames": frames,
                       "lines": [], "untimed": [], "beats": {"frames": [], "downbeat_frames": [], "bpm": None, "source": {"kind": "analysis"}},
                       "scenes": None, "show_lyrics": True, "sources": {"analysis_sha256": "0" * 64, "audio_sha256": "0" * 64}})


def _run(tmp_path, **kwargs):
    _props(tmp_path / "props.json")
    return layer.run_render(tmp_path / "props.json", out=tmp_path / "runs", renderer=STANDIN, **kwargs)


def test_render_is_recorded_with_hashes_versions_and_timing(tmp_path, monkeypatch):
    monkeypatch.setattr(layer, "remotion_version", lambda: "4.0.532")
    public = tmp_path / "public"
    public.mkdir()
    (public / "font.ttf").write_bytes(b"not a real font")
    result = _run(tmp_path, public=public)
    assert result["status"] == "succeeded" and result["frames"] == 6 and result["reused"] is False and result["deterministic"] is None
    run_dir = Path(result["run_dir"])
    assert sorted(p.name for p in (run_dir / "frames").iterdir()) == [f"element-{i:02d}.png" for i in range(6)]
    frames = read_json(run_dir / "frames.json")
    assert frames["count"] == 6 and all(f["sha256"] == sha256(run_dir / "frames" / f["name"]) for f in frames["frames"])
    manifest = read_json(run_dir / "manifest.json")
    rec = manifest["layer"]
    assert rec["inputs"]["entry"]["path"] == "renderer/src/layer-index.tsx" and rec["inputs"]["props"]["sha256"] == sha256(tmp_path / "props.json")
    assert rec["inputs"]["public"]["files"] == {"font.ttf": sha256(public / "font.ttf")} and rec["inputs"]["remotion_version"] == "4.0.532"
    assert rec["inputs"]["renderer"]["sha256"] == sha256(STANDIN) and rec["inputs"]["composition"] == "LyricLayer"
    assert rec["frames_manifest"] == {"path": "frames.json", "count": 6, "sha256": sha256(run_dir / "frames.json")}
    assert rec["browser"] == {"status": "no-browser", "path": None, "version": None} and rec["render_seconds"] > 0
    assert manifest["warnings"] == ["描画スクリプトの Remotion の版 standin が node_modules の 4.0.532 と違います"]
    assert "standin render log line" not in (run_dir / "render.log").read_text(encoding="utf-8")     # 標準出力は記録に、標準エラーは log に
    assert str(tmp_path) not in json.dumps(rec["inputs"])                                               # 個人のパスを書かない
    assert read_json(tmp_path / "runs" / "index.json")["runs"][manifest["key"]]["run_id"] == result["run_id"]
    again = _run(tmp_path, public=public)
    assert again["reused"] is True and again["run_id"] == result["run_id"]


def test_twice_records_determinism_and_always_renders(tmp_path, monkeypatch):
    monkeypatch.setattr(layer, "remotion_version", lambda: "4.0.532")
    first = _run(tmp_path)
    twice = _run(tmp_path, twice=True)
    assert twice["reused"] is False and twice["run_id"] != first["run_id"] and twice["deterministic"] is True
    rec = read_json(Path(twice["run_dir"]) / "manifest.json")["layer"]
    assert rec["deterministic"]["frames_compared"] == 6 and rec["deterministic"]["differing_frames"] == [] and rec["twice"] is True
    assert not (Path(twice["run_dir"]) / "frames-again").exists()
    monkeypatch.setenv("LAYER_STANDIN_NOISE", "1")
    noisy = _run(tmp_path, twice=True)
    assert noisy["deterministic"] is False and noisy["status"] == "succeeded"       # 一致しないことを記録する（合否ではない）
    rec = read_json(Path(noisy["run_dir"]) / "manifest.json")["layer"]["deterministic"]
    assert rec["same"] is False and rec["differing_frames"] == list(range(6))


def test_bad_inputs_and_a_failing_renderer(tmp_path, monkeypatch):
    monkeypatch.setattr(layer, "remotion_version", lambda: "4.0.532")
    with pytest.raises(PipelineError, match="--props"):
        layer.run_render(tmp_path / "missing.json", renderer=STANDIN)
    _props(tmp_path / "props.json")
    with pytest.raises(PipelineError, match="--public"):
        layer.run_render(tmp_path / "props.json", public=tmp_path / "nope", out=tmp_path / "runs", renderer=STANDIN)
    broken = tmp_path / "broken.py"
    broken.write_text("import sys; print('boom', file=sys.stderr); sys.exit(3)\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="終了コード 3"):
        layer.run_render(tmp_path / "props.json", out=tmp_path / "runs", renderer=broken)
    manifests = [read_json(p) for p in (tmp_path / "runs").glob("*/manifest.json")]
    assert [m["status"] for m in manifests] == ["failed"] and "boom" in (Path(tmp_path / "runs") / manifests[0]["run_id"] / "render.log").read_text()
    empty = tmp_path / "empty.py"
    empty.write_text("import sys, json; print(json.dumps({'frames': 0}))\n", encoding="utf-8")
    with pytest.raises(PipelineError, match="連番"):
        layer.run_render(tmp_path / "props.json", out=tmp_path / "runs", renderer=empty, force=True)


def test_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(layer, "remotion_version", lambda: "4.0.532")
    _props(tmp_path / "props.json", frames=3)
    code, result, err = run_cli("layer", "render", "--props", tmp_path / "props.json", "--out", tmp_path / "runs", "--renderer", STANDIN, "--twice")
    assert code == 0, err
    assert result["frames"] == 3 and result["deterministic"] is True and result["frames_dir"].endswith("frames")
