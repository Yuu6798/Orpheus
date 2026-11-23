"""部位を色分けして描く診断の絵（render.part_colors）: 色の決め方とカメラの置き方（Blender なし）、工程の受け渡しと描いた絵の一覧
（代役の Blender）。本物の Blender 5.2.4 での確かめは docs/model3d.md（CI には Blender が無い）。"""
import json
import math
import os
from pathlib import Path
import subprocess

import pytest

from m3d_helpers import stand_in_executable
from model3d.blender_adapter import part_colors as pc
from model3d.io import ToolError, atomic_json


def test_group_colors_take_the_first_matching_prefix():
    colors = [["hips", [0.9, 0.1, 0.1]], ["upper_leg.L", [0.3, 1.0, 0.3]], ["upper_leg", [0.0, 0.5, 0.0]]]
    assert pc.group_color("upper_leg.L", colors, [0.6, 0.6, 0.6]) == [0.3, 1.0, 0.3]
    assert pc.group_color("upper_leg.R", colors, [0.6, 0.6, 0.6]) == [0.0, 0.5, 0.0]
    assert pc.group_color("spine", colors, [0.6, 0.6, 0.6]) == [0.6, 0.6, 0.6]


def test_camera_frames_the_height_from_the_yaw():
    camera = pc.merged({})["camera"]
    location, rotation, distance = pc.camera_place(camera)
    half = math.atan(18.0 / 85.0)
    assert distance == pytest.approx(0.75 / math.tan(half))           # 縦 1.5 m が画面の高さに入る
    assert location == pytest.approx((0.0, -distance, 0.75)) and rotation == pytest.approx((math.pi / 2, 0.0, 0.0))
    side, rotation, _ = pc.camera_place({**camera, "yaw": 90.0, "target": [0.2, -0.1]})
    assert side == pytest.approx((0.2 + distance, -0.1, 0.75)) and rotation[2] == pytest.approx(math.pi / 2)
    scaled, _, _ = pc.camera_place(camera, unit=0.01)                 # 1 Blender 単位 = 1 cm のシーン
    assert scaled == pytest.approx((0.0, -distance * 100, 75.0))


def test_settings_are_merged_and_files_named_by_T():
    settings = pc.merged({"frames": [13, 307.5], "camera": {"yaw": 45.0}})
    assert settings["camera"] == {**pc.DEFAULTS["camera"], "yaw": 45.0} and settings["format"] == "jpeg"
    assert pc.frame_file(3, "jpeg") == "f0003.jpg" and pc.frame_file(12, "png") == "f0012.png"


# 代役の Blender: 決めた絵（4 × 4 画素）と報告を書く
STAND_IN = r'''
import json, os, sys
from PIL import Image
if "--version" in sys.argv:
    print("Blender 5.2.4 LTS")
    sys.exit(0)
args = sys.argv[sys.argv.index("--") + 1:]
def value(flag):
    return args[args.index(flag) + 1]
settings = json.load(open(value("--settings"), encoding="utf-8"))
folder = value("--frames-dir")
fmt = settings.get("format", "jpeg")
frames = []
for T, f in enumerate(settings["frames"], start=1):
    name = f"f{T:04d}." + ("png" if fmt == "png" else "jpg")
    Image.new("RGB", (4, 4), (10 * T, 0, 0)).save(os.path.join(folder, name))
    frames.append({"T": T, "f": f, "file": name})
    if "poses" in settings:
        frames[-1]["pose"] = settings["poses"][T - 1]["name"]
report = {"schema_version": "model3d-part-colors/0.1", "fps": 24.0, "frames": frames, "drawn": ["Body", "Skirt"],
          "camera": {"distance_m": 3.5}, "x_settings": settings, "x_argv": sys.argv[1:]}
json.dump(report, open(value("--report"), "w", encoding="utf-8"))
'''

COLORS = {"bone_meshes": ["Body"], "bone_colors": [["hips", [0.9, 0.1, 0.1]], ["upper_leg", [0.3, 1.0, 0.3]]],
          "object_colors": {"Skirt": [1.0, 0.85, 0.0]}}


