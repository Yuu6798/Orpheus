"""`mv inspect check`: 設定を読み、クリップごとに特徴を測って信号と出来事にし、直す場所・修正計画・報告を書く。

    mv inspect check inspect.json [--out DIR] [--vlm VLM.json [--allow-send]] [--vlm-packet] [--vlm-response FILE] [--vlm-legacy-response]

出力先は既定で設定の隣の inspect_report/:
    inspect_report.json / inspect_report.md / fix_plan.json / stills/<出来事>.jpg / anchors/<クリップ>_f<コマ>.png / metrics/<クリップ>.csv
    references/NN.png（設定画ごとの切り抜きの確かめ。設定画があるときだけ）
    vlm/（request.json・frames/・reference/・response.json・adapter.log。VLM を使ったときだけ）
    inspect_runs.json（実行ごとの run_id・状態（running / succeeded / failed）・入力の sha256）

1 回の実行は出力先の中の作業用フォルダー（.staging-<run_id>）に全部を書き、最後まで済んだときだけ前の結果と入れ替える。
途中で失敗・中断すると、前の成功した結果（報告と静止画など）がそのまま残る。入れ替えの途中で失敗したとき（ファイルが
開かれていて動かせない・Ctrl+C など）は、その場で前の結果へ戻す。強制終了などで戻せなかったときは、次の実行の初めに戻す。
同じ出力先では 1 つの実行だけが動く（出力先の .lock。ほかの実行が使っている間は、何も触らずに止まる）。

overall は review（要確認の出来事か復号のエラーがある）/ clean_with_skips（見つけた所は無いが、見ていない検査がある）/ clean。
どれも合否ではない（終了コードは設定や入力の誤りのときだけ 1）。
"""
import os
import re
import shutil
import time
import uuid
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

from ..fx import probe
from ..io import PipelineError, atomic_json, read_json, sha256
from ..runner import project_lock
from . import CATEGORIES, REPORT_SCHEMA, VERSION, config, cues, detect, features, fingerprint, reference, report, targets, video, vlm


MARKER = "inspect_output.txt"     # この印のある出力先だけ、前の実行の stills/ などを入れ替える
# 1 回の実行で書くもの（入れ替える順。報告の JSON は最後に置き、置いた時には静止画などがそろっている）
OUTPUTS = ("stills", "anchors", "metrics", "references", "vlm", "fix_plan.json", "inspect_report.md", "inspect_report.json")
FOLDERS = ("stills", "anchors", "metrics", "references", "vlm")
RUNS = "inspect_runs.json"        # 実行ごとの記録（inspect-runs/0.1）
RUNS_SCHEMA = "inspect-runs/0.1"
RUNS_KEPT = 50
STAGING = ".staging-"             # 実行中の作業用フォルダー
BACKUP = ".previous-"             # 入れ替える間だけ、前の結果を置くフォルダー
JOURNAL = ".publish.json"         # 入れ替えの途中で止まったときに、前の結果へ戻すための控え
FAILED = "failed"                 # 失敗した実行の VLM のログと返答（failed/<run_id>/）。新しい FAILED_KEPT 回分だけ残す
FAILED_KEPT = 5


def _safe_id(text, taken):
    base = re.sub(r"[^A-Za-z0-9_.-]", "_", text)[:48] or "clip"
    name, serial = base, 2
    while name in taken:
        name, serial = f"{base}-{serial}", serial + 1
    taken.add(name)
    return name


def _video_record(path):
    if video.is_image(path):
        info = video.image_info(path)
        return info, {"ok": True, "frames": 1, "error": None}
    info = video.describe(path)
    decoded = probe.decode(path, "v")
    return info, {"ok": decoded["ok"], "frames": decoded["frames"], "error": decoded["error"]}


