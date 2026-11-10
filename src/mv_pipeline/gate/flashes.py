"""完成動画から明滅を測る（`mv gate flashes`。実験的）。timeline は要らず、完成した動画 1 本だけに掛けられる。

    mv gate flashes VIDEO [--out DIR] [--delta 0.10] [--max-per-s 3]

hooks で足した光は timeline に載らないので、描き終えた動画そのものから数える。機械で測れる数字だけを出し、
**規格への適合を判定するものではない**（既定値は広く使われている目安に合わせた仮の値。docs/gate.md）。

測り方:
  1. コマを小さく読む（幅 READ_WIDTH 画素の面積平均。色の行列はタグによらず BT.709 として読む。読み手は inspection.video）。
  2. コマごとの明るさ = 表示の値（ガンマのかかった RGB）の輝度 0.2126 R + 0.7152 G + 0.0722 B の画面全体の平均 / 255。
  3. 変化 = 続く 2 コマの間で明るさが delta 以上、上がるか下がること。同じ向きの変化が続くコマに並ぶとき（数コマかけた
     上がり・下がり）は 1 つにまとめ、大きさはその合計。1 コマあたり delta 未満のゆっくりした明暗は、何コマ続いても変化にしない。
  4. 明滅 = 隣り合う 2 つの変化が反対向き（暗 → 明 → 暗、または明 → 暗 → 明）で、始まりのコマの差が 1 秒未満の組。前から順に、
     重ならないように組にする。片方向だけの変化（場面の切り替え）や、1 秒以上離れた 2 つの変化は明滅にしない。
  5. 1 秒の窓の回数 = その窓（始まりのコマから fps コマ未満）に入る変化だけを、4 と同じ決まりで組にした数。窓の前にある
     変化が組をずらして数を減らさないよう、窓ごとに組み直す（明滅の一覧と区切りが違うことがある）。
     どの窓でも最大の回数が max_per_s を超えたら status は fail（終了コード 1）。

出力（既定は動画の隣の `<動画の名前>_flash_report/`）は flash_report.json: 動画の sha256・fps・コマ数、しきい値、変化の一覧、
明滅の一覧、どの 1 秒の窓でも最大何回か、最も多い窓。同じ動画と同じしきい値なら同じ報告になる（時刻や絶対パスは書かない）。

測らないもの: 画面の一部だけの明滅（全体の平均に薄まる）、色（赤）の明滅、1 コマあたり delta 未満でゆっくり消える光の戻り。
cv2 は使わない（numpy・Pillow・ffmpeg）。
"""
import math
from pathlib import Path

import numpy as np

from ..inspection import video
from ..io import PipelineError, atomic_json, sha256

SCHEMA = "gate-flash-report/0.1"
REPORT_NAME = "flash_report.json"
DEFAULTS = {"delta": 0.10, "max_per_s": 3}   # 仮の値（1 秒間に 3 回を超えない・変化は最大の明るさの 10%）。規格の判定ではない
WINDOW_S = 1.0
READ_WIDTH = 64                              # 読む幅（画素）。画面全体の平均だけを使うので小さくてよい
READ_MATRIX = "bt709"
LUMA = (0.2126, 0.7152, 0.0722)              # BT.709 の輝度の係数（表示の値に掛ける）
EPS = 1e-9
NOTE = "機械で測った回数の報告です。規格への適合を判定するものではありません（画面の一部・色の明滅は測っていません）。"


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PipelineError(f"{name} は数値にしてください: {value!r}")
    return value


def settings(value=None, where="flash"):
    """{"delta", "max_per_s"}。無い欄は DEFAULTS。知らない欄・範囲の外はエラー（打ち間違いを既定値で通さない）。"""
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise PipelineError(f'{where} は {{"delta": 0.10, "max_per_s": 3}} の形にしてください（既定値で測るなら {{}}）: {value!r}')
    unknown = sorted(set(value) - set(DEFAULTS))
    if unknown:
        raise PipelineError(f"{where} に知らない欄があります: {', '.join(unknown)}（使えるのは {'・'.join(DEFAULTS)}）")
    delta = float(_number(value.get("delta", DEFAULTS["delta"]), f"{where} の delta"))
    limit = _number(value.get("max_per_s", DEFAULTS["max_per_s"]), f"{where} の max_per_s")
    if not 0 < delta <= 1:
        raise PipelineError(f"{where} の delta は 0 より大きく 1 以下にしてください（明るさは 0〜1）: {delta:g}")
    if limit < 0:
        raise PipelineError(f"{where} の max_per_s は 0 以上にしてください: {limit:g}")
    return {"delta": delta, "max_per_s": int(limit) if float(limit).is_integer() else float(limit)}


def read_levels(path):
    """(コマごとの明るさ 0〜1 の list, 動画の情報, 読んだ大きさ)。情報は inspection.video.describe の形。"""
    path = Path(path)
    if video.is_image(path):
        raise PipelineError(f"動画を指定してください（画像は測れません）: {path.name}")
    info = video.describe(path)
    size = video.even_size(info["width"], info["height"], READ_WIDTH)
    weights = np.array(LUMA)
    levels = [float(frame.reshape(-1, 3).mean(axis=0) @ weights) / 255.0
              for _, frame in video.frames(path, size, {**info, "matrix": READ_MATRIX})]
    if not levels:
        raise PipelineError(f"動画のコマを 1 つも読めません: {path.name}")
    return levels, info, size


