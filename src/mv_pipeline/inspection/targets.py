"""見つけた所を直す場所に結び付ける: 元動画のコマ・dance の spec の clip と出力の秒・生成の素性。手当ての候補と修正計画。

- clips の書き方: 直す場所は動画ファイルの元のコマ [a, b) と秒。clips[].origin（生成の素性）をそのまま写す。
- dance_spec の書き方: 同じ動画のコマが、spec のどの clip（JSON Pointer `/performer/clips/i`）で、出力のどのコマ・秒に出るか。
  出力に出ない区間で見つけたものは unused に分け、数だけ報告する。素性は origins の動画のパスの欄から写す。

手当ての候補（actions）は規則で出す提案で、実行はしない。自動の再生成は未実装（修正計画が将来の入口）。
"""
import json

from . import PLAN_SCHEMA


def _seconds(frames, fps):
    return [round(frames[0] / fps, 4), round(frames[1] / fps, 4)] if fps else None


def runs_of(numbers):
    """整数の列 → 続きの区間 [[a, b), …]。"""
    out = []
    for value in sorted(set(numbers)):
        if out and value == out[-1][1]:
            out[-1][1] = value + 1
        else:
            out.append([value, value + 1])
    return out


def dance_usage(spec):
    """{動画のパス: [(出力のコマ, clip の番号, 元のコマ, 重み)]}・出力のコマ数・fps。blend の窓では前の clip も数える。"""
    from ..dance import clips as danceclips
    from ..fx import timeline

    fps = spec.data["output"]["fps"]
    count = timeline.frame_count(spec.data["duration_s"], fps)
    order = danceclips.Timeline(spec)
    usage = {}
    try:
        for k in range(count):
            for index, number, weight in order.mapping(timeline.frame_time(k, fps)):
                usage.setdefault(spec.data["performer"]["clips"][index]["path"], []).append((k, index, int(number), round(float(weight), 4)))
    finally:
        order.close()
    return usage, count, fps


def dance_uses(usage, frames, last_frame, shot_fps):
    """元のコマ [a, b) が出る所 [{"pointer", "clip_index", "shot_frames", "shot_t"}]。元のコマは動画の最後のコマで止める。"""
    a, b = frames
    by_clip = {}
    for k, index, number, _ in usage:
        if a <= min(number, last_frame) < b:
            by_clip.setdefault(index, []).append(k)
    return [{"pointer": f"/performer/clips/{index}", "clip_index": index, "shot_frames": runs_of(shown),
             "shot_t": [[round(r[0] / shot_fps, 4), round(r[1] / shot_fps, 4)] for r in runs_of(shown)]}
            for index, shown in sorted(by_clip.items())]


def target(clip, frames, fps, uses=None):
    """直す場所。clip は run が作るクリップの記録（id・rel・sha256・origin・spec など）。"""
    out = {"kind": "dance_clip" if clip.get("spec") else "clip", "clip": clip["id"], "path": clip["rel"], "sha256": clip["sha256"],
           "source_frames": list(frames), "source_t": _seconds(frames, fps), "origin": clip.get("origin")}
    if clip.get("spec"):
        out["spec"] = clip["spec"]
        out["uses"] = uses or []
    return out


def actions(event, clip, th):
    """手当ての候補（提案。実行はしない）。"""
    a, b = event["frames"]
    count, fps = clip["frames_total"], clip["fps"] or 0
    checks = {s["check"] for s in event["signals"]}
    out = []
    if "duplicate_cadence" in checks:
        period = next(s["period"] for s in event["signals"] if s["check"] == "duplicate_cadence")
        out.append({"kind": "decimate", "cycle": period,
                    "description": f"{period} コマごとの重複を ffmpeg の decimate=cycle={period} などで除いてから使う（speed を変えるときは先に除く）"})
    if "freeze" in checks:
        out.append({"kind": "interpolate", "frames": [a, b], "in_orpheus": False,
                    "description": "止まったコマの間を補間で埋めるか（外部の補間の道具）、生成し直す"})
    if b - a <= 2 and event["category"] in ("continuity", "shape", "motion") and not checks & {"duplicate_cadence", "freeze"}:
        out.append({"kind": "replace_frames", "frames": [a, b], "with": [a - 1 if a > 0 else None, b if b < count else None], "in_orpheus": False,
                    "description": "前後のコマで置き換える（前のコマを保つか前後を混ぜる）。Orpheus にはまだ無い（dance-shot/0.3 で予定）"})
    if "duplicate_cadence" not in checks:
        out.append({"kind": "regenerate_segment", "frames": [a, b], "anchors": {"before": a - 1 if a > 0 else None, "after": b if b < count else None},
                    "description": "前後のコマを始点・終点にして、この区間だけを生成し直す（始点と終点を指定できる生成器で）"})
    costume = event["category"] == "costume" or any(s["category"] == "costume" and s["check"] in ("reference_missing", "reference_extra", "vlm")
                                                    for s in event["signals"])
    if costume:
        out.append({"kind": "regenerate_with_reference",
                    "description": "設定画を参照に入れて（または強めて）生成し直す。尺を短く切って生成すると配色と画風がずれにくい"})
        out.append({"kind": "color_match", "description": "配色の差だけなら仕上げで色を合わせる（dance は hooks の performer など）"})
    first, last = clip["inspected"]
    span = last - first
    near = max(1, int(round(min(0.2 * span, 0.5 * fps if fps else 0.2 * span))))
    if span > 0 and a - first <= near and b < last:
        out.append({"kind": "trim", "keep": [b, last], "description": f"使う区間を元のコマ {b}〜{last - 1} に縮める" + _trim_note(clip)})
    elif span > 0 and last - b <= near and a > first:
        out.append({"kind": "trim", "keep": [first, a], "description": f"使う区間を元のコマ {first}〜{a - 1} に縮める" + _trim_note(clip)})
    return out


