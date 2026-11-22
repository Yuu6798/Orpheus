"""3D の描画から書き出した動画の検査を、1 枚の表にする（`mv gate final`。実験的）。

    mv gate final VIDEO [--video-frames REPORT] [--show REPORT …] [--width W --height H --fps F --frames N] [--no-audio]
                        [--min-last-mean 0.02] [--out DIR]

リポジトリ外の 4 作目の 3D のダンスで、本番の書き出しのたびに、動画の構造・取り出したコマの照らし合わせ・最後のコマ・影の領域の
あふれ・描画の秒を、使い捨ての台本で 1 枚の表にした。それを、今ある部品を呼ぶ形の表にしたもの。数と合否の行を並べるだけで、
絵の良し悪しは判定しない。

- 動画の構造（大きさ・fps・コマの数・音・BT.709 の印・復号）は、fx の `probe.check_video`（technical_pass）に任せる。
  期待の大きさ・fps・コマの数は、省くと `--show` の報告から埋める（大きさと fps はどれも同じとき、幅と高さは別々に。コマの数は
  描いた T を合わせた数が 1 ずつ続くとき）。コマの数と fps が分かれば、音の長さも コマの数 ÷ fps と比べる。
  分からないまま見なかった期待があれば、構造の行は `partial`（全体は `incomplete`。合格扱いにしない）。
- 3D の側の行は、model3d の工程が書いたファイルを読む（mv_pipeline は model3d を import しない。逆も同じ。つなぎはファイルだけ）。
  - `--video-frames`: model3d の `check.video_frames` の報告（動画から取り出したコマが、仕上げたコマの同じ T に合うか）。
    報告の `video_sha256` が、検査する動画の sha256 と違えば不合格（同じ名前に書き出し直した動画を、古い報告で合格にしない）。
    `video_sha256` の無い報告は、どの動画と比べたか分からないので `not_recorded`。
  - `--show`: model3d の `render.show` の報告（`model3d-show-render/0.1`。区切って描いたなら全部）。影の領域のあふれの行数
    （`shadow_overflow.lines`）の和が 0 でなければ不合格。あふれを数える前の報告（`shadow_overflow` が無い）は `not_recorded`。
    描画の秒（`seconds`）と描いたコマの数は、数だけ。
- 最後のコマの明るさ（R・G・B の平均。0〜1）が `--min-last-mean`（既定 0.02。黒に近い）より小さければ不合格（暗転していない
  ことを確かめる。暗転で終わる動画では `--min-last-mean 0`）。

全体の `status`: どれかの行が不合格なら `fail`（終了コード 1）。不合格が無く、渡していない行（`not_run`）か記録の無い行
（`not_recorded`）か見ていない期待のある行（`partial`）があれば `incomplete`（終了コード 0。合格扱いにしない）。全部の行が合格なら `pass`。
出力（既定は動画の隣の `<動画の名前>_final_report/`）は final_report.json（`gate-final-report/0.1`）。
"""
import json
import math
from pathlib import Path

from ..io import PipelineError, atomic_json, read_json_file, sha256

SCHEMA = "gate-final-report/0.1"
REPORT_NAME = "final_report.json"
SHOW_SCHEMA = "model3d-show-render/0.1"
MIN_LAST_MEAN = 0.02
UNFINISHED = {"not_run", "not_recorded", "partial"}
NOTE = "書き出しの検査の数と合否の表です。絵の良し悪しは判定しません。未実施・記録なし・見ていない期待のある行は合格に数えません。"


def _positive(value, name, integer=False):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or (
            integer and int(value) != value):
        raise PipelineError(f"{name} は正の{'整数' if integer else '数'}にしてください: {value!r}")
    return int(value) if integer else float(value)


def _report(path, what):
    """報告の JSON（辞書）。無い・JSON でない・辞書でなければ PipelineError。"""
    data = read_json_file(path, f"{what}の報告が見つかりません: {path}", f"{what}の報告が JSON ではありません: {path}")
    if not isinstance(data, dict):
        raise PipelineError(f"{what}の報告の形が違います（JSON の辞書ではありません）: {path}")
    return data


