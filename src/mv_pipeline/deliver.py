"""受け渡しの書き出し（`mv deliver`）。描いた区間の動画をつなぎ、音を付けて元データ（master）にする。cv2 を使わない。

    mv deliver join SEGMENT... --out MASTER.mp4 [--audio SONG.wav [--audio-start SEC] [--fade-in 0.02] [--fade-out 0.25]] [--force]

- SEGMENT: 区間の動画（同じ寸法・fps・符号化で書いたもの）を、つなぐ順に。フォルダを 1 つ渡すと、その中の seg_*.mp4 を名前の順に使う。
  名前が seg_<最初のコマ>_<終わりのコマ>.mp4 なら（実制作の区間の描画と同じ）、区間がすき間なく続くことと、区間ごとのコマ数
  （終わり − 最初）を確かめる。どの区間も同じ寸法・fps・映像の符号化であること（つなぎは再エンコードしないため）。
- つなぎ: ffmpeg の concat で、映像を再エンコードせずにつなぐ（-c copy）。全体のコマ数を確かめる。
- 音（--audio）: --audio-start 秒から映像の長さ（コマ数 / fps）を切り出し、頭と終わりにフェードを掛けて AAC 192k・48kHz にする。
- 元データ: 映像と音を -c copy で合わせ、映像の長さで切り、faststart で書く。既にあって中身が違えば --force が要る。
- 記録 <MASTER の名前>.deliver.json: 区間（sha256・コマ数）・音・ffmpeg（パス・版・sha256）・コマンド・元データの bytes と sha256・
  構造検査（technical_pass。作品の採否ではない）。

手順と ffmpeg の引数は実制作の仕上げ（片をつないで音を付ける）と同じ。同じ ffmpeg（ORPHEUS_FFMPEG で選べる）と同じ入力なら、
元データは同じバイト列になる。色のタグは区間のまま（つなぐだけで変えない）。
"""
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import media
from .fx import probe
from .io import PipelineError, atomic_json, sha256

SCHEMA = "deliver-join/0.1"
FADE_IN_S, FADE_OUT_S = 0.02, 0.25          # 実制作の仕上げと fx の書き出しの既定
_SEGMENT = re.compile(r"seg_(\d+)_(\d+)\.mp4", re.IGNORECASE)


