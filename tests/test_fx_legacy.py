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
    print("phase1 start=%s end=%s" % (start, end))   # flush しない: 逐次ログは fx が PYTHONUNBUFFERED を付けて初めて読める
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
    frames = round((float(end) - float(start)) * fps) + cfg.get("extra", 0)
    command = [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
               "-s", "%dx%d" % (width, height), "-framerate", str(fps), "-i", "pipe:0",
               "-c:v", "libx264", "-pix_fmt", "yuv420p", out]
    proc = subprocess.Popen(command, stdin=subprocess.PIPE)
    for i in range(frames):
        proc.stdin.write(bytes((i * 11 + k) % 256 for k in range(width * height * 3)))
    proc.stdin.close()
    assert proc.wait() == 0
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


def test_numbers_are_passed_without_float_noise():
    assert legacy._number(0) == "0.0" and legacy._number(0.1 + 0.2) == "0.3" and legacy._number(10) == "10.0"
