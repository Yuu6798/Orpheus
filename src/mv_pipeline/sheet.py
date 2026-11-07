"""見るためのシート（`mv sheet`）。動画か透過の連番から、指定した時刻か timeline の出来事のコマを 1 枚に並べる。

    mv sheet 動画か連番のフォルダ --out sheet.png (--at 秒 … | --events timeline.json [--kinds lyrics,flashes,seams])
             [--fps 30] [--backgrounds white,black,gray] [--cell-width 320] [--columns 4] [--font PATH]

- コマごとに「コマ番号・秒・出来事の名前」を書き込む。透過の連番なら、白・黒・灰（--backgrounds）の上に置いた版を横に並べる
  （縁と読みやすさの確認用）。動画は不透明として扱い（アルファつきの動画は先に `mv overlay` などで平らにする）、時刻を --columns 列に折り返す。
- 時刻 → コマ番号は validate.frame_at（いちばん近いコマ）。連番の fps は --fps（--events の timeline があればその fps）。
  動画の fps は動画から読み、--fps や timeline の fps と 1% 以上違えば止める。
- 字は --font（TTF / TTC）か、よくある場所の日本語の書体を探して使う。見つからなければ Pillow の既定の書体で、日本語を含む出来事の名前は
  ASCII の略記（line 1 / flash / seam / given）にして画像に書き、正式な名前は隣の JSON に残す（記録の `label_font` にどちらかが分かる）。
- 検査ではなく閲覧の道具。合否は出さない。隣に sheet.json（セルごとのコマ番号・秒・出来事・背景・そのコマの sha256、出所）を書くので、
  AI が動画を見ずに「どのコマに何が写っているか」を読める入口になる。

cv2 は使わない（Pillow・numpy・ffmpeg）。
"""
import hashlib
import math
import os
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import validate
from .inspection import video
from .io import PipelineError, atomic_json, read_json, sha256

FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")
BACKGROUNDS = {"white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128)}
DEFAULT_BACKGROUNDS = ("white", "black", "gray")
KINDS = ("lyrics", "flashes", "seams")
LABEL_HEIGHT_SHARE = 0.11
FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/noto/NotoSansCJKjp-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/opentype/ipafont-gothic/ipag.ttf",
    "/System/Library/Fonts/ヒラギノ角ゴシック W4.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "C:/Windows/Fonts/YuGothM.ttc", "C:/Windows/Fonts/meiryo.ttc", "C:/Windows/Fonts/msgothic.ttc",
)


# ---- 入力 -----------------------------------------------------------------------

