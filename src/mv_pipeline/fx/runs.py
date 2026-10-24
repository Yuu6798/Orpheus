"""fx の run ディレクトリ・manifest・キャッシュ（index.json）・静止画・accept。

出力先 <out> の構成（既定は spec のあるディレクトリの fx_runs/）:

    <out>/<UTC run_id>/   spec.json（写し）manifest.json base.mp4 layers.mp4 light.mp4 both.mp4 grid.mp4 render.log
    <out>/index.json      キャッシュ key -> run_id（成功した run だけ。失敗しても過去の成功 run と index は壊れない）
    <out>/acceptance.jsonl  人間の判定（追記のみ。technical_pass とは別物）
    <out>/stills-<key8>.png   fx stills のシート

run_id = `20261024T120000Z-<key 先頭 8>`。同じ入力は同じ key になり、出力が揃って技術検査を通っていれば再描画しない。
key は**描画に影響する入力すべて**（正規化 spec・全素材の sha256・fx パッケージの指紋・版の集合・区間・出力寸法と scale・
ライブラリ版）から作る。キャッシュが古い絵を返さないための要で、入力を足したらここに足す。

manifest の status は running -> succeeded | failed。running のまま残った run（プロセスが強制終了した）は、
次に同じ <out> をロックしたとき failed に直す。
"""
import hashlib
import json
import os
import shutil
import time
import traceback
from pathlib import Path

from ..io import PipelineError, atomic_json, read_json, safe_path, sha256
from ..runner import now, project_lock
from . import SCHEMA_VERSION, VARIANTS, VERSION, fingerprint, probe, timeline
from . import spec as fxspec

RUN_SCHEMA = "fx-run/0.1"
INDEX_SCHEMA = "fx-index/0.1"
DECISIONS = ("accepted", "rejected", "hold")


# ---- 引数の解決 -------------------------------------------------------------

def parse_variants(value):
    """None / "a,b" / ["a", "b"] -> VARIANTS の順に並べた版のタプル。"""
    if value is None:
        return VARIANTS
    names = [n.strip() for n in value.split(",")] if isinstance(value, str) else [str(n).strip() for n in value]
    names = [n for n in names if n]
    unknown = [n for n in names if n not in VARIANTS]
    if unknown or not names:
        raise PipelineError(f"版は {', '.join(VARIANTS)} から選んでください（不明: {unknown or '指定なし'}）")
    return tuple(v for v in VARIANTS if v in names)


def resolve_interval(sp, start=0.0, duration=None):
    """(start, duration, frames)。区間はショットの長さに収まること（収まらない外挿はしない）。"""
    total = sp.data["duration_s"]
    start = float(start)
    if not start >= 0:
        raise PipelineError(f"--start は 0 以上にしてください: {start}")
    length = total - start if duration is None else float(duration)
    if not length > 0:
        raise PipelineError(f"区間の長さが 0 以下です（ショット {total:g} 秒、start {start:g}）。--start / --duration を確認してください")
    if start + length > total + 1e-6:
        raise PipelineError(f"区間 {start:g}〜{start + length:g} 秒がショットの長さ {total:g} 秒を超えています")
    return start, length, timeline.frame_count(length, sp.data["output"]["fps"])


def content_key_for(kind, payload):
    """正規化した payload の sha256（16 進 64 桁）。"""
    return hashlib.sha256(fxspec.canonical_json({"kind": kind, **payload}).encode("utf-8")).hexdigest()


def content_key(kind, sp, assets, extra):
    """描画に影響する入力すべて（正規化 spec・素材 sha256・fx の指紋・extra）から作るキャッシュ key。"""
    return content_key_for(kind, {"spec": sp.data, "assets": {role: item["sha256"] for role, item in assets.items()},
                                  "engine": fingerprint(), **extra})


def render_key(sp, assets, variants, start, duration, n_frames, size, scale, grid, libraries):
    """fx render の key。描画結果（MP4 のバイト列）に影響する入力はここに全部入れる。"""
    return content_key("render", sp, assets, {
        "variants": list(variants), "start": start, "duration": duration, "frames": n_frames,
        "size": list(size), "scale": float(scale), "grid": grid, "libraries": libraries})


def default_out(sp):
    return sp.base_dir / "fx_runs"


# ---- run ---------------------------------------------------------------------

