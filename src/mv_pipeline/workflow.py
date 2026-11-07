"""Purpose-specific production plans and evidence checks, outside contracts/.

This module does not render, run models, or judge artistic quality. Requirements
come from the selected purpose, never from the editable guidance in the plan.
"""
import os
import shlex
import tempfile
from pathlib import Path

from . import validate
from .fx.timeline import frame_count
from .io import PipelineError, atomic_json, read_json, safe_path, sha256

SCHEMA = "orpheus-workflow/0.1"
PURPOSES = ("lyric-mv", "dance-edit")
OMITTABLE = {"beats", "lyrics"}
STAGES = ("inputs", "beats", "lyrics", "direction", "assets", "render", "verification")
INSPECTION = "inspection"          # 検品（mv inspect）の行。計画の inspection か --inspect-report があるときだけ出す
INSPECTION_MODES = ("optional", "required")
INSPECT_REPORT = "inspect-report/0.1"
SCOPE = "必要工程と現在の入力・出力の証拠を照合します。演出の良し悪し、実曲の同期精度、作品採用は判定しません。"


def _object(path):
    value = read_json(path)
    validate.require(isinstance(value, dict), f"JSON object required: {path}")
    return value


def _reason(reason):
    validate.require(isinstance(reason, str) and bool(reason.strip()), "省略には空でない理由が必要です")
    return reason.strip()


def _load(path):
    path = Path(path).resolve()
    plan = _object(path)
    validate.require(plan.get("schema_version") == SCHEMA, "Unknown workflow schema_version")
    validate.require(plan.get("purpose") in PURPOSES, "Unknown workflow purpose")
    validate.require(isinstance(plan.get("target"), str) and bool(plan["target"].strip()), "workflow target is required")
    validate.require(plan.get("lyrics") is None or isinstance(plan["lyrics"], bool), "lyrics must be true, false, or null")
    validate.require(plan.get("inspection") is None or plan["inspection"] in INSPECTION_MODES,
                     f"inspection must be one of {INSPECTION_MODES} or absent")
    validate.require(plan["purpose"] != "lyric-mv" or plan.get("lyrics") is True, "lyric-mv requires lyrics")
    omissions = plan.get("omissions", {})
    validate.require(isinstance(omissions, dict), "omissions must be an object")
    for stage, reason in omissions.items():
        validate.require(stage in OMITTABLE, f"工程 {stage!r} は省略できません")
        _reason(reason)
    plan["omissions"] = omissions
    return path, plan, (path.parent / plan["target"]).resolve()


def _sung_lines(data):
    layers = {layer["name"]: layer for layer in data["lyrics"]["layers"]}
    return [line for line in data["lyrics"]["lines"]
            if line["start"] < data["duration_s"] and line["end"] > 0
            and (line["sung"] if line["sung"] is not None
                 else line["style"].get("sung", layers[line["layer"]]["sung"]))]


