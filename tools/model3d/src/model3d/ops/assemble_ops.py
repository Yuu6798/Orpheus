"""まとめ: 描いたコマ（f<T>.png）と曲の音を、1 本の動画（H.264・BT.709・AAC）にする（ffmpeg で）。"""
import hashlib
import re
import shutil
import subprocess
import time
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path, sha256
from . import StepResult, number, register

ASSEMBLE_PARAMETERS = {"ffmpeg", "frames_dir", "range", "fps", "audio_start", "fade_in", "fade_out", "crf", "preset",
                       "audio_bitrate", "timeout"}
PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow")
# PNG（RGB）から YUV へは BT.709 の式で変え、BT.709 の印を付ける（印が無いと、HD の再生機は BT.709 として読むので、
# BT.601 の式で変えた動画は色がずれる）。リポジトリ外の試作のまとめと同じ
YUV = ("scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
       "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv")
TAGS = ["-sws_flags", "accurate_rnd+full_chroma_int", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv"]


def seconds(value):
    """ffmpeg に渡す秒（小数 6 桁まで、末尾の 0 は落とす）。"""
    text = format(float(value), ".6f").rstrip("0").rstrip(".")
    return "0" if text in ("", "-0") else text


def fade_filters(fade_in, fade_out, first, fps):
    """窓の秒（T = 1 が 0 秒）で書いたフェードの、ffmpeg の fade の並び。まとめる最初のコマが T = first（> 1）なら、時刻を
    first - 1 コマぶん進めてから窓の秒のままフェードを掛け、元に戻す（コマの列の時刻は 1 コマに 1 ずつ進む）。fade の st は
    負にできないので、範囲より前に始まったフェードも、こうすると途中の濃さから続く（終わったフェードは何もしない）。
    setpts はコマの長さを 0 にし、最後のコマが落ちるので、戻した後に fps で長さを付け直す。"""
    out = []
    if fade_in:
        out.append(f"fade=t=in:st={seconds(fade_in['start'])}:d={seconds(fade_in['duration'])}")
    if fade_out:
        out.append(f"fade=t=out:st={seconds(fade_out['start'])}:d={seconds(fade_out['duration'])}")
    if out and first > 1:
        out = [f"setpts=PTS+{first - 1}", *out, f"setpts=PTS-{first - 1}", f"fps={seconds(fps)}"]
    return out


def assemble_command(ffmpeg, frames_dir, first, count, fps, out, audio=None, audio_offset=0.0, fade_in=None, fade_out=None,
                     crf=18, preset="medium", audio_bitrate="192k", pad_audio=False):
    """コマ f<first>.png から count コマと、音（audio を audio_offset 秒から）を、1 本の動画にする ffmpeg の引数。
    -shortest は短い方の流れで書き終えるので、音が映像より短いときは pad_audio で音の後ろを映像の長さまで無音で足す（映像のコマを
    落とさない）。ただの apad は終わりが無く、ffmpeg 7 では -shortest でも音が映像の後ろまで続いたので、whole_dur で長さを決める。"""
    argv = [str(ffmpeg), "-y", "-v", "error", "-framerate", seconds(fps), "-start_number", str(first),
            "-i", str(Path(frames_dir) / "f%04d.png")]
    if audio is not None:
        if audio_offset > 0:
            argv += ["-ss", seconds(audio_offset)]
        argv += ["-i", str(audio)]
    # 流れは名指しする（音の入力に映像の流れがあると、ffmpeg は大きい方の映像を選ぶので、描いたコマでない映像になりうる）
    argv += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio is not None else [])
    argv += ["-vf", ",".join(fade_filters(fade_in, fade_out, first, fps) + [YUV]), *TAGS,
             "-c:v", "libx264", "-crf", str(crf), "-preset", preset, "-frames:v", str(count)]
    if audio is not None:
        if pad_audio:
            argv += ["-af", f"apad=whole_dur={seconds(count / fps)}"]
        argv += ["-c:a", "aac", "-b:a", audio_bitrate, "-shortest"]
    return argv + [str(out)]


def frame_list(first, last, frames_dir):
    """T = first〜last のコマのファイル。無いコマがあれば止める。"""
    from ..blender_adapter.stage_render import frame_name
    files = [(T, Path(frames_dir) / frame_name(T)) for T in range(first, last + 1)]
    missing = [T for T, f in files if not f.is_file()]
    if missing:
        raise ToolError(f"まとめるコマがありません（{len(missing)} コマ。最初の T: {missing[:10]}）: {frames_dir}")
    return files


