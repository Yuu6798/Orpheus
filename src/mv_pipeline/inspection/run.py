"""`mv inspect check`: 設定を読み、クリップごとに特徴を測って信号と出来事にし、直す場所・修正計画・報告を書く。

    mv inspect check inspect.json [--out DIR] [--vlm VLM.json [--allow-send]] [--vlm-packet] [--vlm-response FILE]

出力先は既定で設定の隣の inspect_report/:
    inspect_report.json / inspect_report.md / fix_plan.json / stills/<出来事>.jpg / anchors/<クリップ>_f<コマ>.png / metrics/<クリップ>.csv
    references/NN.png（設定画ごとの切り抜きの確かめ。設定画があるときだけ）
    vlm/（request.json・frames/・reference/・response.json・adapter.log。VLM を使ったときだけ）

overall は review（要確認の出来事か復号のエラーがある）/ clean_with_skips（見つけた所は無いが、見ていない検査がある）/ clean。
どれも合否ではない（終了コードは設定や入力の誤りのときだけ 1）。
"""
import re
import shutil
import time
from pathlib import Path

from ..fx import probe
from ..io import PipelineError, atomic_json, sha256
from . import CATEGORIES, REPORT_SCHEMA, VERSION, config, detect, features, fingerprint, reference, report, targets, video, vlm


MARKER = "inspect_output.txt"     # この印のある出力先だけ、前の実行の stills/ などを消して作り直す


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
                    "foreground": item["foreground"] or cfg.foreground, "origin": item["origin"], "notes": notes})
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
    out["path"] = out.pop("rel")
    out.update(kind="image" if video.is_image(clip["path"]) else "video", size=[clip["info"]["width"], clip["info"]["height"]],
               foreground=(clip["foreground"] or {"kind": "none"})["kind"], events=len(events), unused_events=len(unused),
               by_category=counts, metrics=f"metrics/{clip['id']}.csv")
    if clip.get("spec"):
        out["spec"] = clip["spec"]
    return out


def run(config_path, out=None, vlm_config=None, allow_send=False, vlm_packet=False, vlm_response=None, progress=None):
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
    stale = [out / name for name in ("stills", "anchors", "metrics", "references", "vlm") if (out / name).exists()]
    if stale and not (out / MARKER).is_file():
        raise PipelineError(f"出力先 {out} に、inspect の出力ではない {', '.join(p.name for p in stale)} があります。"
                            "上書きしないので、別の --out を指定してください")
    (out / MARKER).write_text("mv inspect の出力先。stills/・anchors/・metrics/・references/・vlm/ は実行のたびに作り直す\n", encoding="utf-8")
    for folder in stale:                                         # 前の実行の静止画を今回のものと取り違えない
        shutil.rmtree(folder)
    palettes = reference.prepare_all(cfg.references)
    clips = _clips_from_dance(cfg) if cfg.dance_spec else _clips_from_config(cfg)
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
        vlm_state = {"status": "packet", "request": "vlm/request.json", "request_sha256": request_sha, "frames": vlm.describe_selection(selection)}
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
            response, warnings = vlm.read_response(response_path, request, request_sha)
            tool = response["tool"]
            vlm_state.update(findings=len(response["findings"]), warnings=warnings, response="vlm/response.json",
                             tool=vlm_state.get("tool") or " ".join(filter(None, [tool.get("name"), tool.get("version")])),
                             model=tool.get("model"))
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
    plan = targets.plan(clips, events, cfg.thresholds)
    wanted = report.needed_frames(events, plan["segments"])
    grabbed = {clip["id"]: video.grab(clip["path"], wanted.get(clip["id"], ()), clip["info"], total=clip["frames_total"])
               for clip in clips if clip["id"] in wanted}
    report.write_stills(out, events, grabbed, palettes)
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
        "summary": {"events": len(events), "by_category": by_category, "unused": len(unused)},
        "events": events, "unused": unused, "skipped": skipped, "notes": cfg.notes, "vlm": vlm_state, "fix_plan": "fix_plan.json",
        "seconds": round(time.monotonic() - began, 1),
    }
    atomic_json(out / "fix_plan.json", plan)
    atomic_json(out / "inspect_report.json", result)
    (out / "inspect_report.md").write_text(report.markdown(result, plan), encoding="utf-8")
    return result, out


def _libraries():
    from ..fx import render as fxrender
    return fxrender.library_versions()


def summarize(result, out):
    """CLI の stdout に出す短い要約。"""
    return {"overall": result["overall"], "out": str(out), "events": result["summary"]["events"], "by_category": result["summary"]["by_category"],
            "unused": result["summary"]["unused"], "skipped": [{k: s[k] for k in ("clip", "check")} for s in result["skipped"]],
            "clips": [{"id": c["id"], "events": c["events"], "inspected": c["inspected"]} for c in result["clips"]],
            "vlm": result["vlm"]["status"], "fix_plan": str(Path(out) / "fix_plan.json"), "report": str(Path(out) / "inspect_report.md")}
