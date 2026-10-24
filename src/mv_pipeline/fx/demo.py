"""fx の合成デモ素材: 暗い夜景に、白い炎を掲げた人物。素材はすべてプログラムで描く（実作品の素材は一切使わない）。

`mv fx demo DIR` が DIR に次を書く。素材は固定の乱数だけで作るので、同じ引数なら同じ内容になる。

    base.png         基準画像（出力の 2 倍の解像度。空・遠景の丘と木・地面・人物。光は当たっていない）
    depth.png        深度（16bit。白 = 手前。空が最奥、地面は下ほど手前、人物は中間）
    mask_subject.png 人物のマスク   mask_ground.png 地面のマスク
    audio.wav        合成音（低い持続音 + 点火の風切り音 + 脈動。尺 = duration + 1 秒）
    spec.json        上の素材を使う fx-shot/0.1 の設定

演出が目で分かる値にしてある:
- 白い炎は手の上にあり（emitter.attach = subject。手と一緒に動き、袖に隠れない）、光は対象物ごとに違う当たり方をする。
  手・袖・顔・肩の炎側が明るく、人物の反対側や遠い裾は暗いまま。輪郭光が炎側の縁に出る。
  地面は炎の真下に縦長の反射が出るだけで、霧の塊のようには広がらない。
- 霧は人物の後ろ / 火の粉は人物の前 / カメラはゆっくり寄る。
"""
import math
import os
import tempfile
import wave
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw

from ..io import PipelineError, atomic_json
from . import SCHEMA_VERSION

SUPER = 2                 # 基準画像は出力の 2 倍（zoom しても拡大にならない）
SEED = 7
HORIZON = 0.66            # 地面の上端（画像の高さに対する割合）
FIGURE_X = 0.45           # 人物の中心（画像の幅に対する割合）
FLAME = (0.61, 0.43)      # 白い炎の位置（正規化座標）
REVEAL = 0.045            # 地面マスクが人物の輪郭から内側へ落ちきるまでの距離（高さに対する割合。視差でのぞく穴埋め板の幅より大きく）
HORIZON_FADE = 0.07       # 人物の内側の帯を、地平線から下へ 0 → 1 に立ち上げる距離（高さに対する割合。地平線で段差を作らない）


