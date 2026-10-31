"""dance の run・キャッシュ・静止画シート。run の形と accept は fx と共通（fx/runs.py）。

出力先 <out> の構成（既定は spec のあるディレクトリの dance_runs/）:

    <out>/<UTC run_id>/   spec.json（写し）manifest.json timeline.json dance.mp4 render.log
    <out>/index.json      キャッシュ key -> run_id
    <out>/acceptance.jsonl  人間の判定（追記のみ。`mv dance accept`）
    <out>/stills-<key8>.png
"""
import hashlib
import shutil
import time
from pathlib import Path

from ..fx import runs as fxruns
from ..fx import probe, timeline
from ..io import PipelineError, atomic_json, sha256
from ..runner import project_lock
from . import SCHEMA_VERSION, VARIANT, VERSION, fingerprint
from . import spec as dancespec


def default_out(sp):
    return sp.base_dir / "dance_runs"


def describe_assets(sp):
    """{役割: {path, sha256}}。素材が無ければ原因つきで止める。"""
    out = {}
    for role, rel in dancespec.asset_refs(sp.data).items():
        path = sp.resolve(rel)
        if not path.is_file():
            raise PipelineError(f"素材が見つかりません（{role}）: {rel}")
        out[role] = {"path": rel, "sha256": sha256(path)}
    return out


def _key(kind, sp, assets, extra):
    return fxruns.content_key_for(kind, {"spec": sp.data, "assets": {r: a["sha256"] for r, a in assets.items()},
                                         "engine": fingerprint(), **extra})


def _audio(ctx, start, n_frames):
    path = ctx.audio_path()
    if path is None:
        return None
    return {"path": path, "start": ctx.spec.data["audio"]["offset_s"] + start, "duration": n_frames / ctx.fps}


