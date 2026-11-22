"""mv gate final: 3D の描画から書き出した動画の検査の表（動画の構造は fx の probe.check_video、3D の側は model3d の報告のファイル）。
合成の小さな動画（音つき・音なし・色の印なし・最後が暗転）と、手で書いた render.show・check.video_frames の報告で確かめる。"""
import json
import subprocess

import numpy as np
import pytest
from fx_synth import run_cli

from mv_pipeline import encode, media
from mv_pipeline.gate import final
from mv_pipeline.io import PipelineError, atomic_json, sha256

FPS, SIZE, COUNT = 10, (32, 48), 12


def _pictures(last=None):
    frames = [np.full((SIZE[1], SIZE[0], 3), (40 + 10 * i, 120, 200), np.uint8) for i in range(COUNT)]
    if last is not None:
        frames[-1] = np.full((SIZE[1], SIZE[0], 3), last, np.uint8)
    return frames


def _video(path, audio=True, last=None):
    """render.assemble と同じく BT.709 の印を付けた動画（音は無音の AAC）。"""
    frames = _pictures(last)
    sound = {"silent": True, "duration": COUNT / FPS} if audio else None
    encode.write_video(path, iter(frames), SIZE, FPS, 18, len(frames), sound)
    return path


def _untagged(path):
    """色の印の無い動画（音つき）。"""
    data = b"".join(frame.tobytes() for frame in _pictures())
    subprocess.run([media.ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-f", "rawvideo", "-pix_fmt", "rgb24",
                    "-s", f"{SIZE[0]}x{SIZE[1]}", "-framerate", str(FPS), "-i", "pipe:0", "-f", "lavfi", "-t", str(COUNT / FPS),
                    "-i", "anullsrc=r=48000:cl=stereo", "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-frames:v", str(COUNT), str(path)], input=data, check=True, capture_output=True)
    return path


def _show(path, lines=0, overflow=True, seconds=6.0, rendered=COUNT, resolution=SIZE):
    report = {"schema_version": "model3d-show-render/0.1", "planned": [{"T": T, "f": T} for T in range(1, rendered + 1)],
              "rendered": list(range(1, rendered + 1)), "fps": FPS, "resolution": list(resolution), "seconds": seconds,
              "seconds_per_frame": round(seconds / rendered, 2)}
    if overflow:
        report["shadow_overflow"] = {"lines": lines, "worst": [6000, 4096] if lines else None, "action": "warn" if lines else "fail"}
    atomic_json(path, report)
    return path


def _video_frames(path, video, passed=True, digest=True):
    """check.video_frames の報告（video_sha256 は、書いたときの video の中身の sha256）。"""
    record = {"video_sha256": sha256(video)} if digest and video.is_file() else {}
    atomic_json(path, {"check": "video_frames", "status": "done", "pass": passed, "video": str(video), **record, "matched": 8 if passed else 6,
                       "mismatched": [] if passed else [5, 9], "indistinct": [], "shifts": [] if passed else [1],
                       "reason": "取り出したコマは、どれも同じ T のもとのコマにいちばん近い" if passed else "動画のコマが、もとのコマと合わない",
                       "samples": [1, 4, 8, 12]})
    return path


def _rows(report):
    return {row["check"]: row for row in report["checks"]}


def test_a_good_video_with_the_3d_reports_passes(tmp_path):
    video = _video(tmp_path / "final.mp4")
    report, out = final.run(video, None, _video_frames(tmp_path / "vf.json", video), [_show(tmp_path / "a.json"),
                                                                                     _show(tmp_path / "b.json", seconds=4.0)],
                            frames=COUNT)
    rows = _rows(report)
    assert report["status"] == "pass" and report["schema_version"] == "gate-final-report/0.1"
    assert out == tmp_path / "final_final_report" and (out / "final_report.json").is_file()
    assert rows["structure"]["status"] == "pass" and rows["structure"]["expected"] == {
        "width": 32, "height": 48, "fps": 10.0, "frames": COUNT, "audio": True, "audio_s": 1.2, "bt709": True}     # 大きさと fps は show から
    assert rows["video_frames"]["status"] == "pass" and rows["video_frames"]["matched"] == 8
    assert rows["last_frame"]["status"] == "pass" and rows["last_frame"]["index"] == COUNT - 1 and rows["last_frame"]["mean"] > 0.3
    assert rows["shadow_overflow"] == {**rows["shadow_overflow"], "status": "pass", "lines": 0}
    assert rows["render_seconds"]["status"] == "info" and rows["render_seconds"]["frames"] == 24
    assert rows["render_seconds"]["seconds"] == 10.0 and rows["render_seconds"]["seconds_per_frame"] == pytest.approx(0.42)
    assert report["video"]["frames"] == COUNT and report["video"]["audio"] is True


