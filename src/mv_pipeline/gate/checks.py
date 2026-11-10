"""書き出しの前の品質ゲート: 機械で分かる問題だけを検査し、合否と問題のコマを一覧にする。

    mv gate check gate.json [--out DIR]

gate.json のパスは gate.json のあるフォルダ基準（絶対パスも可）。timeline だけが必須で、他は持っている分だけ書く:

    {"timeline": "dance_runs/<run_id>/timeline.json",   mv dance render が出す
     "video": "dance_runs/<run_id>/dance.mp4",          確認用（問題のコマの静止画と書き出し検査）
     "master": "master.mp4",                            元データ（SSIM の相手）
     "expected_sha256": "…",                            元データの SHA-256 の控え
     "measured_lines": "lines_measured.json",           `mv gate lines` の出力。字と歌の時刻の照合に使う
     "lyric_layer": "band",                             実測と照合する歌詞の層（"lyric_layers": [...] で複数）
     "confirmed": "confirmed.json",                     確かめ済みの時刻 [{"text": …, "start": …}]
     "confirmed_overrides_measured": true,              確認済みの行は実測との差を要確認にする
     "faces": "faces.json",                             [{"start", "end", "box": [x0,y0,x1,y1], "role": "main"}]（正規化座標）。"auto" で推定
     "spec": "spec.json",                               "faces": "auto" のときの dance の spec
     "tracks": "tracks.json",                           {"名前": [[t, x, y], …]}（人物に付く要素の位置。画素）
     "poses": "poses.json",                             [{"t": 秒, "points": [[x, y], …]}]（骨格。正規化座標）
     "beats": "analysis.json",                          "downbeats"・"beats"・"beats_s" のどれかの配列（無ければ timeline の beats）
     "flash": {"delta": 0.10, "max_per_s": 3},          書いたときだけ、master（無ければ video）の明滅を測る検査を足す（gate/flashes.py）
     "thresholds": {…}}                                 下の THRESHOLDS を上書き

結果は status: pass / fail / warn / skip。**skip は「材料が無くて見ていない」で、合格ではない**。
全体は fail が 1 つでもあれば fail、skip があれば pass_with_skips（一覧に何を見ていないかが残る）、
warn があれば pass_with_warnings、なければ pass。
しきい値は最初の 1 作で確かめて直す前提の仮の値。好みの判断はしない。終了コードは fail のとき 1。

timeline は dance-timeline/0.1 と 0.2 の両方を読む。dance/dance_timeline.schema.json で検証し、知らない欄は無視する。

"flash" を書かない設定では、検査は今までの 9 つだけで、結果も合否も出力のファイルも変わらない（明滅の検査は既定で掛けない）。
"""
import bisect
import itertools
import json
import math
import re
import subprocess
from pathlib import Path

import jsonschema

from ..fx.probe import decode
from ..io import PipelineError, sha256
from ..media import ffmpeg

