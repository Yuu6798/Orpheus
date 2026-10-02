import json
import math
import os
import shutil
import tempfile
import wave
from pathlib import Path

import numpy as np

from .io import REPO, PipelineError, run
from .validate import require


def ffmpeg():
    configured = os.environ.get("ORPHEUS_FFMPEG") or shutil.which("ffmpeg")
    if configured:
        return configured
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def node():
    return os.environ.get("ORPHEUS_NODE") or shutil.which("node") or "node"


def ffprobe():
    configured = os.environ.get("ORPHEUS_FFPROBE") or shutil.which("ffprobe")
    if configured:
        return configured
    # Remotion ships a pinned platform-specific ffprobe binary.
    return run([node(), REPO / "renderer/tool-path.mjs", "ffprobe"], cwd=REPO).strip()


def normalize(source, output, start, duration):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    args = [ffmpeg(), "-y", "-i", source, "-ss", str(start)]
    if duration is not None:
        args += ["-t", str(duration)]
    args += ["-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", temporary]
    run(args, output.parent / "normalize.log")
    with wave.open(str(temporary)) as stream:
        require(stream.getnframes() > 0, "Selected audio interval is empty")
        metadata = {"sample_rate": stream.getframerate(), "sample_count": stream.getnframes(),
                    "duration_s": stream.getnframes() / stream.getframerate()}
    os.replace(temporary, output)
    return metadata


def probe(path):
    return json.loads(run([ffprobe(), "-v", "error", "-show_streams", "-show_format", "-of", "json", path]))


def verify_video(path, audio_path, render):
    info = probe(path)
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    require(len(videos) == len(audios) == 1, "Expected exactly one video and audio stream")
    video, audio = videos[0], audios[0]
    require((video["width"], video["height"]) == (render["width"], render["height"]), "Output dimensions mismatch")
    numerator, denominator = map(int, video["avg_frame_rate"].split("/"))
    require(math.isclose(numerator / denominator, render["fps"]), "Output FPS mismatch")
    require(int(video["nb_frames"]) == render["duration_frames"], "Output frame count mismatch")
    require(audio["channels"] == 2, "Output audio is not stereo")
    # AAC stores 1024-sample packets. Record the actual sample rate and tolerance.
    tolerance = 1 / render["fps"] + 1024 / int(audio["sample_rate"])
    expected = render["duration_frames"] / render["fps"]
    require(abs(float(audio["duration"]) - expected) <= tolerance, "Output audio duration mismatch")
    require(abs(float(video.get("start_time", 0))) < tolerance and abs(float(audio.get("start_time", 0))) < tolerance, "Output origin mismatch")
    run([ffmpeg(), "-v", "error", "-xerror", "-i", path, "-f", "null", "-"], Path(path).with_suffix(".decode.log"))
    with tempfile.TemporaryDirectory() as folder:
        decoded = Path(folder) / "decoded.wav"
        run([ffmpeg(), "-y", "-v", "error", "-i", path, "-vn", "-ac", "2", "-ar", "48000", "-c:a", "pcm_s16le", decoded])
        def samples(p):
            with wave.open(str(p)) as stream:
                return np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").astype(float).reshape(-1, 2) / 32768
        original, encoded = samples(audio_path), samples(decoded)
        length = min(len(original), len(encoded))
        require(abs(len(original) - len(encoded)) / 48000 <= tolerance, "Decoded audio length mismatch")
        dropouts = []
        energy_ratios = []
        for start in range(0, length, 12000):
            end = min(length, start + 12000)
            before = float(np.sqrt(np.mean(original[start:end] ** 2)))
            after = float(np.sqrt(np.mean(encoded[start:end] ** 2)))
            if before > 0.001:
                energy_ratios.append(after / before)
                if after < before * 0.05:
                    dropouts.append(start / 48000)
        require(not dropouts, f"Unexpected audio dropout relative to source: {dropouts}")
        # A zero-lag correlation tests placement without requiring lossy PCM equality.
        flat_a, flat_b = original[:length].ravel(), encoded[:length].ravel()
        norm = float(np.linalg.norm(flat_a) * np.linalg.norm(flat_b))
        correlation = float(np.dot(flat_a, flat_b) / norm) if norm > 1e-8 else None
        require(correlation is None or correlation > 0.85, f"Audio mismatch or delay: correlation={correlation}")
    return {"technical_pass": True, "accepted": False, "human_review": "pending", "streams": info["streams"],
            "duration_tolerance_s": tolerance, "audio_correlation": correlation,
            "dropout_times_s": dropouts, "min_energy_ratio": min(energy_ratios, default=None)}
