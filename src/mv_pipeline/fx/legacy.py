"""実制作レンダラ（リポジトリの外にある既存スクリプト）を呼び、同じ run の形式で記録する。

    [python, renderer, config, "seg", start, end, out.mp4]

start・end は既定で秒（end = start + duration）。units="frames" のときはフレーム番号の整数で、
start = round(start秒×fps)、end = start + round(duration×fps)（end のフレームは含まない。range(start, end) を描く形）。
実制作レンダラの多くはこの形で受け取る。フレーム番号に直すので fps が要る。

レンダラのコードも設定も**リポジトリに取り込まない**。fx が行うのは、その呼び出しと、sha256・argv・終了コード・
所要時間の記録、出力の構造検査（technical_pass）だけで、絵の良し悪しは判定しない。

- 検査: フレーム数は round(D×fps) から最大 2 少ないまで許容（実務の `-shortest`）、多いのは不合格。
  寸法・fps は指定したときだけ照合する（fps を指定しなければ、出力の fps から期待枚数を数える。
  出力の fps も読めず期待枚数が決まらないときは、黙って合格にせず technical_pass=false にして理由を checks に残す）。
  出力の fps が読めなくても --fps を指定していれば、fps の照合は省き（読めないので比べられない）、指定した fps で
  期待枚数だけを照合する（省いたことは manifest の warnings に残す）。読めた fps は従来どおり指定値と照合する。
  全復号でエラーが無いこと。
- ログ: 子プロセスの標準出力・標準エラーを描画中に逐次 legacy.log へ書く（子の Python は PYTHONUNBUFFERED=1）。
- timeout: 既定 max(600, 120×duration) 秒。超えたら子プロセスごと止めて failed にする。
- キャッシュしない: 設定が参照する素材などレンダラの隠れた入力を fx は知らないため、毎回実行する。
- 失敗しても最終名 (legacy.mp4) は作らず、過去の run は変えない。
- python_flags: インタープリタに渡す引数（例 "-I -X utf8"）。argv は [python, *flags, renderer, ...] になる。

区間を並べて描く（run_segments。CLI は --cuts）: 区切りのフレーム番号 N0 N1 … Nk で、区間 [N0, N1)・[N1, N2) … を
それぞれ `seg 最初 終わり` で描かせ、jobs 本まで同時に動かす（実制作の区間の並列描画と同じ呼び方）。
- 1 つの run の中に seg_<最初>_<終わり>.mp4（5 桁。最後のコマが 5 桁を超えればその桁数にそろえる）と、区間ごとの
  seg_<最初>_<終わり>.log を並べる。run のフォルダを mv deliver join に渡せば、区間がすき間なく続くことを確かめてつながる。
  fx の run は描いている間 <out> のロックを持つので、区間ごとに別の run にはしない（同じ <out> で同時に開けない）。
- 区間のコマ数は 終わり − 最初 にぴったり（つないだときにすき間・重なりを作らないため。1 本のときの 2 コマの許容は無い）。
- 1 区間でも失敗（終了コード・timeout・出力なし）したら、残りの区間を止めて、まだ始めていない区間は描かない。
  構造検査に 1 区間でも落ちたら、どの区間の動画も最終名にしない（途中の partial_*.mp4 は消す。ログは残す）。
"""
import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

from ..io import PipelineError, sha256
from ..runner import now
from . import probe, timeline
from .runs import Session, content_key_for

SEGMENT_DIGITS = 5      # 実制作の区間の名前（seg_03981_04106.mp4）と同じ桁
POLL_S = 0.1


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


UNITS = ("seconds", "frames")


def _bounds(start, duration, fps, units):
    """argv に渡す区間（start, end）の 2 つの文字列と、フレーム番号の範囲（units="frames" のときだけ）。"""
    if units == "frames":
        first = round(start * fps)
        frame_range = [first, first + timeline.frame_count(duration, fps)]
        return [str(n) for n in frame_range], frame_range
    return [_number(start), _number(start + duration)], None


