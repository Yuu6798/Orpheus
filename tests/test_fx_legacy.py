import json
import os
import sys
import textwrap
import threading
import time

import pytest

from fx_synth import run_cli
from mv_pipeline.fx import legacy, probe
from mv_pipeline.io import PipelineError, read_json, sha256

# fx legacy / fx accept は opencv を使わない。fx extra なしの環境でも動くことを、importorskip せずに確かめる。

# リポジトリに入れない実制作レンダラの代役: `renderer CONFIG seg START END OUT` で短い MP4 を書く。
# 設定で枚数の不足・超過・終了コード・待ち時間・出力なしを指定できる。
FAKE = textwrap.dedent('''
    import json, os, subprocess, sys, time
    import imageio_ffmpeg

    config, seg, start, end, out = sys.argv[1:6]
    assert seg == "seg"
    cfg = json.load(open(config))
    fps, width, height = cfg.get("fps", 12), cfg.get("width", 64), cfg.get("height", 36)
    print("cwd=" + os.getcwd())
    print("utf8_mode=%d" % sys.flags.utf8_mode)
    print("phase1 start=%s end=%s" % (start, end))   # flush しない: 逐次ログは fx が PYTHONUNBUFFERED を付けて初めて読める
    if cfg.get("trace_dir"):                          # 区間ごとの始まりと終わりの時刻（同時に動いた本数を数える）
        open(os.path.join(cfg["trace_dir"], start + ".start"), "w").write(repr(time.time()))
    if cfg.get("fail_start") == start:                # この区間だけすぐ失敗する
        print("boom: segment failed", file=sys.stderr)
        sys.exit(3)
    if cfg.get("child_pid_file"):
        child = subprocess.Popen([sys.executable, "-c", "import os, time; open(%r, 'w').write(str(os.getpid())); time.sleep(60)" % cfg["child_pid_file"]])
        open(cfg["child_pid_file"] + ".parent", "w").write(str(os.getpid()))
    time.sleep(cfg.get("sleep", 0))
    if cfg.get("done_file"):
        open(cfg["done_file"], "w").write("done")   # 「もう終わる」印。これより前に phase1 が読めれば、ログは逐次書かれている
    if cfg.get("exit"):
        print("boom: something broke", file=sys.stderr)
        sys.exit(cfg["exit"])
    if cfg.get("no_output"):
        sys.exit(0)
    if cfg.get("units") == "frames":   # 実制作レンダラと同じく、フレーム番号を int で読み range(start, end) を描く
        frames = int(end) - int(start) + cfg.get("extra", 0)
    else:
        frames = round((float(end) - float(start)) * fps) + cfg.get("extra", 0)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (width, height), "-framerate", str(fps), "-i", "pipe:0",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE)
    for i in range(frames):
        proc.stdin.write(bytes((i * 11 + k) % 256 for k in range(width * height * 3)))
    proc.stdin.close()
    assert proc.wait() == 0
    if cfg.get("trace_dir"):
        open(os.path.join(cfg["trace_dir"], start + ".end"), "w").write(repr(time.time()))
''')


@pytest.fixture
def work(tmp_path):
    """レンダラ（リポジトリ外の別ディレクトリ）と設定を置いた作業場所。"""
    studio = tmp_path / "studio"
    studio.mkdir()
    (studio / "render_fake.py").write_text(FAKE, encoding="utf-8")

    def config(**values):
        path = studio / "config.json"
        path.write_text(json.dumps(values), encoding="utf-8")
        return path

    config()
    return studio, config


def legacy_cli(studio, *extra, out=None):
    return run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json",
                   "--duration", "1", "--out", out or studio / "runs", *extra)


