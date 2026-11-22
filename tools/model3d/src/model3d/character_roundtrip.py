"""Compare a native performance and a single-clip GLB on the native scene's clock."""
import math
import tempfile
from collections import Counter
from pathlib import Path

from .blender_adapter.launcher import run_blender
from .glb import read_glb
from .io import ToolError, atomic_json, read_json, resolve_path, sha256


def _keys(obj, allowed, where):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise ToolError(f"{where}: expected an object with fields {', '.join(allowed)}")


def _name(value, where):
    if not isinstance(value, str) or not value.strip():
        raise ToolError(f"{where}: expected a nonempty name")
    return value


def _number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where}: expected a finite number")
    return value


def _settings(spec):
    _keys(spec, ("native", "delivered", "frames", "native_start", "delivered_start", "sections", "expressions"), "spec")
    frames = spec.get("frames")
    if not isinstance(frames, list) or not frames or any(type(f) is not int for f in frames) or len(set(frames)) != len(frames):
        raise ToolError("frames must be a nonempty list of distinct native integer frame numbers")
    result = {"frames": sorted(frames)}
    result["native_start"] = spec.get("native_start", 1)
    if type(result["native_start"]) is not int:
        raise ToolError("native_start must be an integer")
    result["delivered_start"] = _number(spec.get("delivered_start", 0), "delivered_start")
    if result["frames"][0] < result["native_start"]:
        raise ToolError("frames cannot precede native_start")
    for kind in ("sections", "expressions"):
        rows = spec.get(kind, [])
        if not isinstance(rows, list):
            raise ToolError(f"{kind} must be a list")
        result[kind] = []
        labels = set()
        for row in rows:
            fields = ("label", "native", "delivered")
            if kind == "sections":
                fields += ("at", "half_width", "max_radius")
            _keys(row, fields, kind)
            label = _name(row.get("label"), kind)
            if label in labels:
                raise ToolError(f"duplicate {kind} label: {label}")
            labels.add(label)
            clean = {"label": label}
            for side in ("native", "delivered"):
                names = ("mesh", "key") if kind == "expressions" else ("mesh", "bone", "tip")
                binding = row.get(side)
                _keys(binding, names if kind == "expressions" else (*names, "groups"), f"{label}.{side}")
                clean[side] = {k: _name(binding.get(k), f"{label}.{side}.{k}") for k in names}
                if kind == "sections":
                    groups = binding.get("groups", [binding["bone"]])
                    if not isinstance(groups, list) or not groups:
                        raise ToolError(f"{label}.{side}.groups must contain at least one vertex group")
                    clean[side]["groups"] = [_name(g, "groups") for g in groups]
            if kind == "sections":
                for k, default in (("at", 0.5), ("half_width", 0.1), ("max_radius", 0.65)):
                    clean[k] = _number(row.get(k, default), f"{label}.{k}")
                if not 0 <= clean["at"] <= 1 or clean["half_width"] <= 0 or clean["max_radius"] <= 0:
                    raise ToolError(f"{label}: at must lie in [0, 1]; half_width and max_radius must be positive")
            result[kind].append(clean)
    if not (result["sections"] or result["expressions"]):
        raise ToolError("supply at least one section or expression")
    return result