def _save(path, writer):
    """一時名に書いて置き換える。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp" + path.suffix)
    os.close(fd)
    try:
        writer(temporary)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _noise(rng, shape, sigma):
    """sigma 画素でぼかした平均 0・標準偏差 1 の雑音。"""
    field = cv2.GaussianBlur(rng.standard_normal(shape).astype(np.float32), (0, 0), sigma)
    return field / max(float(field.std()), 1e-6)


def _sky_and_ground(width, height, rng):
    """空の勾配・月・星と、質感のある地面を float32 (H, W, 3) の sRGB 0-255 で描く。"""
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    xs = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    top, low = np.array([5, 8, 22], np.float32), np.array([44, 56, 92], np.float32)
    mix = np.clip(ys / HORIZON, 0, 1) ** 1.5
    image = top + (low - top) * mix[..., None] + np.zeros((1, width, 1), np.float32)
    aspect = width / height
    moon = np.hypot((xs - 0.17) * aspect, ys - 0.17)
    image += (np.exp(-(moon / 0.16) ** 2) * 22)[..., None] * np.array([0.7, 0.8, 1.0], np.float32)
    image += ((moon < 0.032) * 120.0)[..., None] * np.array([0.85, 0.9, 1.0], np.float32)
    for _ in range(int(160 * width / 1920 * 2)):  # 星
        x, y = int(rng.random() * width), int(rng.random() ** 1.5 * height * HORIZON * 0.9)
        image[y, x] += 40 + 90 * rng.random()
    horizon_y = int(HORIZON * height)
    ground = np.zeros((height - horizon_y, width, 3), np.float32)
    depth01 = np.linspace(0, 1, ground.shape[0], dtype=np.float32)[:, None, None]
    ground[:] = np.array([38, 38, 50], np.float32) * (1 - depth01) + np.array([24, 23, 30], np.float32) * depth01
    grain = _noise(rng, ground.shape[:2], 1.2) * 0.5 + _noise(rng, ground.shape[:2], 7) * 0.9 + _noise(rng, ground.shape[:2], 28) * 0.6
    ground *= (1 + 0.11 * grain)[..., None]
    for _ in range(int(900 * width / 1920)):  # 小石
        x, y = int(rng.random() * width), int(rng.random() * ground.shape[0])
        ground[max(0, y - 1):y + 2, max(0, x - 1):x + 2] += 8 + 22 * rng.random()
    image[horizon_y:] = ground
    return image


def _silhouettes(image, width, height, rng):
    """遠景の丘と針葉樹の影を image(PIL) に描く。"""
    draw = ImageDraw.Draw(image)
    xs = np.linspace(0, 1, 160)
    ridge = 0.585 + 0.022 * np.sin(2 * math.pi * (xs * 1.3 + 0.2)) + 0.011 * np.sin(2 * math.pi * xs * 3.1)
    draw.polygon([(0, height * HORIZON)] + [(x * width, y * height) for x, y in zip(xs, ridge)] + [(width, height * HORIZON)],
                 fill=(14, 19, 34))
    x = -0.02
    while x < 1.04:
        h = (0.035 + 0.045 * rng.random() ** 2) * height
        w = h * (0.28 + 0.1 * rng.random())
        base = HORIZON * height + 2
        cx = x * width
        draw.polygon([(cx - w, base), (cx, base - h), (cx + w, base)], fill=(9, 13, 24))
        draw.polygon([(cx - w * 0.7, base - h * 0.35), (cx, base - h * 1.12), (cx + w * 0.7, base - h * 0.35)], fill=(9, 13, 24))
        x += (0.006 + 0.03 * rng.random()) * (0.4 if 0.2 < x < 0.9 else 1.0)


def _figure(width, height, flame_xy):
    """人物の RGBA（白い炎へ片腕を差し出す、フードの付いたマントの人影）。顔は右（炎の側）を向く。"""
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    h = float(height)
    cx = FIGURE_X * width

    def pt(dx, y):
        return (cx + dx * h, y * h)

    def poly(points, fill):
        draw.polygon([pt(*p) for p in points], fill=fill)

    cloak, shadow, trim = (40, 45, 70, 255), (28, 31, 50, 255), (58, 66, 98, 255)
    skin, skin_dark, hair = (112, 88, 80, 255), (84, 64, 60, 255), (17, 15, 22, 255)
    # 胴体: 肩から裾へ広がるマント
    poly([(-0.078, 0.360), (-0.030, 0.338), (0.034, 0.338), (0.074, 0.362), (0.092, 0.50), (0.118, 0.66),
          (0.150, 0.80), (0.162, 0.835), (-0.162, 0.835), (-0.145, 0.78), (-0.112, 0.62), (-0.088, 0.47)], cloak)
    left_edge = ([0.36, 0.47, 0.62, 0.78, 0.835], [-0.078, -0.088, -0.112, -0.145, -0.162])
    right_edge = ([0.362, 0.50, 0.66, 0.80, 0.835], [0.074, 0.092, 0.118, 0.150, 0.162])

    def fold(fraction, color, width):  # 衣の左端から右端までの位置 fraction (0..1) に沿う線
        points = []
        for y in (0.43, 0.83):
            lo, hi = np.interp(y, *left_edge), np.interp(y, *right_edge)
            points.append(pt(lo + (hi - lo) * fraction, y))
        draw.line(points, fill=color, width=width)

    for fraction in (0.12, 0.30, 0.50, 0.70, 0.88):  # 衣のひだ
        fold(fraction, shadow, max(2, int(0.007 * h)))
    for fraction in (0.21, 0.40, 0.60, 0.79):
        fold(fraction, trim, max(1, int(0.004 * h)))
    poly([(-0.088, 0.495), (0.098, 0.495), (0.100, 0.512), (-0.090, 0.512)], (66, 54, 48, 255))  # 帯
    draw.rectangle([*pt(0.018, 0.493), *pt(0.036, 0.514)], fill=(150, 130, 90, 255))
    # 首・襟
    poly([(-0.010, 0.300), (0.026, 0.300), (0.030, 0.350), (-0.016, 0.350)], skin_dark)
    poly([(-0.052, 0.352), (-0.012, 0.330), (0.034, 0.332), (0.068, 0.356), (0.040, 0.380), (-0.030, 0.380)], trim)
    # 頭: 髪の上に顔を重ね、右向きにする
    draw.ellipse([*pt(-0.036, 0.222), *pt(0.040, 0.322)], fill=hair)
    poly([(-0.040, 0.26), (-0.062, 0.34), (-0.040, 0.372), (-0.012, 0.31)], hair)  # 後ろ髪
    draw.ellipse([*pt(-0.004, 0.236), *pt(0.040, 0.320)], fill=skin)
    poly([(0.036, 0.268), (0.052, 0.286), (0.036, 0.294)], skin)  # 鼻
    poly([(-0.008, 0.226), (0.034, 0.236), (0.040, 0.262), (0.012, 0.250), (-0.010, 0.252)], hair)  # 前髪
    draw.ellipse([*pt(0.022, 0.2635), *pt(0.031, 0.2715)], fill=(24, 18, 20, 255))  # 目
    draw.line([pt(0.017, 0.256), pt(0.034, 0.2575)], fill=hair, width=max(1, int(0.003 * h)))  # 眉
    draw.line([pt(0.020, 0.3), pt(0.033, 0.2995)], fill=(70, 40, 44, 255), width=max(1, int(0.0035 * h)))  # 口
    # 炎へ差し出す腕: 肩 -> 肘 -> 手首。袖は先へ広がる
    fx, fy = flame_xy
    wrist = (fx - 0.030 * h, fy + 0.074 * h)
    shoulder, elbow = pt(0.062, 0.385), pt(0.138, 0.468)
    for a, b, w in ((shoulder, elbow, 0.052), (elbow, wrist, 0.058)):
        draw.line([a, b], fill=cloak, width=int(w * h))
    for p, w in ((shoulder, 0.052), (elbow, 0.058)):
        draw.ellipse([p[0] - w * h / 2, p[1] - w * h / 2, p[0] + w * h / 2, p[1] + w * h / 2], fill=cloak)
    ex, ey = wrist[0] - elbow[0], wrist[1] - elbow[1]
    norm = math.hypot(ex, ey)
    nx, ny = -ey / norm, ex / norm
    mid = (elbow[0] + ex * 0.45, elbow[1] + ey * 0.45)
    draw.polygon([(mid[0] + nx * 0.030 * h, mid[1] + ny * 0.030 * h), (wrist[0] + nx * 0.040 * h, wrist[1] + ny * 0.040 * h),
                  (wrist[0] - nx * 0.040 * h, wrist[1] - ny * 0.040 * h), (mid[0] - nx * 0.030 * h, mid[1] - ny * 0.030 * h)], fill=trim)
    draw.line([(wrist[0] + nx * 0.040 * h, wrist[1] + ny * 0.040 * h), (wrist[0] - nx * 0.040 * h, wrist[1] - ny * 0.040 * h)],
              fill=(86, 78, 64, 255), width=max(2, int(0.006 * h)))  # 袖口の縁
    # 手: 手首から上へ、炎を受ける形
    palm = (wrist[0] + 0.028 * h, wrist[1] + 0.001 * h)
    draw.ellipse([palm[0] - 0.022 * h, palm[1] - 0.012 * h, palm[0] + 0.024 * h, palm[1] + 0.016 * h], fill=skin)
    for i, lift in enumerate((0.030, 0.036, 0.032)):  # 指
        fx0 = palm[0] - 0.014 * h + i * 0.014 * h
        draw.rounded_rectangle([fx0, palm[1] - lift * h, fx0 + 0.008 * h, palm[1] + 0.002 * h], radius=int(0.004 * h), fill=skin)
    return layer


def _scene(width, height):
    """(base RGB uint8, subject mask uint8, ground mask uint8, depth uint16)。すべて width×height。"""
    rng = np.random.default_rng(SEED)
    image = _sky_and_ground(width, height, rng)
    canvas = Image.fromarray(np.clip(image, 0, 255).astype(np.uint8))
    _silhouettes(canvas, width, height, rng)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).ellipse([FIGURE_X * width - 0.19 * height, 0.822 * height, FIGURE_X * width + 0.19 * height,
                                    0.858 * height], fill=(8, 8, 12, 150))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), shadow)
    flame_xy = (FLAME[0] * width, FLAME[1] * height)
    figure = _figure(width, height, flame_xy)
    base = Image.alpha_composite(canvas, figure).convert("RGB")
    subject = np.asarray(figure)[..., 3]
    subject_mask = np.where(subject > 0, 255, 0).astype(np.uint8)
    horizon_y = int(HORIZON * height)
    # 地面マスク: 地平線より下。人物の体の中央には載せない（光の版で人物が二重に照らされないように）が、
    # 視差で人物の背後の穴埋め板がのぞく分（REVEAL）は地面として照らしたいので、人物の輪郭から内側へ
    # 距離に応じて 1 → 0 に落とす（硬い境目を作らない）。人物の内側の帯は地平線付近でも縦方向に
    # なだらかに（smoothstep で 0 → 1）立ち上げ、受光マスクと plane マスクが重なる light 版（レイヤーなし）で
    # 裾に白い面や水平の段差が出ないようにする。
    reach = max(2.0, REVEAL * height)
    inside = cv2.distanceTransform(subject_mask, cv2.DIST_L2, 5)
    band = np.clip(1.0 - inside / reach, 0.0, 1.0)
    rise = np.clip((np.arange(height, dtype=np.float32)[:, None] - horizon_y) / max(2.0, HORIZON_FADE * height), 0.0, 1.0)
    band = band * rise * rise * (3.0 - 2.0 * rise)
    ramp = np.where(subject_mask > 0, band, 1.0).astype(np.float32)
    ground_mask = np.zeros((height, width), np.uint8)
    ground_mask[horizon_y:] = np.rint(255 * ramp[horizon_y:]).astype(np.uint8)
    ys = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    depth = 0.05 + 0.10 * np.clip(ys / HORIZON, 0, 1)                  # 空 -> 遠景
    near = np.clip((ys - HORIZON) / (1 - HORIZON), 0, 1) ** 0.8         # 地面: 地平線 0.20 -> 最前面 1.0
    depth = np.where(ys >= HORIZON, 0.20 + 0.80 * near, depth) + np.zeros((1, width), np.float32)
    figure_depth = 0.62 + 0.06 * np.clip((ys - 0.2) / 0.65, 0, 1) + np.zeros((1, width), np.float32)
    depth = np.where(subject_mask > 0, figure_depth, depth)
    return np.asarray(base), subject_mask, ground_mask, np.round(np.clip(depth, 0, 1) * 65535).astype(np.uint16)


def _audio(seconds, rate=48000):
    """低い持続音・空気の雑音・点火の風切り・ゆっくりした脈動。ステレオ int16。決定論。"""
    rng = np.random.default_rng(SEED)
    n = int(seconds * rate)
    t = np.arange(n) / rate
    drone = 0.10 * np.sin(2 * math.pi * 55 * t) + 0.06 * np.sin(2 * math.pi * 82.4 * t + 0.7) * (0.7 + 0.3 * np.sin(2 * math.pi * 0.13 * t))
    spectrum = np.fft.rfft(rng.standard_normal(n))
    freqs = np.fft.rfftfreq(n, 1 / rate)
    air = np.fft.irfft(spectrum * (1.0 / (1.0 + (freqs / 350.0) ** 2)), n)
    air = 0.05 * air / max(float(np.abs(air).max()), 1e-6)
    ignite = np.exp(-((t - 0.8) / 0.45) ** 2)
    whoosh = 0.22 * ignite * np.fft.irfft(spectrum * (freqs / (freqs + 900.0)) * (1.0 / (1.0 + (freqs / 3500.0) ** 2)), n)
    whoosh /= max(float(np.abs(whoosh).max()) / 0.22, 1e-6)
    pulse = 0.07 * np.sin(2 * math.pi * 49 * t) * np.clip(np.sin(2 * math.pi * 0.9 * t), 0, 1) ** 6
    left = drone + air + whoosh + pulse
    right = drone * 0.95 + np.roll(air, 53) + whoosh * 0.9 + pulse
    stereo = np.stack([left, right], axis=1)
    stereo *= 0.8 / max(float(np.abs(stereo).max()), 1e-6)
    return (stereo * 32767).astype("<i2"), rate


def _spec(width, height, duration, fps):
    intro = round(min(1.0, duration / 4), 3)
    return {
        "schema_version": SCHEMA_VERSION,
        "output": {"width": width, "height": height, "fps": fps, "crf": 18},
        "duration_s": duration,
        "seed": SEED,
        "audio": {"path": "audio.wav", "offset_s": 0.0},
        "assets": {
            "base": "base.png",
            "depth": {"path": "depth.png", "near": "white"},
            "masks": {"subject": {"path": "mask_subject.png", "feather_px": 3},
                      "ground": {"path": "mask_ground.png", "feather_px": 6}},
        },
        "camera": {
            "keys": [{"t": 0.0, "zoom": 1.0, "center": [0.5, 0.5]},
                     {"t": duration, "zoom": 1.08, "center": [0.52, 0.48], "ease": "in_out"}],
            "parallax_px": 12, "parallax_dir": [1, 0],
        },
        "layers": {
            "background": {"parallax": 0.4, "inpaint_px": 8},
            "planes": [{"id": "subject", "mask": "subject", "z": 2, "parallax": 1.0, "drift_px": [0, 0]}],
            "effects": [
                {"id": "fog", "type": "fog", "z": 1, "color": [110, 120, 160], "opacity": 0.25, "scale": 0.25,
                 "drift": [0.01, 0.0], "parallax": 0.6},
                {"id": "embers", "type": "particles", "z": 3, "count": 120, "color": [255, 170, 80],
                 "size_px": [3.0, 7.0], "velocity": [0.0, -0.08], "flicker": 0.4, "parallax": 1.3},
            ],
        },
        "lights": [{
            "id": "white_flame", "type": "point", "position": list(FLAME), "color": [230, 236, 255], "radius": 0.30,
            "intensity": [{"t": 0.0, "v": 0.0}, {"t": intro, "v": 1.2}, {"t": duration, "v": 0.9}],
            "flicker": {"amount": 0.12, "hz": 5.0},
            "emitter": {"size": 0.03, "glow": 0.6, "attach": "subject"},
            "receivers": [{"mask": "subject", "gain": 2.2, "rim": 4.0}, {"mask": "ground", "gain": 0.35, "reflection": 0.8}],
            "depth": 0.7, "depth_falloff": 0.45,
        }],
    }


def create(directory, width=960, height=540, duration=10.0, fps=24):
    """DIR にデモ素材と spec.json を作る。戻り値は CLI が stdout に出す要約。"""
    if width < 160 or height < 90 or width % 2 or height % 2:
        raise PipelineError(f"--width / --height は偶数で 160x90 以上にしてください: {width}x{height}")
    if not 0.5 <= duration <= 600 or not 1 <= fps <= 120:
        raise PipelineError(f"--duration は 0.5〜600 秒、--fps は 1〜120 にしてください: {duration}, {fps}")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    base, subject, ground, depth = _scene(width * SUPER, height * SUPER)
    _save(directory / "base.png", lambda p: Image.fromarray(base).save(p, format="PNG"))
    _save(directory / "mask_subject.png", lambda p: Image.fromarray(subject).save(p, format="PNG"))
    _save(directory / "mask_ground.png", lambda p: Image.fromarray(ground).save(p, format="PNG"))
    _save(directory / "depth.png", lambda p: Image.fromarray(depth).save(p, format="PNG"))
    samples, rate = _audio(duration + 1.0)

    def write_wav(path):
        with wave.open(path, "wb") as stream:
            stream.setnchannels(2)
            stream.setsampwidth(2)
            stream.setframerate(rate)
            stream.writeframes(samples.tobytes())

    _save(directory / "audio.wav", write_wav)
    spec = _spec(width, height, float(duration), float(fps))
    atomic_json(directory / "spec.json", spec)  # 最後に書く: spec.json があれば素材は揃っている
    return {"spec": str((directory / "spec.json").resolve()), "size": [width, height], "duration_s": float(duration),
            "fps": float(fps), "files": ["base.png", "depth.png", "mask_subject.png", "mask_ground.png", "audio.wav", "spec.json"],
            "next": f"mv fx render {directory / 'spec.json'}"}