class Run:
    """<out>/<run_id>/ の作成と manifest の更新。作った時点で status=running の manifest が存在する。"""

    def __init__(self, out, key, kind, log_name):
        self.out, self.key = Path(out), key
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        run_id, serial = f"{stamp}-{key[:8]}", 1
        while True:
            try:
                (self.out / run_id).mkdir(parents=True)
                break
            except FileExistsError:  # 同じ秒・同じ key の再実行（--force など）
                serial += 1
                run_id = f"{stamp}-{key[:8]}-{serial}"
        self.dir = self.out / run_id
        self.log_path = self.dir / log_name
        self.manifest = {"schema_version": RUN_SCHEMA, "run_id": run_id, "kind": kind, "key": key,
                         "status": "running", "error": None, "technical_pass": None,
                         "created_at": now(), "finished_at": None}
        self.save()

    @property
    def run_id(self):
        return self.manifest["run_id"]

    def save(self):
        atomic_json(self.dir / "manifest.json", self.manifest)

    def log(self, message):
        with self.log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"{now()} {message}\n")

    def finish(self, technical_pass, error=None):
        """描画は最後まで終えた run を閉じる。技術検査に通れば succeeded、通らなければ failed（理由は error）。"""
        if error:
            self.log(f"failed: {error}")
        self.manifest.update(status="succeeded" if technical_pass else "failed", technical_pass=technical_pass,
                             error=None if technical_pass else error, finished_at=now())
        self.save()

    def fail(self, error):
        text = str(error) if isinstance(error, PipelineError) else f"{type(error).__name__}: {error}"
        if not isinstance(error, PipelineError):
            self.log(traceback.format_exc())
        self.log(f"failed: {text}")
        self.manifest.update(status="failed", error=text[:3000], finished_at=now())
        try:
            self.save()
        except OSError:  # 失敗の記録に失敗しても、元の例外を隠さない
            pass


def close_orphans(out):
    """running のまま残った manifest を failed に直す。<out> のロックを持っているときだけ呼ぶ。"""
    for path in sorted(Path(out).glob("*/manifest.json")):
        try:
            manifest = read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get("status") == "running":
            manifest.update(status="failed", finished_at=now(),
                            error="interrupted: プロセスが終了する前に止まりました（強制終了など）")
            atomic_json(path, manifest)


def load_index(out):
    path = Path(out) / "index.json"
    if not path.exists():
        return {"schema_version": INDEX_SCHEMA, "runs": {}}
    try:
        data = read_json(path)
        if not isinstance(data, dict) or not isinstance(data.get("runs"), dict):
            raise ValueError("形式が違います")
    except (OSError, ValueError) as error:
        raise PipelineError(f"{path} を読めません（{error}）。壊れている場合は削除してください（既存の run は残り、再描画になります）") from error
    return data


def reusable(out, index, key):
    """key に対応する、出力が揃って技術検査を通っている成功 run の manifest（なければ None）。"""
    entry = index["runs"].get(key)
    if not isinstance(entry, dict):
        return None
    try:
        run_dir = safe_path(out, entry["run_id"])
        manifest = read_json(run_dir / "manifest.json")
        if manifest.get("status") != "succeeded" or manifest.get("key") != key or not manifest.get("technical_pass"):
            return None
        outputs = dict(manifest["variants"])
        if manifest.get("grid"):
            outputs["grid"] = manifest["grid"]
        for item in outputs.values():
            file = safe_path(run_dir, item["path"])
            if not item.get("technical_pass") or not file.is_file() or sha256(file) != item["sha256"]:
                return None
    except (PipelineError, OSError, ValueError, KeyError, TypeError):
        return None
    return manifest


def summarize(run_dir, manifest, reused):
    """CLI が stdout に出す短い要約。詳細は manifest.json と render.log。"""
    run_dir = Path(run_dir)
    variants = {name: {"path": str(run_dir / item["path"]), "frames": item["frames"], "technical_pass": item["technical_pass"]}
                for name, item in manifest["variants"].items()}
    grid = manifest.get("grid")
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "technical_pass": manifest["technical_pass"], "interval": manifest["interval"],
            "size": manifest["output"]["size"], "variants": variants,
            "grid": str(run_dir / grid["path"]) if grid else None, "warnings": manifest["warnings"]}


# ---- render ------------------------------------------------------------------