THRESHOLDS = {
    "lead_min_s": 0.0,        # 字は歌の 0 秒前より遅れてはいけない
    "lead_max_s": 0.2,        # 字は歌の 0.2 秒前より早く出てはいけない
    "confirmed_eps_s": 0.001,
    "jitter_px": 3.0,         # 2 階差分の RMS（画素）
    "seam_pose": 0.05,        # 切れ目の前後の関節の平均移動量（画面の割合）
    "hook_overlap_s": 0.1,
    "overlap_mode": "time",   # 字の重なり: time = 見えている時間だけで数える / time_and_box = 箱も交わるときだけ数える
    "beat_frames": 1.0,
    "ssim": 0.98,
    "confirmed_overrides_measured": True,
}
TIMELINE_SCHEMA = Path(__file__).resolve().parents[1] / "dance" / "dance_timeline.schema.json"
HEAD_SHARE = 0.12     # 頭の帯は、体の高さ × 寄りの倍率 のこの割合
FACE_SCALE = 0.25     # 顔の推定に使う描画の縮尺
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
    """描いた区間に出るものだけを残した timeline。区間をまたぐ字の時間（見えている終わり visible_until も）は区間に切る。"""
    first, last = _window(timeline)
    fade = _lyric_fade(timeline)
    # 区間に出るかは、見えている終わり（after ならフェードアウト込み）で決める。区間の初めにフェードアウト中の行も残す
    # measured: false の行（mv gate timeline で見えているコマが無かった行）は画面に出ていないので、見た目の検査からは外す
    lyrics = [{**ln, "start": max(ln["start"], first), "end": min(max(ln["end"], first), last),
               "stable": [max(_stable(ln, fade)[0], first), min(_stable(ln, fade)[1], last)],
               "visible_until": min(_visible_end(ln, fade), last)}
              for ln in timeline["lyrics"] if ln["start"] < last and _visible_end(ln, fade) > first and ln.get("measured") is not False]
    inside = lambda times: [t for t in times if first <= t < last]  # noqa: E731
    within = {key: [item for item in timeline[key] if first <= item["t"] < last] for key in ("seam_info", "flash_info") if key in timeline}
    return {**timeline, "lyrics": lyrics, "seams": inside(timeline.get("seams", [])), "flashes": inside(timeline.get("flashes", [])), **within}


def _intersects(a, b):
    return min(a[2], b[2]) > max(a[0], b[0]) and min(a[3], b[3]) > max(a[1], b[1])


LYRIC_FADES = ("inside", "after")   # fade_out_s が end の前にあるか（dance の描画）、end の後に続くか（手書きの timeline の既定）


def _lyric_fade(timeline):
    fade = timeline.get("lyric_fade", "after")
    if fade not in LYRIC_FADES:
        raise PipelineError(f"timeline の lyric_fade は {' か '.join(LYRIC_FADES)} にしてください: {fade!r}")
    return fade


def _visible_end(line, fade):
    """字が画面から消える時刻。lyric_fade が after なら end + fade_out_s（フェードアウトの間も見えている）、
    inside なら end（dance は end の前でフェードアウトを終える）。区間に切った行は visible_until を持つ。"""
    if "visible_until" in line:
        return line["visible_until"]
    return line["end"] + (line.get("fade_out_s", 0) if fade == "after" else 0)


def _stable(line, fade="inside"):
    """字が動いていない時間 [開始, 終了)。「字が動く途中の一瞬は可」のため、出入りの間は除く。
    フェードアウトが end の前（inside）なら end - fade_out_s まで、end の後（after）なら end まで。
    区間に切った行は、元の時刻から求めた値を使う（切った時刻から求めると区間の端に出入りがあることになる）。"""
    if "stable" in line:
        return tuple(line["stable"])
    return line["start"] + line.get("fade_in_s", 0), line["end"] - (line.get("fade_out_s", 0) if fade == "inside" else 0)


def _is_confirmed(line, confirmed, th):
    return any(item["text"] == line["text"] and abs(item["start"] - line["start"]) <= th["confirmed_eps_s"] for item in confirmed or [])


