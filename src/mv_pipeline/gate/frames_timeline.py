"""透過の連番から timeline を起こす（`mv gate timeline`）。字が実際に見えているコマと範囲（箱）を α から実測し、dance-timeline/0.1 を書く。

    mv gate timeline --frames 連番のフォルダ[:行の id] [--frames …] --lyrics props.json --layer band
                     [--hints hints.json] [--alpha-threshold 64] [--flashes 秒,秒] --out timeline.json

- `--lyrics` は `mv export remotion-props` の props.json（remotion-props/0.1。行ごとの id・text・start_frame・end_frame、fps・寸法・
  duration_frames、拍のコマ）。連番のコマ数は duration_frames と同じでなければならない（途中だけ描いた連番は測れない）。
- 行の切り分けは 2 通り:
    (a) 行ごとに別の連番: `--frames DIR:lyric-001` のように連番のフォルダに props の行の id を付ける（その連番の不透明な画素は全部その行）。
    (b) 1 つの連番に全行: `--hints hints.json`（{行の id: 正規化した箱 [x0, y0, x1, y1]}）で、箱の中の不透明な画素をその行と見なす。
        props の行が 1 つだけなら hints は要らない。
- 「見えている」は α > alpha_threshold（既定 64）の画素があること。行の最初と最後に見えるコマ（end は最後のコマ + 1）と、
  見えている全コマの不透明な画素を覆う箱（正規化）を実測する。アルファの無い連番はエラー。連番の並びは数字の大小（frame2 < frame10）。
- 宣言（props の start_frame / end_frame）と実測の差を、行ごとに `<out の stem>_measured.json` と要約に出す（例: 開始が 1 コマ早い）。
  どちらが正しいかは決めない。見えているコマが無い行は timeline に宣言の時刻のまま残し（measured: false、箱は hints の箱か [0,0,0,0]）、
  要約の unseen_lines に出す（timeline の行数を props と揃え、gate check の行の対応を崩さない）。
- 拍・小節頭は props の beats からそのまま秒に直して写す（コマ / fps。丸めない）。フラッシュは props に無いので `--flashes`（秒）で与える。
- 書く timeline は実測なので、fade_in_s / fade_out_s は 0、lyric_fade は inside（見えている最後のコマまでが end）。
  `exclusive` は true（同じ層で 2 行が同時に見えれば `mv gate check` の字の重なりで分かる）。

連番の各コマの sha256（読んだバイト列から 1 回で計算）と props・使った hints の sha256 は timeline の `measured.sources` に残す。推定はしない。
"""
import hashlib
import io
import re
from pathlib import Path

import numpy as np
from PIL import Image

from ..io import PipelineError, atomic_json, read_json, sha256

FRAME_SUFFIXES = (".png", ".webp", ".tif", ".tiff")
DEFAULT_ALPHA_THRESHOLD = 64
PROPS_SCHEMA = "remotion-props/0.1"


def natural_key(path):
    """frame2 < frame10 になる並び（数字の桁をそろえていない連番でも順序を間違えない）。"""
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", Path(path).name)]


def _frame_files(folder):
    folder = Path(folder)
    if not folder.is_dir():
        raise PipelineError(f"--frames のフォルダが見つかりません: {folder}")
    files = sorted((p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in FRAME_SUFFIXES), key=natural_key)
    if not files:
        raise PipelineError(f"--frames のフォルダに連番（{'/'.join(FRAME_SUFFIXES)}）がありません: {folder}")
    return files


def parse_frames_arg(value, line_ids):
    """'DIR' か 'DIR:行の id' を (Path, 行の id | None) にする。id は props にある行の id だけ（Windows のドライブ文字や ':' を含む名前と取り違えない）。"""
    text = str(value)
    head, sep, tail = text.rpartition(":")
    if sep and head and tail in line_ids:
        return Path(head), tail
    return Path(text), None


def load_props(path):
    props = read_json(path)
    if not isinstance(props, dict) or props.get("schema_version") != PROPS_SCHEMA:
        raise PipelineError(f"--lyrics は {PROPS_SCHEMA} の props.json にしてください（mv export remotion-props の出力）: {path}")
    for key in ("fps", "width", "height", "duration_frames", "lines"):
        if key not in props:
            raise PipelineError(f"props.json に {key} がありません: {path}")
    return props