def video_frames_row(path, video_sha):
    """check.video_frames の報告の行。"""
    if path is None:
        return {"check": "video_frames", "status": "not_run", "summary": "--video-frames を渡していません（check.video_frames の報告）"}
    report = _report(path, "check.video_frames ")
    if report.get("check") != "video_frames":
        raise PipelineError(f"--video-frames は model3d の check.video_frames の報告にしてください: {path}")
    row = {"check": "video_frames", "report": str(path), "video": str(report.get("video")), "video_sha256": report.get("video_sha256"),
           "pass": report.get("pass"),
           **{key: report.get(key) for key in ("samples", "matched", "mismatched", "indistinct", "shifts", "same_mean_abs")}}
    recorded = report.get("video_sha256")
    if not isinstance(recorded, str):
        return {**row, "status": "not_recorded",
                "summary": "報告に比べた動画の sha256（video_sha256）が無いので、検査する動画と同じか確かめられません（今の check.video_frames で回し直す）"}
    if recorded != video_sha:
        return {**row, "status": "fail", "summary": "報告の動画（sha256）が、検査する動画と違います（書き出し直したなら check.video_frames も回し直す）"}
    if report.get("pass") is True:
        return {**row, "status": "pass", "summary": report.get("reason") or "取り出したコマが、同じ T のもとのコマに合います"}
    if report.get("pass") is False:
        return {**row, "status": "fail", "summary": report.get("reason") or "取り出したコマが、同じ T のもとのコマに合いません"}
    return {**row, "status": "not_run", "summary": report.get("reason") or "check.video_frames が比べられませんでした（未実施）"}


def _worst(value):
    return value if (isinstance(value, list) and len(value) == 2
                     and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)) else None


def show_rows(paths):
    """render.show の報告（区切りごと）から、影の領域のあふれの行と、描画の秒の行と、報告ごとの数。"""
    if not paths:
        missing = "--show を渡していません（render.show の報告）"
        return ({"check": "shadow_overflow", "status": "not_run", "summary": missing},
                {"check": "render_seconds", "status": "not_run", "summary": missing}, [])
    parts, unrecorded = [], []
    for path in paths:
        report = _report(path, "render.show ")
        if report.get("schema_version") != SHOW_SCHEMA:
            raise PipelineError(f"--show は model3d の render.show の報告（{SHOW_SCHEMA}）にしてください: {path}")
        overflow = report.get("shadow_overflow")
        if not isinstance(overflow, dict) or isinstance(overflow.get("lines"), bool) or not isinstance(overflow.get("lines"), int):
            unrecorded.append(str(path))
            overflow = None
        else:
            overflow = {"lines": overflow["lines"], "worst": _worst(overflow.get("worst")), "action": overflow.get("action")}
        rendered = report.get("rendered")
        rendered = [T for T in rendered if isinstance(T, int) and not isinstance(T, bool)] if isinstance(rendered, list) else []
        seconds = report.get("seconds")
        parts.append({"report": str(path), "rendered": len(rendered), "Ts": rendered,
                      "seconds": seconds if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None,
                      "seconds_per_frame": report.get("seconds_per_frame"), "resolution": report.get("resolution"),
                      "fps": report.get("fps"), "shadow_overflow": overflow})
    lines = sum(p["shadow_overflow"]["lines"] for p in parts if p["shadow_overflow"])
    worst = [p["shadow_overflow"]["worst"] for p in parts if p["shadow_overflow"] and p["shadow_overflow"]["worst"]]
    shadow = {"check": "shadow_overflow", "lines": lines, "worst": max(worst, key=lambda w: w[0]) if worst else None,
              "with_lines": [p["report"] for p in parts if p["shadow_overflow"] and p["shadow_overflow"]["lines"]],
              "unrecorded": unrecorded}
    if lines:
        shadow.update(status="fail", summary=f"影の領域のあふれが {lines} 行あります（あふれたコマは描くたびに影が変わります）")
    elif unrecorded:
        shadow.update(status="not_recorded", summary=f"あふれを数える前の render.show の報告があります（{len(unrecorded)} 個）")
    else:
        shadow.update(status="pass", summary="影の領域のあふれは 0 行")
    frames = sum(p["rendered"] for p in parts)
    total = round(sum(p["seconds"] for p in parts), 1) if all(p["seconds"] is not None for p in parts) else None
    timing = {"check": "render_seconds", "status": "info", "reports": len(parts), "frames": frames, "seconds": total,
              "seconds_per_frame": round(total / frames, 2) if total is not None and frames else None,
              "summary": (f"{frames} コマを {total} 秒で描きました" if total is not None else "描画の秒が無い報告があります")}
    return shadow, timing, parts


def last_frame_row(video, frames, min_mean):
    """最後のコマの明るさ（R・G・B の平均。0〜1）。"""
    from ..inspection import video as reader
    if not frames:
        return {"check": "last_frame", "status": "fail", "summary": "動画のコマを数えられません（最後のコマを読めません）"}
    info = reader.describe(video)
    picked = reader.grab(video, [frames - 1], info, total=frames)
    if frames - 1 not in picked:
        return {"check": "last_frame", "status": "fail", "summary": f"最後のコマ（{frames - 1} 番）を取り出せません"}
    mean = round(float(picked[frames - 1].mean()) / 255.0, 4)
    row = {"check": "last_frame", "index": frames - 1, "mean": mean, "min_mean": min_mean}
    if mean < min_mean:
        return {**row, "status": "fail", "summary": f"最後のコマが暗すぎます（明るさの平均 {mean:.3f}、目安 {min_mean:g} 以上。暗転していないか）"}
    return {**row, "status": "pass", "summary": f"最後のコマの明るさの平均 {mean:.3f}"}


