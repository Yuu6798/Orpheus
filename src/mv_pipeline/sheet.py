"""見るためのシート（`mv sheet`）。動画か透過の連番から、指定した時刻・等間隔の時刻・timeline の出来事のコマを 1 枚に並べる。

    mv sheet 動画か連番のフォルダ --out sheet.png|.jpg [--at 秒 … | --events timeline.json [--kinds lyrics,flashes,seams]] [--every 秒] [--count N]
             [--crop X0 Y0 X1 Y1 [--units norm|px]] [--fps 30] [--backgrounds white,black,gray] [--cell-width 320] [--columns 4] [--font PATH]

- 時刻は --at か --events（どちらか一方）に、--every（0 秒から S 秒おき。長さ未満）と --count（全体を N 等分した各区間の中央）を足せる
  （--every・--count だけでもよい）。--every・--count を使うと全部を時刻順に並べ、等間隔・等分の時刻は、同じコマがすでにあれば足さない。
- --crop は各コマをその範囲で切り出し、縮めずに原寸でセルにする（--cell-width を明示したときだけその幅に合わせる）。範囲の切り方は `mv fx guides` と同じ。
  --crop のセルは絵に字を掛けない（--crop なしのセルは今までの絵のまま）。
- --out の拡張子で形式が決まる（.png か、.jpg・.jpeg の品質 90）。ほかの拡張子は止める。
- コマごとに「コマ番号・秒・出来事の名前」を書き込む。透過の連番なら、白・黒・灰（--backgrounds）の上に置いた版を横に並べる
  （縁と読みやすさの確認用）。動画は不透明として扱い（アルファつきの動画は先に `mv overlay` などで平らにする）、時刻を --columns 列に折り返す。
- 時刻 → コマ番号は validate.frame_at（いちばん近いコマ）。timeline の時刻はショットの時刻なので、一部の区間だけ描いた run では
  interval.start を引いてから動画のコマにする。連番の fps は --fps（--events の timeline があればその fps）。
  動画の fps は動画から読み、--fps や timeline の fps と 1% 以上違えば止める。
- 字は --font（TTF / TTC）か、よくある場所の日本語の書体を探して使う。見つからなければ Pillow の既定の書体で、日本語を含む出来事の名前は
  ASCII の略記（line 1 / flash / seam / given / every / count）にして画像に書き、正式な名前は隣の JSON に残す（記録の `label_font` にどちらかが分かる）。
- 検査ではなく閲覧の道具。合否は出さない。隣に sheet.json（セルごとのコマ番号・秒・出来事・背景・切り出した範囲・そのコマの sha256、出所）を書くので、
  AI が動画を見ずに「どのコマに何が写っているか」を読める入口になる。

cv2 は使わない（Pillow・numpy・ffmpeg）。
"""
import hashlib
import math
import re
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import validate
from .inspection import video
from .io import PipelineError, atomic_image, atomic_json, image_format, read_json, sha256

FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")
BACKGROUNDS = {"white": (255, 255, 255), "black": (0, 0, 0), "gray": (128, 128, 128)}
DEFAULT_BACKGROUNDS = ("white", "black", "gray")
KINDS = ("lyrics", "flashes", "seams")
LABEL_HEIGHT_SHARE = 0.11
DEFAULT_CELL_WIDTH = 320
CROP_UNITS = ("norm", "px")
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
    """アルファのチャンネルがあるか、PNG の tRNS（パレット・RGB・グレーの透明色）があるか。"""
    return "A" in image.getbands() or "transparency" in image.info


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


def _spread(time_at, step, count, fps, frames, before=math.inf):
    """time_at(k)（k = 0 … count-1。間隔は step 秒）のうち before 秒より前の時刻を [(秒, コマ番号)] で返す。

    動画の中の時刻なので、いちばん近いコマが最後のコマの次になる時刻（最後の半コマ）は最後のコマにする。同じコマになる時刻は最初の 1 つだけ。
    間隔が 1 コマより短いときは次のコマに届く k まで飛ぶので、回る回数はコマ数の程度で済む。"""
    picked, k = [], 0
    while k < count and frames > 0:
        t = time_at(k)
        if not t < before:
            break
        frame = min(validate.frame_at(t, fps), frames - 1)
        if not picked or frame > picked[-1][1]:
            picked.append((t, frame))
        if frame >= frames - 1:
            break
        k = max(k + 1, math.floor(((frame + 0.5) / fps - time_at(0)) / step))     # ここより前の k は、まだ同じコマ
    return picked


