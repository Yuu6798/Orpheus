from types import SimpleNamespace

import pytest

from model3d import ops
from model3d.blender_adapter import vrm_hair_colliders as hair
from model3d.io import ToolError


def spring(name, *group_uuids, joints=2):
    return SimpleNamespace(vrm_name=name, joints=[None] * joints,
                           collider_groups=[SimpleNamespace(collider_group_uuid=uuid) for uuid in group_uuids])


@pytest.mark.parametrize(("mode", "back"), [("back", [1]), ("all", [0, 1]), ("off", None)])
def test_only_hair_springs_receive_groups(mode, back):
    springs = [spring("Hair_front_L"), spring("hair_BACK_R"), spring("Wrap_0"), spring("Hair_stub", joints=1)]
    targets = hair.hair_targets(springs, hair.check_settings({"back_shield": mode}))
    assert targets[hair.SHOULDERS] == [0, 1]
    assert targets.get(hair.BACK_SHIELD) == back


def test_discovery_and_parameter_validation(tmp_path):
    op = ops.get("vrm.hair_colliders")
    assert op.inputs == ("blend",)
    assert op.outputs == ("blend", "report")
    assert op.capability["tools"] == ["blender"]
    for parameters in ({"radius_scale": float("nan")}, {"back_shield": True}, {"bones": {"upper_arm": "bad"}},
                       {"hair_prefix": "Strand_", "back_prefix": "Hair_back_"}):
        with pytest.raises(ToolError):
            op.function({"blend": "source.blend"}, parameters,
                        {"blend": "out.blend", "report": "report.json"}, SimpleNamespace(step_dir=tmp_path))


def test_reuse_compatible_groups_and_reject_reference_from_sleeve():
    spec = {"bone": "shoulder.L", "radius": 0.058, "offset": (0, 0.01, 0), "tail": (0, 0.095, 0)}
    collider = SimpleNamespace(uuid="collider", node=SimpleNamespace(bone_name=spec["bone"]), shape_type="Capsule",
                               shape=SimpleNamespace(capsule=SimpleNamespace(**spec)),
                               extensions=SimpleNamespace(vrmc_spring_bone_extended_collider=SimpleNamespace(enabled=False)))
    group = SimpleNamespace(vrm_name=hair.SHOULDERS, uuid="hair-group",
                            colliders=[SimpleNamespace(collider_uuid=collider.uuid)])
    spring_bone = SimpleNamespace(colliders=[collider], collider_groups=[group],
                                  springs=[spring("Hair_back_R", group.uuid), spring("Sleeve_0", "arm")])
    result = hair.add_groups(None, None, spring_bone, {hair.SHOULDERS: [spec]}, {hair.SHOULDERS: [0]})
    assert result[hair.SHOULDERS] == {"uuid": group.uuid, "created": False, "capsules": [spec],
                                       "springs": ["Hair_back_R"], "attached": []}
    spring_bone.springs[1].collider_groups.append(SimpleNamespace(collider_group_uuid=group.uuid))
    with pytest.raises(ValueError, match="Sleeve_0"):
        hair.add_groups(None, None, spring_bone, {hair.SHOULDERS: [spec]}, {hair.SHOULDERS: [0]})



def test_addon_must_assign_group_uuid_before_linking():
    group = SimpleNamespace(uuid="")
    spring_bone = SimpleNamespace(colliders=[], collider_groups=[], springs=[spring("Hair_back_R")],
                                  add_collider_group=lambda: group)
    with pytest.raises(ValueError, match="UUID"):
        hair.add_groups(None, None, spring_bone, {hair.SHOULDERS: []}, {hair.SHOULDERS: [0]})
    assert not spring_bone.springs[0].collider_groups