def _guidance(purpose, target, lyrics):
    q = shlex.quote(str(target))
    if purpose == "lyric-mv":
        rows = [
            ("inputs", "入力と時間原点", "完成ミックス・対象区間のUTF-8歌詞", f"uv run mv init {q} --audio <AUDIO> --lyrics <LYRICS> --start <SECONDS> --duration <SECONDS>", "project.json / analysis.json / work/audio/clip.wav"),
            ("beats", "拍の取得", "同じ原点の完成ミックス", f"uv run mv analyze {q}", "analysis.json の beats_s → 演出担当"),
            ("lyrics", "歌詞時刻の確認", "歌詞と音源の同期結果、または確認した行時刻", f"uv run mv import-alignment {q} <ALIGNMENT.json>; uv run mv apply-edits {q} <TIMINGS.json>", "analysis.json の行時刻・出所・確認状態 → 演出と字幕"),
            ("direction", "演出の割り当て", "拍・歌詞時刻・歌詞の意味・世界観", f"uv run mv export-brief {q}; uv run mv import-storyboard {q} <STORYBOARD.json>", "work/brief → AIの構成・字幕・カット・動き → storyboard.json"),
            ("assets", "映像素材", "演出に対応する画像と参照関係", f"uv run mv import-assets {q} <ASSETS.json>", "images / assets.json → 描画"),
            ("render", "最終描画", "現在のanalysis・storyboard・assets", f"uv run mv preview {q}; uv run mv render {q} --profile final", "runs/<run_id>/video.mp4 と入力スナップショット"),
            ("verification", "出力の検証", "同じ入力で生成した最終run", f"uv run mv verify {q}", "verification.json / recheck.json → 技術検証（作品採用は別）"),
        ]
    else:
        rows = [
            ("inputs", "入力と歌詞の有無", "完成ミックス・既存ダンス素材・歌詞の有無", f"uv run mv workflow plan {q} --purpose dance-edit --lyrics  # 歌詞なしは --no-lyrics", "spec.json。音源と編集の0秒を揃える"),
            ("beats", "拍の取得と照合", "音源由来の拍候補、または明示したBPM・原点", "uv run mv analyze <AUDIO_PROJECT>; uv run mv gate check <GATE.json>", "analysis.json の拍 → spec.beats → timeline → gateの拍検査"),
            ("lyrics", "歌詞時刻の照合", "歌詞時刻とボーカル、表示対象の歌詞層", "uv run mv gate lines <LINES.lrc> <LINES_DIR> --vocals <VOCALS.wav>", "推定/実測の区別を保持 → spec.lyrics と gate.measured_lines"),
            ("direction", "カット・字幕・動き", "拍・歌詞の意味・既存素材の動き", f"uv run mv dance plan {q}", "AIがspecへ割り当て、解決した時刻の表を確認"),
            ("assets", "素材と文字の配置", "背景・人物・音源・使用書体", f"uv run mv dance stills {q} --times <SECONDS> --guides all --layers", "静止画で配置を確認 → 最終描画"),
            ("render", "全尺・原寸の描画", "現在のspecと素材", f"uv run mv dance render {q}", "dance_runs/<run_id>/dance.mp4 / manifest.json / timeline.json"),
            ("verification", "同一runの検証", "runのtimeline・videoと照合用データ", "uv run mv gate check <GATE.json>", "必須の拍・歌詞検査と技術検証。試聴・作品採用は別"),
        ]
    return [{"id": key, "title": title, "required": key != "lyrics" or lyrics is not False,
             "inputs": inputs, "command": command, "outputs": outputs}
            for key, title, inputs, command, outputs in rows]


def _inspection_row(mode):
    """検品の行。mode が required なら必須（検品を必須にするかは制作ごとに決める）。採否は判定しない。"""
    return {"id": INSPECTION, "title": "検品（mv inspect）", "required": mode == "required",
            "inputs": "最終動画か、spec の素材を見た mv inspect check の inspect_report.json",
            "command": "uv run mv inspect check <INSPECT.json>; uv run mv workflow check <PLAN> --inspect-report <inspect_report.json>",
            "outputs": "検品の記録（run_id・時刻・見た動画の sha256・要確認の数・見ていない検査）。異常かどうか・採否は人"}


def create_plan(target, purpose, out=None, lyrics=None, inspection=None):
    validate.require(purpose in PURPOSES, f"purpose must be one of {PURPOSES}")
    validate.require(inspection is None or inspection in INSPECTION_MODES, f"inspection must be one of {INSPECTION_MODES}")
    validate.require(lyrics is None or isinstance(lyrics, bool), "lyrics must be a boolean")
    target = Path(target).resolve()
    if purpose == "lyric-mv":
        validate.require(lyrics is not False, "lyric-mv requires lyrics")
        validate.require(not target.exists() or target.is_dir(), "lyric-mv target must be a project directory")
        lyrics = True
    elif lyrics is None and target.is_file():
        from .dance.spec import load_spec
        lyrics = bool(_sung_lines(load_spec(target).data))
    path = Path(out).resolve() if out is not None else (target if purpose == "lyric-mv" else target.parent) / "workflow.json"
    validate.require(not path.exists(), f"Workflow plan already exists: {path}")
    try:
        relative = os.path.relpath(target, path.parent).replace(os.sep, "/")
    except ValueError:  # Different Windows drives.
        relative = str(target)
    plan = {"schema_version": SCHEMA, "purpose": purpose, "target": relative,
            "lyrics": lyrics, "omissions": {}, "scope": SCOPE,
            "stages": _guidance(purpose, target, lyrics) + ([_inspection_row(inspection)] if inspection else [])}
    if inspection:
        plan["inspection"] = inspection
    atomic_json(path, plan)
    return {"plan": str(path), **plan}


