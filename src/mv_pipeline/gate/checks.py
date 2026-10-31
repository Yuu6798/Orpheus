"""書き出しの前の品質ゲート: 機械で分かる問題だけを検査し、合否と問題のコマを一覧にする。

    mv gate check gate.json [--out DIR]

gate.json のパスは gate.json のあるフォルダ基準（絶対パスも可）。timeline だけが必須で、他は持っている分だけ書く:

    {"timeline": "dance_runs/<run_id>/timeline.json",   mv dance render が出す
     "video": "dance_runs/<run_id>/dance.mp4",          確認用（問題のコマの静止画と書き出し検査）
     "master": "master.mp4",                            元データ（SSIM の相手）
     "expected_sha256": "…",                            元データの SHA-256 の控え
     "measured_lines": "lines_measured.json",           `mv gate lines` の出力。字と歌の時刻の照合に使う
     "lyric_layer": "band",                             実測と照合する歌詞の層
     "confirmed": "confirmed.json",                     確かめ済みの時刻 [{"text": …, "start": …}]
     "faces": "faces.json",                             [{"start", "end", "box": [x0,y0,x1,y1], "role": "main"}]（正規化座標）
     "tracks": "tracks.json",                           {"名前": [[t, x, y], …]}（人物に付く要素の位置。画素）
     "poses": "poses.json",                             [{"t": 秒, "points": [[x, y], …]}]（骨格。正規化座標）
     "beats": "analysis.json",                          "downbeats" か "beats" の配列
     "thresholds": {…}}                                 下の THRESHOLDS を上書き

結果は status: pass / fail / warn / skip。**skip は「材料が無くて見ていない」で、合格ではない**。
全体は fail が 1 つでもあれば fail、skip があれば pass_with_skips（一覧に何を見ていないかが残る）、なければ pass。
しきい値は最初の 1 作で確かめて直す前提の仮の値。好みの判断はしない。終了コードは fail のとき 1。
"""
import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from ..io import PipelineError
from ..media import ffmpeg

THRESHOLDS = {
    "lead_min_s": 0.0,        # 字は歌の 0 秒前より遅れてはいけない
    "lead_max_s": 0.2,        # 字は歌の 0.2 秒前より早く出てはいけない
    "confirmed_eps_s": 0.001,
    "jitter_px": 3.0,         # 2 階差分の RMS（画素）
    "seam_pose": 0.05,        # 切れ目の前後の関節の平均移動量（画面の割合）
    "hook_overlap_s": 0.1,
    "beat_frames": 1.0,
    "ssim": 0.98,
}
MAX_STILLS = 12


def result(name, status, summary, problems=()):
    return {"check": name, "status": status, "summary": summary, "problems": list(problems)}


def skip(name, needs):
    return result(name, "skip", f"見ていません（{needs} がありません）")


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _stable(line):
    """字が動いていない時間 [開始, 終了)。「字が動く途中の一瞬は可」のため、出入りの間は除く。"""
    return line["start"] + line.get("fade_in_s", 0), line["end"] - line.get("fade_out_s", 0)


def check_lyric_time(timeline, measured, layer, th):
    name = "歌詞の時刻"
    if measured is None:
        return skip(name, "measured_lines")
    lines = [ln for ln in timeline["lyrics"] if ln["layer"] == layer]
    if len(lines) != len(measured):
        return result(name, "fail", f"行数が合いません（字 {len(lines)} 行 / 実測 {len(measured)} 行）")
    problems = []
    for line, sung in zip(lines, measured):
        lead = sung["start"] - line["start"]   # 正 = 字が歌より先に出る
        if not th["lead_min_s"] <= lead <= th["lead_max_s"]:
            problems.append({"t": line["start"], "text": line["text"], "detail": f"字が歌の {lead:+.3f} 秒前（許容 {th['lead_min_s']}〜{th['lead_max_s']}）"})
    unverified = sum(1 for sung in measured if sung.get("method") == "not_found")
    summary = f"{len(lines)} 行を照合、外れ {len(problems)} 行"
    if unverified:
        summary += f"（うち実測できなかった行 {unverified}）"
    return result(name, "fail" if problems else "warn" if unverified else "pass", summary, problems)


def check_confirmed(timeline, confirmed, th):
    name = "確認済みの時刻"
    if confirmed is None:
        return skip(name, "confirmed")
    problems = []
    for item in confirmed:
        hits = [ln for ln in timeline["lyrics"] if ln["text"] == item["text"]]
        if not hits:
            problems.append({"t": item["start"], "text": item["text"], "detail": "この歌詞が見つかりません"})
        elif not any(abs(ln["start"] - item["start"]) <= th["confirmed_eps_s"] for ln in hits):
            problems.append({"t": item["start"], "text": item["text"], "detail": f"確認時 {item['start']} 秒 → 今 {hits[0]['start']} 秒"})
    return result(name, "fail" if problems else "pass", f"{len(confirmed)} 件を照合、動いたもの {len(problems)}", problems)


