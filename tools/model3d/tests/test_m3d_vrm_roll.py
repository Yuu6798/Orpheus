"""合成の GLB だけで、拘束の検算とレシピへの登録を確認する。"""

import json
import math
import struct
from pathlib import Path

import pytest

from model3d import executor, vrm_roll
from model3d.io import ToolError, atomic_json


def _glb(document):
    payload = json.dumps({"asset": {"version": "2.0"}, **document}).encode()
    payload += b" " * (-len(payload) % 4)
    return struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload


def _rotation(axis, degrees):
    half = math.radians(degrees) / 2
    components = [0., 0., 0., math.cos(half)]
    components["XYZ".index(axis)] = math.sin(half)
    return components


def _node(name, kind="roll", source=1, **parameters):
    return {"name": name, "extensions": {"VRMC_node_constraint": {"specVersion": "1.0",
             "constraint": {kind: {"source": source, **parameters}}}}}


def synthetic_document():
    half = math.sqrt(0.5)
    return {"nodes": [
        {"name": "driver parent", "children": [1]},
        {"name": "driver"},
        {"name": "helper parent", "children": [3, 4, 5, 6, 7, 8]},
        _node("helper", rollAxis="X", weight=0.3),
        _node("helper", rollAxis="Y", weight=0.5),
        _node("helper", rollAxis="Z"),
        {**_node("misaligned", rollAxis="Y", weight=0.7), "rotation": _rotation("X", 35)},
        {**_node("tilted", rollAxis="X", weight=0.2), "rotation": [half, half, 0., 0.]},
        _node("direction only", kind="aim", aimAxis="PositiveY"),
    ]}


def test_aligned_axes_and_misalignment():
    report = vrm_roll.inspect(synthetic_document(), {"twists_deg": [40], "swings_deg": [50]})
    assert report["pass"] is True
    assert report["twists_deg"] == [0, 40] and report["swings_deg"] == [0, 50]
    assert report["roll_constraints"] == 5
    x, y, z, misaligned, tilted = report["constraints"]
    assert [row["axis"] for row in (x, y, z)] == ["X", "Y", "Z"]
    assert [row["node_index"] for row in (x, y, z)] == [3, 4, 5]
    assert [row["weight"] for row in (x, y, z)] == [0.3, 0.5, 1.]
    for row in (x, y, z):
        assert row["max_error_deg"] == pytest.approx(0.)
        assert row["max_twist_when_only_swung_deg"] == pytest.approx(0.)
        assert row["source_parent"] == "driver parent"
        assert row["parent"] == "helper parent"
    assert misaligned["rest_differs_deg"] == pytest.approx(35)
    assert misaligned["max_twist_when_only_swung_deg"] > 15
    assert tilted["max_error_deg"] > 0


def test_matrix_follows_gltf_column_order_and_scaled_bases():
    document = synthetic_document()
    # Z のまわりに 90 度、軸ごとに倍率 2、3、4、平行移動 (8, 9, 10)。
    document["nodes"][1]["matrix"] = [0, 2, 0, 0, -3, 0, 0, 0, 0, 0, 4, 0, 8, 9, 10, 1]
    matrix = vrm_roll.inspect(document, {})
    del document["nodes"][1]["matrix"]
    document["nodes"][1]["rotation"] = _rotation("Z", 90)
    rotation = vrm_roll.inspect(document, {})
    for matrix_row, rotation_row in zip(matrix["constraints"], rotation["constraints"]):
        for key in ("rest_differs_deg", "max_error_deg", "max_twist_when_only_swung_deg"):
            assert matrix_row[key] == pytest.approx(rotation_row[key], abs=1e-10)


def test_missing_roll_and_malformed_input_are_reported():
    document = {"nodes": [{"name": "only"}]}
    missing = vrm_roll.inspect(document, {})
    assert missing["pass"] is None and missing["roll_constraints"] == 0
    document["nodes"].extend([
        _node("missing source", source=99, rollAxis="Y"),
        _node("self constraint", source=2, rollAxis="Y"),
        _node("wrong axis", source=0, rollAxis=["Y"]),
        _node("invalid weight", source=0, rollAxis="Y", weight=float("nan")),
        {**_node("bad orientation", source=0, rollAxis="Y"), "rotation": [0, 0, 0, 0]},
    ])
    invalid = vrm_roll.inspect(document, {})
    assert invalid["pass"] is False
    assert len(invalid["failures"]) == invalid["roll_constraints"] == 5
    assert all(row["unread"] for row in invalid["constraints"])
    json.dumps(invalid, allow_nan=False)


@pytest.mark.parametrize("parameters", [
    {"swings_deg": []}, {"swings_deg": [-1]}, {"twists_deg": [-180]},
    {"twists_deg": [True]}, {"tolerance_deg": float("inf")}, {"tolerance_deg": None},
])
def test_rejects_unsuitable_probe_parameters(parameters):
    with pytest.raises(ToolError):
        vrm_roll.inspect(synthetic_document(), parameters)


def test_recipe_writes_report_and_keeps_value_judgement_optional(tmp_path):
    data = _glb(synthetic_document())
    source = tmp_path / "synthetic.vrm"
    source.write_bytes(data)
    recipe = tmp_path / "recipe.json"
    step = {"id": "roll", "operation": "check.vrm_roll", "inputs": {"vrm": "$inputs.fixture"},
            "outputs": {"report": "roll.json"}}
    body = {"schema_version": "model3d-recipe/0.1", "name": "probe",
            "inputs": {"fixture": {"path": source.name}}, "steps": [step]}

    def run(parameters):
        step["parameters"] = parameters
        atomic_json(recipe, body)
        result = executor.run_recipe(recipe, tmp_path / "runs")["steps"]["roll"]
        return result, json.loads(Path(result["artifacts"]["report"]).read_text(encoding="utf-8"))

    result, measured = run({})
    assert result["status"] == "succeeded"
    assert measured["pass"] is True
    result, checked = run({"tolerance_deg": 0.1})
    assert result["status"] == "needs_review" and checked["pass"] is False
    result, unchecked = run({"tolerance_deg": 0.1, "report_only": True})
    assert result["status"] == "succeeded" and unchecked["pass"] is False
    assert source.read_bytes() == data
