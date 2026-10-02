from importlib.metadata import version

from ..io import PipelineError


def analyze_audio(path):
    try:
        import librosa
        import numpy as np
    except ImportError as error:
        raise PipelineError("Install the analysis extra: uv sync --extra analysis") from error
    y, sr = librosa.load(path, sr=22050, mono=True)
    duration = len(y) / sr
    if np.max(np.abs(y)) < 1e-8:
        tempo, beats = None, []
    else:
        tempo_array, indices = librosa.beat.beat_track(y=y, sr=sr, hop_length=512)
        tempo = float(np.asarray(tempo_array).reshape(-1)[0])
        tempo = tempo if tempo > 0 else None
        beats = [float(t) for t in librosa.frames_to_time(indices, sr=sr, hop_length=512) if t < duration]
    rms = librosa.feature.rms(y=y, hop_length=512)[0]
    loudness = [{"time_s": float(i * 512 / sr), "rms": float(value)} for i, value in enumerate(rms) if i * 512 / sr < duration]
    return {"bpm": tempo, "beats_s": beats, "loudness": loudness, "version": version("librosa"),
            "note": "Beat estimates only. No semantic chorus/verse labels inferred."}
