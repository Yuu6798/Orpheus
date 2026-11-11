"""mv gate voice・notes・repeat（分離ボーカルの数字）の検査。声はすべて合成（倍音のある音の旋律と、mv gate demo の声の代役）。

実制作の声の区間の台本（activity・segments）と、歌詞の時刻の台本（load・f0・notes・match）は、ここにそのまま写した関数
（_production_*）と、同じ音で値まで同じになることを確かめる。
"""
import json

import numpy as np
import pytest

librosa = pytest.importorskip("librosa")
soundfile = pytest.importorskip("soundfile")

from fx_synth import run_cli  # noqa: E402
from mv_pipeline.gate import vocals  # noqa: E402
from mv_pipeline.io import PipelineError  # noqa: E402

PHRASE = [67, 69, 71, 72, 71, 69]          # G4 A4 B4 C5 B4 A4、0.25 秒ずつ
NOTE_S = 0.25
STARTS = (1.0, 6.37)                       # 同じ旋律を 2 回（ずれ 5.37 秒）


def melody(path, rate=44100, seconds=10.0, starts=STARTS):
    """倍音のある音で旋律を 2 回歌う合成の声（左右同じ。床にごく小さな雑音）。"""
    def tone(midi, length):
        f = 440.0 * 2 ** ((midi - 69) / 12)
        t = np.arange(int(length * rate)) / rate
        envelope = np.minimum(1, np.minimum(t / 0.02, (length - t) / 0.03))
        return envelope * sum(0.5 / k * np.sin(2 * np.pi * k * f * t) for k in range(1, 6))
    y = np.zeros(int(seconds * rate))
    for start in starts:
        for k, midi in enumerate(PHRASE):
            i = int((start + k * NOTE_S) * rate)
            part = tone(midi, NOTE_S)
            y[i:i + len(part)] += part
    y += 0.0005 * np.random.default_rng(1).standard_normal(len(y))
    soundfile.write(str(path), np.stack([y, y], 1).astype(np.float32), rate, subtype="FLOAT")
    return path


# ---------------------------------------------------------------- 実制作の台本（そのまま写したもの）
def _production_activity(path, a=0.0, b=None, hop=0.01, thr=-32.0):
    y, sr = soundfile.read(path, dtype="float32")
    if y.ndim > 1:
        y = y.mean(1)
    if b is None:
        b = len(y) / sr
    y = y[int(a * sr):int(b * sr)]
    h = int(sr * hop)
    r = librosa.feature.rms(y=y, frame_length=int(sr * 0.03), hop_length=h)[0]
    e = 20 * np.log10(r + 1e-7)
    e -= np.percentile(e, 95)
    return e, e > thr, hop


def _production_segments(on, hop, a, gap=0.14, mn=0.08):
    segs = []
    i = 0
    n = len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            segs.append([a + i * hop, a + j * hop])
            i = j
        else:
            i += 1
    out = []
    for s in segs:
        if out and s[0] - out[-1][1] < gap:
            out[-1][1] = s[1]
        else:
            out.append(s)
    return [s for s in out if s[1] - s[0] >= mn]


def _production_load(path, a, b, sr_out=16000):
    sr = soundfile.info(path).samplerate
    y, _ = soundfile.read(path, start=int(a * sr), stop=int(b * sr), dtype='float32')
    if y.ndim > 1: y = y.mean(1)
    return librosa.resample(y, orig_sr=sr, target_sr=sr_out)


def _production_f0(path, a, b):
    y = _production_load(path, a, b)
    f, v, _ = librosa.pyin(y, fmin=150, fmax=1000, sr=16000, frame_length=1024, hop_length=160)
    return librosa.hz_to_midi(np.where(v, f, np.nan)), y


def _production_notes(path, a, b):
    """台本の notes の計算（表示の代わりに値を返す）。音符は (頭, 終わり, MIDI − 60)。"""
    m, y = _production_f0(path, a, b)
    o = librosa.onset.onset_strength(y=y, sr=16000, hop_length=160)
    on = librosa.onset.onset_detect(onset_envelope=o, sr=16000, hop_length=160, units='time', delta=0.12, wait=6) + a
    mm = np.round(m); out = []; cur = None; st = 0
    for i, x in enumerate(list(mm) + [np.nan]):
        if np.isnan(x) or cur is None or abs(x - cur) >= 1:
            if cur is not None and (i - st) >= 7: out.append((a + st * 0.01, a + i * 0.01, int(cur - 60)))
            cur = None if np.isnan(x) else x; st = i
    r = librosa.feature.rms(y=y, frame_length=640, hop_length=160)[0]; e = 20 * np.log10(r + 1e-6); e -= np.percentile(e, 95)
    low = e < -14; i = 0; dips = []
    while i < len(low):
        if low[i]:
            j = i
            while j < len(low) and low[j]: j += 1
            if j - i >= 6: dips.append((a + i * 0.01, a + j * 0.01))
            i = j
        else: i += 1
    return out, list(on), dips


