"""受け渡しの書き出し（`mv deliver`）。描いた区間の動画をつなぎ、音を付けて元データ（master）にし、確認用の小さい版と、
受け渡しのための片を作る。cv2 を使わない。

    mv deliver join SEGMENT... --out MASTER.mp4 [--audio SONG.wav [--audio-start SEC] [--fade-in 0.02] [--fade-out 0.25]] [--force]
    mv deliver review-copy MASTER --out REVIEW.mp4 [--limit-bytes 28000000] [--target-bytes 27000000] [--threads 1] [--force]
    mv deliver split MASTER --out DIR [--part-bytes 25000000] [--name NAME] [--title TITLE] [--force]

join:

- SEGMENT: 区間の動画（同じ寸法・fps・符号化で書いたもの）を、つなぐ順に。フォルダを 1 つ渡すと、その中の seg_*.mp4 を名前の順に使う。
  名前が seg_<最初のコマ>_<終わりのコマ>.mp4 なら（実制作の区間の描画と同じ）、区間がすき間なく続くことと、区間ごとのコマ数
  （終わり − 最初）を確かめる。どの区間も同じ寸法・fps・映像の符号化であること（つなぎは再エンコードしないため）。
- つなぎ: ffmpeg の concat で、映像を再エンコードせずにつなぐ（-c copy）。全体のコマ数を確かめる。
- 音（--audio）: --audio-start 秒から映像の長さを切り出し、頭と終わりにフェードを掛けて AAC 192k・48kHz にする。映像の長さは
  パケットの時刻から正確に出す（表示の fps は丸めてあるため。整数の fps ならコマ数 / fps と同じ）。フェードの長さは音を付けるときだけ確かめる。
- 元データ: 映像と音を -c copy で合わせ、映像の長さで切り、faststart で書く。既にあって中身が違えば --force が要る。
  --out が入力（区間・--audio）と同じファイルなら、--force でも書かない。
- 記録 <MASTER の名前>.deliver.json: 区間（sha256・コマ数）・音・ffmpeg（パス・版・sha256）・コマンド・元データの bytes と sha256・
  構造検査（technical_pass。作品の採否ではない）。

手順と ffmpeg の引数は実制作の仕上げ（片をつないで音を付ける）と同じ。同じ ffmpeg（ORPHEUS_FFMPEG で選べる）と同じ入力なら、
元データは同じバイト列になる。色のタグは区間のまま（つなぐだけで変えない）。

review-copy（確認用の小さい版。実制作の仕上げと同じ式）:
- 元データが --limit-bytes（28,000,000）未満なら、そのまま写す。
- 超えたら 2 パスの H.264 にする。映像のビットレート = int((--target-bytes × 8 / 長さ − 192k) × 0.985)、maxrate 11M・bufsize 22M、
  preset slow、yuv420p。音はそのまま写す。元データとの SSIM（ffmpeg の ssim の All）を測る。
- x264 は複数のスレッドで動かすと、同じ入力でも走らせるたびにバイト列がわずかに変わる（1 パス目の統計から変わる。測った）。
  既定の --threads 1 は毎回同じバイト列になる。--threads 0 は ffmpeg に任せる（実制作と同じ。速いが毎回変わる）。
- 記録 <REVIEW の名前>.review.json: 元データ・作り方（写す / 2 パス）・ビットレート・コマンド・確認用の bytes と sha256・SSIM・構造検査。

split（受け渡しの片。実制作の受け渡しの道具と同じ形）:
- 元データを確かめる（moov が mdat より前の faststart、映像の流れ）。--part-bytes（25,000,000）ごとに <NAME>.partNN へ切る。
- join_master.bat（CP932・CRLF）: copy /b で片をつなぎ、certutil の SHA-256 を照合して【OK】／【NG】を出す。--title は bat の
  見出しの行に入る（既定は NAME）。NAME は ASCII の英数字と ._- だけ（bat の文字コードと区切りに依らないように）。
- <NAME の _master より前>_parts_SHA256SUMS.txt（片と元データの sha256）。
- 片を順につないだ sha256 が元データと同じかを、その場で確かめる（bat は実行しない）。記録は DIR の隣の <DIR の名前>.split.json
  （DIR を "." で渡しても、絶対パスにしてから名前と隣を決める）。片・bat・SHA256SUMS が元データと同じファイルになるなら、--force でも書かない。
"""
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from fractions import Fraction
from pathlib import Path