def expected_from(parts, width, height, fps, frames):
    """省いた期待の大きさ・fps・コマの数を、render.show の報告から埋める。大きさと fps はどの報告も同じとき（幅と高さは別々に）、
    コマの数は描いた T を合わせた数が 1 ずつ続くとき。どこから来たかも返す。"""
    sizes = {tuple(p["resolution"]) for p in parts if isinstance(p.get("resolution"), list) and len(p["resolution"]) == 2}
    rates = {float(p["fps"]) for p in parts if isinstance(p.get("fps"), (int, float)) and not isinstance(p.get("fps"), bool)}
    source = {}
    if len(sizes) == 1:
        w, h = next(iter(sizes))
        if width is None:
            width, source["width"] = w, "show"
        if height is None:
            height, source["height"] = h, "show"
    if fps is None and len(rates) == 1:
        fps, source["fps"] = next(iter(rates)), "show"
    Ts = sorted({T for p in parts for T in p["Ts"]})
    if frames is None and Ts and Ts == list(range(Ts[0], Ts[0] + len(Ts))):
        frames, source["frames"] = len(Ts), "show"
    return width, height, fps, frames, source


def measure(video, video_frames=None, shows=(), width=None, height=None, fps=None, frames=None, audio=True, min_last_mean=MIN_LAST_MEAN):
    from ..fx import probe
    video = Path(video)
    if not video.is_file():
        raise PipelineError(f"動画が見つかりません: {video}")
    width, height = _positive(width, "--width", True), _positive(height, "--height", True)
    fps, frames = _positive(fps, "--fps"), _positive(frames, "--frames", True)
    if isinstance(min_last_mean, bool) or not isinstance(min_last_mean, (int, float)) or not 0 <= min_last_mean <= 1:
        raise PipelineError(f"--min-last-mean は 0〜1 の数にしてください: {min_last_mean!r}")
    digest = sha256(video)
    shadow, timing, parts = show_rows([Path(p) for p in shows])
    width, height, fps, frames, filled = expected_from(parts, width, height, fps, frames)
    audio_s = frames / fps if audio and frames and fps else None
    checks = probe.check_video(video, width=width, height=height, fps=fps, frames=frames, audio=audio, audio_s=audio_s, bt709=True)
    video_info = checks.get("video") or {}
    expected = {"width": width, "height": height, "fps": fps, "frames": frames, "audio": audio, "audio_s": audio_s, "bt709": True}
    unchecked = [key for key in ("width", "height", "fps", "frames") if expected[key] is None]
    if audio and audio_s is None:
        unchecked.append("audio_s")
    if not checks["pass"]:
        status, summary = "fail", "; ".join(checks["failures"])
    elif unchecked:
        status, summary = "partial", f"見た所は期待どおりですが、見ていない期待があります: {'・'.join(unchecked)}（--width・--height・--fps・--frames か --show）"
    else:
        status, summary = "pass", "動画の構造は期待どおり（technical_pass）"
    structure = {"check": "structure", "status": status, "summary": summary, "expected": expected, "filled_from": filled,
                 "unchecked": unchecked, "technical_pass": checks}
    last = last_frame_row(video, checks.get("frames"), float(min_last_mean)) if checks.get("decode_ok") else {
        "check": "last_frame", "status": "fail", "summary": "動画を復号できないので、最後のコマを読めません"}
    rows = [structure, video_frames_row(Path(video_frames) if video_frames else None, digest), last, shadow, timing]
    statuses = [row["status"] for row in rows]
    overall = "fail" if "fail" in statuses else ("incomplete" if UNFINISHED & set(statuses) else "pass")
    return {"schema_version": SCHEMA,
            "video": {"name": video.name, "sha256": digest, "width": video_info.get("width"), "height": video_info.get("height"),
                      "fps": video_info.get("fps"), "frames": checks.get("frames"), "audio": checks.get("audio") is not None},
            "status": overall, "checks": rows, "renders": [{k: v for k, v in p.items() if k != "Ts"} for p in parts], "note": NOTE}


def run(video, out=None, video_frames=None, shows=(), width=None, height=None, fps=None, frames=None, audio=True,
        min_last_mean=MIN_LAST_MEAN):
    """mv gate final。報告を書き、(報告, 出力フォルダ) を返す。"""
    report = measure(video, video_frames, shows, width, height, fps, frames, audio, min_last_mean)
    video = Path(video)
    out = Path(out) if out else video.with_name(video.stem + "_final_report")
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / REPORT_NAME, report)
    return report, out


def summary(report, out):
    return {"status": report["status"], "checks": [{k: row[k] for k in ("check", "status", "summary")} for row in report["checks"]],
            "report": str(Path(out) / REPORT_NAME)}


def dumps(report):
    return json.dumps(report, ensure_ascii=False, indent=2)
