"""仕上げ: 描いたコマに、コマ割り（同じ瞬間を 2〜3 のカメラで上下に割って見せる）か目の残像を掛けて、別のフォルダへ書く（render.assemble の前）。"""
import hashlib
import math
import shutil
import time
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path, sha256
from . import StepResult, number, register

PANEL_PARAMETERS = {"frames_dir", "range", "fps", "frames_b_dir", "frames_c_dir", "out_dir", "reference_height", "panels", "line"}
SOURCES = ("main", "b", "c")
PANEL_KEYS = {"range", "bands", "borders"}
BAND_KEYS = {"source", "shift"}
BORDER_KEYS = {"left", "right", "seed", "steps", "jag", "jag_x"}
LINE_KEYS = {"glow_color", "glow_width", "glow_blur", "glow_gain", "core_color", "core_width", "settle"}


def _int(value, where, minimum=None):
    if isinstance(value, bool) or not isinstance(value, int) or (minimum is not None and value < minimum):
        bound = f"{minimum} 以上の" if minimum is not None else ""
        raise ToolError(f"{where} は{bound}整数にしてください: {value!r}")
    return value


def _real(value, where, minimum=None, exclusive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ToolError(f"{where} は数にしてください: {value!r}")
    if minimum is not None and (value <= minimum if exclusive else value < minimum):
        raise ToolError(f"{where} は {minimum} {'より大きく' if exclusive else '以上に'}してください: {value!r}")
    return value


def _span(value, where):
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in value) or value[0] > value[1]):
        raise ToolError(f"{where} は [最初の T, 最後の T]（1 以上の整数、最初 ≤ 最後）にしてください: {value!r}")
    return value[0], value[1]


def _color(value, where):
    if (not isinstance(value, list) or len(value) != 3
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in value)):
        raise ToolError(f"{where} は [r, g, b]（0〜255 の整数）にしてください: {value!r}")
    return tuple(value)


def _ramp(value, where, positive):
    """[落ち着いた値, 割れた最初のコマで足す量]。"""
    if not isinstance(value, list) or len(value) != 2:
        raise ToolError(f"{where} は [落ち着いた値, 割れた最初のコマで足す量] にしてください: {value!r}")
    base = _real(value[0], f"{where}[0]", 0, exclusive=positive)
    extra = _real(value[1], f"{where}[1]", 0)
    return base, extra


def line_settings(value):
    """境の線の設定。省くと線を描かない（段の境で、そのまま切り替わる）。"""
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != LINE_KEYS:
        raise ToolError(f"line は {{{', '.join(sorted(LINE_KEYS))}}} を全部書いてください（線を描かないなら line を省く）: {value!r}")
    return {"glow_color": _color(value["glow_color"], "line.glow_color"),
            "glow_width": _ramp(value["glow_width"], "line.glow_width", True),
            "glow_blur": _ramp(value["glow_blur"], "line.glow_blur", True),
            "glow_gain": _ramp(value["glow_gain"], "line.glow_gain", False),
            "core_color": _color(value["core_color"], "line.core_color"),
            "core_width": _ramp(value["core_width"], "line.core_width", True),
            "settle": _int(value["settle"], "line.settle", 1)}


