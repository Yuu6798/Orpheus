"""光のキー: 曲の音から、コマごとの帯の強さ（low・mid・high、0〜1）と拍の格子を作る（model3d-light-keys/0.1。
plan.stage が光に使う）。リポジトリ外の試作（32 秒の本番の光のキー）と同じ解析を、numpy だけで計算する。

- 音は左右を平均して 1 本にし、コマ i（0 から）の時刻 i / fps（窓の頭が 0 秒）を中心に、n_fft 標本の周期的な
  ハン窓をかけてパワーにする（音の外は 0）。中心が音の終わりより後のコマは、パワーが 0（無音）。
  標本の速さが fps で割り切れるとき（48000 Hz と 30 fps など）は、librosa.stft(mono, n_fft=n_fft,
  hop_length=標本の速さ / fps, center=True) の |·|² と同じ値になる（試作はこれを使った）。
- 帯の強さ = log10(帯の中のパワーの和 + 1e-6) を、窓の中の 20〜99 パーセンタイルで 0〜1 に伸ばす。
- low は、低音の強さの立ち上がり（前のコマとの差 × kick_gain、0〜1）と、強さ × low_floor の大きい方。
- どの帯も、立ち上がりは即座に、下がりは前のコマの release 倍まで（アタック・リリース）。
- 拍は、格子 k0_time + period × k（秒、窓の頭が 0）の、窓に入る全部。
音は soundfile があればそれで（libsndfile が読む形式）読み、無ければ WAV（PCM の 8・16・24・32 ビット）を標準の
wave で読む（どちらも float32 で、整数の標本は同じ値になる）。
"""
import math
from numbers import Real

import numpy as np

from .io import ToolError

BANDS = {"low": [30.0, 130.0], "mid": [300.0, 3000.0], "high": [5000.0, 16000.0]}
RELEASE = {"low": 0.78, "mid": 0.86, "high": 0.72}
PERCENTILES = [20.0, 99.0]
KICK_GAIN, LOW_FLOOR, N_FFT, FPS = 3.0, 0.55, 2048, 30
PARAMETERS = {"grid", "fps", "frames", "start", "song_start", "bands", "release", "percentiles", "kick_gain", "low_floor",
              "n_fft"}
BLOCK = 512                                              # 一度に変換するコマの数（長い曲でもメモリを食いすぎない）


def norm(x, lo=20.0, hi=99.0):
    """窓の中の lo〜hi パーセンタイルを 0〜1 に伸ばす（外は 0 と 1 に）。"""
    p, q = np.percentile(x, [lo, hi])
    return np.clip((x - p) / (q - p + 1e-9), 0, 1)


def attack_release(x, rel):
    """立ち上がりは即座に、下がりは前のコマの rel 倍まで。"""
    out, level = np.zeros_like(x), 0.0
    for i, v in enumerate(x):
        level = max(float(v), level * rel)
        out[i] = level
    return out


def grid_beats(k0_time, period, frames, fps):
    """窓（frames コマ）に入る拍 [{k, t（秒、4 桁）, frame（コマ、0 から）}]。k は時刻が 0 秒以上の最小のものから。"""
    if len(range(math.ceil(-k0_time / period), math.ceil((frames / fps - k0_time) / period))) > 10 * frames:
        raise ToolError(f"拍の格子の period（{period} 秒）が短すぎます（1 コマに 10 拍より多い）")
    k, beats = int(np.ceil(-k0_time / period)), []
    while k0_time + k * period < frames / fps:
        t = k0_time + k * period
        beats.append({"k": k, "t": round(t, 4), "frame": int(round(t * fps))})
        k += 1
    return beats


def hann(n):
    """周期的なハン窓（scipy.signal.get_window("hann", n, fftbins=True) と同じ計算。librosa.stft の既定の窓）。"""
    fac = np.linspace(-np.pi, np.pi, n + 1)
    w = np.zeros(n + 1)
    for k, a in enumerate((0.5, 0.5)):
        w += a * np.cos(k * fac)
    return w[:n]


def centers(frames, rate, fps):
    """コマ i（0 から）の窓の中心の標本（窓の頭が 0）: round(i × 標本の速さ / fps)。"""
    return np.rint(np.arange(frames) * (rate / fps)).astype(np.int64)


def power(mono, rate, fps, frames, n_fft=N_FFT):
    """コマごとのパワー（(1 + n_fft // 2, frames) の float32）と、音が届いたコマの数（中心が音の中のコマ。後ろは 0）。"""
    mono = np.asarray(mono, dtype=np.float32)
    half = n_fft // 2
    middle = centers(frames, rate, fps)
    shown = int(np.searchsorted(middle, len(mono), side="right"))
    padded = np.pad(mono, (half, half))
    frames_of = np.lib.stride_tricks.sliding_window_view(padded, n_fft)
    window = hann(n_fft)[:, None]
    spectrum = np.zeros((1 + half, shown), dtype=np.complex64, order="F")
    for a in range(0, shown, BLOCK):
        b = min(a + BLOCK, shown)
        spectrum[:, a:b] = np.fft.rfft(window * frames_of[middle[a:b]].T, axis=0)
    S = np.abs(spectrum) ** 2
    if shown < frames:                                   # 音より後ろのコマは無音（試作と同じく 0 を足す）
        S = np.pad(S, ((0, 0), (0, frames - shown)))
    return S, shown


