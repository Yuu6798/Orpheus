"""`mv fx quick --render` の後の確認（実験的）: 1 枚の画像（要所のコマと、一番寄ったコマの等倍の切り出し）と短い要約。

描いた動画を 1 回だけ読み、次を OUT/check.jpg と OUT/check.json に書く（quick の stdout にも短く出す）。

- 寄りの最大: カメラの zoom の最大と、そのとき元画像の 1 画素が画面の何画素か（目安 1.5 まで）。
- 明滅: `mv gate flashes` と同じ測り方（報告は動画の横の *_flash_report）。
- 顔の切れ: 寄りの点のまわりが画面の端で切れたまま続いた最長の秒と、その段（カメラから数えた値）。
- 元画像との差: 要所のコマと、元画像を同じカメラで写しただけの画との差の平均（0..255。周辺減光・演出・圧縮の分を含む）。
  画像の下の段には、一番寄ったコマの等倍の切り出しと、同じ所を元画像から同じカメラで写しただけの切り出しを並べる（ぼけ・色の
  ずれ・演出の強さを目で比べる）。
- 止まったコマ: `mv inspect` と同じ決まり（前のコマとの差が dup_max 以下のコマが freeze_frames 以上続き、前後 window_s 秒の差の
  中央値が dup_max × dup_context 以上の所）。差は解析の大きさ（幅 320）の灰色の画で数える。

どれも機械で測った値で、見た目の採否ではない（technical_pass と採否は分ける）。
"""
import json
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, atomic_image, atomic_json
from .timeline import frame_count

CHECK_IMAGE, CHECK_JSON = "check.jpg", "check.json"
ANALYSIS_WIDTH = 320            # 止まったコマを数える画の幅（mv inspect の analysis_width の既定と同じ）
MAGNIFICATION_LIMIT = 1.5


def _thresholds():
    from ..inspection.config import THRESHOLDS
    return {name: THRESHOLDS[name] for name in ("dup_max", "dup_context", "window_s", "freeze_frames")}


def frozen(diffs, fps, thresholds=None):
    """止まったコマの区間 [(始まりの秒, 終わりの秒)]。diffs[i] はコマ i と i−1 の差（0..1。diffs[0] は nan）。"""
    th = thresholds or _thresholds()
    n, window = len(diffs), max(4, int(round(th["window_s"] * fps)))
    duplicate = [math.isfinite(d) and d <= th["dup_max"] for d in diffs]
    flagged = []
    for i in range(1, n):
        around = [diffs[j] for j in range(max(1, i - window), min(n, i + window + 1)) if not duplicate[j] and math.isfinite(diffs[j])]
        moving = bool(around) and float(np.median(around)) >= th["dup_max"] * th["dup_context"]
        flagged.append(duplicate[i] and moving)
    out, start = [], None
    for i, flag in enumerate(flagged + [False], 1):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            if i - start + 1 >= th["freeze_frames"]:
                out.append((round((start - 1) / fps, 3), round((i - 1) / fps, 3)))
            start = None
    return out


def key_times(layout, path, fps):
    """確認する要所 [(名前, 秒)]: 頭（黒から上がった後）・一番寄った所・山場（無ければ真ん中）・終わり（暗転の前）。コマの秒にそろえる。"""
    from .quick import OPEN_S
    p, total = layout["phases"], frame_count(layout["seconds"], fps)
    zooms = [path.at(i / fps)[0] for i in range(total)]
    closest = int(np.argmax(zooms))
    middle = p["impact"] + 0.25 if p["impact"] is not None else 0.5 * (p["pull"][0] + p["pull"][1])
    picks = [("head", OPEN_S + 0.25), ("closest", closest / fps), ("impact" if p["impact"] is not None else "middle", middle),
             ("end", p["fade"][0] - 0.05)]
    return [(name, min(max(int(round(t * fps)), 0), total - 1) / fps) for name, t in picks], max(zooms)


