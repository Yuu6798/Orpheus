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
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import validate
from .fx.layering import natural_key  # noqa: F401（以前の名前。fx の overlay と同じもの）
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


def _at_rate(source, rate):
    """動画を ffmpeg の fps フィルタで rate（"30" など）の一定の間隔のコマに揃えて読むときの source（fps・frames は揃えた後。rate を足す）。

    元のコマは、始まりの時刻がいちばん近い揃えたコマに置かれ（重なれば後のコマ。空いたコマは前のコマをくり返す）、可変フレームレートの
    動画でも、コマ番号 = 時刻 × fps になる。コマ数は、読むときと同じ読み手で小さく読んで数える（1 回の復号）。連番はそのまま。"""
    if source["kind"] != "video":
        return source
    count = sum(1 for _ in video.frames(source["path"], (2, 2), source["info"], fps=rate))
    return {**source, "fps": float(rate), "frames": count, "rate": rate}


def _load_frames(source, indices):
    """{コマ番号: (uint8 RGB か RGBA, sha256)}。連番は使うコマだけ読む。動画は source に rate があれば、揃えた後の番号（_at_rate）。"""
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
    for index, image in video.grab(source["path"], wanted, source["info"], total=source["frames"], fps=source.get("rate")).items():
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


# ---- 並べる（版どうし） ------------------------------------------------------------

def _source_labels(sources, labels):
    names = list(labels) if labels else [Path(s).stem or Path(s).name for s in sources]
    if len(names) != len(sources):
        raise PipelineError(f"--labels の数（{len(names)}）が並べる動画・連番の数（{len(sources)}）と違います")
    return names


def build_rows(sources, out, at=None, every=None, count=None, fps=None, labels=None, cell_width=None, font=None):
    """複数の動画（か不透明な連番）を、同じ時刻のコマで 1 段ずつ並べたシート（段 = 動画、列 = 時刻）と sheet.json を書く。
    直す前後・参照と完成版と新しい版を見比べるため。時刻は --at と、1 本目の長さで数えた --every・--count。
    動画は、その動画の fps の一定の間隔のコマに揃えてから時刻でコマを選ぶ（_at_rate。可変フレームレートの動画も時刻で合う）。"""
    has_at = at is not None and len(at) > 0
    if not has_at and every is None and count is None:
        raise PipelineError("並べる時刻を --at・--every・--count のどれかで指定してください（--events は 1 本のときだけ）")
    if every is not None and not (math.isfinite(every) and every > 0):
        raise PipelineError(f"--every は正の秒数にしてください: {every}")
    if count is not None and (isinstance(count, bool) or not isinstance(count, int) or count < 1):
        raise PipelineError(f"--count は 1 以上の整数にしてください: {count}")
    if cell_width is not None and cell_width < 32:
        raise PipelineError("--cell-width は 32 以上にしてください")
    out = Path(out)
    kind = image_format(out)
    names = _source_labels(sources, labels)
    described = [describe_source(path, fps) for path in sources]
    clear = [str(s["path"]) for s in described if s["transparent"]]
    if clear:
        raise PipelineError(f"段で並べるのは不透明な動画・連番だけです（透過: {clear}。先に背景へ重ねてください）")
    timed = [_at_rate(s, f"{s['fps']:g}") for s in described]
    first = timed[0]
    times = [float(t) for t in at] if has_at else []
    if every is not None or count is not None:
        times += [t for t, _, _, _ in spaced_times(first["fps"], first["frames"], every, count)]
        times = sorted(set(times))
    font_path = find_font(font)
    width = DEFAULT_CELL_WIDTH if cell_width is None else cell_width
    rows, layout, missing = [], [], []
    for row, (source, name) in enumerate(zip(timed, names)):
        indices = [_frame_in(source, t) for t in times]
        frames = _load_frames(source, [i for i in indices if 0 <= i < source["frames"]])
        cells = []
        for t, index in zip(times, indices):
            if index in frames:
                image, digest = frames[index]
                cells.append(cell(image[..., :3], width, f"{name} f{index} {t:.3f}s", font_path))
                layout.append({"row": row, "label": name, "t": t, "frame": index, "sha256": digest})
            else:
                missing.append({"row": row, "label": name, "t": t, "frame": index})
                cells.append(None)
        rows.append(cells)
    shown = [c for r in rows for c in r if c is not None]
    if not shown:
        raise PipelineError("どの時刻も、どの動画の中にもありません")
    shape = (max(c.shape[0] for c in shown), width, 3)
    grid = [np.zeros(shape, np.uint8) if c is None else _fit(c, shape) for r in rows for c in r]
    sheet = _grid(grid, len(times))
    atomic_image(out, sheet)
    record = {"schema_version": "sheet-rows/0.1", "sheet": str(out), "format": kind.lower(), "size": [sheet.shape[1], sheet.shape[0]],
              "columns": len(times),
              "rows": [{"label": n, "source": {k: v for k, v in s.items() if k not in ("files", "info")},
                        "read": {"rate": t["rate"], "frames": t["frames"]} if "rate" in t else None}
                       for n, s, t in zip(names, described, timed)],
              "times": times, "label_font": font_path, "cells": layout, "missing": missing,
              "note": "閲覧用。合否は出さない。段は動画（並べた順）、列は時刻。どの段も同じ時刻のいちばん近いコマ"
                      "（動画は ffmpeg の fps フィルタでその動画の fps の一定の間隔のコマに揃えてから選ぶ。コマ番号は揃えた後の番号）"}
    atomic_json(out.with_suffix(".json"), record)
    return {"sheet": str(out), "json": str(out.with_suffix(".json")), "format": record["format"], "rows": len(described),
            "columns": len(times), "size": record["size"], "missing": len(missing)}


