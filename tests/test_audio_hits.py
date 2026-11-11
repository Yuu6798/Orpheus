"""mv audio hits（打点）の検査。音はすべて合成（雑音の立ち上がりと低い打音のクリック）。

実制作の縦の Shorts の格子の台本の strong（解析の台本と同じ選び方に時刻の補正）と帯域差分の打点、小節と拍の位置は、
ここに台本のまま写した関数（_production_*）と、同じ音で値まで同じになることを確かめる。
クリックの音は、実制作が検出の時刻の偏りを測った台本と同じ作り方（乱数の種 0、1.0 秒から 0.34488 秒おき）。
"""
import json

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")
soundfile = pytest.importorskip("soundfile")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.audio import grid, hits  # noqa: E402
from mv_pipeline.io import PipelineError, sha256  # noqa: E402

RATE = 22050
CLICKS = np.arange(1.0, 11.0, 0.34488)


def calibration_clicks(path, rate=RATE, seconds=12):
    """実制作の較正の台本と同じクリック（雑音の立ち上がり＋60 Hz の低い打音）。rate が 22050 でなければ同じ時刻で作り直す。"""
    rng = np.random.default_rng(0)
    y = 0.001 * rng.standard_normal(rate * seconds)
    for t in CLICKS:
        i, n = int(t * rate), int(0.08 * rate)
        env = np.exp(-np.arange(n) / (0.015 * rate))
        y[i:i + n] += 0.5 * env * rng.standard_normal(n)
        y[i:i + n] += 0.8 * env * np.sin(2 * np.pi * 60 * np.arange(n) / rate)
    soundfile.write(str(path), np.stack([y, y], 1).astype(np.float32), rate, subtype="FLOAT")
    return path


def _production_strong(y, sr):
    """実制作の縦の Shorts の格子の台本の strong（そのまま。時刻は −16.7 ms 補正、0 秒未満は捨てる）。"""
    hop = 512
    onset = librosa.onset.onset_strength(y=y, sr=sr, hop_length=hop)
    peaks = librosa.util.peak_pick(onset, pre_max=10, post_max=10, pre_avg=50, post_avg=50,
                                   delta=np.percentile(onset, 97) * 0.5, wait=20)
    strong = librosa.frames_to_time(peaks, sr=sr, hop_length=hop) - 0.0167
    return [float(s) for s in strong if s >= 0]


def _production_onsets(y, sr):
    """同じ台本の帯域差分の打点（そのまま。丸める前の時刻と強さ）。"""
    H2 = 64
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=H2)) ** 2
    f = librosa.fft_frequencies(sr=sr, n_fft=1024)
    ee = np.log1p(1000 * S[(f >= 30) & (f < 11000)].sum(0)); d = np.maximum(np.diff(ee, prepend=ee[0]), 0)
    t2 = librosa.frames_to_time(np.arange(len(d)), sr=sr, hop_length=H2) + 0.0187
    pk = librosa.util.peak_pick(d, pre_max=20, post_max=20, pre_avg=40, post_avg=40, delta=0.05, wait=20)
    ref = float(np.percentile(d[pk], 99))
    return [(float(t2[p]), float(d[p] / ref)) for p in pk]


def _production_pos(x, bar1, period):
    """同じ台本の小節と拍の位置（そのまま。拍は小数 2 桁）。"""
    b = (x - bar1) / period
    bar = int(np.floor(b / 4)) + 1; beat = b - 4 * (bar - 1) + 1
    return bar, round(float(beat), 2)


def test_ms_become_the_same_seconds_as_the_production_literals():
    assert 18.7 / 1000 != 0.0187                     # そのまま割ると 1 ulp 違う
    assert hits.seconds(18.7) == 0.0187 and hits.seconds(-16.7) == -0.0167 and hits.seconds(0) == 0.0


@pytest.mark.parametrize("rate", [22050, 44100])
def test_strong_and_onsets_equal_the_production_script(tmp_path, rate):
    audio = calibration_clicks(tmp_path / "clicks.wav", rate)
    record = hits.estimate(audio)
    y, sr = librosa.load(str(audio), sr=22050, mono=True)
    assert [h["t"] for h in record["strong"]] == _production_strong(y, sr)
    assert [(h["t"], h["strength"]) for h in record["onsets"]] == _production_onsets(y, sr)
    assert record["estimated"] is True and record["grid"] is None
    assert record["audio"] == {"sha256": sha256(audio), "sample_rate": rate, "channels": 2, "duration_s": 12.0}
    assert record["settings"]["strong"]["shift_ms"] == -16.7 and record["settings"]["onsets"]["shift_ms"] == 18.7


def test_the_default_shifts_put_the_hits_on_the_clicks(tmp_path):
    audio = calibration_clicks(tmp_path / "clicks.wav")

    def errors(found):
        return np.array([h["t"] - CLICKS[np.argmin(np.abs(CLICKS - h["t"]))] for h in found])

    raw, shifted = hits.estimate(audio, strong_shift_ms=0, onset_shift_ms=0), hits.estimate(audio)
    onset_raw, onset = errors(raw["onsets"]), errors(shifted["onsets"])
    near = np.abs(onset) < 0.1                           # 頭の 1 つは、音の始まり（0 秒）の立ち上がり
    assert near.sum() == len(CLICKS) and abs(onset[near].mean()) < 0.002 and np.abs(onset[near]).max() < 0.003
    assert onset_raw[near].mean() < -0.015              # 補正しなければ 18 ms ほど早い
    strong_raw, strong = errors(raw["strong"]), errors(shifted["strong"])
    assert len(strong) >= 10 and np.abs(strong).max() < 0.02 and abs(strong.mean()) < abs(strong_raw.mean())


