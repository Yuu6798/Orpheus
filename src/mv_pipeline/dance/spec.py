"""dance-shot/0.2 設定の読み込み・検証・既定値補完（dance-shot/0.1 も読める）。

load_spec(path) は検証済みで既定値を埋めた Spec を返す。以降のモジュールは spec.data の正規化済み辞書だけを見る。
素材パスは spec ファイルのあるディレクトリ基準で、io.safe_path により逸脱を拒否する（存在確認は読み込み時）。

座標はすべて**出力画面の正規化座標** 0..1（字の箱・UI 枠・人物の anchor・カメラの中心）。時間は**ショット時刻**（秒）。
時刻の欄は拍の参照でも書ける（beats.py）。読み込み時に秒へ解決するので、spec.data には秒だけが入る。

0.1 の spec は 0.1 のスキーマで検証し、新機能を全部切った 0.2 のデータに正規化する（`data["source_schema"]` に元の版が残る）。
"""
import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from ..fx import spec as fxspec
from ..fx.spec import canonical_json  # noqa: F401  キャッシュ key の正規形は fx と同じ
from ..io import PipelineError, read_json, safe_path, sha256
from . import SCHEMA_V01, SCHEMA_VERSION
from . import beats as dancebeats
from . import camera as dancecamera

_SCHEMAS = {SCHEMA_VERSION: Path(__file__).with_name("dance_shot.schema.json"),
            SCHEMA_V01: Path(__file__).with_name("dance_shot_0_1.schema.json")}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")
SHAKE_HZ = [15.1, 12.7]

# 縦画面の上・右・下の操作部品のおおよその範囲（値は目安。spec の lyrics.ui_regions で上書きできる）
PORTRAIT_UI = [{"name": "top", "box": [0.0, 0.0, 1.0, 0.06]},
               {"name": "right", "box": [0.86, 0.45, 1.0, 0.88]},
               {"name": "bottom", "box": [0.0, 0.80, 1.0, 1.0]}]
_TEXT_DEFAULTS = {"font": None, "rows": {"align": "center", "gap_em": 0.05}, "reveal": "line", "enter": None, "exit": None, "glow": None,
                  "follow_camera": False, "exclusive": True, "sung": True}
LAYER_DEFAULTS = {
    "back": {"z": "behind", "box": [0.04, 0.18, 0.96, 0.44], "size_px": 180.0, "color": [255.0, 255.0, 255.0],
             "accent_color": [255.0, 90.0, 170.0], "opacity": 0.92, "stroke_px": 0.0, "stroke_color": [0.0, 0.0, 0.0],
             "fade_in_s": 0.04, "fade_out_s": 0.08, **_TEXT_DEFAULTS},
    "band": {"z": "front", "box": [0.04, 0.07, 0.96, 0.15], "size_px": 64.0, "color": [255.0, 255.0, 255.0],
             "accent_color": [255.0, 215.0, 90.0], "opacity": 1.0, "stroke_px": 6.0, "stroke_color": [12.0, 10.0, 24.0],
             "fade_in_s": 0.04, "fade_out_s": 0.08, **_TEXT_DEFAULTS},
}
AUDIO_FADE_IN_S = 0.02    # fx の書き出しと同じ既定値
AUDIO_FADE_OUT_S = 0.25


def schema(version=SCHEMA_VERSION):
    """JSON Schema。0.2 の look は fx と同じ定義を使う。"""
    data = json.loads(_SCHEMAS[version].read_text(encoding="utf-8"))
    if version == SCHEMA_VERSION:
        look = fxspec.look_schema()
        data["properties"]["look"] = look["look"]
        data["$defs"].update(look["defs"])
    return data


@dataclass(frozen=True)
class Spec:
    """data = 正規化済み辞書、base_dir = 素材の基準ディレクトリ、warnings = 設定上の注意。"""
    data: dict
    base_dir: Path
    path: Path | None
    sha256: str
    warnings: tuple

    def resolve(self, rel):
        return safe_path(self.base_dir, rel)