def check_faces(timeline, faces):
    name = "字と顔"
    if faces is None:
        return skip(name, "faces")
    problems = []
    for line in timeline["lyrics"]:
        if line["layer"] == "back":      # 人物の後ろの字は顔に隠れるので重なっても読みに関係しない
            continue
        a, b = _stable(line)
        for face in faces:
            if face.get("role", "main") != "main":
                continue
            start, end = max(a, face["start"]), min(b, face["end"])
            if end > start and _intersects(line["box"], face["box"]):
                problems.append({"t": start, "text": line["text"], "detail": f"主役の顔に {end - start:.2f} 秒重なります"})
    return result(name, "fail" if problems else "pass", f"重なり {len(problems)} 件", problems)


def check_ui(timeline):
    name = "UI 枠"
    fps = timeline["fps"]
    problems, frames = [], 0
    for line in timeline["lyrics"]:
        for region in timeline["ui_regions"]:
            if _intersects(line["box"], region["box"]):
                count = int(round((line["end"] - line["start"]) * fps))
                frames += count
                problems.append({"t": line["start"], "text": line["text"], "detail": f"{region['name']} に {count} コマ入ります"})
    return result(name, "fail" if problems else "pass", f"UI 枠に入るコマ {frames}", problems)


def check_jitter(tracks, th):
    name = "揺れ"
    if tracks is None:
        return skip(name, "tracks")
    problems = []
    for label, points in tracks.items():
        if len(points) < 3:
            continue
        worst, at, total, count = 0.0, 0.0, 0.0, 0
        for p0, p1, p2 in zip(points, points[1:], points[2:]):
            dx, dy = p2[1] - 2 * p1[1] + p0[1], p2[2] - 2 * p1[2] + p0[2]
            value = dx * dx + dy * dy
            total, count = total + value, count + 1
            if value > worst:
                worst, at = value, p1[0]
        rms = math.sqrt(total / count)
        if rms > th["jitter_px"]:
            problems.append({"t": at, "text": label, "detail": f"小刻みな動き RMS {rms:.2f} 画素（しきい値 {th['jitter_px']}）"})
    return result(name, "fail" if problems else "pass", f"{len(tracks)} 要素を確認、超過 {len(problems)}", problems)


def _nearest(poses, t):
    return min(poses, key=lambda p: abs(p["t"] - t))


def check_seams(timeline, poses, th):
    name = "つなぎ目"
    seams = timeline.get("seams", [])
    if not seams:
        return result(name, "pass", "切れ目がありません")
    if poses is None:
        return skip(name, "poses")
    dt = 1.0 / timeline["fps"]
    problems = []
    for seam in seams:
        before, after = _nearest(poses, seam - dt), _nearest(poses, seam)
        pairs = list(zip(before["points"], after["points"]))
        move = sum(math.dist(a, b) for a, b in pairs) / max(1, len(pairs))
        if move > th["seam_pose"]:
            problems.append({"t": seam, "text": "", "detail": f"姿勢の差 {move:.3f}。切るコマを変えるか camera.seams を入れてください"})
    return result(name, "warn" if problems else "pass", f"切れ目 {len(seams)}、姿勢の差が大きいもの {len(problems)}", problems)


def check_hook_overlap(timeline, th):
    name = "字の重なり"
    hooks = sorted((ln for ln in timeline["lyrics"] if ln["layer"] == "back"), key=lambda ln: ln["start"])
    problems = []
    for left, right in zip(hooks, hooks[1:]):
        shared = left["end"] - right["start"]
        if shared > th["hook_overlap_s"]:
            problems.append({"t": right["start"], "text": f"{left['text']} / {right['text']}", "detail": f"{shared:.3f} 秒同時に見えます"})
    return result(name, "fail" if problems else "pass", f"入れ替わり {max(0, len(hooks) - 1)} 回、超過 {len(problems)}", problems)


def check_beats(timeline, analysis, th):
    name = "拍"
    if analysis is None:
        return skip(name, "beats")
    grid = analysis.get("downbeats") or analysis.get("beats")
    if not grid:
        return skip(name, "beats の downbeats / beats 配列")
    events = [("フラッシュ", t) for t in timeline.get("flashes", [])] + [("切れ目", t) for t in timeline.get("seams", [])]
    limit = th["beat_frames"] / timeline["fps"]
    problems = []
    for label, t in events:
        off = min(abs(t - g) for g in grid)
        if off > limit:
            problems.append({"t": t, "text": label, "detail": f"拍から {off:.3f} 秒ずれています（許容 {limit:.3f} 秒）"})
    kind = "小節頭" if analysis.get("downbeats") else "拍"
    return result(name, "fail" if problems else "pass", f"{len(events)} 件を{kind}と照合、ずれ {len(problems)}", problems)


def _probe(path):
    """(フレーム数, 映像の尺, 音声があるか)。ffmpeg だけで数える。"""
    run = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    frames = re.findall(r"frame=\s*(\d+)", run.stderr)
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", run.stderr)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else None
    return int(frames[-1]) if frames else 0, seconds, "Audio:" in run.stderr


