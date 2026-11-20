"""fx の timeline の顔の枠を、描かずに各コマの画面へ写し、画面から切れたコマを数える（`mv gate faces`）。

    mv gate faces TIMELINE --out DIR [--start S] [--duration D] [--extra boxes.json] [--min-visible 1.0]

- 枠は、spec のショットは spec の `faces`、動画のショットは timeline のショットの `faces`（どれも元画像・動画の正規化座標）。
  `--extra` で、ショットごとに枠を足せる（手・扇など、顔のほかに字を重ねたくない物。spec を変えずに足せる）:

      {"schema_version": "gate-boxes/0.1",
       "boxes": [{"shot": "shots/a.json", "box": [x0, y0, x1, y1], "role": "other", "label": "手", "estimated": true}]}

  `shot` は timeline のショットの `spec`（動画のショットは `video.path`）と同じ文字列か、ショットの番号（0 から）。
  同じ spec のショットが複数あれば、どれにも足す。`role` は main / other（既定 other）、`estimated` は既定 true。
- 写し方は `mv fx timeline render` の faces.json と同じ: コマの時刻 start + i/fps のショット（xfade・burn の間は入ってくるショット）の
  camera（層の視差 1。深度視差は入れない。拍の脈は入れる）で、枠の辺を画面へ写す（`camera.screen_box`）。
- コマ・枠ごとに `visible`（写した枠の面積のうち画面に入っている割合 0..1）を付ける。画面から全部出た枠も、面積 0 の箱と visible 0 で残す
  （faces.json の箱は画面の外を切った箱で、`mv gate check` の faces にそのまま渡せる。面積 0 の箱はどの字とも交わらない）。
- DIR に faces.json（[{"start", "end", "box", "role", "estimated", "visible"[, "label"]}]）と faces_report.json（ショットごとの、
  visible が min_visible より小さいコマの数・最小の visible・切れた辺）を書く。描画はしない（素材は読む）。
- 好みは判定しない。切れたコマを数えるだけ（寄りの絵で額や顎を切るのは演出のこともある）。枠は目で測った推定のことが多い。
"""
import json
import math
from pathlib import Path

from ..io import PipelineError, atomic_json, read_json, sha256

BOXES_SCHEMA = "gate-boxes/0.1"
REPORT_SCHEMA = "gate-faces-report/0.1"
FACES_NAME, REPORT_NAME = "faces.json", "faces_report.json"
ROLES = ("main", "other")
EPS = 1e-6      # visible の比較の丸め（画面の端ちょうどに置いた枠を「切れた」としない）


def _box(value, where):
    if (not isinstance(value, list) or len(value) != 4 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
            or not (value[2] > value[0] and value[3] > value[1])):
        raise PipelineError(f"{where} の box は [x0, y0, x1, y1]（x0 < x1・y0 < y1 の数）にしてください: {value!r}")
    return [float(v) for v in value]


