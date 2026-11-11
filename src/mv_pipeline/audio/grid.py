"""拍の格子（`mv audio grid`）。曲の音の立ち上がりに一定のテンポの格子を当てはめ、拍と小節の頭の時刻を推定する。

    mv audio grid AUDIO|PROJECT --bpm-min A --bpm-max B [--fair-refine] [--downbeat onset|chroma] [--beats-per-bar 4] [--out FILE] [--force]
    mv audio grid AUDIO|PROJECT --bpm B --phase P [--downbeat onset|chroma] …     決めた格子を記録して確かめる（探さない）

格子の探し方（実制作の拍の格子の台本と同じ。値はすべて推定）:
1. 音を元のサンプリング周波数のまま読み、左右を平均してモノにする。librosa の onset_strength（hop 256）を最大値で割る（o）。
2. 点数: 格子 phase, phase+period, … の各点（o のフレームへ searchsorted）の前後 w = int(tol / (hop/sr)) フレームの o の最大を、
   格子の順に float32 で足し、点の数で割る。
3. 粗い探索: bpm を bpm_min から bpm_max の手前まで 0.02 刻み、位相を 0 から 1 拍の手前まで 0.01 秒刻みで総当たりする（tol 0.03 秒）。
   点がいちばん高い組を採る（同じ点なら先に見つけた組）。
4. 細かい探索: bpm ±0.05 を 0.002 刻み、位相 ±0.02 秒（0 未満は切る）を 0.002 秒刻みで、tol 0.02 秒の点が今の最良の点を超えたら
   置き換える（位相の範囲は、その時点の最良の位相を中心に作り直す）。
   今の最良の点は粗い探索の点（tol 0.03 秒）のままなので、窓が狭いぶん点が下がり、ふつうは置き換わらない（実制作と同じ）。
   fair_refine は、細かい探索の前に、粗い探索の最良を tol 0.02 秒で採点し直す（同じ窓どうしで比べる）。
5. ずれの確かめ: onset_strength の山（peak_pick）のうち、格子の各点にいちばん近い山が ±60 ms 未満なら合致とし、ずれ（山 − 点）の
   平均と標準偏差、曲を 3 つに分けた区間ごとのずれの平均を残す（テンポが途中で揺れていないかの目安）。

小節の頭（downbeat_phase。拍の番号 % beats_per_bar がこの値の拍が小節の頭）:
- onset（既定。実制作の拍の格子の台本と同じ）: 格子の各点の o（点の直後のフレーム）を、拍の番号 % beats_per_bar ごとに平均し、
  いちばん高い位相。候補どうしの差が小さいときは当てにならない（記録の downbeat.scores で確かめる）。
- chroma（実制作の小節の頭の確かめと同じ）: 22050 Hz のモノで読み、調波成分（margin 3）の chroma_cqt（hop 256）を取る。
  8 分の格子（phase + k × period/2）の各点で、点の 20 ms 後〜8 分後と、8 分前〜20 ms 前の平均の chroma（長さ 1 に正規化）の
  1 − 内積（和声の変化）を出し、格子の番号 % (2 × beats_per_bar) ごとに平均する。拍に乗る番号のうち、いちばん高いものを小節の頭にする。

記録（audio-grid/0.1。contracts/ の外）は、dance・fx-timeline の beats（{"path": …}）がそのまま読める形にする:
beats_s（拍の時刻）と downbeats（小節の頭の時刻）を持つ。
"""
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from ..io import PipelineError, read_json, safe_path, sha256

SCHEMA = "audio-grid/0.1"
HOP = 256
BPM_STEP, PHASE_STEP_S, TOL_S = 0.02, 0.01, 0.03
REFINE_BPM, REFINE_BPM_STEP, REFINE_PHASE_S, REFINE_PHASE_STEP_S, REFINE_TOL_S = 0.05, 0.002, 0.02, 0.002, 0.02
DRIFT_WINDOW_S, DRIFT_PARTS = 0.06, 3
PEAKS = {"pre_max": 3, "post_max": 3, "pre_avg": 10, "post_avg": 10, "wait": 5}      # delta は 0.3 × 標準偏差
CHROMA_RATE, CHROMA_MARGIN, CHROMA_GAP_S = 22050, 3, 0.02
DOWNBEAT_METHODS = ("onset", "chroma")