def spaced_times(fps, frames, every=None, count=None):
    """--every・--count の時刻 [(秒, コマ番号, 名前, ASCII の略記)]。秒は動画（連番）の先頭を 0 とする。

    every: 0 秒から every 秒おき（長さ frames / fps 未満）。count: 全体を count 等分した各区間の中央。"""
    duration = frames / fps
    out = []
    if every is not None:
        out += [(t, frame, "等間隔", "every") for t, frame in _spread(lambda k: k * every, every, math.inf, fps, frames, duration - 1e-9)]
    if count is not None:
        out += [(t, frame, "等分", "count") for t, frame in _spread(lambda k: (k + 0.5) * duration / count, duration / count, count, fps, frames)]
    return out


def crop_box(crop, units, size):
    """--crop [x0, y0, x1, y1]（units "norm": 0..1、"px": 画素）→ 画素の [x0, y0, x1, y1]。size はコマの (幅, 高さ)。

    範囲の切り方は `mv fx guides` の --crop と同じ式（norm は幅・高さを掛けて切り捨て。px は渡した値をそのまま使い、小数は切り捨て）。
    範囲が逆・コマの外・1 画素より小さい範囲は PipelineError。"""
    if units not in CROP_UNITS:
        raise PipelineError(f"--units は {' か '.join(CROP_UNITS)} にしてください: {units!r}")
    width, height = size
    scale_x, scale_y = (1.0, 1.0) if units == "norm" else (float(width), float(height))
    values = [float(v) for v in crop]
    if len(values) != 4:
        raise PipelineError(f"--crop は [x0 y0 x1 y1] の 4 つの数にしてください: {list(crop)}")
    x0, y0, x1, y1 = values
    if not (0 <= x0 < x1 <= scale_x and 0 <= y0 < y1 <= scale_y):
        raise PipelineError(f"--crop はコマの中の [x0 y0 x1 y1]（x0 < x1、y0 < y1）にしてください: {values}"
                            f"（{'正規化座標 0..1' if units == 'norm' else f'画素 0..{width}x{height}'}）")
    # px を幅で割って掛け直すと、123 が 122.99999999999999 になって 1 画素ずれる（1920 幅など）。px は割らない
    box = [int(x0 * width), int(y0 * height), int(x1 * width), int(y1 * height)] if units == "norm" else [int(v) for v in values]
    if box[2] - box[0] < 1 or box[3] - box[1] < 1:
        raise PipelineError(f"--crop の範囲が 1 画素より小さくなります: {values}（コマは {width}x{height}）")
    return box


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
                if _has_alpha(image) != source["transparent"]:   # 透過の有無が途中で変わる連番は、黙ってどちらかに寄せない
                    raise PipelineError(f"連番の透過の有無がそろっていません: {source['files'][index].name} は {image.mode}"
                                        f"（最初のコマは{'透過あり' if source['transparent'] else '透過なし'}）")
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


