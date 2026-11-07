"""`mv gate demo`: 正解の分かっている試験素材で、gate lines → gate check を端から端まで通す。"""
import builtins
import json

import numpy as np
import pytest
from fx_synth import run_cli

from mv_pipeline.gate import checks as gate
from mv_pipeline.gate import demo
from mv_pipeline.gate import lines as lines_table
from mv_pipeline.io import PipelineError, read_json


def _run(root, **kwargs):
    summary = demo.create(root, **kwargs)
    expected = read_json(root / "expected.json")
    lines_table.make_table(root / "lyrics.lrc", root / "lines", root / "vocals.wav")
    report, _ = gate.run(root / "gate.json")
    return summary, expected, report, {c["check"]: c for c in report["checks"]}


def test_default_material_reproduces_the_known_answers(tmp_path):
    """既定: 2 行目は 0.333 秒早く、4 行目は正解の 0.05 秒後。拍のフラッシュ 5 件は拍と照合して合格。"""
    summary, expected, report, by_name = _run(tmp_path / "d")
    assert expected["failing_lines"] == ["demo line two", "demo line four"] and expected["checks"] == {"歌詞の時刻": "fail", "拍": "pass"}
    assert summary["expected"] == expected["checks"]
    measured = read_json(tmp_path / "d/lines/lines_measured.json")
    assert [m["method"] for m in measured] == ["vocals_onset"] * 4
    for row, found in zip(expected["lines"], measured):
        assert abs(found["start"] - row["true_onset"]) <= expected["measure_tolerance_s"], (row, found)
    assert report["overall"] == "fail" == expected["overall"] and expected["exit_code"] == 1
    assert by_name["歌詞の時刻"]["status"] == "fail" and [p["text"] for p in by_name["歌詞の時刻"]["problems"]] == expected["failing_lines"]
    assert by_name["拍"]["status"] == "pass" and by_name["拍"]["summary"] == "5 件を拍と照合、ずれ 0"
    assert by_name["字の重なり"]["status"] == "pass" and by_name["UI 枠"]["status"] == "pass"


def test_shifts_and_flashes_are_configurable(tmp_path):
    """ずらしを 0 にすれば歌詞は合格、拍から外したフラッシュは不合格。expected と gate の結果が一致する。"""
    _, expected, report, by_name = _run(tmp_path / "d", shifts="0,0,0,0", flashes="5.5,6.0,6.2")
    assert expected["checks"] == {"歌詞の時刻": "pass", "拍": "fail"} and expected["failing_flashes"] == [6.2]
    assert by_name["歌詞の時刻"]["status"] == "pass" and by_name["拍"]["status"] == "fail"
    assert by_name["拍"]["problems"][0]["t"] == 6.2 and by_name["拍"]["problems"][0]["detail"].startswith("拍から 0.200 秒")


def test_downbeat_flashes_without_a_downbeat_grid_are_unseen(tmp_path):
    _, expected, report, by_name = _run(tmp_path / "d", downbeats=False, flash_align="downbeat", flashes="6.0", shifts="0,0,0,0")
    assert expected["checks"]["拍"] == "warn" and expected["flashes"][0]["status"] == "unseen"
    assert by_name["拍"]["status"] == "warn" and "見ていない 1 件" in by_name["拍"]["summary"]
    _, expected, _, by_name = _run(tmp_path / "e", downbeats=True, flash_align="downbeat", flashes="6.0,6.5", shifts="0,0,0,0")
    assert expected["failing_flashes"] == [6.5] and by_name["拍"]["problems"][0]["detail"].startswith("小節頭から 0.500 秒")


def test_a_line_at_the_edge_of_the_tolerance_is_uncertain_not_predicted(tmp_path):
    """宣言が正解の 0.0 秒前（lead 0）は推定の誤差（約 -20 ms）で合否が揺れるので、expected は uncertain にして予想しない。"""
    summary = demo.create(tmp_path / "d", shifts="0.1,0,0,0")
    expected = read_json(tmp_path / "d/expected.json")
    assert expected["lines"][0]["status"] == "uncertain" and expected["uncertain_lines"] == ["demo line one"]
    assert expected["checks"]["歌詞の時刻"] == "uncertain" and expected["overall"] == "uncertain" and expected["exit_code"] == 0
    assert summary["uncertain_lines"] == ["demo line one"]


def test_bad_arguments_are_errors(tmp_path):
    with pytest.raises(PipelineError, match="--shifts"):
        demo.create(tmp_path / "a", shifts="0,x")
    with pytest.raises(PipelineError, match="上限"):
        demo.create(tmp_path / "far", shifts="2.5,0,0,0")        # 探索窓の外。正解が見つからず「見ていない」になるので正解を名乗れない
    with pytest.raises(PipelineError, match="負"):
        demo.create(tmp_path / "neg", shifts="-0.5,0,0,0")
    with pytest.raises(PipelineError, match="フラッシュの時刻"):
        demo.create(tmp_path / "b", flashes="12.0")
    with pytest.raises(PipelineError, match="--duration"):
        demo.create(tmp_path / "c", duration=0.5)


def test_cli_writes_the_material_and_its_commands(tmp_path):
    code, result, err = run_cli("gate", "demo", tmp_path / "d", "--no-downbeats", "--attack-ms", "5", "--floor-db", "-25")
    assert code == 0, err
    assert result["downbeats"] is False and len(result["commands"]) == 2
    expected = read_json(tmp_path / "d/expected.json")
    assert expected["vocals"]["attack_ms"] == 5.0 and expected["vocals"]["floor_db"] == -25.0 and expected["beats"]["downbeats"] is False
    assert (tmp_path / "d/vocals.wav").is_file() and (tmp_path / "d/lyrics.lrc").read_text(encoding="utf-8").startswith("[00:00.450]")


def test_wav_is_read_without_soundfile(tmp_path, monkeypatch):
    """soundfile が無い環境（CI の python の job）でも、PCM の WAV は標準ライブラリで読めて、あれば soundfile と同じ包絡になる。"""
    demo.vocals(tmp_path / "v.wav", [1.0, 2.5], 4.0)
    try:
        import soundfile  # noqa: F401
        with_soundfile = lines_table.envelope(tmp_path / "v.wav")
    except (ModuleNotFoundError, OSError):
        with_soundfile = None
    real_import = builtins.__import__

    def no_soundfile(name, *args, **kwargs):
        if name == "soundfile":
            raise ModuleNotFoundError("No module named 'soundfile'", name="soundfile")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_soundfile)
    without = lines_table.envelope(tmp_path / "v.wav")
    assert 395 <= without.shape[0] <= 400 and without.max() == pytest.approx(0.0, abs=0.5)   # 10 ms 刻み、95% 点が 0 dB
    assert without[:int(0.9 / lines_table.HOP_S)].max() < -30                                                      # 歌の前は床の雑音
    if with_soundfile is not None:
        assert without.shape == with_soundfile.shape and np.allclose(without, with_soundfile, atol=1e-3)
    (tmp_path / "v.flac").write_bytes(b"fLaC")
    with pytest.raises(PipelineError, match="soundfile"):
        lines_table.envelope(tmp_path / "v.flac")