def check_lyric_time(timeline, measured, layers, th, confirmed=None):
    """layers は層の名前か、その配列。sung が false の行（題字・クレジット）は数えない。"""
    name = "歌詞の時刻"
    if measured is None:
        return skip(name, "measured_lines")
    layers = [layers] if isinstance(layers, str) else layers
    lines = [ln for ln in timeline["lyrics"] if ln["layer"] in layers and ln.get("sung", True)]
    if len(lines) != len(measured):
        return result(name, "fail", f"行数が合いません（字 {len(lines)} 行 / 実測 {len(measured)} 行）")
    first, last = _window(timeline)     # 実測は曲全体の行と順に対応するので、対応させてから区間で絞る
    shown = [(line, sung) for line, sung in zip(lines, measured) if line["start"] < last and line["end"] > first]
    if not shown:
        return result(name, "pass", "描いた区間に照合する行がありません")
    # 字の側が実測でない行（mv gate timeline で見えているコマが無く、宣言の時刻のまま残した行）も照合しない
    pairs = [(line, sung) for line, sung in shown if sung.get("method") not in UNMEASURED and line.get("measured") is not False]
    if not pairs:
        return skip(name, "実測した行（どの行も推定のまま）")
    problems, settled = [], 0
    for line, sung in pairs:
        lead = sung["start"] - line["start"]   # 正 = 字が歌より先に出る
        if not th["lead_min_s"] - EPS <= lead <= th["lead_max_s"] + EPS:
            detail = f"字が歌の {lead:+.3f} 秒前（許容 {th['lead_min_s']}〜{th['lead_max_s']}）"
            if th["confirmed_overrides_measured"] and _is_confirmed(line, confirmed, th):
                settled += 1
                detail = f"要確認（試聴で確定済み）: {detail}"
            problems.append({"t": line["start"], "text": line["text"], "detail": detail})
    unverified = len(shown) - len(pairs)
    unseen = sum(1 for line, _ in shown if line.get("measured") is False)
    summary = f"{len(pairs)} 行を照合、外れ {len(problems)} 行"
    if settled:
        summary += f"（うち確定済み {settled} 行）"
    if unverified:
        summary += f"（実測できず見ていない行 {unverified}" + (f"。うち字が見えなかった行 {unseen}" if unseen else "") + "）"
    return result(name, "fail" if len(problems) > settled else "warn" if problems or unverified else "pass", summary, problems)


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


def _behind_layers(timeline):
    """人物の後ろに描く層の名前。layers の無い timeline は back だけ。"""
    return {layer["name"] for layer in timeline["layers"] if layer["z"] == "behind"} if "layers" in timeline else {"back"}


def _on_frames(seconds, fps):
    """時刻をコマに丸めた秒。"""
    return round(seconds * fps) / fps


def check_faces(timeline, faces):
    name = "字と顔"
    if faces is None:
        return skip(name, "faces")
    fps = timeline["fps"]
    fade = _lyric_fade(timeline)
    behind = _behind_layers(timeline)
    problems, measured = [], 0
    for line in timeline["lyrics"]:
        if line["layer"] in behind:      # 人物の後ろの字は顔に隠れるので重なっても読みに関係しない
            continue
        a, b = _stable(line, fade)
        for face in faces:
            if face.get("role", "main") != "main":
                continue
            start, end = max(a, _on_frames(face["start"], fps)), min(b, _on_frames(face["end"], fps))
            if end > start and _intersects(line["box"], face["box"]):
                measured += not face.get("estimated")
                problems.append({"t": start, "text": line["text"], "detail": f"主役の顔{'（推定）' if face.get('estimated') else ''}に {end - start:.2f} 秒重なります"})
    # 推定の顔との重なりは実測ではないので、不合格にしない（AGENTS.md「推定を実測扱いしない」）
    summary = f"重なり {len(problems)} 件"
    if any(face.get("estimated") for face in faces):
        summary = f"推定の顔を含む {len(faces)} 区間を照合、{summary}（うち実測 {measured} 件）"
    return result(name, "fail" if measured else "warn" if problems else "pass", summary, problems)