POSES = [
    {"name": "rest", "rotations": []},
    {"name": "turn", "rotations": [{"bone": "hinge", "axis": "Y", "degrees": 90, "space": "armature"}]},
    {"name": "rest again", "rotations": []},
]


@pytest.mark.parametrize("pose_samples", [False, True])
def test_part_colors_writes_the_frames_list(tmp_path, pose_samples):
    from model3d import contracts, executor
    blender = str(stand_in_executable(tmp_path, "blender", STAND_IN))
    (tmp_path / "dance.blend").write_bytes(b"dance")
    sampling = {"poses": POSES} if pose_samples else {"frames": [13, 307, 613.5]}
    step = {"id": "diag", "operation": "render.part_colors", "inputs": {"blend": "$inputs.dance"},
            "parameters": {"blender": blender, **sampling, **COLORS, "show": ["skirt_proxy"], "hide": ["Hair"],
                           "camera": {"yaw": 90, "height": 1.2, "center": 0.6, "target": [0.1, 0]}, "format": "png",
                           "resolution": [540, 720], "cavity": False, "world": [0.1, 0.1, 0.1]},
            "outputs": {"frames": "frames.json", "report": "diag.json"}}
    atomic_json(tmp_path / "recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "diag",
                                           "inputs": {"dance": {"path": "dance.blend"}}, "steps": [step]})
    result = executor.run_recipe(tmp_path / "recipe.json", tmp_path / "runs")
    assert result["status"] == "succeeded", result
    artifacts = result["steps"]["diag"]["artifacts"]
    manifest = contracts.load("frames", artifacts["frames"])
    expected_frames = [1, 2, 3] if pose_samples else sampling["frames"]
    assert [(f["T"], f["f"], f["file"]) for f in manifest["frames"]] == [
        (index, f, f"f{index:04d}.png") for index, f in enumerate(expected_frames, start=1)]
    assert manifest["resolution"] == [540, 720] and manifest["fps"] == 24.0 and manifest["missing"] == []
    assert all((Path(manifest["directory"]) / f["file"]).is_file() for f in manifest["frames"])
    report = json.loads(Path(artifacts["report"]).read_text(encoding="utf-8"))
    if pose_samples:
        assert all("pose" not in frame for frame in manifest["frames"])
        assert [item["pose"] for item in report["frames"]] == [pose["name"] for pose in POSES]
    settings = report["x_settings"]
    assert settings["camera"] == {"yaw": 90.0, "height": 1.2, "center": 0.6, "target": [0.1, 0.0]}
    assert settings["bone_colors"] == COLORS["bone_colors"] and settings["show"] == ["skirt_proxy"] and settings["cavity"] is False
    argv = report["x_argv"]
    assert Path(argv[argv.index("--python") + 1]).name == "part_colors.py" and "--python-expr" not in argv
    assert result["steps"]["diag"]["observations"][0]["frames"] == 3
    # 描いた絵の一覧は check.frames で比べられる（同じ並びの 2 回を比べると全部同じ）
    from model3d import checks
    assert checks.compare_frames(checks.frame_files(artifacts["frames"]), checks.frame_files(artifacts["frames"]))["frames"]


