"""mv audio grid（拍の格子）の検査。音はすべて合成（クリックと和音）。

実制作の拍の格子の台本の点数と探し方は、ここに台本のまま写した関数（_production_score・_production_search）と、
同じ入力で値まで同じになることを確かめる（float32 で順に足す和と、同じ点なら先の組を残す選び方）。
"""
import json

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")
soundfile = pytest.importorskip("soundfile")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.audio import grid  # noqa: E402
from mv_pipeline.dance.beats import load_grid  # noqa: E402
from mv_pipeline.io import PipelineError, sha256  # noqa: E402

RATE = 25600          # hop 256 で 1 フレーム 10 ms。拍（0.5 秒おき）がいつも同じフレームの位置に来る


def clicks(path, bpm=120.0, phase=0.25, seconds=16.0, accent=1, beats_per_bar=4, rate=RATE, lead=0.01):
    """拍ごとの短いクリック（減衰する雑音）。拍の番号 % beats_per_bar == accent の拍を強くする。
    クリックは拍の lead 秒前に鳴らす（onset_strength の山はクリックの 1 フレーム後に来る。実制作の手順は拍の直後のフレームを見る）。"""
    rng = np.random.default_rng(3)
    y = np.zeros(int(seconds * rate), np.float32)
    burst = (rng.standard_normal(int(0.03 * rate)) * np.exp(-np.arange(int(0.03 * rate)) / (0.006 * rate))).astype(np.float32)
    for k, t in enumerate(np.arange(phase, seconds - 0.05, 60 / bpm)):
        i = int(round((t - lead) * rate))
        gain = 0.9 if k % beats_per_bar == accent else 0.35
        y[i:i + len(burst)] += gain * burst[:len(y) - i]
    soundfile.write(str(path), np.stack([y, y], 1), rate)
    return path


def chords(path, bpm=120.0, phase=0.25, seconds=16.0, bar_phase=2, rate=RATE):
    """小節ごとに和音が変わる音（拍の番号 % 4 == bar_phase の拍で変わる）。立ち上がりの強さは拍ごとに同じ。"""
    notes = [(261.63, 329.63, 392.00), (349.23, 440.00, 523.25), (392.00, 493.88, 587.33), (440.00, 523.25, 659.25)]
    t = np.arange(int(seconds * rate)) / rate
    period = 60 / bpm
    heads = phase + period * (bar_phase + 4 * np.arange(int(seconds / (4 * period)) + 2))
    which = np.searchsorted(heads, t, side="right") % len(notes)
    y = np.zeros_like(t)
    for index, chord in enumerate(notes):
        mask = which == index
        for f in chord:
            y[mask] += 0.15 * np.sin(2 * np.pi * f * t[mask])
    soundfile.write(str(path), y.astype(np.float32), rate)
    return path


def _production_score(o, t_frames, dur, sr, period, phase, tol=0.03, hop=256):
    """実制作の拍の格子の台本の score（そのまま）。"""
    grid_ = np.arange(phase, dur, period)
    idx = np.searchsorted(t_frames, grid_)
    w = int(tol / (hop / sr))
    s = 0.0
    for i in idx:
        a = max(0, i - w)
        b = min(len(o), i + w + 1)
        if b > a:
            s += o[a:b].max()
    return s / len(grid_)


def _production_search(o, t_frames, dur, sr, low, high):
    """実制作の拍の格子の台本の粗い探索と細かい探索（範囲だけ引数にした）。"""
    best = None
    for bpm in np.arange(low, high, 0.02):
        period = 60 / bpm
        for phase in np.arange(0, period, 0.01):
            sc = _production_score(o, t_frames, dur, sr, period, phase)
            if best is None or sc > best[0]:
                best = (sc, bpm, phase)
    sc, bpm, phase = best
    for bpm2 in np.arange(bpm - 0.05, bpm + 0.05, 0.002):
        p2 = 60 / bpm2
        for ph2 in np.arange(max(0, phase - 0.02), phase + 0.02, 0.002):
            s2 = _production_score(o, t_frames, dur, sr, p2, ph2, tol=0.02)
            if s2 > sc:
                sc, bpm, phase = s2, bpm2, ph2
    return bpm, phase, sc


