"""書き出した動画からコマを取り出して、もとのコマと比べる（check.video_frames）。動画の中身が、いま描いたコマ（の並び）であることを
数で確かめる: 古い版のコマのまま書き出した、コマが 1 つずれた、別のフォルダのコマを渡した、を見つけるため。

リポジトリ外の 3 作目の VRM のダンスで、演出版を書き出すたびに、動画から数コマを取り出してもとのコマと比べた使い捨ての比べ
（同じコマとの差は平均 1.2〜1.9/255、5 コマ先との差は 10〜62 だった）を部品にした。

- 取り出し: 動画の n 番目のコマ（復号した順。0 から）が、コマ T = first + n。ffmpeg の select で取り出し、BT.709 の式と TV の範囲で
  RGB に戻す（render.assemble と同じ書き方の動画）。
- 比べ: 取り出したコマと、もとのコマ T・T ± offset の範囲の各コマとの、色の差の平均（R・G・B の 3 つで割る。0〜255）。
- 合う: 範囲の中で差がいちばん小さいのが T で、T との差が T ± offset のコマとの差の ratio 倍以下（既定 0.5。古い版の動画のように、
  どのコマとも同じくらい離れていれば合わない）。max_mean_abs を渡せば、T との差がそれ以下であることも。
- 見分けられない: もとのコマ T と T ± offset のコマの差が still（既定 1.0）より小さい（動きの無い所）。
"""
import re
import subprocess

import numpy as np
from PIL import Image

from .io import ToolError


def video_size(ffmpeg, path):
    """動画の映像の幅と高さ（ffmpeg -i の表示から）。"""
    run = subprocess.run([str(ffmpeg), "-hide_banner", "-i", str(path)], capture_output=True, text=True, encoding="utf-8",
                         errors="replace")
    for line in run.stderr.splitlines():
        if "Video:" in line:
            m = re.search(r"\b(\d{2,5})x(\d{2,5})\b", line)
            if m:
                return int(m.group(1)), int(m.group(2))
    raise ToolError(f"check.video_frames: 動画の映像の大きさが読めません: {path}")


def decode(ffmpeg, path, indices, size, timeout=600):
    """動画の、復号した順のコマ indices（0 から）を、BT.709・TV の範囲から RGB（uint8、(高さ, 幅, 3)）に戻して {番号: 配列}。"""
    wanted = sorted(set(int(i) for i in indices))
    chosen = "+".join(f"eq(n\\,{i})" for i in wanted)
    width, height = size
    argv = [str(ffmpeg), "-hide_banner", "-v", "error", "-i", str(path), "-map", "0:v:0", "-fps_mode", "passthrough",
            "-vf", f"select='{chosen}',scale=iw:ih:flags=bicubic+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv:"
                   "out_range=pc", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    try:
        run = subprocess.run(argv, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"check.video_frames: ffmpeg が timeout（{timeout} 秒）を超えました") from error
    if run.returncode:
        raise ToolError(f"check.video_frames: ffmpeg が終了コード {run.returncode} で失敗しました: "
                        f"{run.stderr.decode('utf-8', 'replace')[-1500:]}")
    frame_bytes = width * height * 3
    count = len(run.stdout) // frame_bytes
    if count < len(wanted):
        raise ToolError(f"check.video_frames: 動画から取り出せたコマが {count} 個で、頼んだ {len(wanted)} 個より少ないです"
                        f"（動画のコマの数を超える T: {wanted[count:][:5]}）")
    return {i: np.frombuffer(run.stdout[k * frame_bytes:(k + 1) * frame_bytes], np.uint8).reshape(height, width, 3)
            for k, i in enumerate(wanted)}


def load_rgb(path):
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def mean_abs(a, b):
    return float(np.abs(a.astype(np.int16) - b.astype(np.int16)).mean())


def compare(video_frames, files, samples, first, offset, ratio=0.5, still=1.0, max_mean_abs=None):
    """[行]。video_frames は {動画のコマの番号: RGB}、files は {T: もとのコマのファイル}。"""
    cache, rows = {}, []

    def frame(T):
        if T not in cache:
            cache[T] = load_rgb(files[T])
        return cache[T]

    for T in samples:
        shown = video_frames[T - first]
        if shown.shape != frame(T).shape:
            raise ToolError(f"check.video_frames: 動画のコマ（{shown.shape[1]}×{shown.shape[0]}）と、もとのコマ T {T}"
                            f"（{frame(T).shape[1]}×{frame(T).shape[0]}）の大きさが違います")
        window = [u for u in range(T - offset, T + offset + 1) if u in files]
        diffs = {u: mean_abs(shown, frame(u)) for u in window}
        others = {u: d for u, d in diffs.items() if u != T}
        best = min(diffs, key=lambda u: (diffs[u], abs(u - T)))
        far = {u: diffs[u] for u in (T - offset, T + offset) if u in diffs}
        motion = min((mean_abs(frame(T), frame(u)) for u in far), default=0.0)       # もとのコマどうしの差（動きの大きさ）
        close = bool(far) and diffs[T] <= ratio * min(far.values())
        level = max_mean_abs is None or diffs[T] <= max_mean_abs
        if motion < still:
            status = "indistinct"
        elif best == T and close and level:
            status = "matched"
        else:
            status = "mismatched"
        rows.append({"T": T, "video_frame": T - first, "same_mean_abs": diffs[T],
                     "nearest_other": None if not others else {"T": min(others, key=others.get), "mean_abs": min(others.values())},
                     "offset_mean_abs": {str(u - T): d for u, d in far.items()}, "frames_motion_mean_abs": motion,
                     "best_T": best, "status": status})
    return rows


def summarize(rows):
    mismatched = [r["T"] for r in rows if r["status"] == "mismatched"]
    indistinct = [r["T"] for r in rows if r["status"] == "indistinct"]
    shifts = sorted({r["best_T"] - r["T"] for r in rows if r["status"] == "mismatched"})
    same = [r["same_mean_abs"] for r in rows]
    others = [r["nearest_other"]["mean_abs"] for r in rows if r["nearest_other"] is not None and r["status"] != "indistinct"]
    if mismatched:
        passed, reason = False, f"動画のコマが、もとのコマと合わない（T {mismatched[:10]}。いちばん近いコマとのずれ {shifts}）"
    elif len(indistinct) == len(rows):
        passed, reason = None, "取り出したどのコマも、もとのコマが前後と同じで見分けられない"
    else:
        passed, reason = True, "取り出したコマは、どれも同じ T のもとのコマにいちばん近い"
    return {"pass": passed, "reason": reason, "matched": sum(1 for r in rows if r["status"] == "matched"),
            "mismatched": mismatched, "indistinct": indistinct, "shifts": shifts,
            "same_mean_abs": [min(same), max(same)] if same else None,
            "nearest_other_mean_abs": [min(others), max(others)] if others else None}
