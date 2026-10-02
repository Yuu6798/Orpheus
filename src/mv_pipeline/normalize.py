"""Model-neutral alignment import and separately preserved manual edits."""
import copy

from .io import PipelineError


def alignment(base, imported):
    if imported["audio_sha256"] != base["audio"]["sha256"]:
        raise PipelineError("Alignment belongs to a different audio file")
    offset = imported.get("offset_s")
    if not isinstance(offset, (int, float)):
        raise PipelineError("Alignment must declare offset_s (clip_time = model_time + offset_s)")
    tool = imported["tool"]
    if not tool.get("name") or not tool.get("version"):
        raise PipelineError("Alignment requires tool name and version")
    result = copy.deepcopy(base)
    by_id = {x["id"]: x for x in result["lyrics"]}
    seen = set()
    for item in imported["lyrics"]:
        if item["id"] not in by_id or item["id"] in seen:
            raise PipelineError("Unknown or duplicate alignment lyric ID")
        seen.add(item["id"])
        row = by_id[item["id"]]
        if item["text"] != row["text"]:
            raise PipelineError(f"Alignment lyric text changed: {item['id']}")
        source = item.get("timing_source", "model")
        if source not in {"model", "interpolated", "unknown"}:
            raise PipelineError("Imported model timings must be model, interpolated or unknown")
        row.update(start_s=None if source == "unknown" else item["start_s"] + offset,
                   end_s=None if source == "unknown" else item["end_s"] + offset,
                   timing_source=source, review_status="unreviewed", confidence=None)
        if imported.get("confidence_definition") and source != "unknown":
            row["confidence"] = item.get("confidence")
    result["provenance"].append({"stage": "alignment", "tool": tool["name"], "version": tool["version"], "model": tool.get("model")})
    return result


def apply_edits(base, edits):
    result = copy.deepcopy(base)
    if edits["audio_sha256"] != base["audio"]["sha256"]:
        result["issues"].append({"code": "stale_edits", "target_id": None, "message": "Manual edits refer to another audio; rebase explicitly"})
        return result
    by_id = {x["id"]: x for x in result["lyrics"]}
    seen = set()
    for edit in edits.get("lyrics", []):
        key = edit["id"]
        if key in seen:
            raise PipelineError("Duplicate manual edit ID")
        seen.add(key)
        if key not in by_id or by_id[key]["text"] != edit["text"]:
            result["issues"].append({"code": "stale_lyric_edit", "target_id": key, "message": f"Lyric changed: {key}; manual edit not applied"})
            continue
        by_id[key].update(start_s=edit["start_s"], end_s=edit["end_s"], timing_source="manual",
                          review_status="confirmed", confidence=None)
    if "sections" in edits:
        result["sections"] = copy.deepcopy(edits["sections"])
        for section in result["sections"]:
            section.update(source="manual", review_status="confirmed")
    return result