def _has_alpha(image):
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def measure_sequence(files, size, threshold, regions):
    """連番を 1 回読み、(領域ごとの {"frames": 見えているコマの番号の list, "box_px": [x0, y0, x1, y1] | None}, 各コマの sha256) を返す。

    regions は {名前: 画素の箱 [x0, y0, x1, y1] | None}（None は全面）。箱の端は含まない（x1, y1 は外側）。"""
    width, height = size
    found = {name: {"frames": [], "x0": width, "y0": height, "x1": 0, "y1": 0} for name in regions}
    digests = []
    for index, path in enumerate(files):
        data = path.read_bytes()
        digests.append(hashlib.sha256(data).hexdigest())
        with Image.open(io.BytesIO(data)) as image:
            if image.size != (width, height):
                raise PipelineError(f"連番の寸法が props と違います: {path.name} は {image.size[0]}x{image.size[1]}（期待 {width}x{height}）")
            if not _has_alpha(image):
                raise PipelineError(f"連番にアルファがありません（{image.mode}）: {path.name}。透過の PNG にしてください")
            alpha = np.asarray(image.convert("RGBA"))[..., 3]
        solid = alpha > threshold
        for name, box in regions.items():
            x0, y0, x1, y1 = (0, 0, width, height) if box is None else box
            part = solid[y0:y1, x0:x1]
            if not part.any():
                continue
            rows, cols = np.nonzero(part.any(axis=1))[0], np.nonzero(part.any(axis=0))[0]
            item = found[name]
            item["frames"].append(index)
            item["x0"], item["y0"] = min(item["x0"], x0 + int(cols[0])), min(item["y0"], y0 + int(rows[0]))
            item["x1"], item["y1"] = max(item["x1"], x0 + int(cols[-1]) + 1), max(item["y1"], y0 + int(rows[-1]) + 1)
    measured = {name: {"frames": item["frames"], "box_px": None if not item["frames"] else [item["x0"], item["y0"], item["x1"], item["y1"]]}
                for name, item in found.items()}
    return measured, digests


def _pixel_box(box, size):
    if not (isinstance(box, list) and len(box) == 4 and all(isinstance(v, (int, float)) for v in box)):
        raise PipelineError(f"hints の箱は正規化した [x0, y0, x1, y1] にしてください: {box!r}")
    x0, y0, x1, y1 = box
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise PipelineError(f"hints の箱は 0〜1 で x0 < x1、y0 < y1 にしてください: {box!r}")
    width, height = size
    return [int(round(x0 * width)), int(round(y0 * height)), int(round(x1 * width)), int(round(y1 * height))]


def _norm(box, size):
    width, height = size
    return [round(box[0] / width, 5), round(box[1] / height, 5), round(box[2] / width, 5), round(box[3] / height, 5)]


