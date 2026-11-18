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


def _command(id, path):
    return {
        "id": id, "kind": "command", "domain": "test", "maturity": "experimental", "summary": id,
        "aliases": [], "tags": [], "entrypoint": {"argv": ["mv", *path], "docs": "docs/test.md"},
        "inputs": [], "outputs": [], "parameters": {"status": "cli", "schema": {"type": "object"}},
        "requires": catalog._requirements(), "availability": {"available": True, "reason": None},
    }


def test_gate_check_declares_conditional_ffmpeg_use():
    effect = catalog.MV_RUNTIME[("gate", "check")]
    assert effect["external_process"] == "opt-in"
    assert effect["tools"] == ["ffmpeg"]


def test_demo_resume_audio_and_tail_runtime_effects_are_explicit():
    assert catalog.MV_RUNTIME[("demo",)]["tools"] == ["ffmpeg"]
    assert catalog.MV_RUNTIME[("demo",)]["external_process"] == "always"
    assert catalog.MV_RUNTIME[("resume",)]["external_process"] == "opt-in"
    assert {"node", "remotion", "ffmpeg", "ffprobe"} <= set(catalog.MV_RUNTIME[("resume",)]["tools"])
    assert catalog.MV_RUNTIME[("audio", "end")]["tools"] == ["ffmpeg"]
    assert catalog.MV_RUNTIME[("audio", "lows")]["tools"] == ["ffmpeg"]
    assert catalog.MV_RUNTIME[("gate", "tail")]["tools"] == ["ffmpeg"]


def test_unannotated_mv_command_is_unknown_not_never(monkeypatch):
    component = {"available": True, "state": "available", "reason": None, "commands": 2}
    rows = [_command("mv.unannotated", ("unannotated",)), _command("mv.demo", ("demo",))]
    monkeypatch.setattr(catalog, "_ORIGINAL_MV_CAPABILITIES", lambda: (component, rows))

    _, capabilities = catalog._mv_capabilities()
    by_id = {item["id"]: item for item in capabilities}
    assert by_id["mv.unannotated"]["requires"]["network"]["mode"] == "unknown"
    assert by_id["mv.unannotated"]["requires"]["external_process"]["mode"] == "unknown"
    assert by_id["mv.demo"]["requires"]["external_process"]["mode"] == "never"  # stand-in collector did not apply MV_RUNTIME


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


def test_real_mv_registry_marks_review_examples_when_available():
    try:
        import mv_pipeline.cli  # noqa: F401
    except ImportError:
        pytest.skip("最小単体試験にはmv CLIを置いていない")
    component, capabilities = catalog._mv_capabilities()
    if not component["available"]:
        pytest.skip(component["reason"] or "mv parserを読み込めない")
    by_id = {item["id"]: item for item in capabilities}
    assert by_id["mv.demo"]["requires"]["tools"] == ["ffmpeg"]
    assert by_id["mv.resume"]["requires"]["external_process"]["mode"] == "opt-in"
    assert by_id["mv.gate.check"]["requires"]["external_process"]["mode"] == "opt-in"
    assert by_id["mv.gate.tail"]["requires"]["tools"] == ["ffmpeg"]


def test_real_model3d_registry_marks_review_examples_when_available():
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
