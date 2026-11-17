"""mv dance passes の spec: 版と既定の値、区間（segments）・光の強さのキー・色の読み方、spec の確かめと埋め（normalize・parse_spec・
load_spec）。mv_pipeline.dance.passes から以前の名前で読める。"""
import json
import re
from pathlib import Path

import jsonschema
import numpy as np

from ..fx import spec as fxspec
from ..io import PipelineError, read_json_file, schema_lines, sha256
from . import cues as dancecues, space


SCHEMA_VERSION = "dance-passes/0.1"


SCHEMA_FILE = Path(__file__).resolve().parent / "dance_passes.schema.json"


SPRITES_SCHEMA = Path(__file__).resolve().parent / "dance_sprites.schema.json"


KINDS = ("room", "char", "shade", "disc", "head", "rchar", "rshade", "pool", "fx", "rfx")


PHANTOM_KIND = re.compile(r"^r?ph([0-9]+)$")       # 分身 ph<i> と、床で反転したカメラの rph<i>


FLOOR_SHADOW = {"strength": 0.45, "sigma": 26.0, "gain": 1.0, "down": 1}      # 既定は試作の値


FLASH_COLOR = [255.0, 255.0, 255.0]


def schema():
    """JSON Schema。look は fx と同じ定義を使う。"""
    data = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
    look = fxspec.look_schema()
    data["properties"]["look"] = look["look"]
    data["$defs"].update(look["defs"])
    return data


def _check_keys(keys, where):
    try:
        dancecues.envelope(keys, 0.0)
    except ValueError as error:
        raise PipelineError(f"{where}: {error}") from error
    return [list(key) for key in keys]


def _segments(value, where):
    """部屋のぼかしの区間。数は 1 区間。区間の並びは until が昇順で、最後だけ until を省ける。"""
    if isinstance(value, (int, float)):
        return [{"until": None, "sigma": float(value)}]
    out = []
    for n, part in enumerate(value):
        until = part.get("until")
        if until is None and n != len(value) - 1:
            raise PipelineError(f"{where}[{n}] には until が要ります（省けるのは最後の区間だけ）")
        if until is not None and out and out[-1]["until"] is not None and until <= out[-1]["until"]:
            raise PipelineError(f"{where}[{n}] の until は前の区間より後にしてください: {until}")
        out.append({"until": None if until is None else float(until), "sigma": float(part["sigma"])})
    return out


def segment_value(segments, t):
    """時刻 t の区間の値（区間の頭は、前の区間の until。最後の区間の後は最後の区間の値）。"""
    for part in segments:
        if part["until"] is None or t < part["until"]:
            return part["sigma"]
    return segments[-1]["sigma"]


def level_at(level, t):
    """数か時刻のキー（cues.envelope）の t での値。"""
    return float(level) if isinstance(level, (int, float)) else float(dancecues.envelope(level, t))


def clock_at(data, t):
    """効果の時刻（spec の clock。省くと t）。時間停止でゆっくり進む時計など（水鏡のうねりが使う）。"""
    return t if data.get("clock") is None else float(dancecues.envelope(data["clock"], t))


def _level(value, where):
    return float(value) if isinstance(value, (int, float)) else _check_keys(value, where)


def _signed(value, where):
    """数か時刻のキー（負の値も許す。にじみのしきい値など）。"""
    return float(value) if isinstance(value, (int, float)) else _check_keys(value, where)


def _color(value, where):
    """色（チャンネルごとの倍率 [R, G, B]）か、色のキー [[時刻, [R, G, B]], [時刻, [R, G, B], つなぎ方], …]。"""
    if len(value) == 3 and all(isinstance(v, (int, float)) for v in value):
        return [float(v) for v in value]
    keys = [[float(key[0]), [float(v) for v in key[1]], *key[2:]] for key in value]
    try:
        dancecues.envelope([[key[0], np.asarray(key[1], np.float32), *key[2:]] for key in keys], 0.0)
    except ValueError as error:
        raise PipelineError(f"{where}: {error}") from error
    return keys


_color_cache = {}


