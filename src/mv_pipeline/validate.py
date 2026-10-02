"""The supplied schemas remain the canonical contract; checks here add semantics."""
import math
import wave
from pathlib import Path

from jsonschema import Draft202012Validator
from PIL import Image

from .io import REPO, PipelineError, read_json, safe_path, sha256


def schema(kind, data):
    contract = read_json(REPO / "contracts" / f"{kind}.schema.json")
    Draft202012Validator.check_schema(contract)
    errors = sorted(Draft202012Validator(contract).iter_errors(data), key=lambda e: str(e.path))
    if errors:
        raise PipelineError("; ".join(f"{kind}:{'/'.join(map(str, e.path))}: {e.message}" for e in errors))


def require(condition, message):
    if not condition:
        raise PipelineError(message)


def unique(items, key):
    ids = [item[key] for item in items]
    require(len(ids) == len(set(ids)), f"Duplicate {key}")
    return set(ids)


def analysis(data, root=None):
    schema("analysis", data)
    a = data["audio"]
    duration = a["sample_count"] / a["sample_rate"]
    require(math.isclose(a["duration_s"], duration, abs_tol=1e-9), "Audio duration/sample count mismatch")
    safe_path(root or Path.cwd(), a["path"])
    beats = data["beats_s"]
    require(all(0 <= b < duration for b in beats), "Beat outside audio")
    require(all(x < y for x, y in zip(beats, beats[1:])), "Beats must be strictly increasing")
    unique(data["lyrics"], "id")
    unique(data["sections"], "id")
    for item in data["lyrics"] + data["sections"]:
        if item["start_s"] is not None:
            require(0 <= item["start_s"] < item["end_s"] <= duration, f"Invalid time range: {item['id']}")
    if root:
        path = safe_path(root, a["path"])
        require(path.is_file(), f"Missing audio: {path}")
        require(sha256(path) == a["sha256"], "Audio hash mismatch")
        with wave.open(str(path)) as stream:
            require(stream.getframerate() == a["sample_rate"] and stream.getnframes() == a["sample_count"], "WAV metadata mismatch")
            require(stream.getnchannels() == 2, "Normalized audio must be stereo")
        config_path = Path(root) / "project.json"
        if config_path.exists():
            config = read_json(config_path)
            source = safe_path(root, config["source_audio"])
            require(sha256(source) == a["source_sha256"] == config["source_sha256"], "Source audio changed: initialize a new project")
            require(sha256(safe_path(root, config["lyrics_path"])) == config["lyrics_sha256"], "Input lyrics changed: use update-lyrics")
            require(a["source_start_s"] == config["start_s"], "Source origin mismatch")
    return duration


def assets(data, root=None):
    schema("assets", data)
    ids = unique(data["assets"], "asset_id")
    graph = {}
    for a in data["assets"]:
        path = safe_path(root or Path.cwd(), a["path"])
        require(set(a["reference_asset_ids"]) <= ids, "Unknown reference asset")
        require(a["asset_id"] not in a["reference_asset_ids"], "Asset references itself")
        graph[a["asset_id"]] = a["reference_asset_ids"]
        if root:
            require(path.is_file(), f"Missing image: {a['path']}")
            require(sha256(path) == a["sha256"], f"Image hash mismatch: {a['asset_id']}")
            with Image.open(path) as image:
                require(image.size == (a["width"], a["height"]), f"Image size mismatch: {a['asset_id']}")
                image.verify()
    def visit(key, stack):
        require(key not in stack, "Cyclic image references")
        for ref in graph[key]:
            visit(ref, stack | {key})
    for key in graph:
        visit(key, set())
    return ids


def captions(data, fps, total):
    rows = sorted((x for x in data["lyrics"] if x["start_s"] is not None), key=lambda x: x["start_s"])
    result = []
    for row in rows:
        start = max(0, math.floor(row["start_s"] * fps))
        end = min(total, math.ceil(row["end_s"] * fps))
        if result and result[-1]["endFrame"] > start:
            require(result[-1]["endMs"] <= row["start_s"] * 1000 + 1e-7,
                    "Overlapping singing: select one display line manually")
            result[-1]["endFrame"] = start
        result.append({"id": row["id"], "text": row["text"], "startMs": row["start_s"] * 1000,
                       "endMs": row["end_s"] * 1000, "timestampMs": None,
                       "confidence": row["confidence"], "startFrame": start, "endFrame": end})
    return result


def storyboard(data, a, analysis_hash, asset_ids=None):
    schema("storyboard", data)
    require(data["analysis_id"] == a["analysis_id"], "Analysis ID mismatch")
    require(data["analysis_sha256"] == analysis_hash, "Analysis hash mismatch: reimport reviewed storyboard")
    require(data["audio_sha256"] == a["audio"]["sha256"], "Storyboard audio hash mismatch")
    r = data["render"]
    require(r["duration_frames"] == math.ceil(a["audio"]["sample_count"] * r["fps"] / a["audio"]["sample_rate"]), "Frame count mismatch")
    require(r["width"] % 2 == r["height"] % 2 == 0, "H.264 dimensions must be even")
    unique(data["scenes"], "id")
    lyric_ids = {x["id"] for x in a["lyrics"]}
    cursor = 0
    for i, scene in enumerate(data["scenes"]):
        require(scene["start_frame"] == cursor and scene["end_frame"] > cursor, "Scene gap, overlap or empty range")
        cursor = scene["end_frame"]
        require(set(scene["lyric_ids"]) <= lyric_ids, "Unknown lyric reference")
        if asset_ids is not None:
            require(scene["asset_id"] in asset_ids, "Unknown scene asset")
        t = scene["transition_in"]
        require(t["duration_frames"] <= cursor - scene["start_frame"], "Transition exceeds scene")
        require(i > 0 or t["type"] == "cut", "First scene must use cut")
    require(cursor == r["duration_frames"], "Scenes do not cover duration")
    if asset_ids is not None and data["world"]["reference_asset_id"]:
        require(data["world"]["reference_asset_id"] in asset_ids, "Unknown world reference")


def bundle(root, profile="final"):
    root = Path(root)
    a = read_json(root / "analysis.json")
    s = read_json(root / "storyboard.json")
    images = read_json(root / "assets.json")
    analysis(a, root)
    ids = assets(images, root)
    storyboard(s, a, sha256(root / "analysis.json"), ids)
    warnings = [f"{x['id']}: {x['timing_source']}/{x['review_status']}" for x in a["lyrics"]
                if x["timing_source"] == "unknown" or x["review_status"] != "confirmed"]
    warnings += [x["message"] for x in a["issues"]]
    if profile == "final" and s["render"]["show_lyrics"]:
        require(not warnings, "Unresolved lyric timing/issues: " + "; ".join(warnings))
    rows = captions(a, s["render"]["fps"], s["render"]["duration_frames"]) if s["render"]["show_lyrics"] else []
    return a, s, images, rows, warnings
