"""`mv overlay` で重ねる透過の字と、顔・手などの枠の重なりを全部のコマで数える（`mv gate check` の「重ねの字と顔」）。

gate.json に `"overlay": {"layers": "layers.json", "alpha": 26, "roles": ["main", "other"]}` を書いたときだけ、検査の行を足す
（書かなければ、検査も報告も今までどおり）。今の「字と顔」は timeline の字の箱を見るので、外で描いて重ねる連番の字は見ない。
その字を、重ねる前の透過の層のアルファで数える。

- layers: `mv overlay --layers` の設定（fx-overlay-layers/0.1。gate.json のあるフォルダ基準）。出力の 0 コマ目は、描いた区間
  （timeline の interval）の頭のコマ。連番の first_frame・missing・resize・opacity と、1 枚の画像（start_s〜end_s と出入り）は
  `mv overlay` と同じに読む。仕上げ（style の影・光彩・後光）で広がる分は数えない（層のアルファだけ）。
- 字: どれかの層の アルファ（0..255）× 不透明度 が alpha（既定 26。約 0.1）より大きい画素。
- 枠: gate.json の faces（`mv gate faces` の faces.json か、手で書いた区間）のうち、roles（既定は main と other）の枠。
  コマの時刻 t が start ≤ t < end の枠を、そのコマに当てる。画面の正規化座標の箱を画素にして、端の画素は含める
  （左上は切り捨て、右下は切り上げ）。
- 重なり: 役・label・推定かどうかごとに、字が 1 画素でもあるコマを続いたコマへまとめ、コマ数と最大の画素数を出す。
- 判定: 実測の主役の枠（role main・estimated false）に重なれば不合格。推定の主役の枠と、ほかの枠（手・扇・ほかの人）は要確認。
  字の読みやすさや置き場の好みは判定しない。
"""
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, sha256

OVERLAY_CHECK = "重ねの字と顔"
REPORT_NAME = "overlay_report.json"
REPORT_SCHEMA = "gate-overlay-report/0.1"
ROLES = ("main", "other")
DEFAULT_ALPHA = 26
FRAME_EPS = 1e-3        # 区間の端のコマを決めるときの丸め（コマの単位）


def settings(value, where):
    """gate の設定の "overlay" → {"layers", "alpha", "roles"}。"""
    if not isinstance(value, dict):
        raise PipelineError(f'{where} は {{"layers": "layers.json", "alpha": {DEFAULT_ALPHA}, "roles": ["main", "other"]}} の形の辞書にしてください')
    unknown = sorted(set(value) - {"layers", "alpha", "roles"})
    if unknown:
        raise PipelineError(f"{where} に知らない鍵があります: {unknown}")
    layers = value.get("layers")
    if not isinstance(layers, str) or not layers:
        raise PipelineError(f"{where} の layers に、mv overlay --layers の設定ファイル（fx-overlay-layers/0.1）のパスを書いてください")
    alpha = value.get("alpha", DEFAULT_ALPHA)
    if isinstance(alpha, bool) or not isinstance(alpha, int) or not 0 <= alpha <= 254:
        raise PipelineError(f"{where} の alpha は 0〜254 の整数（アルファ 0..255 でこの値より大きい画素を字とみなす）にしてください: {alpha!r}")
    roles = value.get("roles", list(ROLES))
    if not isinstance(roles, list) or not roles or any(role not in ROLES for role in roles):
        raise PipelineError(f"{where} の roles は {list(ROLES)} から選んだ 1 つ以上の並びにしてください: {roles!r}")
    return {"layers": layers, "alpha": alpha, "roles": list(dict.fromkeys(roles))}


def _interval(timeline):
    """(描いた区間の頭の秒, コマ数, fps)。"""
    fps = float(timeline["fps"])
    interval = timeline.get("interval")
    if not interval:
        return 0.0, int(round(timeline["duration_s"] * fps)), fps
    frames = interval.get("frames")
    return float(interval["start"]), int(frames if frames is not None else round(interval["duration"] * fps)), fps