def asset_refs(data):
    """{役割: 相対パス}。役割は stage / clip:<n> / audio / font / beats。manifest の素材 sha256 の列挙に使う。"""
    refs = {"stage": data["stage"]["path"]}
    for index, clip in enumerate(data["performer"]["clips"]):
        refs[f"clip:{index}"] = clip["path"]
    if data["audio"]:
        refs["audio"] = data["audio"]["path"]
    for name, font in data["lyrics"]["fonts"].items():
        refs[f"font:{name}"] = font["path"]
    if data["beats"] and data["beats"]["path"]:
        refs["beats"] = data["beats"]["path"]
    return refs


def load_spec(path):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"dance spec not found: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"dance spec is not valid JSON ({path.name}): {error}") from error
    return parse_spec(raw, path.resolve().parent, path.resolve(), sha256(path))


def parse_spec(raw, base_dir, path=None, digest=""):
    """生の辞書から Spec を作る（テストやメモリ上の spec 用）。"""
    _validate_schema(raw)
    base_dir = Path(base_dir).resolve()
    data = _normalize(raw, base_dir)
    warnings = _validate_semantics(data)
    for rel in asset_refs(data).values():
        safe_path(base_dir, rel)  # 逸脱は PipelineError
    return Spec(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings))


def _validate_schema(raw):
    if not isinstance(raw, dict):
        raise PipelineError("dance spec must be a JSON object")
    version = raw.get("schema_version")
    if version not in _SCHEMAS:
        raise PipelineError(f"dance spec schema_version must be {SCHEMA_VERSION!r} or {SCHEMA_V01!r}, got {version!r}")
    validator = jsonschema.Draft202012Validator(schema(version))
    errors = sorted(validator.iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {e.message[:200]}" for e in errors[:8]]
        more = f"\n  ... and {len(errors) - 8} more" if len(errors) > 8 else ""
        raise PipelineError("dance spec is invalid:\n" + "\n".join(lines) + more)


def _ext(raw):
    return {k: v for k, v in raw.items() if k.startswith("x_")}


def _vec(value):
    return [float(v) for v in value]


def _normalize_clip(clip, time, legacy):
    place = clip.get("placement", {})
    transition = clip.get("transition_in", {"type": "cut"})
    return {"path": clip["path"], "kind": "image" if not legacy and clip["path"].lower().endswith(IMAGE_SUFFIXES) else "video",
            "start": time(clip["start"], "clip の start"), "end": time(clip["end"], "clip の end"),
            "source_start": float(clip.get("source_start", 0)), "speed": float(clip.get("speed", 1.0)),
            "placement": {"offset": _vec(place.get("offset", [0, 0])), "scale": float(place.get("scale", 1.0)),
                          "flip_x": bool(place.get("flip_x", False)), **_ext(place)},
            "fit": clip.get("fit", True), "beat_exempt": bool(clip.get("beat_exempt", False)),
            "transition_in": {"type": transition["type"], "frames": int(transition.get("frames", 0))}, **_ext(clip)}


def _normalize_stage(stage, legacy):
    return {"path": stage["path"], "kind": "video" if not legacy and not stage["path"].lower().endswith(IMAGE_SUFFIXES) else "image",
            "source_start": float(stage.get("source_start", 0)), "loop": bool(stage.get("loop", True)),
            "parallax": float(stage.get("parallax", 1.0)), **_ext(stage)}


def _accent_times(at, grid, time, duration):
    """accents の at を秒の配列にする（時刻の配列か、拍の格子の指定）。"""
    if isinstance(at, list):
        return sorted(time(value, "accents の at") for value in at)
    if grid is None:
        raise PipelineError("dance spec: accents の grid の指定には beats が必要です")
    source = grid.downbeats if at["grid"] == "downbeats" else grid.beats
    first, last = time(at.get("from", 0), "accents の from"), time(at.get("to", duration), "accents の to")
    picked = [t for t in source if first <= t <= last][::int(at.get("every", 1))]
    tolerance = float(at.get("except_tolerance_s", 0.05))
    skip = [time(value, "accents の except") for value in at.get("except", [])]
    return [t for t in picked if all(abs(t - other) > tolerance for other in skip)]


def _normalize_accent(accent, grid, time, duration):
    decay = float(accent.get("decay_s", 0.12))
    return {"times": _accent_times(accent["at"], grid, time, duration), "zoom": float(accent.get("zoom", 1.0)),
            "attack_s": float(accent.get("attack_s", 0)), "decay_s": decay, "shake_px": float(accent.get("shake_px", 0)),
            "shake_decay_s": float(accent.get("shake_decay_s", decay)), "shake_hz": _vec(accent.get("shake_hz", SHAKE_HZ)), **_ext(accent)}


def _normalize_seam(seam, time):
    span = float(seam.get("span_s", 0.4))
    shaped = "pre_s" in seam or "post_s" in seam or "rotate_post_deg" in seam
    blur = seam.get("blur")
    return {"t": time(seam["t"], "camera.seams の t"), "span_s": span, "zoom": float(seam.get("zoom", 1.25)),
            "rotate_deg": float(seam.get("rotate_deg", 6)), "pre_s": float(seam.get("pre_s", span)) if shaped else None,
            "post_s": float(seam.get("post_s", span)) if shaped else None,
            "rotate_post_deg": None if seam.get("rotate_post_deg") is None else float(seam["rotate_post_deg"]),
            "pivot": _vec(seam.get("pivot", [0.5, 0.5])),
            "blur": None if blur is None else {"samples": int(blur.get("samples", 8)), "shutter_frames": float(blur.get("shutter_frames", 1.0)),
                                               "peak_shutter_frames": float(blur.get("peak_shutter_frames", 1.6)), **_ext(blur)},
            **_ext(seam)}


def _normalize_fit(fit):
    return {"mode": fit.get("mode", "frame"), "body_height": float(fit.get("body_height", 0.62)), "x": fit.get("x", "none"),
            "x_smooth_s": float(fit.get("x_smooth_s", 0.5)), "ground": fit.get("ground", "none"),
            "ground_window_s": float(fit.get("ground_window_s", 0.7)), "ground_smooth_s": float(fit.get("ground_smooth_s", 0.3)),
            "analysis_scale": float(fit.get("analysis_scale", 0.5)), **_ext(fit)}


def _style_fields(raw):
    """層の設定のうち書かれた欄だけを正規化する（行の style の上書きにも使う）。"""
    out = {}
    for key in ("box", "color", "accent_color", "stroke_color"):
        if key in raw:
            out[key] = _vec(raw[key])
    for key in ("size_px", "opacity", "stroke_px", "fade_in_s", "fade_out_s"):
        if key in raw:
            out[key] = float(raw[key])
    for key in ("font", "reveal"):
        if key in raw:
            out[key] = raw[key]
    for key in ("follow_camera", "exclusive", "sung"):
        if key in raw:
            out[key] = bool(raw[key])
    if "rows" in raw:
        out["rows"] = {"align": raw["rows"].get("align", "center"), "gap_em": float(raw["rows"].get("gap_em", 0.05)), **_ext(raw["rows"])}
    if "enter" in raw:
        come = raw["enter"]
        out["enter"] = {"scale_from": float(come.get("scale_from", 1.3)), "rise_px": float(come.get("rise_px", 26)),
                        "dur_s": float(come.get("dur_s", 0.14)), **_ext(come)}
    if "exit" in raw:
        leave = raw["exit"]
        out["exit"] = {"scale_to": float(leave.get("scale_to", 1.16)), "dur_s": float(leave.get("dur_s", 0.30)), **_ext(leave)}
    if "glow" in raw:
        glow = raw["glow"]
        out["glow"] = {"color": _vec(glow.get("color", [255, 255, 255])), "radius_px": float(glow.get("radius_px", 16)),
                       "strength": float(glow.get("strength", 0.6)), **_ext(glow)}
    return {**out, **_ext(raw)}


def _normalize_layer(raw, name):
    """既定の層（back / band）以外は z が必須で、残りの既定値は band と同じ。"""
    raw = raw or {}
    if name not in LAYER_DEFAULTS and "z" not in raw:
        raise PipelineError(f"dance spec: lyrics の層 {name!r} には z（behind / front）が必要です")
    defaults = LAYER_DEFAULTS.get(name, LAYER_DEFAULTS["band"])
    return {"name": name, **defaults, "z": raw.get("z", defaults["z"]), **_style_fields(raw)}


def _normalize_layers(lyrics, legacy):
    """層の列。描く順は z ごとに書いた順なので、順序が残るように配列にする（既定の層を書かなければ先頭に置く）。"""
    written = {"back": lyrics.get("back"), "band": lyrics.get("band")} if legacy else lyrics.get("layers", {})
    names = [name for name in LAYER_DEFAULTS if name not in written] + list(written)
    return [_normalize_layer(written.get(name), name) for name in names]


def _normalize_fonts(lyrics):
    """(既定の書体名, {名前: {path, index}})。fonts が無ければ font はパスで、"default" の書体にする（0.1 の書き方）。"""
    if "fonts" in lyrics:
        return lyrics.get("font"), {name: {"path": f["path"], "index": int(f.get("index", 0))} for name, f in lyrics["fonts"].items()}
    if lyrics.get("font"):
        return "default", {"default": {"path": lyrics["font"], "index": int(lyrics.get("font_index", 0))}}
    return None, {}


def _normalize_line(line, time):
    words = [{"text": w["text"], "t": None if w.get("t") is None else time(w["t"], "語の t"), "accent": bool(w.get("accent", True)),
              "color": None if w.get("color") is None else _vec(w["color"]), "row": int(w.get("row", 0))}
             for w in line.get("words", [])]
    return {"layer": line["layer"], "text": line["text"], "start": time(line["start"], "歌詞の start"),
            "end": time(line["end"], "歌詞の end"), "words": words, "style": _style_fields(line.get("style", {})),
            "sung": line.get("sung"), **_ext(line)}


def _normalize_audio(audio):
    return {"path": audio["path"], "offset_s": float(audio.get("offset_s", 0)), "fit": audio.get("fit", "strict"),
            "end_s": None if audio.get("end_s") is None else float(audio["end_s"]),
            "fade_in_s": float(audio.get("fade_in_s", AUDIO_FADE_IN_S)),
            "fade_out_s": float(audio.get("fade_out_s", AUDIO_FADE_OUT_S)), **_ext(audio)}


def _normalize(raw, base_dir):
    out = raw["output"]
    audio = raw.get("audio")
    performer = raw["performer"]
    key = performer.get("key", {})
    place = performer.get("placement", {})
    camera = raw.get("camera", {})
    lyrics = raw.get("lyrics", {})
    portrait = out["height"] > out["width"]
    legacy = raw["schema_version"] == SCHEMA_V01
    font, fonts = _normalize_fonts(lyrics)
    grid = dancebeats.load_grid(raw.get("beats"), lambda rel: safe_path(base_dir, rel))
    time = lambda value, where: dancebeats.resolve_time(value, grid, where)  # noqa: E731
    data = {
        "schema_version": SCHEMA_VERSION,
        "source_schema": raw["schema_version"],
        "seed": int(raw.get("seed", 0)),
        "output": {"width": int(out["width"]), "height": int(out["height"]), "fps": float(out.get("fps", 24)),
                   "crf": int(out.get("crf", 18)), **_ext(out)},
        "duration_s": float(raw["duration_s"]),
        "beats": None if grid is None else {"path": grid.path, **grid.record()},
        "audio": None if audio is None else _normalize_audio(audio),
        "stage": _normalize_stage(raw["stage"], legacy),
        "performer": {
            "clips": [_normalize_clip(c, time, legacy) for c in performer["clips"]],
            "key": {"color": _vec(key.get("color", [0, 255, 0])), "tolerance": float(key.get("tolerance", 0.22)),
                    "softness": float(key.get("softness", 0.12)), "spill": float(key.get("spill", 1.0)),
                    "choke_px": float(key.get("choke_px", 1)), "feather_px": float(key.get("feather_px", 1)), **_ext(key)},
            "placement": {"anchor": _vec(place.get("anchor", [0.5, 0.92])), "pivot": _vec(place.get("pivot", [0.5, 1.0])),
                          "height": float(place.get("height", 0.72)), "flip_x": bool(place.get("flip_x", False)), **_ext(place)},
            "fit": None if "fit" not in performer else _normalize_fit(performer["fit"]),
            **_ext(performer),
        },
        "camera": {
            "keys": [{"t": time(k["t"], "camera.keys の t"), "zoom": float(k.get("zoom", 1.0)), "center": _vec(k.get("center", [0.5, 0.5])),
                      "rotate_deg": float(k.get("rotate_deg", 0)), "ease": k.get("ease", "linear"), **_ext(k)}
                     for k in camera.get("keys") or [{"t": 0.0}]],
            "accents": [_normalize_accent(a, grid, time, float(raw["duration_s"])) for a in camera.get("accents", [])],
            "seams": [_normalize_seam(s, time) for s in camera.get("seams", [])],
            "cover_rotation": bool(camera.get("cover_rotation", not legacy)),
            **_ext(camera),
        },
        "look": None if raw.get("look") is None else fxspec.normalize_look(raw["look"]),
        "flashes": [{"t": time(f["t"], "flashes の t"), "strength": float(f.get("strength", 0.6)), "decay_s": float(f.get("decay_s", 0.2)),
                     "color": _vec(f.get("color", [255, 255, 255])), "mode": f.get("mode", "lerp_linear"),
                     "beat_exempt": bool(f.get("beat_exempt", False)), **_ext(f)} for f in raw.get("flashes", [])],
        "lyrics": {
            "font": font, "fonts": fonts,
            "ui_regions": [{"name": r["name"], "box": _vec(r["box"])} for r in lyrics["ui_regions"]] if "ui_regions" in lyrics
            else (PORTRAIT_UI if portrait else []),
            "layers": _normalize_layers(lyrics, legacy),
            "lines": [_normalize_line(line, time) for line in lyrics.get("lines", [])],
            **_ext(lyrics),
        },
    }
    data.update(_ext(raw))
    return data


def _box_ok(box):
    return box[0] < box[2] and box[1] < box[3]


def _validate_clips(clips, duration, warnings):
    for clip in clips:
        if clip["end"] <= clip["start"]:
            raise PipelineError(f"dance spec: clip {clip['path']!r} の end は start より後にしてください")
    ordered = sorted(clips, key=lambda c: c["start"])
    if ordered != clips:
        raise PipelineError("dance spec: performer.clips は start の昇順に並べてください")
    if clips[0]["transition_in"]["type"] == "blend":
        raise PipelineError(f"dance spec: 最初の clip {clips[0]['path']!r} は blend できません（前のクリップがありません）")
    for left, right in zip(clips, clips[1:]):
        if right["start"] < left["end"] - 1e-9:
            raise PipelineError(f"dance spec: clip {left['path']!r} と {right['path']!r} の区間が重なっています")
        if right["start"] > left["end"] + 1e-9:
            if right["transition_in"]["type"] == "blend":
                raise PipelineError(f"dance spec: blend する clip {right['path']!r} は前のクリップと隙間なく接してください")
            warnings.append(f"performer: {left['end']:g}〜{right['start']:g} 秒に人物がいません")
    if clips[0]["start"] > 0:
        warnings.append(f"performer: 0〜{clips[0]['start']:g} 秒に人物がいません")
    if clips[-1]["end"] < duration - 1e-9:
        warnings.append(f"performer: {clips[-1]['end']:g}〜{duration:g} 秒に人物がいません")


def _validate_audio(audio, warnings, duration):
    if audio is not None and audio["end_s"] is not None and audio["end_s"] > duration:
        warnings.append(f"audio: end_s {audio['end_s']:g} 秒がショットの長さ {duration:g} 秒より後です")


def _validate_seams(seams, fps, warnings):
    for seam in seams:
        post = seam["rotate_post_deg"]
        if post is None:
            continue
        rotate = seam["rotate_deg"]
        if rotate * post <= 0 or abs(post) < abs(rotate):
            raise PipelineError(f"dance spec: seams（{seam['t']:g} 秒）の rotate_post_deg は rotate_deg と同じ符号で、絶対値が rotate_deg 以上にしてください")
        if seam["post_s"] * fps < 3:
            raise PipelineError(f"dance spec: seams（{seam['t']:g} 秒）の rotate_post_deg には 3 コマ以上の post_s が要ります")
        curve, adjusted = dancecamera.rotate_curve(seam, fps)
        if adjusted:
            warnings.append(f"camera: seams（{seam['t']:g} 秒）の回転が切れ目で止まったり逆に回ったりしないよう、頂点を {curve.sign * curve.peak:.2f} 度にしました")


def _validate_semantics(data):
    warnings = []
    out, duration = data["output"], data["duration_s"]
    if out["width"] % 2 or out["height"] % 2:
        raise PipelineError(f"dance spec: 出力寸法は偶数にしてください: {out['width']}x{out['height']}")
    keys = [k["t"] for k in data["camera"]["keys"]]
    if any(b <= a for a, b in zip(keys, keys[1:])):
        raise PipelineError(f"dance spec: camera.keys の t は厳密な昇順にしてください: {keys}")
    _validate_seams(data["camera"]["seams"], out["fps"], warnings)
    _validate_clips(data["performer"]["clips"], duration, warnings)
    _validate_audio(data["audio"], warnings, duration)
    warnings += fxspec.look_warnings(data["look"])
    lyrics = data["lyrics"]
    layers = {layer["name"]: layer for layer in lyrics["layers"]}
    if lyrics["font"] is not None and lyrics["font"] not in lyrics["fonts"]:
        raise PipelineError(f"dance spec: lyrics.font の書体 {lyrics['font']!r} が lyrics.fonts にありません")
    for layer in layers.values():
        if not _box_ok(layer["box"]):
            raise PipelineError(f"dance spec: lyrics.{layer['name']}.box は [x0, y0, x1, y1]（x0 < x1, y0 < y1）にしてください")
    for region in lyrics["ui_regions"]:
        if not _box_ok(region["box"]):
            raise PipelineError(f"dance spec: ui_regions の {region['name']!r} の box が不正です")
    for line in lyrics["lines"]:
        if line["layer"] not in layers:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の層 {line['layer']!r} が lyrics.layers にありません")
        style = {**layers[line["layer"]], **line["style"]}
        if "box" in line["style"] and not _box_ok(style["box"]):
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の style.box は [x0, y0, x1, y1]（x0 < x1, y0 < y1）にしてください")
        name = style["font"] or lyrics["font"]
        if name is not None and name not in lyrics["fonts"]:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の書体 {name!r} が lyrics.fonts にありません")
        if name is None and not line["text"].isascii():
            raise PipelineError("dance spec: ASCII 以外の歌詞には lyrics.font が必要です（フォントファイルを spec の隣に置いてください）")
        if style["exit"] is not None and style["exit"]["dur_s"] > line["end"] - line["start"]:
            warnings.append(f"lyrics: {line['text']!r} の exit が行の長さより長いので、行の頭から動きます")
        if line["end"] <= line["start"]:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の end は start より後にしてください")
        if line["words"] and "".join(w["text"] for w in line["words"]) != line["text"]:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の words をつなげた文字列が text と一致しません")
        if line["start"] >= duration:
            warnings.append(f"lyrics: {line['text']!r} はショットの長さの後に始まるので出ません")
    return warnings