def omit(plan_path, stage, reason):
    path, plan, _ = _load(plan_path)
    validate.require(stage in OMITTABLE, f"工程 {stage!r} は省略できません（beats / lyrics のみ）")
    plan["omissions"][stage] = _reason(reason)
    atomic_json(path, plan)
    return {"plan": str(path), "omissions": plan["omissions"]}


def restore(plan_path, stage):
    path, plan, _ = _load(plan_path)
    validate.require(stage in OMITTABLE, f"Unknown omittable stage: {stage}")
    plan["omissions"].pop(stage, None)
    atomic_json(path, plan)
    return {"plan": str(path), "omissions": plan["omissions"]}


def _set(rows, stage, status, summary, evidence=()):
    rows[stage].update(observed_status=status, status=status, summary=summary,
                       evidence=[str(path) for path in evidence])


def _attempt(rows, stage, action):
    try:
        return action()
    except (PipelineError, OSError, ValueError, KeyError, TypeError) as error:
        _set(rows, stage, "fail", str(error))
        return None


def _file_hash(path, expected):
    validate.require(isinstance(expected, str) and len(expected) == 64, f"Missing SHA-256: {path}")
    validate.require(Path(path).is_file(), f"Missing referenced file: {path}")
    validate.require(sha256(path) == expected, f"File hash mismatch: {path}")


