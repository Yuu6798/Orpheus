"""Find the first maximum of an animated shape key in each requested frame interval."""
import json
import math
import sys
from pathlib import Path


def measure(bpy, scene, events):
    peaks = {}
    for event in events:
        spec = event["peak"]
        obj = scene.objects.get(spec["object"])
        keys = getattr(getattr(obj, "data", None), "shape_keys", None)
        key = keys.key_blocks.get(spec["shape_key"]) if keys else None
        if key is None or not keys.use_relative or key == keys.reference_key:
            raise ValueError(f"Expected a relative expression shape key: {spec['object']} / {spec['shape_key']}")
        start, end = spec["range"]
        if start < scene.frame_start or end > scene.frame_end:
            raise ValueError(f"Peak search outside scene range: {start}..{end}")
        values = []
        for frame in range(start, end + 1):
            scene.frame_set(frame)
            graph = bpy.context.evaluated_depsgraph_get()
            if obj.evaluated_get(graph).show_only_shape_key:
                raise ValueError(f"Disable the pinned shape-key preview on {obj.name}")
            evaluated = keys.evaluated_get(graph).key_blocks[key.name]
            value = 0.0 if evaluated.mute else float(evaluated.value)
            if not math.isfinite(value):
                raise ValueError(f"Non-finite shape key at frame {frame}")
            values.append(value)
        highest = max(values)
        if min(values) == highest:
            raise ValueError(f"No varying expression in the selected range: {event['id']}")
        peaks[event["id"]] = {"frame": start + values.index(highest), "value": highest}
    return peaks


if __name__ == "__main__":
    import bpy
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from character_review_views import use_review_scene

    config, output = map(Path, sys.argv[sys.argv.index("--") + 1:])
    job = json.loads(config.read_text(encoding="utf-8"))
    rig = bpy.data.objects.get(job["armature"])
    if rig is None or rig.type != "ARMATURE":
        raise ValueError(f"Unknown armature: {job['armature']}")
    result = measure(bpy, use_review_scene(bpy, rig, job.get("scene")), job["events"])
    output.write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
