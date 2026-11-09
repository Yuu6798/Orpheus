"""BT.709 の H.264 書き出しと音の切り出し（ffmpeg）。cv2 を使わない共通の部品。

fx・dance・overlay・timeline・clips の書き出しと、`mv deliver` が同じ書式（libx264 / bt709 / tv / yuv420p / AAC 192k・48kHz）で書く。

    write_video(out, frames, size, fps, crf, n_frames, audio=None, log_path=None)   任意のフレーム列を書く（一時名 → 成功時だけ置き換え）
    ffmpeg_command(out, size, fps, crf, n_frames, audio=None)                       その ffmpeg の引数
    audio_filter(audio)                                                             音のフェードと詰め（afade・apad）

ffmpeg は media.ffmpeg()（ORPHEUS_FFMPEG、PATH の ffmpeg、imageio-ffmpeg 同梱版の順）。fx の指紋（fx.fingerprint）はこのファイルも含む。
fx/render.py からも同じ名前で使える（以前の置き場所）。
"""
import os
import shlex
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np

from . import media
from .io import PipelineError

FADE_IN_S = 0.02
FADE_OUT_S = 0.25


def ffmpeg_command(out_path, size, fps, crf, n_frames, audio=None):
    width, height = size
    command = [media.ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "warning",
               "-thread_queue_size", "512", "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{width}x{height}",
               "-framerate", f"{fps:g}", "-i", "pipe:0"]
    if audio and audio.get("silent"):
        command += ["-f", "lavfi", "-t", f"{audio['duration']:.6f}", "-i", "anullsrc=r=48000:cl=stereo"]
    elif audio:
        command += ["-ss", f"{audio['start']:.6f}", "-t", f"{audio.get('length', audio['duration']):.6f}", "-i", str(audio["path"])]
    command += ["-map", "0:v:0"] + (["-map", "1:a:0"] if audio else [])
    # ffmpeg 7.1（imageio-ffmpeg 同梱の Windows 版など）はフレームの色属性が出力オプションより優先されるため、setparams でも付ける
    command += ["-vf", "scale=out_color_matrix=bt709:out_range=tv,format=yuv420p,"
                "setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=tv",
                "-sws_flags", "accurate_rnd+full_chroma_int",
                "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
                "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709", "-color_range", "tv"]
    if audio:
        command += ["-af", audio_filter(audio), "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2"]
    command += ["-frames:v", str(n_frames), "-movflags", "+faststart", str(out_path)]
    return command


def audio_filter(audio):
    """音声のフィルタ。length・fade_in・fade_out・pad・silent は dance だけが使う（無ければ fx の既定どおり全区間に掛ける）。"""
    if audio.get("silent"):
        return "anull"
    length = audio.get("length", audio["duration"])
    fade_in = min(audio.get("fade_in", FADE_IN_S), length / 2)
    fade_out = min(audio.get("fade_out", FADE_OUT_S), length / 2)
    steps = [f"afade=t=in:st=0:d={fade_in:.4f}"] if fade_in > 0 else []
    steps += [f"afade=t=out:st={length - fade_out:.4f}:d={fade_out:.4f}"] if fade_out > 0 else []
    steps += [f"apad=whole_dur={audio['duration']:.6f}"] if audio.get("pad") else []
    return ",".join(steps) or "anull"


def _close(stream):
    try:
        stream.close()
    except OSError:  # ffmpeg が先に終了していると、残りの書き込みの flush が BrokenPipeError になる
        pass


def partial_path(out_path):
    """write_video が書き出しの間に使う一時ファイル（<stem>.part<suffix>）。書き終えると out_path へ移し、失敗すると消す。"""
    out_path = Path(out_path)
    return out_path.with_name(f"{out_path.stem}.part{out_path.suffix}")


def write_video(out_path, frames, size, fps, crf, n_frames, audio=None, log_path=None):
    """フレーム列 (uint8 H×W×3) を n_frames ちょうど書く。-shortest は使わない。

    audio: None または {"path", "start", "duration"}（audio_for が作る）。dance は length・fade_in・fade_out・pad・silent も渡す。
    一時名 (<stem>.part<suffix>) に書き、ffmpeg が成功し枚数が揃ったときだけ out_path へ置き換える。
    戻り値: {"frames", "command", "seconds"}。ffmpeg の警告と実行行は log_path に追記する。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    partial = partial_path(out_path)
    command = ffmpeg_command(partial, size, fps, crf, n_frames, audio)
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        log = open(log_path, "a+b")
    else:
        log = tempfile.TemporaryFile()
    started = time.monotonic()
    written = 0
    error = None
    try:
        log.write(f"$ {shlex.join(command)}\n".encode())
        log.flush()
        proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=log)
        try:
            for image in frames:
                if image.shape != (size[1], size[0], 3) or image.dtype != np.uint8:
                    raise PipelineError(f"frame {written}: expected uint8 {size[1]}x{size[0]}x3, got {image.dtype} {image.shape}")
                proc.stdin.write(np.ascontiguousarray(image).data)
                written += 1
        except BrokenPipeError:
            error = "ffmpeg が途中で終了しました"
        except BaseException:
            proc.kill()
            _close(proc.stdin)
            proc.wait()
            raise
        _close(proc.stdin)
        code = proc.wait()
        if error is None and code != 0:
            error = f"ffmpeg が終了コード {code} で失敗しました"
        if error is None and written != n_frames:
            error = f"フレーム数が合いません（{written} 枚 / 期待 {n_frames} 枚）"
        if error:
            log.flush()
            log.seek(0)
            tail = log.read()[-2500:].decode("utf-8", "replace")
            raise PipelineError(f"{error}。ffmpeg の出力:\n{tail}")
        os.replace(partial, out_path)
    finally:
        log.close()
        if partial.exists():
            partial.unlink()
    return {"frames": written, "command": command, "seconds": round(time.monotonic() - started, 3)}
