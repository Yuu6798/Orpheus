"""合成デモ: 設定画と、異常を決まったコマに入れた緑背景の人物動画と inspect.json を作る。実作品の素材は使わない。

    mv inspect demo DIR → sheet.png / clip.mp4 / inspect.json / expected.json

人物は頭・髪・上着（深紅）・スカート・脚の図形で、左右にゆっくり揺れて腕を振る。入れる異常（24 fps のときのコマ。fps に合わせて秒で決める）:

| 秒 | 分類 | 中身 |
|---|---|---|
| 0.33〜0.83 | 動作 | 小刻みな揺れ（毎コマ左右に数画素） |
| 1.0 | 連続性 | 1 コマだけ明るくなる |
| 1.25 | 連続性 | 1 コマだけ別の画（緑背景ごと） |
| 1.67〜1.83 | 連続性 | 同じ絵が 4 コマ続き、その後に追い付く |
| 2.08〜2.21 | 形状 | 腕の先に離れた塊が出る（3 コマ） |
| 2.5〜3.13 | 衣装 | 上着が青に変わる（設定画の深紅が消える） |
| 3.5〜3.58 | 動作 | 人物が横へ飛んで戻る（2 コマ） |

expected.json に入れたコマと分類を書く（テストと CI の確かめ用）。
"""
import json
import math
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..fx import render as fxrender

GREEN = (0, 255, 0)
SKIN, HAIR, TOP, SKIRT, BLUE = (236, 205, 186), (70, 40, 90), (165, 18, 40), (40, 36, 52), (40, 90, 220)
ANOMALIES = [   # (名前, 分類, 開始秒, 終了秒)。終了は含まない
    ("jitter", "motion", 0.34, 0.84),
    ("flicker", "continuity", 1.0, 1.04),
    ("glitch", "continuity", 1.25, 1.29),
    ("freeze", "continuity", 1.67, 1.84),
    ("fragment", "shape", 2.08, 2.21),
    ("costume", "costume", 2.5, 3.13),
    ("teleport", "motion", 3.5, 3.59),
]


def _frames_of(fps, names=None):
    """異常の名前 → [開始, 終了) のコマ。names に無い異常は空の区間（入れない）。"""
    return {name: (int(round(a * fps)), int(round(b * fps))) if names is None or name in names else (-1, -1) for name, _, a, b in ANOMALIES}


def draw_character(width, height, cx, foot, swing, top=TOP, background=GREEN, blob=False):
    """緑の背景（background=None なら透明）に人物の図形を描いた uint8 RGB(A)。cx・foot は画面の割合、swing は腕の振り（-1..1）。"""
    channels = 3 if background is not None else 4
    image = np.zeros((height, width, channels), np.uint8)
    if background is not None:
        image[:] = background
    tall = 0.78 * height
    # 座標は 1/16 画素の固定小数（shift=4）で描く。整数に丸めると、ゆっくりした動きで偶然同じ絵のコマができる
    px = lambda x, y: (int(round((cx * width + x * tall) * 16)), int(round((foot * height - y * tall) * 16)))  # noqa: E731
    color = lambda c: tuple(int(v) for v in c) + (() if channels == 3 else (255,))  # noqa: E731
    thick = max(3, int(round(0.045 * tall)))
    radius = lambda r: max(64, int(round(r * tall * 16)))  # noqa: E731
    for side in (-1, 1):                                                       # 脚
        cv2.line(image, px(side * 0.05, 0.42), px(side * 0.07, 0.0), color(SKIN), thick, cv2.LINE_AA, 4)
    cv2.fillPoly(image, [np.array([px(-0.13, 0.45), px(0.13, 0.45), px(0.08, 0.28), px(-0.08, 0.28)])], color(SKIRT), cv2.LINE_AA, 4)
    for side in (-1, 1):                                                       # 腕（振り）
        hand = px(side * 0.2, 0.52 + 0.1 * side * swing)
        cv2.line(image, px(side * 0.09, 0.72), hand, color(SKIN), thick, cv2.LINE_AA, 4)
    if blob:                                                                   # 腕から離れた塊（形状の異常）
        cv2.circle(image, px(0.27, 0.78), radius(0.055), color(SKIN), -1, cv2.LINE_AA, 4)
    cv2.line(image, px(0, 0.74), px(0, 0.82), color(SKIN), thick, cv2.LINE_AA, 4)                    # 首
    cv2.fillPoly(image, [np.array([px(-0.1, 0.76), px(0.1, 0.76), px(0.08, 0.44), px(-0.08, 0.44)])], color(top), cv2.LINE_AA, 4)
    cv2.circle(image, px(0, 0.86), radius(0.085), color(SKIN), -1, cv2.LINE_AA, 4)
    cv2.ellipse(image, px(0, 0.9), (radius(0.095), radius(0.06)), 0, 180, 360, color(HAIR), -1, cv2.LINE_AA, 4)
    return image