def panel_settings(value, index):
    where = f"panels[{index}]"
    if not isinstance(value, dict):
        raise ToolError(f"{where} は {{\"range\", \"bands\", \"borders\"}} にしてください: {value!r}")
    unknown = set(value) - PANEL_KEYS
    if unknown:
        raise ToolError(f"{where} の知らない項目: {sorted(unknown)}（使えるのは {sorted(PANEL_KEYS)}）")
    first, last = _span(value.get("range"), f"{where}.range")
    bands = value.get("bands")
    if not isinstance(bands, list) or len(bands) < 2:
        raise ToolError(f"{where}.bands は、上から順の段の並び（2 つ以上）にしてください: {bands!r}")
    out_bands = []
    for j, band in enumerate(bands):
        if not isinstance(band, dict) or set(band) - BAND_KEYS or "source" not in band:
            raise ToolError(f"{where}.bands[{j}] は {{\"source\": \"main\" か \"b\" か \"c\", \"shift\": 下へずらす量}} にしてください: {band!r}")
        if band["source"] not in SOURCES:
            raise ToolError(f"{where}.bands[{j}].source は {' か '.join(SOURCES)} にしてください: {band['source']!r}")
        out_bands.append({"source": band["source"], "shift": float(_real(band.get("shift", 0.0), f"{where}.bands[{j}].shift"))})
    borders = value.get("borders")
    if not isinstance(borders, list) or len(borders) != len(bands) - 1:
        raise ToolError(f"{where}.borders は、上から順の境の並び（段の数 − 1 = {len(bands) - 1} 個）にしてください: {borders!r}")
    out_borders = []
    for j, border in enumerate(borders):
        at = f"{where}.borders[{j}]"
        if not isinstance(border, dict):
            raise ToolError(f"{at} は {{\"left\", \"right\", \"seed\", \"steps\", \"jag\"}} にしてください: {border!r}")
        unknown = set(border) - BORDER_KEYS
        missing = sorted(BORDER_KEYS - {"jag_x"} - set(border))
        if unknown or missing:
            raise ToolError(f"{at} の項目が違います（知らない項目: {sorted(unknown)}、足りない項目: {missing}）")
        out_borders.append({"left": float(_real(border["left"], f"{at}.left")), "right": float(_real(border["right"], f"{at}.right")),
                            "seed": _int(border["seed"], f"{at}.seed"), "steps": _int(border["steps"], f"{at}.steps", 1),
                            "jag": float(_real(border["jag"], f"{at}.jag", 0)),
                            "jag_x": float(_real(border.get("jag_x", 0.0), f"{at}.jag_x", 0))})
    return {"range": [first, last], "bands": out_bands, "borders": out_borders}


def main_frames(inputs, parameters, context, operation):
    """入力 frames（model3d-frames/0.1）か、パラメーター frames_dir と range の T。render.title と同じ決まり。"""
    from .. import contracts
    from ..blender_adapter.stage_render import frame_name
    from .assemble_ops import consecutive
    manifest = contracts.load("frames", inputs["frames"]) if inputs.get("frames") is not None else None
    span = parameters.get("range")
    if manifest is not None:
        if span is not None:
            raise ToolError(f"{operation}: frames と range は一緒に使えません（frames があれば、その一覧のコマに掛けます）")
        if manifest["missing"]:
            raise ToolError(f"{operation}: 描いたコマの一覧に、描いていないコマがあります（T: {manifest['missing'][:10]}）。全部描いてから掛けてください")
        if not manifest["frames"]:
            raise ToolError(f"{operation}: 描いたコマの一覧が空です")
        consecutive([fr["T"] for fr in manifest["frames"]], "描いたコマの一覧")
        directory = (resolve_path(context.recipe_dir, parameters["frames_dir"]) if parameters.get("frames_dir")
                     else Path(manifest["directory"]))
        return Path(directory), [(fr["T"], fr["f"], Path(directory) / fr["file"], fr["sha256"]) for fr in manifest["frames"]], manifest
    if not parameters.get("frames_dir") or span is None:
        raise ToolError(f"{operation} には、入力 frames か、パラメーター frames_dir と range（[最初の T, 最後の T]）が要ります")
    first, last = _span(span, "range")
    directory = resolve_path(context.recipe_dir, parameters["frames_dir"])
    return Path(directory), [(T, T, Path(directory) / frame_name(T), None) for T in range(first, last + 1)], None


def other_frames(inputs, parameters, context, name, operation):
    """別のカメラのコマ: 入力 frames_<name>（model3d-frames/0.1。描いていない T（missing）があってよい）か、パラメーター
    frames_<name>_dir（f<T>.png のフォルダ。無い T があってよい）。{T: (ファイル, 一覧の sha256 か None)} とフォルダ。どちらも無ければ None。"""
    from .. import checks, contracts
    given, folder = inputs.get(f"frames_{name}"), parameters.get(f"frames_{name}_dir")
    if given is not None and folder is not None:
        raise ToolError(f"{operation}: 入力 frames_{name} とパラメーター frames_{name}_dir は一緒に使えません")
    if given is not None:
        manifest = contracts.load("frames", given)
        directory = Path(manifest["directory"])
        return {fr["T"]: (directory / fr["file"], fr["sha256"]) for fr in manifest["frames"]}, directory
    if folder is not None:
        directory = resolve_path(context.recipe_dir, folder)
        if not Path(directory).is_dir():
            raise ToolError(f"{operation} の frames_{name}_dir のフォルダがありません: {directory}")
        return {T: (path, None) for T, path in checks.frame_files(directory).items()}, Path(directory)
    return None, None