def _production_match(path, ra, rb, ca, cb):
    ref, _ = _production_f0(path, ra, rb); c, _ = _production_f0(path, ca, cb); best = []
    for sh in range(0, len(c) - len(ref)):
        x = c[sh:sh + len(ref)]; ok = ~np.isnan(ref) & ~np.isnan(x)
        if ok.sum() < 0.3 * len(ref): continue
        d = np.abs(ref[ok] - x[ok]); d = np.minimum(d, np.abs(d - 12))
        best.append((round(float(np.mean(d < 1.0)), 3), round(ca + sh * 0.01 - ra, 2)))
    best.sort(reverse=True)
    return best[:3]


# ---------------------------------------------------------------- 実制作と同じ値
@pytest.fixture(scope="module", params=[44100, 48000])
def sung(request, tmp_path_factory):
    return melody(tmp_path_factory.mktemp(f"sung{request.param}") / "vocals.wav", rate=request.param)


def test_voice_equals_the_production_segments(sung):
    record = vocals.voice(sung)
    _, on, hop = _production_activity(str(sung))
    assert [(s["start"], s["end"]) for s in record["segments"]] == [tuple(s) for s in _production_segments(on, hop, 0.0)]
    part = vocals.voice(sung, 0.7, 8.0, threshold_db=-28, gap_s=0.05, min_s=0.2)
    _, on, hop = _production_activity(str(sung), 0.7, 8.0, thr=-28)
    assert [(s["start"], s["end"]) for s in part["segments"]] == [tuple(s) for s in _production_segments(on, hop, 0.7, 0.05, 0.2)]
    assert len(record["segments"]) == 2 and record["segments"][0]["gap_before_s"] is None
    assert record["segments"][1]["gap_before_s"] == pytest.approx(record["segments"][1]["start"] - record["segments"][0]["end"])


def test_notes_and_repeat_equal_the_production_script(sung):
    record = vocals.notes(sung, 0.5, 3.0)
    notes, onsets, dips = _production_notes(str(sung), 0.5, 3.0)
    assert [(n["start"], n["end"], n["midi"] - 60) for n in record["notes"]] == notes
    assert record["onsets_s"] == onsets and [(d["start"], d["end"]) for d in record["dips"]] == dips
    found = vocals.repeat(sung, (1.0, 2.5), (4.0, 9.0))
    assert [(m["rate"], m["offset_s"]) for m in found["matches"]] == _production_match(str(sung), 1.0, 2.5, 4.0, 9.0)


# ---------------------------------------------------------------- 合成の声で何が出るか
def test_notes_find_the_sung_pitches_and_the_repeat_offset(sung):
    record = vocals.notes(sung, 0.5, 3.0)
    assert [n["midi"] for n in record["notes"]] == PHRASE and record["notes"][0]["name"] == "G4"
    heads = [1.0 + k * NOTE_S for k in range(len(PHRASE))]
    assert max(abs(n["start"] - h) for n, h in zip(record["notes"], heads)) <= 0.025
    assert [round(d["start"], 2) for d in record["dips"]][:1] == [0.5] and record["dips"][-1]["start"] == pytest.approx(2.5, abs=0.03)
    found = vocals.repeat(sung, (1.0, 2.5), (4.0, 9.0))
    assert found["matches"][0] == {"rate": 1.0, "offset_s": round(STARTS[1] - STARTS[0], 2)}


def test_voice_heads_come_about_10_ms_early_on_the_demo_voice(tmp_path):
    # mv gate demo の声の代役（立ち上がり 30 ms）。窓 30 ms の中央の時刻で数えるので、区間の頭は正解の約 10 ms 前
    code, _, err = run_cli("gate", "demo", tmp_path / "demo")
    assert code == 0, err
    expected = json.loads((tmp_path / "demo" / "expected.json").read_text(encoding="utf-8"))
    record = vocals.voice(tmp_path / "demo" / "vocals.wav")
    truth = [line["true_onset"] for line in expected["lines"]]
    assert len(record["segments"]) == len(truth)
    assert [round(s["start"] - t, 3) for s, t in zip(record["segments"], truth)] == [-0.01] * len(truth)
    note = expected["vocals"]["note_s"]
    assert all(0.0 < s["end"] - (t + note) <= 0.0201 for s, t in zip(record["segments"], truth))     # 終わりは 10〜20 ms 後


