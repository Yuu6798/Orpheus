"""Remotion 用の props（`mv export remotion-props`）。analysis.json（と任意で storyboard.json）から、コマ番号だけの JSON を書く。

    mv export remotion-props PROJECT --out props.json [--beats beats.json | --beats-per-bar 4 [--downbeat-phase 0]] [--no-storyboard]

Remotion 側（renderer/src/lyric-props.ts の LyricProps）は秒を扱わず、ここで丸めたコマ番号だけを読む。
行の start_frame は floor、end_frame は ceil で、`mv render` の字幕（validate.captions）と同じ関数（validate.frame_span）を呼ぶ。
拍と小節頭はいちばん近いコマ（validate.frame_at）。analysis.json には小節頭が無いので、--beats（gate / dance と同じ
{"beats", "downbeats"} の JSON。sha256 を記録する）か --beats-per-bar（beats_s を数えて作る）で与える。どちらも無ければ空。

形式 remotion-props/0.1 は contracts/ の外（この module が正。contracts/ への昇格はユーザー承認が要る）。
時刻の無い行（start_s が null）は lines に入れず untimed に残す。推定の行（timing_source / review_status）はそのまま写し、確定扱いにしない。
"""
import math
from pathlib import Path

from . import validate
from .io import PipelineError, atomic_json, read_json, sha256

SCHEMA = "remotion-props/0.1"


def _downbeats(a, beats_path, beats_per_bar, downbeat_phase):
    """(拍の秒, 小節頭の秒, 出所)。"""
    beats = list(a["beats_s"])
    if beats_path is not None and beats_per_bar is not None:
        raise PipelineError("--beats と --beats-per-bar は同時に指定できません")
    if beats_path is not None:
        grid = read_json(beats_path)
        external = grid.get("beats") or grid.get("beats_s") or []
        downbeats = grid.get("downbeats") or []
        validate.require(isinstance(external, list) and isinstance(downbeats, list), "--beats は beats / downbeats の配列を持つ JSON にしてください")
        validate.require(all(isinstance(t, (int, float)) for t in external + downbeats), "--beats の時刻は数値にしてください")
        return [float(t) for t in external], [float(t) for t in downbeats], {"kind": "file", "path": str(beats_path), "sha256": sha256(beats_path)}
    if beats_per_bar is not None:
        validate.require(beats_per_bar >= 1 and 0 <= downbeat_phase < beats_per_bar, "--beats-per-bar は 1 以上、--downbeat-phase はその未満にしてください")
        return beats, beats[downbeat_phase::beats_per_bar], {"kind": "beats_per_bar", "beats_per_bar": beats_per_bar, "downbeat_phase": downbeat_phase}
    return beats, [], {"kind": "analysis", "downbeats": "none"}


def build(root, beats_path=None, beats_per_bar=None, downbeat_phase=0, storyboard=True):
    root = Path(root)
    config = read_json(root / "project.json")
    a = read_json(root / "analysis.json")
    validate.analysis(a, root)
    fps, width, height = config["fps"], config["width"], config["height"]
    total = math.ceil(a["audio"]["sample_count"] * fps / a["audio"]["sample_rate"])
    lines, untimed = [], []
    for row in a["lyrics"]:
        if row["start_s"] is None:
            untimed.append({"id": row["id"], "text": row["text"], "timing_source": row["timing_source"]})
            continue
        start, end = validate.frame_span(row["start_s"], row["end_s"], fps, total)
        lines.append({"id": row["id"], "text": row["text"], "display": row["text"], "sung": True,
                      "start_frame": start, "end_frame": end, "timing_source": row["timing_source"],
                      "review_status": row["review_status"], "confidence": row["confidence"]})
    beats, downbeats, source = _downbeats(a, beats_path, beats_per_bar, downbeat_phase)
    props = {
        "schema_version": SCHEMA,
        "fps": fps, "width": width, "height": height, "duration_frames": total,
        "lines": lines, "untimed": untimed,
        "beats": {"frames": [validate.frame_at(t, fps) for t in beats if t < total / fps],
                  "downbeat_frames": [validate.frame_at(t, fps) for t in downbeats if t < total / fps],
                  "bpm": a["bpm"], "source": source},
        "scenes": None, "show_lyrics": True,
        "sources": {"analysis_sha256": sha256(root / "analysis.json"), "audio_sha256": a["audio"]["sha256"]},
    }
    story_path = root / "storyboard.json"
    if storyboard and story_path.exists():
        s = read_json(story_path)
        validate.storyboard(s, a, props["sources"]["analysis_sha256"])
        props["scenes"] = [{key: scene[key] for key in ("id", "start_frame", "end_frame", "asset_id", "motion", "transition_in")}
                           for scene in s["scenes"]]
        props["show_lyrics"] = s["render"]["show_lyrics"]
        props["sources"]["storyboard_sha256"] = sha256(story_path)
    return props


def export(root, out, beats_path=None, beats_per_bar=None, downbeat_phase=0, storyboard=True):
    props = build(root, beats_path, beats_per_bar, downbeat_phase, storyboard)
    out = Path(out)
    atomic_json(out, props)
    return {"props": str(out), "schema_version": SCHEMA, "fps": props["fps"], "size": [props["width"], props["height"]],
            "duration_frames": props["duration_frames"], "lines": len(props["lines"]), "untimed": len(props["untimed"]),
            "beats": len(props["beats"]["frames"]), "downbeats": len(props["beats"]["downbeat_frames"]),
            "scenes": None if props["scenes"] is None else len(props["scenes"])}
