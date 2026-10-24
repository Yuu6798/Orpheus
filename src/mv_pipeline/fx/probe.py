"""動画の構造検査（technical_pass）。ffmpeg だけを使い、ffprobe には依存しない。

CI の runner や Windows の PATH には ffprobe が無いことが多く、ffmpeg は imageio-ffmpeg 同梱版へ
フォールバックできる（media.ffmpeg）。そのため `ffmpeg -i`（ストリーム行の読み取り）と
`ffmpeg -progress`（全復号のフレーム数と尺）で同じ情報を取る。

    check_video(path, width=…, height=…, fps=…, frames=…, audio=…, audio_s=…, bt709=True)
        -> {"pass": bool, "failures": [理由…], "decode_ok", "frames", "video": {…}, "audio": {…} | None}

これは**構造検査**であり、絵の良し悪し・同期精度・作品としての採用を判定しない（accept とは別物）。
"""
import math
import re
import subprocess
from pathlib import Path

from .. import media
from ..io import PipelineError

_VIDEO = re.compile(r"Stream #\d+:\d+[^\n]*?: Video: (?P<codec>[\w-]+)[^\n]*?, (?P<pix>[a-z0-9]+)"
                    r"(?:\((?P<tags>[^)]*)\))?, (?P<w>\d+)x(?P<h>\d+)")
_AUDIO = re.compile(r"Stream #\d+:\d+[^\n]*?: Audio: (?P<codec>[\w-]+)[^\n]*?, (?P<rate>\d+) Hz, (?P<layout>[^,\n]+)")
_FPS = re.compile(r"(?P<fps>\d+(?:\.\d+)?) fps")
_TBR = re.compile(r"(?P<fps>\d+(?:\.\d+)?) tbr")
_FIELD_ORDER = {"progressive", "tt", "bb", "tb", "bt"}
_CHANNELS = {"mono": 1, "stereo": 2}


def _color(tags):
    """`yuv420p(tv, bt709, progressive)` の括弧内 → {range, matrix, primaries, transfer}。

    行列・原色・伝達特性が同じなら ffmpeg は 1 語（bt709）で、違えば `bt709/unknown/unknown` で表示する。
    """
    out = {"range": None, "matrix": None, "primaries": None, "transfer": None}
    for token in (t.strip() for t in (tags or "").split(",")):
        if token in ("tv", "pc"):
            out["range"] = token
        elif token and token not in _FIELD_ORDER and out["matrix"] is None:
            parts = token.split("/")
            out["matrix"] = parts[0]
            out["primaries"] = parts[1] if len(parts) > 1 else parts[0]
            out["transfer"] = parts[2] if len(parts) > 2 else parts[0]
    return out


def _channels(layout):
    layout = layout.strip()
    if layout in _CHANNELS:
        return _CHANNELS[layout]
    match = re.match(r"(\d+) channels", layout)
    if match:
        return int(match.group(1))
    match = re.match(r"(\d+)\.(\d+)", layout)  # 5.1 など
    return int(match.group(1)) + int(match.group(2)) if match else None


def _ffmpeg(arguments):
    return subprocess.run([media.ffmpeg(), "-hide_banner", "-nostdin", *arguments], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def inspect(path):
    """ストリームの構造 {"video": {…} | None, "audio": {…} | None}。映像が読めなければ PipelineError。"""
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"動画が見つかりません: {path.name}")
    text = _ffmpeg(["-i", str(path)]).stderr  # 出力先なしの -i は終了コード 1 で、構造だけを stderr に出す
    video, audio = _VIDEO.search(text), _AUDIO.search(text)
    if not video:
        streams = [ln.strip() for ln in text.splitlines() if "Stream #" in ln]
        raise PipelineError(f"映像ストリームを読み取れません ({path.name}): {'; '.join(streams) or text.strip()[-300:]}")
    line = text[video.start():text.find("\n", video.start())]
    rate = _FPS.search(line) or _TBR.search(line)
    info = {"video": {"codec": video["codec"], "pix_fmt": video["pix"], "width": int(video["w"]),
                      "height": int(video["h"]), "fps": float(rate["fps"]) if rate else None,
                      **_color(video["tags"])},
            "audio": None}
    if audio:
        info["audio"] = {"codec": audio["codec"], "sample_rate": int(audio["rate"]), "channels": _channels(audio["layout"])}
    return info