def estimate_faces(spec_path, timeline):
    """dance の api から 2 コマごとに人物のアルファを取り、頭の帯から顔の箱を推定する。

    頭の帯は、fit の体の高さ（無ければその時刻のアルファの高さ）× 寄りの倍率の、上端から HEAD_SHARE。"""
    import numpy as np

    from ..dance import api

    first, last = _window(timeline)
    fps = timeline["fps"]
    ctx = api.prepare(spec_path, scale=FACE_SCALE)
    faces = []
    try:
        body = api.body_height(ctx)
        count = int(round((last - first) * fps))
        for index in range(0, count, 2):
            t = first + index / fps
            person = api.performer(ctx, t)
            if person is None:
                continue
            solid = person[1] > 0.5
            rows = np.nonzero(solid.any(axis=1))[0]
            if not len(rows):
                continue
            tall = (rows[-1] + 1 - rows[0]) if body is None else body * api.zoom(ctx, t)
            bottom = min(solid.shape[0], rows[0] + max(1, int(round(HEAD_SHARE * tall))))
            columns = np.nonzero(solid[rows[0]:bottom].any(axis=0))[0]
            height, width = solid.shape
            faces.append({"start": t, "end": t + 2.0 / fps, "role": "main", "estimated": True,
                          "box": [columns[0] / width, rows[0] / height, (columns[-1] + 1) / width, bottom / height]})
    finally:
        api.close(ctx)
    return faces


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


def _camera_note(timeline, seam):
    """切れ目の 1 コマ以内にある camera.seams の設定の文言。"""
    fps = timeline["fps"]
    for item in timeline.get("camera_seams", []):
        if abs(round(item["t"] * fps) - round(seam * fps)) <= 1:
            return f"（camera.seams あり: zoom {item.get('zoom')}・回転 {item.get('rotate_deg')} 度）"
    return ""


def check_seams(timeline, poses, th):
    name = "つなぎ目"
    seams = timeline.get("seams", [])
    if not seams:
        return result(name, "pass", "切れ目がありません")
    if poses is None:
        return skip(name, "poses")
    fps = timeline["fps"]
    frame_of = lambda t: int(round(t * fps))  # noqa: E731  時刻の丸め（4 桁など）で切れ目の前後を取り違えないよう、コマで比べる
    problems = []
    for seam in seams:
        at = frame_of(seam)
        before = [p for p in poses if at - 2 <= frame_of(p["t"]) < at]
        after = [p for p in poses if at <= frame_of(p["t"]) <= at + 2]
        pairs = list(zip(max(before, key=lambda p: p["t"])["points"], min(after, key=lambda p: p["t"])["points"])) \
            if before and after else []
        note = _camera_note(timeline, seam)
        if not pairs:      # 同じ骨格どうしや空の点を比べると差が 0 で合格に見えるので、見ていない扱い
            problems.append({"t": seam, "text": "", "detail": f"切れ目の前後 2 コマ以内に骨格がなく、姿勢の差を見ていません{note}"})
            continue
        move = sum(math.dist(a, b) for a, b in pairs) / len(pairs)
        if move > th["seam_pose"]:
            proposal = note or "。切るコマを変えるか camera.seams を入れてください"
            problems.append({"t": seam, "text": "", "detail": f"姿勢の差 {move:.3f}{proposal}"})
    return result(name, "warn" if problems else "pass", f"切れ目 {len(seams)}、姿勢の差が大きいもの {len(problems)}", problems)


OVERLAP_MODES = ("time", "time_and_box")


def check_hook_overlap(timeline, th):
    """同時に 1 行だけの層（exclusive）ごとに、行の重なりを見る。exclusive の無い timeline は back だけ。

    見えている区間は [start, _visible_end]（timeline の lyric_fade。無ければ after = フェードアウト込み）。問題の行には 2 行の箱が
    交わるか（box_intersects）を添える。thresholds.overlap_mode が time_and_box なら、箱が交わるときだけ数える（別の列の 2 行は重なりではない）。"""
    name = "字の重なり"
    mode = th.get("overlap_mode", "time")
    if mode not in OVERLAP_MODES:
        raise PipelineError(f"thresholds.overlap_mode は {' か '.join(OVERLAP_MODES)} にしてください: {mode!r}")
    fade = _lyric_fade(timeline)
    grouped = {}
    for line in timeline["lyrics"]:
        if line.get("exclusive", line["layer"] == "back"):
            grouped.setdefault(line["layer"], []).append((line, _visible_end(line, fade)))
    problems, changes, apart = [], 0, 0
    for lines in grouped.values():
        lines = sorted(lines, key=lambda item: item[0]["start"])
        changes += max(0, len(lines) - 1)
        for (left, left_until), (right, right_until) in itertools.combinations(lines, 2):
            shared = min(left_until, right_until) - right["start"]
            if shared <= th["hook_overlap_s"] + EPS:
                continue
            crossing = _intersects(left["box"], right["box"])
            if mode == "time_and_box" and not crossing:
                apart += 1
                continue
            note = "フェードアウト込み" if fade == "after" else "end まで"
            problems.append({"t": right["start"], "text": f"{left['text']} / {right['text']}", "box_intersects": crossing,
                             "detail": f"{shared:.3f} 秒同時に見えます（{note}。箱は{'交わる' if crossing else '交わらない'}）"})
    summary = f"入れ替わり {changes} 回、超過 {len(problems)}"
    if apart:
        summary += f"（箱が交わらず数えなかった重なり {apart}）"
    return result(name, "fail" if problems else "pass", summary, problems)


