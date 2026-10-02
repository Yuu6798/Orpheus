"""Generate deterministic test signals and geometric fixtures, not artwork."""
import math
import wave
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from . import pipeline
from .io import atomic_json, read_json, sha256


def create(root, width=1920, height=1080):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    fixture = root / "work/fixture"
    fixture.mkdir(parents=True, exist_ok=True)
    sr, seconds = 48000, 6
    t = np.arange(sr * seconds) / sr
    # Continuous chords and a pulse allow audio placement/dropout checks.
    envelope = 0.35 + 0.65 * np.exp(-18 * (t % 0.5))
    left = 0.11 * envelope * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 330 * t))
    right = 0.11 * envelope * (np.sin(2 * np.pi * 220 * t) + 0.5 * np.sin(2 * np.pi * 440 * t))
    pcm = (np.stack([left, right], axis=1) * 32767).astype('<i2')
    audio = fixture / "test-signal.wav"
    with wave.open(str(audio), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(sr)
        stream.writeframes(pcm.tobytes())
    lyrics = fixture / "lyrics.txt"
    lyrics.write_text("窓の向こうへ\n声をつないで\n", encoding="utf-8")
    pipeline.initialize(root, audio, lyrics, duration=seconds, width=width, height=height)
    a = read_json(root / "analysis.json")
    edits = {"audio_sha256": a["audio"]["sha256"], "lyrics": [
        {"id": "lyric-001", "text": "窓の向こうへ", "start_s": 0.75, "end_s": 3.4},
        {"id": "lyric-002", "text": "声をつないで", "start_s": 3.4, "end_s": 5.6}]}
    atomic_json(fixture / "edits.json", edits)
    pipeline.import_edits(root, fixture / "edits.json")
    assets = []
    for i, colors in enumerate([((16, 29, 59), (81, 119, 151)), ((37, 32, 53), (239, 159, 100))], 1):
        image = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(image)
        for y in range(height):
            p = y / height
            draw.line([(0, y), (width, y)], fill=tuple(int(a * (1-p) + b * p) for a, b in zip(*colors)))
        draw.ellipse((int(width*.61), int(height*.17), int(width*.73), int(height*.17+width*.12)), fill=(246, 215, 151))
        for n in range(12):
            x = int(n * width / 11)
            top = int(height * (.57 + .12 * math.sin(n * 2.3)))
            draw.rectangle((x, top, x + int(width*.085), height), fill=(13, 21, 39))
        file = root / "images" / f"fixture-{i}.png"
        image.save(file)
        assets.append({"asset_id": f"image-{i:03d}", "path": file.relative_to(root).as_posix(), "sha256": sha256(file),
                       "width": width, "height": height, "source": "imported", "prompt": None,
                       "reference_asset_ids": [], "generator": None, "generation_params": {}})
    atomic_json(fixture / "assets.json", {"schema_version": "0.1", "assets": assets})
    pipeline.import_assets(root, fixture / "assets.json")
    a = read_json(root / "analysis.json")
    story = {"schema_version": "0.1", "storyboard_id": "synthetic-smoke", "analysis_id": a["analysis_id"],
             "analysis_sha256": sha256(root / "analysis.json"), "audio_sha256": a["audio"]["sha256"],
             "render": {"fps": 30, "width": width, "height": height, "duration_frames": 180, "show_lyrics": True},
             "world": {"description": "Synthetic geometric test fixtures. Not a real song or finished MV.", "reference_asset_id": None},
             "scenes": [{"id": f"scene-{i:03d}", "start_frame": (i-1)*90, "end_frame": i*90,
                         "lyric_ids": [f"lyric-{i:03d}"], "asset_id": f"image-{i:03d}", "prompt": "",
                         "motion": motion, "transition_in": {"type": "cut" if i == 1 else "crossfade", "duration_frames": 0 if i == 1 else 12},
                         "intent": "Test motion and globally timed captions across a scene boundary"}
                        for i, motion in enumerate(["slow_push", "pan_right"], 1)]}
    atomic_json(fixture / "storyboard.json", story)
    pipeline.import_storyboard(root, fixture / "storyboard.json")
    return root