from . import media
from .fx import probe
from .io import PipelineError, atomic_json, sha256

SCHEMA = "deliver-join/0.1"
REVIEW_SCHEMA = "deliver-review/0.1"
SPLIT_SCHEMA = "deliver-split/0.1"
FADE_IN_S, FADE_OUT_S = 0.02, 0.25          # 実制作の仕上げと fx の書き出しの既定
REVIEW_LIMIT, REVIEW_TARGET = 28_000_000, 27_000_000      # 確認用: これ未満ならそのまま / 2 パスの目標（バイト）
PART_BYTES = 25_000_000
_SEGMENT = re.compile(r"seg_(\d+)_(\d+)\.mp4", re.IGNORECASE)
_NAME = re.compile(r"[A-Za-z0-9._-]+")
_TITLE_BAD = set('&|<>^%"\r\n')
BAT = '''@echo off
rem {title} の元データ（{nbytes:,} バイト）を片から結合して SHA-256 を照合します（このフォルダで実行）
cd /d "%~dp0"
for %%F in ({parts}) do if not exist "%%F" (echo 見つからない片があります: %%F & pause & exit /b 1)
copy /y /b {plus} {name} >nul
if errorlevel 1 (echo 片を結合できませんでした & pause & exit /b 1)
certutil -hashfile {name} SHA256
echo 期待する SHA-256: {sha}
certutil -hashfile {name} SHA256 | findstr /i /c:"{sha}" >nul
if errorlevel 1 (echo 【NG】一致しません。片がそろっているか確かめてください) else (echo 【OK】一致しました。{name} が元の動画です)
pause
'''


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


def video_seconds(path):
    """映像の長さ（秒の Fraction）。パケットの time base・pts・長さから正確に出す（最後のパケットの終わり − 最初の pts）。

    ffmpeg が表示する fps は 23.98 のように丸めてあるので、コマ数 / fps では端数のある fps（24000/1001 など）の長さがずれる
    （14,400 コマで 600.6 秒のところが 600.5004 秒）。整数の fps なら、コマ数 / fps とまったく同じ値になる。"""
    result = subprocess.run([media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:v:0",
                             "-c", "copy", "-f", "framecrc", "-"], capture_output=True, text=True, encoding="utf-8", errors="replace")
    base = re.search(r"^#tb 0: (\d+)/(\d+)", result.stdout, re.MULTILINE)
    start = end = None
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",")]
        if line.startswith("#") or len(parts) < 4:
            continue
        pts, length = int(parts[2]), int(parts[3])          # 列は stream, dts, pts, 長さ, 大きさ, crc
        start = pts if start is None else min(start, pts)
        end = pts + length if end is None else max(end, pts + length)
    if result.returncode != 0 or base is None or start is None:
        raise PipelineError(f"{Path(path).name} の映像の時刻を読み取れません: {result.stderr.strip()[-300:]}")
    return Fraction(end - start) * Fraction(int(base[1]), int(base[2]))


def _concat_list(segments, path):
    """ffmpeg の concat の一覧（実制作と同じ file '<スラッシュ区切りの絶対パス>'。' はエスケープする）。"""
    lines = []
    for segment in segments:
        name = segment.resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{name}'\n")
    path.write_bytes("".join(lines).encode("utf-8"))