def test_a_video_without_sound_fails(tmp_path):
    video = _video(tmp_path / "mute.mp4", audio=False)
    report, _ = final.run(video, tmp_path / "out")
    assert report["status"] == "fail" and "音声ストリームがありません" in _rows(report)["structure"]["summary"]
    allowed, _ = final.run(video, tmp_path / "out2", audio=False, width=32, height=48, fps=FPS, frames=COUNT)   # 音の無い動画と決めたなら通る
    assert _rows(allowed)["structure"]["status"] == "pass"


def test_a_video_without_the_bt709_tags_fails(tmp_path):
    report, _ = final.run(_untagged(tmp_path / "plain.mp4"), tmp_path / "out")
    assert report["status"] == "fail" and "色タグ" in _rows(report)["structure"]["summary"]


def test_a_last_frame_faded_to_black_fails(tmp_path):
    video = _video(tmp_path / "fade.mp4", last=0)
    report, _ = final.run(video, tmp_path / "out")
    rows = _rows(report)
    assert report["status"] == "fail" and rows["last_frame"]["status"] == "fail" and rows["last_frame"]["mean"] < 0.02
    ending, _ = final.run(video, tmp_path / "out2", min_last_mean=0)                    # 暗転で終わる動画は 0 にする
    assert _rows(ending)["last_frame"]["status"] == "pass"


def test_missing_reports_make_the_table_incomplete_not_pass(tmp_path):
    video = _video(tmp_path / "v.mp4")
    report, _ = final.run(video, tmp_path / "out")
    rows = _rows(report)
    assert report["status"] == "incomplete"
    assert rows["video_frames"]["status"] == rows["shadow_overflow"]["status"] == rows["render_seconds"]["status"] == "not_run"
    assert rows["structure"]["status"] == "partial"                                      # 期待を渡していないので、見ていない所がある
    assert rows["structure"]["unchecked"] == ["width", "height", "fps", "frames", "audio_s"]
    old, _ = final.run(video, tmp_path / "out2", _video_frames(tmp_path / "vf.json", video), [_show(tmp_path / "s.json", overflow=False)])
    assert old["status"] == "incomplete" and _rows(old)["shadow_overflow"]["status"] == "not_recorded"   # あふれを数える前の報告


def test_shadow_overflow_lines_fail(tmp_path):
    video = _video(tmp_path / "v.mp4")
    report, _ = final.run(video, tmp_path / "out", _video_frames(tmp_path / "vf.json", video),
                          [_show(tmp_path / "a.json"), _show(tmp_path / "b.json", lines=3)])
    shadow = _rows(report)["shadow_overflow"]
    assert report["status"] == "fail" and shadow["lines"] == 3 and shadow["worst"] == [6000, 4096]
    assert shadow["with_lines"] == [str(tmp_path / "b.json")]


def test_the_video_frames_report_must_be_for_this_video(tmp_path):
    video, other = _video(tmp_path / "v.mp4"), _video(tmp_path / "w.mp4", last=255)
    report, _ = final.run(video, tmp_path / "out", _video_frames(tmp_path / "vf.json", other))
    assert _rows(report)["video_frames"]["status"] == "fail" and "違います" in _rows(report)["video_frames"]["summary"]
    failed, _ = final.run(video, tmp_path / "out3", _video_frames(tmp_path / "vf3.json", video, passed=False))
    assert failed["status"] == "fail" and _rows(failed)["video_frames"]["mismatched"] == [5, 9]
    copy = tmp_path / "copy.mp4"                                                         # 同じ中身の写しの報告は同じ動画とみなす
    copy.write_bytes(video.read_bytes())
    same, _ = final.run(video, tmp_path / "out4", _video_frames(tmp_path / "vf4.json", copy))
    assert _rows(same)["video_frames"]["status"] == "pass"
    old = _video_frames(tmp_path / "vf5.json", video)                                    # 同じ名前に書き出し直すと、古い報告は合わない
    _video(video, last=255)
    stale, _ = final.run(video, tmp_path / "out5", old)
    assert _rows(stale)["video_frames"]["status"] == "fail"
    bare, _ = final.run(video, tmp_path / "out6", _video_frames(tmp_path / "vf6.json", video, digest=False))
    assert _rows(bare)["video_frames"]["status"] == "not_recorded" and bare["status"] in ("incomplete", "fail")   # sha256 の無い報告