ALIGN_NAMES = {"beat": "拍", "downbeat": "小節頭"}


def _beat_events(timeline):
    """[(種類, 時刻, 対象外か, 照合先)]。照合先は flash_info / seam_info の align（無ければ beat。値はスキーマで検証済み）。
    0.1 の timeline は数値の配列だけを持つ。blend の切れ目の時刻は blend の開始時刻。"""
    flashes = [("フラッシュ", f["t"], f["beat_exempt"], f.get("align", "beat")) for f in timeline["flash_info"]] if "flash_info" in timeline \
        else [("フラッシュ", t, False, "beat") for t in timeline.get("flashes", [])]
    seams = [("切れ目", item["t"], item["beat_exempt"], item.get("align", "beat")) for item in timeline["seam_info"]] if "seam_info" in timeline \
        else [("切れ目", t, False, "beat") for t in timeline.get("seams", [])]
    return flashes + seams


def _grids(analysis):
    """{照合先: (時刻の配列, 実際に比べる種類)}。拍が無ければ小節頭で代用する（小節頭は拍の部分集合）。
    小節頭が無いときの "downbeat" は代用せず、配列を空にして「見ていない」にする。"""
    beats = sorted(analysis.get("beats") or analysis.get("beats_s") or [])
    downbeats = sorted(analysis.get("downbeats") or [])
    return {"beat": (beats, "beat") if beats else (downbeats, "downbeat"), "downbeat": (downbeats, "downbeat")}


def _nearest(grid, t):
    """昇順の grid でいちばん近い時刻との差。"""
    i = bisect.bisect_left(grid, t)
    return min(abs(t - g) for g in grid[max(0, i - 1):i + 1])


def check_beats(timeline, analysis, th):
    """フラッシュと切れ目が、拍（align が downbeat のものは小節頭）から 1 コマ以内か。"""
    name = "拍"
    analysis = analysis if analysis is not None else timeline.get("beats")
    if analysis is None:
        return skip(name, "beats")
    grids = _grids(analysis)
    if not grids["beat"][0]:
        return skip(name, "beats の downbeats / beats / beats_s 配列")
    limit = th["beat_frames"] / timeline["fps"]
    problems, exempt, unseen, counted = [], 0, 0, {"beat": 0, "downbeat": 0}
    for label, t, excused, align in _beat_events(timeline):
        if excused:
            exempt += 1
            continue
        grid, kind = grids[align]
        if not grid:     # align: "downbeat" なのに小節頭の配列が無い。拍で代用すると求めた検査をしたことになるので、見ていない扱い
            unseen += 1
            problems.append({"t": t, "text": label, "detail": "小節頭の配列が無く、小節頭との照合をしていません（beats に downbeats を入れてください）"})
            continue
        counted[kind] += 1
        off = _nearest(grid, t)
        if off > limit + EPS:
            problems.append({"t": t, "text": label, "detail": f"{ALIGN_NAMES[kind]}から {off:.3f} 秒ずれています（許容 {limit:.3f} 秒）"})
    parts = [f"{counted[kind]} 件を{ALIGN_NAMES[kind]}と照合" for kind in ("beat", "downbeat") if counted[kind]]
    summary = "、".join(parts or ["0 件を拍と照合"]) + f"、ずれ {len(problems) - unseen}"
    if exempt:
        summary += f"（対象外（意図）{exempt} 件）"
    if unseen:
        summary += f"（小節頭の配列が無く見ていない {unseen} 件）"
    status = "fail" if len(problems) > unseen else "warn" if unseen else "pass"
    return result(name, status, summary, problems)


