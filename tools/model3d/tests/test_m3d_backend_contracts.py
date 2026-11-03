"""Backend error/provenance contracts with synthetic Git repos and mocked inference.

These tests do not run TripoSR, torchmcubes, or real Blender.
"""
import builtins
import contextlib
import importlib.util
import json
import subprocess
import sys
import types
from pathlib import Path

import numpy as np
import pytest


@pytest.fixture
def backend():
    path = Path(__file__).resolve().parents[1] / "backends" / "triposr_initial_model.py"
    spec = importlib.util.spec_from_file_location("model3d_test_triposr_backend", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def source_checkout(tmp_path, backend, monkeypatch):
    root = tmp_path / "source"
    (root / "tsr").mkdir(parents=True)
    (root / "tsr" / "system.py").write_text("# synthetic source fixture\n", encoding="utf-8")

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()

    git("init")
    git("add", "tsr/system.py")
    git("-c", "user.name=Model3d test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    commit = git("rev-parse", "HEAD")
    monkeypatch.setitem(backend.PINNED, "TripoSR", commit)
    return root, commit


def test_source_pin_is_measured_and_dirty_or_wrong_checkout_is_rejected(backend, source_checkout, monkeypatch, tmp_path):
    root, commit = source_checkout
    verified = backend.verify_source(root)
    assert verified["commit"] == commit and verified["clean"] is True
    assert verified["python_files_sha256"]["tsr/system.py"] == backend.digest(root / "tsr/system.py")
    with pytest.raises(ValueError, match="ルート"):
        backend.verify_source(root / "tsr")
    monkeypatch.setitem(backend.PINNED, "TripoSR", "0" * 40)
    with pytest.raises(ValueError, match="commit"):
        backend.verify_source(root)
    monkeypatch.setitem(backend.PINNED, "TripoSR", commit)
    (root / "tsr/system.py").write_text("# changed fixture\n", encoding="utf-8")
    with pytest.raises(ValueError, match="未コミット"):
        backend.verify_source(root)
    with pytest.raises(ValueError, match="Git checkout"):
        backend.verify_source(tmp_path / "not-a-checkout")


def test_wrong_checkpoint_fails_before_heavy_import_or_load(backend, tmp_path, monkeypatch, capsys):
    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "model.ckpt").write_bytes(b"incorrect checkpoint")
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert name not in {"torch", "trimesh", "torchmcubes", "tsr.system"}, name
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    code = backend.main([str(tmp_path / "input.png"), str(tmp_path / "out"),
                         "--source", str(tmp_path / "source"), "--weights", str(weights)])
    assert code == 2
    assert "sha256" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("equal,expected_code", [(True, 0), (False, 4)])
def test_original_comparison_controls_exit_and_keeps_record(backend, source_checkout, tmp_path, monkeypatch, equal, expected_code):
    """Synthetic arrays stand in for the neural model; this is not an inference test."""
    root, commit = source_checkout
    weights = tmp_path / "weights"
    weights.mkdir()
    (weights / "model.ckpt").write_bytes(b"synthetic weights")
    (weights / "config.yaml").write_text("synthetic: true\n", encoding="utf-8")
    monkeypatch.setitem(backend.PINNED, "weight_sha256", backend.digest(weights / "model.ckpt"))
    prepared = tmp_path / "prepared.png"
    prepared.write_bytes(b"synthetic image (PIL is mocked)")

    class Scene:
        shape = (1, 3, 4)

        def __getitem__(self, index):
            return self

        def numpy(self):
            return np.zeros(self.shape)

    class Mesh:
        def __init__(self, offset=0):
            self.vertices = np.array([[0., 0, 0], [1, 0, 0], [0, 1, 0]]) + offset
            self.faces = np.array([[0, 1, 2]])
            self.visual = types.SimpleNamespace(vertex_colors=np.ones((3, 4), dtype=np.uint8) * 255)
            self.bounds = np.array([self.vertices.min(axis=0), self.vertices.max(axis=0)])
            self.extents = self.bounds[1] - self.bounds[0]
            self.is_watertight = False
            self.is_winding_consistent = True

        def export(self, path):
            Path(path).write_text("synthetic placeholder, not a model\n", encoding="utf-8")

        def split(self, only_watertight):
            return [self]

    class TSR:
        @classmethod
        def from_pretrained(cls, *args):
            return cls()

        def __init__(self):
            self.renderer = types.SimpleNamespace(set_chunk_size=lambda value: None)

        def __call__(self, *args, **kwargs):
            return Scene()

        def extract_mesh(self, *args, **kwargs):
            assert kwargs["threshold"] == 17.5
            assert kwargs["resolution"] == 64
            return [Mesh(offset=0 if equal else 1)]

    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(set_num_threads=lambda x: None,
                        manual_seed=lambda x: None, no_grad=contextlib.nullcontext))
    monkeypatch.setitem(sys.modules, "trimesh", types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "PIL", types.SimpleNamespace(Image=types.SimpleNamespace(
        open=lambda path: types.SimpleNamespace(convert=lambda mode: object()))))
    monkeypatch.setitem(sys.modules, "torchmcubes", types.SimpleNamespace(marching_cubes=object()))
    monkeypatch.setitem(sys.modules, "tsr.system", types.SimpleNamespace(TSR=TSR))
    def reconstructed(model, scene, resolution, threshold, *args):
        assert resolution == 64 and threshold == 17.5
        return Mesh()

    monkeypatch.setattr(backend, "reconstruct_extraction", reconstructed)
    output = tmp_path / "out"
    code = backend.main([str(prepared), str(output), "--source", str(root), "--weights", str(weights),
                         "--verify-original", "--resolution", "64", "--threshold", "17.5"])
    assert code == expected_code
    record = json.loads((output / "result.json").read_text(encoding="utf-8"))
    assert record["original_extraction_comparison"]["passed"] is equal
    assert record["status"] == ("succeeded" if equal else "failed")
    assert record["source"]["commit"] == commit
    assert record["config_sha256"] == backend.digest(weights / "config.yaml")
    assert record["pin_verification"]["config_expected_hash"] is False
    assert "initial_model.obj" in record["output_files"]