def _inspect_mv(root, rows, run_dir, media):
    if not all((root / name).is_file() for name in ("project.json", "analysis.json")):
        return

    def inputs():
        a = _object(root / "analysis.json")
        validate.analysis(a, root)
        _set(rows, "inputs", "pass", "音源・歌詞・切り出し原点とハッシュを確認", [root / "analysis.json"])
        return a

    a = _attempt(rows, "inputs", inputs)
    if a is None:
        return
    _set(rows, "beats", "pass" if a["beats_s"] else "pending",
         f"拍候補 {len(a['beats_s'])} 件。カットへの使い方・同期精度の評価は別" if a["beats_s"] else "拍情報がありません。mv analyze で音源から取得してください", [root / "analysis.json"])
    unresolved = [line["id"] for line in a["lyrics"] if line["start_s"] is None or line["end_s"] is None
                  or line["timing_source"] == "unknown" or line["review_status"] != "confirmed"]
    stale = [item["code"] for item in a["issues"] if item["code"].startswith("stale_")]
    _set(rows, "lyrics", "fail" if stale else "pending" if not a["lyrics"] or unresolved or a["issues"] else "pass",
         f"古い入力に対する修正があります: {stale}" if stale else f"確認済み {len(a['lyrics']) - len(unresolved)} / {len(a['lyrics'])} 行。未解決: {unresolved or a['issues']}" if unresolved or a["issues"]
         else "歌詞がありません" if not a["lyrics"] else "全行の時刻と確認状態が揃っています（推定の出所は保持）", [root / "analysis.json"])

    story = None
    if (root / "storyboard.json").is_file():
        def direction():
            s = _object(root / "storyboard.json")
            validate.storyboard(s, a, sha256(root / "analysis.json"))
            validate.require(s["render"]["show_lyrics"], "lyric-mv requires storyboard.render.show_lyrics=true")
            _set(rows, "direction", "pass", "現在の解析に対応する演出と歌詞表示を確認。演出の採否は別", [root / "storyboard.json"])
            return s
        story = _attempt(rows, "direction", direction)
    if (root / "assets.json").is_file():
        def assets():
            data = _object(root / "assets.json")
            ids = validate.assets(data, root)
            if story is not None:
                validate.storyboard(story, a, sha256(root / "analysis.json"), ids)
            review = root / "work/asset-review.json"
            pending = _object(review).get("review_required", []) if review.exists() else []
            _set(rows, "assets", "pending" if pending else "pass", f"参照画像変更の確認待ち: {pending}" if pending else "素材・寸法・ハッシュ・参照関係を確認", [root / "assets.json"])
        _attempt(rows, "assets", assets)
    if story is None or rows["assets"]["status"] != "pass":
        return
    job_path = root / "job.json"
    job = _attempt(rows, "render", lambda: _object(job_path)) if job_path.exists() else None
    if job is None:
        return
    current_id = job.get("current_run_id")
    chosen = Path(run_dir).resolve() if run_dir is not None else safe_path(root, f"runs/{current_id}") if current_id else None
    if chosen is None or not chosen.is_dir():
        return

    def rendered():
        stage = job.get("stages", {}).get("render", {})
        validate.require(stage.get("status") != "failed", "The recorded render failed")
        if stage.get("status") != "succeeded":
            _set(rows, "render", "pending", "成功した最終描画の記録がありません")
            return None
        manifest = _object(chosen / "manifest.json")
        validate.require(manifest.get("profile") == "final", "A preview render cannot complete the final workflow")
        for name in ("project", "analysis", "storyboard", "assets"):
            _file_hash(chosen / f"{name}.json", sha256(root / f"{name}.json"))
        _file_hash(chosen / "public/audio.wav", a["audio"]["sha256"])
        for asset in _object(root / "assets.json")["assets"]:
            _file_hash(chosen / "public" / (asset["asset_id"] + Path(asset["path"]).suffix.lower()), asset["sha256"])
        # The normal pipeline stores output hashes in job.json, not in manifest.
        # An exported copy of the current run can live outside the project.
        outputs = stage.get("outputs", {})
        prefix = f"runs/{current_id}/"
        validate.require(prefix + "video.mp4" in outputs and prefix + "verification.json" in outputs,
                         "Render journal does not bind the video and verification to this run")
        for relative, expected in outputs.items():
            validate.require(relative.startswith(prefix), "Render journal contains a different run")
            _file_hash(safe_path(chosen, relative[len(prefix):]), expected)
        _set(rows, "render", "pass", "現在の入力に一致する最終runと出力ハッシュを確認", [chosen / "video.mp4"])
        media["final_video"] = chosen / "video.mp4"
        return chosen

    chosen = _attempt(rows, "render", rendered)
    if chosen is None:
        return

    def verified():
        report = _object(chosen / "verification.json")
        expected = {name: sha256(root / f"{name}.json") for name in ("analysis", "storyboard", "assets")}
        validate.require(report.get("technical_pass") is True and report.get("profile") == "final", "Final technical verification has not passed")
        validate.require(report.get("input_hashes") == expected, "Verification refers to different inputs")
        _set(rows, "verification", "pass", "技術検証の記録と現在の入力・出力を照合。試聴・作品採用は別", [chosen / "verification.json"])
    _attempt(rows, "verification", verified)