def test_grid_positions_equal_the_production_formula(tmp_path):
    audio = calibration_clicks(tmp_path / "clicks.wav")
    grid.run(audio, bpm=60 / 0.34488, phase=1.0)
    record = hits.estimate(audio, grid=tmp_path / "clicks.grid.json")
    info = record["grid"]
    grid_record = json.loads((tmp_path / "clicks.grid.json").read_text(encoding="utf-8"))
    assert info == {"path": "clicks.grid.json", "sha256": sha256(tmp_path / "clicks.grid.json"), "period_s": grid_record["period_s"],
                    "beats_per_bar": 4, "first_bar_s": grid_record["downbeats"][0]}
    for hit in record["onsets"] + record["strong"]:
        assert (hit["bar"], round(hit["beat"], 2)) == _production_pos(hit["t"], info["first_bar_s"], info["period_s"])
    on_clicks = [h for h in record["onsets"] if np.min(np.abs(CLICKS - h["t"])) < 0.01]
    assert len(on_clicks) == len(CLICKS)
    downbeat = grid_record["downbeat_phase"]
    for hit in on_clicks:
        # クリック k（1.0 秒が 0）は、最初の小節の頭から k − downbeat 拍。小節の頭のわずかに前の打点は、台本の式のとおり
        # 前の小節の 5.0 拍目近くになるので、(小節 − 1) × 4 + (拍 − 1) の続いた値で比べる
        k = round((hit["t"] - 1.0) / info["period_s"]) - downbeat
        assert abs((hit["bar"] - 1) * 4 + hit["beat"] - 1 - k) < 0.01


def test_the_grid_must_be_a_grid_record_of_the_same_audio(tmp_path):
    audio = calibration_clicks(tmp_path / "clicks.wav")
    other = calibration_clicks(tmp_path / "other.wav", seconds=13)
    grid.run(other, bpm=120, phase=0.5)
    with pytest.raises(PipelineError, match="別の音源"):
        hits.estimate(audio, grid=tmp_path / "other.grid.json")
    (tmp_path / "plain.json").write_text(json.dumps({"beats_s": [0.5, 1.0]}), encoding="utf-8")
    with pytest.raises(PipelineError, match="mv audio grid の記録"):
        hits.estimate(audio, grid=tmp_path / "plain.json")
    with pytest.raises(PipelineError, match="見つかりません"):
        hits.estimate(audio, grid=tmp_path / "none.json")


def test_a_project_folder_writes_beside_analysis_json_and_never_replaces_inputs(tmp_path):
    project = tmp_path / "project"
    (project / "audio").mkdir(parents=True)
    audio = calibration_clicks(project / "audio" / "song.wav")
    (project / "analysis.json").write_text(json.dumps({"audio": {"path": "audio/song.wav", "sha256": sha256(audio)}}), encoding="utf-8")
    code, result, err = run_cli("audio", "hits", project)
    assert code == 0, err
    assert result["hits"] == str(project / "analysis.hits.json") and result["state"] == "written" and result["onsets"] >= len(CLICKS)
    assert json.loads((project / "analysis.hits.json").read_text(encoding="utf-8"))["audio"]["path"] == "audio/song.wav"
    before = (project / "analysis.json").read_bytes()
    code, _, err = run_cli("audio", "hits", project, "--out", project / "analysis.json", "--force")
    assert code == 1 and "--out がanalysis.jsonと同じ" in err and (project / "analysis.json").read_bytes() == before
    grid.run(project, bpm=120, phase=0.5)
    grid_before = (project / "analysis.grid.json").read_bytes()
    code, _, err = run_cli("audio", "hits", project, "--grid", project / "analysis.grid.json", "--out", project / "analysis.grid.json",
                           "--force")
    assert code == 1 and "--grid の記録と同じ" in err and (project / "analysis.grid.json").read_bytes() == grid_before


def test_a_rerun_is_unchanged_and_other_settings_need_force(tmp_path):
    audio = calibration_clicks(tmp_path / "clicks.wav")
    first, second = run_cli("audio", "hits", audio), run_cli("audio", "hits", audio)
    assert first[0] == 0 and second[0] == 0 and second[1]["state"] == "unchanged"
    code, _, err = run_cli("audio", "hits", audio, "--onset-shift-ms", "0")
    assert code == 1 and "--force" in err
    code, result, err = run_cli("audio", "hits", audio, "--onset-shift-ms", "0", "--force")
    assert code == 0 and result["state"] == "written" and result["onset_shift_ms"] == 0.0


def test_bad_inputs_are_refused(tmp_path):
    audio = calibration_clicks(tmp_path / "clicks.wav")
    for args, message in ((("--strong-shift-ms", "nan"), "--strong-shift-ms"), (("--onset-shift-ms", "2000"), "--onset-shift-ms")):
        code, _, err = run_cli("audio", "hits", audio, *args)
        assert code == 1 and message in err
    assert not (tmp_path / "clicks.hits.json").exists()
    code, _, err = run_cli("audio", "hits", tmp_path / "none.wav")
    assert code == 1 and "見つかりません" in err
    short = tmp_path / "short.wav"
    soundfile.write(str(short), np.zeros(int(0.5 * RATE), np.float32), RATE)
    code, _, err = run_cli("audio", "hits", short)
    assert code == 1 and "短すぎ" in err
    code, _, err = run_cli("audio", "hits", audio, "--out", audio)
    assert code == 1 and "音源と同じ" in err