def color_at(value, t):
    """色か色のキーの t での値（float32 の [R, G, B]）。キーの間は cues.envelope と同じ（同じ時刻のキーを並べるとそこで跳ぶ）。"""
    if not value or not isinstance(value[0], list):
        return np.asarray(value, np.float32)
    keys = _color_cache.get(id(value))
    if keys is None or keys[0] is not value:
        keys = (value, [[key[0], np.asarray(key[1], np.float32), *key[2:]] for key in value])
        _color_cache.clear()
        _color_cache[id(value)] = keys
    return np.asarray(dancecues.envelope(keys[1], t), np.float32)


def _phantoms(raw):
    """分身の設定（書いたときだけ。既定は試作の値）。"""
    if raw is None:
        return None
    out = {"strength": _level(raw.get("strength", 0.62), "phantoms.strength")}
    for key in ("dark", "hot"):
        out[key] = [float(v) for v in raw.get(key, space.PHANTOM[key])]
    for key in ("fill", "edge", "edge_blur", "edge_gain", "glow", "glow_gain"):
        out[key] = float(raw.get(key, space.PHANTOM[key]))
    return out


def _world(raw):
    """世界の切り替えの設定（書いたときだけ。既定は試作の値。焼け抜けは fx-timeline の burn と同じ書き方と既定）。"""
    if raw is None:
        return None
    from ..fx.sequence import BURN, BURN_EDGE
    halo = {**space.WORLD["halo"], **raw.get("halo", {})}
    out = {"mix": _level(raw["mix"], "world.mix"), "sky": [float(v) for v in raw.get("sky", space.WORLD["sky"])],
           "pool": [float(v) for v in raw.get("pool", space.WORLD["pool"])],
           "halo": {"sigma": float(halo["sigma"]), "color": [float(v) for v in halo["color"]]}}
    burn = raw.get("burn", {})
    edge = burn.get("edge")
    if edge is not None:
        edge = {"color": [float(v) for v in edge.get("color", BURN_EDGE["color"])],
                **{key: float(edge.get(key, BURN_EDGE[key])) for key in ("width", "gain")}}
    out["burn"] = {"seed": int(burn.get("seed", BURN["seed"])), **{key: float(burn.get(key, BURN[key])) for key in ("scale", "soft")},
                   "edge": edge if edge and edge["gain"] > 0 else None, "char": float(burn.get("char", BURN["char"])),
                   "bias": float(burn.get("bias", BURN["bias"]))}
    return out


def _effects(raw):
    """効果の足し方（書いたときだけ。既定は試作の値）。"""
    if raw is None:
        return None
    return {"gain": _level(raw.get("gain", space.EFFECTS["gain"]), "effects.gain"),
            "glow": _level(raw.get("glow", space.EFFECTS["glow"]), "effects.glow"),
            **{key: float(raw.get(key, space.EFFECTS[key])) for key in ("blur", "glow_blur")}}


CARD_KEYS = ("blur", "focal_ref")


def _cards(raw):
    """手前の板の並び（書いたときだけ）。"""
    if raw is None:
        return None
    out = []
    for n, item in enumerate(raw):
        card = {"image": item["image"], "corners": item.get("corners"), "place": item.get("place"),
                "during": None if item.get("during") is None else [[float(a), float(b)] for a, b in item["during"]],
                "blur": float(item.get("blur", 0.0)), "focal_ref": None if item.get("focal_ref") is None else float(item["focal_ref"]),
                "gain": _level(item.get("gain", 1.0), f"cards[{n}].gain"), "wave": None}
        if (card["corners"] is None) == (card["place"] is None):
            raise PipelineError(f"cards[{n}] は corners（4 隅 m）か place（基準のカメラから置く）のどちらか 1 つにしてください")
        if card["blur"] > 0 and card["focal_ref"] is None:
            raise PipelineError(f"cards[{n}] の blur には focal_ref（blur を決めた焦点距離。カメラの控えの画素）が要ります")
        if item.get("wave") is not None:
            w = item["wave"]
            card["wave"] = {"amount": _level(w.get("amount", 0.0), f"cards[{n}].wave.amount"), "speed": float(w.get("speed", 0.0)),
                            "start": float(w.get("start", 0.0)), "wavelength": float(w.get("wavelength", 260.0))}
        out.append(card)
    return out


