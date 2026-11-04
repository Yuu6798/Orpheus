"""A small, inspectable lyric-MV workflow using synthetic fixtures only.

Run from the repository root:
    uv run python examples/workflow/run.py projects/workflow-example

The signal has no singing. The automatic confirmation below is restricted to
known display intervals in this fixture; it is not a vocal alignment result or
a human acceptance decision. Nothing is uploaded or downloaded by this script
(Remotion may fetch its browser on the first render).
"""
import argparse
import json
import sys
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from mv_pipeline import pipeline, validate, workflow
from mv_pipeline.io import PipelineError, atomic_json, read_json, sha256


DURATION = 8.0
FPS = 30
RATE = 48000
TEXT = ("OPEN THE WINDOW", "FOLLOW THE LIGHT")
DISPLAY_INTERVALS = ((0.5, 4.2), (4.2, 7.5))


def step(root, commands, name, argv, action):
    """Record the actual order and its equivalent CLI arguments for inspection."""
    print(f"[{name}]", file=sys.stderr, flush=True)
    result = action()
    commands.append({"step": name, "argv": [str(value) for value in argv]})
    atomic_json(root / "work/example/commands.json", commands)
    return result


def fixtures(root, width, height):
    """Create a pulse signal, fictional text and geometric images at runtime."""
    folder = root / "work/example"
    folder.mkdir(parents=True, exist_ok=True)
    t = np.arange(int(DURATION * RATE)) / RATE
    pulse = np.zeros_like(t)
    click_t = np.arange(int(0.08 * RATE)) / RATE
    click = 0.25 * np.sin(2 * np.pi * 1200 * click_t) * np.exp(-click_t / 0.012)
    for seconds in np.arange(0.5, DURATION, 0.5):
        start = int(seconds * RATE)
        pulse[start:start + len(click)] += click
    left = pulse + 0.025 * np.sin(2 * np.pi * 220 * t)
    right = pulse + 0.025 * np.sin(2 * np.pi * 330 * t)
    audio = folder / "pulse.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(RATE)
        stream.writeframes((np.stack([left, right], axis=1) * 32767).astype("<i2").tobytes())
    lyrics = folder / "lyrics.txt"
    lyrics.write_text("\n".join(TEXT) + "\n", encoding="utf-8")
    (root / "images").mkdir()
    assets = []
    for index, background in enumerate(((16, 30, 56), (53, 34, 53)), 1):
        image = Image.new("RGB", (width, height), background)
        draw = ImageDraw.Draw(image)
        box = (int(width * .25), int(height * .15), int(width * .75), int(height * .7))
        draw.rectangle(box, fill=(79, 126, 161) if index == 1 else (224, 170, 91))
        draw.line((width // 2, box[1], width // 2, box[3]), fill=background, width=max(2, width // 80))
        draw.line((box[0], height * .4, box[2], height * .4), fill=background, width=max(2, width // 80))
        if index == 2:
            draw.polygon([(width * .48, height * .45), (width * .75, height * .7),
                          (width * .95, height * .95)], fill=(139, 99, 69))
        path = root / "images" / f"fixture-{index}.png"
        image.save(path)
        assets.append({"asset_id": f"image-{index:03d}", "path": path.relative_to(root).as_posix(),
                       "sha256": sha256(path), "width": width, "height": height,
                       "source": "imported", "prompt": None, "reference_asset_ids": [],
                       "generator": None, "generation_params": {"synthetic_fixture": True}})
    asset_file = folder / "assets-import.json"
    atomic_json(asset_file, {"schema_version": "0.1", "assets": assets})
    return audio, lyrics, asset_file


def direct(analysis, context):
    """Use a lyric boundary as the intent, then select a nearby detected beat."""
    render = context["render"]
    fps, total = render["fps"], render["duration_frames"]
    target = analysis["lyrics"][1]["start_s"]
    beats = [value for value in analysis["beats_s"] if 0 < round(value * fps) < total]
    if not beats:
        raise PipelineError("No detected beat is available for the example cut; inspect analysis.json")
    beat = min(beats, key=lambda value: abs(value - target))
    cut = round(beat * fps)
    scenes = []
    for index, (start, end, motion) in enumerate(((0, cut, "slow_push"), (cut, total, "pan_right")), 1):
        ids = [row["id"] for row in analysis["lyrics"]
               if row["start_s"] < end / fps and row["end_s"] > start / fps]
        scenes.append({"id": f"scene-{index:03d}", "start_frame": start, "end_frame": end,
                       "lyric_ids": ids, "asset_id": f"image-{index:03d}", "prompt": "",
                       "motion": motion, "transition_in": {"type": "cut", "duration_frames": 0},
                       "intent": "Approach the geometric window" if index == 1 else
                                 "Follow the light; cut near the second lyric on a detected beat"})
    story = {"schema_version": "0.1", "storyboard_id": "synthetic-workflow-example",
             "analysis_id": analysis["analysis_id"], "analysis_sha256": context["analysis_sha256"],
             "audio_sha256": context["audio_sha256"], "render": render,
             "world": {"description": "Synthetic pulse and geometric fixtures; no singing or real-song evaluation.",
                       "reference_asset_id": None}, "scenes": scenes}
    decision = {"synthetic_fixture": True, "requested_lyric_boundary_s": target,
                "detected_beat_s": beat, "cut_frame": cut, "cut_s": cut / fps,
                "beat_rounding_error_s": cut / fps - beat,
                "caption_policy": "Keep confirmed display intervals even when a caption crosses the cut.",
                "scene_lyric_ids": {scene["id"]: scene["lyric_ids"] for scene in scenes}}
    return story, decision


def prepare(root, width=320, height=180):
    """Prepare a new project and preserve unknown, estimated and reviewed states."""
    root = Path(root).resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise PipelineError("Use a new or empty output directory; the example never replaces existing work")
    if width <= 0 or height <= 0 or width % 2 or height % 2:
        raise PipelineError("Width and height must be positive even integers")
    root.mkdir(parents=True, exist_ok=True)
    commands = []
    planned = step(root, commands, "workflow-plan", ["mv", "workflow", "plan", root, "--purpose", "lyric-mv"],
                   lambda: workflow.create_plan(root, "lyric-mv"))
    plan = Path(planned["plan"])
    audio, lyrics, assets = fixtures(root, width, height)
    step(root, commands, "init", ["mv", "init", root, "--audio", audio, "--lyrics", lyrics,
                                  "--duration", DURATION, "--width", width, "--height", height],
         lambda: pipeline.initialize(root, audio, lyrics, duration=DURATION, fps=FPS, width=width, height=height))
    evidence = root / "work/example"
    atomic_json(evidence / "01-unknown.json", read_json(root / "analysis.json"))
    step(root, commands, "analyze", ["mv", "analyze", root], lambda: pipeline.analyze(root))
    analysis = read_json(root / "analysis.json")
    estimates = {"audio_sha256": analysis["audio"]["sha256"], "offset_s": 0.0,
                 "tool": {"name": "synthetic-fixture-estimates", "version": "1", "model": None},
                 "lyrics": [{"id": row["id"], "text": row["text"], "start_s": start + .12,
                             "end_s": end - .12, "timing_source": "interpolated"}
                            for row, (start, end) in zip(analysis["lyrics"], DISPLAY_INTERVALS)]}
    estimate_file = evidence / "alignment-estimates.json"
    atomic_json(estimate_file, estimates)
    step(root, commands, "import-alignment", ["mv", "import-alignment", root, estimate_file],
         lambda: pipeline.import_alignment(root, estimate_file))
    atomic_json(evidence / "02-unreviewed.json", read_json(root / "analysis.json"))
    timings = {"audio_sha256": analysis["audio"]["sha256"],
               "lyrics": [{"id": row["id"], "text": row["text"], "start_s": start, "end_s": end}
                          for row, (start, end) in zip(analysis["lyrics"], DISPLAY_INTERVALS)]}
    timing_file = evidence / "fixture-timings.json"
    atomic_json(timing_file, timings)
    atomic_json(evidence / "fixture-review.json", {
        "synthetic_fixture": True, "contains_singing": False, "human_review": "not_performed",
        "accepted": False,
        "basis": "Known display intervals for this generated fixture, not measured vocal timestamps.",
        "warning": "For real music, review alignment against the audio before applying timing edits."})
    step(root, commands, "fixture-confirmation", ["mv", "apply-edits", root, timing_file],
         lambda: pipeline.import_edits(root, timing_file))
    atomic_json(evidence / "03-fixture-confirmed.json", read_json(root / "analysis.json"))
    brief = step(root, commands, "export-brief", ["mv", "export-brief", root], lambda: pipeline.export_brief(root))
    story, decision = direct(read_json(brief / "analysis.json"), read_json(brief / "context.json"))
    story_file = evidence / "storyboard-proposal.json"
    atomic_json(story_file, story)
    atomic_json(evidence / "direction.json", decision)
    step(root, commands, "import-storyboard", ["mv", "import-storyboard", root, story_file],
         lambda: pipeline.import_storyboard(root, story_file))
    step(root, commands, "import-assets", ["mv", "import-assets", root, assets],
         lambda: pipeline.import_assets(root, assets))
    step(root, commands, "validate", ["mv", "validate", root], lambda: validate.bundle(root))
    return {"project": str(root), "plan": str(plan), "synthetic_fixture": True,
            "contains_singing": False, "accepted": False, "commands": commands}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path, help="new or empty output directory")
    parser.add_argument("--prepare-only", action="store_true", help="stop before rendering; workflow remains incomplete")
    parser.add_argument("--width", type=int, default=320)
    parser.add_argument("--height", type=int, default=180)
    args = parser.parse_args(argv)
    try:
        result = prepare(args.project, args.width, args.height)
        root, plan = Path(result["project"]), Path(result["plan"])
        if not args.prepare_only:
            video = step(root, result["commands"], "render", ["mv", "render", root], lambda: pipeline.render(root))
            verified = step(root, result["commands"], "verify", ["mv", "verify", root], lambda: pipeline.verify(root))
            if verified.get("technical_pass") is not True or verified.get("current_inputs_match") is not True:
                raise PipelineError("Rendered example did not verify against current inputs")
            result["video"] = str(video)
        report_dir = root / "work/workflow-check"
        report = step(root, result["commands"], "workflow-check", ["mv", "workflow", "check", plan, "--out", report_dir],
                      lambda: workflow.check(plan, out=report_dir))
        result["workflow"] = report["overall"]
        result["workflow_report"] = str(report_dir / "workflow_report.json")
        result["prepared_only"] = args.prepare_only
        atomic_json(root / "work/example/result.json", result)
        if report["overall"] == "failed" or (not args.prepare_only and report["overall"] != "complete"):
            raise PipelineError(f"Example workflow is {report['overall']}; inspect its report")
        print(json.dumps({key: value for key, value in result.items() if key != "commands"}, ensure_ascii=False, indent=2))
        return 0
    except (PipelineError, OSError, ValueError, ImportError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
