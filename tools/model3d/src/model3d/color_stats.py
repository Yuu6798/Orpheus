"""色の数を、絵の組で比べる（check.colors）: 参考の絵（left）とこちらの絵（right）を同じ物差しで測る。時刻の違う止め絵の組
（参考の動画の決めた時刻のコマと、こちらの見本のコマ）で、明るさと色の分布が近いかを数で見るため。並べて見るのは mv sheet --with。

リポジトリ外の 4 作目の 3D のダンスで、参考の動画と舞台・塗りの見本を比べた 2 つの台本（絵ごとの明るさと色の分布、決めた所の色の
平均）を 1 つにした。どれも表示の値（sRGB の 0〜255 を 255 で割った値。線形に直さない）で測る。
- 明るさ: 画素ごとに 0.2126 R + 0.7152 G + 0.0722 B。その平均と、5 %・50 %・95 % の値（numpy の percentile。間は直線で補う）。
- 色: R・G・B の平均、緑÷赤・青÷赤（平均どうしの比。赤の平均は 1e-6 より小さくしない）。
- 彩度: OpenCV の 8 ビットの HSV の S と同じ整数の計算（S = (差 × round(255 × 4096 ÷ V) + 2048) >> 12。V = 0 なら 0）の平均 ÷ 255。
- 決めた所（spots）: 中心 (x, y) と半径 r の正方形（x − r〜x + r、y − r〜y + r の画素。画面の外は切る）の R・G・B の平均（0〜255）、
  いちばん大きい値、どれかの色が 254 以上の画素の割合（白飛び）。
- 切り抜き（crop）: [x0, y0, x1, y1]（画素。x1・y1 は含まない）。決めた所の座標は、切り抜く前の絵の座標。
"""
from bisect import bisect_right
from fractions import Fraction

import numpy as np
from PIL import Image

from .io import ToolError

STATS = ("mean", "p05", "p50", "p95", "g_r", "b_r", "sat")


def load(path):
    """絵を RGB（uint8、(高さ, 幅, 3)）で読む（透けは捨てる）。"""
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGB"))
    except (OSError, ValueError) as error:
        raise ToolError(f"check.colors: 絵を読めません: {path}（{error}）") from error


def saturation(rgb):
    """OpenCV の 8 ビットの HSV の S（0〜255 の整数）と同じ計算。"""
    a = rgb.astype(np.int64)
    v = a.max(axis=-1)
    diff = v - a.min(axis=-1)
    table = np.zeros(256, np.int64)
    table[1:] = np.array([int(round((255 << 12) / i)) for i in range(1, 256)], np.int64)
    return (diff * table[v] + (1 << 11)) >> 12


def stats(rgb):
    """絵 1 枚の明るさと色の分布（表示の値 0〜1）。"""
    f = rgb.astype(np.float32) / 255.0
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    lum = 0.2126 * r + 0.7152 * g + 0.0722 * b
    means = [float(x.mean()) for x in (r, g, b)]
    return {"mean": float(lum.mean()), "p05": float(np.percentile(lum, 5)), "p50": float(np.percentile(lum, 50)),
            "p95": float(np.percentile(lum, 95)), "rgb": means, "g_r": float(g.mean() / max(1e-6, r.mean())),
            "b_r": float(b.mean() / max(1e-6, r.mean())), "sat": float(saturation(rgb).mean() / 255)}


def spot(rgb, x, y, r):
    """中心 (x, y)・半径 r の正方形の色（0〜255 の R・G・B の平均・いちばん大きい値・白飛びの割合）。"""
    height, width = rgb.shape[:2]
    x0, x1, y0, y1 = max(0, x - r), min(width, x + r + 1), max(0, y - r), min(height, y + r + 1)
    if x0 >= x1 or y0 >= y1:
        raise ToolError(f"check.colors: 決めた所 ({x}, {y}) r {r} が絵（{width}×{height}）の外です")
    patch = rgb[y0:y1, x0:x1].reshape(-1, 3).astype(float)
    return {"rgb": [float(v) for v in patch.mean(axis=0)], "max": float(patch.max()),
            "clipped": float((patch.max(axis=1) >= 254).mean()), "pixels": int(len(patch))}