def _wipes(raw):
    """柱が横切るつなぎの並び（書いたときだけ。柱の既定は試作の値）。"""
    if raw is None:
        return None
    out = []
    for n, item in enumerate(raw):
        pillar = {**space.PILLAR, **{k: float(v) for k, v in item["pillar"].items() if k != "image"}, "image": item["pillar"]["image"]}
        out.append({"at": float(item["at"]), "seconds": float(item["seconds"]), "direction": int(item["direction"]),
                    "passes": item["passes"], "cameras": item.get("cameras"), "pillar": pillar})
    starts = sorted((w["at"] - w["seconds"] / 2, w["at"] + w["seconds"] / 2) for w in out)
    if any(b[0] < a[1] for a, b in zip(starts, starts[1:])):
        raise PipelineError("wipes の時間が重なっています")
    return out


def _impact(raw):
    if raw is None:
        return None
    return {"amount": _level(raw["amount"], "impact.amount"), "zoom": float(raw.get("zoom", 0.05)), "steps": int(raw.get("steps", 3))}


def _sprites(raw):
    """小さな絵の設定（書いたときだけ。既定は試作の値）。"""
    if raw is None:
        return None
    out = {"items": raw["items"], "images": list(raw["images"])}
    for key in ("gain", "glow", "glow_blur", "focus_blur", "max_blur", "room_blur", "near_clip", "margin", "mirror_blur", "floor"):
        out[key] = float(raw.get(key, space.SPRITES[key]))
    out["fade"] = [float(v) for v in raw.get("fade", space.SPRITES["fade"])]
    out["mirror_mode"] = raw.get("mirror_mode", space.SPRITES["mirror_mode"])
    out["mirror_order"] = raw.get("mirror_order", space.SPRITES["mirror_order"])
    out["focus_metric"] = raw.get("focus_metric", space.SPRITES["focus_metric"])
    return out


def _reflection(raw):
    """床への映り込みの設定（書いた部分だけ。既定は試作の値）。"""
    if raw is None:
        return None
    out = {"char": None, "ripple": None, "phantoms": None, "sprites": None, "fx": None}
    if raw.get("fx") is not None:
        out["fx"] = {"blur": float(raw["fx"].get("blur", 3.0)), "gain": float(raw["fx"].get("gain", 0.35)),
                     "strength": _level(raw["fx"].get("strength", 1.0), "reflection.fx.strength")}
    if raw.get("sprites") is not None:
        out["sprites"] = {"strength": float(raw["sprites"].get("strength", 0.5)),
                          "gain": _level(raw["sprites"].get("gain", 1.0), "reflection.sprites.gain")}
    if raw.get("phantoms") is not None:
        out["phantoms"] = {"blur": float(raw["phantoms"].get("blur", 3.0)), "gain": float(raw["phantoms"].get("gain", 0.45))}
    if raw.get("char") is not None:
        char = raw["char"]
        out["char"] = {"strength": _level(char.get("strength", 0.16), "reflection.char.strength"), "blur": float(char.get("blur", 2.5)),
                       "tint": _color(char.get("tint", [1.0, 1.0, 1.0]), "reflection.char.tint")}
    if raw.get("ripple") is not None:
        ripple = raw["ripple"]
        out["ripple"] = {"amount": _level(ripple.get("amount", 1.0), "reflection.ripple.amount"),
                         **{k: float(ripple.get(k, space.RIPPLE[k])) for k in ("px", "wave_px")},
                         "speed": [float(v) for v in ripple.get("speed", space.RIPPLE["speed"])]}
    return out