def _frame_in(source, t):
    """時刻 t のいちばん近いコマ。動画の中の時刻（最後の半コマも）は、最後のコマを越えないようにする（--every・--count の時刻と同じ）。"""
    index = validate.frame_at(t, source["fps"])
    if index >= source["frames"] and 0 <= t < source["frames"] / source["fps"]:
        index = source["frames"] - 1
    return index


def _fit(image, shape):
    """セルを shape（高さ, 幅, 3）にそろえる（縦横比の違う動画の段。上に寄せ、足りない所は黒）。"""
    if image.shape == tuple(shape):
        return image
    out = np.zeros(shape, np.uint8)
    h, w = min(shape[0], image.shape[0]), min(shape[1], image.shape[1])
    out[:h, :w] = image[:h, :w]
    return out


def side_by_side(sources, out, height=None, audio=1, labels=None, crf=20, fps=None, font=None):
    """複数の動画を左右に並べた 1 本の動画（BT.709。encode.write_video）と、隣の JSON を書く。

    どれも高さ height（省くといちばん低い動画の高さ。偶数）に縦横比を保ってそろえ、ffmpeg の fps フィルタで出力の fps（省くと 1 本目）の
    一定の間隔のコマに揃えて（_at_rate。可変フレームレートの動画も時刻で合う）、出力のコマ i にそれぞれの i 番目のコマを置く。
    長さはいちばん短い動画（揃えた後のコマ数）。音は audio 本目（1 から。0 で無し）の動画の音。labels があれば、それぞれの左上に名前の帯を書く。"""
    from . import encode
    from .fx import probe

    out = Path(out)
    if out.suffix.lower() != ".mp4":
        raise PipelineError(f"並べた動画の --out は .mp4 にしてください: {out}")
    if len(sources) < 2:
        raise PipelineError("左右に並べるには、動画を 2 本以上渡してください（--with）")
    names = _source_labels(sources, labels) if labels else None
    described = [describe_source(path) for path in sources]
    if any(s["kind"] != "video" for s in described):
        raise PipelineError("左右に並べるのは動画だけです（連番は先に動画にしてください）")
    fps = float(fps or described[0]["fps"])
    if not (math.isfinite(fps) and fps > 0):
        raise PipelineError(f"--fps は正の数にしてください: {fps:g}")
    rate = f"{fps:g}"                 # 書き出し（encode.write_video の -framerate）と同じ表記で、どの動画も揃える
    fps = float(rate)
    target = int(height or min(s["size"][1] for s in described))
    target -= target % 2
    if target < 2:
        raise PipelineError(f"--height は 2 以上にしてください: {height}")
    sizes = [(max(2, int(round(s["size"][0] * target / s["size"][1] / 2.0)) * 2), target) for s in described]
    if isinstance(audio, bool) or not isinstance(audio, int) or not 0 <= audio <= len(sources):
        raise PipelineError(f"--audio は 0（音なし）か 1〜{len(sources)} の番号にしてください: {audio}")
    if audio and probe.inspect(described[audio - 1]["path"]).get("audio") is None:
        raise PipelineError(f"{audio} 本目の動画に音がありません（--audio 0 で音なしにできます）: {described[audio - 1]['path']}")
    timed = [_at_rate(s, rate) for s in described]
    n_frames = min(t["frames"] for t in timed)
    if n_frames < 1:
        raise PipelineError("並べる動画のコマがありません")
    audio_plan = None
    if audio:
        chosen = described[audio - 1]
        # 選んだ動画の音をそのまま使う（write_video の既定の明けと暗転は掛けない）
        audio_plan = {"path": chosen["path"], "start": 0.0, "duration": n_frames / fps, "fade_in": 0.0, "fade_out": 0.0}
        sound = probe.decode(chosen["path"], "a")                          # 音のストリームの長さ（入れ物の長さは絵の長さのことがある）
        if sound["ok"] and 0 < sound["seconds"] < n_frames / fps:        # 音が短ければ、後ろを無音で足して動画の長さにそろえる
            audio_plan.update(length=sound["seconds"], pad=True)
    font_path = find_font(font) if names else None
    bands = [cell(np.zeros((2, w, 3), np.uint8), w, name, font_path)[:-2] for (w, _), name in zip(sizes, names)] if names else None
    total_width = sum(w for w, _ in sizes)

    def frames():
        readers = [video.frames(t["path"], size, t["info"], fps=rate) for t, size in zip(timed, sizes)]
        try:
            for i in range(n_frames):
                row = []
                for k, (t, reader) in enumerate(zip(timed, readers)):
                    try:
                        _, image = next(reader)
                    except StopIteration:
                        raise PipelineError(f"{t['path']} のコマが、数えたコマ数（{t['frames']}）より先に尽きました（コマ {i}）") from None
                    panel = image
                    if bands is not None:
                        panel = panel.copy()
                        band = bands[k][:panel.shape[0]]
                        panel[:band.shape[0]] = band
                    row.append(panel)
                yield np.ascontiguousarray(np.hstack(row))
        finally:
            for reader in readers:
                reader.close()

    result = encode.write_video(out, frames(), (total_width, target), fps, crf, n_frames, audio_plan)
    record = {"schema_version": "side-by-side/0.1", "video": str(out), "sha256": sha256(out), "size": [total_width, target], "fps": fps,
              "frames": n_frames, "seconds": n_frames / fps, "crf": crf,
              "audio": None if audio_plan is None else {"from": audio, "path": str(audio_plan["path"])},
              "panels": [{"label": None if names is None else names[k], "source": s["path"], "fps": s["fps"], "frames": s["frames"],
                          "read_frames": timed[k]["frames"], "size": list(sizes[k])} for k, s in enumerate(described)],
              "note": "閲覧用。合否は出さない。どの動画も ffmpeg の fps フィルタで出力の fps の一定の間隔のコマに揃え、コマ i にはそれぞれの "
                      "i 番目のコマ（可変フレームレートの動画も時刻で合う）。長さはいちばん短い動画（揃えた後のコマ数 read_frames）"}
    atomic_json(out.with_suffix(".json"), record)
    return {"video": str(out), "json": str(out.with_suffix(".json")), "size": record["size"], "frames": n_frames, "fps": fps,
            "seconds": round(n_frames / fps, 3), "audio": audio if audio_plan else 0, "write_seconds": result["seconds"]}
