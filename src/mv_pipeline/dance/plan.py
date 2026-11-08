"""解決した時刻の一覧（`mv dance plan`）。表で出すだけで、判定はしない。cv2 を使わない。"""
from . import SCHEMA_VERSION
from . import spec as dancespec


def _seconds(value):
    return "" if value is None else f"{value:.3f}"


def _table(title, headers, rows):
    if not rows:
        return [f"## {title}", "（なし）", ""]
    cells = [headers] + [[str(cell) for cell in row] for row in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    lines = ["  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip() for row in cells]
    return [f"## {title}", lines[0], "  ".join("-" * width for width in widths)] + lines[1:] + [""]


def _beat_of(t, data):
    """t に最も近い拍の番号と、そこからのずれ（格子が無ければ空）。"""
    if data["beats"] is None:
        return ""
    index, nearest = min(enumerate(data["beats"]["beats"]), key=lambda item: abs(item[1] - t))
    return f"{index}{t - nearest:+.3f}"


def _clips(data):
    clips = data["performer"]["clips"]
    return _table("clips", ["#", "start", "end", "beat", "kind", "in", "exempt", "path"], [
        [i, _seconds(c["start"]), _seconds(c["end"]), _beat_of(c["start"], data), c["kind"],
         c["transition_in"]["type"] + (f" {c['transition_in']['frames']}f" if c["transition_in"]["frames"] else ""),
         "yes" if c["beat_exempt"] else "", c["path"]] for i, c in enumerate(clips)])


def _retime(data):
    rows = []
    for i, clip in enumerate(data["performer"]["clips"]):
        for segment in clip["retime"]:
            a, b = segment["source"]
            rows.append([i, _seconds(segment["from"]), _seconds(segment["to"]), _beat_of(segment["from"], data),
                         f"{a:g}→{b:g} {segment['unit']}" + ("（止め）" if a == b else ""), clip["path"]])
    return _table("retime", ["#", "from", "to", "beat", "source", "path"], rows)


def _replace(data):
    def target(value):
        if isinstance(value, dict):
            return f"画像 {value['image']}"
        return f"コマ {value}" if isinstance(value, int) else value
    return _table("replace_frames", ["path", "frames", "with"], [
        [run["path"], f"[{run['frames'][0]}, {run['frames'][1]})", target(run["with"])] for run in data["performer"]["replace_frames"]])


def _seams(data):
    clips = data["performer"]["clips"]
    joins = [[i, _seconds(right["start"]), _beat_of(right["start"], data), right["transition_in"]["type"], "yes" if right["beat_exempt"] else ""]
             for i, (left, right) in enumerate(zip(clips, clips[1:])) if abs(right["start"] - left["end"]) < 1e-9]
    return _table("つなぎ", ["from", "t", "beat", "kind", "exempt"], joins)


def _accents(data):
    return _table("camera.accents", ["回数", "最初", "最後", "zoom", "attack", "decay", "shake"], [
        [len(a["times"]), _seconds(a["times"][0] if a["times"] else None), _seconds(a["times"][-1] if a["times"] else None),
         a["zoom"], a["attack_s"], a["decay_s"], a["shake_px"]] for a in data["camera"]["accents"]])


def _camera(data):
    return _table("camera.seams", ["t", "beat", "pre", "post", "zoom", "rotate", "rotate_post", "blur"], [
        [_seconds(s["t"]), _beat_of(s["t"], data), s["span_s"] if s["pre_s"] is None else s["pre_s"],
         s["span_s"] if s["post_s"] is None else s["post_s"], s["zoom"], s["rotate_deg"],
         "" if s["rotate_post_deg"] is None else s["rotate_post_deg"], "" if s["blur"] is None else s["blur"]["samples"]]
        for s in data["camera"]["seams"]])


def _flashes(data):
    return _table("flashes", ["t", "beat", "strength", "decay", "mode", "exempt"], [
        [_seconds(f["t"]), _beat_of(f["t"], data), f["strength"], f["decay_s"], f["mode"], "yes" if f["beat_exempt"] else ""]
        for f in data["flashes"]])


def _lyrics(data):
    lines = sorted(data["lyrics"]["lines"], key=lambda line: (line["layer"], line["start"]))
    return _table("lyrics", ["layer", "start", "end", "beat", "text"], [
        [line["layer"], _seconds(line["start"]), _seconds(line["end"]), _beat_of(line["start"], data), line["text"]] for line in lines])


def build(data):
    current = data["source_schema"] == SCHEMA_VERSION      # 0.3 の欄（丸め・retime・replace_frames）は 0.3 の spec だけに出す
    head = [f"# {data['source_schema']}  {data['output']['width']}x{data['output']['height']} {data['output']['fps']:g}fps "
            f"{data['duration_s']:g} 秒" + (f"  元のコマの丸め {data['performer']['frame_rounding']}" if current else ""), ""]
    frames = _retime(data) + _replace(data) if current else []
    return "\n".join(head + _clips(data) + frames + _seams(data) + _accents(data) + _camera(data) + _flashes(data) + _lyrics(data)).rstrip()


def text(spec_path):
    return build(dancespec.load_spec(spec_path).data)
