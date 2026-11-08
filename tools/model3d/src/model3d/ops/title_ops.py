"""題字: 描いたコマに、曲名などの題字（文字や画像）を、窓の秒の start から浮かび上がらせて重ねる（render.assemble の前）。"""
import hashlib
import shutil
import time
from pathlib import Path

from ..io import ToolError, atomic_json, resolve_path, sha256
from . import StepResult, number, register

TITLE_PARAMETERS = {"frames_dir", "range", "fps", "out_dir", "start", "fade_in", "reference_width", "shadow", "layers"}
LAYER_KEYS = {"text", "image", "font", "variation", "size", "color", "stroke", "stroke_color", "tracking", "glow", "center", "rise",
              "opacity", "blend"}
TEXT_ONLY = {"font", "variation", "size", "color", "stroke", "stroke_color", "tracking"}
DEFAULT_SHADOW = {"blur": 9, "amount": 0.7}


def _color(value, where):
    if (not isinstance(value, list) or len(value) != 3
            or not all(isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 255 for v in value)):
        raise ToolError(f"{where} は [r, g, b]（0〜255 の整数）にしてください: {value!r}")
    return tuple(value)


def _pair(value, where):
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise ToolError(f"{where} は [x, y]（基準の幅での画素）にしてください: {value!r}")
    return float(value[0]), float(value[1])


def _blur_setting(value, where, keys):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise ToolError(f"{where} は {{{', '.join(repr(k) for k in keys)}}} にしてください: {value!r}")
    blur = float(number(value, "blur", minimum=0, exclusive=True))
    amount = float(number(value, "amount", minimum=0))
    return blur, amount


def _refuse_transparency(image, T):
    """透けた画素（アルファか透けの色が 255 未満）のあるコマは止める。題字のコマは RGB に書くので、透けが消えるため。
    アルファがあっても全部 255 なら重ねる（試作のコマは RGBA で、題字を重ねたコマは RGB に書いた）。"""
    if image.mode not in ("RGBA", "LA", "PA") and "transparency" not in image.info:
        return
    if image.convert("RGBA").getchannel("A").getextrema()[0] < 255:
        raise ToolError(f"コマに透けた画素があります（T {T}）。render.title は透けを残せないので、透けたコマには重ねません。"
                        "先に土台へ重ねてから（mv overlay など）、題字を重ねてください")


def layer_settings(layer, index, recipe_dir):
    """層の設定を確かめ、既定を埋める。文字（text）か画像（image）のどちらか 1 つ。"""
    from .. import title
    where = f"layers[{index}]"
    if not isinstance(layer, dict):
        raise ToolError(f"{where} は {{\"text\" か \"image\", \"center\", …}} にしてください: {layer!r}")
    unknown = set(layer) - LAYER_KEYS
    if unknown:
        raise ToolError(f"{where} の知らない項目: {sorted(unknown)}（使えるのは {sorted(LAYER_KEYS)}）")
    if ("text" in layer) == ("image" in layer):
        raise ToolError(f"{where} には text か image のどちらか 1 つを書いてください")
    out = {"center": _pair(layer.get("center"), f"{where}.center"),
           "rise": float(number(layer, "rise", 0.0)),
           "opacity": float(number(layer, "opacity", 1.0, minimum=0))}
    if out["opacity"] > 1:
        raise ToolError(f"{where}.opacity は 0〜1 にしてください: {out['opacity']}")
    out["blend"] = layer.get("blend", "prototype")
    if out["blend"] not in title.BLENDS:
        raise ToolError(f"{where}.blend は {' か '.join(repr(b) for b in title.BLENDS)} にしてください"
                        f"（既定の 'prototype' は試作と同じ重ね方、'over' はふつうの重ね方）: {out['blend']!r}")
    glow = layer.get("glow")
    if glow is not None:
        if not isinstance(glow, dict) or set(glow) != {"color", "blur", "amount"}:
            raise ToolError(f"{where}.glow は {{\"color\": [r, g, b], \"blur\": 画素, \"amount\": 強さ}} にしてください: {glow!r}")
        blur, amount = _blur_setting({"blur": glow["blur"], "amount": glow["amount"]}, f"{where}.glow", ("blur", "amount"))
        out["glow"] = (_color(glow["color"], f"{where}.glow.color"), blur, amount)
    else:
        out["glow"] = None
    if "text" in layer:
        text = layer["text"]
        if not isinstance(text, str) or not text.strip():
            raise ToolError(f"{where}.text は空でない文字列にしてください")
        size = number(layer, "size", minimum=0, exclusive=True)
        font = layer.get("font")
        if font is not None and not isinstance(font, str):
            raise ToolError(f"{where}.font は書体のファイルのパスにしてください: {font!r}")
        font_path = resolve_path(recipe_dir, font) if font else None
        if font_path is not None and not Path(font_path).is_file():
            raise ToolError(f"{where}.font の書体のファイルがありません: {font_path}")
        variation = layer.get("variation")
        if variation is not None and (not isinstance(variation, str) or not variation):
            raise ToolError(f"{where}.variation は可変の書体の形の名前（\"Black\" など）にしてください: {variation!r}")
        out.update(kind="text", text=text, font=font_path, variation=variation, size=float(size),
                   color=_color(layer.get("color", [255, 255, 255]), f"{where}.color"),
                   stroke=float(number(layer, "stroke", 0, minimum=0)),
                   stroke_color=_color(layer.get("stroke_color", [8, 6, 30]), f"{where}.stroke_color"),
                   tracking=float(number(layer, "tracking", 0.0)))
    else:
        extra = TEXT_ONLY & set(layer)
        if extra:
            raise ToolError(f"{where} は画像の層なので、文字の項目 {sorted(extra)} は使えません")
        image = layer["image"]
        if not isinstance(image, str) or not image:
            raise ToolError(f"{where}.image は画像のファイルのパスにしてください: {image!r}")
        path = resolve_path(recipe_dir, image)
        if not Path(path).is_file():
            raise ToolError(f"{where}.image の画像がありません: {path}")
        out.update(kind="image", image=path)
    return out


