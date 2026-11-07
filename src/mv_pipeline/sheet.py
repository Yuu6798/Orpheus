"""見るためのシート（`mv sheet`）。動画か透過の連番から、指定した時刻か timeline の出来事のコマを 1 枚に並べる。

    mv sheet 動画か連番のフォルダ --out sheet.png (--at 秒 … | --events timeline.json [--kinds lyrics,flashes,seams])
             [--fps 30] [--backgrounds white,black,gray] [--cell-width 320] [--columns 4]

- コマごとに「コマ番号・秒・出来事の名前」を書き込む。透過の連番なら、白・黒・灰（--backgrounds）の上に置いた版を横に並べる
  （縁と読みやすさの確認用）。不透明な動画は時刻を --columns 列に折り返す。
- 時刻 → コマ番号は validate.frame_at（いちばん近いコマ）。連番の fps は --fps（--events の timeline があればその fps）。
- 検査ではなく閲覧の道具。合否は出さない。隣に sheet.json（セルごとのコマ番号・秒・出来事・出所）を書くので、
  AI が動画を見ずに「どのコマに何が写っているか」を読める入口になる。

cv2 は使わない（Pillow・numpy・ffmpeg）。
"""
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


# ---- 入力 -----------------------------------------------------------------------

def _frame_files(folder):
    files = sorted(p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES)
    if not files:
        raise PipelineError(f"連番（{'/'.join(FRAME_SUFFIXES)}）がありません: {folder}")
    return files


def describe_source(path, fps=None):
    """{kind: frames|video, path, fps, frames, size, transparent, files?}。"""
    path = Path(path)
    if path.is_dir():
        files = _frame_files(path)
        if fps is None:
            raise PipelineError("連番には --fps（または --events の timeline）が要ります")
        with Image.open(files[0]) as image:
            size, transparent = image.size, image.mode in ("RGBA", "LA", "P")
        return {"kind": "frames", "path": str(path), "fps": float(fps), "frames": len(files), "size": list(size),
                "transparent": transparent, "files": files}
    if not path.is_file():
        raise PipelineError(f"動画か連番のフォルダが見つかりません: {path}")
    info = video.describe(path)
    from .fx import probe
    decoded = probe.decode(path, "v")
    if not decoded["ok"]:
        raise PipelineError(f"動画を復号できません: {decoded['error']}")
    if fps is not None and abs(float(fps) - info["fps"]) > 1e-3:
        raise PipelineError(f"--fps {fps:g} が動画の fps {info['fps']:g} と違います")
    return {"kind": "video", "path": str(path), "fps": info["fps"], "frames": decoded["frames"], "size": [info["width"], info["height"]],
            "transparent": "a" in (video.probe.inspect(path)["video"]["pix_fmt"] or ""), "info": info, "sha256": sha256(path)}


def events_from_timeline(timeline, kinds=KINDS):
    """timeline の出来事 [(秒, 名前)]（行の開始・フラッシュ・切れ目）。時刻順。"""
    items = []
    if "lyrics" in kinds:
        items += [(line["start"], f"行の開始: {line['text']}") for line in timeline.get("lyrics", [])]
    if "flashes" in kinds:
        items += [(t, "フラッシュ") for t in timeline.get("flashes", [])]
    if "seams" in kinds:
        items += [(t, "切れ目") for t in timeline.get("seams", [])]
    return sorted(items, key=lambda item: item[0])


def _load_frames(source, indices):
    """{コマ番号: uint8 RGB か RGBA}。"""
    wanted = sorted({i for i in indices if 0 <= i < source["frames"]})
    if source["kind"] == "frames":
        out = {}
        for index in wanted:
            with Image.open(source["files"][index]) as image:
                out[index] = np.asarray(image.convert("RGBA" if source["transparent"] else "RGB"))
        return out
    return video.grab(source["path"], wanted, source["info"], total=source["frames"])


# ---- 描画 -----------------------------------------------------------------------

def over(rgba, color):
    """straight α の RGBA を単色の背景に置く（sRGB の値のまま）。"""
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    back = np.array(color, np.float32).reshape(1, 1, 3)
    return np.clip(rgba[..., :3].astype(np.float32) * alpha + back * (1.0 - alpha) + 0.5, 0, 255).astype(np.uint8)


def _font(pixels):
    try:
        return ImageFont.load_default(size=pixels)
    except (TypeError, OSError):
        return ImageFont.load_default()


