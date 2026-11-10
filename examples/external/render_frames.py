"""自前の描画スクリプトの見本（合成素材）。`python render_frames.py CONFIG seg START END OUT`

- 絵はコマ番号だけで決まる（状態を持たない）。どの区間から描いても同じコマになる。
- 書き出しは共通の部品 `mv_pipeline.encode.write_video`（BT.709 の式と印。一時名に書き、成功したときだけ置き換える）。
- 引数の形は `mv fx legacy --units frames` が呼ぶ形と同じ。START・END はコマ番号で、END のコマは含まない。

手順は docs/external-renderer.md。
"""
import json
import sys

import numpy as np

from mv_pipeline import encode

PATCHES = ((255, 0, 0), (0, 255, 0), (0, 0, 255), (168, 107, 255), (255, 153, 64), (200, 200, 200))   # RGB


def frame(n, width, height):
    """コマ n の絵（RGB の uint8）。上半分は色票、下半分は左から右へ進む明るい帯。"""
    image = np.zeros((height, width, 3), np.uint8)
    step = width // len(PATCHES)
    for i, color in enumerate(PATCHES):
        image[: height // 2, i * step:(i + 1) * step] = color
    x = (n * 3) % width
    image[height // 2:, max(0, x - 2): x + 3] = 235
    return image


def main(argv):
    if len(argv) != 5 or argv[1] != "seg":
        raise SystemExit("usage: render_frames.py CONFIG seg START END OUT")
    config_path, _, start, end, out = argv
    with open(config_path, encoding="utf-8") as stream:
        config = json.load(stream)
    width, height = config.get("width", 320), config.get("height", 180)
    first, last = int(start), int(end)
    frames = (frame(n, width, height) for n in range(first, last))
    encode.write_video(out, frames, (width, height), config.get("fps", 24), config.get("crf", 18), last - first)


if __name__ == "__main__":
    main(sys.argv[1:])
