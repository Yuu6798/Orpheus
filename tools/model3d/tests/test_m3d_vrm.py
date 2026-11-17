"""VRM のキャラを動かす工程: 骨の対応表の作り方（Blender なし）と、工程の受け渡し（代役の Blender）を確かめる。
本物の Blender 5.2.4 と VRM Add-on での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
from pathlib import Path

import numpy as np
import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import retarget, vrm_apply_motion, vrm_bake_springs, vrm_motion_rig
from model3d.io import ToolError, atomic_json
from model3d.semantic import MIXAMO_ROLES


def _charakit_like_assignment(skip=()):
    """きゃらくる風の骨の名前（upper_arm.L など）で、VRM 1.0 の人型の骨を全部割り当てたもの。親指の付け根は metacarpal。"""
    names = {"hips": "hips", "spine": "spine", "chest": "chest", "upper_chest": "upperChest", "neck": "neck", "head": "head",
             "left_eye": "eye.L", "right_eye": "eye.R"}
    for side, s in (("left", "L"), ("right", "R")):
        for limb in ("shoulder", "upper_arm", "lower_arm", "hand", "upper_leg", "lower_leg", "foot", "toes"):
            names[f"{side}_{limb}"] = f"{limb}.{s}"
        for finger, segments in (("thumb", ("metacarpal", "proximal", "distal")), ("index", None), ("middle", None), ("ring", None),
                                 ("little", None)):
            bones = ("proximal", "intermediate", "distal")
            for vrm, bone in zip(segments or bones, bones):
                names[f"{side}_{finger}_{vrm}"] = f"{finger}_{bone}.{s}"
    return {role: bone for role, bone in names.items() if role not in skip}


def test_the_table_covers_what_retarget_needs_with_names_model3d_knows():
    table = vrm_motion_rig.VRM_TO_MIXAMO
    assert len(table) == 52 and len(set(table.values())) == 52
    assert set(table.values()) <= set(MIXAMO_ROLES)                       # どれも役割の分かる Mixamo の名前
    assert set(retarget.REQUIRED_BONES) <= set(table.values())
    assert vrm_motion_rig.RETARGET_REQUIRED == retarget.REQUIRED_BONES     # 台本が持つ写しが同じ
    assert list(table)[:6] == ["hips", "spine", "chest", "upper_chest", "neck", "head"]
    assert table["left_thumb_metacarpal"] == "LeftHandThumb1" and table["left_thumb_distal"] == "LeftHandThumb3"
    assert table["right_little_intermediate"] == "RightHandPinky2" and table["right_toes"] == "RightToeBase"


def test_bone_map_from_a_vrm_assignment():
    mapping, unmapped, missing = vrm_motion_rig.bone_map(_charakit_like_assignment())
    assert len(mapping) == 52 and missing == []
    assert mapping["upperChest"] == "mixamorig:Spine2" and mapping["upper_arm.L"] == "mixamorig:LeftArm"
    assert mapping["thumb_proximal.L"] == "mixamorig:LeftHandThumb1"      # VRM 1.0 の metacarpal に割り当てられた骨
    assert mapping["thumb_intermediate.R"] == "mixamorig:RightHandThumb2"
    assert unmapped == {"left_eye": "eye.L", "right_eye": "eye.R"}         # 目は付け替えない
    assert list(mapping)[:7] == ["hips", "spine", "chest", "upperChest", "neck", "head", "shoulder.L"]
    assert list(mapping)[29] == "shoulder.R"                                # 左の 8 本と指 15 本の後に右
    custom, _, _ = vrm_motion_rig.bone_map(_charakit_like_assignment(), prefix="rig_")
    assert custom["hips"] == "rig_Hips"


def test_bone_map_reports_missing_bones_and_rejects_double_assignment():
    _, _, missing = vrm_motion_rig.bone_map(_charakit_like_assignment(skip=("upper_chest", "left_lower_arm")))
    assert missing == ["Spine2", "LeftForeArm"]
    mapping, _, missing = vrm_motion_rig.bone_map(_charakit_like_assignment(skip=("left_index_distal", "right_toes")))
    assert missing == [] and len(mapping) == 50                             # 指とつま先は無くてもよい
    doubled = _charakit_like_assignment()
    doubled["chest"] = "spine"
    with pytest.raises(ValueError, match="2 つの役割"):
        vrm_motion_rig.bone_map(doubled)
    with pytest.raises(ValueError, match="知らない人型の骨の役割"):
        vrm_motion_rig.bone_map({**_charakit_like_assignment(), "tail": "tail"})
    with pytest.raises(ValueError, match="目・あご"):                   # あごを頭の骨に割り当てたもの
        vrm_motion_rig.bone_map({**_charakit_like_assignment(), "jaw": "head"})


def test_bone_map_keeps_bones_that_already_have_mixamo_names():
    assignment = {role: "mixamorig:" + name for role, name in vrm_motion_rig.VRM_TO_MIXAMO.items()}
    mapping, _, missing = vrm_motion_rig.bone_map(assignment)
    assert missing == [] and all(old == new for old, new in mapping.items())
    assert vrm_motion_rig.rename_conflicts(list(mapping), mapping) == []


def test_rename_conflicts_catch_names_that_blender_would_shift():
    mapping = {"hips": "mixamorig:Hips", "spine": "mixamorig:Spine"}
    assert vrm_motion_rig.rename_conflicts(["hips", "spine", "tail"], mapping) == []
    assert vrm_motion_rig.rename_conflicts(["hips", "spine", "mixamorig:Hips"], mapping) == ["mixamorig:Hips"]
    assert vrm_motion_rig.rename_conflicts(["a", "b"], {"a": "b", "b": "c"}) == ["b"]          # 付け替える前の別の骨の名前


def test_parse_args_defaults():
    args = vrm_motion_rig.parse_args(["--character", "c.blend", "--out-blend", "o.blend", "--bone-map", "m.json", "--report", "r.json"])
    assert args.bone_prefix == "mixamorig:" and args.armature == "" and args.skip_meshes == "" and args.skip == ""


# 代役の Blender: -- の後ろの引数から出力を書き、受け取った argv を報告に残す
STAND_IN = r'''
import json, sys
import numpy as np
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"working copy")
json.dump({"schema_version": "model3d-vrm-bone-map/0.1", "prefix": "mixamorig:", "map": {"hips": "mixamorig:Hips"}},
          open(value("--bone-map"), "w"))
if "--skip" in args:
    with open(value("--skip"), "wb") as stream:
        np.savez(stream, Hair=np.ones(4, bool))
json.dump({"schema_version": "model3d-vrm-motion-rig/0.1", "armature": "Rig", "mapped": 52, "renamed": 52,
           "unmapped": {"left_eye": "eye.L"}, "removed_scenes": ["Work"], "argv": sys.argv[1:]}, open(value("--report"), "w"))
'''


def _recipe(tmp_path, parameters, outputs=None):
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    folder = tmp_path / "ext" / "user_default" / "vrm"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "blender_manifest.toml").write_text('version = "4.10.5"\n', encoding="utf-8")
    step = {"id": "rig", "operation": "vrm.motion_rig", "inputs": {"character": "$inputs.avatar"}, "parameters": parameters,
            "outputs": outputs or {"blend": "rig.blend", "bone_map": "bone_map.json", "report": "rig.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-rig",
                                           "inputs": {"avatar": {"path": "avatar.blend"}}, "steps": [step]})
    return tmp_path / "recipe.json"


def test_motion_rig_passes_the_character_and_enables_the_vrm_addon(tmp_path):
    from model3d import executor
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    recipe = _recipe(tmp_path, {"blender": str(blender), "blender_extensions": "ext", "skip_meshes": ["Hair"], "armature": "Rig"},
                     {"blend": "rig.blend", "bone_map": "bone_map.json", "report": "rig.json", "skip": "skip.npz"})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    step = result["steps"]["rig"]
    argv = json.loads(Path(step["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "vrm_motion_rig.py"
    assert "bl_ext.user_default.vrm" in argv[argv.index("--python-expr") + 1]          # VRM Add-on を有効にする前置き
    assert argv.index("--python-expr") < argv.index("--python")
    tail = argv[argv.index("--") + 1:]
    assert tail[tail.index("--bone-prefix") + 1] == "mixamorig:" and tail[tail.index("--armature") + 1] == "Rig"
    assert tail[tail.index("--skip-meshes") + 1] == "Hair" and Path(tail[tail.index("--skip") + 1]).name == "skip.npz"
    assert Path(tail[tail.index("--character") + 1]).name == "avatar.blend"
    assert Path(tail[tail.index("--bone-map") + 1]) == Path(step["artifacts"]["bone_map"])
    assert Path(tail[tail.index("--out-blend") + 1]) == Path(step["artifacts"]["blend"])
    with np.load(step["artifacts"]["skip"]) as data:
        assert data["Hair"].all()
    observation = step["observations"][0]
    assert observation["kind"] == "vrm_motion_rig" and observation["renamed"] == 52 and observation["unmapped"] == ["left_eye"]
    assert observation["addons"][0]["version"] == "4.10.5" and observation["blender_version"] == "5.2.4"
    assert executor.run_recipe(recipe, tmp_path / "runs")["steps"]["rig"]["reused_from"] is None     # 毎回実行する


@pytest.mark.parametrize("parameters, outputs, message", [
    ({"wobble": 1}, None, "知らないパラメーター"),
    ({}, None, "Blender"),
    ({"skip_meshes": ["Hair"]}, None, "skip_meshes と outputs の skip"),
    ({}, {"blend": "o.blend", "bone_map": "m.json", "report": "r.json", "skip": "s.npz"}, "skip_meshes と outputs の skip"),
    ({"skip_meshes": "Hair"}, None, "skip_meshes"),
    ({"skip_meshes": ["Hair,Body"]}, None, "skip_meshes"),
    ({"armature": 3}, None, "armature"),
    ({"bone_prefix": 3}, None, "bone_prefix"),
    ({"addons": "bl_ext.user_default.vrm"}, None, "並び"),
    ({}, {"blend": "o.blend", "report": "r.json"}, "bone_map"),
])
def test_motion_rig_rejects_bad_settings(tmp_path, parameters, outputs, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "avatar.blend").write_bytes(b"vrm character")
    if message != "Blender":
        parameters = {"blender": str(blender), **parameters}
    declared = {k: tmp_path / v for k, v in (outputs or {"blend": "o.blend", "bone_map": "m.json", "report": "r.json"}).items()}

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("vrm.motion_rig").function({"character": tmp_path / "avatar.blend"}, parameters, declared, Context())


# ---------- 動きを元の VRM へ戻す・揺れものを焼く ----------

def test_inverse_map_and_renamed_paths():
    inverse = vrm_apply_motion.inverse_map({"prefix": "mixamorig:", "map": {"upper_arm.L": "mixamorig:LeftArm", "hips": "mixamorig:Hips"}})
    assert inverse == {"mixamorig:LeftArm": "upper_arm.L", "mixamorig:Hips": "hips"}
    path = 'pose.bones["mixamorig:LeftArm"].rotation_quaternion'
    assert vrm_apply_motion.bone_of(path) == "mixamorig:LeftArm"
    assert vrm_apply_motion.renamed_path(path, inverse) == 'pose.bones["upper_arm.L"].rotation_quaternion'
    assert vrm_apply_motion.renamed_path('pose.bones["hair_01"].location', inverse) == 'pose.bones["hair_01"].location'
    assert vrm_apply_motion.bone_of('["model3d_points_frames"]') is None
    # 名前の中の " と \ は Blender が \" と \\ にして書く。戻すときもそう書く
    quoted = 'pose.bones["a\\"]b\\\\c"].location'
    assert vrm_apply_motion.bone_of(quoted) == 'a"]b\\c' and vrm_bake_springs.bone_span(quoted) == ('a"]b\\c', len(quoted) - 10)
    assert vrm_apply_motion.renamed_path(quoted, {'a"]b\\c': 'x"y'}) == 'pose.bones["x\\"y"].location'
    assert vrm_apply_motion.bone_of('pose.bones["broken') is None


def test_both_scripts_carry_the_same_data_path_reader():
    import inspect
    for name in ("bone_span", "use_scene_of"):
        assert inspect.getsource(getattr(vrm_apply_motion, name)) == inspect.getsource(getattr(vrm_bake_springs, name)).replace(
            "vrm_apply_motion.py", "vrm_bake_springs.py"), name
    from model3d.blender_adapter import vrm_export
    assert inspect.getsource(vrm_export.use_scene_of) == inspect.getsource(vrm_bake_springs.use_scene_of)   # 書き出しも同じ写し


def test_use_scene_of_switches_to_a_scene_that_holds_the_armature():
    from types import SimpleNamespace as N
    rig = N(name="Rig")
    work, main = N(name="Work", objects={"Cam"}), N(name="Main", objects={"Rig", "Body"})
    win = N(scene=work)

    class Context:                                        # bpy.context.scene は窓のシーン（Blender と同じ）
        window_manager = N(windows=[win])
        window = None

        @property
        def scene(self):
            return win.scene

    bpy = N(data=N(scenes=[work, main]), context=Context())
    assert vrm_apply_motion.use_scene_of(bpy, rig) is main and win.scene is main
    assert vrm_bake_springs.use_scene_of(bpy, rig) is main                         # もう今のシーンならそのまま
    with pytest.raises(SystemExit, match="入ったシーン"):
        vrm_apply_motion.use_scene_of(N(data=N(scenes=[work]), context=Context()), rig)
    win.scene = work
    Context.window_manager = N(windows=[])
    with pytest.raises(SystemExit, match="窓"):
        vrm_apply_motion.use_scene_of(bpy, rig)


def test_reset_pose_matches_the_retarget_reset_and_keeps_scale():
    from types import SimpleNamespace as N
    bone = N(location=(0, 0.1, 0), rotation_quaternion=(0.9, 0.1, 0, 0), rotation_euler=(0.3, 0, 0), rotation_axis_angle=(0, 0, 1, 0),
             scale=(1.2, 1, 1))
    assert vrm_apply_motion.reset_pose(bone) is True
    assert bone.location == (0.0, 0.0, 0.0) and bone.rotation_quaternion == (1.0, 0.0, 0.0, 0.0) and bone.rotation_euler == (0.0, 0.0, 0.0)
    assert bone.scale == (1.2, 1, 1)                                  # 大きさは motion.retarget と同じく変えない
    assert vrm_apply_motion.reset_pose(bone) is False                 # もう基本の姿勢


def test_fps_settings_keep_the_effective_rate():
    assert vrm_apply_motion.fps_settings(24) == (24, 1.0)
    whole, base = vrm_apply_motion.fps_settings(29.97)
    assert whole == 30 and abs(whole / base - 29.97) < 1e-9
    with pytest.raises(ValueError):
        vrm_apply_motion.fps_settings(0.2)


def test_spring_steps_follow_the_private_script_rounding():
    # 24 fps と 1/60 秒: 経過秒 ÷ 刻み は奇数コマでちょうど .5 になる。台本と同じ式（round の偶数への丸めを含む）
    step = 1.0 / 60.0
    steps = [vrm_bake_springs.target_steps(frame, 1, 24.0, step) for frame in range(1, 8)]
    assert steps == [int(round((frame - 1) / 24.0 / step)) for frame in range(1, 8)]
    assert steps[:4] == [0, 2, 5, 8] and vrm_bake_springs.target_steps(720, 1, 24.0, step) == 1798


def test_spring_overrides_are_checked():
    good = {"_note": "メモ", "sleeve": {"stiffness": 0.2, "gravity_power": 0.9}, "_capsule_radius": {"upper_leg": 0.085},
            "_colliders": [{"bone": "lower_leg.L", "offset": [0, 0, 0], "tail": [0, 0.3, 0], "radius": 0.05, "groups": ["legs"]}]}
    assert vrm_bake_springs.check_overrides(good) is good
    collider = good["_colliders"][0]
    for bad, message in (([], "オブジェクト"), ({"_capsule_radius": {"leg": -1}}, "_capsule_radius"),
                         ({"_colliders": [{"bone": "x"}]}, "_colliders"), ({"skirt": 1}, "skirt"),
                         ({"_colliders": [{**collider, "offset": ["0", 0, 0]}]}, "_colliders"),
                         ({"_colliders": [{**collider, "groups": [3]}]}, "_colliders"),
                         ({"skirt": {"stiffness": "0.2"}}, "skirt"), ({"skirt": {"gravity_dir": [0, -1]}}, "skirt")):
        with pytest.raises(ValueError, match=message):
            vrm_bake_springs.check_overrides(bad)
    assert vrm_bake_springs.keys_of(" skirt, ,sleeve") == ["skirt", "sleeve"]


# 代役の Blender: 台本の名前で出力を書き分け、受け取った argv を報告に残す
STAND_IN_MOTION = r'''
import json, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
script = sys.argv[sys.argv.index("--python") + 1]
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"moved")
report = {"armature": "Rig", "frames": [1, 3], "argv": sys.argv[1:], "script": script}
if script.endswith("vrm_apply_motion.py"):
    report.update(curves=10, curves_renamed=8, fps=24.0)
else:
    report.update(joints=5, still_joints=2, steps=4, overridden={"sleeve": {"stiffness": 0.2}})
json.dump(report, open(value("--report"), "w"))
'''


def _motion_recipe(tmp_path, apply_parameters, bake_parameters, overrides=True):
    for name in ("avatar.blend", "motion.blend"):
        (tmp_path / name).write_bytes(b"blend")
    atomic_json(tmp_path / "bone_map.json", {"prefix": "mixamorig:", "map": {"hips": "mixamorig:Hips"}})
    atomic_json(tmp_path / "overrides.json", {"sleeve": {"stiffness": 0.2}})
    inputs = {"avatar": {"path": "avatar.blend"}, "motion": {"path": "motion.blend"}, "map": {"path": "bone_map.json"},
              "overrides": {"path": "overrides.json"}}
    bake_inputs = {"blend": "$steps.apply.blend", **({"overrides": "$inputs.overrides"} if overrides else {})}
    steps = [{"id": "apply", "operation": "vrm.apply_motion", "parameters": apply_parameters,
              "inputs": {"character": "$inputs.avatar", "motion": "$inputs.motion", "bone_map": "$inputs.map"},
              "outputs": {"blend": "dance.blend", "report": "apply.json"}},
             {"id": "springs", "operation": "vrm.bake_springs", "inputs": bake_inputs, "parameters": bake_parameters,
              "outputs": {"blend": "springs.blend", "report": "springs.json"}}]
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-motion", "inputs": inputs,
                                           "steps": steps})
    return tmp_path / "recipe.json"


def test_apply_motion_and_bake_springs_pass_their_inputs_to_blender(tmp_path):
    from model3d import executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN_MOTION))
    recipe = _motion_recipe(tmp_path, {"blender": blender, "fps": 24},
                            {"blender": blender, "no_spring": ["skirt"], "settle_s": 1.5, "rate": 120})
    result = executor.run_recipe(recipe, tmp_path / "runs")
    assert result["status"] == "succeeded", result
    apply_report = json.loads(Path(result["steps"]["apply"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    tail = apply_report["argv"][apply_report["argv"].index("--") + 1:]
    assert Path(apply_report["script"]).name == "vrm_apply_motion.py" and "--python-expr" in apply_report["argv"]
    assert tail[tail.index("--fps") + 1] == "24.0" and Path(tail[tail.index("--bone-map") + 1]).name == "bone_map.json"
    assert Path(tail[tail.index("--motion") + 1]).name == "motion.blend"
    bake_report = json.loads(Path(result["steps"]["springs"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    tail = bake_report["argv"][bake_report["argv"].index("--") + 1:]
    assert Path(bake_report["script"]).name == "vrm_bake_springs.py"
    assert Path(tail[tail.index("--blend") + 1]).name == "dance.blend"                       # 前の工程の出力
    assert tail[tail.index("--no-spring") + 1] == "skirt" and tail[tail.index("--settle") + 1] == "1.5"
    assert tail[tail.index("--rate") + 1] == "120.0" and tail[tail.index("--addon-module") + 1] == "bl_ext.user_default.vrm"
    assert Path(tail[tail.index("--overrides") + 1]).name == "overrides.json"
    observation = result["steps"]["springs"]["observations"][0]
    assert observation["kind"] == "vrm_bake_springs" and observation["joints"] == 5 and observation["overridden"] == ["sleeve"]
    assert result["steps"]["apply"]["observations"][0]["curves_renamed"] == 8
    # 揺れものの計算を読むアドオンは、挙げたもののうち名前の最後が vrm のもの
    recipe = _motion_recipe(tmp_path, {"blender": blender}, {"blender": blender, "addons": ["rigify", "bl_ext.blender_org.vrm"]},
                            overrides=False)
    result = executor.run_recipe(recipe, tmp_path / "runs")
    bake_report = json.loads(Path(result["steps"]["springs"]["artifacts"]["report"]).read_text(encoding="utf-8"))
    tail = bake_report["argv"][bake_report["argv"].index("--") + 1:]
    assert tail[tail.index("--addon-module") + 1] == "bl_ext.blender_org.vrm" and "--overrides" not in tail and "--fps" not in tail


@pytest.mark.parametrize("operation, parameters, message", [
    ("vrm.apply_motion", {"wobble": 1}, "知らないパラメーター"),
    ("vrm.apply_motion", {"fps": 0}, "fps"),
    ("vrm.apply_motion", {"armature": 1}, "armature"),
    ("vrm.bake_springs", {"rate": 0}, "rate"),
    ("vrm.bake_springs", {"settle_s": -1}, "settle_s"),
    ("vrm.bake_springs", {"no_spring": "skirt"}, "no_spring"),
    ("vrm.bake_springs", {"overrides_bad": True}, "overrides"),
])
def test_motion_operations_reject_bad_settings(tmp_path, operation, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN_MOTION)
    for name in ("avatar.blend", "motion.blend"):
        (tmp_path / name).write_bytes(b"blend")
    atomic_json(tmp_path / "bone_map.json", {"map": {"hips": "mixamorig:Hips"}})
    atomic_json(tmp_path / "overrides.json", {"_colliders": [{"bone": "x"}]})
    inputs = {"character": tmp_path / "avatar.blend", "motion": tmp_path / "motion.blend", "bone_map": tmp_path / "bone_map.json",
              "blend": tmp_path / "avatar.blend"}
    if parameters.pop("overrides_bad", False):
        inputs["overrides"] = tmp_path / "overrides.json"

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get(operation).function(inputs, {"blender": str(blender), **parameters},
                                {"blend": tmp_path / "o.blend", "report": tmp_path / "r.json"}, Context())


# ---------- VRM を書き出す・中身を確かめる ----------

def _glb(document, blob=b""):
    """JSON と BIN のチャンクを持つ GLB のバイト列（4 バイトにそろえる）。"""
    import struct
    text = json.dumps(document).encode("utf-8")
    text += b" " * (-len(text) % 4)
    blob += b"\x00" * (-len(blob) % 4)
    body = struct.pack("<I4s", len(text), b"JSON") + text + (struct.pack("<I4s", len(blob), b"BIN\x00") + blob if blob else b"")
    return struct.pack("<4sII", b"glTF", 2, 12 + len(body)) + body


def _vrm_document(**changes):
    from model3d import vrm
    bones = {name: {"node": index} for index, name in enumerate(vrm.REQUIRED_HUMAN_BONES)}
    nodes = [{} for _ in range(20)]
    nodes[1] = {"mesh": 0}
    document = {"asset": {"version": "2.0", "generator": "test"}, "nodes": nodes,
                "meshes": [{"name": "Body", "primitives": [{"targets": [{}, {}]}], "extras": {"targetNames": ["smile", "blink"]}}],
                "materials": [{"extensions": {"VRMC_materials_mtoon": {}}}, {}],
                "bufferViews": [{"buffer": 0, "byteLength": 8}], "images": [{"name": "skin", "mimeType": "image/jpeg", "bufferView": 0}],
                "extensions": {"VRMC_vrm": {"specVersion": "1.0", "humanoid": {"humanBones": bones},
                                            "meta": {"name": "テスト", "authors": ["誰か"], "licenseUrl": "https://vrm.dev/licenses/1.0/",
                                                     "avatarPermission": "onlyAuthor", "thumbnailImage": 0},
                                            "expressions": {"preset": {"happy": {"morphTargetBinds": [{"node": 1, "index": 0, "weight": 0.5}]},
                                                                       "blink": {}}}},
                               "VRMC_springBone": {"specVersion": "1.0", "colliders": [{"node": 16}], "colliderGroups": [{"colliders": [0]}],
                                                   "springs": [{"name": "hair", "joints": [{"node": 17}, {"node": 18}]}]}}}
    for key, value in changes.items():
        document["extensions"]["VRMC_vrm"][key] = value
    return document


def test_check_vrm_reports_the_contents_of_a_vrm():
    from model3d import vrm
    report = vrm.inspect(_glb(_vrm_document(), b"12345678"))
    assert report["pass"] is True and report["failures"] == []
    assert report["spec_version"] == "1.0" and report["human_bones"] == 15 and report["missing_human_bones"] == []
    assert report["meta"]["name"] == "テスト" and "thumbnailImage" not in report["meta"]
    assert report["materials"] == 2 and report["mtoon_materials"] == 1
    assert report["images"] == [{"name": "skin", "mime_type": "image/jpeg", "bytes": 8}]
    assert report["springs"] == ["hair"] and report["spring_joints"] == 2 and report["colliders"] == 1
    assert report["expressions_bound"] == ["happy"] and report["bin_bytes"] == 8
    assert report["morph_targets"] == [{"mesh": 0, "name": "Body", "targets": 2, "names": ["smile", "blink"]}]
    assert report["expressions"] == {"preset": {"happy": [{"node": 1, "mesh": "Body", "index": 0, "target": "smile", "weight": 0.5}],
                                                "blink": []}, "custom": {}}


def test_check_vrm_reads_the_shape_binds_and_fails_on_bad_shape_numbers():
    from model3d import vrm
    document = _vrm_document()
    document["meshes"].append({"name": "Face", "primitives": [{"targets": [{}]}, {"targets": [{}]}]})   # 形の名前が無いメッシュ
    document["meshes"].append({"name": "Plain", "primitives": [{}]})                                     # 形の無いメッシュ
    document["nodes"][2], document["nodes"][3] = {"mesh": 1}, {"mesh": 2}
    document["extensions"]["VRMC_vrm"]["expressions"] = {
        "preset": {"blink": {"morphTargetBinds": [{"node": 1, "index": 1, "weight": 1.0}, {"node": 2, "index": 0, "weight": 1.0}]}},
        "custom": {"wink": {"morphTargetBinds": [{"node": 1, "index": 2}]}, "nose": {"morphTargetBinds": [{"node": 4, "index": 0}]},
                   "flat": {"morphTargetBinds": [{"node": 3, "index": 0}]}, "odd": {"morphTargetBinds": [{"node": 1, "index": True}]}}}
    report = vrm.inspect(_glb(document))
    assert report["morph_targets"] == [{"mesh": 0, "name": "Body", "targets": 2, "names": ["smile", "blink"]},
                                       {"mesh": 1, "name": "Face", "targets": 1, "names": None}]
    assert report["expressions"]["preset"]["blink"] == [{"node": 1, "mesh": "Body", "index": 1, "target": "blink", "weight": 1.0},
                                                        {"node": 2, "mesh": "Face", "index": 0, "target": None, "weight": 1.0}]
    text = " / ".join(report["failures"])
    assert report["pass"] is False and len(report["failures"]) == 4
    assert "表情 wink の形の割り当ての index 2 が node 1 のメッシュの形の数（2）の外です" in text
    assert "表情 nose の形の割り当ての node 4 にメッシュがありません" in text
    assert "表情 flat の形の割り当ての index 0 が node 3 のメッシュの形の数（0）の外です" in text
    assert "表情 odd の形の割り当ての index True" in text                     # 真偽値は番号にしない


def test_check_vrm_fails_on_what_vrm_1_requires():
    from model3d import vrm
    document = _vrm_document(specVersion="0.0", meta={"name": "x"})
    del document["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["leftHand"]
    document["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["chest"] = {"node": 99}
    document["extensions"]["VRMC_vrm"]["humanoid"]["humanBones"]["neck"] = {"node": 0}
    document["extensions"]["VRMC_springBone"]["springs"][0]["joints"].append({"node": -1})
    document["extensions"]["VRMC_springBone"]["springs"][0]["colliderGroups"] = [3]
    document["extensions"]["VRMC_springBone"]["springs"][0]["center"] = 999
    document["extensions"]["VRMC_springBone"]["colliderGroups"][0]["colliders"] = [0, 7]
    document["extensions"]["VRMC_vrm"]["meta"]["thumbnailImage"] = 5
    document["extensions"]["VRMC_vrm"]["firstPerson"] = {"meshAnnotations": [{"node": 99, "type": "auto"}]}
    document["extensions"]["VRMC_vrm"]["expressions"] = {"custom": {"blush": {"materialColorBinds": [{"material": 9}]}}}
    report = vrm.inspect(_glb(document))
    text = " / ".join(report["failures"])
    assert report["pass"] is False and report["missing_human_bones"] == ["leftHand"]
    for part in ("specVersion", "authors", "licenseUrl", "leftHand", "chest", "neck", "hair", "当たりの組 0", "colliderGroups",
                 "center", "thumbnailImage", "meshAnnotations", "表情 blush"):
        assert part in text, part
    assert vrm.inspect(_glb({"asset": {"version": "2.0"}}))["failures"] == ["VRMC_vrm（VRM 1.0 の拡張）がありません"]
    with pytest.raises(ToolError, match="GLB"):
        vrm.inspect(b"not a glb at all")


def test_meta_of_reads_name_version_and_authors():
    from model3d import vrm
    assert vrm.meta_of(_glb(_vrm_document())) == {"name": "テスト", "version": "", "authors": ["誰か"]}
    with pytest.raises(ToolError, match="メタ"):
        vrm.meta_of(_glb({"asset": {"version": "2.0"}}))


def test_check_vrm_operation_follows_the_check_rules(tmp_path):
    from model3d.ops import get
    (tmp_path / "good.vrm").write_bytes(_glb(_vrm_document()))
    (tmp_path / "bad.vrm").write_bytes(_glb(_vrm_document(meta={})))

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    run = get("check.vrm").function
    assert run({"vrm": tmp_path / "good.vrm"}, {}, {"report": tmp_path / "r.json"}, Context()).review is False
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["pass"] is True
    result = run({"vrm": tmp_path / "bad.vrm"}, {}, {"report": tmp_path / "r.json"}, Context())
    assert result.review is True and any("name" in item for item in result.unresolved)
    with pytest.raises(ToolError, match="不合格"):
        run({"vrm": tmp_path / "bad.vrm"}, {"strict": True}, {}, Context())
    assert run({"vrm": tmp_path / "bad.vrm"}, {"report_only": True}, {}, Context()).review is False
    with pytest.raises(ToolError, match="知らないパラメーター"):
        run({"vrm": tmp_path / "good.vrm"}, {"wobble": 1}, {}, Context())


def test_texture_names_are_safe_and_unique():
    from model3d.blender_adapter import vrm_export
    taken = set()
    assert vrm_export.texture_name("lower_body texture.png", taken) == "lower_body_texture_png.jpg"
    assert vrm_export.texture_name("lower_body texture.png", taken) == "lower_body_texture_png_2.jpg"
    assert vrm_export.texture_name("顔", taken) == "image.jpg"


# 代役の Blender: 書き出しの台本の引数を読み、テストの VRM を書く
STAND_IN_EXPORT = r'''
import json, shutil, sys
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
open(value("--out-blend"), "wb").write(b"rest pose")
shutil.copyfile(SOURCE, value("--out-vrm"))
meta = json.load(open(value("--meta"), encoding="utf-8")) if "--meta" in args else None
json.dump({"armature": "Rig", "vrm_bytes": 10, "meta_copied": meta, "textures": [{"image": "skin"}] if "--jpeg-quality" in args else [],
           "argv": sys.argv[1:]}, open(value("--report"), "w", encoding="utf-8"), ensure_ascii=False)
'''


def test_vrm_export_writes_the_vrm_and_checks_it(tmp_path):
    from model3d import executor
    (tmp_path / "made.vrm").write_bytes(_glb(_vrm_document()))
    (tmp_path / "source.vrm").write_bytes(_glb(_vrm_document(meta={"name": "元", "version": "1", "authors": ["作者"],
                                                                     "licenseUrl": "https://vrm.dev/licenses/1.0/"})))
    (tmp_path / "asset.blend").write_bytes(b"character")
    blender = stand_in_executable(tmp_path, "blender", f"SOURCE = {str(tmp_path / 'made.vrm')!r}\n" + STAND_IN_EXPORT)
    step = {"id": "export", "operation": "vrm.export", "inputs": {"blend": "$inputs.asset", "meta_from": "$inputs.source"},
            "parameters": {"blender": str(blender), "jpeg_quality": 95},
            "outputs": {"vrm": "asset.vrm", "blend": "asset.blend", "report": "export.json", "check": "check.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-export",
                                           "inputs": {"asset": {"path": "asset.blend"}, "source": {"path": "source.vrm"}},
                                           "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    artifacts = result["steps"]["export"]["artifacts"]
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    tail = report["argv"][report["argv"].index("--") + 1:]
    assert report["meta_copied"] == {"name": "元", "version": "1", "authors": ["作者"]}        # 元の VRM のメタを渡した
    assert tail[tail.index("--jpeg-quality") + 1] == "95" and Path(tail[tail.index("--textures") + 1]).name == "textures"
    assert "bl_ext.user_default.vrm" in report["argv"][report["argv"].index("--python-expr") + 1]
    assert json.loads(Path(artifacts["check"]).read_text(encoding="utf-8"))["pass"] is True
    observation = result["steps"]["export"]["observations"][0]
    assert observation["check_pass"] is True and observation["human_bones"] == 15 and observation["textures"] == 1
    # メタも JPEG も挙げなければ、台本にはどちらも渡さない
    plain = {**step, "id": "plain", "inputs": {"blend": "$inputs.asset"}, "parameters": {"blender": str(blender)}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-export",
                                           "inputs": {"asset": {"path": "asset.blend"}}, "steps": [plain]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    argv = json.loads(Path(result["steps"]["plain"]["artifacts"]["report"]).read_text(encoding="utf-8"))["argv"]
    assert result["status"] == "succeeded" and not {"--meta", "--jpeg-quality", "--textures"} & set(argv)
    # 書き出した VRM が検査に通らなければ needs_review（strict なら失敗）
    (tmp_path / "made.vrm").write_bytes(_glb(_vrm_document(meta={})))
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["status"] == "needs_review"
    step["parameters"]["strict"] = True
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "vrm-export",
                                           "inputs": {"asset": {"path": "asset.blend"}, "source": {"path": "source.vrm"}},
                                           "steps": [step]})
    assert executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")["status"] == "failed"


@pytest.mark.parametrize("parameters, message", [
    ({"jpeg_quality": 101}, "jpeg_quality"),
    ({"jpeg_quality": 9.5}, "jpeg_quality"),
    ({"wobble": True}, "知らないパラメーター"),
])
def test_vrm_export_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", "SOURCE = ''\n" + STAND_IN_EXPORT)

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("vrm.export").function({"blend": tmp_path / "a.blend"}, {"blender": str(blender), **parameters},
                                   {"vrm": tmp_path / "a.vrm", "blend": tmp_path / "o.blend", "report": tmp_path / "r.json"},
                                   Context())