@pytest.mark.parametrize("sr", [22050, 48000, 8000])          # 8000 Hz では tol 0.02 の窓が 0 フレーム（最後の点の窓が空になる）
def test_the_scorer_equals_the_production_loop(sr):
    rng = np.random.default_rng(sr)
    o = rng.random(700).astype(np.float32)
    t_frames = librosa.frames_to_time(np.arange(len(o)), sr=sr, hop_length=256)
    dur = float(t_frames[-1]) + 0.02
    scorer = grid.Scorer(o, t_frames, sr, dur)
    for period, phase in [(0.5, 0.0), (0.4987, 0.13), (0.62, 0.61), (60 / 173.99, 0.018)]:
        for tol in (0.03, 0.02):
            mine, theirs = scorer.score(period, phase, tol), _production_score(o, t_frames, dur, sr, period, phase, tol)
            assert mine == theirs and type(mine) is type(theirs)            # float32 の和まで同じ


def test_search_equals_the_production_steps():
    rng = np.random.default_rng(7)
    sr = 22050
    t_frames = librosa.frames_to_time(np.arange(3000), sr=sr, hop_length=256)      # 35 秒（短いと位相を寄せて多くの bpm が満点になる）
    o = (rng.random(3000) * 0.2).astype(np.float32)
    for t in np.arange(0.31, t_frames[-1], 60 / 120.37):                     # 120.37 bpm・位相 0.31 秒の山
        o[np.searchsorted(t_frames, t)] = 1.0
    dur = float(t_frames[-1])
    bpm, phase, score, coarse = grid.search(grid.Scorer(o, t_frames, sr, dur), 119.5, 121.5)
    assert (bpm, phase, score) == _production_search(o, t_frames, dur, sr, 119.5, 121.5)
    assert coarse["bpm"] == pytest.approx(120.37, abs=0.3)


def test_search_finds_the_click_grid_and_the_accented_downbeat(tmp_path):
    audio = clicks(tmp_path / "song.wav", bpm=120, phase=0.25, accent=1, seconds=60)      # 曲 1 本分を想定した手順なので長めに
    code, result, err = run_cli("audio", "grid", audio, "--bpm-min", "118", "--bpm-max", "122")
    assert code == 0, err
    record = json.loads((tmp_path / "song.grid.json").read_text(encoding="utf-8"))
    assert result["grid"] == str(tmp_path / "song.grid.json") and result["state"] == "written" and result["mode"] == "search"
    assert record["schema_version"] == "audio-grid/0.1" and record["estimated"] is True
    # 窓が ±30 ms なので、120 拍では 0.1 BPM ほどのずれまで点が変わらない（曲 1 本分ならもっと絞れる）
    assert record["bpm"] == pytest.approx(120, abs=0.15) and record["phase_s"] == pytest.approx(0.25, abs=0.03)
    assert record["downbeat_phase"] == 1 and record["downbeat"]["method"] == "onset" and len(record["downbeat"]["scores"]) == 4
    assert record["downbeats"] == record["beats_s"][1::4] and record["beats_per_bar"] == 4
    assert record["audio"] == {"path": "song.wav", "sha256": sha256(audio), "sample_rate": RATE, "channels": 2, "duration_s": 60.0}
    assert record["fit"]["mode"] == "search" and record["fit"]["coarse"]["bpm"] == pytest.approx(120, abs=0.15)
    assert record["drift"]["matched"] >= 0.9 * record["drift"]["beats"] and len(record["drift"]["parts"]) == 3
    assert record["settings"]["bpm_min"] == 118 and record["tools"]["librosa"] == librosa.__version__


def test_fair_refine_compares_with_the_refine_window(tmp_path):
    audio = clicks(tmp_path / "song.wav", bpm=120.013, phase=0.2537, accent=0)
    plain = grid.estimate(audio, 119, 121)
    fair = grid.estimate(audio, 119, 121, fair_refine=True)
    assert plain["fit"]["fair_refine"] is False and fair["fit"]["fair_refine"] is True
    assert plain["fit"]["coarse"] == fair["fit"]["coarse"]
    mono, rate, _ = grid.load_mono(audio)
    onset = librosa.onset.onset_strength(y=mono, sr=rate, hop_length=256)
    times = librosa.frames_to_time(np.arange(len(onset)), sr=rate, hop_length=256)
    scorer = grid.Scorer(onset / (onset.max() + 1e-9), times, rate, len(mono) / rate)
    coarse = scorer.score(60 / fair["fit"]["coarse"]["bpm"], fair["fit"]["coarse"]["phase_s"], 0.02)
    assert scorer.score(fair["period_s"], fair["phase_s"], 0.02) >= coarse          # 同じ窓で比べて悪くならない


