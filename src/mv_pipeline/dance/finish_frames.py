"""描き終えたコマ（完成コマのフォルダ）に、仕上げ（閃光・暗転・look）を掛けて 1 本の動画にする（`mv dance finish`。docs/dance.md の
「完成コマに仕上げを掛ける」）。

model3d が描いたコマの一覧（model3d-frames/0.1。render.stage・render.title など）を読み、コマ T ごとに、光のトラック
（model3d の plan.lights の model3d-light-track/0.1）の flash・flash_color・fade と、spec の look を掛ける。1 コマの計算は
`mv dance passes` の仕上げ（passes_layers.finish_frame: 閃光は fx.fire.screen、暗転は明るさの掛け算、look は fx の look）をそのまま
呼ぶので、要素別の描画を重ねた動画と同じ仕上げになる。書き出しは encode.write_video（BT.709）と技術検査。run の形と版の名前は
mv dance render と同じ（mv dance accept がそのまま使える）。時刻はコマ T の (T − 1) ÷ fps 秒。
"""
import json
from pathlib import Path
from types import SimpleNamespace

import jsonschema
import numpy as np

from ..fx import spec as fxspec
from ..io import PipelineError, read_json_file, schema_lines, sha256

SCHEMA_VERSION = "dance-finish/0.1"
SCHEMA_FILE = Path(__file__).resolve().parent / "dance_finish.schema.json"
FRAMES_VERSION = "model3d-frames/0.1"
TRACK_VERSION = "model3d-light-track/0.1"


def schema():
    """JSON Schema。look は fx と同じ定義を使う。"""
    data = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    look = fxspec.look_schema()
    data["properties"]["look"] = look["look"]
    data["$defs"].update(look["defs"])
    return data


def normalize(raw):
    """検証済みの生の spec → 既定で埋めた辞書。"""
    output = raw.get("output", {})
    return {"schema_version": SCHEMA_VERSION, "frames": raw["frames"], "lights": raw.get("lights"),
            "audio": None if raw.get("audio") is None else {"path": raw["audio"]["path"], "offset_s": float(raw["audio"].get("offset_s", 0.0))},
            "output": {"crf": int(output.get("crf", 18)), "frames": bool(output.get("frames", False))},
            "look": None if raw.get("look") is None else fxspec.normalize_look(raw["look"])}


def parse_spec(raw, base_dir, path=None, digest=""):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"dance finish spec の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise PipelineError("dance finish spec is invalid:\n" + "\n".join(schema_lines(errors)))
    data = normalize(raw)
    warnings = fxspec.look_warnings(data["look"])
    spec = fxspec.Spec(data=data, base_dir=Path(base_dir).resolve(), path=path, sha256=digest, warnings=tuple(warnings))
    spec.resolve(data["frames"])
    if data["lights"] is not None:
        spec.resolve(data["lights"])
    if data["audio"] is not None:
        spec.resolve(data["audio"]["path"])
    return spec


def load_spec(path):
    path = Path(path)
    raw = read_json_file(path, f"dance finish spec が見つかりません: {path}", f"dance finish spec is not valid JSON ({path.name})")
    return parse_spec(raw, path.resolve().parent, path.resolve(), sha256(path))


def load_frames(path):
    """コマの一覧（model3d-frames/0.1）→ (fps, (幅, 高さ), [(T, ファイル)])。T は小さい順で、重ならず、抜けが無い（missing が空）。
    ファイルは一覧の sha256 と同じこと（描いた後に変わったコマは止める）。"""
    record = read_json_file(path, f"コマの一覧が見つかりません: {path}", f"コマの一覧が JSON ではありません: {path}")
    if record.get("schema_version") != FRAMES_VERSION:
        raise PipelineError(f"frames は {FRAMES_VERSION} のコマの一覧にしてください: {path}")
    if record.get("missing"):
        raise PipelineError(f"コマの一覧に、描いていないコマがあります（missing: {record['missing'][:10]}）: {path}")
    rows = sorted(record["frames"], key=lambda fr: fr["T"])
    Ts = [int(fr["T"]) for fr in rows]
    if not Ts:
        raise PipelineError(f"コマの一覧にコマがありません: {path}")
    if len(set(Ts)) != len(Ts):
        raise PipelineError(f"コマの一覧の T が重なっています: {path}")
    if Ts != list(range(Ts[0], Ts[0] + len(Ts))):
        raise PipelineError(f"コマの一覧の T がとんでいます（T {Ts[0]}〜{Ts[-1]} の {len(Ts)} コマ）: {path}")
    directory = Path(record["directory"])
    files = []
    for fr in rows:
        file = directory / fr["file"]
        if not file.is_file():
            raise PipelineError(f"コマのファイルがありません: {file}")
        if sha256(file) != fr["sha256"]:
            raise PipelineError(f"コマのファイルが一覧の sha256 と違います（描いた後に変わった）: {file}")
        files.append((int(fr["T"]), file))
    width, height = (int(v) for v in record["resolution"])
    return float(record["fps"]), (width, height), files