def _info(path):
    """(映像の尺, 音声があるか)。ffmpeg の情報の出力から読む。"""
    run = subprocess.run([ffmpeg(), "-hide_banner", "-nostdin", "-i", str(path)],
                         capture_output=True, text=True, encoding="utf-8", errors="replace")
    duration = re.search(r"Duration: (\d+):(\d+):([\d.]+)", run.stderr)
    seconds = int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3]) if duration else None
    return seconds, "Audio:" in run.stderr


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
    decoded = decode(video, "v")
    frames = decoded["frames"]
    seconds, audio = _info(video)
    problems = []
    if not decoded["ok"]:
        problems.append({"t": 0, "text": "", "detail": f"復号に失敗します: {decoded['error']}"})
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


FLASH_CHECK = "明滅"


def _flash_source(video, master):
    """明滅を測る動画と、その呼び名。master があれば master、無ければ video（どちらも無ければ None）。"""
    return (master, "master") if master is not None else (video, "video")


def check_flashes(timeline, video, master, limits):
    """完成動画の明滅（gate.json に flash を書いたときだけ）。(検査の行, flash_report.json の中身 | None)。

    master（無ければ video）を gate/flashes.py で測り、どの 1 秒の窓でも明滅が max_per_s 回以下かを見る。
    回数は機械で測った数字で、規格への適合の判定ではない。問題の時刻は、ほかの検査と同じショットの時刻（描いた区間の開始を足す）。
    動画を読めないときは、ほかの検査の結果を捨てずに、この行を不合格にする（測れていないものを合格にしない）。"""
    name = FLASH_CHECK
    target, label = _flash_source(video, master)
    if target is None:
        return skip(name, "master か video"), None
    from . import flashes
    try:
        measured = flashes.measure(target, limits["delta"], limits["max_per_s"])
    except PipelineError as error:
        return result(name, "fail", f"{label} の明滅を測れません", [{"t": 0, "text": "", "detail": str(error)[:400]}]), None
    counts, top, window = measured["counts"], measured["max_per_window"], measured["busiest_window"]
    # 変化と明滅の数は動画全体のもの。合否は 1 秒の窓ごとに数え直した最大の回数で決まる（全体の数より多いことがある）
    summary = (f"{label} を測定、動画全体で変化 {counts['changes']}（上がり {counts['up']}・下がり {counts['down']}）・明滅 {counts['flashes']} 回、"
               f"1 秒の窓ごとに数え直して最大 {top} 回（上限 {limits['max_per_s']:g}、変化は相対輝度で {limits['delta']:g} 以上）")
    problems = []
    if measured["status"] == "fail":
        frames = "・".join(f"{a}-{b}" for a, b in window["pairs"])
        # 時刻はミリ秒に切り捨てる。静止画は「この時刻以降の最初のコマ」を取るので、四捨五入で 1 コマ先（光った次のコマ）へずれないように
        at = math.floor(window["start_frame"] / measured["video"]["fps"] * 1000) / 1000
        problems.append({"t": _window(timeline)[0] + at, "text": "",
                         "detail": f"動画のコマ {window['start_frame']}（{at:.3f} 秒）からの 1 秒に明滅 {top} 回"
                                   f"（上限 {limits['max_per_s']:g}。組のコマ {frames}）"})
    return result(name, measured["status"], summary, problems), measured