def _trim_note(clip):
    return "（dance は clips[].source_start・start・end を直す）" if clip.get("spec") else ""


def _cadence(event):
    return any(s["check"] == "duplicate_cadence" for s in event.get("signals", ()))


def plan(clips, events, th):
    """修正計画。クリップごとに、近い出来事（plan_merge_s 以内）をまとめて生成し直す区間にする（前後に plan_pad_frames 広げる）。

    重複コマの周期の出来事は、ほかとまとめずに 1 つの区間にする（間引きの手当てで、生成し直す区間を広げない）。
    まとめた区間の手当ては、種類とコマが同じものだけを 1 つにする（コマの違う置き換えや補間は全部残す）。"""
    segments = []
    by_clip = {}
    for event in events:
        by_clip.setdefault(event["clip"], []).append(event)
    for clip in clips:
        items = sorted(by_clip.get(clip["id"], []), key=lambda e: e["frames"][0])
        if not items:
            continue
        fps, count = clip["fps"] or 0, clip["frames_total"]
        gap = max(1, int(round(th["plan_merge_s"] * fps))) if fps else 1
        groups, apart = [], []
        for event in items:
            if _cadence(event):          # クリップ全体の重複の周期（手当ては間引き）は、局所の生成し直しの区間に混ぜない
                apart.append([event])
            elif groups and event["frames"][0] - max(e["frames"][1] for e in groups[-1]) <= gap:   # 群のいちばん後ろの終わりと比べる
                groups[-1].append(event)
            else:
                groups.append([event])
        groups = sorted(groups + apart, key=lambda group: min(e["frames"][0] for e in group))
        for group in groups:
            a, b = min(e["frames"][0] for e in group), max(e["frames"][1] for e in group)
            pad = th["plan_pad_frames"]
            window = [max(0, a - pad), min(count, b + pad)]
            seen, merged = set(), []
            for event in group:
                for action in event["actions"]:
                    key = json.dumps({k: v for k, v in action.items() if k != "description"}, sort_keys=True, ensure_ascii=False)
                    if key not in seen and action["kind"] != "regenerate_segment":   # 同じ種類でもコマの違う手当ては残す
                        seen.add(key)
                        merged.append(action)
            if any(a["kind"] == "regenerate_segment" for e in group for a in e["actions"]):
                merged.insert(0, {"kind": "regenerate_segment", "frames": window,
                                  "description": "前後のコマ（anchors）を始点・終点にして、この区間だけを生成し直す"})
            uses = {}
            for event in group:
                for use in event["target"].get("uses", []):
                    uses.setdefault(use["pointer"], set()).update(k for r in use["shot_frames"] for k in range(r[0], r[1]))
            segment = {"id": f"R{len(segments) + 1:02d}", "clip": clip["id"], "path": clip["rel"], "sha256": clip["sha256"],
                       "source_frames": window, "source_t": _seconds(window, fps), "event_frames": [a, b],
                       "events": [e["id"] for e in group], "categories": sorted({e["category"] for e in group},
                                                                                key=lambda c: ("continuity", "shape", "costume", "motion", "other").index(c)),
                       "evidence": sorted({e["evidence"] for e in group}),
                       "anchors": {"before": {"frame": window[0] - 1} if window[0] > 0 else None,
                                   "after": {"frame": window[1]} if window[1] < count else None},
                       "origin": clip.get("origin"), "actions": merged}
            if clip.get("spec"):
                segment["spec"] = clip["spec"]
                segment["uses"] = [{"pointer": pointer, "shot_frames": runs_of(shown),
                                    "shot_t": [[round(r[0] / clip["shot_fps"], 4), round(r[1] / clip["shot_fps"], 4)] for r in runs_of(shown)]}
                                   for pointer, shown in sorted(uses.items(), key=lambda item: int(item[0].rsplit("/", 1)[1]))]
            segments.append(segment)
    return {"schema_version": PLAN_SCHEMA, "status": "proposed", "executor": None,
            "note": "提案だけで、何も実行していない。自動の再生成は未実装（将来）。区間ごとに素性（origin）・前後のコマ（anchors）・手当ての候補を渡す",
            "segments": segments}