def load_track(path, Ts):
    """光のトラック（model3d-light-track/0.1）→ ({T: {flash, flash_color, fade}}, fps)。描くどのコマにも値が要る。"""
    record = read_json_file(path, f"光のトラックが見つかりません: {path}", f"光のトラックが JSON ではありません: {path}")
    if record.get("schema_version") != TRACK_VERSION:
        raise PipelineError(f"lights は {TRACK_VERSION} の光のトラックにしてください: {path}")
    values = {}
    for fr in record["frames"]:
        v = fr["values"]
        flash, color, fade = v.get("flash"), v.get("flash_color"), v.get("fade")
        if (not isinstance(flash, (int, float)) or flash < 0 or not isinstance(fade, (int, float)) or fade < 0
                or not isinstance(color, list) or len(color) != 3):
            raise PipelineError(f"光のトラックの T {fr['T']} の flash・flash_color・fade が読めません: {path}")
        values[int(fr["T"])] = {"flash": float(flash), "flash_color": [float(c) for c in color], "fade": float(fade)}
    missing = [T for T in Ts if T not in values]
    if missing:
        raise PipelineError(f"光のトラックに、描いたコマの値がありません（T {missing[:10]}）: {path}")
    return values, float(record["fps"])


def finish_values(values):
    """コマの光の値 → passes_layers.finish_frame の仕上げの値（閃光は 1 つのキー、暗転は明るさ。どちらも無ければ何もしない）。"""
    data = {"impact": None, "bloom": None, "flash": None, "level": 1.0}
    if values is None:
        return data
    if values["flash"] > 0:
        data["flash"] = {"keys": [[0.0, values["flash"]]], "color": [255.0 * c for c in values["flash_color"]]}
    data["level"] = values["fade"]
    return data


def finish_one(image, values, T, fps, look=None):
    """表示の値の画（float32 (H, W, 3)、0〜1）に、コマの光の値（無ければ None）と look を掛けて uint8 (H, W, 3) を返す。"""
    from .passes_layers import finish_frame
    height, width = image.shape[:2]
    plan = SimpleNamespace(fps=fps, size=(width, height))
    return finish_frame(finish_values(values), plan, T, image, look)


def default_out(spec):
    return spec.base_dir / "dance_runs"