def _clips_from_config(cfg):
    out = []
    for item in cfg.clips:
        info, decoded = _video_record(item["path"])
        total = decoded["frames"]
        first, last = item["frames"] or (0, total)
        notes = []
        if last > total:
            notes.append(f"frames の終わり {last} が動画のコマ数 {total} を超えるので、{total} までにしました")
            last = total
        if first >= last:
            raise PipelineError(f"{item['id']}: 見るコマがありません（frames {item['frames']}・動画 {total} コマ）")
        out.append({"id": item["id"], "path": item["path"], "rel": item["rel"], "sha256": sha256(item["path"]), "info": info,
                    "decode": decoded, "fps": info["fps"], "frames_total": total, "inspected": [first, last],
                    "foreground": item["foreground"] or cfg.foreground, "origin": item["origin"], "notes": notes, "cues": item.get("cues")})
    return out


def _clips_from_dance(cfg):
    from ..dance import spec as dancespec

    sp = dancespec.load_spec(cfg.dance_spec)
    usage, shot_frames, shot_fps = targets.dance_usage(sp)
    key = sp.data["performer"]["key"]
    default = cfg.foreground or {"kind": "key", "key": {"color": key["color"], "tolerance": key["tolerance"], "softness": key["softness"]}}
    unknown = sorted(set(cfg.origins) - {clip["path"] for clip in sp.data["performer"]["clips"]})
    if unknown:
        raise PipelineError(f"origins に spec の performer.clips に無いパスがあります: {unknown}")
    taken, out = set(), []
    for rel in dict.fromkeys(clip["path"] for clip in sp.data["performer"]["clips"]):
        path = sp.resolve(rel)
        if not path.is_file():
            raise PipelineError(f"spec の人物の素材が見つかりません: {rel}")
        shown = usage.get(rel, [])
        if not shown:
            cfg.notes.append(f"{rel}: spec の区間では一度も出ないので見ていません")
            continue
        info, decoded = _video_record(path)
        notes = []
        if decoded["frames"] < 1:
            raise PipelineError(f"spec の人物の素材のコマを 1 つも復号できません（{rel}）: {decoded['error']}")
        last_frame = decoded["frames"] - 1
        numbers = [min(number, last_frame) for _, _, number, _ in shown]
        out.append({"id": _safe_id(Path(rel).stem, taken), "path": path, "rel": rel, "sha256": sha256(path), "info": info, "decode": decoded,
                    "fps": info["fps"], "frames_total": decoded["frames"], "inspected": [min(numbers), max(numbers) + 1],
                    "foreground": default, "origin": cfg.origins.get(rel), "notes": notes, "spec": cfg.raw["dance_spec"],
                    "usage": shown, "shot_fps": shot_fps, "shot_frames": shot_frames})
    return out


def _measure(clip, cfg, palettes, skipped, say):
    """1 クリップの特徴を測り、信号を集める。→ (Features, 信号)。"""
    info, (first, last) = clip["info"], clip["inspected"]
    size = video.even_size(info["width"], info["height"], cfg.analysis_width)
    use_palettes = palettes if "costume" in cfg.checks else []
    began = time.monotonic()
    found = features.extract(clip["path"], info, size, clip["foreground"], first, last, use_palettes, cfg.thresholds)
    if found is None:
        raise PipelineError(f"{clip['id']}: コマを読めませんでした（{clip['rel']}）")
    say(f"[inspect] {clip['id']}: {found.count} コマを測りました（{time.monotonic() - began:.1f} 秒）")
    clip["analysis_size"] = list(size)
    clip["present_frames"] = int(found.present.sum()) if found.has_foreground else None
    fps = clip["fps"] or 1.0
    signals = []
    no_foreground = "前景の切り抜きがありません（foreground に key か box を書いてください）"
    for category in CATEGORIES:
        if category not in cfg.checks:
            skipped.append({"clip": clip["id"], "check": category, "reason": "設定の checks で外しています"})
            continue
        if found.count < 2:
            skipped.append({"clip": clip["id"], "check": category, "reason": "1 コマだけなので、時間の変化は見ていません"})
            continue
        if category in detect.NEEDS_FOREGROUND and not found.has_foreground:
            skipped.append({"clip": clip["id"], "check": category, "reason": no_foreground})
            continue
        if found.count < 5:
            skipped.append({"clip": clip["id"], "check": category, "reason": f"{found.count} コマしかないので、急変は見ていません（5 コマ以上が要ります）"})
            continue
        if category == "motion" and not found.has_foreground:
            skipped.append({"clip": clip["id"], "check": "motion.position", "reason": "位置の飛びと揺れは見ていません（" + no_foreground + "）。動きの急変だけ見ました"})
        signals += detect.DETECTORS[category](found, cfg.thresholds, fps)
    if palettes and "costume" in cfg.checks:
        if found.has_foreground:
            signals += reference.signals(found, palettes, cfg.thresholds, fps)
            clip["reference"] = reference.summary(found, palettes)
        else:
            skipped.append({"clip": clip["id"], "check": "reference", "reason": "設定画と比べていません（" + no_foreground + "）"})
    return found, signals