def crop(rgb, box):
    if box is None:
        return rgb
    x0, y0, x1, y1 = box
    height, width = rgb.shape[:2]
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise ToolError(f"check.colors: 切り抜き {box} が絵（{width}×{height}）の中にありません")
    return rgb[y0:y1, x0:x1]


def measure(rgb, item):
    """1 枚を測る: {名前, 大きさ, 明るさと色の数, 決めた所の色}。item は確かめた見本（name・crop・spots）。"""
    row = {"name": item["name"], "size": [int(rgb.shape[1]), int(rgb.shape[0])], **stats(crop(rgb, item.get("crop")))}
    if item.get("crop") is not None:
        row["crop"] = list(item["crop"])
    if item.get("spots"):
        row["spots"] = {s["name"]: spot(rgb, s["x"], s["y"], s["r"]) for s in item["spots"]}
    return row


def average(rows):
    """組の平均（数ごと。R・G・B は色ごと）。"""
    if not rows:
        return None
    out = {key: float(np.mean([r[key] for r in rows])) for key in STATS}
    out["rgb"] = [float(np.mean([r["rgb"][k] for r in rows])) for k in range(3)]
    return out


def compare(left, right):
    """組の平均どうしの差（こちら − 参考）。"""
    if left is None or right is None:
        return None
    out = {key: right[key] - left[key] for key in STATS}
    out["rgb"] = [b - a for a, b in zip(left["rgb"], right["rgb"])]
    return out


AT_SLACK = Fraction(1, 1_000_000)                       # 時刻のゆとり（秒）。ちょうどコマの頭の時刻を、丸めで前のコマにしない


def parse_frame_times(text):
    """ffmpeg の framemd5（映像の 1 本目。-c copy）の表示から、コマの表示の時刻（秒の分数。最初のコマを 0。表示の順）と、
    最後のコマの終わりの時刻。時間の単位（#tb）と pts・duration の整数のまま計算する（29.97 のような丸めた fps を使わない）。"""
    base, rows = None, []
    for line in text.splitlines():
        if line.startswith("#tb 0:"):
            num, den = line.split(":", 1)[1].strip().split("/")
            base = Fraction(int(num), int(den))
        elif line.strip() and not line.startswith("#"):
            parts = [p.strip() for p in line.split(",")]
            if parts[0] == "0":
                rows.append((int(parts[2]), int(parts[3])))
    if base is None or not rows:
        raise ToolError("check.colors: 動画のコマの時刻が読めません（映像が無いか、ffmpeg の framemd5 の表示が読めない）")
    rows.sort()
    start = rows[0][0]
    times = [(pts - start) * base for pts, _ in rows]
    return times, times[-1] + max(rows[-1][1], 0) * base


def frame_at(times, end, at):
    """時刻 at 秒に映っているコマの番号（表示の順。0 から）: 頭の時刻が at（＋ 1e-6 秒）以下の、いちばん後ろのコマ。
    動画の終わりより後ろなら None。"""
    t = Fraction(at) + AT_SLACK
    if Fraction(at) >= end:
        return None
    return bisect_right(times, t) - 1


def video_frame_times(ffmpeg, path, timeout=600):
    """動画の映像のコマの時刻（parse_frame_times。ffmpeg で包みを読むだけで、絵は戻さない）。"""
    import subprocess
    argv = [str(ffmpeg), "-hide_banner", "-v", "error", "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "framemd5", "-"]
    try:
        run = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired as error:
        raise ToolError(f"check.colors: ffmpeg が timeout（{timeout} 秒）を超えました") from error
    if run.returncode:
        raise ToolError(f"check.colors: 動画のコマの時刻が読めません: {path}（{run.stderr.strip()[-300:]}）")
    return parse_frame_times(run.stdout)
