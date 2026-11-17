"""fx-timeline のデモ（mv fx timeline demo）: 合成の素材と、3 つのショットと 1 つの動画のショットをつなぐ timeline。
mv_pipeline.fx.sequence から以前の名前で読める。"""
import json
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, read_json
from .sequence_spec import SCHEMA_V01


def _demo_clip(path, base_path, seconds=3.0, fps=16):
    """デモの動画（合成）: デモの絵の上を、明るい光の玉が左から右へ横切り、明滅する。BT.709 のタグつき・音なし。"""
    from PIL import Image

    from . import render
    base = np.asarray(Image.open(base_path).convert("RGB")).astype(np.float32)
    height, width = base.shape[:2]
    ys, xs = np.mgrid[0:height, 0:width].astype(np.float32)
    count = int(round(seconds * fps))

    def frames():
        for k in range(count):
            u = k / max(count - 1, 1)
            cx, cy = width * (0.15 + 0.7 * u), height * (0.35 + 0.08 * math.sin(6.0 * u))
            glow = np.exp(-((xs - cx) ** 2 + (ys - cy) ** 2) / (2 * (0.05 * width) ** 2)) * (0.7 + 0.3 * math.sin(9.0 * u))
            yield np.clip(base + glow[..., None] * np.float32([255, 210, 150]), 0, 255).astype(np.uint8)

    render.write_video(path, frames(), (width, height), fps, 18, count)
    return count


HEAD = [0.42, 0.2, 0.49, 0.3]       # デモの人物の頭の枠（元画像の正規化座標。人物のマスクの上端の帯から目で決めた）


def demo(directory, width=320, height=180, duration=6.0, fps=24):
    """fx のデモ素材から 3 つの spec のショット（寄り・横移動・引き）と 1 つの動画のショット（合成の動画をループ）を作り、
    cut 以外の 0.1 のつなぎ（black・xfade・flash）を全部使う fx-timeline の見本を書く。0.1 の項目だけを使うので、版は 0.1 のまま書く
    （0.2 の burn は入れない。デモの timeline と絵を変えないため）。"""
    if duration < 3:
        raise PipelineError(f"timeline のデモの長さは 3 秒以上にしてください（つなぎの長さが 0.3〜0.5 秒のため）: {duration:g}")
    from . import demo as fxdemo

    directory = Path(directory)
    shots_dir = directory / "shots"
    fxdemo.create(shots_dir, width, height, duration, fps)
    spec = read_json(shots_dir / "spec.json")
    cameras = {"shot_a.json": [{"t": 0, "zoom": 1.1, "center": [0.5, 0.5]}, {"t": duration, "zoom": 1.3, "center": [0.47, 0.47], "ease": "in_out"}],
               "shot_b.json": [{"t": 0, "zoom": 1.35, "center": [0.4, 0.5]}, {"t": duration, "zoom": 1.35, "center": [0.6, 0.5], "ease": "in_out"}],
               "shot_c.json": [{"t": 0, "zoom": 1.4, "center": [0.5, 0.55]}, {"t": duration, "zoom": 1.1, "center": [0.5, 0.5], "ease": "in_out"}]}
    for name, keys in cameras.items():
        shot = {**spec, "camera": {**spec["camera"], "keys": keys}}
        if name == "shot_a.json":
            shot["faces"] = [{"box": HEAD}]          # 顔の枠（目で測った推定）。run が faces.json に写す
        (shots_dir / name).write_text(json.dumps(shot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    frames = _demo_clip(shots_dir / "clip.mp4", shots_dir / "base.png")
    beat = 0.5                                   # 120 BPM
    timeline = {"schema_version": SCHEMA_V01, "output": {"width": width, "height": height, "fps": fps, "crf": 18},
                "duration_s": duration, "beats": {"bpm": 120, "count": int(duration / beat) + 4},
                "audio": {"path": "shots/audio.wav"},
                "look": {"vignette": {"amount": 0.3}, "grain": {"sigma": 1.5}},
                "shots": [{"spec": "shots/shot_a.json", "start": 0, "transition_in": {"type": "black", "duration_s": 0.5}},
                          {"spec": "shots/shot_b.json", "start": {"beat": round(duration / beat * 0.3)}, "offset_s": 1.0,
                           "transition_in": {"type": "xfade", "duration_s": 0.4}},
                          {"spec": "shots/shot_c.json", "start": round(duration * 0.62, 3), "snap": True, "transition_in": {"type": "flash"}},
                          {"video": {"path": "shots/clip.mp4", "speed": 0.8, "loop": {"frames": [4, frames - 8], "crossfade_frames": 6, "mix": 0.5}},
                           "camera": {"keys": [{"t": 0, "zoom": 1.05, "center": [0.5, 0.5]}, {"t": 2.0, "zoom": 1.2, "center": [0.55, 0.45], "ease": "in_out"}]},
                           "faces": [{"box": HEAD}],
                           "start": round(duration * 0.8, 3), "snap": True, "transition_in": {"type": "xfade", "duration_s": 0.3}}]}
    path = directory / "timeline.json"
    path.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"timeline": str(path), "shots": [str(shots_dir / name) for name in cameras] + [str(shots_dir / "clip.mp4")],
            "size": [width, height], "duration_s": duration}
