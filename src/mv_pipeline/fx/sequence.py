"""fx-timeline/0.1: 複数の fx ショット（fx-shot の spec）を時刻順に並べ、つなぎで 1 本の動画にする（実験的。docs/fx.md）。

ショットは start の昇順で、次のショットの start（最後は duration_s）まで。各ショットは fx render の 1 コマ（出力の大きさで描く。
spec の look と overlays 込みの 8 ビットの RGB）を、ショットの中の時刻 offset_s + (t − start) で描き、表示の値でつなぐ。
つなぎの式と順番は実制作のレンダラと同じ（ease は smoothstep）:

    xfade   t < start + duration_s の間、前のショットを描き続けて 前·(1 − a) + 今·a（a = ease((t − start)/duration_s)）
    black   t < start + duration_s の間、今·ease((t − start)/duration_s)（黒から上げる）
    flash   t − start < window_s の間、今 + (色 − 今)·strength·exp(−(t − start)·rate)（既定 0.75・(235, 210, 255)・0.30 秒・13）

つないだ後に timeline の look（fx-shot と同じ grade・vignette・grain。時刻は timeline の t）を掛け、0〜255 に切って切り捨てる
（実制作と同じく、色調整・周辺減光・粒状感はつなぎの後）。snap は start をいちばん近い拍に寄せる（0 以下は 0、終わりの 0.01 秒以内は終わり。
同じ距離なら前の拍）。run には timeline.mp4・timeline.json（dance-timeline/0.1 の形に seam_info・flash_info・beats を足したもの。`mv gate check` が切れ目とフラッシュを拍と照合できる）・
manifest.json を残す。
"""
import bisect
import hashlib
import json
import math
import shutil
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import numpy as np

from ..io import PipelineError, read_json, safe_path, sha256
from . import spec as fxspec
from . import timeline as fxtimeline

SCHEMA_VERSION = "fx-timeline/0.1"
RECORD_SCHEMA = "dance-timeline/0.1"   # 0.2 は dance の描画の記録（frames・layers）が要る形
_SCHEMA = Path(__file__).with_name("fx_timeline.schema.json")
FLASH = {"strength": 0.75, "color": [235.0, 210.0, 255.0], "window_s": 0.30, "rate": 13.0}
KEEP_SHOTS = 2          # 前処理済みのショットを持つ数（xfade は前と今の 2 つ）。順に描くので、古いものから捨てる


def ease(x):
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def schema():
    """JSON Schema。look は fx-shot と同じ定義を使う。"""
    data = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    look = fxspec.look_schema()
    data["properties"]["look"] = look["look"]
    data["$defs"].update(look["defs"])
    return data


@dataclass(frozen=True)
class Timeline:
    data: dict
    base_dir: Path
    path: Path | None
    sha256: str
    warnings: tuple
    specs: tuple           # ショットごとの fx の Spec（data["shots"] と同じ順）

    def resolve(self, rel):
        return safe_path(self.base_dir, rel)


# ---- 読み込み ------------------------------------------------------------------------

def load_timeline(path):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"fx timeline not found: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"fx timeline is not valid JSON ({path.name}): {error}") from error
    return parse_timeline(raw, path.resolve().parent, path.resolve(), sha256(path))


