"""fx-shot/0.1 設定の読み込み・検証・既定値補完。

load_spec(path) は検証済みで既定値を埋めた Spec を返す。以降のモジュールは spec.data の
正規化済み辞書だけを見る（生 JSON は見ない）。`x_` で始まる鍵はそのまま残るが描画には使われない。
素材パスは spec ファイルのあるディレクトリ基準で、io.safe_path により逸脱を拒否する
（存在確認は assets 読み込み時）。

座標（light.position・camera.center など）は基準画像の正規化座標 0..1。出力と縦横比が違うときは
cover で中央に合わせて切った後の画面（zoom 1 で見える範囲）を 0..1 とする。時間は**ショット時刻**
（spec の時間軸。区間を切って描いても同じ t は同じ絵になる）。
"""
import json
from dataclasses import dataclass
from pathlib import Path

import jsonschema

from ..io import PipelineError, read_json, safe_path, sha256
from . import SCHEMA_VERSION

_SCHEMA_PATH = Path(__file__).with_name("fx_shot.schema.json")


def schema():
    return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class Spec:
    """data = 正規化済み辞書、base_dir = 素材の基準ディレクトリ、warnings = 設定上の注意。"""
    data: dict
    base_dir: Path
    path: Path | None
    sha256: str
    warnings: tuple

    def resolve(self, rel):
        """spec 内の相対パスを絶対パスにする（safe_path で逸脱を拒否）。"""
        return safe_path(self.base_dir, rel)


def canonical_json(data):
    """キャッシュ key 用の正規形（鍵順固定・空白なし・非 ASCII そのまま）。"""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def asset_refs(data):
    """{役割: 相対パス}。役割は base / depth / mask:<id> / audio。manifest の素材 sha256 の列挙に使う。"""
    refs = {"base": data["assets"]["base"]}
    if data["assets"]["depth"]:
        refs["depth"] = data["assets"]["depth"]["path"]
    for mask_id, mask in data["assets"]["masks"].items():
        refs[f"mask:{mask_id}"] = mask["path"]
    if data["audio"]:
        refs["audio"] = data["audio"]["path"]
    return refs


def load_spec(path):
    path = Path(path)
    if not path.is_file():
        raise PipelineError(f"fx spec not found: {path}")
    try:
        raw = read_json(path)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise PipelineError(f"fx spec is not valid JSON ({path.name}): {error}") from error
    return parse_spec(raw, path.resolve().parent, path.resolve(), sha256(path))


def parse_spec(raw, base_dir, path=None, digest=""):
    """生の辞書から Spec を作る（テストやメモリ上の spec 用）。"""
    _validate_schema(raw)
    data = _normalize(raw)
    warnings = _validate_semantics(data)
    base_dir = Path(base_dir).resolve()
    for rel in asset_refs(data).values():
        safe_path(base_dir, rel)  # 逸脱は PipelineError
    return Spec(data=data, base_dir=base_dir, path=path, sha256=digest, warnings=tuple(warnings))


def _validate_schema(raw):
    if not isinstance(raw, dict):
        raise PipelineError("fx spec must be a JSON object")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise PipelineError(f"fx spec schema_version must be {SCHEMA_VERSION!r}, got {raw.get('schema_version')!r}")
    validator = jsonschema.Draft202012Validator(schema())
    errors = sorted(validator.iter_errors(raw), key=lambda e: [str(p) for p in e.absolute_path])
    if errors:
        lines = [f"  {'.'.join(str(p) for p in e.absolute_path) or '(top)'}: {_message(e)}" for e in errors[:8]]
        more = f"\n  ... and {len(errors) - 8} more" if len(errors) > 8 else ""
        raise PipelineError("fx spec is invalid:\n" + "\n".join(lines) + more)


def _message(error):
    if error.validator in ("oneOf", "anyOf") and error.context:
        # 「文字列」か「オブジェクト」の選択肢では、型違いの枝を除いた最も関連する指摘を出す
        deeper = [c for c in error.context if not (c.validator == "type" and not c.path)]
        if deeper:
            inner = jsonschema.exceptions.best_match(deeper)
            where = ".".join(str(p) for p in inner.path)
            return f"{where + ': ' if where else ''}{_message(inner)}"
    if error.validator == "additionalProperties" and isinstance(error.instance, dict):
        known = error.schema.get("properties", {})
        unknown = sorted(k for k in error.instance if k not in known and not k.startswith("x_"))
        return f"未知の鍵 {unknown}（綴りを確認してください。試行用の鍵は x_ で始めます）"
    return error.message[:200]


def _ext(raw):
    return {k: v for k, v in raw.items() if k.startswith("x_")}


def _f(value):
    return float(value)


def _vec(value):
    return [float(v) for v in value]


def _keys(raw_keys, fields):
    """キーフレーム列を float 化し ease を補う。"""
    out = []
    for key in raw_keys:
        item = {"t": _f(key["t"]), "ease": key.get("ease", "linear")}
        for name, default in fields.items():
            item[name] = _vec(key.get(name, default)) if isinstance(default, list) else _f(key.get(name, default))
        out.append({**item, **_ext(key)})
    return out