def test_success_records_hashes_argv_exit_code_and_log(work):
    studio, config = work
    config(fps=12, sleep=0)
    code, result, err = legacy_cli(studio, "--start", "0.5")
    assert code == 0, err
    run_dir = studio / "runs" / result["run_id"]
    manifest = read_json(run_dir / "manifest.json")
    info = manifest["legacy"]
    assert manifest["kind"] == "legacy" and manifest["status"] == "succeeded" and manifest["technical_pass"] is True
    assert info["renderer"] == {"name": "render_fake.py", "sha256": sha256(studio / "render_fake.py")}
    assert info["config"] == {"name": "config.json", "sha256": sha256(studio / "config.json")}
    assert info["argv"][:7] == [sys.executable, str(studio / "render_fake.py"), str(studio / "config.json"), "seg", "0.5", "1.5",
                                str(run_dir / "legacy.part.mp4")]
    assert info["exit_code"] == 0 and info["seconds"] > 0 and info["cacheable"] is False
    assert (run_dir / "legacy.mp4").is_file() and not (run_dir / "legacy.part.mp4").exists()
    item = manifest["variants"]["legacy"]
    assert item["path"] == "legacy.mp4" and item["frames"] == 12 and item["sha256"] == sha256(run_dir / "legacy.mp4")
    assert result["output"] == str(run_dir / "legacy.mp4") and result["frames"] == 12 and result["exit_code"] == 0
    log = (run_dir / "legacy.log").read_text(encoding="utf-8")
    assert "phase1 start=0.5 end=1.5" in log and log.startswith("20")                       # 子の出力が legacy.log に入る
    assert probe.decode(run_dir / "legacy.mp4", "v")["frames"] == 12
    assert "cwd=" + str(studio) in log                                                      # 既定の作業ディレクトリ = レンダラのある場所