def frequencies(rate, n_fft=N_FFT):
    """power の行の周波数（Hz）。"""
    return np.fft.rfftfreq(n_fft, d=1.0 / rate)


def light_keys(samples, rate, frames, fps, grid, song_start=0.0, bands=None, release=None, percentiles=None,
               kick_gain=KICK_GAIN, low_floor=LOW_FLOOR, n_fft=N_FFT):
    """光のキー（model3d-light-keys/0.1）と、音が届いたコマの数。samples は (標本, 左右) の float32、grid は {"period", "k0_time"}。"""
    bands, release = {**BANDS, **(bands or {})}, {**RELEASE, **(release or {})}
    lo, hi = percentiles or PERCENTILES
    mono = np.asarray(samples).mean(axis=1)
    S, shown = power(mono, rate, fps, frames, n_fft)
    freqs = frequencies(rate, n_fft)

    def band(name):
        a, b = bands[name]
        inside = (freqs >= a) & (freqs < b)
        if not inside.any():
            raise ToolError(f"帯 {name}（{a}〜{b} Hz）に入る周波数がありません（標本の速さ {rate} Hz、n_fft {n_fft} で、"
                            f"周波数の間隔 {rate / n_fft:.2f} Hz、上限 {rate / 2:g} Hz）。bands で帯を変えてください")
        return np.log10(S[inside].sum(axis=0) + 1e-6)

    low_raw = norm(band("low"), lo, hi)
    kick = np.clip(np.diff(low_raw, prepend=low_raw[0]) * kick_gain, 0, 1)
    low = attack_release(np.maximum(kick, low_raw * low_floor), release["low"])
    mid = attack_release(norm(band("mid"), lo, hi), release["mid"])
    high = attack_release(norm(band("high"), lo, hi), release["high"])
    period, k0 = float(grid["period"]), float(grid["k0_time"])
    keys = {"schema_version": "model3d-light-keys/0.1", "start_s": float(song_start), "fps": fps, "frames": int(frames),
            "grid": {"period": period, "k0_time": k0},
            "low": [round(float(v), 3) for v in low], "mid": [round(float(v), 3) for v in mid],
            "high": [round(float(v), 3) for v in high], "beats": grid_beats(k0, period, frames, fps)}
    return keys, shown


def _number(value, name, low=None, high=None, low_open=False):
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ToolError(f"plan.light_keys の {name} は数にしてください（{value!r}）")
    if low is not None and (value <= low if low_open else value < low):
        raise ToolError(f"plan.light_keys の {name} は {low} {'より大きく' if low_open else '以上に'}してください（{value!r}）")
    if high is not None and value > high:
        raise ToolError(f"plan.light_keys の {name} は {high} 以下にしてください（{value!r}）")
    return value


def settings(parameters):
    """パラメーターを確かめて、省いたものを既定で埋める。"""
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise ToolError(f"plan.light_keys の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PARAMETERS)}）")
    grid = parameters.get("grid")
    if not isinstance(grid, dict) or set(grid) != {"period", "k0_time"}:
        raise ToolError("plan.light_keys には grid（{\"period\": 拍の間隔の秒, \"k0_time\": 拍 0 の時刻の秒（窓の頭が 0）}）が要ります")
    out = {"grid": {"period": _number(grid["period"], "grid.period", 0, low_open=True),
                    "k0_time": _number(grid["k0_time"], "grid.k0_time")},
           "fps": _number(parameters.get("fps", FPS), "fps", 0, low_open=True),
           "start": _number(parameters.get("start", 0.0), "start", 0),
           "kick_gain": _number(parameters.get("kick_gain", KICK_GAIN), "kick_gain", 0),
           "low_floor": _number(parameters.get("low_floor", LOW_FLOOR), "low_floor", 0, 1)}
    out["song_start"] = _number(parameters.get("song_start", out["start"]), "song_start")
    frames = parameters.get("frames")
    if frames is not None and (isinstance(frames, bool) or not isinstance(frames, int) or frames < 1):
        raise ToolError(f"plan.light_keys の frames は 1 以上の整数にしてください（{frames!r}）")
    out["frames"] = frames
    n_fft = parameters.get("n_fft", N_FFT)
    if isinstance(n_fft, bool) or not isinstance(n_fft, int) or n_fft < 16 or n_fft > 65536 or n_fft % 2:
        raise ToolError(f"plan.light_keys の n_fft は 16〜65536 の偶数にしてください（{n_fft!r}）")
    out["n_fft"] = n_fft
    bands = parameters.get("bands", {})
    if not isinstance(bands, dict) or set(bands) - set(BANDS):
        raise ToolError(f"plan.light_keys の bands は {sorted(BANDS)} の帯ごとの [下の Hz, 上の Hz] にしてください（{bands!r}）")
    for name, pair in bands.items():
        if not isinstance(pair, list) or len(pair) != 2:
            raise ToolError(f"plan.light_keys の bands.{name} は [下の Hz, 上の Hz] にしてください（{pair!r}）")
        a, b = (_number(v, f"bands.{name}", 0) for v in pair)
        if a >= b:
            raise ToolError(f"plan.light_keys の bands.{name} は下の Hz を上の Hz より小さくしてください（{pair!r}）")
    out["bands"] = {**BANDS, **{name: [float(v) for v in pair] for name, pair in bands.items()}}
    release = parameters.get("release", {})
    if not isinstance(release, dict) or set(release) - set(RELEASE):
        raise ToolError(f"plan.light_keys の release は {sorted(RELEASE)} の帯ごとの 0〜1 にしてください（{release!r}）")
    out["release"] = {**RELEASE, **{name: _number(v, f"release.{name}", 0, 1) for name, v in release.items()}}
    percentiles = parameters.get("percentiles", PERCENTILES)
    if not isinstance(percentiles, list) or len(percentiles) != 2:
        raise ToolError(f"plan.light_keys の percentiles は [下, 上]（0〜100）にしてください（{percentiles!r}）")
    lo, hi = (_number(v, "percentiles", 0, 100) for v in percentiles)
    if lo >= hi:
        raise ToolError(f"plan.light_keys の percentiles は下を上より小さくしてください（{percentiles!r}）")
    out["percentiles"] = [float(lo), float(hi)]
    return out


