"""vrm.export の骨の拘束（VRMC_node_constraint）: .blend の骨の拘束と、書き出した VRM の拘束（check.vrm の作りの一覧）の突き合わせ
（Blender なし）と、工程の観測（代役の Blender）。本物の Blender 5.2.4 と VRM Add-on 4.10.5 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import struct

from m3d_helpers import stand_in_executable
from model3d import vrm
from model3d.blender_adapter import vrm_export as ve
from model3d.io import atomic_json


def glb(document):
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    chunks = struct.pack("<I4s", len(text), b"JSON") + text
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


NODES = [
    {"name": "hand.L"}, {"name": "lower_arm.L"},
    {"name": "lower_arm_twist.L", "extensions": {"VRMC_node_constraint": {"specVersion": "1.0", "constraint": {
        "roll": {"source": 0, "rollAxis": "Y", "weight": 1.0}}}}},
    {"name": "lower_arm_swing.L", "extensions": {"VRMC_node_constraint": {"specVersion": "1.0", "constraint": {
        "aim": {"source": 0, "aimAxis": "PositiveY", "weight": 0.5}}}}},
    {"name": "spine", "extensions": {"VRMC_node_constraint": {"constraint": {"rotation": {"source": 1}}}}},
]


def copy_rotation(bone, name, source, axes="Y", mix="ADD", spaces=("LOCAL", "LOCAL"), inverted=False, mute=False, influence=1.0):
    return {"bone": bone, "constraint": name, "type": "COPY_ROTATION", "mute": mute, "influence": influence, "source": source,
            "axes": axes, "inverted": inverted, "mix": mix, "spaces": list(spaces)}


def damped_track(bone, name, source, track="TRACK_Y", head_tail=0.0, mute=False, influence=1.0):
    return {"bone": bone, "constraint": name, "type": "DAMPED_TRACK", "mute": mute, "influence": influence, "source": source,
            "track": track, "head_tail": head_tail}


BLEND = [copy_rotation("lower_arm_twist.L", "Not roll", "hand.L", mix="REPLACE"),      # 先にある書き出されない形（混ぜ方が違う）
         copy_rotation("lower_arm_twist.L", "Copy Rotation", "hand.L"),
         copy_rotation("lower_arm_twist.L", "Copy Rotation.001", "hand.L"),           # 骨 1 本に 2 つ目は書かれない
         damped_track("lower_arm_swing.L", "Muted", "hand.L", mute=True, influence=0.5),  # ミュートは書かれない
         damped_track("lower_arm_swing.L", "Damped Track", "hand.L", influence=0.5),
         copy_rotation("spine", "Copy Rotation", "lower_arm.L", axes="XYZ"),
         {"bone": "leg.L", "constraint": "IK", "type": "IK", "mute": False, "influence": 1.0, "source": "foot.L"}]   # VRM の拘束でない種類


def exported_rows():
    return vrm.inspect(glb({"asset": {"version": "2.0"}, "nodes": NODES}))["structure"]["node_constraints"]


def test_vrm_form_follows_the_vrm_constraint_shapes():
    assert ve.vrm_form(copy_rotation("b", "c", "s")) == ("roll", "Y")
    assert ve.vrm_form(copy_rotation("b", "c", "s", axes="XYZ")) == ("rotation", None)
    assert ve.vrm_form(damped_track("b", "c", "s", track="TRACK_NEGATIVE_Z")) == ("aim", "NegativeZ")
    for row in (copy_rotation("b", "c", "s", axes="XY"), copy_rotation("b", "c", "s", mix="REPLACE"),
                copy_rotation("b", "c", "s", spaces=("WORLD", "LOCAL")), copy_rotation("b", "c", "s", inverted=True),
                copy_rotation("b", "c", "s", mute=True), damped_track("b", "c", "s", head_tail=0.5), BLEND[-1]):
        assert ve.vrm_form(row) is None


def test_constraints_left_out_of_the_vrm_are_listed():
    exported = exported_rows()
    assert [(row["node"], row["kind"], row["source"], row["axis"]) for row in exported] == [
        ("lower_arm_twist.L", "roll", "hand.L", "Y"), ("lower_arm_swing.L", "aim", "hand.L", "PositiveY"),
        ("spine", "rotation", "lower_arm.L", None)]
    left, unmatched = ve.left_out(BLEND, exported)
    # 書き出されない形の拘束が先にあっても、形・軸・相手・重みが同じ「Copy Rotation」を書き出されたものとする
    assert [(row["bone"], row["constraint"]) for row in left] == [
        ("lower_arm_twist.L", "Not roll"), ("lower_arm_twist.L", "Copy Rotation.001"), ("lower_arm_swing.L", "Muted"), ("leg.L", "IK")]
    assert unmatched == []
    assert ve.left_out(BLEND, []) == (BLEND, [])                    # 何も書き出さなければ、全部が書き出されなかった拘束


def test_vrm_constraints_without_a_matching_blend_constraint_are_reported():
    exported = exported_rows()
    other_weight = [dict(row, influence=0.25) if row["constraint"] == "Damped Track" else row for row in BLEND]
    left, unmatched = ve.left_out(other_weight, exported)
    assert [row["node"] for row in unmatched] == ["lower_arm_swing.L"]          # 重みが違えば同じ拘束とみなさない
    assert ("lower_arm_swing.L", "Damped Track") in [(row["bone"], row["constraint"]) for row in left]
    other_source = [dict(row, source="hand.R") if row["constraint"] == "Copy Rotation" and row["bone"] == "spine" else row for row in BLEND]
    assert [row["node"] for row in ve.left_out(other_source, exported)[1]] == ["spine"]   # 相手が違っても同じ


STAND_IN = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"rest pose")
shutil.copyfile(SOURCE, value("--out-vrm"))
json.dump({"armature": "Rig", "vrm_bytes": 10, "bone_constraints": BONES}, open(value("--report"), "w", encoding="utf-8"))
'''


def test_vrm_export_observes_the_constraints_it_left_out(tmp_path):
    from model3d import executor
    (tmp_path / "made.vrm").write_bytes(glb({"asset": {"version": "2.0"}, "nodes": NODES}))
    (tmp_path / "asset.blend").write_bytes(b"character")
    blender = stand_in_executable(tmp_path, "blender", f"SOURCE = {str(tmp_path / 'made.vrm')!r}\nBONES = {BLEND!r}\n" + STAND_IN)
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-constraints",
                                           "inputs": {"asset": {"path": "asset.blend"}},
                                           "steps": [{"id": "export", "operation": "vrm.export", "inputs": {"blend": "$inputs.asset"},
                                                      "parameters": {"blender": str(blender)},
                                                      "outputs": {"vrm": "a.vrm", "blend": "a.blend", "report": "export.json"}}]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    observation = result["steps"]["export"]["observations"][0]
    assert observation["constraints_exported"] == 3 and observation["constraints_unmatched"] == []
    assert [row["constraint"] for row in observation["constraints_not_exported"]] == ["Not roll", "Copy Rotation.001", "Muted", "IK"]
    assert result["status"] == "needs_review"          # この代役の VRM は VRM 1.0 の拡張が無いので検査に通らない（拘束は合否に使わない）
