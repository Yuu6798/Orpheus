"""mv audio keys（打点から光の強さのキー）の検査。打点の記録は手で書き、spec は fx の合成の素材（librosa も cv2 も使わない）。

明滅の打点の選び方と量は、実制作の本編の描画の台本（稲妻）をここにそのまま写した関数（_production_lightning）と、
全コマで値まで同じになることを確かめる。キーは fx の補間でコマの時刻に読み戻して比べる。
"""
import json
import math
import wave

import pytest
from PIL import Image

from fx_synth import make_project, run_cli
from mv_pipeline.audio import keys
from mv_pipeline.fx import spec as fxspec
from mv_pipeline.fx import timeline
from mv_pipeline.io import PipelineError, sha256

HITS = [0.4, 1.3, 2.2, 2.5, 4.05, 4.9, 6.1, 7.2, 7.25, 9.0, 10.62, 12.3, 12.31, 14.0]
STRENGTH = [0.9, 0.4, 1.2, 0.3, 0.8, 0.5, 1.0, 0.7, 0.2, 0.6, 1.1, 0.9, 0.3, 0.75]


def _production_lightning(strong, high, extra):
    """実制作の本編の描画の台本の稲妻（打点の選び方と lightning_amount。そのまま）。"""
    def is_high(t): return any(a <= t < b for a, b in high)
    STRONG = []
    for s in strong:
        if (is_high(s) or any(a <= s < b for a, b in extra)) and (not STRONG or s - STRONG[-1] > 1.6):
            STRONG.append(s)

    def lightning_amount(t):
        v = 0.0
        for s in STRONG:
            dt = t - s
            if -0.01 <= dt < 0.5: v = max(v, math.exp(-dt * 14) + 0.5 * math.exp(-abs(dt - 0.12) * 40))
        return min(v, 1.2)
    return STRONG, lightning_amount


def _hits(times=HITS, strengths=STRENGTH):
    return [{"t": t, "strength": s} for t, s in zip(times, strengths)]


@pytest.mark.parametrize("high, extra", [([], []), ([(1.0, 8.0)], []), ([(0.0, 3.0), (6.0, 13.0)], [(3.0, 4.5)])])
def test_selection_and_amount_equal_the_production_lightning(high, extra):
    sections = high + extra
    strong, production = _production_lightning(HITS, high or [(-1e9, 1e9)], extra)
    kept = keys.select(_hits(), sections, 1.6)
    assert [h["t"] for h in kept] == strong
    for fps in (24, 30, 29.97):
        frames = int(16 * fps)
        assert [keys.amount(j / fps, kept) for j in range(frames)] == [production(j / fps) for j in range(frames)]


def test_keys_read_back_through_fx_interpolation():
    kept = keys.select(_hits(), [], 1.6)
    _, production = _production_lightning(HITS, [(-1e9, 1e9)], [])
    fps, frames = 24, 16 * 24
    made = keys.keys(kept, 0.0, frames, fps)
    assert len(made) < frames / 3 and made[0]["t"] == 0.0 and made[-1]["t"] == (frames - 1) / fps
    assert all(a["t"] < b["t"] for a, b in zip(made, made[1:]))
    back = [timeline.interp_scalar(made, timeline.frame_time(j, fps)) for j in range(frames)]
    assert max(abs(a - production(j / fps)) for j, a in enumerate(back)) < 1e-12
    shifted = keys.keys(kept, 2.0, 48, fps, base=0.12, gain=1.5)          # ショットの 0 秒が曲の 2.0 秒
    assert max(abs(timeline.interp_scalar(shifted, j / fps) - (0.12 + 1.5 * production(2.0 + j / fps))) for j in range(48)) < 1e-12


def test_min_strength_and_by_strength_are_options():
    kept = keys.select(_hits(), [], 1.6, min_strength=0.75)
    assert [h["t"] for h in kept] == [0.4, 2.2, 4.05, 6.1, 10.62, 12.3, 14.0]
    plain, scaled = keys.amount(4.05, kept), keys.amount(4.05, kept, by_strength=True)
    assert scaled == pytest.approx(0.8 * plain) and keys.amount(2.2, kept, by_strength=True) == 1.2      # 1.2 で頭打ち
    assert keys.amount(2.2 + 0.6, kept) == 0.0
    assert keys.amount(0.4 + 0.12, kept) < keys.amount(0.4, kept) and keys.amount(0.4 - 0.011, kept) == 0.0


def _write_wav(path, seconds=1.0, rate=8000, level=0):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(bytes([level, 0]) * int(seconds * rate))
    return path


def _project(tmp_path, offset=1.5, hit_times=(1.2, 2.0, 2.9, 9.0)):
    """fx の合成の spec（2 秒・12 fps、光 flame）に音を付け、同じ音の打点の記録を書く。"""
    spec = make_project(tmp_path, audio={"path": "audio.wav", "offset_s": offset})
    audio = _write_wav(tmp_path / "audio.wav")
    record = {"schema_version": "audio-hits/0.1", "estimated": True, "audio": {"path": "audio.wav", "sha256": sha256(audio)},
              "strong": [{"t": t, "strength": 1.0} for t in hit_times], "onsets": [{"t": 2.0, "strength": 0.5}], "grid": None}
    hits_path = tmp_path / "audio.hits.json"
    hits_path.write_text(json.dumps(record), encoding="utf-8")
    return spec, hits_path