def consecutive(Ts, where):
    """T が 1 ずつ続いているか。動画は 1 コマずつ並べるので、とびがあれば止める。"""
    Ts = sorted(Ts)
    gaps = [b for a, b in zip(Ts, Ts[1:]) if b != a + 1]
    if gaps or len(set(Ts)) != len(Ts):
        raise ToolError(f"{where} の T が 1 ずつ続いていません（とびの後の T: {gaps[:10]}）")
    return Ts[0], Ts[-1]


def find_ffmpeg(parameters, context):
    """ffmpeg の実行ファイル: パラメーター ffmpeg → --tool ffmpeg=PATH → imageio-ffmpeg → PATH の順。"""
    chosen = parameters.get("ffmpeg") or (getattr(context, "tools", None) or {}).get("ffmpeg")
    if chosen:
        if not Path(chosen).is_file():
            raise ToolError(f"ffmpeg の実行ファイルがありません: {chosen}")
        return str(chosen)
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:                                    # 入っていなければ PATH を探す
        pass
    found = shutil.which("ffmpeg")
    if not found:
        raise ToolError("render.assemble には ffmpeg が要ります（パラメーター ffmpeg か --tool ffmpeg=PATH）")
    return found


def probe(ffmpeg, path):
    """書いた動画の長さと流れ（ffmpeg -i の表示）。ffprobe は使わない（imageio-ffmpeg に無い）。"""
    run = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    lines = [line.strip() for line in run.stderr.splitlines()]
    duration = next((m.group(1) for line in lines if (m := re.search(r"Duration: ([0-9:.]+)", line))), None)
    return {"duration": duration, "streams": [line for line in lines if line.startswith("Stream #")]}


def seconds_of(duration):
    """ffmpeg の Duration の表示（HH:MM:SS.xx）の秒。読めなければ None。"""
    m = re.fullmatch(r"(\d+):(\d+):(\d+(?:\.\d+)?)", duration or "")
    return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3)) if m else None


def needs_padding(audio_seconds, offset, count, fps):
    """音（offset 秒から）が映像（count / fps 秒）より短いか。長さが読めなければ短いとみなす（足しても映像の長さで切る）。
    Duration の表示は 0.01 秒きざみなので、0.005 秒より短いときだけ足す（同じ長さの音は今までどおり）。"""
    return audio_seconds is None or audio_seconds - offset < count / fps - 0.005


def _fade(value, key):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"start", "duration"}:
        raise ToolError(f"{key} は {{\"start\": 窓の秒, \"duration\": 秒}} にしてください: {value!r}")
    return {"start": float(number(value, "start", minimum=0)), "duration": float(number(value, "duration", minimum=0, exclusive=True))}


@register("render.assemble", inputs=("frames", "audio"), outputs=("video", "report"), cacheable=False,
          summary="描いたコマ（frames の model3d-frames/0.1 か、frames_dir と range の T）と曲の音（audio）を、1 本の動画にする（ffmpeg）。"
                  "色は BT.709 の式で YUV（4:2:0・TV の範囲）にして BT.709 の印を付け、H.264（crf・preset）と AAC で書く。"
                  "音とフェードは窓の秒（T = 1 が 0 秒）で書き、まとめる最初のコマに合わせてずらす（範囲より前に始まったフェードは途中の濃さから）。抜けたコマ・とびのある T は止める。"
                  "ffmpeg は外部の実行ファイルなので再利用しない（毎回実行）")
