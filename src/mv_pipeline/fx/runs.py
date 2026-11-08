"""fx の run ディレクトリ・manifest・キャッシュ（index.json）・静止画・accept。

出力先 <out> の構成（既定は spec のあるディレクトリの fx_runs/）:

    <out>/<UTC run_id>/   spec.json（写し）manifest.json base.mp4 layers.mp4 light.mp4 both.mp4 grid.mp4 render.log
    <out>/index.json      キャッシュ key -> run_id（成功した run だけ。失敗しても過去の成功 run と index は壊れない）
    <out>/acceptance.jsonl  人間の判定（追記のみ。technical_pass とは別物）
    <out>/stills-<key8>.png   fx stills のシート
    <out>/compare-<run_id>-<key8>.mp4 / .json   fx compare の比較動画と記録（--out を省いたとき。run の親に置く）

run_id = `20261024T120000Z-<key 先頭 8>`。同じ入力は同じ key になり、出力が揃って技術検査を通っていれば再描画しない。
key は**描画に影響する入力すべて**（正規化 spec・全素材の sha256・fx パッケージの指紋・版の集合・区間・出力寸法と scale・
ライブラリ版）から作る。キャッシュが古い絵を返さないための要で、入力を足したらここに足す。
spec の look は正規化 spec に入るので key に入る。`--no-look` は spec から look を外した有効 spec で key を作るため、
look 有りの run とは key が分かれ（キャッシュが混ざらない）、spec に look が無いときは付けても付けなくても同じ key になる。

manifest の status は running -> succeeded | failed。running のまま残った run（プロセスが強制終了した）は、
次に同じ <out> をロックしたとき failed に直す。
"""
import hashlib
import json
import os
import shutil
import time
import traceback
from contextlib import ExitStack
from pathlib import Path

from ..io import PipelineError, atomic_json, read_json, safe_path, sha256
from ..runner import now, project_lock
from .. import fx
from . import VARIANTS, fingerprint, probe, timeline
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


def resolve_look(sp, no_look=False):
    """(有効な spec, look の状態)。状態は applied（spec の look を描く）/ disabled（--no-look で外した）/ absent（spec に look が無い）。"""
    if sp.data["look"] is None:
        return sp, "absent"
    return (fxspec.without_look(sp), "disabled") if no_look else (sp, "applied")


def resolve_times(sp, times):
    """stills の --times を float の list にする。空や、ショットの長さ（0〜duration_s 秒）の外の時刻は止める。"""
    times = [float(t) for t in times]
    total = sp.data["duration_s"]
    if not times:
        raise PipelineError("--times に 1 つ以上の時刻（秒）を指定してください")
    bad = [t for t in times if not 0 <= t <= total]
    if bad:
        raise PipelineError(f"時刻がショットの長さ 0〜{total:g} 秒の外です: {bad}")
    return times


def spec_record(sp):
    """manifest の spec の欄。写しのファイル名と、元ファイル・正規化した内容の sha256。"""
    return {"file": "spec.json", "sha256": sp.sha256,
            "normalized_sha256": hashlib.sha256(fxspec.canonical_json(sp.data).encode("utf-8")).hexdigest()}


def engine_record(package, name):
    """manifest の engine の欄。package は VERSION / SCHEMA_VERSION / fingerprint() を持つ fx か dance。"""
    return {"name": name, "version": package.VERSION, "schema_version": package.SCHEMA_VERSION,
            "fingerprint": package.fingerprint()}


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
        """例外で途中で終わった run を failed にする。記録（log・manifest）に失敗しても、元の例外を隠さない。"""
        text = str(error) if isinstance(error, PipelineError) else f"{type(error).__name__}: {error}"
        self.manifest.update(status="failed", error=text[:3000], finished_at=now())
        try:
            if not isinstance(error, PipelineError):
                self.log("".join(traceback.format_exception(error)))
            self.log(f"failed: {text}")
            self.save()
        except OSError:
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


