"""Prepare a local review package using matched body-relative Blender views."""
import math
import re
import shutil
import tempfile
from pathlib import Path

from .blender_adapter import launcher
from .io import ToolError, atomic_json, read_json, resolve_path, sha256

VIEWS = ("left-front", "left", "left-back", "right-front", "right", "right-back")
SCRIPTS = Path(__file__).with_name("blender_adapter")


def _object(value, allowed, label):
    if not isinstance(value, dict) or value.keys() - set(allowed):
        raise ToolError(f"Invalid fields in {label}: expected {', '.join(allowed)}")
    return value


def _name(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"Expected a non-empty name: {label}")
    return value


def _file(base, value, suffixes=()):
    path = resolve_path(base, value)
    if not path.is_file() or (suffixes and path.suffix.lower() not in suffixes):
        raise ToolError(f"Missing or unsupported file: {path}")
    return path


def _source(base, data):
    _object(data, ("path", "armature", "left", "right", "anchor", "meshes"), "model")
    source = {k: _name(data.get(k), k) for k in ("armature", "left", "right", "anchor")}
    source["path"] = str(_file(base, data.get("path"), (".blend",)))
    if "meshes" in data:
        meshes = data["meshes"]
        if not isinstance(meshes, list) or not meshes:
            raise ToolError("meshes must be a nonempty list of objects")
        source["meshes"] = [_name(name, "mesh") for name in meshes]
    return source


def _events(data):
    if not isinstance(data, list) or not data:
        raise ToolError("At least one review event is required")
    ids = set()
    for event in data:
        _object(event, ("id", "label", "frame", "peak", "finding", "region", "rest", "disable", "focus_view",
                        "measurements", "framing"), "event")
        identifier = _name(event.get("id"), "event id")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", identifier) or identifier.casefold() in ids:
            raise ToolError(f"Expected unique portable event id: {identifier}")
        if re.fullmatch(r"CON|PRN|AUX|NUL|(?:COM|LPT)[1-9]", identifier, re.IGNORECASE):
            raise ToolError(f"Reserved event id: {identifier}")
        ids.add(identifier.casefold())
        _name(event.get("label", identifier), "event label")
        if "region" in event:
            _name(event["region"], "region")
        if "focus_view" in event and event["focus_view"] not in VIEWS:
            raise ToolError("focus_view must be a labelled body-relative view")
        if "framing" in event:
            framing = _object(event["framing"], ("anchor", "scale", "offset_z"), "framing")
            if "anchor" in framing:
                _name(framing["anchor"], "framing.anchor")
            for key in ("scale", "offset_z"):
                if key in framing and (type(framing[key]) not in (float, int) or not math.isfinite(framing[key])):
                    raise ToolError(f"framing.{key} must be finite")
            if "scale" in framing and framing["scale"] <= 0:
                raise ToolError("framing.scale must be positive")
        measurements = event.get("measurements", [])
        if not isinstance(measurements, list):
            raise ToolError("measurements must be a list")
        for measurement in measurements:
            _object(measurement, ("label", "value", "unit"), "measurement")
            _name(measurement.get("label"), "measurement.label")
            _name(measurement.get("unit"), "measurement.unit")
            value = measurement.get("value")
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ToolError("measurement.value must be a finite number")
        if sum(k in event for k in ("frame", "peak", "finding")) != 1:
            raise ToolError(f"{identifier} needs exactly one frame, peak or finding")
        if "frame" in event and type(event["frame"]) is not int:
            raise ToolError("frame must be an integer")
        if type(event.get("rest", False)) is not bool or (event.get("rest") and "frame" not in event):
            raise ToolError("rest requires an explicit frame")
        if "disable" in event:
            control = _object(event["disable"], ("object", "hide", "modifier", "shape_key"), "disable")
            _name(control.get("object"), "disable.object")
            if len(control) != 2 or ("hide" in control and control["hide"] is not True):
                raise ToolError("disable takes one shape_key, modifier or hide:true")
            for k in ("modifier", "shape_key"):
                if k in control:
                    _name(control[k], k)
        if "peak" in event:
            peak = _object(event["peak"], ("object", "shape_key", "range"), "peak")
            for k in ("object", "shape_key"):
                _name(peak.get(k), k)
            bounds = peak.get("range")
            if not isinstance(bounds, list) or len(bounds) != 2 or any(type(n) is not int for n in bounds) or bounds[0] > bounds[1]:
                raise ToolError("peak.range must be [first_frame, last_frame] in ascending order")
        if "finding" in event:
            finding = _object(event["finding"], ("kind", "label"), "finding")
            if finding.get("kind") not in ("sections", "expressions"):
                raise ToolError("finding.kind must be sections or expressions")
            _name(finding.get("label"), "finding.label")
    return data