def test_voice_times_use_the_real_hop_when_10_ms_is_not_a_whole_number_of_samples(tmp_path):
    # 22050 Hz では hop が 220 標本（0.009977 秒）。実制作の台本はコマを 0.01 秒と数えるので、曲の後ろほど遅れる
    # （声の基準は 95 パーセンタイルなので、声が曲の 5% より長くあるように 3 秒おきに歌う）
    path = melody(tmp_path / "vocals.wav", rate=22050, seconds=34.0, starts=tuple(1.0 + 3.0 * k for k in range(11)))
    record = vocals.voice(path)
    assert record["settings"]["step_s"] == 220 / 22050 and len(record["segments"]) == 11
    assert record["segments"][-1]["start"] == pytest.approx(31.0, abs=0.02)
    _, on, hop = _production_activity(str(path))
    assert _production_segments(on, hop, 0.0)[-1][0] == pytest.approx(31.07, abs=0.02)   # 台本の数え方では 70 ms 遅れる


# ---------------------------------------------------------------- CLI と書き出し
def test_cli_prints_the_record_and_writes_out_only_when_asked(tmp_path):
    path = melody(tmp_path / "vocals.wav")
    code, result, err = run_cli("gate", "voice", path)
    assert code == 0, err
    assert result["schema_version"] == "gate-voice/0.1" and result["out"] is None and result["state"] is None
    assert result["vocals"]["path"] == "vocals.wav" and len(result["segments"]) == 2
    assert sorted(p.name for p in tmp_path.iterdir()) == ["vocals.wav"]
    out = tmp_path / "voice.json"
    first = run_cli("gate", "voice", path, "--out", out)
    again = run_cli("gate", "voice", path, "--out", out)
    assert first[1]["state"] == "written" and again[1]["state"] == "unchanged"
    assert json.loads(out.read_text(encoding="utf-8"))["segments"] == first[1]["segments"]
    code, _, err = run_cli("gate", "voice", path, "--out", out, "--min-s", "0.5")
    assert code == 1 and "--force" in err
    assert run_cli("gate", "voice", path, "--out", out, "--min-s", "0.5", "--force")[1]["state"] == "written"
    before = path.read_bytes()
    code, _, err = run_cli("gate", "voice", path, "--out", path, "--force")
    assert code == 1 and "ボーカルと同じ" in err and path.read_bytes() == before
    code, result, err = run_cli("gate", "repeat", path, "--ref", "1.0", "2.5", "--search", "4", "9", "--top", "1")
    assert code == 0 and result["matches"] == [{"rate": 1.0, "offset_s": 5.37}]
    code, result, err = run_cli("gate", "notes", path, "--from", "6.2", "--to", "8.0")
    assert code == 0 and [n["name"] for n in result["notes"]] == ["G4", "A4", "B4", "C5", "B4", "A4"]


@pytest.mark.parametrize("args, message", [
    (("voice", "--from", "-1"), "0 秒以上"),
    (("voice", "--from", "11"), "より後"),
    (("voice", "--from", "2", "--to", "2.05"), "より長く"),
    (("voice", "--gap-s", "-0.1"), "--gap-s"),
    (("notes", "--from", "1", "--to", "1"), "より長く"),
    (("repeat", "--ref", "1", "3", "--search", "4", "5"), "--search の区間は --ref の区間より長く"),
    (("repeat", "--ref", "1", "2", "--search", "4", "9", "--top", "0"), "--top"),
])
def test_bad_ranges_are_refused(tmp_path, args, message):
    path = melody(tmp_path / "vocals.wav")
    code, _, err = run_cli("gate", args[0], path, *args[1:])
    assert code == 1 and message in err, err


def test_missing_vocals_are_refused(tmp_path):
    code, _, err = run_cli("gate", "voice", tmp_path / "none.wav")
    assert code == 1 and "見つかりません" in err
    with pytest.raises(PipelineError, match="見つかりません"):
        vocals.notes(tmp_path / "none.wav", 0, 1)
