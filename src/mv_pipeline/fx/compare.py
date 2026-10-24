"""版の比較: 2×2 グリッド動画と、行=時刻・列=版の静止画シート。

グリッド動画は、書き出し済みの各版の MP4 を復号して並べる（各版の絵を 2 回描かない）。
つまり**目で見るのと同じ圧縮後の絵**の比較で、版ごとの枠は固定: base=左上 / layers=右上 / light=左下 / both=右下。
一部の版だけを描いたときも枠は動かず、空いた枠は黒。各タイルは W/2×H/2、左上に ASCII ラベル（Pillow の既定フォント）。
静止画シートは frame() を直接呼ぶ（圧縮なし）。
"""
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .. import media
from ..io import PipelineError
from . import VARIANTS, render

SLOTS = {variant: (index % 2, index // 2) for index, variant in enumerate(VARIANTS)}
GAP = 6
SHEET_BACKGROUND = 24


def tile_size(size):
    return size[0] // 2, size[1] // 2


def _font(pixels):
    try:
        return ImageFont.load_default(size=pixels)
    except (TypeError, OSError):  # FreeType の無い Pillow は固定サイズのビットマップ字体
        return ImageFont.load_default()


def label_patch(text, height):
    """ラベル (rgb float32, alpha float32 (h, w, 1))。半透明の黒帯に白字。height = 画像の高さ（字の大きさの基準）。"""
    font = _font(max(11, height // 18))
    probe = ImageDraw.Draw(Image.new("L", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    pad = 4
    canvas = Image.new("RGBA", (right - left + 2 * pad, bottom - top + 2 * pad), (0, 0, 0, 150))
    ImageDraw.Draw(canvas).text((pad - left, pad - top), text, font=font, fill=(255, 255, 255, 255))
    data = np.asarray(canvas, dtype=np.float32)
    alpha = data[..., 3:4] / 255.0
    return data[..., :3], alpha


def stamp(image, patch, x=0, y=0):
    """image (uint8 H×W×3) の (x, y) にラベルを重ねる（image を直接書き換える）。"""
    rgb, alpha = patch
    height, width = alpha.shape[:2]
    height, width = min(height, image.shape[0] - y), min(width, image.shape[1] - x)
    if height <= 0 or width <= 0:
        return image
    region = image[y:y + height, x:x + width].astype(np.float32)
    blended = region * (1.0 - alpha[:height, :width]) + rgb[:height, :width] * alpha[:height, :width]
    image[y:y + height, x:x + width] = np.clip(blended + 0.5, 0, 255).astype(np.uint8)
    return image


def label_for(variant, note=None):
    """ASCII のラベル。spec に該当機能が無く base と同じ映像の版には [no-op] を付ける。"""
    return f"{variant} [no-op]" if note and note.get("empty") and variant != "base" else variant


class _Reader:
    """書き出し済み MP4 を tile 寸法の rgb24 で 1 枚ずつ読む（ffmpeg の復号をパイプで受ける）。"""

    def __init__(self, path, tile, n_frames):
        width, height = tile
        self.path, self.bytes_per_frame = Path(path), width * height * 3
        self.shape = (height, width, 3)
        self.errors = tempfile.TemporaryFile()
        # 色変換は書き出しと同じ bt709 / tv を明示する（タグ任せにしない）。面積平均で縮小。
        scale = f"scale={width}:{height}:flags=area+accurate_rnd+full_chroma_int:in_color_matrix=bt709:in_range=tv"
        self.proc = subprocess.Popen(
            [media.ffmpeg(), "-hide_banner", "-nostdin", "-v", "error", "-i", str(path), "-an",
             "-frames:v", str(n_frames), "-vf", scale, "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
            stdout=subprocess.PIPE, stderr=self.errors)

    def read(self):
        data = self.proc.stdout.read(self.bytes_per_frame)
        while data is not None and len(data) < self.bytes_per_frame:
            more = self.proc.stdout.read(self.bytes_per_frame - len(data))
            if not more:
                break
            data += more
        if data is None or len(data) != self.bytes_per_frame:
            self.errors.seek(0)
            detail = self.errors.read()[-400:].decode("utf-8", "replace")
            raise PipelineError(f"グリッド用に {self.path.name} を復号できませんでした: {detail or '途中で終わりました'}")
        return np.frombuffer(data, dtype=np.uint8).reshape(self.shape)

    def close(self):
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.stdout.close()
        self.proc.wait()
        self.errors.close()


def grid_frames(sources, notes, size, n_frames):
    """sources = {版: MP4 のパス}。タイルを 2×2 に並べたフレーム (uint8 H×W×3) を n_frames 枚 yield する。

    yield する配列は使い回す（書き出し側が受け取ってすぐ書く前提）。保持したい呼び出し側は copy すること。
    """
    tile = tile_size(size)
    canvas = np.zeros((size[1], size[0], 3), dtype=np.uint8)
    patches = {v: label_patch(label_for(v, notes.get(v)), tile[1]) for v in sources}
    readers = {}
    try:
        for variant, path in sources.items():
            readers[variant] = _Reader(path, tile, n_frames)
        for _ in range(n_frames):
            for variant, reader in readers.items():
                col, row = SLOTS[variant]
                x, y = col * tile[0], row * tile[1]
                canvas[y:y + tile[1], x:x + tile[0]] = reader.read()
                stamp(canvas, patches[variant], x, y)
            yield canvas
    finally:
        for reader in readers.values():
            reader.close()


def write_grid(out_path, sources, notes, size, fps, crf, n_frames, audio=None, log_path=None):
    """各版の MP4 から grid.mp4 を書く（書式は版の MP4 と同じ。write_video と同じく一時名 → 置き換え）。"""
    unknown = [v for v in sources if v not in SLOTS]
    if unknown:
        raise PipelineError(f"unknown variant for grid: {unknown}")
    frames = grid_frames(sources, notes, size, n_frames)
    try:
        return render.write_video(out_path, frames, size, fps, crf, n_frames, audio, log_path)
    finally:
        frames.close()  # 書き出しが途中で失敗しても、復号プロセスを確実に止める


def stills_sheet(ctx, times, variants, notes):
    """行=時刻・列=版の静止画シート (uint8)。各マスは ctx.size の frame() そのまま（圧縮なし）で、左上に版と時刻。"""
    width, height = ctx.size
    rows, cols = len(times), len(variants)
    sheet = np.full((rows * height + (rows + 1) * GAP, cols * width + (cols + 1) * GAP, 3), SHEET_BACKGROUND, dtype=np.uint8)
    for r, t in enumerate(times):
        for c, variant in enumerate(variants):
            y, x = GAP + r * (height + GAP), GAP + c * (width + GAP)
            sheet[y:y + height, x:x + width] = render.frame(ctx, t, variant)
            stamp(sheet, label_patch(f"{label_for(variant, notes.get(variant))}  t={t:.2f}s", height), x, y)
    return sheet


def save_png_atomic(image, path):
    """PNG を一時名に書いて置き換える（途中で止まっても壊れた PNG を残さない）。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            Image.fromarray(image).save(stream, format="PNG")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