def test_a_fixed_grid_is_recorded_without_searching(tmp_path):
    audio = clicks(tmp_path / "song.wav", accent=3)
    code, result, err = run_cli("audio", "grid", audio, "--bpm", "120", "--phase", "0.25", "--out", tmp_path / "fixed.json")
    assert code == 0, err
    record = json.loads((tmp_path / "fixed.json").read_text(encoding="utf-8"))
    assert record["fit"]["mode"] == "fixed" and "coarse" not in record["fit"] and "bpm_min" not in record["settings"]
    assert record["bpm"] == 120.0 and record["period_s"] == 0.5 and record["phase_s"] == 0.25
    assert record["beats_s"] == [float(t) for t in np.arange(0.25, 16.0, 0.5)] and record["downbeat_phase"] == 3
    assert result["first_bar_s"] == 1.75 and result["bars"] == len(record["beats_s"][3::4])


def test_chroma_downbeat_follows_the_chord_changes(tmp_path):
    audio = chords(tmp_path / "song.wav", phase=0.1, bar_phase=2)
    record = grid.estimate(audio, bpm=120, phase=0.1, downbeat="chroma")
    info = record["downbeat"]
    assert record["downbeat_phase"] == 2 and info["method"] == "chroma" and [p["downbeat_phase"] for p in info["parts"]] == [2, 2, 2]
    assert len(info["eighth_slots"]) == 8 and info["first_eighth_s"] == 0.1 and len(info["scores"]) == 4
    assert info["scores"][2] == info["eighth_slots"][4] == max(info["scores"])   # 位相が 8 分より前なら、拍 2 は 8 分の番号 4
    assert record["settings"]["chroma"] == {"sample_rate": 22050, "harmonic_margin": 3, "gap_s": 0.02}


def test_chroma_slots_map_to_beats_when_the_eighth_grid_starts_before_the_phase(tmp_path):
    audio = chords(tmp_path / "song.wav", phase=0.4, bar_phase=1)
    record = grid.estimate(audio, bpm=120, phase=0.4, downbeat="chroma")          # 位相が 8 分以上: 8 分の格子は 0.15 秒から（実制作と同じ）
    info = record["downbeat"]
    assert info["first_eighth_s"] == pytest.approx(0.15) and record["downbeat_phase"] == 1
    assert info["scores"][1] == info["eighth_slots"][3] == max(info["scores"])   # 拍 1 は 8 分の番号 3（−1 + 3 = 2 つ目の 8 分）


def test_a_project_folder_writes_beside_analysis_json(tmp_path):
    project = tmp_path / "project"
    (project / "audio").mkdir(parents=True)
    audio = clicks(project / "audio" / "song.wav")
    (project / "analysis.json").write_text(json.dumps({"audio": {"path": "audio/song.wav", "sha256": sha256(audio)}}), encoding="utf-8")
    code, result, err = run_cli("audio", "grid", project, "--bpm", "120", "--phase", "0.25")
    assert code == 0, err
    assert result["grid"] == str(project / "analysis.grid.json")
    assert json.loads((project / "analysis.grid.json").read_text(encoding="utf-8"))["audio"]["path"] == "audio/song.wav"
    (project / "analysis.json").write_text(json.dumps({"audio": {"path": "audio/song.wav", "sha256": "0" * 64}}), encoding="utf-8")
    code, _, err = run_cli("audio", "grid", project, "--bpm", "120", "--phase", "0.25")
    assert code == 1 and "sha256" in err
    code, _, err = run_cli("audio", "grid", tmp_path, "--bpm", "120", "--phase", "0.25")
    assert code == 1 and "analysis.json" in err