def _timeline_matches(sp, record, manifest):
    """Bind the unhashed timeline to current spec, including actual lyric boxes."""
    from .dance import SCHEMA_V01
    from .dance import lyrics as dance_lyrics
    from .gate.checks import _validate_timeline
    data = sp.data
    _validate_timeline(record)
    # align は後から足した任意の欄。無い timeline（前の版の run）は "beat" として比べる
    record = {**record, **{key: [{"align": "beat", **item} for item in record[key]] for key in ("seam_info", "flash_info") if key in record}}
    size = [data["output"]["width"], data["output"]["height"]]
    fps, duration = data["output"]["fps"], data["duration_s"]
    interval = {"start": 0.0, "duration": duration, "frames": frame_count(duration, fps)}
    expected = {"size": size, "fps": fps, "duration_s": duration, "interval": interval,
                "ui_regions": data["lyrics"]["ui_regions"], "flashes": [f["t"] for f in data["flashes"]]}
    clips = data["performer"]["clips"]
    seams = [(right["start"], i, i + 1) for i, (left, right) in enumerate(zip(clips, clips[1:])) if abs(right["start"] - left["end"]) < 1e-9]
    expected["seams"] = [t for t, _, _ in seams]
    clip_keys = ("path", "start", "end") if data["source_schema"] == SCHEMA_V01 else ("path", "start", "end", "kind", "transition_in", "beat_exempt")
    expected["clips"] = [{key: c[key] for key in clip_keys} for c in clips]
    if data["source_schema"] != SCHEMA_V01:
        expected.update(
            beats=None if data["beats"] is None else {key: data["beats"][key] for key in ("beats", "downbeats")},
            layers=[{key: layer[key] for key in ("name", "z", "exclusive", "sung", "follow_camera")} for layer in data["lyrics"]["layers"]],
            seam_info=[{"t": t, "from": left, "to": right, "kind": clips[right]["transition_in"]["type"], "beat_exempt": clips[right]["beat_exempt"],
                        "align": clips[right]["align"]} for t, left, right in seams],
            flash_info=[{key: f[key] for key in ("t", "strength", "decay_s", "mode", "beat_exempt", "align")} for f in data["flashes"]],
            camera_seams=[{key: s[key] for key in ("t", "span_s", "pre_s", "post_s", "zoom", "rotate_deg", "rotate_post_deg", "pivot", "blur")} for s in data["camera"]["seams"]],
        )
    for key, value in expected.items():
        validate.require(record.get(key) == value, f"timeline {key} does not match the current full-size/full-duration spec")
    # dance は end の前でフェードアウトを終える。lyric_fade の無い前の版の run は通すが、after と書いた timeline は dance の描画ではない
    validate.require(record.get("lyric_fade", "inside") == "inside", "timeline lyric_fade must be inside for a dance render")
    lines, _ = dance_lyrics.prepare(data, sp.resolve, size)
    validate.require(len(record["lyrics"]) == len(lines), "timeline lyric count differs from spec")
    for row, line in zip(record["lyrics"], lines):
        box = [round(value / size[i % 2], 5) for i, value in enumerate(line.box_px)]
        expected_line = {"layer": line.layer, "text": line.text, "start": line.start, "end": line.end,
                         "box": box, "overflow": line.overflow}
        if data["source_schema"] != SCHEMA_V01:
            expected_line.update(sung=line.sung, exclusive=line.style["exclusive"],
                                 fade_in_s=max(line.style["fade_in_s"], line.style["enter"]["dur_s"] if line.style["enter"] else 0.0),
                                 fade_out_s=line.fall_s)
        for key, value in expected_line.items():
            validate.require(row.get(key) == value, f"timeline lyric {key} differs from spec: {line.text}")
    if manifest.get("hooks"):
        finished = record.get("finished")
        validate.require(isinstance(finished, dict) and isinstance(finished.get("hooks"), dict),
                         "timeline is missing the recorded finishing hook")
        validate.require(finished["hooks"].get("sha256") == manifest["hooks"]["sha256"], "timeline finishing hook differs from render")
    else:
        validate.require(record.get("finished") is None, "timeline contains an unrecorded finishing hook")