class Session:
    """<out> をロックし、同じ key の成功 run があれば再利用、無ければ新しい run を開く。fx / dance / legacy の run で共通。

        with Session(out, key, "render", "render.log", say=say, tag="[fx]", force=force) as session:
            if session.reused:                       # 再利用できる成功 run の manifest（dir は session.reused_dir）
                return summarize(session.reused_dir, session.reused, reused=True)
            run = session.run                        # status=running の Run。manifest を埋めて描く
            ...
            session.succeed()                        # run を succeeded にして index.json に登録

    with を抜けるまで <out> のロックを持つ。本体が例外で抜けたら、running のままの run を failed にして例外をそのまま通す。
    技術検査に落ちた run は本体が run.finish(False, message) で閉じてから PipelineError を投げる。
    cache=False は index.json を読まず書かず、再利用もしない（legacy のようにキャッシュしない run）。
    accept_reuse(run_dir, manifest) -> bool を渡すと、再利用の前に出力がそろっているかなどを呼び出し側が確かめられる（False なら描き直す）。
    """

    def __init__(self, out, key, kind, log_name, *, say, tag, force=False, cache=True, accept_reuse=None):
        self.out, self.key, self.kind, self.log_name = Path(out), key, kind, log_name
        self.say, self.tag, self.force, self.cache, self.accept_reuse = say, tag, force, cache, accept_reuse
        self.index = self.run = self.reused = self.reused_dir = None
        self._stack = ExitStack()

    def __enter__(self):
        self._stack.__enter__()
        try:
            self._stack.enter_context(project_lock(self.out))
            close_orphans(self.out)
            manifest = None
            if self.cache:
                self.index = load_index(self.out)
                manifest = None if self.force else reusable(self.out, self.index, self.key)
            if manifest and self.accept_reuse is not None and not self.accept_reuse(self.out / manifest["run_id"], manifest):
                self.say(f"{self.tag} 同じ入力の成功 run {manifest['run_id']} がありますが、出力がそろっていないので描き直します")
                manifest = None
            if manifest:
                self.say(f"{self.tag} 同じ入力の成功 run を再利用します: {manifest['run_id']}（作り直すには --force）")
                self.reused, self.reused_dir = manifest, self.out / manifest["run_id"]
            else:
                self.run = Run(self.out, self.key, self.kind, self.log_name)
        except BaseException:
            self._stack.close()
            raise
        return self

    def __exit__(self, exc_type, error, tb):
        try:
            if error is not None and self.run is not None and self.run.manifest["status"] == "running":
                self.run.fail(error)
        finally:
            self._stack.__exit__(exc_type, error, tb)
        return False

    def succeed(self):
        """run を succeeded で閉じ、cache なら index.json に key -> run_id を登録する。"""
        self.run.finish(True)
        if self.cache:
            self.index["runs"][self.key] = {"run_id": self.run.run_id, "finished_at": self.run.manifest["finished_at"]}
            self.index["schema_version"] = INDEX_SCHEMA
            atomic_json(self.out / "index.json", self.index)


def summarize(run_dir, manifest, reused):
    """CLI が stdout に出す短い要約。詳細は manifest.json と render.log。"""
    run_dir = Path(run_dir)
    variants = {name: {"path": str(run_dir / item["path"]), "frames": item["frames"], "technical_pass": item["technical_pass"]}
                for name, item in manifest["variants"].items()}
    grid = manifest.get("grid")
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "technical_pass": manifest["technical_pass"], "interval": manifest["interval"],
            "size": manifest["output"]["size"], "look": manifest.get("look"), "depth": manifest.get("depth"), "variants": variants,
            "grid": str(run_dir / grid["path"]) if grid else None, "warnings": manifest["warnings"]}


# ---- render ------------------------------------------------------------------