def _refuse(image, path, T, operation, size, what="コマ"):
    """透けた画素のあるコマと、大きさの違うコマは止める（書くコマは RGB なので透けが消える。写すだけのコマも同じ）。"""
    if image.mode in ("RGBA", "LA", "PA") or "transparency" in image.info:
        if image.convert("RGBA").getchannel("A").getextrema()[0] < 255:
            raise ToolError(f"{operation}: {what}に透けた画素があります（T {T}: {path}）。透けは残せないので、先に土台へ重ねてから掛けてください")
    if size is not None and image.size != tuple(size):
        raise ToolError(f"{operation}: {what}の大きさがそろっていません（T {T}: {image.size[0]}×{image.size[1]}、最初のコマ: {size[0]}×{size[1]}）")


def read_rgb(path, T, operation, size=None, what="コマ"):
    """コマを uint8 の RGB（高さ × 幅 × 3）で読む。透けた画素のあるコマと、size（幅, 高さ）と違うコマは止める。"""
    import numpy as np
    from PIL import Image
    with Image.open(path) as image:
        _refuse(image, path, T, operation, size, what)
        return np.asarray(image.convert("RGB"))


def check_copy(path, T, operation, size):
    """そのまま写すコマも、読むコマと同じく、透けと大きさを確かめる。"""
    from PIL import Image
    with Image.open(path) as image:
        _refuse(image, path, T, operation, size)


def checked(path, expected, T, what):
    """一覧の sha256 と今のファイルが同じか（描いた後にコマが変わっていないか）。今の sha256 を返す。"""
    h = sha256(path)
    if expected is not None and h != expected:
        raise ToolError(f"{what}の一覧と、今のファイルが違います（T: {T}）。描き直したなら一覧も作り直してください")
    return h


def write_frames(outputs, out_dir, fps, size, listing):
    from .. import contracts
    written = {"schema_version": "model3d-frames/0.1", "directory": str(Path(out_dir).resolve()), "fps": fps,
               "resolution": list(size), "frames": listing, "missing": []}
    atomic_json(outputs["frames"], contracts.validate("frames", written))


def at_fresh(line, fresh, s):
    """割れてからの強さ fresh（0〜1）での、境の線の画素の太さ・ぼかし・強さ（台本と同じ丸め: 太さは四捨五入、にじみは 2 以上、芯は 1 以上）。"""
    value = lambda key: line[key][0] + line[key][1] * fresh
    return {"glow_width": max(2, int(round(value("glow_width") * s))), "glow_sigma": value("glow_blur") * s,
            "glow_gain": value("glow_gain"), "core_width": max(1, int(round(value("core_width") * s)))}


@register("render.panels", inputs=("frames", "frames_b", "frames_c"), outputs=("frames", "report"), cacheable=False,
          summary="コマ割り: 描いたコマ（frames の model3d-frames/0.1 か、frames_dir と range の T）の決めた区間（panels）で、"
                  "同じ T の別のカメラのコマ（frames_b・frames_c の一覧か、frames_b_dir・frames_c_dir のフォルダ）と上下に割って、別のフォルダへ書く。"
                  "段（bands）ごとに、どのカメラのコマを上下にどれだけずらして入れるか。境（borders）は折れ線（左右の端の高さ・折れの数・"
                  "折れの量・種。同じ種なら同じ折れ線）。境の線（line）は色のにじみと芯で、割れた最初のコマで太く明るく、settle コマで落ち着く。"
                  "大きさは基準の高さ（reference_height。既定 1 で、画面の高さの比）での数。別のカメラのコマが無い T は、割らずに主のコマを"
                  "そのまま写して報告に書く。区間の外のコマもそのまま写す。透けた画素のあるコマは止める。frames は書いたコマの一覧"
                  "（model3d-frames/0.1。render.assemble・mv dance finish の入力になる）。コマのフォルダは隠れた入力なので再利用しない（毎回実行）")