def build_sprite(layer, k, shadow):
    """層を、幅の倍率 k（コマの幅 / 基準の幅）で絵にする。大きさ・縁取り・ぼかしは基準の幅の画素を k 倍（試作と同じ丸め）。"""
    from .. import title
    shadow_k = (max(1.0, shadow[0] * k), shadow[1]) if shadow is not None else None
    glow = layer["glow"]
    glow_k = (glow[0], max(1.0, glow[1] * k), glow[2]) if glow is not None else None
    try:
        if layer["kind"] == "text":
            size = int(round(layer["size"] * k))
            stroke = max(1, int(round(layer["stroke"] * k))) if layer["stroke"] > 0 else 0
            font = title.load_font(layer["font"], size, layer["variation"])
            color, cover = title.text_layer(layer["text"], font, size, layer["color"], stroke, layer["stroke_color"], layer["tracking"])
            detail = {"kind": "text", "chars": len(layer["text"]),
                      "text_sha256": hashlib.sha256(layer["text"].encode("utf-8")).hexdigest(),
                      "font": str(layer["font"]) if layer["font"] else "Pillow の内蔵の書体",
                      "font_sha256": sha256(layer["font"]) if layer["font"] else None, "variation": layer["variation"],
                      "size_px": size, "stroke_px": stroke, "tracking": layer["tracking"]}
        else:
            # 影と光りが画像の外へ広がる余白（ぼかしの届く画素）。文字の層の余白は試作と同じ（text_layer）
            sigmas = ([shadow_k[0]] if shadow_k is not None else []) + ([glow_k[1]] if glow_k is not None else [])
            margin = max((title.blur_reach(s) for s in sigmas), default=0)
            color, cover = title.image_layer(layer["image"], k, margin)
            detail = {"kind": "image", "image": str(layer["image"]), "image_sha256": sha256(layer["image"]), "margin_px": margin}
        spr = title.sprite(color, cover, shadow_k, glow_k)
    except (OSError, ValueError) as error:
        raise ToolError(f"題字の層を絵にできません: {error}") from error
    detail.update(sprite=[int(spr[1].shape[1]), int(spr[1].shape[0])], cover_box=list(spr[2]),
                  glow=({"color": list(glow[0]), "blur_px": glow_k[1], "amount": glow[2]} if glow else None))
    return spr, detail


