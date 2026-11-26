"""Compose selected rendered frames with a screen-space effect and a face matte."""

import hashlib
from io import BytesIO
from pathlib import Path

from .. import contracts
from ..blender_adapter.stage_render import frame_name
from ..io import ToolError, atomic_json, resolve_path, safe_path
from . import StepResult, register, require
from .assemble_ops import consecutive
from .finish_ops import _real, _span, write_frames


def _index(rows, label):
    by_frame = {row["T"]: row for row in rows}
    if len(by_frame) != len(rows):
        raise ToolError(f"{label} に同じ T が複数あります")
    return by_frame


def _compatible(manifest, base, label):
    for key in ("fps", "resolution"):
        if manifest[key] != base[key]:
            raise ToolError(f"{label} の {key} が frames と違います")


def _selected(ranges, first, last):
    if ranges is None:
        return set(range(first, last + 1))
    if not isinstance(ranges, list) or not ranges:
        raise ToolError("update_ranges は [最初の T, 最後の T] の空でない並びにしてください")
    selected = set()
    for index, value in enumerate(ranges):
        start, end = _span(value, f"update_ranges[{index}]")
        if start < first or end > last:
            raise ToolError(f"update_ranges[{index}] が frames の範囲の外です")
        selected.update(range(start, end + 1))
    return selected


def _frame_bytes(manifest, row, label):
    data = safe_path(manifest["directory"], row["file"]).read_bytes()
    if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ToolError(f"{label} の一覧と、今のファイルが違います（T: {row['T']}）")
    return data


def _decode(data, size, label, *, formats=("PNG",)):
    from PIL import Image

    try:
        with Image.open(BytesIO(data)) as image:
            if image.size != size or image.format not in formats:
                raise ToolError(f"{label} の寸法または画像形式が違います（{image.size}, {image.format}）")
            return image.copy()
    except OSError as error:
        raise ToolError(f"{label} の画像を読めません: {error}") from error


def _composite(base, effect, face, mode, opacity, protection, feather):
    from PIL import Image, ImageChops, ImageFilter

    alpha = effect.getchannel("A")
    if opacity != 1:
        alpha = alpha.point(lambda value: round(value * opacity))
    if face is not None and protection:
        face = face.convert("L")
        if feather:
            face = face.filter(ImageFilter.GaussianBlur(feather))
        alpha = ImageChops.multiply(alpha, face.point(lambda value: 255 - round(value * protection)))
    colors = effect.convert("RGB")
    if mode == "add":
        contribution = Image.composite(colors, Image.new("RGB", base.size), alpha)
        return ImageChops.add(base, contribution)
    return Image.composite(colors, base, alpha)


@register("render.composite_fx", inputs=("frames", "replacement", "previous", "face_passes"), outputs=("frames", "report"),
          cacheable=False,
          summary="再描画したコマを差し替え、指定区間の透過 PNG 効果を合成する。head パスで顔の上の効果だけを弱められる。"
                  "区間外は前回の出力か土台をバイト単位で写す。出力 frames は render.assemble へ渡せる。",
          capability={"aliases": ["VFX 顔保護", "差分コンポジット", "partial recomposite"],
                      "tags": ["render", "composite", "mask"]})