def _librosa():
    try:
        import librosa
    except ImportError as error:
        raise PipelineError("mv audio には librosa が要ります。`uv sync --locked --extra analysis` を実行してください") from error
    return librosa


def load_mono(path):
    """元のサンプリング周波数のまま float32 で読み、左右を平均したモノ（実制作と同じ読み方）と周波数・チャンネル数。"""
    import soundfile
    try:
        samples, rate = soundfile.read(str(path), dtype="float32")
    except RuntimeError as error:
        raise PipelineError(f"音源を読めません（{path}）: {error}") from error
    mono = samples.mean(axis=1) if samples.ndim == 2 else samples
    return mono, rate, 1 if samples.ndim == 1 else samples.shape[1]


class Scorer:
    """格子の点数（実制作の score と同じ値）。窓の最大は前もって作り、点の和は格子の順に float32 で足す。"""

    def __init__(self, o, frame_times, rate, duration):
        self.o, self.times, self.rate, self.duration = o, frame_times, rate, duration
        self._maxima = {}

    def window_max(self, w):
        """M[i] = max(o[max(0, i−w) : min(n, i+w+1)])（i = 0 … n）。窓が空なら −inf。"""
        if w not in self._maxima:
            n = len(self.o)
            padded = np.full(n + 2 * w + 1, -np.inf, np.float32)
            padded[w:w + n] = self.o
            maxima = np.full(n + 1, -np.inf, np.float32)
            for k in range(2 * w + 1):
                np.maximum(maxima, padded[k:k + n + 1], out=maxima)
            self._maxima[w] = maxima
        return self._maxima[w]

    def score(self, period, phase, tol):
        grid = np.arange(phase, self.duration, period)
        if len(grid) == 0:
            return -np.inf
        values = self.window_max(int(tol / (HOP / self.rate)))[np.searchsorted(self.times, grid)]
        values = values[np.isfinite(values)]                    # 空の窓は足さない（実制作の `if b > a`）
        total = np.add.accumulate(values)[-1] if len(values) else 0.0   # 0.0 + v0 + v1 … を float32 で順に（実制作の s += …）
        return total / len(grid)


def search(scorer, bpm_min, bpm_max, fair_refine=False):
    """粗い探索と細かい探索。(bpm, phase, score, coarse) を返す。"""
    best = None
    for bpm in np.arange(bpm_min, bpm_max, BPM_STEP):
        period = 60 / bpm
        for phase in np.arange(0, period, PHASE_STEP_S):
            value = scorer.score(period, phase, TOL_S)
            if best is None or value > best[0]:
                best = (value, bpm, phase)
    if best is None:
        raise PipelineError(f"bpm の範囲に候補がありません（{bpm_min}〜{bpm_max}）")
    value, bpm, phase = best
    coarse = {"bpm": float(bpm), "phase_s": float(phase), "score": float(value)}
    if fair_refine:
        value = scorer.score(60 / bpm, phase, REFINE_TOL_S)
    for bpm2 in np.arange(bpm - REFINE_BPM, bpm + REFINE_BPM, REFINE_BPM_STEP):
        period2 = 60 / bpm2
        for phase2 in np.arange(max(0, phase - REFINE_PHASE_S), phase + REFINE_PHASE_S, REFINE_PHASE_STEP_S):
            value2 = scorer.score(period2, phase2, REFINE_TOL_S)
            if value2 > value:
                value, bpm, phase = value2, bpm2, phase2
    return bpm, phase, value, coarse


def drift(onset, frame_times, beats, duration, librosa):
    """格子の各点にいちばん近い立ち上がりの山とのずれ（±60 ms 未満を合致とする）。"""
    peaks = librosa.util.peak_pick(onset, delta=0.3 * onset.std(), **PEAKS)
    times = frame_times[peaks]
    matched = []
    if len(times):
        for beat in beats:
            j = np.argmin(np.abs(times - beat))
            if abs(times[j] - beat) < DRIFT_WINDOW_S:
                matched.append((beat, times[j] - beat))
    matched = np.array(matched).reshape(-1, 2)

    def mean_ms(rows):
        return float(rows[:, 1].mean() * 1000) if len(rows) else None

    parts = []
    for k in range(DRIFT_PARTS):
        a, b = k * duration / DRIFT_PARTS, (k + 1) * duration / DRIFT_PARTS
        rows = matched[(matched[:, 0] >= a) & (matched[:, 0] < b)]
        parts.append({"from_s": a, "to_s": b, "matched": int(len(rows)), "mean_ms": mean_ms(rows)})
    return {"window_s": DRIFT_WINDOW_S, "peaks": int(len(times)), "beats": int(len(beats)), "matched": int(len(matched)),
            "mean_ms": mean_ms(matched), "sd_ms": float(matched[:, 1].std() * 1000) if len(matched) else None, "parts": parts}