def _frames(inputs, parameters, context):
    """入力 frames（model3d-frames/0.1）か、パラメーター frames_dir と range の T。render.assemble と同じ決まり。"""
    from .. import contracts
    from .assemble_ops import consecutive
    manifest = contracts.load("frames", inputs["frames"]) if inputs.get("frames") is not None else None
    span = parameters.get("range")
    if manifest is not None:
        if span is not None:
            raise ToolError("frames と range は一緒に使えません（frames があれば、その一覧のコマに重ねます）")
        if manifest["missing"]:
            raise ToolError(f"描いたコマの一覧に、描いていないコマがあります（T: {manifest['missing'][:10]}）。全部描いてから重ねてください")
        if not manifest["frames"]:
            raise ToolError("描いたコマの一覧が空です")
        consecutive([fr["T"] for fr in manifest["frames"]], "描いたコマの一覧")
        directory = (resolve_path(context.recipe_dir, parameters["frames_dir"]) if parameters.get("frames_dir")
                     else Path(manifest["directory"]))
        frames = [(fr["T"], fr["f"], Path(directory) / fr["file"], fr["sha256"]) for fr in manifest["frames"]]
        return Path(directory), frames, manifest
    if not parameters.get("frames_dir") or span is None:
        raise ToolError("render.title には、入力 frames か、パラメーター frames_dir と range（[最初の T, 最後の T]）が要ります")
    if (not isinstance(span, list) or len(span) != 2 or not all(isinstance(v, int) and not isinstance(v, bool) and v >= 1 for v in span)
            or span[0] > span[1]):
        raise ToolError(f"range は [最初の T, 最後の T]（1 以上の整数、最初 ≤ 最後）にしてください: {span!r}")
    from ..blender_adapter.stage_render import frame_name
    directory = resolve_path(context.recipe_dir, parameters["frames_dir"])
    frames = [(T, T, Path(directory) / frame_name(T), None) for T in range(span[0], span[1] + 1)]
    return Path(directory), frames, None


@register("render.title", inputs=("frames",), outputs=("frames", "report"), cacheable=False,
          summary="描いたコマ（frames の model3d-frames/0.1 か、frames_dir と range の T）に、題字の層（文字か画像）を重ねて、別のフォルダへ書く。"
                  "窓の秒（T = 1 が 0 秒）の start から fade_in 秒で、なめらかに浮かび上がる（rise 画素ぶん上がりながら）。文字は書体と形（可変の書体）・"
                  "大きさ・色・縁取り・字送り、層ごとに光り（glow）、全体に影。画像の層には、影と光りが広がる透けた余白を足す。"
                  "重ね方の既定は試作と同じ（覆いを二度掛ける）。層ごとの blend で、ふつうの重ね方 over も選べる。"
                  "大きさ・位置・ぼかしは基準の幅（既定 1080）の画素で書き、コマの幅に合わせる。"
                  "start より前のコマはそのまま写す。透けた画素のあるコマは止める（題字のコマは RGB に書くので）。"
                  "frames は書いたコマの一覧（model3d-frames/0.1。render.assemble の入力になる）。"
                  "書体・画像・コマのフォルダは隠れた入力なので再利用しない（毎回実行）")
