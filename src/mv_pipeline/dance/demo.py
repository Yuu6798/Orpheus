"""合成デモ: プログラムで描いたステージ・緑背景の人物（棒人間）・クリック音と spec.json を作る。実作品の素材は使わない。

    mv dance demo DIR → stage.mp4 / clip_a〜c.mp4 / end.png / audio.wav / spec.json

クリップは腕の振りの位相が違うので、切れ目で姿勢が飛ぶ。そこに camera.seams の動きが入る。
0.2 のデモのクリップは縮尺・左右の位置・足元の高さが本ごとに違い、足元はゆっくり流れ、途中で 1 回跳ぶ（fit の確認用）。
`--schema 0.1` は 0.1 の spec と素材を作る（互換の確認用）。
"""
import json
import math
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from ..fx import render as fxrender
from . import SCHEMA_V01, SCHEMA_VERSION

GREEN = (0, 255, 0)
BPM = 120.0
JUMP_S = 0.3
LOOP_S = 2.0


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


def _stage_frame(width, height, t):
    """2 秒で一周する動くステージ（グラデーションと横切る光）。"""
    image = _stage(width, height).astype(np.float32)
    phase = 2 * math.pi * t / LOOP_S
    x = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :, None]
    glow = np.exp(-((x - 0.5 - 0.35 * math.sin(phase)) / 0.18) ** 2)
    return np.clip(image + glow * np.array([70, 40, 20], np.float32), 0, 255).astype(np.uint8)


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


