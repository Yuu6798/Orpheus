from orpheus_catalog import capability_catalog as catalog_module
from orpheus_catalog.capability_catalog import build_catalog
from orpheus_catalog.capability_validation import validate_catalog


def _available(kind, count=0):
    key = "commands" if kind == "mv" else "operations"
    return {"available": True, "state": "available", "reason": None, key: count}


def _missing(kind):
    key = "commands" if kind == "mv" else "operations"
    return {"available": False, "state": "missing", "reason": "not installed", key: 0}


def _broken(kind):
    key = "commands" if kind == "mv" else "operations"
    return {"available": False, "state": "broken", "reason": "ImportError: broken dependency", key: 0}


def test_validate_rejects_missing_capabilities_from_an_available_component(monkeypatch):
    monkeypatch.setattr(catalog_module, "_mv_capabilities", lambda: (_available("mv"), []))
    monkeypatch.setattr(catalog_module, "_model3d_capabilities", lambda: (_missing("model3d"), []))
    report = validate_catalog(build_catalog())
    assert report["valid"] is False
    assert any(error["code"] == "UNKNOWN_CAPABILITY" and error["id"].startswith("mv.")
               for error in report["errors"])
    assert not any(error["code"] == "UNKNOWN_CAPABILITY" and error["id"].startswith("model3d.")
                   for error in report["errors"])


def test_validate_allows_capabilities_from_a_missing_component(monkeypatch):
    monkeypatch.setattr(catalog_module, "_mv_capabilities", lambda: (_missing("mv"), []))
    monkeypatch.setattr(catalog_module, "_model3d_capabilities", lambda: (_missing("model3d"), []))
    report = validate_catalog(build_catalog())
    assert report["valid"] is True
    assert report["errors"] == []


def test_validate_rejects_a_broken_component(monkeypatch):
    monkeypatch.setattr(catalog_module, "_mv_capabilities", lambda: (_broken("mv"), []))
    monkeypatch.setattr(catalog_module, "_model3d_capabilities", lambda: (_missing("model3d"), []))
    report = validate_catalog(build_catalog())
    assert report["valid"] is False
    assert {"code": "BROKEN_COMPONENT", "component": "mv", "reason": "ImportError: broken dependency"} in report["errors"]


def test_current_catalog_passes_strict_validation():
    from pathlib import Path
    import pytest

    if not Path("tools/model3d/src/model3d/ops/__init__.py").is_file():
        pytest.skip("局所コピーにはmodel3dの完全な登録表がない")
    report = validate_catalog(build_catalog())
    assert report["valid"] is True, report
