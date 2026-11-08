"""外の描画（Remotion の透過 PNG 連番）を台帳に載せる（`mv layer render`。実験的）。

    mv layer render --props props.json [--entry renderer/src/layer-index.tsx] [--composition LyricLayer] [--public DIR]
                    [--out DIR] [--twice] [--force] [--renderer SCRIPT] [--concurrency 2]

Remotion の連番の描画（renderer/render-frames.mjs）を工程として包み、run（<out>/<run_id>/）と台帳（index.json）に残す:
    frames/element-<コマ>.png   描いた連番（Remotion の既定の名前）
    frames.json                 コマごとの名前と sha256（連番の manifest）
    manifest.json               Remotion の版、エントリ・props・public の各ファイルの sha256、コンポジション、ブラウザーの実行ファイルと版、
                                連番の manifest の sha256、描画時間、--twice のときは 2 回目の連番との一致（deterministic）
    render.log                  node の標準エラー（[orpheus-render-error] の行を含む）

- key（台帳の鍵）はエントリと import するコード（renderer/src・renderer/presets）・props・public の sha256、コンポジション名、Remotion の版、
  描画スクリプトの sha256、ORPHEUS_BROWSER から作る。同じ入力の成功 run は、連番が frames.json のとおり残っていれば再利用する
  （--force で描き直す。--twice は常に描く）。
- --twice は同じ入力で 2 回描き、コマごとの sha256 が全部一致するかを manifest に残す（決定論の確認。2 回目の連番は消す）。
- --renderer は描画スクリプト（node の .mjs か任意の実行ファイル）の差し替え。同じ引数（--entry --composition --props --out [--public]）を
  受け取り、標準出力の最後の行に JSON を出す決まり。テストは Remotion を呼ばない代役で確かめる。
- 連番の中身の良し悪しは判定しない（見るのは `mv sheet`、重ねるのは `mv overlay`、timeline は `mv gate timeline`）。

Remotion の利用条件（個人と小さな組織は無料。確認日つき）は docs/dependencies.md。
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from . import media
from .fx import runs
from .io import REPO, PipelineError, atomic_json, read_json, sha256

DEFAULT_ENTRY = REPO / "renderer/src/layer-index.tsx"
DEFAULT_COMPOSITION = "LyricLayer"
DEFAULT_RENDERER = REPO / "renderer/render-frames.mjs"
RENDERER_SOURCES = (REPO / "renderer/src", REPO / "renderer/presets")   # 既定のエントリが import するコード。key に入れる
PROPS_SCHEMA = "remotion-props/0.1"
FRAME_PATTERN = re.compile(r"^element-(\d+)\.png$")


def _files_record(folder):
    """フォルダーの中の全ファイル（再帰）の {相対パス: sha256}。無ければ空。"""
    if folder is None:
        return {}
    folder = Path(folder)
    if not folder.is_dir():
        raise PipelineError(f"--public のフォルダーが見つかりません: {folder}")
    return {p.relative_to(folder).as_posix(): sha256(p) for p in sorted(folder.rglob("*")) if p.is_file()}


def _source_tree(entry):
    """エントリが import しうるコードの {相対パス: sha256}。リポジトリの renderer/ の中のエントリなら renderer/src と renderer/presets の
    .ts / .tsx / .mjs を全部（LyricLayer.tsx などを変えても key が変わる）。外のエントリなら、同じフォルダーの .ts / .tsx。"""
    entry = Path(entry)
    folders = RENDERER_SOURCES if entry.is_relative_to(REPO / "renderer") else (entry.parent,)
    files = sorted(p for folder in folders if folder.is_dir() for p in folder.rglob("*") if p.is_file() and p.suffix in (".ts", ".tsx", ".mjs"))
    base = REPO if entry.is_relative_to(REPO) else entry.parent
    return {p.relative_to(base).as_posix(): sha256(p) for p in files}


def _repo_path(path):
    """台帳に書くファイルの path。リポジトリの中ならリポジトリからの相対パス（OS によらず / 区切り。key も OS で変わらない）、
    外ならファイル名だけ（個人のパスを書かない）。"""
    return path.relative_to(REPO).as_posix() if path.is_relative_to(REPO) else path.name


def _check_props(props_path, entry):
    data = read_json(props_path)
    if not isinstance(data, dict):
        raise PipelineError("--props は JSON オブジェクトにしてください")
    if Path(entry) == DEFAULT_ENTRY:    # 既定のエントリは remotion-props/0.1 を読む。描く前に形を確かめる（bundle の後で落ちない）
        if data.get("schema_version") != PROPS_SCHEMA:
            raise PipelineError(f"既定のエントリは {PROPS_SCHEMA} の props（mv export remotion-props の出力）を読みます: schema_version={data.get('schema_version')!r}")
        missing = [key for key in ("fps", "width", "height", "duration_frames", "lines") if key not in data]
        if missing:
            raise PipelineError(f"props.json に {', '.join(missing)} がありません")
    return data


def frames_intact(run_dir):
    """run の連番が frames.json のとおりに残っているか（再利用の前に確かめる）。"""
    run_dir = Path(run_dir)
    try:
        manifest = read_json(run_dir / "frames.json")
        return all((run_dir / "frames" / item["name"]).is_file() and sha256(run_dir / "frames" / item["name"]) == item["sha256"]
                   for item in manifest["frames"])
    except (OSError, ValueError, KeyError, TypeError):
        return False


def remotion_version():
    package = REPO / "node_modules/remotion/package.json"
    if not package.is_file():
        raise PipelineError("node_modules/remotion が見つかりません。`pnpm install --frozen-lockfile` を実行してください")
    return read_json(package)["version"]


def browser_version(path):
    """ブラウザーの実行ファイルの --version の 1 行目（読めなければ None）。"""
    if not path:
        return None
    try:
        run = subprocess.run([str(path), "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    line = (run.stdout or run.stderr).strip().splitlines()
    return line[0][:120] if line else None


def frames_manifest(folder):
    """連番の manifest {"count", "frames": [{"name", "frame", "sha256"}]}（コマ番号順）。連番が無ければエラー。"""
    items = []
    for path in sorted(Path(folder).iterdir()):
        found = FRAME_PATTERN.match(path.name)
        if found and path.is_file():
            items.append({"name": path.name, "frame": int(found[1]), "sha256": sha256(path)})
    items.sort(key=lambda item: item["frame"])
    if not items:
        raise PipelineError(f"描画スクリプトは連番（element-<コマ>.png）を作りませんでした: {folder}")
    expected = list(range(len(items)))
    if [item["frame"] for item in items] != expected:
        raise PipelineError(f"連番のコマ番号が 0 から連続していません: {[item['frame'] for item in items][:10]}…")
    return {"count": len(items), "frames": items}


def _run_renderer(renderer, entry, composition, props, out_dir, public, concurrency, log_path):
    """描画スクリプトを呼び、標準出力の最後の JSON 行を返す。標準エラーは log に追記する。"""
    renderer = Path(renderer)
    if renderer.suffix in (".mjs", ".js", ".cjs"):
        argv = [media.node(), str(renderer)]
    elif renderer.suffix == ".py":
        argv = [sys.executable, str(renderer)]
    else:
        argv = [str(renderer)]
    argv += ["--entry", str(entry), "--composition", composition, "--props", str(props), "--out", str(out_dir), "--concurrency", str(concurrency)]
    if public is not None:
        argv += ["--public", str(public)]
    # 出力は UTF-8 で読む。Windows の Python の子はパイプへ ANSI コードページ（cp932 など）で書くので、
    # 子の標準入出力を UTF-8 にそろえる（利用者が PYTHONIOENCODING を決めていればそれを使う。io.run と同じ）
    env = {**os.environ}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    with open(log_path, "a", encoding="utf-8", newline="\n") as log:
        log.write(f"$ {' '.join(argv)}\n")
        started = time.monotonic()
        run = subprocess.run(argv, cwd=REPO, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        log.write(run.stdout)       # 標準出力も残す（Remotion の情報や代役の診断はこちらに出る）
        log.write(run.stderr)
        if run.returncode != 0:
            raise PipelineError(f"描画スクリプトが終了コード {run.returncode} で失敗しました。ログ: {log_path}\n{(run.stdout + run.stderr)[-1500:]}")
    lines = [line for line in run.stdout.strip().splitlines() if line.strip()]
    try:
        report = json.loads(lines[-1]) if lines else {}
    except ValueError:
        report = {}
    if not isinstance(report, dict):
        report = {}
    report["seconds"] = round(time.monotonic() - started, 3)
    return report


def run_render(props, entry=None, composition=DEFAULT_COMPOSITION, public=None, out=None, twice=False, force=False,
               renderer=None, concurrency=2, progress=None):
    """連番を描いて run と台帳に記録する。CLI が stdout に出す要約を返す。"""
    began = time.monotonic()
    say = progress or (lambda message: None)
    props, entry = Path(props).resolve(), Path(entry or DEFAULT_ENTRY).resolve()
    renderer = Path(renderer or DEFAULT_RENDERER).resolve()
    for label, path in (("--props", props), ("--entry", entry), ("--renderer", renderer)):
        if not path.is_file():
            raise PipelineError(f"{label} のファイルが見つかりません: {path}")
    _check_props(props, entry)
    if int(concurrency) < 1:
        raise PipelineError(f"--concurrency は 1 以上にしてください: {concurrency}")
    public = None if public is None else Path(public).resolve()
    version = remotion_version()
    # key: 描く絵に効く入力だけ（エントリと import するコード・props・public・コンポジション・描画スクリプト・Remotion の版・
    # 指定したブラウザー）。concurrency は絵に効かないので入れない
    inputs = {"entry": {"path": _repo_path(entry), "sha256": sha256(entry), "sources": _source_tree(entry)},
              "props": {"name": props.name, "sha256": sha256(props)},
              "public": None if public is None else {"name": public.name, "files": _files_record(public)},
              "composition": composition,
              "renderer": {"path": _repo_path(renderer), "sha256": sha256(renderer)},
              "remotion_version": version, "browser_executable": os.environ.get("ORPHEUS_BROWSER")}
    key = runs.content_key_for("layer-render", inputs)
    out = (Path(out) if out else props.parent / "layer_runs").resolve()
    # 台帳の照合は frames.json の sha256 だけなので、連番そのものが残っているかも確かめてから再利用する
    with runs.Session(out, key, "layer-render", "render.log", say=say, tag="[layer]", force=force or twice,
                      accept_reuse=lambda run_dir, manifest: frames_intact(run_dir)) as session:
        if session.reused:
            return _summarize(session.reused_dir, session.reused, reused=True, began=began)
        run = session.run
        run.manifest.update(layer={"inputs": inputs, "report": None, "browser": None, "frames_manifest": None, "deterministic": None,
                                   "render_seconds": None, "twice": bool(twice)},
                            variants={}, grid=None, warnings=[])
        run.save()
        run.log(f"layer render start key={key} composition={composition} entry={inputs['entry']['path']} props={props.name}")
        say(f"[layer] Remotion {version} で {composition} を描きます（{props.name}）")
        frames_dir = run.dir / "frames"
        frames_dir.mkdir()
        report = _run_renderer(renderer, entry, composition, props, frames_dir, public, concurrency, run.log_path)
        manifest = frames_manifest(frames_dir)
        atomic_json(run.dir / "frames.json", manifest)
        browser = report.get("browser") if isinstance(report.get("browser"), dict) else {}
        layer = run.manifest["layer"]
        layer.update(report={k: report.get(k) for k in ("frames", "duration_frames", "fps", "width", "height", "remotion_version")},
                     browser={"status": browser.get("status"), "path": browser.get("path"), "version": browser_version(browser.get("path"))},
                     frames_manifest={"path": "frames.json", "count": manifest["count"], "sha256": sha256(run.dir / "frames.json")},
                     render_seconds=report["seconds"])
        if report.get("remotion_version") not in (None, version):
            run.manifest["warnings"].append(f"描画スクリプトの Remotion の版 {report['remotion_version']} が node_modules の {version} と違います")
        if report.get("duration_frames") not in (None, manifest["count"]):
            raise_message = f"連番のコマ数 {manifest['count']} がコンポジションの {report['duration_frames']} と違います"
            run.finish(False, raise_message)
            raise PipelineError(raise_message)
        run.save()
        run.log(f"frames={manifest['count']} browser={layer['browser']['path']} seconds={report['seconds']}")
        say(f"[layer] {manifest['count']} コマ（{report['seconds']:.1f} 秒）")
        if twice:
            again_dir = run.dir / "frames-again"
            again_dir.mkdir()
            try:
                second = _run_renderer(renderer, entry, composition, props, again_dir, public, concurrency, run.log_path)
                again = frames_manifest(again_dir)
            except PipelineError as error:   # 2 回目の失敗は記録であって、1 回目の成功した連番を失う理由にはしない
                layer["deterministic"] = {"same": None, "frames_compared": 0, "differing_frames": [], "error": str(error)[:1000]}
                run.manifest["warnings"].append("2 回目の描画が失敗したため、決定論は確かめられていません")
                run.log(f"twice: second render failed: {error}")
                say("[layer] 2 回目の描画が失敗しました（1 回目の連番は残します）")
            else:
                same = [a["sha256"] == b["sha256"] for a, b in zip(manifest["frames"], again["frames"])]
                layer["deterministic"] = {"same": all(same) and again["count"] == manifest["count"], "frames_compared": len(same),
                                          "differing_frames": [a["frame"] for a, ok in zip(manifest["frames"], same) if not ok][:50],
                                          "second_render_seconds": second["seconds"]}
                run.log(f"twice: same={layer['deterministic']['same']} differing={len(layer['deterministic']['differing_frames'])}")
                say(f"[layer] 2 回目: {'全コマ一致' if layer['deterministic']['same'] else '一致しないコマあり'}")
            finally:
                shutil.rmtree(again_dir, ignore_errors=True)
        # 台帳の再利用は variants の path（ファイル）の sha256 で確かめるので、連番の manifest を出力の代表にする
        run.manifest["variants"]["frames"] = {"path": "frames.json", "frames": manifest["count"], "sha256": layer["frames_manifest"]["sha256"],
                                              "technical_pass": True, "checks": {"pass": True, "failures": [], "kind": "frames_manifest"}}
        run.save()
        session.succeed()
    return _summarize(run.dir, run.manifest, reused=False, began=began)


def _summarize(run_dir, manifest, reused, began):
    layer = manifest["layer"]
    return {"run_id": manifest["run_id"], "run_dir": str(run_dir), "reused": reused, "status": manifest["status"],
            "frames": layer["frames_manifest"]["count"], "frames_dir": str(Path(run_dir) / "frames"),
            "frames_manifest_sha256": layer["frames_manifest"]["sha256"], "remotion_version": layer["inputs"]["remotion_version"],
            "browser": layer["browser"], "render_seconds": layer["render_seconds"],
            "deterministic": None if layer["deterministic"] is None else layer["deterministic"]["same"],
            "warnings": manifest["warnings"], "seconds": round(time.monotonic() - began, 1)}