def cell(image, width, label):
    """コマを幅 width に縮め、上に黒帯の字（label）を足した uint8 RGB。"""
    source = Image.fromarray(image)
    height = max(2, int(round(source.height * width / source.width)))
    scaled = source.resize((width, height), Image.Resampling.BOX)
    band = max(14, int(round(height * LABEL_HEIGHT_SHARE)))
    canvas = Image.new("RGB", (width, height + band), (0, 0, 0))
    canvas.paste(scaled, (0, band))
    ImageDraw.Draw(canvas).text((4, 2), label, font=_font(max(10, band - 6)), fill=(255, 255, 255))
    return np.asarray(canvas)


def _grid(cells, columns):
    rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    blank = np.zeros_like(cells[0])
    return np.vstack([np.hstack(row + [blank] * (columns - len(row))) for row in rows])


def build(source_path, out, at=None, events_path=None, fps=None, backgrounds=DEFAULT_BACKGROUNDS, cell_width=320, columns=4, kinds=KINDS):
    """シート（PNG）と sheet.json を書く。要約を返す。"""
    if (at is None or len(at) == 0) == (events_path is None):
        raise PipelineError("--at（秒の並び）か --events（timeline.json）のどちらか一方を指定してください")
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise PipelineError(f"--kinds は {', '.join(KINDS)} から選んでください（不明: {unknown}）")
    bad = [b for b in backgrounds if b not in BACKGROUNDS]
    if bad:
        raise PipelineError(f"--backgrounds は {', '.join(BACKGROUNDS)} から選んでください（不明: {bad}）")
    if cell_width < 32 or columns < 1:
        raise PipelineError("--cell-width は 32 以上、--columns は 1 以上にしてください")
    timeline = None
    if events_path is not None:
        timeline = read_json(events_path)
        fps = fps or timeline.get("fps")
    source = describe_source(source_path, fps)
    fps = source["fps"]
    wanted = [(float(t), "指定") for t in at] if events_path is None else events_from_timeline(timeline, kinds)
    if not wanted:
        raise PipelineError("並べる時刻がありません（timeline に行・フラッシュ・切れ目が無い）")
    entries = []
    for t, name in wanted:
        index = validate.frame_at(t, fps)
        if not 0 <= index < source["frames"]:
            entries.append({"t": t, "frame": index, "event": name, "missing": True})
        else:
            entries.append({"t": t, "frame": index, "event": name, "missing": False})
    frames = _load_frames(source, [e["frame"] for e in entries if not e["missing"]])
    cells, layout = [], []
    for entry in entries:
        if entry["missing"]:
            continue
        image = frames[entry["frame"]]
        label = f"f{entry['frame']} {entry['t']:.3f}s {entry['event']}"
        if source["transparent"] and image.shape[2] == 4:
            for name in backgrounds:
                cells.append(cell(over(image, BACKGROUNDS[name]), cell_width, f"{label} [{name}]"))
                layout.append({**entry, "background": name, "cell": len(cells) - 1})
        else:
            cells.append(cell(image[..., :3], cell_width, label))
            layout.append({**entry, "background": None, "cell": len(cells) - 1})
    if not cells:
        raise PipelineError(f"どの時刻も動画の中にありません（コマ数 {source['frames']}、fps {fps:g}）")
    per_row = len(backgrounds) if source["transparent"] else columns
    sheet = _grid(cells, per_row)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp.png")
    Image.fromarray(sheet).save(tmp, format="PNG")
    tmp.replace(out)
    record = {"schema_version": "sheet/0.1", "sheet": str(out), "size": [sheet.shape[1], sheet.shape[0]], "columns": per_row,
              "cell_width": cell_width, "fps": fps,
              "source": {k: v for k, v in source.items() if k not in ("files", "info")},
              "events": None if events_path is None else {"path": str(events_path), "sha256": sha256(events_path), "kinds": list(kinds)},
              "backgrounds": list(backgrounds) if source["transparent"] else [],
              "cells": layout, "missing": [e for e in entries if e["missing"]], "note": "閲覧用。合否は出さない"}
    atomic_json(out.with_suffix(".json"), record)
    return {"sheet": str(out), "json": str(out.with_suffix(".json")), "cells": len(cells), "rows": -(-len(cells) // per_row),
            "size": record["size"], "missing": len(record["missing"]), "transparent": source["transparent"]}