def parse_timeline(raw, base_dir, path=None, digest=""):
    from ..dance import beats as dancebeats            # 拍の格子と時刻の参照は dance と同じ書き方

    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"fx timeline schema_version must be {SCHEMA_VERSION!r}")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("fx timeline is invalid:\n" + "\n".join(lines))
    base_dir = Path(base_dir).resolve()
    out = raw["output"]
    fps = float(out.get("fps", 24))
    duration = float(raw["duration_s"])
    grid = dancebeats.load_grid(raw.get("beats"), lambda rel: safe_path(base_dir, rel))
    shots = []
    for index, shot in enumerate(raw["shots"]):
        start = dancebeats.resolve_time(shot["start"], grid, f"shots[{index}] の start", fps)
        snapped = bool(shot.get("snap", False))
        if snapped and grid is None:
            raise PipelineError(f"fx timeline: shots[{index}] の snap には beats が必要です")
        shots.append({"spec": shot["spec"], "start": snap(start, grid.beats, duration) if snapped else start, "requested": start,
                      "snap": snapped, "offset_s": float(shot.get("offset_s", 0.0)), "variant": shot.get("variant", "both"),
                      "transition_in": _transition(shot.get("transition_in", {"type": "cut"})),
                      "beat_exempt": bool(shot.get("beat_exempt", False)), "align": shot.get("align", "beat")})
    for index, shot in enumerate(shots):
        shot["end"] = shots[index + 1]["start"] if index + 1 < len(shots) else duration
    specs = tuple(fxspec.load_spec(safe_path(base_dir, shot["spec"])) for shot in shots)
    look = None if raw.get("look") is None else fxspec.normalize_look(raw["look"])
    data = {"schema_version": SCHEMA_VERSION,
            "output": {"width": int(out["width"]), "height": int(out["height"]), "fps": fps, "crf": int(out.get("crf", 18))},
            "duration_s": duration, "beats": None if grid is None else {"path": grid.path, **grid.record()},
            "audio": None if "audio" not in raw else {"path": raw["audio"]["path"], "offset_s": float(raw["audio"].get("offset_s", 0.0))},
            "look": look, "shots": shots}
    warnings = _validate(data, specs) + fxspec.look_warnings(look)
    if data["audio"]:
        safe_path(base_dir, data["audio"]["path"])
    return Timeline(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings), specs=specs)


def _transition(raw):
    kind = raw["type"]
    if kind == "flash":
        return {"type": "flash", "strength": float(raw.get("strength", FLASH["strength"])),
                "color": [float(v) for v in raw.get("color", FLASH["color"])],
                "window_s": float(raw.get("window_s", FLASH["window_s"])), "rate": float(raw.get("rate", FLASH["rate"]))}
    if kind in ("xfade", "black"):
        return {"type": kind, "duration_s": float(raw["duration_s"])}
    return {"type": "cut"}


def snap(t, beats, duration):
    """いちばん近い拍（実制作の snap と同じ: 0 以下は 0、終わりの 0.01 秒以内は終わり。同じ距離なら前の拍）。"""
    if t <= 0:
        return 0.0
    if t >= duration - 0.01:
        return duration
    values = np.asarray(beats, dtype=np.float64)
    return float(values[np.argmin(np.abs(values - t))])


def _validate(data, specs):
    shots, duration, fps = data["shots"], data["duration_s"], data["output"]["fps"]
    if shots[0]["start"] > 1e-9:
        raise PipelineError(f"fx timeline: 最初のショットは 0 秒から始めてください（{shots[0]['start']:g} 秒）")
    warnings = []
    for index, (shot, sp) in enumerate(zip(shots, specs)):
        where = f"shots[{index}]（{shot['spec']}）"
        if shot["end"] <= shot["start"] + 1e-9:
            moved = "（snap で寄せた後）" if shot["snap"] or (index + 1 < len(shots) and shots[index + 1]["snap"]) else ""
            raise PipelineError(f"fx timeline: {where} の長さが 0 以下です{moved}（start {shot['start']:g} 秒、終わり {shot['end']:g} 秒）。"
                                "start は昇順にし、最後のショットは duration_s より前から始めてください")
        transition, length = shot["transition_in"], shot["end"] - shot["start"]
        if index == 0 and transition["type"] == "xfade":
            raise PipelineError("fx timeline: 最初のショットは xfade できません（前のショットがありません。黒から上げるなら black）")
        if transition["type"] in ("xfade", "black") and transition["duration_s"] > length + 1e-9:
            raise PipelineError(f"fx timeline: {where} の {transition['type']} の duration_s {transition['duration_s']:g} 秒が"
                                f"ショットの長さ {length:g} 秒より長いです")
        local_end = shot["offset_s"] + length
        if local_end > sp.data["duration_s"] + 1e-6:
            warnings.append(f"{where}: spec の長さ {sp.data['duration_s']:g} 秒を超えて {local_end:g} 秒まで描きます"
                            "（カメラのキーなどは spec の終わりの値のまま）")
        if abs(sp.data["output"]["fps"] - fps) > 1e-9:
            warnings.append(f"{where}: spec の fps {sp.data['output']['fps']:g} が timeline の fps {fps:g} と違います"
                            "（spec の look の粒状感のコマなど、spec の fps で数えるものがあります）")
        if shot["snap"] and abs(shot["start"] - shot["requested"]) > 0.25:
            warnings.append(f"shots[{index}]: snap で start が {shot['requested']:g} → {shot['start']:g} 秒に動きました")
    return warnings


