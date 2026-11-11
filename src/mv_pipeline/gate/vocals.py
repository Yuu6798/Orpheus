"""分離ボーカルの数字（`mv gate voice` / `mv gate notes` / `mv gate repeat`）。歌詞の行の頭・終わり・息継ぎや、同じ旋律の
繰り返しを探すための数字を出す。手順は実制作の歌詞の時刻の台本と同じ。値は推定で、良し悪しは判定しない（確定はユーザーの試聴）。

    mv gate voice  VOCALS [--from A] [--to B] [--threshold-db -32] [--gap-s 0.14] [--min-s 0.08] [--out FILE]
    mv gate notes  VOCALS --from A --to B [--out FILE]
    mv gate repeat VOCALS --ref A B --search C D [--top 3] [--include-end] [--out FILE]

- voice（実制作の声の区間の台本）: 元のサンプリング周波数のまま読み、左右を平均したモノの [A, B) を、librosa の rms（窓 30 ms・
  hop 10 ms。窓の中央がそのコマの時刻）で dB にして、95 パーセンタイルを 0 dB とする。threshold を超えるコマの続きを区間にし、
  前の区間との間が gap 秒未満ならつなぎ、min 秒未満の区間は捨てる。コマの時刻は A + コマ × hop の標本数 ÷ 周波数
  （0.01 秒がちょうどの標本数になる 44100・48000・16000 Hz などでは、実制作の台本の A + コマ × 0.01 と同じ値）。
- notes（実制作の歌詞の時刻の台本の notes）: [A, B) を 16000 Hz に変換し、pYIN（150〜1000 Hz、窓 1024・hop 160 = 10 ms）の音程を
  半音に丸め、同じ半音が 7 コマ（70 ms）以上続く所を 1 音とする。立ち上がり（onset_detect、delta 0.12・間隔 6 コマ）と、
  音量（rms 窓 640・hop 160）が 95 パーセンタイルから −14 dB 未満のまま 6 コマ（60 ms）以上続く谷も出す。
- repeat（同じ台本の match）: 基準の区間の音程の並びを、探す区間の中でずらしながら比べる。両方に音程があるコマが基準の 30% 以上
  あるずらし方だけを数え、半音の差（12 半音の差は折り返す）が 1 未満のコマの割合（一致率。小数 3 桁）を出す。
  ずれ = 探す区間の頭 + ずらしたコマ × 0.01 − 基準の区間の頭（秒、2 桁）。一致率の高い順（同じなら、ずれの大きい順）。
  実制作の目安: 一致率 0.9 前後なら確度が高く、0.6 前後は補助。
  既定は実制作の台本と同じく、探す区間の終わりにぴったり合うずらし方（最後の 1 つ）は比べない。include_end（`--include-end`）なら比べる。
"""
import math
from pathlib import Path

import numpy as np

from ..io import PipelineError, atomic_json, sha256

VOICE_HOP_S, VOICE_FRAME_S = 0.01, 0.03
VOICE_THRESHOLD_DB, VOICE_GAP_S, VOICE_MIN_S = -32.0, 0.14, 0.08
NOTE_RATE, NOTE_HOP, NOTE_FRAME, NOTE_FMIN, NOTE_FMAX = 16000, 160, 1024, 150, 1000
NOTE_STEP_S = NOTE_HOP / NOTE_RATE                  # 0.01
NOTE_MIN_FRAMES = 7
ONSET_DELTA, ONSET_WAIT = 0.12, 6
DIP_FRAME, DIP_DB, DIP_MIN_FRAMES = 640, -14.0, 6
MATCH_OVERLAP, MATCH_SEMITONES, MATCH_TOP = 0.3, 1.0, 3
MIN_RANGE_S = 0.1


def _librosa():
    try:
        import librosa
    except ImportError as error:
        raise PipelineError("mv gate voice・notes・repeat には librosa が要ります。`uv sync --locked --extra analysis` を実行してください") from error
    return librosa


