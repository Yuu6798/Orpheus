"""Remotion 用の props（`mv export remotion-props`）。analysis.json（と任意で storyboard.json）から、コマ番号だけの JSON を書く。

    mv export remotion-props PROJECT --out props.json [--beats beats.json | --beats-per-bar 4 [--downbeat-phase 0]] [--no-storyboard]

Remotion 側（renderer/src/lyric-props.ts の LyricProps）は秒を扱わず、ここで丸めたコマ番号だけを読む。
行の start_frame は floor、end_frame は ceil で、`mv render` の字幕（validate.captions）と同じ関数（validate.frame_span）を呼ぶ。
拍と小節頭はいちばん近いコマ（validate.frame_at。duration_frames の中だけ）。analysis.json には小節頭が無いので、--beats（gate / dance と
同じ {"beats", "downbeats"[, "bpm"]} の JSON。拍も bpm もこれに置き換え、名前と sha256 を記録する）か --beats-per-bar（beats_s を数えて作る）で
与える。どちらも無ければ空。描画の設定（fps・寸法・duration_frames）は storyboard.json があればその render、無ければ project.json。
行は mv render の字幕と同じ validate.captions で作る（開始順、前の行の終わりを次の始まりで切る、歌の重なりは止める）。

形式 remotion-props/0.1 は contracts/ の外（この module が正。contracts/ への昇格はユーザー承認が要る）。
時刻の無い行（start_s が null）は lines に入れず untimed に残す。推定の行（timing_source / review_status）はそのまま写し、確定扱いにしない。
"""
import math
from pathlib import Path

from . import validate
from .io import PipelineError, atomic_json, read_json, sha256

SCHEMA = "remotion-props/0.1"


def _grid(a, beats_path, beats_per_bar, downbeat_phase):
    """(拍の秒, 小節頭の秒, bpm, 出所)。"""
    beats = list(a["beats_s"])
    if beats_path is not None and beats_per_bar is not None:
        raise PipelineError("--beats と --beats-per-bar は同時に指定できません")
    if downbeat_phase and beats_per_bar is None:
        raise PipelineError("--downbeat-phase は --beats-per-bar と一緒に指定してください")
    if beats_path is not None:
        grid = read_json(beats_path)
        validate.require(isinstance(grid, dict), "--beats は beats / downbeats の配列を持つ JSON オブジェクトにしてください")
        external = grid.get("beats") or grid.get("beats_s") or []
        downbeats = grid.get("downbeats") or []
        for name, times in (("beats", external), ("downbeats", downbeats)):
            validate.require(isinstance(times, list) and all(isinstance(t, (int, float)) and t >= 0 for t in times),
                             f"--beats の {name} は 0 以上の数値の配列にしてください")
            validate.require(all(x < y for x, y in zip(times, times[1:])), f"--beats の {name} は昇順にしてください")
        bpm = grid.get("bpm")
        validate.require(bpm is None or isinstance(bpm, (int, float)), "--beats の bpm は数値にしてください")
        # 拍も bpm も analysis.json のものは使わない（混ぜない）。パスは名前だけ残す（個人のパスを書かない）
        return [float(t) for t in external], [float(t) for t in downbeats], bpm, {
            "kind": "file", "name": Path(beats_path).name, "sha256": sha256(beats_path), "replaces_analysis_beats": True}
    if beats_per_bar is not None:
        validate.require(beats_per_bar >= 1 and 0 <= downbeat_phase < beats_per_bar, "--beats-per-bar は 1 以上、--downbeat-phase はその未満にしてください")
        return beats, beats[downbeat_phase::beats_per_bar], a["bpm"], {"kind": "beats_per_bar", "beats_per_bar": beats_per_bar, "downbeat_phase": downbeat_phase}
    return beats, [], a["bpm"], {"kind": "analysis", "downbeats": "none"}


def build(root, beats_path=None, beats_per_bar=None, downbeat_phase=0, storyboard=True):
    root = Path(root)
    config = read_json(root / "project.json")
    a = read_json(root / "analysis.json")
    validate.analysis(a, root)
    analysis_sha = sha256(root / "analysis.json")
    story_path = root / "storyboard.json"
    s = None
    if storyboard and story_path.exists():
        s = read_json(story_path)
        validate.storyboard(s, a, analysis_sha)
    # 描画の設定は、storyboard があればその render（mv render が使うもの）、無ければ project.json
    render = s["render"] if s is not None else {"fps": config["fps"], "width": config["width"], "height": config["height"],
                                                 "duration_frames": math.ceil(a["audio"]["sample_count"] * config["fps"] / a["audio"]["sample_rate"])}
    fps, width, height, total = render["fps"], render["width"], render["height"], render["duration_frames"]
    # 行は mv render の字幕と同じ関数で作る（開始順、前の行の終わりを次の始まりで切る、歌の重なりは止める）
    by_id = {row["id"]: row for row in a["lyrics"]}
    lines = []
    for caption in validate.captions(a, fps, total):
        row = by_id[caption["id"]]
        lines.append({"id": row["id"], "text": row["text"], "display": row["text"], "sung": True,
                      "start_frame": caption["startFrame"], "end_frame": caption["endFrame"], "timing_source": row["timing_source"],
                      "review_status": row["review_status"], "confidence": row["confidence"]})
    untimed = [{"id": row["id"], "text": row["text"], "timing_source": row["timing_source"]} for row in a["lyrics"] if row["start_s"] is None]
    beats, downbeats, bpm, source = _grid(a, beats_path, beats_per_bar, downbeat_phase)
    frames_of = lambda times: [f for f in (validate.frame_at(t, fps) for t in times) if f < total]  # noqa: E731  コマに丸めてから範囲で切る
    props = {
        "schema_version": SCHEMA,
        "fps": fps, "width": width, "height": height, "duration_frames": total,
        "render_source": "storyboard" if s is not None else "project",
        "lines": lines, "untimed": untimed,
        "beats": {"frames": frames_of(beats), "downbeat_frames": frames_of(downbeats), "bpm": bpm, "source": source},
        "scenes": None, "show_lyrics": True,
        "sources": {"analysis_sha256": analysis_sha, "audio_sha256": a["audio"]["sha256"]},
    }
    if s is not None:
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
