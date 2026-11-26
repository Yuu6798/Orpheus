import runpy
from pathlib import Path

import pytest

from model3d import contracts, recipe
from model3d.io import ToolError, atomic_json


EXAMPLE = Path(__file__).resolve().parents[3] / "examples" / "model3d_motion_audit" / "run.py"


def test_reference_and_angle_recipe_share_the_motion_start(tmp_path):
    audit = runpy.run_path(str(EXAMPLE))
    graph, front, spots = audit["recipe_for"](tmp_path / "dance.blend", "Rig", "Hips", 7, 24, 30, 8,
                                              [0.0, 0.2], [80, 96], [0, -4, 1], [0, 0, 1], 35)
    contracts.validate("recipe", graph)
    assert [step["id"] for step in recipe.order_steps(graph)] == ["front", "joints", "scout", "angles"]
    assert [row["f"] for row in front["frames"][:3]] == pytest.approx([7, 7.8, 8.6])
    assert all(row["camera"] == front["frames"][0]["camera"] for row in front["frames"])
    assert [row["f"] for row in spots["frames"]] == [7, 12]
    scout = graph["steps"][2]["parameters"]
    assert scout["dance"] == {"fps": 24, "frame0": 7}
    assert scout["angles"] == [0, -45, 45, 90]


def test_incomplete_shade_pass_stops_before_assembly(tmp_path):
    audit = runpy.run_path(str(EXAMPLE))
    manifest = tmp_path / "passes.json"
    atomic_json(manifest, {"schema_version": "model3d-passes/0.1", "directory": str(tmp_path), "fps": 24,
                           "resolution": [80, 96], "passes": {"shade": {
                               "format": "png", "alpha": False, "frames": [], "missing": [1]}}})
    with pytest.raises(ToolError, match="shade"):
        audit["complete_shade"](manifest, 1)
