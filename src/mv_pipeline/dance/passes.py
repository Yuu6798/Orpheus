"""要素別の描画（model3d の render.passes）を重ねて、1 本の動画にする（`mv dance passes`。docs/dance.md の「要素別の描画を重ねる」）。

1 コマの重ね方（表示の値。sRGB に符号化した 0〜1 の float32。リポジトリ外の試作の重ね合わせの台本と同じ順）:

    部屋（room）→ ぼかし（room.blur。区間ごとの σ）→ 明るさ（room.level）→ 足もとの円（disc）の影（finish.halo）
    人物（char の色・アルファ）→ 灯りの色で染める（shade と head。finish.face_shade・finish.light_tint）→ 輪郭の光（finish.rim_light）
    重ね（finish.over）→ にじみ（finish.stage_bloom）→ 閃光（fx.fire.screen）→ 明るさ（level）→ look（周辺の減光・粒。fx の look）

既存の部品（dance の finish・cues、fx の fire と look、encode.write_video）を呼ぶ。新しく足したのは、区間ごとの部屋のぼかし
（finish.blur と、区間の値を引く segment_value）と、灯りの色で人物を染める部品（finish.light_tint・finish.face_shade）。
時刻はコマ T の (T − 1) ÷ fps 秒。長さ（画素）は幅 1080 の画面での値で、コマの幅に合わせて掛ける。
"""
import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema
import numpy as np

from ..fx import spec as fxspec
from ..io import PipelineError, read_json, sha256
from . import cues as dancecues

SCHEMA_VERSION = "dance-passes/0.1"
SCHEMA_FILE = Path(__file__).resolve().parent / "dance_passes.schema.json"
KINDS = ("room", "char", "shade", "disc", "head")
FLOOR_SHADOW = {"strength": 0.45, "sigma": 26.0, "gain": 1.0, "down": 1}      # 既定は試作の値
FLASH_COLOR = [255.0, 255.0, 255.0]


def schema():
    """JSON Schema。look は fx と同じ定義を使う。"""
    data = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    look = fxspec.look_schema()
    data["properties"]["look"] = look["look"]
    data["$defs"].update(look["defs"])
    return data


def _check_keys(keys, where):
    try:
        dancecues.envelope(keys, 0.0)
    except ValueError as error:
        raise PipelineError(f"{where}: {error}") from error
    return [list(key) for key in keys]


def _segments(value, where):
    """部屋のぼかしの区間。数は 1 区間。区間の並びは until が昇順で、最後だけ until を省ける。"""
    if isinstance(value, (int, float)):
        return [{"until": None, "sigma": float(value)}]
    out = []
    for n, part in enumerate(value):
        until = part.get("until")
        if until is None and n != len(value) - 1:
            raise PipelineError(f"{where}[{n}] には until が要ります（省けるのは最後の区間だけ）")
        if until is not None and out and out[-1]["until"] is not None and until <= out[-1]["until"]:
            raise PipelineError(f"{where}[{n}] の until は前の区間より後にしてください: {until}")
        out.append({"until": None if until is None else float(until), "sigma": float(part["sigma"])})
    return out


def segment_value(segments, t):
    """時刻 t の区間の値（区間の頭は、前の区間の until。最後の区間の後は最後の区間の値）。"""
    for part in segments:
        if part["until"] is None or t < part["until"]:
            return part["sigma"]
    return segments[-1]["sigma"]


def level_at(level, t):
    """数か時刻のキー（cues.envelope）の t での値。"""
    return float(level) if isinstance(level, (int, float)) else float(dancecues.envelope(level, t))


def _level(value, where):
    return float(value) if isinstance(value, (int, float)) else _check_keys(value, where)


