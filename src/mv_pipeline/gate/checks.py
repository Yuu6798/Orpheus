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
     "beats": "analysis.json",                          "downbeats"・"beats"・"beats_s" のどれかの配列
     "thresholds": {…}}                                 下の THRESHOLDS を上書き

結果は status: pass / fail / warn / skip。**skip は「材料が無くて見ていない」で、合格ではない**。
全体は fail が 1 つでもあれば fail、skip があれば pass_with_skips（一覧に何を見ていないかが残る）、
warn があれば pass_with_warnings、なければ pass。
しきい値は最初の 1 作で確かめて直す前提の仮の値。好みの判断はしない。終了コードは fail のとき 1。
"""
import json
import math
import re
import subprocess
from pathlib import Path

from ..io import PipelineError, sha256
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
EPS = 1e-6   # 秒の引き算の丸め誤差。0.8 - 0.6 = 0.20000000000000007 のような境目を不合格にしない
UNMEASURED = ("not_found", "estimate")   # 推定のままの行。実測として照合しない（method が無い行は実測として扱う）


def result(name, status, summary, problems=()):
    return {"check": name, "status": status, "summary": summary, "problems": list(problems)}


def skip(name, needs):
    return result(name, "skip", f"見ていません（{needs} がありません）")


def _window(timeline):
    """描いた区間 (開始, 終わり)。interval が無い timeline はショット全体。"""
    interval = timeline.get("interval")
    if not interval:
        return 0.0, timeline["duration_s"]
    return interval["start"], interval["start"] + interval["duration"]


def _scoped(timeline):
    """描いた区間に出るものだけを残した timeline。区間をまたぐ字の時間は区間に切る。"""
    first, last = _window(timeline)
    lyrics = [{**ln, "start": max(ln["start"], first), "end": min(ln["end"], last),
               "stable": [max(_stable(ln)[0], first), min(_stable(ln)[1], last)]}
              for ln in timeline["lyrics"] if ln["start"] < last and ln["end"] > first]
    inside = lambda times: [t for t in times if first <= t < last]  # noqa: E731
    return {**timeline, "lyrics": lyrics, "seams": inside(timeline.get("seams", [])), "flashes": inside(timeline.get("flashes", []))}


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


def _stable(line):
    """字が動いていない時間 [開始, 終了)。「字が動く途中の一瞬は可」のため、出入りの間は除く。
    区間に切った行は、元の時刻から求めた値を使う（切った時刻から求めると区間の端に出入りがあることになる）。"""
    if "stable" in line:
        return tuple(line["stable"])
    return line["start"] + line.get("fade_in_s", 0), line["end"] - line.get("fade_out_s", 0)


def check_lyric_time(timeline, measured, layer, th):
    name = "歌詞の時刻"
    if measured is None:
        return skip(name, "measured_lines")
    lines = [ln for ln in timeline["lyrics"] if ln["layer"] == layer]
    if len(lines) != len(measured):
        return result(name, "fail", f"行数が合いません（字 {len(lines)} 行 / 実測 {len(measured)} 行）")
    first, last = _window(timeline)     # 実測は曲全体の行と順に対応するので、対応させてから区間で絞る
    shown = [(line, sung) for line, sung in zip(lines, measured) if line["start"] < last and line["end"] > first]
    if not shown:
        return result(name, "pass", "描いた区間に照合する行がありません")
    pairs = [(line, sung) for line, sung in shown if sung.get("method") not in UNMEASURED]
    if not pairs:
        return skip(name, "実測した行（どの行も推定のまま）")
    problems = []
    for line, sung in pairs:
        lead = sung["start"] - line["start"]   # 正 = 字が歌より先に出る
        if not th["lead_min_s"] - EPS <= lead <= th["lead_max_s"] + EPS:
            problems.append({"t": line["start"], "text": line["text"], "detail": f"字が歌の {lead:+.3f} 秒前（許容 {th['lead_min_s']}〜{th['lead_max_s']}）"})
    unverified = len(shown) - len(pairs)
    summary = f"{len(pairs)} 行を照合、外れ {len(problems)} 行"
    if unverified:
        summary += f"（実測できず見ていない行 {unverified}）"
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
        if line.get("overflow"):
            problems.append({"t": line["start"], "text": line["text"], "detail": "字が箱に収まらず、欠けている可能性があります"})
        for region in timeline["ui_regions"]:
            if _intersects(line["box"], region["box"]):
                count = int(round((line["end"] - line["start"]) * fps))
                frames += count
                problems.append({"t": line["start"], "text": line["text"], "detail": f"{region['name']} に {count} コマ入ります"})
    return result(name, "fail" if problems else "pass", f"UI 枠に入るコマ {frames}", problems)


def check_jitter(tracks, th, window=None):
    name = "揺れ"
    if tracks is None:
        return skip(name, "tracks")
    if window:
        tracks = {label: [p for p in points if window[0] <= p[0] < window[1]] for label, points in tracks.items()}
    problems, short = [], []
    for label, points in tracks.items():
        if len(points) < 3:      # 2 階差分が取れない要素は、揺れていないとは言えない
            short.append(label)
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
    measured = len(tracks) - len(short)
    if not measured:
        return skip(name, "3 点以上ある tracks")
    summary = f"{measured} 要素を確認、超過 {len(problems)}"
    if short:
        summary += f"（点が足りず見ていない要素 {len(short)}: {', '.join(short)}）"
    return result(name, "fail" if problems else "warn" if short else "pass", summary, problems)


def check_seams(timeline, poses, th):
    name = "つなぎ目"
    seams = timeline.get("seams", [])
    if not seams:
        return result(name, "pass", "切れ目がありません")
    if poses is None:
        return skip(name, "poses")
    reach = 2.0 / timeline["fps"]
    problems = []
    for seam in seams:
        before = [p for p in poses if seam - reach <= p["t"] < seam]
        after = [p for p in poses if seam <= p["t"] <= seam + reach]
        pairs = list(zip(max(before, key=lambda p: p["t"])["points"], min(after, key=lambda p: p["t"])["points"])) \
            if before and after else []
        if not pairs:      # 同じ骨格どうしや空の点を比べると差が 0 で合格に見えるので、見ていない扱い
            problems.append({"t": seam, "text": "", "detail": "切れ目の前後 2 コマ以内に骨格がなく、姿勢の差を見ていません"})
            continue
        move = sum(math.dist(a, b) for a, b in pairs) / len(pairs)
        if move > th["seam_pose"]:
            problems.append({"t": seam, "text": "", "detail": f"姿勢の差 {move:.3f}。切るコマを変えるか camera.seams を入れてください"})
    return result(name, "warn" if problems else "pass", f"切れ目 {len(seams)}、姿勢の差が大きいもの {len(problems)}", problems)


def check_hook_overlap(timeline, th):
    name = "字の重なり"
    hooks = sorted((ln for ln in timeline["lyrics"] if ln["layer"] == "back"), key=lambda ln: ln["start"])
    problems = []
    for left, right in zip(hooks, hooks[1:]):
        shared = left["end"] - right["start"]
        if shared > th["hook_overlap_s"] + EPS:
            problems.append({"t": right["start"], "text": f"{left['text']} / {right['text']}", "detail": f"{shared:.3f} 秒同時に見えます"})
    return result(name, "fail" if problems else "pass", f"入れ替わり {max(0, len(hooks) - 1)} 回、超過 {len(problems)}", problems)


def check_beats(timeline, analysis, th):
    name = "拍"
    if analysis is None:
        return skip(name, "beats")
    grid = analysis.get("downbeats") or analysis.get("beats") or analysis.get("beats_s")
    if not grid:
        return skip(name, "beats の downbeats / beats / beats_s 配列")
    events = [("フラッシュ", t) for t in timeline.get("flashes", [])] + [("切れ目", t) for t in timeline.get("seams", [])]
    limit = th["beat_frames"] / timeline["fps"]
    problems = []
    for label, t in events:
        off = min(abs(t - g) for g in grid)
        if off > limit + EPS:
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


def _size(path):
    """映像の (幅, 高さ)。ffmpeg の情報の出力から読む。"""
    run = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.search(r"Video:.*?(\d{2,})x(\d{2,})", run.stderr)
    return (int(found[1]), int(found[2])) if found else None


def _ssim(preview, master):
    # scale2ref は 2 本のコマがずれることがあり（環境で変わる）、同じ動画でも SSIM が下がるので使わない
    size = _size(master)
    graph = "[0:v][1:v]ssim" if size == _size(preview) else f"[0:v]scale={size[0]}:{size[1]}:flags=bicubic[a];[a][1:v]ssim"
    run = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(preview), "-i", str(master), "-lavfi", graph, "-f", "null", "-"],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    found = re.findall(r"All:([\d.]+)", run.stderr)
    return float(found[-1]) if found else None


def check_export(timeline, video, master, expected, th):
    name = "書き出し"
    if video is None:
        return skip(name, "video")
    interval = timeline.get("interval") or {"duration": timeline["duration_s"], "frames": int(round(timeline["duration_s"] * timeline["fps"]))}
    want = interval["frames"]
    frames, seconds, audio = _probe(video)
    problems = []
    if frames != want:
        problems.append({"t": 0, "text": "", "detail": f"コマ数 {frames}（期待 {want}）"})
    if seconds is None or abs(seconds - interval["duration"]) > 2.0 / timeline["fps"] + 0.03:
        problems.append({"t": 0, "text": "", "detail": f"尺 {seconds} 秒（期待 {interval['duration']} 秒）"})
    notes = [f"コマ {frames}", "音声あり" if audio else "音声なし"]
    if master is not None:
        digest = sha256(master)
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


def _extract_stills(report, video, out, first=0.0):
    """問題のコマを静止画で書き出す（検査ごとに最大 MAX_STILLS 枚）。first は動画の先頭のショット時刻。"""
    if video is None:
        return
    for item in report["checks"]:
        for index, problem in enumerate(item["problems"][:MAX_STILLS]):
            if not problem.get("t"):
                continue
            target = out / f"{item['check']}_{index:02d}_{problem['t']:.2f}s.jpg"
            run = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, problem['t'] - first):.3f}", "-i", str(video),
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
    scoped = _scoped(timeline)
    checks = [check_lyric_time(timeline, load("measured_lines"), config.get("lyric_layer", "band"), th),
              check_confirmed(timeline, load("confirmed"), th),
              check_faces(scoped, load("faces")),
              check_ui(scoped),
              check_jitter(load("tracks"), th, _window(timeline)),
              check_seams(scoped, load("poses"), th),
              check_hook_overlap(scoped, th),
              check_beats(scoped, load("beats"), th),
              check_export(timeline, video, master, config.get("expected_sha256"), th)]
    statuses = {c["status"] for c in checks}
    overall = ("fail" if "fail" in statuses else "pass_with_skips" if "skip" in statuses
               else "pass_with_warnings" if "warn" in statuses else "pass")
    report = {"overall": overall, "timeline": str(path_of("timeline")), "thresholds": th, "checks": checks}
    out = Path(out) if out else base / "gate_report"
    out.mkdir(parents=True, exist_ok=True)
    _extract_stills(report, video, out, _window(timeline)[0])
    (out / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "gate_report.md").write_text(_markdown(report), encoding="utf-8")
    return report, out