def normalize(raw):
    """検証済みの生の spec → 既定で埋めた辞書。"""
    room = raw.get("room", {})
    output = raw.get("output", {})
    data = {"schema_version": SCHEMA_VERSION, "passes": raw["passes"],
            "output": {"crf": int(output.get("crf", 18)), "frames": bool(output.get("frames", False))},
            "audio": None if raw.get("audio") is None else {"path": raw["audio"]["path"],
                                                            "offset_s": float(raw["audio"].get("offset_s", 0.0))},
            "room": {"blur": _segments(room.get("blur", 0.0), "room.blur"), "level": _level(room.get("level", 1.0), "room.level")},
            "floor_shadow": (None if "floor_shadow" in raw and raw["floor_shadow"] is None
                             else {**FLOOR_SHADOW, **{k: float(v) if k != "down" else int(v)
                                                      for k, v in (raw.get("floor_shadow") or {}).items() if not k.startswith("x_")}}),
            "figure": None, "rim": None, "bloom": None, "flash": None, "level": _level(raw.get("level", 1.0), "level"),
            "clock": None if raw.get("clock") is None else _check_keys(raw["clock"], "clock"),
            "reflection": _reflection(raw.get("reflection")), "phantoms": _phantoms(raw.get("phantoms")),
            "cameras": raw.get("cameras"), "sprites": _sprites(raw.get("sprites")),
            "world": _world(raw.get("world")), "impact": _impact(raw.get("impact")), "effects": _effects(raw.get("effects")),
            "references": raw.get("references"), "cards": _cards(raw.get("cards")), "wipes": _wipes(raw.get("wipes")),
            "look": None if raw.get("look") is None else fxspec.normalize_look(raw["look"])}
    if raw.get("figure") is not None:
        figure = raw["figure"]
        low, high = (float(v) for v in figure.get("shade_range", [0.5, 0.92]))
        if not low < high:
            raise PipelineError(f"figure.shade_range は [low, high]（low < high）にしてください: {[low, high]}")
        data["figure"] = {"light": _color(figure["light"], "figure.light"), "shadow": _color(figure["shadow"], "figure.shadow"),
                          "shade_range": [low, high], "face_smooth": float(figure.get("face_smooth", 0.0))}
    if raw.get("rim") is not None:
        rim = raw["rim"]
        data["rim"] = {"rgb": _color(rim["rgb"], "rim.rgb"), "strength": _level(rim["strength"], "rim.strength"),
                       "kernel": float(rim.get("kernel", 7)), "shape": rim.get("shape", "ellipse"), "blur": float(rim.get("blur", 1.6)),
                       "gradient": None if "gradient" in rim and rim["gradient"] is None else [float(v) for v in rim.get("gradient", [1.25, 0.75])],
                       "clip": bool(rim.get("clip", True)), "band": rim.get("band", "erode")}
    if raw.get("bloom") is not None:
        bloom = raw["bloom"]
        data["bloom"] = {"threshold": _signed(bloom.get("threshold", 0.45), "bloom.threshold"),
                         "ceiling": None if "ceiling" in bloom and bloom["ceiling"] is None else float(bloom.get("ceiling", 0.8)),
                         "sigma": float(bloom.get("sigma", 7.0)), "strength": _level(bloom.get("strength", 0.6), "bloom.strength"),
                         "down": int(bloom.get("down", 4))}
    if raw.get("flash") is not None:
        data["flash"] = {"keys": _check_keys(raw["flash"]["keys"], "flash.keys"),
                         "color": [float(v) for v in raw["flash"].get("color", FLASH_COLOR)]}
    return data


def parse_spec(raw, base_dir, path=None, digest=""):
    if not isinstance(raw, dict) or raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"dance passes spec の schema_version は {SCHEMA_VERSION!r} にしてください")
    errors = sorted(jsonschema.Draft202012Validator(schema()).iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = schema_lines(errors)
        raise PipelineError("dance passes spec is invalid:\n" + "\n".join(lines))
    base_dir = Path(base_dir).resolve()
    data = normalize(raw)
    warnings = fxspec.look_warnings(data["look"])      # fx・dance と同じ look の確かめ（grade のキーの並びは止め、注意は warnings に）
    spec = fxspec.Spec(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings))
    spec.resolve(spec.data["passes"])
    if spec.data["audio"] is not None:
        spec.resolve(spec.data["audio"]["path"])
    if spec.data["sprites"] is not None:
        if spec.data["cameras"] is None:
            raise PipelineError("sprites（小さな絵）には cameras（カメラの控え。model3d-cameras/0.1）が要ります")
        for name in [spec.data["sprites"]["items"], *spec.data["sprites"]["images"]]:
            spec.resolve(name)
    if spec.data["cameras"] is not None:
        spec.resolve(spec.data["cameras"])
    return spec


def load_spec(path):
    path = Path(path)
    raw = read_json_file(path, f"dance passes spec が見つかりません: {path}", f"dance passes spec is not valid JSON ({path.name})")
    return parse_spec(raw, path.resolve().parent, path.resolve(), sha256(path))
