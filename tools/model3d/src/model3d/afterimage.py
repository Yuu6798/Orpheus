"""目の残像の絵: 暗くなる直前の明るいコマを、ぼかして明るい所だけを、決めた色でうすく足す（暗くなった頭でいちばん濃く、決めたコマ数で
1/e に薄れる）。

リポジトリ外の 4 作目の 3D のダンスの仕上げの台本（OpenCV で書いた）を移した。model3d は OpenCV に頼らないので、numpy で書く。
- 明るさは BT.709 の係数（0.2126 R + 0.7152 G + 0.0722 B。表示の値のまま）。台本は 8 ビットの色をぼかしてから明るさにしたが、
  ここでは明るさ（float）をぼかす（足し算の順が違うだけの同じ式。8 ビットへの丸めの分だけ違う）。
- ぼかしは題字と同じ（OpenCV の GaussianBlur の float の絵と同じ長さと形の核）。
- 足し方は台本と同じ（0〜255 に切り、切り捨て）。
2D の側の `afterimage_echo`（人物の輪郭の残像を、人物の後ろに足す）とは別のもの。こちらは画面全体の、目に残る光の像。"""
import math

import numpy as np

LUMA = (0.2126, 0.7152, 0.0722)


def ghost(bright, sigma, threshold, color):
    """明るいコマ（uint8 の RGB）から、足す像（float32 の高さ × 幅 × 3。濃さを掛ける前）。明るさをぼかし（sigma 画素）、threshold を
    引いて 0〜1 に切り、color（0〜1 の RGB）を掛ける。"""
    from .title import blur
    luminance = (bright.astype(np.float32) / np.float32(255.0)) @ np.asarray(LUMA, np.float32)
    lit = np.clip(blur(luminance, sigma) - np.float32(threshold), 0, 1)
    return lit[..., None] * np.asarray(color, np.float32)[None, None]


def strength(T, first, amount, decay_frames):
    """コマ T の濃さ: first で amount、decay_frames コマごとに 1/e。"""
    return amount * math.exp(-(T - first) / decay_frames)


def add(image, picture, amount):
    """コマ（uint8 の RGB）に、像を濃さ amount で足す（0〜255 に切り、切り捨て）。"""
    return np.clip(image.astype(np.float32) + picture * 255.0 * amount, 0, 255).astype(np.uint8)