def _pixel_rect(box, width, height):
    """箱（画面の正規化座標）が掛かる画素の範囲 (x0, y0, x1, y1)。端の画素は含める（左上は切り捨て、右下は切り上げ）。"""
    x0 = max(0, math.floor(box[0] * width))
    y0 = max(0, math.floor(box[1] * height))
    x1 = min(width, math.ceil(box[2] * width))
    y1 = min(height, math.ceil(box[3] * height))
    return x0, y0, x1, y1


def frame_boxes(faces, first, n_frames, fps, roles):
    """{コマの番号: [枠]}。枠は start ≤ t < end のコマ（t = first + i / fps）に当てる。"""
    boxes = {}
    for face in faces:
        if face.get("role", "main") not in roles:
            continue
        i0 = math.ceil((face["start"] - first) * fps - FRAME_EPS)
        i1 = math.ceil((face["end"] - first) * fps - FRAME_EPS)
        for i in range(max(0, i0), min(n_frames, i1)):
            boxes.setdefault(i, []).append(face)
    return boxes


class _Alpha:
    """1 つの層の、コマ i のアルファ × 不透明度（float32 の (高さ, 幅)。字が無ければ None）。コマは小さい順に聞く。"""

    def __init__(self, layer, n_frames, size, fps):
        from ..fx import overlay
        self.layer, self.size, self.fps, self.overlay = layer, size, fps, overlay
        options = layer["options"]
        self.first = options["first_frame"]
        self.still = None
        self.stream = None
        self.position = -1
        self.current = None
        if layer["kind"] == "image":
            self.still = overlay._rgba(layer["path"], size, options)[..., 3].astype(np.float32)
        elif layer["kind"] == "frames":
            if self.first is None:
                self.names = [item["name"] for item in layer["files"]]
            else:
                self.names = {item["number"]: item["name"] for item in layer["files"]}
        else:
            self.stream = overlay._layer_frames(layer, n_frames, size)

    def at(self, index):
        overlay, layer = self.overlay, self.layer
        weight = overlay.opacity_at(layer, index, self.fps)
        if weight <= 0:
            if self.stream is not None:
                self._advance(index)
            return None
        if self.still is not None:
            alpha = self.still
        elif self.stream is not None:
            rgba = self._advance(index)
            if rgba is None:
                return None
            alpha = rgba[..., 3].astype(np.float32)
        else:
            name = self.names[index] if self.first is None else self.names.get(self.first + index)
            if name is None:
                return None
            rgba = overlay._rgba(Path(layer["path"]) / name, self.size, layer["options"], f"連番のコマ {name}")
            alpha = rgba[..., 3].astype(np.float32)
        return alpha if weight == 1.0 else alpha * np.float32(weight)

    def skip(self, index):
        """枠の無いコマ。動画の層だけ、読む位置を進める（復号は頭から順にしかできない）。"""
        if self.stream is not None:
            self._advance(index)

    def _advance(self, index):
        while self.position < index:
            self.current = next(self.stream)
            self.position += 1
        return self.current

    def close(self):
        if self.stream is not None:
            self.stream.close()


def load(layers_path, n_frames, size, fps):
    """layers.json → 層ごとの _Alpha。コマ数・寸法が合わなければ止まる（mv overlay と同じ）。"""
    from ..fx import overlay
    entries = overlay.load_layers(layers_path)
    sources = []
    for index, (path, options) in enumerate(entries):
        layer = overlay.select_frames(overlay.describe_layer(path, options), n_frames)
        if layer["kind"] != "image" and options["first_frame"] is None and layer["frames"] != n_frames:
            raise PipelineError(f"layers[{index}] のコマ数 {layer['frames']} が、描いた区間のコマ数 {n_frames} と合いません"
                                f"（曲全体の番号の連番なら first_frame を書いてください）")
        overlay._check_fit(layer["size"], list(size), layer["options"], f"layers[{index}]")
        sources.append(_Alpha(layer, n_frames, list(size), fps))
    return sources