def _finish_event(event, clip, cfg, number=None):
    """番号（number があれば）・秒・直す場所・手当ての候補を付ける。VLM で区間が変わった出来事は number なしで呼び直す。"""
    if number is not None:
        event["id"] = f"{clip['id']}-F{number:02d}"
    if clip["fps"]:
        event["t"] = [round(event["frames"][0] / clip["fps"], 4), round(event["frames"][1] / clip["fps"], 4)]
    uses = None
    if clip.get("spec"):
        uses = targets.dance_uses(clip["usage"], event["frames"], clip["frames_total"] - 1, clip["shot_fps"])
    event["target"] = targets.target(clip, event["frames"], clip["fps"], uses)
    event["actions"] = targets.actions(event, clip, cfg.thresholds)
    return event


def _clip_report(clip, events, unused):
    counts = {}
    for event in events:
        counts[event["category"]] = counts.get(event["category"], 0) + 1
    keys = ("id", "rel", "sha256", "fps", "frames_total", "inspected", "decode", "analysis_size", "present_frames", "reference", "origin", "notes")
    out = {key: clip.get(key) for key in keys}
    if clip.get("cues"):
        out["cues"] = cues.describe(clip["cues"])
    out["path"] = out.pop("rel")
    out.update(kind="image" if video.is_image(clip["path"]) else "video", size=[clip["info"]["width"], clip["info"]["height"]],
               foreground=(clip["foreground"] or {"kind": "none"})["kind"], events=len(events), unused_events=len(unused),
               by_category=counts, metrics=f"metrics/{clip['id']}.csv")
    if clip.get("spec"):
        out["spec"] = clip["spec"]
    return out


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def runs(out):
    """出力先の実行の記録（inspect-runs/0.1。古い順）。無ければ空。"""
    path = Path(out) / RUNS
    if not path.is_file():
        return []
    try:
        data = read_json(path)
    except (ValueError, UnicodeDecodeError):
        return []
    return list(data.get("runs", [])) if isinstance(data, dict) else []


def _record_run(out, entry):
    """実行の記録を足すか書き換える（run_id が同じものを置き換える）。新しい RUNS_KEPT 件だけ残す。"""
    entries = [e for e in runs(out) if e.get("run_id") != entry["run_id"]] + [entry]
    atomic_json(Path(out) / RUNS, {"schema_version": RUNS_SCHEMA, "runs": entries[-RUNS_KEPT:]})


def _replace(source, target, tries=8):
    """os.replace を、Windows の一時的な共有違反（書いたばかりのファイルをウイルス対策などが一瞬開いている）のときだけ、
    少し待ってやり直す。"""
    for attempt in range(tries):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == tries - 1:
                raise
            time.sleep(0.05 * 2 ** attempt)