def build(frames_args, props_path, layer, hints_path=None, threshold=DEFAULT_ALPHA_THRESHOLD, flashes=()):
    """(timeline, measured)。frames_args は 'DIR' / 'DIR:行の id' の list。flashes は秒の list。"""
    props = load_props(props_path)
    fps, size, total = float(props["fps"]), (int(props["width"]), int(props["height"])), int(props["duration_frames"])
    lines = {line["id"]: line for line in props["lines"]}
    if not lines:
        raise PipelineError("props.json に時刻のある行がありません")
    if not frames_args:
        raise PipelineError("--frames を 1 つ以上指定してください")
    if not 0 <= int(threshold) <= 254:
        raise PipelineError(f"--alpha-threshold は 0〜254 にしてください: {threshold}")
    flashes = [float(t) for t in flashes]
    if any(not 0 <= t < total / fps for t in flashes):
        raise PipelineError(f"--flashes の時刻が 0〜{total / fps:g} 秒の外です: {flashes}")
    sequences = [parse_frames_arg(value, lines) for value in frames_args]
    hints = None if hints_path is None else read_json(hints_path)
    if hints is not None and all(line_id is not None for _, line_id in sequences):
        raise PipelineError("--hints は、行の id を付けていない連番にだけ使います（全部の連番に id が付いているので使われません）")
    bound = [line_id for _, line_id in sequences if line_id is not None]
    if len(bound) != len(set(bound)):
        raise PipelineError(f"同じ行の id を 2 つの連番に付けています: {sorted({b for b in bound if bound.count(b) > 1})}")
    hint_boxes = {}
    sources, measured_lines, hints_used = [], {}, False
    for folder, line_id in sequences:
        if line_id is not None:
            regions = {line_id: None}
        elif len(lines) == 1:
            regions = {next(iter(lines)): None}
        else:
            if not isinstance(hints, dict):
                raise PipelineError("1 つの連番に複数の行があるときは --hints（{行の id: 正規化した箱}）が要ります（または --frames DIR:行の id で行ごとに分ける）")
            missing = [line_id for line_id in lines if line_id not in hints]
            if missing:
                raise PipelineError(f"hints に無い行があります: {', '.join(missing)}")
            hint_boxes = {line_id: _pixel_box(hints[line_id], size) for line_id in lines}
            regions = dict(hint_boxes)
            hints_used = True
        files = _frame_files(folder)
        if len(files) != total:
            raise PipelineError(f"連番のコマ数 {len(files)} が props の duration_frames {total} と違います: {folder}（途中だけ描いた連番は測れません）")
        measured, digests = measure_sequence(files, size, int(threshold), regions)
        sources.append({"frames": str(folder), "line": line_id, "count": len(files), "sha256": digests})
        for name, item in measured.items():
            if name in measured_lines:
                raise PipelineError(f"行 {name} が 2 つの連番に割り当てられています")
            measured_lines[name] = item
    rows, timeline_lines, unseen = [], [], []
    for line_id, line in lines.items():
        item = measured_lines.get(line_id)
        declared = [int(line["start_frame"]), int(line["end_frame"])]
        if item is None or not item["frames"]:
            unseen.append(line_id)
            box = hint_boxes.get(line_id)
            rows.append({"id": line_id, "text": line["text"], "declared": declared, "measured": None, "box": None if box is None else _norm(box, size),
                         "diff_frames": None, "note": "見えているコマがありません（連番に無いか、α がしきい値以下）"})
            timeline_lines.append({"layer": layer, "text": line["text"], "start": declared[0] / fps, "end": declared[1] / fps,
                                   "fade_in_s": 0.0, "fade_out_s": 0.0, "box": [0.0, 0.0, 0.0, 0.0] if box is None else _norm(box, size),
                                   "box_px": box, "overflow": False, "sung": bool(line.get("sung", True)), "exclusive": True,
                                   "lyric_id": line_id, "measured": False})
            continue
        first, last = item["frames"][0], item["frames"][-1] + 1
        box = item["box_px"]
        gaps = [b - a - 1 for a, b in zip(item["frames"], item["frames"][1:]) if b - a > 1]
        diff = {"start": first - declared[0], "end": last - declared[1]}
        rows.append({"id": line_id, "text": line["text"], "declared": declared, "measured": [first, last], "box": _norm(box, size), "box_px": box,
                     "visible_frames": len(item["frames"]), "gap_frames": sum(gaps), "diff_frames": diff, "note": _note(diff, gaps)})
        timeline_lines.append({"layer": layer, "text": line["text"], "start": first / fps, "end": last / fps,
                               "fade_in_s": 0.0, "fade_out_s": 0.0, "box": _norm(box, size), "box_px": box, "overflow": False,
                               "sung": bool(line.get("sung", True)), "exclusive": True, "lyric_id": line_id, "measured": True})
    beats = props.get("beats") or {}
    timeline = {
        "schema_version": "dance-timeline/0.1", "size": list(size), "fps": fps, "duration_s": total / fps,
        "interval": {"start": 0.0, "duration": total / fps, "frames": total},
        "lyric_fade": "inside",
        "lyrics": timeline_lines, "seams": [], "flashes": flashes, "ui_regions": [],
        "beats": {"beats": [int(f) / fps for f in beats.get("frames", [])], "downbeats": [int(f) / fps for f in beats.get("downbeat_frames", [])]},
        "measured": {"tool": "mv gate timeline", "alpha_threshold": int(threshold), "layer": layer, "unseen_lines": unseen,
                     "sources": {"props": {"path": str(props_path), "sha256": sha256(props_path)}, "frames": sources,
                                 "hints": None if not hints_used else {"path": str(hints_path), "sha256": sha256(hints_path)}}},
    }
    measured = {"schema_version": "gate-timeline-measured/0.1", "fps": fps, "frames": total, "alpha_threshold": int(threshold),
                "lines": rows, "unseen_lines": unseen, "flashes": flashes, "props": str(props_path)}
    return timeline, measured


def _differs(row):
    return row["diff_frames"] is None or any(row["diff_frames"].values()) or bool(row.get("gap_frames"))


def _note(diff, gaps):
    parts = []
    for key, label in (("start", "開始"), ("end", "終了")):
        value = diff[key]
        if value:
            parts.append(f"{label}が {abs(value)} コマ{'遅い' if value > 0 else '早い'}")
    if gaps:
        parts.append(f"途中で見えないコマ {sum(gaps)}")
    return "、".join(parts) or "宣言と一致"


def write(frames_args, props_path, layer, out, hints_path=None, threshold=DEFAULT_ALPHA_THRESHOLD, flashes=()):
    timeline, measured = build(frames_args, props_path, layer, hints_path, threshold, flashes)
    out = Path(out)
    report = out.with_name(out.stem + "_measured.json")
    atomic_json(out, timeline)
    atomic_json(report, measured)
    return {"timeline": str(out), "measured": str(report), "frames": measured["frames"], "fps": timeline["fps"],
            "lines": len(measured["lines"]), "unseen_lines": measured["unseen_lines"],
            "differing": [{"id": r["id"], "note": r["note"]} for r in measured["lines"] if _differs(r)],
            "flashes": len(timeline["flashes"]), "beats": len(timeline["beats"]["beats"])}
