import sys
from types import ModuleType

import pytest

from orpheus_catalog import capability_catalog as catalog


def _fake_model3d(monkeypatch):
    package = ModuleType("model3d")
    package.__path__ = []
    ops = ModuleType("model3d.ops")
    ops.describe = lambda: [
        {"operation": "blender.run_script", "inputs": ["blend"], "outputs": ["blend"],
         "summary": "run blender", "cacheable": False, "selectable": []},
        {"operation": "render.assemble", "inputs": ["frames", "audio"], "outputs": ["video"],
         "summary": "assemble", "cacheable": False, "selectable": []},
        {"operation": "check.unannotated", "inputs": ["track"], "outputs": ["report"],
         "summary": "unannotated", "cacheable": True, "selectable": []},
    ]
    package.ops = ops
    monkeypatch.setitem(sys.modules, "model3d", package)
    monkeypatch.setitem(sys.modules, "model3d.ops", ops)


def test_gate_check_declares_conditional_ffmpeg_use():
    effect = catalog.MV_RUNTIME[("gate", "check")]
    assert effect["external_process"] == "opt-in"
    assert effect["tools"] == ["ffmpeg"]


def test_model3d_subprocesses_are_explicit_and_unknown_is_not_never(monkeypatch):
    _fake_model3d(monkeypatch)
    component, capabilities = catalog._model3d_capabilities()
    assert component["state"] == "available"
    by_id = {item["id"]: item for item in capabilities}

    blender = by_id["model3d.blender.run_script"]["requires"]
    assert blender["tools"] == ["blender"]
    assert blender["network"]["mode"] == "recipe-defined"
    assert blender["external_process"]["mode"] == "always"

    assemble = by_id["model3d.render.assemble"]["requires"]
    assert assemble["tools"] == ["ffmpeg"]
    assert assemble["network"]["mode"] == "never"
    assert assemble["external_process"]["mode"] == "always"

    unannotated = by_id["model3d.check.unannotated"]["requires"]
    assert unannotated["network"]["mode"] == "unknown"
    assert unannotated["external_process"]["mode"] == "unknown"
    assert "unknown" in catalog.VALID_EFFECT_MODES


def test_real_registry_marks_review_examples_when_available():
    try:
        import model3d.ops  # noqa: F401
    except ImportError:
        pytest.skip("最小単体試験にはmodel3d extraを置いていない")
    component, capabilities = catalog._model3d_capabilities()
    if not component["available"]:
        pytest.skip(component["reason"] or "model3d registryを読み込めない")
    by_id = {item["id"]: item for item in capabilities}
    assert by_id["model3d.blender.run_script"]["requires"]["external_process"]["mode"] == "always"
    assert by_id["model3d.render.assemble"]["requires"]["tools"] == ["ffmpeg"]