# ---- 1 コマの計画（画を描かずに決まる。実制作の式との比較にも使う） -------------------------------------

def shot_index(data, t):
    """時刻 t のショットの番号（start ≤ t の最後のショット。0 秒より前は最初のショット）。"""
    return max(0, bisect.bisect_right([shot["start"] for shot in data["shots"]], t) - 1)


def plan_frame(data, t):
    """時刻 t の ([(ショットの番号, ショットの中の時刻, 重み)], 黒の倍率, フラッシュ (量, 色) か None)。"""
    index = shot_index(data, t)
    shot = data["shots"][index]
    local = lambda i: data["shots"][i]["offset_s"] + (t - data["shots"][i]["start"])  # noqa: E731
    layers = [(index, local(index), 1.0)]
    transition, since = shot["transition_in"], t - shot["start"]
    dim, flash = 1.0, None
    if transition["type"] == "xfade" and index > 0 and t < shot["start"] + transition["duration_s"]:
        a = ease(since / transition["duration_s"])
        layers = [(index - 1, local(index - 1), 1 - a), (index, local(index), a)]
    elif transition["type"] == "black" and t < shot["start"] + transition["duration_s"]:
        dim = ease(since / transition["duration_s"])
    if transition["type"] == "flash" and since < transition["window_s"]:
        flash = (transition["strength"] * math.exp(-since * transition["rate"]), transition["color"])
    return layers, dim, flash


def blend(layers, dim, flash):
    """plan_frame の重みで、ショットの画（表示の値 0..255）をつなぐ。layers は [(画, 重み)]。float32 を返す（まだ切らない）。"""
    if len(layers) == 2:
        (before, weight_before), (now, weight_now) = layers
        image = before.astype(np.float32) * weight_before + now.astype(np.float32) * weight_now
    else:
        image = layers[0][0].astype(np.float32)
    if dim != 1.0:
        image = image * dim
    if flash is not None:
        amount, color = flash
        image = image + (np.asarray(color, np.float32) - image) * amount
    return image


def finish(image, look=None, t=0.0):
    """つないだ画に timeline の look（fx の look.Look か None）を掛け、0〜255 に切って切り捨てた uint8 にする。"""
    if look is None:
        return np.clip(image, 0, 255).astype(np.uint8)
    from . import look as fxlook
    return fxlook.finish(look, np.clip(image, 0, 255) / np.float32(255.0), t)


# ---- 描画 ------------------------------------------------------------------------

def output_size(data, scale=1.0):
    """出力の (W, H) × scale を偶数に丸めたもの（fx render と同じ丸め）。"""
    if not 0 < scale <= 4:
        raise PipelineError(f"scale は 0 より大きく 4 以下にしてください: {scale}")
    even = lambda v: max(16, 2 * int(round(v * scale / 2)))  # noqa: E731
    return even(data["output"]["width"]), even(data["output"]["height"])


class Context:
    """timeline を描く状態。ショットの fx の Context は初めて使うときに作り、新しい順に KEEP_SHOTS 個まで持つ（spec が同じなら共有）。"""

    def __init__(self, timeline, scale=1.0):
        from . import look as fxlook
        from . import render
        self.timeline, self.data = timeline, timeline.data
        self.size = output_size(self.data, scale)
        self.fps = self.data["output"]["fps"]
        self.look = None if self.data["look"] is None else fxlook.prepare(self.data["look"], self.size, self.fps)
        self.warnings = list(timeline.warnings)
        self._render, self._contexts, self._warned = render, OrderedDict(), set()

    def shot(self, index):
        name = self.data["shots"][index]["spec"]
        if name in self._contexts:
            self._contexts.move_to_end(name)
            return self._contexts[name]
        ctx = self._render.prepare(self.timeline.specs[index], size=self.size)
        if name not in self._warned:
            self._warned.add(name)
            self.warnings += [f"{name}: {w}" for w in ctx.warnings]
        self._contexts[name] = ctx
        while len(self._contexts) > KEEP_SHOTS:
            self._contexts.popitem(last=False)
        return ctx

    def frame(self, t):
        layers, dim, flash = plan_frame(self.data, t)
        images = [(self._render.frame(self.shot(i), local, self.data["shots"][i]["variant"]), weight) for i, local, weight in layers]
        return finish(blend(images, dim, flash), self.look, t)