def render_run(spec_path, variants=None, start=0.0, duration=None, out=None, scale=1.0, grid=True, force=False,
               progress=None, no_look=False):
    """spec から版ごとの MP4（と grid.mp4）を描き、run として記録する。同じ入力で成功済みなら再利用する。
    no_look=True なら spec の look を外して描く（比較用。manifest の look は disabled）。"""
    from . import assets as fxassets
    from . import compare, render

    began = time.monotonic()
    say = progress or (lambda message: None)
    sp, look_state = resolve_look(fxspec.load_spec(spec_path), no_look)
    variants = parse_variants(variants)
    start, duration, n_frames = resolve_interval(sp, start, duration)
    size = render.scaled_size(sp, scale)
    assets = fxassets.describe(sp)
    libraries = render.library_versions()
    grid = bool(grid) and len(variants) > 1
    fps = sp.data["output"]["fps"]
    key = render_key(sp, assets, variants, start, duration, n_frames, size, scale, grid, libraries)
    out = (Path(out) if out else default_out(sp)).resolve()
    with Session(out, key, "render", "render.log", say=say, tag="[fx]", force=force) as session:
        if session.reused:
            # 再利用した run の manifest.look は作ったときの呼び出しの状態。この呼び出しの状態（映像は同じ）を返す
            return {**summarize(session.reused_dir, session.reused, reused=True), "look": look_state}
        run = session.run
        shutil.copyfile(sp.path, run.dir / "spec.json")
        run.manifest.update(
            spec=spec_record(sp), assets=assets, engine=engine_record(fx, "fx"), libraries=libraries,
            seed=sp.data["seed"], look=look_state,
            interval={"start": start, "duration": duration, "fps": fps, "frames": n_frames},
            output={"size": list(size), "scale": float(scale), "crf": sp.data["output"]["crf"]},
            variants={}, grid=None, warnings=[])
        run.save()
        run.log(f"render start key={key} variants={','.join(variants)} start={start:g} duration={duration:g} "
                f"frames={n_frames} size={size[0]}x{size[1]} look={look_state}")
        if look_state == "disabled":
            say("[fx] --no-look: spec の look を外して描きます")
        ctx = render.prepare(sp, scale)
        run.manifest["warnings"] = list(ctx.warnings)
        run.manifest["depth"] = ctx.depth_info
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
        session.succeed()
        run.log(f"render done in {time.monotonic() - began:.1f}s")
    result = summarize(run.dir, run.manifest, reused=False)
    result["seconds"] = round(time.monotonic() - began, 1)
    return result


# ---- stills ------------------------------------------------------------------

def stills(spec_path, times, variants=None, out=None, scale=1.0, no_look=False, guides=None, grid_step=None,
           reference=None, reference_offset=0.0, reference_label="reference"):
    """行=時刻・列=版の静止画シートを <out>/stills-<key8>.png に書く（同じ入力は同じファイル名）。
    no_look=True なら spec の look を外して描く（look の有無はファイル名＝key に出る）。
    guides: 各マスに重ねる印（"grid,lights,masks" の部分集合。guides.parse_kinds）。grid_step は格子の間隔（省けば見えている幅から）。
    reference: 基準の動画。先頭の列に、時刻 reference_offset + t に最も近いコマを置く（印は重ねない）。"""
    from . import assets as fxassets
    from . import compare, render
    from . import guides as fxguides

    began = time.monotonic()
    sp, look_state = resolve_look(fxspec.load_spec(spec_path), no_look)
    variants = parse_variants(variants)
    times = resolve_times(sp, times)
    size = render.scaled_size(sp, scale)
    assets = fxassets.describe(sp)
    kinds = fxguides.parse_kinds(guides)
    if grid_step is not None and not 0 < grid_step <= 1:
        raise PipelineError(f"--grid-step は 0 より大きく 1 以下（元画像の正規化座標）にしてください: {grid_step}")
    extra = {"variants": list(variants), "times": times, "size": list(size), "scale": float(scale), "libraries": render.library_versions()}
    if kinds:
        extra["guides"] = {"kinds": list(kinds), "grid_step": grid_step}
    ref = None
    if reference is not None:
        ref = reference_cells(reference, times, reference_offset, size, reference_label, sp.data["output"]["fps"])
        extra["reference"] = {key: ref[key] for key in ("sha256", "offset_s", "label", "frames_used")}
    key = content_key("stills", sp, assets, extra)
    ctx = render.prepare(sp, scale)
    sheet = compare.stills_sheet(ctx, times, variants, ctx.variant_notes(), kinds, grid_step, ref)
    path = (Path(out) if out else default_out(sp)).resolve() / f"stills-{key[:8]}.png"
    compare.save_png_atomic(sheet, path)
    result = {"path": str(path), "times": times, "variants": list(variants), "cell": list(size), "look": look_state,
              "sheet": [sheet.shape[1], sheet.shape[0]], "warnings": list(ctx.warnings), "depth": ctx.depth_info}
    if kinds:
        result["guides"] = list(kinds)
    if ref:
        result["reference"] = {key: ref[key] for key in ("path", "sha256", "offset_s", "label", "fps", "fps_used", "frames_used",
                                                         "warnings")}
    return {**result, "seconds": round(time.monotonic() - began, 1)}


