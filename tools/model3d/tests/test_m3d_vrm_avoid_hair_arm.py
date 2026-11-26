import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("model3d")

from model3d.blender_adapter import launcher, vrm_avoid_hair_arm as hair
from model3d.io import ToolError
from model3d.ops import get


def settings():
    return {"chains": [{"object": "Hair", "bones": ["Root", "Middle", "Tip"]}], "colliders": [{"object": "Sleeve"}]}


@pytest.mark.parametrize("invalid", [
    {"chains": [{"object": "Hair", "bones": ["Root", "Root", "Tip"]}]},
    {"chains": [{"object": "Hair", "bones": ["Root", "Middle", "Tip"], "direction": True}]},
    {"chains": [{"object": "Hair", "bones": ["Root", "Middle", "Tip"], "groups": ["Root"], "outward_ratio": float("nan")}]},
    {"colliders": [{"object": "Sleeve"}, {"object": "Sleeve"}]},
    {"outline_modifiers": {"Absent": ["Outline"]}},
    {"extra_frames": [False]},
    {"angle_step_deg": 15, "max_angle_deg": 10},
])
def test_settings_reject_ambiguous_and_invalid_selectors(invalid):
    with pytest.raises(ValueError):
        hair.check_settings(settings() | invalid)


def test_sample_range_and_bounded_angle_search():
    assert hair.sample_frames(-2, 8, 3, [0]) == [-2, 0, 1, 4, 7, 8]
    with pytest.raises(ValueError):
        hair.sample_frames(-2, 8, 3, [9])
    seen = []

    def measure(angle):
        seen.append(angle)
        return {4: 12, 8: 5, 12: 0}[angle]

    assert hair.best_angle(measure, 10, 4, 56) == (12, 0)
    assert seen == [4, 8, 12]
    assert hair.best_angle(lambda angle: 20, 10, 4, 6) == (0, 10)
    assert hair.best_angle(lambda angle: 10 - angle, 10, 4, 6) == (6, 4)


def test_smoothing_keeps_probed_peaks_and_handles_short_sequences():
    result = hair.smooth_angles([-1, 0, 1, 2, 3], [-1, 1, 3], [0, 8, 0], 1, 5)
    assert result[2] == pytest.approx(8)
    assert np.all(result >= [0, 4, 8, 4, 0])
    assert result[0] > 0
    assert np.all(result <= 8)
    assert hair.smooth_angles([1], [1], [8], 5, 5).tolist() == [8]


def test_outline_state_is_restored_even_on_failure():
    automatic = SimpleNamespace(name="Automatic", type="NODES", node_group=SimpleNamespace(name=hair.OUTLINE_NODE_GROUP), show_viewport=True)
    explicit = SimpleNamespace(name="Explicit", type="NODES", node_group=None, show_viewport=False)
    other = SimpleNamespace(name="Other", type="NODES", node_group=None, show_viewport=True)

    class Modifiers(list):
        def get(self, name):
            return next((item for item in self if item.name == name), None)

    obj = SimpleNamespace(name="Hair", modifiers=Modifiers([automatic, explicit, other]))
    with pytest.raises(RuntimeError), hair.hide_outlines({"Hair": obj}, {"Hair": ["Explicit"]}):
        assert not automatic.show_viewport and not explicit.show_viewport and other.show_viewport
        raise RuntimeError("failure")
    assert automatic.show_viewport and not explicit.show_viewport and other.show_viewport


def test_registered_operation_launches_and_reports_remaining_pairs(tmp_path, monkeypatch):
    operation = get("vrm.avoid_hair_arm")
    input_blend, output_blend, report_path = (tmp_path / name for name in ("in.blend", "out.blend", "report.json"))
    context = SimpleNamespace(step_dir=tmp_path, tools={"blender": "fake-blender"})

    def fake_blender(blender, script, blend, args, **kwargs):
        assert blender == "fake-blender"
        assert script.name == "vrm_avoid_hair_arm.py"
        assert args[1] == input_blend and args[5] == output_blend
        passed_settings = json.loads(Path(args[3]).read_text(encoding="utf-8"))
        assert passed_settings == settings() | {"armature": "Rig"}
        assert not kwargs["addons"]
        report_path.write_text(json.dumps({"schema_version": hair.SCHEMA, "armature": "Rig", "frames": [1, 12],
                                           "before_pairs": 7, "after_pairs": 2}), encoding="utf-8")
        return {"blender_version": "5.2.4", "seconds": 1}

    monkeypatch.setattr(launcher, "run_blender", fake_blender)
    inputs, outputs = {"blend": input_blend}, {"blend": output_blend, "report": report_path}
    result = operation.function(inputs, settings() | {"armature": "Rig"}, outputs, context)
    assert result.review and len(result.unresolved) == 1
    assert result.observations[0]["after_pairs"] == 2
    assert not operation.cacheable
    with pytest.raises(ToolError, match="chains"):
        operation.function(inputs, settings() | {"chains": []}, outputs, context)