def _soundfile(path):
    import soundfile
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"ボーカルが見つかりません: {path}")
    try:
        info = soundfile.info(str(path))
    except RuntimeError as error:
        raise PipelineError(f"ボーカルを読めません（{path}）: {error}") from error
    return soundfile, info


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise PipelineError(f"{name} は数にしてください: {value!r}")
    return float(value)


def _range(start, stop, duration, name):
    """[start, stop) を確かめる（stop が None か、ボーカルより後なら、ボーカルの終わり）。"""
    start = _number(start, f"{name} の始め")
    stop = duration if stop is None else min(_number(stop, f"{name} の終わり"), duration)
    if start >= duration:
        raise PipelineError(f"{name} の始め（{start:g} 秒）がボーカルの長さ（{duration:.3f} 秒）より後です")
    if start < 0 or stop - start < MIN_RANGE_S:
        raise PipelineError(f"{name} は 0 秒以上で、{MIN_RANGE_S:g} 秒より長くしてください（{start:g}〜{stop:g} 秒）")
    return start, stop


def _vocals(path, info):
    return {"path": Path(path).name, "sha256": sha256(path), "sample_rate": int(info.samplerate), "channels": int(info.channels),
            "duration_s": info.frames / info.samplerate}


# ---------------------------------------------------------------- voice（声の出ている区間）
def activity(path, start=0.0, stop=None, threshold_db=VOICE_THRESHOLD_DB):
    """(dB の列, 声のあるコマ, コマの秒)。実制作の声の区間の台本の activity と同じ値（コマの秒は hop の標本数 ÷ 周波数）。"""
    soundfile, _ = _soundfile(path)
    librosa = _librosa()
    y, sr = soundfile.read(str(path), dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)
    if stop is None:
        stop = len(y) / sr
    y = y[int(start * sr):int(stop * sr)]
    hop = int(sr * VOICE_HOP_S)
    r = librosa.feature.rms(y=y, frame_length=int(sr * VOICE_FRAME_S), hop_length=hop)[0]
    e = 20 * np.log10(r + 1e-7)
    e -= np.percentile(e, 95)
    return e, e > threshold_db, hop / sr


def segments(on, step, start, gap=VOICE_GAP_S, minimum=VOICE_MIN_S):
    """声のあるコマの続きを [頭, 終わり] の区間にし、間が gap 未満ならつなぎ、minimum 未満を捨てる（実制作の台本と同じ）。"""
    runs, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            runs.append([start + i * step, start + j * step])
            i = j
        else:
            i += 1
    out = []
    for run in runs:
        if out and run[0] - out[-1][1] < gap:
            out[-1][1] = run[1]
        else:
            out.append(run)
    return [run for run in out if run[1] - run[0] >= minimum]


def voice(path, start=0.0, stop=None, threshold_db=VOICE_THRESHOLD_DB, gap_s=VOICE_GAP_S, min_s=VOICE_MIN_S):
    """声の出ている区間の記録（gate-voice/0.1）。"""
    _, info = _soundfile(path)
    start, stop = _range(start, stop, info.frames / info.samplerate, "--from・--to")
    threshold_db = _number(threshold_db, "--threshold-db")
    gap_s, min_s = _number(gap_s, "--gap-s"), _number(min_s, "--min-s")
    if gap_s < 0 or min_s < 0:
        raise PipelineError(f"--gap-s と --min-s は 0 以上にしてください（{gap_s:g}, {min_s:g}）")
    _, on, step = activity(path, start, stop, threshold_db)
    found, rows, previous = segments(on, step, start, gap_s, min_s), [], None
    for begin, end in found:
        rows.append({"start": begin, "end": end, "length_s": end - begin, "gap_before_s": None if previous is None else begin - previous})
        previous = end
    return {"schema_version": "gate-voice/0.1", "estimated": True, "vocals": _vocals(path, info), "range_s": [start, stop],
            "settings": {"frame_s": VOICE_FRAME_S, "hop_s": VOICE_HOP_S, "step_s": step, "threshold_db": threshold_db,
                         "reference": "percentile 95", "gap_s": gap_s, "min_s": min_s},
            "segments": rows}


