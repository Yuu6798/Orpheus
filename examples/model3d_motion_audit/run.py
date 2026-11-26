"""Make a fixed-camera reference comparison and a four-angle pose sheet."""

import argparse
import json
import math
from pathlib import Path

from model3d import contracts, executor
from model3d.io import ToolError, atomic_json, safe_path
from model3d.ops.plan_ops import retime_track
from mv_pipeline import sheet
from mv_pipeline.io import PipelineError


ANGLES = [0, -45, 45, 90]


def recipe_for(blend, armature, hips, start, motion_fps, fps, frames, times, resolution, location, target, lens):
    track = retime_track(fps, motion_fps, start, frames)
    for frame in track["frames"]:
        frame["camera"] = {"location": location, "target": target, "lens": lens}

    rig = {"bone_prefix": ""}
    if armature is not None:
        rig["armature"] = armature
    draw = {**rig, "passes": ["shade"], "resolution": resolution, "camera": {"sensor_height": 36},
            "dancer": {"bones": [hips]}}
    spot_frames = [start + round(t * motion_fps) for t in times]
    spot_track = contracts.validate("stage-track", {"schema_version": "model3d-stage-track/0.1", "fps": 1,
                                                       "frames": [{"T": j, "f": f} for j, f in enumerate(spot_frames, 1)]})
    pass_outputs = {"passes": "passes.json", "cameras": "cameras.json", "report": "report.json"}
    recipe = {"schema_version": "model3d-recipe/0.1", "name": "motion-audit",
              "inputs": {"blend": {"path": str(blend)}, "front_track": {"path": "front_track.json"},
                         "spot_track": {"path": "spot_track.json"}},
              "steps": [
                  {"id": "front", "operation": "render.passes", "inputs": {"blend": "$inputs.blend", "track": "$inputs.front_track"},
                   "parameters": draw, "outputs": pass_outputs},
                  {"id": "joints", "operation": "motion.bone_path", "inputs": {"blend": "$inputs.blend", "track": "$inputs.spot_track"},
                   "parameters": {**rig, "bones": [hips]}, "outputs": {"path": "path.json"}},
                  {"id": "scout", "operation": "plan.scout", "inputs": {"bones": "$steps.joints.path"},
                   "parameters": {"aim": hips, "times": times, "angles": ANGLES,
                                  "dance": {"frame0": start, "fps": motion_fps},
                                  "dist": math.dist(location, target), "lens": lens,
                                  "camera": {"resolution": resolution, "sensor_height": 36}},
                   "outputs": {"track": "track.json", "cameras": "cameras.json", "report": "angles.json"}},
                  {"id": "angles", "operation": "render.passes",
                   "inputs": {"blend": "$inputs.blend", "track": "$steps.scout.track", "cameras": "$steps.scout.cameras"},
                   "parameters": draw, "outputs": pass_outputs},
              ]}
    return recipe, contracts.validate("stage-track", track), spot_track


def complete_shade(path, count):
    passes = contracts.load("passes", path)
    shade = passes["passes"]["shade"]
    if shade["missing"] or [row["T"] for row in shade["frames"]] != list(range(1, count + 1)):
        raise ToolError(f"shade パスに必要なコマが揃っていません: {path}")
    directory = safe_path(passes["directory"], shade["frames"][0]["file"]).parent
    frames = [{**row, "file": safe_path(passes["directory"], row["file"]).relative_to(directory).as_posix()}
              for row in shade["frames"]]
    return contracts.validate("frames", {"schema_version": "model3d-frames/0.1", "directory": str(directory),
                                          "fps": passes["fps"], "resolution": passes["resolution"],
                                          "frames": frames, "missing": []})


def run_recipe(path, output, tools=None):
    result = executor.run_recipe(path, output, tools=tools)
    if result["status"] != "succeeded":
        raise ToolError(f"{path.name} の実行が {result['status']} になりました: {result['error']}。記録: {output}")
    return result["steps"]


