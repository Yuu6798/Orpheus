"""dance のステージのセットの見本（`mv dance render spec.json --hooks examples/dance/backdrop_hooks.py`）。

`backdrop` はカメラの前のステージに描く口で、返した画に spec のカメラ・視差・ブレが掛かる（人物と同じく寄りや揺れで動く）。
ここでは、ステージに横の光の帯を 3 本足し、spec の cues の seam で暗くして end_card までに戻す（`cues.envelope`）。
cues が無い spec ではステージのまま。帯の位置・色・強さは見本の値なので、作品ごとに変える。画は線形の float32。
"""
import numpy as np

from mv_pipeline.dance import api, cues

ROWS = (0.30, 0.42, 0.54)                                   # 帯の高さ（画面の高さに対する割合）
COLOR = np.array([0.40, 0.45, 0.60], np.float32)          # 線形の光の色


def backdrop(img, t, ctx):
    when = api.cues(ctx)
    seam, end = cues.first(when, "seam"), cues.first(when, "end_card")
    if seam is None or end is None:
        return img
    level = cues.envelope([(seam, 1.0), (seam + 0.1, 0.3), (end, 1.0, "smooth")], t)
    height = img.shape[0]
    y = np.arange(height, dtype=np.float32)[:, None]
    bars = sum(np.exp(-((y - row * height) / (0.006 * height)) ** 2) for row in ROWS)
    return img + bars[..., None] * COLOR * np.float32(level)
