import copy
import math
import os
import shutil
import uuid
from pathlib import Path

from . import __version__, media, validate
from .io import REPO, PipelineError, atomic_json, read_json, run, safe_path, sha256
from .normalize import alignment, apply_edits
from .runner import Runner, cache_key, run_id


def initialize(root, audio, lyrics, start=0, duration=None, fps=30, width=1920, height=1080):
    root = Path(root)
    validate.require(not (root / "project.json").exists(), "Project already exists; use a new directory for new audio")
    validate.require(start >= 0 and (duration is None or duration > 0), "Invalid clip range")
    validate.require(fps > 0 and width > 0 and height > 0 and width % 2 == height % 2 == 0, "Invalid render dimensions/FPS")
    for folder in ["inputs", "work/audio", "edits", "runs", "images"]:
        (root / folder).mkdir(parents=True, exist_ok=True)
    source = root / "inputs" / ("source" + Path(audio).suffix.lower())
    shutil.copy2(audio, source)
    lyrics_path = root / "inputs/lyrics.txt"
    shutil.copy2(lyrics, lyrics_path)
    config = {"source_audio": source.relative_to(root).as_posix(), "source_sha256": sha256(source),
              "lyrics_path": "inputs/lyrics.txt", "lyrics_sha256": sha256(lyrics_path),
              "start_s": start, "requested_duration_s": duration, "fps": fps, "width": width, "height": height}
    audio_path = root / "work/audio/clip.wav"
    metadata = media.normalize(source, audio_path, start, duration)
    atomic_json(root / "project.json", config)
    lines = lyrics_path.read_text(encoding="utf-8-sig").splitlines()
    a = {"schema_version": "0.1", "analysis_id": "analysis-" + uuid.uuid4().hex,
         "audio": {"path": "work/audio/clip.wav", "sha256": sha256(audio_path), "source_sha256": sha256(source),
                   "source_start_s": start, **metadata}, "bpm": None, "beats_s": [], "sections": [],
         "lyrics": [{"id": f"lyric-{i:03d}", "text": text, "alignment_text": text,
                     "start_s": None, "end_s": None, "timing_source": "unknown", "review_status": "unreviewed", "confidence": None}
                    for i, text in enumerate((t.strip() for t in lines if t.strip()), 1)],
         "provenance": [{"stage": "normalize", "tool": "ffmpeg", "version": run([media.ffmpeg(), "-version"]).splitlines()[0], "model": None}], "issues": []}
    save_analysis(root, a, raw=True)
    runner = Runner(root)
    runner.job["input_hashes"] = {"source_audio": config["source_sha256"], "lyrics": config["lyrics_sha256"]}
    runner.save()
    return root


def save_analysis(root, a, raw=False):
    validate.analysis(a, root)
    if raw:
        atomic_json(root / "work/analysis.raw.json", a)
    edits_path = root / "edits/timings.json"
    result = apply_edits(a, read_json(edits_path)) if edits_path.exists() else a
    validate.analysis(result, root)
    atomic_json(root / "analysis.json", result)
    Runner(root).stale("storyboard", "render", "verify")


def analyze(root):
    from .adapters.librosa_adapter import analyze_audio
    from importlib.metadata import version
    a = read_json(root / "work/analysis.raw.json")
    validate.analysis(a, root)
    key = cache_key("analyze", [a["audio"]["sha256"]], version("librosa"))
    def action():
        raw = analyze_audio(safe_path(root, a["audio"]["path"]))
        atomic_json(root / "work/librosa.json", raw)
        return ["work/librosa.json"]
    Runner(root).execute("analyze", key, action)
    raw = read_json(root / "work/librosa.json")
    a.update(bpm=raw["bpm"], beats_s=raw["beats_s"])
    a["provenance"] = [p for p in a["provenance"] if p["stage"] != "beats"] + [
        {"stage": "beats", "tool": "librosa", "version": raw["version"], "model": None}]
    save_analysis(root, a, raw=True)


def import_alignment(root, path):
    raw = read_json(root / "work/analysis.raw.json")
    imported = read_json(path)
    result = alignment(raw, imported)
    validate.analysis(result, root)
    atomic_json(root / "work/alignment.imported.json", imported)
    save_analysis(root, result, raw=True)


def import_edits(root, path):
    edits = read_json(path)
    raw = read_json(root / "work/analysis.raw.json")
    result = apply_edits(raw, edits)
    validate.analysis(result, root)
    validate.require(not any(x["code"].startswith("stale_") for x in result["issues"]), "Edits do not match input; resolve stale edits first")
    old = root / "edits/timings.json"
    if old.exists():
        shutil.copy2(old, root / "edits" / f"timings-{run_id()}.json")
    atomic_json(old, edits)
    save_analysis(root, raw)