def _ascii_label(label, option):
    if not label or not all(32 <= ord(ch) < 127 for ch in label):
        raise PipelineError(f"{option} は ASCII の文字で書いてください（ラベルの字体に日本語が無いため）: {label!r}")
    return label


def reference_cells(path, times, offset, size, label, fps=None):
    """stills の基準の列: 各時刻 offset + t に最も近いコマを size に縮めたもの（mv inspect と同じ読み手。復号順のコマ番号）。

    fps（spec の fps）を渡すと、読んだ fps がそれと同じとみなせるとき、丸めていない fps でコマを数える（reference_fps）。"""
    import math

    from .. import validate
    from ..inspection import video
    label = _ascii_label(label, "--reference-label")
    path = Path(path).resolve()
    info = video.describe(path)
    rate = reference_fps(info["fps"], fps)
    indices = [validate.frame_at(offset + t, rate) for t in times]
    if min(indices) < 0:
        raise PipelineError(f"--reference-offset {offset:g} では基準の動画の 0 秒より前を指します（時刻 {min(times):g}）")
    grabbed = video.grab(path, indices, info, size=tuple(size))
    missing = [t for t, i in zip(times, indices) if i not in grabbed]
    if missing:
        raise PipelineError(f"基準の動画に、時刻 {missing} のコマがありません（offset {offset:g} 秒。--reference-offset と動画の長さを確かめてください）")
    warnings = []
    if not math.isclose(info["width"] / info["height"], size[0] / size[1], rel_tol=0.01):
        warnings.append(f"基準の動画 {info['width']}x{info['height']} は出力 {size[0]}x{size[1]} と縦横比が違い、引き伸ばして並べています")
    return {"label": label, "path": str(path), "sha256": sha256(path), "offset_s": float(offset), "fps": info["fps"],
            "fps_used": rate, "frames": [grabbed[i] for i in indices], "frames_used": indices, "times": [i / rate for i in indices],
            "warnings": warnings}


# ---- compare -------------------------------------------------------------------

COMPARE_SCHEMA = "fx-compare/0.1"
FPS_READ_TOLERANCE = 0.005   # ffmpeg は fps を小数 2 桁で出す（23.976 → 23.98）。読める幅はこの丸めまで


def same_fps(measured, fps):
    """読んだ fps（ffmpeg の表示。小数 2 桁）が run の fps と同じか。丸めの幅（0.005）より大きい差は本当に違う fps。

    24 と 23.976（23.98 と出る）を同じとみなすと、1 対 1 に並べたコマが 60 秒で 1 コマ以上ずれる。"""
    return abs(float(measured) - float(fps)) <= FPS_READ_TOLERANCE + 1e-9


def reference_fps(measured, fps=None):
    """基準の動画のコマを数える fps。読んだ fps が fps と同じとみなせれば、丸めていない fps（読んだ値は小数 2 桁に丸めてあり、
    24000/1001 の動画を 23.98 で数えると 300 秒で 1 コマ、600 秒で 2 コマずれる）。違うか fps が無ければ、読んだ fps。"""
    return float(fps) if fps is not None and same_fps(measured, fps) else float(measured)