def _flags(python_flags):
    """インタープリタに渡す引数（例 "-I -X utf8"）。文字列は shlex で区切り、並びはそのまま使う。"""
    if not python_flags:
        return []
    flags = shlex.split(python_flags) if isinstance(python_flags, str) else [str(item) for item in python_flags]
    if not flags or any(not flag for flag in flags):
        raise PipelineError(f"--python-flags に空の引数があります: {python_flags!r}")
    return flags


def _interpreter(python):
    """レンダラを動かすインタープリタ。フォルダを含む相対パスは、今いるフォルダから見て存在すれば絶対パスにする
    （子は作業ディレクトリ（既定はレンダラのある場所）で起動するので、そのままだとそこからの相対で探されて見つからない）。
    venv の python はリンクのことがあるので、リンクはたどらない（たどると venv の外の python になる）。"""
    if not python:
        return sys.executable
    path = Path(python)
    if path.parent != Path(".") and not path.is_absolute() and path.exists():
        return os.path.abspath(path)
    return str(python)


def _inputs(renderer, config, cwd, out):
    """レンダラ・設定・作業ディレクトリ・出力先を確かめて絶対パスにする（1 本でも区間を並べても同じ）。"""
    renderer, config = Path(renderer).resolve(), Path(config).resolve()
    for label, path in (("--renderer", renderer), ("--config", config)):
        if not path.is_file():
            raise PipelineError(f"{label} のファイルが見つかりません: {path}")
    work_dir = Path(cwd).resolve() if cwd else renderer.parent
    if not work_dir.is_dir():
        raise PipelineError(f"--cwd のディレクトリが見つかりません: {work_dir}")
    out = (Path(out) if out else config.parent / "fx_runs").resolve()
    return renderer, config, work_dir, out