def _same_file(path):
    return os.path.normcase(str(Path(path).resolve()))


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
    inputs = {_same_file(path): path.name for path in segments}
    if audio is not None:
        inputs[_same_file(audio)] = Path(audio).name
    if _same_file(out) in inputs:          # 置き換えると入力が消える（区間のフォルダに書く、--audio に動画を渡すなど）
        raise PipelineError(f"--out {out.name} は入力（{inputs[_same_file(out)]}）と同じファイルです。入力は --force でも上書きしません")
    record_path = out.with_name(f"{out.stem}.deliver.json")
    if _same_file(record_path) in inputs:  # 記録を書くと入力が JSON で置き換わる
        raise PipelineError(f"記録 {record_path.name} が入力（{inputs[_same_file(record_path)]}）と同じファイルになります。--out の名前を変えてください")
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
    duration = float(sum((video_seconds(path) for path in segments), Fraction(0)))     # 表示の fps（丸めてある）では割らない
    if audio is not None and fade_in + fade_out > duration:                        # フェードは音にだけ掛ける
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
    atomic_json(record_path, record)
    return {"master": str(out), "sha256": record["master"]["sha256"], "bytes": record["master"]["bytes"], "state": state,
            "frames": total, "fps": fps, "duration_s": record["duration_s"], "segments": len(records),
            "audio": None if audio is None else str(audio), "technical_pass": check["pass"], "failures": check["failures"],
            "record": str(record_path)}


# ---- 確認用の小さい版 ---------------------------------------------------------------------------

def _ssim(review, master):
    """ffmpeg の ssim（All）。読めなければ None。"""
    command = [media.ffmpeg(), "-hide_banner", "-nostdin", "-i", str(review), "-i", str(master), "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.search(r"All:([0-9.]+)", result.stderr)
    return float(found.group(1)) if found else None


def _place(produced, out, force):
    """作ったファイルを out へ置く。同じ中身なら unchanged、違えば --force が要る。"""
    if out.is_file():
        if sha256(out) == sha256(produced):
            return "unchanged"
        if not force:
            raise PipelineError(f"中身の違う {out.name} が既にあります（上書きするなら --force）")
    os.replace(produced, out)
    return "written"


def review_copy(master, out, limit=REVIEW_LIMIT, target=REVIEW_TARGET, maxrate="11M", bufsize="22M", preset="slow", threads=1,
                force=False):
    """確認用の小さい版を書き、結果の辞書を返す。"""
    master, out = Path(master), Path(out)
    if not master.is_file():
        raise PipelineError(f"元データが見つかりません: {master}")
    if out.suffix.lower() != ".mp4":
        raise PipelineError(f"--out は .mp4 にしてください: {out.name}")
    if _same_file(out) == _same_file(master):
        raise PipelineError("--out が元データと同じファイルです")
    record_path = out.with_name(f"{out.stem}.review.json")
    if _same_file(record_path) == _same_file(master):    # 記録を書くと元データが JSON で置き換わる
        raise PipelineError(f"記録 {record_path.name} が元データと同じファイルになります。--out の名前を変えてください")
    if not target > 0 or not limit > 0:
        raise PipelineError(f"--target-bytes と --limit-bytes は 0 より大きくしてください: {target}, {limit}")
    if threads < 0:
        raise PipelineError(f"--threads は 0（ffmpeg に任せる）か 1 以上にしてください: {threads}")
    info = probe.inspect(master)
    fps = info["video"]["fps"]
    if not fps:
        raise PipelineError(f"{master.name} の fps を読み取れません")
    frames = probe.decode(master, "v")["frames"]
    duration = float(video_seconds(master))        # 表示の fps（丸めてある）では割らない。整数の fps ならコマ数 / fps と同じ
    size = master.stat().st_size
    out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=".deliver-", dir=out.parent))
    commands, rate, ssim = [], None, 1.0
    try:
        review = work / "review.mp4"
        if size < limit:
            shutil.copyfile(master, review)
            method = f"元データをそのまま（{limit:,} バイト未満）"
        else:
            rate = int((target * 8 / duration - 192e3) * 0.985)
            if rate <= 0:
                raise PipelineError(f"--target-bytes {target:,} では、長さ {duration:g} 秒の映像に使えるビットレートが残りません")
            log = work / "2pass"
            encoder = ["-c:v", "libx264", *(["-threads", str(threads)] if threads else []), "-preset", preset, "-b:v", str(rate),
                       "-maxrate", maxrate, "-bufsize", bufsize, "-pix_fmt", "yuv420p"]
            _run(["-i", master, *encoder, "-pass", "1", "-passlogfile", log, "-an", "-f", "null", os.devnull], commands)
            _run(["-i", master, *encoder, "-pass", "2", "-passlogfile", log, "-c:a", "copy", "-movflags", "+faststart", review], commands)
            ssim = _ssim(review, master)
            method = f"2 パス {rate / 1e6:.2f} Mbps（maxrate {maxrate}）"
        written = probe.decode(review, "v")["frames"]
        if written != frames:
            raise PipelineError(f"確認用の版のコマ数 {written} が元データの {frames} と合いません")
        state = _place(review, out, force)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    check = probe.check_video(out, fps=fps, frames=frames, audio=info["audio"] is not None)
    record = {"schema_version": REVIEW_SCHEMA,
              "master": {"path": str(master), "bytes": size, "sha256": sha256(master), "frames": frames, "fps": fps},
              "method": method, "settings": {"limit_bytes": limit, "target_bytes": target, "video_bitrate": rate, "maxrate": maxrate,
                                             "bufsize": bufsize, "preset": preset, "threads": threads},
              "repeatable": rate is None or threads == 1,
              "ffmpeg": _ffmpeg_info(), "commands": [[str(part) for part in command[1:]] for command in commands],
              "review": {"path": str(out), "bytes": out.stat().st_size, "sha256": sha256(out), "state": state},
              "ssim_vs_master": ssim, "check": check, "technical_pass": check["pass"]}
    atomic_json(record_path, record)
    return {"review": str(out), "bytes": record["review"]["bytes"], "sha256": record["review"]["sha256"], "state": state,
            "method": method, "ssim_vs_master": ssim, "repeatable": record["repeatable"], "frames": frames,
            "technical_pass": check["pass"], "failures": check["failures"], "record": str(record_path)}