def compare_run(run_dir, reference, offset=0.0, label="reference", out=None, progress=None):
    """成功した render の run の各版と基準の動画を 1 本に並べた比較動画を書く（再描画しない）。

    基準の動画のコマ round((offset + 区間の開始 + i/fps)·fps) を、run の i コマ目と並べる（基準の動画と run の fps は同じであること）。
    出力の既定は <run の親>/compare-<run_id>-<key8>.mp4 と、隣に同じ名前の .json（入力の sha256・並び・技術検査）。
    """
    from .. import validate
    from ..inspection import video
    from . import compare, render

    began = time.monotonic()
    say = progress or (lambda message: None)
    label = _ascii_label(label, "--reference-label")
    run_dir = Path(run_dir).resolve()
    manifest_path = run_dir / "manifest.json"
    if not manifest_path.is_file():
        raise PipelineError(f"run の manifest.json が見つかりません: {run_dir}")
    manifest = read_json(manifest_path)
    if manifest.get("schema_version") != RUN_SCHEMA or manifest.get("status") != "succeeded" or "interval" not in manifest:
        raise PipelineError(f"成功した fx render の run を指定してください（{manifest.get('schema_version')} / {manifest.get('status')}）")
    sources, recorded = {}, {}
    for name, item in manifest.get("variants", {}).items():
        path = run_dir / item["path"]
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise PipelineError(f"run の {item['path']} が manifest の記録と違います（消えたか、書き換わっています）")
        sources[name], recorded[name] = path, {"path": item["path"], "sha256": item["sha256"], "empty": item.get("empty", False)}
    if not sources:
        raise PipelineError("run に版の動画がありません")
    size = tuple(manifest["output"]["size"])
    fps, n_frames, start = manifest["interval"]["fps"], manifest["interval"]["frames"], manifest["interval"]["start"]
    reference = Path(reference).resolve()
    info = video.describe(reference)
    if not same_fps(info["fps"], fps):
        raise PipelineError(f"基準の動画の fps {info['fps']:g} が run の fps {fps:g} と違います（コマを 1 対 1 に並べるため、同じ fps の動画に"
                            f"してください。24 と 23.976 のように少しでも違うと、だんだんずれます）")
    rate = reference_fps(info["fps"], fps)      # 同じとみなしたので、丸めていない run の fps で数える
    first = validate.frame_at(offset + start, rate)
    if first < 0:
        raise PipelineError(f"--reference-offset {offset:g} では基準の動画の 0 秒より前を指します")
    reference_sha = sha256(reference)
    key = content_key_for("compare", {"run_id": manifest["run_id"], "variants": {v: r["sha256"] for v, r in recorded.items()},
                                      "reference": reference_sha, "offset": float(offset), "first_frame": first, "label": label,
                                      "engine": fingerprint()})
    out = Path(out).resolve() if out else run_dir.parent / f"compare-{manifest['run_id']}-{key[:8]}.mp4"
    layout = compare.compare_layout(size)
    top = layout["tiles"]["reference"][2:]
    audio_source = next((path for path in sources.values() if probe.inspect(path)["audio"]), None)
    audio = {"path": audio_source, "start": 0.0, "duration": n_frames / fps} if audio_source else None
    notes = {v: {"empty": r["empty"]} for v, r in recorded.items()}

    def footer(i):
        return f"frame {i}   t={start + i / fps:.3f}s   {label} {(first + i) / rate:.3f}s"

    reader = video.frames(reference, top, info, start=first, end=first + n_frames)
    say(f"[fx] compare: {', '.join(sources)} + {label}（{n_frames} フレーム）")
    try:
        written = compare.write_compare(out, (image for _, image in reader), sources, notes, size, fps,
                                        manifest["output"].get("crf", 18), n_frames, label, footer, audio)
    finally:
        reader.close()          # 途中で止まっても、基準の動画の復号（ffmpeg）を止める
    checks = probe.check_video(out, width=size[0], height=size[1], fps=fps, frames=n_frames, audio=audio is not None,
                               audio_s=n_frames / fps if audio else None, bt709=True)
    record = {"schema_version": COMPARE_SCHEMA, "created_at": now(), "key": key,
              "run": {"run_id": manifest["run_id"], "dir": str(run_dir), "interval": manifest["interval"], "variants": recorded},
              "reference": {"path": str(reference), "sha256": reference_sha, "label": label, "offset_s": float(offset),
                            "fps": info["fps"], "fps_used": rate, "first_frame": first, "frames": n_frames},
              "layout": {name: list(tile) for name, tile in layout["tiles"].items()},
              "output": {"path": str(out), "sha256": sha256(out), "frames": checks["frames"], "technical_pass": checks["pass"],
                         "checks": checks, "render_seconds": written["seconds"]},
              "engine": {"version": fx.VERSION, "fingerprint": fingerprint()}}
    atomic_json(out.with_suffix(".json"), record)
    if not checks["pass"]:
        raise PipelineError(f"比較動画が技術検査に通りませんでした（{checks['failures']}）。詳細: {out.with_suffix('.json')}")
    return {"path": str(out), "record": str(out.with_suffix(".json")), "frames": checks["frames"], "technical_pass": True,
            "reference": {"first_frame": first, "offset_s": float(offset)}, "variants": list(sources),
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