def test_run_writes_a_copy_of_the_spec_with_one_light_keyed(tmp_path):
    spec, hits_path = _project(tmp_path)
    before = spec.read_bytes()
    code, result, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--base", "0.1")
    assert code == 0, err
    out = tmp_path / "spec_keys.json"
    assert result["spec"] == str(out) and result["state"] == "written" and result["start_s"] == 1.5
    assert result["frames"] == 24 and result["hits_used_s"] == [1.2, 2.9]      # 2.0 は 1.2 から 1.6 秒以内なので使わない
    assert spec.read_bytes() == before
    written, original = json.loads(out.read_text(encoding="utf-8")), json.loads(before)
    light = written["lights"][0]
    info = light.pop("x_audio_keys")
    assert info["start_s"] == 1.5 and info["hits_sha256"] == sha256(hits_path) and info["from"] == "strong" and info["base"] == 0.1
    made = light.pop("intensity")
    original["lights"][0].pop("intensity")
    assert written == original                            # 変わるのはその光の intensity と x_audio_keys だけ
    _, production = _production_lightning([1.2, 2.0, 2.9, 9.0], [(-1e9, 1e9)], [])
    for j in range(24):
        assert timeline.interp_scalar(made, j / 12) == pytest.approx(0.1 + production(1.5 + j / 12), abs=1e-12)
    loaded = fxspec.load_spec(out)
    assert [(k["t"], k["v"]) for k in loaded.data["lights"][0]["intensity"]] == [(k["t"], k["v"]) for k in made]
    again = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--base", "0.1")
    assert again[0] == 0 and again[1]["state"] == "unchanged"
    code, _, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame")
    assert code == 1 and "--force" in err
    code, result, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--force", "--from", "onsets")
    assert code == 0 and result["state"] == "written" and result["hits_used_s"] == [2.0]


def test_start_is_needed_when_the_spec_audio_is_another_file(tmp_path):
    spec, hits_path = _project(tmp_path)
    _write_wav(tmp_path / "audio.wav", level=3)            # 打点の記録と違う音
    code, _, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame")
    assert code == 1 and "--start" in err
    code, result, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--start", "1.8")
    assert code == 0 and result["start_s"] == 1.8 and result["hits_used_s"] == [2.9]
    (tmp_path / "audio.wav").unlink()                       # spec の音源が無いときも、落ちずに --start を求める
    code, _, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--force")
    assert code == 1 and "--start" in err


def test_refusals(tmp_path):
    spec, hits_path = _project(tmp_path)
    (tmp_path / "other").mkdir()
    cases = [
        (("--light", "sun"), "光 'sun' がありません"),
        (("--light", "flame", "--out", tmp_path / "other" / "spec_keys.json"), "同じフォルダ"),
        (("--light", "flame", "--out", spec, "--force"), "--out がspecと同じ"),
        (("--light", "flame", "--out", hits_path, "--force"), "--out が打点の記録と同じ"),
        (("--light", "flame", "--gain", "30"), "20"),
        (("--light", "flame", "--section", "3", "2"), "--section"),
        (("--light", "flame", "--base", "-1"), "--base"),
    ]
    for args, message in cases:
        code, _, err = run_cli("audio", "keys", hits_path, "--spec", spec, *args)
        assert code == 1 and message in err, (args, err)
    assert not (tmp_path / "spec_keys.json").exists() and not (tmp_path / "other" / "spec_keys.json").exists()
    (tmp_path / "plain.json").write_text(json.dumps({"strong": []}), encoding="utf-8")
    code, _, err = run_cli("audio", "keys", tmp_path / "plain.json", "--spec", spec, "--light", "flame")
    assert code == 1 and "audio-hits/0.1" in err
    with pytest.raises(PipelineError, match="--from"):
        keys.run(hits_path, spec, "flame", source="loud")


def test_the_spec_assets_are_never_replaced(tmp_path):
    # --out が spec の素材（音・画像・マスク・overlays）だと、--force で素材が JSON に置き換わっていた
    spec, hits_path = _project(tmp_path)
    data = json.loads(spec.read_text(encoding="utf-8"))
    data["schema_version"], data["overlays"] = fxspec.SCHEMA_VERSION, [{"image": "card.png", "start_s": 0.0, "end_s": 1.0}]
    spec.write_text(json.dumps(data), encoding="utf-8")
    Image.new("RGBA", (160, 90)).save(tmp_path / "card.png")
    for name, role in (("audio.wav", "audio"), ("base.png", "base"), ("depth.png", "depth"), ("mask_subject.png", "mask:subject"),
                       ("mask_ground.png", "mask:ground"), ("card.png", "overlays")):
        before = (tmp_path / name).read_bytes()
        code, _, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame", "--out", tmp_path / name, "--force")
        assert code == 1 and f"spec の素材（{role}）と同じ" in err and (tmp_path / name).read_bytes() == before, (name, err)
    code, result, err = run_cli("audio", "keys", hits_path, "--spec", spec, "--light", "flame")
    assert code == 0 and result["state"] == "written", err