def test_out_never_replaces_analysis_json_even_with_force(tmp_path):
    # 確かめていたのは音源だけで、--out analysis.json --force だと、プロジェクトの analysis.json（歌詞など）が格子で置き換わっていた
    project = tmp_path / "project"
    (project / "audio").mkdir(parents=True)
    audio = clicks(project / "audio" / "song.wav")
    (project / "analysis.json").write_text(json.dumps({"audio": {"path": "audio/song.wav", "sha256": sha256(audio)},
                                                       "lyrics": [{"id": "L1", "text": "keep me"}]}), encoding="utf-8")
    before = (project / "analysis.json").read_bytes()
    code, _, err = run_cli("audio", "grid", project, "--bpm", "120", "--phase", "0.25", "--out", project / "analysis.json", "--force")
    assert code == 1 and "--out がanalysis.jsonと同じ" in err and (project / "analysis.json").read_bytes() == before


def test_the_record_is_written_through_a_new_temporary_file(tmp_path):
    # 決まった名前 <名前>.tmp に書いてから置き換えていたので、既にある同じ名前のファイルを書き換えて消していた
    audio = clicks(tmp_path / "song.wav")
    keep = tmp_path / "song.grid.json.tmp"
    keep.write_text("someone else's file", encoding="utf-8")
    code, _, err = run_cli("audio", "grid", audio, "--bpm", "120", "--phase", "0.25")
    assert code == 0, err
    assert keep.read_text(encoding="utf-8") == "someone else's file" and (tmp_path / "song.grid.json").is_file()
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == [keep.name]


def test_a_rerun_is_unchanged_and_other_contents_need_force(tmp_path):
    audio = clicks(tmp_path / "song.wav")
    args = ("audio", "grid", audio, "--bpm", "120", "--phase", "0.25")
    first, second = run_cli(*args), run_cli(*args)
    assert first[0] == 0 and second[0] == 0 and second[1]["state"] == "unchanged"
    code, _, err = run_cli(*args, "--beats-per-bar", "3")
    assert code == 1 and "--force" in err
    code, result, err = run_cli(*args, "--beats-per-bar", "3", "--force")
    assert code == 0 and result["state"] == "written" and result["beats_per_bar"] == 3


def test_the_record_loads_as_dance_and_timeline_beats(tmp_path):
    audio = clicks(tmp_path / "song.wav", accent=2)
    run_cli("audio", "grid", audio, "--bpm", "120", "--phase", "0.25")
    record = json.loads((tmp_path / "song.grid.json").read_text(encoding="utf-8"))
    loaded = load_grid({"path": "song.grid.json"}, lambda p: tmp_path / p)
    assert loaded.beats == record["beats_s"] and loaded.downbeats == record["downbeats"] and loaded.at_bar(0) == 1.25


@pytest.mark.parametrize("args, message", [
    ((), "--bpm-min"),
    (("--bpm-min", "122", "--bpm-max", "118"), "0 < 最小 < 最大"),
    (("--bpm", "120"), "--phase"),
    (("--bpm", "120", "--phase", "0.25", "--bpm-min", "100"), "--bpm-min"),
    (("--bpm", "120", "--phase", "0.25", "--fair-refine"), "--fair-refine"),
    (("--bpm", "120", "--phase", "0.25", "--beats-per-bar", "0"), "--beats-per-bar"),
    (("--bpm", "0", "--phase", "0.25"), "--bpm"),
])
def test_bad_arguments_are_refused(tmp_path, args, message):
    audio = clicks(tmp_path / "song.wav")
    code, _, err = run_cli("audio", "grid", audio, *args)
    assert code == 1 and message in err
    assert not (tmp_path / "song.grid.json").exists()


def test_missing_short_or_same_files_are_refused(tmp_path):
    code, _, err = run_cli("audio", "grid", tmp_path / "none.wav", "--bpm", "120", "--phase", "0")
    assert code == 1 and "見つかりません" in err
    short = clicks(tmp_path / "short.wav", seconds=0.6)
    code, _, err = run_cli("audio", "grid", short, "--bpm", "60", "--phase", "0")
    assert code == 1 and "短すぎ" in err
    audio = clicks(tmp_path / "song.wav")
    code, _, err = run_cli("audio", "grid", audio, "--bpm", "120", "--phase", "0.25", "--out", audio)
    assert code == 1 and "音源と同じ" in err
    with pytest.raises(PipelineError, match="--downbeat"):
        grid.estimate(audio, bpm=120, phase=0.25, downbeat="loud")