def onset_downbeat(o, frame_times, beats, beats_per_bar):
    """拍の番号 % beats_per_bar ごとの、点の直後のフレームの o の平均（実制作の拍の格子の台本と同じ）。"""
    strength = o[np.clip(np.searchsorted(frame_times, beats), 0, len(o) - 1)]
    scores = [strength[p::beats_per_bar].mean() for p in range(beats_per_bar)]
    return int(np.argmax(scores)), {"method": "onset", "scores": [float(v) for v in scores]}


def chroma_change(path, period, phase, librosa):
    """8 分の格子（phase + k × period/2。k は 0 以上の時刻から）と、各点の和声の変化（実制作の小節の頭の確かめと同じ）。"""
    y, rate = librosa.load(str(path), sr=CHROMA_RATE, mono=True)
    eighth = period / 2
    chroma = librosa.feature.chroma_cqt(y=librosa.effects.harmonic(y, margin=CHROMA_MARGIN), sr=rate, hop_length=HOP)
    times = librosa.frames_to_time(np.arange(chroma.shape[1]), sr=rate, hop_length=HOP)
    duration = len(y) / rate
    k0 = int(np.ceil(-phase / eighth))
    grid = phase + eighth * np.arange(k0, int((duration - phase) / eighth) + 1)

    def mean_chroma(a, b):
        inside = (times >= a) & (times < b)
        v = chroma[:, inside].mean(1) if inside.any() else np.zeros(12)
        return v / (np.linalg.norm(v) + 1e-9)

    change = np.array([1 - mean_chroma(g + CHROMA_GAP_S, g + eighth).dot(mean_chroma(g - eighth, g - CHROMA_GAP_S)) for g in grid])
    return grid, change, k0


def slot_profile(grid, change, slots, start=0.0, stop=float("inf")):
    """格子の番号 % slots ごとの和声の変化の平均（start 以上 stop 未満の点だけ）。"""
    index = np.arange(len(grid))[(grid >= start) & (grid < stop)]
    return [float(change[index[(index % slots) == r]].mean()) if ((index % slots) == r).any() else None for r in range(slots)]