def run(args):
    if any(not p.is_file() for p in (args.blend, args.reference, args.blender)):
        raise ToolError("blend・reference・blender のファイルを確認してください")
    if not all(math.isfinite(v) and v > 0 for v in (args.fps, args.motion_fps, args.lens)):
        raise ToolError("fps・motion-fps・lens は正の有限値にしてください")
    if args.frames < 1 or args.start < 1 or any(v < 2 or v % 2 for v in args.resolution):
        raise ToolError("frames・start は正の整数、resolution は 2 以上の偶数にしてください")
    if any(not math.isfinite(v) for v in (*args.location, *args.target)) or args.location == args.target:
        raise ToolError("location・target は異なる有限の 3 次元座標にしてください")
    if any(not math.isfinite(v) or not 0 <= v <= (args.frames - 1) / args.fps for v in args.at):
        raise ToolError("at は監査区間内の時刻（秒）にしてください")

    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    recipe, front, spots = recipe_for(args.blend.resolve(), args.armature, args.hips_bone, args.start, args.motion_fps,
                                     args.fps, args.frames, args.at, args.resolution, args.location, args.target, args.lens)
    atomic_json(out / "front_track.json", front)
    atomic_json(out / "spot_track.json", spots)
    atomic_json(out / "audit.recipe.json", recipe)
    steps = run_recipe(out / "audit.recipe.json", out / "runs", {"blender": str(args.blender.resolve())})
    front_frames = complete_shade(steps["front"]["artifacts"]["passes"], args.frames)
    angle_frames = complete_shade(steps["angles"]["artifacts"]["passes"], len(args.at) * len(ANGLES))
    atomic_json(out / "front_frames.json", front_frames)
    atomic_json(out / "assemble.recipe.json", {"schema_version": "model3d-recipe/0.1", "name": "motion-audit-video",
                "inputs": {"frames": {"path": "front_frames.json"}},
                "steps": [{"id": "assemble", "operation": "render.assemble", "inputs": {"frames": "$inputs.frames"},
                           "outputs": {"video": "front.mp4"}}]})
    assembly = run_recipe(out / "assemble.recipe.json", out / "runs")
    video = assembly["assemble"]["artifacts"]["video"]
    comparison = sheet.side_by_side([args.reference.resolve(), video], out / "comparison.mp4", audio=0,
                                    labels=("Reference", "Motion"), fps=args.fps)
    sheet.build(angle_frames["directory"], out / "angles.png", fps=1, every=1, columns=len(ANGLES))
    result = {"comparison": comparison["video"], "compared_frames": comparison["frames"], "rendered_frames": args.frames,
              "reference_shorter": comparison["frames"] < args.frames, "angle_sheet": str(out / "angles.png"),
              "angle_plan": steps["scout"]["artifacts"]["report"], "angles_deg": ANGLES}
    atomic_json(out / "audit.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("blend", type=Path)
    parser.add_argument("reference", type=Path, help="reference video trimmed to start at the audit's first pose")
    parser.add_argument("--blender", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="new audit directory")
    parser.add_argument("--start", type=int, default=1, help="first .blend frame")
    parser.add_argument("--frames", type=int, required=True, help="number of output frames")
    parser.add_argument("--motion-fps", type=float, required=True)
    parser.add_argument("--fps", type=float, default=30)
    parser.add_argument("--at", type=float, nargs="+", required=True, help="spot-check times in seconds from the audit start")
    parser.add_argument("--resolution", type=int, nargs=2, default=[360, 640])
    parser.add_argument("--location", type=float, nargs=3, default=[0.0, -3.5, 1.0], help="fixed camera position in metres")
    parser.add_argument("--target", type=float, nargs=3, default=[0.0, 0.0, 1.0], help="fixed camera aim in metres")
    parser.add_argument("--lens", type=float, default=35)
    parser.add_argument("--armature", help="armature object name")
    parser.add_argument("--hips-bone", default="mixamorig:Hips", help="full name of the hips bone")
    args = parser.parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False))
    except (OSError, ToolError, PipelineError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
