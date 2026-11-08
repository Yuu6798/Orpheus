"""透過レイヤーを重ねる式と、レイヤーの設定（`mv overlay --layers` と fx-shot の `overlays` が共有する部品）。

    entry_options(item, label)                  設定の 1 要素（fx_overlay_layers.schema.json の layer）を検証して、既定値を埋めた設定にする
    apply_layer(f, rgba, options, opacity, scale)  表示用の値（float32 の 0..255）の f に rgba を style つきで重ねる
    opacity(kind, options, t)                   時刻 t の不透明度（1 枚の画像は start_s ≤ t < end_s の間だけ。出入りは smoothstep）

重ねの式は実制作の 2 つの重ねの式（歌詞の連番に掛けた式・最後のカードに掛けた式）と同じ式・同じ順で、既定値も実制作の値。
後光 → 影 → 光彩 → 本体（f × (1 − α) + 色 × α。色は tint × 輝度、tint が無ければレイヤーの色）。どの項にも不透明度を掛ける。
長さ（sigma_px・dilate_px）は出力の短い辺を 1080 としたときの画素で、scale にその倍率を渡す。
"""
import json
import re
from pathlib import Path

import jsonschema
import numpy as np
from PIL import Image

from ..io import PipelineError

LAYERS_VERSION = "fx-overlay-layers/0.1"
LAYERS_SCHEMA_PATH = Path(__file__).with_name("fx_overlay_layers.schema.json")
PLAIN = {"first_frame": None, "missing": "error", "alpha": "straight", "resize": False, "resize_space": "straight", "opacity": 1.0,
         "style": None}
STYLE_DEFAULTS = {"shadow": {"amount": 0.45, "sigma_px": 4.0, "gain": 1.5}, "glow": {"amount": 0.9, "sigma_px": 14.0},
                  "backdrop": {"amount": 0.55, "dilate_px": 5, "iterations": 3, "sigma_px": 26.0, "gain": 1.6}}
TIMED = ("start_s", "end_s", "fade_in_s", "fade_out_s")


def layers_schema():
    return json.loads(LAYERS_SCHEMA_PATH.read_text(encoding="utf-8"))