def read_extra(path, data):
    """--extra の枠 → {ショットの番号: [{box, role, estimated, label}]}。"""
    if path is None:
        return {}
    raw = read_json(path)
    if not isinstance(raw, dict) or raw.get("schema_version") != BOXES_SCHEMA or not isinstance(raw.get("boxes"), list):
        raise PipelineError(f'--extra は {{"schema_version": "{BOXES_SCHEMA}", "boxes": [...]}} の形にしてください: {path}')
    names = {}
    for index, shot in enumerate(data["shots"]):
        names.setdefault(shot["spec"] if shot["kind"] == "spec" else shot["video"]["path"], []).append(index)
    extra = {}
    for n, item in enumerate(raw["boxes"]):
        where = f"--extra の boxes[{n}]"
        if not isinstance(item, dict):
            raise PipelineError(f"{where} は辞書にしてください")
        unknown = sorted(set(item) - {"shot", "box", "role", "label", "estimated"} - {k for k in item if k.startswith("x_")})
        if unknown:
            raise PipelineError(f"{where} に知らない鍵があります: {unknown}")
        shot = item.get("shot")
        if isinstance(shot, bool) or not isinstance(shot, (int, str)):
            raise PipelineError(f"{where} の shot は、timeline のショットの spec（動画は video.path）の文字列か番号にしてください")
        if isinstance(shot, int):
            if not 0 <= shot < len(data["shots"]):
                raise PipelineError(f"{where} の shot {shot} は timeline のショットの番号 0〜{len(data['shots']) - 1} にありません")
            indexes = [shot]
        elif shot in names:
            indexes = names[shot]
        else:
            raise PipelineError(f"{where} の shot {shot!r} は timeline のショットにありません")
        role = item.get("role", "other")
        if role not in ROLES:
            raise PipelineError(f"{where} の role は {' か '.join(ROLES)} にしてください: {role!r}")
        estimated = item.get("estimated", True)
        if not isinstance(estimated, bool):
            raise PipelineError(f"{where} の estimated は true か false にしてください")
        label = item.get("label")
        if label is not None and not isinstance(label, str):
            raise PipelineError(f"{where} の label は文字列にしてください")
        entry = {"box": _box(item.get("box"), where), "role": role, "estimated": estimated, "label": label}
        for index in indexes:
            extra.setdefault(index, []).append(entry)
    return extra


def visible_part(box):
    """写した枠（正規化座標。画面の外にはみ出してよい）→（画面の外を切った箱, 画面に入っている面積の割合）。"""
    clipped = [min(max(v, 0.0), 1.0) for v in box]
    area = (box[2] - box[0]) * (box[3] - box[1])
    inside = max(0.0, clipped[2] - clipped[0]) * max(0.0, clipped[3] - clipped[1])
    return clipped, (inside / area if area > 0 else 0.0)


def _cut_sides(box):
    """画面の外に出ている辺（left・top・right・bottom）。"""
    return [side for side, out in (("left", box[0] < -EPS), ("top", box[1] < -EPS), ("right", box[2] > 1 + EPS),
                                   ("bottom", box[3] > 1 + EPS)) if out]


def project(timeline_path, start=0.0, duration=None, extra_path=None):
    """各コマの枠を画面へ写す。戻り値は (timeline, 区間 (start, length, fps, フレーム数), コマの記録の並び)。
    記録は {"i", "t", "shot", "entries": [{box, role, estimated, label, raw}]}（raw は画面の外を切る前の箱）。"""
    from ..fx import camera
    from ..fx import timeline as fxtimeline
    from ..fx.sequence import Context, load_timeline
    from ..fx.sequence_plan import shot_index

    timeline = load_timeline(timeline_path)
    data = timeline.data
    fps, total = data["output"]["fps"], data["duration_s"]
    start = float(start)
    length = total - start if duration is None else float(duration)
    if not start >= 0:
        raise PipelineError(f"--start は 0 以上にしてください: {start}")
    if not length > 0 or start + length > total + 1e-6:
        raise PipelineError(f"区間 {start:g}〜{start + length:g} 秒が timeline の長さ 0〜{total:g} 秒に収まりません")
    extra = read_extra(extra_path, data)
    n_frames = fxtimeline.frame_count(length, fps)
    ctx = Context(timeline, 1.0, None)
    frames = []
    try:
        for i in range(n_frames):
            t = fxtimeline.frame_time(i, fps, start)
            index = shot_index(data, t)
            shot = data["shots"][index]
            faces = timeline.specs[index].data["faces"] if shot["kind"] == "spec" else shot["faces"]
            boxes = [{**face, "label": None} for face in faces] + extra.get(index, [])
            entries = []
            if boxes:
                local, path = shot["offset_s"] + (t - shot["start"]), ctx.shot(index).path
                for face in boxes:
                    raw = [float(v) for v in camera.screen_box(path, local, face["box"])]
                    entries.append({"raw": raw, "role": face["role"], "estimated": face["estimated"], "label": face.get("label")})
            frames.append({"i": i, "t": t, "shot": index, "entries": entries})
    finally:
        ctx.close()
    return timeline, (start, length, fps, n_frames), frames