def publish(out, staging, run_id):
    """作業用フォルダーの結果を出力先へ入れ替える。前の結果はいったん BACKUP へ動かし、全部そろってから消す。
    報告の JSON を先に外して最後に置くので、報告が見える間は、その報告の静止画などがそろっている。
    途中で失敗したとき（ファイルが開かれていて動かせない・Ctrl+C など）は、その場で前の結果へ戻してから止まる。
    戻せなかったとき（強制終了など）のために、前からあったものの一覧を JOURNAL に書いておく（次の実行の初めに recover が戻す）。"""
    out, staging = Path(out), Path(staging)
    backup = out / f"{BACKUP}{run_id}"
    old = [name for name in OUTPUTS if (out / name).exists()]
    try:
        atomic_json(out / JOURNAL, {"run_id": run_id, "staging": staging.name, "backup": backup.name, "old": old})
        backup.mkdir()
        for name in reversed(OUTPUTS):               # 報告の JSON から外す
            if (out / name).exists():
                _replace(out / name, backup / name)
        for name in OUTPUTS:                         # 報告の JSON を最後に置く
            if (staging / name).exists():
                _replace(staging / name, out / name)
    except BaseException as error:                   # Ctrl+C も含む。前の結果へ戻してから、元の失敗のまま止まる
        try:
            _rollback(out)
        except Exception as again:                   # 戻せなければ、控えと前の結果を残す（次の実行の初めに recover が戻す）
            error.add_note(f"前の結果へ戻せませんでした（{type(again).__name__}: {again}）。{backup.name} に残したので、"
                           "次の実行の初めに戻します")
        raise
    (out / JOURNAL).unlink()
    shutil.rmtree(backup, ignore_errors=True)
    shutil.rmtree(staging, ignore_errors=True)


def _keep_failure_logs(out, staging, run_id):
    """失敗した実行の VLM のログと返答を failed/<run_id>/ に残す（ほかの途中の結果は消す）。→ {元のパス: 残したパス}。"""
    kept = {}
    for name in ("adapter.log", "response.json"):
        source = Path(staging) / "vlm" / name
        if source.is_file():
            target = Path(out) / FAILED / run_id / name
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace(source, target)
            kept[str(source)] = str(target)
    folders = sorted((p for p in (Path(out) / FAILED).glob("*") if p.is_dir()), key=lambda p: p.name) if (Path(out) / FAILED).is_dir() else []
    for old in folders[:-FAILED_KEPT]:
        shutil.rmtree(old, ignore_errors=True)
    return kept


def _rollback(out):
    """入れ替えの途中で止まった跡（JOURNAL）から、前の結果を戻し、途中まで入れた新しい結果を消す。→ 止まった実行の run_id
    （跡が無ければ None）。途中で失敗しても JOURNAL は残るので、もう一度呼べば続きから戻せる。"""
    out = Path(out)
    journal = out / JOURNAL
    if not journal.is_file():
        return None
    try:
        data = read_json(journal)
    except (ValueError, UnicodeDecodeError):
        data = {}
    backup = out / str(data.get("backup", ""))
    old = set(data.get("old", []))
    if data.get("backup") and backup.is_dir():
        for name in OUTPUTS:
            target = out / name
            if (backup / name).exists():                           # まだ戻していない前の結果
                if target.is_dir():
                    shutil.rmtree(target)
                elif target.exists():
                    target.unlink()
                _replace(backup / name, target)
            elif name not in old and target.exists():              # 前には無く、途中まで入れた新しい結果
                shutil.rmtree(target) if target.is_dir() else target.unlink()
        shutil.rmtree(backup, ignore_errors=True)
    journal.unlink()
    return data.get("run_id", "?")