def _ffmpeg_info():
    """使う ffmpeg の {path, version, sha256}（実行ファイルが見つからなければ sha256 は None）。"""
    command = media.ffmpeg()
    path = Path(command) if Path(command).is_file() else (Path(shutil.which(command)) if shutil.which(command) else None)
    result = subprocess.run([command, "-hide_banner", "-version"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    version = (result.stdout.splitlines() or [""])[0].strip()
    return {"path": str(path or command), "version": version, "sha256": sha256(path) if path else None}


def _run(args, log):
    """実制作の仕上げと同じ前置き（-y -hide_banner -loglevel error）で ffmpeg を動かす。失敗したら出力の終わりを添えて止める。"""
    command = [media.ffmpeg(), "-y", "-hide_banner", "-nostdin", "-loglevel", "error", *[str(a) for a in args]]
    log.append(command)
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise PipelineError(f"ffmpeg が終了コード {result.returncode} で失敗しました: {result.stderr.strip()[-800:]}")
    return command


def _segments(paths):
    """つなぐ区間の並び。フォルダ 1 つなら seg_*.mp4 を名前の順に。"""
    paths = [Path(p) for p in paths]
    if len(paths) == 1 and paths[0].is_dir():
        found = sorted(paths[0].glob("seg_*.mp4"), key=lambda p: p.name)
        if not found:
            raise PipelineError(f"{paths[0]} に区間の動画（seg_*.mp4）がありません")
        return found
    missing = [str(p) for p in paths if not p.is_file()]
    if missing:
        raise PipelineError("区間の動画が見つかりません: " + ", ".join(missing))
    return paths


def _ranges(segments):
    """名前が seg_<最初>_<終わり>.mp4 なら [(最初, 終わり)]。すき間・重なりがあれば止める。名前が違う区間があれば None。"""
    found = [_SEGMENT.fullmatch(p.name) for p in segments]
    if not all(found):
        return None
    ranges = [(int(m[1]), int(m[2])) for m in found]
    for (name, (a, b)) in zip(segments, ranges):
        if b <= a:
            raise PipelineError(f"{name.name}: 終わりのコマ {b} が最初のコマ {a} 以下です")
    for (left, (_, b)), (right, (a, _)) in zip(zip(segments, ranges), zip(segments[1:], ranges[1:])):
        if a != b:
            raise PipelineError(f"区間がつながっていません: {left.name} はコマ {b} で終わり、{right.name} はコマ {a} から始まります")
    return ranges


def _concat_list(segments, path):
    """ffmpeg の concat の一覧（実制作と同じ file '<スラッシュ区切りの絶対パス>'。' はエスケープする）。"""
    lines = []
    for segment in segments:
        name = segment.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{name}'\n")
    path.write_bytes("".join(lines).encode("utf-8"))


def join(segments, out, audio=None, audio_start=0.0, fade_in=FADE_IN_S, fade_out=FADE_OUT_S, force=False):
    """区間をつないで（音を付けて）元データを書き、結果の辞書を返す。"""
    segments = _segments(segments)
    out = Path(out)
    if out.suffix.lower() != ".mp4":
        raise PipelineError(f"--out は .mp4 にしてください: {out.name}")
    if not fade_in >= 0 or not fade_out >= 0:
        raise PipelineError(f"--fade-in と --fade-out は 0 以上にしてください: {fade_in}, {fade_out}")
    if audio is not None and not Path(audio).is_file():
        raise PipelineError(f"音声が見つかりません: {audio}")
    if not audio_start >= 0:
        raise PipelineError(f"--audio-start は 0 以上にしてください: {audio_start}")
    ranges = _ranges(segments)
    infos = [probe.inspect(p)["video"] for p in segments]
    keys = {(i["codec"], i["pix_fmt"], i["width"], i["height"], i["fps"]) for i in infos}
    if len(keys) != 1:
        raise PipelineError("区間の寸法・fps・映像の符号化がそろっていません（再エンコードせずにつなぐため）: "
                            + "; ".join(f"{p.name} {i['codec']} {i['pix_fmt']} {i['width']}x{i['height']} {i['fps']}fps"
                                        for p, i in zip(segments, infos)))
    fps = infos[0]["fps"]
    if not fps:
        raise PipelineError(f"{segments[0].name} の fps を読み取れません")
    records = []
    for index, path in enumerate(segments):
        frames = probe.decode(path, "v")["frames"]
        expected = None if ranges is None else ranges[index][1] - ranges[index][0]
        if expected is not None and frames != expected:
            raise PipelineError(f"{path.name} のコマ数 {frames} が名前の区間 {ranges[index][0]}〜{ranges[index][1]}（{expected} コマ）と合いません")
        records.append({"path": str(path), "sha256": sha256(path), "frames": frames,
                        "range": None if ranges is None else list(ranges[index])})
    total = sum(r["frames"] for r in records)
    duration = total / fps
    if fade_in + fade_out > duration:
        raise PipelineError(f"フェード（{fade_in:g} + {fade_out:g} 秒）が長さ {duration:g} 秒より長いです")
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".deliver-", dir=out.parent))       # 置き換えを同じドライブで行うため、出力の隣に作る
    commands = []
    try:
        listing = work / "concat.txt"
        _concat_list(segments, listing)
        video = work / "video.mp4"
        _run(["-f", "concat", "-safe", "0", "-i", listing, "-c", "copy", video], commands)
        joined = probe.decode(video, "v")["frames"]
        if joined != total:
            raise PipelineError(f"つないだ映像のコマ数 {joined} が区間の合計 {total} と合いません")
        master = work / "master.mp4"
        if audio is not None:
            sound = work / "audio.m4a"
            fades = ([f"afade=t=in:st=0:d={float(fade_in)}"] if fade_in > 0 else []) \
                + ([f"afade=t=out:st={duration - fade_out:.6f}:d={float(fade_out)}"] if fade_out > 0 else [])
            _run(["-ss", f"{audio_start:.6f}", "-t", f"{duration:.6f}", "-i", Path(audio), *(["-af", ",".join(fades)] if fades else []),
                  "-c:a", "aac", "-b:a", "192k", "-ar", "48000", sound], commands)
            _run(["-i", video, "-i", sound, "-map", "0:v", "-map", "1:a", "-c", "copy", "-t", f"{duration:.6f}", "-movflags", "+faststart",
                  master], commands)
        else:
            _run(["-i", video, "-map", "0:v", "-c", "copy", "-t", f"{duration:.6f}", "-movflags", "+faststart", master], commands)
        payload_sha = sha256(master)
        state = "written"
        if out.is_file():
            if sha256(out) == payload_sha:
                state = "unchanged"
            elif not force:
                raise PipelineError(f"中身の違う {out.name} が既にあります（上書きするなら --force）")
        if state == "written":
            os.replace(master, out)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    check = probe.check_video(out, fps=fps, frames=total, audio=audio is not None, audio_s=duration if audio is not None else None)
    record = {"schema_version": SCHEMA, "segments": records, "fps": fps, "frames": total, "duration_s": round(duration, 6),
              "audio": None if audio is None else {"path": str(audio), "sha256": sha256(audio), "start_s": audio_start,
                                                   "duration_s": round(duration, 6), "fade_in_s": fade_in, "fade_out_s": fade_out,
                                                   "codec": "aac 192k 48000Hz"},
              "ffmpeg": _ffmpeg_info(), "commands": [[str(part) for part in command[1:]] for command in commands],
              "master": {"path": str(out), "bytes": out.stat().st_size, "sha256": sha256(out), "state": state},
              "check": check, "technical_pass": check["pass"]}
    record_path = out.with_name(f"{out.stem}.deliver.json")
    atomic_json(record_path, record)
    return {"master": str(out), "sha256": record["master"]["sha256"], "bytes": record["master"]["bytes"], "state": state,
            "frames": total, "fps": fps, "duration_s": record["duration_s"], "segments": len(records),
            "audio": None if audio is None else str(audio), "technical_pass": check["pass"], "failures": check["failures"],
            "record": str(record_path)}