def timeline_record(timeline, size, start=0.0, duration=None, frames=None):
    """mv gate check が読む timeline.json（dance-timeline/0.1 の形 + seam_info・flash_info・beats。字幕は無い）。切れ目はショットの頭、フラッシュは flash のつなぎ。
    interval は描いた区間（dance の記録と同じく、長さは頼まれた長さ）。"""
    data = timeline.data
    fps, total, shots = data["output"]["fps"], data["duration_s"], data["shots"]
    length = total - start if duration is None else duration
    count = fxtimeline.frame_count(length, fps) if frames is None else frames
    flashes = [shot for shot in shots if shot["transition_in"]["type"] == "flash"]
    beats = data["beats"]
    return {"schema_version": RECORD_SCHEMA, "size": list(size), "fps": fps, "duration_s": total,
            "interval": {"start": start, "duration": length, "frames": count}, "lyric_fade": "inside", "lyrics": [],
            "ui_regions": [], "beats": None if beats is None else {"beats": beats["beats"], "downbeats": beats["downbeats"]},
            "clips": [{"path": shot["spec"], "start": shot["start"], "end": shot["end"], "kind": "fx-shot",
                       "transition_in": shot["transition_in"], "beat_exempt": shot["beat_exempt"]} for shot in shots],
            "seams": [shot["start"] for shot in shots[1:]], "flashes": [shot["start"] for shot in flashes],
            "seam_info": [{"t": shot["start"], "from": index, "to": index + 1, "kind": shot["transition_in"]["type"],
                           "beat_exempt": shot["beat_exempt"], "align": shot["align"]} for index, shot in enumerate(shots[1:])],
            "flash_info": [{"t": shot["start"], "strength": shot["transition_in"]["strength"], "decay_s": 1 / shot["transition_in"]["rate"],
                            "window_s": shot["transition_in"]["window_s"], "mode": "lerp_display", "beat_exempt": shot["beat_exempt"],
                            "align": shot["align"]} for shot in flashes]}


def plan_text(path):
    """`mv fx timeline plan`: ショットとつなぎの表（秒・いちばん近い拍）。描かない。"""
    timeline = load_timeline(path)
    data = timeline.data
    beats = data["beats"]["beats"] if data["beats"] else None

    def beat_of(t):
        if beats is None:
            return "-"
        index = int(np.argmin(np.abs(np.asarray(beats) - t)))
        return f"{index}{t - beats[index]:+.3f}"

    out = data["output"]
    lines = [f"# {SCHEMA_VERSION}  {out['width']}x{out['height']} {out['fps']:g}fps {data['duration_s']:g} 秒  ショット {len(data['shots'])}"
             f"  look {'あり' if data['look'] else 'なし'}  音声 {data['audio']['path'] if data['audio'] else 'なし'}", "",
             f"{'#':<3} {'start':>8} {'end':>8} {'拍':>10}  {'つなぎ':<18} {'offset':>7}  spec"]
    for index, shot in enumerate(data["shots"]):
        transition = shot["transition_in"]
        text = transition["type"] + (f" {transition['duration_s']:g}s" if "duration_s" in transition else "") \
            + (f" {transition['strength']:g}" if transition["type"] == "flash" else "")
        moved = f"  (snap {shot['requested']:.3f}→)" if shot["snap"] and shot["start"] != shot["requested"] else ""
        lines.append(f"{index:<3} {shot['start']:>8.3f} {shot['end']:>8.3f} {beat_of(shot['start']):>10}  {text:<18} "
                     f"{shot['offset_s']:>7.3f}  {shot['spec']}{moved}")
    lines += [f"注意: {w}" for w in timeline.warnings]
    return "\n".join(lines)