def render_run(spec_path, start=0.0, duration=None, out=None, scale=1.0, force=False, progress=None):
    """spec から dance.mp4 を描き、run として記録する。同じ入力で成功済みなら再利用する。"""
    from ..fx import render as fxrender
    from . import compose

    began = time.monotonic()
    say = progress or (lambda message: None)
    sp = dancespec.load_spec(spec_path)
    start, duration, n_frames = fxruns.resolve_interval(sp, start, duration)
    size = compose.scaled_size(sp, scale)
    assets = describe_assets(sp)
    libraries = fxrender.library_versions()
    fps = sp.data["output"]["fps"]
    key = _key("dance-render", sp, assets, {"start": start, "duration": duration, "frames": n_frames, "size": list(size),
                                            "scale": float(scale), "libraries": libraries})
    out = (Path(out) if out else default_out(sp)).resolve()
    with project_lock(out):
        fxruns.close_orphans(out)
        index = fxruns.load_index(out)
        if not force:
            manifest = fxruns.reusable(out, index, key)
            if manifest:
                say(f"[dance] 同じ入力の成功 run を再利用します: {manifest['run_id']}（作り直すには --force）")
                return fxruns.summarize(out / manifest["run_id"], manifest, reused=True)
        run = fxruns.Run(out, key, "dance-render", "render.log")
        ctx = None
        try:
            shutil.copyfile(sp.path, run.dir / "spec.json")
            run.manifest.update(
                spec={"file": "spec.json", "sha256": sp.sha256,
                      "normalized_sha256": hashlib.sha256(dancespec.canonical_json(sp.data).encode("utf-8")).hexdigest()},
                assets=assets, engine={"name": "dance", "version": VERSION, "schema_version": SCHEMA_VERSION,
                                       "fingerprint": fingerprint()},
                libraries=libraries, interval={"start": start, "duration": duration, "fps": fps, "frames": n_frames},
                output={"size": list(size), "scale": float(scale), "crf": sp.data["output"]["crf"]},
                variants={}, grid=None, warnings=[])
            run.save()
            run.log(f"render start key={key} start={start:g} duration={duration:g} frames={n_frames} size={size[0]}x{size[1]}")
            ctx = compose.prepare(sp, scale)
            run.manifest["warnings"] = list(ctx.warnings)
            for warning in ctx.warnings:
                run.log(f"warning: {warning}")
            atomic_json(run.dir / "timeline.json", compose.timeline_record(ctx))
            audio = _audio(ctx, start, n_frames)
            if audio and not audio["path"].is_file():
                raise PipelineError(f"音声が見つかりません: {sp.data['audio']['path']}")

            def frames():
                for i in range(n_frames):
                    yield compose.frame(ctx, timeline.frame_time(i, fps, start))

            path = run.dir / f"{VARIANT}.mp4"
            result = fxrender.write_video(path, frames(), size, fps, ctx.crf, n_frames, audio, run.log_path)
            checks = probe.check_video(path, width=size[0], height=size[1], fps=fps, frames=n_frames, audio=audio is not None,
                                       audio_s=n_frames / fps if audio else None, bt709=True)
            run.manifest["variants"][VARIANT] = {"path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                                                 "technical_pass": checks["pass"], "checks": checks,
                                                 "render_seconds": result["seconds"]}
            run.save()
            run.log(f"{VARIANT}: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
            say(f"[dance] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
            if not checks["pass"]:
                message = f"技術検査に失敗しました（{checks['failures']}）。詳細: {run.dir / 'manifest.json'}"
                run.finish(False, message)
                raise PipelineError(message)
            run.finish(True)
            index["runs"][key] = {"run_id": run.run_id, "finished_at": run.manifest["finished_at"]}
            index["schema_version"] = fxruns.INDEX_SCHEMA
            atomic_json(out / "index.json", index)
        except BaseException as error:
            if run.manifest["status"] == "running":
                run.fail(error)
            raise
        finally:
            if ctx is not None:
                ctx.close()
    result = fxruns.summarize(run.dir, run.manifest, reused=False)
    result["timeline"] = str(run.dir / "timeline.json")
    result["seconds"] = round(time.monotonic() - began, 1)
    return result


def _guides(image, ctx):
    """UI 枠（赤）と字の箱（シアン）を重ねた写しを返す。"""
    import cv2

    out = image.copy()
    width, height = ctx.size
    for region in ctx.spec.data["lyrics"]["ui_regions"]:
        x0, y0, x1, y1 = region["box"]
        cv2.rectangle(out, (int(x0 * width), int(y0 * height)), (int(x1 * width) - 1, int(y1 * height) - 1), (255, 40, 40), 2)
    for line in ctx.lines:
        box = line.box_px
        cv2.rectangle(out, (box[0], box[1]), (max(box[0], box[2] - 1), max(box[1], box[3] - 1)), (40, 230, 255), 1)
    return out


def stills(spec_path, times, out=None, scale=1.0, guides=False, columns=4):
    """指定時刻のフレームを並べたシートを <out>/stills-<key8>.png に書く。guides=True で UI 枠と字の箱を重ねる。"""
    import numpy as np

    from ..fx import compare
    from ..fx import render as fxrender
    from . import compose

    began = time.monotonic()
    sp = dancespec.load_spec(spec_path)
    times = [float(t) for t in times]
    if not times:
        raise PipelineError("--times に 1 つ以上の時刻（秒）を指定してください")
    bad = [t for t in times if not 0 <= t <= sp.data["duration_s"]]
    if bad:
        raise PipelineError(f"時刻がショットの長さ 0〜{sp.data['duration_s']:g} 秒の外です: {bad}")
    size = compose.scaled_size(sp, scale)
    assets = describe_assets(sp)
    key = _key("dance-stills", sp, assets, {"times": times, "size": list(size), "scale": float(scale), "guides": guides,
                                            "libraries": fxrender.library_versions()})
    ctx = compose.prepare(sp, scale)
    try:
        cells = [compose.frame(ctx, t) for t in times]
        if guides:
            cells = [_guides(cell, ctx) for cell in cells]
    finally:
        ctx.close()
    blank = np.zeros_like(cells[0])
    rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    rows = [np.hstack(row + [blank] * (columns - len(row))) for row in rows]
    path = (Path(out) if out else default_out(sp)).resolve() / f"stills-{key[:8]}.png"
    compare.save_png_atomic(np.vstack(rows), path)
    return {"path": str(path), "times": times, "cell": list(size), "warnings": list(ctx.warnings),
            "seconds": round(time.monotonic() - began, 1)}