def decode(path, stream):
    """stream ('v' | 'a') の最初のストリームを全復号する。{"ok", "frames", "seconds", "error"}。"""
    result = _ffmpeg(["-v", "error", "-xerror", "-progress", "pipe:1", "-nostats", "-i", str(path),
                      "-map", f"0:{stream}:0", "-f", "null", "-"])
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    error = result.stderr.strip()
    ok = result.returncode == 0 and not error
    try:
        frames = int(values.get("frame", "0"))
        seconds = int(values.get("out_time_us", "0")) / 1e6
    except ValueError:
        frames, seconds, ok = 0, 0.0, False
    return {"ok": ok, "frames": frames, "seconds": seconds,
            "error": None if ok else (error[-400:] or f"ffmpeg が終了コード {result.returncode} で失敗しました")}


def check_video(path, *, width=None, height=None, fps=None, frames=None, audio=None, audio_s=None, bt709=False):
    """動画の構造検査。指定した項目だけを照合し、失敗は例外でなく `failures` に集める。

    frames:  期待フレーム数。整数なら一致、(最小, 最大) ならその範囲（legacy の `-shortest` 許容）。
    audio:   True なら音声ストリームが必須、False なら不要、None なら問わない。
    audio_s: 復号した音声の尺（秒）の期待値。許容 = 1 フレーム + AAC の 1 パケット (1024 サンプル)。
    bt709:   True なら fx の出力契約（bt709・tv・yuv420p のタグ）を要求する。
    """
    failures = []
    try:
        info = inspect(path)
    except PipelineError as error:  # 壊れた動画は例外でなく不合格として記録する（manifest に残す）
        return {"pass": False, "failures": [str(error)[:400]], "decode_ok": False, "frames": 0, "video": None, "audio": None}
    video, sound = info["video"], info["audio"]
    run = decode(path, "v")
    if not run["ok"]:
        failures.append(f"復号エラー: {run['error']}")
    if (width is not None and video["width"] != width) or (height is not None and video["height"] != height):
        failures.append(f"寸法が違います: {video['width']}x{video['height']}（期待 {width}x{height}）")
    if fps is not None and (video["fps"] is None or not math.isclose(video["fps"], fps, rel_tol=2e-3)):
        failures.append(f"fps が違います: {video['fps']}（期待 {fps:g}）")
    if frames is not None:
        low, high = (frames, frames) if isinstance(frames, int) else frames
        if not low <= run["frames"] <= high:
            wanted = str(low) if low == high else f"{low}〜{high}"
            failures.append(f"フレーム数が違います: {run['frames']}（期待 {wanted}）")
    if bt709 and (video["pix_fmt"], video["range"], video["matrix"], video["primaries"], video["transfer"]) != (
            "yuv420p", "tv", "bt709", "bt709", "bt709"):
        failures.append(f"色タグが bt709/tv/yuv420p ではありません: {video['pix_fmt']} {video['range']} "
                        f"{video['matrix']}/{video['primaries']}/{video['transfer']}")
    audio_report = None
    if audio is True and not sound:
        failures.append("音声ストリームがありません")
    elif audio is False and sound:
        failures.append("想定外の音声ストリームがあります")
    if sound:
        audio_report = dict(sound)
        sound_run = decode(path, "a")
        audio_report["decoded_s"] = round(sound_run["seconds"], 4)
        if not sound_run["ok"]:
            failures.append(f"音声の復号エラー: {sound_run['error']}")
        if audio_s is not None:
            tolerance = (1.0 / fps if fps else 1.0 / 24) + 1024 / sound["sample_rate"]
            audio_report["expected_s"] = round(audio_s, 4)
            audio_report["tolerance_s"] = round(tolerance, 4)
            if abs(sound_run["seconds"] - audio_s) > tolerance:
                failures.append(f"音声の尺が違います: {sound_run['seconds']:.3f} 秒（期待 {audio_s:.3f}±{tolerance:.3f}）")
    return {"pass": not failures, "failures": failures, "decode_ok": run["ok"], "frames": run["frames"],
            "video": video, "audio": audio_report}