def recover(out):
    """前の実行が途中で止まった跡を片付ける。→ 報告の注意に足す文の列。出力先のロック（project_lock）を持って呼ぶ
    （ロックがあれば、ほかに動いている実行は無いので、残った作業用フォルダーと running の記録は止まった実行の跡）。
    - 入れ替えの途中で止まった（JOURNAL がある）: 前の結果を戻し、途中まで入れた新しい結果を消す。
    - 作業用フォルダーが残っている（測る途中で止まった）: 消す（前の結果はそのまま）。
    - 記録が running のまま: failed（interrupted）にする。"""
    out = Path(out)
    notes = []
    stopped_run = _rollback(out)
    if stopped_run is not None:
        notes.append(f"前の実行（{stopped_run}）が結果の入れ替えの途中で止まっていたので、その前の結果に戻しました")
    for leftover in sorted(out.glob(f"{STAGING}*")) + sorted(out.glob(f"{BACKUP}*")):
        shutil.rmtree(leftover, ignore_errors=True)
    entries = runs(out)
    stopped = [e for e in entries if e.get("status") == "running"]
    for entry in stopped:
        _record_run(out, {**entry, "status": "failed", "interrupted": True,
                          "error": "途中で止まりました（強制終了など）。前の成功した結果はそのまま残しています"})
        notes.append(f"前の実行（{entry.get('run_id')}・{entry.get('started_at')} 開始）は途中で止まっていました（failed として記録）")
    return notes


def _inputs(cfg, result, adapter, vlm_response):
    """この実行の入力と sha256 の一覧（設定・動画・設定画・dance の spec・演出の時刻のファイル・VLM の設定と返答）。"""
    items = [{"role": "config", "path": str(cfg.path), "sha256": cfg.sha256}]
    if cfg.dance_spec:
        items.append({"role": "dance_spec", "path": str(cfg.dance_spec), "sha256": sha256(cfg.dance_spec)})
    items += [{"role": "clip", "id": clip["id"], "path": clip["path"], "sha256": clip["sha256"]} for clip in result["clips"]]
    items += [{"role": "reference", "path": entry["rel"], "sha256": sha256(entry["path"])} for entry in cfg.references]
    for clip in result["clips"]:                     # 演出の時刻のファイル（cues.track・cues.light_keys。全体の cues はクリップごとに同じ）
        for source in (clip.get("cues") or {}).get("sources", []):
            files = zip(source["paths"], source["sha256"]) if source["kind"] == "track" else [(source["path"], source["sha256"])]
            for path, digest in files:
                item = {"role": f"cues_{source['kind']}", "path": path, "sha256": digest}
                if item not in items:
                    items.append(item)
    if adapter:
        items.append({"role": "vlm_adapter", "path": adapter["path"], "sha256": adapter["sha256"]})
    if vlm_response:
        items.append({"role": "vlm_response", "path": str(Path(vlm_response).resolve()), "sha256": result["vlm"].get("response_sha256")})
    if result["vlm"].get("request_sha256"):
        items.append({"role": "vlm_request", "path": "vlm/request.json", "sha256": result["vlm"]["request_sha256"]})
    return items