def find_changes(levels, delta):
    """変化の一覧 [{"frame", "end_frame", "direction", "from", "to", "amount"}]。

    frame は変わった後の最初のコマ（frame - 1 → frame の間で delta 以上）。同じ向きの変化が続くコマに並べば 1 つにまとめ、
    end_frame はその最後のコマ、amount は from（変わる前）と to（変わった後）の差。"""
    changes = []
    for index in range(1, len(levels)):
        step = levels[index] - levels[index - 1]
        if abs(step) < delta - EPS:
            continue
        direction = "up" if step > 0 else "down"
        last = changes[-1] if changes else None
        if last is not None and last["end_frame"] == index - 1 and last["direction"] == direction:
            last["end_frame"], last["to"] = index, levels[index]
        else:
            changes.append({"frame": index, "end_frame": index, "direction": direction, "from": levels[index - 1], "to": levels[index]})
    for change in changes:
        change["amount"] = abs(change["to"] - change["from"])
    return changes


def pair(changes, span):
    """明滅の組 [(i, j)]（changes の添字）。隣り合う 2 つが反対向きで、始まりのコマの差が span 未満なら 1 組。

    前から順に、重ならないように取る（隣どうしの組を最も多く取る取り方になる）。"""
    pairs, index = [], 0
    while index + 1 < len(changes):
        first, second = changes[index], changes[index + 1]
        if first["direction"] != second["direction"] and second["frame"] - first["frame"] < span - EPS:
            pairs.append((index, index + 1))
            index += 2
        else:
            index += 1
    return pairs


def busiest(changes, span):
    """(最大の回数, その窓の最初の変化の添字 | None, その窓の組)。窓は変化の始まりのコマから span コマ未満。

    どの 1 秒の窓に入る変化も、ある変化から始まる窓に全部入るので、変化から始まる窓だけを見れば足りる。同じ回数なら早い窓。"""
    best = (0, None, [])
    for start, first in enumerate(changes):
        stop = start
        while stop < len(changes) and changes[stop]["frame"] - first["frame"] < span - EPS:
            stop += 1
        pairs = pair(changes[start:stop], span)
        if len(pairs) > best[0]:
            best = (len(pairs), start, [(start + a, start + b) for a, b in pairs])
    return best


def _seconds(frame, fps):
    return round(frame / fps, 4)


def measure(path, delta=DEFAULTS["delta"], max_per_s=DEFAULTS["max_per_s"]):
    """動画 1 本を測って報告（flash_report.json の中身）を返す。ファイルは書かない。"""
    path = Path(path)
    limits = settings({"delta": delta, "max_per_s": max_per_s}, "明滅のしきい値")
    levels, info, size = read_levels(path)
    fps = float(info["fps"])
    span = fps * WINDOW_S
    changes = find_changes(levels, limits["delta"])
    pairs = pair(changes, span)
    top, start, window_pairs = busiest(changes, span)
    rows = [{"frame": c["frame"], "end_frame": c["end_frame"], "t": _seconds(c["frame"], fps), "direction": c["direction"],
             "amount": round(c["amount"], 4), "from": round(c["from"], 4), "to": round(c["to"], 4)} for c in changes]
    flashes = [{"frame": changes[a]["frame"], "t": _seconds(changes[a]["frame"], fps),
                "return_frame": changes[b]["frame"], "return_t": _seconds(changes[b]["frame"], fps),
                "order": f"{changes[a]['direction']}_{changes[b]['direction']}",
                "amount": round(min(changes[a]["amount"], changes[b]["amount"]), 4)} for a, b in pairs]
    window = None
    if start is not None:
        first = changes[start]["frame"]
        inside = [c for c in changes[start:] if c["frame"] - first < span - EPS]
        window = {"start_frame": first, "start_t": _seconds(first, fps), "end_t": round(_seconds(first, fps) + WINDOW_S, 4),
                  "flashes": top, "changes": len(inside), "pairs": [[changes[a]["frame"], changes[b]["frame"]] for a, b in window_pairs]}
    ups = sum(1 for c in changes if c["direction"] == "up")
    return {
        "schema_version": SCHEMA,
        "video": {"name": path.name, "sha256": sha256(path), "fps": fps, "frames": len(levels),
                  "size": [info["width"], info["height"]], "matrix_tag": info["matrix"]},
        "read": {"size": list(size), "matrix": READ_MATRIX, "brightness": "mean(0.2126 R + 0.7152 G + 0.0722 B) / 255"},
        "thresholds": {**limits, "window_s": WINDOW_S},
        "counts": {"changes": len(changes), "up": ups, "down": len(changes) - ups, "flashes": len(flashes),
                   "unpaired_changes": len(changes) - 2 * len(flashes)},
        "changes": rows,
        "flashes": flashes,
        "max_per_window": top,
        "busiest_window": window,
        "status": "fail" if top > limits["max_per_s"] else "pass",
        "note": NOTE,
    }


def write(report, out):
    """報告を out/flash_report.json に書いて、そのパスを返す。"""
    target = Path(out) / REPORT_NAME
    atomic_json(target, report)
    return target


def run(path, out=None, delta=DEFAULTS["delta"], max_per_s=DEFAULTS["max_per_s"]):
    """`mv gate flashes`: 測って flash_report.json を書く。(報告, 出力フォルダ)。"""
    path = Path(path).resolve()
    report = measure(path, delta, max_per_s)
    out = Path(out) if out else path.with_name(f"{path.stem}_flash_report")
    write(report, out)
    return report, out


def summary(report, out):
    """stdout に出す短い要約。"""
    return {"status": report["status"], "out": str(out), "report": str(Path(out) / REPORT_NAME),
            "frames": report["video"]["frames"], "fps": report["video"]["fps"], **report["thresholds"],
            **report["counts"], "max_per_window": report["max_per_window"], "busiest_window": report["busiest_window"],
            "note": report["note"]}
