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
BLEND = [{"bone": "lower_arm_twist.L", "constraint": "Copy Rotation", "type": "COPY_ROTATION", "mute": False},
         {"bone": "lower_arm_twist.L", "constraint": "Copy Rotation.001", "type": "COPY_ROTATION", "mute": False},   # 2 つ目は書かれない
         {"bone": "lower_arm_swing.L", "constraint": "Muted", "type": "DAMPED_TRACK", "mute": True},              # ミュートは書かれない
         {"bone": "lower_arm_swing.L", "constraint": "Damped Track", "type": "DAMPED_TRACK", "mute": False},
         {"bone": "spine", "constraint": "Copy Rotation", "type": "COPY_ROTATION", "mute": False},
         {"bone": "leg.L", "constraint": "IK", "type": "IK", "mute": False}]                                     # VRM の拘束でない種類


def test_constraints_left_out_of_the_vrm_are_listed():
    exported = vrm.inspect(glb({"asset": {"version": "2.0"}, "nodes": NODES}))["structure"]["node_constraints"]
    assert [(row["node"], row["kind"], row["source"]) for row in exported] == [
        ("lower_arm_twist.L", "roll", "hand.L"), ("lower_arm_swing.L", "aim", "hand.L"), ("spine", "rotation", "lower_arm.L")]
    left = ve.left_out(BLEND, exported)
    assert [(row["bone"], row["constraint"]) for row in left] == [("lower_arm_twist.L", "Copy Rotation.001"),
                                                                  ("lower_arm_swing.L", "Muted"), ("leg.L", "IK")]
    assert ve.left_out(BLEND, []) == BLEND                       # 何も書き出さなければ、全部が書き出されなかった拘束


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
    assert observation["constraints_exported"] == 3
    assert [row["constraint"] for row in observation["constraints_not_exported"]] == ["Copy Rotation.001", "Muted", "IK"]
    assert result["status"] == "needs_review"          # この代役の VRM は VRM 1.0 の拡張が無いので検査に通らない（拘束は合否に使わない）