def run(config_path, out=None, vlm_config=None, allow_send=False, vlm_packet=False, vlm_response=None, progress=None,
        vlm_legacy=False):
    say = progress or (lambda message: None)
    began = time.monotonic()
    cfg = config.load(config_path)
    if vlm_config and vlm_response:
        raise PipelineError("--vlm と --vlm-response は同時に使えません（返答を取り込むときは --vlm を外してください）")
    if vlm_config and vlm_packet:
        raise PipelineError("--vlm-packet は依頼の束を書くだけの指定です。--vlm と同時には使えません（送らないなら --vlm を外してください）")
    adapter = vlm.load_adapter(vlm_config) if vlm_config else None
    if adapter and adapter["sends_data"] and not allow_send:     # 重い処理の前に止める
        raise PipelineError(f"VLM の設定 {Path(adapter['path']).name} は素材（コマと設定画）を外部へ送ります（sends_data: true）。"
                            "送ってよいときだけ --allow-send を付けてください。送らずに確かめるなら --vlm-packet で依頼の束だけを書けます")
    response_bytes = None
    if vlm_response:                                             # 出力先を片付ける前に読む（前回の vlm/response.json を渡されても消さない）
        source = Path(vlm_response).resolve()
        if not source.is_file():
            raise PipelineError(f"VLM の返答が見つかりません: {vlm_response}")
        response_bytes = source.read_bytes()
    out = Path(out).resolve() if out else cfg.base / "inspect_report"
    out.mkdir(parents=True, exist_ok=True)
    stale = [out / name for name in FOLDERS if (out / name).exists()]
    if stale and not (out / MARKER).is_file():
        raise PipelineError(f"出力先 {out} に、inspect の出力ではない {', '.join(p.name for p in stale)} があります。"
                            "上書きしないので、別の --out を指定してください")
    with ExitStack() as stack:
        try:                                                     # 同じ出力先では 1 つの実行だけ（fx・dance と同じ出力先のロック。強制終了でも外れる）。
            stack.enter_context(project_lock(out))              # 動いている実行の作業用フォルダーを、止まった実行の跡と取り違えて消さない
        except PipelineError as error:
            raise PipelineError(f"出力先 {out} は、ほかの mv inspect の実行が使っています（{error}）。終わるのを待つか、"
                                "別の --out を指定してください") from error
        (out / MARKER).write_text("mv inspect の出力先。stills/・anchors/・metrics/・references/・vlm/ と報告は、実行が最後まで済んだときだけ"
                                  "入れ替える\n", encoding="utf-8")
        cfg.notes.extend(recover(out))                           # 前の実行が途中で止まっていれば、前の結果に戻し、失敗として記録する
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        staging = out / f"{STAGING}{run_id}"
        staging.mkdir()
        record = {"run_id": run_id, "status": "running", "started_at": _now(), "pid": os.getpid(), "config": str(cfg.path),
                  "config_sha256": cfg.sha256}
        _record_run(out, record)
        try:
            result, plan = _inspect(cfg, staging, adapter, allow_send, vlm_packet, vlm_response, response_bytes, vlm_legacy, say)
            inputs = _inputs(cfg, result, adapter, vlm_response)
            result["run"] = {"id": run_id, "started_at": record["started_at"], "finished_at": _now(), "inputs": inputs}
            result["seconds"] = round(time.monotonic() - began, 1)
            atomic_json(staging / "fix_plan.json", plan)
            atomic_json(staging / "inspect_report.json", result)
            (staging / "inspect_report.md").write_text(report.markdown(result, plan), encoding="utf-8")
            publish(out, staging, run_id)
        except BaseException as error:                           # Ctrl+C も含む。前の結果はそのまま
            kept = _keep_failure_logs(out, staging, run_id)
            shutil.rmtree(staging, ignore_errors=True)
            message = str(error)
            for old_path, new_path in kept.items():              # エラーの文のログの場所を、残した場所に直す
                message = message.replace(old_path, new_path)
            detail = " ".join([f"{type(error).__name__}: {message}", *getattr(error, "__notes__", [])])   # 注は前の結果へ戻せなかったときなど
            _record_run(out, {**record, "status": "failed", "finished_at": _now(), "error": detail[:600], "kept": sorted(kept.values())})
            if isinstance(error, PipelineError) and message != str(error):
                raise PipelineError(message) from error
            raise
        _record_run(out, {**record, "status": "succeeded", "finished_at": result["run"]["finished_at"], "overall": result["overall"],
                          "events": result["summary"]["events"], "inputs": inputs})
    return result, out