def cell(image, width, label, font_path=None, keep_picture=False):
    """コマを幅 width に縮め（width が None なら縮めず原寸のまま）、上に黒帯の字（label）を足した uint8 RGB。

    字の下の端は帯から数画素はみ出して、絵のいちばん上の行に掛かることがある（今までの絵のまま）。keep_picture なら字を帯の中に寄せて
    先に書き、絵を後から置くので、絵の画素は 1 つも変わらない（--crop のセル用）。"""
    scaled = Image.fromarray(image)
    if width is not None:
        scaled = scaled.resize((width, max(2, int(round(scaled.height * width / scaled.width)))), Image.Resampling.BOX)
    width, height = scaled.size
    band = max(14, int(round(height * LABEL_HEIGHT_SHARE)))
    canvas = Image.new("RGB", (width, height + band), (0, 0, 0))
    font, draw = _font(max(10, band - 6), font_path), ImageDraw.Draw(canvas)
    if not keep_picture:
        canvas.paste(scaled, (0, band))
        draw.text((4, 2), label, font=font, fill=(255, 255, 255))
    else:
        if isinstance(font, ImageFont.FreeTypeFont):
            draw.text((4, band), label, font=font, fill=(255, 255, 255), anchor="ld")     # 字のいちばん下（ディセンダー）を帯の下の端に合わせる
        else:
            draw.text((4, 2), label, font=font, fill=(255, 255, 255))
        canvas.paste(scaled, (0, band))
    return np.asarray(canvas)


def _grid(cells, columns):
    rows = [cells[i:i + columns] for i in range(0, len(cells), columns)]
    blank = np.zeros_like(cells[0])
    return np.vstack([np.hstack(row + [blank] * (columns - len(row))) for row in rows])


