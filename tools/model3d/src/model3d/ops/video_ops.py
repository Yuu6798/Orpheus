"""書き出した動画からコマを取り出して、もとのコマと比べる（check.video_frames）。動画の中身が、いま描いたコマの並びであることを数で
確かめる（ffmpeg で取り出す）。"""
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path
from . import register, require

PARAMETERS = {"frames_dir", "range", "at", "count", "offset", "first", "ratio", "still", "max_mean_abs", "ffmpeg", "timeout",
              "strict", "report_only"}


def _ints(value, where, minimum=1):
    if not isinstance(value, list) or not value or not all(isinstance(v, int) and not isinstance(v, bool) and v >= minimum for v in value):
        raise ToolError(f"check.video_frames の {where} は {minimum} 以上の整数の並びにしてください: {value!r}")
    return value


def samples_of(Ts, at, count):
    """比べる T: at があればそれ、無ければ最初から最後まで count 個（両端を含み、等間隔に近い T）。"""
    if at is not None:
        missing = sorted(set(at) - set(Ts))
        if missing:
            raise ToolError(f"check.video_frames の at に、もとのコマに無い T があります: {missing[:10]}")
        return sorted(set(at))
    Ts = sorted(Ts)
    if count >= len(Ts):
        return Ts
    picks = [Ts[round(k * (len(Ts) - 1) / (count - 1))] for k in range(count)] if count > 1 else [Ts[0]]
    return sorted(set(picks))


@register("check.video_frames", inputs=("video", "frames"), outputs=("report",), cacheable=False,
          summary="書き出した動画（video）からコマを取り出して、もとのコマ（frames の model3d-frames/0.1 か、frames_dir と range）と比べる。"
                  "動画の n 番目のコマ（復号した順。0 から）を T = first + n（first の既定はもとのコマの最初の T）として、BT.709・TV の範囲から"
                  "RGB に戻し、T と T ± offset（既定 5）の各コマとの色の差の平均を出す。どれも同じ T にいちばん近く、T との差が"
                  "T ± offset との差の ratio（既定 0.5）倍以下なら合格、そうでないコマ（ずれた・古い版）があれば不合格（ずれの量も出す）。"
                  "もとのコマに動きが無く見分けられないコマは数えるだけ。比べる T は at か、両端を含む count 個（既定 8）。動画とコマは隠れた入力なので"
                  "再利用しない")
def video_frames(inputs, parameters, outputs, context):
    from .. import checks
    from ..video_frames import compare, decode, summarize, video_size
    from .assemble_ops import find_ffmpeg
    from .check_ops import _finish
    require(inputs, ("video",), "check.video_frames")
    unknown = set(parameters) - PARAMETERS
    if unknown:
        raise ToolError(f"check.video_frames の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PARAMETERS)}）")
    given, folder = inputs.get("frames"), parameters.get("frames_dir")
    if (given is None) == (folder is None):
        raise ToolError("check.video_frames のもとのコマは、入力 frames（描いたコマの一覧 model3d-frames/0.1）か、パラメーター frames_dir"
                        "（コマのフォルダ）のどちらか 1 つにしてください")
    source = Path(given) if given is not None else resolve_path(context.recipe_dir, folder)
    if folder is not None and not source.is_dir():
        raise ToolError(f"check.video_frames の frames_dir のフォルダがありません: {source}")
    files = checks.frame_files(source)
    span = parameters.get("range")
    if span is not None:
        span = _ints(span, "range")
        if len(span) != 2 or span[0] > span[1]:
            raise ToolError(f"check.video_frames の range は [最初の T, 最後の T]（最初 ≤ 最後）にしてください: {span!r}")
        files = {T: path for T, path in files.items() if span[0] <= T <= span[1]}
    if not files:
        raise ToolError(f"check.video_frames: もとのコマがありません（f<T>.png）: {source}")
    at = _ints(parameters["at"], "at") if parameters.get("at") is not None else None
    count = parameters.get("count", 8)
    offset = parameters.get("offset", 5)
    first = parameters.get("first", min(files))
    for key, value, minimum in (("count", count, 1), ("offset", offset, 1), ("first", first, 1)):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ToolError(f"check.video_frames の {key} は {minimum} 以上の整数にしてください: {value!r}")
    import math
    limits = {}
    for key, default, low, high in (("ratio", 0.5, 0.0, 1.0), ("still", 1.0, 0.0, None), ("max_mean_abs", None, 0.0, None)):
        value = parameters.get(key, default)
        if value is None:
            limits[key] = None
            continue
        if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < low
                or (high is not None and value > high) or (key == "ratio" and value == 0)):
            bound = f"0 より大きく {high:g} 以下" if key == "ratio" else "0 以上"
            raise ToolError(f"check.video_frames の {key} は {bound}の数にしてください: {value!r}")
        limits[key] = float(value)
    samples = samples_of(list(files), at, count)
    early = [T for T in samples if T < first]
    if early:
        raise ToolError(f"check.video_frames: first（{first}）より前の T は動画に無いので比べられません: {early[:10]}")
    ffmpeg = find_ffmpeg(parameters, context)
    size = video_size(ffmpeg, inputs["video"])
    timeout = parameters.get("timeout", 600)
    shown = decode(ffmpeg, inputs["video"], [T - first for T in samples], size, timeout=timeout)
    rows = compare(shown, files, samples, first, offset, limits["ratio"], limits["still"], limits["max_mean_abs"])
    report = {"check": "video_frames", "status": "done", **summarize(rows), "video": str(inputs["video"]), "frames": str(source),
              "first": first, "offset": offset, **limits, "size": list(size), "samples": samples, "rows": rows}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    summary = {k: v for k, v in report.items() if k != "rows"}
    if report["pass"] is False:
        summary["failures"] = [report["reason"]]
    return _finish(summary, {}, parameters, "動画のコマの比べ")