def _delivery_info(glb):
    document, _ = read_glb(glb.read_bytes(), str(glb))
    clips = document.get("animations", [])
    if len(clips) != 1:
        raise ToolError(f"expected a GLB with one animation; found {len(clips)}")
    # Only samplers targeted by channels define the playable interval. Each
    # channel holds its endpoint when another channel extends farther in time.
    try:
        intervals = []
        clip = clips[0]
        for index in {c["sampler"] for c in clip["channels"]}:
            if type(index) is not int or index < 0:
                raise ValueError("invalid channel sampler")
            input_index = clip["samplers"][index]["input"]
            if type(input_index) is not int or input_index < 0:
                raise ValueError("invalid sampler input")
            accessor = document["accessors"][input_index]
            start, = accessor["min"]
            end, = accessor["max"]
            if not 0 <= _number(start, "animation start time") <= _number(end, "animation end time"):
                raise ValueError("invalid animation interval")
            intervals.append((start, end))
        first = min(a for a, b in intervals)
        last = max(b for a, b in intervals)
    except (KeyError, TypeError, IndexError, ValueError) as exc:
        raise ToolError("GLB has no valid animation sampler time bounds") from exc
    # Blender may import the animated node as an empty and create a separate skinned
    # mesh using its glTF data name. Only one-to-one, named bindings are resolvable.
    nodes = [n for n in document.get("nodes", []) if "mesh" in n and n.get("name")]
    meshes = document.get("meshes", [])
    node_names = Counter(n["name"] for n in document.get("nodes", []) if n.get("name"))
    used = Counter(n["mesh"] for n in document.get("nodes", []) if "mesh" in n)
    mesh_names = Counter(m.get("name") for m in meshes)
    try:
        aliases = {n["name"]: meshes[n["mesh"]]["name"] for n in nodes
                   if node_names[n["name"]] == 1 and used[n["mesh"]] == 1
                   and meshes[n["mesh"]].get("name") and mesh_names[meshes[n["mesh"]]["name"]] == 1}
    except (IndexError, KeyError, TypeError) as exc:
        raise ToolError("GLB has an invalid node mesh reference") from exc
    return {"name": clip.get("name"), "start_seconds": first, "end_seconds": last}, aliases


def compare(native, delivered):
    rows = []
    worst = {"sections": {}, "expressions": {}}
    for n, d in zip(native["samples"], delivered["samples"], strict=True):
        row = {"frame": n["frame"], "seconds": n["seconds"], "delivered_frame": d["frame"]}
        for kind in worst:
            row[kind] = {}
            for label, left in n[kind].items():
                right = d[kind][label]
                if kind == "sections":
                    key = "ratio"
                    metric = right / left if left > 0 else None
                    neutral = 1
                else:
                    key, metric, neutral = "delta", right - left, 0
                result = {"native": left, "delivered": right, key: metric}
                row[kind][label] = result
                previous = worst[kind].get(label)
                if metric is not None and (previous is None or abs(metric-neutral) > abs(previous[key]-neutral)):
                    worst[kind][label] = {"frame": n["frame"], "seconds": n["seconds"], **result}
        rows.append(row)
    return {"frames": rows, "worst": worst}


def run(spec, out, blender):
    spec, out = Path(spec).resolve(), Path(out).resolve()
    cfg = read_json(spec)
    settings = _settings(cfg)
    assets = {}
    for side, suffix in (("native", ".blend"), ("delivered", ".glb")):
        path = resolve_path(spec.parent, cfg.get(side))
        if not path.is_file() or path.suffix.lower() != suffix:
            raise ToolError(f"{side} must be an existing {suffix} file: {path}")
        assets[side] = str(path)
    if out in {spec, *(Path(p) for p in assets.values())}:
        raise ToolError("report must not overwrite an input")
    digests = {side: sha256(p) for side, p in assets.items()}
    clip, aliases = _delivery_info(Path(assets["delivered"]))
    adapter = Path(__file__).parent / "blender_adapter" / "character_roundtrip.py"
    with tempfile.TemporaryDirectory(prefix="model3d-roundtrip-") as tmp:
        options, raw = Path(tmp) / "settings.json", Path(tmp) / "result.json"
        atomic_json(options, {**settings, **assets, "clip": clip, "node_meshes": aliases})
        record = run_blender(blender, adapter, args=[options, raw])
        data = read_json(raw)
    if any(sha256(p) != digests[side] for side, p in assets.items()):
        raise ToolError("an input asset changed during the comparison")
    report = {"schema_version": "model3d-character-roundtrip/0.1", **assets,
              "native_sha256": digests["native"], "delivered_sha256": digests["delivered"], "clip": clip,
              "blender": record["blender_version"], "native_scene": data["native_scene"],
              "fps": data["fps"], "native_start": settings["native_start"],
              "delivered_start": settings["delivered_start"], "delivered_fps": data["delivered_fps"],
              "units": {"sections": "mean radial distance / rest joint-to-joint length",
                        "expressions": "evaluated relative shape-key weight (muted = 0)"},
              "sections": settings["sections"], "expressions": settings["expressions"],
              "probes": {side: data[side]["probes"] for side in assets}, **compare(data["native"], data["delivered"])}
    atomic_json(out, report)
    return {"out": str(out), "sampled_frames": len(report["frames"]), "worst": report["worst"]}
