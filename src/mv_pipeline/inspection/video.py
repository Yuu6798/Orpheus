"""動画と画像の読み手。動画は ffmpeg だけで、色の式を固定して読む。

OpenCV の色の読み方は環境で違う（Windows の 5.0.0 はタグを見ず BT.601 で読む。docs/dance.md の限界）。
配色を比べる検品が環境で変わらないよう、タグが bt709 の動画は ffmpeg に行列を指定し、ほかは ffmpeg の既定（BT.601）で読む。
コマ番号は 0 始まりの復号順で、時刻のシークは使わない（隣のコマを返すことがあるため）。
"""
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .. import media
from ..fx import probe
from ..io import PipelineError

IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def is_image(path):
    return Path(path).suffix.lower() in IMAGE_SUFFIXES


def describe(path):
    """{"width", "height", "fps", "matrix", "codec"}。映像が読めなければ PipelineError。"""
    info = probe.inspect(path)["video"]
    if not info["fps"]:
        raise PipelineError(f"動画の fps を読めません: {Path(path).name}")
    return {"width": info["width"], "height": info["height"], "fps": info["fps"], "matrix": info["matrix"], "codec": info["codec"]}


def even_size(width, height, target_width):
    """縦横比を保って幅 target_width（元より大きくしない）に縮めた偶数の (幅, 高さ)。"""
    w = min(int(target_width), int(width))
    w -= w % 2
    h = max(2, int(round(height * w / width / 2.0)) * 2)
    return max(2, w), h


def _scale(size, matrix):
    """大きさと色の行列を決める scale フィルター（測るコマも取り出すコマも同じ式で読む）。"""
    scale = f"scale={size[0]}:{size[1]}:flags=area" if size else "scale=iw:ih:flags=bicubic+accurate_rnd+full_chroma_int"
    return scale + (":in_color_matrix=bt709" if matrix == "bt709" else "")


def _filters(size, matrix, start, end):
    chain = []
    if start or end is not None:
        chain.append(f"trim=start_frame={int(start)}" + (f":end_frame={int(end)}" if end is not None else ""))
    chain.append(_scale(size, matrix))
    return ",".join(chain)


def frames(path, size, info, start=0, end=None):
    """(コマ番号, uint8 RGB (h, w, 3)) を復号順に返す。size=(w, h)（None は元の大きさ）、[start, end) はコマ番号。

    最後まで読んだら ffmpeg のエラー出力を確かめ、壊れていれば PipelineError。"""
    width, height = size or (info["width"], info["height"])
    frame_bytes = width * height * 3
    errors = tempfile.TemporaryFile()
    process = subprocess.Popen(
        [media.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
         "-fps_mode", "passthrough", "-vf", _filters(size, info["matrix"], start, end), "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
        stdout=subprocess.PIPE, stderr=errors)
    index = int(start)
    try:
        while True:
            data = process.stdout.read(frame_bytes)
            if len(data) < frame_bytes:
                break
            yield index, np.frombuffer(data, np.uint8).reshape(height, width, 3)
            index += 1
        code = process.wait()
        errors.seek(0)
        message = errors.read().decode("utf-8", "replace").strip()
        if code != 0:
            raise PipelineError(f"動画を復号できません（{Path(path).name}、終了コード {code}）: {message[-300:]}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
        errors.close()


SELECT_LIMIT = 200    # これより多いコマは select の式にせず、全部のコマを変換して選ぶ


def _sequential(path, wanted, info, size):
    """番号の確かな順読みで、wanted のコマだけを残す。"""
    keep, last = {}, wanted[-1]
    for index, image in frames(path, size, info, 0, last + 1):
        if index in wanted:
            keep[index] = image.copy()
    return keep


def grab(path, indices, info, size=None, total=None):
    """{コマ番号: uint8 RGB}。indices の番号のコマだけを、1 回の復号で取り出す（元の大きさ。size=(w, h) で縮める）。

    total（動画のコマ数）を渡すと、その外の番号は先に除く。番号の少ないときは ffmpeg の select で選んだコマだけを変換する
    （番号は復号順で、frames() と同じ）。出てきたコマの数が合わなければ（途中のコマが壊れているなど）順読みで取り直す。"""
    wanted = sorted({int(i) for i in indices if i is not None and i >= 0 and (total is None or i < total)})
    if not wanted:
        return {}
    if is_image(path):
        return {0: load_rgb(path)} if 0 in wanted else {}
    if len(wanted) > SELECT_LIMIT:
        return _sequential(path, wanted, info, size)
    width, height = size or (info["width"], info["height"])
    chosen = "+".join(f"eq(n\\,{i})" for i in wanted)
    run = subprocess.run([media.ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(path), "-map", "0:v:0",
                          "-fps_mode", "passthrough", "-vf", f"select='{chosen}',{_scale(size, info['matrix'])}", "-pix_fmt", "rgb24",
                          "-f", "rawvideo", "pipe:1"], capture_output=True)
    if run.returncode != 0:
        raise PipelineError(f"コマを取り出せません（{Path(path).name}）: {run.stderr.decode('utf-8', 'replace')[-300:]}")
    size_bytes = width * height * 3
    count = len(run.stdout) // size_bytes
    if count != len(wanted):
        return _sequential(path, wanted, info, size)
    return {index: np.frombuffer(run.stdout[k * size_bytes:(k + 1) * size_bytes], np.uint8).reshape(height, width, 3).copy()
            for k, index in enumerate(wanted)}


def load_rgba(path):
    """画像を uint8 RGBA (h, w, 4) で読む。"""
    try:
        with Image.open(path) as image:
            return np.asarray(image.convert("RGBA"))
    except OSError as error:
        raise PipelineError(f"画像を読めません（{Path(path).name}）: {error}") from error


def load_rgb(path):
    return np.ascontiguousarray(load_rgba(path)[..., :3])


def image_info(path):
    image = load_rgba(path)
    return {"width": image.shape[1], "height": image.shape[0], "fps": None, "matrix": None, "codec": "image"}


def resize(image, size):
    """uint8 画像を size=(w, h) に縮める（面積平均）。"""
    if (image.shape[1], image.shape[0]) == tuple(size):
        return image
    return np.array(Image.fromarray(image).resize(tuple(size), Image.Resampling.BOX))