def _meter(say, total):
    began, every = time.monotonic(), max(1, round(total / 20))

    def report(done):
        if say is not None and (done % every == 0 or done == total):
            spent = time.monotonic() - began
            say(f"[fx timeline] {done}/{total} コマ 経過 {spent:.1f} 秒 残り約 {spent / done * (total - done):.1f} 秒")
    return report


def _shot_records(timeline):
    from . import assets as fxassets
    from . import spec_layers
    records = []
    for shot, sp in zip(timeline.data["shots"], timeline.specs):
        record = {"spec": shot["spec"], "sha256": sp.sha256, "assets": {role: item["sha256"] for role, item in fxassets.describe(sp).items()}}
        if sp.data["overlays"]:
            record["overlays"] = spec_layers.describe(sp)
        records.append(record)
    return records


def render_run(path, out=None, start=0.0, duration=None, scale=1.0, force=False, progress=None, quiet=False):
    """timeline.mp4 と timeline.json を run として出す（fx の run と同じ記録・再利用・構造検査）。"""
    from .. import fx
    from . import probe, render, runs

    began = time.monotonic()
    say = progress or (lambda message: None)
    timeline = load_timeline(path)
    data = timeline.data
    fps, total = data["output"]["fps"], data["duration_s"]
    start = float(start)
    length = total - start if duration is None else float(duration)
    if not start >= 0:
        raise PipelineError(f"--start は 0 以上にしてください: {start}")
    if not length > 0 or start + length > total + 1e-6:
        raise PipelineError(f"区間 {start:g}〜{start + length:g} 秒が timeline の長さ 0〜{total:g} 秒に収まりません")
    n_frames = fxtimeline.frame_count(length, fps)
    size = output_size(data, scale)
    shots = _shot_records(timeline)
    audio_path = timeline.resolve(data["audio"]["path"]) if data["audio"] else None
    if audio_path is not None and not audio_path.is_file():
        raise PipelineError(f"音声が見つかりません: {data['audio']['path']}")
    libraries = render.library_versions()
    key = runs.content_key_for("timeline", {"timeline": data, "shots": shots, "engine": fx.fingerprint(),
                                            "audio": sha256(audio_path) if audio_path else None, "start": start, "duration": length,
                                            "frames": n_frames, "size": list(size), "scale": float(scale), "libraries": libraries})
    out = (Path(out) if out else timeline.base_dir / "fx_runs").resolve()
    has_record = lambda run_dir, manifest: (Path(run_dir) / "timeline.json").is_file()  # noqa: E731
    with runs.Session(out, key, "timeline", "render.log", say=say, tag="[fx timeline]", force=force, accept_reuse=has_record) as session:
        if session.reused:
            return _summary(session.reused_dir, session.reused, reused=True)
        run = session.run
        shutil.copyfile(timeline.path, run.dir / "fx_timeline.json")
        run.manifest.update(
            timeline={"file": "fx_timeline.json", "sha256": timeline.sha256,
                      "normalized_sha256": hashlib.sha256(fxspec.canonical_json(data).encode("utf-8")).hexdigest()},
            shots=shots, engine=runs.engine_record(fx, "fx"), libraries=libraries,
            interval={"start": start, "duration": length, "fps": fps, "frames": n_frames},
            output={"size": list(size), "scale": float(scale), "crf": data["output"]["crf"]},
            look="applied" if data["look"] else "absent", variants={}, grid=None, warnings=[])
        run.save()
        run.log(f"timeline start key={key} shots={len(data['shots'])} start={start:g} duration={length:g} frames={n_frames} "
                f"size={size[0]}x{size[1]}")
        ctx = Context(timeline, scale)
        audio = None if audio_path is None else {"path": audio_path, "start": data["audio"]["offset_s"] + start, "duration": n_frames / fps}
        video = run.dir / "timeline.mp4"
        meter = _meter(None if quiet else say, n_frames)

        def frames():
            for i in range(n_frames):
                yield ctx.frame(fxtimeline.frame_time(i, fps, start))
                meter(i + 1)

        result = render.write_video(video, frames(), size, fps, data["output"]["crf"], n_frames, audio, run.log_path)
        record = timeline_record(timeline, size, start, length, n_frames)
        (run.dir / "timeline.json").write_text(json.dumps(record, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        checks = probe.check_video(video, width=size[0], height=size[1], fps=fps, frames=n_frames, audio=audio is not None,
                                   audio_s=n_frames / fps if audio else None, bt709=True)
        run.manifest["warnings"] = list(ctx.warnings)
        for warning in ctx.warnings:
            run.log(f"warning: {warning}")
        run.manifest["variants"]["timeline"] = {"path": video.name, "frames": checks["frames"], "sha256": sha256(video),
                                                "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"],
                                                "timeline_json": "timeline.json"}
        run.save()
        run.log(f"timeline: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
        say(f"[fx timeline] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
        if not checks["pass"]:
            message = f"技術検査に失敗しました（{json.dumps(checks['failures'], ensure_ascii=False)[:800]}）。詳細: {run.dir / 'manifest.json'}"
            run.finish(False, message)
            raise PipelineError(message)
        session.succeed()
        run.log(f"timeline done in {time.monotonic() - began:.1f}s")
    summary = _summary(run.dir, run.manifest, reused=False)
    summary["seconds"] = round(time.monotonic() - began, 1)
    return summary


def _summary(run_dir, manifest, reused):
    run_dir = Path(run_dir)
    item = manifest["variants"]["timeline"]
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "technical_pass": manifest["technical_pass"], "interval": manifest["interval"], "size": manifest["output"]["size"],
            "look": manifest.get("look"), "video": str(run_dir / item["path"]), "timeline": str(run_dir / item["timeline_json"]),
            "warnings": manifest["warnings"]}


# ---- デモ ------------------------------------------------------------------------

def demo(directory, width=320, height=180, duration=6.0, fps=24):
    """fx のデモ素材から 3 つのショット（寄り・横移動・引き）の spec と、つなぎを全部使う fx-timeline の見本を書く。"""
    if duration < 3:
        raise PipelineError(f"timeline のデモの長さは 3 秒以上にしてください（つなぎの長さが 0.4〜0.5 秒のため）: {duration:g}")
    from . import demo as fxdemo

    directory = Path(directory)
    shots_dir = directory / "shots"
    fxdemo.create(shots_dir, width, height, duration, fps)
    spec = read_json(shots_dir / "spec.json")
    cameras = {"shot_a.json": [{"t": 0, "zoom": 1.1, "center": [0.5, 0.5]}, {"t": duration, "zoom": 1.3, "center": [0.47, 0.47], "ease": "in_out"}],
               "shot_b.json": [{"t": 0, "zoom": 1.35, "center": [0.4, 0.5]}, {"t": duration, "zoom": 1.35, "center": [0.6, 0.5], "ease": "in_out"}],
               "shot_c.json": [{"t": 0, "zoom": 1.4, "center": [0.5, 0.55]}, {"t": duration, "zoom": 1.1, "center": [0.5, 0.5], "ease": "in_out"}]}
    for name, keys in cameras.items():
        shot = {**spec, "camera": {**spec["camera"], "keys": keys}}
        (shots_dir / name).write_text(json.dumps(shot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    beat = 0.5                                   # 120 BPM
    timeline = {"schema_version": SCHEMA_VERSION, "output": {"width": width, "height": height, "fps": fps, "crf": 18},
                "duration_s": duration, "beats": {"bpm": 120, "count": int(duration / beat) + 4},
                "audio": {"path": "shots/audio.wav"},
                "look": {"vignette": {"amount": 0.3}, "grain": {"sigma": 1.5}},
                "shots": [{"spec": "shots/shot_a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.5}},
                          {"spec": "shots/shot_b.json", "start": {"beat": round(duration / beat * 0.3)}, "offset_s": 1.0,
                           "transition_in": {"type": "xfade", "duration_s": 0.4}},
                          {"spec": "shots/shot_c.json", "start": round(duration * 0.62, 3), "snap": True, "transition_in": {"type": "flash"}}]}
    path = directory / "timeline.json"
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"timeline": str(path), "shots": [str(shots_dir / name) for name in cameras], "size": [width, height], "duration_s": duration}