def composite_fx(inputs, parameters, outputs, context):
    from .finish_ops import _refuse

    operation = "render.composite_fx"
    require(inputs, ("frames",), operation)
    require(outputs, ("frames", "report"), operation)
    unknown = set(parameters) - {"effect_dir", "mode", "opacity", "face_protection", "feather_px", "update_ranges"}
    if unknown:
        raise ToolError(f"{operation} の知らないパラメーター: {sorted(unknown)}")
    mode = parameters.get("mode", "over")
    if mode not in ("over", "add"):
        raise ToolError("mode は over か add にしてください")
    opacity = _real(parameters.get("opacity", 1.0), "opacity", 0)
    protection = _real(parameters.get("face_protection", 1.0), "face_protection", 0)
    feather = _real(parameters.get("feather_px", 0.0), "feather_px", 0)
    if opacity > 1 or protection > 1:
        raise ToolError("opacity と face_protection は 0〜1 にしてください")
    effect_dir = resolve_path(context.recipe_dir, parameters["effect_dir"]) if "effect_dir" in parameters else None
    if effect_dir is not None and not effect_dir.is_dir():
        raise ToolError(f"effect_dir のフォルダがありません: {effect_dir}")
    if effect_dir is None and ("face_passes" in inputs or set(parameters) & {"mode", "opacity", "face_protection", "feather_px"}):
        raise ToolError("効果や顔の設定を使うには effect_dir が要ります")
    if "face_passes" not in inputs and set(parameters) & {"face_protection", "feather_px"}:
        raise ToolError("顔の設定を使うには face_passes が要ります")
    if "previous" in inputs and "update_ranges" not in parameters:
        raise ToolError("previous を使うには update_ranges が要ります")

    base = contracts.load("frames", inputs["frames"])
    if base["missing"] or not base["frames"]:
        raise ToolError("frames には欠けていないコマの一覧が要ります")
    first, last = consecutive([row["T"] for row in base["frames"]], "frames")
    selected = _selected(parameters.get("update_ranges"), first, last)
    size = tuple(base["resolution"])
    manifests, rows = {"frames": base}, {}
    for name in ("replacement", "previous", "face_passes"):
        if name not in inputs:
            continue
        manifest = contracts.load("passes" if name == "face_passes" else "frames", inputs[name])
        _compatible(manifest, base, name)
        manifests[name] = manifest
        if name == "face_passes":
            if "head" not in manifest["passes"]:
                raise ToolError("face_passes に head パスがありません")
            rows[name] = _index(manifest["passes"]["head"]["frames"], name)
        else:
            rows[name] = _index(manifest["frames"], name)
    if rows.get("replacement", {}).keys() - selected:
        raise ToolError("replacement に update_ranges の外のコマがあります")
    if "replacement" in inputs and not rows["replacement"]:
        raise ToolError("replacement に描いたコマがありません")

    out_dir = Path(context.step_dir) / "frames"
    out_dir.mkdir(parents=True, exist_ok=False)
    listing, sources = [], []
    for base_row in base["frames"]:
        T = base_row["T"]
        source = "frames"
        if T in rows.get("replacement", {}):
            source = "replacement"
        elif T not in selected and "previous" in rows:
            source = "previous"
        row = base_row if source == "frames" else rows[source].get(T)
        if row is None:
            raise ToolError(f"{source} に必要なコマがありません（T: {T}）")
        if source == "previous" and row["f"] != base_row["f"]:
            raise ToolError(f"previous の動きのコマ f が frames と違います（T: {T}）")
        data = _frame_bytes(manifests[source], row, source)
        image = _decode(data, size, f"{source} T={T}")
        _refuse(image, row["file"], T, operation, size)
        target = out_dir / frame_name(T)
        receipt = {"T": T, "source": source, "source_sha256": row["sha256"]}
        if T in selected and effect_dir is not None:
            effect_bytes = safe_path(effect_dir, frame_name(T)).read_bytes()
            effect = _decode(effect_bytes, size, f"effect T={T}")
            if effect.mode != "RGBA":
                raise ToolError(f"effect のコマは RGBA PNG にしてください（T: {T}）")
            receipt["effect_sha256"] = hashlib.sha256(effect_bytes).hexdigest()
            face = None
            if "face_passes" in rows:
                matte = rows["face_passes"].get(T)
                if matte is None or matte["f"] != row["f"]:
                    raise ToolError(f"head パスに動きのコマ f が同じマスクがありません（T: {T}）")
                face = _decode(_frame_bytes(manifests["face_passes"], matte, "head"), size,
                               f"head T={T}", formats=("PNG", "JPEG"))
                receipt["head_sha256"] = matte["sha256"]
            _composite(image.convert("RGB"), effect, face, mode, opacity, protection, feather).save(target)
            data = target.read_bytes()
        else:
            target.write_bytes(data)
        sources.append(receipt)
        listing.append({"T": T, "f": row["f"], "file": target.name, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})

    write_frames(outputs, out_dir, base["fps"], size, listing)
    reprocessed = len(selected)
    composited = reprocessed if effect_dir is not None else 0
    atomic_json(outputs["report"], {"schema_version": "model3d-composite-fx/0.1", "updated": sorted(selected), "mode": mode,
                                    "opacity": opacity, "face_protection": protection, "feather_px": feather, "frames": sources})
    return StepResult(observations=[{"kind": "composite_fx", "frames": len(listing), "updated": reprocessed,
                                     "composited": composited, "replaced": len(rows.get("replacement", {}))}])