def render_panels(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image

    from .. import panels as P
    operation = "render.panels"
    unknown = set(parameters) - PANEL_PARAMETERS
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(PANEL_PARAMETERS)}）")
    if "frames" not in outputs:
        raise ToolError(f"{operation} には outputs の frames（書いたコマの一覧）が要ります")
    source_dir, frames, manifest = main_frames(inputs, parameters, context, operation)
    fps = parameters.get("fps") if parameters.get("fps") is not None else (manifest or {}).get("fps")
    if fps is None:
        raise ToolError(f"{operation} には fps が要ります（パラメーター fps か、描いたコマの一覧の fps）")
    fps = float(number({"fps": fps}, "fps", minimum=0, exclusive=True))
    reference = float(number(parameters, "reference_height", 1.0, minimum=0, exclusive=True))
    items = parameters.get("panels")
    if not isinstance(items, list) or not items:
        raise ToolError(f"{operation} には panels（割る区間の並び。1 つ以上）が要ります")
    settings = [panel_settings(item, i) for i, item in enumerate(items)]
    line = line_settings(parameters.get("line"))
    Ts = {T for T, *_ in frames}
    order = sorted(range(len(settings)), key=lambda i: settings[i]["range"][0])
    for a, b in zip(order, order[1:]):
        if settings[b]["range"][0] <= settings[a]["range"][1]:
            raise ToolError(f"panels[{a}] と panels[{b}] の range が重なっています")
    for i, panel in enumerate(settings):
        first, last = panel["range"]
        outside = [T for T in range(first, last + 1) if T not in Ts]
        if outside:
            raise ToolError(f"panels[{i}].range の T が、主のコマにありません: {outside[:10]}")
    others, other_dirs = {}, {}
    for name in ("b", "c"):
        others[name], other_dirs[name] = other_frames(inputs, parameters, context, name, operation)
    for i, panel in enumerate(settings):
        for band in panel["bands"]:
            if band["source"] != "main" and others[band["source"]] is None:
                raise ToolError(f"panels[{i}] の段が {band['source']} のカメラのコマを使いますが、入力 frames_{band['source']} か"
                                f"パラメーター frames_{band['source']}_dir がありません")
    out_dir = (resolve_path(context.recipe_dir, parameters["out_dir"]) if parameters.get("out_dir")
               else Path(context.step_dir) / "frames")
    for name, directory in [("主のコマ", source_dir)] + [(f"{k} のカメラのコマ", d) for k, d in other_dirs.items() if d is not None]:
        if Path(out_dir).resolve() == Path(directory).resolve():
            raise ToolError(f"out_dir が{name}のフォルダと同じです（元のコマを書き換えません）: {out_dir}")
    missing = [T for T, _f, file, _h in frames if not file.is_file()]
    if missing:
        raise ToolError(f"{operation}: 主のコマがありません（{len(missing)} コマ。最初の T: {missing[:10]}）: {source_dir}")
    with Image.open(frames[0][2]) as image:
        W, H = image.size
    s = H / reference
    by_T = {}
    for i, panel in enumerate(settings):
        lines = [P.jagged(W, H, b["left"], b["right"], b["seed"], b["steps"], b["jag"], b["jag_x"], reference) for b in panel["borders"]]
        for j, points in enumerate(lines):
            if any(q[0] <= p[0] for p, q in zip(points, points[1:])):
                raise ToolError(f"panels[{i}].borders[{j}] の折れ線の点が、左から右へ並びません（jag_x が折れの間より大きい）")
        panel["lines"] = lines
        for T in range(panel["range"][0], panel["range"][1] + 1):
            by_T[T] = i
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    digest, listing, split, skipped, fresh_rows = hashlib.sha256(), [], {i: [] for i in range(len(settings))}, [], []
    for T, f, source, expected in frames:
        h = checked(source, expected, T, "描いたコマ")
        digest.update(f"{T}:{h}\n".encode())
        target = Path(out_dir) / source.name
        i = by_T.get(T)
        absent = []
        if i is not None:
            panel = settings[i]
            needed = sorted({b["source"] for b in panel["bands"]} - {"main"})
            absent = [name for name in needed if T not in others[name] or not others[name][T][0].is_file()]
        if i is None or absent:                       # 区間の外か、別のカメラのコマが無い T は、そのまま写す
            check_copy(source, T, operation, (W, H))
            shutil.copyfile(source, target)
            listing.append({"T": T, "f": f, "file": target.name, "sha256": h, "bytes": target.stat().st_size})
            if absent:
                skipped.append({"T": T, "panel": i, "missing": absent})
            continue
        images = {"main": read_rgb(source, T, operation, (W, H))}
        for name in needed:
            path, listed = others[name][T]
            checked(path, listed, T, f"{name} のカメラのコマ")
            images[name] = read_rgb(path, T, operation, (W, H), f"{name} のカメラのコマ")
        picture = P.compose([images[b["source"]] for b in panel["bands"]], [b["shift"] * s for b in panel["bands"]], panel["lines"])
        if line is not None:
            start = split[i][0] if split[i] else T     # 割れた最初のコマから数える（区間の頭の T をとばしても、最初に割れたコマで太い）
            fresh = max(0.0, 1.0 - (T - start) / line["settle"])
            px = at_fresh(line, fresh, s)
            picture = P.gutter(picture, panel["lines"], line["glow_color"], px["glow_width"], px["glow_sigma"], px["glow_gain"],
                               line["core_color"], px["core_width"])
            if fresh > 0:
                fresh_rows.append({"T": T, "fresh": round(fresh, 6), **px})
        Image.fromarray(np.ascontiguousarray(picture)).save(target)
        split[i].append(T)
        listing.append({"T": T, "f": f, "file": target.name, "sha256": sha256(target), "bytes": target.stat().st_size})
    write_frames(outputs, out_dir, fps, (W, H), listing)
    elapsed = round(time.monotonic() - began, 2)
    count = sum(len(v) for v in split.values())
    report = {"schema_version": "model3d-panels/0.1",
              "source": {"directory": str(source_dir.resolve()), "first": frames[0][0], "last": frames[-1][0], "count": len(frames),
                         "sha256": digest.hexdigest()},
              "others": {name: ({"directory": str(Path(d).resolve()), "frames": len(others[name])} if d is not None else None)
                         for name, d in other_dirs.items()},
              "out_dir": str(Path(out_dir).resolve()), "resolution": [W, H], "fps": fps, "reference_height": reference, "scale": s,
              "panels": [{"range": p["range"], "bands": [{"source": b["source"], "shift_px": b["shift"] * s} for b in p["bands"]],
                          "borders": [{**b, "points": [list(q) for q in points]} for b, points in zip(p["borders"], p["lines"])],
                          "split": split[i], "skipped": [row["T"] for row in skipped if row["panel"] == i]}
                         for i, p in enumerate(settings)],
              "line": ({k: (list(v) if isinstance(v, tuple) else v) for k, v in line.items()} if line is not None else None),
              "fresh": fresh_rows, "split": count, "skipped": skipped, "copied": len(frames) - count, "seconds": elapsed}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "panels", "frames": len(frames), "split": count, "skipped": len(skipped), "copied": len(frames) - count,
                   "seconds": elapsed}
    unresolved = ([f"別のカメラのコマが無いので割らなかった T: {[row['T'] for row in skipped][:20]}"] if skipped else [])
    return StepResult(observations=[observation], unresolved=unresolved)