def _inspect_dance(target, plan, rows, run_dir, gate_config, out, media):
    from .dance import spec as dancespec
    if not target.is_file():
        return None
    sp = _attempt(rows, "inputs", lambda: dancespec.load_spec(target))
    if sp is None:
        return None
    media["spec_sha256"] = sp.sha256
    data = sp.data
    if data["audio"] is None:
        _set(rows, "inputs", "pending", "音楽に合わせる編集には音源を指定してください")
    elif plan["lyrics"] is None:
        _set(rows, "inputs", "pending", "plan の lyrics に歌詞の有無を明示してください（true / false）")
    else:
        _set(rows, "inputs", "pass", "spec・音源の指定と歌詞の有無を確認", [target])
    _set(rows, "direction", "pass", "カット・字幕・カメラ時刻を解決。動きの採否は別", [target])
    sung = _sung_lines(data)
    if plan["lyrics"] is False and sung:
        _set(rows, "lyrics", "fail", "歌詞なしの計画に sung=true の表示行があります。計画かspecを修正してください")
    elif plan["lyrics"] is False:
        _set(rows, "lyrics", "not_applicable", "歌詞なしを明示。題字・クレジットは歌詞同期の対象外")
    else:
        _set(rows, "lyrics", "pending", "歌詞行がありません" if not sung else "歌詞時刻の照合がまだありません", [target])
    _set(rows, "beats", "pending", "拍格子はありますが同一runの拍検査が必要です" if data["beats"] else "拍格子がありません。spec.beatsへ接続してください", [target])

    def assets():
        refs = dancespec.asset_refs(data)
        result = {}
        for role, relative in refs.items():
            path = sp.resolve(relative)
            validate.require(path.is_file(), f"Missing referenced asset ({role}): {path}")
            result[role] = {"path": relative, "sha256": sha256(path)}
        _set(rows, "assets", "pass", "素材の存在と現在のハッシュを取得", [sp.resolve(path) for path in refs.values()])
        media["assets"] = {item["sha256"] for item in result.values()}
        return result
    current_assets = _attempt(rows, "assets", assets)
    if current_assets is None or run_dir is None:
        if run_dir is None:
            _set(rows, "render", "pending", "確認対象の最終runを --run DIR で指定してください")
        return None
    run = Path(run_dir).resolve()
    if not run.is_dir():
        _set(rows, "render", "fail", f"Run directory does not exist: {run}")
        return None

    def rendered():
        m = _object(run / "manifest.json")
        validate.require(m.get("kind") == "dance-render", "This is not a dance render")
        validate.require(m.get("status") != "failed", "Dance render failed")
        if m.get("status") != "succeeded":
            _set(rows, "render", "pending", "描画が完了していません")
            return None
        validate.require(m.get("technical_pass") is True, "Dance technical verification failed")
        _file_hash(run / "spec.json", sp.sha256)
        validate.require(m.get("spec", {}).get("sha256") == sp.sha256, "Render uses a different spec")
        validate.require(m.get("assets") == current_assets, "Render uses different or stale assets")
        if m.get("hooks"):
            hook_path = Path(m["hooks"]["path"])
            if not hook_path.is_absolute() and not hook_path.is_file():
                hook_path = sp.base_dir / hook_path
            _file_hash(hook_path, m["hooks"].get("sha256"))
        fps, duration = data["output"]["fps"], data["duration_s"]
        wanted = {"start": 0.0, "duration": duration, "fps": fps, "frames": frame_count(duration, fps)}
        validate.require(m.get("interval") == wanted, "A partial render cannot complete this workflow")
        output = m.get("output", {})
        validate.require(output.get("scale") == 1.0 and output.get("size") == [data["output"]["width"], data["output"]["height"]], "A scaled preview cannot complete this workflow")
        variant = m.get("variants", {}).get("dance", {})
        validate.require(variant.get("technical_pass") is True, "Dance output verification did not pass")
        video = safe_path(run, variant.get("path"))
        _file_hash(video, variant.get("sha256"))
        record = _object(run / "timeline.json")
        _timeline_matches(sp, record, m)
        _set(rows, "render", "pass", "現在のspec・素材と一致する全尺・原寸runを確認", [video, run / "manifest.json", run / "timeline.json"])
        media["final_video"] = video
        return video, record
    rendered_result = _attempt(rows, "render", rendered)
    if rendered_result is None:
        return None
    if gate_config is None:
        _set(rows, "verification", "pending", "同一runを指定した --gate-config PATH が必要です")
        return None
    video, record = rendered_result

    def gated():
        from .gate import checks
        config_path = Path(gate_config).resolve()
        config = _object(config_path)
        def path_of(key):
            return (config_path.parent / config[key]).resolve() if config.get(key) else None
        validate.require(path_of("timeline") == run / "timeline.json" and path_of("video") == video,
                         "gate config must reference the selected run's timeline and video")
        if config.get("spec"):
            validate.require(path_of("spec") == sp.path, "gate spec must be the current spec")
        if config.get("beats"):
            external = _object(path_of("beats"))
            beats = external.get("beats") or external.get("beats_s") or []
            downbeats = external.get("downbeats") or []
            validate.require(data["beats"] is not None and beats == data["beats"]["beats"]
                             and downbeats == data["beats"]["downbeats"],
                             "gate beats must match the grid connected to the current spec")
        if plan["lyrics"] is not False and sung and config.get("measured_lines"):
            layers = config.get("lyric_layers") or config.get("lyric_layer", "band")
            layers = [layers] if isinstance(layers, str) else layers
            validate.require({line["layer"] for line in sung} <= set(layers), "gate lyric layers do not cover every sung layer")
            shown = [line for line in record["lyrics"] if line["layer"] in layers and line.get("sung", True)]
            measured = read_json(path_of("measured_lines"))
            validate.require(isinstance(measured, list) and [line["text"] for line in shown] == [line["text"] for line in measured],
                             "Measured lyric text/order does not match the selected sung lines")
        if out is None:
            with tempfile.TemporaryDirectory(prefix="orpheus-workflow-gate-") as temporary:
                report, _ = checks.run(config_path, Path(temporary))
        else:
            report, _ = checks.run(config_path, Path(out) / "gate_report")
        lookup = {item["check"]: item for item in report["checks"]}
        for stage, label in (("beats", "拍"), ("lyrics", "歌詞の時刻")):
            if stage == "lyrics" and (plan["lyrics"] is False or not sung):
                continue
            item = lookup[label]
            status = "pass" if item["status"] == "pass" else "fail" if item["status"] == "fail" else "pending"
            _set(rows, stage, status, item["summary"], [config_path, run / "timeline.json"])
            rows[stage]["gate_status"] = item["status"]
        _set(rows, "verification", "fail" if report["overall"] == "fail" else "pass",
             f"現在のrunでgateを再実行: {report['overall']}。必須項目は各工程で別途照合", [config_path])
        return report
    return _attempt(rows, "verification", gated)


