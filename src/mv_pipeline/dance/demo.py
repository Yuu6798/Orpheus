"""合成デモ: プログラムで描いたステージ・緑背景の人物（棒人間）・クリック音と spec.json を作る。実作品の素材は使わない。

    mv dance demo DIR → stage.png / clip_a.mp4 / clip_b.mp4 / audio.wav / spec.json

clip_a と clip_b は腕の振りの位相が違うので、切れ目で姿勢が飛ぶ。そこに camera.seams の動きが入る。
"""
import json
import math
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..fx import render as fxrender
from . import SCHEMA_VERSION

GREEN = (0, 255, 0)
BPM = 120.0


def _stage(width, height):
    y = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None, None]
    top, bottom = np.array([14, 10, 40], np.float32), np.array([70, 30, 90], np.float32)
    image = np.broadcast_to(top + (bottom - top) * y, (height, width, 3)).astype(np.uint8).copy()
    floor = int(height * 0.8)
    image[floor:] = (30, 20, 44)
    for fx_pos, fy_pos in ((0.15, 0.62), (0.85, 0.62), (0.3, 0.55), (0.7, 0.55)):
        center = (int(fx_pos * width), int(fy_pos * height))
        cv2.circle(image, center, int(0.03 * width), (255, 190, 110), -1, cv2.LINE_AA)
    return cv2.GaussianBlur(image, (0, 0), max(1.0, width / 400))


def _dancer(width, height, t, phase):
    """緑の背景に棒人間を描く。腕は拍の半分の周期で振る。"""
    image = np.full((height, width, 3), GREEN, np.uint8)
    swing = math.sin(2 * math.pi * BPM / 60 / 2 * t + phase)
    thick = max(3, width // 40)
    head = (width // 2, int(height * 0.14))
    neck = (width // 2, int(height * 0.24))
    hip = (width // 2, int(height * 0.58))
    color = (235, 210, 200)
    cloth = (120, 60, 200)
    cv2.line(image, neck, hip, cloth, thick * 2, cv2.LINE_AA)
    for side in (-1, 1):
        hand = (int(width * (0.5 + side * 0.3)), int(height * (0.32 - 0.14 * side * swing)))
        foot = (int(width * (0.5 + side * 0.16)), int(height * 0.97))
        cv2.line(image, neck, hand, color, thick, cv2.LINE_AA)
        cv2.line(image, hip, foot, cloth, thick, cv2.LINE_AA)
    cv2.circle(image, head, int(height * 0.08), color, -1, cv2.LINE_AA)
    return image


def _write_clip(path, width, height, duration, fps, phase):
    n = int(round(duration * fps))
    frames = (_dancer(width, height, i / fps, phase) for i in range(n))
    fxrender.write_video(path, frames, (width, height), fps, 12, n)


def _audio(seconds, rate=48000):
    """拍ごとのクリック音（16bit モノラル）。"""
    samples = np.zeros(int(seconds * rate), np.float32)
    n = int(0.05 * rate)
    click = np.sin(2 * np.pi * 880 * np.arange(n) / rate) * np.exp(-np.arange(n) / (0.01 * rate)) * 0.4
    for beat in range(int(seconds * BPM / 60) + 1):
        at = int(beat * 60 / BPM * rate)
        end = min(len(samples), at + n)
        samples[at:end] += click[:end - at]
    return (np.clip(samples, -1, 1) * 32767).astype(np.int16)


def _spec(width, height, duration, fps):
    half = duration / 2
    return {
        "schema_version": SCHEMA_VERSION,
        "output": {"width": width, "height": height, "fps": fps, "crf": 18},
        "duration_s": duration,
        "audio": {"path": "audio.wav"},
        "stage": {"path": "stage.png"},
        "performer": {
            "clips": [{"path": "clip_a.mp4", "start": 0.0, "end": half},
                      {"path": "clip_b.mp4", "start": half, "end": duration}],
            "key": {"color": list(GREEN)},
            "placement": {"anchor": [0.5, 0.95], "pivot": [0.5, 1.0], "height": 0.72},
        },
        "camera": {"keys": [{"t": 0.0, "zoom": 1.0}, {"t": duration, "zoom": 1.08, "center": [0.5, 0.55], "ease": "in_out"}],
                   "seams": [{"t": half, "span_s": 0.4, "zoom": 1.3, "rotate_deg": 8}]},
        "flashes": [{"t": half, "strength": 0.6, "decay_s": 0.2}],
        "lyrics": {"lines": [
            {"layer": "back", "text": "HOLD", "start": 0.5, "end": half - 0.1},
            {"layer": "back", "text": "LIGHT", "start": half, "end": duration - 0.2,
             "words": [{"text": "LI", "t": half + 0.5}, {"text": "GHT", "t": half + 1.0}]},
            {"layer": "band", "text": "demo band line one", "start": 0.5, "end": half - 0.1},
            {"layer": "band", "text": "demo band line two", "start": half, "end": duration - 0.2},
        ]},
    }


def create(directory, width=540, height=960, duration=6.0, fps=24):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    Image.fromarray(_stage(width, height)).save(directory / "stage.png")
    half = duration / 2
    clip_w, clip_h = 2 * (width // 3), 2 * (height // 3)   # 素材は出力より小さい。人物の縮尺を確かめるため
    _write_clip(directory / "clip_a.mp4", clip_w, clip_h, half, fps, 0.0)
    _write_clip(directory / "clip_b.mp4", clip_w, clip_h, half, fps, math.pi)
    with wave.open(str(directory / "audio.wav"), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(_audio(duration).tobytes())
    spec_path = directory / "spec.json"
    spec_path.write_text(json.dumps(_spec(width, height, duration, fps), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"spec": str(spec_path), "size": [width, height], "duration_s": duration, "fps": fps}