def update_lyrics(root, path):
    config = read_json(root / "project.json")
    raw = read_json(root / "work/analysis.raw.json")
    validate.analysis(raw, root)
    text = Path(path).read_text(encoding="utf-8-sig")
    shutil.copy2(root / "inputs/lyrics.txt", root / "edits" / f"lyrics-{run_id()}.txt")
    (root / "inputs/lyrics.txt").write_text(text, encoding="utf-8")
    config["lyrics_sha256"] = sha256(root / "inputs/lyrics.txt")
    atomic_json(root / "project.json", config)
    old = raw["lyrics"]
    new = []
    for i, line in enumerate((s.strip() for s in text.splitlines() if s.strip()), 1):
        if i <= len(old) and old[i-1]["text"] == line:
            new.append(old[i-1])
        else:
            new.append({"id": f"lyric-{i:03d}", "text": line, "alignment_text": line, "start_s": None,
                        "end_s": None, "timing_source": "unknown", "review_status": "unreviewed", "confidence": None})
    raw["lyrics"] = new
    save_analysis(root, raw, raw=True)
    runner = Runner(root)
    runner.job["input_hashes"]["lyrics"] = config["lyrics_sha256"]
    runner.stale("alignment", "storyboard", "render", "verify")


def import_storyboard(root, path):
    data = read_json(path)
    a = read_json(root / "analysis.json")
    validate.analysis(a, root)
    validate.storyboard(data, a, sha256(root / "analysis.json"))
    atomic_json(root / "storyboard.json", data)
    runner = Runner(root)
    runner.stale("render", "verify")
    runner.job["stages"]["storyboard"] = {"status": "succeeded", "outputs": {"storyboard.json": sha256(root / "storyboard.json")}}
    runner.save()


def import_assets(root, path):
    data = read_json(path)
    validate.assets(data, root)
    old_path = root / "assets.json"
    if old_path.exists():
        old = {a["asset_id"]: a for a in read_json(old_path)["assets"]}
        changed = {a["asset_id"] for a in data["assets"] if a["asset_id"] in old and a["sha256"] != old[a["asset_id"]]["sha256"]}
        # Reimport is explicit review, but list all affected dependents for the run log.
        affected = set(changed)
        while True:
            expanded = affected | {a["asset_id"] for a in data["assets"] if set(a["reference_asset_ids"]) & affected}
            if expanded == affected:
                break
            affected = expanded
        review_file = root / "work/asset-review.json"
        pending = set(read_json(review_file)["review_required"]) if review_file.exists() else set()
        pending &= {a["asset_id"] for a in data["assets"]}
        atomic_json(review_file, {"changed": sorted(changed), "review_required": sorted(pending | (affected - changed))})
    atomic_json(old_path, data)
    Runner(root).stale("render", "verify")


def export_brief(root):
    a = read_json(root / "analysis.json")
    validate.analysis(a, root)
    folder = root / "work/brief"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "analysis.json", folder / "analysis.json")
    shutil.copy2(REPO / "contracts/storyboard.schema.json", folder / "storyboard.schema.json")
    config = read_json(root / "project.json")
    atomic_json(folder / "context.json", {"analysis_sha256": sha256(root / "analysis.json"),
                                         "audio_sha256": a["audio"]["sha256"],
                                         "render": {"fps": config["fps"], "width": config["width"], "height": config["height"],
                                                    "duration_frames": math.ceil(a["audio"]["sample_count"] * config["fps"] / a["audio"]["sample_rate"]), "show_lyrics": True}})
    shutil.copy2(REPO / "prompts/storyboard.md", folder / "instructions.md")
    return folder


def renderer_fingerprint():
    files = [REPO / "package.json", REPO / "pnpm-lock.yaml", REPO / "pyproject.toml", REPO / "uv.lock", *sorted((REPO / "renderer").rglob("*.tsx")),
             *sorted((REPO / "renderer").rglob("*.ts")), *sorted((REPO / "renderer").glob("*.mjs")),
             *sorted((REPO / "contracts").glob("*.json")), *sorted((REPO / "src/mv_pipeline").rglob("*.py"))]
    return {p.relative_to(REPO).as_posix(): sha256(p) for p in files}