def run_legacy(renderer, config, start, duration, python=None, width=None, height=None, fps=None, out=None,
               timeout=None, cwd=None, progress=None, units="seconds", python_flags=None):
    began = time.monotonic()
    if units not in UNITS:
        raise PipelineError(f"--units は {' か '.join(UNITS)} にしてください: {units!r}")
    if units == "frames" and fps is None:
        raise PipelineError("--units frames には --fps が要ります（秒の区間をフレーム番号に直すため）")
    say = progress or (lambda message: None)
    renderer, config, work_dir, out = _inputs(renderer, config, cwd, out)
    start, duration = float(start), float(duration)
    if not start >= 0 or not duration > 0:
        raise PipelineError(f"--start は 0 以上、--duration は 0 より大きくしてください（start={start}, duration={duration}）")
    timeout = float(timeout) if timeout is not None else default_timeout(duration)
    if not timeout > 0:
        raise PipelineError(f"--timeout は 0 より大きくしてください: {timeout}")
    python = _interpreter(python)
    flags = _flags(python_flags)
    renderer_sha, config_sha = sha256(renderer), sha256(config)
    bounds, frame_range = _bounds(start, duration, fps, units)
    key = content_key_for("legacy", {"renderer": renderer_sha, "config": config_sha, "start": start,
                                     "duration": duration, "width": width, "height": height, "fps": fps,
                                     **({"units": units} if units != "seconds" else {}),
                                     **({"python_flags": flags} if flags else {})})
    with Session(out, key, "legacy", "legacy.log", say=say, tag="[fx]", cache=False) as session:
        run = session.run
        partial = run.dir / "legacy.part.mp4"
        final = run.dir / "legacy.mp4"
        argv = [str(python), *flags, str(renderer), str(config), "seg", *bounds, str(partial)]
        try:
            run.manifest.update(
                legacy={"renderer": {"name": renderer.name, "sha256": renderer_sha},
                        "config": {"name": config.name, "sha256": config_sha}, "argv": argv, "python_flags": flags,
                        "cwd": str(work_dir), "timeout_s": timeout, "exit_code": None, "seconds": None,
                        "cacheable": False},
                interval={"start": start, "duration": duration, "fps": fps, "frames": None, "units": units,
                          **({"frame_range": frame_range} if frame_range else {})},
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
            checks = probe.check_video(partial, width=width, height=height, fps=fps, frames=expected,
                                       fps_unreadable_ok=True)
            if fps is not None and observed["fps"] is None:  # 読めない fps とは比べられない。省いたことを残す
                note = f"出力から fps を読めないため fps の照合を省き、--fps {fps:g} でフレーム数だけを照合しました"
                run.manifest["warnings"].append(note)
                run.log("legacy: " + note)
            if expected is None:  # フレーム数を照合できていない。偽の合格にしない
                checks["failures"].append("fps が分からないため期待フレーム数を決められません（出力から fps を読めず --fps も未指定）。"
                                          "--fps を指定して再実行すると、指定した fps で期待フレーム数を照合します")
                checks["pass"] = False
            run.manifest["interval"]["frames"] = checks["frames"]
            if checks["pass"]:
                os.replace(partial, final)
            run.manifest["variants"]["legacy"] = {
                "path": final.name if checks["pass"] else partial.name, "frames": checks["frames"],
                "sha256": sha256(final if checks["pass"] else partial), "technical_pass": checks["pass"],
                "checks": checks}
            run.log(f"legacy: {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
            if not checks["pass"]:
                partial.unlink(missing_ok=True)
                message = f"技術検査に失敗しました（{'; '.join(checks['failures'])[:800]}）。詳細: {run.dir / 'manifest.json'}"
                run.finish(False, message)
                raise PipelineError(message)
            session.succeed()
        except BaseException:
            partial.unlink(missing_ok=True)   # 途中までの出力を残さない。run を failed にするのは Session
            raise
    item = run.manifest["variants"]["legacy"]
    return {"run_id": run.run_id, "run_dir": str(run.dir), "status": run.manifest["status"],
            "technical_pass": True, "output": str(run.dir / item["path"]), "frames": item["frames"],
            "exit_code": 0, "seconds": round(time.monotonic() - began, 1)}


def segment_ranges(cuts):
    """区切りのフレーム番号 N0 N1 … Nk を [(N0, N1), (N1, N2), …] にする。0 以上の整数で、増えていく順。"""
    if not isinstance(cuts, (list, tuple)) or len(cuts) < 2:
        raise PipelineError("--cuts には区切りのフレーム番号を 2 つ以上渡してください（N0 N1 … で、区間は [N0, N1)・[N1, N2) …）")
    if any(isinstance(cut, bool) or not isinstance(cut, int) for cut in cuts):
        raise PipelineError(f"--cuts はフレーム番号の整数にしてください: {list(cuts)}")
    if cuts[0] < 0 or any(b <= a for a, b in zip(cuts, cuts[1:])):
        raise PipelineError(f"--cuts は 0 以上で、増えていく順にしてください（同じ番号を続けない）: {list(cuts)}")
    return list(zip(cuts[:-1], cuts[1:]))


def _jobs(jobs, count):
    """同時に動かす区間の数。既定は区間の数（CPU の数まで）。"""
    if jobs is None:
        return max(1, min(count, os.cpu_count() or 1))
    if isinstance(jobs, bool) or not isinstance(jobs, int) or jobs < 1:
        raise PipelineError(f"--jobs は 1 以上の整数にしてください: {jobs!r}")
    return min(jobs, count)


def _segment_record(item):
    checks = item.get("checks")
    return {"file": item["final"].name, "log": item["log"].name, "frame_range": list(item["range"]), "argv": item["argv"],
            "timeout_s": item["timeout"], "exit_code": item["exit_code"], "seconds": item["seconds"], "error": item["error"],
            "frames": checks["frames"] if checks else None, "sha256": item.get("sha256"),
            "technical_pass": checks["pass"] if checks else None, "checks": checks}


def run_segments(renderer, config, cuts, fps, python=None, width=None, height=None, out=None, timeout=None, cwd=None,
                 progress=None, python_flags=None, jobs=None):
    """区切り cuts の区間を、それぞれ `seg 最初 終わり` でレンダラに描かせ、jobs 本まで同時に動かす（モジュールの説明を参照）。"""
    began = time.monotonic()
    say = progress or (lambda message: None)
    ranges = segment_ranges(cuts)
    if fps is None or isinstance(fps, bool) or not float(fps) > 0:
        raise PipelineError("--cuts には --fps が要ります（区間の長さを秒で記録し、出力の fps を照合するため）")
    fps = float(fps)
    jobs = _jobs(jobs, len(ranges))
    renderer, config, work_dir, out = _inputs(renderer, config, cwd, out)
    if timeout is not None and not float(timeout) > 0:
        raise PipelineError(f"--timeout は 0 より大きくしてください: {timeout}")
    python = _interpreter(python)
    flags = _flags(python_flags)
    renderer_sha, config_sha = sha256(renderer), sha256(config)
    first, last = ranges[0][0], ranges[-1][1]
    marks = [a for a, _ in ranges] + [last]
    digits = max(SEGMENT_DIGITS, len(str(last)))
    key = content_key_for("legacy", {"renderer": renderer_sha, "config": config_sha, "cuts": marks, "width": width,
                                     "height": height, "fps": fps, "units": "frames",
                                     **({"python_flags": flags} if flags else {})})
    with Session(out, key, "legacy", "legacy.log", say=say, tag="[fx]", cache=False) as session:
        run = session.run
        items = []
        for a, b in ranges:
            span = f"{a:0{digits}d}_{b:0{digits}d}"
            partial = run.dir / f"partial_{span}.mp4"
            items.append({"range": (a, b), "name": f"seg_{span}", "partial": partial, "final": run.dir / f"seg_{span}.mp4",
                          "log": run.dir / f"seg_{span}.log",
                          "argv": [str(python), *flags, str(renderer), str(config), "seg", str(a), str(b), str(partial)],
                          "timeout": float(timeout) if timeout is not None else default_timeout((b - a) / fps),
                          "exit_code": None, "seconds": None, "error": None})
        try:
            run.manifest.update(
                legacy={"renderer": {"name": renderer.name, "sha256": renderer_sha},
                        "config": {"name": config.name, "sha256": config_sha}, "python": str(python),
                        "python_flags": flags, "cwd": str(work_dir), "jobs": jobs, "exit_code": None, "seconds": None,
                        "cacheable": False},
                interval={"start": first / fps, "duration": (last - first) / fps, "fps": fps, "frames": None,
                          "units": "frames", "frame_range": [first, last], "cuts": marks},
                segments=[_segment_record(item) for item in items], variants={}, grid=None, warnings=[])
            run.save()
            say(f"[fx] legacy {len(items)} 区間を {jobs} 本ずつ描きます: {renderer.name} {config.name}")
            failed = _run_parallel(items, jobs, work_dir, run, say)
            run.manifest["segments"] = [_segment_record(item) for item in items]
            run.manifest["legacy"].update(exit_code=failed["exit_code"] if failed else 0,
                                          seconds=round(time.monotonic() - began, 3))
            run.save()
            if failed:
                a, b = failed["range"]
                raise PipelineError(f"区間 {a}-{b} が失敗しました（{failed['error']}）。ほかの区間は止めました。"
                                    f"ログ: {failed['log']}\n{_tail(failed['log'])}")
            failures = []
            for item in items:
                observed = probe.inspect(item["partial"])["video"]
                checks = probe.check_video(item["partial"], width=width, height=height, fps=fps,
                                           frames=item["range"][1] - item["range"][0], fps_unreadable_ok=True)
                if observed["fps"] is None:  # 読めない fps とは比べられない。省いたことを残す
                    note = f"{item['name']}: 出力から fps を読めないため fps の照合を省き、コマの数だけを照合しました"
                    run.manifest["warnings"].append(note)
                    run.log("legacy: " + note)
                item["checks"] = checks
                run.log(f"legacy: {item['name']} {checks['frames']} frames technical_pass={checks['pass']} {checks['failures']}")
                if not checks["pass"]:
                    failures.append(f"{item['name']}: {'; '.join(checks['failures'])}")
            if not failures:
                for item in items:
                    os.replace(item["partial"], item["final"])
                    item["sha256"] = sha256(item["final"])
            run.manifest["segments"] = [_segment_record(item) for item in items]
            run.manifest["interval"]["frames"] = sum(item["checks"]["frames"] for item in items)
            run.save()
            if failures:
                _remove_partials(items)
                message = f"技術検査に失敗しました（{' / '.join(failures)[:800]}）。詳細: {run.dir / 'manifest.json'}"
                run.finish(False, message)
                raise PipelineError(message)
            session.succeed()
        except BaseException:
            _remove_partials(items)   # 途中までの出力を残さない。run を failed にするのは Session
            raise
    return {"run_id": run.run_id, "run_dir": str(run.dir), "status": run.manifest["status"], "technical_pass": True,
            "segments": [{"file": str(item["final"]), "frame_range": list(item["range"]), "frames": item["checks"]["frames"],
                          "seconds": item["seconds"]} for item in items],
            "frames": run.manifest["interval"]["frames"], "jobs": jobs, "exit_code": 0,
            "seconds": round(time.monotonic() - began, 1)}


def _remove_partials(items):
    for item in items:
        item["partial"].unlink(missing_ok=True)


def _run_parallel(items, jobs, cwd, run, say):
    """items を jobs 本まで同時に実行し、出力を区間ごとのログへ逐次書く。

    1 本でも失敗（終了コード・timeout・出力なし）したら、動いている区間を子プロセスごと止め、まだの区間は始めずに、
    最初に失敗した区間を返す。全部成功なら None。"""
    environment = {**os.environ, "PYTHONUNBUFFERED": "1"}
    options = {"start_new_session": True} if os.name != "nt" else {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    pending, running, failed, total = list(items), [], None, len(items)
    try:
        while pending or running:
            while pending and len(running) < jobs and failed is None:
                item = pending.pop(0)
                handle = open(item["log"], "ab")
                try:
                    handle.write(f"{now()} $ {' '.join(item['argv'])}\n".encode("utf-8"))
                    handle.flush()
                    process = subprocess.Popen(item["argv"], cwd=str(cwd), env=environment, stdin=subprocess.DEVNULL,
                                               stdout=handle, stderr=subprocess.STDOUT, **options)
                except OSError as error:
                    handle.close()
                    raise PipelineError(f"レンダラを起動できません（{item['argv'][0]}）: {error}。--python を確認してください") from error
                item["started"] = time.monotonic()
                running.append((item, process, handle))
                run.log("$ " + " ".join(item["argv"]))
                a, b = item["range"]
                say(f"[fx] legacy 区間 {a}-{b} を描き始めました（{total - len(pending)}/{total}、timeout {item['timeout']:g} 秒）")
            if not running:
                break
            time.sleep(POLL_S)
            for entry in list(running):
                item, process, handle = entry
                code = process.poll()
                elapsed = time.monotonic() - item["started"]
                if code is None and elapsed <= item["timeout"]:
                    continue
                if code is None:
                    _terminate(process)
                    item["error"] = (f"timeout ({item['timeout']:g} 秒) を超えたため止めました。"
                                     "--timeout を延ばすか、区切りを細かくしてください")
                else:
                    item["exit_code"] = code
                    if code != 0:
                        item["error"] = f"レンダラが終了コード {code} で失敗しました"
                    elif not item["partial"].is_file():
                        item["error"] = "レンダラは成功しましたが、出力 (argv の最後の引数) が作られていません"
                handle.close()
                running.remove(entry)
                item["seconds"] = round(elapsed, 3)
                a, b = item["range"]
                run.log(f"legacy: {item['name']} exit={item['exit_code']} {item['seconds']} 秒" +
                        (f" {item['error']}" if item["error"] else ""))
                say(f"[fx] legacy 区間 {a}-{b}: " + (item["error"] or f"終わりました（{item['seconds']:.1f} 秒）"))
                if item["error"] and failed is None:
                    failed = item
            if failed is not None:
                for item, process, handle in running:
                    _terminate(process)
                    handle.close()
                    item["seconds"] = round(time.monotonic() - item["started"], 3)
                    item["error"] = "ほかの区間が失敗したため止めました"
                running.clear()
                for item in pending:
                    item["error"] = "ほかの区間が失敗したため描いていません"
                pending.clear()
    except BaseException:
        for item, process, handle in running:
            _terminate(process)
            handle.close()
        raise
    return failed


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