def _resolve_events(events, report, source, blender, stage):
    peaks = [e for e in events if "peak" in e]
    peak_values = {}
    if peaks:
        job, output = stage / "peaks-job.json", stage / "peaks.json"
        atomic_json(job, {"armature": source["armature"], "scene": source.get("scene"), "events": peaks})
        launcher.run_blender(blender, SCRIPTS / "character_review_peaks.py", source["path"],
                             [str(job), str(output)], log=stage / "peaks.log")
        peak_values = read_json(output)
    resolved = []
    for spec in events:
        event = dict(spec, observations=[])
        for m in event.get("measurements", []):
            event["observations"].append(f"{m['label']}: {m['value']} {m['unit']} (入力した測定値)")
        if "peak" in spec:
            peak = peak_values[spec["id"]]
            event["frame"] = peak["frame"]
            event["observations"].append(f"{spec['peak']['shape_key']} = {peak['value']:.4g} (peak)")
        if "finding" in spec:
            kind, label = spec["finding"]["kind"], spec["finding"]["label"]
            try:
                row = report["worst"][kind][label]
            except (TypeError, KeyError) as error:
                raise ToolError(f"No roundtrip finding for {kind} / {label}") from error
            if not isinstance(row, dict) or type(row.get("frame")) is not int:
                raise ToolError(f"Missing measured frame for {label}")
            event["frame"] = row["frame"]
            metric = "ratio" if kind == "sections" else "delta"
            value = row.get(metric)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ToolError(f"Missing measured {metric} for {label}")
            comparison = "GLB ÷ Blender" if kind == "sections" else "GLB − Blender"
            event["observations"].append(f"{label}: {metric} = {value:.6g} ({comparison})")
        event.setdefault("label", event["id"])
        control = event.get("disable")
        if control:
            event["control_label"] = " / ".join([control["object"], str(control.get("shape_key") or control.get("modifier") or "visibility")])
        resolved.append(event)
    return resolved