AFTERIMAGE_PARAMETERS = {"frames_dir", "range", "fps", "out_dir", "reference_height", "source", "span", "amount", "decay_frames",
                         "threshold", "blur", "color", "min_amount"}


@register("render.afterimage", inputs=("frames",), outputs=("frames", "report"), cacheable=False,
          summary="目の残像: 描いたコマ（frames の model3d-frames/0.1 か、frames_dir と range の T）のうち、span の T に、source の T の"
                  "明るいコマ（暗くなる直前など）をぼかして、明るい所（明るさ − threshold）だけを color（0〜1 の RGB。補色など）でうすく足して、"
                  "別のフォルダへ書く。濃さは span の最初の T で amount、decay_frames コマごとに 1/e に薄れ、min_amount 以下のコマは足さない。"
                  "ぼかし（blur）は基準の高さ（reference_height。既定 1 で画面の高さの比）での sigma。ほかのコマはそのまま写す。"
                  "2D の側の afterimage_echo（人物の輪郭の残像）とは別のもの。frames は書いたコマの一覧（render.assemble の入力になる）。"
                  "コマのフォルダは隠れた入力なので再利用しない（毎回実行）")
def render_afterimage(inputs, parameters, outputs, context):
    import numpy as np
    from PIL import Image

    from .. import afterimage as A
    operation = "render.afterimage"
    unknown = set(parameters) - AFTERIMAGE_PARAMETERS
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(AFTERIMAGE_PARAMETERS)}）")
    if "frames" not in outputs:
        raise ToolError(f"{operation} には outputs の frames（書いたコマの一覧）が要ります")
    source_dir, frames, manifest = main_frames(inputs, parameters, context, operation)
    fps = parameters.get("fps") if parameters.get("fps") is not None else (manifest or {}).get("fps")
    if fps is None:
        raise ToolError(f"{operation} には fps が要ります（パラメーター fps か、描いたコマの一覧の fps）")
    fps = float(number({"fps": fps}, "fps", minimum=0, exclusive=True))
    reference = float(number(parameters, "reference_height", 1.0, minimum=0, exclusive=True))
    source_T = _int(parameters.get("source"), "source（明るいコマの T）", 1)
    first, last = _span(parameters.get("span"), "span")
    amount = float(_real(parameters.get("amount"), "amount", 0, exclusive=True))
    decay = float(_real(parameters.get("decay_frames"), "decay_frames", 0, exclusive=True))
    threshold = float(_real(parameters.get("threshold"), "threshold", 0))
    if threshold >= 1:
        raise ToolError(f"threshold は 0 以上 1 より小さい明るさにしてください: {threshold!r}")
    sigma = float(_real(parameters.get("blur"), "blur", 0, exclusive=True))
    color = parameters.get("color")
    if (not isinstance(color, list) or len(color) != 3
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in color)):
        raise ToolError(f"color は [r, g, b]（0〜1 の数）にしてください: {color!r}")
    min_amount = float(_real(parameters.get("min_amount", 0.0), "min_amount", 0))
    by_T = {T: (f, path, h) for T, f, path, h in frames}
    if first <= source_T:
        raise ToolError(f"span（{first}〜{last}）は、source の T {source_T} より後から始めてください（明るいコマの後に残る像）")
    if source_T not in by_T:
        raise ToolError(f"source の T {source_T} が、描いたコマにありません")
    if not any(first <= T <= last for T in by_T):
        raise ToolError(f"span の T（{first}〜{last}）が、描いたコマに 1 つもありません")
    out_dir = (resolve_path(context.recipe_dir, parameters["out_dir"]) if parameters.get("out_dir")
               else Path(context.step_dir) / "frames")
    if Path(out_dir).resolve() == source_dir.resolve():
        raise ToolError(f"out_dir が元のコマのフォルダと同じです（元のコマを書き換えません）: {out_dir}")
    missing = [T for T, _f, file, _h in frames if not file.is_file()]
    if missing:
        raise ToolError(f"{operation}: コマがありません（{len(missing)} コマ。最初の T: {missing[:10]}）: {source_dir}")
    _f, source_path, source_expected = by_T[source_T]
    source_sha = checked(source_path, source_expected, source_T, "描いたコマ")
    bright = read_rgb(source_path, source_T, operation)
    H, W = bright.shape[:2]
    s = H / reference
    picture = A.ghost(bright, sigma * s, threshold, [float(c) for c in color])
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    digest, listing, applied = hashlib.sha256(), [], []
    for T, f, path, expected in frames:
        h = checked(path, expected, T, "描いたコマ")
        digest.update(f"{T}:{h}\n".encode())
        target = Path(out_dir) / path.name
        strength = A.strength(T, first, amount, decay) if first <= T <= last else 0.0
        if strength <= min_amount:                    # 区間の外と、薄すぎるコマは、そのまま写す
            check_copy(path, T, operation, (W, H))
            shutil.copyfile(path, target)
            listing.append({"T": T, "f": f, "file": target.name, "sha256": h, "bytes": target.stat().st_size})
            continue
        image = read_rgb(path, T, operation, (W, H))
        Image.fromarray(np.ascontiguousarray(A.add(image, picture, strength))).save(target)
        applied.append({"T": T, "amount": round(strength, 6)})
        listing.append({"T": T, "f": f, "file": target.name, "sha256": sha256(target), "bytes": target.stat().st_size})
    write_frames(outputs, out_dir, fps, (W, H), listing)
    elapsed = round(time.monotonic() - began, 2)
    report = {"schema_version": "model3d-afterimage/0.1",
              "source": {"directory": str(source_dir.resolve()), "first": frames[0][0], "last": frames[-1][0], "count": len(frames),
                         "sha256": digest.hexdigest()},
              "bright": {"T": source_T, "sha256": source_sha},
              "out_dir": str(Path(out_dir).resolve()), "resolution": [W, H], "fps": fps, "reference_height": reference, "scale": s,
              "span": [first, last], "amount": amount, "decay_frames": decay, "threshold": threshold, "blur_px": sigma * s,
              "color": [float(c) for c in color], "min_amount": min_amount,
              "lit_pixels": int((picture.max(axis=2) > 0).sum()), "applied": applied, "copied": len(frames) - len(applied),
              "seconds": elapsed}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "afterimage", "frames": len(frames), "applied": len(applied),
                   "first_applied": applied[0]["T"] if applied else None, "last_applied": applied[-1]["T"] if applied else None,
                   "copied": len(frames) - len(applied), "seconds": elapsed}
    return StepResult(observations=[observation])