# ---------------------------------------------------------------- notes・repeat（音程）
def _load(path, start, stop, rate):
    """[start, stop) を rate Hz のモノにする（実制作の台本の load と同じ）。"""
    soundfile, info = _soundfile(path)
    librosa = _librosa()
    sr = info.samplerate
    y, _ = soundfile.read(str(path), start=int(start * sr), stop=int(stop * sr), dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)
    return librosa.resample(y, orig_sr=sr, target_sr=rate)


def pitch(path, start, stop):
    """(10 ms ごとの MIDI の音程（声の無いコマは nan）, 16000 Hz の音)。"""
    librosa = _librosa()
    y = _load(path, start, stop, NOTE_RATE)
    f, voiced, _ = librosa.pyin(y, fmin=NOTE_FMIN, fmax=NOTE_FMAX, sr=NOTE_RATE, frame_length=NOTE_FRAME, hop_length=NOTE_HOP)
    return librosa.hz_to_midi(np.where(voiced, f, np.nan)), y


def note_list(midi, start):
    """半音に丸めた音程の続き（7 コマ以上）を (頭, 終わり, MIDI) にする（実制作の台本と同じ。MIDI は整数）。"""
    rounded, out, current, first = np.round(midi), [], None, 0
    for i, x in enumerate(list(rounded) + [np.nan]):
        if np.isnan(x) or current is None or abs(x - current) >= 1:
            if current is not None and (i - first) >= NOTE_MIN_FRAMES:
                out.append((start + first * NOTE_STEP_S, start + i * NOTE_STEP_S, int(current)))
            current = None if np.isnan(x) else x
            first = i
    return out


def dips(y, start):
    """音量が 95 パーセンタイルから −14 dB 未満のまま 6 コマ以上続く谷 [(頭, 終わり)]（実制作の台本と同じ）。"""
    librosa = _librosa()
    r = librosa.feature.rms(y=y, frame_length=DIP_FRAME, hop_length=NOTE_HOP)[0]
    e = 20 * np.log10(r + 1e-6)
    e -= np.percentile(e, 95)
    low, i, out = e < DIP_DB, 0, []
    while i < len(low):
        if low[i]:
            j = i
            while j < len(low) and low[j]:
                j += 1
            if j - i >= DIP_MIN_FRAMES:
                out.append((start + i * NOTE_STEP_S, start + j * NOTE_STEP_S))
            i = j
        else:
            i += 1
    return out


def notes(path, start, stop):
    """音程の音符・立ち上がり・音量の谷の記録（gate-notes/0.1）。"""
    librosa = _librosa()
    _, info = _soundfile(path)
    start, stop = _range(start, stop, info.frames / info.samplerate, "--from・--to")
    midi, y = pitch(path, start, stop)
    strength = librosa.onset.onset_strength(y=y, sr=NOTE_RATE, hop_length=NOTE_HOP)
    onsets = librosa.onset.onset_detect(onset_envelope=strength, sr=NOTE_RATE, hop_length=NOTE_HOP, units="time",
                                        delta=ONSET_DELTA, wait=ONSET_WAIT) + start
    rows = [{"start": a, "end": b, "midi": m, "name": librosa.midi_to_note(m, unicode=False)} for a, b, m in note_list(midi, start)]
    return {"schema_version": "gate-notes/0.1", "estimated": True, "vocals": _vocals(path, info), "range_s": [start, stop],
            "settings": {"sample_rate": NOTE_RATE, "hop": NOTE_HOP, "frame": NOTE_FRAME, "fmin_hz": NOTE_FMIN, "fmax_hz": NOTE_FMAX,
                         "min_frames": NOTE_MIN_FRAMES, "onset": {"delta": ONSET_DELTA, "wait": ONSET_WAIT},
                         "dip": {"frame": DIP_FRAME, "below_db": DIP_DB, "min_frames": DIP_MIN_FRAMES}},
            "notes": rows, "onsets_s": [float(t) for t in onsets],
            "dips": [{"start": a, "end": b} for a, b in dips(y, start)]}