# ---- 受け渡しの片 ----------------------------------------------------------------------------------

def top_boxes(path):
    """MP4 のいちばん上の箱の名前の並び（ftyp・moov・mdat など）。"""
    path = Path(path)
    size, out, position = path.stat().st_size, [], 0
    with path.open("rb") as stream:
        while position < size:
            stream.seek(position)
            head = stream.read(16)
            if len(head) < 8:
                raise PipelineError(f"{path.name} の箱を読めません（{position} バイト目）")
            length = int.from_bytes(head[:4], "big")
            if length == 1:
                length = int.from_bytes(head[8:16], "big")
            elif length == 0:
                length = size - position
            if length < 8:
                raise PipelineError(f"{path.name} の箱の大きさが壊れています（{position} バイト目）")
            out.append(head[4:8].decode("latin1"))
            position += length
    return out


def _write_new(path, data):
    """data を、同じフォルダに新しく作った一時ファイル（ランダムな名前。既にあるファイルやリンクには作らない）に書いてから、
    path へ置き換える。決まった名前（<名前>.tmp）だと、既にある同じ名前のファイルを書き換えて消したり、それが元データへの
    リンクなら元データを書き換えたりする。path がリンクなら、リンクそのものを置き換える（リンクの先は書き換えない）。"""
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _sums_name(name):
    """片の SHA256SUMS の名前（実制作と同じく、NAME の _master より前。_master が無ければ拡張子の前）。"""
    stem = name.rsplit("_master", 1)[0] if "_master" in name else Path(name).stem
    return f"{stem}_parts_SHA256SUMS.txt"


