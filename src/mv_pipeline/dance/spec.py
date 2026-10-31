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

from ..fx.spec import canonical_json  # noqa: F401  キャッシュ key の正規形は fx と同じ
from ..io import PipelineError, read_json, safe_path, sha256
from . import SCHEMA_V01, SCHEMA_VERSION
from . import beats as dancebeats

_SCHEMAS = {SCHEMA_VERSION: Path(__file__).with_name("dance_shot.schema.json"),
            SCHEMA_V01: Path(__file__).with_name("dance_shot_0_1.schema.json")}
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

# 縦画面の上・右・下の操作部品のおおよその範囲（値は目安。spec の lyrics.ui_regions で上書きできる）
PORTRAIT_UI = [{"name": "top", "box": [0.0, 0.0, 1.0, 0.06]},
               {"name": "right", "box": [0.86, 0.45, 1.0, 0.88]},
               {"name": "bottom", "box": [0.0, 0.80, 1.0, 1.0]}]
LAYER_DEFAULTS = {
    "back": {"box": [0.04, 0.18, 0.96, 0.44], "size_px": 180.0, "color": [255.0, 255.0, 255.0],
             "accent_color": [255.0, 90.0, 170.0], "opacity": 0.92, "stroke_px": 0.0, "stroke_color": [0.0, 0.0, 0.0],
             "fade_in_s": 0.04, "fade_out_s": 0.08},
    "band": {"box": [0.04, 0.07, 0.96, 0.15], "size_px": 64.0, "color": [255.0, 255.0, 255.0],
             "accent_color": [255.0, 215.0, 90.0], "opacity": 1.0, "stroke_px": 6.0, "stroke_color": [12.0, 10.0, 24.0],
             "fade_in_s": 0.04, "fade_out_s": 0.08},
}
AUDIO_FADE_IN_S = 0.02    # fx の書き出しと同じ既定値
AUDIO_FADE_OUT_S = 0.25


def schema(version=SCHEMA_VERSION):
    return json.loads(_SCHEMAS[version].read_text(encoding="utf-8"))


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
    if data["lyrics"]["font"]:
        refs["font"] = data["lyrics"]["font"]
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


def _normalize_clip(clip, time):
    place = clip.get("placement", {})
    transition = clip.get("transition_in", {"type": "cut"})
    return {"path": clip["path"], "kind": "image" if clip["path"].lower().endswith(IMAGE_SUFFIXES) else "video",
            "start": time(clip["start"], "clip の start"), "end": time(clip["end"], "clip の end"),
            "source_start": float(clip.get("source_start", 0)), "speed": float(clip.get("speed", 1.0)),
            "placement": {"offset": _vec(place.get("offset", [0, 0])), "scale": float(place.get("scale", 1.0)),
                          "flip_x": bool(place.get("flip_x", False)), **_ext(place)},
            "fit": clip.get("fit", True), "beat_exempt": bool(clip.get("beat_exempt", False)),
            "transition_in": {"type": transition["type"], "frames": int(transition.get("frames", 0))}, **_ext(clip)}


def _normalize_fit(fit):
    return {"mode": fit.get("mode", "frame"), "body_height": float(fit.get("body_height", 0.62)), "x": fit.get("x", "none"),
            "x_smooth_s": float(fit.get("x_smooth_s", 0.5)), "ground": fit.get("ground", "none"),
            "ground_window_s": float(fit.get("ground_window_s", 0.7)), "ground_smooth_s": float(fit.get("ground_smooth_s", 0.3)),
            "analysis_scale": float(fit.get("analysis_scale", 0.5)), **_ext(fit)}


def _normalize_layer(raw, name):
    defaults = LAYER_DEFAULTS[name]
    raw = raw or {}
    out = {key: (_vec(raw[key]) if isinstance(value, list) else float(raw[key])) if key in raw else value
           for key, value in defaults.items()}
    return {**out, **_ext(raw)}


def _normalize_line(line, time):
    words = [{"text": w["text"], "t": None if w.get("t") is None else time(w["t"], "語の t"), "accent": bool(w.get("accent", True))}
             for w in line.get("words", [])]
    return {"layer": line["layer"], "text": line["text"], "start": time(line["start"], "歌詞の start"),
            "end": time(line["end"], "歌詞の end"), "words": words, **_ext(line)}


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
        "stage": {"path": raw["stage"]["path"], **_ext(raw["stage"])},
        "performer": {
            "clips": [_normalize_clip(c, time) for c in performer["clips"]],
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
            "seams": [{"t": time(s["t"], "camera.seams の t"), "span_s": float(s.get("span_s", 0.4)), "zoom": float(s.get("zoom", 1.25)),
                       "rotate_deg": float(s.get("rotate_deg", 6)), **_ext(s)} for s in camera.get("seams", [])],
            **_ext(camera),
        },
        "flashes": [{"t": time(f["t"], "flashes の t"), "strength": float(f.get("strength", 0.6)), "decay_s": float(f.get("decay_s", 0.2)),
                     "color": _vec(f.get("color", [255, 255, 255])), **_ext(f)} for f in raw.get("flashes", [])],
        "lyrics": {
            "font": lyrics.get("font"), "font_index": int(lyrics.get("font_index", 0)),
            "ui_regions": [{"name": r["name"], "box": _vec(r["box"])} for r in lyrics["ui_regions"]] if "ui_regions" in lyrics
            else (PORTRAIT_UI if portrait else []),
            "back": _normalize_layer(lyrics.get("back"), "back"),
            "band": _normalize_layer(lyrics.get("band"), "band"),
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


def _validate_semantics(data):
    warnings = []
    out, duration = data["output"], data["duration_s"]
    if out["width"] % 2 or out["height"] % 2:
        raise PipelineError(f"dance spec: 出力寸法は偶数にしてください: {out['width']}x{out['height']}")
    keys = [k["t"] for k in data["camera"]["keys"]]
    if any(b <= a for a, b in zip(keys, keys[1:])):
        raise PipelineError(f"dance spec: camera.keys の t は厳密な昇順にしてください: {keys}")
    _validate_clips(data["performer"]["clips"], duration, warnings)
    _validate_audio(data["audio"], warnings, duration)
    lyrics = data["lyrics"]
    for name in ("back", "band"):
        if not _box_ok(lyrics[name]["box"]):
            raise PipelineError(f"dance spec: lyrics.{name}.box は [x0, y0, x1, y1]（x0 < x1, y0 < y1）にしてください")
    for region in lyrics["ui_regions"]:
        if not _box_ok(region["box"]):
            raise PipelineError(f"dance spec: ui_regions の {region['name']!r} の box が不正です")
    for line in lyrics["lines"]:
        if line["end"] <= line["start"]:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の end は start より後にしてください")
        if line["words"] and "".join(w["text"] for w in line["words"]) != line["text"]:
            raise PipelineError(f"dance spec: 歌詞 {line['text']!r} の words をつなげた文字列が text と一致しません")
        if line["start"] >= duration:
            warnings.append(f"lyrics: {line['text']!r} はショットの長さの後に始まるので出ません")
    if not lyrics["font"] and any(not line["text"].isascii() for line in lyrics["lines"]):
        raise PipelineError("dance spec: ASCII 以外の歌詞には lyrics.font が必要です（フォントファイルを spec の隣に置いてください）")
    return warnings