def match(reference, candidate, ref_start, search_start, include_end=False):
    """基準の音程の並びを候補の中でずらした一致率とずれ [(一致率, ずれ秒)]（実制作の台本の match と同じ。高い順）。

    台本は、候補の終わりにぴったり合うずらし方（最後の 1 つ）を比べない（範囲の上限が 1 つ足りない）。既定はそのまま。
    include_end なら最後の 1 つも比べる（繰り返しが候補の終わりにぴったり来るときや、候補と基準のコマの数が同じときに要る）。
    """
    best = []
    for shift in range(0, len(candidate) - len(reference) + (1 if include_end else 0)):
        x = candidate[shift:shift + len(reference)]
        ok = ~np.isnan(reference) & ~np.isnan(x)
        if ok.sum() < MATCH_OVERLAP * len(reference):
            continue
        d = np.abs(reference[ok] - x[ok])
        d = np.minimum(d, np.abs(d - 12))
        best.append((round(float(np.mean(d < MATCH_SEMITONES)), 3), round(search_start + shift * NOTE_STEP_S - ref_start, 2)))
    best.sort(reverse=True)
    return best


def repeat(path, ref, search, top=MATCH_TOP, include_end=False):
    """同じ旋律の繰り返しのずれの記録（gate-repeat/0.1）。ref・search は (始め, 終わり) の秒。

    include_end なら、探す区間の終わりにぴったり合うずらし方も比べる（既定は実制作の台本と同じく比べない）。
    """
    include_end = bool(include_end)
    _, info = _soundfile(path)
    duration = info.frames / info.samplerate
    ref = _range(*ref, duration, "--ref")
    search = _range(*search, duration, "--search")
    if isinstance(top, bool) or not isinstance(top, int) or top < 1:
        raise PipelineError(f"--top は 1 以上の整数にしてください: {top!r}")
    if search[1] - search[0] <= ref[1] - ref[0]:
        raise PipelineError("--search の区間は --ref の区間より長くしてください（基準をずらしながら比べるため）")
    reference, _ = pitch(path, *ref)
    candidate, _ = pitch(path, *search)
    found = match(reference, candidate, ref[0], search[0], include_end)
    return {"schema_version": "gate-repeat/0.1", "estimated": True, "vocals": _vocals(path, info), "ref_s": list(ref),
            "search_s": list(search),
            "settings": {"sample_rate": NOTE_RATE, "hop": NOTE_HOP, "min_overlap": MATCH_OVERLAP, "within_semitones": MATCH_SEMITONES,
                         "octave_fold": 12, "include_end": include_end},
            "shifts_compared": len(found), "matches": [{"rate": rate, "offset_s": offset} for rate, offset in found[:top]]}


# ---------------------------------------------------------------- 書き出し
def save(record, out, vocals, force=False):
    """--out に記録を書く（ボーカルと同じファイルには書かない。中身の違うファイルがあれば force が要る）。状態を返す。"""
    if out is None:
        return None
    out = Path(out)
    if out.resolve() == Path(vocals).resolve():
        raise PipelineError(f"--out がボーカルと同じファイルです（--force でも上書きしません）: {out}")
    if out.exists():
        try:
            if json_equal(out, record):
                return "unchanged"
        except (ValueError, UnicodeDecodeError):
            pass
        if not force:
            raise PipelineError(f"{out} は中身の違うファイルとして既にあります。置き換えるなら --force を付けてください")
    atomic_json(out, record)
    return "written"


def json_equal(path, record):
    import json
    return json.loads(Path(path).read_text(encoding="utf-8")) == json.loads(json.dumps(record, allow_nan=False))


def summary(record, out_state, out):
    """CLI に返す要約（記録の全部と、--out の状態）。"""
    return {**record, "out": None if out is None else str(out), "state": out_state}