def _inspection(rows, reports, media):
    """検品の記録を照合する。報告が今の最終動画（sha256）・spec（dance）・素材のどれを見たものかを確かめ、未実施・検品済み
    （要確認・見つけた所なし・見ていない検査あり）・古い検品を分ける。異常かどうか・採否は判定しない。"""
    if not reports:
        _set(rows, INSPECTION, "pending", "検品の記録がありません（mv inspect check の inspect_report.json を --inspect-report で渡す）")
        rows[INSPECTION]["inspection_state"] = "not_run"
        return
    final = sha256(media["final_video"]) if media.get("final_video") else None
    entries = []
    for report_path in reports:
        report_path = Path(report_path).resolve()
        data = _object(report_path)
        validate.require(data.get("schema_version") == INSPECT_REPORT, f"Not an mv inspect report ({INSPECT_REPORT}): {report_path}")
        seen = {clip.get("sha256") for clip in data.get("clips", [])}
        run = data.get("run") or {}
        spec = next((item.get("sha256") for item in run.get("inputs", []) if item.get("role") == "dance_spec"), None)
        current = bool(seen) and bool(media.get("assets")) and seen <= media["assets"]   # 見た動画がどれも今の素材
        if final and final in seen:
            target = "final_video"
        elif spec and spec == media.get("spec_sha256") and current:     # spec の JSON は素材をパスで指すだけ。同じパスで差し替えた
            target = "spec_sources"                                     # 素材は spec の sha256 では分からないので、見た動画も照らす
        elif current:
            target = "assets"
        else:
            target = None
        summary = data.get("summary", {})
        frames = (data.get("vlm") or {}).get("frames") or {}
        entries.append({"report": str(report_path), "run_id": run.get("id"), "finished_at": run.get("finished_at"),
                        "overall": data.get("overall"), "target": target, "events": summary.get("events"),
                        "events_without_cue": (summary.get("cues") or {}).get("events_without_cue"),
                        "skipped": len(data.get("skipped", [])), "vlm": (data.get("vlm") or {}).get("status"),
                        "vlm_unsent": frames.get("events_unsent")})
    rows[INSPECTION]["inspections"] = entries
    matched = [entry for entry in entries if entry["target"]]
    if not matched:
        _set(rows, INSPECTION, "pending", "渡した検品は、今の最終動画・spec・素材のどれとも sha256 が合いません（古いか別の動画の検品）",
             [entry["report"] for entry in entries])
        rows[INSPECTION]["inspection_state"] = "stale"
        return
    if any(entry["overall"] == "review" for entry in matched):
        state = "review"
    elif any(entry["overall"] == "clean_with_skips" for entry in matched):
        state = "clean_with_skips"
    else:
        state = "clean"
    labels = {"final_video": "最終動画", "spec_sources": "spec の素材", "assets": "素材"}
    parts = []
    for entry in matched:
        detail = (f"{labels[entry['target']]}を検品（{entry['run_id'] or '記録なし'}・{entry['finished_at'] or '時刻なし'}）: "
                  f"{entry['overall']}・要確認 {entry['events']} 件")
        if entry["events_without_cue"] is not None:
            detail += f"（演出と重ならない {entry['events_without_cue']} 件）"
        if entry["skipped"]:
            detail += f"・見ていない検査 {entry['skipped']}"
        if entry["vlm_unsent"]:
            detail += f"・VLM に見せなかった出来事 {entry['vlm_unsent']}"
        parts.append(detail)
    _set(rows, INSPECTION, "pass", " / ".join(parts) + "。異常かどうか・採否は人が決める", [entry["report"] for entry in matched])
    rows[INSPECTION]["inspection_state"] = state