def measure(timeline, faces, opts, layers_path):
    """コマ・枠ごとに字の画素を数え、まとめた重なりの並びと数を返す。"""
    first, n_frames, fps = _interval(timeline)
    width, height = timeline["size"]
    boxes = frame_boxes(faces, first, n_frames, fps, opts["roles"])
    sources = load(layers_path, n_frames, (width, height), fps)
    threshold = np.float32(opts["alpha"])
    groups, open_groups, with_text, checked = [], {}, 0, 0
    try:
        for index in range(n_frames):
            active = boxes.get(index)
            if not active:
                for source in sources:
                    source.skip(index)
                continue
            alpha = None
            for source in sources:
                a = source.at(index)
                if a is not None:
                    alpha = a if alpha is None else np.maximum(alpha, a)
            text = None if alpha is None else alpha > threshold
            with_text += text is not None and bool(text.any())          # 枠のあるコマのうち、画面のどこかに字があるコマ
            t = first + index / fps
            for face in active:
                checked += 1
                x0, y0, x1, y1 = _pixel_rect(face["box"], width, height)
                hit = 0 if text is None or x1 <= x0 or y1 <= y0 else int(text[y0:y1, x0:x1].sum())
                if not hit:
                    continue
                key = (face.get("role", "main"), face.get("label") or "", bool(face.get("estimated")))
                group = open_groups.get(key)
                if group is None or index - group["last_index"] > 1:
                    group = {"role": key[0], "label": key[1] or None, "estimated": key[2], "first_t": round(t, 6),
                             "last_t": round(t, 6), "frames": 0, "max_px": 0, "max_t": round(t, 6), "rect": [x0, y0, x1, y1],
                             "last_index": index, "counted": -1}
                    groups.append(group)
                    open_groups[key] = group
                if group["counted"] != index:
                    group["frames"] += 1
                    group["counted"] = index
                group["last_index"], group["last_t"] = index, round(t, 6)
                if hit > group["max_px"]:
                    group["max_px"], group["max_t"], group["rect"] = hit, round(t, 6), [x0, y0, x1, y1]
    finally:
        for source in sources:
            source.close()
    for group in groups:
        group.pop("last_index")
        group.pop("counted")
    return groups, {"frames": n_frames, "frames_with_boxes": len(boxes), "frames_with_text": with_text, "boxes_checked": checked}


def check(timeline, faces, opts, base):
    """「重ねの字と顔」の行と、overlay_report.json の中身。faces が無ければ未実施（報告なし）。"""
    from .checks import result, skip
    name = OVERLAY_CHECK
    if faces is None:
        return skip(name, "faces"), None
    layers_path = (base / opts["layers"]).resolve()
    groups, counts = measure(timeline, faces, opts, layers_path)
    problems = []
    for group in sorted(groups, key=lambda g: g["first_t"]):
        who = group["label"] or ("主役の顔" if group["role"] == "main" else "ほかの枠")
        tag = "（推定の枠）" if group["estimated"] else ""
        problems.append({"t": group["max_t"], "text": who,
                         "detail": f"{group['frames']} コマで字が枠に重なります{tag}（最大 {group['max_px']} 画素。"
                                   f"{group['first_t']:.2f}〜{group['last_t']:.2f} 秒）"})
    measured = sum(1 for g in groups if g["role"] == "main" and not g["estimated"])
    summary = f"{counts['frames_with_boxes']} コマで {counts['boxes_checked']} 個の枠を照合、重なり {len(groups)} 件"
    if groups:
        summary += f"（うち実測の主役の顔 {measured} 件）"
    status = "fail" if measured else "warn" if problems else "pass"
    report = {"schema_version": REPORT_SCHEMA, "layers": str(layers_path), "layers_sha256": sha256(layers_path),
              "alpha": opts["alpha"], "roles": opts["roles"], **counts, "overlaps": groups}
    return result(name, status, summary, problems), report
