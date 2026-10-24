"""実制作レンダラ（リポジトリの外にある既存スクリプト）を呼び、同じ run の形式で記録する。

    [python, renderer, config, "seg", start, end, out.mp4]

レンダラのコードも設定も**リポジトリに取り込まない**。fx が行うのは、その呼び出しと、sha256・argv・終了コード・
所要時間の記録、出力の構造検査（technical_pass）だけで、絵の良し悪しは判定しない。

- 検査: フレーム数は round(D×fps) から最大 2 少ないまで許容（実務の `-shortest`）、多いのは不合格。
  寸法・fps は指定したときだけ照合する（fps を指定しなければ、出力の fps から期待枚数を数える。
  出力の fps も読めず期待枚数が決まらないときは、黙って合格にせず technical_pass=false にして理由を checks に残す）。
  全復号でエラーが無いこと。
- ログ: 子プロセスの標準出力・標準エラーを描画中に逐次 legacy.log へ書く（子の Python は PYTHONUNBUFFERED=1）。
- timeout: 既定 max(600, 120×duration) 秒。超えたら子プロセスごと止めて failed にする。
- キャッシュしない: 設定が参照する素材などレンダラの隠れた入力を fx は知らないため、毎回実行する。
- 失敗しても最終名 (legacy.mp4) は作らず、過去の run は変えない。
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..io import PipelineError, sha256
from ..runner import project_lock
from . import probe, timeline
from .runs import Run, close_orphans, content_key_for


def default_timeout(duration):
    return max(600.0, 120.0 * duration)


def _number(value):
    """argv に渡す秒。末尾の浮動小数点の揺れを丸める（0.1+0.2 のような文字列を渡さない）。"""
    return repr(round(float(value), 6))


def _terminate(process):
    """子プロセスとその子孫を止める（レンダラが ffmpeg などを起動していても残さない）。"""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, timeout=30)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    process.kill()
    process.wait()


def _expected_frames(duration, fps_given, observed_fps):
    fps = fps_given if fps_given is not None else observed_fps
    if not fps:
        return None
    n = timeline.frame_count(duration, fps)
    return (max(1, n - 2), n)


def run_legacy(renderer, config, start, duration, python=None, width=None, height=None, fps=None, out=None,
               timeout=None, cwd=None, progress=None):
    began = time.monotonic()
    say = progress or (lambda message: None)
    renderer, config = Path(renderer).resolve(), Path(config).resolve()
    for label, path in (("--renderer", renderer), ("--config", config)):
        if not path.is_file():
            raise PipelineError(f"{label} のファイルが見つかりません: {path}")
    start, duration = float(start), float(duration)
    if not start >= 0 or not duration > 0:
        raise PipelineError(f"--start は 0 以上、--duration は 0 より大きくしてください（start={start}, duration={duration}）")
    timeout = float(timeout) if timeout is not None else default_timeout(duration)
    if not timeout > 0:
        raise PipelineError(f"--timeout は 0 より大きくしてください: {timeout}")
    python = python or sys.executable
    work_dir = Path(cwd).resolve() if cwd else renderer.parent
    if not work_dir.is_dir():
        raise PipelineError(f"--cwd のディレクトリが見つかりません: {work_dir}")
    out = (Path(out) if out else config.parent / "fx_runs").resolve()
    renderer_sha, config_sha = sha256(renderer), sha256(config)
    key = content_key_for("legacy", {"renderer": renderer_sha, "config": config_sha, "start": start,
                                     "duration": duration, "width": width, "height": height, "fps": fps})
    with project_lock(out):
        close_orphans(out)
        run = Run(out, key, "legacy", "legacy.log")
        partial = run.dir / "legacy.part.mp4"
        final = run.dir / "legacy.mp4"
        argv = [str(python), str(renderer), str(config), "seg", _number(start), _number(start + duration), str(partial)]
        try:
            run.manifest.update(
                legacy={"renderer": {"name": renderer.name, "sha256": renderer_sha},
                        "config": {"name": config.name, "sha256": config_sha}, "argv": argv,
                        "cwd": str(work_dir), "timeout_s": timeout, "exit_code": None, "seconds": None,
                        "cacheable": False},
                interval={"start": start, "duration": duration, "fps": fps, "frames": None},
                variants={}, grid=None, warnings=[])
            run.save()
            run.log("$ " + " ".join(argv))
            say(f"[fx] legacy 実行中（timeout {timeout:g} 秒）: {renderer.name} {config.name}")
            exit_code = _execute(argv, work_dir, run.log_path, timeout)
            run.manifest["legacy"].update(exit_code=exit_code, seconds=round(time.monotonic() - began, 3))
            run.save()
            if exit_code != 0:
                raise PipelineError(f"レンダラが終了コード {exit_code} で失敗しました。ログ: {run.log_path}\n{_tail(run.log_path)}")
            if not partial.is_file():
                raise PipelineError(f"レンダラは成功しましたが、出力 (argv の最後の引数) が作られていません。ログ: {run.log_path}")
            observed = probe.inspect(partial)["video"]
            expected = _expected_frames(duration, fps, observed["fps"])
            checks = probe.check_video(partial, width=width, height=height, fps=fps, frames=expected)
            if expected is None:  # フレーム数を照合できていない。偽の合格にしない
                checks["failures"].append("fps が分からないため期待フレーム数を決められません（出力から fps を読めず --fps も未指定）。"
                                          "--fps を指定して再実行してください")
                checks["pass"] = False
            run.manifest["interval"]["frames"] = checks["frames"]
            if checks["pass"]:
                os.replace(partial, final)
            run.manifest["variants"]["legacy"] = {
                "path": final.name if checks["pass"] else partial.name, "frames": checks["frames"],
                "sha256": sha256(final if checks["pass"] else partial), "technical_pass": checks["pass"],
                "checks": checks}
            run.log(f"legacy: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
            if checks["pass"]:
                run.finish(True)
            else:
                partial.unlink(missing_ok=True)
                message = f"技術検査に失敗しました（{'; '.join(checks['failures'])[:800]}）。詳細: {run.dir / 'manifest.json'}"
                run.finish(False, message)
                raise PipelineError(message)
        except BaseException as error:
            if run.manifest["status"] == "running":
                run.fail(error)
            partial.unlink(missing_ok=True)
            raise
    item = run.manifest["variants"]["legacy"]
    return {"run_id": run.run_id, "run_dir": str(run.dir), "status": run.manifest["status"],
            "technical_pass": True, "output": str(run.dir / item["path"]), "frames": item["frames"],
            "exit_code": 0, "seconds": round(time.monotonic() - began, 1)}


def _execute(argv, cwd, log_path, timeout):
    """argv を実行し、出力を log_path へ逐次書く。終了コードを返す。timeout は PipelineError。"""
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with open(log_path, "ab") as log:
        try:
            process = subprocess.Popen(argv, cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
                                       stdout=log, stderr=subprocess.STDOUT, **options)
        except OSError as error:
            raise PipelineError(f"レンダラを起動できません（{argv[0]}）: {error}。--python を確認してください") from error
        try:
            return process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _terminate(process)
            raise PipelineError(f"レンダラが timeout ({timeout:g} 秒) を超えたため止めました。--timeout を延ばすか、区間を短くしてください") from None
        except BaseException:
            _terminate(process)
            raise


def _tail(path, limit=1500):
    try:
        return Path(path).read_bytes()[-limit:].decode("utf-8", "replace")
    except OSError:
        return ""