def check(plan_path, run_dir=None, gate_config=None, out=None, inspect_reports=()):
    path, plan, target = _load(plan_path)
    stages = _guidance(plan["purpose"], target, plan["lyrics"])
    if plan.get("inspection") or inspect_reports:      # 検品の行は、計画で決めたときか、記録を渡したときだけ（前の計画は前のまま）
        stages.append(_inspection_row(plan.get("inspection", "optional")))
    rows = {item["id"]: {**item, "observed_status": "pending", "status": "pending", "summary": "必要な入力・成果物がまだありません", "evidence": []}
            for item in stages}
    gate, media = None, {}
    if plan["purpose"] == "lyric-mv":
        _inspect_mv(target, rows, run_dir, media)
    else:
        gate = _inspect_dance(target, plan, rows, run_dir, gate_config, out, media)
    if INSPECTION in rows:
        _attempt(rows, INSPECTION, lambda: _inspection(rows, list(inspect_reports), media))
    for stage, reason in plan["omissions"].items():
        rows[stage]["omission_reason"] = reason
        if rows[stage]["observed_status"] == "pending":
            rows[stage]["status"] = "omitted"
    failed = [row["id"] for row in rows.values() if row["status"] == "fail"]
    missing = [row["id"] for row in rows.values() if row["required"] and row["status"] not in {"pass", "omitted"}]
    omitted = [row["id"] for row in rows.values() if row["status"] == "omitted"]
    overall = "failed" if failed else "incomplete" if missing else "complete_with_omissions" if omitted else "complete"
    report = {"schema_version": SCHEMA, "plan": str(path), "purpose": plan["purpose"], "target": str(target),
              "overall": overall, "accepted": False, "scope": SCOPE,
              "failed": failed, "missing": missing, "omitted": omitted, "stages": list(rows.values())}
    if gate is not None:
        report["gate"] = gate
    if out is not None:
        folder = Path(out).resolve()
        report["report"] = str(folder / "workflow_report.json")
        atomic_json(folder / "workflow_report.json", report)
        text = [f"# 制作工程: {overall}", "", SCOPE, "", f"対象: `{target}`", "", "| 工程 | 状態 | 確認結果 |", "| --- | --- | --- |"]
        for row in rows.values():
            detail = row["summary"] + (f" / 省略理由: {row['omission_reason']}" if row.get("omission_reason") else "")
            detail = detail.replace("|", "\\|").replace("\n", " ")
            text.append(f"| {row['title']} | {row['status']} | {detail} |")
        (folder / "workflow_report.md").write_text("\n".join(text) + "\n", encoding="utf-8")
    return report
