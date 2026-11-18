from mv_pipeline import capability_catalog as catalog_module
from mv_pipeline.capability_catalog import build_catalog
from mv_pipeline.capability_validation import validate_catalog


def test_validate_rejects_missing_capabilities_from_an_available_component(monkeypatch):
    monkeypatch.setattr(catalog_module, "_mv_capabilities",
                        lambda: ({"available": True, "reason": None, "commands": 0}, []))
    monkeypatch.setattr(catalog_module, "_model3d_capabilities",
                        lambda: ({"available": False, "reason": "not installed", "operations": 0}, []))

    report = validate_catalog(build_catalog())

    assert report["valid"] is False
    assert any(error["code"] == "UNKNOWN_CAPABILITY" and error["id"].startswith("mv.")
               for error in report["errors"])
    assert not any(error["code"] == "UNKNOWN_CAPABILITY" and error["id"].startswith("model3d.")
                   for error in report["errors"])


def test_validate_allows_capabilities_from_an_unavailable_component(monkeypatch):
    monkeypatch.setattr(catalog_module, "_mv_capabilities",
                        lambda: ({"available": False, "reason": "not installed", "commands": 0}, []))
    monkeypatch.setattr(catalog_module, "_model3d_capabilities",
                        lambda: ({"available": False, "reason": "not installed", "operations": 0}, []))

    report = validate_catalog(build_catalog())

    assert report["valid"] is True
    assert report["errors"] == []