def _wav(path, first, count):
    """soundfile が無いときの読み方: PCM の WAV（8・16・24・32 ビット）を float32 に（libsndfile と同じ割り方）。"""
    import wave
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate, total = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
            if first >= total:
                return np.zeros((0, channels), dtype=np.float32), rate, total
            w.setpos(first)
            data = w.readframes(count)
    except (wave.Error, EOFError) as error:
        raise ToolError(f"音のファイル {path} を読めません（{error}）。soundfile が無いときは PCM の WAV だけ読めます"
                        "（soundfile は uv sync --extra analysis で入る）") from error
    if width == 1:
        values = (np.frombuffer(data, np.uint8).astype(np.float32) - np.float32(128)) * np.float32(1 / 128)
    elif width == 2:
        values = np.frombuffer(data, "<i2").astype(np.float32) * np.float32(1 / 32768)
    elif width == 3:
        b = np.frombuffer(data, np.uint8).reshape(-1, 3).astype(np.uint32)
        packed = ((b[:, 0] << 8) | (b[:, 1] << 16) | (b[:, 2] << 24)).view(np.int32)   # 上の 24 ビットに置いた符号つきの値
        values = packed.astype(np.float32) * np.float32(2.0 ** -31)
    elif width == 4:
        values = np.frombuffer(data, "<i4").astype(np.float32) * np.float32(2.0 ** -31)
    else:
        raise ToolError(f"音のファイル {path} の標本の幅（{width} バイト）は読めません")
    return values.reshape(-1, channels), rate, total


def read_window(path, start, frames, fps, n_fft=N_FFT):
    """音のファイルの start 秒から、窓（frames コマ。frames が None なら音の終わりまで）の解析に要る標本
    （(標本, 左右) の float32）と、標本の速さと、frames と、音の長さ（秒）。"""
    try:
        import soundfile
    except ImportError:
        soundfile = None
    if soundfile is not None:
        try:
            info = soundfile.info(str(path))
        except Exception as error:                       # libsndfile が読めない形式
            raise ToolError(f"音のファイル {path} を読めません（{error}）") from error
        rate, total = info.samplerate, info.frames
    else:
        _, rate, total = _wav(path, 0, 0)
    if rate <= 0 or total <= 0:
        raise ToolError(f"音のファイル {path} に音がありません")
    length = total / rate
    first = int(math.floor(start * rate + 1e-6))         # 窓の頭の標本（秒の丸めの誤差で 1 つ手前にならないように）
    if first >= total:
        raise ToolError(f"窓の頭 start（{start} 秒）が、音の長さ（{length:.3f} 秒）より後です")
    if frames is None:
        frames = int(math.floor((total - first) * fps / rate + 1e-9))   # 標本の数から（秒の引き算の誤差でコマを落とさない）
        if frames < 1:
            raise ToolError(f"窓の頭 start（{start} 秒）から音の終わりまでが、1 コマ（{1 / fps:.4f} 秒）より短いです")
    if rate / fps < 1:
        raise ToolError(f"fps（{fps}）が、音の標本の速さ（{rate} Hz）より大きいです")
    count = int((frames / fps + 0.2) * rate) + n_fft     # 試作と同じ 0.2 秒の余り + 窓の半分より多く
    if soundfile is not None:
        samples, rate = soundfile.read(str(path), start=first, frames=count, dtype="float32", always_2d=True)
    else:
        samples, rate, _ = _wav(path, first, count)
    return samples, rate, frames, length