def chroma_downbeat(path, period, phase, beats_per_bar, duration, librosa):
    """和声の変化がいちばん大きい拍を小節の頭にする。曲を 3 つに分けた区間ごとの選び方も残す（揺れていないかの目安）。"""
    grid, change, k0 = chroma_change(path, period, phase, librosa)
    slots = 2 * beats_per_bar

    def pick(profile):
        # 番号 r の点の時刻は phase + (k0 + r) × 8 分。k0 + r が偶数なら拍 (k0 + r) / 2 に乗る（拍の番号は phase を 0 とする）
        on_beat = {((k0 + r) // 2) % beats_per_bar: profile[r] for r in range(slots) if (k0 + r) % 2 == 0}
        scores = [on_beat[p] for p in range(beats_per_bar)]
        valid = [(-1 if v is None else v) for v in scores]
        return int(np.argmax(valid)), scores

    whole = slot_profile(grid, change, slots)
    downbeat, scores = pick(whole)
    parts = []
    for k in range(DRIFT_PARTS):
        a, b = k * duration / DRIFT_PARTS, (k + 1) * duration / DRIFT_PARTS
        part = slot_profile(grid, change, slots, a, b)
        parts.append({"from_s": a, "to_s": b, "downbeat_phase": pick(part)[0]})
    return downbeat, {"method": "chroma", "scores": scores, "eighth_slots": whole, "first_eighth_s": float(grid[0]),
                      "parts": parts}


def estimate(path, bpm_min=None, bpm_max=None, beats_per_bar=4, fair_refine=False, bpm=None, phase=None, downbeat="onset"):
    """音源 path の拍の格子（bpm_min〜bpm_max で探す。bpm と phase を渡せば、その格子をそのまま使う）の記録の本体。"""
    librosa = _librosa()
    fixed = bpm is not None or phase is not None
    if fixed:
        if bpm is None or phase is None or bpm_min is not None or bpm_max is not None or fair_refine:
            raise PipelineError("決めた格子を使うなら --bpm と --phase の両方を渡し、--bpm-min・--bpm-max・--fair-refine は付けないでください")
        if not float(bpm) > 0 or not 0 <= float(phase):
            raise PipelineError(f"--bpm は 0 より大きく、--phase は 0 以上にしてください（{bpm}, {phase}）")
        shortest = 60 / float(bpm)
    else:
        if bpm_min is None or bpm_max is None:
            raise PipelineError("--bpm-min と --bpm-max で探す範囲を渡してください（決めた格子なら --bpm と --phase）")
        bpm_min, bpm_max = float(bpm_min), float(bpm_max)
        if not 0 < bpm_min < bpm_max:
            raise PipelineError(f"--bpm-min と --bpm-max は 0 < 最小 < 最大 にしてください（{bpm_min}, {bpm_max}）")
        shortest = 60 / bpm_min
    if isinstance(beats_per_bar, bool) or not isinstance(beats_per_bar, int) or beats_per_bar < 1:
        raise PipelineError(f"--beats-per-bar は 1 以上の整数にしてください: {beats_per_bar!r}")
    if downbeat not in DOWNBEAT_METHODS:
        raise PipelineError(f"--downbeat は {' か '.join(DOWNBEAT_METHODS)} にしてください: {downbeat!r}")
    mono, rate, channels = load_mono(path)
    duration = len(mono) / rate
    if duration < 2 * shortest:
        raise PipelineError(f"音源が短すぎます（{duration:.3f} 秒。2 拍より長いこと）")
    onset = librosa.onset.onset_strength(y=mono, sr=rate, hop_length=HOP)
    frame_times = librosa.frames_to_time(np.arange(len(onset)), sr=rate, hop_length=HOP)
    o = onset / (onset.max() + 1e-9)
    scorer = Scorer(o, frame_times, rate, duration)
    if fixed:
        bpm, phase = float(bpm), float(phase)
        fit = {"mode": "fixed", "score": float(scorer.score(60 / bpm, phase, TOL_S)), "tol_s": TOL_S}
    else:
        bpm, phase, score, coarse = search(scorer, bpm_min, bpm_max, fair_refine)
        fit = {"mode": "search", "score": float(score), "coarse": coarse, "fair_refine": bool(fair_refine),
               "refined": (float(bpm), float(phase)) != (coarse["bpm"], coarse["phase_s"])}
    period = 60 / bpm
    beats = np.arange(phase, duration, period)
    if len(beats) < beats_per_bar:
        raise PipelineError(f"拍が 1 小節分（{beats_per_bar} 拍）もありません（{len(beats)} 拍）。音源の長さか --beats-per-bar を確かめてください")
    if downbeat == "onset":
        phase_of_bar, downbeat_info = onset_downbeat(o, frame_times, beats, beats_per_bar)
    else:
        phase_of_bar, downbeat_info = chroma_downbeat(path, period, phase, beats_per_bar, duration, librosa)
    import soundfile
    settings = {"hop": HOP, "tol_s": TOL_S, "peaks": {**PEAKS, "delta": "0.3 * std"}}
    if not fixed:
        settings.update(bpm_min=bpm_min, bpm_max=bpm_max, bpm_step=BPM_STEP, phase_step_s=PHASE_STEP_S,
                        refine={"bpm": REFINE_BPM, "bpm_step": REFINE_BPM_STEP, "phase_s": REFINE_PHASE_S,
                                "phase_step_s": REFINE_PHASE_STEP_S, "tol_s": REFINE_TOL_S})
    if downbeat == "chroma":
        settings["chroma"] = {"sample_rate": CHROMA_RATE, "harmonic_margin": CHROMA_MARGIN, "gap_s": CHROMA_GAP_S}
    return {
        "schema_version": SCHEMA,
        "estimated": True,
        "audio": {"sha256": sha256(path), "sample_rate": int(rate), "channels": int(channels), "duration_s": duration},
        "bpm": float(bpm), "period_s": float(period), "phase_s": float(phase),
        "beats_per_bar": beats_per_bar, "downbeat_phase": phase_of_bar,
        "beats_s": [float(t) for t in beats], "downbeats": [float(t) for t in beats[phase_of_bar::beats_per_bar]],
        "fit": fit, "downbeat": downbeat_info,
        "drift": drift(onset, frame_times, beats, duration, librosa),
        "settings": settings,
        "tools": {"librosa": librosa.__version__, "numpy": np.__version__, "soundfile": soundfile.__version__},
    }


def _source(source):
    """(音源のパス, 記録に書く音源のパス, 既定の出力)。フォルダなら analysis.json の音源を使い、隣に analysis.grid.json を書く。"""
    source = Path(source)
    if source.is_dir():
        analysis_path = source / "analysis.json"
        if not analysis_path.is_file():
            raise PipelineError(f"フォルダに analysis.json がありません: {source}（音源のファイルを直接渡すこともできます）")
        analysis = read_json(analysis_path)
        audio = analysis.get("audio") if isinstance(analysis, dict) else None
        if not isinstance(audio, dict) or not audio.get("path"):
            raise PipelineError(f"analysis.json に audio.path がありません: {analysis_path}")
        path = safe_path(source, audio["path"])
        if not path.is_file():
            raise PipelineError(f"analysis.json の音源が見つかりません: {path}")
        if audio.get("sha256") and audio["sha256"] != sha256(path):
            raise PipelineError(f"音源が analysis.json の sha256 と違います（{audio['path']}）。音源か analysis.json を確かめてください")
        return path, audio["path"], source / "analysis.grid.json"
    if not source.is_file():
        raise PipelineError(f"音源が見つかりません: {source}")
    return source, source.name, source.with_name(source.stem + ".grid.json")


def _same_file(path):
    return os.path.normcase(str(Path(path).resolve()))


def run(source, bpm_min=None, bpm_max=None, out=None, beats_per_bar=4, fair_refine=False, force=False, bpm=None, phase=None,
        downbeat="onset"):
    """格子を推定して記録を書く。同じ中身のファイルがあれば unchanged、違えば --force が要る。"""
    path, recorded, default_out = _source(source)
    out = Path(out) if out else default_out
    inputs = {_same_file(path): "音源"}
    if Path(source).is_dir():
        inputs[_same_file(Path(source) / "analysis.json")] = "analysis.json"
    if _same_file(out) in inputs:          # 置き換えると入力が消える（--force でも書かない）
        raise PipelineError(f"--out が{inputs[_same_file(out)]}と同じファイルです（入力は --force でも上書きしません）: {out}")
    record = estimate(path, bpm_min, bpm_max, beats_per_bar, fair_refine, bpm, phase, downbeat)
    record["audio"] = {"path": recorded, **record["audio"]}
    text = json.dumps(record, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    state = "written"
    if out.exists():
        if out.read_text(encoding="utf-8", errors="replace") == text:
            state = "unchanged"
        elif not force:
            raise PipelineError(f"{out} は中身の違うファイルとして既にあります。置き換えるなら --force を付けてください")
    if state == "written":
        out.parent.mkdir(parents=True, exist_ok=True)
        # 新しく作った一時ファイル（ランダムな名前）から置き換える。決まった名前（<名前>.tmp）だと、既にある同じ名前のファイルを
        # 消したり、それが音源へのリンクなら音源を書き換えたりする
        fd, temporary = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            os.replace(temporary, out)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    drift_info = record["drift"]
    return {"grid": str(out), "state": state, "estimated": True, "mode": record["fit"]["mode"], "bpm": record["bpm"],
            "period_s": record["period_s"], "phase_s": record["phase_s"], "beats_per_bar": beats_per_bar,
            "downbeat_phase": record["downbeat_phase"], "downbeat_method": downbeat, "beats": len(record["beats_s"]),
            "bars": len(record["downbeats"]), "first_bar_s": record["downbeats"][0] if record["downbeats"] else None,
            "drift": {key: drift_info[key] for key in ("matched", "beats", "mean_ms", "sd_ms")}}