def assemble(inputs, parameters, outputs, context):
    from .. import contracts
    unknown = set(parameters) - ASSEMBLE_PARAMETERS
    if unknown:
        raise ToolError(f"render.assemble の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(ASSEMBLE_PARAMETERS)}）")
    if "video" not in outputs:
        raise ToolError("render.assemble には outputs の video が要ります")
    manifest = contracts.load("frames", inputs["frames"]) if inputs.get("frames") is not None else None
    span = parameters.get("range")
    if manifest is not None and span is not None:
        raise ToolError("frames と range は一緒に使えません（frames があれば、その一覧のコマをまとめます）")
    if manifest is not None:
        if manifest["missing"]:
            raise ToolError(f"描いたコマの一覧に、描いていないコマがあります（T: {manifest['missing'][:10]}）。全部描いてからまとめてください")
        if not manifest["frames"]:
            raise ToolError("描いたコマの一覧が空です")
        first, last = consecutive([fr["T"] for fr in manifest["frames"]], "描いたコマの一覧")
        frames_dir = (resolve_path(context.recipe_dir, parameters["frames_dir"]) if parameters.get("frames_dir")
                      else Path(manifest["directory"]))
    else:
        if not parameters.get("frames_dir") or span is None:
            raise ToolError("render.assemble には、入力 frames か、パラメーター frames_dir と range（[最初の T, 最後の T]）が要ります")
        if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in span)
                or span[0] > span[1]):
            raise ToolError(f"range は [最初の T, 最後の T]（1 以上の整数、最初 ≤ 最後）にしてください: {span!r}")
        first, last = span
        frames_dir = resolve_path(context.recipe_dir, parameters["frames_dir"])
    fps = parameters.get("fps") if parameters.get("fps") is not None else (manifest or {}).get("fps")
    if fps is None:
        raise ToolError("render.assemble には fps が要ります（パラメーター fps か、描いたコマの一覧の fps）")
    fps = float(number({"fps": fps}, "fps", minimum=0, exclusive=True))
    audio_start = float(number(parameters, "audio_start", 0.0, minimum=0))
    fade_in, fade_out = _fade(parameters.get("fade_in"), "fade_in"), _fade(parameters.get("fade_out"), "fade_out")
    crf = int(number(parameters, "crf", 18, minimum=0, integer=True))
    if crf > 51:
        raise ToolError(f"crf は 0〜51 にしてください: {crf}")
    preset = parameters.get("preset", "medium")
    if preset not in PRESETS:
        raise ToolError(f"preset は {', '.join(PRESETS)} のどれかにしてください: {preset!r}")
    audio_bitrate = parameters.get("audio_bitrate", "192k")
    if not isinstance(audio_bitrate, str) or not re.fullmatch(r"[1-9][0-9]*k", audio_bitrate):
        raise ToolError(f"audio_bitrate は 192k のような値にしてください: {audio_bitrate!r}")
    ffmpeg = find_ffmpeg(parameters, context)
    files = frame_list(first, last, frames_dir)
    digest, hashes = hashlib.sha256(), {}
    for T, f in files:
        h = sha256(f)
        hashes[T] = h
        digest.update(f"{T}:{h}\n".encode())
    if manifest is not None:                             # 描いた後にコマが変わっていないか
        changed = [fr["T"] for fr in manifest["frames"] if hashes.get(fr["T"]) != fr["sha256"]]
        if changed:
            raise ToolError(f"描いたコマの一覧と、今のファイルが違います（T: {changed[:10]}）。描き直したなら一覧も作り直してください")
    audio = inputs.get("audio")
    offset = audio_start + (first - 1) / fps if audio is not None else 0.0
    audio_seconds = seconds_of(probe(ffmpeg, audio)["duration"]) if audio is not None else None
    padded = audio is not None and needs_padding(audio_seconds, offset, len(files), fps)
    argv = assemble_command(ffmpeg, frames_dir, first, len(files), fps, outputs["video"], audio, offset, fade_in, fade_out, crf,
                            preset, audio_bitrate, pad_audio=padded)
    timeout = None if parameters.get("timeout") is None else number(parameters, "timeout", minimum=0, exclusive=True)
    began = time.monotonic()
    try:
        run = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             stdin=subprocess.DEVNULL)
    except OSError as error:
        raise ToolError(f"ffmpeg を起動できません（{ffmpeg}）: {error}") from error
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"ffmpeg が timeout（{timeout} 秒）を超えました") from error
    if run.returncode:
        raise ToolError(f"ffmpeg が終了コード {run.returncode} で失敗しました: {run.stderr[-2000:]}")
    elapsed = round(time.monotonic() - began, 2)
    version = subprocess.run([str(ffmpeg), "-hide_banner", "-version"], capture_output=True, text=True, encoding="utf-8",
                             errors="replace").stdout.splitlines()[:1]
    video = Path(outputs["video"])
    report = {"schema_version": "model3d-assemble/0.1",
              "frames": {"directory": str(Path(frames_dir).resolve()), "first": first, "last": last, "count": len(files),
                         "sha256": digest.hexdigest()},
              "fps": fps, "seconds": len(files) / fps,
              "audio": ({"file": str(audio), "sha256": sha256(audio), "start": audio_start, "offset": offset, "seconds": audio_seconds,
                         "padded": padded} if audio is not None else None),
              "fade_in": fade_in, "fade_out": fade_out,
              "encode": {"ffmpeg": str(ffmpeg), "version": version[0] if version else None, "video": "libx264", "crf": crf,
                         "preset": preset, "audio": "aac" if audio is not None else None,
                         "audio_bitrate": audio_bitrate if audio is not None else None,
                         "color": "bt709 tv yuv420p", "argv": argv[1:]},
              "video": {"file": video.name, "sha256": sha256(video), "bytes": video.stat().st_size}, "probe": probe(ffmpeg, video),
              "seconds_to_encode": elapsed}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "assemble", "frames": len(files), "first": first, "last": last, "fps": fps,
                   "seconds": round(len(files) / fps, 3), "audio": audio is not None, "audio_padded": padded,
                   "video_bytes": report["video"]["bytes"],
                   "probe_duration": report["probe"]["duration"], "seconds_to_encode": elapsed}
    return StepResult(observations=[observation])