def build(source_path, out, at=None, events_path=None, fps=None, backgrounds=DEFAULT_BACKGROUNDS, cell_width=None, columns=4, kinds=KINDS, font=None,
          every=None, count=None, crop=None, units="norm"):
    """シート（--out の拡張子で PNG か JPEG）と sheet.json を書く。要約を返す。

    cell_width を省くと 320（crop があれば、切り出した範囲の原寸）。"""
    has_at = at is not None and len(at) > 0
    if has_at and events_path is not None:
        raise PipelineError("--at（秒の並び）と --events（timeline.json）はどちらか一方にしてください（--every・--count はどちらとも併用できます）")
    if not has_at and events_path is None and every is None and count is None:
        raise PipelineError("並べる時刻を --at（秒の並び）・--events（timeline.json）・--every（S 秒おき）・--count（N 等分）のどれかで指定してください")
    if every is not None and not (math.isfinite(every) and every > 0):
        raise PipelineError(f"--every は正の秒数にしてください: {every}")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise PipelineError(f"--count は 1 以上の整数にしてください: {count}")
    out = Path(out)
    kind = image_format(out)                          # 知らない拡張子は、動画を読む前に止める
    unknown = [k for k in kinds if k not in KINDS]
    if unknown:
        raise PipelineError(f"--kinds は {', '.join(KINDS)} から選んでください（不明: {unknown}）")
    bad = [b for b in backgrounds if b not in BACKGROUNDS]
    if bad or not backgrounds:
        raise PipelineError(f"--backgrounds は {', '.join(BACKGROUNDS)} から 1 つ以上選んでください（不明: {bad}）")
    if (cell_width is not None and cell_width < 32) or columns < 1:
        raise PipelineError("--cell-width は 32 以上、--columns は 1 以上にしてください")
    timeline, fps_origin = None, "--fps"
    if events_path is not None:
        timeline = read_json(events_path)
        if fps is None and timeline.get("fps"):
            fps, fps_origin = timeline["fps"], "timeline"
    source = describe_source(source_path, fps, fps_origin)
    fps = source["fps"]
    box = None if crop is None else crop_box(crop, units, source["size"])
    width = DEFAULT_CELL_WIDTH if cell_width is None and box is None else cell_width      # None: 切り出した範囲を縮めず原寸で並べる
    font_path = find_font(font)
    wanted = []
    if has_at:
        wanted = [(float(t), "指定", "given") for t in at]
    elif events_path is not None:
        wanted = events_from_timeline(timeline, kinds)
    # timeline の時刻はショットの時刻。一部の区間だけ描いた run の動画は interval.start から始まるので、その分を引いてコマにする
    offset = float((timeline or {}).get("interval", {}).get("start", 0.0)) if events_path is not None else 0.0
    entries = [{"t": t, "frame": validate.frame_at(t - offset, fps), "event": name, "event_ascii": short} for t, name, short in wanted]
    for entry in entries:
        entry["missing"] = not 0 <= entry["frame"] < source["frames"]
    if every is not None or count is not None:
        if not (math.isfinite(fps) and fps > 0):
            raise PipelineError(f"--every・--count には正の fps が要ります: {fps:g}")
        try:
            spaced = spaced_times(fps, source["frames"], every, count)
        except OverflowError:                 # 浮動小数で数えられないほど小さい間隔・多い等分
            raise PipelineError(f"--every・--count の値が桁外れで、時刻を数えられません（--every {every}、--count {count}）") from None
        taken = {entry["frame"] for entry in entries}
        for t, frame, name, short in spaced:
            if frame not in taken:            # 同じコマがすでにあれば足さない（--at・--events のコマ、--every、--count の順に残る）
                taken.add(frame)
                entries.append({"t": offset + t, "frame": frame, "event": name, "event_ascii": short, "missing": False})
        entries.sort(key=lambda entry: entry["t"])       # 併用したら時刻順（--events があれば、等間隔・等分の時刻も timeline の時刻で書く）
    if not entries:
        raise PipelineError("並べる時刻がありません" + ("（timeline に行・フラッシュ・切れ目が無い）" if events_path is not None else ""))
    frames = _load_frames(source, [e["frame"] for e in entries if not e["missing"]])
    cells, layout = [], []
    for entry in entries:
        if entry["missing"]:
            continue
        image, digest = frames[entry["frame"]]
        if box is not None:
            image = image[box[1]:box[3], box[0]:box[2]]
        shown = entry["event"] if font_path else entry["event_ascii"]
        label = f"f{entry['frame']} {entry['t']:.3f}s {shown}"
        record = {**{k: entry[k] for k in ("t", "frame", "event", "event_ascii")}, "crop": None if box is None else list(box)}
        if source["transparent"]:
            for name in backgrounds:
                cells.append(cell(over(image, BACKGROUNDS[name]), width, f"{label} [{name}]", font_path, keep_picture=box is not None))
                layout.append({**record, "background": name, "cell": len(cells) - 1, "sha256": digest})
        else:
            cells.append(cell(image[..., :3], width, label, font_path, keep_picture=box is not None))
            layout.append({**record, "background": None, "cell": len(cells) - 1, "sha256": digest})
    if not cells:
        raise PipelineError(f"どの時刻も動画の中にありません（コマ数 {source['frames']}、fps {fps:g}）")
    per_row = len(backgrounds) if source["transparent"] else columns
    sheet = _grid(cells, per_row)
    atomic_image(out, sheet)
    record = {"schema_version": "sheet/0.1", "sheet": str(out), "format": kind.lower(), "size": [sheet.shape[1], sheet.shape[0]], "columns": per_row,
              "cell_width": cells[0].shape[1], "fps": fps, "fps_origin": fps_origin if source["kind"] == "frames" else "video",
              "label_font": font_path, "labels": "full" if font_path else "ascii",
              "source": {k: v for k, v in source.items() if k not in ("files", "info")},
              "events": None if events_path is None else {"path": str(events_path), "sha256": sha256(events_path), "kinds": list(kinds),
                                                          "interval_start": offset},
              "every": every, "count": count,
              "crop": None if box is None else {"units": units, "box": [float(v) for v in crop], "pixels": list(box), "native": width is None},
              "backgrounds": list(backgrounds) if source["transparent"] else [],
              "cells": layout, "missing": [{k: e[k] for k in ("t", "frame", "event")} for e in entries if e["missing"]],
              "note": "閲覧用。合否は出さない。cells[*].sha256 はそのコマの画素（連番はファイル）の sha256（crop の前のコマ全体）。cells[*].crop は切り出した画素の範囲"}
    atomic_json(out.with_suffix(".json"), record)
    return {"sheet": str(out), "json": str(out.with_suffix(".json")), "format": record["format"], "cells": len(cells), "rows": -(-len(cells) // per_row),
            "size": record["size"], "missing": len(record["missing"]), "transparent": source["transparent"], "labels": record["labels"]}