def _figure(width, height, t, phase, shape):
    """緑の背景に棒人間を描く（0.2 のデモ用）。shape は縮尺・左右の位置・足元の高さ。足元はゆっくり流れ、途中で 1 回跳ぶ。"""
    image = np.full((height, width, 3), GREEN, np.uint8)
    swing = math.sin(2 * math.pi * BPM / 60 / 2 * t + phase)
    scale, side_x = shape["scale"], shape["x"]
    foot_y = shape["foot"] + shape["drift"] * t
    if shape["jump"] is not None and abs(t - shape["jump"]) < JUMP_S / 2:
        foot_y -= 0.05 * math.cos(math.pi * (t - shape["jump"]) / JUMP_S)
    tall = 0.7 * height * scale
    point = lambda x, y: (int(round(width * side_x + x * tall)), int(round(height * foot_y - y * tall)))  # noqa: E731
    thick = max(3, width // 40)
    skin, cloth = (235, 210, 200), (120, 60, 200)
    neck, hip = point(0, 0.76), point(0, 0.42)
    cv2.line(image, neck, hip, cloth, thick * 2, cv2.LINE_AA)
    for side in (-1, 1):
        cv2.line(image, neck, point(side * 0.3, 0.68 + 0.14 * side * swing), skin, thick, cv2.LINE_AA)
        cv2.line(image, hip, point(side * 0.16, 0.0), cloth, thick, cv2.LINE_AA)
    cv2.circle(image, point(0, 0.88), int(0.11 * tall), skin, -1, cv2.LINE_AA)
    return image


def _write_figure(path, width, height, duration, fps, phase, shape):
    n = int(round(duration * fps))
    frames = (_figure(width, height, i / fps, phase, shape) for i in range(n))
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


def _spec_v01(width, height, duration, fps):
    half = duration / 2
    return {
        "schema_version": SCHEMA_V01,
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


def _cuts(duration):
    """クリップの切れ目の拍の番号 (1 本目の終わり, 2 本目の終わり, 3 本目の終わり)。どれも拍の上で、最後の静止画は 1 拍。"""
    count = int(round(duration * BPM / 60))
    second = min(round(2 * count / 3), count - 2)
    return min(round(count / 3), second - 1), second, count - 1


def _spec(width, height, duration, fps):
    first, second, third = _cuts(duration)
    half = {"beat": second}
    count = int(round(duration * BPM / 60))
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": 0,
        "output": {"width": width, "height": height, "fps": fps, "crf": 18},
        "duration_s": duration,
        "beats": {"bpm": BPM, "offset_s": 0.0, "beats_per_bar": 4, "count": int(duration * BPM / 60) + 8},
        "audio": {"path": "audio.wav", "fit": "pad", "end_s": duration - 0.25, "fade_in_s": 0.0, "fade_out_s": 0.06},
        "stage": {"path": "stage.mp4", "loop": True, "parallax": 0.7},
        "performer": {
            "clips": [{"path": "clip_a.mp4", "start": 0.0, "end": {"beat": first}, "source_start": -0.04},
                      {"path": "clip_b.mp4", "start": {"beat": first}, "end": {"beat": second}},
                      {"path": "clip_c.mp4", "start": {"beat": second}, "end": {"beat": third}, "placement": {"flip_x": True}},
                      {"path": "end.png", "start": {"beat": third}, "end": duration, "transition_in": {"type": "blend", "frames": 3}}],
            "key": {"color": list(GREEN)},
            "placement": {"anchor": [0.5, 0.92]},
            "fit": {"mode": "body", "body_height": 0.62, "x": "median", "ground": "lock", "ground_window_s": 0.7, "ground_smooth_s": 0.3},
        },
        "camera": {
            "keys": [{"t": 0.0, "zoom": 1.0}, {"t": duration, "zoom": 1.08, "center": [0.5, 0.55], "ease": "in_out"}],
            "accents": [{"at": {"grid": "downbeats", "to": {"beat": third}, "except": [half]}, "zoom": 1.02, "decay_s": 0.12},
                        {"at": [{"beat": first}], "zoom": 1.05, "decay_s": 0.12, "shake_px": 14, "shake_decay_s": 0.18}],
            "seams": [{"t": half, "pre_s": min(0.3, count / 40), "post_s": min(0.5, count / 24), "zoom": 1.3, "rotate_deg": 6, "rotate_post_deg": 9,
                       "blur": {"samples": 8, "shutter_frames": 1.0, "peak_shutter_frames": 1.6}}],
            "cover_rotation": True},
        "flashes": [{"t": half, "strength": 0.22, "decay_s": 0.08, "mode": "add_display"}],
        "look": {"vignette": {"amount": 0.3}, "grain": {"sigma": 1.5}},
        "lyrics": {
            "layers": {
                "back": {"box": [0.04, 0.12, 0.84, 0.46], "size_px": 300, "reveal": "word", "rows": {"align": "stagger", "gap_em": 0.04},
                         "enter": {"scale_from": 1.3, "rise_px": 26, "dur_s": 0.14}, "exit": {"scale_to": 1.16, "dur_s": 0.3},
                         "glow": {"color": [255, 150, 70], "radius_px": 22, "strength": 0.8}},
                "band": {"size_px": 72, "stroke_px": 6, "fade_in_s": 0.1},
                "title": {"z": "front", "box": [0.06, 0.08, 0.84, 0.2], "size_px": 132, "sung": False},
            },
            "lines": [
                {"layer": "back", "text": "HOLD ON TIGHT", "start": {"beat": 1, "offset_s": -0.04}, "end": {"beat": second},
                 "words": [{"text": "HOLD", "t": {"beat": 1, "offset_s": -0.04}, "row": 0},
                           {"text": " ON", "t": {"beat": 1.5, "offset_s": -0.04}, "row": 1},
                           {"text": " TIGHT", "t": {"beat": 2, "offset_s": -0.04}, "row": 2}]},
                {"layer": "back", "text": "LIGHT", "start": half, "end": {"beat": third},
                 "words": [{"text": "LI", "t": half}, {"text": "GHT", "t": {"beat": second, "offset_s": 0.25}}]},
                {"layer": "band", "text": "demo band line one", "start": {"beat": 1, "offset_s": -0.1}, "end": {"beat": second},
                 "words": [{"text": "demo", "t": {"beat": 1, "offset_s": -0.1}, "color": [205, 160, 255]},
                           {"text": " band line one", "accent": False}]},
                {"layer": "band", "text": "demo band line two", "start": half, "end": {"beat": third},
                 "style": {"color": [255, 222, 170], "glow": {"color": [255, 150, 60], "radius_px": 14, "strength": 0.55}}},
                {"layer": "title", "text": "TITLE", "start": {"beat": third}, "end": duration},
            ],
        },
    }


def _write_wav(path, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(48000)
        stream.writeframes(samples.tobytes())


def _write_spec(directory, spec):
    path = directory / "spec.json"
    path.write_text(json.dumps(spec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def create(directory, width=540, height=960, duration=6.0, fps=24, schema="0.2"):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    clip_w, clip_h = 2 * (width // 3), 2 * (height // 3)   # 素材は出力より小さい。人物の縮尺を確かめるため
    if schema == "0.1":
        half = duration / 2
        Image.fromarray(_stage(width, height)).save(directory / "stage.png")
        _write_clip(directory / "clip_a.mp4", clip_w, clip_h, half, fps, 0.0)
        _write_clip(directory / "clip_b.mp4", clip_w, clip_h, half, fps, math.pi)
        _write_wav(directory / "audio.wav", _audio(duration))
        spec_path = _write_spec(directory, _spec_v01(width, height, duration, fps))
        return {"spec": str(spec_path), "size": [width, height], "duration_s": duration, "fps": fps}
    loop = int(round(LOOP_S * fps))
    fxrender.write_video(directory / "stage.mp4", (_stage_frame(width, height, i / fps) for i in range(loop)), (width, height), fps, 12, loop)
    first, second, third = (cut * 60 / BPM for cut in _cuts(duration))
    jump = lambda length: length / 2 if length > 2 * JUMP_S else None  # noqa: E731
    shapes = [{"scale": 0.8, "x": 0.42, "foot": 0.86, "drift": 0.012, "jump": jump(first)},
              {"scale": 1.15, "x": 0.56, "foot": 0.93, "drift": -0.012, "jump": jump(second - first)},
              {"scale": 1.0, "x": 0.5, "foot": 0.9, "drift": 0.0, "jump": None}]
    lengths = [first, second - first, third - second]
    for name, shape, length, phase in zip(("clip_a", "clip_b", "clip_c"), shapes, lengths, (0.0, math.pi, math.pi / 2)):
        _write_figure(directory / f"{name}.mp4", clip_w, clip_h, length + 0.5, fps, phase, shape)
    Image.fromarray(_figure(clip_w, clip_h, 0.0, 0.0, shapes[2])).save(directory / "end.png")
    _write_wav(directory / "audio.wav", _audio(duration - 0.5))      # 音声は描画より短い。fit: pad で足す
    spec_path = _write_spec(directory, _spec(width, height, duration, fps))
    return {"spec": str(spec_path), "size": [width, height], "duration_s": duration, "fps": fps}