def test_the_expected_size_and_frames_are_checked(tmp_path):
    video = _video(tmp_path / "v.mp4")
    report, _ = final.run(video, tmp_path / "out", shows=[_show(tmp_path / "s.json", resolution=(64, 96))])
    assert "寸法が違います" in _rows(report)["structure"]["summary"]                   # show の大きさと違う
    counted, _ = final.run(video, tmp_path / "out2", frames=COUNT + 1, fps=FPS)
    assert "フレーム数が違います" in _rows(counted)["structure"]["summary"]
    short, _ = final.run(video, tmp_path / "out3", shows=[_show(tmp_path / "t.json", rendered=COUNT - 2)])
    assert "フレーム数が違います" in _rows(short)["structure"]["summary"]              # コマの数も show の描いた T から
    filled, _ = final.run(video, tmp_path / "out4", shows=[_show(tmp_path / "u.json")])
    structure = _rows(filled)["structure"]
    assert structure["status"] == "pass" and structure["filled_from"] == {"width": "show", "height": "show", "fps": "show", "frames": "show"}
    wide, _ = final.run(video, tmp_path / "out5", shows=[_show(tmp_path / "w.json", resolution=(32, 96))], width=32)
    assert "寸法が違います" in _rows(wide)["structure"]["summary"]                     # 幅だけ渡しても、高さは show から埋める
    assert _rows(wide)["structure"]["expected"]["height"] == 96


def test_bad_inputs_stop(tmp_path):
    video = _video(tmp_path / "v.mp4")
    with pytest.raises(PipelineError, match="動画が見つかりません"):
        final.run(tmp_path / "nothing.mp4")
    atomic_json(tmp_path / "x.json", {"schema_version": "model3d-frames/0.1"})
    with pytest.raises(PipelineError, match="render.show"):
        final.run(video, tmp_path / "out", shows=[tmp_path / "x.json"])
    with pytest.raises(PipelineError, match="check.video_frames"):
        final.run(video, tmp_path / "out", tmp_path / "x.json")
    (tmp_path / "list.json").write_text("[1, 2]", encoding="utf-8")
    (tmp_path / "text.json").write_text('"report"', encoding="utf-8")
    for bad in ("list.json", "text.json"):                                               # 辞書でない JSON は分かる言葉で止める
        with pytest.raises(PipelineError, match="形が違います"):
            final.run(video, tmp_path / "out", tmp_path / bad)
        with pytest.raises(PipelineError, match="形が違います"):
            final.run(video, tmp_path / "out", shows=[tmp_path / bad])
    with pytest.raises(PipelineError, match="min-last-mean"):
        final.run(video, tmp_path / "out", min_last_mean=2)
    with pytest.raises(PipelineError, match="--frames"):
        final.run(video, tmp_path / "out", frames=0)


def test_the_command_exits_1_on_a_failure(tmp_path):
    video = _video(tmp_path / "v.mp4")
    code, result, _ = run_cli("gate", "final", video, "--video-frames", _video_frames(tmp_path / "vf.json", video),
                              "--show", _show(tmp_path / "a.json"), "--show", _show(tmp_path / "b.json"), "--frames", COUNT)
    assert code == 0 and result["status"] == "pass" and [row["check"] for row in result["checks"]] == [
        "structure", "video_frames", "last_frame", "shadow_overflow", "render_seconds"]
    assert json.loads((tmp_path / "v_final_report" / "final_report.json").read_text(encoding="utf-8"))["status"] == "pass"
    code, result, _ = run_cli("gate", "final", _video(tmp_path / "m.mp4", audio=False), "--out", tmp_path / "o")
    assert code == 1 and result["status"] == "fail"
    code, result, _ = run_cli("gate", "final", _video(tmp_path / "n.mp4", audio=False), "--no-audio", "--out", tmp_path / "o2")
    assert code == 0 and result["status"] == "incomplete"