def natural_key(path):
    """frame2 < frame10 になる並び。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def _frame_files(folder):
    files = sorted((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES), key=natural_key)
    if not files:
        raise PipelineError(f"連番（{'/'.join(FRAME_SUFFIXES)}）がありません: {folder}")
    return files


def _has_alpha(image):
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def describe_source(path, fps=None, fps_origin="--fps"):
    """{kind: frames|video, path, fps, frames, size, transparent, files? | info}。"""
    path = Path(path)
    if path.is_dir():
        files = _frame_files(path)
        if fps is None:
            raise PipelineError("連番には --fps（または --events の timeline）が要ります")
        with Image.open(files[0]) as image:
            size, transparent = image.size, _has_alpha(image)
        return {"kind": "frames", "path": str(path), "fps": float(fps), "frames": len(files), "size": list(size),
                "transparent": transparent, "files": files}
    if not path.is_file():
        raise PipelineError(f"動画か連番のフォルダが見つかりません: {path}")
    info = video.describe(path)                      # ffmpeg の情報（1 回）
    from .fx import probe
    decoded = probe.decode(path, "v")                # コマ数は全部復号して数える（probe.check_video と同じ）
    if not decoded["ok"]:
        raise PipelineError(f"動画を復号できません: {decoded['error']}")
    if fps is not None and not math.isclose(float(fps), info["fps"], rel_tol=1e-2):
        raise PipelineError(f"{fps_origin} の fps {float(fps):g} が動画の fps {info['fps']:g} と違います（動画の fps を使います）")
    return {"kind": "video", "path": str(path), "fps": info["fps"], "frames": decoded["frames"], "size": [info["width"], info["height"]],
            "transparent": False, "info": info, "sha256": sha256(path)}


def events_from_timeline(timeline, kinds=KINDS):
    """timeline の出来事 [(秒, 名前, ASCII の略記)]（行の開始・フラッシュ・切れ目）。時刻順。"""
    items = []
    if "lyrics" in kinds:
        items += [(line["start"], f"行の開始: {line['text']}", f"line {i}") for i, line in enumerate(timeline.get("lyrics", []), 1)]
    if "flashes" in kinds:
        items += [(t, "フラッシュ", "flash") for t in timeline.get("flashes", [])]
    if "seams" in kinds:
        items += [(t, "切れ目", "seam") for t in timeline.get("seams", [])]
    return sorted(items, key=lambda item: item[0])


def _load_frames(source, indices):
    """{コマ番号: (uint8 RGB か RGBA, sha256)}。連番は使うコマだけ読む。"""
    wanted = sorted({i for i in indices if 0 <= i < source["frames"]})
    out = {}
    if source["kind"] == "frames":
        width, height = source["size"]
        for index in wanted:
            data = source["files"][index].read_bytes()
            with Image.open(source["files"][index]) as image:
                if image.size != (width, height):
                    raise PipelineError(f"連番の寸法がそろっていません: {source['files'][index].name} は {image.size[0]}x{image.size[1]}（最初のコマは {width}x{height}）")
                out[index] = (np.asarray(image.convert("RGBA" if source["transparent"] else "RGB")), hashlib.sha256(data).hexdigest())
        return out
    for index, image in video.grab(source["path"], wanted, source["info"], total=source["frames"]).items():
        out[index] = (image, hashlib.sha256(np.ascontiguousarray(image).tobytes()).hexdigest())
    return out


# ---- 描画 -----------------------------------------------------------------------

def over(rgba, color):
    """straight α の RGBA を単色の背景に置く（sRGB の値のまま）。"""
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    back = np.array(color, np.float32).reshape(1, 1, 3)
    return np.clip(rgba[..., :3].astype(np.float32) * alpha + back * (1.0 - alpha) + 0.5, 0, 255).astype(np.uint8)


def find_font(path=None):
    """(書体のパス | None)。--font が無ければ、よくある場所の日本語の書体を探す。"""
    if path is not None:
        if not Path(path).is_file():
            raise PipelineError(f"--font のファイルが見つかりません: {path}")
        return str(path)
    return next((p for p in FONT_CANDIDATES if Path(p).is_file()), None)


def _font(pixels, path):
    if path:
        try:
            return ImageFont.truetype(path, pixels)
        except OSError as error:
            raise PipelineError(f"書体を読めません: {path}（{error}）") from error
    try:
        return ImageFont.load_default(size=pixels)
    except (TypeError, OSError):
        return ImageFont.load_default()


def cell(image, width, label, font_path=None):
    """コマを幅 width に縮め、上に黒帯の字（label）を足した uint8 RGB。"""
    source = Image.fromarray(image)
    height = max(2, int(round(source.height * width / source.width)))
    scaled = source.resize((width, height), Image.Resampling.BOX)
    band = max(14, int(round(height * LABEL_HEIGHT_SHARE)))
    canvas = Image.new("RGB", (width, height + band), (0, 0, 0))
    canvas.paste(scaled, (0, band))
    ImageDraw.Draw(canvas).text((4, 2), label, font=_font(max(10, band - 6), font_path), fill=(255, 255, 255))
    return np.asarray(canvas)


def _grid(cells, columns):
    rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    blank = np.zeros_like(cells[0])
    return np.vstack([np.hstack(row + [blank] * (columns - len(row))) for row in rows])


def _save_png(image, out):
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp.png")
    try:
        with open(tmp, "wb") as stream:
            Image.fromarray(image).save(stream, format="PNG")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, out)
    finally:
        if tmp.exists():
            tmp.unlink()


def build(source_path, out, at=None, events_path=None, fps=None, backgrounds=DEFAULT_BACKGROUNDS, cell_width=320, columns=4, kinds=KINDS, font=None):
    """シート（PNG）と sheet.json を書く。要約を返す。"""
    if (at is None or len(at) == 0) == (events_path is None):
        raise PipelineError("--at（秒の並び）か --events（timeline.json）のどちらか一方を指定してください")
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise PipelineError(f"--kinds は {', '.join(KINDS)} から選んでください（不明: {unknown}）")
    bad = [b for b in backgrounds if b not in BACKGROUNDS]
    if bad or not backgrounds:
        raise PipelineError(f"--backgrounds は {', '.join(BACKGROUNDS)} から 1 つ以上選んでください（不明: {bad}）")
    if cell_width < 32 or columns < 1:
        raise PipelineError("--cell-width は 32 以上、--columns は 1 以上にしてください")
    timeline, fps_origin = None, "--fps"
    if events_path is not None:
        timeline = read_json(events_path)
        if fps is None and timeline.get("fps"):
            fps, fps_origin = timeline["fps"], "timeline"
    source = describe_source(source_path, fps, fps_origin)
    fps = source["fps"]
    font_path = find_font(font)
    wanted = [(float(t), "指定", "given") for t in at] if events_path is None else events_from_timeline(timeline, kinds)
    if not wanted:
        raise PipelineError("並べる時刻がありません（timeline に行・フラッシュ・切れ目が無い）")
    entries = [{"t": t, "frame": validate.frame_at(t, fps), "event": name, "event_ascii": short} for t, name, short in wanted]
    for entry in entries:
        entry["missing"] = not 0 <= entry["frame"] < source["frames"]
    frames = _load_frames(source, [e["frame"] for e in entries if not e["missing"]])
    cells, layout = [], []
    for entry in entries:
        if entry["missing"]:
            continue
        image, digest = frames[entry["frame"]]
        shown = entry["event"] if font_path else entry["event_ascii"]
        label = f"f{entry['frame']} {entry['t']:.3f}s {shown}"
        record = {k: entry[k] for k in ("t", "frame", "event", "event_ascii")}
        if source["transparent"]:
            for name in backgrounds:
                cells.append(cell(over(image, BACKGROUNDS[name]), cell_width, f"{label} [{name}]", font_path))
                layout.append({**record, "background": name, "cell": len(cells) - 1, "sha256": digest})
        else:
            cells.append(cell(image[..., :3], cell_width, label, font_path))
            layout.append({**record, "background": None, "cell": len(cells) - 1, "sha256": digest})
    if not cells:
        raise PipelineError(f"どの時刻も動画の中にありません（コマ数 {source['frames']}、fps {fps:g}）")
    per_row = len(backgrounds) if source["transparent"] else columns
    sheet = _grid(cells, per_row)
    out = Path(out)
    _save_png(sheet, out)
    record = {"schema_version": "sheet/0.1", "sheet": str(out), "size": [sheet.shape[1], sheet.shape[0]], "columns": per_row,
              "cell_width": cell_width, "fps": fps, "fps_origin": fps_origin if source["kind"] == "frames" else "video",
              "label_font": font_path, "labels": "full" if font_path else "ascii",
              "source": {k: v for k, v in source.items() if k not in ("files", "info")},
              "events": None if events_path is None else {"path": str(events_path), "sha256": sha256(events_path), "kinds": list(kinds)},
              "backgrounds": list(backgrounds) if source["transparent"] else [],
              "cells": layout, "missing": [{k: e[k] for k in ("t", "frame", "event")} for e in entries if e["missing"]],
              "note": "閲覧用。合否は出さない。cells[*].sha256 はそのコマの画素（連番はファイル）の sha256"}
    atomic_json(out.with_suffix(".json"), record)
    return {"sheet": str(out), "json": str(out.with_suffix(".json")), "cells": len(cells), "rows": -(-len(cells) // per_row),
            "size": record["size"], "missing": len(record["missing"]), "transparent": source["transparent"], "labels": record["labels"]}