def render_run(spec_path, variants=None, start=0.0, duration=None, out=None, scale=1.0, grid=True, force=False,
               progress=None):
    """spec から版ごとの MP4（と grid.mp4）を描き、run として記録する。同じ入力で成功済みなら再利用する。"""
    from . import assets as fxassets
    from . import compare, render

    began = time.monotonic()
    say = progress or (lambda message: None)
    sp = fxspec.load_spec(spec_path)
    variants = parse_variants(variants)
    start, duration, n_frames = resolve_interval(sp, start, duration)
    size = render.scaled_size(sp, scale)
    assets = fxassets.describe(sp)
    libraries = render.library_versions()
    grid = bool(grid) and len(variants) > 1
    fps = sp.data["output"]["fps"]
    key = render_key(sp, assets, variants, start, duration, n_frames, size, scale, grid, libraries)
    out = (Path(out) if out else default_out(sp)).resolve()
    with project_lock(out):
        close_orphans(out)
        index = load_index(out)
        if not force:
            manifest = reusable(out, index, key)
            if manifest:
                say(f"[fx] 同じ入力の成功 run を再利用します: {manifest['run_id']}（作り直すには --force）")
                return summarize(out / manifest["run_id"], manifest, reused=True)
        run = Run(out, key, "render", "render.log")
        try:
            shutil.copyfile(sp.path, run.dir / "spec.json")
            run.manifest.update(
                spec={"file": "spec.json", "sha256": sp.sha256,
                      "normalized_sha256": hashlib.sha256(fxspec.canonical_json(sp.data).encode("utf-8")).hexdigest()},
                assets=assets,
                engine={"name": "fx", "version": VERSION, "schema_version": SCHEMA_VERSION, "fingerprint": fingerprint()},
                libraries=libraries, seed=sp.data["seed"],
                interval={"start": start, "duration": duration, "fps": fps, "frames": n_frames},
                output={"size": list(size), "scale": float(scale), "crf": sp.data["output"]["crf"]},
                variants={}, grid=None, warnings=[])
            run.save()
            run.log(f"render start key={key} variants={','.join(variants)} start={start:g} duration={duration:g} "
                    f"frames={n_frames} size={size[0]}x{size[1]}")
            ctx = render.prepare(sp, scale)
            run.manifest["warnings"] = list(ctx.warnings)
            for warning in ctx.warnings:
                run.log(f"warning: {warning}")
            notes = ctx.variant_notes()
            has_audio = ctx.audio_path() is not None
            expect = {"width": size[0], "height": size[1], "fps": fps, "frames": n_frames, "audio": has_audio,
                      "audio_s": n_frames / fps if has_audio else None, "bt709": True}
            for variant in variants:
                path = run.dir / f"{variant}.mp4"
                result = render.render_variant(ctx, variant, path, start, duration, log_path=run.log_path)
                checks = probe.check_video(path, **expect)
                run.manifest["variants"][variant] = {
                    "path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                    "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"],
                    "features": notes[variant]["features"], "empty": notes[variant]["empty"],
                    **({"note": notes[variant]["note"]} if "note" in notes[variant] else {})}
                run.save()
                run.log(f"{variant}: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
                say(f"[fx] {variant}: {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
            if grid:
                path = run.dir / "grid.mp4"
                sources = {v: run.dir / f"{v}.mp4" for v in variants}
                result = compare.write_grid(path, sources, notes, size, fps, ctx.crf, n_frames,
                                            render.audio_for(ctx, start, n_frames), run.log_path)
                checks = probe.check_video(path, **expect)
                run.manifest["grid"] = {"path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                                        "technical_pass": checks["pass"], "checks": checks,
                                        "variants": list(variants), "render_seconds": result["seconds"]}
                run.save()
                run.log(f"grid: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
                say(f"[fx] grid: {checks['frames']} フレーム technical_pass={checks['pass']}")
            items = {**run.manifest["variants"], **({"grid": run.manifest["grid"]} if run.manifest["grid"] else {})}
            passed = all(item["technical_pass"] for item in items.values())
            if not passed:
                bad = {name: item["checks"]["failures"] for name, item in items.items() if not item["technical_pass"]}
                message = (f"技術検査に失敗しました（{json.dumps(bad, ensure_ascii=False)[:800]}）。"
                           f"詳細: {run.dir / 'manifest.json'}")
                run.finish(False, message)
                raise PipelineError(message)
            run.finish(True)
            index["runs"][key] = {"run_id": run.run_id, "finished_at": run.manifest["finished_at"]}
            index["schema_version"] = INDEX_SCHEMA
            atomic_json(out / "index.json", index)
            run.log(f"render done in {time.monotonic() - began:.1f}s")
        except BaseException as error:
            if run.manifest["status"] == "running":
                run.fail(error)
            raise
    result = summarize(run.dir, run.manifest, reused=False)
    result["seconds"] = round(time.monotonic() - began, 1)
    return result


# ---- stills ------------------------------------------------------------------

def stills(spec_path, times, variants=None, out=None, scale=1.0):
    """行=時刻・列=版の静止画シートを <out>/stills-<key8>.png に書く（同じ入力は同じファイル名）。"""
    from . import assets as fxassets
    from . import compare, render

    began = time.monotonic()
    sp = fxspec.load_spec(spec_path)
    variants = parse_variants(variants)
    times = [float(t) for t in times]
    total = sp.data["duration_s"]
    if not times:
        raise PipelineError("--times に 1 つ以上の時刻（秒）を指定してください")
    bad = [t for t in times if not 0 <= t <= total]
    if bad:
        raise PipelineError(f"時刻がショットの長さ 0〜{total:g} 秒の外です: {bad}")
    size = render.scaled_size(sp, scale)
    assets = fxassets.describe(sp)
    key = content_key("stills", sp, assets, {"variants": list(variants), "times": times, "size": list(size),
                                              "scale": float(scale), "libraries": render.library_versions()})
    ctx = render.prepare(sp, scale)
    sheet = compare.stills_sheet(ctx, times, variants, ctx.variant_notes())
    path = (Path(out) if out else default_out(sp)).resolve() / f"stills-{key[:8]}.png"
    compare.save_png_atomic(sheet, path)
    return {"path": str(path), "times": times, "variants": list(variants), "cell": list(size),
            "sheet": [sheet.shape[1], sheet.shape[0]], "warnings": list(ctx.warnings),
            "seconds": round(time.monotonic() - began, 1)}


# ---- accept ------------------------------------------------------------------

def accept(run_dir, variant, decision, by, note=None):
    """人間の判定を <out>/acceptance.jsonl に 1 行追記する（既存の行は変えない）。technical_pass とは別物。

    判定は特定の出力ファイル（sha256）に対するもの。accepted は技術検査に通った版にだけ付けられる。
    """
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"run ディレクトリではありません（manifest.json がない）: {run_dir}")
    manifest = read_json(manifest_path)
    if decision not in DECISIONS:
        raise PipelineError(f"--decision は {', '.join(DECISIONS)} のいずれかにしてください: {decision!r}")
    if not isinstance(by, str) or not by.strip():
        raise PipelineError("--by に判定者の名前を指定してください")
    if manifest.get("status") != "succeeded" and decision == "accepted":
        raise PipelineError(f"この run は {manifest.get('status')} です。成功した run だけを accepted にできます")
    entry = manifest.get("variants", {}).get(variant)
    if entry is None:
        raise PipelineError(f"この run に版 {variant!r} はありません: {', '.join(manifest.get('variants', {})) or '(なし)'}")
    if decision == "accepted" and not entry.get("technical_pass"):
        raise PipelineError(f"版 {variant!r} は技術検査に通っていないため accepted にできません（rejected か hold にしてください）")
    video = run_dir / entry["path"]
    if not video.is_file() or sha256(video) != entry["sha256"]:
        raise PipelineError(f"{entry['path']} が manifest の記録と違うか、存在しません。判定対象の出力が差し替わっていないか確認してください")
    record = {"at": now(), "run_id": manifest["run_id"], "variant": variant, "decision": decision, "by": by.strip(),
              "note": note, "video_sha256": entry["sha256"], "key": manifest["key"]}
    path = run_dir.parent / "acceptance.jsonl"
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    if path.is_file() and path.stat().st_size > 0:
        with path.open("rb") as stream:
            stream.seek(-1, os.SEEK_END)
            if stream.read(1) != b"\n":  # 途中で切れた最終行があっても、新しい判定を同じ行に連結しない
                line = b"\n" + line
    with path.open("ab") as stream:
        stream.write(line)
        stream.flush()
        os.fsync(stream.fileno())
    return {"acceptance": str(path), **record}