def _frame(width, height, i, fps, spans):
    """コマ i の絵。spans は異常の名前 → [開始, 終了) のコマ。"""
    def inside(name):
        a, b = spans[name]
        return a <= i < b
    if inside("freeze"):
        i = spans["freeze"][0]                     # 止まる（同じ絵を描く）
    t = i / fps
    cx = 0.5 + 0.035 * math.sin(2 * math.pi * t / 2.0)
    foot = 0.94 + 0.006 * math.sin(2 * math.pi * t / 1.0)
    swing = math.sin(2 * math.pi * t / 1.5)
    if inside("jitter"):
        cx += (1 if i % 2 else -1) * 4.0 / width
    if inside("teleport"):
        cx += 0.22
    image = draw_character(width, height, cx, foot, swing, top=BLUE if inside("costume") else TOP, blob=inside("fragment"))
    if inside("glitch"):
        rng = np.random.default_rng(i)
        image = np.full((height, width, 3), (120, 110, 100), np.uint8)
        for _ in range(6):
            x0, y0 = int(rng.integers(0, width - 8)), int(rng.integers(0, height - 8))
            cv2.rectangle(image, (x0, y0), (x0 + width // 4, y0 + height // 6), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    if inside("flicker"):
        image = np.clip(image.astype(np.int16) + 60, 0, 255).astype(np.uint8)
    return image


def create(directory, width=216, height=384, fps=24, duration=4.0, anomalies=None):
    """anomalies は入れる異常の名前（None = 全部、空 = 異常の無い素材）。"""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    n = int(round(duration * fps))
    names = None if anomalies is None else set(anomalies)
    unknown = (names or set()) - {name for name, *_ in ANOMALIES}
    if unknown:
        raise ValueError(f"知らない異常の名前です: {sorted(unknown)}")
    spans = _frames_of(fps, names)
    if max(b for _, b in spans.values()) > n:
        raise ValueError(f"duration は 3.6 秒以上にしてください（入れる異常が収まりません）: {duration}")
    sheet = draw_character(480, 640, 0.5, 0.95, 0.0, background=None)
    Image.fromarray(sheet, "RGBA").save(directory / "sheet.png")
    fxrender.write_video(directory / "clip.mp4", (_frame(width, height, i, fps, spans) for i in range(n)), (width, height), fps, 12, n)
    config = {
        "schema_version": "inspect/0.1",
        "clips": [{"id": "demo", "path": "clip.mp4",
                   "origin": {"tool": "synthetic-demo", "note": "mv inspect demo が描いた合成素材。生成器の素性の書き方の例"}}],
        "reference": [{"path": "sheet.png", "view": "front"}],
        "foreground": {"key": {"color": list(GREEN), "tolerance": 0.22, "softness": 0.12}},
    }
    (directory / "inspect.json").write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    expected = {"fps": fps, "frames": n, "anomalies": [{"name": name, "category": category, "frames": list(spans[name])}
                                                       for name, category, _, _ in ANOMALIES if spans[name][1] > 0]}
    (directory / "expected.json").write_text(json.dumps(expected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"config": str(directory / "inspect.json"), "clip": str(directory / "clip.mp4"), "sheet": str(directory / "sheet.png"),
            "expected": str(directory / "expected.json"), "frames": n, "fps": fps}