def _inspect(cfg, out, adapter, allow_send, vlm_packet, vlm_response, response_bytes, vlm_legacy, say):
    """測って、出来事・修正計画・静止画などを out（作業用フォルダー）に書く。→ (報告, 修正計画)。報告と計画のファイルは呼んだ側が書く。"""
    palettes = reference.prepare_all(cfg.references)
    clips = _clips_from_dance(cfg) if cfg.dance_spec else _clips_from_config(cfg)
    keys = [(clip["id"], (clip["foreground"] or {}).get("key")) for clip in clips if (clip["foreground"] or {}).get("kind") == "key"]
    keys += [(entry["rel"], entry["key"]) for entry in cfg.references if entry.get("key")]
    for where, key in keys:                           # 緑抜きで肌や髪まで抜ける設定は、検出の前に知らせる
        warning = features.key_warning(key, where)
        if warning and warning not in cfg.notes:
            cfg.notes.append(warning)
    skipped, events, unused = [], [], []
    for clip in clips:
        found, signals = _measure(clip, cfg, palettes, skipped, say)
        report.write_metrics(out / "metrics" / f"{clip['id']}.csv", found, clip["fps"], palettes, cfg.thresholds)
        number = 0
        for event in detect.events(signals, clip["id"], clip["fps"]):
            number += 1
            _finish_event(event, clip, cfg, number)
            (unused if clip.get("spec") and not event["target"]["uses"] else events).append(event)
        clip["next_number"] = number
    vlm_state = {"status": "not_run"}
    if adapter or vlm_packet or vlm_response:
        selection = vlm.select(clips, events, cfg.vlm)
        wanted = {}
        for item in selection:
            wanted.setdefault(item["clip"], set()).add(item["frame"])
        grabbed = {clip["id"]: video.grab(clip["path"], wanted.get(clip["id"], ()), clip["info"], total=clip["frames_total"])
                   for clip in clips if clip["id"] in wanted}
        request_path, request = vlm.write_packet(out, selection, grabbed, cfg.references, cfg.vlm)
        request_sha = sha256(request_path)
        vlm_state = {"status": "packet", "request": "vlm/request.json", "request_sha256": request_sha,
                     "frames": vlm.describe_selection(selection, events, clips)}
        response_path = None
        if adapter:
            response_path = out / "vlm" / "response.json"
            say(f"[inspect] VLM のプログラムを呼びます: {adapter['tool']} {adapter['version']}（外部へ送る: {adapter['sends_data']}）")
            vlm.run_adapter(adapter, request_path, response_path, out / "vlm" / "adapter.log", allow_send)
            vlm_state.update(status="run", tool=f"{adapter['tool']} {adapter['version']}", sends_data=adapter["sends_data"],
                             adapter={"path": adapter["path"], "sha256": adapter["sha256"]})
        elif vlm_response:
            response_path = out / "vlm" / "response.json"
            response_path.write_bytes(response_bytes)
            vlm_state.update(status="imported", source=str(Path(vlm_response).resolve()))
        if response_path:
            response, warnings = vlm.read_response(response_path, request, request_sha, legacy=vlm_legacy)
            vlm_state["response_sha256"] = sha256(response_path)
            if vlm_legacy and "request_sha256" not in response:
                vlm_state["legacy_response"] = True              # 互換の扱いで、どの依頼への返答かを確かめずに取り込んだ
            tool = response["tool"]
            vlm_state.update(findings=len(response["findings"]), warnings=warnings, response="vlm/response.json",
                             tool=vlm_state.get("tool") or " ".join(filter(None, [tool.get("name"), tool.get("version")])),
                             model=tool.get("model"))
            usage = response.get("x_usage")          # アダプタが書いたトークン数（任意。料金はモデルの料金表で計算する）
            if isinstance(usage, dict) and usage and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in usage.values()):
                vlm_state["usage"] = usage
            by_id = {clip["id"]: clip for clip in clips}
            for event in vlm.attach(events, response, request, clips):
                clip = by_id[event["clip"]]
                clip["next_number"] += 1
                _finish_event(event, clip, cfg, clip["next_number"])
                (unused if clip.get("spec") and not event["target"]["uses"] else events).append(event)
    if vlm_state.get("findings"):        # VLM の指摘で区間や分類が変わった出来事の秒・直す場所・手当ての候補を作り直す
        by_id = {clip["id"]: clip for clip in clips}
        for event in events:
            _finish_event(event, by_id[event["clip"]], cfg)
    events.sort(key=lambda e: ([c["id"] for c in clips].index(e["clip"]), e["frames"][0], e["frames"][1]))
    by_id = {clip["id"]: clip for clip in clips}
    cue_counts = None
    for event in events + unused:                   # 演出の時刻と重なるかを書き添える（出来事は消さない）
        clip = by_id[event["clip"]]
        if clip.get("cues"):
            event["cues"] = cues.match(event, clip["cues"], clip["fps"])
    if any(clip.get("cues") for clip in clips):
        cued = [e for e in events if by_id[e["clip"]].get("cues")]
        cue_counts = {"events_with_cue": sum(1 for e in cued if e["cues"]), "events_without_cue": sum(1 for e in cued if not e["cues"]),
                      "events_unchecked": len(events) - len(cued)}
    plan = targets.plan(clips, events, cfg.thresholds)
    wanted = report.still_frames(events)            # 静止画は STILL_WIDTH の幅で足りる（元の大きさのコマを出来事の数だけ抱えない）
    small = {clip["id"]: video.grab(clip["path"], wanted.get(clip["id"], ()), clip["info"], total=clip["frames_total"],
                                    size=video.even_size(clip["info"]["width"], clip["info"]["height"], report.STILL_WIDTH))
             for clip in clips if clip["id"] in wanted}
    wanted = report.anchor_frames(plan["segments"])  # 修正計画の前後のコマは、生成し直すときの画なので元の大きさ
    grabbed = {clip["id"]: video.grab(clip["path"], wanted.get(clip["id"], ()), clip["info"], total=clip["frames_total"])
               for clip in clips if clip["id"] in wanted}
    report.write_stills(out, events, small, palettes)
    cutouts = report.write_references(out, palettes)
    report.write_anchors(out, plan, grabbed)
    problems = [clip for clip in clips if not clip["decode"]["ok"]]
    overall = "review" if events or problems else "clean_with_skips" if skipped else "clean"
    by_category = {}
    for event in events:
        by_category[event["category"]] = by_category.get(event["category"], 0) + 1
    result = {
        "schema_version": REPORT_SCHEMA, "overall": overall, "config": str(cfg.path), "config_sha256": cfg.sha256,
        "engine": {"name": "inspect", "version": VERSION, "fingerprint": fingerprint(), "libraries": _libraries()},
        "thresholds": cfg.thresholds, "checks": list(cfg.checks),
        "references": [reference.describe(p, cutout) for p, cutout in zip(palettes, cutouts)],
        "clips": [_clip_report(c, [e for e in events if e["clip"] == c["id"]], [e for e in unused if e["clip"] == c["id"]]) for c in clips],
        "summary": {"events": len(events), "by_category": by_category, "unused": len(unused), **({"cues": cue_counts} if cue_counts else {})},
        "events": events, "unused": unused, "skipped": skipped, "notes": cfg.notes, "vlm": vlm_state, "fix_plan": "fix_plan.json",
    }
    return result, plan


def _libraries():
    from ..fx import render as fxrender
    return fxrender.library_versions()


def summarize(result, out):
    """CLI の stdout に出す短い要約。"""
    return {"overall": result["overall"], "out": str(out), "run_id": result["run"]["id"], "events": result["summary"]["events"],
            "by_category": result["summary"]["by_category"],
            "unused": result["summary"]["unused"], "skipped": [{k: s[k] for k in ("clip", "check")} for s in result["skipped"]],
            "clips": [{"id": c["id"], "events": c["events"], "inspected": c["inspected"]} for c in result["clips"]],
            "vlm": result["vlm"]["status"], "fix_plan": str(Path(out) / "fix_plan.json"), "report": str(Path(out) / "inspect_report.md")}