def split(master, out_dir, part_bytes=PART_BYTES, name=None, title=None, force=False):
    """元データを片に切り、結合の bat と SHA256SUMS を書いて、結果の辞書を返す。"""
    master, out_dir = Path(master), Path(out_dir)
    if not master.is_file():
        raise PipelineError(f"元データが見つかりません: {master}")
    name = name or master.name
    if not _NAME.fullmatch(name) or name in (".", ".."):
        raise PipelineError(f"--name は ASCII の英数字と . _ - だけにしてください（結合の bat で使うため）: {name!r}")
    title = title if title is not None else name
    bad = sorted(set(title) & _TITLE_BAD)
    if bad:
        raise PipelineError(f"--title に bat で使えない字 {' '.join(repr(c) for c in bad)} があります")
    try:
        title.encode("cp932")
    except UnicodeEncodeError as error:
        raise PipelineError(f"--title の {title[error.start:error.end]!r} は CP932（bat の文字コード）で書けません") from error
    if part_bytes < 1:
        raise PipelineError(f"--part-bytes は 1 以上にしてください: {part_bytes}")
    folder = out_dir.resolve()             # "." で渡されても、記録は片のフォルダの隣に、フォルダの名前で書く
    if not folder.name:
        raise PipelineError(f"--out には名前のあるフォルダを指定してください（記録をその隣の <フォルダの名前>.split.json に書くため）: {out_dir}")
    boxes = top_boxes(master)
    if "moov" not in boxes or "mdat" not in boxes or boxes.index("moov") > boxes.index("mdat"):
        raise PipelineError(f"{master.name} は moov が mdat より前にありません（faststart で書き直してください）: {boxes}")
    streams = probe.inspect(master)
    total = master.stat().st_size
    count = max(1, -(-total // part_bytes))
    width = max(2, len(str(count - 1)))
    parts = [f"{name}.part{i:0{width}d}" for i in range(count)]
    digest = sha256(master)
    bat = BAT.format(title=title, nbytes=total, parts=" ".join(parts), plus=" + ".join(parts), name=name, sha=digest)
    bat_bytes = bat.replace("\n", "\r\n").encode("cp932")
    targets = [out_dir / p for p in parts] + [out_dir / "join_master.bat", out_dir / _sums_name(name)]
    record_path = folder.parent / f"{folder.name}.split.json"
    if any(_same_file(target) == _same_file(master) for target in targets + [record_path]):     # 置き換えると元データが消える
        raise PipelineError(f"片・bat・SHA256SUMS・記録の名前が元データ {master.name} と同じになります。--name か --out を変えてください"
                            "（元データは --force でも上書きしません）")
    if not force:
        clash = [t.name for t in targets if t.exists()]
        if clash:
            raise PipelineError(f"{out_dir} に同じ名前のファイルがあります（上書きするなら --force）: {', '.join(clash)}")
    out_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with master.open("rb") as stream:
        for part in parts:
            data = stream.read(part_bytes)
            target = out_dir / part
            _write_new(target, data)
            records.append({"name": part, "bytes": len(data), "sha256": sha256(target)})
    _write_new(out_dir / "join_master.bat", bat_bytes)
    sums = "".join(f"{r['sha256']}  {r['name']}\n" for r in records) + f"{digest}  {name}\n"
    sums_path = out_dir / _sums_name(name)
    _write_new(sums_path, sums.encode("utf-8"))
    joined = hashlib.sha256()
    for part in parts:
        with (out_dir / part).open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                joined.update(block)
    ok = joined.hexdigest() == digest
    if not ok:
        raise PipelineError("片をつないだ sha256 が元データと合いません（書き出しの途中で元データが変わった可能性があります）")
    record = {"schema_version": SPLIT_SCHEMA,
              "master": {"path": str(master), "name": name, "bytes": total, "sha256": digest, "top_boxes": boxes,
                         "video": streams["video"], "audio": streams["audio"]},
              "part_bytes": part_bytes, "parts": records,
              "bat": {"name": "join_master.bat", "encoding": "cp932", "newline": "CRLF", "sha256": sha256(out_dir / "join_master.bat")},
              "sums": {"name": sums_path.name, "sha256": sha256(sums_path)},
              "join_check": {"ok": ok, "joined_sha256": joined.hexdigest(), "how": "片を順につないで sha256 を数えた（bat は実行していない）"}}
    atomic_json(record_path, record)
    return {"dir": str(out_dir), "parts": [r["name"] for r in records], "part_bytes": part_bytes, "bat": str(out_dir / "join_master.bat"),
            "sums": str(sums_path), "master_sha256": digest, "join_check": ok, "record": str(record_path)}