def _read(video, indices, size):
    """動画を 1 回読み、indices のコマ（RGB uint8）と、コマごとの前のコマとの差（幅 ANALYSIS_WIDTH の灰色。0..1）を返す。"""
    import cv2
    capture = cv2.VideoCapture(str(video))
    if not capture.isOpened():
        raise PipelineError(f"動画を開けません: {video}")
    small = (ANALYSIS_WIDTH, max(8, int(round(ANALYSIS_WIDTH * size[1] / size[0]))))
    frames, diffs, previous, index = {}, [], None, 0
    try:
        while True:
            ok, bgr = capture.read()
            if not ok:
                break
            if index in indices:
                frames[index] = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            gray = cv2.cvtColor(cv2.resize(bgr, small, interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2GRAY).astype(np.int16)
            diffs.append(math.nan if previous is None else float(np.mean(np.abs(gray - previous))) / 255.0)
            previous, index = gray, index + 1
    finally:
        capture.release()
    return frames, diffs


def _reference(source, path, t, size):
    """元画像（RGB float32）を、時刻 t のカメラで画面へ写しただけの画（演出も周辺減光も無し。plate: full の写しと同じ camera.warp_fine）。"""
    from . import camera
    return camera.warp_fine(source, path.matrix(t, 1.0), tuple(size))


def _crop_box(center, size, crop):
    x0 = int(round(min(max(center[0] - crop[0] / 2, 0), size[0] - crop[0])))
    y0 = int(round(min(max(center[1] - crop[1] / 2, 0), size[1] - crop[1])))
    return x0, y0, x0 + crop[0], y0 + crop[1]


def sheet(frames, labels, crops, crop_labels, max_width=1600):
    """要所のコマを 1 列に並べ、その下に等倍の切り出し（左: 描いたコマ、右: 元画像を同じカメラで写しただけ）を置いた 1 枚
    （字は ASCII）。"""
    import cv2
    gap, bar = 8, 26
    height, width = frames[0].shape[:2]
    cell_w = (max_width - gap * (len(frames) + 1)) // len(frames)
    cell_h = int(round(height * cell_w / width))
    crop_h, crop_w = crops[0].shape[:2]
    canvas_w = max(gap + len(frames) * (cell_w + gap), gap + len(crops) * (crop_w + gap))
    canvas = np.full((gap + bar + cell_h + gap + bar + crop_h + gap, canvas_w, 3), 18, np.uint8)
    for i, (frame, label) in enumerate(zip(frames, labels)):
        x = gap + i * (cell_w + gap)
        canvas[gap + bar:gap + bar + cell_h, x:x + cell_w] = cv2.resize(frame, (cell_w, cell_h), interpolation=cv2.INTER_AREA)
        cv2.putText(canvas, label, (x + 2, gap + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
    y = gap + bar + cell_h + gap
    for i, (crop, label) in enumerate(zip(crops, crop_labels)):
        x = gap + i * (crop_w + gap)
        cv2.putText(canvas, label, (x + 2, y + 19), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (235, 235, 235), 1, cv2.LINE_AA)
        canvas[y + bar:y + bar + crop_h, x:x + crop_w] = crop
    return canvas


def run(out, layout, image, image_size, size, fps, video, flashes_report=None):
    """確認の画像と要約を書いて、要約（dict）を返す。flashes_report は mv gate flashes の報告（無ければここで測る）。"""
    from PIL import Image

    from . import quick
    from .view import View
    out, video = Path(out), Path(video)
    path = quick.camera_path(layout, image_size, size)
    picks, max_zoom = key_times(layout, path, fps)
    indices = {int(round(t * fps)) for _, t in picks}
    frames, diffs = _read(video, indices, size)
    if len(frames) < len(indices):
        raise PipelineError(f"動画のコマが足りません（{len(diffs)} コマ）: {video}")
    with Image.open(image) as handle:
        source = np.asarray(handle.convert("RGB"), np.float32)
    differences = {}
    for name, t in picks:
        reference = _reference(source, path, t, size)
        differences[name] = round(float(np.mean(np.abs(frames[int(round(t * fps))].astype(np.float32) - reference))), 1)
    closest_t = dict(picks)["closest"]
    view = View(path, (0.0, 0.0, 1.0, 1.0), image_size, size, closest_t)
    focus = view.point(*layout["start"])
    crop_size = (min(size[0], 780), min(size[1], 440)) if size[0] >= size[1] else (min(size[0], 540), min(size[1], 720))
    x0, y0, x1, y1 = _crop_box(focus, size, crop_size)
    crop = frames[int(round(closest_t * fps))][y0:y1, x0:x1]
    reference = np.clip(_reference(source, path, closest_t, size)[y0:y1, x0:x1] + 0.5, 0, 255).astype(np.uint8)
    if flashes_report is None:
        from ..gate import flashes
        flashes_report, _ = flashes.run(video)
    stops = frozen(diffs[:quick.inspect_frames(layout, fps)[1]], fps)       # 暗転の終わりの黒は数えない（mv inspect の設定と同じ）
    edge = layout["edge"]
    worst = max(edge, key=edge.get)
    labels = [f"{t:.2f}s {name}" for name, t in picks]
    image_out = out / CHECK_IMAGE
    atomic_image(image_out, sheet([frames[int(round(t * fps))] for _, t in picks], labels, [crop, reference],
                                  [f"1:1 crop at {closest_t:.2f}s (closest)", "same view from the source (no effects)"]))
    summary = {
        "image": str(image_out),
        "zoom_max": round(max_zoom, 3), "magnification_max": round(max_zoom * path.cover, 3), "magnification_limit": MAGNIFICATION_LIMIT,
        "flashes": {"status": flashes_report["status"], "flashes": flashes_report["counts"]["flashes"],
                    "max_per_window": flashes_report["max_per_window"], "limit_per_s": flashes_report["thresholds"]["max_per_s"]},
        "edge": {"seconds": edge[worst], "phase": worst, "limit": quick.EDGE_LIMIT_S},
        "difference": differences,
        "frozen": [list(span) for span in stops],
        "times": {name: round(t, 3) for name, t in picks},
        "note": "機械で測った値で、見た目の採否ではない。差は元画像を同じカメラで写しただけの画との平均（0..255。周辺減光・演出・圧縮を含む）",
    }
    atomic_json(out / CHECK_JSON, summary)
    return summary


def lines(summary, image=None):
    """要約の短い行（stdout・log.md 用）。image は確認の画像のパスの書き方（無ければ check.json の値）。"""
    from .quick import PHASE_NAMES
    f, e = summary["flashes"], summary["edge"]
    stops = summary["frozen"]
    difference = "・".join(f"{name} {value:g}" for name, value in summary["difference"].items())
    return [f"確認: {image or summary['image']}（要所 4 コマと、一番寄ったコマの等倍・元画像の同じ所）",
            f"寄りの最大 {summary['zoom_max']:.2f} 倍（元画像の 1 画素が {summary['magnification_max']:.2f} 画素。目安 "
            f"{summary['magnification_limit']:g} まで）",
            f"明滅 {f['status']}（{f['flashes']} 回、1 秒に最大 {f['max_per_window']} 回。上限 {f['limit_per_s']:g}）・"
            f"顔の切れ 最長 {e['seconds']:.2f} 秒（{PHASE_NAMES.get(e['phase'], e['phase'])}。目安 {e['limit']:g}）",
            f"元画像との差 {difference}（0..255）・止まったコマ "
            + ("なし" if not stops else "、".join(f"{a:.2f}–{b:.2f} 秒" for a, b in stops))]


def dumps(summary):
    return json.dumps(summary, ensure_ascii=False)