def faces_records(frames, fps):
    """`mv gate check` の faces の形（区間はコマの 1 つ分）+ visible・label。"""
    out = []
    for frame in frames:
        for entry in frame["entries"]:
            clipped, visible = visible_part(entry["raw"])
            record = {"start": round(frame["t"], 6), "end": round(frame["t"] + 1 / fps, 6), "box": [round(v, 5) for v in clipped],
                      "role": entry["role"], "estimated": entry["estimated"], "visible": round(visible, 4)}
            if entry["label"]:
                record["label"] = entry["label"]
            out.append(record)
    return out


def report(timeline, interval, frames, min_visible=1.0, roles=("main",)):
    """ショットごとに、roles の枠の visible が min_visible より小さいコマを数える。"""
    start, length, fps, n_frames = interval
    data = timeline.data
    shots, cut_frames, without = {}, 0, 0
    for frame in frames:
        shot = data["shots"][frame["shot"]]
        row = shots.setdefault(frame["shot"], {"shot": frame["shot"], "name": shot["spec"] if shot["kind"] == "spec" else shot["video"]["path"],
                                               "frames": 0, "boxes": 0, "cut_frames": 0, "min_visible": None, "sides": [],
                                               "first_cut_t": None, "last_cut_t": None})
        row["frames"] += 1
        watched = [e for e in frame["entries"] if e["role"] in roles]
        row["boxes"] = max(row["boxes"], len(watched))
        if not watched:
            without += 1
            continue
        cut = False
        for entry in watched:
            visible = round(visible_part(entry["raw"])[1], 4)      # faces.json と同じ丸め（mv gate check と同じコマを数える）
            row["min_visible"] = visible if row["min_visible"] is None else min(row["min_visible"], visible)
            if visible < min_visible - EPS:
                cut = True
                row["sides"] = sorted(set(row["sides"]) | set(_cut_sides(entry["raw"])))
        if cut:
            cut_frames += 1
            row["cut_frames"] += 1
            row["first_cut_t"] = round(frame["t"], 6) if row["first_cut_t"] is None else row["first_cut_t"]
            row["last_cut_t"] = round(frame["t"], 6)
    rows = [{**row, "min_visible": None if row["min_visible"] is None else round(row["min_visible"], 4)} for row in shots.values()]
    return {"schema_version": REPORT_SCHEMA, "timeline": str(timeline.path), "timeline_sha256": timeline.sha256,
            "interval": {"start": start, "duration": length, "fps": fps, "frames": n_frames},
            "min_visible": min_visible, "roles": list(roles), "frames_with_cut": cut_frames, "frames_without_boxes": without,
            "shots": rows}


def write(timeline_path, out, start=0.0, duration=None, extra_path=None, min_visible=1.0):
    if not 0 < min_visible <= 1:
        raise PipelineError(f"--min-visible は 0 より大きく 1 以下にしてください: {min_visible}")
    timeline, interval, frames = project(timeline_path, start, duration, extra_path)
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    records = faces_records(frames, interval[2])
    (out / FACES_NAME).write_text(json.dumps(records, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    rep = report(timeline, interval, frames, min_visible)
    rep["faces"] = FACES_NAME
    rep["extra"] = None if extra_path is None else {"path": str(Path(extra_path).resolve()), "sha256": sha256(extra_path)}
    atomic_json(out / REPORT_NAME, rep)
    cut = [{k: row[k] for k in ("shot", "name", "cut_frames", "min_visible", "sides", "first_cut_t", "last_cut_t")}
           for row in rep["shots"] if row["cut_frames"]]
    return {"faces": str(out / FACES_NAME), "report": str(out / REPORT_NAME), "frames": interval[3], "boxes": len(records),
            "frames_with_cut": rep["frames_with_cut"], "min_visible": min_visible, "cut_shots": cut}