def _ssim(preview, master):
    run = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(preview), "-i", str(master), "-lavfi",
                          "[0:v][1:v]scale2ref=flags=bicubic[a][b];[a][b]ssim", "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"All:([\d.]+)", run.stderr)
    return float(found[-1]) if found else None


def check_export(timeline, video, master, expected, th):
    name = "書き出し"
    if video is None:
        return skip(name, "video")
    want = int(round(timeline["duration_s"] * timeline["fps"]))
    frames, seconds, audio = _probe(video)
    problems = []
    if frames != want:
        problems.append({"t": 0, "text": "", "detail": f"コマ数 {frames}（期待 {want}）"})
    if seconds is None or abs(seconds - timeline["duration_s"]) > 2.0 / timeline["fps"] + 0.03:
        problems.append({"t": 0, "text": "", "detail": f"尺 {seconds} 秒（期待 {timeline['duration_s']} 秒）"})
    notes = [f"コマ {frames}", "音声あり" if audio else "音声なし"]
    if master is not None:
        digest = hashlib.sha256(Path(master).read_bytes()).hexdigest()
        notes.append(f"元データ SHA-256 {digest[:12]}…")
        if expected and digest.lower() != expected.lower():
            problems.append({"t": 0, "text": "", "detail": "元データの SHA-256 が控えと違います"})
        score = _ssim(video, master)
        notes.append(f"SSIM {score}")
        if score is None or score < th["ssim"]:
            problems.append({"t": 0, "text": "", "detail": f"SSIM {score}（しきい値 {th['ssim']}）"})
    else:
        notes.append("元データとの照合なし")
    status = "fail" if problems else ("pass" if master is not None else "warn")
    return result(name, status, "、".join(notes), problems)


def _extract_stills(report, video, out):
    """問題のコマを静止画で書き出す（検査ごとに最大 MAX_STILLS 枚）。"""
    if video is None:
        return
    for item in report["checks"]:
        for index, problem in enumerate(item["problems"][:MAX_STILLS]):
            if not problem.get("t"):
                continue
            target = out / f"{item['check']}_{index:02d}_{problem['t']:.2f}s.jpg"
            run = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-ss", f"{problem['t']:.3f}", "-i", str(video),
                                  "-frames:v", "1", "-q:v", "3", str(target)], capture_output=True)
            if run.returncode == 0 and target.is_file():
                problem["still"] = target.name


def _markdown(report):
    mark = {"pass": "合格", "fail": "不合格", "warn": "要確認", "skip": "未実施"}
    lines = [f"# 品質ゲート: {report['overall']}", "", f"timeline: `{report['timeline']}`", "",
             "| 検査 | 結果 | 内容 |", "| --- | --- | --- |"]
    lines += [f"| {c['check']} | {mark[c['status']]} | {c['summary']} |" for c in report["checks"]]
    for check in report["checks"]:
        if check["problems"]:
            lines += ["", f"## {check['check']}", ""]
            lines += [f"- {p['t']:.3f} 秒 {p['text']}: {p['detail']}" + (f"（{p['still']}）" if p.get("still") else "") for p in check["problems"]]
    return "\n".join(lines) + "\n"


def run(config_path, out=None):
    config_path = Path(config_path).resolve()
    base = config_path.parent
    config = json.loads(config_path.read_text(encoding="utf-8-sig"))
    path_of = lambda key: (base / config[key]).resolve() if config.get(key) else None  # noqa: E731

    def load(key):
        path = path_of(key)
        return None if path is None else json.loads(path.read_text(encoding="utf-8-sig"))

    timeline = load("timeline")
    if timeline is None:
        raise PipelineError("gate の設定に timeline がありません（mv dance render の run にある timeline.json を指定してください）")
    th = {**THRESHOLDS, **config.get("thresholds", {})}
    video, master = path_of("video"), path_of("master")
    checks = [check_lyric_time(timeline, load("measured_lines"), config.get("lyric_layer", "band"), th),
              check_confirmed(timeline, load("confirmed"), th),
              check_faces(timeline, load("faces")),
              check_ui(timeline),
              check_jitter(load("tracks"), th),
              check_seams(timeline, load("poses"), th),
              check_hook_overlap(timeline, th),
              check_beats(timeline, load("beats"), th),
              check_export(timeline, video, master, config.get("expected_sha256"), th)]
    statuses = {c["status"] for c in checks}
    overall = "fail" if "fail" in statuses else "pass_with_skips" if "skip" in statuses else "pass"
    report = {"overall": overall, "timeline": str(path_of("timeline")), "thresholds": th, "checks": checks}
    out = Path(out) if out else base / "gate_report"
    out.mkdir(parents=True, exist_ok=True)
    _extract_stills(report, video, out)
    (out / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "gate_report.md").write_text(_markdown(report), encoding="utf-8")
    return report, out