def render_run(spec_path, out=None, force=False, progress=None, quiet=False):
    """spec の完成コマに仕上げを掛けて dance.mp4 を描き、run として記録する（同じ入力で成功済みなら再利用する）。"""
    import shutil
    import time

    from .. import dance
    from ..fx import look as fxlook
    from ..fx import probe
    from ..fx import render as fxrender
    from ..fx import runs as fxruns
    from . import VARIANT, fingerprint
    from .passes import audio_plan
    from .passes_scene import read_image
    from .runs import _meter

    began = time.monotonic()
    say = progress or (lambda message: None)
    spec = load_spec(spec_path)
    data = spec.data
    frames_path = spec.resolve(data["frames"])
    fps, size, files = load_frames(frames_path)
    Ts = [T for T, _ in files]
    track, notes = None, list(spec.warnings)
    if data["lights"] is not None:
        track, track_fps = load_track(spec.resolve(data["lights"]), Ts)
        if track_fps != fps:
            raise PipelineError(f"光のトラックの fps（{track_fps:g}）が、コマの一覧の fps（{fps:g}）と違います")
    width, height = size
    if width % 2 or height % 2:
        raise PipelineError(f"コマの大きさは偶数にしてください（H.264 の 4:2:0）: {width}×{height}")
    audio_path = spec.resolve(data["audio"]["path"]) if data["audio"] is not None else None
    if audio_path is not None and not audio_path.is_file():
        raise PipelineError(f"音声が見つかりません: {data['audio']['path']}")
    assets = {"frames": {"path": data["frames"], "sha256": sha256(frames_path)}}
    if data["lights"] is not None:
        assets["lights"] = {"path": data["lights"], "sha256": sha256(spec.resolve(data["lights"]))}
    if audio_path is not None:
        assets["audio"] = {"path": data["audio"]["path"], "sha256": sha256(audio_path)}
    libraries = fxrender.library_versions()
    key = fxruns.content_key_for("dance-finish", {"spec": data, "assets": {r: a["sha256"] for r, a in assets.items()},
                                                  "engine": fingerprint(), "libraries": libraries})
    out = (Path(out) if out else default_out(spec)).resolve()
    n_frames = len(files)
    with fxruns.Session(out, key, "dance-finish", "render.log", say=say, tag="[dance]", force=force) as session:
        if session.reused:
            return fxruns.summarize(session.reused_dir, session.reused, reused=True)
        run = session.run
        shutil.copyfile(spec.path, run.dir / "spec.json")
        seconds = n_frames / fps
        run.manifest.update(
            spec=fxruns.spec_record(spec), assets=assets, engine=fxruns.engine_record(dance, "dance"), libraries=libraries,
            interval={"start": (Ts[0] - 1) / fps, "duration": seconds, "fps": fps, "frames": n_frames, "T": [Ts[0], Ts[-1]]},
            output={"size": [width, height], "scale": 1.0, "crf": data["output"]["crf"]}, variants={}, grid=None, warnings=notes,
            look=data["look"] is not None,
            finish={"flash_frames": sum(1 for T in Ts if track and track[T]["flash"] > 0),
                    "fade_frames": sum(1 for T in Ts if track and track[T]["fade"] != 1.0)})
        run.save()
        run.log(f"finish start key={key} frames={n_frames} size={width}x{height} lights={data['lights'] is not None}")
        look = fxlook.prepare(data["look"], size, fps) if data["look"] is not None else None
        frames_dir = run.dir / "frames" if data["output"]["frames"] else None
        if frames_dir is not None:
            frames_dir.mkdir()
        meter = _meter(None if quiet else progress, n_frames)

        def frames():
            from ..fx import compare
            for i, (T, file) in enumerate(files):
                image = read_image(file)
                if image.shape[:2] != (height, width):
                    raise PipelineError(f"コマ T {T} の大きさ（{image.shape[1]}×{image.shape[0]}）が一覧（{width}×{height}）と違います")
                finished = finish_one(image, track[T] if track else None, T, fps, look)
                if frames_dir is not None:
                    compare.save_png_atomic(finished, frames_dir / f"f{T:04d}.png")
                yield finished
                meter(i + 1)

        start = (data["audio"]["offset_s"] + (Ts[0] - 1) / fps) if audio_path is not None else 0.0
        audio = audio_plan(audio_path, start, seconds, run.manifest["warnings"])
        path = run.dir / f"{VARIANT}.mp4"
        result = fxrender.write_video(path, frames(), size, fps, data["output"]["crf"], n_frames, audio, run.log_path)
        checks = probe.check_video(path, width=width, height=height, fps=fps, frames=n_frames, audio=audio is not None,
                                   audio_s=seconds if audio else None, bt709=True)
        run.manifest["variants"][VARIANT] = {"path": path.name, "frames": checks["frames"], "sha256": sha256(path),
                                              "technical_pass": checks["pass"], "checks": checks, "render_seconds": result["seconds"]}
        run.save()
        run.log(f"{VARIANT}: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
        say(f"[dance] {checks['frames']} フレーム（{result['seconds']:.1f} 秒）technical_pass={checks['pass']}")
        if not checks["pass"]:
            message = f"技術検査に失敗しました（{checks['failures']}）。詳細: {run.dir / 'manifest.json'}"
            run.finish(False, message)
            raise PipelineError(message)
        session.succeed()
    result = fxruns.summarize(run.dir, run.manifest, reused=False)
    result["frames_dir"] = str(frames_dir) if frames_dir is not None else None
    result["seconds"] = round(time.monotonic() - began, 1)
    return result