def _normalize(raw):
    out = raw["output"]
    assets = raw["assets"]
    depth = assets.get("depth")
    if isinstance(depth, str):
        depth = {"path": depth}
    masks = {}
    for mask_id, mask in assets.get("masks", {}).items():
        mask = {"path": mask} if isinstance(mask, str) else mask
        masks[mask_id] = {"path": mask["path"], "feather_px": _f(mask.get("feather_px", 0)), **_ext(mask)}
    camera = raw.get("camera", {})
    keys = camera.get("keys") or [{"t": 0.0}]
    audio = raw.get("audio")
    layers = raw.get("layers")
    data = {
        "schema_version": SCHEMA_VERSION,
        "output": {"width": out["width"], "height": out["height"], "fps": _f(out.get("fps", 24)),
                   "crf": out.get("crf", 18), **_ext(out)},
        "duration_s": _f(raw["duration_s"]),
        "seed": raw.get("seed", 0),
        "audio": None if audio is None else {"path": audio["path"], "offset_s": _f(audio.get("offset_s", 0)), **_ext(audio)},
        "assets": {
            "base": assets["base"],
            "depth": None if depth is None else {"path": depth["path"], "near": depth.get("near", "white"), **_ext(depth)},
            "masks": masks,
            **_ext(assets),
        },
        "camera": {
            "keys": _keys(keys, {"zoom": 1.0, "center": [0.5, 0.5]}),
            "parallax_px": _f(camera.get("parallax_px", 0)),
            "parallax_dir": _vec(camera.get("parallax_dir", [1, 0])),
            **_ext(camera),
        },
        "layers": None if layers is None else _normalize_layers(layers),
        "lights": [_normalize_light(light) for light in raw.get("lights", [])],
    }
    data.update(_ext(raw))
    return data


def _normalize_layers(layers):
    background = layers.get("background", {})
    planes = [{"id": p["id"], "mask": p["mask"], "z": _f(p.get("z", 0)), "parallax": _f(p.get("parallax", 1.0)),
               "drift_px": _vec(p.get("drift_px", [0, 0])), **_ext(p)} for p in layers.get("planes", [])]
    effects = []
    for e in layers.get("effects", []):
        if e["type"] == "fog":
            effects.append({"id": e["id"], "type": "fog", "z": _f(e.get("z", 0)),
                            "color": _vec(e.get("color", [170, 175, 200])), "opacity": _f(e.get("opacity", 0.3)),
                            "scale": _f(e.get("scale", 0.25)), "drift": _vec(e.get("drift", [0.01, 0])),
                            "parallax": _f(e.get("parallax", 0.6)), **_ext(e)})
        else:
            effects.append({"id": e["id"], "type": "particles", "z": _f(e.get("z", 0)), "count": e.get("count", 100),
                            "color": _vec(e.get("color", [255, 255, 255])), "size_px": _vec(e.get("size_px", [1.5, 4.0])),
                            "velocity": _vec(e.get("velocity", [0, -0.05])), "flicker": _f(e.get("flicker", 0)),
                            "parallax": _f(e.get("parallax", 1.0)), **_ext(e)})
    return {"background": {"parallax": _f(background.get("parallax", 0.4)),
                           "inpaint_px": _f(background.get("inpaint_px", 8)), **_ext(background)},
            "planes": planes, "effects": effects, **_ext(layers)}


def _normalize_light(light):
    intensity = light.get("intensity", 1.0)
    intensity = [{"t": 0.0, "v": _f(intensity), "ease": "linear"}] if not isinstance(intensity, list) \
        else _keys(intensity, {"v": 0.0})
    flicker = light.get("flicker", {})
    emitter = light.get("emitter")
    out = {
        "id": light["id"], "type": "point", "position": _vec(light["position"]),
        "color": _vec(light.get("color", [255, 255, 255])), "radius": _f(light.get("radius", 0.45)),
        "intensity": intensity,
        "flicker": {"amount": _f(flicker.get("amount", 0)), "hz": _f(flicker.get("hz", 5.0)), **_ext(flicker)},
        "emitter": None if emitter is None else {"size": _f(emitter.get("size", 0.03)),
                                                 "glow": _f(emitter.get("glow", 0.5)), **_ext(emitter)},
        "receivers": [{"mask": r["mask"], "gain": _f(r.get("gain", 1.0)), "rim": _f(r.get("rim", 0)),
                       "reflection": _f(r.get("reflection", 0)), **_ext(r)} for r in light.get("receivers", [])],
        "depth": None if light.get("depth") is None else _f(light["depth"]),
        "depth_falloff": _f(light.get("depth_falloff", 0.25)),
    }
    out.update(_ext(light))
    return out


def _increasing(keys, label):
    times = [k["t"] for k in keys]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise PipelineError(f"fx spec: {label} の t は厳密な昇順にしてください: {times}")


def _validate_semantics(data):
    warnings = []
    masks = data["assets"]["masks"]
    _increasing(data["camera"]["keys"], "camera.keys")
    layers = data["layers"]
    if layers:
        names = [p["id"] for p in layers["planes"]] + [e["id"] for e in layers["effects"]]
        if len(set(names)) != len(names):
            raise PipelineError(f"fx spec: layers の id（planes と effects 通し）が重複しています: {sorted(names)}")
        for plane in layers["planes"]:
            if plane["mask"] not in masks:
                raise PipelineError(f"fx spec: plane {plane['id']!r} の mask {plane['mask']!r} が assets.masks にありません")
    ids = [light["id"] for light in data["lights"]]
    if len(set(ids)) != len(ids):
        raise PipelineError(f"fx spec: lights の id が重複しています: {sorted(ids)}")
    for light in data["lights"]:
        _increasing(light["intensity"], f"lights[{light['id']}].intensity")
        for receiver in light["receivers"]:
            if receiver["mask"] not in masks:
                raise PipelineError(f"fx spec: light {light['id']!r} の receiver mask {receiver['mask']!r} が assets.masks にありません")
        if light["depth"] is not None and not data["assets"]["depth"]:
            warnings.append(f"light {light['id']!r}: depth を指定していますが assets.depth がないため無視します")
    if data["camera"]["parallax_px"] > 0 and not data["assets"]["depth"]:
        warnings.append("camera.parallax_px > 0 ですが assets.depth がないため深度視差は無効です")
    return warnings