def run(spec, out, blender, *, roundtrip_report=None):
    from .character_review_sheet import write_character_review_sheet
    spec, out, blender = Path(spec).resolve(), Path(out).resolve(), Path(blender).resolve()
    base = spec.parent
    data = _object(read_json(spec), ("before", "after", "references", "motion_reference", "events",
                                    "scale", "offset_z", "size", "roundtrip_report"), "review")
    sources = {side: _source(base, data.get(side)) for side in ("before", "after")}
    events = _events(data.get("events"))
    refs = _object(data.get("references"), VIEWS, "references")
    if not refs:
        raise ToolError("Provide at least one labelled appearance reference")
    refs = {k: _file(base, v, (".png", ".jpg", ".jpeg", ".webp")) for k, v in refs.items()}
    motion = _file(base, data["motion_reference"]) if "motion_reference" in data else None
    identities = {side: {"file": Path(source["path"]).name, "sha256": sha256(source["path"])}
                  for side, source in sources.items()}
    report = None
    if roundtrip_report is not None or "roundtrip_report" in data:
        report_path = Path(roundtrip_report) if roundtrip_report is not None else _file(base, data["roundtrip_report"])
        report = read_json(report_path)
        if not isinstance(report, dict) or report.get("schema_version") != "model3d-character-roundtrip/0.1":
            raise ToolError("Expected a character-roundtrip report")
        if report.get("native_sha256") != identities["after"]["sha256"]:
            raise ToolError("Roundtrip report does not match the after model")
        if not isinstance(report.get("delivered_sha256"), str) or not re.fullmatch("[0-9a-f]{64}", report["delivered_sha256"]):
            raise ToolError("Missing delivered model identity in roundtrip report")
        if "native_scene" in report:
            scene = _name(report["native_scene"], "roundtrip native_scene")
            sources["after"]["scene"] = scene
            # An identical before file has the same measured scene and timeline.
            if identities["before"]["sha256"] == identities["after"]["sha256"]:
                sources["before"]["scene"] = scene
    scale, offset, size = data.get("scale"), data.get("offset_z", 0), data.get("size", 512)
    if not all(type(n) in (int, float) and math.isfinite(n) for n in (scale, offset)) or scale <= 0:
        raise ToolError("Provide positive scale and a finite offset_z in metres")
    if type(size) is not int or not 16 <= size <= 4096:
        raise ToolError("size must be an integer between 16 and 4096")
    out.mkdir(parents=True, exist_ok=True)
    # Each completed package has its own directory. Failed renders leave prior runs intact.
    with tempfile.TemporaryDirectory(prefix=".review-", dir=out) as temp:
        stage = Path(temp)
        resolved = _resolve_events(events, report, sources["after"], blender, stage)
        job = dict(sources, output=str(stage / "views"), scale=scale, offset_z=offset, size=size, views=list(VIEWS),
                   events=[dict(name=e["id"], frame=e["frame"], **{k: e[k] for k in ("disable", "rest", "framing") if k in e}) for e in resolved])
        atomic_json(stage / "render-job.json", job)
        record = launcher.run_blender(blender, SCRIPTS / "character_review_views.py", None, [str(stage / "render-job.json")],
                                      log=stage / "render.log")
        cameras = read_json(stage / "views/cameras.json")
        reference_dir = stage / "references"
        reference_dir.mkdir()
        copied = {}
        for view, path in refs.items():
            copied[view] = reference_dir / (view + path.suffix.lower())
            shutil.copyfile(path, copied[view])
        renders = []
        for event in resolved:
            for view in VIEWS:
                folder = stage / "views" / event["id"] / view
                row = dict(event=event["id"], view=view, before=folder / "before.png", after=folder / "after.png")
                if "disable" in event:
                    row["control_off"] = folder / "disabled.png"
                renders.append(row)
        measurement_file = None
        if report is not None:
            measurement_file = stage / "roundtrip.json"
            atomic_json(measurement_file, report)
        write_character_review_sheet(stage, events=resolved, references=copied, motion_reference=motion,
                                     roundtrip_report=measurement_file, renders=renders, fps=cameras["fps"],
                                     first_frame=cameras["first_frame"])
        atomic_json(stage / "events.json", resolved)
        if any(sha256(source["path"]) != identities[side]["sha256"] for side, source in sources.items()):
            raise ToolError("An input model changed during the review")
        provenance = {"models": identities, "blender": record["blender_version"]}
        if report is not None:
            provenance["roundtrip"] = {"native_sha256": report["native_sha256"],
                                       "delivered_sha256": report["delivered_sha256"]}
            if "native_scene" in report:
                provenance["roundtrip"]["native_scene"] = report["native_scene"]
        atomic_json(stage / "sources.json", provenance)
        (stage / "render-job.json").unlink()
        # Rename the staging directory within the same filesystem; keep the report's links relative.
        published = out / stage.name.replace(".review-", "review-", 1)
        stage.rename(published)
    return {"index": str(published / "index.html"), "events": len(resolved), "views": len(VIEWS),
            "cameras": str(published / "views/cameras.json")}