def title_frames(inputs, parameters, outputs, context):
    from PIL import Image
    import numpy as np

    from .. import contracts, title
    unknown = set(parameters) - TITLE_PARAMETERS
    if unknown:
        raise ToolError(f"render.title の知らないパラメーター: {sorted(unknown)}（使えるのは {sorted(TITLE_PARAMETERS)}）")
    if "frames" not in outputs:
        raise ToolError("render.title には outputs の frames（書いたコマの一覧）が要ります")
    source_dir, frames, manifest = _frames(inputs, parameters, context)
    fps = parameters.get("fps") if parameters.get("fps") is not None else (manifest or {}).get("fps")
    if fps is None:
        raise ToolError("render.title には fps が要ります（パラメーター fps か、描いたコマの一覧の fps）")
    fps = float(number({"fps": fps}, "fps", minimum=0, exclusive=True))
    start = float(number(parameters, "start", minimum=0))
    fade_in = float(number(parameters, "fade_in", 0.5, minimum=0))
    reference = float(number(parameters, "reference_width", 1080, minimum=0, exclusive=True))
    shadow = parameters.get("shadow", DEFAULT_SHADOW)
    shadow = _blur_setting(shadow, "shadow", ("blur", "amount")) if shadow is not None else None
    layers = parameters.get("layers")
    if not isinstance(layers, list) or not layers:
        raise ToolError("render.title には layers（題字の層の並び。1 つ以上）が要ります")
    settings = [layer_settings(layer, i, context.recipe_dir) for i, layer in enumerate(layers)]
    out_dir = (resolve_path(context.recipe_dir, parameters["out_dir"]) if parameters.get("out_dir")
               else Path(context.step_dir) / "frames")
    if Path(out_dir).resolve() == source_dir.resolve():
        raise ToolError(f"out_dir が元のコマのフォルダと同じです（元のコマを書き換えません）: {out_dir}")
    missing = [T for T, _f, file, _h in frames if not file.is_file()]
    if missing:
        raise ToolError(f"重ねるコマがありません（{len(missing)} コマ。最初の T: {missing[:10]}）: {source_dir}")
    with Image.open(frames[0][2]) as first:
        W, H = first.size
        _refuse_transparency(first, frames[0][0])        # 写す前に止める（render.stage の透けた背景など）
    k = W / reference
    sprites = [build_sprite(layer, k, shadow) for layer in settings]
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    began = time.monotonic()
    digest, listing, titled = hashlib.sha256(), [], []
    for T, f, source, expected in frames:
        h = sha256(source)
        if expected is not None and h != expected:          # 描いた後にコマが変わった
            raise ToolError(f"描いたコマの一覧と、今のファイルが違います（T: {T}）。描き直したなら一覧も作り直してください")
        digest.update(f"{T}:{h}\n".encode())
        target = Path(out_dir) / source.name
        s = (T - 1) / fps
        if s < start:                                    # 題字の前は、そのまま写す
            shutil.copyfile(source, target)
            listing.append({"T": T, "f": f, "file": target.name, "sha256": h, "bytes": target.stat().st_size})
            continue
        with Image.open(source) as image:
            if image.size != (W, H):
                raise ToolError(f"コマの大きさがそろっていません（T {T}: {image.size}、最初のコマ: {(W, H)}）")
            _refuse_transparency(image, T)
            img = np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
        u = title.smoothstep((s - start) / fade_in) if fade_in > 0 else 1.0
        for layer, (spr, _detail) in zip(settings, sprites):
            cx, cy = layer["center"]
            title.blit(img, spr, cx * k, (cy + layer["rise"] * (1 - u)) * k, u * layer["opacity"], layer["blend"])
        Image.fromarray(title.to_uint8(img)).save(target)
        titled.append(T)
        listing.append({"T": T, "f": f, "file": target.name, "sha256": sha256(target), "bytes": target.stat().st_size})
    written = {"schema_version": "model3d-frames/0.1", "directory": str(Path(out_dir).resolve()), "fps": fps, "resolution": [W, H],
               "frames": listing, "missing": []}
    atomic_json(outputs["frames"], contracts.validate("frames", written))
    elapsed = round(time.monotonic() - began, 2)
    first_T, last_T = frames[0][0], frames[-1][0]
    report = {"schema_version": "model3d-title/0.1",
              "source": {"directory": str(source_dir.resolve()), "first": first_T, "last": last_T, "count": len(frames),
                         "sha256": digest.hexdigest()},
              "out_dir": str(Path(out_dir).resolve()), "resolution": [W, H], "fps": fps, "start": start, "fade_in": fade_in,
              "reference_width": reference, "scale": k,
              "shadow": {"blur_px": max(1.0, shadow[0] * k), "amount": shadow[1]} if shadow is not None else None,
              "layers": [dict(detail, center=list(layer["center"]), rise=layer["rise"], opacity=layer["opacity"], blend=layer["blend"])
                         for layer, (_spr, detail) in zip(settings, sprites)],
              "titled": {"count": len(titled), "first": titled[0] if titled else None, "last": titled[-1] if titled else None},
              "copied": len(frames) - len(titled), "seconds": elapsed}
    if "report" in outputs:
        atomic_json(outputs["report"], report)
    observation = {"kind": "title", "frames": len(frames), "titled": len(titled), "first_titled": titled[0] if titled else None,
                   "copied": len(frames) - len(titled), "layers": len(settings), "seconds": elapsed}
    return StepResult(observations=[observation])
