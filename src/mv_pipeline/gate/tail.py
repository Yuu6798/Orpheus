"""完成動画の音の終わりと絵の終わり（`mv gate tail`。実験的）。timeline は要らず、完成した動画 1 本だけに掛けられる。

    mv gate tail VIDEO [--out DIR] [--max-tail-s 1.0] [--window 0.1] [--floor-db -60]

リポジトリ外の 2 作目の VRM のダンスの演出版で、曲の音が切れてから絵が 2.1 秒続き「余韻が長い」と言われ、0.5 秒に直した。その終わり方を
書き出しの前に機械で見るための道具（音の終わりの測り方は mv audio end と同じ。audio/end.py）。数を出すだけで、絵の良し悪しは判定しない。

- 音が切れる時刻 `audio_end_s`: 動画の音の、最後の鳴っている窓の終わり（window 秒ごとの RMS が、いちばん大きい窓から floor_db 以内）。
- 絵が続く長さ `picture_after_s` = 映像の長さ − `audio_end_s`（音が切れてから、暗転や静止を含めて絵が続く秒）。
- `status`: 映像に音が無ければ `fail`（測れない）。`picture_after_s` が `max_tail_s`（仮の値。既定 1.0 秒）を超えたら `warn`（長い余韻。
  好みなので不合格にはしない）。最後の窓まで鳴っている（音が鳴っている所で終わる）ときも `warn`（曲を途中で切ったかを聞いて確かめる）。
  ほかは `pass`。
- 出力（既定は動画の隣の `<動画の名前>_tail_report/`）は tail_report.json: 動画の名前と sha256、測り方、しきい値、測った数、終わりの 5 秒の窓ごとの
  dBFS。同じ動画と同じしきい値なら同じ報告になる（時刻や絶対パスは書かない）。
"""
import json
import math
from pathlib import Path

from ..audio import end as audio_end
from ..io import PipelineError, atomic_json, sha256

SCHEMA = "gate-tail-report/0.1"
REPORT_NAME = "tail_report.json"
DEFAULTS = {"max_tail_s": 1.0, "window_s": audio_end.WINDOW_S, "floor_db": audio_end.FLOOR_DB}
NOTE = "音の終わりと絵の終わりの数です。余韻の長さの良し悪しは判定しません（max_tail_s は仮の値）。"


def settings(value=None, where="tail"):
    """{"max_tail_s", "window_s", "floor_db"}。無い欄は DEFAULTS。知らない欄・範囲の外はエラー。"""
    value = {} if value is None else value
    if not isinstance(value, dict):
        raise PipelineError(f'{where} は {{"max_tail_s": 1.0}} の形にしてください（既定値なら {{}}）: {value!r}')
    unknown = sorted(set(value) - set(DEFAULTS))
    if unknown:
        raise PipelineError(f"{where} に知らない欄があります: {', '.join(unknown)}（使えるのは {'・'.join(DEFAULTS)}）")
    out = {}
    for key, default in DEFAULTS.items():
        number = value.get(key, default)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
            raise PipelineError(f"{where} の {key} は数にしてください: {number!r}")
        out[key] = float(number)
    if out["max_tail_s"] < 0 or out["window_s"] <= 0 or out["floor_db"] >= 0:
        raise PipelineError(f"{where} の値の範囲が違います（max_tail_s ≥ 0、window_s > 0、floor_db < 0）: {out}")
    return out


def measure(video, limits):
    """動画の音の終わりと絵の終わり（報告の中身）。"""
    from ..fx import probe
    video = Path(video)
    if not video.is_file():
        raise PipelineError(f"動画が見つかりません: {video}")
    info = probe.inspect(video)
    if info["audio"] is None:
        raise PipelineError(f"動画に音がありません（音の終わりを測れません）: {video.name}")
    samples = audio_end.decode(video)
    measured = audio_end.measure(samples, audio_end.RATE, limits["window_s"], limits["floor_db"])
    decoded = probe.decode(video, "v")
    fps = info["video"]["fps"]
    if not decoded["ok"] or not fps:
        raise PipelineError(f"映像を読めません（{video.name}）: {decoded['error']}")
    video_s = decoded["frames"] / fps
    after = video_s - measured["end_s"]
    reasons = []
    if after > limits["max_tail_s"] + 1e-9:
        reasons.append(f"音が切れてから絵が {after:.2f} 秒続きます（目安 {limits['max_tail_s']:g} 秒以下）")
    if measured["cut_while_sounding"]:
        reasons.append("最後の窓まで音が鳴っています（曲を途中で切っていないか確かめてください）")
    return {"schema_version": SCHEMA, "video": {"name": video.name, "sha256": sha256(video), "fps": fps, "frames": decoded["frames"],
                                                "seconds": round(video_s, 6), "audio_seconds": round(measured["seconds"], 6)},
            "method": {"rate": audio_end.RATE, "channels": "mean", "level": "rms_dbfs", "window_s": limits["window_s"],
                       "floor_db": limits["floor_db"]},
            "thresholds": {"max_tail_s": limits["max_tail_s"]},
            "measure": {"audio_end_s": measured["end_s"], "picture_after_s": round(after, 6), "peak_dbfs": measured["peak_dbfs"],
                        "last_sounding_window": measured["last_sounding_window"],
                        "digital_silence_from_s": measured["digital_silence_from_s"],
                        "cut_while_sounding": measured["cut_while_sounding"]},
            "tail": audio_end.tail_profile(samples, audio_end.RATE, limits["window_s"]),
            "status": "warn" if reasons else "pass", "reasons": reasons, "note": NOTE}


def run(video, out=None, max_tail_s=None, window_s=None, floor_db=None):
    """mv gate tail。報告を書き、(報告, 出力フォルダ) を返す。"""
    limits = settings({k: v for k, v in (("max_tail_s", max_tail_s), ("window_s", window_s), ("floor_db", floor_db)) if v is not None},
                      "--max-tail-s・--window・--floor-db")
    video = Path(video)
    report = measure(video, limits)
    out = Path(out) if out else video.with_name(video.stem + "_tail_report")
    out.mkdir(parents=True, exist_ok=True)
    atomic_json(out / REPORT_NAME, report)
    return report, out


def summary(report, out):
    m = report["measure"]
    return {"status": report["status"], "audio_end_s": m["audio_end_s"], "picture_after_s": m["picture_after_s"],
            "video_s": report["video"]["seconds"], "cut_while_sounding": m["cut_while_sounding"], "reasons": report["reasons"],
            "report": str(Path(out) / REPORT_NAME)}


def dumps(report):
    return json.dumps(report, ensure_ascii=False, indent=2)