def prepare_run(root, profile):
    a, s, images, rows, warnings = validate.bundle(root, profile)
    review_path = root / "work/asset-review.json"
    if review_path.exists():
        pending = read_json(review_path)["review_required"]
        if pending and profile == "final":
            raise PipelineError(f"Reference images changed; review dependent images and run confirm-assets: {pending}")
        if pending:
            warnings.append(f"Reference image review pending: {pending}")
    key = cache_key("render", [sha256(root / f"{k}.json") for k in ["analysis", "storyboard", "assets"]],
                    {"profile": profile, "versions": renderer_fingerprint(), "pipeline": __version__})
    return a, s, images, rows, warnings, key


def snapshot(root, a, s, images, rows, warnings, profile):
    folder = root / "runs" / run_id()
    public = folder / "public"
    public.mkdir(parents=True)
    for name in ["project", "analysis", "storyboard", "assets"]:
        shutil.copy2(root / f"{name}.json", folder / f"{name}.json")
    if (root / "edits/timings.json").exists():
        shutil.copy2(root / "edits/timings.json", folder / "timings.json")
    shutil.copy2(safe_path(root, a["audio"]["path"]), public / "audio.wav")
    image_props = {}
    for image in images["assets"]:
        original = safe_path(root, image["path"])
        filename = image["asset_id"] + original.suffix.lower()
        shutil.copy2(original, public / filename)
        image_props[image["asset_id"]] = filename
    atomic_json(folder / "props.json", {"storyboard": s, "assets": image_props, "audio": "audio.wav", "captions": rows})
    atomic_json(folder / "manifest.json", {"profile": profile, "warnings": warnings, "versions": renderer_fingerprint(),
                                          "technical_pass": False, "accepted": False})
    return folder


def render(root, profile="final", studio=False):
    a, s, images, rows, warnings, key = prepare_run(root, profile)
    runner = Runner(root)
    if studio:
        folder = snapshot(root, a, s, images, rows, warnings, "preview")
        # Foreground process: hold the project lock until Studio exits.
        import subprocess
        cli = REPO / "node_modules/@remotion/cli/remotion-cli.js"
        return subprocess.run([media.node(), str(cli), "studio", str(REPO / "renderer/src/index.tsx"),
                               "--props", str(folder / "props.json"), "--public-dir", str(folder / "public")], cwd=REPO, shell=False, check=True)
    def action():
        folder = snapshot(root, a, s, images, rows, warnings, profile)
        runner.job["current_run_id"] = folder.name
        runner.job["render_profile"] = profile
        runner.save()
        temp = folder / "video.tmp.mp4"
        run([media.node(), REPO / "renderer/render.mjs", folder / "props.json", folder / "public", temp], folder / "render.log", cwd=REPO)
        report = media.verify_video(temp, folder / "public/audio.wav", s["render"])
        report["warnings"] = warnings
        report["profile"] = profile
        report["input_hashes"] = {k: sha256(root / f"{k}.json") for k in ["analysis", "storyboard", "assets"]}
        os.replace(temp, folder / "video.mp4")
        atomic_json(folder / "verification.json", report)
        # Cache validity includes every frozen input, not only the output video.
        return [(folder / "video.mp4").relative_to(root).as_posix()] + [
            p.relative_to(root).as_posix() for p in sorted(folder.rglob("*"))
            if p.is_file() and p.name != "video.mp4"]
    paths = runner.execute("render", key, action)
    return root / paths[0]


def verify(root):
    runner = Runner(root)
    validate.require(bool(runner.job["current_run_id"]), "No render exists")
    folder = safe_path(root, "runs/" + runner.job["current_run_id"])
    s = read_json(folder / "storyboard.json")
    report = media.verify_video(folder / "video.mp4", folder / "public/audio.wav", s["render"])
    try:
        validate.bundle(root, read_json(folder / "manifest.json")["profile"])
        report["current_inputs_match"] = all(sha256(root / f"{name}.json") == sha256(folder / f"{name}.json") for name in ["analysis", "storyboard", "assets"])
    except (PipelineError, OSError) as error:
        report["current_inputs_match"] = False
        report["current_input_error"] = str(error)
    report["verified_run_id"] = folder.name
    # Preserve the original result and its warnings, and do not invalidate cache.
    atomic_json(folder / "recheck.json", report)
    return report


def resume(root):
    runner = Runner(root)
    for name in ["analysis", "storyboard", "assets"]:
        if not (root / f"{name}.json").exists():
            runner.waiting(name, f"Import {name}.json to continue")
            raise PipelineError(f"Waiting for {name}.json")
    # Never invoke separation, alignment or paid image generation on resume.
    return render(root, runner.job.get("render_profile", "final"))