def normalize(raw):
    """検証済みの生の spec → 既定で埋めた辞書。"""
    room = raw.get("room", {})
    output = raw.get("output", {})
    data = {"schema_version": SCHEMA_VERSION, "passes": raw["passes"],
            "output": {"crf": int(output.get("crf", 18)), "frames": bool(output.get("frames", False))},
            "audio": None if raw.get("audio") is None else {"path": raw["audio"]["path"],
                                                            "offset_s": float(raw["audio"].get("offset_s", 0.0))},
            "room": {"blur": _segments(room.get("blur", 0.0), "room.blur"), "level": _level(room.get("level", 1.0), "room.level")},
            "floor_shadow": (None if "floor_shadow" in raw and raw["floor_shadow"] is None
                             else {**FLOOR_SHADOW, **{k: float(v) if k != "down" else int(v)
                                                      for k, v in (raw.get("floor_shadow") or {}).items() if not k.startswith("x_")}}),
            "figure": None, "rim": None, "bloom": None, "flash": None, "level": _level(raw.get("level", 1.0), "level"),
            "look": None if raw.get("look") is None else fxspec.normalize_look(raw["look"])}
    if raw.get("figure") is not None:
        figure = raw["figure"]
        low, high = (float(v) for v in figure.get("shade_range", [0.5, 0.92]))
        if not low < high:
            raise PipelineError(f"figure.shade_range は [low, high]（low < high）にしてください: {[low, high]}")
        data["figure"] = {"light": [float(v) for v in figure["light"]], "shadow": [float(v) for v in figure["shadow"]],
                          "shade_range": [low, high], "face_smooth": float(figure.get("face_smooth", 0.0))}
    if raw.get("rim") is not None:
        rim = raw["rim"]
        data["rim"] = {"rgb": [float(v) for v in rim["rgb"]], "strength": float(rim["strength"]),
                       "kernel": float(rim.get("kernel", 7)), "shape": rim.get("shape", "ellipse"), "blur": float(rim.get("blur", 1.6)),
                       "gradient": None if "gradient" in rim and rim["gradient"] is None else [float(v) for v in rim.get("gradient", [1.25, 0.75])],
                       "clip": bool(rim.get("clip", True)), "band": rim.get("band", "erode")}
    if raw.get("bloom") is not None:
        bloom = raw["bloom"]
        data["bloom"] = {"threshold": float(bloom.get("threshold", 0.45)),
                         "ceiling": None if "ceiling" in bloom and bloom["ceiling"] is None else float(bloom.get("ceiling", 0.8)),
                         "sigma": float(bloom.get("sigma", 7.0)), "strength": float(bloom.get("strength", 0.6)),
                         "down": int(bloom.get("down", 4))}
    if raw.get("flash") is not None:
        data["flash"] = {"keys": _check_keys(raw["flash"]["keys"], "flash.keys"),
                         "color": [float(v) for v in raw["flash"].get("color", FLASH_COLOR)]}
    return data


def parse_spec(raw, base_dir, path=None, digest=""):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"dance passes spec の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("dance passes spec is invalid:\n" + "\n".join(lines))
    base_dir = Path(base_dir).resolve()
    data = normalize(raw)
    warnings = fxspec.look_warnings(data["look"])      # fx・dance と同じ look の確かめ（grade のキーの並びは止め、注意は warnings に）
    spec = fxspec.Spec(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings))
    spec.resolve(spec.data["passes"])
    if spec.data["audio"] is not None:
        spec.resolve(spec.data["audio"]["path"])
    return spec


def load_spec(path):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"dance passes spec が見つかりません: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"dance passes spec is not valid JSON ({path.name}): {error}") from error
    return parse_spec(raw, path.resolve().parent, path.resolve(), sha256(path))


@dataclass
class Plan:
    """重ねるコマの並び。files[要素][T] は要素のファイルの絶対パス。"""
    manifest: dict
    directory: Path
    fps: float
    size: tuple
    frames: list
    files: dict


def load_passes(path, verify=True):
    """要素の一覧（model3d-passes/0.1）を読み、重ねるコマの並びにする。char は要る。どの要素も同じコマがそろい、T が 1 ずつ続くこと。
    verify なら、一覧の sha256 と今のファイルを照合する（描いた後に変わったコマは止める）。"""
    manifest = read_json(path)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != "model3d-passes/0.1":
        raise PipelineError(f"要素の一覧は model3d-passes/0.1 にしてください: {path}")
    passes = manifest.get("passes") or {}
    if "char" not in passes:
        raise PipelineError("要素の一覧に char（人物）がありません")
    unknown = sorted(set(passes) - set(KINDS))
    if unknown:
        raise PipelineError(f"要素の一覧に知らない要素があります: {unknown}")
    directory = Path(manifest["directory"])
    frames = [fr["T"] for fr in passes["char"]["frames"]]
    if not frames:
        raise PipelineError("char のコマがありません")
    if any(b != a + 1 for a, b in zip(frames, frames[1:])):
        raise PipelineError("char のコマの T が 1 ずつ続いていません（とびがあると 1 本の動画にできません）")
    files = {}
    for kind, entry in passes.items():
        if entry.get("missing"):
            raise PipelineError(f"{kind} に描いていないコマがあります: {entry['missing'][:10]}")
        by_T = {fr["T"]: fr for fr in entry["frames"]}
        if sorted(by_T) != frames:
            raise PipelineError(f"{kind} のコマが char とそろっていません")
        files[kind] = {}
        for T in frames:
            file = directory / by_T[T]["file"]
            if not file.is_file():
                raise PipelineError(f"{kind} のコマ T {T} のファイルがありません: {file}")
            if verify and sha256(file) != by_T[T]["sha256"]:
                raise PipelineError(f"{kind} のコマ T {T} が、一覧を書いた後に変わっています: {file}")
            files[kind][T] = file
    width, height = manifest["resolution"]
    return Plan(manifest, directory, float(manifest["fps"]), (int(width), int(height)), frames, files)