def validate_entries(items, where):
    """設定の要素の列を、fx_overlay_layers.schema.json の layer の形で検証する（where は場所の名前。例: "overlays"）。"""
    schema = layers_schema()
    validator = jsonschema.Draft202012Validator({"type": "array", "items": {"$ref": "#/$defs/layer"}, "$defs": schema["$defs"]})
    errors = sorted(validator.iter_errors(items), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        raise PipelineError(f"{where} の設定が正しくありません:\n" + "\n".join(
            f"  {where}.{'.'.join(str(p) for p in e.absolute_path)}: {e.message[:200]}" for e in errors[:8]))


def entry_options(item, label):
    """検証済みの設定の 1 要素 → 既定値を埋めた設定（image なら image=True と時刻の項目つき）。組み合わせの誤りは止める。"""
    item = {k: v for k, v in item.items() if not k.startswith("x_")}
    if "image" in item:
        if item["end_s"] <= item["start_s"]:
            raise PipelineError(f"{label}: end_s は start_s より後にしてください（{item['start_s']} → {item['end_s']}）")
        bad = [k for k in ("first_frame", "missing") if k in item]
    else:
        bad = [k for k in TIMED if k in item]
    if bad:
        raise PipelineError(f"{label}: {bad} は {'path（連番・動画）' if 'image' in item else 'image（1 枚の画像）'} のレイヤーでは使えません")
    if "missing" in item and "first_frame" not in item:
        raise PipelineError(f"{label}: missing は first_frame と一緒に使います")
    options = {**PLAIN, **item, "opacity": float(item.get("opacity", 1.0)), "style": normalize_style(item.get("style"))}
    if "image" in item:
        options.update(image=True, fade_in_s=float(item.get("fade_in_s", 0.0)), fade_out_s=float(item.get("fade_out_s", 0.0)),
                       start_s=float(item["start_s"]), end_s=float(item["end_s"]))
    return options


def normalize_style(style):
    """style を既定値で埋めた形にする（無ければ None）。正規化した形をもう一度通しても同じ形になる。"""
    if not style:
        return None
    out = {"tint": None if style.get("tint") is None else [float(v) for v in style["tint"]]}
    for name, defaults in STYLE_DEFAULTS.items():
        given = style.get(name)
        out[name] = None if given is None else {**defaults, **{k: v for k, v in given.items() if not k.startswith("x_")}}
    return out


def frame_number(path):
    """連番のファイル名の最後の数字（first_frame で引くときの番号）。"""
    digits = re.findall(r"\d+", Path(path).stem)
    if not digits:
        raise PipelineError(f"first_frame を使う連番は、ファイル名の最後に番号が要ります: {Path(path).name}")
    return int(digits[-1])


def has_alpha(image):
    return "A" in image.getbands() or (image.mode == "P" and "transparency" in image.info)


def fit(rgba, size, options=None):
    """レイヤーを size へ（縮めるなら INTER_AREA、広げるなら INTER_LINEAR。実制作の重ねと同じ）。同じ大きさならそのまま。

    resize_space の既定の straight は、色とアルファを別々に補間する（実制作の重ねと同じ。透明な所の色が黒だと、縁が暗くなる）。
    premultiplied は、straight のレイヤーの色にアルファを掛けてから補間して戻す（縁が暗くならない）。alpha: premultiplied の
    レイヤーは、もともと色にアルファが掛かっているので、どちらでもそのまま補間する。"""
    if (rgba.shape[1], rgba.shape[0]) == tuple(size):
        return rgba
    import cv2
    interpolation = cv2.INTER_AREA if rgba.shape[1] > size[0] else cv2.INTER_LINEAR
    options = options or PLAIN
    if options.get("resize_space", "straight") != "premultiplied" or options.get("alpha") == "premultiplied":
        return cv2.resize(rgba, tuple(size), interpolation=interpolation)
    alpha = rgba[..., 3:4].astype(np.float32)
    weighted = np.concatenate([rgba[..., :3].astype(np.float32) * (alpha / 255.0), alpha], axis=2)
    out = cv2.resize(weighted, tuple(size), interpolation=interpolation)
    covered = out[..., 3:4]
    rgb = np.where(covered > 0, out[..., :3] * 255.0 / np.maximum(covered, np.float32(1e-6)), 0.0)
    return np.clip(np.concatenate([rgb, covered], axis=2) + 0.5, 0, 255).astype(np.uint8)


def check_fit(found, size, options, where, target="base"):
    """寸法 found のレイヤー（か連番のコマ）を size に重ねられるか。違う寸法は、resize: true で縦横比が 1% 以内のときだけ
    縮尺する（それ以外は止める）。target は size の名前（mv overlay は土台、spec の overlays は spec の出力）。"""
    if list(found) == list(size):
        return
    if not options["resize"]:
        raise PipelineError(f"寸法が合いません: {target} {size[0]}x{size[1]} / {where} {found[0]}x{found[1]}"
                            f"（縮尺して重ねるなら resize: true）")
    if abs(found[0] * size[1] - size[0] * found[1]) > 0.01 * size[0] * found[1]:
        raise PipelineError(f"縦横比が合いません: {target} {size[0]}x{size[1]} / {where} {found[0]}x{found[1]}")


def read_rgba(path, size, options=None, where=None, expected=None, target="base"):
    """画像を uint8 RGBA で読み、size へ合わせる（options の resize_space で補間）。where（連番のコマの名前）を渡すと、
    RGBA に直して縮尺する前に、このコマのアルファと、寸法を expected（省けば size）に対して確かめる（入口は連番の 1 枚目しか
    見ないので、途中のコマの違いを黙って通さない。アルファの無いコマは RGBA に直すと不透明になり、下の絵を覆う）。"""
    with Image.open(path) as image:
        if where is not None:
            if not has_alpha(image):
                raise PipelineError(f"{where} にアルファがありません（{image.mode}）。透過の PNG にしてください"
                                    f"（アルファの無いコマは不透明になり、下の絵を覆います）")
            check_fit(image.size, expected or size, options, where, target)
        return fit(np.asarray(image.convert("RGBA"), dtype=np.uint8), size, options)


def opacity(kind, options, t):
    """時刻 t の不透明度。1 枚の画像（kind image）は start_s ≤ t < end_s の間だけで、出入りは smoothstep。"""
    if kind != "image":
        return options["opacity"]
    if not options["start_s"] <= t < options["end_s"]:
        return 0.0
    from .timeline import ease
    value = 1.0
    if options["fade_in_s"] > 0:
        value = min(value, float(ease("in_out", (t - options["start_s"]) / options["fade_in_s"])))
    if options["fade_out_s"] > 0:
        value = min(value, float(ease("in_out", (options["end_s"] - t) / options["fade_out_s"])))
    return options["opacity"] * value


def apply_layer(f, rgba, options, opacity=1.0, scale=1.0):
    """表示用の値（float32 の 0..255）の f に、rgba（uint8）を style つきで重ねた f を返す（実制作の重ねの式と同じ式・同じ順）。

    後光（アルファを dilate_px の正方形で iterations 回太らせて σ でぼかし、gain 倍して 0..1 に切ったものの分だけ暗くする）→
    影（σ でぼかしたアルファ × gain の分だけ暗くする）→ 光彩（color × ぼかしたアルファ × amount を足す）→
    本体（f × (1 − α) + 色 × α。色は tint × 輝度、tint が無ければレイヤーの色）。どの項にも opacity を掛ける。scale は長さの倍率。
    アルファがどこも 0 なら f をそのまま返す。"""
    import cv2
    alpha = rgba[..., 3:4].astype(np.float32) / 255.0
    if alpha.max() <= 0:
        return f
    style = options["style"] or {}
    if options["alpha"] == "premultiplied":
        rgb = np.clip(rgba[..., :3].astype(np.float32) / np.maximum(alpha, np.float32(1e-6)), 0, 255).astype(np.float32)
        bgr = rgb[..., ::-1]
    else:
        rgb, bgr = rgba[..., :3].astype(np.float32), rgba[..., 2::-1].astype(np.float32)
    if style.get("tint") is not None:
        lum = (bgr / 255.0).mean(-1, keepdims=True)       # 実制作は BGR の順に平均していた（float32 の丸めまで同じにする）
        color = np.array(style["tint"], np.float32) * lum
    else:
        color = rgb
    plane = alpha[..., 0]
    backdrop, shadow, glow = style.get("backdrop"), style.get("shadow"), style.get("glow")
    if backdrop:
        size = max(1, int(round(backdrop["dilate_px"] * scale)))
        grown = cv2.dilate(plane, np.ones((size, size), np.uint8), iterations=int(backdrop["iterations"]))
        halo = np.clip(cv2.GaussianBlur(grown, (0, 0), backdrop["sigma_px"] * scale) * backdrop["gain"], 0, 1)
        f = f * (1 - backdrop["amount"] * halo[..., None] * opacity)
    if shadow:
        soft = cv2.GaussianBlur(plane, (0, 0), shadow["sigma_px"] * scale)[..., None]
        f = f * (1 - shadow["amount"] * np.clip(soft * shadow["gain"], 0, 1) * opacity)
    if glow:
        wide = cv2.GaussianBlur(plane, (0, 0), glow["sigma_px"] * scale)[..., None]
        f = f + np.array(glow["color"], np.float32) * wide * glow["amount"] * opacity
    return f * (1 - alpha * opacity) + color * alpha * opacity