def test_cwd_can_be_overridden_and_default_out_is_next_to_the_config(work, tmp_path):
    studio, config = work
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    code, result, err = run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json",
                                "--duration", "0.5", "--cwd", elsewhere)
    assert code == 0, err
    assert result["run_dir"].startswith(str(studio / "fx_runs"))
    assert "cwd=" + str(elsewhere) in (studio / "fx_runs" / result["run_id"] / "legacy.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("extra, passes", [(0, True), (-1, True), (-2, True), (-3, False), (1, False)])
def test_frame_count_may_be_up_to_two_short_but_never_over(work, extra, passes):
    studio, config = work
    config(fps=12, extra=extra)
    code, result, err = legacy_cli(studio)
    runs_dir = studio / "runs"
    (run_dir,) = [d for d in runs_dir.iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["variants"]["legacy"]["frames"] == 12 + extra
    if passes:
        assert code == 0 and manifest["status"] == "succeeded" and (run_dir / "legacy.mp4").is_file()
    else:
        assert code == 1 and "フレーム数" in err and manifest["status"] == "failed" and manifest["technical_pass"] is False
        assert "フレーム数" in manifest["error"]                                              # 失敗の理由が manifest に残る
        assert not (run_dir / "legacy.mp4").exists() and not (run_dir / "legacy.part.mp4").exists()
        assert manifest["variants"]["legacy"]["path"] == "legacy.part.mp4"


def test_size_and_fps_are_checked_only_when_given(work):
    studio, config = work
    config(fps=12, width=64, height=36)
    assert legacy_cli(studio)[0] == 0
    assert legacy_cli(studio, "--width", "64", "--height", "36", "--fps", "12")[0] == 0
    for flags, text in ((("--width", "128"), "寸法"), (("--height", "40"), "寸法"), (("--fps", "24"), "fps")):
        code, _, err = legacy_cli(studio, *flags)
        assert code == 1 and text in err, flags


def test_expected_frames_follow_the_observed_fps_when_fps_is_not_given(work):
    studio, config = work
    config(fps=25)
    code, result, _ = legacy_cli(studio)
    assert code == 0 and result["frames"] == 25


def test_unreadable_fps_without_the_fps_option_is_a_failure_not_a_silent_pass(work, monkeypatch):
    studio, config = work
    config(fps=12, extra=-1)                                                                 # 枚数は不足しているが、期待枚数が決まらなければ検査できない
    real = probe.inspect

    def blind(path):                                                                         # 出力から fps を読めなかった場合
        info = real(path)
        info["video"]["fps"] = None
        return info

    monkeypatch.setattr(probe, "inspect", blind)
    code, _, err = legacy_cli(studio)
    assert code == 1 and "fps が分からない" in err and "--fps" in err
    (run_dir,) = [d for d in (studio / "runs").iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    item = manifest["variants"]["legacy"]
    assert manifest["status"] == "failed" and manifest["technical_pass"] is False and item["technical_pass"] is False
    assert any("fps が分からない" in f for f in item["checks"]["failures"]) and "fps が分からない" in manifest["error"]
    assert not (run_dir / "legacy.mp4").exists()                                             # 合格していないので最終名を作らない
    assert legacy._expected_frames(1.0, None, None) is None and legacy._expected_frames(1.0, 12.0, None) == (10, 12)


def blind_fps(monkeypatch):
    """出力から fps を読めない状況（inspect が fps=None を返す）にする。"""
    real = probe.inspect

    def blind(path):
        info = real(path)
        info["video"]["fps"] = None
        return info

    monkeypatch.setattr(probe, "inspect", blind)


def test_unreadable_fps_passes_with_the_fps_option_and_checks_only_the_frame_count(work, monkeypatch):
    studio, config = work
    config(fps=12)
    blind_fps(monkeypatch)
    code, _, err = legacy_cli(studio)                                                        # 指定なし: 従来どおり不合格
    assert code == 1 and "fps が分からない" in err and "--fps" in err
    code, result, err = legacy_cli(studio, "--fps", "12")                                    # 指定あり: 案内どおりに合格する
    assert code == 0, err
    manifest = read_json(studio / "runs" / result["run_id"] / "manifest.json")
    item = manifest["variants"]["legacy"]
    assert manifest["status"] == "succeeded" and item["technical_pass"] is True and item["frames"] == 12
    assert any("fps の照合を省" in w for w in manifest["warnings"])                           # 省いたことは記録に残る
    config(fps=12, extra=-3)                                                                 # フレーム数は指定した fps で照合する
    code, _, err = legacy_cli(studio, "--fps", "12")
    assert code == 1 and "フレーム数" in err and "fps が違います" not in err


def test_readable_fps_is_still_compared_with_the_fps_option(work):
    studio, config = work
    config(fps=12)
    code, _, err = legacy_cli(studio, "--fps", "24")
    assert code == 1 and "fps が違います" in err


def test_nonzero_exit_fails_without_touching_earlier_runs(work):
    studio, config = work
    config(fps=12)
    good = legacy_cli(studio)[1]
    good_dir = studio / "runs" / good["run_id"]
    before = {p.name: p.read_bytes() for p in good_dir.iterdir()}
    index_before = (studio / "runs" / "index.json").exists()
    config(fps=12, exit=3)
    code, result, err = legacy_cli(studio)
    assert code == 1 and result is None and "終了コード 3" in err and "boom: something broke" in err
    assert {p.name: p.read_bytes() for p in good_dir.iterdir()} == before                    # 過去の run はバイト一致
    (bad,) = [d for d in (studio / "runs").iterdir() if d.is_dir() and d.name != good["run_id"]]
    manifest = read_json(bad / "manifest.json")
    assert manifest["status"] == "failed" and manifest["legacy"]["exit_code"] == 3 and "終了コード 3" in manifest["error"]
    assert not (bad / "legacy.mp4").exists() and "boom" in (bad / "legacy.log").read_text(encoding="utf-8")
    assert (studio / "runs" / "index.json").exists() == index_before


def test_missing_output_is_a_failure(work):
    studio, config = work
    config(no_output=True)
    code, _, err = legacy_cli(studio)
    assert code == 1 and "出力" in err
    (run_dir,) = [d for d in (studio / "runs").iterdir() if d.is_dir()]
    assert read_json(run_dir / "manifest.json")["status"] == "failed"


@pytest.mark.skipif(os.name == "nt", reason="プロセスグループの検査は POSIX のみ")
def test_timeout_stops_the_renderer_and_its_children(work):
    studio, config = work
    pid_file = studio / "child.pid"
    config(sleep=30, child_pid_file=str(pid_file))
    started = time.monotonic()
    code, _, err = legacy_cli(studio, "--timeout", "2")
    assert code == 1 and "timeout" in err and time.monotonic() - started < 20
    (run_dir,) = [d for d in (studio / "runs").iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["status"] == "failed" and "timeout" in manifest["error"] and manifest["legacy"]["timeout_s"] == 2.0
    assert not (run_dir / "legacy.mp4").exists()
    for path in (pid_file, str(pid_file) + ".parent"):
        pid = int(open(path).read())
        for _ in range(50):                                                                  # 孫プロセスも止まる
            try:
                os.kill(pid, 0)
                time.sleep(0.1)
            except ProcessLookupError:
                break
        else:
            pytest.fail(f"process {pid} is still alive")


def test_default_timeout_scales_with_duration():
    assert legacy.default_timeout(1) == 600 and legacy.default_timeout(5) == 600 and legacy.default_timeout(10) == 1200


def test_log_is_written_while_the_renderer_is_still_running(work, monkeypatch):
    monkeypatch.delenv("PYTHONUNBUFFERED", raising=False)                                    # 環境の設定に頼らず、fx が付けることを確かめる
    studio, config = work
    done = studio / "renderer.done"
    config(fps=12, sleep=1.5, done_file=str(done))
    holder = {}
    thread = threading.Thread(target=lambda: holder.update(result=legacy_cli(studio)))
    thread.start()
    seen, finished_already = False, None
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and thread.is_alive():
        logs = list((studio / "runs").glob("*/legacy.log"))
        if logs and "phase1" in logs[0].read_text(encoding="utf-8", errors="replace"):
            seen, finished_already = True, done.exists()
            break
        time.sleep(0.02)
    thread.join()
    assert holder["result"][0] == 0 and seen
    assert finished_already is False                                                         # レンダラが終わる前に phase1 が読めた


def test_legacy_runs_are_never_cached_and_can_be_accepted(work):
    studio, config = work
    config(fps=12)
    first, second = legacy_cli(studio)[1], legacy_cli(studio)[1]
    assert first["run_id"] != second["run_id"] and not (studio / "runs" / "index.json").exists()
    code, record, err = run_cli("fx", "accept", studio / "runs" / second["run_id"], "--variant", "legacy",
                                "--decision", "accepted", "--by", "reviewer")
    assert code == 0, err
    assert record["video_sha256"] == read_json(studio / "runs" / second["run_id"] / "manifest.json")["variants"]["legacy"]["sha256"]
    assert (studio / "runs" / "acceptance.jsonl").is_file()


def test_bad_arguments_and_unrunnable_python_are_reported(work, tmp_path):
    studio, config = work
    code, _, err = run_cli("fx", "legacy", "--renderer", tmp_path / "none.py", "--config", studio / "config.json", "--duration", "1")
    assert code == 1 and "--renderer" in err
    code, _, err = run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", tmp_path / "none.json", "--duration", "1")
    assert code == 1 and "--config" in err
    code, _, err = legacy_cli(studio, "--python", str(tmp_path / "no-such-python"))
    assert code == 1 and "--python" in err
    for flags in (("--timeout", "0"), ("--start", "-1")):
        assert legacy_cli(studio, *flags)[0] == 1
    code, _, err = run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json", "--duration", "0")
    assert code == 1
    with pytest.raises(PipelineError):
        legacy.run_legacy(studio / "render_fake.py", studio / "config.json", 0, 1, cwd=tmp_path / "no-such-dir")


def test_frame_units_pass_integer_frame_numbers_with_the_end_excluded(work):
    studio, config = work
    config(fps=12, sleep=0, units="frames")
    code, result, err = legacy_cli(studio, "--start", "0.5", "--fps", "12", "--units", "frames")
    assert code == 0, err
    manifest = read_json(studio / "runs" / result["run_id"] / "manifest.json")
    assert manifest["legacy"]["argv"][3:6] == ["seg", "6", "18"]                 # 0.5 秒 = 6、1 秒 = 12 フレーム
    assert manifest["interval"]["units"] == "frames" and manifest["interval"]["frame_range"] == [6, 18]
    assert manifest["technical_pass"] is True and result["frames"] == 12


def test_frame_units_need_fps(work):
    studio, _ = work
    code, _, err = legacy_cli(studio, "--units", "frames")
    assert code == 1 and "--fps" in err
    with pytest.raises(PipelineError, match="units"):
        legacy.run_legacy(studio / "render_fake.py", studio / "config.json", 0, 1, fps=12, units="minutes")


def test_numbers_are_passed_without_float_noise():
    assert legacy._number(0) == "0.0" and legacy._number(0.1 + 0.2) == "0.3" and legacy._number(10) == "10.0"


# ---- 区間を並べて描く（--cuts） -------------------------------------------------------------------------

def cuts_cli(studio, *cuts, extra=(), out=None):
    return run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json",
                   "--fps", "12", "--units", "frames", "--cuts", *[str(c) for c in cuts], "--out", out or studio / "runs", *extra)


def test_cuts_render_every_segment_in_one_run_and_the_folder_joins(work, tmp_path):
    studio, config = work
    config(fps=12, units="frames")
    code, result, err = cuts_cli(studio, 0, 5, 12, 20, extra=("--jobs", "2"))
    assert code == 0, err
    run_dir = studio / "runs" / result["run_id"]
    names = ["seg_00000_00005", "seg_00005_00012", "seg_00012_00020"]
    assert [os.path.basename(s["file"]) for s in result["segments"]] == [n + ".mp4" for n in names]
    assert [s["frames"] for s in result["segments"]] == [5, 7, 8] and result["frames"] == 20 and result["jobs"] == 2
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["kind"] == "legacy" and manifest["status"] == "succeeded" and manifest["technical_pass"] is True
    assert manifest["variants"] == {} and manifest["legacy"]["jobs"] == 2 and manifest["legacy"]["exit_code"] == 0
    assert manifest["interval"] == {"start": 0.0, "duration": 20 / 12, "fps": 12.0, "frames": 20, "units": "frames",
                                    "frame_range": [0, 20], "cuts": [0, 5, 12, 20]}
    for name, item, (a, b) in zip(names, manifest["segments"], [(0, 5), (5, 12), (12, 20)]):
        assert item["file"] == name + ".mp4" and item["log"] == name + ".log" and item["frame_range"] == [a, b]
        assert item["argv"] == [sys.executable, str(studio / "render_fake.py"), str(studio / "config.json"), "seg", str(a), str(b),
                                str(run_dir / f"partial_{a:05d}_{b:05d}.mp4")]
        assert item["exit_code"] == 0 and item["error"] is None and item["technical_pass"] is True and item["frames"] == b - a
        assert item["sha256"] == sha256(run_dir / item["file"]) and probe.decode(run_dir / item["file"], "v")["frames"] == b - a
        log = (run_dir / item["log"]).read_text(encoding="utf-8")
        assert log.startswith("20") and f" $ {sys.executable}" in log and f"phase1 start={a} end={b}" in log
    assert not list(run_dir.glob("partial_*")) and not (studio / "runs" / "index.json").exists()   # キャッシュしない
    code, joined, err = run_cli("deliver", "join", run_dir, "--out", tmp_path / "master.mp4")
    assert code == 0, err
    assert joined["frames"] == 20 and joined["segments"] == 3 and joined["technical_pass"] is True


def test_cuts_run_at_most_jobs_segments_at_once(work, tmp_path):
    studio, config = work
    trace = tmp_path / "trace"
    trace.mkdir()
    config(fps=12, units="frames", sleep=1.5, trace_dir=str(trace))
    code, result, err = cuts_cli(studio, 0, 2, 4, 6, 8, extra=("--jobs", "2"))
    assert code == 0, err
    spans = [(float((trace / f"{a}.start").read_text()), float((trace / f"{a}.end").read_text())) for a in (0, 2, 4, 6)]
    most = max(sum(1 for s, e in spans if s <= t < e) for t, _ in spans)
    assert most == 2                                                          # 2 本ずつ（1 本ずつでも、全部同時でもない）


def test_a_failed_segment_stops_the_others_and_leaves_no_segment_videos(work):
    studio, config = work
    config(fps=12, units="frames", sleep=8, fail_start="5")
    started = time.monotonic()
    code, result, err = cuts_cli(studio, 0, 5, 12, 20, extra=("--jobs", "2"))
    assert code == 1 and result is None and "区間 5-12" in err and "終了コード 3" in err and "boom: segment failed" in err
    assert time.monotonic() - started < 7                                     # 眠っている区間 0-5 を待たずに止めた
    (run_dir,) = [d for d in (studio / "runs").iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["status"] == "failed" and "区間 5-12" in manifest["error"] and manifest["legacy"]["exit_code"] == 3
    first, failed, never = manifest["segments"]
    assert failed["exit_code"] == 3 and "終了コード 3" in failed["error"]
    assert first["exit_code"] is None and first["error"] == "ほかの区間が失敗したため止めました"
    assert never["exit_code"] is None and never["error"] == "ほかの区間が失敗したため描いていません"
    assert not list(run_dir.glob("*.mp4"))                                    # 最終名も途中の出力も残さない
    assert (run_dir / "seg_00005_00012.log").is_file() and not (run_dir / "seg_00012_00020.log").exists()


def test_segment_frame_counts_must_be_exact(work):
    studio, config = work
    config(fps=12, units="frames", extra=-1)                                  # 1 本のときは 2 コマ少ないまで通るが、区間は通さない
    code, _, err = cuts_cli(studio, 0, 6, 12)
    assert code == 1 and "技術検査" in err and "フレーム数が違います" in err
    (run_dir,) = [d for d in (studio / "runs").iterdir() if d.is_dir()]
    manifest = read_json(run_dir / "manifest.json")
    assert manifest["status"] == "failed" and [s["technical_pass"] for s in manifest["segments"]] == [False, False]
    assert not list(run_dir.glob("*.mp4"))


def test_segment_names_widen_past_five_digits(work):
    studio, config = work
    config(fps=12, units="frames")
    code, result, err = cuts_cli(studio, 99998, 100000, 100003)
    assert code == 0, err
    assert [os.path.basename(s["file"]) for s in result["segments"]] == ["seg_099998_100000.mp4", "seg_100000_100003.mp4"]


def test_python_flags_go_between_the_interpreter_and_the_renderer(work):
    studio, config = work
    config(fps=12)
    code, result, err = legacy_cli(studio, "--python-flags=-X utf8")
    assert code == 0, err
    manifest = read_json(studio / "runs" / result["run_id"] / "manifest.json")
    assert manifest["legacy"]["argv"][:4] == [sys.executable, "-X", "utf8", str(studio / "render_fake.py")]
    assert manifest["legacy"]["python_flags"] == ["-X", "utf8"]
    assert "utf8_mode=1" in (studio / "runs" / result["run_id"] / "legacy.log").read_text(encoding="utf-8")
    config(fps=12, units="frames")
    code, result, err = cuts_cli(studio, 0, 3, 6, extra=("--python-flags=-X utf8",))
    assert code == 0, err
    manifest = read_json(studio / "runs" / result["run_id"] / "manifest.json")
    assert all(s["argv"][1:3] == ["-X", "utf8"] for s in manifest["segments"]) and manifest["legacy"]["python_flags"] == ["-X", "utf8"]
    assert "utf8_mode=1" in (studio / "runs" / result["run_id"] / "seg_00000_00003.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("args, message", [
    (("--cuts", "0", "6", "--duration", "1"), "--duration"),
    (("--cuts", "0", "6", "--start", "0"), "--start"),
    (("--cuts", "0"), "2 つ以上"),
    (("--cuts", "0", "6", "6"), "増えていく"),
    (("--cuts", "6", "3"), "増えていく"),
    (("--cuts", "0", "6", "--jobs", "0"), "--jobs"),
    (("--cuts", "0", "6", "--python-flags= "), "--python-flags"),
])
def test_bad_cuts_are_reported_before_anything_runs(work, args, message):
    studio, _ = work
    code, _, err = run_cli("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json",
                           "--fps", "12", "--units", "frames", "--out", studio / "runs", *args)
    assert code == 1 and message in err
    assert not (studio / "runs").exists() or not [d for d in (studio / "runs").iterdir() if d.is_dir()]


def test_cuts_need_frame_units_and_fps_and_jobs_need_cuts(work):
    studio, _ = work
    base = ("fx", "legacy", "--renderer", studio / "render_fake.py", "--config", studio / "config.json", "--out", studio / "runs")
    code, _, err = run_cli(*base, "--fps", "12", "--cuts", "0", "6")
    assert code == 1 and "--units frames" in err
    code, _, err = run_cli(*base, "--units", "frames", "--cuts", "0", "6")
    assert code == 1 and "--fps" in err
    code, _, err = run_cli(*base, "--duration", "1", "--jobs", "2")
    assert code == 1 and "--jobs" in err
    code, _, err = run_cli(*base)
    assert code == 1 and "--duration" in err
    with pytest.raises(PipelineError, match="整数"):
        legacy.segment_ranges([0, 2.5])


def test_a_relative_python_path_is_read_from_the_current_folder_not_the_renderers(work, tmp_path, monkeypatch):
    studio, config = work
    config(fps=12)
    here = tmp_path / "here"
    here.mkdir()
    monkeypatch.chdir(here)
    relative = os.path.relpath(sys.executable, here)                          # フォルダを含む相対パス
    assert os.path.dirname(relative)
    code, result, err = legacy_cli(studio, "--python", relative)
    assert code == 0, err
    argv = read_json(studio / "runs" / result["run_id"] / "manifest.json")["legacy"]["argv"]
    assert argv[0] == os.path.abspath(relative) and os.path.isabs(argv[0])  # リンクはたどらない（venv の python のまま）
    config(fps=12, units="frames")
    code, result, err = cuts_cli(studio, 0, 3, 6, extra=("--python", relative))
    assert code == 0, err
    segments = read_json(studio / "runs" / result["run_id"] / "manifest.json")["segments"]
    assert all(s["argv"][0] == os.path.abspath(relative) for s in segments)
    assert legacy._interpreter("python") == "python" and legacy._interpreter(None) == sys.executable   # 名前だけは PATH から探す