def read_image(path, alpha=False):
    """画像を RGB（alpha なら RGBA）の 0〜1 の float32 で読む（日本語の入ったパスでも読めるよう、バイト列から）。"""
    import cv2
    data = np.frombuffer(Path(path).read_bytes(), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_UNCHANGED if alpha else cv2.IMREAD_COLOR)
    if image is None:
        raise PipelineError(f"画像を読めません: {path}")
    if alpha:
        if image.ndim != 3 or image.shape[2] != 4:
            raise PipelineError(f"char は透けのある RGBA の画像にしてください: {path}")
        return image[..., [2, 1, 0, 3]].astype(np.float32) / 255
    return image[..., ::-1].astype(np.float32) / 255


def compose(data, plan, T, look=None):
    """コマ T を重ねて uint8 (H, W, 3) RGB を返す（表示の値で計算する。上の順）。"""
    from ..fx import fire
    from ..fx import look as fxlook
    from . import finish

    t = (T - 1) / plan.fps
    width, height = plan.size
    unit = width / 1080
    files = plan.files
    character = _same_size(read_image(files["char"][T], alpha=True), plan, "char", T)
    room = _same_size(read_image(files["room"][T]), plan, "room", T) if "room" in files else np.zeros((height, width, 3), np.float32)
    room = finish.blur(room, segment_value(data["room"]["blur"], t), unit=unit)
    room = room * np.float32(level_at(data["room"]["level"], t))
    shadow = data["floor_shadow"]
    if shadow is not None and "disc" in files:
        disc = _same_size(read_image(files["disc"][T]), plan, "disc", T).mean(axis=2)
        room = finish.halo(room, disc, strength=shadow["strength"], gain=shadow["gain"], sigma=shadow["sigma"], down=shadow["down"],
                           unit=unit)
    alpha = character[..., 3]
    figure = character[..., :3]
    tint = data["figure"]
    if tint is not None:
        shade = _same_size(read_image(files["shade"][T]), plan, "shade", T).mean(axis=2)
        if tint["face_smooth"] > 0 and "head" in files:
            head = _same_size(read_image(files["head"][T]), plan, "head", T).mean(axis=2)
            shade = finish.face_shade(shade, head, tint["face_smooth"], unit=unit)
        figure = finish.light_tint(figure, shade, tint["light"], tint["shadow"], *tint["shade_range"])
    rim = data["rim"]
    if rim is not None:
        figure = finish.rim_light(figure, alpha, rim["rgb"], rim["strength"], kernel=rim["kernel"], shape=rim["shape"],
                                  blur=rim["blur"], gradient=rim["gradient"], clip=rim["clip"], unit=unit, band=rim["band"])
    frame = finish.over(figure, alpha, room)
    bloom = data["bloom"]
    if bloom is not None:
        frame = finish.stage_bloom(frame, threshold=bloom["threshold"], ceiling=bloom["ceiling"], sigma=bloom["sigma"],
                                   strength=bloom["strength"], down=bloom["down"], unit=unit)
    flash = data["flash"]
    if flash is not None:
        strength = level_at(flash["keys"], t)
        if strength > 0:
            add = np.asarray(flash["color"], np.float32) * np.float32(strength)
            frame = fire.screen(frame * np.float32(255.0), add) / np.float32(255.0)
    frame = frame * np.float32(level_at(data["level"], t))
    if look is not None:
        return fxlook.finish(look, np.ascontiguousarray(frame, dtype=np.float32), t)
    return (np.clip(frame, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def audio_plan(path, offset, seconds, warnings):
    """write_video の音（T = 1 が音の offset 秒）。音が動画より短ければ、足りない分を無音で足して動画の長さにそろえ、warnings に残す
    （そろえないと、重ね終えた後の技術検査で音の長さが合わずに止まる）。読み始めより音が短ければ止める。"""
    if path is None:
        return None
    from ..fx import probe
    plan = {"path": path, "start": offset, "duration": seconds}
    available = probe.media_seconds(path)
    if available is None:
        return plan
    remaining = available - offset
    if remaining <= 0:
        raise PipelineError(f"音の読み始め（{offset:g} 秒）が音の長さ（{available:g} 秒）より後です: {path}")
    if remaining < seconds:
        warnings.append(f"audio: 音が動画より {seconds - remaining:.3f} 秒短いので、後ろを無音で足します")
        plan.update(length=remaining, pad=True)
    return plan


def _same_size(image, plan, kind, T):
    width, height = plan.size
    if image.shape[:2] != (height, width):
        raise PipelineError(f"{kind} のコマ T {T} の大きさ（{image.shape[1]}×{image.shape[0]}）が一覧（{width}×{height}）と違います")
    return image


def default_out(spec):
    return spec.base_dir / "dance_runs"


def render_run(spec_path, out=None, force=False, progress=None, quiet=False):
    """spec の要素を重ねて dance.mp4 を描き、run として記録する（run の形と版の名前は mv dance render と同じなので、mv dance accept が
    そのまま使える）。同じ入力で成功済みなら再利用する。"""
    import shutil
    import time

    from .. import dance
    from ..fx import look as fxlook
    from ..fx import probe
    from ..fx import render as fxrender
    from ..fx import runs as fxruns
    from . import VARIANT, fingerprint
    from .runs import _meter

    began = time.monotonic()
    say = progress or (lambda message: None)
    spec = load_spec(spec_path)
    data = spec.data
    passes_path = spec.resolve(data["passes"])
    plan = load_passes(passes_path)
    if data["figure"] is not None and "shade" not in plan.files:
        raise PipelineError("figure（灯りの色で染める）には、要素の一覧に shade（陰影）が要ります")
    notes = list(spec.warnings)
    if data["figure"] is not None and data["figure"]["face_smooth"] > 0 and "head" not in plan.files:
        notes.append("figure.face_smooth: 要素の一覧に head（顔の範囲）が無いので、顔の陰影はならしません")
    width, height = plan.size
    if width % 2 or height % 2:
        raise PipelineError(f"コマの大きさは偶数にしてください（H.264 の 4:2:0）: {width}×{height}")
    n_frames = len(plan.frames)
    audio_path = spec.resolve(data["audio"]["path"]) if data["audio"] is not None else None
    if audio_path is not None and not audio_path.is_file():
        raise PipelineError(f"音声が見つかりません: {data['audio']['path']}")
    assets = {"passes": {"path": data["passes"], "sha256": sha256(passes_path)}}
    if audio_path is not None:
        assets["audio"] = {"path": data["audio"]["path"], "sha256": sha256(audio_path)}
    libraries = fxrender.library_versions()
    key = fxruns.content_key_for("dance-passes", {"spec": data, "assets": {r: a["sha256"] for r, a in assets.items()},
                                                  "engine": fingerprint(), "libraries": libraries})
    out = (Path(out) if out else default_out(spec)).resolve()
    with fxruns.Session(out, key, "dance-passes", "render.log", say=say, tag="[dance]", force=force) as session:
        if session.reused:
            return fxruns.summarize(session.reused_dir, session.reused, reused=True)
        run = session.run
        shutil.copyfile(spec.path, run.dir / "spec.json")
        seconds = n_frames / plan.fps
        run.manifest.update(
            spec=fxruns.spec_record(spec), assets=assets, engine=fxruns.engine_record(dance, "dance"), libraries=libraries,
            # 最初のコマの時刻（T = 1 が 0 秒）。fx の比較（compare_run）などが、ここから基準の動画のコマを選ぶ
            interval={"start": (plan.frames[0] - 1) / plan.fps, "duration": seconds, "fps": plan.fps, "frames": n_frames,
                      "T": [plan.frames[0], plan.frames[-1]]},
            output={"size": [width, height], "scale": 1.0, "crf": data["output"]["crf"]}, passes=sorted(plan.files),
            variants={}, grid=None, warnings=notes, look=data["look"] is not None)
        run.save()
        run.log(f"passes start key={key} frames={n_frames} size={width}x{height} passes={sorted(plan.files)}")
        look = fxlook.prepare(data["look"], plan.size, plan.fps) if data["look"] is not None else None
        frames_dir = run.dir / "frames" if data["output"]["frames"] else None
        if frames_dir is not None:
            frames_dir.mkdir()
        meter = _meter(None if quiet else progress, n_frames)

        def frames():
            from ..fx import compare
            for i, T in enumerate(plan.frames):
                image = compose(data, plan, T, look)
                if frames_dir is not None:
                    compare.save_png_atomic(image, frames_dir / f"f{T:04d}.png")
                yield image
                meter(i + 1)

        # 音の読み始め: T = 1 が offset_s 秒なので、一覧の最初の T のぶん先から読む（T が 1 から始まらない一覧でも絵と合う）
        start = (data["audio"]["offset_s"] + (plan.frames[0] - 1) / plan.fps) if audio_path is not None else 0.0
        audio = audio_plan(audio_path, start, seconds, run.manifest["warnings"])
        path = run.dir / f"{VARIANT}.mp4"
        result = fxrender.write_video(path, frames(), plan.size, plan.fps, data["output"]["crf"], n_frames, audio, run.log_path)
        checks = probe.check_video(path, width=width, height=height, fps=plan.fps, frames=n_frames, audio=audio is not None,
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