def _extract_stills(report, video, out, first=0.0, sources=None):
    """問題のコマを静止画で書き出す（検査ごとに最大 MAX_STILLS 枚）。first は動画の先頭のショット時刻。

    sources は {検査の名前: 動画}。その検査の静止画だけを、測った動画から取る（明滅は master を測るので master から）。
    ほかの検査は今までどおり video から取り、video が無ければ取らない。"""
    for item in report["checks"]:
        source = (sources or {}).get(item["check"], video)
        if source is None:
            continue
        for index, problem in enumerate(item["problems"][:MAX_STILLS]):
            if not problem.get("t"):
                continue
            target = out / f"{item['check']}_{index:02d}_{problem['t']:.2f}s.jpg"
            run = subprocess.run([ffmpeg(), "-y", "-loglevel", "error", "-nostdin", "-ss", f"{max(0.0, problem['t'] - first):.3f}", "-i", str(source),
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


def _validate_timeline(timeline):
    schema = json.loads(TIMELINE_SCHEMA.read_text(encoding="utf-8"))
    errors = sorted(jsonschema.Draft202012Validator(schema).iter_errors(timeline), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        raise PipelineError("timeline の形式が違います:\n" + "\n".join(lines))


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
    _validate_timeline(timeline)
    th = {**THRESHOLDS, **config.get("thresholds", {})}
    if "confirmed_overrides_measured" in config:
        th["confirmed_overrides_measured"] = config["confirmed_overrides_measured"]
    flash = config.get("flash")       # 書いたときだけ明滅を測る。書かなければ、下の 9 つの検査も報告も今までどおり
    if flash is not None:
        from . import flashes
        flash = flashes.settings(flash, 'gate の設定の "flash"')
    video, master = path_of("video"), path_of("master")
    scoped = _scoped(timeline)
    confirmed = load("confirmed")
    if config.get("faces") == "auto":
        if not config.get("spec"):
            raise PipelineError('gate の設定の "faces": "auto" には "spec"（dance の spec のパス）が必要です')
        faces = estimate_faces(path_of("spec"), timeline)
    else:
        faces = load("faces")
    layers = config.get("lyric_layers") or config.get("lyric_layer", "band")
    checks = [check_lyric_time(timeline, load("measured_lines"), layers, th, confirmed),
              check_confirmed(timeline, confirmed, th),
              check_faces(scoped, faces),
              check_ui(scoped),
              check_jitter(load("tracks"), th, _window(timeline)),
              check_seams(scoped, load("poses"), th),
              check_hook_overlap(scoped, th),
              check_beats(scoped, load("beats"), th),
              check_export(timeline, video, master, config.get("expected_sha256"), th)]
    flash_report = None
    if flash is not None:
        row, flash_report = check_flashes(timeline, video, master, flash)
        checks.append(row)
    statuses = {c["status"] for c in checks}
    overall = ("fail" if "fail" in statuses else "pass_with_skips" if "skip" in statuses
               else "pass_with_warnings" if "warn" in statuses else "pass")
    report = {"overall": overall, "timeline": str(path_of("timeline")), "thresholds": th, "checks": checks}
    if flash is not None:
        report["flash"] = {**flash, "report": flashes.REPORT_NAME if flash_report is not None else None}
    out = Path(out) if out else base / "gate_report"
    out.mkdir(parents=True, exist_ok=True)
    if flash_report is not None:
        flashes.write(flash_report, out)
    # flash を書いたときだけ、明滅の静止画を測った動画（master があれば master）から取る。ほかは今までどおり video から
    sources = {FLASH_CHECK: _flash_source(video, master)[0]} if flash is not None else None
    _extract_stills(report, video, out, _window(timeline)[0], sources)
    (out / "gate_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "gate_report.md").write_text(_markdown(report), encoding="utf-8")
    return report, out