@pytest.mark.skipif(not os.getenv("MODEL3D_BLENDER"), reason="set MODEL3D_BLENDER to the Blender executable")
def test_blender_part_colors_renders_poses_and_resets(tmp_path):
    from PIL import Image
    from model3d import contracts, executor

    script = tmp_path / "fixture.py"
    script.write_text("""
import sys
import bpy

bpy.ops.object.select_all(action="SELECT")
bpy.ops.object.delete(use_global=False)
rig = bpy.data.objects.new("Rig", bpy.data.armatures.new("Rig"))
bpy.context.scene.collection.objects.link(rig)
bpy.context.view_layer.objects.active = rig
bpy.ops.object.mode_set(mode="EDIT")
rig.data.edit_bones.new("hinge").tail = (0, 1, 0)
bpy.ops.object.mode_set(mode="OBJECT")
bpy.ops.mesh.primitive_cube_add(size=0.3, location=(0.5, 0, 0))
part = bpy.context.object
part.name = "Part"
part.vertex_groups.new(name="hinge").add(list(range(8)), 1, "REPLACE")
part.modifiers.new("Armature", "ARMATURE").object = rig
bpy.ops.wm.save_as_mainfile(filepath=sys.argv[-1])
""", encoding="utf-8")
    blender = os.environ["MODEL3D_BLENDER"]
    blend = tmp_path / "part.blend"
    subprocess.run([blender, "--background", "--factory-startup", "--disable-autoexec", "--python-exit-code", "1",
                    "--python", str(script), "--", str(blend)], check=True, capture_output=True, timeout=60)
    original = blend.read_bytes()
    recipe = tmp_path / "recipe.json"
    atomic_json(recipe, {"schema_version": "model3d-recipe/0.1", "name": "parts",
                         "inputs": {"part": {"path": blend.name}}, "steps": [{"id": "draw", "operation": "render.part_colors",
                         "inputs": {"blend": "$inputs.part"}, "parameters": {"blender": blender, "poses": POSES,
                         "object_colors": {"Part": [0.8, 0.1, 0.1]}, "resolution": [128, 128], "format": "png",
                         "cavity": False, "camera": {"height": 2, "center": 0}}, "outputs": {"frames": "frames.json", "report": "draw.json"}}]})
    run = executor.run_recipe(recipe, tmp_path / "runs")
    assert run["status"] == "succeeded", run
    manifest = contracts.load("frames", run["steps"]["draw"]["artifacts"]["frames"])
    def center(path):
        from statistics import mean
        with Image.open(path) as image:
            pixels = image.convert("RGB").load()
            points = [(x, y) for x in range(image.width) for y in range(image.height)
                      if pixels[x, y][0] > 1.3 * pixels[x, y][1]]
            return tuple(mean(coord) for coord in zip(*points))

    rest, turn, reset = [center(Path(manifest["directory"]) / f["file"]) for f in manifest["frames"]]
    assert rest[0] > turn[0] + 20 and turn[1] > rest[1] + 20
    assert reset == pytest.approx(rest, abs=0.25)
    assert blend.read_bytes() == original


@pytest.mark.parametrize("parameters, message", [
    ({"wobble": 1}, "知らないパラメーター"),
    ({"frames": []}, "frames"),
    ({"frames": ["13"]}, "frames"),
    ({"frames": [1], "poses": POSES}, "poses"),
    ({"poses": []}, "poses"),
    ({"frames": [1], "bone_meshes": ["Body"]}, "bone_colors"),
    ({"frames": [1], "bone_colors": [["hips", [1, 0]]]}, "bone_colors"),
    ({"frames": [1], "bone_colors": [["", [1, 0, 0]]]}, "bone_colors"),
    ({"frames": [1], "object_colors": {"Skirt": [1, -0.1, 0]}}, "object_colors"),
    ({"frames": [1], "show": ["A"], "hide": ["A"]}, "show と hide"),
    ({"frames": [1], "format": "tiff"}, "format"),
    ({"frames": [1], "quality": 0}, "quality"),
    ({"frames": [1], "antialiasing": "4"}, "antialiasing"),
    ({"frames": [1], "cavity": "on"}, "cavity"),
    ({"frames": [1], "camera": {"height": 0}}, "height"),
    ({"frames": [1], "camera": {"target": [0, 0, 0]}}, "target"),
    ({"frames": [1], "camera": {"pitch": 10}}, "camera"),
    ({"frames": [1], "resolution": [8, 8]}, "resolution"),
])
def test_part_colors_rejects_bad_settings(tmp_path, parameters, message):
    from model3d.ops import get
    blender = stand_in_executable(tmp_path, "blender", STAND_IN)
    (tmp_path / "dance.blend").write_bytes(b"dance")

    class Context:
        tools, step_dir, recipe_dir = {}, tmp_path, tmp_path

    with pytest.raises(ToolError, match=message):
        get("render.part_colors").function({"blend": tmp_path / "dance.blend"}, {"blender": str(blender), **parameters},
                                           {"frames": tmp_path / "f.json", "report": tmp_path / "r.json"}, Context())
